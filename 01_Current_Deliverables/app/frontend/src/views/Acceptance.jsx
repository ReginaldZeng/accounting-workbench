// [Change Log] Date:2026-09-06 Author:Claude/Reginald Zeng Version:V2.492
// 验收台账（放「通用」板块，全员可见）。一页看四件事：需求做到哪步(进度=实时nav状态) / 验收结论(管理员拍板) /
// 一线满意度(全员打分取均值) / 近7天调用量(复用日志中心 ops 埋点)。验收/打分动作都进 audit_log →「日志中心›操作留痕」可复查。
// 角色差异：一线只看状态+打分；验收结论下拉、意见编辑、账号/时间调用明细仅主管理员/子管理员。
import React, { useState, useEffect, useCallback } from 'react'
import { getAcceptance, rateTool, setVerdict, getUsageDetail } from '../api.js'

const pad = n => String(n).padStart(2, '0')
const fmtAgo = (sec) => {
  if (!sec) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - sec))
  if (s < 60) return '刚刚'
  if (s < 3600) return Math.floor(s / 60) + '分钟前'
  if (s < 86400) return Math.floor(s / 3600) + '小时前'
  const d = new Date(sec * 1000)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 进度状态色（与侧栏同口径）
const stStyle = (s) => {
  if (s === '已上线' || s === '引擎正常') return { c: 'var(--green)', b: 'var(--green-bg)' }
  if (s === '待验收' || s === '测试验证') return { c: 'var(--accent)', b: 'var(--accent-soft)' }
  if (s === '人工并行') return { c: 'var(--teal)', b: 'var(--teal-bg)' }
  if (s === '开发中') return { c: 'var(--amber)', b: 'var(--amber-bg)' }
  return { c: 'var(--ink-3)', b: 'var(--bg-rail)' }
}
const vdStyle = (v) => {
  if (v === '通过') return { c: 'var(--green)', b: 'var(--green-bg)', t: '✓ 通过' }
  if (v === '打回') return { c: 'var(--red)', b: 'var(--red-bg)', t: '⤴ 打回' }
  return { c: 'var(--amber)', b: 'var(--amber-bg)', t: '⏳ 待验收' }
}

const CSS = `
.ac-nudge{display:flex;align-items:center;gap:10px;background:var(--accent-soft);border:1px solid var(--accent-soft);border-radius:12px;padding:11px 15px;font-size:13px}
.ac-nudge .b{color:var(--accent);font-weight:700}
.ac-nudge .done{color:var(--green);font-weight:600}
.ac-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
.ac-kpi{background:var(--bg-sub);border:1px solid var(--line);border-radius:11px;padding:11px 13px}
.ac-kpi .v{font-size:22px;font-weight:800;line-height:1.1}
.ac-kpi .k{font-size:11.5px;color:var(--ink-3);margin-top:3px}
.ac-seg{display:inline-flex;border:1px solid var(--line-strong);border-radius:8px;overflow:hidden}
.ac-seg button{font-family:inherit;border:0;background:var(--bg);color:var(--ink-2);cursor:pointer;font-size:12.5px;padding:6px 13px}
.ac-seg button+button{border-left:1px solid var(--line)}
.ac-seg button.on{background:var(--accent-soft);color:var(--accent);font-weight:700}
.ac-twrap{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--bg)}
table.ac-t{width:100%;border-collapse:collapse;font-size:12.5px}
.ac-t th{position:sticky;top:0;z-index:1;background:var(--bg-rail);color:var(--ink-2);font-weight:600;text-align:left;padding:9px 12px;white-space:nowrap;border-bottom:1px solid var(--line)}
.ac-t td{padding:9px 12px;border-bottom:1px solid var(--line);color:var(--ink);vertical-align:middle}
.ac-t tr:last-child td{border-bottom:0}
.ac-t .sec td{background:var(--bg-sub);color:var(--ink-2);font-size:11.5px;font-weight:600;padding:6px 12px}
.ac-t tbody tr.data:hover{background:var(--bg-sub)}
.ac-pill{display:inline-block;font-size:11px;font-weight:600;padding:1.5px 9px;border-radius:999px;white-space:nowrap}
.ac-mut{color:var(--ink-3)}
.ac-stars{white-space:nowrap;font-size:16px;letter-spacing:1px;line-height:1}
.ac-star{color:var(--line-strong)}
.ac-star.on{color:var(--amber)}
.ac-star.ed{cursor:pointer}
.ac-star.ed:hover{transform:scale(1.15)}
.ac-sel{font-family:inherit;font-size:12px;height:28px;border:1px solid var(--line-strong);border-radius:7px;background:var(--bg);color:var(--ink);padding:0 6px}
.ac-note{font-family:inherit;font-size:12px;height:28px;width:100%;min-width:90px;border:1px solid transparent;border-radius:7px;background:transparent;color:var(--ink);padding:0 7px}
.ac-note:hover{border-color:var(--line)}
.ac-note:focus{border-color:var(--accent);background:var(--bg);outline:none}
.ac-chev{cursor:pointer;color:var(--accent);font-size:11px;margin-left:5px;user-select:none}
.ac-usage{background:var(--bg-sub)}
.ac-ucards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;padding:10px 14px}
.ac-uc{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:12px}
.ac-uc .n{font-weight:600}
.ac-note-hint{font-size:11px;color:var(--ink-3);margin:12px 2px 0;line-height:1.7}
`

