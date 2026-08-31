// [Change Log] Date:2026-08-01 Author:Claude/c Version:V2.159
// 汇率录入·前端页（P3）。两页签：①本期建汇率（四步：选期间+组织 → 取人行数 → 核对预览+闸门 → 写入结果）
// ②历史复核（金蝶已建对回人行，偏差+已知豁免）。样式取自已签样机 v0.1（fx- 前缀作用域，复用工作台 CSS 变量）。
// 写入走「只提交、不审核」（确认书 v1.1 D11）；组织可选下拉、默认 101（D9）；四道机器闸门（D17）。
import React, { useEffect, useState } from 'react'
import { getFxOrgs, getFxStatus, autorunFxRate, getFxAutorunConfig, toggleFxAutorun, previewFxRate, postFxRate, getFxPosted, unpostFxRate, fxRateHistory, getFxNotifyConfig, saveFxNotifyConfig, testFxNotify } from '../api.js'

const YEARS = [2026, 2025]
const MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
const STEPS = [
  { n: 1, t: '选期间', d: '结账期间与组织' },
  { n: 2, t: '取人行数', d: '抓公告并解析' },
  { n: 3, t: '核对预览', d: '逐条比对 + 闸门' },
  { n: 4, t: '写入金蝶', d: '确认后录入' },
]
const GATE_CLASS = { ok: 'ok', block: 'bad', hold: 'next', warn: 'exist' }
const GATE_WORD = { ok: '通过', block: '拦下', hold: '挂起', warn: '未校验' }

function defaultYM() {              // 默认上一个已结束的月（本工具建的是已结账月）
  const n = new Date()
  let y = n.getFullYear(), m = n.getMonth()   // getMonth() 0-11：8月得 7＝上月7月
  if (m === 0) { m = 12; y -= 1 }
  return { y, m }
}

