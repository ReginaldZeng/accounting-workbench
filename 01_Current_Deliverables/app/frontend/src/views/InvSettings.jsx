// [Change Log] Date: 2026-10-14 | Author: Claude / c | Version: V2.950（发票管家·后补票自动转待认证）| 加「后补票自动转待认证」开关：关／演练／真做（真做只有主管理员能开）
// [Change Log] Date: 2026-10-13 | Author: Claude / c | Version: V2.948（钉钉流程里后到的发票走后补单）| ④ 钉钉自动接入里加「财务初审人」：他点同意之后才发到流程里的发票算后补票
// [Change Log] Date: 2026-10-13 | Author: Claude / c | Version: V2.943（发票管家·清空试运行数据）| 最下面加「⑧ 清空试运行数据」：只有主管理员看得到
//   （别人打预览接口回 403，这一块整个不画）；列出会清多少、留多少，填重新接钉钉审批单的起始日，手打「清零」才能点。
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票管家设置：财务人员名单（钉钉绑定＋接收人）、本公司抬头、审批模板、催票规则、没附发票的付款单拦/提醒、站点地址；未保存提示＋逐项校验
// [Change Log] Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.736 | 加「④ 钉钉自动接入」：接入审批人（审批流走到他节点的单子自动建票夹）＋起始日＋上次扫描情况＋立即扫一次；后面几节顺延编号
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 和后端校验对齐：名单里没绑工作台账号的人直接挡保存并在行内说清楚（后端本来就拒）；
//   催票天数范围改成后端实际收的 0～30 / 1～60（原来前端放 60/90，后端悄悄压成 30/60）。
// 需求确认书 v1.4 五/十一 + 技术方案 §3「设置」+ §5.2 GET/POST /api/inv/settings。
// 既能当后补池的「设置」页签（embedded，不再画页头），也能被收票工作台放进 Modal 里用——宽度全跟容器走，不写死页面宽。
// 保存时把后端给的其它键（corpId 等本页不编辑的）原样带回去，免得本页一保存把别处的配置抹掉。
import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { invSettings, invSaveSettings, invAccounts, invIntake, invIntakeScan, invResetPreview, invResetRun, invLaterVoucherConfig, invLaterVoucherConfigSet } from '../api.js'
import { PersonPicker, useToast } from './invShared.jsx'
import './inv-later.css'

// ───────────────────────── 规范化 / 校验 ─────────────────────────

let _k = 0
const key = () => 'k' + (++_k)
const str = v => (v === null || v === undefined ? '' : String(v))
const errText = e => (e && (e.message || String(e))) || '未知原因'
const splitFields = s => str(s).split(/[,，、;；\n]+/).map(x => x.trim()).filter(Boolean)

// 后端设置 → 编辑用草稿：每行加 _k 做稳定 key；金额字段转成逗号串方便改；数字转成字符串方便输入框
function toDraft(s) {
  const src = s && typeof s === 'object' ? s : {}
  const r = src.remind && typeof src.remind === 'object' ? src.remind : {}
  const ik = src.intake && typeof src.intake === 'object' ? src.intake : {}
  return {
    people: (Array.isArray(src.people) ? src.people : []).map(p => ({
      ...p, _k: key(), dtName: str(p?.dtName), dtUserid: str(p?.dtUserid), account: str(p?.account), receiver: !!p?.receiver,
    })),
    company: (Array.isArray(src.company) ? src.company : []).map(c => ({ ...c, _k: key(), name: str(c?.name), taxId: str(c?.taxId) })),
    templates: (Array.isArray(src.templates) ? src.templates : []).map(t => ({
      ...t, _k: key(), name: str(t?.name),
      amountFields: Array.isArray(t?.amountFields) ? t.amountFields.join('，') : str(t?.amountFields),
      allowLater: !!t?.allowLater,
    })),
    remind: {
      enabled: r.enabled === undefined ? true : !!r.enabled,
      beforeDays: str(r.beforeDays ?? 3), everyDays: str(r.everyDays ?? 7), hour: str(r.hour ?? 10),
    },
    blockNoInvoice: !!src.blockNoInvoice,
    portalUrl: str(src.portalUrl),
    intake: {
      // 每位接入审批人带自己的启用日（留空＝还没启用）；老数据没有这一栏的，后端已按原来的起始日补上
      approvers: (Array.isArray(ik.approvers) ? ik.approvers : []).map(a => ({ dtUserid: str(a?.dtUserid), dtName: str(a?.dtName), since: str(a?.since) })),
      firstReviewers: (Array.isArray(ik.firstReviewers) ? ik.firstReviewers : []).map(a => ({ dtUserid: str(a?.dtUserid), dtName: str(a?.dtName) })),
      since: str(ik.since || '2026-09-01'),
    },
  }
}
const stripK = ({ _k: _drop, ...rest }) => rest
// 草稿 → 提交给后端：去掉 _k、字符串去空格、金额字段拆回数组、数字转整数；再叠在原始设置上保留别的键
function toPayload(d, raw) {
  return {
    ...(raw && typeof raw === 'object' ? raw : {}),
    people: d.people.map(p => ({ ...stripK(p), dtName: p.dtName.trim(), dtUserid: p.dtUserid.trim(), account: p.account.trim(), receiver: !!p.receiver })),
    company: d.company.filter(c => c.name.trim() || c.taxId.trim())
      .map(c => ({ ...stripK(c), name: c.name.trim(), taxId: c.taxId.trim().toUpperCase() })),
    templates: d.templates.filter(t => t.name.trim() || t.amountFields.trim())
      .map(t => ({ ...stripK(t), name: t.name.trim(), amountFields: splitFields(t.amountFields), allowLater: !!t.allowLater })),
    remind: {
      ...((raw && raw.remind) || {}), enabled: !!d.remind.enabled,
      beforeDays: parseInt(d.remind.beforeDays, 10), everyDays: parseInt(d.remind.everyDays, 10), hour: parseInt(d.remind.hour, 10),
    },
    blockNoInvoice: !!d.blockNoInvoice,
    portalUrl: d.portalUrl.trim(),
    intake: { approvers: d.intake.approvers.map(a => ({ ...a, since: str(a.since).trim() })), firstReviewers: d.intake.firstReviewers, since: d.intake.since.trim() },
  }
}
// 比较有没有改动：不看 _k（每次载入都会重新编号）
const sig = d => JSON.stringify({ ...d, people: d.people.map(stripK), company: d.company.map(stripK), templates: d.templates.map(stripK) })

