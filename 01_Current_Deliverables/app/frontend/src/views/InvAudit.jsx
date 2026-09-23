// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票审核：左队列（待审核/已退回/已通过＋搜索＋无异常批量通过）＋右票夹（抬头卡/缩略条/看图器点字段框位置/字段核对/逐张可否抵扣/异常/留痕/通过·退回）
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 通过时带上看过的待审票 itemIds（审核期间进了新票→后端 409，提示后重读票夹）；
//   改了票面字段后没手动改判的票，可否抵扣跟着新建议走；票夹级异常用票夹详情自带的（深链打开也有）；拦截项按 itemIds 出「第 N 张」；留痕可只看当前这张票。
// 需求确认书 v1.4 第八节 + 技术方案 §5.2「发票审核」。共用组件全部来自 invShared.jsx，这里只拼页面。
// 权限：按 invConfig().can.auditAct 决定能不能动手（后端同样拦）；没开时照样能看，按钮置灰并说明找管理员开。
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  invConfig, invAuditQueue, invAuditBatch, invAuditApprove, invAuditReturn,
  invFolder, invItemUpdate, invItemRotate,
} from '../api.js'
import {
  money, fmtTime, useToast, usePoll, Modal, StatusBadges, itemBadges,
  ThumbStrip, InvViewer, FieldPanel, modalCovers,
} from './invShared.jsx'
import './inv-audit.css'

// ───────────────────────── 常量与小工具 ─────────────────────────

const TABS = [
  { key: 'pending', label: '待审核' },
  { key: 'returned', label: '已退回' },
  { key: 'done', label: '已通过' },
]
const PAGE_SIZE = 30
const FOLDER_ST = { collecting: '收票中', submitted: '待审核', approved: '已通过', returned: '已退回' }
const ITEM_REVIEW = { draft: '未提交', pending: '待审', approved: '已通过', returned: '已退回', void: '已作废' }
const KIND_RANK = { invoice: 0, receipt: 1, other: 2 }
const NO_PERM_HINT = '审核权限需要管理员单独开通'

const num = v => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
// 票夹统计：规范是 stats{…}；内核原始行是 counts{…}——两种都认，免得后端口径差一点就全显 0
const statOf = (f, k, alt) => {
  const s = f?.stats || {}, c = f?.counts || {}
  const v = s[k] ?? (alt ? c[alt] : undefined) ?? c[k]
  return v === undefined ? null : v
}
// 异常等级 → 徽标色（后端可能给 err/error/warn/warning/info 等写法）
const toneOf = (lv) => {
  const s = String(lv || '').toLowerCase()
  if (['err', 'error', 'high', 'red', 'danger', 'block'].includes(s)) return 'err'
  if (['warn', 'warning', 'mid', 'yellow', 'amber'].includes(s)) return 'warn'
  if (['ok', 'green'].includes(s)) return 'ok'
  return 'info'
}
const sortItems = (items) => [...(items || [])].sort((a, b) =>
  ((KIND_RANK[a.kind] ?? 3) - (KIND_RANK[b.kind] ?? 3)) || ((a.id || 0) - (b.id || 0)))
// 这张票要不要会计定「可否抵扣」：发票、没作废、还没审过
const needsDecision = it => it && it.kind === 'invoice' && it.review !== 'void' && it.review !== 'approved'
const defaultDeduct = it => (it.deductible === null || it.deductible === undefined) ? it.deductSuggest === 'yes' : !!it.deductible
// 票夹里还有没有要审的（决定能不能点通过/退回、字段能不能改）
// 以票为准（统计数可能慢一拍）；已通过的票夹又收到后补票时，那张票是 pending，也要能审
const isActionable = (folder, items) => {
  if (!folder) return false
  if (folder.status === 'submitted') return true
  if (items && items.length) return items.some(it => it.review === 'pending')
  return (num(statOf(folder, 'pendingReview', 'pending_items')) || 0) > 0
}
const errText = (e) => (e && (e.message || e.msg)) || String(e || '未知原因')
// 拦截项指向哪几张票：后端给 itemIds:[…]（老写法 itemId 也认）
const blockerIds = (b) => {
  if (!b || typeof b !== 'object') return []
  const ids = Array.isArray(b.itemIds) ? b.itemIds : (b.itemId !== undefined && b.itemId !== null ? [b.itemId] : [])
  return ids.filter(id => id !== null && id !== undefined && id !== '')
}
// 拦截清单里每条可能是字符串，也可能是 {code, msg/label/reason, itemIds:[…]}；orderOf 把票 id 换成「第 N 张」
const blockerText = (b, orderOf) => {
  if (b === null || b === undefined) return ''
  if (typeof b === 'string') return b
  const t = b.msg || b.label || b.reason || b.text || ''
  const ids = blockerIds(b)
  const nums = ids.map(id => { const n = orderOf ? orderOf(id) : 0; return n > 0 ? `第 ${n} 张` : `票 #${id}` })
  return (nums.length ? nums.join('、') + '：' : '') + (t || JSON.stringify(b))
}
/**
 * 通过时要带给后端的 itemIds：审核人眼前这批"待审"的票（后端只通过这些；审核期间又进了新票会回 409）。
 * @param {object[]} items 当前票夹里的票
 * @returns {number[]}
 */
export function pendingItemIds(items) {
  return (items || []).filter(it => it && it.review === 'pending' && it.status !== 'removed').map(it => it.id)
}
/**
 * 票面改动/重读之后重排可否抵扣：审核人没亲手点过的票跟着系统新建议走（改了票种/税额，建议从「可」变「不可」，判定也要跟着变）；
 * 点过的票保留审核人的选择。
 * @param {object} prev 现有判定 {itemId: bool}
 * @param {object[]} items 最新的票
 * @param {Set} touched 审核人亲手点过的票 id
 * @returns {object}
 */
export function mergeDecisions(prev, items, touched) {
  const n = { ...(prev || {}) }
  ;(items || []).forEach(it => {
    if (!needsDecision(it)) return
    if (!(it.id in n) || !(touched && touched.has(it.id))) n[it.id] = defaultDeduct(it)
  })
  return n
}
const isTyping = (t) => !!t && t !== document.body && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)

