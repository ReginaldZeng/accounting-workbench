// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 收票工作台：扫审批单开票夹 → 高拍仪/手机/拖文件进票 → 核对 → 提交（需求确认书五、技术方案 §5.2）
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 轮询分快慢两档（手机连着但没在拍只 10 秒看一眼，配对码扫到一半关了弹窗也照样等到连上）；
//   提交被拦的「去看这张」按 itemIds 出链接；只有查看权限的人也能打开/收起票夹看（只读，标「只能看」）。
// 页面只做编排：共用件（扫码框/相机/看图器/字段面板/配对弹窗/弹窗/轮询）全在 invShared.jsx，接口全在 api.js 的发票管家块。
// 轮询规矩见 deskPollMs：有票在识别 / 在拉钉钉附件 / 配对弹窗开着 / 手机正在用（2 分钟内来过）→ 1.5 秒；
//   手机连着但闲着、或配对码已生成还没扫上 → 10 秒；其余时候不轮询，每次操作后刷新一次。
import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  invConfig, invDesk, invScan, invOpenFolder, invCloseFolder, invUpload, invManualFolder, invRefreshFolder,
  invSubmitFolder, invItemUpdate, invItemRemove, invItemSplit, invItemRotate, invItemReprocess,
} from '../api.js'
import {
  money, fmtTime, useToast, usePoll, Modal, StatusBadges, itemBadges, InvViewer, FieldPanel,
  ScanInput, FileDrop, CameraPanel, PairModal,
} from './invShared.jsx'
import InvSettings from './InvSettings.jsx'
import './inv-desk.css'

// ───────────────────────── 常量与小工具 ─────────────────────────

const AUTO_KEY = 'inv_desk_auto'
const readAuto = () => { try { return localStorage.getItem(AUTO_KEY) === '1' } catch { return false } }
const saveAuto = v => { try { localStorage.setItem(AUTO_KEY, v ? '1' : '0') } catch { /* 隐私模式存不了：下次打开恢复默认 */ } }

const FOLDER_ST = {
  collecting: ['info', '收票中'],
  submitted: ['warn', '已提交，待审核'],
  approved: ['ok', '审核通过'],
  returned: ['err', '被退回'],
}
// 钉钉审批实例的状态/结果（topapi 原值）→ 白话
const APPROVAL_ST = { NEW: '新建', RUNNING: '审批中', COMPLETED: '审批完成', TERMINATED: '已撤销', CANCELED: '已撤销' }
const APPROVAL_RES = { agree: '同意', refuse: '拒绝' }
const LATER_ST = { open: '待收票', partial: '部分收到', done: '已收齐', closed: '已关闭' }
const ORIGIN_LABEL = {
  attachment: '审批附件', photo_field: '审批图片栏', camera: '高拍仪', phone: '手机', upload: '拖入文件',
  taxpack: '税局文件包', later: '后补收票', scanner: '扫码枪',
}
const ACTION_TONE = { folder: 'ok', item: 'ok', confirm: 'ok', dup: 'err', noFolder: 'warn', unknown: 'warn', error: 'err', failed: 'err' }

const isBusyItem = it => it && (it.procStatus === 'pending' || it.procStatus === 'running')
const isAttachBusy = f => f && (f.attachStatus === 'pending' || f.attachStatus === 'running')

export const DESK_POLL_FAST = 1500
export const DESK_POLL_IDLE = 10000
/**
 * 收票台该多久拉一次 /api/inv/desk（毫秒；0＝不轮询）。
 * 1.5 秒：有票在识别、在从钉钉拉附件、配对弹窗开着、手机正在用（后端 pair.active＝2 分钟内手机来过）。
 * 10 秒：手机连着但闲着（锁屏/关了页面），或配对码已生成还没扫上（弹窗提前关了也要等到连上，连上后手机一动就切回 1.5 秒）。
 * @param {{items?:object[], folder?:object|null, pair?:object|null, pairOpen?:boolean}} s
 * @returns {number}
 */
export function deskPollMs({ items, folder, pair, pairOpen } = {}) {
  if ((items || []).some(isBusyItem) || isAttachBusy(folder) || pairOpen || (pair && pair.active)) return DESK_POLL_FAST
  if (pair) return DESK_POLL_IDLE
  return 0
}
const num = v => (v === null || v === undefined || v === '' ? NaN : Number(v))
// 收款账号只露头尾：页面常开在工位上，旁边人能看到
function maskAccount(a) {
  const s = String(a || '').replace(/\s+/g, '')
  if (!s) return ''
  if (s.length <= 8) return s.replace(/^(.{2}).*(.{2})$/, '$1****$2')
  return s.slice(0, 4) + ' **** ' + s.slice(-4)
}
function dupWhere(item) {
  const b = itemBadges(item).find(x => x.key === 'dup')
  return b ? b.tip : ''
}
function blockerText(b) {
  if (b === null || b === undefined) return ''
  if (typeof b === 'string') return b
  return b.label || b.msg || b.text || b.reason || JSON.stringify(b)
}
// 拦截项指向哪几张票：后端给 itemIds:[…]（老写法 itemId 也认）
export function blockerItemIds(b) {
  if (!b || typeof b !== 'object') return []
  const ids = Array.isArray(b.itemIds) ? b.itemIds : (b.itemId !== undefined && b.itemId !== null ? [b.itemId] : [])
  return ids.filter(id => id !== null && id !== undefined && id !== '')
}

