// [Change Log] Date:2026-09-06 Author:Claude/Reginald Zeng Version:V2.489
// 日志中心（放在「系统设置」内，仅主管理员）。两个标签页：
//   ① 运维请求日志——移植自 BP 工作台运维监控：谁在用/在线/并发峰值/慢接口/报错/各板块使用 + 原始日志翻页 + CSV。
//      数据源＝后端 ops.py 埋点（内存实时态 + ops_log.db 历史，独立库，不碰业务库）。
//   ② 业务操作留痕——把已有的 audit_log（登录/改权限/封存/建账号…35 类敏感动作）做成可查页面：
//      谁、何时、对谁、做了什么、备注。财务治理/审计向。
// 本页只读、无副作用；权限闸在后端逐点挂 enter_settings（无权直接 403，前端据 catch 提示）。
import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  getOpsLive, getOpsStats, getOpsLogs, getOpsUserSessions, opsLogsCsvUrl,
  getAudit, auditCsvUrl,
} from '../api.js'

// ── 小工具 ──
const pad = n => String(n).padStart(2, '0')
const fmtTs = (sec) => {           // epoch 秒 → 本地 MM-DD HH:MM:SS
  if (!sec) return '—'
  const d = new Date(sec * 1000)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
const fmtAgo = (sec) => {
  if (!sec) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - sec))
  if (s < 60) return '刚刚'
  if (s < 3600) return Math.floor(s / 60) + ' 分钟前'
  if (s < 86400) return Math.floor(s / 3600) + ' 小时前'
  return Math.floor(s / 86400) + ' 天前'
}
const fmtDur = (sec) => {
  if (!sec && sec !== 0) return ''
  const s = Math.floor(sec)
  if (s < 60) return s + '秒'
  if (s < 3600) return Math.floor(s / 60) + '分'
  return Math.floor(s / 3600) + '时' + Math.floor((s % 3600) / 60) + '分'
}
const msColor = (ms) => ms >= 5000 ? 'var(--red)' : ms >= 1000 ? 'var(--amber)' : 'var(--ink-2)'
const stColor = (st) => (st >= 500 || st === 499) ? 'var(--red)' : st >= 400 ? 'var(--amber)' : 'var(--green)'

