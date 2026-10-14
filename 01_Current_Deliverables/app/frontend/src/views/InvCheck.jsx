// [Change Log] Date: 2026-10-14 | Author: Claude Opus 5.5 | Version: V2.960（发票管家·对纸质票）
// 用户 2026-10-14 定：月末准备认证时有一堆票夹，要确定手上的纸质票和选中的票夹是不是一样的。
// 选中几个票夹开一次「点验」，把纸质票连续扫一遍（电脑扫码枪、手机都行，可以同时扫）：扫到的那一行变绿，没扫到的一直是白的；
// 同一张又扫了＝重复；不在选中票夹里的 / 系统里没有的只提醒（红字写明在哪），不登记也不挪。没有二维码的票手工勾。
// 入口放在收票工作台和发票审核两处（PaperCheckButton）；从发票审核进来的，扫完可以直接「审核扫到的」。
// 手机扫的记到同一次点验上：这里每 2 秒重读一次，手机扫到的那一行跟着变绿。
import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  invCheckStart, invCheckStartList, invCheckCurrent, invCheckGet, invCheckScan, invCheckMark, invCheckClose, invCheckExportUrl, invAuditItemsApprove,
  invTickLists,
} from '../api.js'
import { money, fmtTime, usePoll, Modal, ScanInput } from './invShared.jsx'
import './inv-check.css'

const errText = (e) => (e && (e.message || e.msg)) || String(e || '未知原因')
const SRC_CN = { dingtalk: '钉钉接入', scan: '收票台', manual: '收票台', later: '后补单', logistics: '物流对账' }

// 提示音：对上了一声短的，不对的两声低的（电脑上不念字，手机上才念）
function beep(ok) {
  try {
    const AC = window.AudioContext || window.webkitAudioContext
    if (!AC) return
    const ctx = beep.ctx || (beep.ctx = new AC())
    const tone = (f, at, len) => {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.frequency.value = f; o.connect(g); g.connect(ctx.destination)
      g.gain.setValueAtTime(0.12, ctx.currentTime + at); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + at + len)
      o.start(ctx.currentTime + at); o.stop(ctx.currentTime + at + len)
    }
    if (ok) tone(1040, 0, 0.12)
    else { tone(300, 0, 0.18); tone(240, 0.22, 0.22) }
  } catch { /* 没声音不影响点验 */ }
}

// 念出来（整理纸质票时不用看屏幕）：确认 / 重复 / 9 月的 / 还没做账 / 没有这张。浏览器不给念就只有提示音
function speak(text) {
  try {
    if (!text || !window.speechSynthesis || !window.SpeechSynthesisUtterance) return
    const u = new window.SpeechSynthesisUtterance(text)
    u.lang = 'zh-CN'; u.rate = 1.3
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u)
  } catch { /* 念不了不影响点验 */ }
}

// ───────────────────────── 开始：勾要对的票夹 ─────────────────────────

