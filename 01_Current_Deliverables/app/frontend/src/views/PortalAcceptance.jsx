// [Change Log] Date:2026-09-06 Author:Claude/Reginald Zeng Version:V2.499
// 门户「验收台账」——你的跨台驾驶舱。一张表把核算/BP/法务所有工具列一起：进度 / 验收(发起+结论) / 一线满意度 /
//   近7天调用。管理员可对某工具「发起验收」（指派给某账号，附要看的点）→ 对方登录弹窗验收；可收口（关闭/打回开发中/隐藏）。
//   一线只打星。近7天调用：核算卡走埋点，BP/法务暂显「—」（待接 BP）。渲染在 Portal(.pt-root) 内，沿用门户深色变量。
import React, { useState, useEffect, useCallback } from 'react'
import { getPortalAcceptance, assignVerify, escalateVerify, ratePortalTool } from '../api.js'

const ST = { ok: { t: '已上线', c: 'var(--green,#34D399)' }, par: { t: '人工并行', c: 'var(--amber,#FBBF24)' },
  beta: { t: '开发中', c: '#6FA8FF' }, soon: { t: '敬请期待', c: 'var(--ink3,#8B84AD)' } }
const LANE_ORDER = ['accounting', 'bp', 'legal']

const CSS = `
.pa{color:var(--ink,#EDEAF6);font-size:13px}
.pa-bar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:14px}
.pa-seg{display:inline-flex;border:1px solid var(--line2,rgba(255,255,255,.14));border-radius:9px;overflow:hidden}
.pa-seg button{font-family:inherit;border:0;background:transparent;color:var(--ink3,#8B84AD);cursor:pointer;font-size:12px;padding:6px 12px}
.pa-seg button+button{border-left:1px solid var(--line,rgba(255,255,255,.08))}
.pa-seg button.on{background:rgba(124,92,255,.2);color:#CFC4FF;font-weight:700}
.pa-hint{font-size:11.5px;color:var(--ink3,#8B84AD);margin-left:auto}
.pa-lane{font-size:12px;font-weight:800;letter-spacing:.5px;color:var(--ink2,#B4ABD4);margin:16px 2px 8px;display:flex;align-items:center;gap:8px}
.pa-lane i{width:3px;height:14px;border-radius:2px;background:var(--brand,#7C5CFF);display:inline-block}
.pa-wrap{border:1px solid var(--line,rgba(255,255,255,.08));border-radius:12px;overflow:hidden;background:rgba(255,255,255,.02)}
table.pa-t{width:100%;border-collapse:collapse;font-size:12.5px}
.pa-t th{text-align:left;padding:9px 12px;color:var(--ink3,#8B84AD);font-weight:600;border-bottom:1px solid var(--line,rgba(255,255,255,.08));white-space:nowrap}
.pa-t td{padding:10px 12px;border-bottom:1px solid var(--line,rgba(255,255,255,.06));vertical-align:middle}
.pa-t tr:last-child td{border-bottom:0}
.pa-t tbody tr:hover{background:rgba(255,255,255,.02)}
.pa-nm{font-weight:700;color:var(--ink,#EDEAF6)}
.pa-desc{font-size:11px;color:var(--ink3,#8B84AD);margin-top:2px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pa-dot{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink2,#B4ABD4)}
.pa-dot i{width:7px;height:7px;border-radius:50%}
.pa-badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 9px;border-radius:999px;white-space:nowrap}
.pa-b-pend{color:#FBE8B0;background:rgba(251,191,36,.16)}
.pa-b-pass{color:#B7F5D8;background:rgba(52,211,153,.16)}
.pa-b-rej{color:#F7CBA0;background:rgba(232,131,74,.18)}
.pa-b-none{color:var(--ink3,#8B84AD);background:rgba(255,255,255,.05)}
.pa-sub{font-size:10.5px;color:var(--ink3,#8B84AD);margin-top:3px}
.pa-imp{font-size:10.5px;color:#FBE8B0;margin-top:3px;max-width:220px}
.pa-stars{white-space:nowrap;font-size:15px;letter-spacing:1px;line-height:1}
.pa-star{color:rgba(255,255,255,.18)}
.pa-star.on{color:var(--amber,#FBBF24)}
.pa-star.ed{cursor:pointer}
.pa-star.ed:hover{transform:scale(1.15)}
.pa-mut{color:var(--ink3,#8B84AD)}
.pa-btn{font-family:inherit;cursor:pointer;font-size:11.5px;font-weight:600;border-radius:8px;padding:5px 11px;white-space:nowrap;
  color:#D9D2F5;border:1px solid rgba(124,92,255,.45);background:rgba(124,92,255,.12)}
.pa-btn:hover{background:rgba(124,92,255,.22)}
.pa-mini{font-family:inherit;cursor:pointer;font-size:11px;color:var(--ink3,#8B84AD);border:0;background:none;padding:2px 5px}
.pa-mini:hover{color:var(--ink,#EDEAF6)}
.pa-mask{position:fixed;inset:0;z-index:110;background:rgba(10,8,20,.7);display:flex;align-items:center;justify-content:center;padding:20px}
.pa-modal{width:min(460px,94vw);background:var(--panel,#221A3A);border:1px solid var(--line2,rgba(255,255,255,.14));border-radius:16px;overflow:hidden}
.pa-mhd{padding:14px 18px;border-bottom:1px solid var(--line,rgba(255,255,255,.08));font-size:14px;font-weight:800;color:var(--ink,#EDEAF6)}
.pa-mbd{padding:16px 18px}
.pa-mlbl{font-size:12px;color:var(--ink3,#8B84AD);margin:0 0 6px}
.pa-sel,.pa-inp{width:100%;box-sizing:border-box;font-family:inherit;font-size:13px;border:1px solid var(--line2,rgba(255,255,255,.14));border-radius:9px;
  background:rgba(255,255,255,.04);color:var(--ink,#EDEAF6);padding:9px 11px}
.pa-sel:focus,.pa-inp:focus{outline:none;border-color:var(--brand,#7C5CFF)}
.pa-inp{min-height:60px;resize:vertical;line-height:1.6}
.pa-mft{display:flex;gap:10px;padding:14px 18px;border-top:1px solid var(--line,rgba(255,255,255,.08))}
.pa-cancel{font-family:inherit;cursor:pointer;font-size:13px;color:var(--ink3,#8B84AD);background:none;border:0}
.pa-ok{margin-left:auto;font-family:inherit;cursor:pointer;font-size:13px;font-weight:700;color:#fff;padding:8px 18px;border-radius:9px;border:0;background:linear-gradient(180deg,#8B6BFF,#6A4CE6)}
.pa-ok:disabled{opacity:.5}
.pa-menu{position:relative;display:inline-block}
.pa-pop{position:absolute;right:0;top:calc(100% + 4px);z-index:20;background:var(--panel,#221A3A);border:1px solid var(--line2,rgba(255,255,255,.14));border-radius:10px;padding:5px;min-width:130px;box-shadow:0 14px 40px rgba(0,0,0,.4)}
.pa-pop button{display:block;width:100%;text-align:left;font-family:inherit;font-size:12px;color:var(--ink2,#B4ABD4);background:none;border:0;cursor:pointer;padding:7px 10px;border-radius:7px}
.pa-pop button:hover{background:rgba(255,255,255,.06);color:var(--ink,#EDEAF6)}
.pa-err{color:#F87171;font-size:12px}
`