// 重复票的"嘀嘀"提示音：WebAudio 现生成，不带音频文件。扫码/拍照都是人手触发，浏览器允许出声。
let _audio = null
function beepError() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext
    if (!AC) return
    _audio = _audio || new AC()
    if (_audio.state === 'suspended') _audio.resume()
    const t = _audio.currentTime
    const o = _audio.createOscillator()
    const g = _audio.createGain()
    o.type = 'square'
    o.frequency.setValueAtTime(330, t)
    o.frequency.setValueAtTime(220, t + 0.16)
    g.gain.setValueAtTime(0.07, t)
    g.gain.setValueAtTime(0.07, t + 0.32)
    g.gain.linearRampToValueAtTime(0, t + 0.38)
    o.connect(g); g.connect(_audio.destination)
    o.start(t); o.stop(t + 0.4)
  } catch { /* 没声卡/被禁音：红屏照样有 */ }
}

// ───────────────────────── 顶部：扫码条 ─────────────────────────

function ScanStrip({ onScan, disabled, busy, res, pair }) {
  return (
    <div className="inv-dk-scan">
      <div className="inv-dk-scan-main">
        <ScanInput keepFocus onScan={onScan} disabled={disabled} placeholder="扫审批单或发票二维码…" />
        {busy && <span className="inv-dk-scan-busy"><span className="inv-spin" />&nbsp;正在认码…</span>}
      </div>
      {res && <div className={'inv-dk-scanres ' + (res.tone || 'info')} role="status">{res.text}{res.sub && <span className="inv-dk-scanres-sub">{res.sub}</span>}</div>}
      <div className="inv-dk-hints">
        <span><b>扫码枪</b>扫审批单开票夹，扫发票二维码登记进当前票夹</span>
        <span><b>高拍仪</b>票放稳自动拍，或按空格拍</span>
        <span><b>没有设备</b>点右上角「手机当相机」{pair && pair.bound ? <em className="inv-dk-ok">（手机已连上{pair.dtName ? '：' + pair.dtName : ''}）</em> : null}</span>
      </div>
    </div>
  )
}

// ───────────────────────── 左栏：我的票夹 ─────────────────────────

function RecentFolders({ rows, currentId, onOpen, busyId, err }) {
  return (
    <div className="inv-dk-card">
      <div className="inv-dk-card-h">我的票夹<span className="inv-muted">最近经手的单</span></div>
      {err && <div className="inv-dk-err">{err}</div>}
      {!rows || !rows.length
        ? <div className="inv-dk-empty-s">还没有经手过的票夹</div>
        : <div className="inv-dk-recent">
          {rows.map(f => {
            const st = FOLDER_ST[f.status] || ['mute', f.status || '—']
            return (
              <button type="button" key={f.id} className={'inv-dk-rec' + (f.id === currentId ? ' on' : '')}
                disabled={busyId === f.id} onClick={() => onOpen(f.id)}>
                <span className="inv-dk-rec-t">{f.title || f.businessId || ('票夹 #' + f.id)}</span>
                <span className="inv-dk-rec-m">
                  <span className={'inv-badge ' + st[0]}>{st[1]}</span>
                  <span>{f.applicant || '—'}</span>
                  <span className="inv-num">{money(f.amount)}</span>
                  {f.stats && <span>{f.stats.invoices || 0} 张票</span>}
                </span>
              </button>
            )
          })}
        </div>}
    </div>
  )
}

// ───────────────────────── 票夹头卡 ─────────────────────────

function AttachLine({ folder, canEdit, onRefresh, refreshing, refreshErr }) {
  const st = folder.attachStatus
  let body
  if (st === 'pending' || st === 'running') body = <span className="inv-dk-att busy"><span className="inv-spin" />&nbsp;正在从钉钉拉附件和图片栏里的电子票…</span>
  else if (st === 'failed') body = <span className="inv-dk-att err">附件没拉下来：{folder.attachMsg || '原因未知'}</span>
  else if (st === 'done') body = <span className="inv-dk-att ok">附件已拉取{folder.attachMsg ? '：' + folder.attachMsg : ''}</span>
  else if (folder.instId) body = <span className="inv-dk-att">这张单没有附件</span>
  else body = <span className="inv-dk-att">手工票夹（没有钉钉审批单）</span>
  return (
    <div className="inv-dk-attline">
      {body}
      {folder.instId && canEdit && (
        <button type="button" className="inv-ib wide" disabled={refreshing || isAttachBusy(folder)} onClick={onRefresh}
          title="重新从钉钉取这张审批单的状态和附件">{refreshing ? '取数中…' : '重新取审批'}</button>
      )}
      {refreshErr && <span className="inv-dk-err-in">{refreshErr}</span>}
    </div>
  )
}

function LaterLine({ folder }) {
  const l = folder.later
  if (l) {
    return <div className="inv-dk-later info">
      发票后补：{l.expectDate ? `预计 ${l.expectDate} 到票` : '预计到票日未填'}，接收人：{l.receiverName || '未指定'}
      <span className="inv-badge info">{LATER_ST[l.status] || l.status || '已登记'}</span>
    </div>
  }
  // 后端判定"付款单没附发票、也没登记后补"时置 laterMissing（前端不自己猜：报销单的纸票本来就是后放的）
  if (folder.laterMissing) {
    return <div className="inv-dk-later warn">无票、未登记后补：这张付款单没附发票，也没在「发票后补池」登记。请提醒申请人登记后补，或把发票放进来。</div>
  }
  return null
}

