// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票审核：左队列（待审核/已退回/已通过＋搜索＋无异常批量通过）＋右票夹（抬头卡/缩略条/看图器点字段框位置/字段核对/逐张可否抵扣/异常/留痕/通过·退回）
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 通过时带上看过的待审票 itemIds（审核期间进了新票→后端 409，提示后重读票夹）；
//   改了票面字段后没手动改判的票，可否抵扣跟着新建议走；票夹级异常用票夹详情自带的（深链打开也有）；拦截项按 itemIds 出「第 N 张」；留痕可只看当前这张票。
// [Change Log] Date: 2026-09-25 | Author: Claude / c | Version: V-draft（发票管家·审核页改版）| 按用户定稿样机重做右半边：
//   单据头（标题后带审批编号）＋金额核对「付款金额 −（专票＋普票＋发票后补单）＝ 差额」→ 已收发票表（点一行弹窗：左看图、右基础信息、
//   右上 提交/有疑问/删除、下方本张明细、扫描人）→ 发票后补单列表 → 底部固定只有「提交」（有疑问＝退回；有差额要写说明）。
// 需求确认书 v1.4 第八节 + 技术方案 §5.2「发票审核」。共用组件全部来自 invShared.jsx，这里只拼页面。
// 权限：按 invConfig().can.auditAct 决定能不能动手（后端同样拦）；没开时照样能看，按钮置灰并说明找管理员开。
import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  invConfig, invAuditQueue, invAuditBatch, invAuditApprove, invAuditReturn, invAuditMark,
  invFolder, invItemUpdate, invItemRotate, invItemRemove,
} from '../api.js'
import {
  money, fmtTime, useToast, usePoll, Modal, StatusBadges, itemBadges, InvViewer, FieldPanel,
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
// ───────────────────────── 右：票夹（单据头＋金额核对 → 已收发票 → 发票后补单 → 底部提交） ─────────────────────────

const ORIGIN_CN = {
  attachment: '审批附件自动拉取', photo_field: '审批图片栏自动拉取', camera: '高拍仪', phone: '手机拍照',
  upload: '上传', scanner: '扫码枪', later: '发票后补池收票', taxpack: '税局文件包',
}
const TYPE_SHORT = {
  special: '专票', normal: '普票', travel: '旅客运输', toll: '通行费', train: '火车票', flight: '机票行程单',
  vehicle: '机动车', quota: '定额', taxi: '出租车', tollpaper: '过路费', general: '通用机打', other: '其他',
}
// 核对表只列发票和收据（非发票附件另列一行）；作废、移除的不算
const isBill = it => !!it && (it.kind === 'invoice' || it.kind === 'receipt') && it.review !== 'void' && it.status !== 'removed'
// 这一轮还没核的票：待审、没点过「提交」
const needCheck = it => isBill(it) && it.review === 'pending' && !it.auditOk
const shareOf = it => num(it.split ? (it.alloc ?? it.total) : it.total) ?? num(it.amount) ?? 0
const typeShort = it => (it.kind === 'receipt' ? '收据' : (TYPE_SHORT[it.invType] || it.typeLabel || '发票'))
const r2 = v => Math.round(v * 100) / 100
// 核对列只放要人留意的系统结论（重复、抬头、销方、码面、验真、识别失败）；都没有就是「正常」
const CHECK_KEYS = new Set(['dup', 'buyer', 'buyerNc', 'seller', 'qr', 'verify', 'proc', 'void'])

function Fig({ label, v, cls }) {
  return <span className={'inv-au-fig ' + (cls || '')}><span>{label}</span><b className="inv-num">{v}</b></span>
}

function AuditHeader({ folder }) {
  const f = folder || {}
  const payee = f.payee || {}
  const g = f.gap || null
  const gap = g ? num(g.gap) : null
  const off = gap !== null && Math.abs(gap) > 0.005
  const reason = Array.isArray(f.reason) ? f.reason.filter(Boolean).join('；') : (typeof f.reason === 'object' && f.reason ? JSON.stringify(f.reason) : (f.reason || ''))
  const [open, setOpen] = useState(false)
  const long = reason.length > 90
  return (
    <section className="inv-au-hd2">
      <div className="inv-au-hd2-t">
        <b>{f.title || f.template || '（无标题）'}</b>
        {f.businessId ? <span className="inv-num inv-au-bid">（{f.businessId}）</span>
          : f.source === 'manual' ? <span className="inv-muted">（手工票夹）</span> : null}
        <span className={'inv-au-st ' + (f.status || '')}>{FOLDER_ST[f.status] || f.status || '—'}</span>
        {f.selfReview ? <span className="inv-badge warn" title="主管理员审核了自己提交的票夹">自审</span> : null}
      </div>
      <div className="inv-au-kv2">
        <span>类型 <b>{f.template || '—'}</b></span>
        <span>申请人 <b>{f.applicant || '—'}</b>{f.dept ? <span className="inv-muted"> · {f.dept}</span> : null}</span>
        <span>公司主体 <b>{f.company || '—'}</b></span>
        {payee.name ? <span>收款方 <b>{payee.name}</b>{(payee.bank || payee.account) ? <span className="inv-muted"> · {payee.bank || ''} {payee.account || ''}</span> : null}</span> : null}
        <span>提交 <b>{f.submittedBy || '—'}</b>{f.submittedAt ? <span className="inv-muted"> · {fmtTime(f.submittedAt)}</span> : null}</span>
        {f.reviewedBy ? <span>审核 <b>{f.reviewedBy}</b><span className="inv-muted"> · {fmtTime(f.reviewedAt)}</span></span> : null}
      </div>
      {f.reviewNote ? <div className="inv-au-rnote">{f.status === 'returned' ? '退回原因' : '审核备注'}：{f.reviewNote}</div> : null}
      <div className="inv-au-money" title="差额＝付款金额 −（专票＋普票＋发票后补单还没到的）">
        <span className="inv-au-money-t">金额核对</span>
        {g ? <>
          <Fig label="付款金额" v={g.pay === null || g.pay === undefined ? '—' : money(g.pay)} />
          <span className="inv-au-op">−（</span>
          <Fig label={`专票（${g.specialN} 张）`} v={money(g.special)} />
          <span className="inv-au-op">＋</span>
          <Fig label={`普票（${g.normalN} 张）`} v={money(g.normal)} />
          <span className="inv-au-op">＋</span>
          <Fig label="发票后补单" v={money(g.later)} cls={num(g.later) ? 'lt' : ''} />
          <span className="inv-au-op">）＝</span>
          <Fig label="差额" v={gap === null ? '—' : off ? money(gap) : '0.00'} cls={gap === null ? '' : off ? 'diff' : 'same'} />
        </> : <span className="inv-muted">没取到金额核对</span>}
      </div>
      {g && gap === null && <div className="inv-muted">这张单没有付款金额（手工票夹没填），不做差额核对</div>}
      {off && <div className="inv-au-focus">有差额 {money(Math.abs(gap))}（{gap > 0 ? '票比付款少' : '票比付款多'}），重点关注：提交时要写明差额原因</div>}
      {reason ? <div className={'inv-au-why' + (open || !long ? ' open' : '')}>事由：{reason}
        {long && <button type="button" className="inv-lk inv-au-lkb" onClick={() => setOpen(o => !o)}>{open ? '收起' : '展开'}</button>}</div> : null}
    </section>
  )
}

function CheckCell({ it }) {
  const bad = itemBadges(it).filter(b => CHECK_KEYS.has(b.key) && (b.tone === 'err' || b.tone === 'warn'))
  if (!bad.length) return <span className="inv-badge ok">正常</span>
  return <span className="inv-badges inv-au-nowrap">{bad.map(b => <span key={b.key} className={'inv-badge ' + b.tone} title={b.tip || undefined}>{b.label}</span>)}</span>
}

function StateCell({ it }) {
  if (it.review !== 'pending') return <span className="inv-muted">{ITEM_REVIEW[it.review] || ''}</span>
  if (it.doubt) return <span className="inv-au-st-q" title={it.doubt.text}>有疑问</span>
  if (it.auditOk) return <span className="inv-au-st-ok" title={`${it.auditOk.by || ''} ${it.auditOk.at ? fmtTime(it.auditOk.at) : ''}`}>✓ 已核</span>
  const n = Array.isArray(it.pending) ? it.pending.length : 0
  return <span className="inv-au-st-p">{n ? `待核 ${n} 项` : '待核对'}</span>
}

function dedOf(it, decisions) {
  if (it.kind !== 'invoice') return null
  return needsDecision(it) ? !!decisions[it.id] : !!it.deductible
}

function InvoiceTable({ items, decisions, orderOf, onOpen }) {
  const bills = items.filter(isBill)
  const others = items.filter(it => it.kind === 'other' && it.status !== 'removed')
  const sum = k => r2(bills.reduce((s, it) => s + (num(it[k]) || 0), 0))
  const ded = r2(bills.reduce((s, it) => s + (dedOf(it, decisions) ? (num(it.tax) || 0) : 0), 0))
  const nOk = bills.filter(it => it.review === 'pending' && it.auditOk).length
  const nPend = bills.filter(it => it.review === 'pending').length
  const nQ = bills.filter(it => it.review === 'pending' && it.doubt).length
  return (
    <section className="inv-au-sec">
      <div className="inv-au-sec-h">
        <b>已收发票</b>
        <span className="inv-muted">{bills.length} 张{nPend ? ` · 已核 ${nOk}/${nPend}` : ''}{nQ ? ` · 有疑问 ${nQ}` : ''}</span>
        <span className="inv-au-grow" />
        <span className="inv-muted inv-au-kbd">点一行打开核对 · 弹窗里 ← → 换票、Enter 提交这张</span>
      </div>
      {!bills.length ? <div className="inv-au-empty">这张单还没有发票</div> : <div className="inv-au-tw">
        <table className="inv-au-tbl">
          <thead><tr>
            <th>#</th><th>票种</th><th>销方</th><th>发票号码</th>
            <th className="num">价税合计</th><th className="num">税额</th><th>项目 · 税率</th>
            <th>系统核对</th><th>状态</th><th>可抵扣</th><th />
          </tr></thead>
          <tbody>{bills.map(it => {
            const d = dedOf(it, decisions)
            return <tr key={it.id} tabIndex={0} className={it.doubt ? 'q' : ''} onClick={() => onOpen(it.id)}
              onKeyDown={e => { if (e.key === 'Enter') onOpen(it.id) }}>
              <td className="inv-num">{orderOf(it.id)}</td>
              <td className={it.invType === 'special' ? '' : 'inv-au-nsp'}>{typeShort(it)}</td>
              <td className="inv-au-seller" title={it.sellerName || ''}>{it.sellerName || '—'}</td>
              <td className="inv-num" title={[it.number, it.date].filter(Boolean).join(' · ')}>{it.number ? '…' + String(it.number).slice(-8) : '—'}</td>
              <td className="num"><b>{money(shareOf(it))}</b>{it.split ? <span className="inv-muted" title={'票面 ' + money(it.total)}>（分摊）</span> : null}</td>
              <td className="num">{money(it.tax)}</td>
              <td className="inv-au-cat" title={it.category || ''}>{(it.category || '—').replace(/^.*\*/, '')}{it.taxRate ? ' ' + it.taxRate : ''}</td>
              <td><CheckCell it={it} /></td>
              <td><StateCell it={it} /></td>
              <td>{d === null ? <span className="inv-muted">—</span> : d
                ? <span className="inv-au-dd yes">可抵 <b className="inv-num">{money(it.tax)}</b></span>
                : <span className="inv-au-dd no">不可抵</span>}</td>
              <td className="inv-au-open" title="打开核对">›</td>
            </tr>
          })}</tbody>
          <tfoot><tr>
            <td /><td colSpan={3}>合计 {bills.length} 张</td>
            <td className="num">{money(r2(bills.reduce((s, it) => s + shareOf(it), 0)))}</td><td className="num">{money(sum('tax'))}</td>
            <td colSpan={3} /><td className="inv-au-dd yes">可抵 <b className="inv-num">{money(ded)}</b></td><td />
          </tr></tfoot>
        </table>
      </div>}
      {others.length > 0 && <div className="inv-au-others">
        <span className="inv-muted">非发票附件 {others.length} 份：</span>
        {others.map(it => <button type="button" key={it.id} className="inv-lk inv-au-lkb" onClick={() => onOpen(it.id)}
          title={it.typeLabel || ''}>{it.file?.name || it.typeLabel || `附件 #${it.id}`}</button>)}
      </div>}
    </section>
  )
}

function LaterTable({ laters }) {
  const rows = Array.isArray(laters) ? laters : []
  return (
    <section className="inv-au-sec">
      <div className="inv-au-sec-h"><b>发票后补单</b><span className="inv-muted">申请人付款时登记的</span></div>
      {!rows.length ? <div className="inv-au-empty">没有发票后补单</div> : <div className="inv-au-tw">
        <table className="inv-au-tbl inv-au-ltbl">
          <thead><tr>
            <th>后补单</th><th>登记</th><th className="num">预计补票</th><th className="num">已到</th><th className="num">还没到</th>
            <th>到票进度</th><th>预计到票</th><th>接收人</th><th>状态</th>
          </tr></thead>
          <tbody>{rows.map(l => {
            const exp = num(l.expectAmount) || 0
            const got = r2((num(l.receivedAmount) || 0) + (num(l.unregisteredAmount) || 0))
            const pct = exp > 0 ? Math.min(100, Math.round(got / exp * 100)) : 0
            return <tr key={l.id}>
              <td className="inv-num">#{l.id}</td>
              <td>{l.filedBy || '—'}{l.filedAt ? <span className="inv-muted"> · {fmtTime(l.filedAt)}</span> : null}</td>
              <td className="num">{money(l.expectAmount)}</td>
              <td className="num">{money(got)}</td>
              <td className="num"><b className={num(l.left) ? 'inv-au-lt' : ''}>{money(l.left)}</b></td>
              <td><i className="inv-au-bar"><i style={{ width: pct + '%' }} /></i> {pct}%</td>
              <td className="inv-num">{l.expectDate || '—'}</td>
              <td>{l.receiverName || '—'}</td>
              <td><span className={'inv-badge ' + (l.status === 'done' ? 'ok' : l.status === 'closed' ? 'mute' : 'info')}>{l.statusText || l.status}</span>
                {l.remindCount ? <span className="inv-muted"> 已催 {l.remindCount} 次</span> : null}</td>
            </tr>
          })}</tbody>
        </table>
      </div>}
    </section>
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

function LinesTable({ lines }) {
  if (!Array.isArray(lines) || !lines.length) return <div className="inv-au-empty">票面上没读出明细</div>
  return <div className="inv-au-tw"><table className="inv-au-lines2">
    <thead><tr><th>项目名称</th><th>规格型号</th><th>单位</th><th className="num">数量</th><th className="num">单价</th><th className="num">金额</th><th>税率</th><th className="num">税额</th></tr></thead>
    <tbody>{lines.map((l, i) => <tr key={i}>
      <td>{l.category ? `*${l.category}*` : ''}{l.name || '—'}</td>
      <td>{l.spec || ''}</td><td>{l.unit || ''}</td>
      <td className="num">{l.qty ?? ''}</td><td className="num">{l.price ?? ''}</td>
      <td className="num">{money(l.amount)}</td><td>{l.rate ?? ''}</td><td className="num">{money(l.tax)}</td>
    </tr>)}</tbody>
  </table></div>
}

const DOUBT_QUICK = ['抬头不对', '金额和付款对不上', '不是这张单的票', '图片看不清', '项目不能报销']

// 点一张票弹出来：左看图（旋转、缩放、拖动），右基础信息（系统已核的写依据）；右上 提交 / 有疑问 / 删除；下方本张明细
function ItemDialog({ item, order, total, editable, decision, setDecision, logs, onClose, onPrev, onNext,
  onMarkOk, onDoubt, onClearDoubt, onRemove, onSave, onRotate }) {
  const [mode, setMode] = useState('')       // '' | 'doubt' | 'del'
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [activeField, setActiveField] = useState(null)
  const dirty = useRef(false)
  useEffect(() => { setMode(''); setText(item?.doubt?.text || ''); setErr(''); setActiveField(null) }, [item?.id])
  const isBillItem = isBill(item)
  const run = async (fn) => {
    if (busy) return
    setBusy(true); setErr('')
    try { await fn() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const ok = () => {
    if (!editable || !isBillItem) return
    if (dirty.current) { setErr('字段改了还没保存：先点「保存」，再提交这张'); return }
    run(onMarkOk)
  }
  // ← → 换票，Enter 提交这张；人在打字、或在写疑问/确认删除时不抢
  useEffect(() => {
    const onKey = (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || mode) return
      if (isTyping(e.target) || isTyping(document.activeElement)) return
      if (e.key === 'ArrowLeft' && onPrev) { e.preventDefault(); onPrev() }
      else if (e.key === 'ArrowRight' && onNext) { e.preventDefault(); onNext() }
      else if (e.key === 'Enter' && editable && isBillItem) { e.preventDefault(); ok() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })
  if (!item) return null
  const who = item.createdBy === '系统' || !item.createdBy ? '系统' : item.createdBy
  const via = ORIGIN_CN[item.origin] || item.origin || ''
  const myLogs = (Array.isArray(logs) ? logs : []).filter(l => l && l.itemId === item.id)
  const title = <div className="inv-au-dlg-h">
    <b>第 {order} 张 · {item.kind === 'other' ? '非发票附件' : typeShort(item)}{isBillItem ? ' · ' + money(shareOf(item)) : ''}</b>
    <span className="inv-au-dlg-st"><StateCell it={item} /></span>
    <span className="inv-au-who" title={item.createdAt ? fmtTime(item.createdAt) : ''}>扫描人 <b>{who}</b> · {via}{item.createdAt ? ' · ' + fmtTime(item.createdAt) : ''}</span>
    <span className="inv-au-nav">
      <button type="button" className="inv-ib" disabled={!onPrev} onClick={onPrev} title="上一张（←）">‹</button>
      <span className="inv-num">{order} / {total}</span>
      <button type="button" className="inv-ib" disabled={!onNext} onClick={onNext} title="下一张（→）">›</button>
    </span>
    <span className="inv-au-grow" />
    {editable && isBillItem && <>
      <button type="button" className="btn inv-au-btn-q" disabled={busy} onClick={() => setMode(m => (m === 'doubt' ? '' : 'doubt'))}>有疑问</button>
      <button type="button" className="btn inv-au-btn-del" disabled={busy} onClick={() => setMode(m => (m === 'del' ? '' : 'del'))}>删除</button>
      <button type="button" className="btn primary" disabled={busy} onClick={ok} title="这张核对无误（Enter）">提交</button>
    </>}
  </div>
  return (
    <Modal title={title} onClose={onClose} width={1280}>
      <div className="inv-au-dlg">
        <div className="inv-au-dlg-view">
          <InvViewer item={item} activeField={activeField} height={520} onRotate={editable ? onRotate : undefined} />
        </div>
        <div className="inv-au-dlg-side">
          {err && <div className="inv-au-err">{err}</div>}
          {mode === 'doubt' && <div className="inv-au-qbox">
            <b>这张有什么疑问？（整单提交时一起退回收票台）</b>
            <span className="inv-au-quick">{DOUBT_QUICK.map(q => <button type="button" key={q} onClick={() => setText(q)}>{q}</button>)}</span>
            <textarea className="inv-au-ta" rows={2} autoFocus value={text} maxLength={200} placeholder="写一下疑问"
              onChange={e => setText(e.target.value)} />
            <span className="inv-au-row-end">
              <button type="button" className="btn" onClick={() => setMode('')}>取消</button>
              <button type="button" className="btn inv-au-btn-q" disabled={busy || !text.trim()}
                onClick={() => run(async () => { await onDoubt(text.trim()); setMode('') })}>记下疑问</button>
            </span>
          </div>}
          {mode === 'del' && <div className="inv-au-qbox del">
            <b>从这张单里删除这张票？删除后不计入收票合计（原件留档，留痕里查得到）。</b>
            <span className="inv-au-row-end">
              <button type="button" className="btn" onClick={() => setMode('')}>取消</button>
              <button type="button" className="btn inv-au-btn-del" disabled={busy} onClick={() => run(onRemove)}>确认删除</button>
            </span>
          </div>}
          {!mode && item.doubt && <div className="inv-au-qbox">
            <b>疑问：{item.doubt.text}</b>
            <span className="inv-muted">{item.doubt.by} {item.doubt.at ? fmtTime(item.doubt.at) : ''}</span>
            {editable && <span className="inv-au-row-end"><button type="button" className="btn" disabled={busy} onClick={() => run(onClearDoubt)}>清掉疑问</button></span>}
          </div>}
          <StatusBadges item={item} />
          {Array.isArray(item.warnings) && item.warnings.length > 0 && <ul className="inv-au-warns">{item.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          {item.kind === 'other'
            ? <div className="inv-au-note">这是附件（合同、清单、水单、行程单等），只留存不入台账，不用核字段。</div>
            : <>
              <div className="inv-muted inv-au-tip">点一行字段，左边图上框出它的位置。二维码读的、系统已核的不用再看，黄色「待核」的看一眼。</div>
              <FieldPanel item={item} editable={editable} activeField={activeField} onFieldFocus={setActiveField}
                onSave={onSave} hideConfirm onDirtyChange={d => { dirty.current = d }} />
              {item.kind === 'invoice' && <div className="inv-au-dedrow">
                <span>本张可抵扣进项税 <b className={'inv-num ' + (decision ? 'yes' : 'no')}>{money(item.tax)}</b></span>
                <span className="inv-muted">{item.deductSuggest === 'yes' ? '建议可抵扣' : item.deductSuggest === 'no' ? '建议不可抵扣' : '系统没给建议'}{item.deductReason ? '：' + item.deductReason : ''}</span>
                <span className="inv-au-grow" />
                {needsDecision(item)
                  ? <DeductToggle value={decision} disabled={!editable} onChange={setDecision} />
                  : <span className="inv-muted">{item.deductible ? '已定：可抵扣' : '已定：不可抵扣'}</span>}
              </div>}
            </>}
        </div>
      </div>
      {item.kind !== 'other' && <div className="inv-au-dlg-lines">
        <div className="inv-au-sec-h"><b>本张明细</b><span className="inv-muted">{Array.isArray(item.lines) ? item.lines.length : 0} 行</span></div>
        <LinesTable lines={item.lines} />
      </div>}
      {myLogs.length > 0 && <details className="inv-au-dlg-logs">
        <summary>这张票的留痕（{myLogs.length}）</summary>
        <ul>{myLogs.map((l, i) => <li key={l.id ?? i}><span className="inv-num inv-muted">{fmtTime(l.ts)}</span> {l.user || '系统'} <b>{l.action}</b></li>)}</ul>
      </details>}
    </Modal>
  )
}

const GAP_QUICK = ['餐补/补贴，不需要发票', '发票多开，按付款金额入账', '付款抹零']

// 底部固定：进度一行＋「提交」。有疑问 → 提交＝退回（带上疑问）；有没核的 → 先提示；有差额 → 写说明才能提交
function SubmitBar({ folder, items, decisions, canAct, actionable, busy, err, blockers, selfWarn, orderOf, onOpen,
  onApprove, onReturn }) {
  const [ask, setAsk] = useState(null)       // null | 'doubt' | 'unchecked' | 'gap'
  const [gapNote, setGapNote] = useState('')
  useEffect(() => { setAsk(null); setGapNote('') }, [folder?.id])
  const bills = items.filter(isBill)
  const pend = bills.filter(it => it.review === 'pending')
  const doubts = pend.filter(it => it.doubt)
  const unchecked = pend.filter(it => !it.auditOk && !it.doubt)
  const g = folder?.gap
  const gap = g ? num(g.gap) : null
  const off = gap !== null && Math.abs(gap) > 0.005
  const ded = r2(bills.reduce((s, it) => s + (dedOf(it, decisions) ? (num(it.tax) || 0) : 0), 0))
  const disabled = !canAct || !actionable || busy
  const doubtNote = () => '有疑问的票：' + doubts.map(it => `第 ${orderOf(it.id)} 张${it.number ? '（' + it.number.slice(-8) + '）' : ''}：${it.doubt.text}`).join('；')
  const step = (from) => {
    if (from < 1 && doubts.length) return setAsk('doubt')
    if (from < 2 && unchecked.length) return setAsk('unchecked')
    if (from < 3 && off) return setAsk('gap')
    setAsk(null)
    onApprove('')
  }
  return (
    <div className="inv-au-bar2">
      <div className="inv-au-bar2-row">
        <span className="inv-au-bar2-sum">
          <span>已核 <b className="inv-num">{pend.length - unchecked.length - doubts.length}/{pend.length}</b> 张</span>
          {unchecked.length > 0 && <span className="warn">还有 <b>{unchecked.length}</b> 张没核</span>}
          {doubts.length > 0 && <span className="err">有疑问 <b>{doubts.length}</b> 张</span>}
          <span>差额 <b className={'inv-num ' + (off ? 'err' : 'ok')}>{gap === null ? '—' : off ? money(gap) : '0.00'}</b></span>
          <span>可抵扣进项税 <b className="inv-num inv-au-acc">{money(ded)}</b></span>
        </span>
        {!canAct && <span className="inv-au-hint">{NO_PERM_HINT}，现在只能查看</span>}
        {canAct && !actionable && <span className="inv-au-hint">这张单现在没有要审的票（已审完或已退回收票台）</span>}
        {canAct && actionable && selfWarn && <span className="inv-au-hint warn">{selfWarn}</span>}
        <button type="button" className="btn primary inv-au-submit" disabled={disabled} onClick={() => step(0)}>
          {busy ? '正在处理…' : doubts.length ? `提交（退回 ${doubts.length} 个疑问）` : '提交'}
        </button>
      </div>
      {(err || (blockers && blockers.length > 0)) && <div className="inv-au-err">
        {err && <div>{err}</div>}
        {blockers && blockers.length > 0 && <ul>{blockers.map((b, i) => {
          const ids = blockerIds(b).filter(id => orderOf(id) > 0)
          return <li key={i}>{blockerText(b, orderOf)}
            {ids.map(id => <button type="button" key={id} className="inv-lk inv-au-lkb" onClick={() => onOpen(id)}>去看第 {orderOf(id)} 张</button>)}
          </li>
        })}</ul>}
      </div>}
      {ask === 'doubt' && <div className="inv-au-ask warn">
        <b>有 {doubts.length} 张记了疑问，提交会把整张单退回收票台，带上这些疑问：</b>
        <ul>{doubts.map(it => <li key={it.id}>第 {orderOf(it.id)} 张（{money(shareOf(it))}）：{it.doubt.text}
          <button type="button" className="inv-lk inv-au-lkb" onClick={() => onOpen(it.id)}>去看</button></li>)}</ul>
        <span className="inv-au-row-end">
          <button type="button" className="btn" onClick={() => setAsk(null)}>再看看</button>
          <button type="button" className="btn primary" disabled={busy} onClick={() => { setAsk(null); onReturn(doubtNote()) }}>确认退回</button>
        </span>
      </div>}
      {ask === 'unchecked' && <div className="inv-au-ask warn">
        <b>还有 {unchecked.length} 张没打开核对：{unchecked.slice(0, 8).map(it => `第 ${orderOf(it.id)} 张`).join('、')}{unchecked.length > 8 ? '…' : ''}</b>
        <span className="inv-au-row-end">
          <button type="button" className="btn" onClick={() => { setAsk(null); onOpen(unchecked[0].id) }}>去核第 {orderOf(unchecked[0].id)} 张</button>
          <button type="button" className="btn primary" disabled={busy} onClick={() => step(2)}>不逐张看了，仍然提交</button>
        </span>
      </div>}
      {ask === 'gap' && <div className="inv-au-ask err">
        <b>有差额 {money(gap)}：付款 {money(g.pay)} −（专票 {money(g.special)} ＋ 普票 {money(g.normal)} ＋ 后补 {money(g.later)}），写明原因才能提交；不合理就退回收票台</b>
        <span className="inv-au-quick">{GAP_QUICK.map(q => <button type="button" key={q} onClick={() => setGapNote(q)}>{q}</button>)}</span>
        <textarea className="inv-au-ta" rows={2} autoFocus value={gapNote} maxLength={300} placeholder="差额说明（必填，会留痕）"
          onChange={e => setGapNote(e.target.value)} />
        <span className="inv-au-row-end">
          <button type="button" className="btn" onClick={() => setAsk(null)}>先不提交</button>
          <button type="button" className="btn inv-au-btn-del" disabled={busy || !gapNote.trim()}
            onClick={() => { setAsk(null); onReturn('差额 ' + money(gap) + '：' + gapNote.trim()) }}>退回收票台</button>
          <button type="button" className="btn primary" disabled={busy || !gapNote.trim()}
            onClick={() => { setAsk(null); onApprove(gapNote.trim()) }}>确认差额并提交</button>
        </span>
      </div>}
    </div>
  )
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
  const [dlgId, setDlgId] = useState(null)
  const [decisions, setDecisions] = useState({})
  const [actBusy, setActBusy] = useState(false)
  const [actErr, setActErr] = useState('')
  const [blockers, setBlockers] = useState(null)
  const [lastRes, setLastRes] = useState(null)      // 上一次通过/退回的结果（切到下一个票夹后仍显示，自审在这里打标）
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

  const loadDetail = useCallback(async (id, { keep = false } = {}) => {
    if (!id) { setDetail(null); return }
    const my = ++dReq.current
    if (!keep) { setDLoading(true); setDErr('') }
    try {
      const r = await invFolder(id)
      if (my !== dReq.current) return
      const items = sortItems(r?.items)
      setDetail({ folder: r?.folder || null, items, logs: r?.logs || [] })
      setDErr('')
      // 抵扣判定：会计亲手点过的保留（刷新不冲掉），没点过的和新票用"已定值或系统建议"（建议随票面改动会变）
      if (!keep) touched.current = new Set()
      setDecisions(prev => mergeDecisions(keep ? prev : {}, items, touched.current))
      if (keep) setDlgId(cur => (cur !== null && items.some(it => it.id === cur) ? cur : null))
      else setDlgId(null)
    } catch (e) {
      if (my === dReq.current) { setDErr(errText(e)); if (!keep) setDetail(null) }
    } finally { if (my === dReq.current) setDLoading(false) }
  }, [])

  useEffect(() => {
    setActErr(''); setBlockers(null)
    loadDetail(selId)
  }, [selId, loadDetail])

  const items = detail?.items || []
  const folder = detail?.folder || null
  const orderOf = useCallback(id => items.findIndex(it => it.id === id) + 1, [items])
  const actionable = isActionable(folder, items)
  const busyItems = items.some(it => it.procStatus === 'pending' || it.procStatus === 'running')
  // 有票还在识别时，每 3 秒刷一下这个票夹（识别完自动停）
  usePoll(() => loadDetail(selId, { keep: true }), !!selId && busyItems, 3000)

  const dlgItem = items.find(it => it.id === dlgId) || null
  const dlgIdx = dlgItem ? items.indexOf(dlgItem) : -1
  const dlgEditable = !!dlgItem && canAct && actionable && dlgItem.review === 'pending'

  const replaceItem = (it) => {
    if (!it || !it.id) return
    setDetail(d => d ? { ...d, items: d.items.map(x => (x.id === it.id ? { ...x, ...it } : x)) } : d)
    // 改了票种/税额/项目类别，系统建议可能从「可抵扣」变「不可抵扣」：没亲手改判过的票跟着新建议走
    setDecisions(prev => mergeDecisions(prev, [it], touched.current))
  }
  // 改完字段顺手重读票夹：金额核对、留痕跟着刷新
  const refreshQuiet = () => { if (selId) loadDetail(selId, { keep: true }) }
  const onSave = async (changed) => {
    const r = await invItemUpdate(dlgItem.id, { fields: changed })   // 出错抛给 FieldPanel，显示在面板里
    replaceItem(r?.item)
    flash('字段已保存', 'ok')
    refreshQuiet()
  }
  // 弹窗「提交」：这张核对无误 → 跳到下一张还没核的（没有了就关弹窗）
  const onMarkOk = async () => {
    const id = dlgItem.id
    const r = await invAuditMark(id, { mark: 'ok' })
    replaceItem(r?.item)
    const rest = items.filter(it => it.id !== id && needCheck(it))
    const next = rest.find(it => items.indexOf(it) > dlgIdx) || rest[0]
    flash(`第 ${orderOf(id)} 张已核${next ? '，下一张' : '，全部核完'}`, 'ok')
    setDlgId(next ? next.id : null)
  }
  const onDoubt = async (text) => {
    const r = await invAuditMark(dlgItem.id, { mark: 'doubt', text })
    replaceItem(r?.item)
    flash(`第 ${orderOf(dlgItem.id)} 张记下疑问`, 'ok')
  }
  const onClearDoubt = async () => {
    const r = await invAuditMark(dlgItem.id, { mark: 'clear' })
    replaceItem(r?.item)
  }
  const onRemove = async () => {
    const id = dlgItem.id, n = orderOf(id)
    const rest = items.filter(it => it.id !== id && needCheck(it))
    await invItemRemove(id)
    flash(`已删除第 ${n} 张`, 'ok')
    setDlgId(rest.length ? rest[0].id : null)
    await loadDetail(selId, { keep: true })
  }
  const onRotate = async (rotation) => {
    try { await invItemRotate(dlgItem.id, rotation) } catch (e) { flash('旋转角度没存上：' + errText(e), 'err') }
  }
  const setDecision = (id, v) => { touched.current.add(id); setDecisions(d => ({ ...d, [id]: v })) }
  const step = (d) => {
    if (dlgIdx < 0) return
    const j = dlgIdx + d
    if (j >= 0 && j < items.length) setDlgId(items[j].id)
  }

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

  const approve = async (gapNote) => {
    if (!folder || actBusy) return
    const dec = {}
    items.filter(needsDecision).forEach(it => { dec[String(it.id)] = { deductible: !!decisions[it.id] } })
    // 只通过眼前这批待审的票：审核期间后补池又收进来的票，后端会回 409 让人先看
    const itemIds = pendingItemIds(items)
    setActBusy(true); setActErr(''); setBlockers(null)
    try {
      const r = await invAuditApprove({ folderId: folder.id, itemIds, decisions: dec, note: '', gapNote: gapNote || '' })
      const newItems = Array.isArray(r?.newItems) ? r.newItems : null
      if (r && r.ok === false && (newItems || r.httpStatus === 409)) {
        setActErr((r.msg || '审核期间这张单又进了新票，请刷新后再审') + '——已重新读取，新进的票请先看一眼再提交')
        flash('这张单进了新票，已刷新', 'err')
        await loadDetail(folder.id, { keep: true })
        if (newItems && newItems.length) setDlgId(newItems[0])
        return
      }
      if (!r || r.ok === false || r.detail) {
        const d = r && (r.msg || r.detail)
        setActErr('没能提交：' + (typeof d === 'string' && d ? d : '服务端没说原因，请稍后再试'))
        setBlockers(Array.isArray(r?.blockers) ? r.blockers : null)
        if (r && r.code === 'gapNote') await loadDetail(folder.id, { keep: true })   // 差额口径变了（后补单刚到票等）：重读
        return
      }
      const f = r.folder || folder
      setLastRes({ kind: 'ok', name: nameOf(f), selfReview: !!f.selfReview, id: f.id })
      flash(f.selfReview ? '已通过（自审，已留痕）' : '已通过', 'ok')
      await moveNext(folder.id)
    } catch (e) {
      setActErr('没能提交：' + errText(e))
    } finally { setActBusy(false) }
  }

  const doReturn = async (note) => {
    if (!folder || actBusy) return
    setActBusy(true); setActErr(''); setBlockers(null)
    try {
      const r = await invAuditReturn({ folderId: folder.id, note: note.slice(0, 500) })
      const f = r?.folder || folder
      setLastRes({ kind: 'ret', name: nameOf(f), id: f.id, note: f.reviewNote || note })
      flash('已退回收票台', 'ok')
      await moveNext(folder.id)
    } catch (e) { setActErr('退回没成功：' + errText(e)) } finally { setActBusy(false) }
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
    ? (me.isSuper ? '这张单是你提交的，主管理员可以自审，提交后标「自审」' : '这张单是你提交的——提交人不能审核自己提交的单，请交给其他会计审')
    : ''

  const changeTab = (t) => {
    if (t === tab) return
    setTab(t); setPage(1); setSelId(null); setChecked(new Set()); setBatchRes(null); setBatchErr('')
  }
  const doSearch = (v) => { setPage(1); setQ((v || '').trim()); setSelId(null) }

  return (
    <div>
      <div className="head">
        <div>
          <div className="h-title">发票审核</div>
          <div className="h-sub">收票工作台提交的单子在这里复核：先看金额核对，再逐张打开核对（系统已核的不用再看），最后点底部「提交」。没有异常的单子可以批量通过。</div>
        </div>
        <div className="inv-au-hacts">
          {cfg && !canAct && <span className="inv-badge mute" title="可以查看，不能通过/退回">{NO_PERM_HINT}</span>}
          <button type="button" className="btn" disabled={qLoading} onClick={() => { loadQueue(); loadCounts(); if (selId) loadDetail(selId, { keep: true }) }}>刷新</button>
        </div>
      </div>
      <div className="body">
        {cfgErr && <div className="banner err">没取到发票管家的权限设置：{cfgErr}。可以先看队列，提交等按钮会不可用。</div>}
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
            {!selId && <div className="inv-au-blank">{qLoading ? '正在取审核队列…' : '在左边点一张单开始审核'}</div>}
            {selId && dLoading && !detail && <div className="loading">正在打开…</div>}
            {selId && dErr && <div className="banner err">没打开：{dErr}
              <button type="button" className="btn" onClick={() => loadDetail(selId)}>重试</button></div>}
            {selId && folder && <>
              <AuditHeader folder={folder} />
              <InvoiceTable items={items} decisions={decisions} orderOf={orderOf} onOpen={setDlgId} />
              <LaterTable laters={folder.laters} />
              <SubmitBar folder={folder} items={items} decisions={decisions} canAct={canAct} actionable={actionable}
                busy={actBusy} err={actErr} blockers={blockers} selfWarn={selfWarn} orderOf={orderOf} onOpen={setDlgId}
                onApprove={approve} onReturn={doReturn} />
            </>}
          </main>
        </div>
      </div>
      {dlgItem && <ItemDialog item={dlgItem} order={dlgIdx + 1} total={items.length} editable={dlgEditable}
        decision={decisions[dlgItem.id]} setDecision={v => setDecision(dlgItem.id, v)} logs={detail?.logs}
        onClose={() => setDlgId(null)}
        onPrev={dlgIdx > 0 ? () => step(-1) : undefined} onNext={dlgIdx < items.length - 1 ? () => step(1) : undefined}
        onMarkOk={onMarkOk} onDoubt={onDoubt} onClearDoubt={onClearDoubt} onRemove={onRemove} onSave={onSave} onRotate={onRotate} />}
      {toast}
    </div>
  )
}
