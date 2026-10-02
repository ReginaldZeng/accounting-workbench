// [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
// Description: 应付板块 › 物流对账 › 付款做账。一张物流请款单付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。
//   上：请款单清单（按能不能做账分组：可做账 / 纸质件未到 / 票不齐 / 发票≠请款 / 未付款 / 已做账）。
//   点开：计提 vs 发票逐张比（核销 / 红冲更正 + 原因）+ 整张凭证预览（带全部核算维度，借贷平衡）。
//   第一版只预览、不写金蝶；纸质件没到可手动放行（用户 2026-10-02 定）。
import React, { useEffect, useMemo, useState } from 'react'
import { voucherList, voucherPreview, voucherPaperOverride, voucherPlans, voucherPost } from '../api.js'

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
const ACT = { subj: '原主体红冲、本主体补提后再做', manual: '人工核对差额（补提 / 查发票）', noacc: '先计提', err: '刷新重试' }
const KIND_ORDER = ['hx', 'tail', 'redo', 'subj', 'manual', 'noacc']
const BLOCK_CLS = { 红冲: 'b-red', 更正: 'b-fix', 核销: 'b-hx', 支付: 'b-pay' }
const MODE = { hx: ['核销', 'ok'], rate: ['红冲+更正', 'bad'], fix: ['红冲+更正', 'bad'] }

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
  acc.forEach(a => { put(a.rate, 'acc', a.gross); put(a.mode === 'rate' || a.mode === 'fix' ? (a.new_rate ?? a.rate) : a.rate, 'fixed', a.gross) })
  inv.forEach(i => { const r = rateOf(i.rate); if (r != null) put(r, 'inv', i.gross) })
  return Object.values(g).sort((a, b) => a.r - b.r).map(x => ({ ...x, acc: r2(x.acc), fixed: r2(x.fixed), inv: r2(x.inv) }))
}

function dimLine(d) {
  return dimText(d).join(' · ')
}

// 计提调整单（V2.759，用户 2026-10-02「审核的时候就出来，打印后贴在钉钉单据后面」）：每张请款单一页 A4，
// 列出要红冲更正的计提：原计提 / 调整后 / 差额 + 原因，对应发票，会计处理，签字栏。数据来自预览接口的 adjust。
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const dimKey = l => [l.acct, l.fee_code, l.dept_code, l.biz_code, l.proj_code].join('|')
const expText = l => esc([l.acct + ' ' + (l.acct_name || ''), ...dimText(l)].join(' · '))
const MODE_CN = { rate: '改税率', fix: '改科目/维度/金额', tail: '尾差' }