function FolderHead({ folder, canEdit, viewOnly, onRefresh, refreshing, refreshErr, onClose, closing }) {
  const st = FOLDER_ST[folder.status] || ['mute', folder.status || '—']
  const payee = folder.payee || {}
  const appr = [APPROVAL_ST[folder.approvalStatus] || folder.approvalStatus, APPROVAL_RES[folder.approvalResult] || folder.approvalResult].filter(Boolean).join(' · ')
  return (
    <div className="inv-dk-fhead">
      <div className="inv-dk-fhead-top">
        <div className="inv-dk-ftitle">
          <span className={'inv-badge ' + st[0]}>{st[1]}</span>
          <b>{folder.title || folder.businessId || ('票夹 #' + folder.id)}</b>
        </div>
        <span className="inv-dk-fhead-acts">
          {viewOnly && <span className="inv-badge mute" title="没有「收票」权限：只能看，不能放票、改字段或提交">只能看</span>}
          {/* 打开/收起只是"看哪个票夹"，只有查看权限也能用（后端 desk/open、desk/close 只要收票工作台页面权限） */}
          <button type="button" className="inv-ib wide" onClick={onClose} disabled={closing}
            title={viewOnly ? '收起这个票夹' : '把这个票夹收起来，扫下一张审批单'}>收起</button>
        </span>
      </div>
      <div className="inv-dk-kv">
        <div><span>审批类型</span><b>{folder.template || '—'}</b></div>
        <div><span>审批编号</span><b className="inv-num">{folder.businessId || '—'}</b></div>
        <div><span>申请人</span><b>{folder.applicant || '—'}{folder.dept ? <em> · {folder.dept}</em> : null}</b></div>
        <div><span>公司主体</span><b>{folder.company || '—'}</b></div>
        <div className="wide"><span>收款方</span><b>{payee.name || '—'}{payee.bank ? <em> · {payee.bank}</em> : null}{payee.account ? <em className="inv-num"> · {maskAccount(payee.account)}</em> : null}</b></div>
        <div><span>单据金额</span><b className="inv-num inv-dk-amt">{money(folder.amount)}</b></div>
        <div><span>审批状态</span><b>{appr || '—'}</b></div>
      </div>
      <AttachLine folder={folder} canEdit={canEdit} onRefresh={onRefresh} refreshing={refreshing} refreshErr={refreshErr} />
      <LaterLine folder={folder} />
      {folder.status === 'returned' && (
        <div className="inv-dk-returned">
          <b>会计退回了这张单</b>
          <div className="inv-dk-returned-note">{folder.reviewNote || '（没写退回原因，请问一下审核的会计）'}</div>
          <span className="inv-muted">{folder.reviewedBy || ''}{folder.reviewedAt ? ' · ' + fmtTime(folder.reviewedAt) : ''} —— 按原因改好后，再点下面的「提交」</span>
        </div>
      )}
      {folder.status === 'submitted' && (
        <div className="inv-dk-submitted">已提交，待审核{folder.submittedBy ? `（${folder.submittedBy}${folder.submittedAt ? ' · ' + fmtTime(folder.submittedAt) : ''}）` : ''}。审核前这里只能看，不能再改。</div>
      )}
      {folder.status === 'approved' && (
        <div className="inv-dk-submitted ok">会计已审核通过{folder.reviewedBy ? `（${folder.reviewedBy}${folder.reviewedAt ? ' · ' + fmtTime(folder.reviewedAt) : ''}）` : ''}，票已进发票台账。</div>
      )}
    </div>
  )
}

// ───────────────────────── 票列表 ─────────────────────────

function ItemCard({ item, idx, onOpen }) {
  const f = item.file || null
  const isOther = item.kind === 'other'
  return (
    <button type="button" className={'inv-dk-item' + (item.flags && item.flags.dup ? ' dup' : '') + (item.review === 'void' ? ' void' : '')}
      onClick={() => onOpen(item.id)}>
      <span className="inv-dk-item-img">
        {f && f.thumb ? <img src={f.thumb} alt="" loading="lazy" draggable={false} />
          : <span className="inv-thumb-ph">{f ? '无预览' : '仅二维码'}</span>}
        {isBusyItem(item) && <span className="inv-thumb-busy"><span className="inv-spin" /></span>}
      </span>
      <span className="inv-dk-item-b">
        <span className="inv-dk-item-l1">
          <b className="inv-dk-item-n">{idx + 1}</b>
          <span className="inv-dk-item-type">{isOther ? '非发票附件' : (item.typeLabel || (item.kind === 'receipt' ? '收据' : '发票'))}</span>
          <span className="inv-dk-grow" />
          {!isOther && <b className="inv-num inv-dk-item-total">{money(item.total)}</b>}
        </span>
        <span className="inv-dk-item-l2">
          {item.number && <span className="inv-num">{item.number}</span>}
          {item.date && <span>{item.date}</span>}
          <span className="inv-dk-item-seller" title={item.sellerName || ''}>{item.sellerName || (isOther ? (f && f.name) || '' : isBusyItem(item) ? '销方识别中…' : '')}</span>
        </span>
        <span className="inv-dk-item-l3">
          <StatusBadges item={item} />
          {item.split ? <span className="inv-badge info">拆分 · 本单 {money(item.alloc)}</span> : null}
          <span className="inv-dk-grow" />
          <span className="inv-muted">{ORIGIN_LABEL[item.origin] || item.origin || ''}</span>
        </span>
        {item.flags && item.flags.dup && <span className="inv-dk-item-dup">{dupWhere(item)}</span>}
      </span>
    </button>
  )
}

function UploadReceipt({ res, onClose }) {
  if (!res) return null
  return (
    <div className={'inv-dk-receipt' + (res.error ? ' err' : '')}>
      <div className="inv-dk-receipt-h">
        <b>{res.error ? '上传没成功' : `刚收到 ${res.list.length} 个文件`}</b>
        <span className="inv-dk-grow" />
        <button type="button" className="inv-x" onClick={onClose} aria-label="关闭回执">×</button>
      </div>
      {res.error && <div className="inv-dk-err-in">{res.error}</div>}
      {res.list.map((r, i) => (
        <div key={i} className={'inv-dk-receipt-row ' + (ACTION_TONE[r.action] || 'info')}>
          <span className="inv-dk-receipt-name" title={r.name}>{r.name || '照片'}</span>
          <span>{r.msg || r.action || ''}</span>
        </div>
      ))}
    </div>
  )
}