// 统一社会信用代码（GB 32100-2015）：18 位，第 18 位是校验位。只做软提示——抄错税号会让"抬头不符"误报或漏报
const _USCC = '0123456789ABCDEFGHJKLMNPQRTUWXY'
const _W = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
function taxIdWarn(v) {
  const s = str(v).trim().toUpperCase()
  if (!s) return ''
  if (s.length !== 18) return `税号一般是 18 位，这里是 ${s.length} 位，请核对`
  if (![...s].every(ch => _USCC.includes(ch))) return '税号里有不该出现的字符（不含 I、O、Z、S、V），请核对'
  let sum = 0
  for (let i = 0; i < 17; i++) sum += _USCC.indexOf(s[i]) * _W[i]
  const c = (31 - (sum % 31)) % 31
  return _USCC[c] === s[17] ? '' : '税号最后一位校验对不上，可能抄错了一位，请核对'
}

const intIn = (v, lo, hi) => { const s = str(v).trim(); if (!/^\d+$/.test(s)) return false; const n = parseInt(s, 10); return n >= lo && n <= hi }
// 催票天数：和后端 normalize_settings 收的范围一致（超出后端会悄悄压回去，这里先挡住说清楚）
export const REMIND_LIMITS = { beforeDays: [0, 30], everyDays: [1, 60], hour: [0, 23] }
const NO_ACCOUNT = '请选他的工作台账号；没有工作台账号的人不用加进名单，不然整张设置存不上'

/**
 * 返回 { errors:{路径:文字}, warns:{路径:文字} }；errors 挡保存，warns 只提示。
 * 名单里没选工作台账号的人算错误（后端保存时整张设置会被拒），同时放一份在 warns 里——没点保存前就在行内黄字提示。
 */
export function validate(d) {
  const errors = {}, warns = {}
  const accSeen = {}, uidSeen = {}
  d.people.forEach((p, i) => {
    const a = p.account.trim()
    if (!a) {
      errors['people.' + i] = p.receiver ? '接收人要选工作台账号，否则「我接收的」里看不到他的单子' : NO_ACCOUNT
      warns['people.' + i] = errors['people.' + i]
    }
    if (a) { if (accSeen[a] !== undefined) { errors['people.' + i] = `账号「${a}」已经绑给了上面的 ${d.people[accSeen[a]].dtName || '另一人'}，一个账号只能对一个人`; } else accSeen[a] = i }
    const u = p.dtUserid.trim()
    if (u) { if (uidSeen[u] !== undefined) errors['people.' + i] = `${p.dtName || '这个人'} 在名单里出现了两次，删掉一行`; else uidSeen[u] = i }
  })
  const nameSeen = {}
  d.company.forEach((c, i) => {
    const n = c.name.trim(), t = c.taxId.trim()
    if (!n && !t) return
    if (!n) errors['company.' + i + '.name'] = '请填公司全称（和发票抬头一致）'
    if (!t) errors['company.' + i + '.taxId'] = '请填税号'
    else { const w = taxIdWarn(t); if (w) warns['company.' + i + '.taxId'] = w }
    if (n) { if (nameSeen[n]) errors['company.' + i + '.name'] = '这个抬头上面已经有了'; nameSeen[n] = true }
  })
  const tplSeen = {}
  d.templates.forEach((t, i) => {
    const n = t.name.trim()
    if (!n && !t.amountFields.trim()) return
    if (!n) errors['templates.' + i] = '请填审批模板名称（和钉钉里的名称一字不差）'
    else if (tplSeen[n]) errors['templates.' + i] = '这个模板上面已经有了'
    else if (!splitFields(t.amountFields).length) warns['templates.' + i] = '没填金额字段：扫这类单子时读不出金额'
    if (n) tplSeen[n] = true
  })
  const [b0, b1] = REMIND_LIMITS.beforeDays, [e0, e1] = REMIND_LIMITS.everyDays, [h0, h1] = REMIND_LIMITS.hour
  if (!intIn(d.remind.beforeDays, b0, b1)) errors['remind.beforeDays'] = `填 ${b0}～${b1} 的整数`
  if (!intIn(d.remind.everyDays, e0, e1)) errors['remind.everyDays'] = `填 ${e0}～${e1} 的整数`
  if (!intIn(d.remind.hour, h0, h1)) errors['remind.hour'] = `填 ${h0}～${h1} 的整点`
  const u = d.portalUrl.trim()
  d.intake.approvers.forEach(a => {
    if (str(a.since).trim() && !/^\d{4}-\d{2}-\d{2}$/.test(str(a.since).trim())) errors['intake.since'] = `${a.dtName || '接入审批人'} 的启用日请选一个日期（还没启用就留空）`
  })
  if (u && !/^https?:\/\/[^\s/]+/i.test(u)) errors.portalUrl = '要以 http:// 或 https:// 开头的完整网址，例如 http://111.229.72.116'
  return { errors, warns }
}

