// [Change Log]
// Date: 2026-08-17 | Author: Claude / c | Version: V2.301
// Description: 门户「模型配置」页 —— P0.5 聚合看板（替换占位盒子）。
//   设计稿：03_Source_Materials/中台模型配置页_设计稿_20260816.html（阶段化版）；
//   方案：模型配置中台_设计说明_20260816.md §5-bis —— 不建网关，只聚合各工作台
//   现成的 /api/llm/health（状态，登录可见）与 /api/llm/usage（用量/额度/豁免，管理员可见）。
//   页面骨架两阶段共用：网关才有的动作（更换密钥/新建凭证/轮换/吊销/集中审计）灰显 + P1 角标，
//   不做假按钮；P1 网关（OneAPI/LiteLLM 选型）落地后点亮这些位、数据源换网关库即可。
//   渲染于 Portal 的 .pt-root 内，复用其 CSS 变量（--panel/--ink/--brand/--ai/--green/--amber…）。
import React, { useEffect, useState } from 'react'
import { getLlmHubStatus, getLlmHubUsage, getGwCredentials, createGwCredential,
         revokeGwCredential, rotateGwCredential, getGwUsage, setWorkbenchKey,
         setWorkbenchPolicy, setWorkbenchModel, addWorkbenchProvider, getLlmHubAudit } from '../api.js'

const CSS = `
.mc-wrap{max-width:1180px;margin:26px auto;padding:0 24px}
.mc-wrap h2{font-size:20px;font-weight:800;margin-bottom:4px}
.mc-sub{color:var(--ink3);font-size:12.5px;margin-bottom:20px;display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.mc-pill{display:inline-flex;align-items:center;gap:5px;padding:1.5px 9px;border-radius:99px;font-size:11px;font-weight:600;white-space:nowrap}
.mc-pill::before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor}
.mc-pill.ok{background:rgba(52,211,153,.12);color:var(--green)}
.mc-pill.warn{background:rgba(251,191,36,.12);color:var(--amber)}
.mc-pill.off{background:rgba(248,113,113,.12);color:#f87171}
.mc-pill.gray{background:rgba(138,130,168,.14);color:var(--gray)}
.mc-card{background:linear-gradient(180deg,rgba(255,255,255,.028),rgba(255,255,255,0));border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:16px}
.mc-card h3{font-size:14.5px;font-weight:800;margin-bottom:14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.mc-vendor{background:rgba(255,255,255,.03);border:1px solid var(--line2);border-radius:12px;padding:13px 15px;transition:border-color .15s}
.mc-vendor:hover{border-color:rgba(124,92,255,.45)}
.mc-vendor b{font-size:13.5px}
.mc-kv{display:flex;justify-content:space-between;align-items:center;font-size:12px;margin-top:7px;color:var(--ink3)}
.mc-kv span:last-child{color:var(--ink2);font-family:Consolas,monospace}
.mc-btn{display:inline-flex;align-items:center;padding:4px 12px;border-radius:8px;font-size:12px;cursor:pointer;background:transparent;border:1px solid var(--line2);color:var(--brand2);margin:8px 6px 0 0;font-family:inherit;transition:border-color .15s,background .15s}
.mc-btn:hover{border-color:var(--brand2);background:rgba(124,92,255,.08)}
.mc-btn.p1{opacity:.38;cursor:not-allowed}
.mc-btn.p1:hover{border-color:var(--line2);background:transparent}
.mc-btn.pri{background:linear-gradient(180deg,var(--brand),#6A4CE6);color:#fff;border:0;font-weight:600;box-shadow:0 6px 14px rgba(90,60,200,.28)}
.mc-btn.pri:hover{filter:brightness(1.1);background:linear-gradient(180deg,var(--brand),#6A4CE6)}
.mc-btn.danger{color:#f87171;border-color:rgba(248,113,113,.35)}
.mc-btn.danger:hover{background:rgba(248,113,113,.08);border-color:#f87171}
.mc-btn small{font-size:9.5px;color:var(--amber);margin-left:3px}
.mc-inp{background:rgba(255,255,255,.05);border:1px solid var(--line2);border-radius:8px;padding:5px 10px;color:var(--ink);font-size:12.5px;font-family:inherit;outline:none;transition:border-color .15s}
.mc-inp:focus{border-color:var(--brand2)}
.mc-inp::placeholder{color:var(--ink3)}
.mc-table{width:100%;border-collapse:collapse;font-size:12.5px}
.mc-table th{text-align:left;color:var(--ink3);font-weight:600;font-size:11px;letter-spacing:.4px;padding:7px 10px;border-bottom:1px solid var(--line2);white-space:nowrap;background:transparent}
.mc-table td{padding:7px 10px;border-bottom:1px solid var(--line);color:var(--ink2)}
.mc-table tbody tr:last-child td{border-bottom:0}
.mc-table tbody tr:hover td{background:rgba(124,92,255,.06)}
/* 滚动区：表头钉住、细暗滚动条、只纵向滚（横向靠列宽约束，绝不出横条） */
.mc-scroll{max-height:320px;overflow-y:auto;overflow-x:hidden;border:1px solid var(--line);border-radius:10px}
.mc-scroll .mc-table th{position:sticky;top:0;background:#1d1830;z-index:1}
.mc-scroll .mc-table td{border-bottom-color:rgba(255,255,255,.05)}
.mc-scroll::-webkit-scrollbar{width:8px}
.mc-scroll::-webkit-scrollbar-track{background:transparent}
.mc-scroll::-webkit-scrollbar-thumb{background:rgba(124,92,255,.25);border-radius:4px}
.mc-scroll::-webkit-scrollbar-thumb:hover{background:rgba(124,92,255,.45)}
.mc-note{font-size:11.5px;color:var(--ink3);margin-top:10px;line-height:1.7}
.mc-note b{color:var(--brand2)}
.mc-banner{display:flex;gap:8px;align-items:flex-start;background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.2);color:var(--amber);border-radius:10px;padding:8px 12px;font-size:11.5px;margin-bottom:14px;line-height:1.65}
.mc-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.mc-kpi{position:relative;background:rgba(255,255,255,.03);border:1px solid var(--line2);border-radius:11px;padding:11px 14px 12px;overflow:hidden}
.mc-kpi::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--brand);opacity:.7}
.mc-kpi.teal::before{background:var(--ai)}
.mc-kpi.red::before{background:#f87171}
.mc-kpi.gray::before{background:var(--gray)}
.mc-kpi .v{font-size:22px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums}
.mc-kpi .s{font-size:10.5px;color:var(--ink3);letter-spacing:.5px}
.mc-bar{height:6px;background:rgba(255,255,255,.08);border-radius:3px;overflow:hidden;margin-top:5px;max-width:120px}
.mc-bar i{display:block;height:100%;background:linear-gradient(90deg,var(--brand),var(--brand2))}
.mc-tag{font-size:10.5px;padding:1px 8px;border-radius:6px;background:rgba(124,92,255,.14);color:var(--brand2);margin-right:4px;white-space:nowrap}
.mc-seg{display:inline-flex;border:1px solid var(--line2);border-radius:9px;overflow:hidden;margin-left:auto;background:rgba(255,255,255,.03)}
.mc-seg button{border:0;background:transparent;color:var(--ink3);font-size:12px;padding:4px 13px;cursor:pointer;font-family:inherit}
.mc-seg button:hover{color:var(--ink2)}
.mc-seg button.on{background:var(--brand);color:#fff;font-weight:600}
.mc-back{display:inline-block;font-size:12.5px;color:var(--brand2);cursor:pointer;margin-bottom:6px}
.mc-cols{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
@media(max-width:860px){.mc-cols{grid-template-columns:1fr}}
/* ②-b 策略表单：标签列定宽对齐，行距呼吸感 */
.mc-form{display:grid;grid-template-columns:96px 1fr;gap:12px 14px;align-items:center;font-size:12.5px}
.mc-form .lbl{color:var(--ink3);text-align:right}
.mc-form .hint{grid-column:2;font-size:10.5px;color:var(--ink3);margin-top:-8px}
`

