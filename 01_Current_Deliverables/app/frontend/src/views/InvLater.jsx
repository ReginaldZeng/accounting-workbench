// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票后补池页：列表筛选（我接收的/全部＋状态＋搜索）、财务代填新建后补单、收到（扫码/上传/先标记）、催一下、详情、修改、关闭、导出欠票清单、「设置」页签
// [Change Log] Date: 2026-09-25 | Author: Claude / c | Version: V2.621 | 页头加「业务自助登记入口」（网址＋二维码，申请人自己登记发票后补）
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 新建后补单按后端 notified/notifyMsg 如实说接收人收没收到钉钉消息；
//   「收到」弹窗加高拍仪拍照（需求 v1.4 七「扫码枪或放高拍仪」），拍的照片走 receive-upload。
// 需求确认书 v1.4 七 + 技术方案 §5.2「发票后补池」。接口全走 api.js 的 invLater*；共用组件来自 invShared.jsx。
// 后端与本页同批开发：字段按 §5.1 Later 视图对象取，取不到时一律给默认值（可选链），不因缺字段白屏。
import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  invConfig, invLaterList, invLater, invLaterResolve, invLaterCreate, invLaterDocs, invLaterReceive,
  invLaterReceiveUpload, invLaterMark, invLaterRemind, invLaterUpdate, invLaterClose, invLaterExportUrl, invSLink,
} from '../api.js'
import { Modal, useToast, ScanInput, FileDrop, CameraPanel, ThumbStrip, InvViewer, StatusBadges, money, fmtTime } from './invShared.jsx'
import InvSettings from './InvSettings.jsx'
import './inv-later.css'

// ───────────────────────── 小工具 ─────────────────────────

const KIND_LABEL = { special: '专票', normal: '普票', receipt: '收据' }
const STATUS_META = {
  open: { label: '待收', tone: 'info' },
  partial: { label: '部分到票', tone: 'warn' },
  done: { label: '已收齐', tone: 'ok' },
  closed: { label: '已关闭', tone: 'mute' },
}
const STATUS_CHIPS = [['open', '待收'], ['partial', '部分到票'], ['overdue', '超期'], ['done', '已收齐'], ['closed', '已关闭'], ['all', '全部']]
const PAGE_SIZE = 50
const UPLOAD_ACCEPT = '.pdf,.ofd,.xml,.zip,image/*'
const TAX_RATES = ['13%', '9%', '6%', '5%', '3%', '1%', '0%', '免税', '不征税']
const isLive = l => l && (l.status === 'open' || l.status === 'partial')   // 还在等票的单子才能收/催/关

const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
// 还差多少：后端给了 remaining 就用；没给按「预计到票金额 − 已到 − 已收未登记」自己算，不出负数
const remainingOf = l => (l && l.remaining !== undefined && l.remaining !== null)
  ? num(l.remaining) : Math.max(0, num(l?.expectAmount ?? l?.payAmount) - num(l?.receivedAmount) - num(l?.unregisteredAmount))

// 银行账号只露头尾四位：列表会被截图转发，全号只在详情里给
function maskAcct(a) {
  const s = String(a || '').replace(/\s+/g, '')
  if (!s) return ''
  return s.length > 8 ? s.slice(0, 4) + ' **** ' + s.slice(-4) : s
}
// 事由等字段后端存的是长文本或 JSON：字符串原样，数组/对象拼成一行，免得出现 [object Object]
function txt(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return v.map(txt).filter(Boolean).join('；')
  if (typeof v === 'object') return Object.entries(v).map(([k, x]) => k + '：' + txt(x)).join('；')
  return String(v)
}
const errText = e => (e && (e.message || String(e))) || '未知原因'
// 后端在测试环境（INV_DRY_SEND）不真发，回 {sent:false, msg:'dry-run'}——这不算失败，别报红吓人
const DRY = 'dry-run'

/**
 * 新建后补单后，接收人到底收没收到钉钉消息（后端 notified/notifyMsg；老写法 notify{sent,msg} 也认）。
 * @param {object} r invLaterCreate 的返回
 * @returns {{ok: boolean, text: string}} ok=false 时要让人看见（电话/当面告诉接收人）
 */
export function createNotice(r) {
  const n = (r && r.notify) || {}
  const sent = r && typeof r.notified === 'boolean' ? r.notified : !!n.sent
  const why = String((r && r.notifyMsg) || n.msg || '')
  if (sent) return { ok: true, text: '后补单已登记，钉钉消息已发给接收人' }
  if (why === DRY) return { ok: true, text: '后补单已登记（测试环境：钉钉消息没有真发）' }
  return { ok: false, text: '后补单已登记，但接收人没收到钉钉消息' + (why ? '：' + why : '') + '。请电话或当面告诉接收人去后补池看这张单。' }
}

// 读 #/invlater?id=12 的 id（钉钉通知里的链接）
function hashId() {
  const m = (window.location.hash || '').match(/^#\/invlater\?(.*)$/)
  if (!m) return null
  const id = new URLSearchParams(m[1]).get('id')
  return id && /^\d+$/.test(id) ? Number(id) : null
}
const lsGet = (k, d) => { try { return localStorage.getItem(k) || d } catch { return d } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* 隐私模式存不了就算了 */ } }

// ───────────────────────── 行内小件 ─────────────────────────

function Tag({ tone = 'mute', children, title }) {
  return <span className={'inv-badge ' + tone} title={title}>{children}</span>
}

/** 状态徽标：主状态 + 超期 + 已收未登记 + 销方不符 */
function LaterBadges({ l }) {
  const st = STATUS_META[l?.status] || { label: l?.status || '—', tone: 'mute' }
  const sm = l?.sellerMismatch
  const smTip = sm && typeof sm === 'object'
    ? `发票销方「${sm.seller || '?'}」和付款单收款方「${sm.payee || '?'}」对不上——钱付给了一家，票是另一家开的，请核实`
    : '有发票的销方和付款单收款方对不上——钱付给了一家，票是另一家开的，请核实'
  return <span className="inv-badges">
    <Tag tone={st.tone}>{st.label}</Tag>
    {l?.overdue && isLive(l) && <Tag tone="err">超期</Tag>}
    {num(l?.unregisteredAmount) > 0 && <Tag tone="warn" title="点过「先标记收到」，发票号码还没扫进来">已收未登记 {money(l.unregisteredAmount)}</Tag>}
    {sm && <Tag tone="err" title={smTip}>销方不符</Tag>}
  </span>
}