// ───────────────────────── 小件 ─────────────────────────

function Section({ title, sub, children, extra }) {
  return <section className="inv-set-sec">
    <div className="inv-set-sh">
      <div><div className="inv-set-st">{title}</div>{sub && <div className="inv-set-ss">{sub}</div>}</div>
      {extra}
    </div>
    {children}
  </section>
}
const Msg = ({ err, warn }) => err ? <div className="inv-lt-fe">{err}</div> : (warn ? <div className="inv-set-warn">{warn}</div> : null)

// ───────────────────────── ① 财务人员名单 ─────────────────────────

function AccountSelect({ accounts, accErr, value, onChange }) {
  if (!accounts) {   // 账号清单没拉到：退回手填，不挡人干活
    return <input className="inv-in" value={value} placeholder={accErr ? '手填工作台登录名' : '加载中…'} onChange={e => onChange(e.target.value)} />
  }
  const known = accounts.some(a => a.name === value)
  return <select className="inv-in" value={value} onChange={e => onChange(e.target.value)}>
    <option value="">（请选工作台账号）</option>
    {accounts.map(a => <option key={a.name} value={a.name}>{a.name}{a.post || a.grp ? `（${a.post || a.grp}）` : ''}</option>)}
    {value && !known && <option value={value}>{value}（账号已不存在）</option>}
  </select>
}

function PeopleSection({ rows, setRows, accounts, accErr, errors, warns }) {
  const [addErr, setAddErr] = useState('')
  const add = (p) => {
    if (!p) return
    if (rows.some(r => r.dtUserid && r.dtUserid === p.userid)) { setAddErr(`${p.name} 已经在名单里了`); return }
    setAddErr('')
    // 工作台账号和钉钉同名的，顺手帮他选上（多数人登录名就是姓名）
    const same = (accounts || []).find(a => a.name === p.name)
    setRows([...rows, { _k: key(), dtName: p.name, dtUserid: p.userid, account: same ? same.name : '', receiver: false }])
  }
  const upd = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return <Section title="① 财务人员名单"
    sub="把收票、收后补票的同事从钉钉通讯录加进来，并绑定他的工作台账号：手机配对时靠它确认「手机上的人和电脑上登录的是同一个」；勾了「接收人」的人会出现在后补单的接收人下拉里，他的「我接收的」也靠这个绑定。">
    {accErr && <div className="inv-set-warn">工作台账号清单没拉到（{accErr}），账号一栏先手填登录名。</div>}
    {rows.length > 0 ? <div className="tbl-wrap">
      <table className="inv-set-tbl">
        <thead><tr><th>钉钉姓名</th><th>工作台账号</th><th>接收人</th><th /></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={r._k}>
          <td><b>{r.dtName || '（无名字）'}</b><div className="sub inv-num">{r.dtUserid || '没有钉钉身份'}</div></td>
          <td className="inv-set-acc">
            <AccountSelect accounts={accounts} accErr={accErr} value={r.account} onChange={v => upd(i, { account: v })} />
            <Msg err={errors['people.' + i]} warn={warns['people.' + i]} />
          </td>
          <td><label className="inv-set-ck"><input type="checkbox" checked={!!r.receiver} onChange={e => upd(i, { receiver: e.target.checked })} />接收人</label></td>
          <td><button type="button" className="inv-set-del" onClick={() => setRows(rows.filter((_, j) => j !== i))}>移除</button></td>
        </tr>)}</tbody>
      </table>
    </div> : <div className="inv-lt-empty">名单还是空的。在下面按名字搜人加进来。</div>}
    <div className="inv-set-add">
      <span className="inv-set-addl">加人</span>
      <PersonPicker value={null} onChange={add} placeholder="打名字，从钉钉通讯录里搜" />
    </div>
    {addErr && <div className="inv-lt-fe">{addErr}</div>}
  </Section>
}

// ───────────────────────── ② 本公司抬头 ─────────────────────────