// 横向 A4（用户 2026-10-02「可以做成横向的」）：每张计提一行，原计提 / 调整后 / 差额 并排；下方左发票、右会计处理
function adjustSheet(d) {
  const q = d.req, p = q.posted, adj = d.adjust || []
  const ym = String(d.voucher.date || '').slice(0, 7).split('-')
  const vno = p ? `${ym[0]}年${+ym[1]}月 记-${esc(p.vno)}` : '（保存到金蝶后生成）'
  const sgn = x => (x > 0 ? '+' : '') + money(x)
  const inv = d.invoices || [], acc = d.accruals || []
  const accG = r2(acc.reduce((s, a) => s + (a.gross || 0), 0)), invG = r2(inv.reduce((s, i) => s + (i.gross || 0), 0))
  const S = (k, w) => r2(adj.reduce((s, a) => s + (a[w][k] || 0), 0))
  const rows = adj.map((a, i) => {
    const dn = r2(a.new.net - a.old.net), dt = r2(a.new.tax - a.old.tax)
    const dimChg = a.old.exp.length !== a.new.exp.length || a.old.exp.some((l, j) => dimKey(l) !== dimKey(a.new.exp[j] || {}))
    const exps = dimChg ? `<tr class="sub"><td></td><td colspan="13"><b>费用分录调整：</b>${
      Array.from({ length: Math.max(a.old.exp.length, a.new.exp.length) }, (_, j) => {
        const o = a.old.exp[j], n = a.new.exp[j]
        return `<div>${o ? expText(o) + ' ' + money(o.dr) : '（无）'} → ${n ? expText(n) + ' ' + money(n.dr) : '（无）'}</div>`
      }).join('')}</td></tr>` : ''
    return `<tr><td class="c">${i + 1}</td><td class="nw">${esc(a.year)}年${esc(a.month)}月<br>记-${esc(a.vno)}</td>
      <td class="ex2">${esc(a.expl)}</td><td>${esc(MODE_CN[a.mode] || a.mode)}<div class="why">${esc(a.why || '')}</div></td>
      <td class="n">${money(a.old.gross)}</td><td class="c">${pct(a.old.rate)}</td><td class="n">${money(a.old.net)}</td><td class="n">${money(a.old.tax)}</td>
      <td class="n b">${money(a.new.gross)}</td><td class="c b">${pct(a.new.rate)}</td><td class="n b">${money(a.new.net)}</td><td class="n b">${money(a.new.tax)}</td>
      <td class="n d">${sgn(dn)}</td><td class="n d">${sgn(dt)}</td></tr>${exps}`
  }).join('')
  const tot = adj.length > 1 ? `<tr class="tt"><td colspan="4">合计</td><td class="n">${money(S('gross', 'old'))}</td><td></td><td class="n">${money(S('net', 'old'))}</td><td class="n">${money(S('tax', 'old'))}</td>
    <td class="n">${money(S('gross', 'new'))}</td><td></td><td class="n">${money(S('net', 'new'))}</td><td class="n">${money(S('tax', 'new'))}</td>
    <td class="n">${sgn(r2(S('net', 'new') - S('net', 'old')))}</td><td class="n">${sgn(r2(S('tax', 'new') - S('tax', 'old')))}</td></tr>` : ''
  const invTb = `<table class="t"><thead><tr><th>发票号码</th><th>类型</th><th class="c">税率</th><th class="n">含税金额</th><th class="n">税额</th></tr></thead><tbody>${
    inv.map(i => `<tr><td class="m">${esc(i.number)}</td><td>${esc(i.type)}</td><td class="c">${esc(i.rate)}</td><td class="n">${money(i.gross)}</td><td class="n">${money(i.tax)}</td></tr>`).join('')}
    <tr class="tt"><td colspan="3">合计 ${inv.length} 张</td><td class="n">${money(invG)}</td><td class="n">${money(r2(inv.reduce((s, i) => s + (i.tax || 0), 0)))}</td></tr></tbody></table>`
  const refs = adj.map(a => `记-${esc(a.vno)}`).join('、')
  return `<div class="sheet">
  <div class="top"><div></div><div><h1>计 提 调 整 单</h1><div class="sub">物流费用 · 付款时按发票红冲更正原计提</div></div>
    <div class="vno">调整凭证：<b>${vno}</b></div></div>
  <table class="hd"><colgroup><col style="width:7%"><col style="width:30%"><col style="width:7%"><col style="width:26%"><col style="width:7%"><col></colgroup>
  <tr><th>主体</th><td>${esc(q.subject_full || q.subject)}</td><th>物流商</th><td>${esc(q.payee)}（${esc(q.code)}）</td><th>账单期间</th><td>${esc(q.period)}</td></tr>
  <tr><th>钉钉审批</th><td>${esc(q.bid)} · 申请人 ${esc(q.applicant)}</td><th>付款金额</th><td>${money(q.amount)}${p ? ` · 付款单 ${esc(p.bill_no)}` : ''}</td><th>核销计提</th><td>${acc.length} 张 · 含税 ${money(accG)}（发票 ${money(invG)}）</td></tr></table>
  <h2>一、调整明细 <span>本次核销 ${acc.length} 张计提（${acc.map(a => `记-${esc(a.vno)}`).join('、')}），其中下列 ${adj.length} 张整笔红冲后更正；金额单位：元</span></h2>
  <table class="t adj"><colgroup><col style="width:3%"><col style="width:7%"><col><col style="width:10%"><col style="width:7.5%"><col style="width:4.5%"><col style="width:7.5%"><col style="width:6.5%"><col style="width:7.5%"><col style="width:4.5%"><col style="width:7.5%"><col style="width:6.5%"><col style="width:6.5%"><col style="width:6%"></colgroup>
  <thead><tr><th rowspan="2" class="c">#</th><th rowspan="2">原计提凭证</th><th rowspan="2">摘要</th><th rowspan="2">调整原因</th><th colspan="4" class="c g">原计提</th><th colspan="4" class="c g">调整后</th><th colspan="2" class="c g">差额</th></tr>
  <tr><th class="n">含税</th><th class="c">税率</th><th class="n">不含税</th><th class="n">税额</th><th class="n">含税</th><th class="c">税率</th><th class="n">不含税</th><th class="n">税额</th><th class="n">不含税</th><th class="n">税额</th></tr></thead>
  <tbody>${rows}${tot}</tbody></table>
  <div class="two"><div><h2>二、对应发票</h2>${invTb}</div>
    <div><h2>三、会计处理</h2><div class="ex">在付款凭证 ${vno} 中：<br>① 红冲原计提 ${refs}（整笔）；<br>② 按调整后金额重新计提（税额挂暂估进项税）；<br>③ 凭左列发票核销，暂估进项税转待认证；<br>④ 支付。</div></div></div>
  <table class="sign"><tr><td>制单：${esc(p ? p.by : '')}</td><td>复核：</td><td>审核：</td><td>日期：${p ? esc(String(p.at || '').slice(0, 10)) : ''}</td></tr></table>
  <div class="ft">财务核算工作台 · 付款做账 · 打印于 ${new Date().toLocaleString('zh-CN', { hour12: false })}${p ? '' : ' · 未写金蝶，凭证号待定'}</div></div>`
}