/** 预计到票日：超期标红写「超期N天」；3 天内到期标琥珀 */
function DueCell({ l }) {
  if (!l?.expectDate) return <span className="inv-muted">未填</span>
  const d = l.daysLeft
  const live = isLive(l)
  if (live && (l.overdue || (typeof d === 'number' && d < 0))) {
    return <span className="inv-lt-due err">{l.expectDate}<em>超期{typeof d === 'number' ? Math.abs(d) : ''}天</em></span>
  }
  if (live && typeof d === 'number' && d <= 3) {
    return <span className="inv-lt-due warn">{l.expectDate}<em>{d === 0 ? '今天到期' : `还剩${d}天`}</em></span>
  }
  return <span className="inv-lt-due">{l.expectDate}</span>
}

// 用 div 不用 label：label 会把点击转给里面第一个可聚焦控件（文件框/分段按钮），点标题就误弹选文件
function Field({ label, children, wide, err, hint }) {
  return <div className={'inv-lt-f' + (wide ? ' wide' : '')}>
    <span className="inv-lt-fl">{label}</span>
    {children}
    {hint && !err && <span className="inv-lt-fh">{hint}</span>}
    {err && <span className="inv-lt-fe">{err}</span>}
  </div>
}

function ReceiverSelect({ receivers, value, onChange }) {
  const list = receivers || []
  const known = list.some(r => r.account === value)
  return <select className="inv-in" value={value || ''} onChange={e => onChange(e.target.value)}>
    <option value="">请选择财务接收人</option>
    {list.map(r => <option key={r.account} value={r.account}>
      {r.dtName && r.dtName !== r.account ? `${r.dtName}（${r.account}）` : r.account}
    </option>)}
    {value && !known && <option value={value}>{value}（已不在接收人名单）</option>}
  </select>
}

// ───────────────────────── 收到（扫码 / 上传 / 先标记） ─────────────────────────

const _BAD_ACTIONS = ['dup', 'error', 'fail', 'failed', 'unknown', 'reject', 'rejected', 'notInvoice']
const CAM_KEY = 'inv_later_cam'
// 高拍仪拍下的 Blob 没有文件名：包成 jpg 文件再走 receive-upload（和拖进来的照片一样处理）
const camFile = (b) => {
  try { return new File([b], 'camera.jpg', { type: 'image/jpeg' }) } catch { return b }
}

function ReceiveModal({ later, onClose, onChanged }) {
  const [l, setL] = useState(later)
  const [busy, setBusy] = useState('')
  const [log, setLog] = useState([])          // 本次弹窗里的回执，最新在上
  const [amt, setAmt] = useState(() => { const r = remainingOf(later); return r > 0 ? String(r) : '' })
  const [note, setNote] = useState('')
  const [markErr, setMarkErr] = useState('')
  // 高拍仪：点了才开摄像头（免得每次点「收到」都弹摄像头授权）；用过的人下次自动打开
  const [cam, setCam] = useState(() => lsGet(CAM_KEY, '') === '1')
  const isReceipt = l?.invKind === 'receipt'

  const amtTouched = useRef(false)             // 人改过金额就不再自动跟着「还差」变
  const push = (rows) => setLog(prev => [...rows, ...prev].slice(0, 30))
  const took = (nl) => {
    if (!nl) return
    setL(nl); onChanged?.(nl)
    if (!amtTouched.current) { const r = remainingOf(nl); setAmt(r > 0 ? String(r) : '') }
  }

  const onScan = async (code) => {
    if (busy) return
    setBusy('scan')
    try {
      const r = await invLaterReceive(l.id, code)
      const it = r?.item
      const bad = it?.flags?.dup
      push([{ tone: bad ? 'err' : 'ok', text: bad ? '这张票别处已经登记过，不能重复冲减' : `已收到：${it?.number ? '发票号 ' + it.number + '，' : ''}价税合计 ${money(it?.total)}`, item: it }])
      took(r?.later)
    } catch (e) {
      push([{ tone: 'err', text: '扫码没登记上：' + errText(e) }])
    } finally { setBusy('') }
  }
  const onFiles = async (files) => {
    if (busy || !files?.length) return
    setBusy('upload')
    try {
      const r = await invLaterReceiveUpload(l.id, files)
      const rows = (r?.results || []).map(x => {
        const bad = _BAD_ACTIONS.includes(x?.action) || x?.item?.flags?.dup
        return { tone: bad ? 'err' : 'ok', text: `${x?.name || '文件'}：${x?.msg || (bad ? '没收进来' : '已收到')}`, item: x?.item }
      })
      push(rows.length ? rows : [{ tone: 'ok', text: `已上传 ${files.length} 个文件` }])
      took(r?.later)
    } catch (e) {
      const rs = e?.body?.results
      push(Array.isArray(rs) && rs.length
        ? rs.map(x => ({ tone: 'err', text: `${x?.name || '文件'}：${x?.msg || '没收进来'}` }))
        : [{ tone: 'err', text: '上传失败：' + errText(e) }])
    } finally { setBusy('') }
  }
  const onMark = async () => {
    const a = Number(String(amt).replace(/,/g, ''))
    if (!Number.isFinite(a) || a <= 0) { setMarkErr('请填收到的金额（大于 0）'); return }
    setMarkErr(''); setBusy('mark')
    try {
      const r = await invLaterMark(l.id, { amount: a, note: note.trim() })
      push([{ tone: 'warn', text: `已标记收到 ${money(a)}（号码还没登记，之后扫码或上传补上）` }])
      setNote('')
      amtTouched.current = false
      took(r?.later)
    } catch (e) { setMarkErr('没标记上：' + errText(e)) } finally { setBusy('') }
  }

  const toggleCam = (v) => { setCam(v); lsSet(CAM_KEY, v ? '1' : '0') }

  const rest = remainingOf(l)
  return <Modal title={`收到发票 · ${l?.payee?.name || '后补单 #' + l?.id}`} onClose={onClose} width={760}
    footer={<button type="button" className="btn" onClick={onClose}>完成</button>}>
    <div className="inv-lt-sum">
      <div><span>预计到票</span><b className="inv-num">{money(l?.expectAmount ?? l?.payAmount)}</b></div>
      <div><span>已到</span><b className="inv-num">{money(l?.receivedAmount)}</b></div>
      {num(l?.unregisteredAmount) > 0 && <div className="warn"><span>已收未登记</span><b className="inv-num">{money(l.unregisteredAmount)}</b></div>}
      <div className={rest > 0 ? 'hot' : 'ok'}><span>还差</span><b className="inv-num">{money(rest)}</b></div>
      <div><span>状态</span><LaterBadges l={l} /></div>
    </div>
    {!isLive(l) && <div className="inv-lt-note ok">这张后补单{STATUS_META[l?.status]?.label || '已结束'}，不用再收了。</div>}

    <div className="inv-lt-rcv">
      <section>
        <h4>① 扫发票上的二维码</h4>
        {isReceipt
          ? <div className="inv-lt-note">收据没有二维码和号码：拍照上传，或用下面的「先标记收到」按金额冲减。</div>
          : <ScanInput onScan={onScan} disabled={!!busy || !isLive(l)} placeholder="用扫码枪扫发票左上角的二维码" />}
        {busy === 'scan' && <div className="inv-muted"><span className="inv-spin" /> 正在登记…</div>}
      </section>
      <section>
        <h4>② 电子票或照片</h4>
        <FileDrop onFiles={onFiles} accept={UPLOAD_ACCEPT} disabled={!!busy || !isLive(l)}>
          {busy === 'upload'
            ? <span className="inv-drop-tx"><span className="inv-spin" /> 正在上传并读取…</span>
            : <span className="inv-drop-tx">把 PDF / OFD / XML / 图片 / 压缩包拖到这里，或<b>点击选择</b></span>}
        </FileDrop>
        <div className="inv-lt-camrow">
          <span className="inv-muted">纸质票也可以放到高拍仪下拍：</span>
          <button type="button" className="btn" onClick={() => toggleCam(!cam)}>{cam ? '收起高拍仪' : '用高拍仪拍'}</button>
        </div>
        {cam && <CameraPanel height={240} disabled={!!busy || !isLive(l)}
          disabledHint={busy ? '正在处理上一张，稍等再拍' : '这张后补单不用再收了'}
          onCapture={b => onFiles([camFile(b)])} />}
      </section>
      <section>
        <h4>③ 先标记收到（号码稍后补）</h4>
        <div className="inv-lt-mark">
          <input className="inv-in inv-num" inputMode="decimal" value={amt} placeholder="收到的金额"
            onChange={e => { amtTouched.current = true; setAmt(e.target.value) }} disabled={!!busy || !isLive(l)} />
          <input className="inv-in" value={note} placeholder="备注（选填，如：纸质票在抽屉，下周补扫）"
            onChange={e => setNote(e.target.value)} disabled={!!busy || !isLive(l)} />
          <button type="button" className="btn" onClick={onMark} disabled={!!busy || !isLive(l)}>
            {busy === 'mark' ? '标记中…' : '先标记收到'}
          </button>
        </div>
        {markErr && <div className="inv-lt-fe">{markErr}</div>}
        <div className="inv-muted">实在来不及扫时用：这笔会标黄「已收到、号码未登记」，直到补扫为止。</div>
      </section>
    </div>

    {log.length > 0 && <div className="inv-lt-log">
      {log.map((x, i) => <div key={log.length - i} className={'inv-lt-logrow ' + x.tone}>
        <span>{x.text}</span>{x.item && <StatusBadges item={x.item} />}
      </div>)}
    </div>}
  </Modal>
}