// ───────────────────────── 左：队列 ─────────────────────────

function QueueTabs({ tab, counts, onTab }) {
  return <div className="inv-au-tabs" role="tablist">
    {TABS.map(t => <button type="button" key={t.key} role="tab" aria-selected={tab === t.key}
      className={'inv-au-tab' + (tab === t.key ? ' on' : '')} onClick={() => onTab(t.key)}>
      {t.label}{counts[t.key] !== undefined && counts[t.key] !== null && <b>{counts[t.key]}</b>}
    </button>)}
  </div>
}

function AnomalyBadges({ list, max = 4 }) {
  const arr = Array.isArray(list) ? list : []
  if (!arr.length) return null
  const shown = arr.slice(0, max)
  return <span className="inv-badges">
    {shown.map((a, i) => <span key={(a.code || '') + i} className={'inv-badge ' + toneOf(a.level)} title={a.tip || a.label || ''}>{a.label || a.code}</span>)}
    {arr.length > max && <span className="inv-badge mute" title={arr.slice(max).map(a => a.label || a.code).join('、')}>+{arr.length - max}</span>}
  </span>
}

function QueueRow({ row, tab, on, checked, canCheck, onPick, onCheck }) {
  const invoices = statOf(row, 'invoices')
  const clean = !!row.clean
  return (
    <div className={'inv-au-row' + (on ? ' on' : '')} onClick={() => onPick(row.id)} role="button" tabIndex={0}
      onKeyDown={e => { if (e.key === 'Enter') onPick(row.id) }}>
      <div className="inv-au-row-ck" onClick={e => e.stopPropagation()}>
        {tab === 'pending' && clean && canCheck
          ? <input type="checkbox" checked={checked} onChange={e => onCheck(row.id, e.target.checked)} title="没有异常，可以批量通过" />
          : <span className="inv-au-ck-ph" />}
      </div>
      <div className="inv-au-row-main">
        <div className="inv-au-row-t">
          <span className="inv-au-row-title">{row.title || row.template || '（无标题）'}</span>
          <span className="inv-num inv-au-row-amt">{money(row.amount)}</span>
        </div>
        <div className="inv-au-row-sub">
          {row.template && row.title ? <span>{row.template}</span> : null}
          <span className="inv-num">{row.businessId || (row.source === 'manual' ? '手工票夹' : '—')}</span>
          <span>{row.applicant || '—'}</span>
          <span>{invoices !== null ? `${invoices} 张票` : ''}</span>
        </div>
        <div className="inv-au-row-b">
          {tab === 'pending' && clean && <span className="inv-badge ok">无异常</span>}
          <AnomalyBadges list={row.anomalies} />
          {tab === 'returned' && row.reviewNote && <span className="inv-au-row-note" title={row.reviewNote}>退回：{row.reviewNote}</span>}
          {tab === 'done' && <span className="inv-muted">{row.reviewedBy || ''} {row.reviewedAt ? fmtTime(row.reviewedAt) : ''}</span>}
          {row.selfReview ? <span className="inv-badge warn" title="主管理员审核了自己提交的票夹">自审</span> : null}
          <span className="inv-au-grow" />
          {tab === 'pending' && <span className="inv-muted">{fmtTime(row.submittedAt || row.updatedAt || row.createdAt)}</span>}
        </div>
      </div>
    </div>
  )
}

function BatchResult({ res, titleOf: liveTitle, onClose, onPick }) {
  if (!res) return null
  // 通过的票夹刷新后就不在待审列表里了，名字用批量前记下的
  const titleOf = id => (res.names && res.names[id]) || liveTitle(id)
  const done = Array.isArray(res.done) ? res.done : []
  const skipped = Array.isArray(res.skipped) ? res.skipped : []
  return <div className="inv-au-bres">
    <div className="inv-au-bres-h">
      <b>批量通过结果</b><span>通过 {done.length} 个{skipped.length ? `，跳过 ${skipped.length} 个` : ''}</span>
      <span className="inv-au-grow" /><button type="button" className="inv-x" onClick={onClose} aria-label="收起">×</button>
    </div>
    <ul>
      {done.map(id => <li key={'d' + id} className="ok"><span className="inv-badge ok">已通过</span>
        <button type="button" className="inv-lk inv-au-lkb" onClick={() => onPick(id)}>{titleOf(id)}</button></li>)}
      {skipped.map((s, i) => {
        const id = typeof s === 'object' && s ? s.id : s
        return <li key={'s' + i} className="skip"><span className="inv-badge warn">跳过</span>
          <button type="button" className="inv-lk inv-au-lkb" onClick={() => id && onPick(id)}>{titleOf(id)}</button>
          <span className="inv-au-bres-why">{(s && s.reason) || '有异常，要逐张审'}</span></li>
      })}
    </ul>
  </div>
}