// ── 添加接入（V2.306，Owner「现在有点写死的意思」）：选工作台 → 厂商（预置四家或自定义
//    任意 OpenAI 兼容厂商）→ 密钥/默认模型。自定义=工作台侧 BP_LLM_PROVIDERS 注册（V2.187 口子）。
const PRESET_VENDORS = ['deepseek', 'kimi', 'glm', 'dashscope']
function AddIntegration({ wbs, onDone }) {
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ workbench: '', provider: 'deepseek', custom: '', baseUrl: '', apiKey: '', model: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const integrated = wbs.filter(w => w.integrated)
  const isCustom = f.provider === '__custom__'
  const submit = () => {
    const wb = f.workbench || (integrated[0] && integrated[0].key)
    const prov = isCustom ? f.custom.trim().toLowerCase() : f.provider
    if (!wb) { setMsg('请选择工作台'); return }
    if (!prov) { setMsg('请填厂商名'); return }
    if (isCustom && !f.baseUrl.trim()) { setMsg('自定义厂商必须填接入地址'); return }
    if (isCustom && !f.model.trim()) { setMsg('自定义厂商必须填默认模型'); return }
    if (!f.apiKey.trim() && !f.model.trim() && !f.baseUrl.trim()) { setMsg('密钥/模型/地址至少填一样'); return }
    setBusy(true); setMsg('')
    addWorkbenchProvider({ workbench: wb, provider: prov,
      baseUrl: f.baseUrl.trim() || undefined, apiKey: f.apiKey.trim() || undefined,
      model: f.model.trim() || undefined })
      .then(() => { setOpen(false); setF({ workbench: '', provider: 'deepseek', custom: '', baseUrl: '', apiKey: '', model: '' }); onDone && onDone() })
      .catch(e => setMsg(String(e.message || e)))
      .finally(() => setBusy(false))
  }
  if (!open) {
    return <button className="mc-btn pri" style={{ marginTop: 0 }} onClick={() => setOpen(true)}>＋ 添加接入</button>
  }
  return (
    <div style={{ flexBasis: '100%', background: 'rgba(124,92,255,.06)', border: '1px solid rgba(124,92,255,.35)',
      borderRadius: 12, padding: '12px 14px', marginTop: 4 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--ink3)' }}>工作台</span>
        <select className="mc-inp" value={f.workbench} onChange={e => setF({ ...f, workbench: e.target.value })}>
          {integrated.map(w => <option key={w.key} value={w.key} style={{ color: '#222' }}>{w.name}</option>)}
          {wbs.filter(w => !w.integrated && w.home).map(w =>
            <option key={w.key} value="" disabled style={{ color: '#999' }}>{w.name}（未接入 llm 模块）</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--ink3)' }}>厂商</span>
        <select className="mc-inp" value={f.provider} onChange={e => setF({ ...f, provider: e.target.value })}>
          {PRESET_VENDORS.map(v => <option key={v} value={v} style={{ color: '#222' }}>{v}</option>)}
          <option value="__custom__" style={{ color: '#222' }}>自定义（任意 OpenAI 兼容）</option>
        </select>
        {isCustom && <>
          <input className="mc-inp" placeholder="厂商名（如 doubao）" value={f.custom}
            onChange={e => setF({ ...f, custom: e.target.value })} style={{ width: 130 }} />
          <input className="mc-inp" placeholder="接入地址 https://…（不含 /v1）" value={f.baseUrl}
            onChange={e => setF({ ...f, baseUrl: e.target.value })} style={{ width: 240, fontFamily: 'Consolas,monospace', fontSize: 11.5 }} />
        </>}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
        <input className="mc-inp" type="password" placeholder="API 密钥（只写不回显；可留空稍后再写）" value={f.apiKey}
          onChange={e => setF({ ...f, apiKey: e.target.value })} style={{ width: 260, fontFamily: 'Consolas,monospace', fontSize: 11.5 }} />
        <input className="mc-inp" placeholder={isCustom ? '默认模型（必填）' : '默认模型（留空=预置）'} value={f.model}
          onChange={e => setF({ ...f, model: e.target.value })} style={{ width: 180, fontFamily: 'Consolas,monospace', fontSize: 11.5 }} />
        <button className="mc-btn pri" style={{ marginTop: 0 }} disabled={busy} onClick={submit}>
          {busy ? '写入中…' : '保存接入'}</button>
        <button className="mc-btn" style={{ marginTop: 0 }} onClick={() => { setOpen(false); setMsg('') }}>取消</button>
        {msg && <span style={{ fontSize: 11.5, color: '#f87171' }}>{msg}</span>}
      </div>
      <div className="mc-note">写入目标工作台自己的 .env，立即生效；密钥全程不落中台。自定义厂商须为 OpenAI 兼容协议
        （工作台按「地址 + /v1/chat/completions」调用）。</div>
    </div>
  )
}

// ── 切默认模型（V2.304）：卡片上点模型名直接改。有常用建议、也允许手输（供应商上新不受限——
//    名字写错的后果是下次调用报供应商 4xx，改回即可，后端有意不做白名单）。
const MODEL_SUGGEST = {
  // deepseek 官方枚举（2026-08 查证）：deepseek-v4-flash / deepseek-v4-pro；
  // deepseek-chat / deepseek-reasoner 是兼容别名（chat 现映射 v4-flash，网关实测）。
  // v4-pro=旗舰（1M 上下文、强推理，更贵更慢）——日常路由/问数用 flash 足够，分析要深可临时切 pro。
  deepseek: ['deepseek-chat', 'deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-reasoner'],
  kimi: ['kimi-k3', 'moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'],
  glm: ['glm-4-plus', 'glm-4-air', 'glm-4-flash'],
  dashscope: ['qwen-plus', 'qwen-max', 'qwen-turbo'],
}
function ModelSwitcher({ wb, p, onDone }) {
  const [open, setOpen] = useState(false)
  const [val, setVal] = useState(p.model || '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  // 切换成功 → status 刷新 → p.model 变化 → 无论 promise 时序如何，编辑态必收起
  useEffect(() => { setOpen(false); setBusy(false); setMsg('') }, [p.model])
  if (!wb.reachable) return <span style={{ fontFamily: 'Consolas,monospace' }}>{p.model || '—'}</span>
  const save = (m) => {
    const target = (m || val).trim()
    if (!target || target === p.model) { setOpen(false); return }
    setBusy(true); setMsg('')
    setWorkbenchModel({ workbench: wb.key, provider: p.name, model: target })
      .then(() => { setOpen(false); onDone && onDone() })
      .catch(e => setMsg(String(e.message || e)))
      .finally(() => setBusy(false))
  }
  if (!open) {
    return (
      <span title="点击切换默认模型" onClick={() => { setVal(p.model || ''); setOpen(true) }}
        style={{ fontFamily: 'Consolas,monospace', cursor: 'pointer', borderBottom: '1px dashed var(--brand2)' }}>
        {p.model || '—'} ▾</span>
    )
  }
  const sugg = MODEL_SUGGEST[p.name] || []
  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 4, alignItems: 'flex-end' }}>
      <span style={{ display: 'inline-flex', gap: 4 }}>
        <input className="mc-inp" value={val} autoFocus onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setOpen(false) }}
          style={{ padding: '2px 8px', fontSize: 11.5, width: 150, fontFamily: 'Consolas,monospace' }} />
        <button className="mc-btn pri" style={{ marginTop: 0, padding: '2px 9px' }}
          disabled={busy} onClick={() => save()}>{busy ? '…' : '✔'}</button>
        <button className="mc-btn" style={{ marginTop: 0, padding: '2px 8px' }} onClick={() => setOpen(false)}>✕</button>
      </span>
      {sugg.length > 0 && (
        <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {sugg.map(m => (
            <span key={m} onClick={() => save(m)}
              className="mc-tag" style={{ cursor: 'pointer', opacity: m === p.model ? .45 : 1 }}>{m}</span>
          ))}
        </span>
      )}
      {msg && <span style={{ fontSize: 11, color: '#f87171' }}>{msg}</span>}
    </span>
  )
}