// ───────────────────────── 详情 ─────────────────────────

function KV({ k, children }) {
  return <div className="inv-lt-kv"><span>{k}</span><div>{children || <span className="inv-muted">—</span>}</div></div>
}

function logDetail(d) {
  if (d === null || d === undefined || d === '') return ''
  if (typeof d === 'string') return d
  if (typeof d === 'object' && d.msg) return String(d.msg)
  return txt(d)
}

function DetailModal({ id, can, onClose, onReceive, onRemind, onEdit, onCloseLater, rev, flash }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [act, setAct] = useState(null)
  const [docErr, setDocErr] = useState('')
  const [docBusy, setDocBusy] = useState(false)
  const [remindRes, setRemindRes] = useState(null)
  const [reminding, setReminding] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await invLater(id)
      setData(r); setErr('')
      setAct(a => (r?.items || []).some(x => x.id === a) ? a : (r?.items?.[0]?.id ?? null))
    } catch (e) { setErr(errText(e)) }
  }, [id])
  useEffect(() => { load() }, [load, rev])

  const l = data?.later
  const items = data?.items || []
  const docs = data?.docs || []
  const logs = data?.logs || []
  const cur = items.find(x => x.id === act) || null
  const idx = items.findIndex(x => x.id === act)

  const upDocs = async (files) => {
    setDocBusy(true); setDocErr('')
    try { await invLaterDocs(id, files); flash?.('资料已上传', 'ok'); load() } catch (e) { setDocErr('资料没传上去：' + errText(e)) } finally { setDocBusy(false) }
  }
  const remind = async () => {
    setReminding(true); setRemindRes(null)
    try { const r = await onRemind(l); setRemindRes(r); load() } finally { setReminding(false) }
  }

  const live = isLive(l)
  const footer = <>
    {remindRes && <span className={'inv-lt-inline ' + (remindRes.ok ? 'ok' : 'err')}>{remindRes.text}</span>}
    {l && can.receive && live && <button type="button" className="btn" disabled={reminding} onClick={remind}>{reminding ? '发送中…' : '催一下'}</button>}
    {l && can.receive && l.status !== 'closed' && <button type="button" className="btn" onClick={() => onEdit(l)}>修改</button>}
    {l && can.receive && live && <button type="button" className="btn" onClick={() => onCloseLater(l)}>关闭此单</button>}
    {l && can.receive && live && <button type="button" className="btn-pri" onClick={() => onReceive(l)}>收到</button>}
    <button type="button" className="btn" onClick={onClose}>关闭窗口</button>
  </>

  return <Modal title={l ? `后补单 #${l.id} · ${l.payee?.name || l.applicant || ''}` : `后补单 #${id}`} onClose={onClose} width={1040} footer={footer}>
    {err && <div className="banner err">没打开这张后补单：{err}</div>}
    {!data && !err && <div className="loading">正在打开后补单…</div>}
    {l && <div className="inv-lt-detail">
      <div className="inv-lt-dhead"><LaterBadges l={l} /><DueCell l={l} /></div>
      <div className="inv-lt-kvs">
        <KV k="收款方">{l.payee?.name}</KV>
        <KV k="开户行">{l.payee?.bank}</KV>
        <KV k="银行账号"><span className="inv-num">{l.payee?.account}</span></KV>
        <KV k="付款金额"><span className="inv-num">{money(l.payAmount)}</span></KV>
        <KV k="发票种类">{KIND_LABEL[l.invKind] || l.invKind}</KV>
        <KV k="税率">{l.taxRate}</KV>
        <KV k="预计到票日">{l.expectDate}</KV>
        <KV k="预计到票金额"><span className="inv-num">{money(l.expectAmount)}</span></KV>
        <KV k="已到"><span className="inv-num">{money(l.receivedAmount)}</span></KV>
        <KV k="已收未登记">{num(l.unregisteredAmount) ? <span className="inv-num inv-lt-amber">{money(l.unregisteredAmount)}</span> : null}</KV>
        <KV k="还差"><b className="inv-num">{money(remainingOf(l))}</b></KV>
        <KV k="财务接收人">{l.receiverName || l.receiver}</KV>
        <KV k="申请人">{[l.applicant, l.dept].filter(Boolean).join(' · ')}</KV>
        <KV k="公司主体">{l.company}</KV>
        <KV k="审批模板">{l.template}</KV>
        <KV k="审批编号"><span className="inv-num">{l.businessId}</span></KV>
        <KV k="ERP 单号"><span className="inv-num">{l.erpNo}</span></KV>
        <KV k="登记人">{l.filedBy ? `${l.filedBy}（${l.filedVia === 'self' ? '业务自助' : '财务代填'}）` : ''}</KV>
        <KV k="登记时间">{fmtTime(l.createdAt)}</KV>
        <KV k="上次催票">{l.lastRemindAt ? `${fmtTime(l.lastRemindAt)}（共 ${l.remindCount || 0} 次）` : '还没催过'}</KV>
        <KV k="事由" >{txt(l.reason)}</KV>
        <KV k="备注">{l.note}</KV>
      </div>

      <h4 className="inv-lt-h">收到的票（{items.length}）</h4>
      {items.length
        ? <div className="inv-lt-imgs">
          <ThumbStrip items={items} activeId={act} onPick={it => setAct(it.id)} />
          {cur && <InvViewer key={cur.id} item={cur} height={420}
            onPrev={idx > 0 ? () => setAct(items[idx - 1].id) : undefined}
            onNext={idx >= 0 && idx < items.length - 1 ? () => setAct(items[idx + 1].id) : undefined} />}
          {cur && <div className="inv-lt-cur">
            <span>{cur.typeLabel || KIND_LABEL[cur.invType] || '票据'}</span>
            {cur.number && <span className="inv-num">号码 {cur.number}</span>}
            {cur.date && <span>{cur.date}</span>}
            {cur.sellerName && <span>销方 {cur.sellerName}</span>}
            <b className="inv-num">{money(cur.total)}</b>
            <StatusBadges item={cur} />
          </div>}
        </div>
        : <div className="inv-muted">还没有收到票。</div>}

      <h4 className="inv-lt-h">资料（{docs.length}）</h4>
      {docs.length > 0 && <ul className="inv-lt-docs">
        {docs.map((d, i) => {
          const href = d?.url || d?.orig || (d?.id ? `/api/inv/file/${d.id}?v=o` : '')
          return <li key={d?.id || i}>
            {href ? <a className="inv-lk" href={href} target="_blank" rel="noreferrer">{d?.name || '资料 ' + (i + 1)}</a> : <span>{d?.name || '资料'}</span>}
            {d?.createdAt && <span className="inv-muted">{fmtTime(d.createdAt)}{d.createdBy ? ' · ' + d.createdBy : ''}</span>}
          </li>
        })}
      </ul>}
      {!docs.length && <div className="inv-muted">没有上传资料。</div>}
      {can.receive && <FileDrop onFiles={upDocs} disabled={docBusy} className="inv-lt-docdrop">
        <span className="inv-drop-tx">{docBusy ? <><span className="inv-spin" /> 正在上传…</> : <>补传资料（合同、对账单等）：拖到这里，或<b>点击选择</b></>}</span>
      </FileDrop>}
      {docErr && <div className="inv-lt-fe">{docErr}</div>}

      <h4 className="inv-lt-h">经办记录</h4>
      {logs.length
        ? <div className="inv-lt-logs">{logs.map((g, i) => <div key={i} className="inv-lt-logline">
          <span className="inv-num">{fmtTime(g.ts)}</span><b>{g.user || '系统'}</b><span>{g.action}</span>
          <span className="inv-muted">{logDetail(g.detail)}</span>
        </div>)}</div>
        : <div className="inv-muted">暂无记录。</div>}
    </div>}
  </Modal>
}