function QueuePane({ tab, onTab, counts, qInput, setQInput, onSearch, queue, loading, err, page, setPage,
  selId, onPick, checked, setChecked, canAct, onBatch, batchBusy, batchErr, batchRes, setBatchRes }) {
  const rows = queue?.rows || []
  const total = queue?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const cleanIds = rows.filter(r => r.clean).map(r => r.id)
  const nChecked = cleanIds.filter(id => checked.has(id)).length
  const allOn = cleanIds.length > 0 && nChecked === cleanIds.length
  const titleOf = (id) => {
    const r = rows.find(x => x.id === id)
    return r ? (r.title || r.template || r.businessId || `票夹 #${id}`) + (r.businessId ? ` · ${r.businessId}` : '') : `票夹 #${id}`
  }
  const toggle = (id, v) => setChecked(s => { const n = new Set(s); if (v) n.add(id); else n.delete(id); return n })
  return (
    <aside className="inv-au-q">
      <QueueTabs tab={tab} counts={counts} onTab={onTab} />
      <div className="inv-au-search">
        <input className="inv-in" value={qInput} placeholder="搜单号、标题、申请人、收款方、发票号码"
          onChange={e => setQInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onSearch(qInput) ; if (e.key === 'Escape' && qInput) { setQInput(''); onSearch('') } }} />
      </div>
      {tab === 'pending' && canAct && cleanIds.length > 0 && (
        <div className="inv-au-batch">
          <label className="inv-au-ckall"><input type="checkbox" checked={allOn}
            onChange={e => setChecked(e.target.checked ? new Set(cleanIds) : new Set())} />全选无异常（{cleanIds.length}）</label>
          <span className="inv-au-grow" />
          <button type="button" className="btn primary" disabled={!nChecked || batchBusy} onClick={onBatch}>
            {batchBusy ? '正在通过…' : `批量通过${nChecked ? `（${nChecked}）` : ''}`}
          </button>
        </div>
      )}
      {batchErr && <div className="inv-au-err">{batchErr}</div>}
      <BatchResult res={batchRes} titleOf={titleOf} onClose={() => setBatchRes(null)} onPick={onPick} />
      <div className="inv-au-list">
        {err && <div className="inv-au-err">队列没取到：{err}</div>}
        {loading && !rows.length && <div className="loading">正在取审核队列…</div>}
        {!loading && !err && !rows.length && <div className="inv-au-empty">
          {qInput ? '没搜到符合的票夹，换个关键字试试' : tab === 'pending' ? '暂时没有要审的票夹' : tab === 'returned' ? '没有被退回的票夹' : '还没有审核通过的票夹'}
        </div>}
        {rows.map(r => <QueueRow key={r.id} row={r} tab={tab} on={r.id === selId} checked={checked.has(r.id)}
          canCheck={canAct} onPick={onPick} onCheck={toggle} />)}
      </div>
      {total > 0 && <div className="inv-au-pager">
        <span className="inv-muted">共 {total} 个{loading ? ' · 刷新中…' : ''}</span>
        <span className="inv-au-grow" />
        {pages > 1 && <>
          <button type="button" className="inv-ib" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)} title="上一页">‹</button>
          <span className="inv-vw-pct">{page} / {pages}</span>
          <button type="button" className="inv-ib" disabled={page >= pages || loading} onClick={() => setPage(page + 1)} title="下一页">›</button>
        </>}
      </div>}
    </aside>
  )
}

// ───────────────────────── 右：票夹 ─────────────────────────

function KV({ k, children, wide }) {
  return <div className={'inv-au-kv' + (wide ? ' wide' : '')}><span className="inv-au-k">{k}</span><span className="inv-au-v">{children}</span></div>
}

function FolderHeader({ folder, items }) {
  const f = folder || {}
  const payee = f.payee || {}
  const amount = num(f.amount)
  const invs = (items || []).filter(it => it.kind === 'invoice' && it.review !== 'void')
  const sum = num(statOf(f, 'sumTotal')) ?? invs.reduce((s, it) => s + (num(it.split ? (it.alloc ?? it.total) : it.total) || 0), 0)
  const diff = num(statOf(f, 'diff')) ?? (amount !== null ? Math.round((sum - amount) * 100) / 100 : null)
  const reason = Array.isArray(f.reason) ? f.reason.filter(Boolean).join('；') : (typeof f.reason === 'object' && f.reason ? JSON.stringify(f.reason) : f.reason)
  const later = f.later
  return (
    <div className="inv-au-hd">
      <div className="inv-au-hd-top">
        <div className="inv-au-hd-title">
          <b>{f.title || f.template || '（无标题）'}</b>
          <span className={'inv-au-st ' + (f.status || '')}>{FOLDER_ST[f.status] || f.status || '—'}</span>
          {f.selfReview ? <span className="inv-badge warn" title="主管理员审核了自己提交的票夹">自审</span> : null}
        </div>
        <div className="inv-au-hd-amt">
          <span className="inv-au-k">单据金额</span><span className="inv-num">{money(amount)}</span>
        </div>
      </div>
      <div className="inv-au-hd-grid">
        <KV k="审批单号"><span className="inv-num">{f.businessId || (f.source === 'manual' ? '手工票夹（无审批单）' : '—')}</span></KV>
        <KV k="审批类型">{f.template || '—'}</KV>
        <KV k="申请人">{f.applicant || '—'}{f.dept ? <span className="inv-muted"> · {f.dept}</span> : null}</KV>
        <KV k="公司主体">{f.company || '—'}</KV>
        <KV k="收款方" wide>{payee.name || '—'}
          {(payee.bank || payee.account) && <span className="inv-muted"> · {payee.bank || ''} {payee.account || ''}</span>}</KV>
        {reason ? <KV k="事由" wide>{reason}</KV> : null}
        {f.erpNo ? <KV k="ERP 单号"><span className="inv-num">{f.erpNo}</span></KV> : null}
        <KV k="提交">{f.submittedBy || '—'}{f.submittedAt ? <span className="inv-muted"> · {fmtTime(f.submittedAt)}</span> : null}</KV>
        {f.reviewedBy ? <KV k="审核">{f.reviewedBy}<span className="inv-muted"> · {fmtTime(f.reviewedAt)}</span></KV> : null}
        {f.reviewNote ? <KV k={f.status === 'returned' ? '退回原因' : '审核备注'} wide>{f.reviewNote}</KV> : null}
      </div>
      <div className={'inv-au-diff' + (diff !== null && Math.abs(diff) > 0.005 ? ' off' : '')}>
        <span>票合计 <b className="inv-num">{money(sum)}</b></span>
        <span>单据金额 <b className="inv-num">{money(amount)}</b></span>
        {diff !== null && <span>{Math.abs(diff) > 0.005 ? <>差额 <b className="inv-num">{money(diff)}</b>（只提示，不拦）</> : '两边一致'}</span>}
        {later && <span className="inv-au-later">发票后补：预计 {later.expectDate || '—'} 到票，接收人 {later.receiverName || '—'}</span>}
      </div>
    </div>
  )
}

