// [Change Log] Date: 2026-09-12 | Author: Codex | Version: V2.564
// Description: Inline feature catalog, immutable keys, layout insertion and independent display channels.
import React, { useEffect, useState } from 'react'
import { getNavModules, moveNavModule, addNavModule, delNavModule, saveNavSections } from '../api.js'
import { STAGES, stageOf, visibleAt, sectionCode } from '../navCatalog.js'
import './NavCatalog.css'

const EMPTY = { key: '', label: '', sec: '', parent: '', status: '敬请期待' }

export default function NavCatalog({ onModsChanged }) {
  const [data, setData] = useState(null), [error, setError] = useState('')
  const [edit, setEdit] = useState(null), [busy, setBusy] = useState(false)
  const [query, setQuery] = useState(''), [newItem, setNewItem] = useState(null)
  const [sectionsDraft, setSectionsDraft] = useState(null)
  const load = () => getNavModules().then(setData).catch(e => setError(e.message || '目录加载失败'))
  useEffect(() => { load() }, [])
  const run = async (action, done) => {
    setBusy(true); setError('')
    try {
      const result = await action()
      if (!result.ok) throw new Error(result.msg || '保存失败')
      setData(d => ({ ...d, ...result })); done?.(); onModsChanged?.()
    } catch (e) { setError(e.message || '请求失败，请重试') }
    finally { setBusy(false) }
  }
  if (!data) return <div className="cat">{error || '加载功能目录…'}{error && <button className="btn" onClick={load}>重试</button>}</div>
  const { modules = [], sections = [], posts = [], state = {} } = data
  const editable = data['可编辑']
  const labelOf = key => modules.find(m => m.key === key)?.label || sections.find(s => s.key === key)?.label || key
  const roots = modules.filter(m => !m.parent && !m.always)
  const begin = m => setEdit({ ...m, status: stageOf(state[m.key]?.status || m.default).value,
    posts: state[m.key]?.posts || [], show_home: visibleAt(m, state[m.key], 'home'), show_sidebar: visibleAt(m, state[m.key], 'sidebar') })
  const change = patch => setEdit(e => ({ ...e, ...patch }))
  const nextLayout = (sec, parent, key) => {
    const peers = modules.filter(m => m.key !== key && m.sec === sec && (m.parent || '') === (parent || ''))
    const prefix = parent ? modules.find(m => m.key === parent).layout : sectionCode(sec, sections)
    return `${prefix}-${String(peers.length + 1).padStart(parent ? 3 : 2, '0')}`
  }
  const destination = (sec, parent) => change({ sec, parent, layout: nextLayout(sec, parent, edit.key) })
  const save = () => {
    const original = modules.find(m => m.key === edit.key)
    const body = { key: edit.key, label: edit.label.trim(), description: edit.description || '' }
    if (!body.label) { setError('名称不能为空'); return }
    if (!original.always) Object.assign(body, { status: edit.status, posts: edit.posts,
      show_home: edit.show_home, show_sidebar: edit.show_sidebar })
    if (edit.layout !== original.layout) body.layout = edit.layout.trim().toUpperCase()
    run(() => moveNavModule(body), () => setEdit(null))
  }
  const add = () => run(() => addNavModule({ ...newItem, key: newItem.key.trim(), label: newItem.label.trim() }), () => setNewItem(null))
  const filtered = modules.filter(m => !query.trim() || `${m.key} ${m.label} ${m.layout}`.toLowerCase().includes(query.trim().toLowerCase()))
  const buttonLocked = busy || !!edit || !!newItem || !!sectionsDraft
  return <section className="cat nc">
    <div className="nc-toolbar">
      <div><h2>功能目录维护</h2><p>规划中 → 开发中 → 测试中 → 并行试运行 → 已验收</p></div>
      <input aria-label="搜索功能" placeholder="搜索名称、功能编码或布局序号" value={query} onChange={e => setQuery(e.target.value)} />
      {editable && <><button className="btn" disabled={buttonLocked} onClick={() => setSectionsDraft(sections.map(s => ({ ...s })))}>一级模块</button>
        <button className="btn btn-pri" disabled={buttonLocked} onClick={() => setNewItem({ ...EMPTY, sec: sections.find(s => !s.bottom)?.key || '' })}>新增功能</button></>}
    </div>
    <p className="nc-note">功能编码固定；布局序号决定位置，插入后同级菜单自动顺移。服务岗位为标签，访问权限仍在账号管理维护。</p>
    {error && <div className="nc-error" role="alert">{error}</div>}
    {sectionsDraft && <div className="nc-form">
      <strong>一级模块名称与顺序</strong>
      {sectionsDraft.filter(s => !s.bottom).map(s => <div className="nc-toolbar" key={s.key}>
        <code>{sectionCode(s.key, sections)}</code>
        <input aria-label={`${s.key}模块名称`} value={s.label} maxLength={20} onChange={e => setSectionsDraft(v => v.map(x => x.key === s.key ? { ...x, label: e.target.value } : x))} />
        <input aria-label={`${s.key}模块排序`} type="number" min="0" value={s.order} onChange={e => setSectionsDraft(v => v.map(x => x.key === s.key ? { ...x, order: Number(e.target.value) } : x))} />
        {!s.builtin && <button className="btn" onClick={() => setSectionsDraft(v => v.filter(x => x.key !== s.key))}>删除</button>}
      </div>)}
      <button className="btn" disabled={busy} onClick={() => { const key = window.prompt('新模块标识（小写字母、数字、下划线）'); if (key) setSectionsDraft(v => [...v, { key, label: '新模块', order: Math.max(0, ...v.filter(s => !s.bottom).map(s => s.order)) + 10, builtin: false }]) }}>添加一级模块</button>
      <button className="btn btn-pri" disabled={busy} onClick={() => run(() => saveNavSections(sectionsDraft), () => setSectionsDraft(null))}>保存模块</button>
      <button className="btn" disabled={busy} onClick={() => setSectionsDraft(null)}>取消</button>
    </div>}
    {newItem && <div className="nc-form">
      <strong>新增功能 · 自动排在所选位置末尾</strong>
      <div className="nc-toolbar">
        <input aria-label="新功能编码" placeholder="功能编码，如 vat_check" value={newItem.key} onChange={e => setNewItem(v => ({ ...v, key: e.target.value }))} />
        <input aria-label="新功能名称" placeholder="功能名称" maxLength={20} value={newItem.label} onChange={e => setNewItem(v => ({ ...v, label: e.target.value }))} />
        <select aria-label="新功能归属位置" value={newItem.parent || `sec:${newItem.sec}`} onChange={e => { const v = e.target.value; setNewItem(n => v.startsWith('sec:') ? { ...n, sec: v.slice(4), parent: '' } : { ...n, parent: v, sec: modules.find(m => m.key === v).sec }) }}>
          {sections.filter(s => !s.bottom).map(s => <option key={s.key} value={`sec:${s.key}`}>{s.label} · 二级</option>)}
          {roots.map(m => <option key={m.key} value={m.key}>{m.label} · 三级</option>)}
        </select>
        <select aria-label="新功能交付阶段" value={newItem.status} onChange={e => setNewItem(n => ({ ...n, status: e.target.value }))}>{STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}</select>
        <button className="btn btn-pri" disabled={busy || !newItem.key.trim() || !newItem.label.trim()} onClick={add}>添加</button>
        <button className="btn" disabled={busy} onClick={() => setNewItem(null)}>取消</button>
      </div>
      <p className="nc-note">新增的是功能入口；业务页面需接入同一编码后才能使用。添加后可在表格内调整展示位置和服务岗位。</p>
    </div>}
    <div className="nc-scroll"><table className="nc-table">
      <colgroup>{[220, undefined, 190, 100, 170, 155, 135, 140, 145].map((width, i) => <col key={i} style={{ width }} />)}</colgroup>
      <thead><tr>{['功能编码', '名称', '服务岗位', '菜单层级', '归属位置', '布局序号', '交付阶段', '展示位置', '操作'].map(t => <th key={t}>{t}</th>)}</tr></thead>
      <tbody>{sections.map(sec => {
        const rows = filtered.filter(m => m.sec === sec.key)
        if (!rows.length) return null
        return <React.Fragment key={sec.key}>
          <tr className="nc-group"><th colSpan={9}>{sectionCode(sec.key, sections)} · {sec.label} <span>{rows.length} 项</span></th></tr>
          {rows.map(m => {
            const active = edit?.key === m.key, row = active ? edit : m
            const phase = stageOf(state[m.key]?.status || m.default)
            const selectedPosts = active ? edit.posts : state[m.key]?.posts || []
            const hasChildren = modules.some(x => x.parent === m.key)
            return <tr key={m.key} className={active ? 'nc-editing' : ''}>
              <td><button className="nc-code" title={`${m.key}（点击复制）`} onClick={async () => { try { await navigator.clipboard.writeText(m.key) } catch { setError('复制失败，请选择编码后手动复制') } }}>{m.key}</button></td>
              <td>{active ? <><input aria-label="名称" maxLength={20} value={row.label} onChange={e => change({ label: e.target.value })} /><input aria-label="功能说明" placeholder="功能说明（选填）" maxLength={120} value={row.description || ''} onChange={e => change({ description: e.target.value })} style={{ marginTop: 6 }} /></> : <span className={m.parent ? 'nc-child' : ''} title={m.description || ''}>{m.label}{m.group_only && <small> 分组</small>}</span>}</td>
              <td>{active && !m.always ? <details className="nc-posts"><summary>已选 {selectedPosts.length} 个岗位</summary><div>{posts.map(p => <label key={p.key}><input type="checkbox" checked={selectedPosts.includes(p.key)} onChange={e => change({ posts: e.target.checked ? [...selectedPosts, p.key] : selectedPosts.filter(k => k !== p.key) })} />{p.label}</label>)}</div></details> : <span title={posts.filter(p => selectedPosts.includes(p.key)).map(p => p.label).join('、')}>{posts.filter(p => selectedPosts.includes(p.key)).map(p => p.label).join('、') || '—'}</span>}</td>
              <td>{m.always ? '固定入口' : active ? <select aria-label="菜单层级" value={row.parent ? '3' : '2'} onChange={e => e.target.value === '2' ? destination(row.sec, '') : destination(roots.find(p => p.key !== m.key)?.sec, roots.find(p => p.key !== m.key)?.key)}>
                <option value="2">二级菜单</option><option value="3" disabled={hasChildren || !roots.some(p => p.key !== m.key)}>三级菜单</option></select> : m.parent ? '三级菜单' : '二级菜单'}</td>
              <td>{active && !m.always ? <select aria-label="归属位置" value={row.parent || row.sec} onChange={e => row.parent ? destination(modules.find(p => p.key === e.target.value).sec, e.target.value) : destination(e.target.value, '')}>
                {(row.parent ? roots.filter(p => p.key !== m.key) : sections.filter(s => !s.bottom)).map(p => <option key={p.key} value={p.key}>{p.label}</option>)}</select> : labelOf(m.parent || m.sec)}</td>
              <td>{active && !m.always ? <input aria-label="布局序号" value={row.layout} onChange={e => change({ layout: e.target.value })} title="二级 B1-01；三级 B1-01-001；保存后自动顺移" /> : <code>{m.layout}</code>}</td>
              <td>{m.always ? '始终开启' : active ? <select aria-label="交付阶段" value={row.status} onChange={e => change({ status: e.target.value })}>{STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}</select> : <span className="nc-stage" style={{ color: phase.color, background: phase.bg }}>{phase.label}</span>}</td>
              <td><div className="nc-places">{[['home', '首页'], ['sidebar', '左侧菜单']].map(([key, text]) => <label key={key}><input aria-label={`${m.label} ${text}`} type="checkbox" disabled={!active || busy || m.always || (key === 'home' && m.group_only)} checked={active ? row[`show_${key}`] : visibleAt(m, state[m.key], key)} onChange={e => change({ [`show_${key}`]: e.target.checked })} />{text}</label>)}</div></td>
              <td><div className="nc-actions">{editable && (active ? <><button className="btn btn-pri" disabled={busy} onClick={save}>{busy ? '保存中' : '保存'}</button><button className="btn" disabled={busy} onClick={() => setEdit(null)}>取消</button></> : <><button className="btn" disabled={buttonLocked} onClick={() => begin(m)}>编辑</button>{m.builtin === false && <button className="btn nc-delete" disabled={buttonLocked} onClick={() => window.confirm(`删除自建功能「${m.label}」？`) && run(() => delNavModule(m.key))}>删除</button>}</>)}</div></td>
            </tr>
          })}
        </React.Fragment>
      })}{!filtered.length && <tr><td colSpan={9}>没有匹配的功能，请调整搜索条件。</td></tr>}</tbody>
    </table></div>
    <p className="nc-note">表格、首页和左侧菜单共享名称与顺序。未勾选任何展示位置的功能仍保留在本表。岗位名单在“门户 → 账号管理 → 岗位模板设置”维护。</p>
  </section>
}