function CompanySection({ rows, setRows, errors, warns }) {
  const upd = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return <Section title="② 本公司抬头" sub="发票购买方要是这里的某一家，否则标「非本公司抬头」。名称和税号照营业执照填。"
    extra={<button type="button" className="btn" onClick={() => setRows([...rows, { _k: key(), name: '', taxId: '' }])}>＋ 加一家</button>}>
    {rows.length ? <div className="inv-set-list">{rows.map((r, i) => <div key={r._k} className="inv-set-line">
      <div className="inv-set-cell grow">
        <input className="inv-in" value={r.name} placeholder="公司全称" onChange={e => upd(i, { name: e.target.value })} />
        <Msg err={errors['company.' + i + '.name']} />
      </div>
      <div className="inv-set-cell tax">
        <input className="inv-in inv-num" value={r.taxId} placeholder="18 位统一社会信用代码" maxLength={24}
          onChange={e => upd(i, { taxId: e.target.value.toUpperCase().replace(/\s+/g, '') })} />
        <Msg err={errors['company.' + i + '.taxId']} warn={warns['company.' + i + '.taxId']} />
      </div>
      <button type="button" className="inv-set-del" onClick={() => setRows(rows.filter((_, j) => j !== i))}>移除</button>
    </div>)}</div> : <div className="inv-lt-empty">还没填本公司抬头：发票抬头就没法核对。点右上「加一家」。</div>}
  </Section>
}

// ───────────────────────── ③ 审批模板 ─────────────────────────

function TemplateSection({ rows, setRows, errors, warns }) {
  const upd = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  return <Section title="③ 审批模板" sub="收票台能认哪些钉钉审批单；金额字段按先后顺序找，第一个有值的当单据金额；勾「可登记后补」的模板（如公对公付款）才能进发票后补池。"
    extra={<button type="button" className="btn" onClick={() => setRows([...rows, { _k: key(), name: '', amountFields: '', allowLater: false }])}>＋ 加一个模板</button>}>
    {rows.length ? <div className="inv-set-list">{rows.map((r, i) => <div key={r._k} className="inv-set-line">
      <div className="inv-set-cell tpl">
        <input className="inv-in" value={r.name} placeholder="模板名称，如：付款申请（公对公）" onChange={e => upd(i, { name: e.target.value })} />
      </div>
      <div className="inv-set-cell grow">
        <input className="inv-in" value={r.amountFields} placeholder="金额字段，用逗号隔开，如：实际付款总额，付款总额" onChange={e => upd(i, { amountFields: e.target.value })} />
      </div>
      <label className="inv-set-ck"><input type="checkbox" checked={!!r.allowLater} onChange={e => upd(i, { allowLater: e.target.checked })} />可登记后补</label>
      <button type="button" className="inv-set-del" onClick={() => setRows(rows.filter((_, j) => j !== i))}>移除</button>
      <div className="inv-set-full"><Msg err={errors['templates.' + i]} warn={warns['templates.' + i]} /></div>
    </div>)}</div> : <div className="inv-lt-empty">还没配审批模板：扫审批单编号时找不到单子。点右上「加一个模板」。</div>}
  </Section>
}

// ───────────────────────── ④ 钉钉自动接入 ─────────────────────────

const INTAKE_DESC = '设置里③那些模板的审批单，审批流一走到下面这几位的节点，系统就自动建票夹、拉附件、识别发票，不用再扫审批码。'
  + '单走到谁的节点，票夹就归谁审；一张单经过几位的，归最先走到、且已启用的那位。'
  + '还没走到的单子系统会一直跟着，走到了再接；撤回、被拒的不接。每 20 分钟扫一轮。'

