// [Change Log] Date:2026-10-17 Author:Claude Opus 5.5 Version:V2.991
// 物流账单复核台第①④步重设计(用户 2026-10-17 看样机定稿，样机 output/intake-redesign/)：
//   IntakeStep   ① 数据接入：一张「三方对账结论条」(原件→读出来的→请款单)说清钱对不对；文件卡、解析记录折叠。
//   InvAuditPanel ④ 发票审核：这页直接做事——纸质票拨杆 + 审核通过(发票管家 /api/inv/audit/items/approve)；
//                 发票后补单【只读核对】(业务在钉钉登记，会计这里只看登记了没有，V2.991 用户定)。
// 性能(V2.991)：第①步「每页核对」报告导入时落库(logistics_intake_reports)，本页只读库——见 routers/logistics_intake_check.py。
import React, { useState } from 'react'
import { reviewIntakeOverview, reviewReparse, invPaperMark, invAuditItemsApprove } from '../api.js'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const isZero = d => d != null && Math.abs(d) < 0.01
const kb = n => (n == null ? '' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB')

// 读全没有：结论 → [说法, 颜色]
export const IK_V = {
  ok: ['读全了', 'ok'], unsplit: ['有钱没拆到费用项', 'warn'], gap: ['和账单合计对不上', 'bad'], maybe: ['可能漏读', 'warn'],
  nocheck: ['没有合计可比', 'neu'], skip: ['跳过', 'neu'], accrual: ['汇总页', 'neu'], err: ['解析不了', 'bad'],
  nobill: ['还没账单', 'neu'], noraw: ['没存原件', 'neu'], wait: ['还没来', 'neu'], nospec: ['没有取数说明', 'warn'], register: ['登记制·不导账单', 'neu'],
}
const ROLE_CN = { bill: '账单', stamp: '盖章版', invoice: '发票', review: '审核留档' }

export const IK_CSS = `
.lrv .lik-files{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;padding:12px 15px}
.lrv .lik-file{border:1px solid #DCE2E7;border-radius:10px;padding:10px 12px;display:grid;gap:3px;min-width:0}
.lrv .lik-file .h{font-size:11.5px;color:#8A96A2}.lrv .lik-file .t{font-weight:600;word-break:break-all}.lrv .lik-file .s{font-size:12px;color:#5E6B78;word-break:break-all}
.lrv .lik-file .a{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.lrv .lik-hist{padding:4px 15px 12px;display:grid}
.lrv .lik-hist>div{display:grid;grid-template-columns:118px 90px minmax(0,1fr);gap:10px;padding:6px 0;border-bottom:1px dotted #E6EBEF;font-size:12.5px}
.lrv .lik-hist>div:last-child{border-bottom:0}.lrv .lik-hist .d{color:#8A96A2;font-family:ui-monospace,Consolas,monospace;font-size:12px}
@media(max-width:700px){.lrv .lik-hist>div{grid-template-columns:1fr}}
.lrv .lik-lbl{font-size:11.5px;color:#8A96A2;margin:4px 0}
.lrv .lik-chips{display:flex;flex-wrap:wrap;gap:6px}
.lrv .lik-chip{display:inline-flex;gap:6px;align-items:baseline;border:1px solid #DCE2E7;border-radius:6px;padding:2px 8px;font-size:12px;background:#fff}
.lrv .lik-chip .v{font-family:ui-monospace,Consolas,monospace;color:#5E6B78}
.lrv .lik-chip.use{border-color:#BFDCCB;background:#EEF7F1}.lrv .lik-chip.tot{border-color:var(--accent)}
.lrv .lik-chip.unread{border-color:#F0D9A8;background:#FDF3E2}.lrv .lik-chip.miss{border-style:dashed;color:#8A96A2}.lrv .lik-chip.off{color:#8A96A2}
.lrv tr.lik-more td{background:#FAFBFC}
.lrv .lik-note{margin:0 15px 12px;padding:9px 12px;border-left:3px solid var(--accent);background:#F5FAFC;border-radius:0 8px 8px 0;font-size:12.5px;color:#3D4A57}
.lrv .lik-ov summary{cursor:pointer;padding:11px 15px;font-weight:600;font-size:13.5px}
.lrv .lik-ov summary small{font-weight:400;color:#8A96A2;margin-left:8px;font-size:12px}
/* 三方对账结论条(V2.991) */
.lrv .lik-verdict{border:1px solid #DCE2E7;border-radius:12px;background:#fff;overflow:hidden;margin-bottom:12px}
.lrv .lik-verdict .vb{display:flex;align-items:center;gap:10px;padding:12px 15px;border-bottom:1px solid #E6EBEF;flex-wrap:wrap}
.lrv .vbadge{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:14px;padding:5px 14px;border-radius:999px;white-space:nowrap}
.lrv .vbadge.ok{color:#1F9D63;background:#EAF6EF;border:1px solid #BFE3CF}
.lrv .vbadge.warn{color:#C77E1F;background:#FBF3E4;border:1px solid #EDD9AE}
.lrv .vbadge.bad{color:#D43D3D;background:#FBECEC;border:1px solid #F0C5C5}
.lrv .vbadge .dot{width:8px;height:8px;border-radius:50%;background:currentColor}
.lrv .vbadge.neu{color:#5E6B78;background:#F0F3F6;border:1px solid #DCE2E7}
.lrv .vb .vsub{color:#5E6B78;font-size:12.5px}
.lrv .vb .sp{flex:1}
.lrv .vflow{display:grid;grid-template-columns:1fr 46px 1fr 46px 1fr;align-items:stretch;padding:13px 15px;gap:0}
.lrv .vnode{padding:9px 13px;border:1px solid #DCE2E7;border-radius:10px;display:grid;gap:3px;min-width:0;background:#fff}
.lrv .vnode .h{font-size:12px;color:#8A96A2}
.lrv .vnode .v{font-size:18px;font-weight:700;font-family:ui-monospace,Consolas,monospace}
.lrv .vnode .s{font-size:12px;color:#5E6B78;word-break:break-all}
.lrv .vnode.money{border-color:#C9D8F8;background:#F7FAFF}
.lrv .vnode.money .v{color:var(--accent)}
.lrv .vlink{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:#1F9D63;font-size:11px;position:relative;white-space:nowrap}
.lrv .vlink::before{content:"";position:absolute;top:50%;left:4px;right:4px;border-top:2px solid currentColor;opacity:.35}
.lrv .vlink span{position:relative;background:#fff;padding:1px 3px;border-radius:4px}
.lrv .vlink.bad{color:#D43D3D}
@media(max-width:760px){.lrv .vflow{grid-template-columns:1fr}.lrv .vlink{padding:2px 0}.lrv .vlink::before{top:4px;bottom:4px;left:50%;right:auto;border-top:0;border-left:2px solid currentColor}}
/* 第④步：请款单卡 + 纸质票开关 + 后补单只读块 */
.lrv .lik-req{border:1px solid #DCE2E7;border-radius:12px;background:#fff;overflow:hidden;margin-bottom:12px}
.lrv .lik-req .ih{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 15px;border-bottom:1px solid #E6EBEF}
.lrv .lik-req .ih h4{margin:0;font-size:14px}.lrv .lik-req .ih .sp{flex:1}
.lrv .lik-trio{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #E6EBEF;border-radius:10px;overflow:hidden;margin:12px 15px 0}
.lrv .lik-trio>div{padding:8px 12px;display:grid;gap:2px;border-right:1px solid #E6EBEF;min-width:0}.lrv .lik-trio>div:last-child{border-right:0}
.lrv .lik-trio .h{font-size:11.5px;color:#8A96A2}.lrv .lik-trio .v{font-family:ui-monospace,Consolas,monospace;font-size:16px;font-weight:600}
.lrv .lik-trio .s{font-size:11.5px;color:#5E6B78;word-break:break-all}
.lrv .lik-trio .v.warnv{color:#C77E1F}
@media(max-width:700px){.lrv .lik-trio{grid-template-columns:1fr}.lrv .lik-trio>div{border-right:0;border-bottom:1px solid #E6EBEF}}
.lrv .lik-invwrap{padding:10px 15px 0}
.lrv .lik-chk{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:6px 16px;padding:10px 15px 12px;font-size:12.5px}
.lrv .lik-chk>div{display:grid;grid-template-columns:18px minmax(0,1fr);gap:6px}
.lrv .lik-chk .x{display:block;font-size:11.5px;color:#8A96A2;word-break:break-all}
.lrv .lik-mk{font-weight:700}.lrv .lik-mk.ok{color:var(--ok)}.lrv .lik-mk.bad{color:var(--bad)}.lrv .lik-mk.warn{color:var(--warn)}
.lrv .lik-sw{position:relative;display:inline-block;width:34px;height:19px;vertical-align:middle;cursor:pointer}
.lrv .lik-sw input{opacity:0;width:0;height:0}
.lrv .lik-sw i{position:absolute;inset:0;background:#CBD5DD;border-radius:999px;transition:.15s}
.lrv .lik-sw i::after{content:"";position:absolute;left:2px;top:2px;width:15px;height:15px;background:#fff;border-radius:50%;transition:.15s}
.lrv .lik-sw input:checked+i{background:var(--ok)}
.lrv .lik-sw input:checked+i::after{left:17px}
.lrv .lik-later{margin:12px 15px 12px;border:1px dashed #EDD9AE;background:#FDF9F0;border-radius:10px;padding:10px 13px;display:grid;gap:6px;font-size:12.5px}
.lrv .lik-later.done{border-style:solid;border-color:#BFDCCB;background:#F2F9F4}
.lrv .lik-later .lr-h{display:flex;align-items:center;gap:8px;font-weight:600;flex-wrap:wrap}
`

function Chips({ s }) {
  const tot = s.total
  return <>
    <div className="lik-lbl">算钱的列（绿）· 账单自己的合计（蓝框，只拿来比）</div>
    <div className="lik-chips">
      {(s.money || []).filter(m => m.found).map((m, i) => <span key={'m' + i} className="lik-chip use">{m.name} <span className="v">{money(m.sum)}</span></span>)}
      {tot && tot.how !== 'self' && <span className="lik-chip tot" title={tot.how === 'spec' ? '取数说明配的合计列：拆项对不上时以它为准' : '取数说明没配合计列，按表头认出来的，只拿来比'}>
        {tot.name} <span className="v">{money(tot.sum)}</span>{tot.how === 'guess' ? '（认出来的）' : ''}</span>}
      {(s.money || []).filter(m => !m.found).map((m, i) => <span key={'x' + i} className="lik-chip miss">{m.name} · 本月账单没这列</span>)}
    </div>
    {(s.unread || []).length > 0 && <>
      <div className="lik-lbl">像是钱、但没算进去的列{s.verdict === 'ok' ? '（加起来已经对上账单合计，多半是打折前的价这类，不用管）' : '（要看是不是漏读了）'}</div>
      <div className="lik-chips">{s.unread.map((u, i) => <span key={i} className={'lik-chip ' + (s.verdict === 'ok' ? 'off' : 'unread')}>{u.name} <span className="v">{money(u.sum)}</span></span>)}</div>
    </>}
    {(s.others || []).length > 0 && <>
      <div className="lik-lbl">有数字、不算钱的列</div>
      <div className="lik-chips">{s.others.map((u, i) => <span key={i} className="lik-chip off">{u.name} <span className="v">{Number(u.sum).toLocaleString('zh-CN')}</span></span>)}</div>
    </>}
  </>
}

function sheetSay(s) {
  if (s.verdict === 'ok') return s.total ? (s.total.how === 'self' ? '直接读的账单金额列' : '拆项加起来＝账单合计') : ''
  if (s.verdict === 'unsplit') return `按账单合计算了，其中 ${money(s.unsplit)} 没拆到费用项：账单多了取数说明不认识的列`
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
  return (
    <details className="card lik-ov" onToggle={e => { const o = e.currentTarget.open; setOpen(o); if (o && !d) load() }}>
      <summary>{period} 各家接入情况<small>哪家读全了、哪家还没来，一张表看完（账单导入时就核过，读库很快）</small></summary>
      {open && <>
        {!d && <div className="ovempty">读取中…</div>}
        {d && d.err && <div className="fxwarn" style={{ margin: '0 15px 12px' }}>{d.err}</div>}
        {d && !d.err && <div className="tw"><table className="ovtable">
          <thead><tr><th>承运商</th><th className="num">请款单</th><th className="num">账单原件</th><th className="num">读出来的</th><th className="num">和请款单差</th><th>钱怎么取</th><th>结论</th></tr></thead>
          <tbody>{[...rows].sort((a, b) => ord(a) - ord(b) || a.carrier.localeCompare(b.carrier, 'zh')).map(r => {
            const [lb, cl] = IK_V[r.status] || [r.status, 'neu']
            return <tr key={r.carrier}>
              <td className="ovcar">{r.carrier}</td>
              <td className="num">{r.n_req ? `${r.n_req} 张 · ${money(r.req_total)}` : <span className="dim">还没来</span>}</td>
              <td className="num">{r.n_raw ? `${r.n_raw} 份` : <span className="dim">—</span>}</td>
              <td className="num mono">{r.bill_total != null ? money(r.bill_total) : '—'}</td>
              <td className={'num mono ' + (r.diff == null ? '' : isZero(r.diff) ? 'diffneg' : 'diffpos')}>{r.diff == null ? '—' : isZero(r.diff) ? '0' : money(r.diff)}</td>
              <td style={{ fontSize: 12 }}>{r.how}{r.risky && <div style={{ color: 'var(--warn)', fontSize: 11.5 }}>几列加起来算钱、没配合计兜底：账单多一列会悄悄少读</div>}</td>
              <td><span className={'pill ' + cl}>{lb}</span></td>
            </tr>
          })}</tbody>
        </table></div>}
        {d && !d.err && <div className="ovfoot">「读出来的」＝复核台现在存着的账单金额；「结论」＝按现在的取数说明把存的原件再解析一遍，算钱的列加起来和账单自己的合计比。<button className="lnk" onClick={load}>重读</button></div>}
      </>}
    </details>
  )
}

export function IntakeStep({ data, period, locked, flash, onOpenBills, onChanged, onNext }) {
  const [more, setMore] = useState({})
  const [busy, setBusy] = useState('')
  if (!data) return <div className="card"><div className="ovempty">读账单原件、按取数说明检查中…</div></div>
  if (data.err) return <div className="card"><div className="fxwarn" style={{ margin: 15 }}>{data.err}</div></div>
  const files = data.files || []
  const reps = files.map(f => f.report).filter(Boolean)
  const totSheets = reps.flatMap(r => (r.sheets || []).filter(s => s.role === 'detail' && s.total))
  const dupRows = reps.reduce((a, r) => a + (r.dup_rows || 0), 0)
  // 账单自己的合计：每页都有合计列、几页之间没有重复行时才加得起来
  const billSelfTotal = totSheets.length && !dupRows && reps.every(r => (r.sheets || []).filter(s => s.role === 'detail').every(s => s.total))
    ? totSheets.reduce((a, s) => a + s.total.sum, 0) : null
  const [vl, vc] = IK_V[data.verdict] || ['', 'neu']
  const nDocs = (data.bills || []).reduce((a, b) => a + (b.n_doc || 0), 0)
  // 三段流的两个连接：读出来的 vs 账单自己的合计；读出来的 vs 请款单
  const link1 = billSelfTotal == null ? null : isZero(data.bill_total - billSelfTotal)
  const link2 = data.diff == null ? null : isZero(data.diff)
  const reparse = f => {
    if (!window.confirm(`用存的原件「${f.name || f.src || '账单'}」按现在的取数说明重新解析一遍？\n\n人工改过的主体/费用类型、写过的备注按单号保留；逐笔、逐单的结论会跟着重算。`)) return
    setBusy('re' + f.id)
    reviewReparse(data.carrier, period, f.src).then(r => { flash(`已重新解析：明细 ${r.detail} 行，合计 ${money(r.before)} → ${money(r.after)}${r.notes ? `，备注写回 ${r.notes} 张` : ''}`); onChanged() })
      .catch(e => flash('重新解析失败：' + e.message)).finally(() => setBusy(''))
  }
  const reqByRole = {}
  ;(data.req_files || []).forEach(f => { (reqByRole[f.role || 'other'] = reqByRole[f.role || 'other'] || []).push(f) })
  return <>
    {/* 三方对账结论条：一张条说清「钱对不对」 */}
    <div className="lik-verdict">
      <div className="vb">
        <span className={'vbadge ' + vc}><span className="dot"></span>{vl || '—'}</span>
        <span className="vsub">账单原件 → 读出来的 → 请款单，三处金额对得上就能进下一步</span>
        <span className="sp"></span>
        <button className="btn sm" disabled={!!locked || !data.has_spec} title={locked || '看现在解析着哪几份、谁传的；上传新的一份、替换或删除'} onClick={onOpenBills}>上传 / 替换 / 删除账单</button>
      </div>
      <div className="vflow">
        <div className="vnode">
          <span className="h">账单原件</span>
          <span className="v">{files.length} 份</span>
          <span className="s">{(data.bills || []).length ? `复核台 ${(data.bills || []).length} 份账单 · ${nDocs} 张单` : (files.length ? '' : '还没有')}</span>
        </div>
        <div className={'vlink' + (link1 === false ? ' bad' : '')}><span>{link1 == null ? '—' : link1 ? '✓ 一致' : `✕ 差 ${money(Math.abs(data.bill_total - billSelfTotal))}`}</span></div>
        <div className="vnode money">
          <span className="h">读出来的</span>
          <span className="v">{money(data.bill_total)}</span>
          <span className="s">{billSelfTotal != null ? '按取数说明拆完 · 和账单合计比' : '按取数说明拆完'}</span>
        </div>
        <div className={'vlink' + (link2 === false ? ' bad' : '')}><span>{link2 == null ? '—' : link2 ? '✓ 差 0' : `✕ 差 ${money(Math.abs(data.diff))}`}</span></div>
        <div className="vnode">
          <span className="h">请款单（钉钉）</span>
          <span className="v">{data.req_total ? money(data.req_total) : '—'}</span>
          <span className="s">{(data.payreqs || []).length} 张{Object.keys(reqByRole).length ? ' · ' + Object.entries(reqByRole).map(([k, xs]) => `${ROLE_CN[k] || '其他'} ${xs.length}`).join('、') : ''}</span>
        </div>
      </div>
    </div>

    <div className="card">
      <h3>文件</h3>
      {!data.has_spec && <div className="fxwarn" style={{ margin: '0 15px 12px' }}>这家还没配取数说明，账单解析不了。</div>}
      {data.register && <div className="dim" style={{ padding: '0 15px 12px', fontSize: 12.5 }}>登记制：这家没有可逐单核的账单，不用导账单，直接到第④步核对计提和请款、登记。</div>}
      {files.length > 0 && <div className="lik-files">{files.map(f => <div key={f.id} className="lik-file">
        <span className="h">账单原件{f.src ? ` · ${f.src}` : ''}</span>
        <span className="t">{f.name || '（没记文件名）'}</span>
        <span className="s">{kb(f.size)} · {f.by || '没记录'} {f.at}</span>
        <span className="s">{(f.origin || '').replace(/「[^」]*」$/, '') || '来源没记录'}</span>
        <span className="a"><button className="btn sm" disabled={!!locked || !!busy} title={locked || '取数说明改过以后，用存的原件再解析一遍'} onClick={() => reparse(f)}>{busy === 're' + f.id ? '解析中…' : '用原件重新解析'}</button></span>
      </div>)}
      {Object.keys(reqByRole).length > 0 && <div className="lik-file">
        <span className="h">钉钉请款单附件</span>
        <span className="t">{Object.entries(reqByRole).map(([k, xs]) => `${ROLE_CN[k] || '其他'} ${xs.length}`).join(' · ')}</span>
        <span className="s">{(reqByRole.bill || []).length > 1 && new Set(reqByRole.bill.map(x => x.size)).size === 1 ? `${reqByRole.bill.length} 张请款单附的账单是同一个文件，只存一份。` : '随请款单自动接入，不用手工传。'}</span>
      </div>}</div>}
      {!files.length && data.has_spec && !data.register && <div className="dim" style={{ padding: '0 15px 12px', fontSize: 12.5 }}>
        {(data.bills || []).length ? '复核台里有账单，但没存原件（V2.843 以前导入的），读全检查做不了；要检查就重新上传一次。' : '这家这月还没有账单。钉钉请款单附了账单会自动接进来，也可以手工上传。'}</div>}
      {(data.history || []).length > 0 && <div style={{ padding: '0 15px 12px' }}>
        <details>
          <summary className="lik-lbl" style={{ cursor: 'pointer' }}>解析记录 · {data.history.length} 条（什么时候传过、重新解析过）</summary>
          <div className="lik-hist" style={{ paddingTop: 4 }}>{data.history.slice(0, 8).map((h, i) => <div key={i}><span className="d">{h.at}</span><span>{h.by}</span>
            <span>{h.action.replace('物流复核-', '')}：{h.detail}</span></div>)}</div>
        </details></div>}
    </div>

    <div className="card">
      <h3>每页核对<span className="dim" style={{ fontWeight: 400, fontSize: 12, marginLeft: 8 }}>算钱的那几列加起来，和账单自己的合计对不对得上</span></h3>
      {reps.length === 0 && <div className="ovempty">没有原件可查</div>}
      {files.filter(f => f.report).map(f => <div key={f.id} className="tw">
        {files.length > 1 && <div className="lik-lbl" style={{ padding: '6px 15px 0' }}>{f.name || f.src}</div>}
        {f.report.err && <div className="fxwarn" style={{ margin: '0 15px 10px' }}>{f.report.err}</div>}
        <table className="fxtbl" style={{ minWidth: 760 }}>
          <thead><tr><th>工作表</th><th className="num">单数</th><th className="num">读出来的</th><th className="num">账单合计</th><th className="num">差</th><th>结论</th><th></th></tr></thead>
          <tbody>{(f.report.sheets || []).map((s, i) => {
            const k = f.id + '|' + i, [lb, cl] = IK_V[s.verdict] || [s.verdict, 'neu'], diff = s.total ? s.amount - s.total.sum : null
            return <React.Fragment key={k}>
              <tr><td><b>{s.sheet}</b>{s.header_row && <div className="dim" style={{ fontSize: 11.5 }}>表头第 {s.header_row} 行</div>}</td>
                <td className="num">{s.role === 'detail' ? s.docs : s.n || '—'}</td>
                <td className="num mono">{s.amount != null ? money(s.amount) : '—'}</td>
                <td className="num mono">{s.total ? money(s.total.sum) : '—'}</td>
                <td className={'num mono ' + (diff == null ? '' : isZero(diff) && !s.unsplit ? 'diffneg' : 'diffpos')}>{s.verdict === 'unsplit' ? money(s.unsplit) : diff == null ? '—' : isZero(diff) ? '0' : money(diff)}</td>
                <td><span className={'pill ' + cl}>{lb}</span><div className="dim" style={{ fontSize: 11.5 }}>{sheetSay(s)}</div></td>
                <td>{s.role === 'detail' && <button className="lnk" aria-expanded={!!more[k]} onClick={() => setMore(o => ({ ...o, [k]: !o[k] }))}>{more[k] ? '收起' : '看列'}</button>}</td></tr>
              {more[k] && <tr className="lik-more"><td colSpan="7"><Chips s={s} /></td></tr>}
            </React.Fragment>
          })}</tbody>
          <tfoot><tr><td><b>合计</b>{f.report.dup_rows > 0 && <div className="dim" style={{ fontSize: 11.5 }}>几页里重复的 {f.report.dup_rows} 行按运单号只算一次</div>}</td><td className="num">{f.report.n}</td>
            <td className="num mono"><b>{money(f.report.amount)}</b></td><td colSpan="4"></td></tr></tfoot>
        </table>
      </div>)}
      {reps.length > 0 && <div className="lik-note"><b>读全没有怎么判：</b>每页把算钱的那几列加起来，和账单自己的合计列（应付金额、合计这类）比。对不上，多半是账单加了新列、取数说明还不认识。
        取数说明配了合计列的，钱按账单合计算、不会少，差额记成「其他费用（未拆）」提醒去补取数说明。</div>}
    </div>

    <Overview period={period} />
    <div className="navbar"><span /><button className="btn pri" onClick={onNext}>下一步：逐笔计提复核 ›</button></div>
  </>
}

// ④ 发票审核：一张请款单一块。核对系统自动做；这页直接审票、登记纸质票；发票后补单只读核对（业务在钉钉登记，这里只看登记了没有，V2.991 用户定）
const norm = s => String(s || '').replace(/[\s()（）]/g, '')
export function InvAuditPanel({ inv3, billOf, onChanged, flash, locked }) {
  const [busy, setBusy] = useState('')
  if (!inv3) return <div className="card"><div className="ovempty">读请款单和发票管家中…</div></div>
  if (inv3.err) return <div className="card"><div className="fxwarn" style={{ margin: 15 }}>{inv3.err}</div></div>
  const bs = (inv3.blocks || []).filter(b => !b.err)
  if (!bs.length) return <div className="card"><div className="ovempty">这家这月还没有钉钉请款单</div></div>
  const invs = bs.flatMap(b => b.invoices || [])
  const nOk = bs.filter(b => (b.invoices || []).length && isZero((b.inv_total || 0) - (b.amount || 0))).length
  const nRev = invs.filter(i => i.review === 'approved').length
  const nPaper = invs.filter(i => i.paper).length
  // 后补单核对：没票的请款单，业务登记了没有
  const noInv = bs.filter(b => !(b.invoices || []).length)
  const laterOk = noInv.filter(b => b.later_slip || b.later).length
  // 顶部结论条
  const allOk = invs.length && isZero((inv3.inv_total || 0) - (inv3.req_total || 0))
  const revAll = invs.length > 0 && nRev === invs.length
  const badge = revAll && (noInv.length === 0 || laterOk === noInv.length) ? ['ok', '发票齐了 · 可以登记'] : invs.length ? ['warn', `${nRev}/${invs.length} 审过${noInv.length ? ` · ${noInv.length - laterOk} 张待后补` : ''}`] : ['warn', '还没有发票']
  const mk = v => v == null ? <span className="lik-mk">·</span> : v ? <span className="lik-mk ok">✓</span> : <span className="lik-mk bad">✕</span>

  const paperToggle = (i, on) => {
    setBusy('p' + i.id)
    invPaperMark(i.id, on).then(() => { flash(`发票 ${i.number || i.id}：纸质票${on ? '已记收到' : '改回未到'}`); onChanged && onChanged() })
      .catch(e => flash('纸质票登记失败：' + e.message)).finally(() => setBusy(''))
  }
  const approve = (b, i) => {
    if (!b.folder) { flash('这张票没有票夹信息，请在发票管家里审'); return }
    if (!window.confirm(`审核通过这张发票？\n\n${i.number || ''} · 价税合计 ${money(i.gross)}\n审核＝票面核对无误、纸质票已收到（系统核对结果在下面清单里）。`)) return
    setBusy('a' + i.id)
    invAuditItemsApprove({ folderId: b.folder, itemIds: [i.id] }).then(r => {
      if (r && r.ok === false) { flash('审核没通过：' + (r.msg || '')); return }
      flash('已审核通过' + (r && r.folderDone ? '，这张单的票齐了、整单通过' : '')); onChanged && onChanged()
    }).catch(e => flash('审核失败：' + e.message)).finally(() => setBusy(''))
  }

  return <>
    <div className="lik-verdict">
      <div className="vb">
        <span className={'vbadge ' + badge[0]}><span className="dot"></span>{badge[1]}</span>
        <span className="vsub">票面核对系统自动做；这里直接审票、登记纸质票；发票后补单看业务登记了没有</span>
      </div>
      <div className="vflow">
        <div className="vnode">
          <span className="h">请款单</span>
          <span className="v">{money(inv3.req_total)}</span>
          <span className="s">{bs.length} 张 · 钉钉审批</span>
        </div>
        <div className={'vlink' + (allOk ? '' : ' bad')}><span>{!invs.length ? '—' : allOk ? '✓ 一致' : `✕ 差 ${money(Math.abs((inv3.inv_total || 0) - (inv3.req_total || 0)))}`}</span></div>
        <div className="vnode money">
          <span className="h">发票</span>
          <span className="v">{invs.length ? money(inv3.inv_total) : '—'}</span>
          <span className="s">{invs.length ? `${invs.length} 张 · 税额 ${money(inv3.inv_tax)}` : '发票管家里还没有票'}</span>
        </div>
        <div className={'vlink' + (revAll ? '' : ' bad')}><span>{invs.length ? `${nRev}/${invs.length} 审过` : '—'}</span></div>
        <div className="vnode">
          <span className="h">审核 · 纸质票</span>
          <span className="v" style={{ fontSize: 16 }}>{invs.length ? `${nRev} / ${invs.length} 通过` : '—'}</span>
          <span className="s">{invs.length ? `纸质票 ${nPaper} / ${invs.length} 收到` : '票还没到'}</span>
        </div>
      </div>
    </div>

    {bs.map(b => {
      const iv = b.invoices || []
      const bill = billOf(b.subject)
      const three = iv.length && isZero((b.inv_total || 0) - (b.amount || 0)) && (bill == null || isZero(bill - (b.amount || 0)))
      const sellerOk = iv.length && b.payee ? iv.every(i => norm(i.seller) === norm(b.payee)) : null
      const buyerOk = iv.length && b.subject_full ? iv.every(i => norm(i.buyer) === norm(b.subject_full)) : null
      const allRev = iv.length && iv.every(i => i.review === 'approved')
      // 发票后补单：只读核对（业务登记）
      const slip = b.later_slip
      const st = !iv.length ? (b.later ? ['发票后补已确认', 'ok'] : slip ? ['后补单待到票', 'warn'] : ['业务未登记后补单', 'warn'])
        : allRev ? ['已审核', 'ok'] : iv.some(i => i.review === 'pending') ? ['待审核', 'warn'] : ['待发票管家审核', 'warn']
      return <div key={b.inst} className="lik-req">
        <div className="ih"><h4>{b.subject}</h4><span className={'pill ' + st[1]}>{st[0]}</span><span className="sp" />
          <span className="dim" style={{ fontSize: 12 }}>请款单 {b.bid} · {b.applicant} {String(b.at || '').slice(5, 16)}{b.st && b.st.label ? ' · ' + b.st.label : ''}</span>
          {b.folder ? <a className="btn sm" href={`#/invdesk?folder=${b.folder}`} target="_blank" rel="noopener">在发票管家打开 ↗</a> : null}</div>
        <div className="lik-trio">
          <div><span className="h">账单（本主体）</span><span className="v">{bill != null ? money(bill) : '—'}</span><span className="s">{bill != null ? '第②步本主体小计' : '一个主体有几张请款单，不逐张比'}</span></div>
          <div><span className="h">请款单</span><span className="v">{money(b.amount)}</span><span className="s">收款方 {b.payee || '—'}</span></div>
          <div><span className="h">发票</span><span className={'v' + (iv.length ? '' : ' warnv')}>{iv.length ? money(b.inv_total) : '还没到'}</span>
            <span className="s">{iv.length ? `${iv.length} 张 · 税额 ${money(b.inv_tax)}` : slip ? `后补单 #${slip.id}，预计 ${slip.expect_date || '—'} 到` : '发票管家里没有这张请款单的票'}</span></div>
        </div>
        {iv.length > 0 && <div className="lik-invwrap tw"><table className="fxtbl" style={{ minWidth: 720 }}>
          <thead><tr><th>发票号码</th><th>类型</th><th>销方</th><th>购方</th><th className="num">价税合计</th><th className="num">税率</th><th>纸质票</th><th>审核</th></tr></thead>
          <tbody>{iv.map(i => <tr key={i.id}>
            <td className="mono" style={{ fontSize: 12 }}>{i.number}</td><td>{i.type_short || i.type}{i.deduct === false ? <span className="dim">（不抵扣）</span> : ''}</td>
            <td>{i.seller}</td><td>{i.buyer}</td><td className="num mono">{money(i.gross)}</td><td className="num">{i.rate}</td>
            <td>{i.paper
              ? <span className="lik-mk ok" style={{ fontSize: 12.5 }}>✓ 已收到</span>
              : <label className="lik-sw" title="纸质票收到没（收到才能审）">
                  <input type="checkbox" disabled={!!locked || !!busy || i.review === 'approved'} checked={false} onChange={e => paperToggle(i, e.target.checked)} /><i></i>
                </label>}
              {!i.paper && <span className="dim" style={{ fontSize: 12, marginLeft: 4 }}>还没到</span>}</td>
            <td>{i.review === 'approved' ? <><span className="pill ok">已审核</span> <span className="dim" style={{ fontSize: 11.5 }}>{i.review_at || ''}</span></>
              : i.review === 'pending' && !locked
                ? <button className="btn sm" disabled={!!busy || !i.paper} title={!i.paper ? '纸质票到了才能审' : '票面核对无误、纸质票已收到 → 通过'} onClick={() => approve(b, i)}>{busy === 'a' + i.id ? '审…' : '审核通过'}</button>
                : <span className="pill warn">待发票管家审核</span>}</td></tr>)}</tbody>
        </table></div>}
        {/* 发票没到：后补单只读核对（业务在钉钉登记，会计这里只看登记了没有） */}
        {!iv.length && (slip || b.later
          ? <div className="lik-later done">
              <div className="lr-h">发票后补单 · 已登记{b.later ? <span className="pill ok">后补已确认</span> : <span className="pill warn">待到票</span>}</div>
              <div style={{ color: '#5E6B78' }}>{slip ? `后补单 #${slip.id} · 业务 ${slip.filed_by || '—'} 登记 · 预计 ${slip.expect_date || '—'} 到票` : '业务已确认发票后补'}</div>
              <div style={{ color: '#8A96A2' }}>到票后业务把票补进发票管家，这张单自动回到正常审核流。</div>
            </div>
          : <div className="lik-later">
              <div className="lr-h" style={{ color: '#C77E1F' }}>发票后补单 · 业务还没登记</div>
              <div style={{ color: '#5E6B78' }}>后补单由<b>业务</b>在钉钉审批里登记，这里只核对登记了没有——没登记，这张请款单付款审核不会放行。</div>
            </div>)}
        <div className="lik-chk">
          <div>{iv.length ? mk(!!three) : <span className="lik-mk">·</span>}<span>账单 ＝ 请款单 ＝ 发票<span className="x">{iv.length ? (three ? `${money(b.amount)} 三个数一样` : `请款 ${money(b.amount)}，发票 ${money(b.inv_total)}${bill != null ? `，账单 ${money(bill)}` : ''}`) : '还没有发票'}</span></span></div>
          <div>{mk(sellerOk)}<span>销方是承运商<span className="x">{sellerOk == null ? '—' : sellerOk ? '销方名称＝请款单收款方' : `收款方是 ${b.payee}`}</span></span></div>
          <div>{mk(buyerOk)}<span>购方是本主体<span className="x">{b.subject_full || b.subject}</span></span></div>
          <div>{iv.length ? (allRev ? <span className="lik-mk ok">✓</span> : <span className="lik-mk warn">…</span>) : <span className="lik-mk">·</span>}<span>发票管家审核<span className="x">{iv.length ? (allRev ? '都审过了' : '纸质票到了、点「审核通过」才算') : slip ? '等票到' : '等票到 / 等业务登记后补'}</span></span></div>
        </div>
      </div>
    })}
  </>
}