const CSS = `
.sl{padding:2px 0 40px}
.sl-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:0 0 16px}
.sl-tab{font-family:inherit;border:0;background:none;cursor:pointer;font-size:13.5px;font-weight:600;color:var(--ink-2);
  padding:9px 16px;border-bottom:2px solid transparent;margin-bottom:-1px}
.sl-tab:hover{color:var(--ink)}
.sl-tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.sl-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin-bottom:14px}
.sl-bar .sp{flex:1}
.sl-seg{display:inline-flex;border:1px solid var(--line-strong);border-radius:8px;overflow:hidden}
.sl-seg button{font-family:inherit;border:0;background:var(--bg);color:var(--ink-2);cursor:pointer;font-size:12px;padding:5px 11px}
.sl-seg button+button{border-left:1px solid var(--line)}
.sl-seg button.on{background:var(--accent-soft);color:var(--accent);font-weight:700}
.sl-lbl{font-size:11.5px;color:var(--ink-3);letter-spacing:.04em}
.sl-live{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-2);cursor:pointer;user-select:none;
  border:1px solid var(--line-strong);border-radius:8px;padding:5px 11px;background:var(--bg)}
.sl-live .d{width:8px;height:8px;border-radius:50%;background:var(--ink-3)}
.sl-live.on{color:var(--green);border-color:var(--green-line);background:var(--green-bg)}
.sl-live.on .d{background:var(--green);animation:slPulse 1.5s ease-in-out infinite}
@keyframes slPulse{0%,100%{opacity:1}50%{opacity:.3}}
@media(prefers-reduced-motion:reduce){.sl-live.on .d{animation:none}}
.sl-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:10px;margin-bottom:14px}
.sl-kpi{border:1px solid var(--line);border-radius:11px;background:var(--bg-sub);padding:11px 13px}
.sl-kpi .v{font-size:21px;font-weight:800;line-height:1.1;color:var(--ink)}
.sl-kpi .k{font-size:11px;color:var(--ink-3);margin-top:3px}
.sl-kpi .sub{font-size:10.5px;color:var(--ink-3);margin-top:2px}
.sl-adv{display:flex;gap:10px;align-items:flex-start;border-radius:11px;padding:12px 14px;margin-bottom:16px;font-size:12.5px;line-height:1.6}
.sl-adv.ok{background:var(--green-bg);border:1px solid var(--green-line);color:var(--ink)}
.sl-adv.warn{background:var(--amber-bg);border:1px solid var(--amber-line);color:var(--ink)}
.sl-adv b{font-weight:700}
.sl-adv .rs{color:var(--ink-2);font-size:11.5px;margin-top:3px}
.sl-h{font-size:13px;font-weight:700;color:var(--ink);margin:18px 0 9px;display:flex;align-items:center;gap:8px}
.sl-h .cnt{font-size:11px;font-weight:600;color:var(--ink-3)}
.sl-h .dl{margin-left:auto;font-size:11.5px;font-weight:600;color:var(--accent);text-decoration:none}
.sl-h .dl:hover{text-decoration:underline}
.sl-twrap{border:1px solid var(--line);border-radius:11px;overflow:hidden;background:var(--bg)}
.sl-scroll{max-height:var(--mh,420px);overflow:auto}
table.sl-t{width:100%;border-collapse:collapse;font-size:12px}
.sl-t th{position:sticky;top:0;z-index:1;background:var(--bg-rail);color:var(--ink-2);font-weight:600;text-align:left;
  padding:8px 11px;white-space:nowrap;border-bottom:1px solid var(--line)}
.sl-t td{padding:7px 11px;border-bottom:1px solid var(--line);color:var(--ink);vertical-align:top}
.sl-t tr:last-child td{border-bottom:0}
.sl-t tbody tr:hover{background:var(--bg-sub)}
.sl-t td.num,.sl-t th.num{text-align:right;font-variant-numeric:tabular-nums}
.sl-t td.mono{font-family:var(--font-mono,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace);font-size:11.5px;color:var(--ink-2)}
.sl-mth{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;background:var(--bg-rail);color:var(--ink-2);border:1px solid var(--line)}
.sl-mth.w{color:var(--accent);background:var(--accent-soft);border-color:var(--accent-soft)}
.sl-pill{display:inline-block;font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}
.sl-user{color:var(--accent);cursor:pointer;font-weight:600}
.sl-user:hover{text-decoration:underline}
.sl-muted{color:var(--ink-3)}
.sl-empty{padding:34px;text-align:center;color:var(--ink-3);font-size:12.5px}
.sl-boards{display:flex;flex-wrap:wrap;gap:5px}
.sl-bd{font-size:10.5px;color:var(--ink-2);background:var(--bg-rail);border:1px solid var(--line);border-radius:6px;padding:1px 7px}
.sl-inp{font-family:inherit;height:30px;border:1px solid var(--line-strong);border-radius:8px;background:var(--bg);color:var(--ink);
  padding:0 9px;font-size:12px;min-width:120px}
.sl-ck{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-2);cursor:pointer}
.sl-note{font-size:11.5px;color:var(--ink-3);margin:10px 2px 0;line-height:1.7}
.sl-hours{display:flex;align-items:flex-end;justify-content:flex-start;gap:2px;height:52px;padding:8px 4px 0;overflow-x:auto}
.sl-hr{flex:0 0 11px;background:var(--accent-soft);border-radius:2px 2px 0 0;position:relative}
.sl-hr.hot{background:var(--amber)}
.sl-hr.err{background:var(--red)}
.sl-sess{background:var(--bg-sub);border-top:1px solid var(--line)}
.sl-sess .row{display:flex;flex-wrap:wrap;gap:8px 14px;padding:8px 14px;font-size:11.5px;color:var(--ink-2);border-bottom:1px dashed var(--line)}
.sl-sess .row:last-child{border-bottom:0}
`

