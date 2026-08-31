// [Change Log] Date:2026-08-20 Author:Claude/Reginald Zeng Version:V2.328（上版 V2.19）
// V2.328：加「同步出厂工具集」——把卡片刷成代码持有的 PORTAL_TOOL_DEFAULTS（两台已开发工具的现状）：
//   同名覆盖、缺失补插、手工新增的卡保留不删。人点按钮才跑，弹确认，结果计数回显。
// 门户管理（门户内页签，仅管理员）：维护各工作台的工具卡片——所属工作台/名称/状态/概述/通用技能/AI技能。
// 数据存 portal_tools 表，门户卡片实时读取；改这里 = 门户即时更新，无需改代码/发版。深色 pa- 作用域。
import React, { useEffect, useState } from 'react'
import { getPortalTools, savePortalTool, deletePortalTool, resetPortalTools } from '../api.js'

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
`

const blank = () => ({ id: null, lane: 'accounting', name: '', status: 'beta', icon: '▤', desc: '', genStr: '', aiStr: '', mods: [], statusSrc: 'manual', autoDetail: [] })

export default function PortalAdmin({ onChange }) {
  const [tools, setTools] = useState([])
  const [sel, setSel] = useState(null)      // 编辑中的 {..} 或 null
  const [msg, setMsg] = useState(null)
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
      <div className="pa-sub">维护各工作台首页的工具卡片，保存后门户即时更新 · 仅管理员
        {msg && <span style={{ color: msg.ok ? 'var(--green)' : 'var(--red)', marginLeft: 8 }}>{msg.t}</span>}</div>

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
    </div>
  )
}
