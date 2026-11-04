// [Change Log] Date:2026-11-04 Author:Kimi Version:V2.1047 第①步双文件卡与逐页勾稽、第④步纵向请款卡整页重构。
// [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 极鲜达增加 A–J 说明及独立原始附件管理，其他承运商不变。
// [Change Log] Date:2026-11-02 Author:Kimi Version:V2.1036
// 数据接入差异首次自动弹窗说明原因，可从待处理区或主体差额重新打开；只读提示，不自动确认。
// [Change Log] Date:2026-10-30 Author:Kimi Version:V2.1032
// 数据接入按主体分别展示请款合计与账单差额，多主体不再显示承运商混合勾稽金额。
// [Change Log] Date:2026-10-26 Author:Kimi Version:V-draft
// ④ 发票审核精简为单行进度、紧凑金额与发票卡片；票夹只读看票及弹窗审核复用原审核逻辑。
// [Change Log] Date:2026-10-24 Author:Kimi Version:V2.1004
// ① 数据接入改为“金额勾稽＋待办＋每页核对”；④ 改为请款单队列＋单张审票，金额/销购方/审核/后补统一形成结论。
import React, { useEffect, useRef, useState } from 'react'
import { reviewIntakeOverview, reviewReparse, reviewRawUrl, invAuditItemsApprove, invFolder, reviewOriginalFiles, reviewOriginalUpload, reviewOriginalDelete, reviewOriginalUrl } from '../api.js'
import { Modal, InvViewer } from './invShared.jsx'
import { BottomNav } from './LogisticsReviewLayout.jsx'
import './logisticsIntakeRedesign.css'

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
.lrv .lik-rawwrap{margin:0 15px 10px;max-width:calc(100% - 30px);overflow-x:auto;border:1px solid #E1E7EC;border-radius:8px}
.lrv .lik-raw{width:100%;min-width:900px;border-collapse:collapse;font-size:12px;background:#fff}.lrv .lik-raw th{padding:6px 9px;text-align:left;background:#F7F9FA;color:#5E6B78;font-weight:600;white-space:nowrap}.lrv .lik-raw td{padding:7px 9px;border-top:1px solid #E6EBEF;vertical-align:top}
.lrv .lik-raw .lik-rawname{min-width:250px;overflow-wrap:anywhere;word-break:break-word}.lrv .lik-raw .lik-rawsrc{font-weight:650;margin-bottom:2px}.lrv .lik-raw .lik-rawfile{color:#5E6B78}.lrv .lik-raw .num{white-space:nowrap;font-variant-numeric:tabular-nums}.lrv .lik-raw .lik-rawops{white-space:nowrap}.lrv .lik-raw .lik-rawops>*{margin:0 4px 3px 0}
.lrv .lik-reqfiles{display:flex;align-items:baseline;gap:5px 12px;flex-wrap:wrap;margin:0 15px 12px;padding:7px 10px;background:#F7F9FA;border-radius:7px;color:#5E6B78;font-size:12px}.lrv .lik-reqfiles b{color:#3D4A57}
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
.lrv .lik-subjrecs{margin:0 14px 12px}.lrv .lik-subjrecs table{min-width:650px}
.lrv .lik-issues{display:flex;gap:8px;align-items:flex-start;padding:10px 14px;margin-bottom:12px;border:1px solid #EDD9AE;background:#FFFAEF;border-radius:9px;font-size:12.5px;line-height:1.6}
.lrv .lik-issues.ok{border-color:#BFDCCB;background:#F0F8F3}.lrv .lik-issues b{white-space:nowrap}.lrv .lik-issues ul{margin:0;padding-left:18px}.lrv .lik-issues.ok span{flex:1}
.lrv .lik-section{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.lrv .lik-section .sp{flex:1}.lrv .lik-section small{font-weight:400;color:#788591}
.lrv .lik-srctitle{padding:10px 15px 8px;font-size:12px;font-weight:700;color:#4A5763}.lrv .lik-empty{padding:12px 15px;color:#788591;font-size:12.5px}
.lrv .lik-req{border:1px solid #DCE2E7;border-radius:10px;background:#fff;overflow:hidden;margin-bottom:12px}.lrv .lik-req .ih{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;padding:10px 15px;border-bottom:1px solid #E6EBEF}
.lrv .lik-req .ih h4{margin:0;font-size:14px}.lrv .lik-req .ih .sp{flex:1}.lrv .lik-req .ihmeta{display:flex;gap:6px 14px;flex-wrap:wrap;color:#5E6B78;font-size:12px}.lrv .lik-req .ihmeta span{overflow-wrap:anywhere}.lrv .lik-trio{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #E6EBEF;border-radius:8px;overflow:hidden;margin:12px 15px 0}
.lrv .lik-trio>div{padding:7px 12px;display:flex;align-items:baseline;gap:7px;border-right:1px solid #E6EBEF;min-width:0}.lrv .lik-trio>div:last-child{border-right:0}.lrv .lik-trio .h{font-size:11.5px;color:#788591;white-space:nowrap}
.lrv .lik-trio .v{font-family:ui-monospace,Consolas,monospace;font-size:15px;font-weight:600;overflow-wrap:anywhere}.lrv .lik-trio .v.warnv{color:#99600F}
.lrv .lik-invwrap{padding:10px 15px 0;display:grid;gap:8px}.lrv .lia-invoice{border:1px solid #E1E7EC;border-radius:8px;padding:10px 12px;display:grid;grid-template-columns:minmax(150px,1.25fr) minmax(110px,1fr) minmax(100px,.7fr) auto;align-items:center;gap:6px 14px;min-width:0;background:#FCFDFD}
.lrv .lia-invoice .ino{font-weight:650;overflow-wrap:anywhere}.lrv .lia-invoice .itp,.lrv .lia-invoice .iamt{color:#5E6B78;font-size:12.5px;min-width:0;overflow-wrap:anywhere}.lrv .lia-invoice .iamt{font-family:ui-monospace,Consolas,monospace;color:#273642;font-weight:650}.lrv .lia-invoice .iact{display:flex;justify-content:flex-end;align-items:center;gap:6px;flex-wrap:wrap}.lrv .lia-invoice .ierr{grid-column:1/-1;color:#B83232;font-size:12px;line-height:1.55;overflow-wrap:anywhere}.lrv .lia-invoice .idone{grid-column:1/-1;color:#18784C;font-size:12px}
.lrv .lik-chk{margin:10px 15px 12px;padding:9px 11px;border-radius:8px;background:#FFFAEF;border:1px solid #EDD9AE;font-size:12.5px;line-height:1.55}.lrv .lik-chk.done{background:#F0F8F3;border-color:#BFDCCB}.lrv .lik-chk>div{display:flex;align-items:flex-start;gap:7px;min-width:0}.lrv .lik-chk .x{display:block;color:#788591;overflow-wrap:anywhere}.lrv .lik-chk .badtext{color:#B83232}.lrv .lik-mk{font-weight:700;flex:none}.lrv .lik-mk.ok{color:var(--ok)}.lrv .lik-mk.bad{color:var(--bad)}.lrv .lik-mk.warn{color:var(--warn)}
.lrv .lik-later{margin:12px 15px 0;border:1px dashed #EDD9AE;background:#FDF9F0;border-radius:8px;padding:10px 13px;display:grid;gap:6px;font-size:12.5px}.lrv .lik-later.done{border-style:solid;border-color:#BFDCCB;background:#F2F9F4}.lrv .lik-later .lr-h{display:flex;align-items:center;gap:8px;font-weight:600;flex-wrap:wrap}.lrv .lik-later .lr-h .sp{flex:1}.lrv .lik-later .lr-meta{color:#5E6B78;overflow-wrap:anywhere}
.lrv .lia-summary{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:9px 14px;border-top:1px solid #E6EBEF;white-space:nowrap;font-size:12.5px;color:#5E6B78}.lrv .lia-summary b{color:#273642;font-variant-numeric:tabular-nums}.lrv .lia-work{display:grid;grid-template-columns:270px minmax(0,1fr);border:1px solid #DCE2E7;border-radius:10px;background:#fff;overflow:hidden;min-height:380px}
.lrv .lia-queue{border-right:1px solid #DCE2E7;background:#FAFBFC;min-width:0}.lrv .lia-qtools{padding:10px;border-bottom:1px solid #E6EBEF;display:grid;gap:7px}.lrv .lia-qtools input{width:100%;box-sizing:border-box}.lrv .lia-qfilter{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}.lrv .lia-qfilter button{min-width:0;padding-left:5px;padding-right:5px;white-space:nowrap}
.lrv .lia-qlist{max-height:650px;overflow:auto}.lrv .lia-qitem{display:grid;width:100%;text-align:left;gap:4px;padding:11px 12px;border:0;border-bottom:1px solid #E6EBEF;background:transparent;cursor:pointer;color:inherit;transition:background .15s}
.lrv .lia-qitem:hover{background:#F0F5F8}.lrv .lia-qitem.on{background:#EAF2F7;box-shadow:inset 3px 0 var(--accent)}.lrv .lia-qitem .qt{display:flex;justify-content:space-between;gap:8px;font-weight:650}.lrv .lia-qitem .qt>span{min-width:0;overflow-wrap:anywhere}.lrv .lia-qitem .qs{display:flex;justify-content:space-between;gap:6px;color:#5E6B78;font-size:11.5px}.lrv .lia-qitem .qn{color:#99600F}
.lrv .lia-detail{min-width:0}.lrv .lia-detail .lik-req{border:0;border-radius:0;margin:0}.lrv .lia-voucher{border-top:1px solid #E6EBEF;padding:10px 14px}.lrv .lia-voucher>summary{cursor:pointer;font-weight:650;font-size:12.5px}.lrv .lia-viewload{padding:40px 15px;text-align:center;color:#5E6B78}.lrv .lia-viewerr{padding:25px 15px;color:#B83232;overflow-wrap:anywhere}
.lrv .lik-diffbtn{cursor:pointer;font:inherit}.lrv .lik-issue-open{margin-left:auto;flex:none}
.lrv .lik-diff{display:grid;gap:13px;font-size:13px;line-height:1.6}.lrv .lik-diff-intro{display:grid;gap:3px;padding:10px 12px;border-left:3px solid var(--warn);background:var(--amber-bg);border-radius:0 8px 8px 0}.lrv .lik-diff-intro span{color:var(--ink-2)}
.lrv .lik-diff-table{overflow-x:auto;border:1px solid var(--line);border-radius:8px}.lrv .lik-diff-table table{width:100%;border-collapse:collapse}.lrv .lik-diff-table th,.lrv .lik-diff-table td{padding:8px 10px;border-bottom:1px solid var(--line)}.lrv .lik-diff-table tr:last-child td{border-bottom:0}
.lrv .lik-diff-subtitle{margin:2px 0 -5px;font-size:13.5px}.lrv .lik-diff-reasons{display:grid;gap:8px}.lrv .lik-diff-reason{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:11px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg-sub)}
.lrv .lik-diff-reason>div{min-width:0}.lrv .lik-diff-reason p{margin:4px 0;color:var(--ink-2)}.lrv .lik-diff-reason small{display:block;color:var(--ink-3);overflow-wrap:anywhere}.lrv .lik-diff-reason>a{flex:none}.lrv .lik-diff-footnote{color:var(--ink-3);font-size:12px}
@media(max-width:900px){.lrv .lia-work{grid-template-columns:220px minmax(0,1fr)}.lrv .lia-invoice{grid-template-columns:minmax(130px,1fr) minmax(95px,.8fr) auto}.lrv .lia-invoice .iamt{grid-row:2}.lrv .lia-invoice .iact{grid-column:2/-1;grid-row:2}}
@media(max-width:700px){.lrv .lik-issue-open{width:100%;min-height:40px;margin:4px 0 0}.lrv .lik-diff-reason{display:grid}.lrv .lik-diff-reason>a{width:100%;min-height:40px;text-align:center}.lrv .lik-diff-table th,.lrv .lik-diff-table td{padding:7px 8px;white-space:nowrap}}
@media(max-width:700px){.lrv .vflow{grid-template-columns:1fr}.lrv .vlink{padding:8px 0}.lrv .vlink::before{display:none}.lrv .lia-work{grid-template-columns:1fr}.lrv .lia-queue{border-right:0;border-bottom:1px solid #DCE2E7}.lrv .lia-qlist{max-height:220px}.lrv .lia-summary{gap:8px;padding:8px 10px;font-size:11.5px}.lrv .lik-trio{margin-left:10px;margin-right:10px}.lrv .lik-trio>div{padding:7px;display:grid;gap:2px}.lrv .lik-invwrap{padding-left:10px;padding-right:10px}.lrv .lia-invoice{grid-template-columns:minmax(0,1fr) auto;padding:9px 10px;gap:5px 10px}.lrv .lia-invoice .itp{grid-row:2}.lrv .lia-invoice .iamt{grid-row:2;text-align:right}.lrv .lia-invoice .iact{grid-column:1/-1;grid-row:3;justify-content:flex-start}.lrv .lik-chk{margin-left:10px;margin-right:10px}.lrv .lik-later{margin-left:10px;margin-right:10px}.lrv .lik-hist>div{grid-template-columns:1fr}}
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

function intakeIssueFacts(data) {
  const subjects = (data.subject_recons || []).filter(r => r.diff != null && !isZero(r.diff))
  if (!subjects.length && data.diff != null && !isZero(data.diff)) subjects.push({ subject: data.carrier, bill_total: data.bill_total, req_total: data.req_total, diff: data.diff })
  const reasons = []
  ;(data.files || []).forEach(f => {
    const rep = f.report || {}
    ;(rep.duplicates || []).forEach(d => reasons.push({ kind: 'duplicate', file: f, sheet: d.sheet, duplicateOf: d.duplicate_of }))
    ;(rep.sheets || []).filter(s => s.verdict === 'gap' && s.total && Math.abs(s.gap || 0) >= 0.01).forEach(s => reasons.push({ kind: 'gap', file: f, sheet: s.sheet, amount: s.amount, total: s.total.sum, gap: s.gap }))
  })
  const signature = subjects.length || reasons.length ? JSON.stringify({ subjects: subjects.map(r => [r.subject, r.bill_total, r.req_total, r.diff]), reasons: reasons.map(r => [r.kind, r.sheet, r.amount, r.total, r.gap]) }) : ''
  return { subjects, reasons, signature }
}

function IntakeIssueModal({ data, period, facts, onClose }) {
  return <Modal title="账单与请款金额不符" onClose={onClose} width={780} footer={<button className="btn pri" onClick={onClose}>知道了</button>}>
    <div className="lik-diff">
      <div className="lik-diff-intro"><b>{data.carrier} · {period}</b><span>系统按账单明细读取的金额与钉钉请款不一致，请先核对以下原因。</span></div>
      {!!facts.subjects.length && <div className="lik-diff-table"><table className="fxtbl"><thead><tr><th>主体</th><th className="num">系统账单</th><th className="num">请款金额</th><th className="num">差额</th></tr></thead><tbody>{facts.subjects.map(r => <tr key={r.subject}><td><b>{r.subject}</b></td><td className="num mono">{money(r.bill_total)}</td><td className="num mono">{money(r.req_total)}</td><td className="num mono diffpos">{r.diff > 0 ? '+' : '−'}{money(Math.abs(r.diff))}</td></tr>)}</tbody></table></div>}
      <h3 className="lik-diff-subtitle">检测到的原因</h3>
      <div className="lik-diff-reasons">{facts.reasons.map((r, i) => <div className="lik-diff-reason" key={r.kind + r.sheet + i}>
        <div><b>{r.kind === 'duplicate' ? `发现完全重复工作表「${r.sheet}」` : `工作表「${r.sheet}」明细与表底合计不一致`}</b>
          <p>{r.kind === 'duplicate' ? `与「${r.duplicateOf}」内容相同，系统已只计一次。请删除重复页后再复核，避免其他版本重复计费。` : `系统读取 ${money(r.amount)}，表底合计 ${money(r.total)}，相差 ${money(Math.abs(r.gap))}。请检查表底合计公式是否覆盖全部明细行。`}</p>
          <small>{r.file.name || r.file.src || '账单原件'}</small></div>
        <a className="btn sm" href={reviewRawUrl(data.carrier, period, r.file.src)} target="_blank" rel="noopener">下载原件</a>
      </div>)}</div>
      {!facts.reasons.length && <div className="lik-diff-reason"><div><b>当前账单与请款口径不一致</b><p>请检查是否漏传账单、替换了错误版本，或请款中存在未体现在账单里的调整。</p></div></div>}
      <div className="lik-diff-footnote">这里只说明差异，不会自动确认、修改请款或登记已复核。</div>
    </div>
  </Modal>
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
      <tbody>{[...rows].sort((a, b) => ord(a) - ord(b) || a.carrier.localeCompare(b.carrier, 'zh')).map(r => { const [lb, cl] = IK_V[r.status] || [r.status, 'neu'], rs = r.subject_recons || [], multi = rs.length > 1; return <tr key={r.carrier}>
        <td className="ovcar">{r.carrier}</td><td className="num">{multi ? rs.map(s => <div key={s.subject}>{s.subject} {s.n_req} 张 · {money(s.req_total)}</div>) : r.n_req ? `${r.n_req} 张 · ${money(r.req_total)}` : <span className="dim">还没来</span>}</td><td className="num">{r.n_raw ? `${r.n_raw} 份` : <span className="dim">—</span>}</td><td className="num mono">{multi ? rs.map(s => <div key={s.subject}>{s.subject} {money(s.bill_total)}</div>) : r.bill_total != null ? money(r.bill_total) : '—'}</td>
        <td className={'num mono ' + (!multi && r.diff != null ? isZero(r.diff) ? 'diffneg' : 'diffpos' : '')}>{multi ? rs.map(s => <div key={s.subject} className={s.diff == null ? '' : isZero(s.diff) ? 'diffneg' : 'diffpos'}>{s.subject} {s.diff == null ? '—' : isZero(s.diff) ? '0' : money(s.diff)}</div>) : r.diff == null ? '—' : isZero(r.diff) ? '0' : money(r.diff)}</td><td style={{ fontSize: 12 }}>{r.how}</td><td><span className={'pill ' + cl}>{lb}</span></td></tr> })}</tbody>
    </table></div>}{d && !d.err && <div className="ovfoot">「读出来的」＝复核台现存账单金额；「结论」＝按当前取数说明重新核对原件。<button className="lnk" onClick={load}>重读</button></div>}</>}
  </details>
}

const STANDARD_COLUMNS = [
  ['A', '主体', '确定计入哪个公司'], ['B', '金蝶系统单据编号', '多个单号用逗号或分号分隔'], ['C', '费用类型', '出库、入库、研发外购等'],
  ['D', '数量计算标准', '重量、件数、箱数、体积、托、单、车或免核'], ['E', '账单数量', '只取供应商数量，不倒算'], ['F', '数量单位', 'kg、件、箱、托、单、车等'],
  ['G', '费用总额', '本行实际账单金额'], ['H', '费用拆分1', '运费'], ['I', '费用拆分2', '附加费'], ['J', '备注', '补充说明，可选'],
]

function StandardBillGuide() {
  return <section className="lrx-panel lir-schema">
    <div className="lrx-panel-head"><div><h2>A–J 固定取数列</h2><small>人工加工时逐行填写；B 列多单号支持中英文逗号、分号</small></div></div>
    <div className="lir-schema-grid">{STANDARD_COLUMNS.map(([col, name, use]) => <div className="lir-schema-cell" key={col}><b>{col}</b><span>{name}</span><small>{use}</small></div>)}</div>
    <div className="lir-footnote">有费用拆分时 G = H + I；E 只取供应商数量，金蝶数量仅用于后续核对。标准块可独立放置，也可追加在原明细后面。</div>
  </section>
}

function OriginalFiles({ carrier, period, locked, flash }) {
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState('')
  const [replaceId, setReplaceId] = useState(0)
  const [state, setState] = useState('loading')
  const [error, setError] = useState('')
  const input = useRef(null)
  const load = async () => {
    setState('loading'); setError('')
    try { const r = await reviewOriginalFiles(carrier, period); setFiles(r.files || []); setState('ready') }
    catch (e) { setState('error'); setError(e.message || String(e)); flash('原始附件读取失败：' + e.message) }
  }
  useEffect(() => { setFiles([]); load() }, [carrier, period])
  const pick = id => { setReplaceId(id || 0); if (input.current) input.current.click() }
  const upload = async e => {
    const file = e.target.files && e.target.files[0]
    e.target.value = ''
    if (!file) return
    setBusy('upload')
    try {
      await reviewOriginalUpload(carrier, period, file, replaceId)
      flash(replaceId ? '原始附件已替换，不参与取数' : '原始附件已上传，不参与取数')
      await load()
    } catch (err) { flash('原始附件上传失败：' + err.message) } finally { setBusy(''); setReplaceId(0) }
  }
  const del = async file => {
    if (!window.confirm(`删除原始附件「${file.name}」？只删除追溯附件，不影响标准账单和复核结果。`)) return
    setBusy('del' + file.id)
    try { await reviewOriginalDelete(carrier, period, file.id); flash('原始附件已删除'); await load() }
    catch (err) { flash('删除失败：' + err.message) } finally { setBusy('') }
  }
  return <article className="lir-file-card" aria-busy={state === 'loading'}>
    <div className="lir-file-kind">供应商原始账单 · 可选附件</div>
    <div className="lir-file-title"><h3>{state === 'ready' && files.length ? `${files.length} 份原始附件` : '原始附件'}</h3>{state === 'ready' && <span className={'lrx-tag ' + (files.length ? 'ok' : '')}>{files.length ? '已留档' : '未上传'}</span>}</div>
    <p>只用于留档追溯，系统不解析此文件，也不与标准逐笔账单混用。</p>
    <input ref={input} type="file" accept=".xlsx,.xls,.pdf" hidden onChange={upload} />
    {state === 'loading' && <div className="lir-file-empty" role="status">正在读取原始附件…</div>}
    {state === 'error' && <div className="lir-file-error" role="alert">原始附件读取失败：{error}<button className="lrx-btn" onClick={load}>重试</button></div>}
    {state === 'ready' && !files.length && <div className="lir-file-empty">当前没有原始附件，可按需上传。</div>}
    {state === 'ready' && files.length > 0 && <div className="lir-source-list">{files.map(file => <div className="lir-source" key={file.id}>
      <div className="lir-source-main"><b title={file.name}>{file.name}</b><span>{file.by || '—'} · {file.at || '—'}{file.size != null ? ` · ${kb(file.size)}` : ''}</span></div>
      <div className="lir-source-actions"><a className="lrx-btn" href={reviewOriginalUrl(file.id)} target="_blank" rel="noopener">下载</a><button className="lrx-btn" disabled={!!locked || !!busy} onClick={() => pick(file.id)}>替换</button><button className="lrx-btn danger" disabled={!!locked || !!busy} onClick={() => del(file)}>{busy === 'del' + file.id ? '删除中…' : '删除'}</button></div>
    </div>)}</div>}
    <div className="lir-file-actions"><button className="lrx-btn" disabled={!!locked || !!busy} onClick={() => pick(0)}>{busy === 'upload' ? '上传中…' : files.length ? '新增原始附件' : '上传原始附件'}</button></div>
  </article>
}

export function IntakeStep({ data, period, locked, flash, onOpenBills, onChanged, onNext, onRetry }) {
  const [more, setMore] = useState({})
  const [busy, setBusy] = useState('')
  const [onlyBad, setOnlyBad] = useState(false)
  const [issueOpen, setIssueOpen] = useState(false)
  const issueFacts = data && !data.err ? intakeIssueFacts(data) : { subjects: [], reasons: [], signature: '' }
  useEffect(() => {
    if (!data || data.err || data.register || !issueFacts.signature) return
    const key = `lik-intake-issue:${data.carrier}:${period}:${issueFacts.signature}`
    try {
      if (window.sessionStorage.getItem(key)) return
      window.sessionStorage.setItem(key, '1')
    } catch { /* 会话存储不可用时仍提示，但不写持久标记 */ }
    setIssueOpen(true)
  }, [data, period, issueFacts.signature])
  if (!data) return <section className="lrx-panel"><div className="lrx-empty" role="status">读账单原件、按取数说明检查中…</div></section>
  if (data.err) return <section className="lrx-panel"><div className="lir-page-error" role="alert">{data.err}<button className="lrx-btn" onClick={onRetry}>重试</button></div></section>
  const files = data.files || [], reps = files.map(f => f.report).filter(Boolean)
  const totSheets = reps.flatMap(r => (r.sheets || []).filter(s => s.role === 'detail' && s.total))
  const dupRows = reps.reduce((a, r) => a + (r.dup_rows || 0), 0)
  const billSelfTotal = totSheets.length && !dupRows && reps.every(r => (r.sheets || []).filter(s => s.role === 'detail').every(s => s.total)) ? totSheets.reduce((a, s) => a + s.total.sum, 0) : null
  const [vl] = IK_V[data.verdict] || ['', 'neu']
  const nDocs = (data.bills || []).reduce((a, b) => a + (b.n_doc || 0), 0)
  const nDetails = (data.bills || []).reduce((a, b) => a + (b.n_detail || 0), 0)
  const recons = data.subject_recons || []
  const link1 = billSelfTotal == null ? null : isZero(data.bill_total - billSelfTotal)
  const link2 = recons.length ? (recons.some(r => r.diff != null && !isZero(r.diff)) ? false : recons.every(r => r.diff != null) ? true : null) : data.diff == null ? null : isZero(data.diff)
  const issues = []
  if (!data.has_spec) issues.push('尚未配置取数说明，请先完成承运商取数配置')
  if (!(data.bills || []).length) issues.push('尚未解析账单，请上传账单或检查钉钉附件')
  if (data.verdict !== 'ok') issues.push(`原件核对：${vl || '尚未形成结论'}`)
  if (link1 === false) issues.push(`解析金额与账单原件合计相差 ${money(Math.abs(data.bill_total - billSelfTotal))}`)
  if (recons.length) recons.forEach(r => {
    if (r.diff == null) issues.push(`${r.subject}：${r.req_total == null ? '有账单，但没有请款单' : '有请款单，但没有解析到该主体账单'}`)
    else if (!isZero(r.diff)) issues.push(`${r.subject}：账单与请款单相差 ${money(Math.abs(r.diff))}`)
  })
  else if (link2 !== true) issues.push(data.diff == null ? '账单与请款单暂时无法核对' : `账单与请款单相差 ${money(Math.abs(data.diff))}`)
  const ready = data.register || issues.length === 0
  const reparse = f => {
    if (!window.confirm(`用存的原件「${f.name || f.src || '账单'}」按现在的取数说明重新解析一遍？\n\n人工改过的主体/费用类型、写过的备注按单号保留；逐笔、逐单的结论会跟着重算。`)) return
    setBusy('re' + f.id)
    reviewReparse(data.carrier, period, f.src).then(r => { flash(`已重新解析：明细 ${r.detail} 行，合计 ${money(r.before)} → ${money(r.after)}${r.notes ? `，备注写回 ${r.notes} 张` : ''}`); onChanged() }).catch(e => flash('重新解析失败：' + e.message)).finally(() => setBusy(''))
  }
  const reqByRole = {}; (data.req_files || []).forEach(f => { (reqByRole[f.role || 'other'] = reqByRole[f.role || 'other'] || []).push(f) })
  const billBySrc = Object.fromEntries((data.bills || []).map(b => [b.src || '', b]))
  const detailRows = files.filter(f => f.report).flatMap(f => (f.report.sheets || []).map((s, i) => ({ f, s, i, bad: s.verdict !== 'ok' }))).filter(x => x.s.role === 'detail')
  const sheetRows = [...detailRows].sort((a, b) => Number(b.bad) - Number(a.bad)).filter(x => !onlyBad || x.bad)
  const readTotal = detailRows.length && detailRows.every(({ s }) => s.amount != null) ? detailRows.reduce((sum, { s }) => sum + Number(s.amount), 0) : null
  const sheetBillTotal = detailRows.length && !dupRows && detailRows.every(({ s }) => s.total) ? detailRows.reduce((sum, { s }) => sum + Number(s.total.sum), 0) : null
  const sheetReady = detailRows.length > 0 && !dupRows && detailRows.every(({ s }) => s.verdict === 'ok')
  return <>
    <section className="lrx-panel lir-files">
      <div className="lrx-panel-head"><div><h2>文件与来源</h2><small>标准账单是取数依据；原始附件只作独立留档追溯</small></div><span className={'lrx-tag ' + (data.register ? 'info' : ready ? 'ok' : 'pending')}>{data.register ? '登记制无需标准账单' : ready ? '接入已核对' : `待处理 ${issues.length} 项`}</span></div>
      <div className="lir-file-grid">
        <article className="lir-file-card">
          <div className="lir-file-kind">标准逐笔账单 · 必需</div>
          <div className="lir-file-title"><h3>{files.length ? `${files.length} 份标准账单` : '标准逐笔账单'}</h3><span className={'lrx-tag ' + ((data.bills || []).length ? 'ok' : 'pending')}>{(data.bills || []).length ? `已解析 ${(data.bills || []).length} 份` : '未形成解析结果'}</span></div>
          <p>{(data.bills || []).length ? `${nDetails} 行逐笔 · ${nDocs} 张单 · 读取金额 ${money(data.bill_total)}` : '上传人工加工后的 A–J 标准账单，系统不会从原始附件猜测取数。'}</p>
          {!files.length && <div className="lir-file-empty">{data.register ? '登记制无需上传标准账单。' : data.has_spec ? '当前没有标准账单，可手工上传或等待钉钉附件自动接入。' : '尚未配置取数说明，暂不能上传解析。'}</div>}
          {files.length > 0 && <div className="lir-source-list">{files.map(f => {
            const name = f.name || '（没记文件名）', bill = billBySrc[f.src || '']
            return <div className="lir-source" key={f.id}>
              <div className="lir-source-main"><b title={name}>{name}</b><span>{f.src || '账单'} · {f.origin || '来源未记录'} · {f.by || '操作人未记录'}</span><span>{f.at || '时间未记录'}{f.size != null ? ` · ${kb(f.size)}` : ''}{bill ? ` · 解析金额 ${money(bill.amount)}` : ' · 尚未关联解析结果'}</span></div>
              <div className="lir-source-actions"><a className="lrx-btn" href={reviewRawUrl(data.carrier, period, f.src)} target="_blank" rel="noopener">下载</a><button className="lrx-btn" disabled={!!locked || !!busy} title={locked || '用新文件替换这一份，其他账单不动'} onClick={() => onOpenBills(f.src)}>替换</button><button className="lrx-btn" disabled={!!locked || !!busy} title={locked || '用保存的原件按当前取数说明重新解析'} onClick={() => reparse(f)}>{busy === 're' + f.id ? '解析中…' : '重新解析'}</button></div>
            </div>
          })}</div>}
          <div className="lir-file-actions"><button className="lrx-btn primary" disabled={!!locked || !data.has_spec} title={locked || (!data.has_spec ? '请先配置取数说明' : '上传、替换或删除标准账单')} onClick={onOpenBills}>上传 / 替换 / 删除标准账单</button></div>
        </article>
        <OriginalFiles carrier={data.carrier} period={period} locked={locked} flash={flash} />
      </div>
    </section>

    <section className="lrx-panel lir-recon">
      <div className="lrx-panel-head"><div><h2>逐页金额勾稽</h2><small>仅列计费明细页；说明页与汇总页不重复计入</small></div><div className="lrx-panel-actions"><span className={'lrx-tag ' + (sheetReady ? 'ok' : detailRows.some(x => x.bad) || dupRows ? 'bad' : 'pending')}>{sheetReady ? `合计 ${money(readTotal)}` : dupRows ? `发现 ${dupRows} 行重复` : '逐页待核对'}</span><button className={'lrx-btn' + (onlyBad ? ' primary' : '')} disabled={!detailRows.length} onClick={() => setOnlyBad(!onlyBad)}>{onlyBad ? '查看全部' : '只看异常'}</button></div></div>
      {!reps.length && <div className="lrx-empty">没有可勾稽的标准账单解析报告</div>}
      {reps.length > 0 && !detailRows.length && <div className="lrx-empty">解析报告中没有计费明细页</div>}
      {detailRows.length > 0 && !sheetRows.length && <div className="lrx-empty">当前筛选下没有异常计费页</div>}
      {sheetRows.length > 0 && <div className="lir-table-scroll"><table className="lrx-table lir-recon-table"><thead><tr><th>计费页</th><th className="num">逐笔行</th><th className="num">读取金额</th><th className="num">账单金额</th><th>结论</th></tr></thead><tbody>{sheetRows.map(({ f, s, i }) => {
        const k = f.id + '|' + i, [lb, cl] = IK_V[s.verdict] || [s.verdict, 'neu']
        return <React.Fragment key={k}><tr><td><b>{s.sheet}</b>{files.length > 1 && <span className="lrx-cell-meta">{f.name || f.src}</span>}</td><td className="num">{s.n != null ? s.n : '—'}</td><td className="num">{s.amount != null ? money(s.amount) : '—'}</td><td className="num">{s.total ? money(s.total.sum) : '—'}</td><td><span className={'lrx-tag ' + (cl === 'ok' ? 'ok' : cl === 'bad' ? 'bad' : cl === 'warn' ? 'pending' : '')}>{lb}</span>{sheetSay(s) && <span className="lrx-cell-meta">{sheetSay(s)}</span>}<button className="lrx-btn subtle lir-columns-toggle" aria-expanded={!!more[k]} onClick={() => setMore(o => ({ ...o, [k]: !o[k] }))}>{more[k] ? '收起取数列' : '查看取数列'}</button></td></tr>{more[k] && <tr className="lik-more"><td colSpan="5"><Chips s={s} /></td></tr>}</React.Fragment>
      })}</tbody><tfoot><tr><td>合计</td><td className="num">{detailRows.reduce((sum, { s }) => sum + Number(s.n || 0), 0)}</td><td className="num">{money(readTotal)}</td><td className="num">{money(sheetBillTotal)}</td><td><span className={'lrx-tag ' + (sheetReady ? 'ok' : 'pending')}>{sheetReady ? '一致' : '待处理'}</span></td></tr></tfoot></table></div>}
      <div className="lir-footnote">合计按接口返回的未舍入金额汇总后显示两位小数；有重复行或缺账单合计时不补造总额。</div>
    </section>

    <StandardBillGuide />

    <details className="lrx-panel lir-aux"><summary><span>主体勾稽与差异原因</span><small>{data.register ? '登记制无需账单勾稽' : ready ? '当前未发现待处理差异' : `${issues.length} 项待处理`}</small></summary><div className="lir-aux-body">
      {!data.register && (ready ? <p>当前账单解析、原件合计与请款金额未发现待处理项；后续替换账单会重新计算。</p> : <ul className="lir-issue-list">{issues.map((x, i) => <li key={i}>{x}</li>)}</ul>)}
      {issueFacts.signature && <button className="lrx-btn" onClick={() => setIssueOpen(true)}>查看差异原因</button>}
      {recons.length > 0 && <div className="lir-table-scroll"><table className="lrx-table lir-subject-table"><thead><tr><th>主体</th><th className="num">解析入账金额</th><th className="num">请款单</th><th className="num">差额</th><th>结论</th></tr></thead><tbody>{recons.map(r => <tr key={r.subject}><td><b>{r.subject}</b></td><td className="num">{money(r.bill_total)}</td><td className="num">{r.req_total == null ? '—' : <>{money(r.req_total)}<span className="lrx-cell-meta">{r.n_req} 张</span></>}</td><td className="num">{r.diff == null ? '—' : isZero(r.diff) ? '0.00' : money(r.diff)}</td><td>{r.diff == null ? <span className="lrx-tag pending">待核对</span> : isZero(r.diff) ? <span className="lrx-tag ok">一致</span> : <button className="lrx-btn" onClick={() => setIssueOpen(true)}>金额不符</button>}</td></tr>)}</tbody></table></div>}
    </div></details>

    <details className="lrx-panel lir-aux"><summary><span>钉钉附件与解析记录</span><small>请款附件 {(data.req_files || []).length} 份 · 历史 {(data.history || []).length} 条</small></summary><div className="lir-aux-body">
      <div className="lir-request-files"><b>钉钉请款单附件</b><span>{Object.keys(reqByRole).length ? Object.entries(reqByRole).map(([k, xs]) => `${ROLE_CN[k] || '其他'} ${xs.length}`).join(' · ') : '当前请款单没有附件'}</span><span>随请款单自动接入，无需重复上传。</span></div>
      {(data.history || []).length > 0 ? <div className="lik-hist">{data.history.slice(0, 8).map((h, i) => <div key={i}><span className="d">{h.at}</span><span>{h.by}</span><span>{h.action.replace('物流复核-', '')}：{h.detail}</span></div>)}</div> : <p>暂无解析操作记录。</p>}
    </div></details>
    <Overview period={period} />
    <BottomNav info={data.register ? '登记制无需标准账单' : ready ? '标准账单与金额勾稽已完成' : `接入待处理 ${issues.length} 项 · 以当前真实读取结果为准`} next={{ label: '下一步：逐单核价核量 ›', onClick: onNext }} />
    {issueOpen && <IntakeIssueModal data={data} period={period} facts={issueFacts} onClose={() => setIssueOpen(false)} />}
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

export function InvAuditPanel({ inv3, billOf, onChanged, flash, locked, onLater, voucherFor, onRetry }) {
  const [busy, setBusy] = useState(''), [query, setQuery] = useState(''), [queueFilter, setQueueFilter] = useState('all'), [view, setView] = useState(null)
  const approving = useRef(new Set())
  const byInst = new Map()
  for (const raw of ((inv3 && inv3.blocks) || []).filter(b => !b.err)) {
    let b = byInst.get(raw.inst)
    if (!b) { b = { ...raw, invoices: [] }; byInst.set(raw.inst, b) }
    const seen = new Set(b.invoices.map(i => i.id != null ? 'id:' + i.id : 'no:' + (i.number || '')))
    for (const i of raw.invoices || []) {
      const k = i.id != null ? 'id:' + i.id : 'no:' + (i.number || '')
      if (!seen.has(k)) { b.invoices.push(i); seen.add(k) }
    }
    b.later = !!(b.later || raw.later)
    b.later_slip = b.later_slip || raw.later_slip
    if (b.invoices.every(i => typeof i.gross === 'number')) b.inv_total = b.invoices.reduce((s, i) => s + i.gross, 0)
  }
  const bs = [...byInst.values()]
  const facts = Object.fromEntries(bs.map(b => [b.inst, blockFacts(b, billOf(b.subject))]))
  const matchQueue = b => queueFilter === 'all' || (queueFilter === 'todo' ? !facts[b.inst].ready : queueFilter === 'invoice' ? !facts[b.inst].ready && facts[b.inst].iv.length > 0 : !facts[b.inst].ready && facts[b.inst].iv.length === 0)
  const visible = bs.filter(b => matchQueue(b) && (!query || `${b.subject}${b.bid}${b.payee}`.toLowerCase().includes(query.toLowerCase()))).sort((a, b) => Number(facts[a.inst].ready) - Number(facts[b.inst].ready))
  if (!inv3) return <section className="lrx-panel"><div className="lrx-empty" role="status">读请款单和发票管家中…</div></section>
  if (inv3.err) return <section className="lrx-panel"><div className="lir-page-error" role="alert">{inv3.err}<button className="lrx-btn" onClick={onRetry}>重试</button></div></section>
  if (!bs.length) return <section className="lrx-panel"><div className="lrx-empty">这家这月还没有钉钉请款单</div></section>
  const invs = bs.flatMap(b => b.invoices || []), nRev = invs.filter(i => i.review === 'approved').length
  const todo = bs.filter(b => !facts[b.inst].ready).length, allReady = todo === 0
  const queueCounts = {
    all: bs.length, todo,
    invoice: bs.filter(b => !facts[b.inst].ready && facts[b.inst].iv.length > 0).length,
    noinvoice: bs.filter(b => !facts[b.inst].ready && facts[b.inst].iv.length === 0).length,
  }
  const approve = async (b, i) => {
    const f = facts[b.inst]
    if (locked || busy || approving.current.has(i.id)) return false
    if (!b.folder) { flash('这张票没有票夹信息，请在发票管家里审'); return false }
    if (!f.amountOk || !f.sellerOk || !f.buyerOk) { flash('金额或销购方核对未通过，不能直接审核'); return false }
    if (!window.confirm(`审核通过这张发票？\n\n${i.number || ''} · 价税合计 ${money(i.gross)}\n系统金额、销方、购方核对已通过。`)) return false
    approving.current.add(i.id); setBusy('a' + i.id)
    try {
      const r = await invAuditItemsApprove({ folderId: b.folder, itemIds: [i.id] })
      if (r && r.ok === false) { flash('审核没通过：' + (r.msg || '')); return false }
      flash('已审核通过' + (r && r.folderDone ? '，整单通过' : '')); onChanged && onChanged()
      return true
    } catch (e) { flash('审核失败：' + e.message); return false } finally { approving.current.delete(i.id); setBusy('') }
  }
  const viewInvoice = async (b, i) => {
    if (!b.folder) { flash('这张票没有票夹信息，请在发票管家里看'); return }
    const key = b.inst + ':' + i.id
    setView({ key, b, summary: i, loading: true, error: '', item: null })
    try {
      const r = await invFolder(b.folder)
      const item = (r.items || []).find(x => String(x.id) === String(i.id))
      if (!item) throw new Error('票夹里没有找到这张发票，请刷新后重试')
      setView(v => v && v.key === key ? { ...v, item, loading: false } : v)
    } catch (e) { setView(v => v && v.key === key ? { ...v, loading: false, error: e.message || String(e) } : v) }
  }
  const approveButton = (b, i, f) => i.review === 'pending' && <button className="lrx-btn primary" disabled={!!locked || !!busy || !f.amountOk || !f.sellerOk || !f.buyerOk} title={locked || (!f.amountOk || !f.sellerOk || !f.buyerOk ? '先解决金额或销购方异常' : '审核通过即确认收到')} onClick={async () => { if (await approve(b, i)) setView(v => v && v.summary.id === i.id ? null : v) }}>{busy === 'a' + i.id ? '审核中…' : '审核通过'}</button>
  return <>
    <section className="lrx-panel lir-invoices">
      <div className="lrx-panel-head"><div><h2>请款单与发票</h2><small>逐单核对金额、销购方和审核状态；无票须确认后补</small></div><span className={'lrx-tag ' + (allReady ? 'ok' : 'pending')}>{allReady ? '发票核对完成' : `${todo} 张请款单待处理`}</span></div>
      <div className="lir-invoice-tools">
        <label className="lir-search"><span>搜索请款单</span><input type="search" aria-label="搜索请款单" placeholder="输入主体、请款单号或收款方" value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div className="lir-status-filters" aria-label="请款单状态筛选">{[['all', '全部'], ['todo', '全部待办'], ['invoice', '有票待审'], ['noinvoice', '无票待确认']].map(([k, label]) => <button key={k} data-queue-filter={k} aria-pressed={queueFilter === k} className={'lrx-btn' + (queueFilter === k ? ' primary' : '')} onClick={() => setQueueFilter(k)}>{label} <b>{queueCounts[k]}</b></button>)}</div>
        <div className="lir-invoice-counts"><span>待处理 <b>{todo}</b> 单</span><span>发票已审 <b>{nRev} / {invs.length}</b></span></div>
      </div>
      {!visible.length && <div className="lrx-empty">没有符合当前搜索与状态筛选的请款单</div>}
      <div className="lir-request-list">{visible.map(b => {
        const f = facts[b.inst], bill = billOf(b.subject), slip = b.later_slip
        const amountDiffs = []
        if (f.iv.length && !isZero((b.inv_total || 0) - (b.amount || 0))) amountDiffs.push(`发票－请款 ${money((b.inv_total || 0) - (b.amount || 0))}`)
        if (f.iv.length && bill != null && !isZero(bill - (b.amount || 0))) amountDiffs.push(`账单－请款 ${money(bill - (b.amount || 0))}`)
        const pending = f.issues.map(x => x === '账单 / 请款 / 发票金额未对平' ? `金额异常${amountDiffs.length ? '：' + amountDiffs.join('；') : ''}` : x)
        const checks = [
          { label: '金额', ok: f.iv.length ? f.amountOk : null, text: f.iv.length ? f.amountOk ? '账单、请款、发票对平' : '金额未对平' : '无票，待后补结论' },
          { label: '销方', ok: f.iv.length ? f.sellerOk : null, text: f.iv.length ? f.sellerOk ? '与请款收款方一致' : '与请款收款方不一致' : '无票暂不判断' },
          { label: '购方', ok: f.iv.length ? f.buyerOk : null, text: f.iv.length ? f.buyerOk ? '与请款主体一致' : '与请款主体不一致' : '无票暂不判断' },
          { label: '审核 / 后补', ok: f.iv.length ? f.allRev : f.laterOk, text: f.iv.length ? f.allRev ? '发票已全部审核' : '仍有发票待审核' : f.laterOk ? '发票后补已确认' : slip ? '后补单待确认' : '后补未登记' },
        ]
        return <article className="lir-request" key={b.inst} data-inst={b.inst}>
          <div className="lir-request-head"><div><h3>{b.subject}</h3><div className="lir-request-meta"><span>申请人 {b.applicant || '—'}</span><span>单号 <span className="lrx-code">{b.bid}</span></span><span>收款方 {b.payee || '—'}</span></div></div><span className={'lrx-tag ' + (f.ready ? 'ok' : 'pending')}>{f.ready ? '已完成' : '待处理'}</span>{b.folder && <a className="lrx-btn" href={`#/invaudit?folder=${encodeURIComponent(b.folder)}`} target="_blank" rel="noopener">完整发票审核 ↗</a>}</div>
          <div className="lir-request-amounts"><div><span>账单金额</span><b>{bill != null ? money(bill) : '—'}</b></div><div><span>请款金额</span><b>{money(b.amount)}</b></div><div><span>发票价税合计</span><b className={f.iv.length ? '' : 'pending'}>{f.iv.length ? money(b.inv_total) : '无票'}</b></div></div>
          <div className="lir-check-grid">{checks.map(check => <div className={'lir-check ' + (check.ok == null ? '' : check.ok ? 'ok' : 'bad')} key={check.label}><span>{check.label}</span><b>{check.ok == null ? '待补充资料' : check.ok ? '通过' : '未通过'}</b><small>{check.text}</small></div>)}</div>
          {f.iv.length > 0 && <div className="lir-invoice-list">{f.iv.map(i => {
            const sellerBad = norm(i.seller) !== norm(b.payee), buyerBad = norm(i.buyer) !== norm(b.subject_full)
            return <div className="lir-invoice" key={i.id != null ? i.id : i.number}><div className="lir-invoice-id"><b className="lrx-code">{i.number || i.id}</b><span>{i.type_short || i.type || '发票'}{i.deduct === false ? '（不抵扣）' : ''}</span></div><b className="lir-invoice-amount">￥{money(i.gross)}</b><div className="lir-invoice-actions"><button className="lrx-btn" disabled={!!view?.loading} onClick={() => viewInvoice(b, i)}>{view?.loading && view.summary.id === i.id ? '读取中…' : '看发票'}</button>{approveButton(b, i, f)}{i.review === 'approved' ? <span className="lrx-tag ok">已审核</span> : <span className="lrx-tag pending">待审核</span>}</div>{sellerBad && <div className="lir-invoice-error">销方异常：{i.seller || '—'} ≠ 收款方 {b.payee || '—'}</div>}{buyerBad && <div className="lir-invoice-error">购方异常：{i.buyer || '—'} ≠ 请款主体 {b.subject_full || b.subject}</div>}</div>
          })}</div>}
          {!f.iv.length && <div className={'lir-later' + (b.later ? ' done' : '')}><div><b>{b.later ? '发票后补已确认' : slip ? '后补单已登记，待财务确认' : '尚未登记发票后补'}</b><span>{slip ? `后补单 #${slip.id} · 登记人 ${slip.filed_by || '—'} · 预计到票 ${slip.expect_date || '—'}` : '业务登记后，财务在这里确认；也可到后补池查看。'}</span></div><div className="lir-later-actions">{!b.later && slip && onLater && <button className="lrx-btn primary" disabled={!!locked} onClick={() => onLater(b, true)}>确认发票后补</button>}{b.later && onLater && <button className="lrx-btn" disabled={!!locked} onClick={() => onLater(b, false)}>撤销后补确认</button>}{!b.later && !slip && <a className="lrx-btn primary" href="#/invlater">打开后补池</a>}</div></div>}
          <div className={'lir-request-conclusion' + (f.ready ? ' done' : '')}>{f.ready ? (f.iv.length ? '已完成：金额、销购方与发票审核均通过。' : '已完成：无票后补已确认。') : `待办：${pending.join('；')}`}</div>
          {voucherFor && <details className="lir-voucher"><summary>高级做账与后补处理</summary><div>{voucherFor(b)}</div></details>}
        </article>
      })}</div>
    </section>
    {view && <Modal title={`发票 ${view.summary.number || view.summary.id}`} onClose={() => setView(null)} width={1040} footer={<><button className="lrx-btn" onClick={() => setView(null)}>关闭</button>{view.item && approveButton(view.b, view.summary, facts[view.b.inst])}</>}>
      {view.loading && <div className="lia-viewload" role="status">正在读取发票…</div>}{view.error && <div className="lia-viewerr" role="alert">发票读取失败：{view.error}</div>}{view.item && <InvViewer item={view.item} height={520} />}
    </Modal>}
  </>
}