// ───────────────────────── 修改 / 关闭 ─────────────────────────

function EditModal({ later, receivers, onClose, onSaved }) {
  const [f, setF] = useState({
    expectDate: later?.expectDate || '',
    expectAmount: later?.expectAmount ?? '',
    receiver: later?.receiver || '',
    note: later?.note || '',
  })
  const [err, setErr] = useState({})
  const [busy, setBusy] = useState(false)
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))
  const save = async () => {
    const e = {}
    const amt = Number(String(f.expectAmount).replace(/,/g, ''))
    if (!f.expectDate) e.expectDate = '请填预计到票日期'
    if (!Number.isFinite(amt) || amt <= 0) e.expectAmount = '请填预计到票金额（大于 0）'
    if (!f.receiver) e.receiver = '请选财务接收人'
    setErr(e)
    if (Object.keys(e).length) return
    setBusy(true)
    try {
      const r = await invLaterUpdate(later.id, { expectDate: f.expectDate, expectAmount: amt, receiver: f.receiver, note: f.note.trim() })
      onSaved?.(r?.later)
    } catch (x) { setErr({ _: '没保存上：' + errText(x) }) } finally { setBusy(false) }
  }
  return <Modal title={`修改后补单 #${later?.id}`} onClose={onClose} width={560}
    footer={<>
      {err._ && <span className="inv-lt-inline err">{err._}</span>}
      <button type="button" className="btn" onClick={onClose}>取消</button>
      <button type="button" className="btn-pri" onClick={save} disabled={busy}>{busy ? '保存中…' : '保存'}</button>
    </>}>
    <div className="inv-lt-form">
      <Field label="预计到票日期" err={err.expectDate}><input type="date" className="inv-in" value={f.expectDate} onChange={e => set('expectDate', e.target.value)} /></Field>
      <Field label="预计到票金额" err={err.expectAmount}><input className="inv-in inv-num" inputMode="decimal" value={f.expectAmount} onChange={e => set('expectAmount', e.target.value)} /></Field>
      <Field label="财务接收人" wide err={err.receiver}><ReceiverSelect receivers={receivers} value={f.receiver} onChange={v => set('receiver', v)} /></Field>
      <Field label="备注" wide><textarea className="inv-in inv-lt-ta" value={f.note} onChange={e => set('note', e.target.value)} /></Field>
    </div>
  </Modal>
}