function Stars({ score, editable, onRate }) {
  const [hover, setHover] = useState(0)
  const cur = hover || score || 0
  return (
    <span className="ac-stars">
      {[1, 2, 3, 4, 5].map(i => (
        <span key={i} className={'ac-star' + (i <= cur ? ' on' : '') + (editable ? ' ed' : '')}
          onMouseEnter={editable ? () => setHover(i) : undefined}
          onMouseLeave={editable ? () => setHover(0) : undefined}
          onClick={editable ? () => onRate(i) : undefined}
          title={editable ? `打 ${i} 星` : undefined}
          style={{ display: 'inline-block' }}>{i <= cur ? '★' : '☆'}</span>
      ))}
    </span>
  )
}

export default function Acceptance({ user, navDef }) {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('all')
  const [exp, setExp] = useState({})       // module_key -> usage detail | 'loading'
  const [err, setErr] = useState('')

  const load = useCallback(() => { getAcceptance().then(d => { setData(d); setErr('') }).catch(e => setErr(String(e.message || e))) }, [])
  useEffect(() => { load() }, [load])

  const rows = data?.rows || []
  const isAdmin = !!data?.isAdmin
  const nudge = data?.nudge || { toRate: 0, toVerify: 0 }
  const sections = navDef?.sections || []
  const secLabel = {}, secOrder = {}
  sections.forEach((s, i) => { secLabel[s.key] = s.label; secOrder[s.key] = s.order ?? i })

  const doRate = async (mk, score) => { try { await rateTool(mk, score); load() } catch (e) { setErr(String(e.message || e)) } }
  const doVerdict = async (mk, verdict, note) => { try { await setVerdict(mk, verdict, note); load() } catch (e) { setErr(String(e.message || e)) } }
  const toggleUsage = async (mk) => {
    if (exp[mk]) { setExp(p => { const n = { ...p }; delete n[mk]; return n }); return }
    setExp(p => ({ ...p, [mk]: 'loading' }))
    try { const d = await getUsageDetail(mk); setExp(p => ({ ...p, [mk]: d })) }
    catch (e) { setExp(p => ({ ...p, [mk]: { byUser: [] } })) }
  }

  // KPI（只统计"已交付"的行）
  const liveRows = rows.filter(r => r.live)
  const nVerify = liveRows.filter(r => r.verdict !== '通过').length
  const nPass = rows.filter(r => r.verdict === '通过').length
  const nReject = rows.filter(r => r.verdict === '打回').length
  const rated = rows.filter(r => r.count > 0)
  const avgAll = rated.length ? (rated.reduce((s, r) => s + r.avg * r.count, 0) / rated.reduce((s, r) => s + r.count, 0)) : 0
  const nActive = rows.filter(r => r.usageCount > 0).length

  const fmatch = r => filter === 'all'
    || (filter === '待验收' && r.verdict !== '通过' && r.verdict !== '打回')
    || (filter === r.verdict)
  const bySec = {}
  rows.filter(fmatch).forEach(r => { (bySec[r.sec] = bySec[r.sec] || []).push(r) })
  const secKeys = Object.keys(bySec).sort((a, b) => (secOrder[a] ?? 99) - (secOrder[b] ?? 99))
  const nCols = 7

  const dataRow = (r) => {
    const ss = stStyle(r.status)
    const vd = vdStyle(r.verdict)
    return (
      <React.Fragment key={r.key}>
        <tr className="data">
          <td style={{ fontWeight: 600 }}>{r.label}</td>
          <td><span className="ac-pill" style={{ color: ss.c, background: ss.b }}>{r.status || '—'}</span></td>
          <td>
            {isAdmin
              ? <select className="ac-sel" value={r.verdict || '待验收'} onChange={e => doVerdict(r.key, e.target.value, r.note || '')}>
                <option value="待验收">待验收</option><option value="通过">通过</option><option value="打回">打回</option>
              </select>
              : <span className="ac-pill" style={{ color: vd.c, background: vd.b }}>{vd.t}</span>}
          </td>
          <td>{r.count > 0
            ? <span><span style={{ color: 'var(--amber)' }}>★</span> {r.avg} <span className="ac-mut">·{r.count}人</span></span>
            : <span className="ac-mut">暂无</span>}</td>
          <td>{r.canAccess
            ? <Stars score={r.myScore} editable onRate={s => doRate(r.key, s)} />
            : <span className="ac-mut" title="没有这个工具的权限，不能打分">—</span>}</td>
          <td>{r.usageCount == null
            ? <span className="ac-mut">— 前端页</span>
            : <span>{r.usageCount}次 <span className="ac-mut">·{r.usageAccounts}账号</span>
              {isAdmin && r.usageCount > 0 && <span className="ac-chev" onClick={() => toggleUsage(r.key)}>{exp[r.key] ? '收起▲' : '明细▾'}</span>}</span>}</td>
          <td style={{ maxWidth: 220 }}>{isAdmin
            ? <input className="ac-note" defaultValue={r.note || ''} placeholder="意见/待办…"
              onBlur={e => { if ((e.target.value || '') !== (r.note || '')) doVerdict(r.key, r.verdict || '待验收', e.target.value) }}
              onKeyDown={e => { if (e.key === 'Enter') e.target.blur() }} />
            : <span className="ac-mut">{r.note || ''}</span>}</td>
        </tr>
        {isAdmin && exp[r.key] && <tr className="ac-usage"><td colSpan={nCols} style={{ padding: 0 }}>
          {exp[r.key] === 'loading'
            ? <div style={{ padding: '10px 14px' }} className="ac-mut">载入明细…</div>
            : (exp[r.key].byUser || []).length === 0
              ? <div style={{ padding: '10px 14px' }} className="ac-mut">近7天该板块无调用记录</div>
              : <div>
                <div className="ac-mut" style={{ fontSize: 11, padding: '8px 14px 0' }}>近7天「{exp[r.key].board}」板块调用 · 账号 / 最近时间 / 次数 · 仅管理员可见</div>
                <div className="ac-ucards">
                  {exp[r.key].byUser.map((u, i) => <div className="ac-uc" key={i}>
                    <div className="n">{u.user}</div>
                    <div className="ac-mut" style={{ fontSize: 11 }}>{fmtAgo(u.lastTs)} · {u.count}次</div>
                  </div>)}
                </div>
              </div>}
        </td></tr>}
      </React.Fragment>
    )
  }

  return (
    <div>
      <style>{CSS}</style>
      <div className="head"><div><div className="h-title">验收台账</div>
        <div className="h-sub">需求做到哪步 · 验收结论 · 一线满意度 · 近7天调用量 — 验收与打分都进「日志中心 › 操作留痕」</div></div></div>
      <div className="body">
        {err && <div className="ac-nudge" style={{ background: 'var(--red-bg)', borderColor: 'var(--red-line)' }}>取数出错：{err}</div>}

        {/* 站内提醒 */}
        <div className="ac-nudge">
          {(isAdmin && nudge.toVerify > 0) || nudge.toRate > 0
            ? <span>
              {isAdmin && nudge.toVerify > 0 && <>待你验收 <span className="b">{nudge.toVerify}</span> 个　</>}
              {nudge.toRate > 0 && <>你还有 <span className="b">{nudge.toRate}</span> 个常用工具没打分——点右侧星星，10 秒搞定</>}</span>
            : <span className="done">✓ 都处理完了：没有待验收、你常用的工具也都打过分了</span>}
        </div>

        {/* KPI */}
        <div className="ac-kpis">
          <div className="ac-kpi"><div className="v" style={{ color: nVerify ? 'var(--accent)' : undefined }}>{nVerify}</div><div className="k">待验收</div></div>
          <div className="ac-kpi"><div className="v">{nPass}</div><div className="k">已通过</div></div>
          <div className="ac-kpi"><div className="v" style={{ color: nReject ? 'var(--red)' : undefined }}>{nReject}</div><div className="k">打回</div></div>
          <div className="ac-kpi"><div className="v"><span style={{ color: 'var(--amber)', fontSize: 17 }}>★</span> {avgAll ? avgAll.toFixed(1) : '—'}</div><div className="k">平均满意度</div></div>
          <div className="ac-kpi"><div className="v">{nActive}</div><div className="k">近7天活跃工具</div></div>
        </div>

        {/* 筛选 */}
        <div className="ac-seg">
          {[['all', '全部'], ['待验收', '待验收'], ['通过', '已通过'], ['打回', '打回']].map(([v, t]) =>
            <button key={v} className={filter === v ? 'on' : ''} onClick={() => setFilter(v)}>{t}</button>)}
        </div>

        {/* 台账 */}
        <div className="ac-twrap"><div style={{ maxHeight: '62vh', overflow: 'auto' }}>
          <table className="ac-t"><thead><tr>
            <th style={{ width: '20%' }}>需求（模块）</th><th>进度</th><th>验收</th><th>满意度</th><th>我的评分</th><th>近7天调用</th><th style={{ width: '20%' }}>意见 / 待办</th>
          </tr></thead><tbody>
            {!data && <tr><td colSpan={nCols} className="ac-mut" style={{ padding: 28, textAlign: 'center' }}>载入中…</td></tr>}
            {data && secKeys.length === 0 && <tr><td colSpan={nCols} className="ac-mut" style={{ padding: 28, textAlign: 'center' }}>没有符合条件的需求</td></tr>}
            {secKeys.map(sk => <React.Fragment key={sk}>
              <tr className="sec"><td colSpan={nCols}>{secLabel[sk] || sk}</td></tr>
              {bySec[sk].map(dataRow)}
            </React.Fragment>)}
          </tbody></table>
        </div></div>

        <div className="ac-note-hint">
          进度＝各模块实时状态（主管理员在系统设置维护）· 满意度＝一线全员打分取均值（你能进的工具才可打分）·
          近7天调用来自日志中心埋点（{isAdmin ? '点「明细」看账号/时间，' : ''}本次上线起累计；纯前端页无独立接口显「—」）·
          {isAdmin ? '验收结论与意见你可直接改；' : '验收结论由管理员拍板；'}验收与打分都记入操作留痕。
        </div>
      </div>
    </div>
  )
}
