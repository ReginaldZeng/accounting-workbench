// [Change Log] Date:2026-10-24 Author:Kimi Version:V2.1004
// ① 数据接入改为“金额勾稽＋待办＋每页核对”；④ 改为请款单队列＋单张审票，金额/销购方/审核/后补统一形成结论。
import React, { useEffect, useState } from 'react'
import { reviewIntakeOverview, reviewReparse, invPaperMark, invAuditItemsApprove } from '../api.js'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const isZero = d => d != null && Math.abs(d) < 0.01
const kb = n => (n == null ? '' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB')

export const IK_V = {
  ok: ['读全了', 'ok'], unsplit: ['有钱没拆到费用项', 'warn'], gap: ['和账单合计对不上', 'bad'], maybe: ['可能漏读', 'warn'],
  nocheck: ['没有合计可比', 'neu'], skip: ['跳过', 'neu'], accrual: ['汇总页', 'neu'], err: ['解析不了', 'bad'],
  nobill: ['还没账单', 'neu'], noraw: ['没存原件', 'neu'], wait: ['还没来', 'neu'], nospec: ['没有取数说明', 'warn'], register: ['登记制·不导账单', 'neu'],
}
const ROLE_CN = { bill: '账单', stamp: '盖章版', invoice: '发票', review: '审核留档' }

export const IK_CSS = `
.lrv .lik-files{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;padding:12px 15px}
.lrv .lik-file{border:1px solid #DCE2E7;border-radius:8px;padding:10px 12px;display:grid;gap:3px;min-width:0;background:#fff}
.lrv .lik-file .h{font-size:11.5px;color:#788591}.lrv .lik-file .t{font-weight:600;word-break:break-all}.lrv .lik-file .s{font-size:12px;color:#5E6B78;word-break:break-all}
.lrv .lik-file .a{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.lrv .lik-hist{padding:4px 15px 12px;display:grid}
.lrv .lik-hist>div{display:grid;grid-template-columns:118px 90px minmax(0,1fr);gap:10px;padding:6px 0;border-bottom:1px dotted #E6EBEF;font-size:12.5px}
.lrv .lik-hist>div:last-child{border-bottom:0}.lrv .lik-hist .d{color:#788591;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.lrv .lik-lbl{font-size:11.5px;color:#788591;margin:4px 0}.lrv .lik-chips{display:flex;flex-wrap:wrap;gap:6px}
.lrv .lik-chip{display:inline-flex;gap:6px;align-items:baseline;border:1px solid #DCE2E7;border-radius:6px;padding:2px 8px;font-size:12px;background:#fff}
.lrv .lik-chip .v{font-family:ui-monospace,Consolas,monospace;color:#5E6B78}.lrv .lik-chip.use{border-color:#BFDCCB;background:#EEF7F1}.lrv .lik-chip.tot{border-color:var(--accent)}
.lrv .lik-chip.unread{border-color:#F0D9A8;background:#FDF3E2}.lrv .lik-chip.miss{border-style:dashed;color:#788591}.lrv .lik-chip.off{color:#788591}
.lrv tr.lik-more td{background:#FAFBFC}.lrv .lik-note{margin:0 15px 12px;padding:9px 12px;border-left:3px solid var(--accent);background:#F5FAFC;border-radius:0 8px 8px 0;font-size:12.5px;color:#3D4A57}
.lrv .lik-ov summary{cursor:pointer;padding:11px 15px;font-weight:600;font-size:13.5px}.lrv .lik-ov summary small{font-weight:400;color:#788591;margin-left:8px;font-size:12px}
.lrv .lik-verdict{border:1px solid #DCE2E7;border-radius:10px;background:#fff;overflow:hidden;margin-bottom:12px}
.lrv .lik-verdict .vb{display:flex;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid #E6EBEF;flex-wrap:wrap}
.lrv .vbadge{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:13.5px;padding:5px 12px;border-radius:999px;white-space:nowrap}
.lrv .vbadge.ok{color:#18784C;background:#EAF6EF;border:1px solid #BFE3CF}.lrv .vbadge.warn{color:#99600F;background:#FBF3E4;border:1px solid #EDD9AE}
.lrv .vbadge.bad{color:#B83232;background:#FBECEC;border:1px solid #F0C5C5}.lrv .vbadge.neu{color:#5E6B78;background:#F0F3F6;border:1px solid #DCE2E7}
.lrv .vbadge .dot{width:7px;height:7px;border-radius:50%;background:currentColor}.lrv .vb .vsub{color:#5E6B78;font-size:12.5px}.lrv .vb .sp{flex:1}
.lrv .vflow{display:grid;grid-template-columns:1fr 54px 1fr 54px 1fr;align-items:stretch;padding:12px 14px;gap:0}
.lrv .vnode{padding:8px 12px;border:1px solid #DCE2E7;border-radius:8px;display:grid;gap:2px;min-width:0;background:#fff}
.lrv .vnode .h{font-size:11.5px;color:#788591}.lrv .vnode .v{font-size:18px;font-weight:700;font-family:ui-monospace,Consolas,monospace}.lrv .vnode .s{font-size:11.5px;color:#5E6B78;word-break:break-all}
.lrv .vnode.money{border-color:#C9D8F8;background:#F7FAFF}.lrv .vnode.money .v{color:var(--accent)}
.lrv .vlink{display:flex;align-items:center;justify-content:center;color:#18784C;font-size:11px;position:relative;white-space:nowrap}
.lrv .vlink::before{content:"";position:absolute;top:50%;left:4px;right:4px;border-top:2px solid currentColor;opacity:.3}.lrv .vlink span{position:relative;background:#fff;padding:2px 4px;border-radius:4px}.lrv .vlink.bad{color:#B83232}
.lrv .lik-issues{display:flex;gap:8px;align-items:flex-start;padding:10px 14px;margin-bottom:12px;border:1px solid #EDD9AE;background:#FFFAEF;border-radius:9px;font-size:12.5px;line-height:1.6}
.lrv .lik-issues.ok{border-color:#BFDCCB;background:#F0F8F3}.lrv .lik-issues b{white-space:nowrap}.lrv .lik-issues ul{margin:0;padding-left:18px}.lrv .lik-issues.ok span{flex:1}
.lrv .lik-section{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.lrv .lik-section .sp{flex:1}.lrv .lik-section small{font-weight:400;color:#788591}
.lrv .lik-srcgrid{display:grid;grid-template-columns:1fr 1fr;gap:0}.lrv .lik-srcgrid>section{min-width:0}.lrv .lik-srcgrid>section+section{border-left:1px solid #E6EBEF}
.lrv .lik-srctitle{padding:10px 15px 0;font-size:12px;font-weight:700;color:#4A5763}.lrv .lik-empty{padding:18px 15px;color:#788591;font-size:12.5px}
.lrv .lik-req{border:1px solid #DCE2E7;border-radius:10px;background:#fff;overflow:hidden;margin-bottom:12px}.lrv .lik-req .ih{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 15px;border-bottom:1px solid #E6EBEF}
.lrv .lik-req .ih h4{margin:0;font-size:14px}.lrv .lik-req .ih .sp{flex:1}.lrv .lik-trio{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #E6EBEF;border-radius:8px;overflow:hidden;margin:12px 15px 0}
.lrv .lik-trio>div{padding:8px 12px;display:grid;gap:2px;border-right:1px solid #E6EBEF;min-width:0}.lrv .lik-trio>div:last-child{border-right:0}.lrv .lik-trio .h{font-size:11.5px;color:#788591}
.lrv .lik-trio .v{font-family:ui-monospace,Consolas,monospace;font-size:16px;font-weight:600}.lrv .lik-trio .s{font-size:11.5px;color:#5E6B78;word-break:break-all}.lrv .lik-trio .v.warnv{color:#99600F}
.lrv .lik-invwrap{padding:10px 15px 0}.lrv .lik-chk{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 16px;padding:12px 15px;font-size:12.5px}
.lrv .lik-chk>div{display:grid;grid-template-columns:18px minmax(0,1fr);gap:6px}.lrv .lik-chk .x{display:block;font-size:11.5px;color:#788591;word-break:break-all}.lrv .lik-mk{font-weight:700}.lrv .lik-mk.ok{color:var(--ok)}.lrv .lik-mk.bad{color:var(--bad)}.lrv .lik-mk.warn{color:var(--warn)}
.lrv .lik-sw{position:relative;display:inline-block;width:36px;height:22px;vertical-align:middle;cursor:pointer}.lrv .lik-sw input{opacity:0;width:0;height:0}.lrv .lik-sw i{position:absolute;inset:0;background:#CBD5DD;border-radius:999px;transition:.15s}
.lrv .lik-sw i::after{content:"";position:absolute;left:2px;top:2px;width:18px;height:18px;background:#fff;border-radius:50%;transition:.15s}.lrv .lik-sw input:checked+i{background:var(--ok)}.lrv .lik-sw input:checked+i::after{left:16px}.lrv .lik-sw input:focus-visible+i{outline:2px solid var(--accent);outline-offset:2px}
.lrv .lik-later{margin:12px 15px 0;border:1px dashed #EDD9AE;background:#FDF9F0;border-radius:8px;padding:10px 13px;display:grid;gap:6px;font-size:12.5px}.lrv .lik-later.done{border-style:solid;border-color:#BFDCCB;background:#F2F9F4}.lrv .lik-later .lr-h{display:flex;align-items:center;gap:8px;font-weight:600;flex-wrap:wrap}
.lrv .lia-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid #E6EBEF}.lrv .lia-summary>div{padding:9px 14px;border-right:1px solid #E6EBEF}.lrv .lia-summary>div:last-child{border-right:0}
.lrv .lia-summary small{display:block;color:#788591;font-size:11.5px}.lrv .lia-summary b{font-size:16px;font-variant-numeric:tabular-nums}.lrv .lia-work{display:grid;grid-template-columns:270px minmax(0,1fr);border:1px solid #DCE2E7;border-radius:10px;background:#fff;overflow:hidden;min-height:380px}
.lrv .lia-queue{border-right:1px solid #DCE2E7;background:#FAFBFC;min-width:0}.lrv .lia-qtools{padding:10px;border-bottom:1px solid #E6EBEF;display:grid;gap:7px}.lrv .lia-qtools input{width:100%;box-sizing:border-box}.lrv .lia-qfilter{display:flex;gap:5px}.lrv .lia-qfilter button{flex:1}
.lrv .lia-qlist{max-height:650px;overflow:auto}.lrv .lia-qitem{display:grid;width:100%;text-align:left;gap:4px;padding:11px 12px;border:0;border-bottom:1px solid #E6EBEF;background:transparent;cursor:pointer;color:inherit;transition:background .15s}
.lrv .lia-qitem:hover{background:#F0F5F8}.lrv .lia-qitem.on{background:#EAF2F7;box-shadow:inset 3px 0 var(--accent)}.lrv .lia-qitem .qt{display:flex;justify-content:space-between;gap:8px;font-weight:650}.lrv .lia-qitem .qs{display:flex;justify-content:space-between;gap:6px;color:#5E6B78;font-size:11.5px}.lrv .lia-qitem .qn{color:#99600F}
.lrv .lia-detail{min-width:0}.lrv .lia-detail .lik-req{border:0;border-radius:0;margin:0}.lrv .lia-voucher{border-top:1px solid #E6EBEF;padding:10px 14px}.lrv .lia-voucher>summary{cursor:pointer;font-weight:650;font-size:12.5px}
@media(max-width:900px){.lrv .lia-work{grid-template-columns:220px minmax(0,1fr)}.lrv .lik-trio{grid-template-columns:1fr}.lrv .lik-trio>div{border-right:0;border-bottom:1px solid #E6EBEF}.lrv .lik-chk{grid-template-columns:1fr}.lrv .lik-srcgrid{grid-template-columns:1fr}.lrv .lik-srcgrid>section+section{border-left:0;border-top:1px solid #E6EBEF}}
@media(max-width:700px){.lrv .vflow{grid-template-columns:1fr}.lrv .vlink{padding:8px 0}.lrv .vlink::before{display:none}.lrv .lia-work{grid-template-columns:1fr}.lrv .lia-queue{border-right:0;border-bottom:1px solid #DCE2E7}.lrv .lia-qlist{max-height:220px}.lrv .lia-summary{grid-template-columns:1fr 1fr}.lrv .lik-hist>div{grid-template-columns:1fr}}
`

function Chips({ s }) {
  const tot = s.total
  return <>
    <div className="lik-lbl">算钱的列（绿）· 账单自己的合计（蓝框，只拿来比）</div>
    <div className="lik-chips">
      {(s.money || []).filter(m => m.found).map((m, i) => <span key={'m' + i} className="lik-chip use">{m.name} <span className="v">{money(m.sum)}</span></span>)}
      {tot && tot.how !== 'self' && <span className="lik-chip tot" title={tot.how === 'spec' ? '取数说明配的合计列：拆项对不上时以它为准' : '按表头认出来的合计，只拿来比'}>{tot.name} <span className="v">{money(tot.sum)}</span>{tot.how === 'guess' ? '（认出来的）' : ''}</span>}
      {(s.money || []).filter(m => !m.found).map((m, i) => <span key={'x' + i} className="lik-chip miss">{m.name} · 本月账单没这列</span>)}
    </div>
    {(s.unread || []).length > 0 && <><div className="lik-lbl">像是钱、但没算进去的列</div><div className="lik-chips">{s.unread.map((u, i) => <span key={i} className={'lik-chip ' + (s.verdict === 'ok' ? 'off' : 'unread')}>{u.name} <span className="v">{money(u.sum)}</span></span>)}</div></>}
    {(s.others || []).length > 0 && <><div className="lik-lbl">有数字、不算钱的列</div><div className="lik-chips">{s.others.map((u, i) => <span key={i} className="lik-chip off">{u.name} <span className="v">{Number(u.sum).toLocaleString('zh-CN')}</span></span>)}</div></>}
  </>
}

function sheetSay(s) {
  if (s.verdict === 'ok') return s.total ? (s.total.how === 'self' ? '直接读的账单金额列' : '拆项加起来＝账单合计') : ''
  if (s.verdict === 'unsplit') return `按账单合计算了，其中 ${money(s.unsplit)} 没拆到费用项`
  if (s.verdict === 'gap') return `读出来的比账单合计${s.gap < 0 ? '少' : '多'} ${money(Math.abs(s.gap))}`
  if (s.verdict === 'maybe') return '没有合计列可比，有像是钱的列没读进来'
  if (s.verdict === 'nocheck') return '账单这页没有合计列可比'
  if (s.verdict === 'skip') return '取数说明没配这页'
  if (s.verdict === 'accrual') return `汇总页（按费用项）${s.n} 行`
  return ''
}

function Overview({ period }) {
  const [d, setD] = useState(null)
  const [open, setOpen] = useState(false)
  const load = () => { setD(null); reviewIntakeOverview(period).then(setD).catch(e => setD({ err: e.message, rows: [] })) }
  const rows = (d && d.rows) || []
  const ord = r => ({ gap: 0, maybe: 1, unsplit: 2, err: 0, nobill: 3, noraw: 4, nocheck: 5, ok: 6, register: 8, wait: 9, nospec: 10 }[r.status] ?? 7)
  return <details className="card lik-ov" onToggle={e => { const o = e.currentTarget.open; setOpen(o); if (o && !d) load() }}>
    <summary>{period} 各家接入情况<small>异常优先，展开查看</small></summary>
    {open && <>{!d && <div className="ovempty">读取中…</div>}{d && d.err && <div className="fxwarn" style={{ margin: '0 15px 12px' }}>{d.err}</div>}{d && !d.err && <div className="tw"><table className="ovtable">
      <thead><tr><th>承运商</th><th className="num">请款单</th><th className="num">账单原件</th><th className="num">读出来的</th><th className="num">和请款单差</th><th>钱怎么取</th><th>结论</th></tr></thead>
      <tbody>{[...rows].sort((a, b) => ord(a) - ord(b) || a.carrier.localeCompare(b.carrier, 'zh')).map(r => { const [lb, cl] = IK_V[r.status] || [r.status, 'neu']; return <tr key={r.carrier}>
        <td className="ovcar">{r.carrier}</td><td className="num">{r.n_req ? `${r.n_req} 张 · ${money(r.req_total)}` : <span className="dim">还没来</span>}</td><td className="num">{r.n_raw ? `${r.n_raw} 份` : <span className="dim">—</span>}</td><td className="num mono">{r.bill_total != null ? money(r.bill_total) : '—'}</td>
        <td className={'num mono ' + (r.diff == null ? '' : isZero(r.diff) ? 'diffneg' : 'diffpos')}>{r.diff == null ? '—' : isZero(r.diff) ? '0' : money(r.diff)}</td><td style={{ fontSize: 12 }}>{r.how}</td><td><span className={'pill ' + cl}>{lb}</span></td></tr> })}</tbody>
    </table></div>}{d && !d.err && <div className="ovfoot">「读出来的」＝复核台现存账单金额；「结论」＝按当前取数说明重新核对原件。<button className="lnk" onClick={load}>重读</button></div>}</>}
  </details>
}

export function IntakeStep({ data, period, locked, flash, onOpenBills, onChanged, onNext }) {
  const [more, setMore] = useState({})
  const [busy, setBusy] = useState('')
  const [onlyBad, setOnlyBad] = useState(false)
  if (!data) return <div className="card"><div className="ovempty">读账单原件、按取数说明检查中…</div></div>
  if (data.err) return <div className="card"><div className="fxwarn" style={{ margin: 15 }}>{data.err}</div></div>
  const files = data.files || [], reps = files.map(f => f.report).filter(Boolean)
  const totSheets = reps.flatMap(r => (r.sheets || []).filter(s => s.role === 'detail' && s.total))
  const dupRows = reps.reduce((a, r) => a + (r.dup_rows || 0), 0)
  const billSelfTotal = totSheets.length && !dupRows && reps.every(r => (r.sheets || []).filter(s => s.role === 'detail').every(s => s.total)) ? totSheets.reduce((a, s) => a + s.total.sum, 0) : null
  const [vl, vc] = IK_V[data.verdict] || ['', 'neu']
  const nDocs = (data.bills || []).reduce((a, b) => a + (b.n_doc || 0), 0)
  const link1 = billSelfTotal == null ? null : isZero(data.bill_total - billSelfTotal), link2 = data.diff == null ? null : isZero(data.diff)
  const issues = []
  if (!data.has_spec) issues.push('尚未配置取数说明，请先完成承运商取数配置')
  if (!(data.bills || []).length) issues.push('尚未解析账单，请上传账单或检查钉钉附件')
  if (data.verdict !== 'ok') issues.push(`原件核对：${vl || '尚未形成结论'}`)
  if (link1 === false) issues.push(`解析金额与账单原件合计相差 ${money(Math.abs(data.bill_total - billSelfTotal))}`)
  if (link2 !== true) issues.push(data.diff == null ? '账单与请款单暂时无法核对' : `账单与请款单相差 ${money(Math.abs(data.diff))}`)
  const ready = data.register || issues.length === 0
  const reparse = f => {
    if (!window.confirm(`用存的原件「${f.name || f.src || '账单'}」按现在的取数说明重新解析一遍？\n\n人工改过的主体/费用类型、写过的备注按单号保留；逐笔、逐单的结论会跟着重算。`)) return
    setBusy('re' + f.id)
    reviewReparse(data.carrier, period, f.src).then(r => { flash(`已重新解析：明细 ${r.detail} 行，合计 ${money(r.before)} → ${money(r.after)}${r.notes ? `，备注写回 ${r.notes} 张` : ''}`); onChanged() }).catch(e => flash('重新解析失败：' + e.message)).finally(() => setBusy(''))
  }
  const reqByRole = {}; (data.req_files || []).forEach(f => { (reqByRole[f.role || 'other'] = reqByRole[f.role || 'other'] || []).push(f) })
  const sheetRows = files.filter(f => f.report).flatMap(f => (f.report.sheets || []).map((s, i) => ({ f, s, i, bad: !['ok', 'skip', 'accrual'].includes(s.verdict) }))).sort((a, b) => Number(b.bad) - Number(a.bad)).filter(x => !onlyBad || x.bad)
  return <>
    <div className="lik-verdict">
      <div className="vb"><span className={'vbadge ' + (ready ? 'ok' : vc === 'neu' ? 'warn' : vc)}><span className="dot" />{data.register ? '登记制 · 无需接入账单' : ready ? '接入完成 · 金额已勾稽' : `接入待处理 · ${issues.length} 项`}</span><span className="vsub">先确认账单读全，再确认账单与请款一致</span><span className="sp" /><button className="btn sm" disabled={!!locked || !data.has_spec} title={locked || '上传、替换或删除账单'} onClick={onOpenBills}>上传 / 替换 / 删除账单</button></div>
      <div className="vflow">
        <div className="vnode"><span className="h">账单原件</span><span className="v">{files.length} 份</span><span className="s">{(data.bills || []).length ? `已解析 ${(data.bills || []).length} 份 · ${nDocs} 张单` : '还没有可核对原件'}</span></div>
        <div className={'vlink' + (link1 === false ? ' bad' : '')}><span>{link1 == null ? '待核对' : link1 ? '✓ 一致' : `✕ 差 ${money(Math.abs(data.bill_total - billSelfTotal))}`}</span></div>
        <div className="vnode money"><span className="h">解析入账金额</span><span className="v">{(data.bills || []).length ? money(data.bill_total) : '—'}</span><span className="s">按取数说明拆分费用</span></div>
        <div className={'vlink' + (link2 === false ? ' bad' : '')}><span>{link2 == null ? '待核对' : link2 ? '✓ 差 0' : `✕ 差 ${money(Math.abs(data.diff))}`}</span></div>
        <div className="vnode"><span className="h">请款单（钉钉）</span><span className="v">{data.req_total != null && (data.payreqs || []).length ? money(data.req_total) : '—'}</span><span className="s">{(data.payreqs || []).length} 张{Object.keys(reqByRole).length ? ' · ' + Object.entries(reqByRole).map(([k, xs]) => `${ROLE_CN[k] || '其他'} ${xs.length}`).join('、') : ''}</span></div>
      </div>
    </div>
    {!data.register && <div className={'lik-issues' + (ready ? ' ok' : '')}>{ready ? <><b>✓ 可以进入逐笔复核</b><span>账单解析、原件合计与请款金额均已核对。后续修改账单会重新计算本步骤状态。</span></> : <><b>待处理 {issues.length} 项</b><ul>{issues.map((x, i) => <li key={i}>{x}</li>)}</ul></>}</div>}
    <div className="card">
      <h3 className="lik-section">文件与来源 <small>原始凭据和附件独立核对</small></h3>
      <div className="lik-srcgrid">
        <section><div className="lik-srctitle">账单原件 · {files.length} 份</div>{files.length ? <div className="lik-files" style={{ gridTemplateColumns: '1fr' }}>{files.map(f => <div key={f.id} className="lik-file"><span className="h">{f.src || '账单'}</span><span className="t">{f.name || '（没记文件名）'}</span><span className="s">{kb(f.size)} · {f.by || '没记录'} {f.at}</span><span className="a"><button className="btn sm" disabled={!!locked || !!busy} onClick={() => reparse(f)}>{busy === 're' + f.id ? '解析中…' : '用原件重新解析'}</button></span></div>)}</div> : <div className="lik-empty">{data.register ? '登记制无需上传账单。' : data.has_spec ? '还没有账单原件，可手工上传或等待钉钉附件自动接入。' : '先配置取数说明，再上传账单。'}</div>}</section>
        <section><div className="lik-srctitle">钉钉请款单附件 · {(data.req_files || []).length} 份</div>{Object.keys(reqByRole).length ? <div className="lik-files" style={{ gridTemplateColumns: '1fr' }}><div className="lik-file"><span className="t">{Object.entries(reqByRole).map(([k, xs]) => `${ROLE_CN[k] || '其他'} ${xs.length}`).join(' · ')}</span><span className="s">随请款单自动接入，无需重复上传。</span></div></div> : <div className="lik-empty">当前请款单没有附件。</div>}</section>
      </div>
      {(data.history || []).length > 0 && <div style={{ padding: '0 15px 12px' }}><details><summary className="lik-lbl" style={{ cursor: 'pointer' }}>解析记录 · {data.history.length} 条</summary><div className="lik-hist">{data.history.slice(0, 8).map((h, i) => <div key={i}><span className="d">{h.at}</span><span>{h.by}</span><span>{h.action.replace('物流复核-', '')}：{h.detail}</span></div>)}</div></details></div>}
    </div>
    <div className="card">
      <h3 className="lik-section">每页核对 <small>异常工作表优先，展开可查看取数列</small><span className="sp" /><button className={'btn sm' + (onlyBad ? ' pri' : '')} onClick={() => setOnlyBad(!onlyBad)}>{onlyBad ? '查看全部' : '只看异常'}</button></h3>
      {reps.length === 0 && <div className="ovempty">没有原件可查</div>}{reps.length > 0 && sheetRows.length === 0 && <div className="ovempty">没有异常工作表 ✓</div>}
      {sheetRows.length > 0 && <div className="tw"><table className="fxtbl" style={{ minWidth: 760 }}><thead><tr><th>工作表</th><th className="num">单数</th><th className="num">读出来的</th><th className="num">账单合计</th><th className="num">差</th><th>结论</th><th></th></tr></thead><tbody>{sheetRows.map(({ f, s, i }) => {
        const k = f.id + '|' + i, [lb, cl] = IK_V[s.verdict] || [s.verdict, 'neu'], diff = s.total ? s.amount - s.total.sum : null
        return <React.Fragment key={k}><tr><td><b>{s.sheet}</b>{files.length > 1 && <div className="dim" style={{ fontSize: 11.5 }}>{f.name || f.src}</div>}</td><td className="num">{s.role === 'detail' ? s.docs : s.n || '—'}</td><td className="num mono">{s.amount != null ? money(s.amount) : '—'}</td><td className="num mono">{s.total ? money(s.total.sum) : '—'}</td><td className={'num mono ' + (diff == null ? '' : isZero(diff) && !s.unsplit ? 'diffneg' : 'diffpos')}>{s.verdict === 'unsplit' ? money(s.unsplit) : diff == null ? '—' : isZero(diff) ? '0' : money(diff)}</td><td><span className={'pill ' + cl}>{lb}</span><div className="dim" style={{ fontSize: 11.5 }}>{sheetSay(s)}</div></td><td>{s.role === 'detail' && <button className="lnk" aria-expanded={!!more[k]} onClick={() => setMore(o => ({ ...o, [k]: !o[k] }))}>{more[k] ? '收起' : '看列'}</button>}</td></tr>{more[k] && <tr className="lik-more"><td colSpan="7"><Chips s={s} /></td></tr>}</React.Fragment>
      })}</tbody></table></div>}
      {reps.length > 0 && <details style={{ margin: '0 15px 12px' }}><summary className="lik-lbl" style={{ cursor: 'pointer' }}>查看判定口径</summary><div className="lik-note" style={{ margin: '6px 0 0' }}>每页把算钱列与账单自己的合计列相比。取数说明配置合计列时，差额记为「其他费用（未拆）」，但仍需补配置后才算复核完成。</div></details>}
    </div>
    <Overview period={period} /><div className="navbar"><span /><button className="btn pri" onClick={onNext}>下一步：逐单核价核量 ›</button></div>
  </>
}

const norm = s => String(s || '').replace(/[\s()（）]/g, '')
function blockFacts(b, bill) {
  const iv = b.invoices || [], amountOk = !!iv.length && isZero((b.inv_total || 0) - (b.amount || 0)) && (bill == null || isZero(bill - (b.amount || 0)))
  const sellerOk = !!iv.length && iv.every(i => norm(i.seller) === norm(b.payee)), buyerOk = !!iv.length && iv.every(i => norm(i.buyer) === norm(b.subject_full))
  const allRev = !!iv.length && iv.every(i => i.review === 'approved'), laterOk = !iv.length && !!b.later
  const issues = []
  if (!iv.length && !laterOk) issues.push(b.later_slip ? '后补单尚未确认' : '无发票且未确认后补')
  if (iv.length && !amountOk) issues.push('账单 / 请款 / 发票金额未对平')
  if (iv.length && !sellerOk) issues.push('销方与请款收款方不一致')
  if (iv.length && !buyerOk) issues.push('购方与请款主体不一致')
  if (iv.length && !allRev) issues.push('发票尚未全部审核')
  return { iv, amountOk, sellerOk, buyerOk, allRev, laterOk, issues, ready: issues.length === 0 }
}

export function InvAuditPanel({ inv3, billOf, onChanged, flash, locked, onLater, voucherFor }) {
  const [busy, setBusy] = useState(''), [query, setQuery] = useState(''), [onlyTodo, setOnlyTodo] = useState(false), [selected, setSelected] = useState('')
  const bs = ((inv3 && inv3.blocks) || []).filter(b => !b.err)
  const facts = Object.fromEntries(bs.map(b => [b.inst, blockFacts(b, billOf(b.subject))]))
  const visible = bs.filter(b => (!onlyTodo || !facts[b.inst].ready) && (!query || `${b.subject}${b.bid}${b.payee}`.toLowerCase().includes(query.toLowerCase()))).sort((a, b) => Number(facts[a.inst].ready) - Number(facts[b.inst].ready))
  const current = visible.find(b => b.inst === selected) || visible[0]
  useEffect(() => { if (current && current.inst !== selected) setSelected(current.inst) }, [current && current.inst])
  if (!inv3) return <div className="card"><div className="ovempty">读请款单和发票管家中…</div></div>
  if (inv3.err) return <div className="card"><div className="fxwarn" style={{ margin: 15 }}>{inv3.err}</div></div>
  if (!bs.length) return <div className="card"><div className="ovempty">这家这月还没有钉钉请款单</div></div>
  const invs = bs.flatMap(b => b.invoices || []), nRev = invs.filter(i => i.review === 'approved').length, nPaper = invs.filter(i => i.paper).length
  const todo = bs.filter(b => !facts[b.inst].ready).length, allReady = todo === 0
  const paperToggle = (i, on) => { setBusy('p' + i.id); invPaperMark(i.id, on).then(() => { flash(`发票 ${i.number || i.id}：纸质票已记收到`); onChanged && onChanged() }).catch(e => flash('纸质票登记失败：' + e.message)).finally(() => setBusy('')) }
  const approve = (b, i) => {
    const f = facts[b.inst]
    if (!b.folder) { flash('这张票没有票夹信息，请在发票管家里审'); return }
    if (!f.amountOk || !f.sellerOk || !f.buyerOk) { flash('金额或销购方核对未通过，不能直接审核'); return }
    if (!window.confirm(`审核通过这张发票？\n\n${i.number || ''} · 价税合计 ${money(i.gross)}\n系统金额、销方、购方核对已通过。`)) return
    setBusy('a' + i.id); invAuditItemsApprove({ folderId: b.folder, itemIds: [i.id] }).then(r => { if (r && r.ok === false) { flash('审核没通过：' + (r.msg || '')); return } flash('已审核通过' + (r && r.folderDone ? '，整单通过' : '')); onChanged && onChanged() }).catch(e => flash('审核失败：' + e.message)).finally(() => setBusy(''))
  }
  const mk = v => v == null ? <span className="lik-mk">·</span> : v ? <span className="lik-mk ok">✓</span> : <span className="lik-mk bad">✕</span>
  return <>
    <div className="lik-verdict"><div className="vb"><span className={'vbadge ' + (allReady ? 'ok' : 'warn')}><span className="dot" />{allReady ? '发票核对完成' : `${todo} 张请款单待处理`}</span><span className="vsub">逐单核对金额、销购方和审核状态；无票须确认后补</span></div><div className="lia-summary"><div><small>请款单</small><b>{bs.length} 张 · {money(inv3.req_total)}</b></div><div><small>发票</small><b>{invs.length} 张 · {money(inv3.inv_total)}</b></div><div><small>审核进度</small><b>{nRev} / {invs.length}</b></div><div><small>纸质票</small><b>{nPaper} / {invs.length}</b></div></div></div>
    <div className="lia-work">
      <aside className="lia-queue"><div className="lia-qtools"><input type="search" aria-label="搜索请款单" placeholder="搜主体 / 请款单号" value={query} onChange={e => setQuery(e.target.value)} /><div className="lia-qfilter"><button className={'btn sm' + (!onlyTodo ? ' pri' : '')} onClick={() => setOnlyTodo(false)}>全部 {bs.length}</button><button className={'btn sm' + (onlyTodo ? ' pri' : '')} onClick={() => setOnlyTodo(true)}>待处理 {todo}</button></div></div><div className="lia-qlist">{visible.map(b => { const f = facts[b.inst]; return <button key={b.inst} className={'lia-qitem' + (current && current.inst === b.inst ? ' on' : '')} onClick={() => setSelected(b.inst)}><span className="qt"><span>{b.subject}</span><span className="mono">{money(b.amount)}</span></span><span className="qs"><span className={f.ready ? 'diffok' : 'qn'}>{f.ready ? '✓ 已就绪' : `${f.issues.length} 项待处理`}</span><span>{f.iv.length ? `${f.iv.filter(i => i.review === 'approved').length}/${f.iv.length} 已审` : b.later ? '后补已确认' : '无票'}</span></span></button>})}{!visible.length && <div className="lik-empty">没有符合条件的请款单</div>}</div></aside>
      <section className="lia-detail">{current && (() => { const b = current, f = facts[b.inst], bill = billOf(b.subject), slip = b.later_slip; return <>
        <div className="lik-req"><div className="ih"><h4>{b.subject}</h4><span className={'pill ' + (f.ready ? 'ok' : 'warn')}>{f.ready ? '核对完成' : '待处理'}</span><span className="sp" /><span className="dim" style={{ fontSize: 12 }}>请款单 {b.bid} · {b.applicant}</span>{b.folder && <a className="btn sm" href={`#/invdesk?folder=${b.folder}`} target="_blank" rel="noopener">发票管家 ↗</a>}</div>
          <div className="lik-trio"><div><span className="h">账单（本主体）</span><span className="v">{bill != null ? money(bill) : '—'}</span><span className="s">{bill != null ? '逐笔复核本主体小计' : '同主体多张请款，不逐张比'}</span></div><div><span className="h">请款单</span><span className="v">{money(b.amount)}</span><span className="s">收款方 {b.payee || '—'}</span></div><div><span className="h">发票</span><span className={'v' + (f.iv.length ? '' : ' warnv')}>{f.iv.length ? money(b.inv_total) : '还没到'}</span><span className="s">{f.iv.length ? `${f.iv.length} 张 · 税额 ${money(b.inv_tax)}` : slip ? `后补单 #${slip.id} · 预计 ${slip.expect_date || '—'} 到` : '发票管家中没有发票'}</span></div></div>
          {f.iv.length > 0 && <div className="lik-invwrap tw"><table className="fxtbl" style={{ minWidth: 720 }}><thead><tr><th>发票号码</th><th>类型</th><th>销方</th><th>购方</th><th className="num">价税合计</th><th className="num">税率</th><th>纸质票</th><th>审核</th></tr></thead><tbody>{f.iv.map(i => <tr key={i.id}><td className="mono" style={{ fontSize: 12 }}>{i.number}</td><td>{i.type_short || i.type}{i.deduct === false && <span className="dim">（不抵扣）</span>}</td><td>{i.seller}</td><td>{i.buyer}</td><td className="num mono">{money(i.gross)}</td><td className="num">{i.rate}</td><td>{i.paper ? <span className="lik-mk ok" style={{ fontSize: 12.5 }}>✓ 已收到</span> : <label className="lik-sw" title="纸质票收到后才能审核"><input type="checkbox" aria-label={`发票 ${i.number} 纸质票已收到`} disabled={!!locked || !!busy || i.review === 'approved'} checked={false} onChange={e => paperToggle(i, e.target.checked)} /><i /></label>}{!i.paper && <span className="dim" style={{ fontSize: 12, marginLeft: 4 }}>还没到</span>}</td><td>{i.review === 'approved' ? <span className="pill ok">已审核</span> : i.review === 'pending' && !locked ? <button className="btn sm" disabled={!!busy || !i.paper || !f.amountOk || !f.sellerOk || !f.buyerOk} title={!i.paper ? '纸质票到了才能审' : !f.amountOk || !f.sellerOk || !f.buyerOk ? '先解决金额或销购方异常' : '审核通过'} onClick={() => approve(b, i)}>{busy === 'a' + i.id ? '审核中…' : '审核通过'}</button> : <span className="pill warn">待发票管家审核</span>}</td></tr>)}</tbody></table></div>}
          {!f.iv.length && <div className={'lik-later' + (b.later ? ' done' : '')}><div className="lr-h">{b.later ? '✓ 发票后补已确认' : slip ? '后补单已登记，待财务确认' : '发票后补单 · 业务尚未登记'}{!b.later && slip && onLater && <button className="btn sm pri" disabled={locked} onClick={() => onLater(b, true)}>确认发票后补</button>}{b.later && onLater && !locked && <button className="lnk" onClick={() => onLater(b, false)}>撤销确认</button>}</div><div style={{ color: '#5E6B78' }}>{slip ? `后补单 #${slip.id} · ${slip.filed_by || '—'} 登记 · 预计 ${slip.expect_date || '—'} 到票` : '后补单由业务在钉钉审批里登记；只有登记并经财务确认后才能放行。'}</div></div>}
          <div className="lik-chk"><div>{f.iv.length ? mk(f.amountOk) : mk(f.laterOk)}<span>金额 / 后补<span className="x">{f.iv.length ? (f.amountOk ? '账单、请款、发票金额一致' : '三处金额未对平') : f.laterOk ? '无票，后补已确认' : '等待到票或确认后补'}</span></span></div><div>{mk(f.iv.length ? f.sellerOk : null)}<span>销方是承运商<span className="x">{f.iv.length ? `收款方：${b.payee || '—'}` : '暂无发票'}</span></span></div><div>{mk(f.iv.length ? f.buyerOk : null)}<span>购方是本主体<span className="x">{b.subject_full || b.subject}</span></span></div><div>{mk(f.iv.length ? f.allRev : null)}<span>发票管家审核<span className="x">{f.iv.length ? `${f.iv.filter(i => i.review === 'approved').length}/${f.iv.length} 张已审核` : '暂无发票'}</span></span></div></div>
        </div>
        {voucherFor && <details className="lia-voucher"><summary>做账与后补处理</summary><div style={{ paddingTop: 10 }}>{voucherFor(b)}</div></details>}
      </> })()}</section>
    </div>
  </>
}