// ── 更换密钥：一次性写入框（V2.303）。只写不回显——保存后只刷新打码 keyHint。
//    key 直达该工作台后端写它自己的 .env（立即生效，不用重启）；核算侧不存不记值。
function KeyWriter({ wb, p, onDone }) {
  const [open, setOpen] = useState(false)
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  if (!wb.reachable) {
    return <button className="mc-btn p1" disabled title="该工作台后端不可达">更换密钥</button>
  }
  const save = () => {
    if (!val.trim()) { setMsg('key 不能为空'); return }
    setBusy(true); setMsg('')
    setWorkbenchKey({ workbench: wb.key, provider: p.name, apiKey: val.trim() })
      .then(r => { setVal(''); setOpen(false); setMsg(''); onDone && onDone(r) })
      .catch(e => setMsg(String(e.message || e)))
      .finally(() => setBusy(false))
  }
  if (!open) {
    return <button className="mc-btn" onClick={() => setOpen(true)}>{p.configured ? '更换密钥' : '写入密钥'}</button>
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
      <input className="mc-inp" type="password" autoFocus placeholder={`粘贴 ${p.name} 的新 key（只写不回显）`}
        value={val} onChange={e => setVal(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save() }}
        style={{ width: 220, fontFamily: 'Consolas,monospace', fontSize: 12 }} />
      <button className="mc-btn pri" style={{ marginTop: 0 }}
        disabled={busy} onClick={save}>{busy ? '写入中…' : '保存'}</button>
      <button className="mc-btn" style={{ marginTop: 0 }} onClick={() => { setOpen(false); setVal(''); setMsg('') }}>取消</button>
      {msg && <span style={{ fontSize: 11.5, color: '#f87171' }}>{msg}</span>}
    </span>
  )
}