function CloseModal({ later, onClose, onSaved }) {
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const go = async () => {
    if (!note.trim()) { setErr('请写关闭原因（例如：供应商退款、改走别的单据），以后查账要看'); return }
    setBusy(true); setErr('')
    try { const r = await invLaterClose(later.id, note.trim()); onSaved?.(r?.later) } catch (e) { setErr('没关闭：' + errText(e)) } finally { setBusy(false) }
  }
  return <Modal title={`关闭后补单 #${later?.id}`} onClose={onClose} width={520}
    footer={<>
      <button type="button" className="btn" onClick={onClose}>取消</button>
      <button type="button" className="btn-pri" onClick={go} disabled={busy}>{busy ? '关闭中…' : '确定关闭'}</button>
    </>}>
    <div className="inv-lt-note">关闭后这张单不再催票、不进欠票清单。还差 <b className="inv-num">{money(remainingOf(later))}</b> 的票就不再追了。</div>
    <Field label="关闭原因" wide err={err}>
      <textarea className="inv-in inv-lt-ta" autoFocus value={note} onChange={e => setNote(e.target.value)} placeholder="例如：供应商已退款，不再开票" />
    </Field>
  </Modal>
}

// ───────────────────────── 新建后补单（财务代填） ─────────────────────────

function PrefillCard({ p, onOpenExisting }) {
  return <div className="inv-lt-pre">
    <div className="inv-lt-kvs">
      <KV k="审批单">{[p.template, p.businessId].filter(Boolean).join(' · ')}</KV>
      <KV k="申请人">{[p.applicant, p.dept].filter(Boolean).join(' · ')}</KV>
      <KV k="公司主体">{p.company}</KV>
      <KV k="收款方">{p.payee?.name}</KV>
      <KV k="开户行">{p.payee?.bank}</KV>
      <KV k="银行账号"><span className="inv-num">{p.payee?.account}</span></KV>
      <KV k="付款金额"><b className="inv-num">{money(p.amount)}</b></KV>
      <KV k="ERP 单号"><span className="inv-num">{p.erpNo}</span></KV>
      <KV k="事由">{txt(p.reason)}</KV>
    </div>
    {p.existingLaterId && <div className="inv-lt-note warn">
      这张付款单已经登记过后补单（#{p.existingLaterId}），
      <button type="button" className="inv-lt-lkbtn" onClick={() => onOpenExisting(p.existingLaterId)}>点这里查看</button>。
      一笔款分几次到票不用重复登记，在原单上点「收到」就行。
    </div>}
    {p.hasInvoice && <div className="inv-lt-note warn">这张付款单已经附了发票，确定还要登记后补吗？</div>}
  </div>
}