function CheckStart({ candidates, preset, entry, onClose, onStarted }) {
  const rows = candidates || []
  // 两种对法：选票夹（这几个票夹里的票）/ 选一份认证清单（这个月要认证的票，按凭证排）
  const [mode, setMode] = useState(rows.length ? 'folders' : 'list')
  const [lists, setLists] = useState(null)
  const [listId, setListId] = useState(null)
  useEffect(() => {
    let live = true
    invTickLists().then(r => { if (live) { const l = Array.isArray(r?.rows) ? r.rows : []; setLists(l); if (l.length) setListId(x => x || l[0].id) } }).catch(() => { if (live) setLists([]) })
    return () => { live = false }
  }, [])
  const [sel, setSel] = useState(() => new Set((preset && preset.length ? preset : rows.map(r => r.id)).filter(id => rows.some(r => r.id === id))))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const all = rows.length > 0 && sel.size === rows.length
  const toggle = (id, v) => setSel(s => { const n = new Set(s); if (v) n.add(id); else n.delete(id); return n })
  const go = async () => {
    if (mode === 'list') {
      if (!listId) { setErr('先选一份认证清单'); return }
      setBusy(true); setErr('')
      try { onStarted(await invCheckStartList(listId, entry)) } catch (e) { setErr(errText(e)); setBusy(false) }
      return
    }
    if (!sel.size) { setErr('先勾上要对的票夹'); return }
    setBusy(true); setErr('')
    try { onStarted(await invCheckStart([...sel], entry)) } catch (e) { setErr(errText(e)); setBusy(false) }
  }
  return (
    <Modal title="对纸质票：选要对的范围" onClose={onClose} width={620}
      footer={<>
        <span className="inv-muted">{mode === 'list' ? '' : `已选 ${sel.size} 个`}</span>
        <button type="button" className="btn" onClick={onClose}>取消</button>
        <button type="button" className="btn primary" disabled={busy || (mode === 'list' ? !listId : !sel.size)} onClick={go}>{busy ? '正在开始…' : '开始对'}</button>
      </>}>
      <div className="inv-ck-modes" role="tablist">
        <button type="button" role="tab" aria-selected={mode === 'folders'} className={mode === 'folders' ? 'on' : ''} onClick={() => { setMode('folders'); setErr('') }}>选票夹</button>
        <button type="button" role="tab" aria-selected={mode === 'list'} className={mode === 'list' ? 'on' : ''} onClick={() => { setMode('list'); setErr('') }}>选认证清单（按做账月份）</button>
      </div>
      {mode === 'list' && <div className="inv-ck-start">
        <div className="inv-muted">对着一份导出过的认证清单过纸质票：清单里的念「确认」；不在清单里的会念它是哪个月做账的（「9 月的」）或「还没做账」，好分到那个月那一摞。
          清单在 发票台账 › 抵扣勾选 › 方式一 导出。</div>
        {lists === null ? <div className="inv-ck-empty">正在取清单…</div>
          : !lists.length ? <div className="inv-ck-empty">还没有导出过认证清单。先到 发票台账 › 抵扣勾选 › 方式一，选做账月份导出一份。</div>
          : <div className="inv-ck-pick">
            {lists.map(l => <label key={l.id} className={'inv-ck-pickrow' + (listId === l.id ? ' on' : '')}>
              <input type="radio" name="inv-ck-list" checked={listId === l.id} onChange={() => setListId(l.id)} />
              <span className="inv-ck-pt" title={l.name}>{l.name}</span>
              <span className="inv-muted">{l.by} · {fmtTime(l.at)}</span>
              <b className="inv-num">{l.rows} 张</b>
            </label>)}
          </div>}
        {err && <div className="inv-ck-err">{err}</div>}
      </div>}
      {mode === 'folders' && <div className="inv-ck-start">
        <div className="inv-muted">勾上要对的票夹，然后把手上的纸质票连续扫一遍（电脑扫码枪、手机都行）。扫到的那一行变绿；不在这些票夹里的票只提醒，不登记。
          {entry === 'audit' ? '这里列的是左边队列里现在看得到的票夹。' : '这里列的是你最近经手的票夹。'}</div>
        {!rows.length ? <div className="inv-ck-empty">现在没有可以对的票夹。</div> : <>
          <label className="inv-ck-all"><input type="checkbox" checked={all} onChange={e => setSel(e.target.checked ? new Set(rows.map(r => r.id)) : new Set())} />全选（{rows.length}）</label>
          <div className="inv-ck-pick">
            {rows.map(r => <label key={r.id} className={'inv-ck-pickrow' + (sel.has(r.id) ? ' on' : '')}>
              <input type="checkbox" checked={sel.has(r.id)} onChange={e => toggle(r.id, e.target.checked)} />
              <span className="inv-ck-pt" title={r.title}>{r.payee || r.title || ('票夹 #' + r.id)}</span>
              <span className="inv-muted">{r.owner ? '归' + r.owner : ''}{r.invoices !== null && r.invoices !== undefined ? ` · ${r.invoices} 张票` : ''}</span>
              <b className="inv-num">{money(r.amount)}</b>
            </label>)}
          </div>
        </>}
        {err && <div className="inv-ck-err">{err}</div>}
      </div>}
    </Modal>
  )
}