function IntakeSection({ v, set, dirty, errors }) {
  const [st, setSt] = useState(null)
  const [stErr, setStErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [addErr, setAddErr] = useState('')
  const [frErr, setFrErr] = useState('')
  const firsts = v.firstReviewers || []
  const addFirst = (p) => {
    if (!p) return
    if (firsts.some(a => a.dtUserid === p.userid)) { setFrErr(`${p.name} 已经在里面了`); return }
    setFrErr('')
    set({ ...v, firstReviewers: [...firsts, { dtUserid: p.userid, dtName: p.name }] })
  }
  const loadSt = useCallback(() => {
    invIntake().then(r => { setSt(r); setStErr('') }).catch(e => setStErr(errText(e)))
  }, [])
  useEffect(() => { loadSt() }, [loadSt])
  const add = (p) => {
    if (!p) return
    if (v.approvers.some(a => a.dtUserid === p.userid)) { setAddErr(`${p.name} 已经在里面了`); return }
    setAddErr('')
    set({ ...v, approvers: [...v.approvers, { dtUserid: p.userid, dtName: p.name, since: '' }] })   // 新加的人先留空：等他开始用发票管家那天再填
  }
  const setSince = (uid, d) => set({ ...v, approvers: v.approvers.map(a => (a.dtUserid === uid ? { ...a, since: d } : a)) })
  const enabled = v.approvers.filter(a => a.since)
  const scan = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await invIntakeScan()
      setMsg({ ok: true, t: `扫完了：新建票夹 ${r.created} 个，还没走到的 ${r.waiting} 张` + (r.failed ? `，${r.failed} 张没取到/没建成` : '') })
      loadSt()
    } catch (e) { setMsg({ ok: false, t: errText(e) }) } finally { setBusy(false) }
  }
  const last = st && st.last
  return <Section title="④ 钉钉自动接入" sub={INTAKE_DESC}
    extra={<button type="button" className="btn" onClick={scan} disabled={busy || dirty || !enabled.length}
      title={dirty ? '先保存设置再扫' : (!v.approvers.length ? '先加接入审批人' : !enabled.length ? '先给开始用发票管家的人填上启用日' : '')}>{busy ? '正在扫…' : '立即扫一次'}</button>}>
    <div className="inv-set-addl" style={{ marginBottom: 6 }}>接入审批人（同时就是审票分工表）</div>
    {!!v.approvers.length && <table className="inv-set-aps">
      <thead><tr><th>接入审批人</th><th>启用日</th><th>现在的情况</th><th></th></tr></thead>
      <tbody>{v.approvers.map(a => <tr key={a.dtUserid}>
        <td><b>{a.dtName || a.dtUserid}</b></td>
        <td><input type="date" className="inv-in inv-num" style={{ width: 160 }} value={a.since || ''} onChange={e => setSince(a.dtUserid, e.target.value)} />
          {!a.since && <span className="inv-muted" style={{ marginLeft: 6 }}>留空</span>}</td>
        <td>{a.since ? <>已启用 · 只接 {a.since} 当天及以后提交的单</> : <span className="inv-muted">还没启用 · 单经过这位不接</span>}</td>
        <td><button type="button" className="inv-set-del" title="从名单里拿掉"
          onClick={() => set({ ...v, approvers: v.approvers.filter(x => x.dtUserid !== a.dtUserid) })}>×</button></td>
      </tr>)}</tbody>
    </table>}
    <div className="inv-set-add" style={{ marginTop: 8 }}>
      <PersonPicker value={null} onChange={add} placeholder="打名字，从钉钉通讯录里搜，加一位接入审批人" />
    </div>
    {addErr && <div className="inv-lt-fe">{addErr}</div>}
    <Msg err={errors['intake.since']} />
    <div className="inv-set-ss">只有<b>提交日</b>在启用日当天及以后的单才接进来、记在这位名下。启用日留空＝还没启用，单经过他不接
      （还在用票总管的同事先留空，等他切过来那天再填）。改了谁的启用日，只从他的启用日起重新找一遍他的单，别人不受影响。</div>
    {!v.approvers.length && <div className="inv-set-warn">还没加人：自动接入不会运行，仍可在收票台手工扫审批码。</div>}
    {!!v.approvers.length && !enabled.length && <div className="inv-set-warn">名单里的人都还没填启用日：自动接入不会运行。</div>}
    <div className="inv-set-add" style={{ marginTop: 10 }}>
      <span className="inv-set-addl">财务初审人</span>
      {firsts.map(a => <span key={a.dtUserid} className="inv-badge">{a.dtName || a.dtUserid}
        <button type="button" className="inv-set-del" style={{ marginLeft: 6 }}
          onClick={() => set({ ...v, firstReviewers: firsts.filter(x => x.dtUserid !== a.dtUserid) })}>×</button></span>)}
      <PersonPicker value={null} onChange={addFirst} placeholder="打名字，从钉钉通讯录里搜" />
    </div>
    {frErr && <div className="inv-lt-fe">{frErr}</div>}
    <div className="inv-set-ss">钉钉流程里的发票按财务初审分：初审人点同意之前就有的，算这张单的正常发票；之后才发到流程里的（多半在评论区），算后补票，
      系统记到这张单的后补单上、再进发票审核。可以加多位；一张单的流程没经过这里的人，就按上面接入审批人那一步算。
      {!firsts.length && ' 现在还没加人：都按接入审批人那一步算。'}</div>
    <div className="inv-set-ss" style={{ marginTop: 10 }}>
      {stErr ? <>扫描情况没读出来：{stErr}</>
        : !st ? '正在读扫描情况…'
        : !last ? '还没扫过。保存设置后约 20 分钟内自动扫第一轮，或点右上「立即扫一次」。'
        : <>上次扫描 {last.at}（{last.trigger}）：看了 {last.listed} 张，新建票夹 {last.created} 个，还没走到接入审批人的 {st.waiting} 张
          {last.failed ? `，${last.failed} 张没取到/没建成` : ''}{last.notes ? `；${last.notes}` : ''}
          {st.running ? '；正在扫…' : ''}</>}
    </div>
    {msg && <div className={msg.ok ? 'inv-set-ss' : 'inv-lt-fe'}>{msg.t}</div>}
  </Section>
}

// ───────────────────────── ⑤ 催票 ⑥ 拦截 ⑦ 高级 ─────────────────────────

function RemindSection({ v, set, errors }) {
  const up = (k, x) => set({ ...v, [k]: x })
  const numIn = (k, w) => <input className="inv-in inv-num inv-set-n" style={{ width: w }} inputMode="numeric" value={v[k]}
    disabled={!v.enabled} onChange={e => up(k, e.target.value.replace(/[^\d]/g, ''))} />
  return <Section title="⑤ 催票规则" sub="系统自动给后补单的申请人发钉钉提醒，抄送财务接收人。">
    <label className="inv-set-ck big"><input type="checkbox" checked={!!v.enabled} onChange={e => up('enabled', e.target.checked)} />自动催票</label>
    <div className={'inv-set-rule' + (v.enabled ? '' : ' off')}>
      <div>预计到票日前 {numIn('beforeDays', 56)} 天提醒一次<Msg err={errors['remind.beforeDays']} /></div>
      <div>超期后每 {numIn('everyDays', 56)} 天再提醒一次<Msg err={errors['remind.everyDays']} /></div>
      <div>每天 {numIn('hour', 56)} 点发送<Msg err={errors['remind.hour']} /></div>
    </div>
    {!v.enabled && <div className="inv-set-warn">关掉后只能在后补池里手动点「催一下」。</div>}
  </Section>
}

