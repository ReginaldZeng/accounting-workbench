// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票管家各页共用组件：看图器/缩略条/字段面板/状态徽标/弹窗/toast/轮询/扫码框/拖拽上传/相机/手机配对/选人
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 看图器从这张票所在页打开（合并 PDF 第 3 张票不再显示第 1 张）；
//   弹窗挂到 body（台账侧栏是 sticky，弹窗在里面会被左侧导航盖住）；扫码框人在选字/按着鼠标时不抢焦点；手机已连上时打开「手机当相机」只看状态、要换手机须明确点。
// 组件接口以每个导出上方的 JSDoc 为准（技术方案 §6）。样式全在 inv.css（类名 inv- 前缀，只用 styles.css 令牌，深色可读）。
// 仓库里此前没有相机、拖拽、缩放看图、全局快捷键——这里从零写，并统一"让路"规则：
//   输入框/下拉/按钮有焦点、或有别的弹窗盖在上面时，扫码框不抢焦点、空格不拍照。
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { invPairCreate, invPairRevoke, invRoster } from '../api.js'
import './inv.css'

// ───────────────────────── 小工具 ─────────────────────────

/**
 * 金额显示：千分位 + 两位小数。空值/非数字 → '—'。
 * @param {number|string|null|undefined} v
 * @returns {string} 例：money(1234.5) → '1,234.50'；money(null) → '—'
 */
export function money(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(typeof v === 'string' ? v.replace(/,/g, '') : v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * 服务器时间串 → 'MM-DD HH:mm'（只做字符串截取，不拿本机时钟换算——服务器时间就是准的）。
 * @param {string} s 'YYYY-MM-DD HH:MM:SS'（也接受 'YYYY-MM-DDTHH:MM'）；空 → '—'；认不出的原样返回
 * @returns {string}
 */
export function fmtTime(s) {
  if (!s) return '—'
  const m = String(s).match(/^\d{4}-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/)
  if (m) return `${m[1]}-${m[2]} ${m[3]}:${m[4]}`
  const d = String(s).match(/^\d{4}-(\d{2})-(\d{2})$/)
  return d ? `${d[1]}-${d[2]}` : String(s)
}

// 弹窗栈：Esc 只关最上面那个；扫码框/空格拍照据此"让路"
const _modalStack = []
const _MODAL_SEL = '[aria-modal="true"], .bom-mask, .fsb-ov'   // 本组件的 Modal + 其它页面现成的两种遮罩
/** 当前是否有弹窗盖在 el 上面（el 本身在最上层弹窗里则不算被盖住）。el 省略＝只问有没有弹窗。 */
export function modalCovers(el) {
  const all = document.querySelectorAll(_MODAL_SEL)
  if (!all.length) return false
  const top = all[all.length - 1]
  return !(el && top.contains(el))
}
// 焦点落在这些元素上＝人在打字/操作控件，扫码框和空格拍照都不能抢
const _isTypingTarget = (t) => !!t && t !== document.body && (
  /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName) || t.isContentEditable || t.getAttribute?.('role') === 'button')
/** 页面上有没有选中的文字（人在选审批编号/发票号码准备复制）。有就别去抢焦点——一抢，选区就没了。 */
export function hasTextSelection() {
  try {
    const s = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null
    return !!(s && !s.isCollapsed && String(s).trim())
  } catch { return false }
}

/**
 * 轻提示。
 * @returns {[React.ReactNode, (text: string, kind?: 'info'|'ok'|'err') => void]}
 *   [toastNode, flash]：把 toastNode 放进页面 JSX 任意位置（fixed 定位）；flash(text, kind) 显示 2.6 秒自动消失。
 * 深色模式：底色用 var(--bg)、字用 var(--ink)，不学 .bom-toast 的 var(--ink)+#fff（深色下白字压浅底看不见）。
 */
export function useToast() {
  const [t, setT] = useState(null)
  const timer = useRef(null)
  const flash = useCallback((text, kind = 'info') => {
    clearTimeout(timer.current)
    setT({ text: String(text ?? ''), kind, n: Date.now() })
    timer.current = setTimeout(() => setT(null), 2600)
  }, [])
  useEffect(() => () => clearTimeout(timer.current), [])
  const node = t ? <div key={t.n} className={'inv-toast ' + (t.kind || 'info')} role="status" aria-live="polite">{t.text}</div> : null
  return [node, flash]
}

/**
 * 轮询：active 变 true 时立刻调一次 fn，之后每 ms 毫秒一次；active=false 或组件卸载即停。
 * 页面切到后台（document.hidden）时暂停，切回来立刻补一次——多开几个标签页也不会一直打后端。
 * 上一轮还没回来就不发下一轮。fn 用最新的（内部 ref），不必 useCallback。
 * fn 自己负责处理/展示错误；这里只兜底打一行 console.warn，防止未处理的 Promise 拒绝。
 * @param {() => any|Promise<any>} fn
 * @param {boolean} active 例：有票在识别中 / 在拉附件 / 配对弹窗开着
 * @param {number} [ms=1500]
 */
export function usePoll(fn, active, ms = 1500) {
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEffect(() => {
    if (!active) return undefined
    let stopped = false, busy = false
    const tick = async () => {
      if (stopped || busy || document.hidden) return
      busy = true
      try { await fnRef.current() } catch (e) { console.warn('[发票管家] 轮询失败', e) } finally { busy = false }
    }
    tick()
    const timer = setInterval(tick, ms)
    const onVis = () => { if (!document.hidden) tick() }
    document.addEventListener('visibilitychange', onVis)
    return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', onVis) }
  }, [active, ms])
}

// ───────────────────────── 弹窗 ─────────────────────────

/**
 * 通用弹窗。点遮罩空白处、按 Esc（只关最上层）关闭；打开时锁 body 滚动，关闭后把焦点还给打开前的元素；Tab 在弹窗内循环。
 * 遮罩不用 backdrop-filter（部分机器后台重绘会闪，V2.611 教训）。
 * 弹窗 DOM 挂到 document.body（createPortal）：写在 sticky/有层叠上下文的容器里（如台账右侧详情栏）也不会被左侧导航盖住；
 * React 事件照旧沿组件树冒泡，页面逻辑不受影响。
 * @param {object} p
 * @param {React.ReactNode} p.title 标题
 * @param {() => void} p.onClose 关闭回调（页面据此卸载本组件）
 * @param {React.ReactNode} p.children 正文（超高时正文内滚动）
 * @param {React.ReactNode} [p.footer] 底部按钮区（右对齐）
 * @param {number} [p.width=720] 最大宽度 px（窄屏自动收到 96vw）
 */
export function Modal({ title, onClose, children, footer, width = 720 }) {
  const ref = useRef(null)
  const prevFocusRef = useRef(typeof document !== 'undefined' ? document.activeElement : null)   // 首次渲染时记下（子元素 autoFocus 之前）
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const me = {}
    _modalStack.push(me)
    const prevFocus = prevFocusRef.current
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    // 已有子元素抢了焦点（如 autoFocus 输入框）就不动它
    if (ref.current && !ref.current.contains(document.activeElement)) ref.current.focus()
    const onKey = (e) => {
      if (_modalStack[_modalStack.length - 1] !== me) return
      if (e.key === 'Escape') { e.stopPropagation(); closeRef.current?.(); return }
      if (e.key === 'Tab' && ref.current) {
        const nodes = [...ref.current.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')]
          .filter(n => n.getClientRects().length)
        if (!nodes.length) { e.preventDefault(); return }
        const first = nodes[0], last = nodes[nodes.length - 1]
        if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      const i = _modalStack.indexOf(me)
      if (i >= 0) _modalStack.splice(i, 1)
      document.body.style.overflow = prevOverflow
      if (prevFocus && prevFocus.focus && document.contains(prevFocus)) { try { prevFocus.focus() } catch { /* 元素已不可聚焦 */ } }
    }
  }, [])
  const node = (
    <div className="inv-ov" onMouseDown={e => { if (e.target === e.currentTarget) onClose?.() }}>
      <div className="inv-modal" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1} ref={ref} style={{ width: `min(${width}px, 96vw)` }}>
        <div className="inv-mhead">
          <div className="inv-mtitle">{title}</div>
          <button type="button" className="inv-x" onClick={() => onClose?.()} aria-label="关闭">×</button>
        </div>
        <div className="inv-mbody">{children}</div>
        {footer && <div className="inv-mfoot">{footer}</div>}
      </div>
    </div>
  )
  return typeof document !== 'undefined' && document.body ? createPortal(node, document.body) : node
}