// ───────────────────────── 点验中：连续扫，扫到的那一行变绿 ─────────────────────────

function CheckPanel({ first, entry, canAudit, onClose, onChanged }) {
  const [v, setV] = useState(first)
  const [alertMsg, setAlertMsg] = useState(null)       // {tone:'err'|'warn', text}
  const [flashId, setFlashId] = useState(null)
  const [onlyMiss, setOnlyMiss] = useState(false)
  const [busy, setBusy] = useState('')
  const [done, setDone] = useState(null)               // 「审核扫到的」的结果
  const cid = v.check.id
  const open = !!v.check.open
  const reload = useCallback(async () => { const r = await invCheckGet(cid); if (r && r.check) setV(r) }, [cid])
  usePoll(reload, open, 2000)        // 手机扫到的那一行跟着变绿

  const onScan = async (code) => {
    try {
      const r = (await invCheckScan(cid, code)) || {}
      speak(r.say)
      if (r.result === 'ok') { beep(true); setAlertMsg(null); setFlashId(r.item && r.item.id) }
      else if (r.result === 'dup') { beep(false); setAlertMsg({ tone: 'warn', text: r.msg || '这张扫过了' }); setFlashId(r.item && r.item.id) }
      else { beep(false); setAlertMsg({ tone: r.result === 'unknown' || r.result === 'closed' ? 'warn' : 'err', text: r.msg || '没对上' }) }
      await reload()
    } catch (e) { beep(false); setAlertMsg({ tone: 'err', text: '没扫成：' + errText(e) }) }
  }
  useEffect(() => {
    if (!flashId) return undefined
    const el = document.getElementById('inv-ck-row-' + flashId)
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' })
    const t = setTimeout(() => setFlashId(null), 1200)
    return () => clearTimeout(t)
  }, [flashId])

  const mark = async (it, on) => {
    try { await invCheckMark(cid, it.id, on); await reload() } catch (e) { setAlertMsg({ tone: 'err', text: errText(e) }) }
  }
  const finish = async () => {
    setBusy('close')
    try { const r = await invCheckClose(cid); if (r && r.check) setV(r); onChanged && onChanged() } catch (e) { setAlertMsg({ tone: 'err', text: errText(e) }) } finally { setBusy('') }
  }
  // 审核扫到的：一个票夹一个票夹送去审核通过（只送扫到了、还在待审的票）；过不了的照实列出来
  const hitPending = v.items.filter(i => i.hit && i.review === 'pending')
  const approveHits = async () => {
    if (!hitPending.length) return
    if (!window.confirm(`把扫到的 ${hitPending.length} 张待审的票审核通过？可否抵扣按系统建议判。`)) return
    setBusy('audit'); setDone(null)
    const by = {}
    hitPending.forEach(i => { (by[i.folderId] = by[i.folderId] || []).push(i.id) })
    let ok = 0
    const bad = []
    for (const fid of Object.keys(by)) {
      const f = v.folders.find(x => String(x.id) === String(fid)) || {}
      try {
        const r = await invAuditItemsApprove({ folderId: Number(fid), itemIds: by[fid], decisions: {} })
        if (r && r.ok !== false && !r.detail) ok += by[fid].length
        else bad.push(`${f.payee || f.title || '票夹 #' + fid}（${by[fid].length} 张）：${(r && (r.msg || r.detail)) || '没说原因'}`)
      } catch (e) { bad.push(`${f.payee || f.title || '票夹 #' + fid}（${by[fid].length} 张）：${errText(e)}`) }
    }
    setDone({ ok, bad })
    setBusy('')
    await reload()
    onChanged && onChanged()
  }

  const st = v.stats || { total: 0, hit: 0, left: 0 }
  const pct = st.total ? Math.round(st.hit * 100 / st.total) : 0
  const lastStr = (v.strangers || []).slice(-3).reverse()
  return (
    <Modal title={v.check.listName ? '对纸质票 · ' + v.check.listName : '对纸质票'} onClose={onClose} width={1080}
      footer={<>
        <span className="inv-muted">{open ? '关掉这个窗口不会结束点验：再点「对纸质票」可以接着对。' : `这次点验已经收了（${fmtTime(v.check.closedAt)}）。`}</span>
        <a className="btn" href={invCheckExportUrl(cid, 'missing')}>导出没扫到的</a>
        {entry === 'audit' && canAudit && <button type="button" className="btn primary" disabled={!hitPending.length || !!busy} onClick={approveHits}>
          {busy === 'audit' ? '正在审核…' : `审核扫到的（${hitPending.length}）`}</button>}
        {open && <button type="button" className="btn" disabled={!!busy} onClick={finish}>{busy === 'close' ? '正在收…' : '对完了，收'}</button>}
        <button type="button" className="btn" onClick={onClose}>关闭</button>
      </>}>
      <div className="inv-ck">
        <div className="inv-ck-bar">
          <span>{v.check.listId ? <>这份清单共 <b>{st.total}</b> 张票，按凭证排</> : <>选中 <b>{v.folders.length}</b> 个票夹 · 共 <b>{st.total}</b> 张票</>}</span>
          <span className="inv-ck-prog"><i style={{ width: pct + '%' }} /></span>
          <b className="inv-num inv-ck-num">扫到 {st.hit} / {st.total}</b>
          <button type="button" className="btn" onClick={() => setOnlyMiss(x => !x)}>{onlyMiss ? '看全部' : `只看没扫到的（${st.left}）`}</button>
        </div>
        {open && <div className="inv-ck-scan">
          <ScanInput keepFocus onScan={onScan} placeholder="用扫码枪连续扫发票左上角的二维码…（手机扫的也会记到这里）" />
        </div>}
        {alertMsg && <div className={'inv-ck-alert ' + alertMsg.tone} role="alert">{alertMsg.text}
          <button type="button" className="inv-x" onClick={() => setAlertMsg(null)} aria-label="收起">×</button></div>}
        {done && <div className={'inv-ck-alert ' + (done.bad.length ? 'warn' : 'ok')}>
          已审核通过 {done.ok} 张{done.bad.length ? `；${done.bad.length} 个票夹没过：` : '。'}
          {done.bad.map((b, i) => <div key={i}>{b}</div>)}
        </div>}
        <div className="inv-ck-scroll">
          <table className="inv-ck-tbl">
            <thead><tr><th style={{ width: 34 }} /><th>凭证</th><th>发票号码</th><th>销方</th><th className="r">价税合计</th><th>票种</th><th>扫到的时间 / 谁扫的</th><th style={{ width: 70 }} /></tr></thead>
            <tbody>
              {v.folders.map(f => {
                const list = v.items.filter(i => i.folderId === f.id)
                const show = list.filter(i => !onlyMiss || !i.hit)
                if (!show.length) return null
                return <React.Fragment key={f.id}>
                  <tr className="grp"><td colSpan={8}>{f.isList ? f.title : <>{f.payee || f.title || ('票夹 #' + f.id)} · {money(f.amount)}</>}
                    {SRC_CN[f.source] ? ' · ' + SRC_CN[f.source] : ''}{f.ownerName ? ' · 归' + f.ownerName : ''}
                    <span className="inv-ck-cnt">{f.hit} / {f.total}</span></td></tr>
                  {show.map(i => <tr key={i.id} id={'inv-ck-row-' + i.id} className={(i.hit ? 'hit' : '') + (flashId === i.id ? ' flash' : '')}>
                    <td>{i.hit ? '✓' : ''}</td>
                    <td className="inv-ck-vno">{i.voucher || <span className="inv-muted">还没做账</span>}</td>
                    <td className="inv-num">{i.number || '（没有号码）'}</td>
                    <td>{i.sellerName}</td>
                    <td className="inv-num r">{money(i.total)}</td>
                    <td>{i.typeLabel}</td>
                    <td className="inv-muted">{i.hit ? `${fmtTime(i.hit.at)} · ${i.hit.by}（${i.hit.viaLabel}）` : ''}</td>
                    <td>{open && (i.hit ? (i.hit.via === 'manual' ? <button type="button" className="inv-ck-lk" onClick={() => mark(i, false)}>取消</button> : null)
                      : <button type="button" className="inv-ck-lk" onClick={() => mark(i, true)} title="没有二维码的票（收据、定额票、出租车票）：人看过以后手工勾">手工勾</button>)}</td>
                  </tr>)}
                </React.Fragment>
              })}
              {!v.items.length && <tr><td colSpan={8} className="inv-ck-empty">{v.check.listId ? '这份清单里的票都不在了。' : '选中的票夹里没有发票。'}</td></tr>}
            </tbody>
          </table>
        </div>
        {lastStr.length > 0 && <div className="inv-ck-str">
          <b>扫到过 {v.stats.strangers} 张不相干的票</b>（没有登记，也没有挪）：
          {lastStr.map((x, i) => <div key={i}>{x.kind === 'other' && v.check.listId
            ? `不在这份清单里：${x.number || ''} · ${money(x.total)} · ${x.bookMonth ? x.bookMonth + ' 做账的（' + (x.voucher || '') + '）' : '还没做账'}`
            : x.kind === 'other'
            ? `不在选中的票夹里：${x.number || ''} · ${money(x.total)} · 在「${x.payee || x.folderTitle || '票夹 #' + x.folderId}」里${x.ownerName ? '（归' + x.ownerName + '审）' : ''}`
            : `系统里没有这张票：${x.number || ''}${x.total !== null && x.total !== undefined ? ' · ' + money(x.total) : ''}（要先收票）`}</div>)}
        </div>}
      </div>
    </Modal>
  )
}

