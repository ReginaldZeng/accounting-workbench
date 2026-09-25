// [Change Log] Date: 2026-09-25 | Author: Claude / c | Version: V2.621（发票管家·申请人自助登记发票后补）
// 业务同事自己登记发票后补：main.jsx 按 #/invself 分流到这里，不走工作台登录（业务同事没有工作台账号）。
// 认人：在钉钉里打开 → 自动免登；电脑浏览器 → 写钉钉上的姓名 → 钉钉收 6 位验证码 → 输入即登录。
// 登录后：「登记后补单」从我近 60 天发起的付款单/报销单里选一张 → 填预计到票 → 提交（进财务的发票后补池，
// 财务审核这张单时在「发票审核」里自动关联出来）；「我的后补单」看到票进度、补传资料。
// 会话令牌只放 sessionStorage + 请求头 X-Inv-Self（不进地址栏）。
import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  invSHello, invSJsConfig, invSLoginDd, invSLoginSend, invSLoginVerify, invSLogout,
  invSPayments, invSReceivers, invSLaterCreate, invSLaters, invSLaterDocs,
} from '../api.js'
import { inDingTalk, getAuthCode, ddConfig } from './ddBridge.js'
import { money } from './invShared.jsx'
import './inv.css'
import './inv-self.css'

const TOKEN_KEY = 'inv_self_t'
const KIND_LABEL = { special: '专票', normal: '普票', receipt: '收据' }
const TAX_RATES = ['13%', '9%', '6%', '5%', '3%', '1%', '0%', '免税', '不征税']
const ST_LABEL = { open: '待到票', partial: '部分到票', done: '已收齐', closed: '已关闭' }
const errText = e => (e && e.message) || String(e || '')
const loadTok = () => { try { return sessionStorage.getItem(TOKEN_KEY) || '' } catch { return '' } }
const saveTok = t => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY) } catch { /* 存不了就只在本页用 */ } }
function plusDays(n) {
  const d = new Date(); d.setDate(d.getDate() + n)
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
}
const num = v => (v === null || v === undefined || v === '' ? 0 : Number(v) || 0)

// ───────────────────────── 登录 ─────────────────────────

function Login({ hello, onIn }) {
  const [step, setStep] = useState('name')        // name / pick / code
  const [name, setName] = useState('')
  const [choices, setChoices] = useState([])
  const [pick, setPick] = useState(null)
  const [ticket, setTicket] = useState('')
  const [to, setTo] = useState('')
  const [code, setCode] = useState('')
  const [dev, setDev] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [wait, setWait] = useState(0)
  const codeRef = useRef(null)

  useEffect(() => {
    if (wait <= 0) return undefined
    const t = setTimeout(() => setWait(w => w - 1), 1000)
    return () => clearTimeout(t)
  }, [wait])

  const send = async (p) => {
    if (!name.trim()) { setMsg('写一下你在钉钉上的姓名'); return }
    setBusy(true); setMsg('')
    try {
      const r = await invSLoginSend(name.trim(), p === undefined ? pick : p)
      if (r.need === 'pick') { setChoices(r.choices || []); setStep('pick'); return }
      setTicket(r.ticket); setTo(r.to || ''); setDev(r.devCode || ''); setCode(''); setStep('code'); setWait(60)
      setTimeout(() => codeRef.current && codeRef.current.focus(), 50)
    } catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  const verify = async () => {
    const c = code.replace(/\D/g, '')
    if (c.length !== 6) { setMsg('请输入钉钉消息里的 6 位验证码'); return }
    setBusy(true); setMsg('')
    try {
      const r = await invSLoginVerify(ticket, c)
      onIn(r.token, r.me)
    } catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }

  return <div className="inv-sf-login">
    <h2>先确认你是谁</h2>
    {hello?.ddNote && <div className="inv-sf-note">{hello.ddNote}</div>}
    <p className="inv-sf-lead">系统会通过<b>钉钉</b>给你发一条 6 位验证码，能收到就说明是你本人。在钉钉里打开这个网址可以直接登录，不用验证码。</p>
    {step === 'name' && <form className="inv-sf-row" onSubmit={e => { e.preventDefault(); setPick(null); send(null) }}>
      <input className="inv-in inv-sf-big" autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="你在钉钉上的姓名" maxLength={20} />
      <button type="submit" className="btn-pri inv-sf-bigbtn" disabled={busy || !hello?.dingtalk}>{busy ? '发送中…' : '发验证码到钉钉'}</button>
    </form>}
    {step === 'pick' && <div className="inv-sf-pick">
      <div>钉钉里有 {choices.length} 位「{name}」，请选你自己：</div>
      {choices.map(c => <button type="button" key={c.i} className="btn" disabled={busy}
        onClick={() => { setPick(c.i); send(c.i) }}>{c.dept || '（部门未知）'}{c.title ? ' · ' + c.title : ''}</button>)}
      <button type="button" className="inv-sf-link" onClick={() => setStep('name')}>← 改名字</button>
    </div>}
    {step === 'code' && <form className="inv-sf-codebox" onSubmit={e => { e.preventDefault(); verify() }}>
      <div>验证码已发到 <b>{to}</b> 的钉钉（工作通知），5 分钟内有效。</div>
      {dev && <div className="inv-sf-note">测试环境没有真发钉钉，验证码是 <b className="inv-num">{dev}</b></div>}
      <div className="inv-sf-row">
        <input ref={codeRef} className="inv-in inv-sf-big inv-sf-code inv-num" inputMode="numeric" autoComplete="one-time-code"
          maxLength={6} value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} placeholder="6 位数字" />
        <button type="submit" className="btn-pri inv-sf-bigbtn" disabled={busy}>{busy ? '核对中…' : '登录'}</button>
      </div>
      <div className="inv-sf-row small">
        <button type="button" className="inv-sf-link" disabled={wait > 0 || busy} onClick={() => send()}>{wait > 0 ? `${wait} 秒后可重发` : '没收到？重发'}</button>
        <button type="button" className="inv-sf-link" onClick={() => { setStep('name'); setMsg('') }}>← 改名字</button>
      </div>
    </form>}
    {!hello?.dingtalk && <div className="inv-sf-err">服务器还没接上钉钉，暂时没法登录，请联系财务。</div>}
    {msg && <div className="inv-sf-err">{msg}</div>}
  </div>
}