export default function FxRate({ user }) {
  const can = k => !!(user && (user.role === 'admin' || (user.perms || {})[k]))
  const canPost = can('fxrate_post')
  const d0 = defaultYM()

  const [tab, setTab] = useState('status')   // 默认落在「录入状态」——自动录入下，第一眼看的是"本月录了没/审核了没"
  const [orgs, setOrgs] = useState([{ code: '101', name: '深圳星期零' }, { code: '107', name: '孝感星期九' }])
  const [org, setOrg] = useState('101')
  const [year, setYear] = useState(d0.y)
  const [month, setMonth] = useState(d0.m)
  const [step, setStep] = useState(1)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [pv, setPv] = useState(null)
  const [postRes, setPostRes] = useState(null)
  // 本期已录入 / 撤销
  const [posted, setPosted] = useState(null)
  const [pSel, setPSel] = useState(new Set())
  const [pBusy, setPBusy] = useState(false)
  // 历史复核
  const [hFrom, setHFrom] = useState('2025-10-01')
  const [hist, setHist] = useState(null)
  const [hBusy, setHBusy] = useState(false)
  // 录入状态看板
  const [status, setStatus] = useState(null)
  const [sBusy, setSBusy] = useState(false)
  const [detailMonth, setDetailMonth] = useState(null)
  // 本期建汇率：先选 手动 / 自动
  const [buildMode, setBuildMode] = useState('manual')
  // 预演/立即跑批（模拟"次月首个工作日14:00自动跑批"；用建汇率页的 year/month/org）
  const [arBusy, setArBusy] = useState('')
  const [arReport, setArReport] = useState(null)
  const [arCfg, setArCfg] = useState(null)   // 自动跑批配置/状态（是否启用/下次检查时点）
  // 通知设置
  const [nCfg, setNCfg] = useState(null)     // 后端读回的当前配置 + 密钥状态
  const [nForm, setNForm] = useState(null)   // 编辑中的表单（收发件人用多行文本）
  const [nPass, setNPass] = useState('')     // 改动口令
  const [nBusy, setNBusy] = useState('')
  const [nMsg, setNMsg] = useState('')

  useEffect(() => { if (tab === 'build' && buildMode === 'auto' && !arCfg) getFxAutorunConfig().then(r => { if (r.ok) setArCfg(r) }).catch(() => {}) }, [tab, buildMode])

  useEffect(() => { getFxOrgs().then(r => { if (r.ok) { setOrgs(r.orgs); setOrg(r.default || '101') } }).catch(() => {}) }, [])
  // 进「录入状态」或切年/组织时自动拉状态
  useEffect(() => { if (tab === 'status' && org) loadStatus() }, [tab, org, year])
  // 进「通知设置」拉当前收发件人 + 密钥状态
  useEffect(() => { if (tab === 'notify' && canPost && !nForm) loadNotify() }, [tab])

  const orgName = c => (orgs.find(o => o.code === c) || {}).name || c
  const gates = pv && pv.gates ? pv.gates.gates : []
  const blocking = gates.some(g => g.status === 'block')
  const meRows = pv ? pv.rows.filter(r => r.kind === 'month_end') : []
  const nrRows = pv ? pv.rows.filter(r => r.kind === 'next_range') : []

  const doPreview = async () => {
    setErr(''); setBusy('preview'); setPv(null); setPostRes(null); setPosted(null)
    const r = await previewFxRate({ year, month, org })
    setBusy('')
    if (!r.ok) { setErr(r.msg || '取数失败'); return }
    setPv(r); setStep(2)
  }
  const doPost = async () => {
    if (!pv) return
    if (!window.confirm(`确认把 ${year}年${month}月 · ${orgName(org)}(${org}) 的汇率写入金蝶？\n\n· 写入并提交，但不审核——审核（生效）请到金蝶完成\n· 已存在同组织·同币对·同生效区间的自动跳过、不覆盖\n· 写入前重新抓取人行数据，以最新公告为准`)) return
    setErr(''); setBusy('post')
    const r = await postFxRate({ year, month, org })
    setBusy('')
    if (!r.ok) { setErr(r.msg || '写入失败'); if (r.gates) setPv(p => ({ ...p, gates: r.gates })); return }
    setPostRes(r); setStep(4)
  }
  const _lines = arr => (arr || []).join('\n')
  const loadNotify = async () => {
    setNMsg('')
    const r = await getFxNotifyConfig().catch(e => ({ ok: false, msg: String(e.message || e) }))
    if (!r || !r.ok) { setNMsg('✗ ' + ((r && r.msg) || '读取失败')); return }
    setNCfg(r)
    setNForm({
      dt_mobiles: _lines(r.dt_mobiles), dt_userids: _lines(r.dt_userids),
      mail_to: _lines(r.mail_to), mail_cc: _lines(r.mail_cc), mail_bcc: _lines(r.mail_bcc),
      dingtalk_on: r.dingtalk_on !== false, email_on: r.email_on !== false,
    })
  }
  const saveNotify = async () => {
    if (!nForm || !nPass) return
    setNBusy('save'); setNMsg('')
    const r = await saveFxNotifyConfig({ ...nForm, passcode: nPass })
      .catch(e => ({ ok: false, msg: String(e.message || e) }))
    setNBusy('')
    if (r && r.ok) { setNMsg('✓ 已保存'); setNPass(''); loadNotify() }
    else setNMsg('✗ ' + ((r && r.msg) || '保存失败'))
  }
  const testNotify = async () => {
    setNBusy('test'); setNMsg('')
    const r = await testFxNotify().catch(e => ({ ok: false, msg: String(e.message || e) }))
    setNBusy('')
    if (!r || !r.ok) { setNMsg('✗ ' + ((r && r.msg) || '测试失败')); return }
    const res = r.result || {}, parts = []
    if (res.dingtalk) parts.push('钉钉：' + (res.dingtalk.sent ? '已发' : '未发—' + (res.dingtalk.msg || '')))
    if (res.email) parts.push('邮件：' + (res.email.sent ? '已发' : '未发—' + (res.email.msg || '')))
    if (!res.dingtalk && !res.email) parts.push('两个渠道都未启用/未配置')
    setNMsg('测试结果 → ' + parts.join('；'))
  }
  const loadPosted = async () => {
    setErr(''); setPBusy(true); setPosted(null); setPSel(new Set())
    const r = await getFxPosted(year, month, org).catch(e => ({ ok: false, msg: String(e.message || e) }))
    setPBusy(false)
    if (!r.ok) { setErr(r.msg || '加载失败'); return }
    setPosted(r); setPSel(new Set((r.items || []).filter(it => it['可撤销']).map(it => it.id)))
  }
  const doUnpost = async () => {
    const ids = [...pSel]
    if (!ids.length) { setErr('请先勾选要撤销的记录'); return }
    if (!window.confirm(`确认撤销勾选的 ${ids.length} 条汇率？\n\n· 草稿直接删除；已提交的先撤销提交再删\n· 已审核的删不了，需先去金蝶反审核\n· 删除不可恢复，删后可重新录入`)) return
    setPBusy(true)
    const r = await unpostFxRate(ids).catch(e => ({ ok: false, msg: String(e.message || e) }))
    setPBusy(false)
    if (!r.ok) { setErr(r.msg || '撤销失败'); return }
    await loadPosted()
    if (tab === 'status') { getFxStatus(year, org).then(x => { if (x.ok) setStatus(x) }).catch(() => {}) }
    alert(`已撤销 ${r['撤销']} 条` + (r['拦下'] ? `，${r['拦下']} 条已审核未删（需去金蝶反审核）` : '') + (r['失败'] ? `，${r['失败']} 条失败` : ''))
  }
  const doHistory = async () => {
    setErr(''); setHBusy(true); setHist(null)
    const r = await fxRateHistory({ org, from_date: hFrom })
    setHBusy(false)
    if (!r.ok) { setErr(r.msg || '复核失败'); return }
    setHist(r)
  }
  const loadStatus = async () => {
    setErr(''); setSBusy(true); setStatus(null); setDetailMonth(null); setPosted(null)
    const r = await getFxStatus(year, org).catch(e => ({ ok: false, msg: String(e.message || e) }))
    setSBusy(false)
    if (!r.ok) { setErr(r.msg || '加载状态失败'); return }
    setStatus(r)
  }
  const openStatusMonth = (m) => {   // 明细直接用看板已返回的金蝶记录，不再另拉
    setMonth(m); setDetailMonth(m); setErr('')
    const mm = (status && status.months || []).find(x => x.month === m)
    setPSel(new Set((mm && mm.records || []).filter(r => r.revocable).map(r => r.log_id)))
  }
  const doUnpostStatus = async () => {
    const ids = [...pSel]
    if (!ids.length) { setErr('没有可撤销的记录（人工录入或已审核的不在此撤）'); return }
    if (!window.confirm(`确认撤销勾选的 ${ids.length} 条（工具录入且未审核）？\n\n· 草稿直删、提交态先撤销提交再删\n· 人工录入 / 已审核的不动\n· 删除不可恢复，删后可重录`)) return
    setPBusy(true)
    const r = await unpostFxRate(ids).catch(e => ({ ok: false, msg: String(e.message || e) }))
    setPBusy(false)
    if (!r.ok) { setErr(r.msg || '撤销失败'); return }
    await loadStatus()
    alert(`已撤销 ${r['撤销']} 条` + (r['拦下'] ? `，${r['拦下']} 条已审核未删` : '') + (r['失败'] ? `，${r['失败']} 条失败` : ''))
  }
  const doAutorun = async (dry) => {
    const t = defaultYM()   // 自动＝上月，跟真实当前月份走（与定时一致）
    const aorg = (arCfg && arCfg.orgs && arCfg.orgs[0]) || org || '101'
    if (!dry && !window.confirm(`确认「立即跑批」${t.y}年${t.m}月（上月）· ${orgName(aorg)}？\n\n· 会真写入金蝶并提交（不审核）\n· 已存在的自动跳过、不覆盖\n· 闸门告警则整批挂起、不写\n（等同定时那次自动跑批，只是现在手动触发）`)) return
    setErr(''); setArBusy(dry ? 'dry' : 'run'); setArReport(null)
    const r = await autorunFxRate({ dry: dry ? 1 : 0, year: t.y, month: t.m, org: aorg }).catch(e => ({ ok: false, msg: String(e.message || e) }))
    setArBusy('')
    if (!r.ok) { setErr(r.msg || '跑批失败'); return }
    setArReport((r.reports && r.reports[0]) || null)
    if (!dry) loadStatus()
  }
  const doToggleAutorun = async () => {
    const on = !(arCfg && arCfg.setting)
    if (on && !window.confirm('开启后，服务器将在每工作日 14:00 自动建"上月结账"汇率并提交金蝶（不审核）。\n确认开启定时自动跑批？')) return
    const r = await toggleFxAutorun(on).catch(e => ({ ok: false, msg: String(e.message || e) }))
    if (!r.ok) { setErr(r.msg || '切换失败'); return }
    getFxAutorunConfig().then(x => { if (x.ok) setArCfg(x) }).catch(() => {})
  }

  const Tag = ({ c, children }) => <span className={'fx-tag ' + c}>{children}</span>
  const RateTable = ({ rows, title, tagc, tagw }) => (
    <>
      <div className="fx-grph"><span className={'fx-tag ' + tagc}>{tagw}</span> {title}</div>
      <div className="fx-tblwrap">
        <table><thead><tr><th>原币</th><th>目标币</th><th>直接汇率</th><th>来源 / 算式</th><th>取数日</th><th>状态</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={r.exists ? 'fx-exist' : ''}>
                <td>{r.from_name} {r.from_code}</td><td>{r.to_name}</td>
                <td className="fx-num"><b>{r.rate}</b></td>
                <td><span className="fx-calc">{r.basis}</span></td>
                <td className="fx-num">{r.source_date}</td>
                <td>{r.exists ? <Tag c="exist">已存在，跳过</Tag> : <Tag c="new">新建</Tag>}</td>
              </tr>
            ))}
          </tbody></table>
      </div>
    </>
  )

  return (
    <div className="fx-wrap">
      <FxStyle />
      <div className="head"><div>
        <div className="h-title">汇率录入</div>
        <div className="h-sub">从人民银行「人民币汇率中间价公告」取月末汇率，核对后写入金蝶（只提交、不审核）</div>
      </div></div>

      <div className="fx-tabs">
        <div className={'fx-tab' + (tab === 'status' ? ' on' : '')} onClick={() => setTab('status')}>录入状态</div>
        <div className={'fx-tab' + (tab === 'build' ? ' on' : '')} onClick={() => setTab('build')}>本期建汇率</div>
        <div className={'fx-tab' + (tab === 'audit' ? ' on' : '')} onClick={() => setTab('audit')}>历史复核</div>
        {canPost && <div className={'fx-tab' + (tab === 'notify' ? ' on' : '')} onClick={() => setTab('notify')}>通知设置</div>}
      </div>

      {err && <div className="fx-note danger" style={{ marginTop: 12 }}>{err}</div>}

      {/* ============ 录入状态看板 ============ */}
      {tab === 'status' && <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="fx-fbar">
          <span className="fx-flabel">年份</span>
          <select className="selctl" value={year} onChange={e => setYear(+e.target.value)}>{YEARS.map(y => <option key={y} value={y}>{y} 年</option>)}</select>
          <span className="fx-flabel">组织</span>
          <select className="selctl" value={org} onChange={e => setOrg(e.target.value)}>{orgs.map(o => <option key={o.code} value={o.code}>{o.code} {o.name}</option>)}</select>
          <span className="fx-spacer" />
          <button className="btn" onClick={loadStatus} disabled={sBusy}>{sBusy ? '刷新中…' : '刷新 ↻'}</button>
          <button className="btn-pri" onClick={() => setTab('build')}>去建本期汇率 ›</button>
        </div>
        <div className="fx-note">
          本看板<b>以金蝶为准</b>：金蝶里有没有、是「<b style={{ color: 'var(--amber)' }}>待审核</b>」还是「<b style={{ color: 'var(--green)' }}>已审核</b>」，历史与人工录入都照实显示；「工具/人工」看金蝶描述里的标记。
          本工具只写入并<b>提交</b>——<b>审核(生效)要人在金蝶点</b>，「待审核」= 还差人工审核。
          <br /><b>汇率全集团共享</b>：101/107 是<b>一条首尾相接、不重叠</b>的接力时间线（同币对同区间全集团唯一），下表含<b>全部组织</b>；上方「组织」用于跑批时写入的创建组织。
        </div>
        {sBusy && !status && <div className="fx-note">正在读取状态…</div>}
        {status && <div className="fx-mgrid">
          {status.months.map(mm => {
            const cls = { 未录入: 'muted', 待审核: 'warn', 已审核: 'ok', 部分审核: 'info' }[mm.state] || 'muted'
            return <div key={mm.month}
              className={'fx-mcell ' + cls + (detailMonth === mm.month ? ' active' : '') + (mm.present ? ' clickable' : '')}
              onClick={() => mm.present && openStatusMonth(mm.month)}
              title={mm.present ? '点看明细' : '本月金蝶里尚无汇率'}>
              <div className="fx-mtop"><b>{mm.month} 月</b>{mm.present ? <span className="fx-mcount">{mm.present} 条</span> : null}</div>
              <div className="fx-mstate">{mm.state}</div>
              {mm.present ? <div className="fx-mmeta">{mm.source} · 已审 {mm.audited}/{mm.present}</div> : <div className="fx-mmeta">—</div>}
            </div>
          })}
        </div>}
        {detailMonth && (() => {
          const mm = (status && status.months || []).find(x => x.month === detailMonth)
          if (!mm || !mm.records.length) return null
          const revocable = mm.records.filter(r => r.revocable)
          return <div>
            <div className="fx-grph" style={{ marginTop: 4 }}>{detailMonth} 月明细（金蝶为准）</div>
            <div className="fx-tblwrap">
              <table><thead><tr><th></th><th>币对</th><th>生效区间</th><th>汇率</th><th>金蝶状态</th><th>来源</th><th>创建组织</th><th>算式 / 出处（金蝶描述）</th></tr></thead>
                <tbody>
                  {mm.records.map((r, i) => (
                    <tr key={i}>
                      <td>{r.revocable
                        ? <input type="checkbox" checked={pSel.has(r.log_id)}
                          onChange={e => { const s = new Set(pSel); e.target.checked ? s.add(r.log_id) : s.delete(r.log_id); setPSel(s) }} />
                        : null}</td>
                      <td>{r.pair}</td><td className="fx-num">{r.beg}{r.end !== r.beg ? ' ~ ' + r.end : ''}</td>
                      <td className="fx-num">{r.rate}</td>
                      <td>{r.audited ? <Tag c="ok">已审核</Tag> : <Tag c="warn">{r.kd_status}</Tag>}</td>
                      <td>{r.source === '工具' ? <Tag c="new">工具</Tag> : <Tag c="exist">人工</Tag>}</td>
                      <td className="fx-num">{r.org || '—'}</td>
                      <td>{r.desc ? <span className="fx-calc">{r.desc}</span> : <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                    </tr>
                  ))}
                </tbody></table>
            </div>
            {canPost && revocable.length > 0 &&
              <button className="btn" style={{ marginTop: 8 }} onClick={doUnpostStatus} disabled={pBusy || pSel.size === 0}>
                {pBusy ? '处理中…' : `撤销勾选（${pSel.size}）`}</button>}
            <div className="fx-note" style={{ marginTop: 8 }}>只有<b>工具录入且未审核</b>的可从这里撤销；<b>人工录入</b>或<b>已审核</b>的请去金蝶处理。</div>
          </div>
        })()}
      </div>}

      {/* ============ 本期建汇率 ============ */}
      {tab === 'build' && <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="fx-modebar">
          <button className={'fx-modebtn' + (buildMode === 'manual' ? ' on' : '')} onClick={() => setBuildMode('manual')}>手动建汇率（逐步核对）</button>
          <button className={'fx-modebtn' + (buildMode === 'auto' ? ' on' : '')} onClick={() => setBuildMode('auto')}>自动跑批（定时 / 一键）</button>
        </div>
        {buildMode === 'manual' && <>
        <div className="steps">
          {STEPS.map(s => {
            const cls = 'step' + (s.n === step ? ' cur' : (s.n < step ? ' done' : ''))
            const clickable = s.n < step
            return <div className={cls} key={s.n} style={clickable ? { cursor: 'pointer' } : undefined}
              onClick={() => clickable && setStep(s.n)}>
              <div className="num">{s.n < step ? '✓' : s.n}</div>
              <div><div className="sn">{s.t}</div><div className="sd">{s.d}</div></div>
            </div>
          })}
        </div>

        {/* 步骤 1 */}
        {step === 1 && <>
          <div className="fx-fbar">
            <span className="fx-flabel">结账期间</span>
            <select className="selctl" value={year} onChange={e => setYear(+e.target.value)}>{YEARS.map(y => <option key={y} value={y}>{y} 年</option>)}</select>
            <select className="selctl" value={month} onChange={e => setMonth(+e.target.value)}>{MONTHS.map(m => <option key={m} value={m}>{m} 月</option>)}</select>
            <span className="fx-flabel">组织</span>
            <select className="selctl" value={org} onChange={e => setOrg(e.target.value)}>{orgs.map(o => <option key={o.code} value={o.code}>{o.code} {o.name}</option>)}</select>
            <span className="fx-spacer" />
            <button className="btn" onClick={loadPosted} disabled={pBusy}>查看本期已录入</button>
            <button className="btn-pri" onClick={doPreview} disabled={busy === 'preview'}>{busy === 'preview' ? '取数中…' : '取人行数并预览 ›'}</button>
          </div>
          <div className="fx-note">
            本期将建 <b>8 条</b>汇率：<b>月末条 5 条</b>（{year}-{String(month).padStart(2, '0')} 月末，供期末调汇）+ <b>次月区间条 3 条</b>（供次月记账）。
            币种为金蝶已启用的美元、港币、英镑，组织写入 <b>{orgName(org)}（{org}）</b>。
          </div>
          {posted && <PostedPanel />}
        </>}

        {/* 步骤 2：取人行数概览 + 闸门 */}
        {step === 2 && pv && <>
          <GateBar />
          <div className="fx-kpis">
            <Kpi l="人行公告" v="2 份" tone="ok" />
            <Kpi l="本期取用" v="3 币种" />
            <Kpi l="应建" v={(meRows.length + nrRows.length) + ' 条'} />
            <Kpi l="其中已存在" v={pv.n_exist + ' 条'} tone={pv.n_exist ? 'warn' : ''} />
          </div>
          <div className="fx-grph"><span className="fx-tag month">月末条取数</span> {year} 年 {month} 月最后一个公布日 = <b>{pv.month_end_ann.date}</b></div>
          <a className="fx-src" href={pv.month_end_ann.url} target="_blank" rel="noreferrer">↗ 打开人行原文核对</a>
          <div className="fx-grph"><span className="fx-tag next">区间条取数</span> 次月第一个公布日 = <b>{pv.next_range_ann.date}</b></div>
          <a className="fx-src" href={pv.next_range_ann.url} target="_blank" rel="noreferrer">↗ 打开人行原文核对</a>
          {pv.warnings && pv.warnings.length > 0 && <div className="fx-note warn">{pv.warnings.map((w, i) => <div key={i}>· {w}</div>)}</div>}
          <div className="fx-actions"><button className="btn" onClick={() => setStep(1)}>‹ 上一步</button><span className="fx-spacer" /><button className="btn-pri" onClick={() => setStep(3)}>核对逐条 ›</button></div>
        </>}

        {/* 步骤 3：逐条核对 + 写入 */}
        {step === 3 && pv && <>
          <GateBar />
          <div className="fx-note"><b>请逐条核对后再写入。</b>「来源/算式」可点上一步的链接回人行原文比对；交叉汇率人行不公布，由中间价相除得出，算式已列明。已存在的按「不覆盖」跳过。</div>
          <RateTable rows={meRows} title={`生效 = 失效 = ${pv.month_end_ann.date === '' ? '' : ''}月末当天，供期末调汇`} tagc="month" tagw="月末条" />
          <RateTable rows={nrRows} title="次月整月，供次月记账（不建交叉汇率）" tagc="next" tagw="次月区间条" />
          <div className="fx-actions">
            <button className="btn" onClick={() => setStep(2)}>‹ 上一步</button>
            <span className="fx-spacer" />
            <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>将写入 <b>{pv.n_new}</b> 条，跳过 {pv.n_exist} 条</span>
            {!canPost
              ? <button className="btn-pri" disabled title="无「汇率录入·写入金蝶」权限">无写入权限</button>
              : <button className="btn-pri" onClick={doPost} disabled={busy === 'post' || blocking || pv.n_new === 0}
                title={blocking ? '机器闸门未通过，不能写入' : (pv.n_new === 0 ? '没有需要新建的条目' : '')}>
                {busy === 'post' ? '写入中…' : '确认无误，写入金蝶 ›'}</button>}
          </div>
          {blocking && <div className="fx-note danger">机器闸门未通过（见上方红色闸门），已禁止写入。请先排除问题或改由手工建。</div>}
        </>}

        {/* 步骤 4：写入结果 */}
        {step === 4 && postRes && <>
          <div className="fx-kpis">
            <Kpi l="写入成功" v={postRes['写入']} tone="ok" />
            <Kpi l="跳过（已存在）" v={postRes['跳过']} />
            <Kpi l="失败" v={postRes['失败']} tone={postRes['失败'] ? 'bad' : ''} />
          </div>
          <div className="fx-note"><b>已写入金蝶 BD_Rate 并提交（未审核）。</b>请到金蝶完成审核（生效）。全量留痕已记录（取数日期 / 公告链接 / 生成值 / 金蝶内码 / 写入人 / 时间）。</div>
          <div className="fx-tblwrap">
            <table><thead><tr><th>币对</th><th>生效区间</th><th>汇率</th><th>金蝶内码</th><th>结果</th></tr></thead>
              <tbody>
                {postRes.results.map((r, i) => (
                  <tr key={i} className={r.status === 'skipped' ? 'fx-exist' : ''}>
                    <td>{r.pair}</td><td className="fx-num">{r.beg}{r.end !== r.beg ? ' ~ ' + r.end : ''}</td>
                    <td className="fx-num">{r.rate}</td><td className="fx-num">{r.kd_id || '—'}</td>
                    <td>{r.status === 'posted' ? <Tag c="ok">成功</Tag> : r.status === 'skipped' ? <Tag c="exist">跳过</Tag> : <Tag c="bad">失败</Tag>}
                      {r.msg && <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 6 }}>{r.msg}</span>}</td>
                  </tr>
                ))}
              </tbody></table>
          </div>
          <div className="fx-actions"><button className="btn" onClick={() => { setStep(1); setPv(null); setPostRes(null) }}>建下一期</button>
            <button className="btn-sec" onClick={loadPosted}>查看 / 撤销本期录入 ›</button></div>
          {posted && <PostedPanel />}
        </>}
        </>}
        {buildMode === 'auto' && <>
          <div className="fx-fbar">
            <span className="fx-flabel">自动跑批目标</span>
            <b>{d0.y} 年 {d0.m} 月结账</b>
            <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>（＝上月，自动跟随当前月份；不用手选）· 组织 {(arCfg && arCfg.orgs && arCfg.orgs[0]) || '101'}</span>
            <span className="fx-spacer" />
            <button className="btn" onClick={() => doAutorun(true)} disabled={!!arBusy}>{arBusy === 'dry' ? '预演中…' : '预演（不写）'}</button>
            {canPost && <button className="btn-pri" onClick={() => doAutorun(false)} disabled={!!arBusy}>{arBusy === 'run' ? '跑批中…' : '立即跑批 ›'}</button>}
          </div>
          <div className="fx-note">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
              <b>定时自动跑批</b>
              {arCfg
                ? (arCfg.enabled ? <span className="fx-tag ok">已启用 · 生效中</span>
                  : (arCfg.setting && arCfg.local) ? <span className="fx-tag next">已开启 · 本机为测试不触发（部署服务器即生效）</span>
                    : <span className="fx-tag exist">未启用</span>)
                : <span className="fx-tag exist">读取中…</span>}
              {canPost && arCfg && <button className="btn" onClick={doToggleAutorun}>{arCfg.setting ? '关闭定时' : '开启定时'}</button>}
            </div>
            每日 <b>{arCfg ? arCfg.hour : 14}:00</b> 检查<b>上月结账（当前＝{d0.y}年{d0.m}月）</b>：若金蝶未齐、人行次月已公布、四道闸门全绿 → 自动写入并提交（不审核）；缺数则静默等待到次日再看。<b>到了下月，它自动改建对应的上月，无需手动选。</b>
            <br />下次检查：<b>{arCfg ? arCfg.next_check : '—'}</b>　·　自动组织：<b>{arCfg && arCfg.orgs ? arCfg.orgs.join(' / ') : '—'}</b>
            <br /><span style={{ color: 'var(--ink-3)' }}>「预演 / 立即跑批」＝手动触发同一套逻辑，随时补跑或验证，不受定时开关影响。</span>
          </div>
          {arReport && <AutorunReport />}
        </>}
      </div>}

      {/* ============ 历史复核 ============ */}
      {tab === 'audit' && <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="fx-fbar">
          <span className="fx-flabel">组织</span>
          <select className="selctl" value={org} onChange={e => setOrg(e.target.value)}>{orgs.map(o => <option key={o.code} value={o.code}>{o.code} {o.name}</option>)}</select>
          <span className="fx-flabel">生效日自</span>
          <input className="selctl" style={{ width: 130 }} value={hFrom} onChange={e => setHFrom(e.target.value)} placeholder="2025-10-01" />
          <span className="fx-spacer" />
          <button className="btn-pri" onClick={doHistory} disabled={hBusy}>{hBusy ? '复核中…（逐月抓人行，稍候）' : '开始复核 ↻'}</button>
        </div>
        {!hist && !hBusy && <div className="fx-note">选择组织与起始生效日，把金蝶已建的汇率<b>对回人行</b>核验。对不上的只标不改（是否修正由人决定）。区间越宽抓取越久。</div>}
        {hist && <>
          <div className="fx-kpis">
            <Kpi l="已核对" v={hist.total + ' 条'} />
            <Kpi l="与人行一致" v={hist.counts['一致'] || 0} tone="ok" />
            <Kpi l="存在偏差" v={hist.counts['偏差'] || 0} tone={(hist.counts['偏差'] || 0) ? 'bad' : ''} />
            <Kpi l="已知豁免 / 无法核对" v={(hist.counts['已知豁免'] || 0) + ' / ' + (hist.counts['无法核对'] || 0)} />
          </div>
          <div className="fx-tblwrap">
            <table><thead><tr><th>组织</th><th>币对</th><th>生效日</th><th>金蝶账上</th><th>人行换算</th><th>差异</th><th>判定</th></tr></thead>
              <tbody>
                {hist.items.map((r, i) => {
                  const bad = r.verdict === '偏差'
                  return <tr key={i} className={bad ? 'fx-prio' : ''}>
                    <td>{r.org}</td><td>{r.pair}</td><td className="fx-num">{r.beg}</td>
                    <td className="fx-num"><b>{r.acct}</b></td><td className="fx-num">{r.pboc || '—'}</td>
                    <td className="fx-num" style={bad ? { color: 'var(--red)' } : undefined}>{r.diff && r.diff !== '0' ? r.diff : '—'}</td>
                    <td>{r.verdict === '一致' ? <Tag c="ok">一致</Tag> : r.verdict === '偏差' ? <Tag c="bad">偏差</Tag> : r.verdict === '已知豁免' ? <Tag c="exist">已知豁免</Tag> : <Tag c="exist">无法核对</Tag>}</td>
                  </tr>
                })}
              </tbody></table>
          </div>
        </>}
      </div>}

      {/* ============ 通知设置 ============ */}
      {tab === 'notify' && canPost && <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {!nForm ? <div className="fx-note">读取中…</div> : <>
          <div className="fx-note">这里设置【录入成功后】通知发给谁。<b>保存需口令</b>（口令写在后端，本页不显示）。钉钉密钥 / 邮箱密码只存服务器 conf.ini，本页不显示、不可改。</div>

          <div className="fx-statusrow">
            <span className="fx-flabel">密钥状态</span>
            <span className={'fx-tag ' + (nCfg.dingtalk_configured ? 'ok' : 'bad')}>钉钉密钥 {nCfg.dingtalk_configured ? '已配' : '未配'}</span>
            <span className={'fx-tag ' + (nCfg.smtp_configured ? 'ok' : 'bad')}>邮件 SMTP {nCfg.smtp_configured ? '已配' : '未配'}</span>
            <span className={'fx-tag ' + (nCfg.passcode_set ? 'ok' : 'bad')}>改动口令 {nCfg.passcode_set ? '后端已设' : '后端未设'}</span>
          </div>

          <div className="fx-ncard">
            <label className="fx-nhead"><input type="checkbox" checked={nForm.dingtalk_on} onChange={e => setNForm({ ...nForm, dingtalk_on: e.target.checked })} /> 钉钉通知</label>
            <div className="fx-ngrid" style={{ opacity: nForm.dingtalk_on ? 1 : .5 }}>
              <div className="fx-field"><label>钉钉手机号（每行一个）</label>
                <textarea className="fx-ta" disabled={!nForm.dingtalk_on} value={nForm.dt_mobiles} onChange={e => setNForm({ ...nForm, dt_mobiles: e.target.value })} placeholder="13800000000" /></div>
              <div className="fx-field"><label>钉钉 userid（可选，每行一个）</label>
                <textarea className="fx-ta" disabled={!nForm.dingtalk_on} value={nForm.dt_userids} onChange={e => setNForm({ ...nForm, dt_userids: e.target.value })} placeholder="留空即用手机号自动解析" /></div>
            </div>
          </div>

          <div className="fx-ncard">
            <label className="fx-nhead"><input type="checkbox" checked={nForm.email_on} onChange={e => setNForm({ ...nForm, email_on: e.target.checked })} /> 邮件通知</label>
            <div className="fx-ngrid" style={{ opacity: nForm.email_on ? 1 : .5 }}>
              <div className="fx-field"><label>收件人（每行一个）</label>
                <textarea className="fx-ta" disabled={!nForm.email_on} value={nForm.mail_to} onChange={e => setNForm({ ...nForm, mail_to: e.target.value })} placeholder="name@starfield.cn" /></div>
              <div className="fx-field"><label>抄送 Cc</label>
                <textarea className="fx-ta" disabled={!nForm.email_on} value={nForm.mail_cc} onChange={e => setNForm({ ...nForm, mail_cc: e.target.value })} /></div>
              <div className="fx-field"><label>密送 Bcc</label>
                <textarea className="fx-ta" disabled={!nForm.email_on} value={nForm.mail_bcc} onChange={e => setNForm({ ...nForm, mail_bcc: e.target.value })} /></div>
            </div>
          </div>

          <div className="fx-fbar">
            <span className="fx-flabel">口令</span>
            <input type="password" className="selctl" style={{ width: 180, height: 30, padding: '0 10px' }} value={nPass} onChange={e => setNPass(e.target.value)} placeholder="改收发件人需口令" />
            <button className="btn-pri" disabled={!!nBusy || !nPass} onClick={saveNotify}>{nBusy === 'save' ? '保存中…' : '保存收发件人'}</button>
            <button className="btn-sec" disabled={!!nBusy} onClick={testNotify}>{nBusy === 'test' ? '发送中…' : '发送测试通知'}</button>
          </div>
          {nMsg && <div className={'fx-note' + (nMsg.startsWith('✗') ? ' danger' : '')}>{nMsg}</div>}
        </>}
      </div>}
    </div>
  )

  // ---- 子组件 ----
  function Kpi({ l, v, tone }) {
    return <div className={'fx-kpi' + (tone === 'ok' ? ' ok' : tone === 'bad' ? ' prio' : tone === 'warn' ? ' warn' : '')}>
      <div className="kl">{l}</div><div className="kv">{v}</div></div>
  }
  function AutorunReport() {
    const rep = arReport
    const M = { would_write: ['info', '预演 · 将写入'], written: ['ok', '已写入'], partial: ['warn', '部分成功'],
      held: ['warn', '闸门挂起 · 未写'], waiting: ['muted', '还没到点'], done: ['ok', '本月已齐'], error: ['bad', '出错'] }
    const [cls, word] = M[rep.status] || ['muted', rep.status]
    const col = cls === 'bad' ? 'var(--red)' : cls === 'ok' ? 'var(--green)' : cls === 'warn' ? 'var(--amber)' : 'var(--accent)'
    const badGates = (rep.gates || []).filter(g => g.status === 'block' || g.status === 'hold')
    return <div className="fx-note" style={{ borderLeftColor: col }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>跑批结果：{rep.month} 月 · {rep.org_name}（{rep.org}）— <span style={{ color: col }}>{word}</span></div>
      <div style={{ marginBottom: (badGates.length || (rep.results && rep.results.length)) ? 6 : 0 }}>{rep.msg}</div>
      {badGates.length > 0 && <ul style={{ margin: '4px 0' }}>{badGates.map((g, i) => <li key={i}>{g.name}：{g.detail}</li>)}</ul>}
      {rep.results && rep.results.length > 0 && <div className="fx-tblwrap">
        <table><thead><tr><th>币对</th><th>生效区间</th><th>汇率</th><th>结果</th></tr></thead>
          <tbody>{rep.results.map((r, i) => (
            <tr key={i}><td>{r.pair}</td><td className="fx-num">{r.beg}{r.end && r.end !== r.beg ? ' ~ ' + r.end : ''}</td>
              <td className="fx-num">{r.rate}</td>
              <td>{r.status === 'would_write' ? '将写入' : r.status === 'posted' ? '已写入' : r.status === 'skipped' ? '跳过' : r.status === 'failed' ? ('失败：' + (r.msg || '')) : r.status}</td></tr>
          ))}</tbody></table>
      </div>}
    </div>
  }
  function GateBar() {
    return <div className="fx-gates">
      {gates.map((g, i) => <span key={i} className={'fx-gate ' + (g.status === 'ok' ? 'ok' : g.status === 'block' ? 'bad' : g.status === 'hold' ? 'warn' : 'muted')}
        title={g.detail}>{g.name}：{GATE_WORD[g.status] || g.status}</span>)}
    </div>
  }
  function PostedPanel() {
    const items = posted.items || []
    return <div className="fx-note" style={{ borderLeftColor: 'var(--blue)' }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>本期已录入（{year}年{month}月 · {orgName(org)}）</div>
      {items.length === 0 ? <div style={{ color: 'var(--ink-3)' }}>本工具在该期间该组织尚无录入记录。</div> : <>
        <div className="fx-tblwrap" style={{ marginBottom: 8 }}>
          <table><thead><tr><th></th><th>币对</th><th>生效区间</th><th>汇率</th><th>金蝶状态</th><th>录入</th></tr></thead>
            <tbody>
              {items.map(it => (
                <tr key={it.id}>
                  <td><input type="checkbox" disabled={!it['可撤销']} checked={pSel.has(it.id)}
                    onChange={e => { const s = new Set(pSel); e.target.checked ? s.add(it.id) : s.delete(it.id); setPSel(s) }} /></td>
                  <td>{it.pair}</td><td className="fx-num">{it.beg}{it.end !== it.beg ? ' ~ ' + it.end : ''}</td>
                  <td className="fx-num">{it.rate}</td><td>{it['金蝶状态']}{!it['可撤销'] && <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>（不可撤）</span>}</td>
                  <td style={{ fontSize: 11, color: 'var(--ink-3)' }}>{it['录入人']} {it['录入时间']}</td>
                </tr>
              ))}
            </tbody></table>
        </div>
        {canPost && <button className="btn" onClick={doUnpost} disabled={pBusy || pSel.size === 0}>{pBusy ? '处理中…' : `撤销勾选（${pSel.size}）`}</button>}
      </>}
    </div>
  }
}

// 作用域样式（fx- 前缀，复用工作台 CSS 变量，自动跟随明暗主题）
function FxStyle() {
  return <style>{`
.fx-wrap .fx-tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-top:4px}
.fx-wrap .fx-tab{padding:11px 15px;font-size:13px;font-weight:600;color:var(--ink-3);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.fx-wrap .fx-tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.fx-wrap .fx-tab:hover{color:var(--ink-2)}
.fx-wrap .fx-fbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;border:1px solid var(--line);border-radius:var(--radius);padding:10px 14px;background:var(--bg-rail)}
.fx-wrap .fx-flabel{font-size:11px;color:var(--ink-3);letter-spacing:.04em}
.fx-wrap .fx-spacer{flex:1}
.fx-wrap .fx-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.fx-wrap .fx-kpi{border:1px solid var(--line);border-radius:var(--radius);padding:11px 13px;background:var(--bg);display:flex;flex-direction:column;gap:5px}
.fx-wrap .fx-kpi .kl{font-size:11px;color:var(--ink-2)}
.fx-wrap .fx-kpi .kv{font-size:21px;font-weight:700;font-variant-numeric:tabular-nums}
.fx-wrap .fx-kpi.prio{border-color:var(--red-line);background:var(--red-bg)} .fx-wrap .fx-kpi.prio .kv{color:var(--red)}
.fx-wrap .fx-kpi.ok .kv{color:var(--green)}
.fx-wrap .fx-kpi.warn .kv{color:var(--amber)}
.fx-wrap .fx-tblwrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg)}
.fx-wrap .fx-tblwrap table{width:100%;border-collapse:collapse;font-size:12.5px}
.fx-wrap .fx-tblwrap thead th{background:var(--bg-sub);text-align:left;font-weight:600;font-size:11px;color:var(--ink-3);letter-spacing:.03em;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
.fx-wrap .fx-tblwrap tbody tr{border-bottom:1px solid var(--line)} .fx-wrap .fx-tblwrap tbody tr:last-child{border-bottom:none}
.fx-wrap .fx-tblwrap td{padding:9px 12px;vertical-align:middle}
.fx-wrap .fx-tblwrap tbody tr:hover td{background:var(--bg-sub)}
.fx-wrap tr.fx-prio td{background:var(--red-bg)} .fx-wrap tr.fx-prio td:first-child{box-shadow:inset 3px 0 0 var(--red)}
.fx-wrap tr.fx-exist td{color:var(--ink-3)}
.fx-wrap .fx-num{font-variant-numeric:tabular-nums;white-space:nowrap}
.fx-wrap .fx-calc{font-size:11px;color:var(--ink-2);background:var(--bg-rail);padding:2px 6px;border-radius:5px;white-space:nowrap}
.fx-wrap .fx-src{font-size:11px;color:var(--blue);text-decoration:none;display:inline-block}
.fx-wrap .fx-src:hover{text-decoration:underline}
.fx-wrap .fx-tag{display:inline-flex;align-items:center;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;border:1px solid}
.fx-wrap .fx-tag.new{color:var(--accent);background:var(--accent-soft);border-color:var(--accent-soft)}
.fx-wrap .fx-tag.exist{color:var(--ink-3);background:var(--bg-rail);border-color:var(--line-strong)}
.fx-wrap .fx-tag.ok{color:var(--green);background:var(--green-bg);border-color:var(--green-bg)}
.fx-wrap .fx-tag.bad{color:var(--red);background:var(--red-bg);border-color:var(--red-line)}
.fx-wrap .fx-tag.month{color:var(--blue);background:var(--blue-bg,var(--accent-soft));border-color:var(--blue)}
.fx-wrap .fx-tag.next{color:var(--amber);background:var(--amber-bg);border-color:var(--amber)}
.fx-wrap .fx-note{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:var(--radius);padding:11px 14px;background:var(--bg-rail);font-size:12px;color:var(--ink-2);line-height:1.6}
.fx-wrap .fx-note.warn{border-left-color:var(--amber);background:var(--amber-bg);color:var(--amber)}
.fx-wrap .fx-note.danger{border-left-color:var(--red);background:var(--red-bg);color:var(--red)}
.fx-wrap .fx-note b{color:inherit}
.fx-wrap .fx-grph{font-size:12px;font-weight:700;color:var(--ink-2);display:flex;align-items:center;gap:8px;margin-top:2px}
.fx-wrap .fx-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.fx-wrap .fx-gates{display:flex;gap:8px;flex-wrap:wrap}
.fx-wrap .fx-gate{font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;border:1px solid}
.fx-wrap .fx-gate.ok{color:var(--green);background:var(--green-bg);border-color:var(--green-bg)}
.fx-wrap .fx-gate.bad{color:var(--red);background:var(--red-bg);border-color:var(--red-line)}
.fx-wrap .fx-gate.warn{color:var(--amber);background:var(--amber-bg);border-color:var(--amber)}
.fx-wrap .fx-gate.muted{color:var(--ink-3);background:var(--bg-rail);border-color:var(--line-strong)}
.fx-wrap .fx-mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.fx-wrap .fx-mcell{border:1px solid var(--line);border-left-width:3px;border-radius:var(--radius);padding:10px 12px;background:var(--bg);display:flex;flex-direction:column;gap:4px}
.fx-wrap .fx-mcell.clickable{cursor:pointer}
.fx-wrap .fx-mcell.clickable:hover{background:var(--bg-sub)}
.fx-wrap .fx-mcell.active{box-shadow:0 0 0 2px var(--accent-soft);border-color:var(--accent)}
.fx-wrap .fx-mtop{display:flex;justify-content:space-between;align-items:baseline;font-size:14px}
.fx-wrap .fx-mcount{font-size:11px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.fx-wrap .fx-mstate{font-size:12px;font-weight:600}
.fx-wrap .fx-mmeta{font-size:11px;color:var(--ink-3)}
.fx-wrap .fx-mcell.muted{border-left-color:var(--line-strong)} .fx-wrap .fx-mcell.muted .fx-mstate{color:var(--ink-3)}
.fx-wrap .fx-mcell.warn{border-left-color:var(--amber);background:var(--amber-bg)} .fx-wrap .fx-mcell.warn .fx-mstate{color:var(--amber)}
.fx-wrap .fx-mcell.ok{border-left-color:var(--green)} .fx-wrap .fx-mcell.ok .fx-mstate{color:var(--green)}
.fx-wrap .fx-mcell.info{border-left-color:var(--blue)} .fx-wrap .fx-mcell.info .fx-mstate{color:var(--blue)}
.fx-wrap .fx-mcell.bad{border-left-color:var(--red);background:var(--red-bg)} .fx-wrap .fx-mcell.bad .fx-mstate{color:var(--red)}
.fx-wrap .fx-modebar{display:inline-flex;border:1px solid var(--line-strong);border-radius:8px;overflow:hidden;align-self:flex-start}
.fx-wrap .fx-modebtn{padding:8px 18px;font-size:13px;font-weight:600;background:var(--bg);color:var(--ink-2);border:none;cursor:pointer}
.fx-wrap .fx-modebtn+.fx-modebtn{border-left:1px solid var(--line-strong)}
.fx-wrap .fx-modebtn.on{background:var(--accent);color:#fff}
.fx-wrap .fx-statusrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.fx-wrap .fx-ncard{border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);padding:14px 16px;display:flex;flex-direction:column;gap:12px}
.fx-wrap .fx-nhead{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;color:var(--ink);cursor:pointer;user-select:none}
.fx-wrap .fx-nhead input{width:15px;height:15px;accent-color:var(--accent)}
.fx-wrap .fx-ngrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;transition:opacity .15s}
.fx-wrap .fx-field{display:flex;flex-direction:column;gap:5px}
.fx-wrap .fx-field label{font-size:11px;color:var(--ink-3);letter-spacing:.04em}
.fx-wrap .fx-ta{border:1px solid var(--line-strong);border-radius:8px;background:var(--bg);color:var(--ink);font-size:12.5px;font-family:inherit;padding:8px 10px;resize:vertical;min-height:62px;line-height:1.7}
.fx-wrap .fx-ta:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.fx-wrap .fx-ta:disabled{background:var(--bg-sub);cursor:not-allowed}
`}</style>
}