// ───────────────────────── 入口按钮（收票工作台、发票审核共用） ─────────────────────────

/** candidates＝可以对的票夹 [{id,title,payee,amount,owner,invoices}]；preset＝默认勾上的 id；entry＝desk|audit；canAudit＝有没有审核权限 */
export default function PaperCheckButton({ candidates, preset, entry, canAudit, onChanged, listId, label }) {
  const [cur, setCur] = useState(null)        // 正在对的那一次（接着对）
  const [starting, setStarting] = useState(false)
  const [view, setView] = useState(null)
  const [lerr, setLerr] = useState('')
  const mounted = useRef(true)
  const look = useCallback(() => {
    invCheckCurrent().then(r => { if (mounted.current) setCur(r && r.check ? r : null) }).catch(() => { /* 取不到不影响别的 */ })
  }, [])
  useEffect(() => { mounted.current = true; look(); return () => { mounted.current = false } }, [look])
  const close = () => { setView(null); look() }
  // 给了 listId（发票台账里某一份认证清单那一行的按钮）：点了直接对着这份清单开始，不用再选
  if (listId) {
    const go = async () => {
      setLerr('')
      try { const r = (cur && cur.check.listId === listId) ? cur : await invCheckStartList(listId, entry); setCur(r); setView(r) } catch (e) { setLerr(errText(e)) }
    }
    return <>
      <button type="button" className="inv-ck-lk" onClick={go}>{cur && cur.check.listId === listId ? `接着对（${cur.stats.hit}/${cur.stats.total}）` : (label || '对纸质票')}</button>
      {lerr && <span className="inv-ck-err"> {lerr}</span>}
      {view && <CheckPanel first={view} entry={entry} canAudit={canAudit} onClose={close} onChanged={onChanged} />}
    </>
  }
  return <>
    <button type="button" className="btn" onClick={() => (cur ? setView(cur) : setStarting(true))}
      title="选中几个票夹，把手上的纸质票连续扫一遍，看是不是一样">
      {cur ? `接着对纸质票（${cur.stats.hit}/${cur.stats.total}）` : '对纸质票'}</button>
    {cur && !view && <button type="button" className="inv-ck-lk" onClick={() => setStarting(true)} title="不接着上一次，重新选票夹开一次">重新选</button>}
    {starting && <CheckStart candidates={candidates} preset={preset} entry={entry} onClose={() => setStarting(false)}
      onStarted={(r) => { setStarting(false); setCur(r); setView(r) }} />}
    {view && <CheckPanel first={view} entry={entry} canAudit={canAudit} onClose={close} onChanged={onChanged} />}
  </>
}