function DeductToggle({ value, onChange, disabled }) {
  return <span className={'inv-au-seg' + (disabled ? ' off' : '')} role="radiogroup" aria-label="可否抵扣">
    <button type="button" role="radio" aria-checked={value === true} className={value === true ? 'on yes' : ''}
      disabled={disabled} onClick={e => { e.stopPropagation(); onChange(true) }}>可抵扣</button>
    <button type="button" role="radio" aria-checked={value === false} className={value === false ? 'on no' : ''}
      disabled={disabled} onClick={e => { e.stopPropagation(); onChange(false) }}>不可抵扣</button>
  </span>
}

const suggestText = it => it.deductSuggest === 'yes' ? '建议可抵扣' : it.deductSuggest === 'no' ? '建议不可抵扣' : '系统没给建议'

// 全票夹的抵扣判定一览：通过时按这里的选择提交
function DeductList({ items, decisions, setDecision, editable, activeId, onPick, orderOf }) {
  const invs = items.filter(it => it.kind === 'invoice' && it.review !== 'void')
  if (!invs.length) return null
  return <section className="inv-au-card">
    <div className="inv-au-card-h"><b>可否抵扣</b><span className="inv-muted">系统按票种和项目类别给建议，会计定；通过时一起提交</span></div>
    <div className="inv-au-dl">
      {invs.map(it => {
        const pend = needsDecision(it)
        const v = pend ? decisions[it.id] : !!it.deductible
        const changed = pend && v !== (it.deductSuggest === 'yes')
        return <div key={it.id} className={'inv-au-dl-row' + (it.id === activeId ? ' on' : '')} onClick={() => onPick(it.id)}>
          <span className="inv-au-dl-n">{orderOf(it.id)}</span>
          <span className="inv-au-dl-main">
            <span className="inv-au-dl-t">{it.sellerName || it.typeLabel || '（销方未读出）'}<span className="inv-num"> {money(it.split ? (it.alloc ?? it.total) : it.total)}</span></span>
            <span className={'inv-au-dl-why' + (changed ? ' chg' : '')}>{suggestText(it)}{it.deductReason ? `：${it.deductReason}` : ''}{changed ? '（已改判）' : ''}</span>
          </span>
          {pend
            ? <DeductToggle value={v} disabled={!editable} onChange={x => setDecision(it.id, x)} />
            : <span className="inv-muted">{it.review === 'approved' ? (it.deductible ? '已定：可抵扣' : '已定：不可抵扣') : ''}</span>}
        </div>
      })}
    </div>
  </section>
}

function LinesTable({ lines }) {
  if (!Array.isArray(lines) || !lines.length) return null
  return <details className="inv-au-lines">
    <summary>票面明细 {lines.length} 行</summary>
    <div className="inv-au-lines-in">
      <table><thead><tr><th>项目</th><th>规格</th><th className="num">数量</th><th className="num">金额</th><th>税率</th><th className="num">税额</th></tr></thead>
        <tbody>{lines.map((l, i) => <tr key={i}>
          <td>{l.name || '—'}{l.category ? <div className="sub">{l.category}</div> : null}</td>
          <td>{l.spec || ''}{l.unit ? ` ${l.unit}` : ''}</td>
          <td className="num">{l.qty ?? ''}</td>
          <td className="num">{money(l.amount)}</td>
          <td>{l.rate ?? ''}</td>
          <td className="num">{money(l.tax)}</td>
        </tr>)}</tbody></table>
    </div>
  </details>
}

// 当前这张票：状态徽标 + 字段面板（点字段→图上框位置）+ 本张可否抵扣
function ItemSide({ item, index, editable, activeField, onFieldFocus, onSave, onConfirm, decision, setDecision }) {
  if (!item) return <div className="inv-au-side"><div className="inv-fp"><div className="inv-fp-empty">这个票夹里还没有票</div></div></div>
  const isOther = item.kind === 'other'
  return (
    <div className="inv-au-side">
      <div className="inv-au-it-h">
        <b>第 {index + 1} 张</b>
        <span>{isOther ? '非发票附件' : item.kind === 'receipt' ? '收据' : (item.typeLabel || '发票')}</span>
        <span className={'inv-au-rv ' + (item.review || '')}>{ITEM_REVIEW[item.review] || ''}</span>
        <span className="inv-au-grow" />
        {item.file?.name && <span className="inv-muted inv-au-fname" title={item.file.name}>{item.file.name}</span>}
      </div>
      <StatusBadges item={item} />
      {item.split ? <div className="inv-au-note">这张票拆到多张单上：本单分摊 <b className="inv-num">{money(item.alloc)}</b>（票面 {money(item.total)}）</div> : null}
      {item.procStatus === 'failed' && <div className="inv-au-note warn">识别没成功：{item.procError || '图片读不出来'}。请对着图把字段补上。</div>}
      {isOther
        ? <div className="inv-au-note">这是附件（合同、清单、水单等），只留存不入台账，不用填字段。</div>
        : <>
          {editable && <div className="inv-muted inv-au-tip">点任意一行字段，左边图上会框出它是从哪读出来的；识别来的「待核」字段核对后点「核对无误」。</div>}
          {!editable && <div className="inv-muted inv-au-tip">点任意一行字段，左边图上会框出它是从哪读出来的。</div>}
          <FieldPanel item={item} editable={editable} activeField={activeField} onFieldFocus={onFieldFocus}
            onSave={onSave} onConfirm={onConfirm} />
          <LinesTable lines={item.lines} />
          {item.kind === 'invoice' && item.review !== 'void' && (
            <div className="inv-au-ded">
              <div className="inv-au-ded-t">
                <b>本张可否抵扣</b>
                {needsDecision(item)
                  ? <DeductToggle value={decision} disabled={!editable} onChange={v => setDecision(item.id, v)} />
                  : <span className="inv-muted">{item.deductible ? '已定：可抵扣' : '已定：不可抵扣'}</span>}
              </div>
              <div className="inv-muted">{suggestText(item)}{item.deductReason ? `：${item.deductReason}` : ''}</div>
            </div>
          )}
        </>}
    </div>
  )
}

