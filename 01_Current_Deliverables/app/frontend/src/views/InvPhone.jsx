// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 手机配对页：钉钉扫配对码打开 → 认人绑定 → 「扫审批单」「拍发票」→ 实时看当前票夹（需求确认书五「手机＝扫码枪＋相机」、技术方案 §5.2 手机端）
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 配对成功后换用后端新发的会话令牌（配对码当场作废、只能扫一次），
//   配对码被用过（409）/ 要用钉钉扫（403）按原话停下；没有缩略图的文件直接显示「无预览」，不再每 30 秒去取一次必然 404 的图。
// main.jsx 按 #/invpair 分流到这里，不走工作台登录：身份全靠配对令牌（请求头 X-Inv-Pair）。
// 令牌只从地址里读一次就收进 sessionStorage 并把地址改回 #/invpair——免得令牌留在浏览记录/截图里。
// 配对（m/bind）成功时后端回一个新的会话令牌 session：从此所有调用都用它，二维码里的那个令牌当场失效。
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { invMHello, invMBind, invMState, invMScan, invMUpload, invMJsConfig } from '../api.js'
import { inDingTalk, getAuthCode, ddConfig, ddCall } from './ddBridge.js'
import { money, usePoll, StatusBadges } from './invShared.jsx'
import './inv-phone.css'

// ───────────────────────── 令牌 ─────────────────────────

const TOKEN_KEY = 'inv_pair_t'
let _token = null   // 模块级只读一次（React 严格模式会把初始化跑两遍，第二遍时地址已经改掉了）
function readToken() {
  if (_token !== null) return _token
  const m = (window.location.hash || '').match(/^#\/invpair\?(.*)$/)
  let t = m ? (new URLSearchParams(m[1]).get('t') || '') : ''
  if (t) { try { sessionStorage.setItem(TOKEN_KEY, t) } catch { /* 存不了就只在本次页面里用 */ } }
  else { try { t = sessionStorage.getItem(TOKEN_KEY) || '' } catch { t = '' } }
  if (m) { try { window.history.replaceState(null, '', '#/invpair') } catch { /* 忽略 */ } }
  _token = t
  return t
}
// 配对成功换上会话令牌：模块缓存和 sessionStorage 一起换（刷新页面、从相机回来都用新的）
function saveToken(t) {
  if (!t) return
  _token = t
  try { sessionStorage.setItem(TOKEN_KEY, t) } catch { /* 存不了就只在本次页面里用 */ }
}

const EXPIRED_TEXT = '配对已过期，请在电脑上重新点「手机当相机」'
const USED_TEXT = '这个配对码已经用过了，请在电脑上重新点「手机当相机」'
// 后端把"令牌过期/被吊销/不存在"回成 401/403 + 中文原因；api.js 的错误对象不带状态码，只能看字眼
function looksExpired(e) {
  const st = e && (e.status || (e.body && e.body.status))
  if (st === 401 || st === 410) return true
  // 登录门拒了令牌会回「未登录」——手机页没有登录这回事，等同于配对失效
  return /过期|吊销|重新配对|未登录|未配对|配对.{0,8}(失效|无效|不存在|断开)/.test(String((e && e.message) || ''))
}

// ───────────────────────── 钉钉 JSAPI ─────────────────────────

// 钉钉「扫一扫」要先 dd.config 鉴权（后端按本页地址签名）；成功 → 签名参数（含 corpId），失败抛带原因的错误
let _ddCfgP = null
function ddSetup(token) {
  if (_ddCfgP) return _ddCfgP
  _ddCfgP = ddConfig(url => invMJsConfig(token, url), ['biz.util.scan', 'runtime.permission.requestAuthCode'])
  _ddCfgP.catch(() => { _ddCfgP = null })   // 失败了下次点按钮再试
  return _ddCfgP
}
function ddScan() {
  const dd = window.dd
  const fn = dd && dd.biz && dd.biz.util && dd.biz.util.scan
  if (!fn) return Promise.reject(new Error('当前环境不能调钉钉扫码'))
  return ddCall(fn, { type: 'qrCode' }, x => (x && (x.text || x.content || x.result)) || '')
}
const isCancel = e => /cancel|取消/i.test(String((e && (e.errorMessage || e.message)) || '')) || (e && String(e.errorCode) === '300001')

// ───────────────────────── 缩略图（要带令牌头，不能直接 <img src>） ─────────────────────────

/**
 * 手机上一张票的缩略图地址（要带令牌头取）。后端没给 file.thumb（xlsx/压缩包/没渲染出来的 PDF 等）＝这张没有缩略图，回 null，
 * 页面直接显示「无预览」——别去拼 /api/inv/m/file/<id>?v=t 硬取，那必然 404，还会跟着轮询一遍遍重试。
 * @param {object} it Item（手机端视图）
 * @returns {{url:string, key:string}|null}
 */
export function thumbOf(it) {
  const f = it && it.file
  if (!f || !f.thumb) return null
  // 后端 m/state 一般直接给手机端地址；万一给了电脑端地址，改走手机端（电脑端要登录 cookie，手机上没有）
  const t = String(f.thumb)
  const pm = /[?&]page=(\d+)/.exec(t)    // 合并文件里第 N 张票的缩略图带着页码，换地址时别丢
  const url = t.indexOf('/api/inv/m/') === 0 ? t
    : (f.id ? '/api/inv/m/file/' + f.id + '?v=t' + (pm ? '&page=' + pm[1] : '') : null)
  return url ? { url, key: url + '|' + (f.rotation || 0) } : null
}
const THUMB_RETRY_MS = 30000   // 取图失败后 30 秒内不重试
const THUMB_MAX_TRIES = 3      // 同一张最多取 3 次，再不行就显示「无预览」不再打后端
function useThumbs(token, items) {
  const cache = useRef(new Map())      // key → objectURL
  const failed = useRef(new Map())     // key → {at: 最近失败时刻, n: 失败次数}
  const inflight = useRef(new Set())
  const alive = useRef(true)
  const [, bump] = useState(0)
  useEffect(() => {
    const want = new Map()
    items.forEach(it => { const t = thumbOf(it); if (t) want.set(t.key, t.url) })
    // 不再显示的图立刻释放，手机内存小
    for (const [k, u] of cache.current) if (!want.has(k)) { URL.revokeObjectURL(u); cache.current.delete(k) }
    want.forEach((url, key) => {
      if (cache.current.has(key) || inflight.current.has(key)) return
      const ft = failed.current.get(key)
      if (ft && (ft.n >= THUMB_MAX_TRIES || Date.now() - ft.at < THUMB_RETRY_MS)) return
      inflight.current.add(key)
      fetch(url, { headers: { 'X-Inv-Pair': token }, cache: 'no-store' })
        .then(r => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
        .then(b => {
          if (!alive.current) return
          cache.current.set(key, URL.createObjectURL(b)); failed.current.delete(key); bump(n => n + 1)
        })
        .catch(() => {
          const prev = failed.current.get(key)
          failed.current.set(key, { at: Date.now(), n: (prev ? prev.n : 0) + 1 })
          if (alive.current) bump(n => n + 1)
        })
        .finally(() => { inflight.current.delete(key) })
    })
  }, [token, items])
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      for (const u of cache.current.values()) URL.revokeObjectURL(u)
      cache.current.clear()
    }
  }, [])
  // get(key) → objectURL；failed(key) → 取过没取到（显示「无预览」，不再显示「加载中」）
  return { get: k => cache.current.get(k), failed: k => !!failed.current.get(k) }
}