// ───────────────────────── 登记 ─────────────────────────

function PayCard({ p, onPick, onMine }) {
  const done = !!p.laterId
  return <div className={'inv-sf-pay' + (done ? ' done' : '')}>
    <div className="inv-sf-pay-main">
      <div className="inv-sf-pay-t">{p.title || '（无标题）'}</div>
      <div className="inv-sf-pay-m">
        <span>审批编号 {p.businessId || '—'}</span>
        <span>{(p.createTime || '').slice(0, 10)}</span>
        {p.payeeName && <span>收款方 {p.payeeName}</span>}
      </div>
    </div>
    <div className="inv-sf-pay-amt inv-num">{p.amount !== null && p.amount !== undefined ? money(p.amount) : '—'}</div>
    <div className="inv-sf-pay-op">
      {done
        ? <button type="button" className="btn" onClick={onMine}>已登记 #{p.laterId} · 看进度</button>
        : <button type="button" className="btn-pri" onClick={() => onPick(p)}>登记后补</button>}
      {!done && p.hasInvoice && <span className="inv-sf-tip">这张单已经收到过发票</span>}
    </div>
  </div>
}

function Form({ token, pay, receivers, onBack, onDone }) {
  const [f, setF] = useState({
    invKind: 'special', taxRate: '', expectDate: plusDays(15),
    expectAmount: pay.amount !== null && pay.amount !== undefined ? String(pay.amount) : '',
    receiver: receivers.length === 1 ? receivers[0].account : '', note: '',
  })
  const [files, setFiles] = useState([])
  const [ack, setAck] = useState(false)
  const [err, setErr] = useState({})
  const [busy, setBusy] = useState(false)
  // 改哪项就清掉哪项的错误提示；换发票种类连带清税率的（收据不用填税率）
  const set = (k, v) => { setF(p => ({ ...p, [k]: v })); setErr(p => ({ ...p, [k]: undefined, ...(k === 'invKind' ? { taxRate: undefined } : {}) })) }

  const submit = async () => {
    const e = {}
    const amt = Number(String(f.expectAmount).replace(/,/g, ''))
    if (f.invKind !== 'receipt' && !f.taxRate) e.taxRate = '请选税率'
    if (!f.expectDate) e.expectDate = '请填预计什么时候能拿到发票'
    if (!Number.isFinite(amt) || amt <= 0) e.expectAmount = '请填预计到票金额（大于 0）'
    if (!f.receiver) e.receiver = '请选发票交给哪位财务'
    if (pay.hasInvoice && !ack) e.ack = '这张单已经收到过发票，确认还要登记请先勾选'
    setErr(e)
    if (Object.keys(e).length) return
    setBusy(true)
    try {
      const r = await invSLaterCreate(token, {
        instId: pay.procInstId, invKind: f.invKind, taxRate: f.invKind === 'receipt' ? '' : f.taxRate, expectDate: f.expectDate,
        expectAmount: amt, receiver: f.receiver, note: f.note.trim(),
      })
      let docMsg = ''
      if (r?.later?.id && files.length) {
        try { await invSLaterDocs(token, r.later.id, files) } catch (x) { docMsg = '，但资料没传上去（' + errText(x) + '），可在「我的后补单」里重传' }
      }
      const rn = r?.later?.receiverName || ''
      onDone(`已登记后补单 #${r?.later?.id}${docMsg}。${r?.notified ? `已通过钉钉通知财务 ${rn}。` : ''}发票到了请交给 ${rn || '财务'}。`)
    } catch (x) { setErr({ _: '没登记上：' + errText(x) }) } finally { setBusy(false) }
  }

  return <div className="inv-sf-form">
    <button type="button" className="inv-sf-link" onClick={onBack}>← 换一张单</button>
    <div className="inv-sf-sel">
      <div className="inv-sf-pay-t">{pay.title}</div>
      <div className="inv-sf-pay-m"><span>审批编号 {pay.businessId || '—'}</span>{pay.payeeName && <span>收款方 {pay.payeeName}</span>}
        <span>付款金额 <b className="inv-num">{money(pay.amount)}</b></span></div>
    </div>
    <div className="inv-sf-grid">
      <div className="inv-sf-f"><span>发票种类</span>
        <div className="inv-sf-seg" role="group" aria-label="发票种类">{Object.entries(KIND_LABEL).map(([k, v]) =>
          <button type="button" key={k} className={f.invKind === k ? 'on' : ''} aria-pressed={f.invKind === k} onClick={() => set('invKind', k)}>{v}</button>)}</div>
      </div>
      <label className="inv-sf-f"><span>税率</span>
        {f.invKind === 'receipt'
          ? <input className="inv-in" disabled value="收据没有税率，不用填" />
          : <select className="inv-in" value={f.taxRate} onChange={e => set('taxRate', e.target.value)}>
            <option value="">请选择</option>
            {TAX_RATES.map(x => <option key={x} value={x}>{x}</option>)}
          </select>}
        {err.taxRate ? <em>{err.taxRate}</em> : f.invKind !== 'receipt' && <i>不清楚就问供应商开几个点的票</i>}
      </label>
      <label className="inv-sf-f"><span>预计什么时候拿到发票</span>
        <input type="date" className="inv-in" value={f.expectDate} onChange={e => set('expectDate', e.target.value)} />
        {err.expectDate && <em>{err.expectDate}</em>}
      </label>
      <label className="inv-sf-f"><span>预计到票金额</span>
        <input className="inv-in inv-num" inputMode="decimal" value={f.expectAmount} onChange={e => set('expectAmount', e.target.value)} />
        {err.expectAmount ? <em>{err.expectAmount}</em> : <i>默认等于付款金额；只有部分要补票就改小</i>}
      </label>
      <label className="inv-sf-f wide"><span>发票交给哪位财务</span>
        <select className="inv-in" value={f.receiver} onChange={e => set('receiver', e.target.value)}>
          <option value="">请选择</option>
          {receivers.map(r => <option key={r.account} value={r.account}>{r.name}</option>)}
        </select>
        {err.receiver ? <em>{err.receiver}</em> : <i>登记后系统会用钉钉告诉他；发票到了交给他</i>}
        {!receivers.length && <em>财务还没设置接收人，请先联系财务</em>}
      </label>
      <label className="inv-sf-f wide"><span>备注（选填）</span>
        <textarea className="inv-in inv-sf-ta" value={f.note} onChange={e => set('note', e.target.value)} placeholder="比如：供应商月底统一开票" maxLength={500} />
      </label>
      <div className="inv-sf-f wide"><span>资料（选填：合同、对账单、供应商承诺函等）</span>
        <input type="file" multiple onChange={e => { const fs = Array.from(e.target.files || []); setFiles(p => [...p, ...fs]); e.target.value = '' }} />
        {files.length > 0 && <div className="inv-sf-files">{files.map((x, i) => <span key={i}>{x.name}
          <button type="button" title="移除" onClick={() => setFiles(fs => fs.filter((_, j) => j !== i))}>×</button></span>)}</div>}
      </div>
      {pay.hasInvoice && <label className="inv-sf-ack wide">
        <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} /> 这张单已经收到过发票，我确认还有发票要后补
        {err.ack && <em>{err.ack}</em>}
      </label>}
    </div>
    <div className="inv-sf-foot">
      {err._ && <span className="inv-sf-err">{err._}</span>}
      <button type="button" className="btn" onClick={onBack} disabled={busy}>取消</button>
      <button type="button" className="btn-pri inv-sf-bigbtn" onClick={submit} disabled={busy}>{busy ? '提交中…' : '提交后补单'}</button>
    </div>
  </div>
}

