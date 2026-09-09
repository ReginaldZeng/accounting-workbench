// [Change Log] Date:2026-08-20 Author:Claude/Reginald Zeng Version:V2.328（上版 V2.19）
// V2.328：加「同步出厂工具集」——把卡片刷成代码持有的 PORTAL_TOOL_DEFAULTS（两台已开发工具的现状）：
//   同名覆盖、缺失补插、手工新增的卡保留不删。人点按钮才跑，弹确认，结果计数回显。
// 门户管理（门户内页签，仅管理员）：维护各工作台的工具卡片——所属工作台/名称/状态/概述/通用技能/AI技能。
// 数据存 portal_tools 表，门户卡片实时读取；改这里 = 门户即时更新，无需改代码/发版。深色 pa- 作用域。
import React, { useEffect, useState } from 'react'
import { getPortalTools, savePortalTool, deletePortalTool, resetPortalTools, getMachines, setMachineAlertRecipients, setMachineResultRecipients, testMachineNotify, getDingtalkRoster } from '../api.js'

const LANES = [{ key: 'accounting', label: '财务核算组' }, { key: 'bp', label: '财务分析组 · BP' }, { key: 'legal', label: '法务部' }]
const LANE_LABEL = { accounting: '财务核算组', bp: '财务分析组 · BP', legal: '法务部' }
// V2.329 状态四档（业务方定，与工作台真实进度挂钩）：已上线(门户闪烁绿灯)/人工并行/开发中/敬请期待。
// DB 键沿用 ok/beta/soon 不迁移，新增 par。
const STATUS = [{ key: 'ok', label: '已上线' }, { key: 'par', label: '人工并行' }, { key: 'beta', label: '开发中' }, { key: 'soon', label: '敬请期待' }]
const ST_LABEL = { ok: '已上线', par: '人工并行', beta: '开发中', soon: '敬请期待' }
const splitTags = (s) => (s || '').split(/[，,、\n]/).map(x => x.trim()).filter(Boolean)