// ── ②-b 人员策略编辑（V2.303 P1）：集中改 BP 的 默认每人日限 + 豁免名单，写回该台 .env 并热载。
//    全站硬闸只展示不可改（防烧穿的最后一道，只能上服务器改 .env——工作台端点就没开放它）。
function PolicyEditor({ limits, onSaved }) {
  const [calls, setCalls] = useState(limits.dailyCalls)
  const [tokens, setTokens] = useState(limits.dailyTokens)
  const [exempt, setExempt] = useState((limits.exempt || []).map(e => { try { return decodeURIComponent(e) } catch { return e } }).join('，'))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const save = () => {
    setBusy(true); setMsg('')
    setWorkbenchPolicy({
      workbench: 'bp', dailyCalls: Number(calls) || 0, dailyTokens: Number(tokens) || 0,
      exempt: exempt.split(/[,，;；\s]+/).map(s => s.trim()).filter(Boolean),
    })
      .then(() => { setMsg('✔ 已生效（无需重启）'); onSaved && onSaved() })
      .catch(e => setMsg('⚠ ' + String(e.message || e)))
      .finally(() => setBusy(false))
  }
  return (
    <>
      <div className="mc-form">
        <span className="lbl">默认每人日限</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <input className="mc-inp" type="number" min="0" value={calls} onChange={e => setCalls(e.target.value)} style={{ width: 84 }} />
          <span style={{ color: 'var(--ink3)' }}>次 /</span>
          <input className="mc-inp" type="number" min="0" value={tokens} onChange={e => setTokens(e.target.value)} style={{ width: 104 }} />
          <span style={{ color: 'var(--ink3)' }}>token</span>
          <span style={{ fontSize: 10.5, color: 'var(--ink3)' }}>（0=不限）</span>
        </span>
        <span className="lbl">豁免名单</span>
        <input className="mc-inp" value={exempt} onChange={e => setExempt(e.target.value)}
          placeholder="逗号分隔姓名（与登录名一致）；空=无人豁免" style={{ maxWidth: 340 }} />
        <span className="hint">豁免只免个人日限；全站硬闸对豁免者同样生效</span>
        <span className="lbl">全站日硬闸</span>
        <span style={{ color: 'var(--ink2)' }}>{limits.siteDailyTokens ? fmtW(limits.siteDailyTokens) + ' token' : '不限'}
          <span style={{ fontSize: 10.5, color: 'var(--ink3)', marginLeft: 8 }}>🔒 只读——防烧穿的最后一道，仅服务器 .env 可改</span></span>
      </div>
      <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
        <button className="mc-btn pri" style={{ marginTop: 0 }} disabled={busy} onClick={save}>
          {busy ? '保存中…' : '保存 · 立即生效'}</button>
        {msg && <span style={{ fontSize: 12, color: msg.startsWith('✔') ? 'var(--green)' : '#f87171' }}>{msg}</span>}
      </div>
      <div className="mc-note">当前对 <b>财务BP工作台</b> 生效（写回该台 .env 并热载）；其他工作台接入后同此管理。</div>
    </>
  )
}

// ── ④ 调用审计聚合（V2.303 P1）：各工作台 llm_ask_log 合并——问数/AI分析/通用对话全覆盖 ──
const AUDIT_CAP_CN = { analysis: 'AI分析', chat: '对话', plan: '多步查询', none: '拒答',
  top_brands: '品牌排名', top_customers: '客户排名', revenue_breakdown: '收入构成',
  achievement: '达成率', expense_summary: '费用', facts_query: '通用查询',
  customer_changes: '客户变动', biz_insight: '经营分析' }