// ───────────────────────── 状态徽标 ─────────────────────────

const _PAPER_ORIGINS = ['attachment', 'photo_field']   // 从钉钉审批附件/图片栏来的电子件，纸质原件要另外到
const _VERIFY = {
  green: ['ok', '已验真', '税局清单里有，状态正常'],
  red: ['err', '作废红冲', '税局清单显示已作废或红冲'],
  yellow: ['warn', '税局未见', '税局清单里没有：可能还没上传、抬头税号填错或假票，需核实'],
  gray: ['mute', '不适用', '税局清单覆盖不到（定额、出租车、个人抬头等）'],
}
const _isBusy = it => it && (it.procStatus === 'pending' || it.procStatus === 'running')

function _dupTip(d) {
  if (!d || typeof d !== 'object') return '这张票别处已经登记过'
  if (d.kind === 'opening') return `期初已有这张票（${d.source || '期初导入'}${d.at ? ' · ' + fmtTime(d.at) : ''}）`
  const where = d.title || (d.businessId ? '审批单 ' + d.businessId : (d.folderId ? '票夹 #' + d.folderId : '别的票夹'))
  return `已在「${where}」登记过${d.applicant ? '（申请人 ' + d.applicant + '）' : ''}${d.at ? ' · ' + fmtTime(d.at) : ''}`
}

/**
 * 按一张票的状态算出徽标清单（页面想自己排版时可直接用）。
 * @param {object} it Item（技术方案 §5.1）
 * @returns {{key:string, label:string, tone:'ok'|'err'|'warn'|'info'|'mute', tip?:string}[]}
 */
export function itemBadges(it) {
  if (!it) return []
  const f = it.flags || {}
  const out = []
  if (it.review === 'void') out.push({ key: 'void', label: '已作废', tone: 'err', tip: it.reviewNote || '' })
  if (_isBusy(it)) out.push({ key: 'proc', label: '识别中', tone: 'info', tip: '正在读票面，几秒后字段会补上' })
  else if (it.procStatus === 'failed') out.push({ key: 'proc', label: '识别失败', tone: 'err', tip: it.procError || '图片读不出来，请手工补字段或重新识别' })
  if (f.dup) out.push({ key: 'dup', label: '重复', tone: 'err', tip: _dupTip(f.dup) })
  const pend = Array.isArray(it.pending) ? it.pending.length : 0
  if (pend) out.push({ key: 'pending', label: `待核${pend}`, tone: 'warn', tip: '这些字段是照片识别出来的，要对着图核一下' })
  if (it.kind === 'other' || f.notInvoice) out.push({ key: 'other', label: '非发票', tone: 'mute', tip: '附件不是发票（合同、清单、截图等），只留存不入台账' })
  else if (_PAPER_ORIGINS.includes(it.origin)) {
    out.push(it.paper ? { key: 'paper', label: '纸质件已到', tone: 'ok' } : { key: 'paper', label: '等纸质件', tone: 'mute', tip: '钉钉附件里有这张票，纸质原件还没扫到' })
  }
  if (f.buyerMismatch) out.push({ key: 'buyer', label: '抬头不符', tone: 'err', tip: `发票抬头：${f.buyerMismatch.buyer || '—'}；这张单的公司：${f.buyerMismatch.expected || '—'}` })
  if (f.buyerNotCompany) out.push({ key: 'buyerNc', label: '非本公司抬头', tone: 'err', tip: '发票抬头不是本公司任何一个主体' })
  if (f.sellerMismatch) out.push({ key: 'seller', label: '销方不符', tone: 'warn', tip: `发票销方：${f.sellerMismatch.seller || '—'}；付款单收款方：${f.sellerMismatch.payee || '—'}` })
  if (f.qrMismatch) out.push({ key: 'qr', label: '码面不符', tone: 'warn', tip: '二维码里的号码/金额和票面读出来的不一致' })
  if (it.verify && _VERIFY[it.verify]) {
    const [tone, label, tip] = _VERIFY[it.verify]
    out.push({ key: 'verify', label, tone, tip: it.verifyNote || tip })
  }
  if (it.deductSuggest === 'yes') out.push({ key: 'deduct', label: '建议可抵扣', tone: 'ok', tip: it.deductReason || '按票种和项目类别给的建议，会计定' })
  return out
}

/**
 * 一张票最要紧的状态 → 缩略图角上的小圆点颜色。
 * @param {object} it Item
 * @returns {'err'|'warn'|'info'|'ok'|'mute'}
 */
export function itemLevel(it) {
  const tones = itemBadges(it).map(b => b.tone)
  if (tones.includes('err')) return 'err'
  if (tones.includes('info')) return 'info'
  if (tones.includes('warn')) return 'warn'
  if (it && it.kind === 'other') return 'mute'
  return 'ok'
}

/**
 * 一张票的状态小徽标：识别中/识别失败、重复（悬停看在哪登记过）、待核N、纸质件已到/等纸质件、非发票、
 * 销方不符、抬头不符、非本公司抬头、码面不符、验真四色（已验真/作废红冲/税局未见/不适用）、已作废、建议可抵扣。
 * 没有任何状态时不渲染。
 * @param {object} p
 * @param {object} p.item Item（技术方案 §5.1）
 */
export function StatusBadges({ item }) {
  const list = itemBadges(item)
  if (!list.length) return null
  return <span className="inv-badges">
    {list.map(b => <span key={b.key} className={'inv-badge ' + b.tone} title={b.tip || undefined}>
      {b.key === 'proc' && b.tone === 'info' && <span className="inv-spin" aria-hidden="true" />}{b.label}
    </span>)}
  </span>
}

// ───────────────────────── 缩略图条 ─────────────────────────

/**
 * 横向缩略图条（票夹里一张张票）。每格：缩略图（没有图片的"仅二维码"占位）、右上角状态圆点、下方"序号 · 价税合计"。
 * 选中格自动滚到可见处。
 * @param {object} p
 * @param {object[]} p.items Item 数组（按页面想要的顺序）
 * @param {number|string} [p.activeId] 当前选中的 item.id
 * @param {(item: object) => void} p.onPick 点某一格
 */
export function ThumbStrip({ items, activeId, onPick }) {
  const wrap = useRef(null)
  useEffect(() => {
    const el = wrap.current?.querySelector('.inv-thumb.on')
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeId])
  if (!items || !items.length) return <div className="inv-thumbs empty">还没有票</div>
  return <div className="inv-thumbs" ref={wrap}>
    {items.map((it, i) => {
      const lv = itemLevel(it)
      return <button type="button" key={it.id} className={'inv-thumb' + (it.id === activeId ? ' on' : '') + (it.review === 'void' ? ' void' : '')}
        onClick={() => onPick?.(it)} title={(it.typeLabel || '') + (it.number ? ' ' + it.number : '')}>
        <span className="inv-thumb-img">
          {it.file && it.file.thumb
            ? <img src={it.file.thumb} alt="" loading="lazy" draggable={false} />
            : <span className="inv-thumb-ph">{it.file ? '无预览' : '仅二维码'}</span>}
          <span className={'inv-dot ' + lv} aria-hidden="true" />
          {_isBusy(it) && <span className="inv-thumb-busy"><span className="inv-spin" /></span>}
        </span>
        <span className="inv-thumb-cap"><b>{i + 1}</b>{it.kind === 'other' ? '非发票' : money(it.total)}</span>
      </button>
    })}
  </div>
}

// ───────────────────────── 看图器 ─────────────────────────

const _clamp = (v, a, b) => Math.max(a, Math.min(b, v))