const CSS = `
.pa-root{--line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.14);--ink:#EDEAF6;--ink2:#B4ABD4;--ink3:#7E76A0;
  --brand:#7C5CFF;--brand2:#9B7BFF;--green:#34D399;--amber:#FBBF24;--red:#F87171;
  color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",sans-serif;
  max-width:1060px;margin:0 auto;padding:8px 34px 40px}
.pa-root *{box-sizing:border-box}
.pa-h1{font-size:20px;font-weight:800}
.pa-sub{font-size:12.5px;color:var(--ink3);margin:6px 0 18px}
.pa-cols{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
.pa-left{flex:0 0 330px;max-width:100%}
.pa-right{flex:1 1 380px;min-width:0}
.pa-card{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.005));border:1px solid var(--line);border-radius:14px;padding:14px}
.pa-ct{font-size:13px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
.pa-grp{font-size:11px;color:var(--ink3);letter-spacing:1px;margin:12px 0 4px;padding-left:2px}
.pa-item{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:10px;cursor:pointer;border:1px solid transparent}
.pa-item:hover{background:rgba(255,255,255,.04)}
.pa-item.sel{background:rgba(124,92,255,.16);border-color:rgba(124,92,255,.45)}
.pa-ic{width:26px;height:26px;flex:none;border-radius:7px;background:rgba(124,92,255,.14);border:1px solid rgba(124,92,255,.22);display:grid;place-items:center;font-size:14px;color:#C3B4FF}
.pa-nm{font-weight:700;font-size:13px}
.pa-badge{font-size:10px;padding:1px 8px;border-radius:999px;margin-left:auto;white-space:nowrap;border:1px solid var(--line2)}
.pa-badge.ok{color:#4ADE9E}.pa-badge.par{color:var(--amber)}.pa-badge.beta{color:#8FBAFF}.pa-badge.soon{color:var(--ink3)}
.pa-fld{margin-bottom:12px}
.pa-lb{font-size:12px;font-weight:600;color:var(--ink2);margin-bottom:6px}
.pa-inp,.pa-ta,select.pa-inp{width:100%;border-radius:8px;border:1px solid var(--line2);background:rgba(255,255,255,.05);color:var(--ink);padding:8px 11px;font-size:13px;font-family:inherit;outline:none}
.pa-inp{height:34px;padding:0 11px}
.pa-ta{min-height:56px;resize:vertical;line-height:1.6}
.pa-inp:focus,.pa-ta:focus{border-color:var(--brand)}
.pa-inp::placeholder,.pa-ta::placeholder{color:var(--ink3)}
select.pa-inp option{background:#221A3A;color:var(--ink)}
.pa-hint{font-size:11px;color:var(--ink3);margin-top:5px}
.pa-btn{height:34px;padding:0 15px;border-radius:8px;border:1px solid var(--line2);background:rgba(255,255,255,.06);color:var(--ink);font-size:13px;font-weight:600;font-family:inherit;cursor:pointer}
.pa-btn:hover{background:rgba(255,255,255,.1)}
.pa-btn.pri{background:linear-gradient(180deg,var(--brand),#6A4CE6);border-color:transparent;color:#fff;box-shadow:0 6px 16px rgba(90,60,200,.3)}
.pa-btn.red{color:var(--red)}
.pa-lk{color:var(--brand2);cursor:pointer;font-weight:600;font-size:12.5px}
.pa-empty{border:1px dashed var(--line2);border-radius:12px;padding:44px 20px;text-align:center;color:var(--ink3);font-size:13px}
.pa-bar{display:flex;gap:10px;align-items:center;margin-top:6px}
.pa-prev{margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:10px;background:rgba(0,0,0,.18)}
.pa-tg{font-size:10.5px;color:var(--ink2);background:rgba(255,255,255,.05);border:1px solid var(--line2);border-radius:6px;padding:2px 8px;margin:0 4px 4px 0;display:inline-block}
.pa-tg.ai{color:#9BF5E6;border-color:rgba(63,224,200,.4)}
.pa-seg{display:inline-flex;gap:2px;background:rgba(255,255,255,.05);border:1px solid var(--line2);border-radius:9px;padding:3px;margin-bottom:16px}
.pa-segbtn{border:none;background:transparent;color:var(--ink2);font:inherit;font-size:12.5px;font-weight:600;padding:6px 15px;border-radius:7px;cursor:pointer}
.pa-segbtn.on{background:var(--brand);color:#fff}
@keyframes pa-pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 var(--pmc)}50%{opacity:.4;box-shadow:0 0 0 5px transparent}}
.pa-dot{flex:0 0 auto;width:11px;height:11px;border-radius:50%}
.pa-dot.g{background:var(--green);--pmc:rgba(52,211,153,.55);animation:pa-pulse 2s ease-in-out infinite}
.pa-dot.r{background:var(--red);--pmc:rgba(248,113,113,.55);animation:pa-pulse 2s ease-in-out infinite}
.pa-dot.x{background:var(--ink3)}
.pa-mcard{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.005));border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.pa-mrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.pa-pill{font-size:11.5px;padding:2px 10px;border-radius:20px;font-weight:600;border:1px solid var(--line2);white-space:nowrap}
.pa-meta{display:flex;flex-wrap:wrap;gap:4px 20px;margin-top:9px;font-size:12px;color:var(--ink2)}
.pa-rcpt{margin-top:10px;padding:9px 11px;border-radius:9px;background:rgba(0,0,0,.18);border:1px solid var(--line)}
.pa-rcpt input{width:100%;max-width:340px;border-radius:7px;border:1px solid var(--line2);background:rgba(255,255,255,.05);color:var(--ink);padding:6px 10px;font:inherit;font-size:12.5px;outline:none}
.pa-rcpt input:focus{border-color:var(--brand)}
.pa-rcpt input.mini{width:172px;max-width:172px}
.pa-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:24px;margin-bottom:7px}
.pa-chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:3px 4px 3px 10px;border-radius:14px;background:rgba(124,92,255,.15);border:1px solid rgba(124,92,255,.42);color:var(--ink)}
.pa-chip.num{background:rgba(255,255,255,.05);border-color:var(--line2);color:var(--ink2)}
.pa-chip-x{cursor:pointer;color:var(--ink3);font-weight:700;line-height:1;padding:1px 5px;border-radius:9px}
.pa-chip-x:hover{background:rgba(255,90,90,.25);color:#fff}
.pa-chip-empty{font-size:12px;color:var(--ink3)}
.pa-rcpt input.srch{width:250px;max-width:250px}
.pa-srwrap{position:relative;display:inline-block}
.pa-sr{position:absolute;z-index:30;top:36px;left:0;width:300px;max-height:238px;overflow-y:auto;background:#1b1430;border:1px solid var(--line2);border-radius:9px;padding:4px;box-shadow:0 14px 34px rgba(0,0,0,.5)}
.pa-sr-item{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:7px;cursor:pointer;font-size:12.5px;color:var(--ink)}
.pa-sr-item:hover{background:rgba(124,92,255,.2)}
.pa-sr-msg{padding:8px 9px;color:var(--ink3);font-size:12px;line-height:1.6}
`

