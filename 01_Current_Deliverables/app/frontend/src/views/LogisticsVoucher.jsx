// [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
// Description: 应付板块 › 物流对账 › 付款做账。一张物流请款单付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。
//   上：请款单清单（按能不能做账分组：可做账 / 纸质件未到 / 票不齐 / 发票≠请款 / 未付款 / 已做账）。
//   点开：计提 vs 发票逐张比（核销 / 红冲更正 + 原因）+ 整张凭证预览（带全部核算维度，借贷平衡）。
//   第一版只预览、不写金蝶；纸质件没到可手动放行（用户 2026-10-02 定）。
// V2.798 计提记错主体(用户 2026-10-05「这得出两张了，一张给星期零做账，一张给星期九做账」)：本张凭证里直接补提到本主体再核销、支付；
//   计提更正单出两张——① 原主体红冲 ② 本主体补提；页面显示那边红冲做了没有。
// V2.799(用户「也是系统做星期零」)：原主体的红冲凭证也由系统建——保存到金蝶时一并在那边账簿新建红冲凭证并提交(不审核)；没建成可点「补做红冲」。
import React, { useEffect, useMemo, useState } from 'react'
import { voucherList, voucherPreview, voucherPaperOverride, voucherPlans, voucherPost, voucherPostXred } from '../api.js'

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
            {run.end && <button className="lnk" onClick={() => setRun(null)}>收起</button>}</span>}
        </div>
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
