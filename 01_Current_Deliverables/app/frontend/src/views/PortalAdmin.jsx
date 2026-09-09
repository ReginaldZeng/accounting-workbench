// [Change Log] Date:2026-08-20 Author:Claude/Reginald Zeng Version:V2.328（上版 V2.19）
// V2.328：加「同步出厂工具集」——把卡片刷成代码持有的 PORTAL_TOOL_DEFAULTS（两台已开发工具的现状）：
//   同名覆盖、缺失补插、手工新增的卡保留不删。人点按钮才跑，弹确认，结果计数回显。
// 门户管理（门户内页签，仅管理员）：维护各工作台的工具卡片——所属工作台/名称/状态/概述/通用技能/AI技能。
// 数据存 portal_tools 表，门户卡片实时读取；改这里 = 门户即时更新，无需改代码/发版。深色 pa- 作用域。
import React, { useEffect, useState } from 'react'
import { getPortalTools, savePortalTool, deletePortalTool, resetPortalTools, getMachines, setMachineAlertRecipients, setMachineResultRecipients, testMachineNotify, getDingtalkDepts, getDingtalkDeptMembers, dingtalkPickMobiles } from '../api.js'

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
// 手打手机号输入 + 加（本地缓冲，回车或点「加」把号码交给父层，不常驻输入框）
function TypedAdd({ onAdd }) {
  const [v, setV] = useState('')
  const go = () => { const t = v.trim(); if (t) { onAdd(t); setV('') } }
  return (
    <>
      <input className="mini" value={v} placeholder="加手机号（外部人）"
        onChange={e => setV(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); go() } }} />
      <button className="pa-btn" onClick={go}>加</button>
    </>
  )
}