// ═════════════ 通用小组件 ═════════════
function Kpi({ v, k, sub, color }) {
  return <div className="sl-kpi"><div className="v" style={color ? { color } : undefined}>{v}</div>
    <div className="k">{k}</div>{sub != null && <div className="sub">{sub}</div>}</div>
}
function Seg({ value, onChange, opts }) {
  return <span className="sl-seg">{opts.map(o =>
    <button key={o.v} className={value === o.v ? 'on' : ''} onClick={() => onChange(o.v)}>{o.t}</button>)}</span>
}
function Method({ m }) {
  const w = m && m !== 'GET' && m !== 'HEAD'
  return <span className={'sl-mth' + (w ? ' w' : '')}>{m}</span>
}

// ═════════════ Tab 1：运维请求日志 ═════════════
function OpsPanel() {
  const [days, setDays] = useState(7)
  const [live, setLive] = useState(null)
  const [stats, setStats] = useState(null)
  const [logs, setLogs] = useState(null)
  const [err, setErr] = useState('')
  const [autoLive, setAutoLive] = useState(true)
  const [openUser, setOpenUser] = useState(null)     // 展开轨迹的用户
  const [sessions, setSessions] = useState(null)
  // 原始日志筛选
  const [fUser, setFUser] = useState('')
  const [fBoard, setFBoard] = useState('')
  const [fErr, setFErr] = useState(false)
  const timer = useRef(null)

  const pullLive = useCallback(() => { getOpsLive().then(setLive).catch(e => setErr(String(e.message || e))) }, [])
  const pullStats = useCallback(() => { getOpsStats(days).then(setStats).catch(e => setErr(String(e.message || e))) }, [days])
  const pullLogs = useCallback(() => {
    getOpsLogs({ days, limit: 300, user: fUser, board: fBoard, onlyErrors: fErr })
      .then(r => setLogs(r.rows || [])).catch(e => setErr(String(e.message || e)))
  }, [days, fUser, fBoard, fErr])

  useEffect(() => { pullLive(); pullStats() }, [pullStats])   // days 变→重拉聚合；首挂也拉一次实时
  useEffect(() => { pullLogs() }, [pullLogs])
  // 实时轮询
  useEffect(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null }
    if (autoLive) { timer.current = setInterval(pullLive, 4000) }
    return () => { if (timer.current) clearInterval(timer.current) }
  }, [autoLive, pullLive])

  const toggleUser = (u) => {
    if (openUser === u) { setOpenUser(null); setSessions(null); return }
    setOpenUser(u); setSessions(null)
    getOpsUserSessions(u, Math.max(days, 1)).then(setSessions).catch(() => setSessions({ sessions: [] }))
  }

  const s = stats?.summary
  const cap = stats?.capacity
  const online = live?.online
  const maxHour = Math.max(1, ...(stats?.byHour || []).map(h => h.count))

  return (
    <div>
      {err && <div className="sl-adv warn" style={{ marginBottom: 12 }}>取数出错：{err}（若提示无权限，本页仅主管理员可看）</div>}

      <div className="sl-bar">
        <span className="sl-lbl">统计窗口</span>
        <Seg value={days} onChange={setDays} opts={[{ v: 1, t: '今天' }, { v: 7, t: '7 天' }, { v: 30, t: '30 天' }]} />
        <span className="sp" />
        <span className={'sl-live' + (autoLive ? ' on' : '')} onClick={() => setAutoLive(v => !v)}
          title="每 4 秒刷新实时探针">
          <span className="d" />{autoLive ? '实时刷新中' : '实时已暂停'}
        </span>
      </div>

      {/* 概览 KPI */}
      {s && <div className="sl-cards">
        <Kpi v={s.total} k={`总请求 · ${days === 1 ? '今天' : days + '天'}`} />
        <Kpi v={online ? online.count : (s.users || 0)} k={online ? '当前在线（真人）' : '活跃用户'}
          sub={online ? `窗口 ${online.windowMin} 分钟` : `${days}天去重`} color="var(--accent)" />
        <Kpi v={s.avgMs + 'ms'} k="平均耗时" sub={'P95 ' + s.p95Ms + 'ms'} />
        <Kpi v={s.peakConcurrency} k="峰值并发" sub={cap ? cap.workers + ' 个进程' : ''} />
        <Kpi v={s.slow1s} k="慢请求 >1s" sub={s.slow5s ? s.slow5s + ' 次 >5s' : '无 >5s'}
          color={s.slow5s ? 'var(--amber)' : undefined} />
        <Kpi v={s.errors} k="错误（真故障）" color={s.errors ? 'var(--red)' : undefined} />
        <Kpi v={s.denied} k="权限拒绝" sub="401/403" color={s.denied ? 'var(--amber)' : undefined} />
      </div>}

      {/* 容量建议 */}
      {cap && <div className={'sl-adv ' + (cap.level === 'ok' ? 'ok' : 'warn')}>
        <div>
          <b>容量研判：{cap.advice}</b>
          {(cap.reasons || []).map((r, i) => <div className="rs" key={i}>· {r}</div>)}
        </div>
      </div>}

      {/* 每小时请求量（并发热点）迷你条 */}
      {stats?.byHour?.length > 0 && <>
        <div className="sl-h">每小时请求量<span className="cnt">（红=有故障，橙=并发≥2）</span></div>
        <div className="sl-twrap" style={{ padding: '0 10px' }}>
          <div className="sl-hours" title="按小时的请求量">
            {stats.byHour.map((h, i) => {
              const cls = h.errors ? 'err' : h.peak >= 2 ? 'hot' : ''
              return <div key={i} className={'sl-hr ' + cls} style={{ height: Math.max(3, 100 * h.count / maxHour) + '%' }}
                title={`${h.day} ${pad(h.hour)}:00　请求 ${h.count}　并发峰值 ${h.peak}　用户 ${h.users}　故障 ${h.errors}　拒绝 ${h.denied}`} />
            })}
          </div>
        </div>
      </>}

      {/* 当前在线 */}
      <div className="sl-h">当前在线
        <span className="cnt">{online ? `真人 ${online.count}${online.system ? ` · 系统自检 ${online.system}` : ''}${online.anon ? ` · 匿名 ${online.anon}` : ''}` : '（探针不可用）'}</span>
      </div>
      <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '240px' }}>
        <table className="sl-t"><thead><tr>
          <th>用户</th><th className="num">最近动作</th><th>正在看</th><th className="num">请求</th><th className="num">写操作</th><th className="num">板块数</th>
        </tr></thead><tbody>
          {(online?.users || []).map((u, i) => <tr key={i}>
            <td><b>{u.user}</b></td>
            <td className="num sl-muted">{fmtAgo(u.lastTs)}</td>
            <td className="sl-muted">{u.lastBoard || '—'}</td>
            <td className="num">{u.count}</td>
            <td className="num">{u.writes || 0}</td>
            <td className="num">{u.boards}</td>
          </tr>)}
          {(!online?.users || online.users.length === 0) && <tr><td colSpan="6" className="sl-empty">最近 5 分钟内没有活动</td></tr>}
        </tbody></table>
      </div></div>

      {/* 各板块使用量 */}
      {stats?.byBoard?.length > 0 && <>
        <div className="sl-h">各板块使用量</div>
        <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '300px' }}>
          <table className="sl-t"><thead><tr>
            <th>板块</th><th className="num">请求</th><th className="num">用户</th><th className="num">平均耗时</th><th className="num">最慢</th><th className="num">故障</th><th className="num">拒绝</th>
          </tr></thead><tbody>
            {stats.byBoard.map((b, i) => <tr key={i}>
              <td><span className="sl-pill">{b.board}</span></td>
              <td className="num">{b.count}</td><td className="num">{b.users}</td>
              <td className="num" style={{ color: msColor(b.avgMs) }}>{b.avgMs}ms</td>
              <td className="num" style={{ color: msColor(b.maxMs) }}>{b.maxMs}ms</td>
              <td className="num" style={{ color: b.errors ? 'var(--red)' : undefined }}>{b.errors || ''}</td>
              <td className="num" style={{ color: b.denied ? 'var(--amber)' : undefined }}>{b.denied || ''}</td>
            </tr>)}
          </tbody></table>
        </div></div>
      </>}

      {/* 谁在用哪块（可点开轨迹） */}
      {stats?.byUser?.length > 0 && <>
        <div className="sl-h">谁在用哪块<span className="cnt">点用户名看操作轨迹</span></div>
        <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '340px' }}>
          <table className="sl-t"><thead><tr>
            <th>用户</th><th className="num">请求</th><th className="num">写操作</th><th className="num">活跃天</th><th className="num">最近</th><th>板块</th>
          </tr></thead><tbody>
            {stats.byUser.map((u, i) => <React.Fragment key={i}>
              <tr>
                <td><span className="sl-user" onClick={() => toggleUser(u.user)}>{openUser === u.user ? '▾ ' : '▸ '}{u.user}</span></td>
                <td className="num">{u.count}</td><td className="num">{u.writes}</td>
                <td className="num">{u.days}</td><td className="num sl-muted">{fmtAgo(u.lastTs)}</td>
                <td><div className="sl-boards">{u.boards.slice(0, 6).map((b, k) => <span className="sl-bd" key={k}>{b.board} {b.count}</span>)}</div></td>
              </tr>
              {openUser === u.user && <tr><td colSpan="6" style={{ padding: 0 }}>
                <div className="sl-sess">
                  {!sessions && <div className="row">载入轨迹中…</div>}
                  {sessions && sessions.sessions?.length === 0 && <div className="row">近 {Math.max(days, 1)} 天无记录</div>}
                  {sessions && (sessions.sessions || []).map((se, k) => <div className="row" key={k}>
                    <span><b>{fmtTs(se.start)}</b> 起 · 历时 {fmtDur(se.end - se.start)}</span>
                    <span>{se.count} 次请求</span>
                    {se.writes > 0 && <span style={{ color: 'var(--accent)' }}>写 {se.writes}</span>}
                    {se.errors > 0 && <span style={{ color: 'var(--red)' }}>故障 {se.errors}</span>}
                    <span className="sl-boards">{(se.boards || []).map((b, j) => <span className="sl-bd" key={j}>{b.board} {b.count}</span>)}</span>
                  </div>)}
                </div>
              </td></tr>}
            </React.Fragment>)}
          </tbody></table>
        </div></div>
      </>}

      {/* 慢接口榜 */}
      {stats?.slow?.length > 0 && <>
        <div className="sl-h">慢接口榜<span className="cnt">按总占用时间排（吃进程的是它）</span></div>
        <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '280px' }}>
          <table className="sl-t"><thead><tr>
            <th>接口</th><th className="num">次数</th><th className="num">平均</th><th className="num">最慢</th><th className="num">总占用</th>
          </tr></thead><tbody>
            {stats.slow.map((r, i) => <tr key={i}>
              <td><Method m={r.method} /> <span className="mono">{r.path}</span></td>
              <td className="num">{r.count}</td>
              <td className="num" style={{ color: msColor(r.avgMs) }}>{r.avgMs}ms</td>
              <td className="num" style={{ color: msColor(r.maxMs) }}>{r.maxMs}ms</td>
              <td className="num">{(r.totalMs / 1000).toFixed(1)}s</td>
            </tr>)}
          </tbody></table>
        </div></div>
      </>}

      {/* 错误 + 权限拒绝 */}
      {stats && (stats.errors?.length > 0 || stats.denied?.length > 0) && <>
        <div className="sl-h">错误与权限拒绝
          <span className="cnt">故障 {s?.errors || 0} · 拒绝 {s?.denied || 0}（各截断 100 条）</span>
        </div>
        <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '300px' }}>
          <table className="sl-t"><thead><tr>
            <th>时间</th><th>用户</th><th>接口</th><th className="num">状态</th><th className="num">耗时</th><th>错误摘要</th>
          </tr></thead><tbody>
            {[...(stats.errors || []).map(e => ({ ...e, _k: 'e' })), ...(stats.denied || []).map(e => ({ ...e, _k: 'd' }))]
              .sort((a, b) => b.ts - a.ts).slice(0, 120).map((e, i) => <tr key={i}>
                <td className="sl-muted">{fmtTs(e.ts)}</td>
                <td>{e.user || <span className="sl-muted">—</span>}</td>
                <td><Method m={e.method} /> <span className="mono">{e.path}</span></td>
                <td className="num" style={{ color: stColor(e.status), fontWeight: 700 }}>{e.status}</td>
                <td className="num" style={{ color: msColor(e.ms) }}>{e.ms}ms</td>
                <td className="sl-muted">{e.err || (e._k === 'd' ? '权限拒绝' : '')}</td>
              </tr>)}
          </tbody></table>
        </div></div>
      </>}

      {/* 原始日志 */}
      <div className="sl-h">原始请求日志
        <span className="cnt">{logs ? logs.length + ' 条' : ''}</span>
        <a className="dl" href={opsLogsCsvUrl(days)}>下载 CSV ↓</a>
      </div>
      <div className="sl-bar" style={{ marginBottom: 10 }}>
        <input className="sl-inp" placeholder="按用户名过滤" value={fUser} onChange={e => setFUser(e.target.value)} />
        <input className="sl-inp" placeholder="按板块过滤（如 银行对账）" value={fBoard} onChange={e => setFBoard(e.target.value)} />
        <label className="sl-ck"><input type="checkbox" checked={fErr} onChange={e => setFErr(e.target.checked)} /> 只看出错</label>
      </div>
      <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '460px' }}>
        <table className="sl-t"><thead><tr>
          <th>时间</th><th>用户</th><th>方法</th><th>路径</th><th>板块</th><th className="num">状态</th><th className="num">耗时</th><th className="num">在飞</th>
        </tr></thead><tbody>
          {(logs || []).map((r, i) => <tr key={i}>
            <td className="sl-muted" style={{ whiteSpace: 'nowrap' }}>{fmtTs(r.ts)}</td>
            <td>{r.user || <span className="sl-muted">—</span>}</td>
            <td><Method m={r.method} /></td>
            <td className="mono">{r.path}</td>
            <td className="sl-muted">{r.board}</td>
            <td className="num" style={{ color: stColor(r.status), fontWeight: 700 }}>{r.status}</td>
            <td className="num" style={{ color: msColor(r.ms) }}>{r.ms}ms</td>
            <td className="num sl-muted">{r.inflight}</td>
          </tr>)}
          {logs && logs.length === 0 && <tr><td colSpan="8" className="sl-empty">该条件下没有日志</td></tr>}
        </tbody></table>
      </div></div>

      <div className="sl-note">
        口径：耗时＝服务端处理时间（不含网络）；峰值并发＝同一刻在处理的请求数（前端一次开页会并行扇出多个请求，
        故此数≈请求扇出宽度，<b>不等于同时在线人数</b>）；「权限拒绝(401/403)」是设计行为、不计入故障。
        日志独立库 ops_log.db、保留 {stats?.retainDays || live?.retainDays || 30} 天，不写业务库；实时探针只反映当前进程。
      </div>
    </div>
  )
}