function ItemList({ items, canEdit, onOpen, onFiles, upBusy, receipt, onReceiptClose }) {
  const pick = useRef(null)
  return (
    <div className="inv-dk-items">
      <div className="inv-dk-items-h">
        <b>票夹里的票</b><span className="inv-muted">{items.length ? `共 ${items.length} 张，点一张放大核对` : ''}</span>
        <span className="inv-dk-grow" />
        {upBusy && <span className="inv-muted"><span className="inv-spin" />&nbsp;上传中…</span>}
        {canEdit && <>
          <button type="button" className="btn" onClick={() => pick.current && pick.current.click()}>选文件</button>
          <input ref={pick} type="file" hidden multiple accept=".pdf,.ofd,.xml,.zip,image/*"
            onChange={e => { const fs = [...(e.target.files || [])]; e.target.value = ''; if (fs.length) onFiles(fs) }} />
        </>}
      </div>
      <UploadReceipt res={receipt} onClose={onReceiptClose} />
      {!items.length
        ? <div className="inv-dk-empty-s">
          {canEdit ? '还没有票。把发票放到高拍仪下、用扫码枪扫发票二维码，或把 PDF／OFD／XML／图片／压缩包拖到这里。' : '这个票夹里没有票。'}
        </div>
        : <div className="inv-dk-grid">{items.map((it, i) => <ItemCard key={it.id} item={it} idx={i} onOpen={onOpen} />)}</div>}
    </div>
  )
}

// ───────────────────────── 底部：合计与提交 ─────────────────────────

function FolderFoot({ folder, items, canEdit, onSubmit, submitting, submitRes, onPickItem }) {
  const s = folder.stats || {}
  const sum = Number.isFinite(num(s.sumTotal)) ? num(s.sumTotal) : items.filter(i => i.kind !== 'other').reduce((a, i) => a + (Number(i.split ? i.alloc : i.total) || 0), 0)
  const amt = num(folder.amount)
  const diff = Number.isFinite(num(s.diff)) ? num(s.diff) : (Number.isFinite(amt) ? sum - amt : NaN)
  const off = Number.isFinite(diff) && Math.abs(diff) > 0.005
  const canSubmit = canEdit && (folder.status === 'collecting' || folder.status === 'returned')
  const blockers = submitRes && Array.isArray(submitRes.blockers) ? submitRes.blockers : []
  return (
    <div className="inv-dk-foot">
      <div className="inv-dk-sum">
        <div><span>票合计</span><b className="inv-num">{money(sum)}</b></div>
        <div><span>单据金额</span><b className="inv-num">{money(folder.amount)}</b></div>
        <div className={off ? 'off' : 'eq'}><span>差额</span><b className="inv-num">{Number.isFinite(diff) ? (off ? money(diff) : '一致') : '—'}</b></div>
        {off && <span className="inv-muted">只提示，不拦提交</span>}
      </div>
      <div className="inv-dk-counts">
        <span>发票 <b>{s.invoices ?? items.filter(i => i.kind !== 'other').length}</b></span>
        <span>其它附件 <b>{s.others ?? items.filter(i => i.kind === 'other').length}</b></span>
        {!!s.dup && <span className="err">重复 <b>{s.dup}</b></span>}
        {!!s.processing && <span className="info">识别中 <b>{s.processing}</b></span>}
        {!!s.unchecked && <span className="warn">待核 <b>{s.unchecked}</b></span>}
        {!!s.paperMissing && <span>等纸质件 <b>{s.paperMissing}</b></span>}
      </div>
      {canSubmit && (
        <div className="inv-dk-submit">
          <div className="inv-dk-submit-msg">
            {submitRes && submitRes.msg && <div className="inv-dk-err-in">{submitRes.msg}</div>}
            {blockers.length > 0 && <ul className="inv-dk-blockers">
              {blockers.map((b, i) => {
                const ids = blockerItemIds(b)
                return <li key={i}>{blockerText(b)}
                  {ids.map((id, j) => {
                    const n = items.findIndex(it => it.id === id)
                    return <React.Fragment key={id}> <a className="inv-lk" role="button" tabIndex={0} onClick={() => onPickItem(id)}
                      onKeyDown={e => { if (e.key === 'Enter') onPickItem(id) }}>
                      {ids.length === 1 ? '去看这张' : (n >= 0 ? `去看第 ${n + 1} 张` : `去看第 ${j + 1} 张`)}</a></React.Fragment>
                  })}
                </li>
              })}
            </ul>}
          </div>
          <button type="button" className="btn primary inv-dk-submit-btn" disabled={submitting} onClick={onSubmit}>
            {submitting ? '提交中…' : folder.status === 'returned' ? '改好了，重新提交' : '提交'}
          </button>
        </div>
      )}
    </div>
  )
}

// ───────────────────────── 单张票详情弹窗 ─────────────────────────