// ───────────────────────── 小组件 ─────────────────────────

const TONE = { folder: 'ok', item: 'ok', confirm: 'ok', dup: 'err', noFolder: 'warn', unknown: 'warn', error: 'err', failed: 'err' }

function Shell({ children, sub }) {
  return (
    <div className="inv-ph">
      <div className="inv-ph-top">
        <div className="inv-ph-title">手机收票</div>
        {sub && <div className="inv-ph-sub">{sub}</div>}
      </div>
      {children}
    </div>
  )
}

function StopPage({ title, text, onRetry }) {
  return (
    <Shell>
      <div className="inv-ph-stop">
        <div className="inv-ph-stop-t">{title}</div>
        {text && <div className="inv-ph-stop-x">{text}</div>}
        {onRetry && <button type="button" className="inv-ph-btn2" onClick={onRetry}>重新试一次</button>}
      </div>
    </Shell>
  )
}

function FolderCard({ folder }) {
  if (!folder) {
    return <div className="inv-ph-folder none">
      <b>电脑上还没有打开票夹</b>
      <span>先点「扫审批单」，扫钉钉审批单上的二维码</span>
    </div>
  }
  const s = folder.stats || {}
  return (
    <div className="inv-ph-folder">
      <div className="inv-ph-folder-k">当前票夹</div>
      <b className="inv-ph-folder-t">{folder.title || folder.businessId || ('票夹 #' + folder.id)}</b>
      <div className="inv-ph-folder-m">
        {folder.businessId && <span>{folder.businessId}</span>}
        {folder.applicant && <span>申请人 {folder.applicant}</span>}
        {folder.amount !== null && folder.amount !== undefined && <span>单据 {money(folder.amount)}</span>}
      </div>
      <div className="inv-ph-folder-m">
        <span>已收 {s.invoices ?? 0} 张票</span>
        {!!s.processing && <span className="info">识别中 {s.processing}</span>}
        {!!s.dup && <span className="err">重复 {s.dup}</span>}
      </div>
    </div>
  )
}