// ───────────────────────── 我的后补单 ─────────────────────────

function MyLaters({ token, rows, onReload }) {
  const [busy, setBusy] = useState(0)
  const [note, setNote] = useState('')
  const up = async (l, fs) => {
    if (!fs.length) return
    setBusy(l.id); setNote('')
    try { await invSLaterDocs(token, l.id, fs); setNote(`后补单 #${l.id} 资料已上传`); onReload() } catch (e) { setNote('没传上去：' + errText(e)) } finally { setBusy(0) }
  }
  if (!rows.length) return <div className="inv-sf-empty">还没有登记过发票后补单。</div>
  return <div className="inv-sf-mine">
    {note && <div className="inv-sf-note">{note}</div>}
    <div className="inv-sf-tw"><table className="inv-sf-tbl">
      <thead><tr><th>#</th><th>审批编号</th><th>收款方</th><th className="num">预计到票</th><th className="num">已到</th><th className="num">还没到</th>
        <th>预计日期</th><th>交给</th><th>状态</th><th>登记</th><th /></tr></thead>
      <tbody>{rows.map(l => {
        const live = l.status === 'open' || l.status === 'partial'
        return <tr key={l.id} className={l.overdue && live ? 'late' : ''}>
          <td className="inv-num">{l.id}</td>
          <td>{l.businessId || '—'}</td>
          <td className="inv-sf-ell" title={l.payee?.name || ''}>{l.payee?.name || '—'}</td>
          <td className="num">{money(l.expectAmount)}</td>
          <td className="num">{money(num(l.receivedAmount) + num(l.unregisteredAmount))}</td>
          <td className="num">{live ? money(l.remaining) : '—'}</td>
          <td>{l.expectDate}{l.overdue && live && <b className="inv-sf-late">已超期</b>}</td>
          <td>{l.receiverName}</td>
          <td className={'st-' + l.status}>{ST_LABEL[l.status] || l.status}</td>
          <td>{l.filedVia === 'self' ? '自己' : '财务代填'}</td>
          <td>{live && <label className={'inv-sf-upl' + (busy === l.id ? ' busy' : '')}>{busy === l.id ? '上传中…' : '补传资料'}
            <input type="file" multiple disabled={!!busy} onChange={e => { const fs = Array.from(e.target.files || []); e.target.value = ''; up(l, fs) }} /></label>}</td>
        </tr>
      })}</tbody>
    </table></div>
  </div>
}