const SHEET_CSS = `@page{size:A4 landscape;margin:10mm 12mm}*{box-sizing:border-box}body{font:11.5px/1.5 "Microsoft YaHei","PingFang SC",sans-serif;color:#111;margin:0}
.sheet{page-break-after:always}.sheet:last-child{page-break-after:auto}
.top{display:grid;grid-template-columns:1fr auto 1fr;align-items:end;margin-bottom:8px}.vno{text-align:right;font-size:12.5px}
h1{text-align:center;font-size:20px;letter-spacing:2px;margin:0}.sub{text-align:center;color:#555}
h2{font-size:13px;margin:10px 0 5px;border-left:3px solid #111;padding-left:6px}h2 span{font-weight:400;color:#444;font-size:11px;margin-left:6px}
table{width:100%;border-collapse:collapse}.hd,.adj{table-layout:fixed}.hd th,.hd td,.t th,.t td{border:1px solid #333;padding:3px 5px;vertical-align:middle}
.hd th{background:#f2f2f2;text-align:left;font-weight:600}.t th{background:#f2f2f2;font-weight:600;text-align:left}.t th.g{background:#e6e6e6}
.n,.t th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.c,.t th.c{text-align:center}.m{font-family:Consolas,monospace}.nw{white-space:nowrap}
td.b{font-weight:700}td.d{font-weight:700;background:#fafafa}tr.tt td{font-weight:700}.ex2{font-size:11px}.why{color:#444;font-size:10.5px}
tr.sub td{font-size:10.5px;background:#fcfcfc}.adj tr{break-inside:avoid}
.two{display:grid;grid-template-columns:62% 1fr;gap:14px}.ex{margin:2px 0 4px;line-height:1.8}
.sign{margin-top:18px}.sign td{padding:6px 4px;width:25%}.ft{margin-top:6px;color:#777;font-size:10px;text-align:right}
.wait{padding:40px;text-align:center;color:#555}@media screen{body{background:#eee}.sheet{background:#fff;width:297mm;min-height:210mm;margin:12px auto;padding:10mm 12mm;box-shadow:0 1px 4px #0002}}`