function AuditPanel() {
  const [days, setDays] = useState(7)
  const [onlyErr, setOnlyErr] = useState(false)
  const [data, setData] = useState(null)
  const [denied, setDenied] = useState(false)
  useEffect(() => {
    getLlmHubAudit(days, onlyErr).then(d => { setData(d); setDenied(false) }).catch(() => setDenied(true))
  }, [days, onlyErr])
  if (denied) return <div className="mc-note">调用审计仅管理员可见。</div>
  const rows = data?.rows || []
  const fmtT = (ts) => { const d = new Date((ts || 0) * 1000); const p = n => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}` }
  const dec = (s) => { try { return decodeURIComponent(s || '') } catch { return s || '' } }
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span className="mc-seg" style={{ marginLeft: 0 }}>
          {[7, 30].map(d => <button key={d} className={days === d ? 'on' : ''} onClick={() => setDays(d)}>近 {d} 天</button>)}
        </span>
        <label style={{ fontSize: 12, color: 'var(--ink3)', cursor: 'pointer' }}>
          <input type="checkbox" checked={onlyErr} onChange={e => setOnlyErr(e.target.checked)} style={{ marginRight: 4 }} />只看报错</label>
        {data?.unreachable?.length ? <span style={{ fontSize: 11.5, color: 'var(--amber)' }}>
          ⚠ {data.unreachable.map(u => u.workbench).join('、')}不可达，未计入</span> : null}
      </div>
      <div className="mc-scroll">
        <table className="mc-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: 86 }} /><col style={{ width: 52 }} /><col style={{ width: 92 }} />
            <col style={{ width: 84 }} /><col /><col style={{ width: 68 }} />
          </colgroup>
          <thead><tr><th>时间</th><th>台</th><th>人</th><th>类型</th><th>内容（悬停看答案）</th><th>状态</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{fmtT(r.ts)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{(r.workbench || '').replace('财务', '').replace('工作台', '').replace('办公室', '')}</td>
                <td style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--ink)' }}>{dec(r.user) || '（匿名）'}</td>
                <td style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{AUDIT_CAP_CN[r.capability] || r.capability || '—'}</td>
                <td style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  title={(r.question || '') + (r.answer ? '\n—— ' + r.answer : '')}>{r.question}</td>
                <td>{r.ok ? <span className="mc-pill ok">成功</span> : <span className="mc-pill off" title={r.err}>失败</span>}</td>
              </tr>
            ))}
            {data && !rows.length && <tr><td colSpan={6} className="mc-note" style={{ padding: 14 }}>该区间没有调用记录。</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="mc-note">口径：各工作台 llm_ask_log 聚合（问数/AI 分析/通用对话全覆盖，BP V2.229 起）；
        悬停「内容」列可见答案摘要；查询计划到 BP 工作台「问数审计」看全量。</div>
    </>
  )
}

// ── P1 网关：内部凭证管理面板（V2.302，网关在线且管理员时渲染）──
function GatewayPanel({ gw }) {
  const [creds, setCreds] = useState(null)
  const [denied, setDenied] = useState(false)
  const [gwUsage, setGwUsage] = useState(null)
  const [form, setForm] = useState({ workbench: '', provider: 'deepseek', dailyCalls: 0, dailyTokens: 0 })
  const [freshToken, setFreshToken] = useState(null)   // {token, note} 签发/轮换后只显示一次
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = () => {
    getGwCredentials().then(r => { setCreds(r.credentials || []); setDenied(false) })
      .catch(() => setDenied(true))
    getGwUsage(7).then(setGwUsage).catch(() => {})
  }
  useEffect(load, [])

  const doCreate = () => {
    if (!form.workbench.trim()) { setErr('workbench 必填（如 bp / hesuan / worker）'); return }
    setBusy(true); setErr('')
    createGwCredential(form)
      .then(r => { setFreshToken(r); load() })
      .catch(e => setErr(String(e.message || e)))
      .finally(() => setBusy(false))
  }
  const doRevoke = (c) => {
    if (!window.confirm(`吊销凭证 #${c.id}（${c.workbench}）？该工作台的 AI 调用将立即失效，且不可恢复（只能重新签发）。`)) return
    revokeGwCredential(c.id).then(load).catch(e => setErr(String(e.message || e)))
  }
  const doRotate = (c) => {
    if (!window.confirm(`轮换凭证 #${c.id}（${c.workbench}）？旧凭证立即失效，需把新凭证写入该工作台 .env。`)) return
    rotateGwCredential(c.id).then(r => { setFreshToken(r); load() }).catch(e => setErr(String(e.message || e)))
  }

  if (denied) return <div className="mc-note">凭证管理仅管理员可见。</div>
  return (
    <>
      {freshToken && (
        <div className="mc-banner" style={{ borderColor: 'rgba(52,211,153,.4)', color: 'var(--green)', background: 'rgba(52,211,153,.07)' }}>
          ✔ 新凭证（<b>只显示这一次</b>，请立即复制并写入该工作台 .env 的 BP_LLM_API_KEY）：
          <div style={{ fontFamily: 'Consolas,monospace', fontSize: 13, margin: '6px 0', userSelect: 'all' }}>{freshToken.token}</div>
          同时把 BASE_URL 指到网关：<code>BP_LLM_BASE_URL={gw.base}/ai</code>
          <span className="mc-btn" style={{ marginTop: 0 }} onClick={() => setFreshToken(null)}>我已保存，关闭</span>
        </div>
      )}
      {err && <div className="mc-banner">⚠ {err}</div>}
      <table className="mc-table">
        <thead><tr><th>#</th><th>凭证</th><th>工作台</th><th>供应商</th><th>允许模型</th><th>日额度</th><th>今日已用</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          {(creds || []).map(c => (
            <tr key={c.id} style={c.active ? null : { opacity: .45 }}>
              <td>{c.id}</td>
              <td style={{ fontFamily: 'Consolas,monospace', fontSize: 12 }}>{c.token}</td>
              <td style={{ color: 'var(--ink)', fontWeight: 600 }}>{c.workbench}</td>
              <td>{c.provider}</td>
              <td>{c.models?.length ? c.models.map(m => <span className="mc-tag" key={m}>{m}</span>) : <span className="mc-tag">默认模型</span>}</td>
              <td>{(c.dailyCalls ? `${c.dailyCalls} 次` : '不限') + ' / ' + (c.dailyTokens ? fmtW(c.dailyTokens) : '不限')}</td>
              <td>{c.todayUsed ? `${c.todayUsed.calls} 次 / ${fmtW(c.todayUsed.tokens)}` : '—'}</td>
              <td><span className={'mc-pill ' + (c.active ? 'ok' : 'off')}>{c.active ? '启用' : '已吊销'}</span></td>
              <td>{c.active && <>
                <button className="mc-btn" style={{ marginTop: 0 }} onClick={() => doRotate(c)}>轮换</button>
                <button className="mc-btn danger" style={{ marginTop: 0 }} onClick={() => doRevoke(c)}>吊销</button>
              </>}</td>
            </tr>
          ))}
          {creds && !creds.length && <tr><td colSpan={9} className="mc-note">还没有签发凭证——用下方表单给工作台签发第一个。</td></tr>}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--ink3)' }}>签发新凭证：</span>
        <input className="mc-inp" placeholder="workbench（bp/hesuan/worker）" value={form.workbench}
          onChange={e => setForm({ ...form, workbench: e.target.value })} style={{ width: 210 }} />
        <select className="mc-inp" value={form.provider} onChange={e => setForm({ ...form, provider: e.target.value })}>
          {(gw.providers || []).map(p => <option key={p.name} value={p.name} style={{ color: '#222' }}>{p.name}{p.configured ? '' : '（网关未配 key）'}</option>)}
        </select>
        <input className="mc-inp" type="number" min="0" placeholder="日调用上限 0=不限" value={form.dailyCalls}
          onChange={e => setForm({ ...form, dailyCalls: Number(e.target.value) || 0 })} title="每日调用次数上限，0=不限" style={{ width: 118 }} />
        <input className="mc-inp" type="number" min="0" placeholder="日token上限 0=不限" value={form.dailyTokens}
          onChange={e => setForm({ ...form, dailyTokens: Number(e.target.value) || 0 })} title="每日 token 上限，0=不限" style={{ width: 128 }} />
        <button className="mc-btn pri" style={{ marginTop: 0 }} disabled={busy} onClick={doCreate}>
          {busy ? '签发中…' : '签发凭证'}</button>
      </div>
      <div className="mc-note">凭证列表永远只显示打码值；完整凭证只在签发/轮换时出现一次。工作台切到网关 = 改两行 .env：
        <b>BP_LLM_BASE_URL={gw.base}/ai</b> + <b>BP_LLM_API_KEY=tok_…</b>，回退直连也是改回这两行。</div>
      {gwUsage?.byWorkbench?.length ? <>
        <div className="mc-note" style={{ marginTop: 14, marginBottom: 6 }}><b>网关近 7 天用量（按工作台）</b></div>
        <table className="mc-table">
          <thead><tr><th>工作台</th><th>调用</th><th>token</th><th>失败</th><th>均耗时</th></tr></thead>
          <tbody>{gwUsage.byWorkbench.map(r => (
            <tr key={r.workbench}><td>{r.workbench}</td><td>{r.calls}</td><td>{fmtW(r.tokens)}</td><td>{r.failed || 0}</td><td>{r.avgMs} ms</td></tr>
          ))}</tbody>
        </table>
      </> : null}
    </>
  )
}