function SplitBox({ item, disabled, onSaved }) {
  const [on, setOn] = useState(!!item.split)
  const [alloc, setAlloc] = useState(item.alloc === null || item.alloc === undefined ? '' : String(item.alloc))
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setOn(!!item.split); setAlloc(item.alloc === null || item.alloc === undefined ? '' : String(item.alloc)); setErr('') }, [item.id, item.split, item.alloc])
  const save = async () => {
    let a = null
    if (on) {
      const s = alloc.replace(/[,，\s¥￥]/g, '')
      a = Number(s)
      if (!s || !Number.isFinite(a) || a <= 0) { setErr('填这张单用掉的金额（大于 0）'); return }
      if (Number.isFinite(num(item.total)) && a > num(item.total) + 0.005) { setErr('本单金额不能超过票面价税合计 ' + money(item.total)); return }
      a = Math.round(a * 100) / 100
    }
    setBusy(true); setErr('')
    try { await invItemSplit(item.id, { split: on, alloc: a }); onSaved('拆分已保存') } catch (e) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  return (
    <div className="inv-dk-split">
      <label className="inv-dk-chk"><input type="checkbox" checked={on} disabled={disabled} onChange={e => setOn(e.target.checked)} />这张票拆给几张单用，本单只用一部分</label>
      {on && <input className="inv-in inv-dk-alloc" value={alloc} disabled={disabled} inputMode="decimal" placeholder="本单用掉的金额"
        onChange={e => { setAlloc(e.target.value); setErr('') }} onKeyDown={e => { if (e.key === 'Enter') save() }} />}
      <button type="button" className="btn" disabled={disabled || busy || (on === !!item.split && (!on || String(item.alloc ?? '') === alloc))} onClick={save}>{busy ? '保存中…' : '保存拆分'}</button>
      {err && <div className="inv-dk-err-in">{err}</div>}
    </div>
  )
}

function ItemLines({ lines }) {
  if (!Array.isArray(lines) || !lines.length) return null
  return (
    <details className="inv-dk-lines">
      <summary>票面明细（{lines.length} 行）</summary>
      <table>
        <thead><tr><th>项目</th><th>规格</th><th>数量</th><th>金额</th><th>税率</th><th>税额</th></tr></thead>
        <tbody>
          {lines.map((l, i) => <tr key={i}>
            <td>{l.name || '—'}</td><td>{l.spec || ''}</td><td className="inv-num">{l.qty ?? ''}{l.unit ? ' ' + l.unit : ''}</td>
            <td className="inv-num">{money(l.amount)}</td><td>{l.rate ?? ''}</td><td className="inv-num">{money(l.tax)}</td>
          </tr>)}
        </tbody>
      </table>
    </details>
  )
}

function ItemModal({ items, id, canEdit, onClose, onPick, reload, flash }) {
  const idx = items.findIndex(i => i.id === id)
  const item = idx >= 0 ? items[idx] : null
  const [field, setField] = useState(null)
  const [actErr, setActErr] = useState('')
  const [busy, setBusy] = useState('')
  useEffect(() => { setField(null); setActErr('') }, [id])
  if (!item) {
    return <Modal title="票据详情" onClose={onClose} width={480}>
      <div className="inv-dk-empty-s">这张票已经不在票夹里了（可能刚被移除）。</div>
    </Modal>
  }
  const editable = canEdit && item.review !== 'void'
  // 看图区跟着屏幕高度走：小屏（1024×720 之类）也能一眼看到整张票
  const viewH = Math.max(360, Math.min(620, (window.innerHeight || 800) - 230))
  const run = async (key, fn, okText) => {
    setBusy(key); setActErr('')
    try { await fn(); if (okText) flash(okText, 'ok'); await reload(); return true } catch (e) { setActErr(e.message || String(e)); return false } finally { setBusy('') }
  }
  const remove = () => {
    if (!window.confirm('把这张票从票夹里移除？移除后不算进这张单，操作会留痕。')) return
    run('remove', () => invItemRemove(item.id), '已移除').then(ok => { if (ok) onClose() })   // 失败时留在弹窗里看原因
  }
  const title = `第 ${idx + 1} 张 · ${item.kind === 'other' ? '非发票附件' : (item.typeLabel || '票据')}${item.number ? ' · ' + item.number : ''}`
  return (
    <Modal title={title} onClose={onClose} width={1180}
      footer={<>
        {actErr && <span className="inv-dk-err-in inv-dk-foot-err">{actErr}</span>}
        {editable && <button type="button" className="btn" disabled={!!busy || !item.file} onClick={() => run('re', () => invItemReprocess(item.id), '已重新送去识别，几秒后刷新')}
          title={item.file ? '重新读一遍这张票（识别失败或读错时用）' : '只有二维码信息，没有图片可识别'}>{busy === 're' ? '提交中…' : '重新识别'}</button>}
        {editable && <button type="button" className="btn inv-dk-danger" disabled={!!busy} onClick={remove}>{busy === 'remove' ? '移除中…' : '移除'}</button>}
        <button type="button" className="btn primary" onClick={onClose}>关闭</button>
      </>}>
      <div className="inv-dk-detail">
        <div className="inv-dk-detail-l">
          <InvViewer item={item} activeField={field} height={viewH}
            onRotate={editable ? (r => { invItemRotate(item.id, r).catch(e => setActErr('旋转没存上：' + (e.message || e))) }) : undefined}
            onPrev={idx > 0 ? () => onPick(items[idx - 1].id) : undefined}
            onNext={idx < items.length - 1 ? () => onPick(items[idx + 1].id) : undefined} />
        </div>
        <div className="inv-dk-detail-r">
          <div className="inv-dk-detail-badges"><StatusBadges item={item} /></div>
          {item.flags && item.flags.dup && <div className="inv-dk-alert err"><b>重复票</b>{dupWhere(item)}。不能挂在这张单上，请移除；如确实是一票多单，请用下面的「拆分」。</div>}
          {item.procStatus === 'failed' && <div className="inv-dk-alert err"><b>识别失败</b>{item.procError || '图片读不出来'}。可以对着图手工补字段，或点「重新识别」。</div>}
          {isBusyItem(item) && <div className="inv-dk-alert info"><span className="inv-spin" />&nbsp;正在读票面，几秒后字段会自动补上</div>}
          <FieldPanel item={item} editable={editable} activeField={field} onFieldFocus={setField}
            onSave={async changed => { await invItemUpdate(item.id, { fields: changed }); flash('已保存', 'ok'); await reload() }}
            onConfirm={async () => { await invItemUpdate(item.id, { fields: {}, confirm: 'all' }); flash('已核对', 'ok'); await reload() }} />
          {item.kind !== 'other' && <SplitBox item={item} disabled={!editable} onSaved={async t => { flash(t, 'ok'); await reload() }} />}
          <ItemLines lines={item.lines} />
          {item.remark && <div className="inv-dk-remark"><span>备注</span>{item.remark}</div>}
          <div className="inv-muted">来源：{ORIGIN_LABEL[item.origin] || item.origin || '—'}{item.createdBy ? ' · ' + item.createdBy : ''}{item.createdAt ? ' · ' + fmtTime(item.createdAt) : ''}</div>
        </div>
      </div>
    </Modal>
  )
}