// 先同步开窗(避免被拦截)，数据到了再写；ds 可以是数组或 Promise
function printAdjust(ds, title) {
  const w = window.open('', '_blank')
  if (!w) { alert('浏览器拦截了弹窗，请允许本站弹出窗口后再点'); return }
  w.document.write(`<!doctype html><meta charset="utf-8"><title>${esc(title || '计提调整单')}</title><style>${SHEET_CSS}</style><div class="wait">正在生成计提调整单…</div>`)
  w.document.close()
  Promise.resolve(ds).then(list => {
    const ok = list.filter(d => d && (d.adjust || []).length)
    w.document.body.innerHTML = ok.length ? ok.map(adjustSheet).join('') : '<div class="wait">没有需要调整的计提</div>'
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
凭证不审核，留给你在金蝶核对后审核。

确定？`)) return
    setPosting({ busy: true })
    voucherPost(inst).then(r => { setPosting({ ok: true, ...r }); load(); onChanged() })
      .catch(e => setPosting({ ok: false, msg: e.message }))
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
            {posting.ok && (d.adjust || []).length > 0 && <> · <button className="lnk" onClick={() => printAdjust([d], `计提调整单 ${d.req.payee}`)}>打印计提调整单（贴钉钉单据后）</button></>}</div>}
          {d.kind && KIND[d.kind] && <div className={'lv-verdict ' + KIND[d.kind][1]}><span className={'lv-pill ' + KIND[d.kind][1]}>{KIND[d.kind][0]}</span>{d.kind_text}
            {(d.adjust || []).length > 0 && <><span style={{ flex: 1 }} /><button className="btn sm" title="打印后贴在钉钉付款单据后面" onClick={() => printAdjust([d], `计提调整单 ${d.req.payee}`)}>打印计提调整单</button></>}</div>}
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

          <div className="lv-sec">② 计提凭证 <span className="dim">{d.accruals.length} 张 · 金蝶 {d.req.period}</span></div>
          <table className="lv-t lv-fix">
            <colgroup><col style={{ width: 110 }} /><col /><col style={{ width: 130 }} /><col style={{ width: 120 }} /><col style={{ width: 110 }} /><col style={{ width: '30%' }} /></colgroup>
            <thead><tr><th>凭证</th><th>费用项目</th><th className="num">含税</th><th>税率</th><th className="num">暂估税</th><th>处理</th></tr></thead>
            <tbody>
              {d.accruals.map(a => <tr key={a.vno} title={a.expl}>
                <td className="mono">{a.month}/{a.vno}#</td><td>{a.fee || '—'}</td>
                <td className="num">{money(a.gross)}</td>
                <td className="nw">{a.mode === 'rate' ? <>{pct(a.rate)} → <b className="bad">{pct(a.new_rate)}</b></> : pct(a.rate)}</td>
                <td className="num">{money(a.tax)}</td>
                <td>{a.mode === 'hx' ? <span className="ok">核销</span> : a.mode ? <><span className="bad">红冲 + 更正</span><span className="dim"> · {a.why}</span></> : <span className="dim">—</span>}</td>
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
                <td className="num">{money(i.gross)}</td><td>{i.rate}</td><td className="num">{money(i.tax)}</td>
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
                    out.push(<tr key={'h' + i} className={'blk ' + BLOCK_CLS[l.block]}><td colSpan="6">{{ 红冲: '一、红冲', 更正: '二、更正', 核销: '三、核销（暂估转待认证）', 支付: '四、支付' }[l.block]}</td></tr>)
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
          <span className="dim">能勾「可做账」且做账类型为 一致·只核销 / 尾差·红冲更正 / 需红冲更正 的；计提记错主体、金额不符的要人工</span>
          {run && <span className="lv-run">{run.end ? `完成：成功 ${run.done.filter(x => x.ok).length} 张，失败 ${run.done.filter(x => !x.ok).length} 张` : `正在写第 ${run.i}/${run.n} 张：${run.cur}…`}
            {run.end && run.done.some(x => x.ok && x.redo) && <button className="lnk" onClick={() => printAdjust(Promise.all(run.done.filter(x => x.ok && x.redo).map(x => voucherPreview(x.inst))), '本批计提调整单')}>打印本批计提调整单（{run.done.filter(x => x.ok && x.redo).length} 张）</button>}
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
                  title={canBatch(r) ? (isRedo(r) ? '勾选批量做账（⚠ 这张要红冲更正）' : '勾选批量做账') : r.posted ? '已写金蝶' : r.status !== 'ready' ? '还不能做账' : '计提记错主体/金额不符的要人工，不进批量'}
                  onChange={e => setSel(o => ({ ...o, [r.inst]: e.target.checked }))} /></td>
                <td className="nw">{r.subject}<div className="code2">{r.book}</div></td>
                <td className="sup">{r.sup_full}<div className="code2">{r.code}</div></td>
                <td>{stack(a => <>{a.fee || '—'}{a.biz && <span className="dim"> · {a.biz}</span>}</>)}</td>
                <td className="num">{stack(a => money(a.gross))}</td>
                <td>{stack(a => a.mode === 'rate' ? <>{pct(a.rate)} → <b className="bad">{pct(a.new_rate)}</b></> : pct(a.rate))}</td>
                <td>{stack(a => <span className="mono">{a.month}/{a.vno}#</span>)}</td>
                {(() => {
                  // 审核结果＝比对出来的事实，建议动作＝要做什么；逐张计提一行，和左边对齐。整单问题(主体错/金额不符…)各写一句。
                  if (!calc) return <><td><span className="dim">{!r.period ? '未认账单月' : '票夹没有发票'}</span></td><td><span className="dim">{!r.period ? '到账单核对总表认领月份' : '收票台补票'}</span></td></>
                  if (pending) return <><td><span className="dim">计算中…</span></td><td></td></>
                  if (['subj', 'manual', 'noacc', 'err'].includes(p.kind)) return <>
                    <td className="kd"><span className={KIND[p.kind] ? (KIND[p.kind][1] === 'bad' ? 'bad' : 'warn') : ''}>{KIND[p.kind]?.[0]}</span><div className="dim kt">{p.text}</div></td>
                    <td className="kd">{ACT[p.kind]}</td></>
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