function MachineMonitor() {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState(null)
  const [alertEdit, setAlertEdit] = useState({})    // {machineId: [{m,n}]}  未编辑=用后端已存
  const [resultEdit, setResultEdit] = useState({})  // {"machineId|通知键": [{m,n}]}
  const [picker, setPicker] = useState(null)         // 通讯录选人弹窗目标 {kind,id,key} | null
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
  const addPicked = (target, ents, noMobile) => {   // 通讯录选人回填：带名字，按号去重覆盖
    const isAlert = target.kind === 'alert'
    const map = new Map(entriesOf(isAlert, target.id, target.key).map(e => [e.m, e]))
    ents.forEach(e => map.set(e.m, e))
    setEntries(isAlert, target.id, target.key, Array.from(map.values()))
    setPicker(null)
    let t = '已加入 ' + ents.length + ' 人，记得点「保存」'
    if (noMobile && noMobile.length) t += '（' + noMobile.join('、') + ' 没手机号，跳过了）'
    flash(true, t)
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
      {picker && <ContactPicker onClose={() => setPicker(null)} onConfirm={(ents, nm) => addPicked(picker, ents, nm)} />}
      {d.machines.map(m => {
        const state = !m.deployed ? 'x' : (m.alive ? 'g' : (m.always_on ? 'r' : 'x'))
        const label = !m.deployed ? '未接监控' : (m.alive ? '在跑' : (m.always_on ? '可能已停' : '未在取件'))
        const col = state === 'g' ? 'var(--green)' : state === 'r' ? 'var(--red)' : 'var(--ink3)'
        const last = m.last || {}
        // 一行收件人：名字/号码标签（× 删）＋ 手打加号码 ＋ 通讯录选 ＋ 保存 ＋ 发测试
        const rcptRow = (isAlert, key, emptyHint) => {
          const ents = entriesOf(isAlert, m.id, key)
          return (
            <>
              <div className="pa-chips">
                {ents.length === 0 && <span className="pa-chip-empty">{emptyHint}</span>}
                {ents.map(e => (
                  <span key={e.m} className={'pa-chip' + (e.n ? '' : ' num')} title={e.n ? e.n + ' · ' + e.m : e.m}>
                    {e.n || e.m}
                    <span className="pa-chip-x" onClick={() => removeChip(isAlert, m.id, key, e.m)}>×</span>
                  </span>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <TypedAdd onAdd={mo => addTyped(isAlert, m.id, key, mo)} />
                <button className="pa-btn" onClick={() => setPicker(isAlert ? { kind: 'alert', id: m.id } : { kind: 'result', id: m.id, key })} title="从钉钉通讯录勾人">通讯录选</button>
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


// 通讯录选人弹窗（V2.539）：按部门树钻取、勾选成员 → 取其手机号回填到收件人框。只门户管理(admin)进得来。
function ContactPicker({ onConfirm, onClose }) {
  const [stack, setStack] = useState([{ id: 1, name: '通讯录' }])   // 部门路径栈（面包屑）
  const [depts, setDepts] = useState([])
  const [members, setMembers] = useState([])
  const [sel, setSel] = useState({})          // {userid: name}
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async (id) => {
    setBusy(true); setErr('')
    const d = await getDingtalkDepts(id).catch(e => ({ ok: false, msg: String(e) }))
    const mm = await getDingtalkDeptMembers(id).catch(e => ({ ok: false, msg: String(e) }))
    setBusy(false)
    if (!d.ok && !mm.ok) { setErr(d.msg || mm.msg || '拉通讯录失败'); setDepts([]); setMembers([]); return }
    setDepts(d.ok ? (d.depts || []) : [])
    setMembers(mm.ok ? (mm.members || []) : [])
  }
  useEffect(() => { load(1) }, [])
  const enter = (dp) => { setStack(s => [...s, dp]); load(dp.id) }
  const jump = (i) => { const ns = stack.slice(0, i + 1); setStack(ns); load(ns[ns.length - 1].id) }
  const toggle = (mem) => setSel(s => { const n = { ...s }; if (n[mem.userid]) delete n[mem.userid]; else n[mem.userid] = mem.name; return n })
  const confirm = async () => {
    const uids = Object.keys(sel)
    if (!uids.length) { onClose(); return }
    setBusy(true)
    const r = await dingtalkPickMobiles(uids).catch(e => ({ ok: false, msg: String(e) }))
    setBusy(false)
    if (!r.ok) { setErr(r.msg || '取手机号失败'); return }
    onConfirm((r.people || []).filter(p => p.mobile).map(p => ({ m: p.mobile, n: p.name })),
      (r.people || []).filter(p => !p.mobile).map(p => p.name))
  }
  const n = Object.keys(sel).length
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)', zIndex: 60, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div className="pa-card" style={{ width: 520, maxWidth: '92vw', maxHeight: '82vh', display: 'flex', flexDirection: 'column' }} onClick={e => e.stopPropagation()}>
        <div className="pa-ct">从钉钉通讯录选人<span className="pa-lk" onClick={onClose}>✕ 关闭</span></div>
        <div style={{ fontSize: 12, color: 'var(--ink2)', marginBottom: 6 }}>
          {stack.map((s, i) => <span key={i}>{i > 0 && ' / '}<span className="pa-lk" onClick={() => jump(i)}>{s.name}</span></span>)}
        </div>
        {err && <div style={{ fontSize: 12, color: 'var(--red)', marginBottom: 6, lineHeight: 1.6 }}>{err}</div>}
        <div style={{ overflowY: 'auto', flex: 1, minHeight: 180, border: '1px solid var(--line)', borderRadius: 8, padding: 4 }}>
          {busy && <div style={{ color: 'var(--ink3)', fontSize: 12, padding: 8 }}>加载中…</div>}
          {!busy && depts.map(dp => (
            <div key={'d' + dp.id} className="pa-item" onClick={() => enter(dp)} style={{ cursor: 'pointer' }}>
              <span>📁 {dp.name}</span><span className="pa-lk" style={{ marginLeft: 'auto' }}>进入 ›</span></div>
          ))}
          {!busy && members.map(mem => (
            <label key={mem.userid} className="pa-item" style={{ cursor: 'pointer' }}>
              <input type="checkbox" checked={!!sel[mem.userid]} onChange={() => toggle(mem)} style={{ marginRight: 8 }} />
              <span>👤 {mem.name}</span></label>
          ))}
          {!busy && !err && !depts.length && !members.length && <div style={{ color: 'var(--ink3)', fontSize: 12, padding: 8 }}>这个部门下没有子部门 / 成员</div>}
        </div>
        <div className="pa-bar" style={{ marginTop: 10, justifyContent: 'space-between' }}>
          <span style={{ fontSize: 12, color: 'var(--ink2)' }}>已选 {n} 人{n > 0 ? '：' + Object.values(sel).slice(0, 4).join('、') + (n > 4 ? '…' : '') : ''}</span>
          <span style={{ display: 'flex', gap: 8 }}><button className="pa-btn" onClick={onClose}>取消</button><button className="pa-btn pri" onClick={confirm} disabled={busy}>填入所选</button></span>
        </div>
      </div>
    </div>
  )
}
