// [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
// Description: 应付板块 › 物流对账 › 付款做账。一张物流请款单付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。
//   上：请款单清单（按能不能做账分组：可做账 / 纸质件未到 / 票不齐 / 发票≠请款 / 未付款 / 已做账）。
//   点开：计提 vs 发票逐张比（核销 / 红冲更正 + 原因）+ 整张凭证预览（带全部核算维度，借贷平衡）。
//   第一版只预览、不写金蝶；纸质件没到可手动放行（用户 2026-10-02 定）。
// V2.798 计提记错主体(用户 2026-10-05「这得出两张了，一张给星期零做账，一张给星期九做账」)：本张凭证里直接补提到本主体再核销、支付；
//   计提更正单出两张——① 原主体红冲 ② 本主体补提；页面显示那边红冲做了没有。
// V2.799(用户「也是系统做星期零」)：原主体的红冲凭证也由系统建——保存到金蝶时一并在那边账簿新建红冲凭证并提交(不审核)；没建成可点「补做红冲」。
// V2.800 装订用(用户 2026-10-05「批量生成了就没人在付款单上写凭证号，装订的同事不好区分」)：已写金蝶的请款单打两样东西——
//   《装订对照清单》(按主体×凭证月份分页、按凭证号排：凭证号/付款日/供应商/金额/钉钉审批编号/金蝶付款单号/发票/更正单/勾选栏)
//   和《凭证号贴条》(一页 21 个，剪下贴在纸质付款单右上角，免手抄)。数据就是列表里的做账记录，不另取数。
// V2.801 扫码查凭证(用户「扫描那个付款单二维码，就知道是什么凭证、哪个主体」)：扫码枪扫纸质付款单右上角的钉钉二维码(或输审批编号)
//   → 大字显示 主体 + 凭证号；本次扫过的列在下面(重复扫会标)，可读出来。只读。
import React, { useEffect, useMemo, useRef, useState } from 'react'
// V2.802 手机也能查(用户「手机可以吗」)：扫码查凭证加「拍二维码」(拍照上传、服务器认码)；`#/vscan` 是手机专用的单页(VoucherScanPage)。
import { voucherList, voucherPreview, voucherPaperOverride, voucherPlans, voucherPost, voucherPostXred, voucherScan, voucherScanPhoto, voucherDdConfig } from '../api.js'
import { inDingTalk, loadDd, ddConfig, ddCall } from './ddBridge.js'

