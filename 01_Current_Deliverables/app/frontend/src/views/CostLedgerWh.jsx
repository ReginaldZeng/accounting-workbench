// [Change Log] Date:2026-08-10 Author:Claude/c Version:V2.254 (原 V2.119/V2.120/V2.121)
// 存货台账 › 基础资料（三级菜单，原名「仓库类型」）。三块：
//   ①类别 → 科目 对照（V2.254 新增）②仓库类型切片器 ③仓库 → 类型 对照 + 每仓备注。
// V2.254 改名与扩容：这页装的已不止仓库类型。类别↔科目对照原先【只存在于 sample_data 的 json 里、
//   没有数据库兜底】，而部署包历来整体排除 sample_data/ → 服务器上一点取数就 500（V2.132 真实事故）。
//   搬进本页后：数据库为唯一真相源、json 降级为首次使用的种子，那颗雷就拆了。
// 定稿口径（V2.121 业务方）：类型不带启用/禁用日期、不带操作；仓库也不做启用/禁用，合并为一条备注。
// 类型留空＝不配，仓库透视里落「（属性缺失）」并提示——不硬归、不猜。
import React, { useEffect, useMemo, useState } from 'react'
import { getWhTypes, saveWhTypes, getCatSubjects, saveCatSubjects } from '../api'

// 切片器取值：null=全部 / ''=未配（与 map 里未配的真实取值一致）/ 其它=类型名。
// 不用字符串哨兵——原先拿「前导空格+全部」当哨兵，既怕撞名，那个空格还在落盘时变成了 NUL 字节。
const ALL = null
const UNSET = ''
const LBL_ALL = '全部'
const LBL_UNSET = '（未配）'

// 分页（V2.354）——业务方：「基础资料参考一下月结核对，分页」。
// ⚠**两页不是三页**：原来的 ②仓库类型 是 ③仓库对照 那张表的筛选器
//   （chip 上写着"点一下即筛出下方该类型的仓库"），拆开就断了，所以②③同页。
//   正好也是本页副标题本来的分法：「类别→科目对照」｜「仓库类型与对照」。
const SECS = [
  { k: 'cat', n: '类别 → 科目对照', d: '账实勾稽按此归集' },
  { k: 'wh', n: '仓库类型与对照', d: '仓库透视按此分组 · 143 个仓库' },
]