// ───────────────────────── 手工票夹弹窗 ─────────────────────────

function ManualModal({ companies, onClose, onDone }) {
  const [title, setTitle] = useState('')
  const [amount, setAmount] = useState('')
  const [company, setCompany] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const list = (companies || []).map(c => (typeof c === 'string' ? c : c && c.name)).filter(Boolean)
  const go = async () => {
    const t = title.trim()
    if (!t) { setErr('写一下这是什么单（例：9 月快递费、某某出差报销）'); return }
    let a
    const s = amount.replace(/[,，\s¥￥]/g, '')
    if (s) { a = Number(s); if (!Number.isFinite(a)) { setErr('单据金额要填数字，不知道可以空着'); return } }
    setBusy(true); setErr('')
    try {
      const r = await invManualFolder({ title: t, amount: s ? Math.round(a * 100) / 100 : undefined, company: company || undefined })
      await onDone(r && r.folder)
    } catch (e) { setErr(e.message || String(e)); setBusy(false) }
  }
  return (
    <Modal title="手工票夹" onClose={onClose} width={480}
      footer={<>
        <button type="button" className="btn" onClick={onClose}>取消</button>
        <button type="button" className="btn primary" disabled={busy} onClick={go}>{busy ? '建票夹中…' : '建票夹'}</button>
      </>}>
      <div className="inv-dk-form">
        <div className="inv-muted">没有钉钉审批单（或审批单扫不出来）时，先建一个手工票夹把票收进来。</div>
        <label><span>这是什么单</span><input className="inv-in" autoFocus value={title} onChange={e => { setTitle(e.target.value); setErr('') }} placeholder="例：9 月快递费" /></label>
        <label><span>单据金额（可空）</span><input className="inv-in" value={amount} inputMode="decimal" onChange={e => { setAmount(e.target.value); setErr('') }} /></label>
        <label><span>公司主体（可空）</span>
          <select className="inv-dk-sel" value={company} onChange={e => setCompany(e.target.value)}>
            <option value="">不指定</option>
            {list.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        {err && <div className="inv-dk-err-in">{err}</div>}
      </div>
    </Modal>
  )
}

// ───────────────────────── 空状态 ─────────────────────────

function EmptyFolder({ canEdit, onManual, lastSubmitted, onReopen }) {
  return (
    <div className="inv-dk-emptyfolder">
      {lastSubmitted && (
        <div className="inv-dk-submitted ok">刚提交：{lastSubmitted.title || lastSubmitted.businessId || ('票夹 #' + lastSubmitted.id)} —— 已提交，待审核。
          <a className="inv-lk" onClick={() => onReopen(lastSubmitted.id)}>再打开看看</a></div>
      )}
      {canEdit ? <>
        <div className="inv-dk-emptyfolder-big">扫一下审批单二维码，开始一个票夹</div>
        <div className="inv-muted">用扫码枪扫钉钉审批单上的二维码（或手机「扫审批单」）；系统会自动把附件里的电子票拉进来。</div>
        <button type="button" className="btn" onClick={onManual}>没有审批单？建一个手工票夹</button>
      </> : <>
        <div className="inv-dk-emptyfolder-big">在左边「我的票夹」里点一个票夹查看</div>
        <div className="inv-muted">你现在只有查看权限：能看票夹里的票和核对情况，不能收票、改字段或提交。</div>
      </>}
    </div>
  )
}

// ───────────────────────── 页面 ─────────────────────────

export default function InvDesk({ user }) {
  const [cfg, setCfg] = useState(null)
  const [cfgErr, setCfgErr] = useState('')
  const [desk, setDesk] = useState(null)
  const [deskErr, setDeskErr] = useState('')
  const [pairOpen, setPairOpen] = useState(false)
  const [setOpen, setSetOpen] = useState(false)
  const [manualOpen, setManualOpen] = useState(false)
  const [detailId, setDetailId] = useState(null)
  const [scanBusy, setScanBusy] = useState(false)
  const [scanRes, setScanRes] = useState(null)
  const [upBusy, setUpBusy] = useState(0)
  const [receipt, setReceipt] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitRes, setSubmitRes] = useState(null)
  const [lastSubmitted, setLastSubmitted] = useState(null)
  const [openBusy, setOpenBusy] = useState(null)
  const [openErr, setOpenErr] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshErr, setRefreshErr] = useState('')
  const [closing, setClosing] = useState(false)
  const [auto, setAuto] = useState(readAuto)
  const [alarm, setAlarm] = useState(0)
  const [toast, flash] = useToast()
  const receiptTimer = useRef(null)

  const can = (cfg && cfg.can) || {}
  const folder = desk ? desk.folder : null
  const items = (desk && Array.isArray(desk.items)) ? desk.items : []
  const pair = desk ? desk.pair : null
  const canEdit = !!can.intake && !!folder && (folder.status === 'collecting' || folder.status === 'returned')

  const loadCfg = useCallback(async () => {
    try { setCfg(await invConfig()); setCfgErr('') } catch (e) { setCfgErr(e.message || String(e)) }
  }, [])
  const loadDesk = useCallback(async () => {
    try { const r = await invDesk(); setDesk(r || {}); setDeskErr('') } catch (e) { setDeskErr(e.message || String(e)) }
  }, [])

  useEffect(() => { loadCfg() }, [loadCfg])

  // 深链 #/invdesk?folder=ID（审核退回的钉钉通知会带）：先打开该票夹再收回 hash；只消费一次
  const deepDone = useRef(false)
  useEffect(() => {
    if (deepDone.current) return
    deepDone.current = true
    const m = (window.location.hash || '').match(/^#\/invdesk\?(.+)$/)
    const id = m ? parseInt(new URLSearchParams(m[1]).get('folder') || '', 10) : 0
    ;(async () => {
      if (id) {
        try { await invOpenFolder(id) } catch (e) { setOpenErr('打开链接里的票夹失败：' + (e.message || e)) }
        try { window.history.replaceState(null, '', '#/invdesk') } catch { /* 忽略 */ }
      }
      await loadDesk()
    })()
  }, [loadDesk])

  // 活跃时才轮询（快慢两档见 deskPollMs）：手机正在拍时 1.5 秒，手机连着但闲着 10 秒——
  // 配对 12 小时里不再整天 1.5 秒一轮地打后端、刷日志中心
  const pollMs = desk ? deskPollMs({ items, folder, pair, pairOpen }) : 0
  usePoll(loadDesk, pollMs > 0, pollMs || DESK_POLL_FAST)

  // 换了票夹：清掉上一个票夹的提交回执
  const fid = folder ? folder.id : null
  useEffect(() => { setSubmitRes(null); setRefreshErr('') }, [fid])
  useEffect(() => () => clearTimeout(receiptTimer.current), [])

  const alarmNow = () => { beepError(); setAlarm(n => n + 1) }

  const onScan = async (code) => {
    setScanBusy(true)
    try {
      const r = (await invScan(code)) || {}
      const act = r.action
      if (act === 'folder') {
        const f = r.folder || {}
        setScanRes({ tone: 'ok', text: r.msg || `已打开票夹：${f.title || f.businessId || ''}` })
        setLastSubmitted(null)
      } else if (act === 'item') setScanRes({ tone: 'ok', text: r.msg || '已登记进当前票夹' })
      else if (act === 'confirm') setScanRes({ tone: 'ok', text: r.msg || '纸质件已到，已对上附件里的电子票' })
      else if (act === 'dup') {
        setScanRes({ tone: 'err', text: r.msg || '重复票！这张票别处已经登记过', sub: r.item ? dupWhere(r.item) : '' })
        alarmNow()
      } else if (act === 'noFolder') setScanRes({ tone: 'warn', text: '先扫审批单，再扫发票', sub: r.msg && r.msg !== '先扫审批单，再扫发票' ? r.msg : '' })
      else setScanRes({ tone: 'warn', text: r.msg || '认不出这个码：请扫钉钉审批单或发票上的二维码' })
      await loadDesk()
    } catch (e) {
      setScanRes({ tone: 'err', text: '扫码没成功：' + (e.message || e) })
    } finally { setScanBusy(false) }
  }

  const showReceipt = (rec) => {
    clearTimeout(receiptTimer.current)
    setReceipt(rec)
    // 全部顺利的回执 8 秒后自己收起；有重复/失败的留着，等人看完手动关
    const bad = rec.error || rec.list.some(r => (ACTION_TONE[r.action] || 'info') !== 'ok')
    if (!bad) receiptTimer.current = setTimeout(() => setReceipt(null), 8000)
  }

  const upload = async (files, origin) => {
    if (!folder) { setScanRes({ tone: 'warn', text: '先扫审批单打开票夹，再放票' }); return }
    if (!canEdit) { flash('这个票夹现在不能再加票', 'err'); return }
    setUpBusy(n => n + 1)
    try {
      const r = (await invUpload(files, { origin, folderId: folder.id })) || {}
      const list = Array.isArray(r.results) ? r.results : []
      showReceipt({ list })
      if (list.some(x => x.action === 'dup')) alarmNow()
      if (origin === 'camera' && list[0]) {
        const x = list[0]
        setScanRes({ tone: ACTION_TONE[x.action] || 'info', text: x.msg || '已拍，正在读票', sub: x.action === 'dup' && x.item ? dupWhere(x.item) : '' })
      }
    } catch (e) {
      const list = e.body && Array.isArray(e.body.results) ? e.body.results : []
      showReceipt({ error: e.message || String(e), list })
    } finally {
      setUpBusy(n => Math.max(0, n - 1))
      await loadDesk()
    }
  }

  const openFolder = async (id) => {
    setOpenBusy(id); setOpenErr('')
    try { await invOpenFolder(id); setLastSubmitted(null); setScanRes(null); await loadDesk() } catch (e) { setOpenErr('打开失败：' + (e.message || e)) } finally { setOpenBusy(null) }
  }
  const closeFolder = async () => {
    setClosing(true)
    try { await invCloseFolder(); setLastSubmitted(null); await loadDesk() } catch (e) { flash('收起失败：' + (e.message || e), 'err') } finally { setClosing(false) }
  }
  const refresh = async () => {
    if (!folder) return
    setRefreshing(true); setRefreshErr('')
    try { await invRefreshFolder(folder.id); flash('已重新取审批，附件在后台拉', 'ok'); await loadDesk() } catch (e) { setRefreshErr(e.message || String(e)) } finally { setRefreshing(false) }
  }
  const submit = async () => {
    if (!folder) return
    setSubmitting(true); setSubmitRes(null)
    try {
      const r = (await invSubmitFolder(folder.id)) || {}
      if (r.ok === false) { setSubmitRes({ msg: r.msg || '提交不了', blockers: r.blockers || [] }); return }
      flash('已提交，等会计审核', 'ok')
      setLastSubmitted(r.folder || folder)
      await loadDesk()
    } catch (e) { setSubmitRes({ msg: '提交没成功：' + (e.message || e), blockers: [] }) } finally { setSubmitting(false) }
  }
  const onManualDone = async (f) => {
    setManualOpen(false)
    // 后端建完一般已设为当前票夹；再开一次兜底（幂等）
    if (f && f.id) { try { await invOpenFolder(f.id) } catch { /* 下面刷新会看到结果 */ } }
    setLastSubmitted(null)
    flash('手工票夹已建好', 'ok')
    await loadDesk()
  }
  const onAuto = v => { setAuto(v); saveAuto(v) }

  const header = (
    <div className="head">
      <div><div className="h-title">收票工作台</div><div className="h-sub">扫审批单 → 放发票 → 提交</div></div>
      <div className="inv-dk-actions">
        {pair && pair.bound && <span className={'inv-badge ' + (pair.active === false ? 'mute' : 'ok')}
          title={[pair.device, pair.active === false && pair.lastSeen ? '手机最近一次在线 ' + fmtTime(pair.lastSeen) : ''].filter(Boolean).join(' · ')}>
          手机已连上{pair.dtName ? '：' + pair.dtName : ''}{pair.active === false ? '（没在用）' : ''}</span>}
        {can.intake && <button type="button" className="btn" onClick={() => setPairOpen(true)}>手机当相机</button>}
        {can.intake && <button type="button" className="btn" onClick={() => setManualOpen(true)}>手工票夹</button>}
        {can.config && <button type="button" className="btn" onClick={() => setSetOpen(true)}>设置</button>}
      </div>
    </div>
  )

  if (!cfg && !cfgErr) return <div>{header}<div className="body"><div className="loading"><span className="inv-spin" />&nbsp;正在打开收票工作台…</div></div></div>
  if (cfg && can.desk === false) {
    return <div>{header}<div className="body"><div className="banner err">你还没有「收票工作台」的权限，请找管理员在账号管理里开通。</div></div></div>
  }

  return (
    <div className="inv-dk">
      {header}
      <div className="body">
        {cfgErr && <div className="banner err">权限和设置没读到：{cfgErr}<button type="button" className="btn" onClick={loadCfg}>重试</button></div>}
        {cfg && !can.intake && <div className="banner info">你现在只能看，不能收票：在左边「我的票夹」里点开票夹可以查看。拍照、登记、提交需要管理员给你开「收票」权限。</div>}
        {cfg && cfg.dingtalk === false && <div className="banner info">系统还没接通钉钉：扫审批单开不了票夹，可以先用「手工票夹」收票。</div>}
        <ScanStrip onScan={onScan} disabled={!can.intake} busy={scanBusy} res={scanRes} pair={pair} />
        {deskErr && <div className="banner err">票夹没刷新出来：{deskErr}<button type="button" className="btn" onClick={loadDesk}>重试</button></div>}
        <div className="inv-dk-cols">
          <div className="inv-dk-left">
            {can.intake && <CameraPanel auto={auto} onAutoChange={onAuto} disabled={!canEdit} height={270}
              onCapture={blob => upload([blob], 'camera')} />}
            <RecentFolders rows={desk && desk.recent} currentId={fid} onOpen={openFolder} busyId={openBusy} err={openErr} />
          </div>
          <div className="inv-dk-right">
            {!desk
              ? <div className="loading"><span className="inv-spin" />&nbsp;正在读取当前票夹…</div>
              : !folder
                ? <EmptyFolder canEdit={!!can.intake} onManual={() => setManualOpen(true)} lastSubmitted={lastSubmitted} onReopen={openFolder} />
                : <FileDrop className="inv-dk-drop" dropOnly disabled={!canEdit} onFiles={fs => upload(fs, 'upload')}>
                  {/* 整块票夹包在拖放区里：dropOnly＝只接拖放，不当按钮（不然只读时里面的按钮全被读成"不可用"）；点击/回车照旧不往外冒 */}
                  <div className="inv-dk-folder" onClick={e => e.stopPropagation()}
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation() }}>
                    <FolderHead folder={folder} canEdit={canEdit} viewOnly={!can.intake} onRefresh={refresh} refreshing={refreshing} refreshErr={refreshErr}
                      onClose={closeFolder} closing={closing} />
                    <ItemList items={items} canEdit={canEdit} onOpen={setDetailId} onFiles={fs => upload(fs, 'upload')}
                      upBusy={upBusy > 0} receipt={receipt} onReceiptClose={() => { clearTimeout(receiptTimer.current); setReceipt(null) }} />
                    <FolderFoot folder={folder} items={items} canEdit={canEdit} onSubmit={submit} submitting={submitting}
                      submitRes={submitRes} onPickItem={setDetailId} />
                  </div>
                  <div className="inv-dk-dropmask" aria-hidden="true">松手，放进这个票夹</div>
                </FileDrop>}
          </div>
        </div>
      </div>
      {detailId !== null && <ItemModal items={items} id={detailId} canEdit={canEdit} onClose={() => setDetailId(null)}
        onPick={setDetailId} reload={loadDesk} flash={flash} />}
      {pairOpen && <PairModal pairState={pair} onClose={() => { setPairOpen(false); loadDesk() }} />}
      {manualOpen && <ManualModal companies={cfg && cfg.settings && cfg.settings.company} onClose={() => setManualOpen(false)} onDone={onManualDone} />}
      {setOpen && <Modal title="发票管家设置" width={1040} onClose={() => { setSetOpen(false); loadCfg() }}>
        <InvSettings user={user} />
      </Modal>}
      {alarm > 0 && <div key={alarm} className="inv-dk-alarm" aria-hidden="true" />}
      {toast}
    </div>
  )
}