function CreateModal({ receivers, me, canConfig, onClose, onCreated, onOpenExisting, onGoSettings }) {
  const [pre, setPre] = useState(null)
  const [resolving, setResolving] = useState(false)
  const [resErr, setResErr] = useState('')
  const [f, setF] = useState({ invKind: 'special', taxRate: '', expectDate: '', expectAmount: '', receiver: '', note: '' })
  const [files, setFiles] = useState([])
  const [ack, setAck] = useState(false)
  const [err, setErr] = useState({})
  const [busy, setBusy] = useState(false)
  // 改哪项就清掉哪项的错误提示；换发票种类连带清税率的（收据不用填税率）
  const set = (k, v) => { setF(p => ({ ...p, [k]: v })); setErr(p => ({ ...p, [k]: undefined, ...(k === 'invKind' ? { taxRate: undefined } : {}) })) }

  const resolve = async (code) => {
    if (resolving) return
    setResolving(true); setResErr('')
    try {
      const r = await invLaterResolve(code)
      const p = r?.prefill
      if (!p) throw new Error('没读到付款单内容')
      setPre(p); setAck(false); setErr({})
      // 默认接收人：登记的人自己在接收人名单里就选自己；名单只有一个人就选他
      const mine = (receivers || []).find(x => x.account === me)
      setF(prev => ({
        ...prev,
        expectAmount: p.amount !== undefined && p.amount !== null ? String(p.amount) : prev.expectAmount,
        receiver: prev.receiver || (mine ? mine.account : ((receivers || []).length === 1 ? receivers[0].account : '')),
      }))
    } catch (e) { setResErr(errText(e)) } finally { setResolving(false) }
  }

  const submit = async () => {
    const e = {}
    const amt = Number(String(f.expectAmount).replace(/,/g, ''))
    if (f.invKind !== 'receipt' && !f.taxRate) e.taxRate = '请选税率'
    if (!f.expectDate) e.expectDate = '请填预计到票日期'
    if (!Number.isFinite(amt) || amt <= 0) e.expectAmount = '请填预计到票金额（大于 0）'
    if (!f.receiver) e.receiver = '请选财务接收人（他会收到钉钉消息）'
    if (pre?.hasInvoice && !ack) e.ack = '这张单已经附了发票，确认要登记请先勾选'
    setErr(e)
    if (Object.keys(e).length) return
    setBusy(true)
    try {
      const r = await invLaterCreate({
        instId: pre.instId, invKind: f.invKind, taxRate: f.invKind === 'receipt' ? '' : f.taxRate, expectDate: f.expectDate,
        expectAmount: amt, receiver: f.receiver, note: f.note.trim(),
      })
      const nl = r?.later
      let docMsg = ''
      if (nl?.id && files.length) {
        try { await invLaterDocs(nl.id, files) } catch (x) { docMsg = '后补单已登记，但资料没传上去：' + errText(x) + '。可在详情里重新上传。' }
      }
      onCreated?.(nl, docMsg, r)
    } catch (x) { setErr({ _: '没登记上：' + errText(x) }) } finally { setBusy(false) }
  }

  const noRecv = !(receivers || []).length
  return <Modal title="新建后补单（财务代填）" onClose={onClose} width={820}
    footer={pre ? <>
      {err._ && <span className="inv-lt-inline err">{err._}</span>}
      <button type="button" className="btn" onClick={onClose}>取消</button>
      <button type="button" className="btn-pri" onClick={submit} disabled={busy}>{busy ? '登记中…' : '登记后补单'}</button>
    </> : <button type="button" className="btn" onClick={onClose}>取消</button>}>
    <div className="inv-lt-step"><span>1</span>找到付款单</div>
    <ScanInput onScan={resolve} disabled={resolving} placeholder="扫付款单二维码或输入审批编号，按回车" />
    {resolving && <div className="inv-muted"><span className="inv-spin" /> 正在从钉钉取付款单…</div>}
    {resErr && <div className="inv-lt-fe">没取到付款单：{resErr}</div>}
    {!pre && !resolving && !resErr && <div className="inv-muted inv-lt-gap">收款方、金额、事由、ERP 单号、申请人会自动带出来。</div>}
    {pre && <PrefillCard p={pre} onOpenExisting={onOpenExisting} />}

    {pre && <>
      <div className="inv-lt-step"><span>2</span>填后补信息</div>
      <div className="inv-lt-form">
        <Field label="发票种类">
          <div className="inv-lt-seg">
            {Object.entries(KIND_LABEL).map(([k, v]) => <button type="button" key={k} className={f.invKind === k ? 'on' : ''} onClick={() => set('invKind', k)}>{v}</button>)}
          </div>
        </Field>
        <Field label="税率" err={err.taxRate}>
          {f.invKind === 'receipt'
            ? <input className="inv-in" disabled value="收据没有税率" />
            : <select className="inv-in" value={f.taxRate} onChange={e => set('taxRate', e.target.value)}>
              <option value="">请选择</option>
              {TAX_RATES.map(x => <option key={x} value={x}>{x}</option>)}
            </select>}
        </Field>
        <Field label="预计到票日期" err={err.expectDate}>
          <input type="date" className="inv-in" value={f.expectDate} onChange={e => set('expectDate', e.target.value)} />
        </Field>
        <Field label="预计到票金额" err={err.expectAmount} hint="默认等于付款金额，分批到票可改小">
          <input className="inv-in inv-num" inputMode="decimal" value={f.expectAmount} onChange={e => set('expectAmount', e.target.value)} />
        </Field>
        <Field label="财务接收人" wide err={err.receiver}>
          <ReceiverSelect receivers={receivers} value={f.receiver} onChange={v => set('receiver', v)} />
          {noRecv && <span className="inv-lt-fh">还没有设置财务接收人。{canConfig
            ? <button type="button" className="inv-lt-lkbtn" onClick={onGoSettings}>去「设置 → 财务人员名单」勾选接收人</button>
            : '请管理员在「设置 → 财务人员名单」里勾选接收人。'}</span>}
        </Field>
        <Field label="备注" wide><textarea className="inv-in inv-lt-ta" value={f.note} onChange={e => set('note', e.target.value)} placeholder="选填" /></Field>
        <Field label="资料（选填）" wide>
          <FileDrop onFiles={fs => setFiles(prev => [...prev, ...fs])} disabled={busy}>
            <span className="inv-drop-tx">合同、对账单、供应商承诺函等：拖到这里，或<b>点击选择</b></span>
          </FileDrop>
          {files.length > 0 && <div className="inv-lt-files">
            {files.map((x, i) => <span key={i} className="inv-lt-file">{x.name}
              <button type="button" className="inv-lt-x" title="移除" onClick={() => setFiles(fs => fs.filter((_, j) => j !== i))}>×</button></span>)}
          </div>}
        </Field>
        {pre.hasInvoice && <label className="inv-lt-ack wide">
          <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} /> 我确认：虽然付款单附了发票，仍要登记后补
          {err.ack && <span className="inv-lt-fe">{err.ack}</span>}
        </label>}
      </div>
    </>}
  </Modal>
}

// ───────────────────────── 列表 ─────────────────────────

function LaterRow({ l, can, onDetail, onReceive, onRemind, onEdit, onClose, remind }) {
  const live = isLive(l)
  const rest = remainingOf(l)
  const hot = live && (l.overdue || (typeof l.daysLeft === 'number' && l.daysLeft < 0))
  return <tr className={hot ? 'inv-lt-hot' : ''}>
    <td>
      <button type="button" className="inv-lt-name" onClick={() => onDetail(l.id)} title="看详情">{l.payee?.name || '（未带出收款方）'}</button>
      <div className="sub inv-num">{maskAcct(l.payee?.account) || ' '}</div>
    </td>
    <td className="num">{money(l.payAmount)}
      {l.expectAmount !== undefined && l.expectAmount !== null && num(l.expectAmount) !== num(l.payAmount) && <div className="sub">预计 {money(l.expectAmount)}</div>}
    </td>
    <td><DueCell l={l} /></td>
    <td className="num">
      <div>已到 {money(l.receivedAmount)}</div>
      {num(l.unregisteredAmount) > 0 && <div className="sub inv-lt-amber">未登记 {money(l.unregisteredAmount)}</div>}
      <div className={'sub' + (rest > 0 && live ? ' inv-lt-rest' : '')}>还差 {money(rest)}</div>
    </td>
    <td className="inv-lt-nw">{KIND_LABEL[l.invKind] || l.invKind || '—'}{l.taxRate ? <div className="sub">{l.taxRate}</div> : null}</td>
    <td className="inv-lt-nw">{l.receiverName || l.receiver || '—'}</td>
    <td className="inv-lt-nw">{l.applicant || '—'}{l.dept ? <div className="sub">{l.dept}</div> : null}</td>
    <td><LaterBadges l={l} /></td>
    <td>
      <div className="inv-lt-acts">
        {can.receive && live && <button type="button" className="inv-lt-a pri" onClick={() => onReceive(l)}>收到</button>}
        {can.receive && live && <button type="button" className="inv-lt-a" disabled={remind?.busy} onClick={() => onRemind(l)}>{remind?.busy ? '发送中…' : '催一下'}</button>}
        <button type="button" className="inv-lt-a" onClick={() => onDetail(l.id)}>详情</button>
        {can.receive && l.status !== 'closed' && <button type="button" className="inv-lt-a" onClick={() => onEdit(l)}>修改</button>}
        {can.receive && live && <button type="button" className="inv-lt-a" onClick={() => onClose(l)}>关闭</button>}
      </div>
      {remind?.text && <div className={'inv-lt-inline ' + (remind.ok ? 'ok' : 'err')}>{remind.text}</div>}
    </td>
  </tr>
}

