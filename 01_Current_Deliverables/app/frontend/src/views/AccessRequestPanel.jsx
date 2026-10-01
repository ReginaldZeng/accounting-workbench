// [Change Log] Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.731
// Description: 系统设置 → 常规设置「权限申请推送」（移植财务BP工作台 V2.553 AccessRequestPanel，交接提示词 §2.3）：
//   选谁收首页「申请开通」的钉钉消息（搜名字加人；不选＝默认管理员＝conf.ini [dingtalk] to_userids/to_mobiles），
//   显示当前生效接收人数（0 人＝申请发不出去，红字提示）+ 最近 10 条申请（谁、哪个页面、是否送达）。
import React, { useEffect, useState } from 'react'
import { getAccessRequestConfig, saveAccessRequestConfig, getDingtalkRoster } from '../api.js'

export default function AccessRequestPanel() {
  const [cfg, setCfg] = useState(undefined)        // undefined=加载中 / null=读取失败
  const [err, setErr] = useState('')
  const [picked, setPicked] = useState([])          // [{userid, name}]
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [roster, setRoster] = useState(undefined)   // undefined=没拉 / null=拉取中 / []=有
  const [rosterErr, setRosterErr] = useState('')
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)

  const load = () => getAccessRequestConfig()
    .then(d => { setCfg(d); setPicked((d.userids || []).map(id => ({ userid: id, name: (d.names || {})[id] || id }))); setDirty(false); setErr('') })
    .catch(e => { setCfg(null); setErr(e.message) })
  useEffect(() => { load() }, [])
  const loadRoster = () => {
    if (roster !== undefined) return
    setRoster(null)
    getDingtalkRoster().then(r => { if (r.ok === false) { setRosterErr(r.msg || '拉通讯录失败'); setRoster([]) } else setRoster(r.people || []) })
      .catch(e => { setRosterErr(e.message); setRoster([]) })
  }
  const kw = q.trim()
  const hits = kw && Array.isArray(roster)
    ? roster.filter(p => !picked.some(x => x.userid === p.userid) && ((p.name || '').includes(kw) || (p.title || '').includes(kw) || (p.dept || '').includes(kw))).slice(0, 8)
    : []
  const add = p => { setPicked(v => [...v, { userid: p.userid, name: p.name }]); setDirty(true); setQ('') }
  const del = id => { setPicked(v => v.filter(x => x.userid !== id)); setDirty(true) }
  const save = async () => {
    setBusy(true)
    try {
      const r = await saveAccessRequestConfig(picked)
      setMsg(r.userids.length ? `已保存：申请会推给 ${r.userids.length} 人` : '已清空：申请会推给默认管理员')
      load()
    } catch (e) { setMsg('保存失败：' + e.message) } finally { setBusy(false) }
  }

  return (
    <div className="cat" style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>权限申请推送</div>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={load}>刷新</button>
        <button className="btn btn-pri" disabled={!dirty || busy} onClick={save}>{busy ? '保存中…' : '保存'}</button>
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
        同事在首页没权限的页面上点「申请开通」后，钉钉会把申请（姓名、岗位、页面、用途）单聊推给这里选的人。
        不选则推给默认管理员（conf.ini [dingtalk] 的收件人，与风控值守、取件机告警同一个钉钉应用）。同一人同一页面 24 小时内只推一次。
      </div>
      {cfg === null && <div style={{ color: 'var(--red)', fontSize: 12.5, marginBottom: 8 }}>读取失败：{err}</div>}
      {cfg && !cfg.robotOk && <div style={{ color: 'var(--amber)', fontSize: 12.5, marginBottom: 8 }}>钉钉未就绪：{cfg.robotMsg}</div>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginBottom: 8 }}>
        {picked.map(p => <span key={p.userid} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, padding: '3px 6px 3px 10px', borderRadius: 999, background: 'var(--accent-soft)', color: 'var(--accent)' }}>
          {p.name}<button type="button" onClick={() => del(p.userid)} title="移除" style={{ border: 0, background: 'none', cursor: 'pointer', color: 'inherit', padding: 0, fontSize: 12 }}>✕</button></span>)}
        <div style={{ position: 'relative' }}>
          <input value={q} placeholder="输名字搜人加；不选 = 默认管理员" onChange={e => setQ(e.target.value)}
            onFocus={() => { setFocused(true); loadRoster() }} onBlur={() => setTimeout(() => setFocused(false), 150)}
            style={{ width: 240, font: 'inherit', fontSize: 12.5, padding: '5px 9px', border: '1px solid var(--line-strong)', borderRadius: 7, background: 'var(--bg)', color: 'var(--ink)' }} />
          {focused && kw && <div style={{ position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 20, minWidth: 300, background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 9, boxShadow: '0 10px 30px rgba(20,28,58,.16)', padding: 4 }}>
            {roster === null ? <div style={{ padding: '6px 10px', fontSize: 12.5, color: 'var(--ink-3)' }}>加载通讯录中…</div>
              : rosterErr ? <div style={{ padding: '6px 10px', fontSize: 12.5, color: 'var(--red)' }}>{rosterErr}</div>
                : hits.length === 0 ? <div style={{ padding: '6px 10px', fontSize: 12.5, color: 'var(--ink-3)' }}>没搜到「{kw}」</div>
                  : hits.map(p => <div key={p.userid} className="navrow" onMouseDown={e => { e.preventDefault(); add(p) }}
                    style={{ display: 'flex', gap: 10, padding: '6px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12.5 }}>
                    <span>{p.name}</span><span style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 11.5 }}>{[p.title, p.dept].filter(Boolean).join(' · ')}</span></div>)}
          </div>}
        </div>
      </div>
      {cfg && <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>
        当前生效：{cfg.source === 'configured' ? `已选 ${cfg.effective} 人` : `默认管理员 ${cfg.effective} 人`}
        {cfg.effective === 0 && <span style={{ marginLeft: 8, color: 'var(--red)', fontWeight: 600 }}>没有接收人，申请会发不出去</span>}
        {cfg.local && <span style={{ marginLeft: 8, color: 'var(--amber)' }}>本机测试库：申请只记录、不真发钉钉</span>}
      </div>}
      {msg && <div style={{ fontSize: 12.5, color: 'var(--green)', marginTop: 6 }}>{msg}</div>}
      {cfg && cfg.recent && cfg.recent.length > 0 && (
        <table style={{ marginTop: 10 }}>
          <thead><tr><th>时间</th><th>申请人</th><th>页面</th><th>用途</th><th></th></tr></thead>
          <tbody>{cfg.recent.slice(0, 10).map(r => <tr key={r.at + r.user + r.cap}>
            <td style={{ whiteSpace: 'nowrap' }}>{String(r.at).slice(5, 16)}</td>
            <td>{r.user}<span style={{ color: 'var(--ink-3)', marginLeft: 4, fontSize: 11.5 }}>{r.post}</span></td>
            <td>{r.label}</td>
            <td style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.note}>{r.note}</td>
            <td>{r.sent ? <span className="tag" style={{ color: 'var(--green)', background: 'var(--green-bg)' }} title={r.error || ''}>{r.error ? '已记录' : '已送达'}</span> : <span className="tag" style={{ color: 'var(--red)', background: 'var(--red-bg)' }} title={r.error}>失败</span>}</td>
          </tr>)}</tbody>
        </table>
      )}
    </div>
  )
}