function ItemGrid({ items, thumbs }) {
  if (!items.length) return <div className="inv-ph-empty">这个票夹里还没有票，拍一张试试</div>
  return (
    <div className="inv-ph-grid">
      {items.map(it => {
        const t = thumbOf(it)
        const src = t ? thumbs.get(t.key) : null
        const ph = t ? (thumbs.failed(t.key) ? '无预览' : '加载中') : (it.file ? '无预览' : '仅二维码')
        return (
          <div key={it.id} className={'inv-ph-item' + (it.flags && it.flags.dup ? ' dup' : '')}>
            <div className="inv-ph-item-img">
              {src ? <img src={src} alt="" /> : <span>{ph}</span>}
            </div>
            <div className="inv-ph-item-amt">{it.kind === 'other' ? '非发票' : money(it.total)}</div>
            <StatusBadges item={it} />
          </div>
        )
      })}
    </div>
  )
}

// ───────────────────────── 页面 ─────────────────────────

export default function InvPhone() {
  const [token, setToken] = useState(readToken)  // 配对前是二维码里的令牌，配对成功后换成后端新发的会话令牌
  const tokRef = useRef(token)                    // 调接口一律读它（配对成功时先于 state 换上会话令牌）
  const [phase, setPhase] = useState('hello')    // hello → binding → ready；或 expired / used / fail / blocked / notoken
  const [failMsg, setFailMsg] = useState('')
  const [who, setWho] = useState({ user: '', dtName: '' })
  const [idNote, setIdNote] = useState('')
  const [st, setSt] = useState(null)             // invMState 结果
  const [netErr, setNetErr] = useState('')
  const [msg, setMsg] = useState(null)           // {tone, text, sub}
  const [busy, setBusy] = useState('')           // '' | 'scan' | 'upload'
  const [scanFallback, setScanFallback] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const scanPhoto = useRef(null)
  const invPhoto = useRef(null)

  const expire = useCallback(() => { setPhase('expired') }, [])

  // 打开 → 问候 → （钉钉里取免登码）→ 绑定。只在打开/点「重新试一次」时跑；配对成功换令牌不重跑
  useEffect(() => {
    const tk = tokRef.current
    if (!tk) { setPhase('notoken'); return undefined }
    let live = true
    ;(async () => {
      setPhase('hello'); setFailMsg('')
      let hello
      try { hello = (await invMHello(tk)) || {} } catch (e) {
        if (!live) return
        if (looksExpired(e)) { setFailMsg(e.message || ''); setPhase('expired') } else { setFailMsg(e.message || String(e)); setPhase('fail') }
        return
      }
      if (!live) return
      if (hello.bound) {   // 刷新页面/从相机回来：已经绑过，直接用
        setWho({ user: hello.user || '', dtName: hello.dtName || '' })
        setPhase('ready'); return
      }
      setPhase('binding')
      let code = ''
      let note = ''
      if (inDingTalk()) {
        // 先 dd.config 鉴权再要免登码：不鉴权时有的钉钉版本不给码（V2.621 修手机认不出人）
        let cid = hello.corpId || ''
        try { const c = await ddSetup(tk); if (!cid && c && c.corpId) cid = c.corpId } catch { /* 鉴权不过也照样试免登 */ }
        if (cid) {
          try { code = await getAuthCode(cid) } catch { code = '' }
        }
        if (!code) note = '未识别钉钉身份：钉钉没给出你是谁，照样可以拍；登记人记为电脑上登录的人。'
      } else {
        note = '请用钉钉扫码打开：现在是普通浏览器，系统认不出你是谁。'
      }
      if (!live) return
      try {
        const r = (await invMBind(tk, { code: code || undefined, device: (navigator.userAgent || '').slice(0, 150) })) || {}
        // 配对码当场作废：换上会话令牌（页面被切走也先存下，免得回来拿着作废的码）
        if (r.session) { saveToken(r.session); tokRef.current = r.session }
        if (!live) return
        if (r.session) setToken(r.session)
        setWho({ user: r.user || hello.user || '', dtName: r.dtName || '' })
        if (!r.dtName) setIdNote([note, r.msg].filter(Boolean).join(' '))
        else setIdNote('')
        setPhase('ready')
      } catch (e) {
        if (!live) return
        // 409：这个配对码已经被扫过（只能扫一次）——重试没用，要回电脑重新生成
        if (e.status === 409) { setFailMsg(e.message || USED_TEXT); setPhase('used'); return }
        if (looksExpired(e)) { setFailMsg(e.message || ''); setPhase('expired'); return }
        // 人对不上 / 必须用钉钉扫（403）等：硬停，原话给人看
        const m = e.message || String(e)
        setFailMsg(m + (inDingTalk() || /扫一扫/.test(m) ? '' : '（请用手机钉钉的「扫一扫」扫电脑上的配对码）'))
        setPhase('blocked')
      }
    })()
    return () => { live = false }
  }, [attempt])

  // 连上后先在后台把钉钉扫码鉴权做了：第一次点「扫审批单」就能直接出扫码框
  useEffect(() => {
    if (phase === 'ready' && inDingTalk()) ddSetup(tokRef.current).catch(() => { /* 点按钮时再试、再说原因 */ })
  }, [phase])

  const loadState = useCallback(async () => {
    const token = tokRef.current
    try {
      const r = (await invMState(token)) || {}
      setSt(r); setNetErr('')
      if (r.user) setWho(w => (w.user ? w : { ...w, user: r.user }))
    } catch (e) {
      if (looksExpired(e)) expire()
      else setNetErr('网络不稳，正在重连…（' + (e.message || e) + '）')
    }
  }, [expire])
  usePoll(loadState, phase === 'ready', 2000)
  // 钩子必须在下面各种"提前返回"之前调用
  const items = useMemo(() => (st && Array.isArray(st.items) ? st.items.slice(0, 12) : []), [st])
  const thumbs = useThumbs(token, items)

  const showResult = (r, fallbackText) => {
    const act = r && r.action
    const tone = TONE[act] || 'info'
    let text = (r && r.msg) || fallbackText
    if (act === 'noFolder') text = '先扫审批单，再拍发票'
    setMsg({ tone, text, sub: act === 'noFolder' && r.msg && r.msg !== text ? r.msg : '' })
    if (act === 'dup' && navigator.vibrate) { try { navigator.vibrate([200, 80, 200]) } catch { /* 不支持震动 */ } }
  }
  const handleErr = (e, what) => {
    if (looksExpired(e)) { expire(); return }
    setMsg({ tone: 'err', text: what + '没成功：' + (e.message || e) })
  }

  const doUpload = async (files, purpose) => {
    if (!files.length) return
    setBusy('upload')
    setMsg({ tone: 'info', text: purpose === 'scan' ? '正在读照片里的二维码…' : `正在上传 ${files.length} 张…` })
    try {
      const r = (await invMUpload(tokRef.current, files, purpose)) || {}
      const list = Array.isArray(r.results) ? r.results : []
      if (list.length === 1) showResult(list[0], purpose === 'scan' ? '已读码' : '已收到')
      else if (list.length > 1) {
        const bad = list.filter(x => (TONE[x.action] || 'info') !== 'ok')
        setMsg({ tone: bad.some(x => x.action === 'dup') ? 'err' : bad.length ? 'warn' : 'ok',
          text: `收到 ${list.length} 张` + (bad.length ? `，其中 ${bad.length} 张要注意` : ''),
          sub: bad.map(x => (x.name ? x.name + '：' : '') + (x.msg || x.action)).join('；') })
        if (bad.some(x => x.action === 'dup') && navigator.vibrate) { try { navigator.vibrate([200, 80, 200]) } catch { /* 忽略 */ } }
      } else setMsg({ tone: 'ok', text: '已上传' })
      await loadState()
    } catch (e) {
      const list = e.body && Array.isArray(e.body.results) ? e.body.results : []
      if (list.length === 1 && !looksExpired(e)) showResult(list[0], e.message)
      else handleErr(e, '上传')
    } finally { setBusy('') }
  }

  const scanApproval = async () => {
    setScanFallback(false)
    if (inDingTalk()) {
      let text = ''
      try {
        await ddSetup(tokRef.current)          // 先鉴权，钉钉才让网页调「扫一扫」
        text = await ddScan()
      } catch (e) {
        if (isCancel(e)) return
        // 鉴权没过/钉钉不给扫：说清原因，改成拍一张、让系统从照片里读码
        const why = (e && (e.errorMessage || e.message)) || ''
        setScanFallback(why || true)
        return
      }
      if (!text) return
      setBusy('scan'); setMsg({ tone: 'info', text: '正在认码…' })
      try { showResult((await invMScan(tokRef.current, text)) || {}, '已识别'); await loadState() } catch (e) { handleErr(e, '扫码') } finally { setBusy('') }
      return
    }
    // 不在钉钉（或组件没加载）：直接拍照读码
    scanPhoto.current && scanPhoto.current.click()
  }

  if (phase === 'notoken') {
    return <StopPage title="没有配对码" text="请在电脑的「收票工作台」点「手机当相机」，再用手机钉钉的「扫一扫」扫屏幕上的码。" />
  }
  if (phase === 'expired') return <StopPage title={EXPIRED_TEXT} text={failMsg && failMsg !== EXPIRED_TEXT ? failMsg : '配对码几分钟内有效、只能扫一次；连上后 12 小时内有效。'} />
  if (phase === 'used') return <StopPage title={USED_TEXT} text={failMsg && failMsg !== USED_TEXT ? failMsg : '配对码只能扫一次：已经有一部手机用它连上了。换手机或重新连，请在电脑上重新生成配对码。'} />
  if (phase === 'blocked') return <StopPage title="配不上" text={failMsg} onRetry={() => setAttempt(n => n + 1)} />
  if (phase === 'fail') return <StopPage title="没连上电脑" text={failMsg} onRetry={() => setAttempt(n => n + 1)} />
  if (phase === 'hello' || phase === 'binding') {
    return <Shell>
      <div className="inv-ph-wait"><span className="inv-spin" />&nbsp;{phase === 'hello' ? '正在连电脑…' : '正在确认你的身份…'}</div>
      {!inDingTalk() && <div className="inv-ph-note">请用钉钉扫码打开</div>}
    </Shell>
  }

  const folder = st ? st.folder : null
  const whoText = who.dtName ? `已连接：${who.dtName}` : (who.user ? `已连接：电脑账号 ${who.user}` : '已连接')
  return (
    <Shell sub={whoText}>
      {idNote && <div className="inv-ph-note">{idNote}</div>}
      {netErr && <div className="inv-ph-net">{netErr}</div>}
      {st ? <FolderCard folder={folder} /> : <div className="inv-ph-folder none"><span><span className="inv-spin" />&nbsp;正在读电脑上的票夹…</span></div>}
      <div className="inv-ph-btns">
        <button type="button" className="inv-ph-big scan" disabled={!!busy} onClick={scanApproval}>扫审批单</button>
        <button type="button" className="inv-ph-big shoot" disabled={!!busy} onClick={() => invPhoto.current && invPhoto.current.click()}>拍发票</button>
      </div>
      {st && !folder && <div className="inv-ph-hint">拍发票前先扫审批单，票才知道放进哪张单</div>}
      {scanFallback && (
        <div className="inv-ph-fb">
          钉钉扫码暂时用不了{typeof scanFallback === 'string' ? `（${scanFallback}）` : ''}，改成拍照读码：对准审批单上的二维码拍一张。
          <button type="button" className="inv-ph-btn2" onClick={() => scanPhoto.current && scanPhoto.current.click()}>拍审批单二维码</button>
        </div>
      )}
      {msg && <div className={'inv-ph-msg ' + msg.tone} role="status">
        {busy && <span className="inv-spin" />}{msg.text}{msg.sub && <span className="inv-ph-msg-sub">{msg.sub}</span>}
      </div>}
      <input ref={invPhoto} type="file" accept="image/*" capture="environment" multiple hidden
        onChange={e => { const fs = [...(e.target.files || [])]; e.target.value = ''; doUpload(fs, 'invoice') }} />
      <input ref={scanPhoto} type="file" accept="image/*" capture="environment" hidden
        onChange={e => { const fs = [...(e.target.files || [])].slice(0, 1); e.target.value = ''; doUpload(fs, 'scan') }} />
      <div className="inv-ph-sec">最近收的票{items.length ? `（${items.length}）` : ''}</div>
      {st ? <ItemGrid items={items} thumbs={thumbs} /> : <div className="inv-ph-empty"><span className="inv-spin" />&nbsp;正在读票夹…</div>}
    </Shell>
  )
}