function BlockSection({ v, set }) {
  return <Section title="⑥ 没附发票、又没登记后补的付款单" sub="收票台扫到这种付款单时怎么处理。">
    <div className="inv-set-radios">
      <label className={'inv-set-radio' + (!v ? ' on' : '')}><input type="radio" name="inv-set-block" checked={!v} onChange={() => set(false)} />
        <span><b>只提醒</b><em>亮黄牌提示，照样能提交审核</em></span></label>
      <label className={'inv-set-radio' + (v ? ' on' : '')}><input type="radio" name="inv-set-block" checked={!!v} onChange={() => set(true)} />
        <span><b>拦下来</b><em>先登记后补单或补上发票，才能提交</em></span></label>
    </div>
  </Section>
}

function AdvancedSection({ v, set, err }) {
  const [edit, setEdit] = useState(false)
  return <details className="inv-set-adv" open={!!err || undefined}>
    <summary>⑦ 高级：站点地址</summary>
    <div className="inv-set-advin">
      <div className="inv-set-ss">手机配对码、钉钉消息里的链接用这个地址开头。留空＝用电脑上正在用的地址（一般不用填）；
        正式域名开通后（备案＋https）再填上，例如 https://finance.starfieldsz.com。</div>
      <div className="inv-set-line">
        <div className="inv-set-cell grow">
          <input className="inv-in inv-num" value={v} readOnly={!edit} onChange={e => set(e.target.value)} placeholder="留空＝用电脑上正在用的地址" />
          <Msg err={err} />
        </div>
        {!edit && <button type="button" className="btn" onClick={() => setEdit(true)}>修改</button>}
      </div>
    </div>
  </details>
}

// ───────────────────────── ⑧ 清空试运行数据（只有主管理员） ─────────────────────────

const mb = n => (Number(n || 0) / 1048576).toFixed(1)

/** 清空试运行数据。dirty＝上面的设置有没保存的改动（清零会改「钉钉自动接入」的起始日，先让人把改动落定）；onDone＝清完重读设置。 */
// ───────────────────────── 后补票自动转待认证（开关单独存，点了就生效，不跟下面的「保存」走） ─────────────────────────

const L3_MODES = [
  ['off', '关', '系统什么都不做，转待认证凭证还是人在金蝶手工做。'],
  ['dry', '演练', '只算不写：系统照样去金蝶找原凭证、算分录，把「打算怎么做」记在后补票上给你看，不往金蝶写任何东西。'],
  ['on', '真做', '对得上的，系统往金蝶新建「暂估转待认证」凭证并提交（不审核），凭证号记到这张发票上，并在首页待办提醒审核人。只管切到真做以后审核通过的票；之前的不自动补做，要做在审核弹窗「核销暂估」里点「现在做」。'],
]