function emptyText(scope, status, q) {
  if (q) return `没有找到和「${q}」相关的后补单。`
  if (scope === 'mine') {
    return (status === 'all' ? '还没有你接收的后补单。' : '这个状态下没有你接收的后补单。')
      + '如果你是财务接收人却看不到，请管理员在「设置 → 财务人员名单」里把你的工作台账号和钉钉绑定并勾选接收人；也可以切到「全部」看。'
  }
  return status === 'all' ? '后补池还是空的。付款单没附发票时，点右上角「新建后补单」登记。' : '这个状态下没有后补单。'
}

function LaterPool({ cfg, cfgErr, can, me, flash, onGoSettings, creating, setCreating }) {
  const [scope, setScope] = useState(() => lsGet('inv_later_scope', 'mine'))
  const [status, setStatus] = useState(() => lsGet('inv_later_status', 'all'))
  const [qIn, setQIn] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [rev, setRev] = useState(0)
  const [detailId, setDetailId] = useState(null)
  const [detailRev, setDetailRev] = useState(0)
  const [recv, setRecv] = useState(null)
  const [edit, setEdit] = useState(null)
  const [closing, setClosing] = useState(null)
  const [reminds, setReminds] = useState({})
  const [notice, setNotice] = useState(null)     // 新建后补单后要人注意的事（接收人没收到钉钉消息等）
  const seq = useRef(0)
  const receivers = cfg?.receivers || []

  useEffect(() => { lsSet('inv_later_scope', scope) }, [scope])
  useEffect(() => { lsSet('inv_later_status', status) }, [status])
  // 搜索框停手 300ms 再查，免得每敲一个字打一次后端
  useEffect(() => { const t = setTimeout(() => { setQ(qIn.trim()); setPage(1) }, 300); return () => clearTimeout(t) }, [qIn])

  useEffect(() => {
    const my = ++seq.current   // 只认最后一次请求的结果，防止快速切筛选时旧结果盖新结果
    setLoading(true)
    invLaterList({ scope, status, q, page, size: PAGE_SIZE })
      .then(r => { if (my === seq.current) { setData(r || { total: 0, rows: [] }); setErr('') } })
      .catch(e => { if (my === seq.current) setErr(errText(e)) })
      .finally(() => { if (my === seq.current) setLoading(false) })
  }, [scope, status, q, page, rev])

  // 深链 #/invlater?id=12：打开一次详情，然后把 hash 收回，刷新页面不会再弹
  const deep = useRef(false)
  useEffect(() => {
    if (deep.current) return
    deep.current = true
    const id = hashId()
    if (id) setDetailId(id)
    if ((window.location.hash || '').startsWith('#/invlater?')) {
      try { window.history.replaceState(null, '', '#/invlater') } catch { /* 忽略 */ }
    }
  }, [])

  const refresh = () => { setRev(x => x + 1); setDetailRev(x => x + 1) }

  const remind = async (l) => {
    setReminds(p => ({ ...p, [l.id]: { busy: true } }))
    let res
    try {
      const r = await invLaterRemind(l.id)
      if (r?.msg === DRY) res = { ok: true, text: '测试环境：催票已记录，钉钉消息没有真发' }
      else if (r?.sent) res = { ok: true, text: '已催：' + (r?.msg || '钉钉消息已发给申请人，抄送接收人') }
      else res = { ok: false, text: '没发出去：' + (r?.msg || '原因未知') + '。可以电话提醒，或检查申请人的钉钉身份' }
    } catch (e) { res = { ok: false, text: '没发出去：' + errText(e) } }
    setReminds(p => ({ ...p, [l.id]: res }))
    flash(res.text, res.ok ? 'ok' : 'err')
    setRev(x => x + 1)
    return res
  }

  const rows = data?.rows || []
  const total = data?.total ?? rows.length
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return <>
    {cfgErr && <div className="banner err">没读到发票管家配置：{cfgErr}。按钮权限可能显示不全，刷新页面再试。</div>}
    {notice && <div className="inv-lt-note warn inv-lt-notice" role="alert">
      <span>{notice.id ? `后补单 #${notice.id}：` : ''}{notice.text}</span>
      <button type="button" className="inv-x" onClick={() => setNotice(null)} aria-label="知道了" title="知道了">×</button>
    </div>}
    <div className="inv-lt-bar">
      <div className="inv-lt-seg">
        <button type="button" className={scope === 'mine' ? 'on' : ''} onClick={() => { setScope('mine'); setPage(1) }}>我接收的</button>
        <button type="button" className={scope === 'all' ? 'on' : ''} onClick={() => { setScope('all'); setPage(1) }}>全部</button>
      </div>
      <div className="inv-lt-chips">
        {STATUS_CHIPS.map(([k, v]) => <button type="button" key={k}
          className={'inv-lt-chip' + (status === k ? ' on' : '') + (k === 'overdue' ? ' hot' : '')}
          onClick={() => { setStatus(k); setPage(1) }}>{v}</button>)}
      </div>
      <input className="inv-in inv-lt-q" value={qIn} onChange={e => setQIn(e.target.value)} placeholder="搜供应商、申请人、审批编号、ERP 单号" />
    </div>

    {err && <div className="banner err">后补单没读出来：{err}</div>}
    {!data && !err && <div className="loading">正在加载后补单…</div>}
    {data && !rows.length && !loading && <div className="inv-lt-empty">{emptyText(scope, status, q)}</div>}
    {rows.length > 0 && <div className={'tbl-wrap' + (loading ? ' inv-lt-dim' : '')}>
      <table className="inv-lt-tbl">
        <thead><tr>
          <th>收款方（供应商）</th><th className="num">付款金额</th><th>预计到票</th><th className="num">已到 / 还差</th>
          <th>票种</th><th>接收人</th><th>申请人</th><th>状态</th><th>操作</th>
        </tr></thead>
        <tbody>{rows.map(l => <LaterRow key={l.id} l={l} can={can} remind={reminds[l.id]}
          onDetail={setDetailId} onReceive={setRecv} onRemind={remind} onEdit={setEdit} onClose={setClosing} />)}</tbody>
      </table>
    </div>}
    {data && total > 0 && <div className="inv-lt-pager">
      <span className="inv-muted">共 {total} 张{loading ? ' · 刷新中…' : ''}</span>
      {pages > 1 && <>
        <button type="button" className="btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
        <span className="inv-muted">第 {page} / {pages} 页</span>
        <button type="button" className="btn" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>下一页</button>
      </>}
    </div>}

    {detailId && <DetailModal id={detailId} can={can} rev={detailRev} flash={flash} onClose={() => setDetailId(null)}
      onReceive={setRecv} onRemind={remind} onEdit={setEdit} onCloseLater={setClosing} />}
    {recv && <ReceiveModal later={recv} onClose={() => { setRecv(null); refresh() }} onChanged={() => setRev(x => x + 1)} />}
    {edit && <EditModal later={edit} receivers={receivers} onClose={() => setEdit(null)}
      onSaved={() => { setEdit(null); flash('已保存', 'ok'); refresh() }} />}
    {closing && <CloseModal later={closing} onClose={() => setClosing(null)}
      onSaved={() => { setClosing(null); flash('后补单已关闭', 'ok'); refresh() }} />}
    {creating && <CreateModal receivers={receivers} me={me} canConfig={!!can.config}
      onClose={() => setCreating(false)}
      onOpenExisting={id => { setCreating(false); setDetailId(id) }}
      onGoSettings={() => { setCreating(false); onGoSettings?.() }}
      onCreated={(nl, docMsg, r) => {
        setCreating(false)
        const n = createNotice(r)
        // 接收人没收到消息 / 资料没传上：常驻在列表上方，等人看见再关（toast 2 秒就没了）
        const warn = [n.ok ? '' : n.text, docMsg].filter(Boolean).join(' ')
        setNotice(warn ? { id: nl?.id, text: warn } : null)
        flash(warn || n.text, warn ? 'err' : 'ok')
        setRev(x => x + 1)
        if (nl?.id) setDetailId(nl.id)
      }} />}
  </>
}