// 票夹异常：票夹详情自带的（抬头/销方/金额差/后补…；深链打开、不在当前队列页的票夹也有）＋逐张票的红黄徽标
function AnomalyList({ rowAnomalies, items, orderOf, onPick }) {
  const perItem = []
  items.forEach(it => {
    itemBadges(it).filter(b => b.tone === 'err' || b.tone === 'warn').forEach(b => perItem.push({ it, b }))
  })
  const fold = Array.isArray(rowAnomalies) ? rowAnomalies : []
  if (!fold.length && !perItem.length) return <section className="inv-au-card"><div className="inv-au-card-h"><b>异常</b><span className="inv-badge ok">没有发现异常</span></div></section>
  return <section className="inv-au-card">
    <div className="inv-au-card-h"><b>异常</b><span className="inv-muted">系统先标出来，请逐条看一眼</span></div>
    <ul className="inv-au-anom">
      {fold.map((a, i) => <li key={'f' + i}><span className={'inv-badge ' + toneOf(a.level)}>{a.label || a.code}</span>{a.tip || a.detail ? <span>{a.tip || a.detail}</span> : null}</li>)}
      {perItem.map(({ it, b }, i) => <li key={'i' + i} className="lk" onClick={() => onPick(it.id)}>
        <span className="inv-au-anom-n">第 {orderOf(it.id)} 张</span>
        <span className={'inv-badge ' + b.tone}>{b.label}</span>
        {b.tip ? <span>{b.tip}</span> : null}
      </li>)}
    </ul>
  </section>
}

