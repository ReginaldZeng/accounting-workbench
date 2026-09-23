// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票管家设置：财务人员名单（钉钉绑定＋接收人）、本公司抬头、审批模板、催票规则、没附发票的付款单拦/提醒、站点地址；未保存提示＋逐项校验
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 和后端校验对齐：名单里没绑工作台账号的人直接挡保存并在行内说清楚（后端本来就拒）；
//   催票天数范围改成后端实际收的 0～30 / 1～60（原来前端放 60/90，后端悄悄压成 30/60）。
// 需求确认书 v1.4 五/十一 + 技术方案 §3「设置」+ §5.2 GET/POST /api/inv/settings。
// 既能当后补池的「设置」页签（embedded，不再画页头），也能被收票工作台放进 Modal 里用——宽度全跟容器走，不写死页面宽。
// 保存时把后端给的其它键（corpId 等本页不编辑的）原样带回去，免得本页一保存把别处的配置抹掉。
import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { invSettings, invSaveSettings, invAccounts } from '../api.js'
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
  if (u && !/^https?:\/\/[^\s/]+/i.test(u)) errors.portalUrl = '要以 https:// 开头的完整网址，例如 https://finance.example.com'
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

// ───────────────────────── ④ 催票 ⑤ 拦截 ⑥ 高级 ─────────────────────────

function RemindSection({ v, set, errors }) {
  const up = (k, x) => set({ ...v, [k]: x })
  const numIn = (k, w) => <input className="inv-in inv-num inv-set-n" style={{ width: w }} inputMode="numeric" value={v[k]}
    disabled={!v.enabled} onChange={e => up(k, e.target.value.replace(/[^\d]/g, ''))} />
  return <Section title="④ 催票规则" sub="系统自动给后补单的申请人发钉钉提醒，抄送财务接收人。">
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
  return <Section title="⑤ 没附发票、又没登记后补的付款单" sub="收票台扫到这种付款单时怎么处理。">
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
    <summary>⑥ 高级：站点地址</summary>
    <div className="inv-set-advin">
      <div className="inv-set-ss">钉钉消息里的「点这里查看」链接用这个地址开头。一般不用改；换了域名才改。</div>
      <div className="inv-set-line">
        <div className="inv-set-cell grow">
          <input className="inv-in inv-num" value={v} readOnly={!edit} onChange={e => set(e.target.value)} placeholder="https://…" />
          <Msg err={err} />
        </div>
        {!edit && <button type="button" className="btn" onClick={() => setEdit(true)}>修改</button>}
      </div>
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
      <div className="h-sub">财务人员与钉钉绑定、本公司抬头、审批模板、催票规则。改完点最下面的「保存」才生效。</div>
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
      <RemindSection v={d.remind} set={put('remind')} errors={shownErr} />
      <BlockSection v={d.blockNoInvoice} set={put('blockNoInvoice')} />
      <AdvancedSection v={d.portalUrl} set={put('portalUrl')} err={shownErr.portalUrl} />
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