/**
 * 看图器打开时该停在第几页（从 0 起）：页面显式传了 page 就用它；否则用这张票自己所在的页 item.page
 * （一个 PDF/OFD/XML 里合并了几张票时，每张票记着自己在第几页——第 3 张票要看第 3 页，不能总停在第 1 页）。
 * file.preview 为空、只好拿缩略图顶替时，缩略图本身就是这张票那一页，所以回 0。
 * @param {object} item Item
 * @param {number} [page] 页面显式指定的页
 * @returns {number}
 */
export function viewerStartPage(item, page) {
  if (Number.isFinite(page)) return Math.max(0, Math.trunc(page))
  const f = item?.file
  if (!f || !Array.isArray(f.preview) || !f.preview.length) return 0
  const p = Number(item?.page)
  return Number.isFinite(p) && p > 0 ? Math.trunc(p) : 0
}

/**
 * 票面看图器：滚轮缩放（以鼠标位置为中心）、按钮 +/−、拖拽平移、左右旋转 90°、"适应"复位、多页翻页、上一张/下一张、下载原件。
 * 打开/换票时停在这张票所在的页（item.page，见 viewerStartPage）；那一页没有预览图时明说，不拿第 1 页顶替。
 * 字段定位：activeField 对应的 item.fieldSrc[field].box（0..1，相对"未旋转"的预览图）在当前页时画高亮框；
 *   框和图片在同一个变换层里，跟着一起转、一起缩。切到某字段时若它在别的页，会自动翻过去。
 * 一张照片拍了几张票、识别时拆开了：item.region（0..1）是这张票在图上的那一块，画虚线框标出来。
 * 旋转只在前端用 CSS 转（不重新取图），同时回调 onRotate(新角度) 让页面落库（invItemRotate）。
 * 没有图片（只扫了二维码）时显示占位"只有二维码信息，暂无图片"。
 * @param {object} p
 * @param {object} p.item Item；用到 item.page、item.file{preview[],thumb,orig,pages,rotation,name}、item.fieldSrc
 * @param {number} [p.page] 指定打开第几页（从 0 起）；不传＝这张票所在的页 item.page
 * @param {(page: number) => void} [p.onPage] 翻页回调
 * @param {string} [p.activeField] 要高亮的字段键（同 FieldPanel 的键：number/date/sellerName…）
 * @param {(rotation: number) => void} [p.onRotate] 旋转后回调（0/90/180/270）
 * @param {number} [p.height=520] 看图区高度 px
 * @param {() => void} [p.onPrev] 上一张（不传不显示按钮）
 * @param {() => void} [p.onNext] 下一张（不传不显示按钮）
 */