function logDetail(d) {
  if (d === null || d === undefined || d === '') return ''
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map(logDetail).filter(Boolean).join('；')
  if (typeof d === 'object') {
    return Object.entries(d).filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}：${typeof v === 'object' ? JSON.stringify(v) : v}`).join('；')
  }
  return String(d)
}

// 留痕：默认整个票夹；勾「只看这张票」按 log.itemId 过滤（改字段、核对、抵扣判定、作废…都记在票上）
function LogList({ logs, activeItem, orderOf }) {
  const [onlyItem, setOnlyItem] = useState(false)
  const all = Array.isArray(logs) ? logs : []
  const aid = activeItem?.id
  const arr = onlyItem && aid ? all.filter(l => l && l.itemId === aid) : all
  return <details className="inv-au-card inv-au-logs">
    <summary><b>处理留痕</b><span className="inv-muted">{arr.length} 条</span></summary>
    {aid ? <label className="inv-au-logf"><input type="checkbox" checked={onlyItem} onChange={e => setOnlyItem(e.target.checked)} />
      只看当前这张票（第 {orderOf(aid)} 张）</label> : null}
    {!arr.length ? <div className="inv-au-empty">{onlyItem ? '这张票还没有单独的留痕' : '还没有留痕'}</div>
      : <ul>{arr.map((l, i) => <li key={l.id ?? i}>
        <span className="inv-num inv-muted">{fmtTime(l.ts)}</span>
        <span className="inv-au-log-u">{l.user || '系统'}</span>
        <b>{l.action}</b>
        {!onlyItem && l.itemId && orderOf(l.itemId) > 0 ? <span className="inv-badge mute">第 {orderOf(l.itemId)} 张</span> : null}
        <span className="inv-au-log-d">{logDetail(l.detail)}</span>
      </li>)}</ul>}
  </details>
}

function ActionBar({ canAct, actionable, busy, note, setNote, onApprove, onReturn, err, blockers, selfWarn, nDecide, orderOf, onPick }) {
  const disabled = !canAct || !actionable || busy
  return <div className="inv-au-act">
    {!canAct && <div className="inv-au-hint">{NO_PERM_HINT}，现在只能查看</div>}
    {canAct && !actionable && <div className="inv-au-hint">这个票夹现在没有要审的票（已审完或已退回收票台）</div>}
    {canAct && actionable && selfWarn && <div className="inv-au-hint warn">{selfWarn}</div>}
    {(err || (blockers && blockers.length > 0)) && <div className="inv-au-err">
      {err && <div>{err}</div>}
      {blockers && blockers.length > 0 && <ul>{blockers.map((b, i) => {
        const ids = blockerIds(b).filter(id => orderOf(id) > 0)
        return <li key={i}>{blockerText(b, orderOf)}
          {ids.map(id => <button type="button" key={id} className="inv-lk inv-au-lkb" onClick={() => onPick(id)}>去看第 {orderOf(id)} 张</button>)}
        </li>
      })}</ul>}
    </div>}
    <div className="inv-au-act-row">
      <input className="inv-in" value={note} disabled={disabled} placeholder="审核备注（选填，通过时一起记下）"
        onChange={e => setNote(e.target.value)} maxLength={500} />
      <button type="button" className="btn" disabled={disabled} onClick={onReturn}>退回</button>
      <button type="button" className="btn primary" disabled={disabled} onClick={onApprove}
        title={nDecide ? `连同 ${nDecide} 张发票的抵扣判定一起提交` : undefined}>{busy ? '正在处理…' : '通过'}</button>
    </div>
  </div>
}

function ReturnModal({ folder, onClose, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const submit = async () => {
    const v = note.trim()
    if (!v) { setErr('请写上退回原因，收票的同事要按这个改'); return }
    setBusy(true); setErr('')
    try {
      const r = await invAuditReturn({ folderId: folder.id, note: v })
      onDone(r)
    } catch (e) { setErr('退回没成功：' + errText(e)) } finally { setBusy(false) }
  }
  return <Modal title={`退回票夹：${folder.title || folder.businessId || '#' + folder.id}`} onClose={() => { if (!busy) onClose() }} width={520}
    footer={<>
      <button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button>
      <button type="button" className="btn primary" onClick={submit} disabled={busy || !note.trim()}>{busy ? '正在退回…' : '确定退回'}</button>
    </>}>
    <div className="inv-au-ret">
      <div className="inv-muted">退回后票夹回到收票工作台，带着下面的原因；收票的同事改好再提交。</div>
      <textarea className="inv-au-ta" autoFocus value={note} rows={4} maxLength={500}
        placeholder="例：第 2 张发票抬头不是本公司；差一张住宿发票"
        onChange={e => { setNote(e.target.value); setErr('') }}
        onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit() } }} />
      {err && <div className="inv-au-err">{err}</div>}
    </div>
  </Modal>
}

// ───────────────────────── 页面 ─────────────────────────

export default function InvAudit({ user }) {
  const [toast, flash] = useToast()
  const [cfg, setCfg] = useState(null)
  const [cfgErr, setCfgErr] = useState('')
  const can = cfg?.can || {}
  const canAct = !!can.auditAct
  const me = cfg?.me || { name: user?.name || user?.username || '', isSuper: user?.role === 'admin' }

  // 队列
  const [tab, setTab] = useState('pending')
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [queue, setQueue] = useState(null)
  const [qLoading, setQLoading] = useState(false)
  const [qErr, setQErr] = useState('')
  const [counts, setCounts] = useState({})
  const [checked, setChecked] = useState(() => new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchErr, setBatchErr] = useState('')
  const [batchRes, setBatchRes] = useState(null)
  const qReq = useRef(0)

  // 票夹
  const [selId, setSelId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [dLoading, setDLoading] = useState(false)
  const [dErr, setDErr] = useState('')
  const [activeId, setActiveId] = useState(null)
  const [activeField, setActiveField] = useState(null)
  const [decisions, setDecisions] = useState({})
  const [note, setNote] = useState('')
  const [actBusy, setActBusy] = useState(false)
  const [actErr, setActErr] = useState('')
  const [blockers, setBlockers] = useState(null)
  const [lastRes, setLastRes] = useState(null)      // 上一次通过/退回的结果（切到下一个票夹后仍显示，自审在这里打标）
  const [retOpen, setRetOpen] = useState(false)
  const dReq = useRef(0)
  const touched = useRef(new Set())   // 审核人亲手点过「可否抵扣」的票；没点过的跟着系统建议走（改字段后建议会变）

  useEffect(() => {
    let live = true
    invConfig().then(r => { if (live) setCfg(r || {}) }).catch(e => { if (live) setCfgErr(errText(e)) })
    return () => { live = false }
  }, [])

  // 各页签数量（不带搜索词；失败不打扰，只是不显示数字）
  const loadCounts = useCallback(() => {
    TABS.forEach(t => {
      invAuditQueue({ tab: t.key, page: 1, size: 1 })
        .then(r => setCounts(c => ({ ...c, [t.key]: r?.total ?? 0 })))
        .catch(() => { /* 数字取不到不影响审核 */ })
    })
  }, [])

  const loadQueue = useCallback(async (opt = {}) => {
    const t = opt.tab ?? tab, qq = opt.q ?? q, pg = opt.page ?? page
    const my = ++qReq.current
    setQLoading(true); setQErr('')
    try {
      const r = await invAuditQueue({ tab: t, q: qq, page: pg, size: PAGE_SIZE })
      if (my !== qReq.current) return null
      const data = { total: r?.total ?? 0, rows: Array.isArray(r?.rows) ? r.rows : [] }
      setQueue(data)
      if (!qq) setCounts(c => ({ ...c, [t]: data.total }))
      // 勾选只保留仍在列表里、且仍无异常的
      setChecked(s => new Set([...s].filter(id => data.rows.some(x => x.id === id && x.clean))))
      return data
    } catch (e) {
      if (my === qReq.current) setQErr(errText(e))
      return null
    } finally { if (my === qReq.current) setQLoading(false) }
  }, [tab, q, page])

  useEffect(() => { loadCounts() }, [loadCounts])
  // 换页签/搜索/翻页 → 重取；没选中票夹时自动打开第一个（深链进来的除外）
  const deepRef = useRef(false)
  useEffect(() => {
    let live = true
    loadQueue().then(d => {
      if (!live || !d) return
      setSelId(cur => (cur !== null && cur !== undefined) ? cur : (d.rows[0]?.id ?? null))
    })
    return () => { live = false }
  }, [tab, q, page])

  // 深链 #/invaudit?folder=ID：只消费一次，然后把地址收回 #/invaudit（刷新不重放）
  useEffect(() => {
    if (deepRef.current) return
    deepRef.current = true
    const m = (window.location.hash || '').match(/^#\/invaudit\?(.+)$/)
    if (!m) return
    const id = parseInt(new URLSearchParams(m[1]).get('folder') || '', 10)
    if (id) setSelId(id)
    try { window.history.replaceState(null, '', '#/invaudit') } catch { /* 忽略 */ }
  }, [])

  const loadDetail = useCallback(async (id, { keepActive = false } = {}) => {
    if (!id) { setDetail(null); return }
    const my = ++dReq.current
    if (!keepActive) { setDLoading(true); setDErr('') }
    try {
      const r = await invFolder(id)
      if (my !== dReq.current) return
      const items = sortItems(r?.items)
      setDetail({ folder: r?.folder || null, items, logs: r?.logs || [] })
      setDErr('')
      // 抵扣判定：会计亲手点过的保留（刷新不冲掉），没点过的和新票用"已定值或系统建议"（建议随票面改动会变）
      if (!keepActive) touched.current = new Set()
      setDecisions(prev => mergeDecisions(keepActive ? prev : {}, items, touched.current))
      if (!keepActive) {
        setActiveId(items[0]?.id ?? null); setActiveField(null)
      } else {
        setActiveId(cur => (items.some(it => it.id === cur) ? cur : (items[0]?.id ?? null)))
      }
    } catch (e) {
      if (my === dReq.current) { setDErr(errText(e)); if (!keepActive) setDetail(null) }
    } finally { if (my === dReq.current) setDLoading(false) }
  }, [])

  useEffect(() => {
    setNote(''); setActErr(''); setBlockers(null); setRetOpen(false)
    loadDetail(selId)
  }, [selId, loadDetail])

  const items = detail?.items || []
  const folder = detail?.folder || null
  const activeIdx = Math.max(0, items.findIndex(it => it.id === activeId))
  const activeItem = items.length ? items[activeIdx] : null
  const orderOf = useCallback(id => items.findIndex(it => it.id === id) + 1, [items])
  const actionable = isActionable(folder, items)
  const editable = canAct && actionable
  const busyItems = items.some(it => it.procStatus === 'pending' || it.procStatus === 'running')
  // 有票还在识别时，每 3 秒刷一下这个票夹（识别完自动停）
  usePoll(() => loadDetail(selId, { keepActive: true }), !!selId && busyItems, 3000)

  const row = useMemo(() => (queue?.rows || []).find(r => r.id === selId) || null, [queue, selId])

  const pickItem = useCallback((id) => { setActiveId(id); setActiveField(null) }, [])
  const step = useCallback((d) => {
    if (!items.length) return
    const i = _clampIdx(activeIdx + d, items.length)
    pickItem(items[i].id)
  }, [items, activeIdx, pickItem])

  // ↑↓ 切上一张/下一张票：人在打字、或有弹窗时不抢
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
      if (isTyping(e.target) || isTyping(document.activeElement) || modalCovers()) return
      if (!items.length) return
      e.preventDefault()
      step(e.key === 'ArrowDown' ? 1 : -1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [items, step])

  const replaceItem = (it) => {
    if (!it || !it.id) return
    setDetail(d => d ? { ...d, items: d.items.map(x => (x.id === it.id ? { ...x, ...it } : x)) } : d)
    // 改了票种/税额/项目类别，系统建议可能从「可抵扣」变「不可抵扣」：没亲手改判过的票跟着新建议走
    setDecisions(prev => mergeDecisions(prev, [it], touched.current))
  }
  // 改完字段顺手重读票夹：票夹级异常（金额差、抬头不符…）和留痕跟着刷新
  const refreshQuiet = () => { if (selId) loadDetail(selId, { keepActive: true }) }
  const onSave = async (changed) => {
    const r = await invItemUpdate(activeItem.id, { fields: changed })   // 出错抛给 FieldPanel，显示在面板里
    replaceItem(r?.item)
    flash('字段已保存', 'ok')
    refreshQuiet()
  }
  const onConfirm = async () => {
    const r = await invItemUpdate(activeItem.id, { confirm: 'all' })
    replaceItem(r?.item)
    flash('这张票的待核字段已确认', 'ok')
    refreshQuiet()
  }
  const onRotate = async (rotation) => {
    const id = activeItem?.id
    try { await invItemRotate(id, rotation) } catch (e) { flash('旋转角度没存上：' + errText(e), 'err') }
  }
  const setDecision = (id, v) => { touched.current.add(id); setDecisions(d => ({ ...d, [id]: v })) }

  // 通过/退回之后：刷新队列，自动跳到下一个待审票夹
  const moveNext = async (doneId) => {
    const old = queue?.rows || []
    const idx = old.findIndex(r => r.id === doneId)
    const d = await loadQueue()
    loadCounts()
    const rows = d?.rows || []
    let next = null
    for (let i = idx + 1; idx >= 0 && i < old.length; i += 1) {
      if (rows.some(r => r.id === old[i].id)) { next = old[i].id; break }
    }
    if (next === null) {
      // 原来那个后面没有了（或是深链打开、不在队列里的）→ 取刷新后同位置的，再不行取最后一个
      const rest = rows.filter(r => r.id !== doneId)
      next = rest.length ? rest[Math.min(Math.max(idx, 0), rest.length - 1)].id : null
    }
    setSelId(next)
  }

  const nameOf = f => (f?.title || f?.template || '票夹') + (f?.businessId ? ` · ${f.businessId}` : '')

  const approve = async () => {
    if (!folder || actBusy) return
    const unchecked = items.filter(it => it.review !== 'void' && Array.isArray(it.pending) && it.pending.length).length
    if (unchecked && !window.confirm(`还有 ${unchecked} 张票的识别字段没核对（标黄「待核」），确定直接通过？`)) return
    const dec = {}
    items.filter(needsDecision).forEach(it => { dec[String(it.id)] = { deductible: !!decisions[it.id] } })
    // 只通过眼前这批待审的票：审核期间后补池又收进来的票，后端会回 409 让人先看
    const itemIds = pendingItemIds(items)
    setActBusy(true); setActErr(''); setBlockers(null)
    try {
      const r = await invAuditApprove({ folderId: folder.id, itemIds, decisions: dec, note: note.trim() })
      const newItems = Array.isArray(r?.newItems) ? r.newItems : null
      if (r && r.ok === false && (newItems || r.httpStatus === 409)) {
        setActErr((r.msg || '审核期间这个票夹又进了新票，请刷新后再审') + '——已重新读取票夹，新进的票请先看一眼再点「通过」')
        flash('票夹里进了新票，已刷新', 'err')
        await loadDetail(folder.id, { keepActive: true })
        if (newItems && newItems.length) pickItem(newItems[0])
        return
      }
      if (!r || r.ok === false || r.detail) {
        const d = r && (r.msg || r.detail)
        setActErr('没能通过：' + (typeof d === 'string' && d ? d : '服务端没说原因，请稍后再试'))
        setBlockers(Array.isArray(r?.blockers) ? r.blockers : null)
        return
      }
      const f = r.folder || folder
      setLastRes({ kind: 'ok', name: nameOf(f), selfReview: !!f.selfReview, id: f.id })
      flash(f.selfReview ? '已通过（自审，已留痕）' : '已通过', 'ok')
      await moveNext(folder.id)
    } catch (e) {
      setActErr('没能通过：' + errText(e))
    } finally { setActBusy(false) }
  }

  const onReturned = async (r) => {
    const f = r?.folder || folder
    setRetOpen(false)
    setLastRes({ kind: 'ret', name: nameOf(f), id: f.id, note: f.reviewNote })
    flash('已退回收票工作台', 'ok')
    await moveNext(folder.id)
  }

  const runBatch = async () => {
    const ids = [...checked]
    if (!ids.length) return
    if (!window.confirm(`批量通过选中的 ${ids.length} 个无异常票夹？发票按系统建议判定可否抵扣。`)) return
    setBatchBusy(true); setBatchErr(''); setBatchRes(null)
    try {
      const names = {}
      ;(queue?.rows || []).forEach(x => { names[x.id] = (x.title || x.template || '票夹 #' + x.id) + (x.businessId ? ' · ' + x.businessId : '') })
      const r = await invAuditBatch(ids)
      setBatchRes({ done: r?.done || [], skipped: r?.skipped || [], names })
      setChecked(new Set())
      const d = await loadQueue()
      loadCounts()
      if (selId && d && !d.rows.some(x => x.id === selId)) loadDetail(selId)
    } catch (e) { setBatchErr('批量通过没成功：' + errText(e)) } finally { setBatchBusy(false) }
  }

  // 自己提交的票夹：提前提示（后端最终判定；主管理员可自审并留痕）
  const selfWarn = folder && me.name && folder.submittedBy === me.name
    ? (me.isSuper ? '这个票夹是你提交的。你是主管理员可以自审，通过后会留痕标「自审」。' : '这个票夹是你提交的——提交人不能审核自己提交的票夹，请交给其他会计审。')
    : ''

  const changeTab = (t) => {
    if (t === tab) return
    setTab(t); setPage(1); setSelId(null); setChecked(new Set()); setBatchRes(null); setBatchErr('')
  }
  const doSearch = (v) => { setPage(1); setQ((v || '').trim()); setSelId(null) }

  const nDecide = items.filter(needsDecision).length

  return (
    <div>
      <div className="head">
        <div>
          <div className="h-title">发票审核</div>
          <div className="h-sub">收票工作台提交的票夹在这里复核：点字段看图上位置、定可否抵扣；通过后进发票台账，退回的带原因回收票台。没有异常的票夹可以批量通过。</div>
        </div>
        <div className="inv-au-hacts">
          {cfg && !canAct && <span className="inv-badge mute" title="可以查看，不能通过/退回">{NO_PERM_HINT}</span>}
          <button type="button" className="btn" disabled={qLoading} onClick={() => { loadQueue(); loadCounts(); if (selId) loadDetail(selId, { keepActive: true }) }}>刷新</button>
        </div>
      </div>
      <div className="body">
        {cfgErr && <div className="banner err">没取到发票管家的权限设置：{cfgErr}。可以先看队列，通过/退回等按钮会不可用。</div>}
        {lastRes && <div className={'inv-au-last ' + lastRes.kind}>
          <span className={'inv-badge ' + (lastRes.kind === 'ok' ? 'ok' : 'warn')}>{lastRes.kind === 'ok' ? '已通过' : '已退回'}</span>
          {lastRes.selfReview && <span className="inv-badge warn" title="主管理员审核了自己提交的票夹，已留痕">自审</span>}
          <span>{lastRes.name}</span>
          {lastRes.kind === 'ret' && lastRes.note ? <span className="inv-muted">原因：{lastRes.note}</span> : null}
          <span className="inv-au-grow" />
          <button type="button" className="inv-lk inv-au-lkb" onClick={() => setSelId(lastRes.id)}>再看一眼</button>
          <button type="button" className="inv-x" onClick={() => setLastRes(null)} aria-label="收起">×</button>
        </div>}
        <div className="inv-au">
          <QueuePane tab={tab} onTab={changeTab} counts={counts} qInput={qInput} setQInput={setQInput} onSearch={doSearch}
            queue={queue} loading={qLoading} err={qErr} page={page} setPage={setPage}
            selId={selId} onPick={setSelId} checked={checked} setChecked={setChecked} canAct={canAct}
            onBatch={runBatch} batchBusy={batchBusy} batchErr={batchErr} batchRes={batchRes} setBatchRes={setBatchRes} />

          <main className="inv-au-d">
            {!selId && <div className="inv-au-blank">{qLoading ? '正在取审核队列…' : '在左边点一个票夹开始审核'}</div>}
            {selId && dLoading && !detail && <div className="loading">正在打开票夹…</div>}
            {selId && dErr && <div className="banner err">票夹没打开：{dErr}
              <button type="button" className="btn" onClick={() => loadDetail(selId)}>重试</button></div>}
            {selId && folder && <>
              <FolderHeader folder={folder} items={items} />
              <section className="inv-au-strip">
                <ThumbStrip items={items} activeId={activeItem?.id} onPick={it => pickItem(it.id)} />
                {items.length > 1 && <span className="inv-muted inv-au-kbd">↑ ↓ 键切换上一张/下一张</span>}
              </section>
              <div className="inv-au-work">
                <div className="inv-au-view">
                  <InvViewer item={activeItem} activeField={activeField} height={600}
                    onRotate={canAct && activeItem ? onRotate : undefined}
                    onPrev={items.length > 1 && activeIdx > 0 ? () => step(-1) : undefined}
                    onNext={items.length > 1 && activeIdx < items.length - 1 ? () => step(1) : undefined} />
                </div>
                <ItemSide item={activeItem} index={activeIdx} editable={editable}
                  activeField={activeField} onFieldFocus={setActiveField}
                  onSave={onSave} onConfirm={onConfirm}
                  decision={activeItem ? decisions[activeItem.id] : undefined} setDecision={setDecision} />
              </div>
              <DeductList items={items} decisions={decisions} setDecision={setDecision} editable={editable}
                activeId={activeItem?.id} onPick={pickItem} orderOf={orderOf} />
              <AnomalyList rowAnomalies={Array.isArray(folder?.anomalies) ? folder.anomalies : row?.anomalies} items={items} orderOf={orderOf} onPick={pickItem} />
              <LogList logs={detail?.logs} activeItem={activeItem} orderOf={orderOf} />
              <ActionBar canAct={canAct} actionable={actionable} busy={actBusy} note={note} setNote={setNote}
                onApprove={approve} onReturn={() => { setActErr(''); setBlockers(null); setRetOpen(true) }}
                err={actErr} blockers={blockers} selfWarn={selfWarn} nDecide={nDecide} orderOf={orderOf} onPick={pickItem} />
            </>}
          </main>
        </div>
      </div>
      {retOpen && folder && <ReturnModal folder={folder} onClose={() => setRetOpen(false)} onDone={onReturned} />}
      {toast}
    </div>
  )
}

function _clampIdx(i, n) { return Math.max(0, Math.min(n - 1, i)) }