// ═════════════ Tab 2：业务操作留痕 ═════════════
function AuditPanel() {
  const [days, setDays] = useState(30)
  const [operator, setOperator] = useState('')
  const [action, setAction] = useState('')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  const pull = useCallback(() => {
    getAudit({ days, limit: 500, operator, action })
      .then(setData).catch(e => setErr(String(e.message || e)))
  }, [days, operator, action])
  useEffect(() => { pull() }, [pull])

  const rows = data?.rows || []
  const meta = data?.meta || {}

  return (
    <div>
      {err && <div className="sl-adv warn" style={{ marginBottom: 12 }}>取数出错：{err}（本页仅主管理员可看）</div>}
      <div className="sl-bar">
        <span className="sl-lbl">时间窗口</span>
        <Seg value={days} onChange={setDays}
          opts={[{ v: 7, t: '7 天' }, { v: 30, t: '30 天' }, { v: 90, t: '90 天' }, { v: 365, t: '一年' }]} />
        <select className="sl-inp" value={operator} onChange={e => setOperator(e.target.value)}>
          <option value="">全部操作人</option>
          {(meta.operators || []).map(o => <option key={o} value={o}>{o}</option>)}
        </select>
        <select className="sl-inp" value={action} onChange={e => setAction(e.target.value)}>
          <option value="">全部动作</option>
          {(meta.actions || []).map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <span className="sp" />
        <a className="dl" style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)', textDecoration: 'none' }}
          href={auditCsvUrl(days)}>下载 CSV ↓</a>
      </div>

      <div className="sl-h">操作留痕<span className="cnt">{rows.length} 条{data && rows.length >= 500 ? '（已截断，请缩小范围或下载 CSV）' : ''}</span></div>
      <div className="sl-twrap"><div className="sl-scroll" style={{ '--mh': '560px' }}>
        <table className="sl-t"><thead><tr>
          <th style={{ whiteSpace: 'nowrap' }}>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>备注</th>
        </tr></thead><tbody>
          {rows.map((r, i) => <tr key={i}>
            <td className="sl-muted" style={{ whiteSpace: 'nowrap' }}>{r.ts}</td>
            <td><b>{r.operator || '—'}</b></td>
            <td><span className="sl-pill">{r.action}</span></td>
            <td>{r.target || <span className="sl-muted">—</span>}</td>
            <td className="sl-muted" style={{ maxWidth: 420, wordBreak: 'break-all' }}>{r.detail || ''}</td>
          </tr>)}
          {data && rows.length === 0 && <tr><td colSpan="5" className="sl-empty">该条件下没有留痕记录</td></tr>}
        </tbody></table>
      </div></div>
      <div className="sl-note">
        留痕记录的是<b>敏感业务动作</b>（登录 / 改密 / 建账号 / 改权限 / 封存解封 / 改账认领 …），
        密码等机密值从不写入。它与「运维请求日志」互补：一个看合规「谁改了什么」，一个看健康「谁在用、卡不卡」。
      </div>
    </div>
  )
}

// ═════════════ 外壳 ═════════════
export default function SysLog() {
  const [tab, setTab] = useState('ops')
  return (
    <div className="sl">
      <style>{CSS}</style>
      <div className="sl-tabs">
        <button className={'sl-tab' + (tab === 'ops' ? ' on' : '')} onClick={() => setTab('ops')}>运维请求日志</button>
        <button className={'sl-tab' + (tab === 'audit' ? ' on' : '')} onClick={() => setTab('audit')}>业务操作留痕</button>
      </div>
      {tab === 'ops' ? <OpsPanel /> : <AuditPanel />}
    </div>
  )
}