export function InvViewer({ item, page, onPage, activeField, onRotate, height = 520, onPrev, onNext }) {
  const file = item?.file || null
  const previews = (file && Array.isArray(file.preview) && file.preview.length) ? file.preview : (file?.thumb ? [file.thumb] : [])
  const nPages = Math.max(previews.length, 1)
  const hasImg = previews.length > 0
  const start = viewerStartPage(item, page)
  const [pg, setPg] = useState(start)
  // 这一页没有预览图（老数据：长 PDF 只生成了前几页）→ 明说，不拿第 1 页冒充，免得对着别的票核字段
  const src = previews[pg]
  const noPage = hasImg && !src
  const [zoom, setZoom] = useState(1)                 // 相对"适应"的倍数
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [rot, setRot] = useState(file?.rotation || 0)
  const [nat, setNat] = useState(null)                // 预览图原始像素 {w,h}
  const [box, setBox] = useState({ w: 0, h: 0 })      // 看图区尺寸
  const [imgErr, setImgErr] = useState(false)
  const stage = useRef(null)
  const drag = useRef(null)
  const st = useRef({})                               // 给原生 wheel 监听读最新值

  const go = useCallback((n) => {
    const v = _clamp(n, 0, nPages - 1)
    setPg(v); onPage?.(v)
  }, [nPages, onPage])
  const fit = () => { setZoom(1); setPan({ x: 0, y: 0 }) }

  // 换票：复位缩放/平移/角度，页码回到这张票自己所在的页（同一文件里的另一张票也跟着翻）
  useEffect(() => { setPg(start); fit(); setImgErr(false); setNat(null) }, [item?.id])
  useEffect(() => { setPg(start) }, [page, item?.page])
  useEffect(() => { setRot(file?.rotation || 0) }, [item?.id, file?.rotation])
  useEffect(() => { setImgErr(false) }, [pg])
  // 点的字段在别的页 → 翻过去
  const fsrc = activeField && item?.fieldSrc ? item.fieldSrc[activeField] : null
  useEffect(() => {
    if (fsrc && fsrc.box && Number.isFinite(fsrc.page) && fsrc.page !== pg && fsrc.page < nPages) go(fsrc.page)
  }, [activeField, item?.id])

  // 看图区尺寸（窗口缩放/侧栏收起时跟着变）
  useEffect(() => {
    const el = stage.current
    if (!el) return undefined
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight })
    measure()
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(measure); ro.observe(el)
      return () => ro.disconnect()
    }
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [hasImg, noPage])

  const turned = rot % 180 !== 0
  const fitScale = nat && box.w && box.h
    ? Math.min((box.w - 16) / (turned ? nat.h : nat.w), (box.h - 16) / (turned ? nat.w : nat.h))
    : 1
  const scale = Math.max(fitScale, 0.01) * zoom
  st.current = { zoom, pan, fitScale, box }

  // 滚轮缩放：React 的 onWheel 是 passive，preventDefault 无效 → 原生监听
  useEffect(() => {
    const el = stage.current
    if (!el) return undefined
    const onWheel = (e) => {
      e.preventDefault()
      const { zoom: z, pan: p, box: b } = st.current
      const nz = _clamp(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 0.2, 8)
      if (nz === z) return
      const r = el.getBoundingClientRect()
      const mx = e.clientX - r.left - b.w / 2, my = e.clientY - r.top - b.h / 2   // 鼠标相对看图区中心
      const k = nz / z
      setZoom(nz)
      setPan({ x: mx - (mx - p.x) * k, y: my - (my - p.y) * k })               // 鼠标下那一点不动
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [hasImg, noPage])

  const zoomBy = (k) => {
    const nz = _clamp(zoom * k, 0.2, 8)
    const r = nz / zoom
    setZoom(nz); setPan(p => ({ x: p.x * r, y: p.y * r }))
  }
  const rotate = (d) => {
    const v = ((rot + d) % 360 + 360) % 360
    setRot(v); setPan({ x: 0, y: 0 }); setZoom(1)
    onRotate?.(v)
  }
  const onDown = (e) => {
    if (e.button !== 0 || e.target.closest('button,a')) return
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
    try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* 老浏览器 */ }
  }
  const onMove = (e) => {
    const d = drag.current
    if (!d) return
    setPan({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y })
  }
  const onUp = () => { drag.current = null }

  if (!item) return <div className="inv-vw" style={{ height }}><div className="inv-vw-ph">请在左侧选一张票</div></div>

  const showBox = fsrc && Array.isArray(fsrc.box) && fsrc.box.length === 4 && (fsrc.page || 0) === pg
  const reg = Array.isArray(item.region) && item.region.length === 4 && (Number(item.page) || 0) === pg ? item.region : null
  const nav = (
    <>
      {onPrev && <button type="button" className="inv-vw-nav l" onClick={onPrev} title="上一张">‹</button>}
      {onNext && <button type="button" className="inv-vw-nav r" onClick={onNext} title="下一张">›</button>}
    </>
  )

  if (!file || !hasImg) {
    return <div className="inv-vw" style={{ height }}>
      <div className="inv-vw-stage"><div className="inv-vw-ph">只有二维码信息，暂无图片</div>{nav}</div>
    </div>
  }
  if (noPage) {
    return <div className="inv-vw" style={{ height }}>
      <div className="inv-vw-bar">
        <span className="inv-vw-pct">这张票在原件第 {pg + 1} 页</span>
        {nPages > 0 && <button type="button" className="inv-ib wide" onClick={() => go(nPages - 1)} title="看有预览图的最后一页">看第 {nPages} 页</button>}
        <span className="inv-vw-grow" />
        {file.orig && <a className="inv-lk" href={file.orig} download={file.name || true}>下载原件</a>}
      </div>
      <div className="inv-vw-stage">
        <div className="inv-vw-ph">原件第 {pg + 1} 页没有生成预览图，请点右上角「下载原件」翻到这一页核对（别拿别的页的票面核字段）</div>
        {nav}
      </div>
    </div>
  }

  const wrapStyle = nat ? {
    width: nat.w, height: nat.h,
    left: (box.w - nat.w) / 2, top: (box.h - nat.h) / 2,
    transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale}) rotate(${rot}deg)`,
  } : { visibility: 'hidden' }

  return (
    <div className="inv-vw" style={{ height }}>
      <div className="inv-vw-bar">
        <button type="button" className="inv-ib" onClick={() => zoomBy(1 / 1.25)} title="缩小">−</button>
        <span className="inv-vw-pct">{Math.round(zoom * 100)}%</span>
        <button type="button" className="inv-ib" onClick={() => zoomBy(1.25)} title="放大">＋</button>
        <button type="button" className="inv-ib wide" onClick={fit} title="整张放进框里">适应</button>
        <span className="inv-vw-sep" />
        <button type="button" className="inv-ib" onClick={() => rotate(-90)} title="向左转 90°">⟲</button>
        <button type="button" className="inv-ib" onClick={() => rotate(90)} title="向右转 90°">⟳</button>
        {nPages > 1 && <>
          <span className="inv-vw-sep" />
          <button type="button" className="inv-ib" disabled={pg <= 0} onClick={() => go(pg - 1)} title="上一页">‹</button>
          <span className="inv-vw-pct">第 {pg + 1} / {nPages} 页</span>
          <button type="button" className="inv-ib" disabled={pg >= nPages - 1} onClick={() => go(pg + 1)} title="下一页">›</button>
        </>}
        <span className="inv-vw-grow" />
        {file.orig && <a className="inv-lk" href={file.orig} download={file.name || true}>下载原件</a>}
      </div>
      <div className="inv-vw-stage grab" ref={stage}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
        onDoubleClick={fit}>
        {imgErr
          ? <div className="inv-vw-ph">图片读不出来（可能还在生成或已被移除），稍后再点一下这张票试试</div>
          : <div className="inv-vw-wrap" style={wrapStyle}>
            <img src={src} alt={file.name || '票面'} draggable={false}
              onLoad={e => setNat({ w: e.currentTarget.naturalWidth || 1, h: e.currentTarget.naturalHeight || 1 })}
              onError={() => setImgErr(true)} />
            {reg && <span className="inv-vw-region" title="一张照片里拍了几张票：这张在虚线框里" style={{
              left: `${reg[0] * 100}%`, top: `${reg[1] * 100}%`,
              width: `${Math.max(reg[2] - reg[0], 0.01) * 100}%`, height: `${Math.max(reg[3] - reg[1], 0.01) * 100}%`,
              borderWidth: Math.max(3 / scale, 1.5),
            }} />}
            {showBox && <span className="inv-vw-box" style={{
              left: `${fsrc.box[0] * 100}%`, top: `${fsrc.box[1] * 100}%`,
              width: `${Math.max(fsrc.box[2] - fsrc.box[0], 0.004) * 100}%`, height: `${Math.max(fsrc.box[3] - fsrc.box[1], 0.004) * 100}%`,
              borderWidth: Math.max(2 / scale, 1),
            }} />}
          </div>}
        {!nat && !imgErr && <div className="inv-vw-ph"><span className="inv-spin" />&nbsp;图片加载中…</div>}
        {nav}
      </div>
    </div>
  )
}

// ───────────────────────── 字段面板 ─────────────────────────

/** 字段键 → 中文名（FieldPanel 的行顺序；页面要引用字段名时也可用） */
export const INV_FIELDS = [
  ['typeLabel', '票种'], ['code', '发票代码'], ['number', '发票号码'], ['date', '开票日期'],
  ['buyerName', '购方名称'], ['buyerTaxId', '购方税号'], ['sellerName', '销方名称'], ['sellerTaxId', '销方税号'],
  ['amount', '金额'], ['tax', '税额'], ['total', '价税合计'], ['taxRate', '税率'], ['category', '项目类别'],
]
const _MONEY_FIELDS = new Set(['amount', 'tax', 'total'])
/** fieldSrc.src → 来源小标签 */
export const SRC_LABEL = { qr: '二维码', pdf: '原件', ofd: '原件', xml: '原件', ocr: '识别', manual: '手工', taxpack: '税局', taxlist: '税局' }
const _fmtVal = (k, v) => (_MONEY_FIELDS.has(k) ? money(v) : (v === null || v === undefined || v === '' ? '—' : String(v)))

/**
 * 票面字段面板：票种、代码、号码、开票日期、购/销方名称税号、金额、税额、价税合计、税率、项目类别。
 * 每行：值 + 来源小标签（二维码/原件/识别/手工/税局）；在 item.pending 里的行标黄"待核"。
 * 点一行 → onFieldFocus(字段键)，页面把它传给 InvViewer.activeField 在图上框出位置。
 * editable 时：值变成输入框，改过的行标"已改"；「保存」→ onSave({字段: 新值})（金额类转成数字，清空＝null），
 *   onSave 返回的 Promise 成功后清掉草稿；「核对无误」→ onConfirm('all')（有待核字段时才可点）。
 * 金额＋税额≠价税合计（差 > 0.01）时在底部提示。
 * @param {object} p
 * @param {object} p.item Item
 * @param {boolean} [p.editable=false]
 * @param {(changed: object) => (void|Promise<any>)} [p.onSave]
 * @param {(which: 'all') => (void|Promise<any>)} [p.onConfirm]
 * @param {string} [p.activeField] 当前高亮的字段键
 * @param {(field: string) => void} [p.onFieldFocus]
 * @param {boolean} [p.hideConfirm] 不显示「核对无误」（审核弹窗用右上角的「提交」代替）
 * @param {(dirty: boolean) => void} [p.onDirtyChange] 有没有没保存的改动（审核弹窗据此拦「提交」）
 * 系统自动核过的字段（fieldSrc[字段].sys＝依据）标「系统已核」，悬停/下方小字写依据。
 */
export function FieldPanel({ item, editable = false, onSave, onConfirm, activeField, onFieldFocus, hideConfirm = false, onDirtyChange }) {
  const [draft, setDraft] = useState({})
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setDraft({}); setErr('') }, [item?.id])
  const dirtyNow = Object.keys(draft).length > 0
  useEffect(() => { onDirtyChange?.(dirtyNow) }, [dirtyNow])
  if (!item) return <div className="inv-fp"><div className="inv-fp-empty">请选一张票</div></div>

  const pending = new Set(Array.isArray(item.pending) ? item.pending : [])
  const src = item.fieldSrc || {}
  const dirty = Object.keys(draft).length > 0
  const valOf = k => (k in draft ? draft[k] : (item[k] ?? ''))
  const setField = (k, v) => {
    setErr('')
    setDraft(d => {
      const n = { ...d }
      const orig = item[k] === null || item[k] === undefined ? '' : String(item[k])
      if (String(v) === orig) delete n[k]; else n[k] = v
      return n
    })
  }
  const save = async () => {
    if (!dirty || busy) return
    const out = {}
    for (const [k, v] of Object.entries(draft)) {
      if (_MONEY_FIELDS.has(k)) {
        const s = String(v).replace(/[,，\s¥￥]/g, '')
        if (s === '') { out[k] = null; continue }
        const n = Number(s)
        if (!Number.isFinite(n)) { setErr(`「${INV_FIELDS.find(f => f[0] === k)?.[1]}」要填数字`); return }
        out[k] = Math.round(n * 100) / 100
      } else if (k === 'date') {
        const s = String(v).trim().replace(/[./年月]/g, '-').replace(/日$/, '')
        if (s && !/^\d{4}-\d{1,2}-\d{1,2}$/.test(s)) { setErr('开票日期写成 2026-09-24 这样'); return }
        out[k] = s ? s.replace(/-(\d)(?=-|$)/g, '-0$1') : ''
      } else out[k] = String(v).trim()
    }
    setBusy(true)
    try { await onSave?.(out); setDraft({}) } catch (e) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }
  const confirm = async () => {
    if (busy) return
    setBusy(true)
    try { await onConfirm?.('all') } catch (e) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }
  const a = Number(item.amount), t = Number(item.tax), tt = Number(item.total)
  const sumOff = [item.amount, item.tax, item.total].every(v => v !== null && v !== undefined && v !== '')
    && Number.isFinite(a + t + tt) && Math.abs(a + t - tt) > 0.01

  return (
    <div className="inv-fp">
      {INV_FIELDS.map(([k, label]) => {
        const s = src[k]
        const isPend = pending.has(k)
        return (
          <div key={k} className={'inv-fp-row' + (k === activeField ? ' on' : '') + (isPend ? ' pend' : '') + (k in draft ? ' dirty' : '')}
            onClick={() => onFieldFocus?.(k)}>
            <span className="inv-fp-k">{label}</span>
            <span className="inv-fp-v">
              {editable
                ? <input className="inv-in" value={valOf(k)} onFocus={() => onFieldFocus?.(k)}
                  inputMode={_MONEY_FIELDS.has(k) ? 'decimal' : undefined}
                  onChange={e => setField(k, e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') { e.preventDefault(); save() }
                    if (e.key === 'Escape' && k in draft) { e.stopPropagation(); setField(k, item[k] ?? '') }
                  }} />
                : <span className={_MONEY_FIELDS.has(k) ? 'inv-num' : ''}>{_fmtVal(k, item[k])}</span>}
            </span>
            <span className="inv-fp-tags">
              {isPend && <span className="inv-badge warn">待核</span>}
              {k in draft ? <span className="inv-src dirty">已改</span>
                : s?.sys && !isPend ? <span className="inv-src sys" title={'系统已核：' + s.sys}>系统已核</span>
                  : s?.src ? <span className={'inv-src ' + s.src}>{SRC_LABEL[s.src] || s.src}</span> : null}
              {s?.sys && !isPend && !(k in draft) ? <span className="inv-fp-why">{s.sys}</span> : null}
            </span>
          </div>
        )
      })}
      {sumOff && <div className="inv-fp-warn">金额＋税额 ≠ 价税合计（差 {money(a + t - tt)}），请对着票面核一下</div>}
      {err && <div className="inv-fp-err">{err}</div>}
      {editable && (
        <div className="inv-fp-act">
          {pending.size > 0 && <span className="inv-fp-hint">还有 {pending.size} 项待核</span>}
          <span className="inv-vw-grow" />
          <button type="button" className="btn" disabled={!dirty || busy} onClick={save}>保存</button>
          {!hideConfirm && <button type="button" className="btn primary" disabled={busy || dirty || pending.size === 0} onClick={confirm}
            title={dirty ? '先保存改动' : pending.size === 0 ? '没有待核字段' : '把识别来的字段都确认为已核对'}>核对无误</button>}
        </div>
      )}
    </div>
  )
}

// ───────────────────────── 扫码框 ─────────────────────────

/**
 * 扫码枪输入框（扫码枪＝键盘输入 + 回车）。回车（或 Tab）提交去掉首尾空格的内容并清空。
 * 检测到中文输入法（compositionstart / keyCode 229）时提示「请切换到英文输入法再扫」——输入法开着，扫码枪敲进来的字母会被吃掉。
 * keepFocus：失焦后自动抢回焦点（让扫码枪随时能扫），但下面这些时候不抢（人在用页面）：
 *   点了别的输入框/下拉/文本域；有弹窗盖着；页面上有选中的文字（在选审批编号、发票号码准备复制）；鼠标还按着（正在拖选）；
 *   用键盘 Tab 走到了某个按钮上。失焦当下只在焦点落回 body（点了空白处）时抢回；点按钮留下的焦点由每秒一次的兜底拿回。
 * @param {object} p
 * @param {(code: string) => void} p.onScan
 * @param {string} [p.placeholder='用扫码枪扫审批单或发票上的二维码']
 * @param {boolean} [p.autoFocus=true]
 * @param {boolean} [p.keepFocus=false]
 * @param {boolean} [p.disabled]
 */
export function ScanInput({ onScan, placeholder = '用扫码枪扫审批单或发票上的二维码', autoFocus = true, keepFocus = false, disabled }) {
  const ref = useRef(null)
  const [val, setVal] = useState('')
  const [ime, setIme] = useState(false)
  const [focused, setFocused] = useState(false)
  const composing = useRef(false)
  const pointerDown = useRef(false)     // 鼠标/手指按着（拖选文字中）
  const kbNav = useRef(false)           // 最近一次是用键盘 Tab 挪的焦点（键盘用户走到按钮上，不抢）

  // onlyFromBody：失焦当下只在焦点落回 body 时抢；兜底轮询也可从"点完按钮留下的焦点"抢回
  const tryFocus = useCallback((onlyFromBody = false) => {
    const el = ref.current
    if (!el || el.disabled || document.activeElement === el) return
    const a = document.activeElement
    const idle = !a || a === document.body || a === document.documentElement
    if (!idle) {
      if (onlyFromBody || kbNav.current) return
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable) return
    }
    if (pointerDown.current) return
    if (hasTextSelection()) return
    if (modalCovers(el)) return
    el.focus({ preventScroll: true })
  }, [])

  useEffect(() => { if (autoFocus && !disabled) tryFocus() }, [autoFocus, disabled])
  // 保持焦点：失焦后稍等再抢（给点击按钮的动作先完成）；另每秒兜底一次（弹窗关掉、焦点落回 body 的情况）
  useEffect(() => {
    if (!keepFocus || disabled) return undefined
    const down = () => { pointerDown.current = true; kbNav.current = false }
    const up = () => { pointerDown.current = false }
    // 冒泡阶段听：扫码枪带的 Tab 已被扫码框自己拦下（defaultPrevented），不算键盘挪焦点
    const key = (e) => { if (e.key === 'Tab' && !e.defaultPrevented) kbNav.current = true }
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('pointerup', up, true)
    document.addEventListener('pointercancel', up, true)
    document.addEventListener('dragend', up, true)
    window.addEventListener('blur', up)
    document.addEventListener('keydown', key)
    const t = setInterval(() => { if (!document.hidden && document.hasFocus()) tryFocus(false) }, 1000)
    return () => {
      clearInterval(t)
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('pointerup', up, true)
      document.removeEventListener('pointercancel', up, true)
      document.removeEventListener('dragend', up, true)
      window.removeEventListener('blur', up)
      document.removeEventListener('keydown', key)
    }
  }, [keepFocus, disabled, tryFocus])
  const onBlur = () => {
    setFocused(false)
    if (keepFocus && !disabled) setTimeout(() => tryFocus(true), 180)
  }

  const submit = () => {
    const v = val.trim()
    setVal('')
    if (!v) return
    // 输入法开着时扫进来的往往是一串中文/全角——照样交给后端认（认不出会说），但提示保留着
    if (/^[\x20-\x7e]+$/.test(v)) setIme(false)
    onScan?.(v)
  }
  const onKeyDown = (e) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) { setIme(true); return }
    if (e.key === 'Enter' || (e.key === 'Tab' && val.trim())) { e.preventDefault(); submit() }
  }
  return (
    <div className={'inv-scan' + (focused ? ' on' : '') + (disabled ? ' off' : '')}>
      <span className="inv-scan-ico" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M4 8V5h3M17 5h3v3M20 16v3h-3M7 19H4v-3M7 12h10" /></svg>
      </span>
      <input ref={ref} className="inv-scan-in" data-inv-scan="1" value={val} disabled={disabled} placeholder={placeholder}
        autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false} inputMode="url"
        onChange={e => { setVal(e.target.value); if (!composing.current && /^[\x20-\x7e]*$/.test(e.target.value) && e.target.value) setIme(false) }}
        onKeyDown={onKeyDown}
        onCompositionStart={() => { composing.current = true; setIme(true) }}
        onCompositionEnd={() => { composing.current = false }}
        onFocus={() => setFocused(true)} onBlur={onBlur} />
      <span className={'inv-scan-st' + (ime ? ' warn' : '')}>
        {ime ? '请切换到英文输入法再扫' : disabled ? '暂不可扫' : focused ? '扫码枪就绪' : '点这里再扫'}
      </span>
    </div>
  )
}

// ───────────────────────── 拖拽上传 ─────────────────────────

/**
 * 拖拽/点击选文件区。拖进来时高亮；点一下弹出选文件（选完清空 value，同一个文件能再选）；disabled 时拖放和点击都不理。
 * 文件类型由后端最终把关（这里的 accept 只影响选文件对话框的过滤）。
 * @param {object} p
 * @param {(files: File[]) => void} p.onFiles
 * @param {string} [p.accept] 例 '.pdf,.ofd,.xml,.zip,image/*'
 * @param {boolean} [p.multiple=true]
 * @param {React.ReactNode} [p.children] 区内文案，缺省「把文件拖到这里，或点击选择」
 * @param {boolean} [p.disabled]
 * @param {string} [p.className] 追加类名（页面自定尺寸用）
 * @param {boolean} [p.dropOnly=false] 只接拖放、不当按钮用（整块面板外面包一层时用：不给 role=button/tabIndex/aria-disabled、
 *   不点一下弹选文件——否则面板里的按钮会被读屏软件当成"不可用"，点面板里的字还会把焦点抢到这层上）
 */
export function FileDrop({ onFiles, accept, multiple = true, children, disabled, className, dropOnly = false }) {
  const inp = useRef(null)
  const depth = useRef(0)
  const [over, setOver] = useState(false)
  const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files')
  const hand = (list) => {
    let files = [...(list || [])]
    if (!multiple) files = files.slice(0, 1)
    if (files.length) onFiles?.(files)
  }
  const open = () => { if (!disabled) inp.current?.click() }
  const asButton = dropOnly ? {} : {
    role: 'button', tabIndex: disabled ? -1 : 0, 'aria-disabled': disabled || undefined,
    onClick: open,
    onKeyDown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } },
  }
  return (
    <div className={'inv-drop' + (over ? ' over' : '') + (disabled ? ' off' : '') + (className ? ' ' + className : '')}
      {...asButton}
      onDragEnter={e => { if (!hasFiles(e)) return; e.preventDefault(); depth.current += 1; if (!disabled) setOver(true) }}
      onDragOver={e => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = disabled ? 'none' : 'copy' }}
      onDragLeave={() => { depth.current = Math.max(0, depth.current - 1); if (!depth.current) setOver(false) }}
      onDrop={e => { e.preventDefault(); depth.current = 0; setOver(false); if (!disabled) hand(e.dataTransfer?.files) }}>
      {children || <span className="inv-drop-tx">把文件拖到这里，或<b>点击选择</b></span>}
      {!dropOnly && <input ref={inp} type="file" hidden accept={accept} multiple={multiple}
        onClick={e => e.stopPropagation()}
        onChange={e => { const fs = e.target.files; hand(fs); e.target.value = '' }} />}
    </div>
  )
}

// ───────────────────────── 相机（高拍仪） ─────────────────────────

const _CAM_KEY = 'inv_cam'
const _camGet = () => { try { return localStorage.getItem(_CAM_KEY) || '' } catch { return '' } }
const _camSet = v => { try { localStorage.setItem(_CAM_KEY, v || '') } catch { /* 隐私模式等存不了，下次再选 */ } }
// 自动拍阈值（64×48 灰度图逐点平均差，0..255）：大于 HIGH＝有东西在动；小于 LOW 持续 STILL_MS＝放稳了；
// 和上一张已拍的差大于 CHANGE 才再拍（同一张票挪一挪不重拍）
const _HIGH = 14, _LOW = 4, _CHANGE = 12, _STILL_MS = 700
function _camErr(e) {
  const n = e && e.name
  if (n === 'NotAllowedError' || n === 'SecurityError') return '没有摄像头权限：点浏览器地址栏左侧的锁形图标，把「摄像头」改成允许，再点「重试」'
  if (n === 'NotFoundError' || n === 'OverconstrainedError' || n === 'DevicesNotFoundError') return '没找到摄像头：检查高拍仪 USB 线是否插好，插好后点「重试」'
  if (n === 'NotReadableError' || n === 'TrackStartError') return '摄像头被别的程序占着（高拍仪自带软件、会议软件等），关掉后点「重试」'
  return '打开摄像头失败：' + ((e && (e.message || e.name)) || '未知原因')
}

/**
 * 高拍仪/摄像头面板。选摄像头（记在本机 localStorage 'inv_cam'）→ 实时画面 → 「拍照」或按空格拍一张，
 * 以全分辨率截帧 JPEG(0.9) 交给 onCapture(blob)（页面再调 invUpload([blob], {origin:'camera'})）。
 * 自动拍（auto）：每 200ms 比一次 64×48 灰度小图——先看到"在动"，再等画面静止约 0.7 秒就拍一张；
 *   和上一张拍下的画面差别不大时不重复拍（票不换就不会连拍）。
 * 空格拍照只在焦点不在输入框/下拉/按钮上、且没有别的弹窗盖着时生效；例外：焦点在「空的」ScanInput 上时照样拍
 *   （收票台扫码框常驻焦点，否则空格永远拍不了）。
 * 非 https（安全上下文）时浏览器不给开摄像头，面板直接说明怎么办。卸载时关掉摄像头。
 * @param {object} p
 * @param {(blob: Blob) => void} p.onCapture
 * @param {boolean} [p.auto=false] 是否自动拍
 * @param {(v: boolean) => void} [p.onAutoChange] 切换自动拍（不传则不显示开关）
 * @param {boolean} [p.disabled] 例：没有当前票夹时
 * @param {number} [p.height=300] 画面高度 px
 * @param {string} [p.disabledHint] 不能拍时拍照按钮旁的说明（缺省按收票台说「先扫审批单打开一个票夹」）
 */
export function CameraPanel({ onCapture, auto = false, onAutoChange, disabled, height = 300, disabledHint = '先扫审批单打开一个票夹，再拍票' }) {
  const secure = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia
  const root = useRef(null)
  const video = useRef(null)
  const stream = useRef(null)
  const [devs, setDevs] = useState([])
  const [dev, setDev] = useState(_camGet())
  const [err, setErr] = useState('')
  const [live, setLive] = useState(false)
  const [res, setRes] = useState('')
  const [shot, setShot] = useState(0)          // 递增触发「已拍」闪一下
  const [autoSt, setAutoSt] = useState('')
  const [retry, setRetry] = useState(0)
  const capRef = useRef(onCapture)
  capRef.current = onCapture
  const disRef = useRef(disabled)
  disRef.current = disabled
  const lastShot = useRef(null)                 // 上一张拍下时的灰度小图（自动拍去重用）

  const stop = () => {
    const s = stream.current
    if (s) s.getTracks().forEach(t => { try { t.stop() } catch { /* 已停 */ } })
    stream.current = null
    if (video.current) video.current.srcObject = null
    setLive(false)
  }
  const listDevs = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices()
      setDevs(all.filter(d => d.kind === 'videoinput'))
    } catch { setDevs([]) }
  }, [])

  // 开摄像头（换设备/重试时重开）
  useEffect(() => {
    if (!secure) return undefined
    let cancelled = false
    const want = (w) => ({ video: { ...(w ? { deviceId: { exact: w } } : {}), width: { ideal: 2592 }, height: { ideal: 1944 } }, audio: false })
    ;(async () => {
      stop(); setErr('')
      let s = null
      try { s = await navigator.mediaDevices.getUserMedia(want(dev)) }
      catch (e) {
        // 记住的那台不在了（换了电脑/拔了高拍仪）→ 退回默认摄像头再试一次
        if (dev && (e.name === 'OverconstrainedError' || e.name === 'NotFoundError')) {
          try { s = await navigator.mediaDevices.getUserMedia(want('')) } catch (e2) { if (!cancelled) setErr(_camErr(e2)); return }
        } else { if (!cancelled) setErr(_camErr(e)); return }
      }
      if (cancelled) { s.getTracks().forEach(t => t.stop()); return }
      stream.current = s
      const v = video.current
      if (v) { v.srcObject = s; try { await v.play() } catch { /* autoplay 被拦时 muted 下通常仍能播 */ } }
      const tr = s.getVideoTracks()[0]
      const set = tr && tr.getSettings ? tr.getSettings() : {}
      setRes(set.width && set.height ? `${set.width}×${set.height}` : '')
      setLive(true)
      listDevs()   // 授权后才拿得到设备名
    })()
    return () => { cancelled = true; stop() }
  }, [secure, dev, retry])

  useEffect(() => {
    if (!secure) return undefined
    listDevs()
    const md = navigator.mediaDevices
    md.addEventListener?.('devicechange', listDevs)
    return () => md.removeEventListener?.('devicechange', listDevs)
  }, [secure, listDevs])

  const gray = useRef(null)
  const grab = () => {   // 64×48 灰度小图
    const v = video.current
    if (!v || !v.videoWidth) return null
    if (!gray.current) { gray.current = document.createElement('canvas'); gray.current.width = 64; gray.current.height = 48 }
    const ctx = gray.current.getContext('2d', { willReadFrequently: true })
    ctx.drawImage(v, 0, 0, 64, 48)
    const d = ctx.getImageData(0, 0, 64, 48).data
    const g = new Uint8Array(64 * 48)
    for (let i = 0, j = 0; j < g.length; i += 4, j += 1) g[j] = (d[i] * 77 + d[i + 1] * 150 + d[i + 2] * 29) >> 8
    return g
  }
  const diff = (a, b) => { let s = 0; for (let i = 0; i < a.length; i += 1) s += Math.abs(a[i] - b[i]); return s / a.length }

  const busy = useRef(false)
  const capture = useCallback(() => {
    const v = video.current
    if (!v || !v.videoWidth || busy.current || disRef.current) return
    busy.current = true
    const c = document.createElement('canvas')
    c.width = v.videoWidth; c.height = v.videoHeight
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height)
    lastShot.current = grab()
    c.toBlob(b => {
      busy.current = false
      if (b) { setShot(n => n + 1); capRef.current?.(b) }
    }, 'image/jpeg', 0.9)
  }, [])

  // 空格拍照
  useEffect(() => {
    if (!secure) return undefined
    const onKey = (e) => {
      if (e.code !== 'Space' && e.key !== ' ') return
      if (e.repeat || e.ctrlKey || e.altKey || e.metaKey) return
      // 例外：焦点在空的扫码框上（收票台扫码框常驻焦点）也放行——审批/发票码里没有空格，不会误拍
      const t = document.activeElement
      const idleScan = t && t.dataset && t.dataset.invScan === '1' && !t.value
      if (!idleScan && (_isTypingTarget(e.target) || _isTypingTarget(t))) return
      if (modalCovers(root.current)) return
      if (disRef.current || !stream.current) return
      e.preventDefault()
      capture()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [secure, capture])

  // 自动拍：动 → 静 → 拍；和上一张差不多不重拍
  useEffect(() => {
    if (!secure || !auto || !live || disabled) { setAutoSt(''); return undefined }
    let prev = null, moved = false, stillSince = 0
    setAutoSt('等放票')
    const t = setInterval(() => {
      if (document.hidden) return
      const cur = grab()
      if (!cur) return
      if (!prev) { prev = cur; return }
      const d = diff(cur, prev)
      prev = cur
      if (d > _HIGH) { moved = true; stillSince = 0; setAutoSt('画面在动…'); return }
      if (!moved) return
      if (d < _LOW) {
        const now = Date.now()
        if (!stillSince) { stillSince = now; setAutoSt('放稳了，马上拍') }
        if (now - stillSince >= _STILL_MS) {
          moved = false; stillSince = 0
          if (!lastShot.current || diff(cur, lastShot.current) > _CHANGE) capture()
          setAutoSt('等放票')
        }
      } else stillSince = 0
    }, 200)
    return () => clearInterval(t)
  }, [secure, auto, live, disabled, capture])

  const [flashOn, setFlashOn] = useState(false)
  useEffect(() => {
    if (!shot) return undefined
    setFlashOn(true)
    const t = setTimeout(() => setFlashOn(false), 900)
    return () => clearTimeout(t)
  }, [shot])

  if (!secure) {
    return <div className="inv-cam" ref={root}>
      <div className="inv-cam-msg" style={{ height }}>
        <b>电脑上的高拍仪要在 https 地址下才能用</b>
        <span>浏览器只在安全地址下允许网页开摄像头；正式域名开通 https 前，请先用「手机当相机」或「拖文件」收票
          （或请管理员在这台电脑的浏览器里把本站设为可信地址）。</span>
      </div>
    </div>
  }
  return (
    <div className={'inv-cam' + (disabled ? ' off' : '')} ref={root}>
      <div className="inv-cam-bar">
        <select value={dev} onChange={e => { setDev(e.target.value); _camSet(e.target.value) }} title="选择摄像头（高拍仪）">
          <option value="">默认摄像头</option>
          {devs.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `摄像头 ${i + 1}`}</option>)}
        </select>
        {onAutoChange && <label className="inv-cam-auto" title="票放上去、放稳了就自动拍一张">
          <input type="checkbox" checked={!!auto} onChange={e => onAutoChange(e.target.checked)} />自动拍
        </label>}
        <span className="inv-vw-grow" />
        {res && <span className="inv-cam-res">{res}</span>}
      </div>
      <div className="inv-cam-stage" style={{ height }}>
        <video ref={video} autoPlay playsInline muted />
        {err && <div className="inv-cam-msg over"><b>{err}</b><button type="button" className="btn" onClick={() => setRetry(n => n + 1)}>重试</button></div>}
        {!err && !live && <div className="inv-cam-msg over"><span><span className="inv-spin" />&nbsp;正在打开摄像头…</span></div>}
        {flashOn && <div className="inv-cam-flash">已拍</div>}
        {autoSt && <span className="inv-cam-st">{autoSt}</span>}
      </div>
      <div className="inv-cam-foot">
        <button type="button" className="btn primary" disabled={!live || disabled} onClick={capture}>拍照</button>
        <span className="inv-muted">{disabled ? disabledHint : '也可以按空格拍'}</span>
      </div>
    </div>
  )
}

// ───────────────────────── 手机配对 ─────────────────────────

/**
 * 「手机当相机」配对弹窗。
 * 已有手机连着（pairState.bound）时打开＝只看状态：显示连着的是谁、最近在不在线，给「断开」和「换一部手机」——
 *   不自动生成新码（生成新码会吊销本人旧配对，正在用的手机会被悄悄踢掉）；要换手机须明确点「换一部手机」。
 * 没连手机时打开即调 invPairCreate() 生成一次性配对码，显示二维码图片和剩余有效时间；
 * 状态来自页面的 desk 轮询 pairState（{id?, bound, dtName, device, lastSeen?, active?}）：未连上显示「等待手机用钉钉扫码」，连上显示「已连接：姓名」。
 * ⚠ 页面在本弹窗开着时要保持轮询 invDesk（usePoll(..., pairOpen || 有票在识别)），否则状态不会变。
 * 「断开」→ invPairRevoke() 后关闭；过期可「重新生成」。
 * @param {object} p
 * @param {() => void} p.onClose
 * @param {{id?:number, bound?:boolean, dtName?:string, device?:string, lastSeen?:string, active?:boolean}|null} [p.pairState] 来自 invDesk().pair
 */
export function PairModal({ onClose, pairState }) {
  const [mode, setMode] = useState(() => (pairState && pairState.bound ? 'status' : 'create'))
  const [res, setRes] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [left, setLeft] = useState(null)       // 剩余秒数
  const [gen, setGen] = useState(0)
  const deadline = useRef(0)

  useEffect(() => {
    if (mode !== 'create') return undefined
    let live = true
    setRes(null); setErr(''); setLeft(null)
    invPairCreate().then(r => {
      if (!live) return
      setRes(r)
      const p = r.pair || {}
      // 剩余时间优先用后端给的秒数；没有就用 expiresAt 和本机时间估（同在东八区，几秒误差无碍）
      let secs = Number(p.expiresIn)
      if (!Number.isFinite(secs) && p.expiresAt) secs = (Date.parse(String(p.expiresAt).replace(' ', 'T')) - Date.now()) / 1000
      deadline.current = Number.isFinite(secs) ? Date.now() + secs * 1000 : 0
      setLeft(Number.isFinite(secs) ? Math.max(0, Math.round(secs)) : null)
    }).catch(e => { if (live) setErr(e.message || String(e)) })
    return () => { live = false }
  }, [gen, mode])
  useEffect(() => {
    if (!deadline.current) return undefined
    const t = setInterval(() => setLeft(Math.max(0, Math.round((deadline.current - Date.now()) / 1000))), 1000)
    return () => clearInterval(t)
  }, [res])

  const mine = res && pairState && (!pairState.id || !res.pair?.id || pairState.id === res.pair.id)
  const bound = !!(mine && pairState.bound)
  const expired = !bound && left === 0
  const revoke = async () => {
    setBusy(true)
    try { await invPairRevoke(); onClose?.() } catch (e) { setErr(e.message || String(e)) } finally { setBusy(false) }
  }
  const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  const newCode = () => { setMode('create'); setGen(n => n + 1) }

  if (mode === 'status') {
    const on = !!(pairState && pairState.bound)
    return (
      <Modal title="手机当相机" onClose={onClose} width={440}
        footer={<>
          {on && <button type="button" className="btn" disabled={busy} onClick={revoke}>断开</button>}
          <button type="button" className="btn" disabled={busy} onClick={newCode}>{on ? '换一部手机' : '生成配对码'}</button>
          <button type="button" className="btn primary" onClick={onClose}>好了</button>
        </>}>
        <div className="inv-pair">
          {err && <div className="banner err">{err}</div>}
          {on ? <>
            <div className="inv-pair-st ok">已连接：<b>{pairState.dtName || '（未识别钉钉身份）'}</b>{pairState.device ? <span className="inv-muted"> · {pairState.device}</span> : null}</div>
            {pairState.active === false
              ? <div className="inv-muted">手机{pairState.lastSeen ? `最近一次在线 ${fmtTime(pairState.lastSeen)}，` : ''}现在没在用（锁屏或关了页面）。要拍照时在手机钉钉里重新打开那个页面即可，不用重新配对。</div>
              : <div className="inv-muted">手机在线，拍的票会自动出现在当前票夹里。</div>}
            {pairState.sessionExpires ? <div className="inv-muted">本次配对到 {fmtTime(pairState.sessionExpires)} 失效</div> : null}
            <div className="inv-pair-note">要换一部手机，点「换一部手机」生成新配对码——原来这部手机会随即断开。</div>
          </> : <div className="inv-pair-st">手机已经断开了（过期或在别处断开）。点「生成配对码」重新配对。</div>}
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="手机当相机" onClose={onClose} width={440}
      footer={<>
        {(expired || err) && <button type="button" className="btn" onClick={() => setGen(n => n + 1)}>重新生成配对码</button>}
        <button type="button" className="btn" disabled={busy || !res} onClick={revoke}>断开</button>
        <button type="button" className="btn primary" onClick={onClose}>{bound ? '好了' : '关闭'}</button>
      </>}>
      <div className="inv-pair">
        {err && <div className="banner err">{err}</div>}
        {!res && !err && <div className="loading"><span className="inv-spin" />&nbsp;正在生成配对码…</div>}
        {res && <>
          <div className={'inv-pair-qr' + (expired ? ' dim' : '')}>
            {res.qr ? <img src={res.qr} alt="配对二维码" /> : <span className="inv-muted">配对码图片没生成出来</span>}
            {expired && <span className="inv-pair-exp">已过期</span>}
          </div>
          <div className={'inv-pair-st' + (bound ? ' ok' : '')}>
            {bound
              ? <>已连接：<b>{pairState.dtName || '（未识别钉钉身份）'}</b>{pairState.device ? <span className="inv-muted"> · {pairState.device}</span> : null}</>
              : expired ? '配对码已过期，点「重新生成配对码」' : <><span className="inv-spin" />&nbsp;等待手机用钉钉扫码</>}
          </div>
          {!bound && left !== null && !expired && <div className="inv-muted">配对码 {mmss(left)} 内有效，只能扫一次</div>}
          <ol className="inv-pair-how">
            <li>打开手机钉钉，用首页的「扫一扫」扫上面的码</li>
            <li>手机上出现「扫审批单」「拍发票」两个按钮，就连上了</li>
            <li>手机上的人要和电脑上登录的是同一个人（工作台账号需先在设置里绑好钉钉）</li>
          </ol>
          <div className="inv-muted">{res.httpsHint || '手机上点「拍发票」会直接调起手机相机，不用装任何东西。'}</div>
        </>}
      </div>
    </Modal>
  )
}

// ───────────────────────── 选人（钉钉花名册） ─────────────────────────

let _rosterP = null   // 同一页面多个选人框共用一次拉取（后端另有 30 分钟缓存）

/**
 * 按名字搜人（钉钉花名册）。第一次获得焦点时才拉 invRoster()；打名字过滤，下拉每行「姓名｜岗位｜部门」；
 * 上下键选、回车确定、Esc 收起；× 清空（onChange(null)）。花名册拉不到时把后端原因（如钉钉未配置、没开通讯录权限）显示在框下。
 * @param {object} p
 * @param {{userid:string,name:string,title?:string,dept?:string}|string|null} p.value 已选的人（或只有名字的字符串）
 * @param {(person: {userid:string,name:string,title:string,dept:string}|null) => void} p.onChange
 * @param {string} [p.placeholder='打名字搜人']
 */
export function PersonPicker({ value, onChange, placeholder = '打名字搜人' }) {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const [loading, setLoading] = useState(false)
  const shown = typeof value === 'string' ? value : (value?.name || '')

  const load = () => {
    if (rows || loading) return
    setLoading(true); setErr('')
    if (!_rosterP) _rosterP = invRoster().then(r => (r && r.rows) || [])
    _rosterP.then(list => setRows(list))
      .catch(e => { _rosterP = null; setErr(e.message || String(e)) })   // 失败不缓存，下次聚焦重试
      .finally(() => setLoading(false))
  }
  const list = useMemo(() => {
    if (!rows) return []
    const k = q.trim()
    return (k ? rows.filter(r => (r.name || '').includes(k)) : rows).slice(0, 30)
  }, [rows, q])
  useEffect(() => { setHi(0) }, [q])

  const pick = (r) => {
    onChange?.(r ? { userid: r.userid, name: r.name, title: r.title || '', dept: r.dept || '' } : null)
    setQ(''); setOpen(false)
  }
  const onKey = (e) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi(h => Math.min(h + 1, list.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(h => Math.max(h - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (list[hi]) pick(list[hi]) }
    else if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false) }
  }
  return (
    <div className="inv-pp">
      <div className="inv-pp-box">
        <input className="inv-in" value={open ? q : shown} placeholder={shown || placeholder}
          onFocus={() => { load(); setOpen(true); setQ('') }}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onChange={e => { setQ(e.target.value); setOpen(true) }}
          onKeyDown={onKey} />
        {shown && !open && <button type="button" className="inv-pp-x" title="清空" onMouseDown={e => e.preventDefault()} onClick={() => pick(null)}>×</button>}
      </div>
      {open && (rows || loading) && <div className="inv-pp-dd" role="listbox">
        {loading && <div className="inv-pp-empty"><span className="inv-spin" />&nbsp;正在拉通讯录…</div>}
        {!loading && !list.length && <div className="inv-pp-empty">{q ? `没有叫「${q}」的人` : '通讯录是空的'}</div>}
        {list.map((r, i) => <div key={r.userid || i} role="option" aria-selected={i === hi}
          className={'inv-pp-row' + (i === hi ? ' on' : '')}
          onMouseDown={e => { e.preventDefault(); pick(r) }} onMouseEnter={() => setHi(i)}>
          <b>{r.name}</b><span>｜{r.title || '—'}｜{r.dept || '—'}</span>
        </div>)}
      </div>}
      {err && <div className="inv-pp-err">拉不到通讯录：{err}</div>}
    </div>
  )
}