const blank = () => ({ id: null, lane: 'accounting', name: '', status: 'beta', icon: '▤', desc: '', genStr: '', aiStr: '', mods: [], statusSrc: 'manual', autoDetail: [] })

export default function PortalAdmin({ onChange }) {
  const [tools, setTools] = useState([])
  const [sel, setSel] = useState(null)      // 编辑中的 {..} 或 null
  const [msg, setMsg] = useState(null)
  const [view, setView] = useState('tools')  // 门户管理两块：'tools' 工具卡片 / 'machines' 取件机监控
  const load = () => getPortalTools().then(r => setTools(r.tools || [])).catch(() => {})
  useEffect(() => { load() }, [])
  const flash = (ok, t) => { setMsg({ ok, t }); setTimeout(() => setMsg(null), 2400) }

  // V2.331：mods/statusSrc/autoDetail 随行带出。status 用**手工档**（statusManual）回填——
  // 列表下发的 status 是推导后的展示值，直接存回去会把推导结果固化成手工档。
  const edit = (t) => setSel({ id: t.id, lane: t.lane, name: t.name, status: t.statusManual || t.status, icon: t.icon || '▤', desc: t.desc || '', genStr: (t.gen || []).join('、'), aiStr: (t.ai || []).join('、'), mods: t.mods || [], statusSrc: t.statusSrc || 'manual', autoDetail: t.autoDetail || [] })
  const save = async () => {
    if (!sel.name.trim()) { flash(false, '工具名称不能为空'); return }
    // mods 必须透传：save 是整行覆盖，漏了它=把自动联动映射抹掉
    const r = await savePortalTool({ id: sel.id, lane: sel.lane, name: sel.name.trim(), status: sel.status, icon: sel.icon, desc: sel.desc, gen: splitTags(sel.genStr), ai: splitTags(sel.aiStr), mods: sel.mods || [] })
    if (r.ok) { flash(true, '已保存'); setSel(null); await load(); onChange && onChange() } else flash(false, r.msg)
  }
  const del = async () => {
    if (!sel.id) { setSel(null); return }
    if (!window.confirm(`确认删除工具「${sel.name}」？`)) return
    const r = await deletePortalTool({ id: sel.id })
    if (r.ok) { flash(true, '已删除'); setSel(null); await load(); onChange && onChange() } else flash(false, r.msg)
  }
  const up = (k, v) => setSel(s => ({ ...s, [k]: v }))
  const syncDefaults = async () => {
    if (!window.confirm('把工具卡片同步为「出厂工具集」（两个工作台已开发工具的现状）？\n同名卡片会被覆盖（状态/概述/标签），缺失的会补上；你手工新增的其他卡片保留不动。')) return
    const r = await resetPortalTools()
    if (r.ok) { flash(true, `已同步：新增 ${r.added.length} · 覆盖 ${r.updated.length} · 保留 ${r.kept.length}`); setSel(null); await load(); onChange && onChange() }
    else flash(false, r.msg || '同步失败')
  }

  return (
    <div className="pa-root">
      <style>{CSS}</style>
      <div className="pa-h1">门户管理</div>
      <div className="pa-sub">维护各工作台首页的工具卡片、看取件机运行 · 仅管理员
        {msg && <span style={{ color: msg.ok ? 'var(--green)' : 'var(--red)', marginLeft: 8 }}>{msg.t}</span>}</div>

      <div className="pa-seg">
        <button className={'pa-segbtn' + (view === 'tools' ? ' on' : '')} onClick={() => setView('tools')}>工具卡片</button>
        <button className={'pa-segbtn' + (view === 'machines' ? ' on' : '')} onClick={() => setView('machines')}>取件机监控</button>
      </div>

      {view === 'machines' && <MachineMonitor />}
      {view === 'tools' && (
      <div className="pa-cols">
        <div className="pa-left">
          <div className="pa-card">
            <div className="pa-ct">工具卡片（{tools.length}）
              <span><span className="pa-lk" onClick={syncDefaults} title="同名覆盖、缺失补插、手工卡保留">⟳ 同步出厂集</span>
                <span className="pa-lk" style={{ marginLeft: 10 }} onClick={() => setSel(blank())}>+ 新建</span></span></div>
            {LANES.map(L => {
              const items = tools.filter(t => t.lane === L.key)
              return (
                <div key={L.key}>
                  <div className="pa-grp">{L.label}（{items.length}）</div>
                  {items.map(t => (
                    <div key={t.id} className={'pa-item' + (sel && sel.id === t.id ? ' sel' : '')} onClick={() => edit(t)}>
                      <div className="pa-ic">{t.icon}</div>
                      <div style={{ minWidth: 0 }}><div className="pa-nm">{t.name}</div></div>
                      <span className={'pa-badge ' + t.status}>{ST_LABEL[t.status]}</span>
                    </div>
                  ))}
                  {items.length === 0 && <div style={{ fontSize: 11.5, color: 'var(--ink3)', padding: '2px 10px 4px' }}>暂无工具</div>}
                </div>
              )
            })}
          </div>
        </div>

        <div className="pa-right">
          {!sel ? (
            <div className="pa-empty">← 选择左侧工具编辑，或点「+ 新建」添加一个工具卡片</div>
          ) : (
            <div className="pa-card">
              <div className="pa-ct">{sel.id ? '编辑工具' : '新建工具'}</div>
              <div className="pa-fld"><div className="pa-lb">所属工作台</div>
                <select className="pa-inp" value={sel.lane} onChange={e => up('lane', e.target.value)}>
                  {LANES.map(L => <option key={L.key} value={L.key}>{L.label}</option>)}</select></div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div className="pa-fld" style={{ flex: 1 }}><div className="pa-lb">工具名称</div>
                  <input className="pa-inp" value={sel.name} onChange={e => up('name', e.target.value)} placeholder="如：银行-金蝶稽核" /></div>
                <div className="pa-fld" style={{ width: 130 }}><div className="pa-lb">当前状态{sel.statusSrc === 'auto' ? '（兜底）' : ''}</div>
                  <select className="pa-inp" value={sel.status} onChange={e => up('status', e.target.value)}>
                    {STATUS.map(S => <option key={S.key} value={S.key}>{S.label}</option>)}</select></div>
              </div>
              {(sel.mods || []).length > 0 && (
                <div className="pa-fld" style={{ fontSize: 11.5, lineHeight: 1.8, color: 'var(--ink3)', background: 'rgba(63,224,200,.06)', border: '1px solid rgba(63,224,200,.25)', borderRadius: 8, padding: '8px 11px' }}>
                  <b style={{ color: '#9BF5E6' }}>⛓ 已开启自动联动</b>——门户上此卡的状态由以下模块在「系统设置 › 导航模块上线管理」的进度推导（多模块取最低档），上面的下拉仅在模块不可推导时兜底：
                  <div style={{ marginTop: 4 }}>
                    {(sel.autoDetail || []).length > 0
                      ? sel.autoDetail.map(d => <span key={d.key} style={{ display: 'inline-block', margin: '2px 6px 0 0', padding: '1px 8px', borderRadius: 999, border: '1px solid var(--line2)', color: 'var(--ink2)' }}>{d.label}·{d.navStatus}</span>)
                      : `模块：${(sel.mods || []).join('、')}（当前均不可推导，正在用兜底档）`}
                  </div>
                </div>
              )}
              <div className="pa-fld"><div className="pa-lb">概述</div>
                <textarea className="pa-ta" value={sel.desc} onChange={e => up('desc', e.target.value)} placeholder="一句话说明这个工具做什么" /></div>
              <div className="pa-fld"><div className="pa-lb">通用技能</div>
                <input className="pa-inp" value={sel.genStr} onChange={e => up('genStr', e.target.value)} placeholder="逐笔稽核、余额调节、账户台账" />
                <div className="pa-hint">用「、」或逗号分隔</div></div>
              <div className="pa-fld"><div className="pa-lb">AI 技能</div>
                <input className="pa-inp" value={sel.aiStr} onChange={e => up('aiStr', e.target.value)} placeholder="AI差异归因、AI晚记识别" />
                <div className="pa-hint">用「、」或逗号分隔，门户上带 ✦ 高亮显示</div></div>

              <div className="pa-prev">
                <div style={{ fontSize: 11, color: 'var(--ink3)', marginBottom: 8 }}>门户上的效果预览</div>
                <div style={{ fontSize: 14.5, fontWeight: 800 }}>{sel.icon} {sel.name || '（工具名称）'} <span style={{ fontSize: 10.5, color: 'var(--amber)', marginLeft: 6 }}>{ST_LABEL[sel.status]}</span></div>
                <div style={{ fontSize: 12, color: 'var(--ink2)', margin: '6px 0 9px' }}>{sel.desc || '（概述）'}</div>
                <div>{splitTags(sel.genStr).map(g => <span key={g} className="pa-tg">{g}</span>)}</div>
                <div style={{ marginTop: 4 }}>{splitTags(sel.aiStr).map(a => <span key={a} className="pa-tg ai">✦ {a}</span>)}</div>
              </div>

              <div className="pa-bar" style={{ marginTop: 14 }}>
                <button className="pa-btn pri" onClick={save}>保存</button>
                <button className="pa-btn" onClick={() => setSel(null)}>取消</button>
                {sel.id && <button className="pa-btn red" style={{ marginLeft: 'auto' }} onClick={del}>删除</button>}
              </div>
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  )
}


// 取件机运行监控（V2.532）：门户管理内的只读监控 + 收件人配置（仅管理员进得来这页）。
// 收件人加人（V2.543 直接搜人，取代旧「通讯录选」弹窗）：输名字→下拉搜通讯录（姓名｜岗位｜部门）点一下加；
// 输 11 位号码→回车/点「加外部号码」当外部人。花名册由父层拉一次共享，首次聚焦时懒加载。
function PersonSearchAdd({ roster, rosterErr, onFocusLoad, onPick, onAddNumber }) {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const kw = q.trim()
  const isNum = /^\d+$/.test(kw)
  const hits = (!isNum && kw && Array.isArray(roster))
    ? roster.filter(p => (p.name || '').includes(kw) || (p.title || '').includes(kw) || (p.dept || '').includes(kw)).slice(0, 8)
    : []
  const pick = (p) => { onPick(p); setQ('') }
  const addNum = () => { if (/^\d{11}$/.test(kw)) { onAddNumber(kw); setQ('') } }
  const open = focused && !!kw
  return (
    <div className="pa-srwrap">
      <input className="srch" value={q} placeholder="输名字搜人加，或填 11 位手机号"
        onFocus={() => { setFocused(true); onFocusLoad() }}
        onBlur={() => setTimeout(() => setFocused(false), 150)}
        onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && isNum) { e.preventDefault(); addNum() } }} />
      {open && (
        <div className="pa-sr">
          {isNum
            ? (kw.length === 11
              ? <div className="pa-sr-item" onMouseDown={e => { e.preventDefault(); addNum() }}>➕ 加外部号码 {kw}</div>
              : <div className="pa-sr-msg">手机号 11 位，还差 {11 - kw.length} 位…</div>)
            : (roster === null
              ? <div className="pa-sr-msg">加载通讯录中…</div>
              : rosterErr
                ? <div className="pa-sr-msg" style={{ color: 'var(--red)' }}>{rosterErr}</div>
                : hits.length === 0
                  ? <div className="pa-sr-msg">没搜到「{kw}」</div>
                  : hits.map(p => (
                    <div key={p.userid} className="pa-sr-item" onMouseDown={e => { e.preventDefault(); pick(p) }}>
                      <span>👤 {p.name}</span>
                      {(p.title || p.dept) && <span style={{ color: 'var(--ink3)', marginLeft: 'auto', fontSize: 11.5 }}>{[p.title, p.dept].filter(Boolean).join(' · ')}</span>}
                    </div>
                  )))}
        </div>
      )}
    </div>
  )
}

function MachineMonitor() {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState(null)
  const [alertEdit, setAlertEdit] = useState({})    // {machineId: [{m,n}]}  未编辑=用后端已存
  const [resultEdit, setResultEdit] = useState({})  // {"machineId|通知键": [{m,n}]}
  const [roster, setRoster] = useState(null)   // 全公司花名册 [{userid,name,title,dept}]；null=没拉过
  const [rosterErr, setRosterErr] = useState('')
  const [rosterBusy, setRosterBusy] = useState(false)
  const ensureRoster = () => {   // 首次聚焦搜人框时懒加载一次（钉钉走一遍部门树，别每次都拉）
    if (roster !== null || rosterBusy) return
    setRosterBusy(true)
    getDingtalkRoster().then(r => {
      setRosterBusy(false)
      if (r.ok) setRoster(r.people || [])
      else { setRoster([]); setRosterErr(r.msg || '拉通讯录失败') }
    }).catch(e => { setRosterBusy(false); setRoster([]); setRosterErr(String(e)) })
  }
  const load = () => getMachines().then(r => { setD(r); setAlertEdit({}); setResultEdit({}) }).catch(() => {})
  useEffect(() => { load() }, [])
  const flash = (ok, t) => { setMsg({ ok, t }); setTimeout(() => setMsg(null), 2600) }
  // 当前行收件人 [{m手机号, n名字}]：编辑中优先，否则由已存手机号 + names 表还原（选过的人有名字，手打的没有）
  const entriesOf = (isAlert, id, key) => {
    const editKey = isAlert ? id : (id + '|' + key)
    const store = isAlert ? alertEdit : resultEdit
    if (store[editKey]) return store[editKey]
    const mac = (d.machines || []).find(x => x.id === id) || {}
    const mobs = isAlert ? (mac.alert_mobiles || [])
      : ((mac.results || []).find(rr => rr.key === key)?.mobiles || [])
    const nm = d.names || {}
    return mobs.map(mo => ({ m: mo, n: nm[mo] || '' }))
  }
  const setEntries = (isAlert, id, key, next) => {
    const editKey = isAlert ? id : (id + '|' + key)
    ;(isAlert ? setAlertEdit : setResultEdit)(prev => ({ ...prev, [editKey]: next }))
  }
  const removeChip = (isAlert, id, key, mo) =>
    setEntries(isAlert, id, key, entriesOf(isAlert, id, key).filter(e => e.m !== mo))
  const addTyped = (isAlert, id, key, mo) => {   // 手打外部人：只有号码、没名字
    if (!/^\d{11}$/.test(mo)) { flash(false, '手机号要 11 位数字：' + mo); return }
    const cur = entriesOf(isAlert, id, key)
    if (cur.some(e => e.m === mo)) { flash(true, '这个号已经在里面了'); return }
    setEntries(isAlert, id, key, [...cur, { m: mo, n: '' }])
  }
  const addPerson = (isAlert, id, key, person) => {   // 搜名字选中一个人：直接存其 userid（发钉钉走 userid，不需读手机号权限）
    const tok = 'u:' + person.userid
    const cur = entriesOf(isAlert, id, key)
    if (cur.some(e => e.m === tok)) { flash(true, (person.name || '此人') + ' 已经在里面了'); return }
    setEntries(isAlert, id, key, [...cur, { m: tok, n: person.name || '' }])
    flash(true, '已加入 ' + (person.name || '此人') + '，记得点「保存」')
  }
  const saveAlert = async (id) => {
    const r = await setMachineAlertRecipients(id, entriesOf(true, id)).catch(e => ({ ok: false, msg: String(e) }))
    flash(r.ok, r.msg); if (r.ok) load()
  }
  const saveResult = async (id, key) => {
    const r = await setMachineResultRecipients(id, key, entriesOf(false, id, key)).catch(e => ({ ok: false, msg: String(e) }))
    flash(r.ok, r.msg); if (r.ok) load()
  }
  const testNotify = async (id, kind, key) => {
    flash(true, '测试发送中…')
    const r = await testMachineNotify(id, kind, key).catch(e => ({ ok: false, msg: String(e) }))
    flash(r.ok, r.msg)
  }
  if (!d) return <div className="pa-empty">加载中…</div>
  const ago = s => s == null ? '' : s < 90 ? '刚刚' : s < 3600 ? Math.round(s / 60) + ' 分钟前' : Math.round(s / 3600) + ' 小时前'
  return (
    <div>
      <div style={{ fontSize: 12.5, color: 'var(--ink3)', margin: '0 0 12px' }}>
        共 {d.machines.length} 台取件机 · <span style={{ color: 'var(--green)' }}>绿=在跑</span>、<span style={{ color: 'var(--red)' }}>红=可能已停</span>、灰=未接/休息。改动即时生效、无需重启。</div>
      {d.dingtalk_configured === false &&
        <div className="pa-mcard" style={{ borderColor: 'var(--amber)', color: 'var(--amber)', fontSize: 12.5 }}>
          ⚠ 服务器还没配钉钉应用（conf.ini [dingtalk]），配了收件人也发不出去——需先配钉钉。</div>}
      {msg && <div style={{ fontSize: 12.5, margin: '0 0 10px', fontWeight: 600, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.t}</div>}
      {d.machines.map(m => {
        const state = !m.deployed ? 'x' : (m.alive ? 'g' : (m.always_on ? 'r' : 'x'))
        const label = !m.deployed ? '未接监控' : (m.alive ? '在跑' : (m.always_on ? '可能已停' : '未在取件'))
        const col = state === 'g' ? 'var(--green)' : state === 'r' ? 'var(--red)' : 'var(--ink3)'
        const last = m.last || {}
        // 一行收件人：名字/号码标签（× 删）＋ 搜名字加人/手打号码 ＋ 保存 ＋ 发测试
        const rcptRow = (isAlert, key, emptyHint) => {
          const ents = entriesOf(isAlert, m.id, key)
          return (
            <>
              <div className="pa-chips">
                {ents.length === 0 && <span className="pa-chip-empty">{emptyHint}</span>}
                {ents.map(e => {
                  const isUid = e.m.startsWith('u:')          // 通讯录选的人：存 u:userid、显名字
                  const show = e.n || (isUid ? '钉钉联系人' : e.m)
                  return (
                    <span key={e.m} className={'pa-chip' + (isUid || e.n ? '' : ' num')} title={isUid ? show : (e.n ? e.n + ' · ' + e.m : e.m)}>
                      {show}
                      <span className="pa-chip-x" onClick={() => removeChip(isAlert, m.id, key, e.m)}>×</span>
                    </span>
                  )
                })}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <PersonSearchAdd roster={roster} rosterErr={rosterErr} onFocusLoad={ensureRoster}
                  onPick={p => addPerson(isAlert, m.id, key, p)}
                  onAddNumber={mo => addTyped(isAlert, m.id, key, mo)} />
                <button className="pa-btn pri" onClick={() => isAlert ? saveAlert(m.id) : saveResult(m.id, key)}>保存</button>
                <button className="pa-btn" onClick={() => testNotify(m.id, isAlert ? 'alert' : 'result', key)} title="给当前收件人发一条【测试】钉钉">发测试</button>
              </div>
            </>
          )
        }
        return (
          <div className="pa-mcard" key={m.id}>
            <div className="pa-mrow">
              <span className={'pa-dot ' + state} />
              <b style={{ fontSize: 14 }}>{m.name}</b>
              <span className="pa-pill" style={{ color: 'var(--ink3)' }}>{m.dir === 'up' ? '上行 · 共享盘→云端' : '下行 · 云端→本地'}</span>
              <span className="pa-pill" style={{ marginLeft: 'auto', color: col, borderColor: col }}>● {label}</span>
            </div>
            <div className="pa-meta">
              <span>用途 {m.purpose}</span><span>频率 {m.freq}</span>
              <span>最近报平安 {m.at || '—'}{m.ago_sec != null ? '（' + ago(m.ago_sec) + '）' : ''}</span>
              {m.host && <span>所在电脑 {m.host}</span>}
            </div>
            <div className="pa-meta" style={{ color: 'var(--ink3)', marginTop: 4 }}>
              {'并入笔数' in last && <span>上轮并入 {last['并入笔数']} 笔{last.need_dup_confirm ? ' · 待确认' : ''}</span>}
              {Array.isArray(last.copied) && <span>上轮取 {last.copied.length} 个{last.skipped ? ' · 跳过 ' + last.skipped : ''}</span>}
              {Array.isArray(last.bomCopied) && last.bomCopied.length > 0 && <span>上轮送公盘 {last.bomCopied.length} 份</span>}
              {Array.isArray(last.errors) && last.errors.length > 0 && <span style={{ color: 'var(--red)' }}>错误 {last.errors.length}</span>}
            </div>
            <div className="pa-rcpt">
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 7 }}>🔔 停机告警 · 推送给谁 <span style={{ color: 'var(--ink3)', fontWeight: 400 }}>（运行 · 给运维/管理员）</span></div>
              {rcptRow(true, null, '空 = 关闭停机告警')}
              <div style={{ fontSize: 11, color: 'var(--ink3)', marginTop: 6 }}>停了超阈值自动用风控 AI 机器人钉钉提醒这些人。选的人显名字、手打的显号码。「发测试」＝立刻给当前收件人发条测试，验链路。</div>
            </div>
            {(m.results || []).map(r => (
              <div className="pa-rcpt" key={r.key} style={{ borderColor: 'rgba(124,92,255,.35)' }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 7 }}>{r.label} <span style={{ color: 'var(--ink3)', fontWeight: 400 }}>（结果 · 给干活的人）</span></div>
                {rcptRow(false, r.key, '空 = 不推送')}
                <div style={{ fontSize: 11, color: 'var(--ink3)', marginTop: 6 }}>{r.note}</div>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