const fmtW = (n) => {
  if (n == null) return '—'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + ' 亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + ' 万'
  return String(n)
}
const lim = (v, unit) => (v ? `${fmtW(v)} ${unit}` : '不限')

export default function ModelConfig({ user, onBack }) {
  const [stat, setStat] = useState(null)       // /api/llm-hub/status
  const [checking, setChecking] = useState(false)
  const [usage, setUsage] = useState(null)     // /api/llm-hub/usage（管理员）
  const [usageDenied, setUsageDenied] = useState(false)
  const [days, setDays] = useState(7)

  const loadStatus = (fresh) => {
    setChecking(true)
    return getLlmHubStatus(fresh).then(setStat).catch(() => {}).finally(() => setChecking(false))
  }
  useEffect(() => { loadStatus(false) }, [])
  useEffect(() => {
    getLlmHubUsage(days).then(r => { setUsage(r); setUsageDenied(false) })
      .catch(e => { setUsage(null); setUsageDenied(true) })
  }, [days])

  const wbs = stat?.workbenches || []
  const bpUsage = usage?.workbenches?.find(w => w.key === 'bp' && w.ok)?.usage
  const limits = bpUsage?.limits
  // sv-SE 区域格式恰好是 YYYY-MM-DD；不用 toISOString——那是 UTC 日期，本地 0–8 点会差一天对不上 BP 的 day
  const today = new Date().toLocaleDateString('sv-SE')
  const todayRow = bpUsage?.byDay?.find(d => d.day === today)

  // ① 供应商卡片：只列已接入工作台的供应商清单（BP /health 的 providers 数组，key 已打码）
  const vendorCards = []
  wbs.filter(w => w.integrated).forEach(w => {
    (w.providers?.length ? w.providers : [{ name: w.provider, configured: w.configured, model: w.model, keyHint: w.keyHint, isDefault: true }])
      .forEach(p => vendorCards.push({ wb: w, p }))
  })

  return (
    <div className="mc-wrap">
      <style>{CSS}</style>
      <span className="mc-back" onClick={onBack}>← 返回工作台门户</span>
      <h2>模型配置</h2>
      <div className="mc-sub">
        统一呈现各工作台的模型状态与用量
        <span className="mc-pill ok">P0.5 聚合看板 · 已启用</span>
        {stat?.gateway?.available
          ? <span className="mc-pill ok">网关 · 在线（{stat.gateway.base}）</span>
          : <span className="mc-pill warn">网关 · 未启动（start_gateway.bat）</span>}
        <span>—— 密钥 / 人员策略 / 调用审计已可集中管理；「内部凭证」需网关在线</span>
      </div>

      {/* ① 供应商与密钥（P0.5 只读聚合：数据来自各工作台 /api/llm/health，key 打码；密钥仍在各台服务器 .env 维护） */}
      <div className="mc-card">
        <h3>① 供应商与密钥 <span className="mc-pill ok">聚合展示 + 一次性写入</span>
          <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink3)' }}>密钥存各工作台服务器 .env；此处只显示打码，管理员可一次性写入（立即生效）</span>
          <button className="mc-btn" style={{ marginLeft: 'auto', marginTop: 0 }} onClick={() => loadStatus(true)} disabled={checking}>
            {checking ? '自检中…' : '↻ 全部自检'}
          </button>
          <AddIntegration wbs={wbs} onDone={() => loadStatus(true)} />
        </h3>
        <div className="mc-grid">
          {vendorCards.map(({ wb, p }, i) => (
            <div className="mc-vendor" key={i}>
              <b>{(p.name || '').toUpperCase() || '—'}</b>{' '}
              <span style={{ fontSize: 11, color: 'var(--ink3)' }}>· {wb.name}</span>{' '}
              {p.isDefault && <span className="mc-tag">默认</span>}
              <span className={'mc-pill ' + (p.configured ? 'ok' : 'gray')} style={{ float: 'right' }}>
                {p.configured ? '已配置' : '未配置'}
              </span>
              <div className="mc-kv"><span>密钥</span><span>{p.keyHint || '—'}</span></div>
              <div className="mc-kv"><span>默认模型</span>
                <ModelSwitcher wb={wb} p={p} onDone={() => loadStatus(true)} /></div>
              <div className="mc-kv"><span>连通性</span>
                <span style={{ color: wb.reachable ? 'var(--green)' : '#f87171' }}>
                  {wb.reachable ? `正常 · ${wb.latencyMs}ms` : (wb.error ? '不可达' : '—')}
                </span>
              </div>
              <div>
                {wb.home && <button className="mc-btn" onClick={() => { window.location.href = wb.home }}>跳转该工作台</button>}
                <KeyWriter wb={wb} p={p} onDone={() => loadStatus(true)} />
              </div>
            </div>
          ))}
          {!vendorCards.length && <div className="mc-note">暂无已接入工作台{stat ? '' : '（状态加载中…）'}</div>}
        </div>
        <div className="mc-note">按<b>工作台</b>开独立厂商账号：账单发票级分离、并发池互不抢占（DeepSeek 额度按账号计，不按 key）。
          「更换密钥」为<b>一次性写入框</b>：只写不回显、直达该工作台自己的 .env、立即生效不用重启；页面任何位置不出现完整密钥。</div>
      </div>

      {/* ② 工作台接入状态（P0.5）／凭证管理（P1） */}
      <div className="mc-card">
        <h3>② 工作台接入状态 <span className="mc-pill ok">P0.5</span>
          <span style={{ fontWeight: 400 }}>／ 凭证管理 <span className="mc-pill warn">P1</span></span>
          <button className="mc-btn p1" style={{ marginLeft: 'auto', marginTop: 0 }} disabled>新建凭证 <small>P1</small></button>
        </h3>
        <table className="mc-table">
          <thead><tr><th>接入方式</th><th>工作台</th><th>厂商账号</th><th>默认模型</th><th>每日额度（每人）</th><th>今日已用（全台）</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            {wbs.map(w => {
              const isBp = w.key === 'bp'
              const used = isBp ? todayRow : null
              return (
                <tr key={w.key}>
                  <td>{w.integrated
                    ? (w.viaGateway
                      ? <span className="mc-pill ok" title="该工作台 BASE_URL 已指向网关——凭证额度/吊销对它生效，用量以 ②-c 网关流水为准">经网关</span>
                      : <>直连厂商 <span style={{ fontSize: 10.5, color: 'var(--ink3)' }} title="该工作台 BASE_URL 直指供应商——网关凭证对它不生效；切网关=改两行 .env">ⓘ</span></>)
                    : (w.home ? '未接入' : '规划中')}</td>
                  <td style={{ color: 'var(--ink)', fontWeight: 600 }}>{w.name}</td>
                  <td>{w.vendor}</td>
                  <td>{w.model || '—'}</td>
                  <td>{isBp && limits ? `${lim(limits.dailyCalls, '次')} / ${lim(limits.dailyTokens, 'token')}` : '—'}</td>
                  <td>{used ? <>
                    {used.calls} 次 / {fmtW(used.tokens)} token
                    {limits?.siteDailyTokens ? <div className="mc-bar"><i style={{ width: Math.min(100, used.tokens / limits.siteDailyTokens * 100) + '%' }} /></div> : null}
                  </> : (isBp && bpUsage ? '0 次' : '—')}</td>
                  <td>{w.integrated
                    ? (w.reachable
                      ? <span className={'mc-pill ' + (w.configured ? 'ok' : 'warn')}>{w.configured ? '启用' : '未配 key'}</span>
                      : <span className="mc-pill off">不可达</span>)
                    : <span className="mc-pill gray">{w.home ? '待接入' : '规划中'}</span>}</td>
                  <td>
                    {w.integrated && <button className="mc-btn" style={{ marginTop: 0 }} onClick={() => document.getElementById('mc-usage')?.scrollIntoView({ behavior: 'smooth' })}>查看用量</button>}
                    <button className="mc-btn p1" style={{ marginTop: 0 }} disabled>轮换 <small>P1</small></button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className="mc-note">「未接入」的接入路径（零新增服务）：拷 BP 的 <b>app/llm*.py</b> 模块获得同款
          /api/llm 接口 + 该台服务器 .env 配独立厂商账号的 key，本页自动点亮。触发建 P1 网关的信号：
          工作台 ≥4 / 月费用显著 / 出现"须立即吊销某台"治理事件（设计说明 §5-bis）。</div>
      </div>

      {/* ②-c 内部凭证（P1 网关；在线才渲染，离线保持 P0.5 只读形态） */}
      {stat?.gateway?.available && (
        <div className="mc-card">
          <h3>②-c 内部凭证 <span className="mc-pill ok">P1 网关在线</span>
            {!stat.gateway.adminEnabled && <span className="mc-pill warn">管理面未启用（网关 .env 缺 GW_ADMIN_TOKEN）</span>}
            <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink3)' }}>
              厂商 key 锁死网关 .env；工作台只持可吊销的内部凭证</span></h3>
          {stat.gateway.adminEnabled
            ? <GatewayPanel gw={stat.gateway} />
            : <div className="mc-note">在网关 backend/.env 配置 GW_ADMIN_TOKEN 并重启网关后，此处可签发/轮换/吊销凭证。</div>}
        </div>
      )}

      <div className="mc-cols">
        {/* ②-b 人员策略（V2.303：集中编辑，写回工作台 .env 并热载） */}
        <div className="mc-card">
          <h3>②-b 人员策略 <span className="mc-pill ok">集中编辑 · 立即生效</span></h3>
          {limits
            ? <PolicyEditor key={JSON.stringify(limits)} limits={limits}
                onSaved={() => { getLlmHubUsage(days).then(setUsage).catch(() => {}) }} />
            : <div className="mc-note">{usageDenied ? '仅管理员可见。' : '加载中…（BP 工作台需在线）'}</div>}
        </div>

        {/* ④ 调用审计（V2.303：各工作台审计账聚合） */}
        <div className="mc-card">
          <h3>④ 调用审计 <span className="mc-pill ok">全工作台聚合</span></h3>
          <AuditPanel />
        </div>
      </div>

      {/* ③ 用量看板（管理员） */}
      <div className="mc-card" id="mc-usage">
        <h3>③ 用量看板 <span className="mc-pill ok">P0.5 · 数据源：BP /api/llm/usage</span>
          {bpUsage && <span className="mc-seg">
            {[7, 30].map(d => <button key={d} className={days === d ? 'on' : ''} onClick={() => setDays(d)}>近 {d} 天</button>)}
          </span>}
        </h3>
        {usageDenied && <div className="mc-note">用量看板仅管理员（主管理员/工作台子管理员）可见。</div>}
        {!usageDenied && !bpUsage && <div className="mc-note">暂无数据（BP 工作台后端需在线）。</div>}
        {bpUsage && <>
          {bpUsage.trustNote && <div className="mc-banner">⚠ {bpUsage.trustNote}</div>}
          <div className="mc-kpis">
            <div className="mc-kpi"><div className="s">调用（近 {days} 天）</div><div className="v">{bpUsage.byUser.reduce((s, r) => s + r.calls, 0)}</div></div>
            <div className="mc-kpi teal"><div className="s">TOKEN</div><div className="v">{fmtW(bpUsage.byUser.reduce((s, r) => s + r.tokens, 0))}</div></div>
            <div className={'mc-kpi' + (bpUsage.byUser.reduce((s, r) => s + (r.failed || 0), 0) ? ' red' : ' gray')}>
              <div className="s">失败次数</div><div className="v">{bpUsage.byUser.reduce((s, r) => s + (r.failed || 0), 0)}</div></div>
            <div className="mc-kpi gray"><div className="s">使用人数</div><div className="v">{bpUsage.byUser.length}</div></div>
          </div>
          <div className="mc-cols">
            <div>
              <div className="mc-note" style={{ marginTop: 0, marginBottom: 6 }}><b>按人</b>（X-BP-User 记账）</div>
              <table className="mc-table">
                <thead><tr><th>人</th><th>调用</th><th>token</th><th>失败</th><th>均耗时</th></tr></thead>
                <tbody>{bpUsage.byUser.map(r => (
                  <tr key={r.user}><td>{decodeURIComponent(r.user || '') || '（匿名）'}</td><td>{r.calls}</td><td>{fmtW(r.tokens)}</td><td>{r.failed || 0}</td><td>{r.avgMs} ms</td></tr>
                ))}</tbody>
              </table>
            </div>
            <div>
              <div className="mc-note" style={{ marginTop: 0, marginBottom: 6 }}><b>按用途</b> / <b>按天</b></div>
              <table className="mc-table">
                <thead><tr><th>用途</th><th>调用</th><th>token</th></tr></thead>
                <tbody>{bpUsage.byPurpose.map(r => (
                  <tr key={r.purpose}><td>{r.purpose}</td><td>{r.calls}</td><td>{fmtW(r.tokens)}</td></tr>
                ))}</tbody>
              </table>
              <table className="mc-table" style={{ marginTop: 10 }}>
                <thead><tr><th>日期</th><th>调用</th><th>token</th></tr></thead>
                <tbody>{bpUsage.byDay.slice(-7).map(r => (
                  <tr key={r.day}><td>{r.day}</td><td>{r.calls}</td><td>{fmtW(r.tokens)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        </>}
      </div>
    </div>
  )
}
