// [Change Log] Date:2026-09-07 Author:Claude/Reginald Zeng Version:V2.514
// 门户管理 ›「系统架构」维护页，管两样东西：
//
//   ① **架构图**（两个槽位）——上传你们那套工具导出的 .html，落到 backend/landscape_pages/。
//      full=完整版（含服务器地址与端口）、public=对外脱敏版。
//      ⚠ 地址端口烤死在 HTML 里，没法按人剥字段，只能按人决定发哪一份；判定在服务端。
//      脱敏版没上传时，无权限者看到的是占位提示，**不会回退到完整版**。
//
//   ② **文字部分**——结论 / 发现 / 建议顺序 / 补充系统清单，存 app_settings 的 landscape 键。
//      这是那张图不回答的东西：图讲「怎么跑的」，这里讲「所以下一步该干什么」。
//      其中「技术细节」列按 landscape_tech 权限在服务端剥离。
//
// 复用 PortalAdmin 的 pa- 作用域样式，本文件只补几个自己的类。
import React, { useEffect, useState } from 'react'
import { getLandscape, saveLandscape, resetLandscape,
         getLandscapeDiagramStatus, uploadLandscapeDiagram } from '../api.js'

const STATUS = [
  { key: 'ok', label: '在跑' }, { key: 'par', label: '人工并行' }, { key: 'beta', label: '开发中' },
  { key: 'soon', label: '规划中' }, { key: 'empty', label: '空壳' },
  { key: 'weak', label: '脆弱环节' }, { key: 'ext', label: '外部系统' },
]

const CSS = `
.ls-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.ls-tbl th{text-align:left;padding:8px 10px;font-size:11px;color:var(--ink3);
  background:rgba(255,255,255,.03);font-weight:700;white-space:nowrap}
.ls-tbl td{padding:7px 10px;border-top:1px solid var(--line);vertical-align:top}
.ls-tbl td.k{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--ink3);white-space:nowrap}
.ls-tbl input,.ls-tbl select,.ls-tbl textarea{width:100%;background:rgba(255,255,255,.04);
  border:1px solid var(--line2);border-radius:7px;color:var(--ink);padding:5px 8px;font:inherit;font-size:12px}
.ls-tbl textarea{min-height:44px;resize:vertical;line-height:1.7}
.ls-sec{margin-top:20px;font-size:13px;font-weight:800}
.ls-hint{font-size:11.5px;color:var(--ink3);margin:3px 0 8px;line-height:1.8}
.ls-blk{border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px}
.ls-blk .row{display:flex;gap:8px;margin-bottom:6px}
.ls-blk .row input{flex:1}
`

