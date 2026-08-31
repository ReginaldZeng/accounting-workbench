// [Change Log] Date:2026-07-10 Author:Claude/c Version:V2.74
// 基础数据页（平台级主数据）。与「基础设置」的分野：设置=工作台怎么跑，数据=公司长什么样。
// 本期仅一个 tab：主体档案（账套全称/简称/简码/金蝶账簿代码/别名/启用）。
//   · 简码 = 凭证归档册号首段（SZL2026-03-02），一经生成过册号即锁死——册号已印在标签上贴到书脊。
//   · 金蝶账簿代码 = 物流计提写金蝶的 FACCOUNTBOOKID，原先硬编码在 kernels/logistics_accrual.py。
//   · 别名 = 把台账里「星期零 / 深零」等写法归到同一主体，否则归一化会把两家公司认成一家。
// 编辑限主管理员：这张表同时决定册号前缀与金蝶账簿，改错一行横跨两个工具。
import React, { useEffect, useState } from 'react'
import { getOrgs, saveOrg, deleteOrg } from '../api.js'

const TABS = [{ key: 'orgs', label: '主体档案' }]
const inp = {
  border: '1px solid var(--line-strong,var(--line-strong))', borderRadius: 7, padding: '6px 9px',
  fontSize: 12.5, background: 'var(--bg,#fff)', color: 'var(--ink,var(--ink))', width: '100%',
}
const btn = {
  border: '1px solid var(--line-strong,var(--line-strong))', borderRadius: 7, padding: '6px 12px',
  fontSize: 12.5, fontWeight: 600, background: 'var(--bg,#fff)', cursor: 'pointer', whiteSpace: 'nowrap',
}
const btnPri = { ...btn, border: 0, background: 'var(--accent,var(--accent))', color: '#fff' }
const ctd = { borderBottom: '1px solid var(--line,var(--line))', padding: '7px 10px', verticalAlign: 'middle' }
const th = { ...ctd, background: 'var(--bg-sub,var(--bg-sub))', fontWeight: 600, fontSize: 11, color: 'var(--ink-3,var(--ink-3))', whiteSpace: 'nowrap' }

const EMPTY = { full_name: '', short_name: '', code: '', book_code: '', aliases: '', color: '', active: true, note: '' }

// 标签纸颜色选择器：色板取自后端（口径唯一），另留自定义 #RRGGBB 兜底。
// 颜色只做界面预览与「该买哪种纸」的指引——打印不出底色，靠换彩色标签纸。
function ColorPick({ value, palette, onChange, readOnly }) {
  const [open, setOpen] = useState(false)
  const cur = value || ''
  const swatch = (hex, title, on, onClick) => (
    <span key={hex} title={title} onClick={onClick} style={{
      width: 18, height: 18, borderRadius: 5, background: hex || 'transparent', cursor: onClick ? 'pointer' : 'default',
      border: on ? '2px solid var(--accent,var(--accent))' : '1px solid var(--line-strong,var(--line-strong))',
      display: 'inline-block', flex: '0 0 auto',
      backgroundImage: hex ? 'none' : 'linear-gradient(45deg,transparent 45%,var(--red) 45%,var(--red) 55%,transparent 55%)',
    }} />
  )
  if (readOnly) return <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
    {swatch(cur, cur || '未设色')}<span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{palette.find(p => p.hex === cur)?.name || cur || '—'}</span>
  </div>

  return <div style={{ position: 'relative' }}>
    <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer' }}>
      {swatch(cur, cur || '未设色')}
      <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{palette.find(p => p.hex === cur)?.name || cur || '选色'}</span>
    </div>
    {open && <div style={{
      position: 'absolute', zIndex: 20, top: 26, left: 0, background: '#fff', padding: 10, borderRadius: 9,
      border: '1px solid var(--line-strong,var(--line-strong))', boxShadow: '0 6px 22px rgba(20,24,40,.14)', width: 208,
    }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 9 }}>
        {swatch('', '不设色', !cur, () => { onChange(''); setOpen(false) })}
        {palette.map(p => swatch(p.hex, `${p.name} ${p.hex}`, cur === p.hex, () => { onChange(p.hex); setOpen(false) }))}
      </div>
      <input style={{ ...inp, fontSize: 11.5 }} placeholder="自定义 #RRGGBB（须浅色）"
        value={cur} onChange={e => onChange(e.target.value)} onKeyDown={e => e.key === 'Enter' && setOpen(false)} />
      <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.6 }}>
        颜色只用于屏幕预览和买纸参考；<b>打印不出底色</b>，靠换彩色标签纸。太深的色会被拦下（黑字看不清）。
      </div>
    </div>}
  </div>
}