// ───────────────────────── 业务自助登记入口（V2.621） ─────────────────────────

// 发票后补由申请人自己登记：给财务一个网址＋二维码，发到群里或贴出来；电脑浏览器、手机钉钉都能打开
function SelfLinkModal({ onClose }) {
  const [r, setR] = useState(null)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState(false)
  useEffect(() => { invSLink().then(setR).catch(e => setErr(errText(e))) }, [])
  const copy = () => {
    const t = r?.url || ''
    const ok = () => { setCopied(true); setTimeout(() => setCopied(false), 1600) }
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(t).then(ok).catch(() => {})
  }
  return <Modal title="业务同事自助登记发票后补" onClose={onClose} width={560}
    footer={<button type="button" className="btn" onClick={onClose}>关闭</button>}>
    {err && <div className="inv-lt-fe">没取到入口地址：{err}</div>}
    {!r && !err && <div className="inv-muted"><span className="inv-spin" /> 正在生成…</div>}
    {r && <div className="inv-lt-self">
      <p>付款时发票还没拿到，由<b>申请人自己</b>在这里登记：选自己发起的付款单/报销单 → 填预计到票 → 提交。
        财务审核这张单时，「发票审核」里会自动带出这张后补单。</p>
      <div className="inv-lt-self-url"><input className="inv-in inv-num" readOnly value={r.url} onFocus={e => e.target.select()} />
        <button type="button" className="btn" onClick={copy}>{copied ? '已复制' : '复制网址'}</button></div>
      <img className="inv-lt-self-qr" src={r.qr + '?t=' + Date.now()} alt="自助登记二维码" width={200} height={200} />
      <ul className="inv-muted">
        <li>手机钉钉扫这个码：自动认出是谁，直接登记。</li>
        <li>电脑浏览器打开网址：写钉钉姓名，钉钉会收到 6 位验证码，输入就能登录。</li>
        <li>只能登记自己发起的单子；登记后系统用钉钉通知所选的财务接收人。</li>
      </ul>
    </div>}
  </Modal>
}

// ───────────────────────── 页面 ─────────────────────────

export default function InvLater({ user }) {
  const [cfg, setCfg] = useState(null)
  const [cfgErr, setCfgErr] = useState('')
  const [tab, setTab] = useState('pool')
  const [setSeen, setSetSeen] = useState(false)   // 设置页签第一次打开后常驻（切走再回来不丢没保存的改动）
  const [creating, setCreating] = useState(false)
  const [selfLink, setSelfLink] = useState(false)
  const [toast, flash] = useToast()

  const loadCfg = useCallback(() => {
    invConfig().then(r => { setCfg(r || {}); setCfgErr('') }).catch(e => setCfgErr(errText(e)))
  }, [])
  useEffect(() => { loadCfg() }, [loadCfg])

  const can = cfg?.can || {}
  const me = cfg?.me?.name || user?.name || user?.username || ''
  const goSettings = () => { setTab('settings'); setSetSeen(true) }

  return <div>
    <div className="head">
      <div>
        <div className="h-title">发票后补池</div>
        <div className="h-sub">付款时没附发票的单子都挂在这里：到票点「收到」冲减，超期自动催，月底导出欠票清单对预付挂账。</div>
      </div>
      <div className="inv-lt-head">
        {can.config && <div className="inv-lt-seg">
          <button type="button" className={tab === 'pool' ? 'on' : ''} onClick={() => setTab('pool')}>后补池</button>
          <button type="button" className={tab === 'settings' ? 'on' : ''} onClick={goSettings}>设置</button>
        </div>}
        {tab === 'pool' && <a className="btn" href={invLaterExportUrl({ status: 'open' })}>导出欠票清单</a>}
        {tab === 'pool' && <button type="button" className="btn" onClick={() => setSelfLink(true)}>业务自助登记入口</button>}
        {tab === 'pool' && can.receive && <button type="button" className="btn-pri" onClick={() => setCreating(true)}>新建后补单</button>}
      </div>
    </div>
    <div className="body" style={tab === 'pool' ? undefined : { display: 'none' }}>
      <LaterPool cfg={cfg} cfgErr={cfgErr} can={can} me={me} flash={flash} onGoSettings={can.config ? goSettings : undefined}
        creating={creating && !!can.receive} setCreating={setCreating} />
    </div>
    {can.config && setSeen && <div style={tab === 'settings' ? undefined : { display: 'none' }}>
      <InvSettings user={user} embedded onSaved={loadCfg} />
    </div>}
    {selfLink && <SelfLinkModal onClose={() => setSelfLink(false)} />}
    {toast}
  </div>
}
