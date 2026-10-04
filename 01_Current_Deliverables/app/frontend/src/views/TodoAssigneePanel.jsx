// [Change Log] Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.790（首页待办区）
// Description: 系统设置 → 常规设置「待办处理人」（《首页待办区 需求确认书 v1.0》D4 / 第六节）：
//   每个环节一行，选一个或多个工作台账号当处理人——首页「待我处理」就按这里挂。任何一人在金蝶审了，所有人名下同时销。
//   没配的环节标出来：待办会先挂到主管理员名下（不是不生成）。换了人，没办完的自动跟到新人名下，不用一条条转。
//   选的是工作台账号（不是钉钉通讯录）：待办要认登录的人。改动由后端留痕（谁、什么时候、从谁改成谁）。
import React, { useEffect, useState } from 'react'
import { getTodoConfig, saveTodoConfig } from '../api.js'

export default function TodoAssigneePanel() {
  const [cfg, setCfg] = useState(undefined)        // undefined=加载中 / null=读取失败
  const [err, setErr] = useState('')
  const [pick, setPick] = useState({})              // {环节 key: [账号名]}
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => getTodoConfig()
    .then(d => { setCfg(d); setPick(Object.fromEntries((d.scenes || []).map(s => [s.key, s.names || []]))); setDirty(false); setErr('') })
    .catch(e => { setCfg(null); setErr(e.message) })
  useEffect(() => { load() }, [])
  const add = (k, name) => { if (!name) return; setPick(p => ({ ...p, [k]: [...(p[k] || []), name] })); setDirty(true); setMsg('') }
  const del = (k, name) => { setPick(p => ({ ...p, [k]: (p[k] || []).filter(x => x !== name) })); setDirty(true); setMsg('') }
  const save = async () => {
    setBusy(true)
    try {
      const r = await saveTodoConfig(pick)
      setMsg(r.changed ? `已保存：改了 ${r.changed} 个环节，没办完的待办已跟到新处理人名下` : '没有改动')
      load()
    } catch (e) { setMsg('保存失败：' + e.message) } finally { setBusy(false) }
  }
  const users = cfg?.users || []
  const supers = users.filter(u => u.admin).map(u => u.name)

  return (
    <div className="cat" style={{ maxWidth: 860 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>待办处理人</div>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load}>刷新</button>
        <button className="btn btn-pri" disabled={!dirty || busy} onClick={save}>{busy ? '保存中…' : '保存'}</button>
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
        同事做完交出来的事（汇率录了要审、计提凭证要审、付款凭证要审、计提更正要办），会挂到这里选的人的首页「待我处理」里。
        一个环节可以选多个人，任何一人在金蝶审了，所有人名下同时销。换了人，没办完的自动跟到新人名下。
      </div>
      {cfg === null && <div style={{ color: 'var(--red)', fontSize: 12.5, marginBottom: 8 }}>读取失败：{err}</div>}
      {cfg && (
        <table>
          <thead><tr><th>环节</th><th>在哪办</th><th>处理人</th><th style={{ textAlign: 'right' }}>没办完的</th></tr></thead>
          <tbody>{cfg.scenes.map(s => {
            const names = pick[s.key] || []
            const rest = users.filter(u => !names.includes(u.name))
            return (
              <tr key={s.key}>
                <td style={{ whiteSpace: 'nowrap' }}><b style={{ fontWeight: 600 }}>{s.role}</b>
                  <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{s.label}</div></td>
                <td style={{ whiteSpace: 'nowrap', color: 'var(--ink-2)' }}>{s.where === 'kd' ? '金蝶' : '工作台'} · {s.place}</td>
                <td>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                    {names.map(n => <span key={n} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, padding: '3px 6px 3px 10px', borderRadius: 999, background: 'var(--accent-soft)', color: 'var(--accent)' }}>
                      {n}<button type="button" onClick={() => del(s.key, n)} title="移除" style={{ border: 0, background: 'none', cursor: 'pointer', color: 'inherit', padding: 0, fontSize: 12 }}>✕</button></span>)}
                    <select value="" onChange={e => add(s.key, e.target.value)} aria-label={`给「${s.role}」加人`}
                      style={{ font: 'inherit', fontSize: 12.5, padding: '4px 6px', border: '1px solid var(--line-strong)', borderRadius: 7, background: 'var(--bg)', color: 'var(--ink-2)' }}>
                      <option value="">＋ 加人</option>
                      {rest.map(u => <option key={u.name} value={u.name}>{u.name}{u.post ? `（${u.post}）` : ''}</option>)}
                    </select>
                  </div>
                  {names.length === 0 && <div style={{ marginTop: 5, fontSize: 12, color: 'var(--amber)' }}>
                    还没指定处理人：这个环节的待办会先挂到主管理员名下{supers.length ? `（${supers.join('、')}）` : ''}</div>}
                </td>
                <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{s.open || '—'}</td>
              </tr>)
          })}</tbody>
        </table>
      )}
      {cfg && <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 8, lineHeight: 1.7 }}>
        在金蝶办的那几类，后台每 {cfg.everyMin} 分钟去金蝶核对一次单据状态，审了自动销账。
        {cfg.local ? <span style={{ marginLeft: 6, color: 'var(--amber)' }}>本机测试库：不起自动核对，只能在首页点「立即核对」</span>
          : cfg.thread?.lastRun ? <span style={{ marginLeft: 6 }}>上次核对 {String(cfg.thread.lastRun).slice(5, 16)}
            {cfg.thread.lastError && <span style={{ marginLeft: 6, color: 'var(--amber)' }}>没连上金蝶：{cfg.thread.lastError}</span>}</span>
            : <span style={{ marginLeft: 6 }}>服务刚启动，还没核对过</span>}
      </div>}
      {msg && <div style={{ fontSize: 12.5, color: msg.startsWith('保存失败') ? 'var(--red)' : 'var(--green)', marginTop: 6 }}>{msg}</div>}
    </div>
  )
}
