// 「刷新全部金蝶凭证」的进度浮窗（V2.968）：挂在 App 外壳上，换页面也还在；窗口正中一张小卡片（不挡别处点击），可收起成右下角小条。
// 只有点刷新的这个标签页弹卡片（sessionStorage 记着，刷新浏览器也接得上）；别人点的只在台账按钮上看到进度。
// 进度只在这里轮询一份，台账页用 useVouchersJob() 跟着变（查到新的就重读当前页）。
import React, { useEffect, useRef, useState } from 'react'
import { invVouchersJob } from '../api.js'
import './inv-job.css'

const MINE = 'inv-vouchers-job-mine'
const EV = 'inv-vouchers-job'
const START = 'inv-vouchers-job-start'
let last = null

const isMine = () => { try { return sessionStorage.getItem(MINE) === '1' } catch { return false } }
const setMine = (on) => { try { if (on) sessionStorage.setItem(MINE, '1'); else sessionStorage.removeItem(MINE) } catch { /* 无痕模式记不住就算了 */ } }
const publish = (job) => { last = job; window.dispatchEvent(new CustomEvent(EV, { detail: job })) }

// 台账页点了「刷新全部金蝶凭证」、接口回了 job → 告诉浮窗开始盯
export function announceStart(job) {
  setMine(true)
  publish(job)
  window.dispatchEvent(new CustomEvent(START, { detail: job }))
}

// 当前进度（没有就是 null）
export function useVouchersJob() {
  const [job, setJob] = useState(last)
  useEffect(() => {
    const f = (e) => setJob(e.detail)
    window.addEventListener(EV, f)
    return () => window.removeEventListener(EV, f)
  }, [])
  return job
}

export default function InvVoucherJob({ enabled }) {
  const [job, setJob] = useState(null)
  const [show, setShow] = useState(false)        // 这个标签页要不要出卡片
  const [open, setOpen] = useState(true)         // 展开（正中卡片）还是收起（右下角小条）
  const [done, setDone] = useState(null)         // 查完了的那一份，显示结果
  const timer = useRef(null)
  const running = !!(job && job.running)

  const poll = async () => {
    try {
      const r = await invVouchersJob()
      const j = r && r.job
      if (!j) return
      publish(j)
      setJob(j)
      if (!j.running && isMine()) { setMine(false); setDone(j); setOpen(true) }
    } catch { /* 下一轮再看 */ }
  }

  useEffect(() => {
    if (!enabled) return undefined
    poll().then(() => { if (isMine()) setShow(true) })
    const onStart = (e) => { setJob(e.detail); setDone(null); setShow(true); setOpen(true) }
    window.addEventListener(START, onStart)
    return () => window.removeEventListener(START, onStart)
  }, [enabled])

  useEffect(() => {
    if (!running) return undefined
    timer.current = setInterval(poll, 1500)
    return () => clearInterval(timer.current)
  }, [running])

  // 查完的结果放 10 秒自己收掉
  useEffect(() => {
    if (!done) return undefined
    const t = setTimeout(() => { setDone(null); setShow(false) }, 10000)
    return () => clearTimeout(t)
  }, [done])

  if (!enabled || !show || (!running && !done)) return null
  const j = done || job
  const total = j.total || 0
  const pct = total ? Math.min(100, Math.round((j.checked || 0) * 100 / total)) : 4
  const split = (j.ledgerN || j.auditN) ? `台账 ${j.ledgerN || 0} 张 ＋ 还在审核的 ${j.auditN || 0} 张` : ''

  if (!open && !done) {
    return <button type="button" className="ivj-pill" onClick={() => setOpen(true)} title="点开看进度">
      <span className="ivj-spin sm" /> 金蝶凭证 {j.checked || 0}/{total || '…'}
    </button>
  }
  const finished = !!done
  return <div className="ivj-wrap" role="status" aria-live="polite">
    <div className={'ivj-card' + (finished ? (j.error ? ' bad' : ' ok') : '')}>
      <div className="ivj-icon">{finished ? (j.error ? '!' : '✓') : <span className="ivj-spin" />}</div>
      <div className="ivj-title">{finished ? (j.error ? '查到一半停了' : '金蝶凭证查完了') : '正在去金蝶找凭证'}</div>
      {finished
        ? <div className="ivj-res">{j.error || `${total} 张里对上 ${j.matched || 0} 张，这次有变化的 ${j.changed || 0} 张`}</div>
        : <div className="ivj-num"><b>{j.checked || 0}</b> / {total || '…'} 张</div>}
      <div className={'ivj-bar' + (finished ? ' end' : '')}><i style={{ width: (finished ? 100 : pct) + '%' }} /></div>
      {split && <div className="ivj-sub">{split}<br />{!finished && j.matched ? `已对上 ${j.matched} 张 · ` : ''}普票不入账，不查</div>}
      {!finished && <div className="ivj-hint">可以去别的页面，查完会在这里告诉你</div>}
      <div className="ivj-act">
        {finished
          ? <button type="button" className="btn" onClick={() => { setDone(null); setShow(false) }}>知道了</button>
          : <button type="button" className="btn" onClick={() => setOpen(false)}>收起</button>}
      </div>
    </div>
  </div>
}