const money = n => (n == null || n === '' ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const pct = r => (r == null ? '—' : `${Math.round(r * 10000) / 100}%`)
const ST = {
  ready: ['可做账', 'ok', '已付款、发票齐（纸质件不卡做账，月末统一查验）'],
  paper: ['纸质件未到', 'warn', '纸质付款单/发票还没交到财务；可手动放行'],
  invdiff: ['发票≠请款', 'bad', '票夹里发票含税合计和请款金额对不上'],
  noinv: ['票不齐', 'warn', '票夹里还没有发票（流程里没传，要财务在收票台补）'],
  unpaid: ['未付款', 'neu', '金蝶还没有付款单'],
  booked: ['已做账', 'done', '发票都已被金蝶凭证引用（发票管家同步）'],
}
const ORDER = ['ready', 'invdiff', 'noinv', 'unpaid', 'booked']
// 批量做账：一致只核销 + 红冲更正(含尾差)。红冲更正写法已在金蝶实测(跨越 记-261 / 易风达 记-264)后放进来，
// 但要提示：勾到红冲更正的，批量条和确认框都单独列出来（用户 2026-10-02「放进去，但是要提示」）
const BATCH_KINDS = ['hx', 'tail', 'redo']
const REDO_KINDS = ['tail', 'redo']
// 做账类型(要读金蝶计提，列表出来后再逐张补)：计提与发票一致只核销 / 含尾差 / 要红冲更正 / 计提记错主体 / 金额不符 / 没有计提
const KIND = {
  hx: ['一致·只核销', 'ok'], tail: ['尾差·红冲更正', 'warn'], redo: ['需红冲更正', 'bad'],
  subj: ['计提记错主体', 'bad'], manual: ['金额不符·人工', 'warn'], noacc: ['没有计提', 'warn'], err: ['读取失败', 'neu'],
}
// 整单问题的建议动作
const ACT = { subj: '原主体红冲、本主体补提后再做', subjAuto: '本张补提并核销（点「凭证预览」单张做）', manual: '人工核对差额（补提 / 查发票）', noacc: '先计提', err: '刷新重试' }
const KIND_ORDER = ['hx', 'tail', 'redo', 'subj', 'manual', 'noacc']
const BLOCK_CLS = { 红冲: 'b-red', 更正: 'b-fix', 核销: 'b-hx', 支付: 'b-pay' }
const MODE = { hx: ['核销', 'ok'], rate: ['红冲+更正', 'bad'], fix: ['红冲+更正', 'bad'], move: ['补提到本主体', 'bad'] }

function dimText(d) {
  if (!d) return ''
  const cn = (c, n) => [c, n].filter(Boolean).join(' ')
  return [d.sup_code || d.sup_name ? '供应商 ' + cn(d.sup_code, d.sup_name) : '',
    d.dept_code || d.dept ? '部门 ' + cn(d.dept_code, d.dept) : '',
    d.fee_code || d.fee ? '费用项目 ' + cn(d.fee_code, d.fee) : '',
    d.biz_code || d.biz ? '产品分类 ' + cn(d.biz_code, d.biz) : '',
    d.proj_code || d.proj ? '产品项目 ' + cn(d.proj_code, d.proj) : '',
    d.bank !== undefined ? '银行账号 ' + (d.bank || '（待补）') : ''].filter(Boolean)
}

// 预览弹窗（V2.752 重排，用户「有点乱了，重新设计下」）：从上往下读——头部 → 一句结论 → 按税率核对 → 计提凭证 → 发票 → 凭证预览。
// 两张表上下排满宽(不再并排挤爆)；计提表不放长摘要(放悬停)；凭证里同一块后面相同摘要写「同上」，核销待认证行只写「发票号 + 同上」。
const rateOf = s => { const m = String(s ?? '').match(/[\d.]+/); if (!m) return null; const f = parseFloat(m[0]); return f > 1 ? f / 100 : f }
const r2 = x => Math.round((x + 1e-9) * 100) / 100

function rateCheck(acc, inv) {
  // 每个税率：计提(原) / 更正后 / 发票，更正后 = 发票 才算对上
  const g = {}
  const put = (r, k, v) => { const key = Math.round(r * 10000); (g[key] = g[key] || { r, acc: 0, fixed: 0, inv: 0 })[k] += v }
  acc.forEach(a => { put(a.rate, 'acc', a.gross); put(a.mode === 'rate' || a.mode === 'fix' || a.mode === 'move' ? (a.new_rate ?? a.rate) : a.rate, 'fixed', a.gross) })
  inv.forEach(i => { const r = i.deduct === false ? 0 : rateOf(i.rate); if (r != null) put(r, 'inv', i.gross) })   // 普票不抵扣按 0%
  return Object.values(g).sort((a, b) => a.r - b.r).map(x => ({ ...x, acc: r2(x.acc), fixed: r2(x.fixed), inv: r2(x.inv) }))
}

function dimLine(d) {
  return dimText(d).join(' · ')
}

// 计提更正单（V2.759，原称计提调整单，用户 2026-10-02「审核的时候就出来，打印后贴在钉钉单据后面」）：每张请款单一页 A4，
// 列出要红冲更正的计提：原计提 / 调整后 / 差额 + 原因，对应发票，会计处理，签字栏。数据来自预览接口的 adjust。
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const dimKey = l => [l.acct, l.fee_code, l.dept_code, l.biz_code, l.proj_code].join('|')
const expText = l => esc([l.acct + ' ' + (l.acct_name || ''), ...dimText(l)].join(' · '))
const MODE_CN = { rate: '改税率', fix: '改科目/维度/金额', tail: '尾差', move: '主体更正' }

// 版式照复核台导出的《计提更正单》(logistics_review._fix_sheet，用户 2026-10-02「你看看那个设计」)：横向；抬头两行居中
// (单名 / 供应商编码·名称)；每笔三行 原记账 / 应改为(没变写灰「不变」，变了标黄) / 原因；序号·主体·凭证号·费用归属月份·调账月份·更正人 三行合并；
// 表下合计(原记账/应改为两行)、说明、签字栏。付款做账多一行钉钉审批/付款/调整凭证信息，和对应发票。
const cnj = (c, n) => [c, n].filter(Boolean).join(' ')
// opt(主体更正的两张用)：title 单名 / subject 主体栏 / vno 调整凭证 / emptyOld、emptyNew 空格子写什么 / why(a) 原因行 / note 说明 / noBy 不印制单人(别人做账)
function adjustSheet(d, adjIn, opt = {}) {
  const q = d.req, p = q.posted, adj = adjIn || d.adjust || []
  const ym = String(d.voucher.date || '').slice(0, 7)
  const hereVno = p ? `${ym.replace('-', '年')}月 记-${esc(p.vno)}`.replace('年0', '年') : '（保存到金蝶后生成）'
  const vno = opt.vno || hereVno
  const inv = d.invoices || []
  const dimsOf = ls => [
    l => cnj(l.acct, l.acct_name), l => cnj(l.fee_code, l.fee), l => cnj(l.dept_code, l.dept),
    l => cnj(l.biz_code, String(l.biz || '').startsWith('（') ? '' : l.biz), l => cnj(l.proj_code, l.proj),
  ].map(f => [...new Set(ls.map(f))].filter(Boolean).join('<br>'))
  const same = (a, b) => Math.abs((a || 0) - (b || 0)) < 0.005
  const td = (o, n, fmt, cls) => same(o, n) ? `<td class="${cls} gray">不变</td>` : `<td class="${cls} hot">${fmt(n)}</td>`
  const blocks = adj.map((a, i) => {
    const od = dimsOf(a.old.exp), nd = dimsOf(a.new.exp)
    const by = p && !opt.noBy ? `${esc(p.by)}<br>${esc(String(p.at || '').slice(0, 10))}` : ''
    return `<tbody class="blk"><tr><td rowspan="3" class="c">${i + 1}</td><td rowspan="3">${esc(opt.subject || q.subject_full || q.subject)}</td>
      <td rowspan="3" class="c">${opt.vnoOf ? opt.vnoOf(a) : '记-' + esc(a.vno)}</td><td rowspan="3" class="c nw">${esc(a.year)}-${String(a.month).padStart(2, '0')}</td>
      <td rowspan="3" class="c nw${(opt.ym || ym) !== `${a.year}-${String(a.month).padStart(2, '0')}` ? ' hotf' : ''}">${esc(opt.ym || ym)}</td>
      <td class="lb old">原记账</td>${od.map(x => `<td class="old">${x || opt.emptyOld || '空'}</td>`).join('')}
      <td class="n old">${money(a.old.gross)}</td><td class="c old">${pct(a.old.rate)}</td><td class="n old">${money(a.old.net)}</td><td class="n old">${money(a.old.tax)}</td>
      <td rowspan="3" class="c">${by}</td></tr>
      <tr><td class="lb new">应改为</td>${nd.map((x, k) => x === od[k] ? `<td class="gray">${!x && opt.side ? '—' : '不变'}</td>` : `<td class="hot">${x || opt.emptyNew || '空'}</td>`).join('')}
      ${td(a.old.gross, a.new.gross, money, 'n')}${same(a.old.rate, a.new.rate) ? '<td class="c gray">不变</td>' : `<td class="c hot">${pct(a.new.rate)}</td>`}
      ${td(a.old.net, a.new.net, money, 'n')}${td(a.old.tax, a.new.tax, money, 'n')}</tr>
      <tr class="why"><td class="lb">原因</td><td colspan="9">${esc(MODE_CN[a.mode] || a.mode)}：${esc(opt.why ? opt.why(a) : (a.why || ''))}　<span class="dim">摘要：${esc(a.expl)}</span></td></tr></tbody>`
  }).join('')
  const S = (k, w) => r2(adj.reduce((s, a) => s + (a[w][k] || 0), 0))
  const tot = (lb, w, cls) => `<tr class="tot ${cls}"><td colspan="11" class="n">${lb}</td><td class="n">${money(S('gross', w))}</td><td></td><td class="n">${money(S('net', w))}</td><td class="n">${money(S('tax', w))}</td><td></td></tr>`
  const invTxt = inv.map(i => `<span class="m">${esc(i.number)}</span>（${esc(i.rate)}${i.deduct === false ? '·不抵扣' : ''}，含税 ${money(i.gross)}，税额 ${money(i.tax)}）`).join('；')
  return `<div class="sheet">
  <div class="t1">${esc(opt.title || '计提更正单')}</div>
  <div class="t2">供应商编码：${esc(q.code)}　　　供应商名称：${esc(q.payee)}</div>
  <div class="t3">钉钉审批 ${esc(q.bid)}（${esc(q.applicant)}）　·　账单期间 ${esc(q.period)}　·　付款 ${money(q.amount)}${p ? `（付款单 ${esc(p.bill_no)}）` : ''}　·　调整凭证 <b>${vno}</b></div>
  <table class="fx"><colgroup><col style="width:3.2%"><col style="width:7.4%"><col style="width:5.4%"><col style="width:5.4%"><col style="width:5.4%"><col style="width:4.6%">
    <col style="width:8.5%"><col style="width:10%"><col style="width:8%"><col style="width:7%"><col style="width:6%"><col style="width:6.8%"><col style="width:4.2%"><col style="width:6.8%"><col style="width:5.2%"><col></colgroup>
  <thead><tr><th>序号</th><th>主体</th><th>凭证号</th><th>费用归属<br>月份</th><th>调账<br>月份</th><th></th><th>科目</th><th>费用项目</th><th>部门</th><th>产品分类</th><th>产品项目</th>
    <th>金额<br>(含税)</th><th>税率</th><th>不含税<br>金额</th><th>税额</th><th>更正人/日期</th></tr></thead>
  ${blocks}<tbody>${tot(adj.length > 1 ? '原记账合计' : '原记账', 'old', 'o')}${tot(adj.length > 1 ? '应改为合计' : '应改为', 'new', 'w')}</tbody></table>
  ${opt.note ? opt.note(hereVno) : `<div class="note">说明：以上计提在付款凭证 ${vno} 中整笔红冲，再按「应改为」重新计提（税额挂暂估进项税），随后凭发票核销转待认证、支付；应改为写「不变」的未动。
    本次付款共核销计提 ${(d.accruals || []).length} 张（${(d.accruals || []).map(a => '记-' + esc(a.vno)).join('、')}），其余未列的按原计提直接核销。</div>`}
  <div class="note">对应发票 ${inv.length} 张：${invTxt}</div>
  <div class="sign"><span>制单人：${esc(p && !opt.noBy ? p.by : '') || (opt.noBy ? '______________' : '')}</span><span>复核人：______________</span><span>审核人：______________</span><span>日期：${p ? esc(String(p.at || '').slice(0, 10)) : '______________'}</span></div>
  <div class="ft">财务核算工作台 · 付款做账 · 打印于 ${new Date().toLocaleString('zh-CN', { hour12: false })}${p ? '' : ' · 未写金蝶，凭证号待定'}</div></div>`
}

const SHEET_CSS = `@page{size:A4 landscape;margin:10mm 10mm}*{box-sizing:border-box}body{font:11px/1.45 "Microsoft YaHei","PingFang SC",sans-serif;color:#1B2733;margin:0}
.sheet{page-break-after:always}.sheet:last-child{page-break-after:auto}
.t1{text-align:center;font-size:20px;font-weight:700;margin:2px 0 4px}.t2{text-align:center;font-size:12.5px;font-weight:700;margin-bottom:4px}
.t3{text-align:center;font-size:11px;color:#5E6B78;margin-bottom:8px}.t3 b{color:#1B2733}
table{width:100%;border-collapse:collapse;table-layout:fixed}.fx th,.fx td{border:1px solid #B8C4CC;padding:4px 5px;vertical-align:middle;word-break:break-all}
.fx th{background:#5E6B78;color:#fff;font-weight:700;text-align:center;line-height:1.25}
.fx tbody.blk{break-inside:avoid}.fx tbody.blk tr:last-child td{border-bottom:2px solid #7A8791}
.c{text-align:center}.nw{white-space:nowrap}.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.m{font-family:Consolas,monospace}
.lb{text-align:center;white-space:nowrap}.old{background:#EEF2F4;color:#3d4852}.lb.old{color:#5E6B78}.lb.new{background:#FBF0DA;color:#8A5A00;font-weight:700}
.hot{background:#FDF6E8;color:#8A5A00;font-weight:700}.gray{color:#9AA5AE}.hotf{color:#8A5A00;font-weight:700}
tr.why td{background:#fff}tr.why .lb{font-weight:700;color:#5E6B78}.dim{color:#7a8791;font-size:10.5px}
tr.tot td{font-weight:700;border:1px solid #B8C4CC}tr.tot.o td{background:#E7ECEF}tr.tot.w td{background:#FBF0DA;color:#8A5A00}
.note{margin-top:6px;color:#5E6B78;font-size:10.5px;line-height:1.6}.note .ent{margin:3px 0 3px 12px;color:#1B2733}.note b{color:#1B2733}
.sign{display:flex;justify-content:space-between;margin-top:16px;font-size:11.5px;padding:0 4px}.ft{margin-top:8px;color:#9AA5AE;font-size:9.5px;text-align:right}
.wait{padding:40px;text-align:center;color:#555}@media screen{body{background:#eee}.sheet{background:#fff;width:297mm;min-height:210mm;margin:12px auto;padding:10mm;box-shadow:0 1px 4px #0002}}`

// 一张请款单要出哪几张更正单：红冲更正的(改税率/维度/尾差)一张；计提记错主体的出两张——① 原主体红冲(交给做那边账的人) ② 本主体补提。
function sheetsOf(d) {
  const adj = d.adjust || [], norm = adj.filter(a => a.mode !== 'move'), mv = adj.filter(a => a.mode === 'move')
  const out = norm.length ? [adjustSheet(d, norm)] : []
  const here = d.req.subject, hereFull = d.req.subject_full || here
  const zero = r => ({ gross: 0, net: 0, tax: 0, rate: r, exp: [] })
  ;[...new Set(mv.map(a => a.from))].forEach(f => {
    const as = mv.filter(a => a.from === f), xs = (d.xbook || []).filter(x => x.short === f)
    const refs = as.map(a => `记-${esc(a.vno)}`).join('、')
    const rev = xs.map(x => x.reversed).filter(Boolean)
    const ent = xs.map(x => `<div class="ent"><b>摘要「${esc((x.lines[0] || {}).expl)}」</b>：` + x.lines.map(l =>
      `${l.dr ? '借' : '贷'} ${esc(l.acct)} ${esc(l.acct_name)} <b>${money(l.dr || l.cr)}</b>${dimText(l.dims).length ? `（${esc(dimText(l.dims).join('，'))}）` : ''}`).join('；') + '</div>').join('')
    // ① 原主体：整笔红冲
    out.push(adjustSheet(d, as.map(a => ({ ...a, new: zero(a.old.rate) })), {
      side: 'from', noBy: !rev.some(r => r.sys), ym: rev.length ? `${rev[0].year}-${String(rev[0].month).padStart(2, '0')}` : '待定', title: `计提更正单（主体更正 ① ${f} 红冲）`, subject: as[0].from_full || f, emptyNew: '冲回',
      vno: rev.length ? rev.map(r => `${r.year}年${r.month}月 记-${esc(r.vno)}`).join('、') : '（保存到金蝶后生成）',
      why: () => `这笔费用应由${here}承担（发票开给${here}、由${here}付款），计提时记到了本主体，整笔红冲`,
      note: hv => `<div class="note">说明：<b>${esc(f)}</b>账簿做一张红冲凭证，把上面的计提整笔冲回（原分录全额取负；${rev.some(r => r.sys) ? '系统已建并提交，待审核' : rev.length ? '已做' : '保存到金蝶时系统建好并提交，待审核'}）：${ent}
        ${esc(here)}已在付款凭证 ${hv} 中补提并核销（见「主体更正 ② ${esc(here)} 补提」）。</div>`,
    }))
    // ② 本主体：补提
    out.push(adjustSheet(d, as.map(a => ({ ...a, old: zero(null) })), {
      side: 'here', title: `计提更正单（主体更正 ② ${here} 补提）`, subject: hereFull, emptyOld: '未计提',
      vnoOf: a => `原 ${esc(a.from)}<br>记-${esc(a.vno)}`,
      why: a => `原记在${a.from} 记-${a.vno}，本主体没有计提，补提到本主体${String(a.why || '').includes('；') ? '；' + String(a.why).split('；').slice(1).join('；') : ''}`,
      note: hv => `<div class="note">说明：这笔计提原记在${esc(f)}（${refs}），由${esc(f)}另做红冲（见「主体更正 ① ${esc(f)} 红冲」）。
        本主体在付款凭证 ${hv} 中按「应改为」补提（税额挂暂估进项税），随后凭发票核销转待认证、支付。</div>`,
    }))
  })
  return out
}

// 先同步开窗(避免被拦截)，数据到了再写；ds 可以是数组或 Promise
function printAdjust(ds, title) {
  const w = window.open('', '_blank')
  if (!w) { alert('浏览器拦截了弹窗，请允许本站弹出窗口后再点'); return }
  w.document.write(`<!doctype html><meta charset="utf-8"><title>${esc(title || '计提更正单')}</title><style>${SHEET_CSS}</style><div class="wait">正在生成计提更正单…</div>`)
  w.document.close()
  Promise.resolve(ds).then(list => {
    const ok = list.filter(d => d && (d.adjust || []).length)
    w.document.body.innerHTML = ok.length ? ok.flatMap(sheetsOf).join('') : '<div class="wait">没有需要调整的计提</div>'
    if (ok.length) setTimeout(() => w.print(), 300)
  }).catch(e => { w.document.body.innerHTML = `<div class="wait">生成失败：${esc(e.message)}</div>` })
}

// ---------- 装订：对照清单 / 凭证号贴条 ----------
const vnum = v => parseInt(String(v || '').replace(/\D/g, ''), 10) || 0
const SUBJ_ORDER = ['深圳星期零', '深圳星期九', '孝感星期九']
// 列表行 → 装订条目。系统写的取做账记录；金蝶里别人已做的取发票管家同步到的凭证号；主体更正在原主体账簿建的红冲凭证单列一条(没有纸质付款单)。
function bindItems(rows, plans) {
  const out = []
  rows.forEach(r => {
    const p = r.posted
    const kd = !p ? [r.paid_voucher, ...(r.booked || []).map(b => (String(b).match(/'number': '([^']+)'/) || [])[1] || (/^记/.test(String(b)) ? b : ''))].filter(Boolean) : []
    const vno = p ? p.vno : String(kd[0] || '').replace(/^记-?/, '')
    if (!vno) return
    const kind = (plans[r.inst] || {}).kind
    out.push({ inst: r.inst, subject: r.subject, book: r.book, month: String(r.paid || (p && p.at) || '').slice(0, 7), vno, paid: r.paid, sup: r.sup_full || r.carrier,
      code: r.code, amount: r.amount, bid: r.bid, bill: p ? p.bill_no : '', n_inv: r.n_inv, period: r.period,
      adj: kind === 'subj' ? '有（② 补提）' : ['tail', 'redo'].includes(kind) ? '有' : '', src: p ? '' : '金蝶已有' })
    Object.values((p && p.xred) || {}).forEach(x => out.push({ inst: r.inst + '|x' + x.src_vno, subject: x.short, book: '', red: true,
      month: `${x.year}-${String(x.month).padStart(2, '0')}`, vno: x.vno, paid: x.date, sup: r.sup_full || r.carrier, code: r.code, amount: -x.gross,
      bid: r.bid, bill: '', n_inv: 0, period: r.period, adj: '有（① 红冲）', src: `红冲凭证 · 冲 ${r.period ? Number(r.period.slice(5)) + '/' : ''}${x.src_vno}#，无纸质付款单` }))
  })
  const so = x => { const i = SUBJ_ORDER.indexOf(x); return i < 0 ? 9 : i }
  return out.sort((a, b) => so(a.subject) - so(b.subject) || a.subject.localeCompare(b.subject) || a.month.localeCompare(b.month) || vnum(a.vno) - vnum(b.vno))
}
const ymCn = m => (m ? `${m.slice(0, 4)}年${Number(m.slice(5))}月` : '')

function bindListHtml(items) {
  const groups = []
  items.forEach(x => { const g = groups[groups.length - 1]; if (g && g.subject === x.subject && g.month === x.month) g.items.push(x); else groups.push({ subject: x.subject, month: x.month, book: x.book, items: [x] }) })
  return groups.map(g => {
    const sum = r2(g.items.filter(x => !x.red).reduce((s, x) => s + (x.amount || 0), 0))
    return `<div class="sheet"><div class="t1">付款凭证装订对照清单</div>
    <div class="t2">${esc(g.subject)}${g.book ? `（账簿 ${esc(g.book)}）` : ''}　·　${esc(ymCn(g.month))}凭证　·　物流请款单</div>
    <table class="bl"><colgroup><col style="width:5%"><col style="width:9%"><col style="width:9%"><col><col style="width:11%"><col style="width:19%"><col style="width:11%"><col style="width:6%"><col style="width:10%"><col style="width:6%"></colgroup>
    <thead><tr><th>序号</th><th>凭证号</th><th>付款日</th><th>供应商</th><th>付款金额</th><th>钉钉审批编号</th><th>金蝶付款单号</th><th>发票</th><th>计提更正单</th><th>已装订</th></tr></thead>
    <tbody>${g.items.map((x, i) => `<tr class="${x.red ? 'red' : ''}"><td class="c">${i + 1}</td><td class="c vno">记-${esc(x.vno)}</td><td class="c">${esc(String(x.paid || '').slice(5))}</td>
      <td>${esc(x.sup)}<div class="sub">${esc(x.code || '')}${x.src ? ` · ${esc(x.src)}` : ''}</div></td><td class="n">${money(x.amount)}</td>
      <td class="m">${esc(x.bid || '')}</td><td class="m c">${esc(x.bill || '—')}</td><td class="c">${x.n_inv ? x.n_inv + ' 张' : '—'}</td><td class="c">${esc(x.adj || '—')}</td><td class="c box">□</td></tr>`).join('')}
    <tr class="tot"><td colspan="4" class="n">合计 ${g.items.length} 张凭证${g.items.some(x => x.red) ? `（其中红冲凭证 ${g.items.filter(x => x.red).length} 张，不计入金额）` : ''}</td><td class="n">${money(sum)}</td><td colspan="5"></td></tr></tbody></table>
    <div class="note">用法：纸质付款单上印有钉钉审批编号，对着本表找到凭证号，按凭证号顺序装订；装好一张在「已装订」打勾。「计提更正单」写「有」的，更正单贴在该付款单后面一起装。${g.items.some(x => x.red) ? '红冲凭证没有纸质付款单，只附计提更正单 ①。' : ''}</div>
    <div class="sign"><span>装订人：______________</span><span>日期：______________</span><span>复核人：______________</span></div>
    <div class="ft">财务核算工作台 · 付款做账 · 打印于 ${new Date().toLocaleString('zh-CN', { hour12: false })}</div></div>`
  }).join('')
}
const BIND_CSS = `@page{size:A4 portrait;margin:12mm 10mm}*{box-sizing:border-box}body{font:11px/1.45 "Microsoft YaHei","PingFang SC",sans-serif;color:#1B2733;margin:0}
.sheet{page-break-after:always}.sheet:last-child{page-break-after:auto}.t1{text-align:center;font-size:19px;font-weight:700;margin:2px 0 4px}
.t2{text-align:center;font-size:12.5px;font-weight:700;margin-bottom:8px}table{width:100%;border-collapse:collapse;table-layout:fixed}
.bl th,.bl td{border:1px solid #B8C4CC;padding:5px 5px;vertical-align:middle;word-break:break-all}.bl th{background:#5E6B78;color:#fff;font-weight:700;text-align:center}
.bl tr{break-inside:avoid}.c{text-align:center}.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.m{font-family:Consolas,monospace;font-size:10.5px}
.vno{font-weight:700;font-size:13px;white-space:nowrap}.sub{color:#7a8791;font-size:9.5px}.box{font-size:15px;line-height:1}tr.red td{background:#FBF3F3}tr.red .n{color:#B03A3A}
tr.tot td{font-weight:700;background:#E7ECEF}.note{margin-top:7px;color:#5E6B78;font-size:10.5px;line-height:1.6}
.sign{display:flex;justify-content:space-between;margin-top:18px;font-size:11.5px;padding:0 4px}.ft{margin-top:8px;color:#9AA5AE;font-size:9.5px;text-align:right}
.wait{padding:40px;text-align:center;color:#555}@media screen{body{background:#eee}.sheet{background:#fff;width:210mm;min-height:297mm;margin:12px auto;padding:12mm 10mm;box-shadow:0 1px 4px #0002}}`

function slipHtml(items) {
  const pages = []
  for (let i = 0; i < items.length; i += 21) pages.push(items.slice(i, i + 21))
  return pages.map(pg => `<div class="pg">${pg.map(x => `<div class="slip${x.red ? ' red' : ''}">
    <div class="top"><span class="vno">记-${esc(x.vno)}</span><span class="who">${esc(x.subject)}<br>${esc(ymCn(x.month))}</span></div>
    <div class="sup">${esc(x.sup)}</div>
    <div class="amt">${x.red ? '红冲 ' : '¥ '}${money(Math.abs(x.amount || 0))}${x.adj ? '<span class="tag">附更正单</span>' : ''}</div>
    <div class="ids">审批 ${esc(x.bid || '—')}${x.bill ? `<br>付款单 ${esc(x.bill)}` : x.red ? '<br>无纸质付款单' : ''}</div></div>`).join('')}</div>`).join('')
}
const SLIP_CSS = `@page{size:A4 portrait;margin:8mm}*{box-sizing:border-box}body{font:10px/1.35 "Microsoft YaHei","PingFang SC",sans-serif;color:#1B2733;margin:0}
.pg{display:grid;grid-template-columns:repeat(3,1fr);grid-auto-rows:39.5mm;page-break-after:always}.pg:last-child{page-break-after:auto}
.slip{border:1px dashed #8A96A2;padding:3mm 3.5mm;overflow:hidden;display:flex;flex-direction:column;gap:1mm}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:4px}.vno{font-size:21px;font-weight:800;line-height:1.05;white-space:nowrap}
.who{text-align:right;font-size:9.5px;color:#3d4852;line-height:1.3;font-weight:700}.sup{font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.amt{font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums}.tag{font-size:8.5px;font-weight:400;border:1px solid #8A5A00;color:#8A5A00;border-radius:3px;padding:0 3px;margin-left:5px;vertical-align:middle}
.ids{font:8.5px/1.4 Consolas,monospace;color:#5E6B78;margin-top:auto}.slip.red .vno,.slip.red .amt{color:#B03A3A}
.wait{padding:40px;text-align:center;color:#555}@media screen{body{background:#eee}.pg{background:#fff;width:210mm;min-height:297mm;margin:12px auto;padding:8mm;box-shadow:0 1px 4px #0002;align-content:start}}`

function printHtml(title, css, html) {
  const w = window.open('', '_blank')
  if (!w) { alert('浏览器拦截了弹窗，请允许本站弹出窗口后再点'); return }
  w.document.write(`<!doctype html><meta charset="utf-8"><title>${esc(title)}</title><style>${css}</style>${html || '<div class="wait">没有可打印的凭证</div>'}`)
  w.document.close()
  if (html) setTimeout(() => w.print(), 300)
}

// 扫码查凭证：扫码枪像键盘一样把二维码内容打进输入框再回车；输入框一直占着焦点，扫一张出一张。
// 手机拍的照片先在本机缩到长边 2000 再传(原图 5～10M，传得慢)；缩不了就传原图
function shrinkPhoto(file, max = 2000) {
  return new Promise(res => {
    try {
      const url = URL.createObjectURL(file), img = new Image()
      img.onload = () => {
        const k = Math.min(1, max / Math.max(img.width, img.height))
        const cv = document.createElement('canvas')
        cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k)
        cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height)
        URL.revokeObjectURL(url)
        cv.toBlob(b => res(b || file), 'image/jpeg', 0.88)
      }
      img.onerror = () => { URL.revokeObjectURL(url); res(file) }
      img.src = url
    } catch { res(file) }
  })
}

// 扫码查凭证的状态与动作（电脑弹窗 ScanBox、手机页 VoucherScanPage 共用）。run(查询Promise) → 结果；after 每次查完调(电脑上用来把光标放回输入框)
function useScan(after) {
  const [v, setV] = useState('')
  const [cur, setCur] = useState(null)        // 最近一次结果
  const [busy, setBusy] = useState(false)
  const [hist, setHist] = useState([])        // 本次扫过的(新的在前)
  const [say, setSay] = useState(false)
  const histRef = useRef(hist); histRef.current = hist
  const sayRef = useRef(say); sayRef.current = say
  const busyRef = useRef(false)
  const speak = t => { try { if (sayRef.current && window.speechSynthesis) { window.speechSynthesis.cancel(); window.speechSynthesis.speak(Object.assign(new SpeechSynthesisUtterance(t), { lang: 'zh-CN', rate: 1.1 })) } } catch { /* 没有语音就算了 */ } }
  const run = pr => {
    if (busyRef.current) return Promise.resolve(null)
    busyRef.current = true; setBusy(true)
    return pr.then(r => {
      const dup = r.ok && histRef.current.some(h => h.inst === r.inst)
      setCur({ ...r, dup })
      if (r.ok) {
        if (!dup) setHist(h => [{ ...r, at: new Date().toLocaleTimeString('zh-CN', { hour12: false }) }, ...h])
        const a = (r.vouchers || [])[0]
        speak(a ? `${dup ? '重复，' : ''}${a.subject}，记 ${a.vno}` : '还没做账')
      } else speak('没查到')
      return r
    }).catch(e => { const r = { ok: false, msg: e.message }; setCur(r); return r })
      .finally(() => { busyRef.current = false; setBusy(false); if (after) after() })
  }
  const go = () => { const code = v.trim(); if (!code || busyRef.current) return; setV(''); run(voucherScan(code)) }
  const shot = e => {                        // 拍照/选图 → 传上去认码
    const f = e.target.files && e.target.files[0]
    e.target.value = ''
    if (f) run(shrinkPhoto(f).then(voucherScanPhoto))
  }
  const bySubj = {}
  hist.forEach(h => { const k = (h.vouchers || [])[0] ? h.vouchers[0].subject : '还没做账'; bySubj[k] = (bySubj[k] || 0) + 1 })
  return { v, setV, cur, setCur, busy, hist, setHist, say, setSay, run, go, shot, bySubj }
}

function ScanBox({ onClose, page }) {
  const ref = useRef(null)
  const focus = () => { if (ref.current) ref.current.focus() }
  useEffect(() => { if (!page) focus() }, [])
  const { v, setV, cur, setCur, busy, hist, setHist, say, setSay, go, shot, bySubj } = useScan(() => { if (!page) setTimeout(focus, 30) })
  const fileRef = useRef(null)
  const phoneUrl = `${window.location.origin}${window.location.pathname}#/vscan`
  return (
    <div className={page ? 'lv-scanpage' : 'lv-mask'} onMouseDown={e => { if (!page && e.target === e.currentTarget) onClose() }}>
      <div className="lv-dlg lv-scan" role="dialog" aria-label="扫码查凭证" onClick={page ? undefined : focus}>
        <div className="lv-dh"><div className="lv-title"><b>扫码查凭证</b>{!page && <><span className="sep">·</span>扫付款单右上角的二维码，看它是哪个主体、哪张凭证</>}</div>
          {!page && <button className="lv-x" onClick={onClose} aria-label="关闭">✕</button>}</div>
        <input ref={fileRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={shot} />
        {page && <button className="btn btn-pri sc-shot" disabled={busy} onClick={() => fileRef.current && fileRef.current.click()}>{busy ? '识别中…' : '📷 拍付款单右上角的二维码'}</button>}
        <div className="sc-in">
          <input ref={ref} value={v} onChange={e => setV(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') go() }}
            placeholder={page ? '或输 20 位审批编号' : '用扫码枪扫付款单右上角的二维码；没有扫码枪就输 20 位审批编号再回车'} autoComplete="off" spellCheck={false} inputMode={page ? 'numeric' : undefined} />
          <button className="btn btn-pri" disabled={busy || !v.trim()} onClick={go}>{busy ? '查询中…' : '查'}</button>
          {!page && <button className="btn" disabled={busy} title="没有扫码枪：选一张拍了二维码的照片/截图，系统认码" onClick={e => { e.stopPropagation(); fileRef.current && fileRef.current.click() }}>传照片</button>}
          <label className="sc-say"><input type="checkbox" checked={say} onChange={e => setSay(e.target.checked)} /> 读出来</label>
        </div>
        {!cur && (page
          ? <div className="sc-hint">点上面的按钮拍照：对准付款单右上角的二维码，拍近一点、别反光。也可以在框里输 20 位审批编号。</div>
          : <div className="sc-hint">扫码枪要在英文输入法下用。光标停在上面的框里，扫一张出一张，不用点鼠标。
            <br />手机也能查：手机浏览器打开 <span className="mono" style={{ userSelect: 'all' }}>{phoneUrl}</span>，登录后点「拍二维码」。</div>)}
        {cur && !cur.ok && <div className="sc-res bad"><div className="sc-big">没查到</div><div>{cur.msg}</div></div>}
        {cur && cur.ok && <div className={'sc-res ' + ((cur.vouchers || []).length ? 'ok' : 'warn')}>
          {(cur.vouchers || []).length ? cur.vouchers.map((x, i) => <div key={i} className={'sc-v' + (i ? ' more' : '')}>
            <span className="sc-subj">{x.subject || '主体未知'}</span><span className="sc-big">记-{x.vno}</span>
            <span className="sc-mon">{ymCn(x.month)}</span><span className="dim">{x.what}{x.src === '金蝶已有' ? ' · 金蝶已有（不是本系统写的）' : ''}</span></div>)
            : <div className="sc-big">{cur.state}</div>}
          <div className="sc-meta">{cur.dup && <b className="bad">这张刚才扫过了　</b>}{cur.kind} · {cur.payee}{cur.amount != null && <> · <b className="mono">{money(cur.amount)}</b></>}
            {cur.bid && <> · 审批 <span className="mono">{cur.bid}</span></>}{(cur.vouchers || [])[0]?.bill_no && <> · 付款单 <span className="mono">{cur.vouchers[0].bill_no}</span></>}
            {(cur.vouchers || [])[0]?.maker && <> · 制单 <b>{cur.vouchers[0].maker}</b>{cur.vouchers[0].operator ? `（经办 ${cur.vouchers[0].operator}）` : ''}</>}
            {(cur.vouchers || [])[0] && <> · {cur.vouchers[0].checker ? `审核 ${cur.vouchers[0].checker}` : cur.vouchers[0].audited === false ? '还没审核' : ''}</>}
            {cur.n_adjust > 0 && <b className="warn">　附计提更正单</b>}{cur.has_xred && <b className="warn">（两张：① 红冲 ② 补提）</b>}</div>
        </div>}
        <div className="lv-sec">本次已扫 <span className="dim">{hist.length} 张{Object.keys(bySubj).length > 0 && '：' + Object.entries(bySubj).map(([k, n]) => `${k} ${n}`).join(' · ')}</span>
          <span style={{ flex: 1 }} />{hist.length > 0 && <button className="lnk" onClick={() => { setHist([]); setCur(null) }}>清空</button>}</div>
        <table className="lv-t"><thead><tr><th style={{ width: 40 }}>#</th><th style={{ width: 110 }}>主体</th><th style={{ width: 110 }}>凭证号</th><th style={{ width: 90 }}>凭证月份</th><th>供应商</th><th className="num" style={{ width: 120 }}>金额</th><th style={{ width: 80 }}>时间</th><th style={{ width: 90 }}>经办/制单</th></tr></thead>
          <tbody>{hist.map((h, i) => { const a = (h.vouchers || [])[0]; return <tr key={h.inst}>
            <td className="dim">{hist.length - i}</td><td>{a ? a.subject : '—'}</td><td className="mono"><b>{a ? '记-' + a.vno : '还没做账'}</b>{(h.vouchers || []).length > 1 && <span className="dim"> +{h.vouchers.length - 1}</span>}</td>
            <td>{a ? ymCn(a.month) : ''}</td><td>{h.payee}</td><td className="num">{money(h.amount)}</td><td className="dim">{h.at}</td><td>{a ? (a.operator || a.maker) : ''}</td></tr> })}
            {!hist.length && <tr><td colSpan="8" className="lv-empty">还没扫</td></tr>}</tbody></table>
      </div>
    </div>
  )
}

// 手机专用单页（#/vscan，App 在登录后直接出这一页）
// ---------- 手机页（#/vscan，V2.803 重做：用户「是不是可以再优化下」「扫描，而不是拍照」）----------
// 在钉钉里打开 → 底部大按钮调钉钉自带的「扫一扫」(实时扫，可连续扫)；别的浏览器(站点是 http，网页不能直接开摄像头)退回拍照识别。
// 结果做成一张大卡：主体按颜色分(分堆时一眼认)，记-号最大；下面是本次已扫的卡片列表。
const SUBJ_TONE = { 深圳星期零: 'b', 深圳星期九: 't', 孝感星期九: 'o' }
const IcScan = () => <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16" /></svg>
const IcCam = () => <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" /><circle cx="12" cy="13" r="3.5" /></svg>
let _vsCfg = null
const vsDdSetup = () => { if (!_vsCfg) { _vsCfg = ddConfig(voucherDdConfig, ['biz.util.scan']); _vsCfg.catch(() => { _vsCfg = null }) } return _vsCfg }

export function VoucherScanPage({ user }) {
  const S = useScan()
  const dd = inDingTalk()
  const fileRef = useRef(null)
  const [manual, setManual] = useState(false)
  const [auto, setAuto] = useState(false)       // 连续扫：查到一张后自动再开扫一扫
  const autoRef = useRef(auto); autoRef.current = auto
  const timer = useRef(null)
  const [note, setNote] = useState('')
  useEffect(() => { if (dd) vsDdSetup().catch(() => {}); return () => clearTimeout(timer.current) }, [])
  const photo = () => { clearTimeout(timer.current); if (fileRef.current) fileRef.current.click() }
  const ddScan = async () => {
    clearTimeout(timer.current); setNote('')
    let cfgErr = ''
    try {
      await loadDd()
      try { await vsDdSetup() } catch (e) { cfgErr = e.message }     // 鉴权没过也试着扫一次，调不起再报
      const fn = window.dd && window.dd.biz && window.dd.biz.util && window.dd.biz.util.scan
      if (!fn) throw new Error('这个钉钉版本调不起扫一扫')
      const text = await ddCall(fn, { type: 'qrCode' }, x => (x && (x.text || x.content || x.result)) || '')
      if (!text) return
      const r = await S.run(voucherScan(text))
      if (autoRef.current && r && r.ok) timer.current = setTimeout(ddScan, 1600)
    } catch (e) {
      const m = String((e && (e.errorMessage || e.message)) || e || '')
      if (/cancel|取消/i.test(m) || String(e && e.errorCode) === '300001') return
      setNote(`钉钉扫一扫没调起来：${m}${cfgErr && cfgErr !== m ? `（${cfgErr}）` : ''}。先用「拍照」识别，把这句话发给管理员。`)
    }
  }
  const c = S.cur, vs = (c && c.vouchers) || [], a = vs[0]
  const tone = x => SUBJ_TONE[x] || 'g'
  // 制单人照金蝶的写(系统做的账在金蝶里是「系统操作员」，和打印的凭证一致)；经办人＝在工作台点「保存到金蝶」的人
  const kv = c && c.ok ? [['制单人', a ? a.maker : ''], ['经办人', a && a.operator ? a.operator + '（在工作台做的账）' : ''],
    ['审核人', a ? (a.checker || (a.audited === false ? '还没审核' : '')) : ''],
    ['供应商', c.payee], ['金额', c.amount != null ? money(c.amount) : ''], ['审批编号', c.bid], ['付款单', a && a.bill_no]].filter(x => x[1]) : []
  // iPhone 底部有一条横条(Home 指示条)：钉钉内置浏览器把页面铺到它下面、又不报安全区高度，主按钮下沿会被压住(用户真机截图)——iPhone 上底部固定多留 34px
  const ios = /iPhone|iPad|iPod/i.test(navigator.userAgent || '')
  return <div className={'lv vs' + (ios ? ' vs-ios' : '')}><style>{CSS}</style>
    <input ref={fileRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={S.shot} />
    <header className="vs-top">
      <div><div className="vs-h1">扫码查凭证</div><div className="vs-sub">扫付款单右上角的二维码，看它记在哪个主体、哪张凭证</div></div>
    </header>
    <main className="vs-main">
      {S.busy ? <div className="vs-card vs-wait"><span className="vs-spin" />正在查…</div>
        : !c ? <div className="vs-card vs-empty">
          <svg width="92" height="112" viewBox="0 0 92 112" fill="none"><rect x="6" y="4" width="80" height="104" rx="6" fill="var(--bg)" stroke="var(--line-strong)" strokeWidth="2" />
            <rect x="56" y="12" width="22" height="22" rx="3" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2" /><path d="M61 17h5v5h-5zM68 24h5v5h-5zM68 17h5M61 29h3" stroke="var(--accent)" strokeWidth="2" />
            <path d="M16 20h30M16 44h60M16 56h60M16 68h60M16 80h40" stroke="var(--line-strong)" strokeWidth="3" strokeLinecap="round" /></svg>
          <div><b>对准付款单右上角的二维码</b><br />{dd ? '点下面「扫一扫」，扫到就出结果' : '点下面「拍二维码」，拍近一点、别反光'}</div></div>
          : !c.ok ? <div className="vs-card bad"><div className="vs-t">没查到</div><div className="vs-msg">{c.msg}</div></div>
            : a ? <div className={'vs-card res tone-' + tone(a.subject)}>
              {c.dup && <div className="vs-dup">这张刚才扫过了</div>}
              <div className="vs-subj">{a.subject || '主体未知'}</div>
              <div className="vs-vno">记-{a.vno}</div>
              <div className="vs-mon">{ymCn(a.month)} · {a.what}{a.src === '金蝶已有' ? '（金蝶已有）' : ''}</div>
              {vs.slice(1).map((x, i) => <div key={i} className="vs-more">另有　<b>{x.subject} 记-{x.vno}</b>　{x.what}</div>)}
              {(c.n_adjust > 0 || c.has_xred) && <div className="vs-tag">附计提更正单{c.has_xred ? '（两张：① 红冲 ② 补提）' : ''}</div>}
              <dl className="vs-kv">{kv.map(([k, x]) => <React.Fragment key={k}><dt>{k}</dt><dd>{x}</dd></React.Fragment>)}</dl></div>
              : <div className="vs-card warn"><div className="vs-t">{c.state}</div>
                <dl className="vs-kv">{kv.map(([k, x]) => <React.Fragment key={k}><dt>{k}</dt><dd>{x}</dd></React.Fragment>)}</dl></div>}
      {note && <div className="vs-note bad">{note}</div>}
      {!dd && <div className="vs-note">想对着就扫、不用拍照：把这个页面的网址发到<b>钉钉</b>里再点开，会用钉钉自带的扫一扫。现在这个浏览器只能拍照识别。</div>}
      <div className="vs-sec"><b>本次已扫 {S.hist.length} 张</b>
        <span className="vs-cnt">{Object.entries(S.bySubj).map(([k, n]) => <span key={k} className={'vs-pill tone-' + tone(k)}>{k} {n}</span>)}</span>
        {S.hist.length > 0 && <button className="vs-lnk" onClick={() => { S.setHist([]); S.setCur(null) }}>清空</button>}</div>
      <div className="vs-list">
        {S.hist.map((h, i) => { const x = (h.vouchers || [])[0]; return <div key={h.inst} className="vs-row" onClick={() => S.setCur({ ...h, dup: false })}>
          <span className={'vs-bar2 tone-' + tone(x ? x.subject : h.subject)} />
          <div className="l"><b>{x ? '记-' + x.vno : '还没做账'}</b><span>{x ? `${x.subject} · ${x.month ? Number(String(x.month).slice(5)) + '月' : ''}${(x.operator || x.maker) ? ' · ' + (x.operator || x.maker) : ''}` : h.subject || ''}</span></div>
          <div className="r"><span>{h.payee}</span><b>{money(h.amount)}</b></div></div> })}
        {!S.hist.length && <div className="vs-none">还没扫。扫过的会排在这里，按主体计数。</div>}
      </div>
      <button className="vs-lnk mid" onClick={() => setManual(!manual)}>{manual ? '收起' : '扫不了？输入审批编号'}</button>
      {manual && <div className="vs-manual"><input value={S.v} onChange={e => S.setV(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') S.go() }}
        placeholder="付款单上的 20 位审批编号" inputMode="numeric" autoComplete="off" />
        <button disabled={S.busy || !S.v.trim()} onClick={S.go}>查</button></div>}
      <div className="vs-foot">{user?.name} · 财务核算工作台</div>
    </main>
    {/* 底部操作区(V2.805，用户「那几个连续扫，是不是可以优化一下」)：上面一排是开关(连续扫 / 读出来)和备用的拍照，下面一个通栏主按钮 */}
    <footer className="vs-bottom">
      <div className="vs-opts">
        {dd && <button className={'vs-sw' + (auto ? ' on' : '')} role="switch" aria-checked={auto} onClick={() => setAuto(!auto)}><i><span /></i>连续扫</button>}
        <button className={'vs-sw' + (S.say ? ' on' : '')} role="switch" aria-checked={S.say} onClick={() => S.setSay(!S.say)}><i><span /></i>读出来</button>
        <span style={{ flex: 1 }} />
        {dd && <button className="vs-cam" disabled={S.busy} onClick={photo}><IcCam />拍照识别</button>}
      </div>
      {dd && auto && <div className="vs-tip">扫到一张会自动接着扫下一张；想停，在扫码界面点返回</div>}
      {dd
        ? <button className="vs-go" disabled={S.busy} onClick={ddScan}><IcScan />{S.busy ? '正在查…' : auto ? (S.hist.length ? '继续连续扫' : '开始连续扫') : S.hist.length ? '扫下一张' : '扫一扫'}</button>
        : <button className="vs-go" disabled={S.busy} onClick={photo}><IcCam />{S.busy ? '识别中…' : S.hist.length ? '拍下一张' : '拍二维码'}</button>}
    </footer>
  </div>
}

function Detail({ inst, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => { setD(null); setErr(''); voucherPreview(inst).then(setD).catch(e => setErr(e.message)) }
  useEffect(load, [inst])
  const ovr = on => {
    let note = ''
    if (on) { note = window.prompt('纸质件还没到，确定先做账？写一句原因（会留痕）', ''); if (note === null) return }
    setBusy(true)
    voucherPaperOverride(inst, on, note).then(() => { load(); onChanged() }).catch(e => alert(e.message)).finally(() => setBusy(false))
  }
  const [posting, setPosting] = useState(null)   // 写金蝶结果 {ok, msg, steps}
  const post = () => {
    if (!window.confirm(`保存到金蝶：
① 系统提交并审核这张金蝶付款单（审核人显示「系统操作员」）
② 金蝶自动生成付款凭证后，往里补红冲/更正/核销分录、改支付摘要
③ 提交这张凭证（进审核人的待审列表）
凭证不审核，留给你在金蝶核对后审核。${(d?.xbook || []).length ? `

⚠ 这张的计提原来记在别的主体：${d.xbook.map(x => `${x.short} 记-${x.vno}`).join('、')}。
本张凭证会在${d.req.subject}补提后核销；
④ 系统在${d.xbook.map(x => x.short).join('、')}账簿新建一张红冲凭证（原计提全额取负）并提交，同样不审核。
写完请打印计提更正单（两张：① 红冲 ② 补提），两边各附一张。` : ''}

确定？`)) return
    setPosting({ busy: true })
    voucherPost(inst).then(r => { setPosting({ ok: true, ...r }); load(); onChanged() })
      .catch(e => setPosting({ ok: false, msg: e.message }))
  }
  const [xbusy, setXbusy] = useState(false)       // 补做原主体红冲
  const [xmsg, setXmsg] = useState(null)
  const xred = () => {
    if (!window.confirm(`在${(d?.xbook || []).map(x => x.short).join('、')}账簿新建红冲凭证（原计提全额取负）并提交，不审核。已经有红冲的不会重复建。\n\n确定？`)) return
    setXbusy(true); setXmsg(null)
    voucherPostXred(inst).then(r => { setXmsg({ ok: true, text: (r.steps || []).join(' → ') }); load(); onChanged() })
      .catch(e => setXmsg({ ok: false, text: e.message })).finally(() => setXbusy(false))
  }
  const lines = d?.voucher?.lines || []
  const rc = d ? rateCheck(d.accruals, d.invoices) : []
  const accSum = k => r2((d?.accruals || []).reduce((s, a) => s + (a[k] || 0), 0))
  const invSum = k => r2((d?.invoices || []).reduce((s, i) => s + (i[k] || 0), 0))
  return (
    <div className="lv-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="lv-dlg" role="dialog" aria-label="付款做账">
        <div className="lv-dh">
          <div className="lv-title">
            {d ? <><span className="cd">{d.req.code}</span><b>{d.req.payee}</b><span className="sep">·</span>{d.req.subject}
              <span className="sep">·</span><b className="mono">{money(d.req.amount)}</b><span className="sep">·</span>{d.req.period} 账单</> : <b>付款做账</b>}
          </div>
          <button className="lv-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        {err && <div className="lv-msg bad">{err}</div>}
        {!d && !err && <div className="lv-empty">读金蝶计提凭证、发票管家票夹…</div>}
        {d && <>
          <div className="lv-meta">
            <span>审批 <span className="mono">{d.req.bid}</span> · {d.req.applicant}</span>
            <span>{d.req.paid ? <>付款 {d.req.paid}{d.req.bank && <> · {d.req.bank}</>}</> : '未付款'}</span>
            {d.req.folder && <a href={`#/invaudit?folder=${d.req.folder}`} target="_blank" rel="noopener">发票管家票夹 #{d.req.folder} ↗</a>}
            <span style={{ flex: 1 }} />
            <span className={'lv-pill ' + (ST[d.req.status] || [])[1]}>{(ST[d.req.status] || [d.req.status])[0]}</span>
            {d.req.paper_ovr
              ? <span className="lv-ovr">已手动放行（{d.req.paper_ovr.by} {d.req.paper_ovr.at}{d.req.paper_ovr.note ? '：' + d.req.paper_ovr.note : ''}）<button className="lnk" disabled={busy} onClick={() => ovr(false)}>撤销</button></span>
              : d.req.status === 'paper' && <button className="btn sm" disabled={busy} onClick={() => ovr(true)}>纸质件没到，先做账</button>}
          </div>

          {d.req.posted && <div className="lv-verdict ok"><span className="lv-pill ok">已写入金蝶</span>
            付款单 {d.req.posted.bill_no} 已审核 · 凭证 <b>记-{d.req.posted.vno}</b>（{d.req.posted.book}）已补分录，借 {money(d.req.posted.dr)} = 贷 {money(d.req.posted.cr)} · {d.req.posted.by} {d.req.posted.at} · 凭证请在金蝶核对后审核</div>}
          {posting && !posting.busy && <div className={'lv-msg ' + (posting.ok ? 'okb' : 'bad')}>{posting.ok ? `已写入：记-${posting.vno}（${(posting.steps || []).join(' → ')}）` : posting.msg}
            {posting.ok && (d.adjust || []).length > 0 && <> · <button className="lnk" onClick={() => printAdjust([d], `计提更正单 ${d.req.payee}`)}>打印计提更正单（贴钉钉单据后）</button></>}</div>}
          {d.kind && KIND[d.kind] && <div className={'lv-verdict ' + KIND[d.kind][1]}><span className={'lv-pill ' + KIND[d.kind][1]}>{KIND[d.kind][0]}</span>{d.kind_text}
            {(d.adjust || []).length > 0 && <><span style={{ flex: 1 }} /><button className="btn sm" title="打印后贴在钉钉付款单据后面" onClick={() => printAdjust([d], `计提更正单 ${d.req.payee}`)}>打印计提更正单</button></>}</div>}
          {(d.xbook || []).map(x => <div key={x.short + x.vno} className={'lv-msg ' + (x.reversed ? 'okb' : 'warn')}>
            {x.short} 记-{x.vno}（{money(x.gross)}）的红冲：{x.reversed
              ? <b>已做 · {x.reversed.month} 月 记-{x.reversed.vno}</b>
              : d.req.posted
                ? <><b>还没做</b>——保存到金蝶时这一步没成。<button className="btn sm" style={{ marginLeft: 8 }} disabled={!!xbusy} onClick={xred}>{xbusy ? '建凭证中…' : `补做红冲（在${x.short}账簿建红冲凭证并提交）`}</button></>
                : <><b>还没做</b>——点「保存到金蝶」时系统一并在{x.short}账簿建红冲凭证并提交（不审核）。计提更正单出两张：「① {x.short} 红冲」「② {d.req.subject} 补提」。</>}
            {x.reversed && x.reversed.sys && <span className="dim">　系统建 · {x.reversed.by} {x.reversed.at}{x.reversed.submitted === false ? ' · 提交没成功，请到金蝶手动提交' : ' · 已提交，等人审核'}</span>}</div>)}
          {xmsg && <div className={'lv-msg ' + (xmsg.ok ? 'okb' : 'bad')}>{xmsg.text}</div>}
          {d.plan.msgs.filter(m => m !== d.kind_text).length > 0 &&
            <ul className="lv-notes">{d.plan.msgs.filter(m => m !== d.kind_text).map((m, i) => <li key={i}>{m}</li>)}</ul>}

          <div className="lv-sec">① 按税率核对 <span className="dim">计提按税率合计，红冲更正后要等于同税率的发票</span></div>
          <table className="lv-t lv-rc">
            <thead><tr><th>税率</th><th className="num">计提</th><th className="num">更正后</th><th className="num">发票</th><th></th></tr></thead>
            <tbody>{rc.map(x => {
              const ok = Math.abs(x.fixed - x.inv) < 0.005
              return <tr key={x.r}><td><b>{pct(x.r)}</b></td><td className="num">{money(x.acc)}</td>
                <td className="num">{Math.abs(x.acc - x.fixed) < 0.005 ? <span className="dim">同左</span> : <b>{money(x.fixed)}</b>}</td>
                <td className="num">{money(x.inv)}</td><td>{ok ? <span className="ok">✓ 对上</span> : <span className="bad">差 {money(r2(x.inv - x.fixed))}</span>}</td></tr>
            })}</tbody>
          </table>

          <div className="lv-sec">② 计提凭证 <span className="dim">{d.accruals.length} 张 · 金蝶 {d.req.period}{d.accruals.some(a => a.from) ? ' · 含记在别的主体账上的' : ''}</span></div>
          <table className="lv-t lv-fix">
            <colgroup><col style={{ width: 110 }} /><col /><col style={{ width: 130 }} /><col style={{ width: 120 }} /><col style={{ width: 110 }} /><col style={{ width: '30%' }} /></colgroup>
            <thead><tr><th>凭证</th><th>费用项目</th><th className="num">含税</th><th>税率</th><th className="num">暂估税</th><th>处理</th></tr></thead>
            <tbody>
              {d.accruals.map(a => <tr key={a.vno} title={a.expl}>
                <td className="mono">{a.month}/{a.vno}#{a.from && <div className="dim" style={{ fontFamily: 'inherit' }}>在{a.from}账上</div>}</td><td>{a.fee || '—'}</td>
                <td className="num">{money(a.gross)}</td>
                <td className="nw">{a.mode === 'rate' ? <>{pct(a.rate)} → <b className="bad">{pct(a.new_rate)}</b></> : pct(a.rate)}</td>
                <td className="num">{money(a.tax)}</td>
                <td>{a.mode === 'hx' ? <span className="ok">核销</span> : a.mode === 'move' ? <><span className="bad">补提到本主体 + 核销</span><span className="dim"> · {a.why}</span></>
                  : a.mode ? <><span className="bad">红冲 + 更正</span><span className="dim"> · {a.why}</span></> : <span className="dim">—</span>}</td>
              </tr>)}
              {!d.accruals.length && <tr><td colSpan="6" className="lv-empty">金蝶本期没找到这家的计提凭证</td></tr>}
              <tr className="tot"><td colSpan="2">合计</td><td className="num">{money(accSum('gross'))}</td><td></td><td className="num">{money(accSum('tax'))}</td><td></td></tr>
            </tbody>
          </table>

          <div className="lv-sec">③ 发票 <span className="dim">{d.invoices.length} 张 · 发票管家</span></div>
          <table className="lv-t lv-fix">
            <colgroup><col style={{ width: 200 }} /><col /><col style={{ width: 130 }} /><col style={{ width: 120 }} /><col style={{ width: 110 }} /><col style={{ width: '30%' }} /></colgroup>
            <thead><tr><th>发票号</th><th>类型</th><th className="num">含税</th><th>税率</th><th className="num">税额</th><th>纸质件 / 做账</th></tr></thead>
            <tbody>
              {d.invoices.map(i => <tr key={i.id}>
                <td className="mono">{i.number}</td><td className="ell" title={i.type}>{i.type}</td>
                <td className="num">{money(i.gross)}</td><td>{i.rate}{i.deduct === false && <span className="warn"> · 不抵扣</span>}</td><td className="num">{money(i.tax)}</td>
                <td>{i.paper ? <span className="ok">纸质件已到</span> : <span className="warn">纸质件未到</span>}
                  {i.booked && <span className="dim"> · 已做账 {(i.vouchers || []).join('、')}</span>}</td>
              </tr>)}
              {!d.invoices.length && <tr><td colSpan="6" className="lv-empty">票夹里还没有发票</td></tr>}
              <tr className="tot"><td colSpan="2">合计</td><td className="num">{money(invSum('gross'))}</td><td></td><td className="num">{money(invSum('tax'))}</td><td></td></tr>
            </tbody>
          </table>

          <div className="lv-sec">④ 凭证预览 <span className="dim">{d.voucher.book} · {d.voucher.date} · 写入时并进金蝶付款单自动生成的那张凭证（支付两行在前），核销摘要里的 □ 填该凭证号</span>
            <span style={{ flex: 1 }} />
            {!d.req.posted && d.plan.status === 'ok' && (d.req.status === 'ready'
              ? <button className="btn btn-pri" disabled={!!posting?.busy || !d.req.bill_id} title={d.req.bill_id ? '' : '没有金蝶付款单'} onClick={post}>{posting?.busy ? '写金蝶中…（约半分钟）' : '保存到金蝶'}</button>
              : <span className="dim">（{ST[d.req.status]?.[0]}，还不能写金蝶）</span>)}</div>
          {d.plan.status !== 'ok'
            ? <div className="lv-msg bad">计提和发票对不上，这张先人工处理（原因见上），不出凭证。</div>
            : <table className="lv-t lv-v">
              <colgroup><col style={{ width: 34 }} /><col /><col style={{ width: 170 }} /><col style={{ width: 120 }} /><col style={{ width: 120 }} /><col style={{ width: '32%' }} /></colgroup>
              <thead><tr><th>#</th><th>摘要</th><th>科目</th><th className="num">借方</th><th className="num">贷方</th><th>核算维度</th></tr></thead>
              <tbody>{(() => {
                const out = []
                let blk = '', base = ''
                lines.forEach((l, i) => {
                  if (l.block !== blk) {
                    blk = l.block; base = ''
                    out.push(<tr key={'h' + i} className={'blk ' + BLOCK_CLS[l.block]}><td colSpan="6">{{ 红冲: '一、红冲', 更正: (d.xbook || []).length ? '二、更正（补提到本主体）' : '二、更正', 核销: '三、核销（暂估转待认证）', 支付: '四、支付' }[l.block]}</td></tr>)
                  }
                  // 同一块里摘要相同写「同上」；核销待认证行＝发票号 + 共同摘要
                  const m = l.expl.match(/^(\d{8,20})(核销.*)$/)
                  let txt
                  if (m && base === m[2]) txt = <><span className="mono">{m[1]}</span> <span className="dim">+ 同上</span></>
                  else if (!m && base === l.expl) txt = <span className="dim">同上</span>
                  else { txt = l.expl; base = m ? m[2] : l.expl }
                  out.push(<tr key={i}><td className="dim">{i + 1}</td><td className="expl" title={l.expl}>{txt}</td>
                    <td className="nw">{l.acct} {l.acct_name}</td>
                    <td className="num">{l.dr ? money(l.dr) : ''}</td><td className="num">{l.cr ? money(l.cr) : ''}</td>
                    <td className="dims">{dimLine(l.dims)}</td></tr>)
                })
                return out
              })()}
                <tr className="tot"><td colSpan="3">合计　{Math.abs(d.voucher.dr - d.voucher.cr) < 0.005 ? <span className="ok">借贷平衡 ✓</span> : <span className="bad">借贷不平</span>}</td>
                  <td className="num">{money(d.voucher.dr)}</td><td className="num">{money(d.voucher.cr)}</td><td></td></tr>
              </tbody>
            </table>}
        </>}
      </div>
    </div>
  )
}

export default function LogisticsVoucher() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [f, setF] = useState('ready')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const [plans, setPlans] = useState({})        // inst → {kind, text}
  const [sel, setSel] = useState({})            // 批量做账勾选 inst → true
  const [run, setRun] = useState(null)          // 批量进度 {i, n, cur, done:[{inst, label, ok, msg, vno}]}
  const [kf, setKf] = useState('')
  const [bm, setBm] = useState('')              // 装订打印的凭证月份，空=最新一个月
  const [scan, setScan] = useState(false)       // 扫码查凭证弹窗
  const load = () => voucherList().then(r => {
    const rs = r.rows || []
    setRows(rs); setErr('')
    // 做账类型：有账单月、有发票的才算；分几批取，先出的先显示
    const ids = rs.filter(x => x.period && x.n_inv).map(x => x.inst)
    const batches = []
    for (let i = 0; i < ids.length; i += 5) batches.push(ids.slice(i, i + 5))
    batches.reduce((p, b) => p.then(() => voucherPlans(b).then(o => setPlans(old => ({ ...old, ...(o.plans || {}) }))).catch(() => {})), Promise.resolve())
  }).catch(e => { setErr(e.message); setRows([]) })
  useEffect(() => { load() }, [])
  const cnt = useMemo(() => { const c = {}; (rows || []).forEach(r => { c[r.status] = (c[r.status] || 0) + 1 }); return c }, [rows])
  const canBatch = r => r.status === 'ready' && !r.posted && plans[r.inst] && BATCH_KINDS.includes(plans[r.inst].kind)
  const label = r => `${r.sup_full || r.carrier} · ${r.subject}`
  const picked = (rows || []).filter(r => sel[r.inst] && canBatch(r))
  const isRedo = r => REDO_KINDS.includes((plans[r.inst] || {}).kind)
  const pickedRedo = picked.filter(isRedo)
  const runBatch = async () => {
    const list = picked
    if (!list.length) return
    const tot = list.reduce((s, r) => s + (r.amount || 0), 0)
    const redo = list.filter(isRedo)
    const warn = redo.length
      ? `\n\n⚠ 其中 ${redo.length} 张要红冲更正（原计提整笔红冲，再按发票重新计提）：\n` +
        redo.map(r => `  · ${label(r)}：${(plans[r.inst] || {}).text || ''}`).join('\n') +
        `\n这几张写完后请在金蝶重点核对红冲、更正两段。`
      : ''
    if (!window.confirm(`批量保存到金蝶：${list.length} 张，付款合计 ${money(tot)}${warn}\n\n每张：系统审核金蝶付款单 → 往金蝶自动生成的付款凭证里补分录、改支付摘要 → 提交凭证。\n凭证不审核，留给你在金蝶核对后审核。\n一张约半分钟，中途可以离开本页。确定？`)) return
    const done = []
    for (let i = 0; i < list.length; i++) {
      const r = list[i]
      setRun({ i: i + 1, n: list.length, cur: label(r), done: [...done] })
      try {
        const x = await voucherPost(r.inst)
        done.push({ inst: r.inst, label: label(r), ok: true, vno: x.vno, redo: isRedo(r),
          msg: `记-${x.vno}${isRedo(r) ? '（含红冲更正，请在金蝶重点核对）' : ''}${(x.steps || []).some(t => t.startsWith('凭证提交失败')) ? '；凭证没提交成功，请在金蝶手动提交' : ''}` })
      } catch (e) {
        done.push({ inst: r.inst, label: label(r), ok: false, msg: e.message })
      }
    }
    setRun({ i: list.length, n: list.length, cur: '', done, end: true })
    setSel({})
    load()
  }
  const kcnt = useMemo(() => { const c = {}; Object.values(plans).forEach(p => { c[p.kind] = (c[p.kind] || 0) + 1 }); return c }, [plans])
  const shown = (rows || []).filter(r => (!f || r.status === f) && (!kf || (plans[r.inst] || {}).kind === kf) &&
    (!q || [r.carrier, r.payee, r.sup_full, r.subject, r.bid, r.code].some(x => String(x || '').includes(q))))
  // 装订：当前筛选下已有凭证号的单(系统写的 + 金蝶里已有的)，按凭证月份挑一个月打
  const bindAll = bindItems(shown, plans)
  const bindMonths = [...new Set(bindAll.map(x => x.month).filter(Boolean))].sort().reverse()
  const bmOn = bm && bindMonths.includes(bm) ? bm : (bindMonths[0] || '')
  const bindNow = bindAll.filter(x => x.month === bmOn)
  const batchInsts = run && run.end ? new Set(run.done.filter(x => x.ok).map(x => x.inst)) : null
  const bindBatch = batchInsts ? bindItems((rows || []).filter(r => batchInsts.has(r.inst)), plans) : []
  return (
    <div className="lv">
      <style>{CSS}</style>
      <div className="head"><div><div className="h-title">付款做账 · 物流请款单</div>
        <div className="h-sub">付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。计提取金蝶、发票取发票管家、维度更正取复核台登记。「保存到金蝶」＝系统审核付款单，再往金蝶自动生成的付款凭证里补分录并提交；凭证留给人在金蝶审核。</div></div></div>
      <div className="body">
        <div className="lv-bar">
          {ORDER.map(k => <button key={k} className={'lv-chip ' + ST[k][1] + (f === k ? ' on' : '')} title={ST[k][2]} onClick={() => setF(f === k ? '' : k)}>{ST[k][0]}<b>{cnt[k] || 0}</b></button>)}
          <button className={'lv-chip' + (!f ? ' on' : '')} onClick={() => setF('')}>全部<b>{(rows || []).length}</b></button>
          <span style={{ flex: 1 }} />
          <input type="search" placeholder="搜承运商/主体/审批编号" value={q} onChange={e => setQ(e.target.value)} />
          <button className="btn" title="用扫码枪扫纸质付款单右上角的二维码，看它是哪个主体、哪张凭证（装订用）" onClick={() => setScan(true)}>扫码查凭证</button>
          <button className="btn" onClick={load}>刷新</button>
        </div>
        <div className="lv-bar">
          <span className="dim">做账类型</span>
          {KIND_ORDER.map(k => <button key={k} className={'lv-chip ' + KIND[k][1] + (kf === k ? ' on' : '')} onClick={() => setKf(kf === k ? '' : k)}>{KIND[k][0]}<b>{kcnt[k] || 0}</b></button>)}
          {kf && <button className="lnk" onClick={() => setKf('')}>不限</button>}
        </div>
        {err && <div className="lv-msg bad">{err}</div>}
        <div className="lv-batch">
          <span>已勾选 <b>{picked.length}</b> 张{picked.length > 0 && <> · 付款合计 <b className="mono">{money(picked.reduce((s, r) => s + (r.amount || 0), 0))}</b></>}</span>
          <button className="btn btn-pri" disabled={!picked.length || (run && !run.end)} onClick={runBatch}>批量保存到金蝶</button>
          <span className="dim">能勾「可做账」且做账类型为 一致·只核销 / 尾差·红冲更正 / 需红冲更正 的；计提记错主体的单张做（两边各出一张凭证、两张更正单），金额不符的要人工</span>
          {run && <span className="lv-run">{run.end ? `完成：成功 ${run.done.filter(x => x.ok).length} 张，失败 ${run.done.filter(x => !x.ok).length} 张` : `正在写第 ${run.i}/${run.n} 张：${run.cur}…`}
            {run.end && run.done.some(x => x.ok && x.redo) && <button className="lnk" onClick={() => printAdjust(Promise.all(run.done.filter(x => x.ok && x.redo).map(x => voucherPreview(x.inst))), '本批计提更正单')}>打印本批计提更正单（{run.done.filter(x => x.ok && x.redo).length} 张）</button>}
            {run.end && bindBatch.length > 0 && <><button className="lnk" onClick={() => printHtml('本批装订对照清单', BIND_CSS, bindListHtml(bindBatch))}>打印本批装订清单（{bindBatch.length} 张）</button>
              <button className="lnk" onClick={() => printHtml('本批凭证号贴条', SLIP_CSS, slipHtml(bindBatch))}>凭证号贴条</button></>}
            {run.end && <button className="lnk" onClick={() => setRun(null)}>收起</button>}</span>}
        </div>
        {bindAll.length > 0 && <div className="lv-batch">
          <span>装订用 · 凭证月份</span>
          <select value={bmOn} onChange={e => setBm(e.target.value)}>{bindMonths.map(m => <option key={m} value={m}>{ymCn(m)}</option>)}</select>
          <span>已有凭证号 <b>{bindNow.length}</b> 张</span>
          <button className="btn" title="按主体分页、按凭证号排序；装订的同事对着纸质付款单上的钉钉审批编号找凭证号" onClick={() => printHtml(`装订对照清单 ${bmOn}`, BIND_CSS, bindListHtml(bindNow))}>打印装订对照清单</button>
          <button className="btn" title="一页 21 个，剪下来贴在纸质付款单右上角，不用手抄凭证号" onClick={() => printHtml(`凭证号贴条 ${bmOn}`, SLIP_CSS, slipHtml(bindNow))}>打印凭证号贴条</button>
          <span className="dim">按当前筛选（上面的状态/搜索）出；批量做完的凭证号都在这里，不用手写到付款单上</span>
        </div>}
        {pickedRedo.length > 0 && <div className="lv-msg warn">⚠ 勾选里有 <b>{pickedRedo.length}</b> 张要<b>红冲更正</b>（原计提整笔红冲，再按发票重新计提）：
          {pickedRedo.map(r => <span key={r.inst} className="lv-redo">{label(r)}<span className="dim">（{(plans[r.inst] || {}).text}）</span></span>)}
          建议先点开预览看一眼；写完后在金蝶重点核对红冲、更正两段。</div>}
        {run && run.done.length > 0 && <ul className="lv-runlist">{run.done.map(x => <li key={x.inst} className={x.ok ? 'ok' : 'bad'}>{x.ok ? '✓' : '✗'} {x.label}：{x.msg}</li>)}</ul>}
        <div className="tbl-wrap"><table className="lv-t lv-list">
          <thead><tr><th className="ck"><input type="checkbox" title="勾选当前列表里能批量做账的"
            checked={shown.some(canBatch) && shown.filter(canBatch).every(r => sel[r.inst])}
            onChange={e => { const on = e.target.checked; setSel(o => { const n = { ...o }; shown.filter(canBatch).forEach(r => { n[r.inst] = on }); return n }) }} /></th><th>主体</th><th>物流商</th><th>费用类型</th><th className="num">计提金额</th><th>税率</th><th>计提凭证</th>
            <th>审核结果</th><th>建议动作</th><th className="num">付款金额</th><th>付款单状态</th><th></th></tr></thead>
          <tbody>
            {rows === null && <tr><td colSpan="12" className="lv-empty">读取中…</td></tr>}
            {rows && !shown.length && <tr><td colSpan="12" className="lv-empty">没有</td></tr>}
            {shown.map(r => {
              const p = plans[r.inst]
              const acc = (p && p.acc) || []
              const calc = !!r.period && !!r.n_inv
              const pending = calc && !p
              // 一张请款单对应几张计提：费用类型/金额/税率/凭证号/审核结果 逐张上下对齐
              const stack = (fn, cls = '') => acc.length
                ? <div className={'ml ' + cls}>{acc.map(a => <div key={a.vno} title={a.expl}>{fn(a)}</div>)}</div>
                : <span className="dim">{pending ? '计算中…' : '—'}</span>
              const [klb, kcl] = p ? (KIND[p.kind] || [p.kind, 'neu']) : ['', '']
              const pay = r.paid
                ? (r.paid_voucher ? <>已记支付凭证 <b>{r.paid_voucher}</b></> : <>付款单 · {r.pay_st || '已生成'}<div className="dim">{r.paid}</div></>)
                : (r.dt_status === 'RUNNING' ? <span className="dim">钉钉审批中</span> : <span className="dim">已通过 · 待付款</span>)
              return <tr key={r.inst}>
                <td className="ck"><input type="checkbox" disabled={!canBatch(r)} checked={!!sel[r.inst] && canBatch(r)}
                  title={canBatch(r) ? (isRedo(r) ? '勾选批量做账（⚠ 这张要红冲更正）' : '勾选批量做账') : r.posted ? '已写金蝶' : r.status !== 'ready' ? '还不能做账' : '计提记错主体的点「凭证预览」单张做；金额不符的要人工，不进批量'}
                  onChange={e => setSel(o => ({ ...o, [r.inst]: e.target.checked }))} /></td>
                <td className="nw">{r.subject}<div className="code2">{r.book}</div></td>
                <td className="sup">{r.sup_full}<div className="code2">{r.code}</div></td>
                <td>{stack(a => <>{a.fee || '—'}{a.biz && <span className="dim"> · {a.biz}</span>}</>)}</td>
                <td className="num">{stack(a => money(a.gross))}</td>
                <td>{stack(a => a.mode === 'rate' ? <>{pct(a.rate)} → <b className="bad">{pct(a.new_rate)}</b></> : pct(a.rate))}</td>
                <td>{stack(a => <><span className="mono">{a.month}/{a.vno}#</span>{a.from && <span className="dim"> {a.from}</span>}</>)}</td>
                {(() => {
                  // 审核结果＝比对出来的事实，建议动作＝要做什么；逐张计提一行，和左边对齐。整单问题(主体错/金额不符…)各写一句。
                  if (!calc) return <><td><span className="dim">{!r.period ? '未认账单月' : '票夹没有发票'}</span></td><td><span className="dim">{!r.period ? '到账单核对总表认领月份' : '收票台补票'}</span></td></>
                  if (pending) return <><td><span className="dim">计算中…</span></td><td></td></>
                  if (['subj', 'manual', 'noacc', 'err'].includes(p.kind)) return <>
                    <td className="kd"><span className={KIND[p.kind] ? (KIND[p.kind][1] === 'bad' ? 'bad' : 'warn') : ''}>{KIND[p.kind]?.[0]}</span><div className="dim kt">{p.text}</div></td>
                    <td className="kd">{p.kind === 'subj' && p.auto ? <>{ACT.subjAuto}
                      {(p.xrev || []).map(x => <div key={x.vno} className={x.reversed ? 'ok' : 'warn'}>{x.short} 记-{x.vno} 红冲{x.reversed ? `已做（记-${x.reversed.vno}）` : '系统一并做'}</div>)}</> : ACT[p.kind]}</td></>
                  return <>
                    <td>{stack(a => a.mode === 'hx' ? <span className="ok">一致</span> : a.mode === 'tail' ? <span className="warn">一致 · 尾差 {money(a.tail)}</span>
                      : a.mode === 'rate' ? <span className="bad" title={a.why}>税率不符</span> : a.mode === 'fix' ? <span className="bad" title={a.why}>有计提更正</span> : '—')}</td>
                    <td>{stack(a => a.mode === 'hx' ? '核销' : a.mode === 'tail' ? <b className="warn">红冲 + 更正（尾差）</b> : a.mode ? <b className="bad">红冲 + 更正</b> : '—')}</td></>
                })()}
                <td className="num">{money(r.amount)}<div className="dim">{r.n_inv ? `发票 ${r.n_inv} 张 · ${money(r.inv_total)}` : '没有发票'}</div></td>
                <td className="nw">{pay}<div className="dim">纸质件 {r.n_inv ? `${r.n_paper}/${r.n_inv}` : '—'}{r.paper_ovr ? ' · 已放行' : ''}</div>
                  <span className={'lv-pill ' + ST[r.status][1]}>{ST[r.status][0]}</span>{r.posted && <div className="dim">已写金蝶 记-{r.posted.vno}</div>}</td>
                <td><button className="btn btn-pri" disabled={!r.period} onClick={() => setOpen(r.inst)}>{r.status === 'booked' ? '查看' : '凭证预览'}</button></td>
              </tr>
            })}
          </tbody>
        </table></div>
      </div>
      {open && <Detail inst={open} onClose={() => setOpen(null)} onChanged={load} />}
      {scan && <ScanBox onClose={() => setScan(false)} />}
    </div>
  )
}

const CSS = `
.lv .lv-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.lv .lv-bar input{font:inherit;font-size:12.5px;padding:5px 9px;border:1px solid var(--line-strong);border-radius:7px;background:var(--bg);color:var(--ink);width:200px}
.lv .lv-chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:12.5px;color:var(--ink)}
.lv .lv-chip b{font-variant-numeric:tabular-nums}
.lv .lv-chip.ok b{color:var(--green)}.lv .lv-chip.warn b{color:var(--amber)}.lv .lv-chip.bad b{color:var(--red)}.lv .lv-chip.done b{color:var(--accent)}
.lv .lv-chip.on{background:var(--accent);border-color:var(--accent);color:#fff}.lv .lv-chip.on b{color:#fff}
.lv .lv-t{width:100%;border-collapse:collapse;font-size:12.5px}
.lv .lv-t th{text-align:left;font-size:11.5px;color:var(--ink-3);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap;background:var(--bg-sub)}
.lv .lv-t td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.lv .lv-t .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.lv .lv-t tr.tot td{font-weight:700;background:var(--bg-sub)}
.lv .dim{color:var(--ink-3);font-size:11.5px}.lv .ok{color:var(--green)}.lv .warn{color:var(--amber)}
.lv .mono{font-family:var(--font-mono)}
.lv .ell{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lv .lv-pill{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:999px;white-space:nowrap}
.lv .lv-pill.ok{background:var(--green-bg);color:var(--green)}.lv .lv-pill.warn{background:var(--amber-bg);color:var(--amber)}
.lv .lv-pill.bad{background:var(--red-bg);color:var(--red)}.lv .lv-pill.neu{background:var(--gray-bg);color:var(--ink-2)}.lv .lv-pill.done{background:var(--accent-soft);color:var(--accent)}
.lv .lv-empty{color:var(--ink-3);text-align:center;padding:18px}
.lv .lv-msg{padding:9px 12px;border-radius:8px;font-size:12.5px;margin:8px 0}.lv .lv-msg.bad{background:var(--red-bg);color:var(--red)}.lv .lv-msg.okb{background:var(--green-bg);color:var(--green)}
.lv .lv-sec{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.lv .lv-mask{position:fixed;inset:0;z-index:1000;background:rgba(20,28,40,.38);display:flex;align-items:flex-start;justify-content:center;padding:28px 16px;overflow:auto}
.lv .lv-dlg{width:min(1100px,100%);background:var(--bg);border:1px solid var(--line);border-radius:12px;box-shadow:0 16px 44px rgba(20,28,58,.22);padding:16px 20px 22px}
.lv .lv-dh{display:flex;align-items:flex-start;gap:12px}
.lv .lv-title{flex:1;min-width:0;font-size:15.5px;line-height:1.5}.lv .lv-title .sep{color:var(--ink-3);margin:0 6px}
.lv .lv-verdict{display:flex;gap:10px;align-items:center;padding:10px 14px;border-radius:9px;font-size:13.5px;margin:4px 0 2px;background:var(--bg-sub);border:1px solid var(--line)}
.lv .lv-verdict.bad{background:var(--red-bg);border-color:var(--red-line)}.lv .lv-verdict.ok{background:var(--green-bg);border-color:var(--green-line)}.lv .lv-verdict.warn{background:var(--amber-bg);border-color:var(--amber-line)}
.lv .lv-notes{margin:6px 0 0;padding:0 0 0 20px;font-size:12.5px;color:var(--ink-2)}
.lv .lv-rc{width:auto;min-width:520px}
.lv .lv-fix,.lv .lv-v{table-layout:fixed}.lv .lv-fix td,.lv .lv-v td{overflow-wrap:anywhere}
.lv .btn.sm{padding:3px 10px;font-size:12px}
.lv .lv-x{border:0;background:none;font-size:16px;color:var(--ink-3);cursor:pointer}
.lv .lv-meta{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--ink-2);margin:10px 0}
.lv .lv-ovr{color:var(--amber)}.lv .lnk{border:0;background:none;color:var(--accent);cursor:pointer;font:inherit;font-size:12px;margin-left:6px}
.lv .lv-kind{font-size:13px;margin:6px 0}
.lv.vs{min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;background:var(--bg-sub);color:var(--ink);font-size:15px;-webkit-tap-highlight-color:transparent}
.lv.vs button{font:inherit;cursor:pointer}
.vs .vs-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:16px 16px 6px}.vs .vs-h1{font-size:21px;font-weight:800;letter-spacing:.5px}
.vs .vs-sub{font-size:12.5px;color:var(--ink-2);margin-top:3px;line-height:1.5}
.vs .vs-chip{flex:none;border:1px solid var(--line-strong);background:var(--bg);color:var(--ink-2);border-radius:999px;padding:6px 12px;font-size:12.5px;white-space:nowrap}
.vs .vs-chip.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:700}
.vs .vs-main{flex:1;padding:8px 16px 16px;display:flex;flex-direction:column;gap:12px}
.vs .vs-card{background:var(--bg);border:1px solid var(--line);border-radius:16px;padding:18px 18px 16px;box-shadow:0 2px 10px rgba(20,28,58,.05)}
.vs .vs-empty{display:flex;gap:16px;align-items:center;color:var(--ink-2);font-size:13.5px;line-height:1.7;border-style:dashed;box-shadow:none;background:transparent}.vs .vs-empty b{color:var(--ink);font-size:15px}
.vs .vs-wait{display:flex;gap:12px;align-items:center;justify-content:center;min-height:132px;color:var(--ink-2);font-size:16px}
.vs .vs-spin{width:22px;height:22px;border-radius:50%;border:3px solid var(--line-strong);border-top-color:var(--accent);animation:vsspin .8s linear infinite}@keyframes vsspin{to{transform:rotate(360deg)}}
.vs .vs-card.bad{background:var(--red-bg);border-color:var(--red-line);color:var(--red)}.vs .vs-card.warn{background:var(--amber-bg);border-color:var(--amber-line)}
.vs .vs-t{font-size:22px;font-weight:800;line-height:1.3}.vs .vs-msg{margin-top:6px;font-size:14px;line-height:1.6}
.vs .tone-b{--tc:var(--blue);--tb:var(--blue-bg);--tl:var(--blue-line)}.vs .tone-t{--tc:var(--teal);--tb:var(--teal-bg);--tl:#b5e8df}
.vs .tone-o{--tc:#b25c00;--tb:#fdf1e0;--tl:#f1d3a6}.vs .tone-g{--tc:var(--green);--tb:var(--green-bg);--tl:var(--green-line)}
.vs .vs-card.res{background:var(--tb);border-color:var(--tl);border-left:8px solid var(--tc);padding-left:16px}
.vs .vs-subj{display:inline-block;background:var(--tc);color:#fff;font-size:17px;font-weight:700;border-radius:8px;padding:3px 12px}
.vs .vs-vno{font-size:56px;font-weight:800;line-height:1.1;margin-top:8px;color:var(--ink);letter-spacing:1px}.vs .vs-mon{font-size:15px;font-weight:600;color:var(--tc);margin-top:2px}
.vs .vs-dup{display:inline-block;background:var(--red);color:#fff;font-size:13px;font-weight:700;border-radius:6px;padding:2px 10px;margin-bottom:8px}
.vs .vs-more{margin-top:8px;font-size:13.5px;color:var(--ink-2)}.vs .vs-more b{color:var(--ink)}
.vs .vs-tag{display:inline-block;margin-top:10px;border:1px solid var(--amber-line);background:var(--amber-bg);color:var(--amber);font-size:13px;font-weight:700;border-radius:6px;padding:2px 9px}
.vs .vs-kv{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;margin:14px 0 0;padding-top:12px;border-top:1px solid rgba(0,0,0,.08);font-size:13.5px}
.vs .vs-kv dt{color:var(--ink-2);white-space:nowrap}.vs .vs-kv dd{margin:0;word-break:break-all;font-variant-numeric:tabular-nums}
.vs .vs-note{font-size:12.5px;line-height:1.7;color:var(--ink-2);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:9px 12px}.vs .vs-note.bad{color:var(--red);background:var(--red-bg);border-color:var(--red-line)}
.vs .vs-sec{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px;font-size:14px}.vs .vs-cnt{display:flex;gap:6px;flex-wrap:wrap;flex:1}
.vs .vs-pill{background:var(--tb);color:var(--tc);border:1px solid var(--tl);border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600}
.vs .vs-lnk{border:0;background:none;color:var(--accent);font-size:13px;padding:4px 2px}.vs .vs-lnk.mid{align-self:center;margin-top:2px}
.vs .vs-list{display:flex;flex-direction:column;gap:8px}.vs .vs-none{color:var(--ink-3);font-size:13px;text-align:center;padding:14px 0}
.vs .vs-row{display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px 10px 0;overflow:hidden}
.vs .vs-bar2{align-self:stretch;width:5px;border-radius:0 4px 4px 0;background:var(--tc);margin:-10px 0}
.vs .vs-row .l{display:flex;flex-direction:column;min-width:0;flex:1}.vs .vs-row .l b{font-size:17px}
.vs .vs-row .l span{font-size:12px;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vs .vs-row .r{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;min-width:0;max-width:46%}
.vs .vs-row .r span{font-size:12px;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}.vs .vs-row .r b{font-size:14px;font-variant-numeric:tabular-nums}
.vs .vs-manual{display:flex;gap:8px}.vs .vs-manual input{flex:1;min-width:0;font:inherit;font-size:16px;padding:11px 12px;border:1px solid var(--line-strong);border-radius:10px;background:var(--bg);color:var(--ink)}
.vs .vs-manual button{border:0;background:var(--accent);color:#fff;border-radius:10px;padding:0 20px;font-weight:700}.vs .vs-manual button:disabled{opacity:.45}
.vs .vs-foot{text-align:center;color:var(--ink-3);font-size:11.5px;margin-top:auto;padding-top:8px}
.vs .vs-opts{display:flex;align-items:center;gap:18px;min-height:32px}
.vs .vs-sw{display:inline-flex;align-items:center;gap:8px;border:0;background:none;padding:4px 0;color:var(--ink-2);font-size:14px;white-space:nowrap}
.vs .vs-sw i{position:relative;flex:none;width:42px;height:25px;border-radius:999px;background:var(--line-strong);transition:background .15s}
.vs .vs-sw i span{position:absolute;top:2.5px;left:2.5px;width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:transform .15s}
.vs .vs-sw.on{color:var(--ink);font-weight:600}.vs .vs-sw.on i{background:var(--accent)}.vs .vs-sw.on i span{transform:translateX(17px)}
.vs .vs-cam{display:inline-flex;align-items:center;gap:5px;border:0;background:none;color:var(--accent);font-size:14px;padding:4px 0;white-space:nowrap}.vs .vs-cam svg{width:18px;height:18px}
.vs .vs-cam:disabled{opacity:.5}.vs .vs-tip{font-size:12px;color:var(--ink-2);line-height:1.5}
.vs .vs-bottom{position:sticky;bottom:0;display:flex;flex-direction:column;gap:9px;padding:10px 16px calc(10px + env(safe-area-inset-bottom));background:var(--bg);border-top:1px solid var(--line);box-shadow:0 -4px 16px rgba(20,28,58,.06)}
.vs.vs-ios .vs-bottom{padding-bottom:max(34px,calc(12px + env(safe-area-inset-bottom)))}
.vs .vs-go{width:100%;display:flex;gap:10px;align-items:center;justify-content:center;border:0;background:var(--accent);color:#fff;font-size:19px;font-weight:800;border-radius:14px;min-height:58px;letter-spacing:1px}
.vs .vs-go:active{background:var(--accent-strong)}.vs .vs-go:disabled{opacity:.55}
.vs .vs-side{flex:none;width:72px;white-space:nowrap;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;border:1px solid var(--line-strong);background:var(--bg);color:var(--ink-2);border-radius:14px;font-size:13px;font-weight:600}
.vs .vs-side small{font-size:11.5px;font-weight:400}.vs .vs-side.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.lv .lv-scanpage{padding:10px;min-height:100vh;background:var(--bg-sub)}.lv .lv-scanpage .lv-dlg{width:100%;max-width:720px;margin:0 auto;box-shadow:none;padding:14px 14px 18px}
.lv .sc-shot{display:flex;justify-content:center;align-items:center;width:100%;height:auto;min-height:60px;line-height:1.3;font-size:18px;padding:14px 10px;border-radius:12px;margin:10px 0 2px}.lv .sc-foot{text-align:center;color:var(--ink-2);font-size:12px;padding:10px}
@media (max-width:640px){.lv .lv-scan .sc-subj{font-size:22px}.lv .lv-scan .sc-big{font-size:34px}.lv .lv-scan .sc-v{gap:10px}.lv .lv-scan .sc-in{flex-wrap:wrap}
.lv .lv-scan .lv-t th:nth-child(4),.lv .lv-scan .lv-t td:nth-child(4),.lv .lv-scan .lv-t th:nth-child(7),.lv .lv-scan .lv-t td:nth-child(7),.lv .lv-scan .lv-t th:nth-child(6),.lv .lv-scan .lv-t td:nth-child(6){display:none}
.lv .lv-scan .lv-t th,.lv .lv-scan .lv-t td{width:auto!important;padding:7px 6px}}
.lv .lv-scan{max-width:900px}.lv .sc-in{display:flex;gap:10px;align-items:center;margin:10px 0}
.lv .sc-in input[type=text],.lv .sc-in input:not([type]){flex:1;font:inherit;font-size:15px;padding:10px 12px;border:2px solid var(--accent);border-radius:9px;background:var(--bg);color:var(--ink)}
.lv .sc-say{display:inline-flex;gap:5px;align-items:center;font-size:12.5px;color:var(--ink-2);white-space:nowrap}.lv .sc-hint{color:var(--ink-2);font-size:12.5px;padding:18px 4px}
.lv .sc-res{border-radius:12px;padding:16px 20px;margin:6px 0 4px;border:1px solid var(--line)}.lv .sc-res.ok{background:var(--green-bg);border-color:var(--green-line)}
.lv .sc-res.warn{background:var(--amber-bg);border-color:var(--amber-line)}.lv .sc-res.bad{background:var(--red-bg);border-color:var(--red-line);color:var(--red)}
.lv .sc-v{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}.lv .sc-v.more{margin-top:6px;opacity:.85}.lv .sc-subj{font-size:26px;font-weight:700}
.lv .sc-big{font-size:38px;font-weight:800;line-height:1.15}.lv .sc-res.warn .sc-big,.lv .sc-res.bad .sc-big{font-size:24px}.lv .sc-mon{font-size:16px;font-weight:600}
.lv .sc-meta{margin-top:8px;font-size:12.5px;color:var(--ink-2)}.lv .sc-meta .warn{color:var(--amber)}
.lv .lv-batch select{font:inherit;font-size:12.5px;padding:4px 8px;border:1px solid var(--line-strong);border-radius:7px;background:var(--bg);color:var(--ink)}
.lv .lv-batch+.lv-batch{margin-top:6px}
.lv .lv-batch{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:8px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg-sub);font-size:12.5px}
.lv .lv-msg.warn{background:var(--amber-bg);border:1px solid var(--amber-line);color:var(--ink);padding:8px 12px;border-radius:8px;font-size:12.5px;line-height:1.9}
.lv .lv-redo{display:inline-block;margin:0 10px 0 4px;font-weight:600}
.lv .lv-run{color:var(--accent);font-weight:600}.lv .lv-runlist{margin:6px 0;padding:8px 12px 8px 26px;font-size:12.5px;background:var(--bg-sub);border-radius:8px}
.lv .lv-runlist li.ok{color:var(--green)}.lv .lv-runlist li.bad{color:var(--red)}
.lv .lv-list th.ck,.lv .lv-list td.ck{width:34px;text-align:center}
.lv .lv-list td{vertical-align:middle}.lv .lv-list .code2{font-family:var(--font-mono);font-size:11px;color:var(--ink-3);margin-top:2px}.lv .lv-list .ml>div{height:21px;line-height:21px;white-space:nowrap}
.lv .lv-list td.nw{white-space:nowrap}.lv .lv-list td.sup{min-width:190px;max-width:240px}
.lv .cd{font-family:var(--font-mono);font-size:10.5px;color:var(--ink-2);background:var(--gray-bg);border-radius:3px;padding:0 4px;margin-right:5px;white-space:nowrap}.lv .bad{color:var(--red)}.lv .lv-list .kres{margin-top:4px}.lv .lv-list .lv-pill{margin-top:3px}.lv td.kd{max-width:280px}.lv td.kd .kt{white-space:normal;line-height:1.4;margin-top:3px}
.lv .lv-msgs{margin:6px 0 4px;padding:8px 12px 8px 28px;background:var(--amber-bg);color:var(--amber);border-radius:8px;font-size:12.5px}
.lv .lv-msgs.bad{background:var(--red-bg);color:var(--red)}
.lv .lv-sec{font-weight:700;font-size:13.5px;margin:16px 0 8px}
.lv .lv-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:1100px){.lv .lv-two{grid-template-columns:1fr}}
.lv .lv-v td.expl{line-height:1.45}.lv td.nw{white-space:nowrap}.lv .lv-v td.dims{font-size:11.5px;color:var(--ink-2);line-height:1.45}
.lv .lv-v tr.blk td{font-weight:700;font-size:12px;padding:6px 10px}
.lv .lv-v tr.b-red td{background:var(--red-bg);color:var(--red)}.lv .lv-v tr.b-fix td{background:var(--amber-bg);color:var(--amber)}
.lv .lv-v tr.b-hx td{background:var(--accent-soft);color:var(--accent)}.lv .lv-v tr.b-pay td{background:var(--green-bg);color:var(--green)}
`