export default function BasicData({ user }) {
  const [tab, setTab] = useState('orgs')
  const [rows, setRows] = useState([])
  const [palette, setPalette] = useState([])
  const [draft, setDraft] = useState({})     // id → 编辑中的行
  const [nw, setNw] = useState(EMPTY)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const canEdit = user?.role === 'admin'

  const load = () => getOrgs().then(r => { setRows(r.orgs || []); setPalette(r.palette || []); setDraft({}) }).catch(() => {})
  useEffect(() => { load() }, [])

  const rowOf = r => ({ ...r, aliases: (draft[r.id]?.aliases ?? r.aliases.join('，')), ...draft[r.id] })
  const edit = (r, k, v) => setDraft(d => ({ ...d, [r.id]: { ...(d[r.id] || { aliases: r.aliases.join('，') }), [k]: v } }))

  const save = async (body) => {
    setBusy(true); setMsg('')
    const r = await saveOrg(body)
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '保存失败'); return false }
    if (!body.id) setNw(EMPTY)
    load(); return true
  }
  const del = async (r) => {
    if (!window.confirm(`删除主体「${r.short_name}」？`)) return
    const res = await deleteOrg(r.id)
    if (!res.ok) setMsg(res.msg || '删除失败'); else load()
  }

  return (<>
    <div className="head"><div>
      <div className="h-title">基础数据</div>
      <div className="h-sub">公司主数据 · 一处维护，各工具共用（凭证归档取简码、物流计提取金蝶账簿代码）</div>
    </div></div>

    <div className="body">
      <div style={{ display: 'flex', gap: 8 }}>
        {TABS.map(t => <span key={t.key} className={'chip' + (tab === t.key ? ' active' : '')} onClick={() => setTab(t.key)}>
          {t.label}<span className="c-n">{t.key === 'orgs' ? rows.length : ''}</span></span>)}
      </div>

      {tab === 'orgs' && <>
        <div style={{ fontSize: 12.5, color: 'var(--ink-2,var(--ink-2))', lineHeight: 1.75 }}>
          <b>简码</b>是凭证册册号的第一段（如 <code>SZL</code>2026-03-02），会印在标签上贴到书脊，
          <b>该主体一旦有过凭证册就锁死不可改</b>——否则柜子里的旧标签会与系统对不上。
          <b>金蝶账簿代码</b>决定物流计提「一键录入金蝶」把凭证录进哪家公司的账。
          <b>别名</b>用来把各处台账里的不同写法（星期零 / 深零）归到同一主体。
          <b>标签纸颜色</b>=该主体用哪种颜色的不干胶：屏幕预览按此上色，<b>打印时不出底色</b>——纸本身就是彩色的，
          再喷一层底既费墨又把黑字压灰。太深的颜色会被拦下（黑字一米外看不清）。
        </div>

        {!canEdit && <div style={{ fontSize: 12, color: 'var(--amber)' }}>
          主体档案只有主管理员能改（它同时决定册号前缀与金蝶账簿）。以下为只读。
        </div>}

        <div className="tbl-wrap">
          <table style={{ minWidth: 1080 }}>
            <thead><tr>
              {['账套全称', '简称', '简码', '金蝶账簿代码', '别名（逗号分隔）', '标签纸颜色', '启用', '维护人 / 时间', ''].map(h =>
                <th key={h} style={th}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map(r => {
                const d = rowOf(r)
                const dirty = !!draft[r.id]
                return <tr key={r.id}>
                  <td style={{ ...ctd, borderLeft: `4px solid ${d.color || 'transparent'}` }}>{canEdit
                    ? <input style={inp} value={d.full_name} placeholder="工商全名" onChange={e => edit(r, 'full_name', e.target.value)} />
                    : d.full_name || <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                  <td style={ctd}>{canEdit
                    ? <input style={{ ...inp, width: 120 }} value={d.short_name} onChange={e => edit(r, 'short_name', e.target.value)} />
                    : d.short_name}</td>
                  <td style={ctd}>
                    {canEdit && !r.locked
                      ? <input style={{ ...inp, width: 84, fontFamily: 'ui-monospace,Consolas,monospace', textTransform: 'uppercase' }}
                        value={d.code} placeholder="SZL" maxLength={4} onChange={e => edit(r, 'code', e.target.value)} />
                      : <span title={r.locked ? '该主体已有凭证册在册，简码已锁定' : ''}
                        style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 600 }}>
                        {r.locked && '🔒 '}{d.code || <span style={{ color: 'var(--ink-3)', fontWeight: 400 }}>待填</span>}</span>}
                  </td>
                  <td style={ctd}>{canEdit
                    ? <input style={{ ...inp, width: 90 }} value={d.book_code} placeholder="101" onChange={e => edit(r, 'book_code', e.target.value)} />
                    : d.book_code || <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                  <td style={ctd}>{canEdit
                    ? <input style={inp} value={d.aliases} placeholder="星期零，深零" onChange={e => edit(r, 'aliases', e.target.value)} />
                    : (r.aliases.join('、') || <span style={{ color: 'var(--ink-3)' }}>—</span>)}</td>
                  <td style={ctd}><ColorPick value={d.color} palette={palette} readOnly={!canEdit}
                    onChange={v => edit(r, 'color', v)} /></td>
                  <td style={ctd}>{canEdit
                    ? <input type="checkbox" checked={!!d.active} onChange={e => edit(r, 'active', e.target.checked)} />
                    : (d.active ? '启用' : '停用')}</td>
                  <td style={{ ...ctd, color: 'var(--ink-3)', fontSize: 11.5, whiteSpace: 'nowrap' }}>{r.updated_by} / {r.updated_at}</td>
                  <td style={{ ...ctd, whiteSpace: 'nowrap' }}>{canEdit && <>
                    <button style={{ ...(dirty ? btnPri : btn), marginRight: 6 }} disabled={busy || !dirty}
                      onClick={() => save({ ...d, id: r.id })}>保存</button>
                    <span className="lk" style={{ color: 'var(--red)' }} onClick={() => del(r)}>删除</span>
                  </>}</td>
                </tr>
              })}
              {!rows.length && <tr><td style={{ ...ctd, color: 'var(--ink-3)' }} colSpan={9}>主体档案还是空的。</td></tr>}

              {canEdit && <tr>
                <td style={{ ...ctd, borderLeft: `4px solid ${nw.color || 'transparent'}` }}><input style={inp} placeholder="新增：工商全名" value={nw.full_name} onChange={e => setNw({ ...nw, full_name: e.target.value })} /></td>
                <td style={ctd}><input style={{ ...inp, width: 120 }} placeholder="简称 *" value={nw.short_name} onChange={e => setNw({ ...nw, short_name: e.target.value })} /></td>
                <td style={ctd}><input style={{ ...inp, width: 84, fontFamily: 'ui-monospace,Consolas,monospace', textTransform: 'uppercase' }} placeholder="SZL" maxLength={4} value={nw.code} onChange={e => setNw({ ...nw, code: e.target.value })} /></td>
                <td style={ctd}><input style={{ ...inp, width: 90 }} placeholder="101" value={nw.book_code} onChange={e => setNw({ ...nw, book_code: e.target.value })} /></td>
                <td style={ctd}><input style={inp} placeholder="别名，逗号分隔" value={nw.aliases} onChange={e => setNw({ ...nw, aliases: e.target.value })} /></td>
                <td style={ctd}><ColorPick value={nw.color} palette={palette} onChange={v => setNw({ ...nw, color: v })} /></td>
                <td style={ctd}><input type="checkbox" checked={nw.active} onChange={e => setNw({ ...nw, active: e.target.checked })} /></td>
                <td style={ctd}></td>
                <td style={ctd}><button style={btnPri} disabled={busy} onClick={() => save(nw)}>新增</button></td>
              </tr>}
            </tbody>
          </table>
        </div>

        {msg && <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--red)' }}>{msg}</div>}

        <div style={{ fontSize: 11.5, color: 'var(--ink-3,var(--ink-3))', lineHeight: 1.8 }}>
          账簿代码 101 / 105 / 107 由物流计提原先的硬编码迁入（孝感星期九=107 系 V2.29 实证）。
          停用的主体不再参与金蝶账簿映射，也不能登记新的凭证册，历史数据照常查询。
        </div>
      </>}
    </div>
  </>)
}