// ───────────────────────── 页面 ─────────────────────────

export default function InvSelf() {
  const [tok, setTok] = useState(loadTok)
  const [hello, setHello] = useState(null)
  const [me, setMe] = useState(null)
  const [phase, setPhase] = useState('loading')    // loading / login / ready / down
  const [tab, setTab] = useState('new')
  const [pays, setPays] = useState(null)
  const [paysMsg, setPaysMsg] = useState('')
  const [recv, setRecv] = useState([])
  const [laters, setLaters] = useState([])
  const [pick, setPick] = useState(null)
  const [flash, setFlash] = useState('')
  const [down, setDown] = useState('')

  const signIn = useCallback((t, who) => { saveTok(t); setTok(t); setMe(who); setPhase('ready') }, [])
  const signOut = useCallback(async () => {
    try { if (tok) await invSLogout(tok) } catch { /* 忽略 */ }
    saveTok(''); setTok(''); setMe(null); setPays(null); setLaters([]); setPick(null); setPhase('login')
  }, [tok])

  // 开页：已有会话直接进；钉钉里自动免登；否则验证码登录
  useEffect(() => {
    let live = true
    ;(async () => {
      let h
      try { h = await invSHello(tok) } catch (e) { if (live) { setDown(errText(e)); setPhase('down') } return }
      if (!live) return
      setHello(h)
      if (h.me) { setMe(h.me); setPhase('ready'); return }
      if (tok) saveTok('')
      if (inDingTalk() && h.dingtalk) {
        let cid = h.corpId || ''
        try { const c = await ddConfig(invSJsConfig, ['runtime.permission.requestAuthCode']); if (!cid && c && c.corpId) cid = c.corpId } catch { /* 不鉴权也照样试免登 */ }
        try {
          const code = cid ? await getAuthCode(cid) : ''
          if (code) {
            const r = await invSLoginDd(code)
            if (live) signIn(r.token, r.me)
            return
          }
        } catch (e) { if (live) setHello({ ...h, ddNote: '钉钉自动登录没成功（' + errText(e) + '），请用验证码登录。' }) }
      }
      if (live) setPhase('login')
    })()
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const expired = useCallback(e => { if (e && e.status === 401) { saveTok(''); setTok(''); setMe(null); setPhase('login'); return true } return false }, [])
  const loadPays = useCallback(async () => {
    setPays(null); setPaysMsg('')
    try { const r = await invSPayments(tok); setPays(r.rows || []); setPaysMsg(r.ok ? (r.msg || '') : (r.msg || '钉钉审批单没取到')) } catch (e) { if (!expired(e)) { setPays([]); setPaysMsg(errText(e)) } }
  }, [tok, expired])
  const loadLaters = useCallback(async () => {
    try { const r = await invSLaters(tok); setLaters(r.rows || []) } catch (e) { expired(e) }
  }, [tok, expired])

  useEffect(() => {
    if (phase !== 'ready' || !tok) return
    loadPays(); loadLaters()
    invSReceivers(tok).then(r => setRecv(r.rows || [])).catch(expired)
  }, [phase, tok, loadPays, loadLaters, expired])

  const tpl = (hello?.templates || []).join('、')
  return <div className="inv-sf">
    <header className="inv-sf-top">
      <div>
        <div className="inv-sf-title">发票后补登记</div>
        <div className="inv-sf-sub">付款时发票还没拿到？在这里登记一下，财务审核时就知道这张单的票会后补。</div>
      </div>
      {me && <div className="inv-sf-me"><b>{me.name}</b>{me.dept && <span>{me.dept.split('-').slice(-1)[0]}</span>}
        <button type="button" className="inv-sf-link" onClick={signOut}>退出</button></div>}
    </header>

    {phase === 'loading' && <div className="inv-sf-wait"><span className="inv-spin" /> 正在打开…</div>}
    {phase === 'down' && <div className="inv-sf-err">页面没连上服务器：{down}</div>}
    {phase === 'login' && <Login hello={hello} onIn={signIn} />}

    {phase === 'ready' && <>
      <nav className="inv-sf-tabs">
        <button type="button" className={tab === 'new' ? 'on' : ''} onClick={() => setTab('new')}>登记后补单</button>
        <button type="button" className={tab === 'mine' ? 'on' : ''} onClick={() => { setTab('mine'); loadLaters() }}>我的后补单{laters.length ? `（${laters.length}）` : ''}</button>
      </nav>
      {flash && <div className="inv-sf-ok">{flash}</div>}

      {tab === 'new' && !pick && <section className="inv-sf-sec">
        <div className="inv-sf-sec-h"><b>选一张你发起的单子</b><span>近 60 天{tpl ? `的「${tpl}」` : ''}</span>
          <button type="button" className="inv-sf-link" onClick={loadPays}>刷新</button></div>
        {pays === null && <div className="inv-sf-wait"><span className="inv-spin" /> 正在从钉钉取你的审批单…</div>}
        {paysMsg && <div className="inv-sf-note">{paysMsg}</div>}
        {pays && !pays.length && !paysMsg && <div className="inv-sf-empty">近 60 天没有你发起的、可以登记后补的单子。</div>}
        {pays && pays.map(p => <PayCard key={p.procInstId} p={p} onPick={x => { setFlash(''); setPick(x) }} onMine={() => { setTab('mine'); loadLaters() }} />)}
      </section>}
      {tab === 'new' && pick && <section className="inv-sf-sec">
        <Form token={tok} pay={pick} receivers={recv} onBack={() => setPick(null)}
          onDone={m => { setFlash(m); setPick(null); setTab('mine'); loadLaters(); loadPays() }} />
      </section>}
      {tab === 'mine' && <section className="inv-sf-sec"><MyLaters token={tok} rows={laters} onReload={loadLaters} /></section>}
    </>}
  </div>
}