function LaterVoucherSection() {
  const [c, setC] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { invLaterVoucherConfig().then(setC).catch(e => setErr(errText(e))) }, [])
  const set = async (mode) => {
    if (mode === 'on' && !window.confirm('切到「真做」后，后补票审核通过、后补单收齐，系统会往金蝶新建「暂估转待认证」凭证并提交（不审核）。确定切换吗？')) return
    setBusy(true); setErr('')
    try { const r = await invLaterVoucherConfigSet(mode); setC(x => ({ ...(x || {}), ...r })) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  if (!c && !err) return null
  const n = (c && c.count) || {}
  return <Section title="后补票自动转待认证" sub={'后补单的发票到了、你审核通过、后补单收齐以后，那张「暂估转待认证」凭证（借待认证进项税额／贷暂估进项税）由系统在后台做。'
    + '系统到金蝶找这张付款单当时的费用凭证（同一个主体、供应商往来贷方＝付款金额、挂同一家供应商），发票税额和账上暂估对得上才做；'
    + '对不上、找到好几张、找不到的不自动做，在审核弹窗「核销暂估」里写明原因，并发钉钉提醒。物流请款单不归这里，由「付款做账」那条线做。'}>
    {c && <div className="inv-set-add">
      <span className="inv-set-addl">现在是</span>
      {L3_MODES.map(([k, label]) => <button key={k} type="button" className={c.mode === k ? 'btn-pri' : 'btn'} disabled={busy || c.mode === k}
        onClick={() => set(k)}>{label}</button>)}
      <span className="inv-muted">{c.by ? `${c.by} 于 ${c.at} 设的` : '默认（还没人改过）'}</span>
    </div>}
    {c && <div className="inv-set-ss">{(L3_MODES.find(m => m[0] === c.mode) || [])[2]}
      {' '}到现在：已做 {n.done || 0} 张，演练 {n.dry || 0} 张，要人工 {n.manual || 0} 张，不用做 {n.none || 0} 张。切到「真做」只有主管理员能点。</div>}
    {err && <div className="inv-lt-fe">{err}</div>}
  </Section>
}

function ResetSection({ dirty, onDone }) {
  const [pv, setPv] = useState(null)        // null＝还没读到 / 不是主管理员（整块不画）
  const [since, setSince] = useState('')
  const [phrase, setPhrase] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  const load = useCallback(keepDate => invResetPreview().then(r => {
    if (!alive.current) return
    setPv(r); if (!keepDate) setSince(str(r.since))
  }).catch(() => { if (alive.current) setPv(null) }), [])
  useEffect(() => { load() }, [load])
  if (!pv) return null
  const c = pv.clear || {}, k = pv.keep || {}
  const blockers = [...(pv.busy || [])]
  if (pv.scanning) blockers.push('钉钉自动接入正在扫单')
  if (dirty) blockers.push('上面的设置有改动还没保存：先保存或撤销')
  const nothing = !c.folders && !c.invoices && !c.others && !c.files && !c.logs
  const dateOk = /^\d{4}-\d{2}-\d{2}$/.test(since)
  const ready = !busy && !blockers.length && dateOk && phrase.trim() === pv.phrase
  const run = async () => {
    setBusy(true); setErr(''); setDone(null)
    try {
      const r = await invResetRun(phrase.trim(), since)
      if (!alive.current) return
      setDone(r); setPhrase('')
      await load(true)
      onDone?.()
    } catch (e) { if (alive.current) setErr(errText(e)) } finally { if (alive.current) setBusy(false) }
  }
  return <details className="inv-set-adv inv-set-reset">
    <summary>⑧ 清空试运行数据（只有主管理员看得到）</summary>
    <div className="inv-set-advin">
      <div className="inv-set-ss">把试运行攒下的票夹、发票、附件和操作记录清掉，重新开始。<b>后补单和它挂的票夹留着</b>，
        人员、公司抬头、审批模板这些设置不动。清之前系统先在服务器上留一份底；原件是挪进留底目录，不是删掉。</div>
      <div className="inv-set-rs">
        <div className="inv-set-rsbox del">
          <div className="inv-set-rsh">会清掉</div>
          <ul>
            <li>票夹 <b>{c.folders || 0}</b> 个</li>
            <li>发票 <b>{c.invoices || 0}</b> 张（含审核通过、等审核、还在收票的）</li>
            <li>其他附件 <b>{c.others || 0}</b> 条</li>
            <li>原始文件 <b>{c.files || 0}</b> 个（{mb(c.bytes)} MB）</li>
            <li>操作记录 <b>{c.logs || 0}</b> 条、新销方名单 <b>{c.sellers || 0}</b> 个</li>
          </ul>
        </div>
        <div className="inv-set-rsbox">
          <div className="inv-set-rsh">会留下</div>
          <ul>
            <li>后补单 <b>{k.laters || 0}</b> 张</li>
            <li>后补单挂的票夹 <b>{k.folders || 0}</b> 个，里面的票据 <b>{k.items || 0}</b> 条、文件 <b>{k.files || 0}</b> 个</li>
            <li>后补单的经办记录、期初导入的清单</li>
            <li>这一页的全部设置</li>
          </ul>
        </div>
      </div>
      <div className="inv-set-line inv-set-rsline">
        <span>清完以后，钉钉审批单从</span>
        <input type="date" className="inv-in" value={since} onChange={e => { setSince(e.target.value); setDone(null) }} disabled={busy} aria-label="重新接钉钉审批单的起始日" />
        <span>起重新接。</span>
      </div>
      <div className="inv-set-ss">这一天以后提交、已经走到接入审批人的审批单，会被系统自动重新拉进来（已有票夹的不会重复拉），
        票要重新审核。几分钟后到收票工作台看。钉钉最多只能往前取 {pv.maxDays || 119} 天。
        <b>各人的启用日不会跟着改</b>：比某位启用日还早提交的单照旧不接——要他从更早的日子接，先在上面④把他的启用日改早、保存，再来清。</div>
      {blockers.map((b, i) => <div key={i} className="inv-set-warn">现在还不能清：{b}。</div>)}
      {nothing && !done && <div className="inv-set-ss">现在没有可清的数据。</div>}
      <div className="inv-set-line inv-set-rsline">
        <span>确认要清，请在框里打「{pv.phrase}」两个字：</span>
        <input className="inv-in inv-set-rsin" value={phrase} onChange={e => setPhrase(e.target.value)} disabled={busy} placeholder={pv.phrase} aria-label="确认字样" />
        <button type="button" className="btn inv-set-rsbtn" onClick={run} disabled={!ready}>{busy ? '正在清…' : '清空试运行数据'}</button>
      </div>
      {err && <div className="inv-lt-fe">{err}</div>}
      {done && <div className="inv-set-rsok">{done.msg}
        {(done.fileErrors || []).length > 0 && <div>有 {done.fileErrors.length} 个原件没挪进留底目录（还在原地，没丢），请告诉管理员。</div>}</div>}
    </div>
  </details>
}

// ───────────────────────── 页面 ─────────────────────────

/**
 * 发票管家设置。
 * @param {object} p
 * @param {object} [p.user]
 * @param {boolean} [p.embedded] 嵌在别的页面里（不画页头）
 * @param {(settings: object) => void} [p.onSaved] 保存成功后回调（例：后补池重拉接收人名单）
 * @param {(dirty: boolean) => void} [p.onDirtyChange] 有无未保存改动变化时回调（放在弹窗里时，宿主可据此在关弹窗前问一句）
 */
export default function InvSettings({ user, embedded, onSaved, onDirtyChange }) {
  const [raw, setRaw] = useState(null)        // 后端原样设置（保存时保留本页不编辑的键）
  const [base, setBase] = useState(null)      // 载入时的草稿，用来判断有没有改动
  const [d, setD] = useState(null)
  const [loadErr, setLoadErr] = useState('')
  const [accounts, setAccounts] = useState(null)
  const [accErr, setAccErr] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState('')
  const [tried, setTried] = useState(false)   // 点过保存才把错误全亮出来，免得一打开就满屏红
  const [toast, flash] = useToast()
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const load = useCallback(() => {
    setLoadErr('')
    invSettings().then(r => {
      if (!alive.current) return
      const s = (r && r.settings) || {}
      const dr = toDraft(s)
      setRaw(s); setBase(sig(dr)); setD(dr); setTried(false); setSaveErr('')
    }).catch(e => { if (alive.current) setLoadErr(errText(e)) })
    invAccounts().then(r => { if (alive.current) { setAccounts((r && r.rows) || []); setAccErr('') } })
      .catch(e => { if (alive.current) setAccErr(errText(e)) })
  }, [])
  useEffect(() => { load() }, [load])

  const dirty = !!d && base !== null && sig(d) !== base
  const { errors, warns } = useMemo(() => (d ? validate(d) : { errors: {}, warns: {} }), [d])
  const nErr = Object.keys(errors).length
  const shownErr = tried ? errors : {}

  const dirtyCb = useRef(onDirtyChange)
  dirtyCb.current = onDirtyChange
  useEffect(() => { dirtyCb.current?.(dirty) }, [dirty])
  // 有没保存的改动时关页面/刷新先问一句
  useEffect(() => {
    if (!dirty) return undefined
    const h = e => { e.preventDefault(); e.returnValue = ''; return '' }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  const put = (k) => (v) => { setD(prev => ({ ...prev, [k]: v })); setSaveErr('') }

  const save = async () => {
    setTried(true)
    if (nErr) { setSaveErr(`还有 ${nErr} 处要改（标红的地方），改好再保存`); return }
    setSaving(true); setSaveErr('')
    try {
      const r = await invSaveSettings(toPayload(d, raw))
      const s = (r && r.settings) || toPayload(d, raw)
      const dr = toDraft(s)
      setRaw(s); setBase(sig(dr)); setD(dr); setTried(false)
      flash('设置已保存', 'ok')
      onSaved?.(s)
    } catch (e) { setSaveErr('没保存上：' + errText(e)) } finally { setSaving(false) }
  }
  const revert = () => { if (raw) { const dr = toDraft(raw); setD(dr); setBase(sig(dr)); setTried(false); setSaveErr('') } }

  const head = !embedded && <div className="head">
    <div>
      <div className="h-title">发票管家设置</div>
      <div className="h-sub">财务人员与钉钉绑定、本公司抬头、审批模板、钉钉自动接入、催票规则。改完点最下面的「保存」才生效。</div>
    </div>
    {dirty && <span className="inv-badge warn">有改动还没保存</span>}
  </div>

  let body
  if (loadErr) {
    body = <div className="banner err">设置没读出来：{loadErr}
      <button type="button" className="btn" style={{ marginLeft: 'auto' }} onClick={load}>重试</button></div>
  } else if (!d) {
    body = <div className="loading">正在读取设置…</div>
  } else {
    body = <>
      <PeopleSection rows={d.people} setRows={put('people')} accounts={accounts} accErr={accErr} errors={shownErr} warns={warns} />
      <CompanySection rows={d.company} setRows={put('company')} errors={shownErr} warns={warns} />
      <TemplateSection rows={d.templates} setRows={put('templates')} errors={shownErr} warns={warns} />
      <IntakeSection v={d.intake} set={put('intake')} dirty={dirty} errors={shownErr} />
      <RemindSection v={d.remind} set={put('remind')} errors={shownErr} />
      <BlockSection v={d.blockNoInvoice} set={put('blockNoInvoice')} />
      <AdvancedSection v={d.portalUrl} set={put('portalUrl')} err={shownErr.portalUrl} />
      <LaterVoucherSection />
      <ResetSection dirty={dirty} onDone={load} />
      <div className={'inv-set-bar' + (dirty ? ' dirty' : '')}>
        <span className="inv-set-state">{saving ? '正在保存…' : dirty ? '● 有改动还没保存' : '已是最新，没有改动'}</span>
        {saveErr && <span className="inv-lt-inline err">{saveErr}</span>}
        <span className="inv-set-grow" />
        <button type="button" className="btn" onClick={revert} disabled={!dirty || saving}>撤销改动</button>
        <button type="button" className="btn-pri" onClick={save} disabled={!dirty || saving}>{saving ? '保存中…' : '保存'}</button>
      </div>
    </>
  }

  return <div className="inv-set">
    {head}
    <div className="body">{body}</div>
    {toast}
  </div>
}