export default function PortalLandscapeAdmin() {
  const [d, setD] = useState(null)
  const [tech, setTech] = useState(false)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [slots, setSlots] = useState(null)      // V2.514 两个架构图槽位的状态

  const flash = (ok, t) => { setMsg({ ok, t }); setTimeout(() => setMsg(null), 2600) }

  const load = async () => {
    const r = await getLandscape()
    if (r && r.ok) { setD(r.landscape); setTech(!!r.tech) }
    else flash(false, (r && r.msg) || '读取失败')
  }
  const loadSlots = async () => {
    const r = await getLandscapeDiagramStatus()
    if (r && r.ok) setSlots(r.slots)
  }
  useEffect(() => { load(); loadSlots() }, [])

  const upload = async (slot, file) => {
    if (!file) return
    setBusy(true)
    const r = await uploadLandscapeDiagram(slot, file)
    setBusy(false)
    if (r && r.ok) { flash(true, `已上传 ${Math.round(r.bytes / 1024)} KB`); loadSlots() }
    else flash(false, (r && r.msg) || '上传失败')
  }

  const setNode = (i, k, v) => setD(o => {
    const nodes = o.nodes.slice(); nodes[i] = { ...nodes[i], [k]: v }; return { ...o, nodes }
  })
  const setList = (key, i, k, v) => setD(o => {
    const arr = (o[key] || []).slice(); arr[i] = { ...arr[i], [k]: v }; return { ...o, [key]: arr }
  })

  const save = async () => {
    setBusy(true)
    const r = await saveLandscape(d)
    setBusy(false)
    if (r && r.ok) flash(true, '已保存，门户即时生效')
    else flash(false, (r && r.msg) || '保存失败')
  }

  const reset = async () => {
    if (!window.confirm('把页面文字恢复为代码里的出厂内容？（不影响已上传的架构图）\n你在这里改过的文字会被全部覆盖，不可撤销。')) return
    setBusy(true)
    const r = await resetLandscape()
    setBusy(false)
    if (r && r.ok) { setD(r.landscape); setTech(!!r.tech); flash(true, '已恢复出厂内容') }
    else flash(false, (r && r.msg) || '恢复失败')
  }

  if (!d) return <div className="pa-empty">加载中…</div>

  return (
    <div>
      <style>{CSS}</style>
      <div className="pa-card">
        <div className="pa-ct">系统架构 · 图与文字维护
          <span>
            <span className="pa-lk" onClick={reset} title="覆盖为代码里的出厂内容">⟳ 恢复出厂</span>
            <span className="pa-lk" style={{ marginLeft: 10 }} onClick={save}>{busy ? '保存中…' : '保存'}</span>
          </span>
        </div>
        <div className="ls-hint">
          上半传图、下半改字，保存后门户即时生效。
          {tech
            ? '「技术细节」列只有持 landscape_tech 权限的人能在门户上看到，普通账号连数据都拿不到。'
            : '⚠ 你当前没有 landscape_tech 权限，技术细节列不显示；直接保存会把它清空，请先取得权限再编辑。'}
          {msg && <span style={{ color: msg.ok ? 'var(--green)' : 'var(--red)', marginLeft: 8 }}>{msg.t}</span>}
        </div>

        <div className="ls-sec">架构图（两个槽位）</div>
        <div className="ls-hint">
          用你们那套工具重新导出 <code>.html</code> 传上来即可，不用改代码不用发版。
          <b style={{ color: 'var(--amber)' }}>服务器地址与端口是烤死在 HTML 里的</b>，
          没法按人剥掉——所以分两份：门户按访问者权限决定发哪一份，判定在服务端。
        </div>
        {[
          { k: 'full', t: '完整版', d: '含服务器地址、端口、数据库类型。仅持 landscape_tech 权限者可见。' },
          { k: 'public', t: '对外脱敏版', d: '把地址端口换成「云服务器」「各服务」再导出一份。所有登录账号可见。' },
        ].map(s2 => {
          const st = slots && slots[s2.k]
          return (
            <div key={s2.k} className="ls-blk">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 5 }}>
                <b style={{ fontSize: 13 }}>{s2.t}</b>
                {st && (st.exists
                  ? <span style={{ fontSize: 11.5, color: 'var(--green)' }}>已上传 · {st.kb} KB · {st.at}</span>
                  : <span style={{ fontSize: 11.5, color: 'var(--amber)' }}>未上传</span>)}
                <label className="pa-lk" style={{ marginLeft: 'auto' }}>
                  {st && st.exists ? '替换…' : '上传…'}
                  <input type="file" accept=".html,.htm" style={{ display: 'none' }} disabled={busy}
                         onChange={e => { upload(s2.k, e.target.files && e.target.files[0]); e.target.value = '' }} />
                </label>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--ink3)', lineHeight: 1.8 }}>{s2.d}</div>
              {st && st.exists && (
                <a href={'/api/portal/landscape/diagram?slot=' + s2.k} target="_blank" rel="noreferrer"
                   style={{ fontSize: 11.5, color: 'var(--brand2)', textDecoration: 'none' }}>预览 ↗</a>
              )}
            </div>
          )
        })}

        <div className="ls-sec">文字部分</div>
        <div className="ls-hint">图讲「怎么跑的」，这部分讲「所以下一步该干什么」——它是那张图不回答的东西。</div>

        <div className="pa-fld"><label className="pa-lb">页面副标题</label>
          <textarea className="pa-ta" value={d.standfirst || ''}
                    onChange={e => setD(o => ({ ...o, standfirst: e.target.value }))} /></div>
        <div className="pa-fld"><label className="pa-lb">一句话结论</label>
          <textarea className="pa-ta" value={d.verdict || ''}
                    onChange={e => setD(o => ({ ...o, verdict: e.target.value }))} /></div>

        <div className="ls-sec">补充系统清单（{(d.nodes || []).length}）</div>
        <div className="ls-hint">
          给上传的架构图作补充——尤其是<b>那张图没画的商务线</b>（星桥智联 / BOM 价管 / 销售报价接单）。
          「技术细节」列会渲染成门户上的技术细节表，仅授权账号可见。key 是这条记录的身份，不可改。
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="ls-tbl">
            <thead><tr>
              <th style={{ width: 92 }}>key</th><th style={{ width: 150 }}>名称</th>
              <th style={{ width: 180 }}>一句话说明</th><th style={{ width: 96 }}>状态</th>
              <th style={{ width: 92 }}>版本</th>{tech && <th>技术细节（授权可见）</th>}
            </tr></thead>
            <tbody>
              {(d.nodes || []).map((n, i) => (
                <tr key={n.key}>
                  <td className="k">{n.key}</td>
                  <td><input value={n.name || ''} onChange={e => setNode(i, 'name', e.target.value)} /></td>
                  <td><input value={n.sub || ''} onChange={e => setNode(i, 'sub', e.target.value)} /></td>
                  <td><select value={n.status || 'ext'} onChange={e => setNode(i, 'status', e.target.value)}>
                    {STATUS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
                  </select></td>
                  <td><input value={n.ver || ''} onChange={e => setNode(i, 'ver', e.target.value)} /></td>
                  {tech && <td><textarea value={n.tech || ''} onChange={e => setNode(i, 'tech', e.target.value)} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="ls-sec">发现（{(d.findings || []).length}）</div>
        {(d.findings || []).map((f, i) => (
          <div key={i} className="ls-blk">
            <div className="row">
              <input value={f.tag || ''} placeholder="小标签" onChange={e => setList('findings', i, 'tag', e.target.value)} />
              <input value={f.title || ''} placeholder="标题" onChange={e => setList('findings', i, 'title', e.target.value)} />
              <label style={{ fontSize: 11.5, color: 'var(--ink2)', display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}>
                <input type="checkbox" style={{ width: 'auto' }} checked={!!f.alert}
                       onChange={e => setList('findings', i, 'alert', e.target.checked)} />标红
              </label>
            </div>
            <textarea value={f.body || ''} onChange={e => setList('findings', i, 'body', e.target.value)} />
          </div>
        ))}

        <div className="ls-sec">建议顺序（{(d.steps || []).length}）</div>
        {(d.steps || []).map((s, i) => (
          <div key={i} className="ls-blk">
            <div className="row">
              <input value={s.title || ''} placeholder="标题" onChange={e => setList('steps', i, 'title', e.target.value)} />
            </div>
            <textarea value={s.body || ''} onChange={e => setList('steps', i, 'body', e.target.value)} />
          </div>
        ))}

        <div className="pa-fld" style={{ marginTop: 16 }}><label className="pa-lb">边界说明（页脚）</label>
          <textarea className="pa-ta" value={d.note || ''}
                    onChange={e => setD(o => ({ ...o, note: e.target.value }))} /></div>

        <div className="pa-bar" style={{ marginTop: 14 }}>
          <button className="pa-btn pri" onClick={save} disabled={busy}>{busy ? '保存中…' : '保存'}</button>
          <button className="pa-btn" onClick={load} disabled={busy}>放弃改动</button>
        </div>
      </div>
    </div>
  )
}