export default function CostLedgerWh({ user }) {
  const [data, setData] = useState(null)
  const [map, setMap] = useState({})          // {仓库: 类型}
  const [notes, setNotes] = useState({})      // {仓库: 备注}
  const [types, setTypes] = useState([])      // [类型名]
  const [tFilter, setTFilter] = useState(ALL)
  const [onlyNew, setOnlyNew] = useState(false)   // 只看「新仓库待配」（取数时自动上档、类型还没配）
  const [page, setPage] = useState(1)          // 仓库表分页（V2.296）
  const [sec, setSec] = useState('cat')        // 分页（V2.354）：本页原是一路往下滚的三大块
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [newT, setNewT] = useState('')
  // ── 类别 → 科目 对照（V2.254）──
  // 编辑态用【逗号分隔的字符串】而不是数组：会计手上就是"产成品、自制半成品、委外半成品"这么一串，
  // 一格一个 tag 反而难改。存的时候再切开，中英文逗号、顿号都认。
  const [cat, setCat] = useState(null)          // 后端原样：{subjects:[{subject,cats}], extra, src, by, at}
  const [catRows, setCatRows] = useState([])    // [{subject, text}]
  const [catExtra, setCatExtra] = useState('')
  const [catBusy, setCatBusy] = useState(false)
  const [newSub, setNewSub] = useState('')

  const splitCats = s => String(s || '').split(/[,，、;；\s]+/).map(x => x.trim()).filter(Boolean)
  const catToRows = r => (r.subjects || []).map(s => ({ subject: s.subject, text: (s.cats || []).join('、') }))

  const loadCat = async () => {
    try {
      const r = await getCatSubjects()
      if (!r.ok) { setErr(r.msg || '读取类别↔科目对照失败'); return }
      setCat(r); setCatRows(catToRows(r)); setCatExtra((r.extra || []).join('、'))
      if (r.msg) setErr(r.msg)
    } catch (e) { setErr('读取类别↔科目对照失败：' + e.message) }
  }

  const catDirty = useMemo(() => {
    if (!cat) return 0
    const base = JSON.stringify(catToRows(cat)) + '|' + (cat.extra || []).join('、')
    return (JSON.stringify(catRows) + '|' + catExtra) !== base ? 1 : 0
  }, [cat, catRows, catExtra])

  const saveCat = async () => {
    setCatBusy(true); setErr(''); setMsg('')
    try {
      const payload = catRows.map(r => ({ subject: r.subject, cats: splitCats(r.text) }))
      const r = await saveCatSubjects(payload, splitCats(catExtra))
      if (!r.ok) setErr(r.msg || '保存失败')
      else { await loadCat(); setMsg(`类别↔科目对照已保存：${r.n_subjects} 个科目 / ${r.n_cats} 个存货类别 / ${r.n_extra} 个单列科目。下次核对即按新对照归集。`) }
    } catch (e) { setErr('保存失败：' + e.message) }
    setCatBusy(false)
  }

  const load = async () => {
    setBusy(true); setErr('')
    try {
      const r = await getWhTypes()
      if (!r.ok) { setErr(r.msg || '取数失败'); setBusy(false); return }
      setData(r)
      setMap(Object.fromEntries(r.rows.map(x => [x.wh, x.type || ''])))
      setNotes(Object.fromEntries(r.rows.map(x => [x.wh, x.note || ''])))
      setTypes(r.types.map(t => t.name))
      setMsg(''); if (r.msg) setErr(r.msg)
    } catch (e) { setErr('取数失败：' + e.message) }
    setBusy(false)
  }
  useEffect(() => { load(); loadCat() }, [])

  const rows = data ? data.rows : []
  const dirty = useMemo(() => {
    if (!data) return 0
    const a = rows.filter(r => (map[r.wh] || '') !== (r.type || '')).length
    const b = rows.filter(r => (notes[r.wh] || '') !== (r.note || '')).length
    const c = types.length !== data.types.length ? 1 : 0
    return a + b + c
  }, [rows, map, notes, types, data])

  // 在用仓库数＝按【编辑中】的对照实时算，切片器计数随手改随动
  const useN = useMemo(() => {
    const c = {}; rows.forEach(r => { const v = map[r.wh] || UNSET; c[v] = (c[v] || 0) + 1 }); return c
  }, [rows, map])

  const shown = rows.filter(r => {
    if (q && !(r.wh.includes(q) || (r.code || '').toLowerCase().includes(q.toLowerCase()))) return false
    if (onlyNew && !r.is_new) return false
    if (tFilter === ALL) return true
    if (tFilter === UNSET) return !(map[r.wh] || '')
    return (map[r.wh] || '') === tFilter
  })

  // 分页（V2.296）：143 个仓库一次全铺出来要滚很久，与其它工具统一成每页 50 条。
  // ⚠**纯前端分页**：仓库对照是一次性全量拉回来的小表（百来行），不像收发存明细那样走服务端筛+分页；
  //   而且编辑中的 map/notes 是整表共享的 state，翻页不丢改动——**在第 1 页改、翻到第 3 页再存，照样都存**。
  // ⚠切筛选条件必须回到第 1 页：否则会停在第 3 页却只剩 1 页数据、看到一片空白（同 CostLedger 明细页那条）。
  const PAGE = 50
  const pages = Math.max(1, Math.ceil(shown.length / PAGE))
  const pg = Math.min(page, pages)
  const pageRows = shown.slice((pg - 1) * PAGE, pg * PAGE)
  useEffect(() => { setPage(1) }, [q, tFilter, onlyNew])

  const addType = () => {
    const n = newT.trim()
    if (!n) return
    if (types.includes(n)) { setErr(`仓库类型「${n}」已存在`); return }
    setTypes([...types, n]); setNewT(''); setErr('')
  }
  // 只允许删【没有仓库在用】的类型——防止手滑新增的错别字永久留在切片器里
  const delType = (t) => { if (!useN[t]) setTypes(types.filter(x => x !== t)) }

  const save = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await saveWhTypes(map, types, notes)
      if (!r.ok) setErr(r.msg || '保存失败')
      // 先 load 再设提示：load() 会清 msg，顺序反了提示会被自己擦掉
      else { await load(); setMsg(`已保存：${r.n_types} 个仓库类型、${r.n} 条仓库对照、${r.n_notes} 条备注。仓库透视下次核对即按新类型分组。`) }
    } catch (e) { setErr('保存失败：' + e.message) }
    setBusy(false)
  }

  const canEdit = data && data.can_edit
  const chip = (key, label, n) => (
    <span key={String(key)} className={'chip' + (tFilter === key ? ' on' : '')} onClick={() => setTFilter(key)}>
      {label} <span className="c-n">{n}</span>
      {canEdit && key !== ALL && key !== UNSET && !useN[key] &&
        <span title="删除这个未被使用的类型" onClick={e => { e.stopPropagation(); delType(key) }}
          style={{ marginLeft: 6, color: 'var(--ink-3)', fontWeight: 700 }}>×</span>}
    </span>
  )

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">存货台账 · 基础资料</div>
          <div className="h-sub">类别 → 科目对照（账实勾稽按此归集） · 仓库类型与「仓库 → 类型」对照（仓库透视按此分组） · 仓库清单取自金蝶仓库档案（只读）</div>
        </div>
        <div className="h-tools">
          <button className="btn-sec" disabled={busy} onClick={load}>{busy ? '刷新中…' : '刷新'}</button>
          {canEdit && <button className="btn-pri" disabled={busy || !dirty} onClick={save}>
            {busy ? '保存中…' : dirty ? `保存改动（${dirty}）` : '保存改动'}</button>}
        </div>
      </div>

      <div className="scroll" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* 与「月结核对」同款步骤条：同一个工具里两页的导航手感应当一致 */}
        <div className="steps">
          {SECS.map((s, i) => (
            <div key={s.k} className={'step' + (sec === s.k ? ' cur' : '')}
              style={{ cursor: 'pointer' }} onClick={() => setSec(s.k)}>
              <div className="num">{i + 1}</div>
              <div><div className="sn">{s.n}</div><div className="sd">{s.d}</div></div>
            </div>
          ))}
        </div>

        {err && <div className="trust" style={{ color: 'var(--amber)', borderColor: 'var(--amber-line)', background: 'var(--amber-bg)' }}>⚠ {err}</div>}
        {msg && <div className="trust" style={{ color: 'var(--green)', borderColor: 'var(--green-line)', background: 'var(--green-bg)' }}>✓ {msg}</div>}
        {!canEdit && data && <div className="trust" style={{ fontSize: 12, color: 'var(--ink-2)' }}>
          你可以查看，但没有修改权限（需管理员授予「存货台账·维护基础资料」）。</div>}

        {/* ── ① 类别 → 科目 对照（V2.254：从 json 搬进数据库，这里是唯一维护入口）── */}
        {sec === 'cat' && <div className="card" style={cardS}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 }}>
            <div style={{ fontWeight: 600 }}>① 存货类别 → 总账科目 对照</div>
            {cat && cat.src === 'json' && <span style={{ fontSize: 12, color: 'var(--amber)' }}>
              尚未在本页保存过 · 当前用的是随程序发布的初始对照</span>}
            {cat && cat.src === 'db' && <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>
              由 {cat.by || '—'} 于 {cat.at || '—'} 保存</span>}
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
            <b>账实勾稽就按这张表把存货类别归到总账科目</b>——改它会直接改变勾稽结果，请与成本会计核过再存。
            一个存货类别<b>只能归一个科目</b>（挂两处会被算两遍，勾稽必然不平，保存时会拦下）。
            多个类别用顿号或逗号隔开。「单列科目」＝不经收发存表、直接取科目余额的那几个（在途物资、委托加工物资）。
          </div>
          <table className="tbl" style={{ width: '100%', maxWidth: 880 }}>
            <thead><tr>
              <th style={{ width: 160 }}>总账科目</th><th>包含的存货类别</th>{canEdit && <th style={{ width: 60 }}></th>}
            </tr></thead>
            <tbody>
              {catRows.map((r, i) => (
                <tr key={r.subject + i}>
                  <td style={{ fontWeight: 600 }}>{r.subject}</td>
                  <td>{canEdit
                    ? <input value={r.text} placeholder="例如：产成品、自制半成品、委外半成品"
                      onChange={e => setCatRows(catRows.map((x, j) => j === i ? { ...x, text: e.target.value } : x))}
                      style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12 }} />
                    : (r.text || <span style={{ color: 'var(--ink-3)' }}>—</span>)}</td>
                  {canEdit && <td><span title="删除该科目" onClick={() => setCatRows(catRows.filter((_, j) => j !== i))}
                    style={{ cursor: 'pointer', color: 'var(--ink-3)', fontWeight: 700 }}>×</span></td>}
                </tr>
              ))}
              {!catRows.length && <tr><td colSpan={canEdit ? 3 : 2} style={{ color: 'var(--ink-3)' }}>
                还没有任何对照——没有它就算不了账实勾稽，请先建立。</td></tr>}
            </tbody>
          </table>
          {canEdit && <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
            <input placeholder="新增总账科目，例如：库存商品" value={newSub} onChange={e => setNewSub(e.target.value)}
              onKeyDown={e => {
                if (e.key !== 'Enter') return
                const n = newSub.trim()
                if (!n) return
                if (catRows.some(x => x.subject === n)) { setErr(`科目「${n}」已存在`); return }
                setCatRows([...catRows, { subject: n, text: '' }]); setNewSub(''); setErr('')
              }}
              style={{ padding: '4px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12, width: 190 }} />
            <button className="btn-sec" disabled={!newSub.trim()} onClick={() => {
              const n = newSub.trim()
              if (catRows.some(x => x.subject === n)) { setErr(`科目「${n}」已存在`); return }
              setCatRows([...catRows, { subject: n, text: '' }]); setNewSub(''); setErr('')
            }}>新增科目</button>
            <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>回车也可新增</span>
          </div>}
          <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>单列科目（不走收发存表）</span>
            {canEdit
              ? <input value={catExtra} onChange={e => setCatExtra(e.target.value)} placeholder="在途物资、委托加工物资"
                style={{ padding: '4px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12, width: 280 }} />
              : <b style={{ fontSize: 12 }}>{catExtra || '—'}</b>}
            {canEdit && <button className="btn-pri" disabled={catBusy || !catDirty} onClick={saveCat}>
              {catBusy ? '保存中…' : catDirty ? '保存类别↔科目对照' : '类别↔科目对照（无改动）'}</button>}
          </div>
        </div>}

        {/* ── ② 仓库类型：横向切片器 ── */}
        {sec === 'wh' && <div className="card" style={cardS}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>② 仓库类型</div>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
            仓库类型只影响<b>仓库透视的分组小计</b>，不参与三道勾稽、不改任何金额。点一下即筛出下方该类型的仓库。
          </div>
          <div className="chips" style={{ marginBottom: canEdit ? 10 : 0 }}>
            {chip(ALL, LBL_ALL, rows.length)}
            {chip(UNSET, LBL_UNSET, useN[UNSET] || 0)}
            {types.map(t => chip(t, t, useN[t] || 0))}
          </div>
          {canEdit && <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input placeholder="新增仓库类型，例如：保税仓" value={newT} onChange={e => setNewT(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') addType() }}
              style={{ padding: '4px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12, width: 190 }} />
            <button className="btn-sec" disabled={!newT.trim()} onClick={addType}>新增类型</button>
            <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>没有仓库在用的类型，chip 上有「×」可删</span>
          </div>}
        </div>}

        {/* ── ③ 仓库 → 类型 对照（与②同页：②是③的筛选器，拆开就断了）── */}
        {sec === 'wh' && <>
        <div className="card" style={{ ...cardS, paddingBottom: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>③ 仓库 → 类型 对照</div>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7 }}>
            类型<b>留空＝不配</b>，该仓在仓库透视里落「（属性缺失）」并单独提示——工具不替你猜。备注随你写（停用原因、归属主体、跟谁核对过…）。
            {data && data.from_db
              ? <> 当前由 <b>{data.updated_by || '—'}</b> 于 {data.updated_at || '—'} 保存。</>
              : <> 尚未在本页保存过（当前为初始对照）。</>}
          </div>
        </div>

        <div className="chips">
          <span className={'chip' + (onlyNew ? ' on' : '')} onClick={() => setOnlyNew(!onlyNew)}
            style={rows.some(r => r.is_new) ? { borderColor: 'var(--amber-line)', color: 'var(--amber)' } : undefined}>
            ✦ 新仓库待配 <span className="c-n">{rows.filter(r => r.is_new).length}</span></span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <input placeholder="搜仓库名 / 编码" value={q} onChange={e => setQ(e.target.value)}
              style={{ padding: '4px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12, width: 150 }} />
            {(q || onlyNew || tFilter !== ALL) && <span className="chip" onClick={() => { setQ(''); setOnlyNew(false); setTFilter(ALL) }}>
              清除筛选 · 当前 {shown.length}/{rows.length}</span>}
          </span>
        </div>

        <div className="twrap">
          <table>
            <thead><tr>
              <th style={{ minWidth: 160 }}>仓库</th><th style={{ minWidth: 78 }}>编码</th>
              <th style={{ minWidth: 160 }}>仓库类型</th><th style={{ minWidth: 260 }}>备注</th>
            </tr></thead>
            <tbody>
              {!data && <tr><td colSpan={4} style={{ color: 'var(--ink-3)', padding: 16 }}>加载中…</td></tr>}
              {data && shown.length === 0 && <tr><td colSpan={4} style={{ color: 'var(--ink-3)', padding: 16 }}>无符合条件的仓库</td></tr>}
              {pageRows.map(r => {
                const v = map[r.wh] || ''
                const nt = notes[r.wh] || ''
                const changed = v !== (r.type || '') || nt !== (r.note || '')
                return (
                  <tr key={r.wh} style={changed ? { background: 'var(--amber-bg)' } : undefined}>
                    <td><b>{r.wh}</b>
                      {r.is_new && <span className="pill" style={{ marginLeft: 6, color: 'var(--amber)', background: 'var(--amber-bg)', border: '1px solid var(--amber-line)' }}
                        title={`${r.since} 期取数时首次出现，工具已自动上档，类型待你配`}>✦ 新</span>}
                      {r.forbid && <span className="pill mut" style={{ marginLeft: 6 }}>金蝶已禁用</span>}
                      {!r.in_kingdee && <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 6 }}>档案外</span>}
                      {r.since && <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 6 }}>自 {r.since}</span>}</td>
                    <td style={{ color: 'var(--ink-3)', fontSize: 12 }}>{r.code || '—'}</td>
                    <td>
                      <select value={v} disabled={!canEdit} onChange={e => setMap({ ...map, [r.wh]: e.target.value })}
                        style={{
                          padding: '3px 7px', borderRadius: 6, fontSize: 12, width: 142,
                          border: '0.5px solid ' + (v ? 'var(--line-strong)' : 'var(--amber-line)'),
                          background: canEdit ? '#fff' : 'var(--bg-sub)',
                        }}>
                        <option value="">未配</option>
                        {types.map(t => <option key={t} value={t}>{t}</option>)}
                        {/* 已配的类型若已被删掉，仍保留可见，避免一编辑就被悄悄清掉 */}
                        {v && !types.includes(v) && <option value={v}>{v}（已删除）</option>}
                      </select>
                    </td>
                    <td>
                      <input value={nt} disabled={!canEdit} placeholder="—" maxLength={200}
                        onChange={e => setNotes({ ...notes, [r.wh]: e.target.value })}
                        style={{
                          padding: '3px 7px', borderRadius: 6, fontSize: 12, width: 214,
                          border: '0.5px solid var(--line)', background: canEdit ? '#fff' : 'var(--bg-sub)',
                        }} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* 分页条（V2.296）：与「收发存明细」「电商剔除明细」同款措辞与手感 */}
        {shown.length > 0 && <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, fontSize: 12.5 }}>
          <span style={{ color: 'var(--ink-2)' }}>
            筛出 {shown.length} 个仓库{pages > 1 ? ` · 第 ${pg} / ${pages} 页（每页 ${PAGE} 个）` : ''}
          </span>
          {pages > 1 && <>
            <button className="btn-sec" style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}
              disabled={pg <= 1} onClick={() => setPage(pg - 1)}>‹ 上一页</button>
            <button className="btn-sec" style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}
              disabled={pg >= pages} onClick={() => setPage(pg + 1)}>下一页 ›</button>
            {/* 翻页不丢改动，但改动可能在别的页上——不说一句，会以为"我改的怎么没了" */}
            {dirty > 0 && <span style={{ color: 'var(--amber,#a35a00)' }}>
              有 {dirty} 处未保存的改动（可能在其它页，保存时一并提交）</span>}
          </>}
        </div>}
        </>}

        {sec === 'wh' && <div className="trust" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
          共 {rows.length} 个仓库（金蝶 107 组织仓库档案 {rows.filter(r => r.in_kingdee).length} 个
          {rows.some(r => !r.in_kingdee) ? `，另有 ${rows.filter(r => !r.in_kingdee).length} 个仅见于历史对照或本期数据、档案中已不存在` : ''}）。
          新类型请先在上方②新增，再到表里选。<b>哪一期缺哪些仓库的类型，去「月结核对」看——那边会明确拦住封存与导出。</b>
        </div>}
      </div>
    </>
  )
}

const cardS = { border: '1px solid var(--line)', borderRadius: 9, background: '#fff', padding: '16px 18px' }