function Stars({ score, onRate }) {
  const [h, setH] = useState(0)
  const cur = h || score || 0
  return <span className="pa-stars">{[1, 2, 3, 4, 5].map(i =>
    <span key={i} className={'pa-star ed' + (i <= cur ? ' on' : '')}
      onMouseEnter={() => setH(i)} onMouseLeave={() => setH(0)} onClick={() => onRate(i)}
      style={{ display: 'inline-block' }}>{i <= cur ? '★' : '☆'}</span>)}</span>
}

export default function PortalAcceptance({ user, onChanged }) {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('all')
  const [assignFor, setAssignFor] = useState(null)   // 发起验收的目标工具行
  const [assignee, setAssignee] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [menu, setMenu] = useState(null)             // 打开收口菜单的工具id

  const load = useCallback(() => {
    getPortalAcceptance().then(d => { setData(d); setErr('') }).catch(e => setErr(String(e.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  const rows = (data?.rows || [])
  const isAdmin = !!data?.isAdmin
  const laneLabel = data?.laneLabel || {}
  const accounts = data?.accounts || []

  const doRate = async (id, s) => { try { await ratePortalTool(id, s); load(); onChanged && onChanged() } catch (e) { setErr(String(e.message || e)) } }
  const doAssign = async () => {
    if (!assignee) { setErr('请选验收人'); return }
    setBusy(true); setErr('')
    try { await assignVerify({ toolId: assignFor.id, assignee, note }); setAssignFor(null); setAssignee(''); setNote(''); load(); onChanged && onChanged() }
    catch (e) { setErr(String(e.message || e)) } finally { setBusy(false) }
  }
  const doEscalate = async (id, action) => {
    setMenu(null)
    try { await escalateVerify({ toolId: id, action }); load(); onChanged && onChanged() } catch (e) { setErr(String(e.message || e)) }
  }
  const openAssign = (r) => { setAssignFor(r); setAssignee(''); setNote(''); setErr('') }

  // 筛选（隐藏的默认不显，除非选"已隐藏"）
  const taskState = r => (r.task && r.task.status) || 'none'
  const fmatch = r => {
    const s = taskState(r)
    if (s === 'hidden') return filter === 'hidden'
    if (filter === 'all') return true
    if (filter === 'pending') return s === 'pending'
    if (filter === 'pass') return s === 'pass'
    if (filter === 'reject') return s === 'reject'
    if (filter === 'none') return s === 'none' || s === 'closed'
    return false
  }
  const shown = rows.filter(fmatch)
  const byLane = {}
  shown.forEach(r => { (byLane[r.lane] = byLane[r.lane] || []).push(r) })
  const lanes = LANE_ORDER.filter(l => byLane[l]).concat(Object.keys(byLane).filter(l => !LANE_ORDER.includes(l)))

  const verdictBadge = (r) => {
    const s = taskState(r)
    if (s === 'pending') return <span><span className="pa-badge pa-b-pend">⏳ 待验收</span><div className="pa-sub">指派给 {r.task.assignee}</div></span>
    if (s === 'pass') return <span><span className="pa-badge pa-b-pass">✓ 已通过</span><div className="pa-sub">{r.task.doneBy} 验</div>{r.task.improve && <div className="pa-imp">完善点：{r.task.improve}</div>}</span>
    if (s === 'reject') return <span><span className="pa-badge pa-b-rej">◔ 暂不验收</span><div className="pa-sub">{r.task.doneBy} 看过</div>{r.task.improve && <div className="pa-imp">待完善：{r.task.improve}</div>}</span>
    return <span className="pa-badge pa-b-none">未发起</span>
  }

  return (
    <div className="pa">
      <style>{CSS}</style>
      {err && <div className="pa-err" style={{ marginBottom: 10 }}>出错：{err}</div>}

      <div className="pa-bar">
        <span className="pa-seg">
          {[['all', '全部'], ['pending', '待验收'], ['pass', '已通过'], ['reject', '暂不验收'], ['none', '未发起'], ['hidden', '已隐藏']].map(([v, t]) =>
            <button key={v} className={filter === v ? 'on' : ''} onClick={() => setFilter(v)}>{t}</button>)}
        </span>
        <span className="pa-hint">{isAdmin ? '你可对任一工具「发起验收」指派给某人；对方登录会弹窗' : '你能给用过的工具打星；被指派的验收登录会弹窗'}</span>
      </div>

      {!data && <div className="pa-mut" style={{ padding: 24 }}>载入中…</div>}
      {data && lanes.length === 0 && <div className="pa-mut" style={{ padding: 24 }}>没有符合条件的工具</div>}

      {lanes.map(lane => (
        <div key={lane}>
          <div className="pa-lane"><i />{laneLabel[lane] || lane}</div>
          <div className="pa-wrap"><table className="pa-t"><thead><tr>
            <th style={{ width: '26%' }}>工具</th><th>进度</th><th>验收</th><th>满意度</th><th>我的评分</th><th>近7天调用</th>{isAdmin && <th style={{ textAlign: 'right' }}>操作</th>}
          </tr></thead><tbody>
            {byLane[lane].map(r => {
              const st = ST[r.status] || { t: r.statusLabel || '—', c: 'var(--ink3,#8B84AD)' }
              const s = taskState(r)
              return (
                <tr key={r.id}>
                  <td><div className="pa-nm">{r.name}</div>{r.descr && <div className="pa-desc">{r.descr}</div>}</td>
                  <td><span className="pa-dot"><i style={{ background: st.c }} />{st.t}</span></td>
                  <td>{verdictBadge(r)}</td>
                  <td>{r.count > 0 ? <span><span style={{ color: 'var(--amber,#FBBF24)' }}>★</span> {r.avg} <span className="pa-mut">·{r.count}人</span></span> : <span className="pa-mut">暂无</span>}</td>
                  <td><Stars score={r.myScore} onRate={sc => doRate(r.id, sc)} /></td>
                  <td>{r.usageCount == null ? <span className="pa-mut">—</span> : <span>{r.usageCount}次 <span className="pa-mut">·{r.usageAccounts}账号</span></span>}</td>
                  {isAdmin && <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button className="pa-btn" onClick={() => openAssign(r)}>{s === 'pending' ? '改派' : '发起验收'}</button>
                    {s !== 'none' && s !== 'hidden' && <span className="pa-menu">
                      <button className="pa-mini" onClick={() => setMenu(menu === r.id ? null : r.id)}>收口▾</button>
                      {menu === r.id && <div className="pa-pop">
                        <button onClick={() => doEscalate(r.id, 'reopen')}>打回开发中</button>
                        <button onClick={() => doEscalate(r.id, 'close')}>关闭验收</button>
                        <button onClick={() => doEscalate(r.id, 'hide')}>隐藏</button>
                      </div>}
                    </span>}
                  </td>}
                </tr>
              )
            })}
          </tbody></table></div>
        </div>
      ))}

      {assignFor && (
        <div className="pa-mask" onClick={e => { if (e.target === e.currentTarget) setAssignFor(null) }}>
          <div className="pa-modal">
            <div className="pa-mhd">发起验收 · {assignFor.name}</div>
            <div className="pa-mbd">
              <p className="pa-mlbl">指派给谁验收</p>
              <select className="pa-sel" value={assignee} onChange={e => setAssignee(e.target.value)}>
                <option value="">— 选一个账号 —</option>
                {accounts.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
              <p className="pa-mlbl" style={{ marginTop: 14 }}>要看的点（会显示在对方的验收弹窗里）</p>
              <textarea className="pa-inp" value={note} onChange={e => setNote(e.target.value)}
                placeholder="例：本次新增自动写金蝶，请重点核对汇率有没有写错" />
              {err && <div className="pa-err" style={{ marginTop: 8 }}>{err}</div>}
            </div>
            <div className="pa-mft">
              <button className="pa-cancel" onClick={() => setAssignFor(null)}>取消</button>
              <button className="pa-ok" onClick={doAssign} disabled={busy}>{busy ? '发起中…' : '发起验收'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
