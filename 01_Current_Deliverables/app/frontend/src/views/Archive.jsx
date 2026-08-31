// [Change Log] Date:2026-07-11 Author:Claude/c Version:V2.77
// 凭证归档（其它小工具）。本增量两屏：①找凭证（主体+年月+凭证号三件套→册号与当前位置+轨迹）
//                                    ②登记新册（起号自动接上一本，人只填一个"止号"，存前号段校验）
// 位置树维护 / 批量转移 / 标签打印 见下一增量。册号首段简码来自「基础数据 › 主体档案」。
import React, { useEffect, useState } from 'react'
import {
  archiveOrgs, archiveFind, archivePeriodInfo, archiveRegister,
  archiveLocations, archiveSaveLocation, archiveVolumes, archiveTransfer,
  archiveBorrow, archiveReturn, archiveDestroyApply, archiveDestroyCancel, archiveDestroyExecute,
  archiveImportTemplateUrl, archiveImport, archiveCheckup,
} from '../api.js'

const inp = { border: '1px solid var(--line-strong,var(--line-strong))', borderRadius: 8, padding: '7px 10px', fontSize: 13, background: 'var(--bg,#fff)', color: 'var(--ink,var(--ink))' }
const auto = { ...inp, background: 'var(--bg-sub,var(--bg-sub))', color: 'var(--ink-2,var(--ink-2))', borderStyle: 'dashed' }
const btnPri = { border: 0, borderRadius: 8, padding: '8px 16px', fontSize: 13, fontWeight: 600, background: 'var(--accent,var(--accent))', color: '#fff', cursor: 'pointer' }
const btnSec = { border: '1px solid var(--line-strong,var(--line-strong))', borderRadius: 8, padding: '8px 14px', fontSize: 13, fontWeight: 600, background: '#fff', cursor: 'pointer' }
const lb = { fontSize: 11, color: 'var(--ink-3,var(--ink-3))', fontWeight: 600, marginBottom: 5, display: 'block' }
const card = { border: '1px solid var(--line,var(--line))', borderRadius: 10, background: '#fff' }
const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1)

function Find({ orgs }) {
  const [org, setOrg] = useState('')
  const [year, setYear] = useState(2026)
  const [month, setMonth] = useState(1)
  const [no, setNo] = useState('')
  const [res, setRes] = useState(null)
  const [msg, setMsg] = useState('')
  useEffect(() => { if (orgs.length && !org) setOrg(orgs[0].short_name) }, [orgs])

  const go = async () => {
    setMsg(''); setRes(null)
    if (!org || !year || !month || !no) { setMsg('主体、年月、凭证号三项缺一不可'); return }
    const r = await archiveFind(org, year, month, no)
    if (!r.ok) { setMsg(r.msg || '查询失败'); return }
    if (!r.hits.length) { setMsg(`没找到收录「${org} ${year}年${month}月 第${no}号」的册子——可能还没登记，或号段有缺口`); return }
    setRes(r.hits[0])
  }

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
    <div style={{ ...card, padding: '14px 16px', display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
      <div><span style={lb}>主体 *</span><select style={{ ...inp, minWidth: 150 }} value={org} onChange={e => setOrg(e.target.value)}>
        {orgs.map(o => <option key={o.short_name} value={o.short_name}>{o.short_name}（{o.code}）</option>)}</select></div>
      <div><span style={lb}>年 *</span><input style={{ ...inp, width: 80 }} value={year} onChange={e => setYear(e.target.value)} /></div>
      <div><span style={lb}>月 *</span><select style={{ ...inp, width: 72 }} value={month} onChange={e => setMonth(+e.target.value)}>
        {MONTHS.map(m => <option key={m} value={m}>{m}</option>)}</select></div>
      <div><span style={lb}>凭证号 *</span><input style={{ ...inp, width: 100, fontWeight: 600 }} value={no} onChange={e => setNo(e.target.value)} placeholder="85" /></div>
      <button style={btnPri} onClick={go}>找这一本</button>
      <button style={btnSec} onClick={() => { setRes(null); setNo(''); setMsg('') }}>清空</button>
      <div style={{ marginLeft: 'auto', maxWidth: 300, fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>
        三项缺一不可——每个主体每年都有自己的「第 1 号」，少填一项会查出好几本。</div>
    </div>

    {msg && <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 8, padding: '10px 14px', fontSize: 12.5, color: 'var(--red)' }}>{msg}</div>}

    {res && <div style={{ ...card, borderColor: 'var(--accent)', overflow: 'hidden' }}>
      <div style={{ padding: '18px 22px', background: 'linear-gradient(100deg,#f6f7fd,#fff)', borderBottom: '1px solid var(--line)', display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontSize: 30, fontWeight: 700 }}>{res.vol_no}</div>
          <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 4 }}>
            {res.org} · {res.year}年{String(res.month).padStart(2, '0')}月 · {res.vtype} · 第{res.seq}册 · 收录 <b>第 {res.no_from}–{res.no_to} 号</b>，共 {res.sheets} 张</div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <span style={lb}>此刻在这里</span>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{res.loc_path || <span style={{ color: 'var(--ink-3)' }}>未指定位置</span>}</div>
          <div style={{ marginTop: 6 }}><StatusPill s={res.display_status} /></div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
        <div style={{ padding: '15px 22px' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 10 }}>这一本的身份（终身不变 · 印在标签上）</div>
          {[['册号', res.vol_no], ['主体 / 期间', `${res.org} / ${res.year}年${String(res.month).padStart(2, '0')}月`],
          ['凭证类型', res.vtype], ['凭证号区间', `${res.no_from} – ${res.no_to} 号`],
          [`你要找的第 ${no} 号`, '✓ 落在本册内'], ['册内张数', `${res.sheets} 张`],
          ['保存到期', `${res.keep_until} 年`]].map(([k, v]) =>
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px dashed var(--line)', fontSize: 12.5 }}>
              <span style={{ color: 'var(--ink-3)' }}>{k}</span><span style={{ fontWeight: 600 }}>{v}</span></div>)}
        </div>
        <div style={{ padding: '15px 22px', borderLeft: '1px solid var(--line)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 10 }}>这一本去过哪里（位置轨迹 · 永久留痕）</div>
          {(res.trail || []).length ? res.trail.map((t, i) =>
            <div key={i} style={{ padding: '8px 0', borderBottom: i < res.trail.length - 1 ? '1px solid var(--line)' : 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>{t.to_path || '—'}</div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 2 }}>{t.ts} ｜ {t.operator} ｜ {t.reason} ｜ {t.transfer_no}</div>
            </div>) : <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>暂无转移记录。</div>}
        </div>
      </div>
    </div>}

    <div style={{ fontSize: 12, color: 'var(--ink-2)', border: '1px dashed var(--line-strong)', borderRadius: 9, padding: '10px 14px', background: 'var(--bg-sub)', lineHeight: 1.7 }}>
      <b>「这本是谁」看标签，「这本在哪」查系统。</b> 标签只印册子身份、不印位置——位置会变、身份不变，位置若印上标签，每搬一次就得重贴。
    </div>
  </div>
}

function StatusPill({ s }) {
  const map = { '在库': ['var(--green)', 'var(--green-bg)', 'var(--green-line)'], '已装箱': ['var(--blue)', 'var(--blue-bg)', 'var(--blue-line)'],
    '借出中': ['var(--amber)', 'var(--amber-bg)', 'var(--amber-line)'], '待销毁': ['var(--violet)', 'var(--violet-bg)', 'var(--violet-line)'], '已销毁': ['var(--ink-3)', 'var(--gray-bg)', 'var(--line-strong)'] }
  const [c, bg, bd] = map[s] || map['在库']
  return <span style={{ fontSize: 11.5, fontWeight: 600, color: c, background: bg, border: `1px solid ${bd}`, borderRadius: 20, padding: '3px 11px' }}>{s}</span>
}

function Register({ orgs, onDone }) {
  const [org, setOrg] = useState('')
  const [year, setYear] = useState(2026)
  const [month, setMonth] = useState(1)
  const [noTo, setNoTo] = useState('')
  const [info, setInfo] = useState(null)
  const [msg, setMsg] = useState('')
  const [okMsg, setOkMsg] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (orgs.length && !org) setOrg(orgs[0].short_name) }, [orgs])
  useEffect(() => {
    if (!org || !year || !month) { setInfo(null); return }
    archivePeriodInfo(org, year, month).then(r => r.ok && setInfo(r)).catch(() => {})
  }, [org, year, month])

  const o = orgs.find(x => x.short_name === org)
  const from = info ? info.suggest_from : 1
  const seq = info ? info.next_seq : 1
  const sheets = noTo && +noTo >= from ? (+noTo - from + 1) : ''
  const volNo = o ? `${o.code}${year}-${String(month).padStart(2, '0')}-${String(seq).padStart(2, '0')}` : ''

  const save = async () => {
    setMsg(''); setOkMsg('')
    if (!noTo) { setMsg('填一下这一本装到第几号为止'); return }
    setBusy(true)
    const r = await archiveRegister({ org, year: +year, month: +month, no_from: from, no_to: +noTo })
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '登记失败'); return }
    setOkMsg(`已登记 ${r.vol_no}`); setNoTo(''); onDone && onDone()
    archivePeriodInfo(org, year, month).then(x => x.ok && setInfo(x))
  }

  return <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 14, alignItems: 'start' }}>
    <div style={{ ...card, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 15 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>① 这一本是谁 <span style={{ fontWeight: 400, color: 'var(--ink-3)' }}>（灰色虚线框 = 系统自动填，你不用管）</span></div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div><span style={lb}>主体 *</span><select style={{ ...inp, minWidth: 150 }} value={org} onChange={e => setOrg(e.target.value)}>
          {orgs.map(x => <option key={x.short_name} value={x.short_name}>{x.short_name}（{x.code}）</option>)}</select></div>
        <div><span style={lb}>年 *</span><input style={{ ...inp, width: 80 }} value={year} onChange={e => setYear(e.target.value)} /></div>
        <div><span style={lb}>月 *</span><select style={{ ...inp, width: 72 }} value={month} onChange={e => setMonth(+e.target.value)}>
          {MONTHS.map(m => <option key={m} value={m}>{m}</option>)}</select></div>
        <div><span style={lb}>凭证类型</span><div style={{ ...auto, width: 130 }}>记账凭证</div></div>
      </div>

      {info && <div style={{ fontSize: 12, color: 'var(--ink-2)', background: 'var(--bg-sub)', border: '1px solid var(--line-strong)', borderRadius: 8, padding: '9px 12px', lineHeight: 1.6 }}>
        该主体该期间已登记 <b>{info.count} 本</b>。
        <span style={{ color: 'var(--accent)', fontWeight: 600 }}> 本册起号自动接 {from}，册序自动排第 {seq} 册——不用你去翻上一本看它止到几号。</span></div>}

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div><span style={lb}>凭证号 起</span><div style={{ ...auto, width: 100 }}>{from} <span style={{ fontSize: 10, color: 'var(--accent)' }}>自动</span></div></div>
        <div><span style={lb}>凭证号 止 *</span><input style={{ ...inp, width: 120, borderColor: 'var(--accent)', fontWeight: 600 }} value={noTo} onChange={e => setNoTo(e.target.value)} placeholder="118" /></div>
        <div><span style={lb}>册内张数</span><div style={{ ...auto, width: 110 }}>{sheets === '' ? '—' : `${sheets} 张`}</div></div>
        <div><span style={lb}>册序</span><div style={{ ...auto, width: 90 }}>第 {seq} 册</div></div>
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>整张表你只需要填一个数字：<b style={{ color: 'var(--ink)' }}>这一本装到第几号为止</b>。</div>

      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 14, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <span style={lb}>系统为这一本生成的册号</span>
          <div style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontSize: 22, fontWeight: 700 }}>{volNo || '—'}</div>
        </div>
        <button style={{ ...btnPri, marginLeft: 'auto' }} disabled={busy} onClick={save}>保存并登记</button>
      </div>
      {msg && <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--red)' }}>{msg}</div>}
      {okMsg && <div style={{ background: 'var(--green-bg)', border: '1px solid var(--green-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--green)' }}>✓ {okMsg}</div>}
    </div>

    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ ...card, padding: 16, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', alignSelf: 'flex-start' }}>标签预览 · 边填边变</div>
        <div style={{ width: 130, minHeight: 210, border: '1.5px solid var(--ink)', borderRadius: 5, padding: '14px 10px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'space-between', textAlign: 'center', background: o && o.color ? o.color : '#fff' }}>
          <div style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontSize: 14, fontWeight: 700, lineHeight: 1.3 }}>{(volNo || '—').replace('-', '\n')}</div>
          <div style={{ fontSize: 11, color: 'var(--ink-2)' }}>{org}<br />{year} 年 {String(month).padStart(2, '0')} 月</div>
          <div style={{ fontSize: 12, fontWeight: 700, borderTop: '1px solid var(--line-strong)', borderBottom: '1px solid var(--line-strong)', padding: '5px 0', width: '100%' }}>第 {from}–{noTo || '?'} 号</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>记账凭证 · 第 {seq} 册</div>
        </div>
        <div style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', lineHeight: 1.6 }}>颜色＝主体标签纸色（在基础数据维护）；一本打两张：书脊 + 外壳</div>
      </div>
      <div style={{ fontSize: 12, color: 'var(--ink-2)', border: '1px dashed var(--line-strong)', borderRadius: 9, padding: '10px 14px', background: 'var(--bg-sub)', lineHeight: 1.7 }}>
        <b>登记发生在装订那一刻，台账才不会失真。</b> Excel 批量导入用于期初存量（只用一次），本屏用于日常——每月装订完当场录一本。
      </div>
    </div>
  </div>
}

function Transfer({ orgs, canEdit, canDestroy, operator }) {
  const [locs, setLocs] = useState([])
  const [vols, setVols] = useState([])
  const [sel, setSel] = useState(new Set())
  const [curLoc, setCurLoc] = useState(null)   // 选中的位置节点 id（null=全部）
  const [msg, setMsg] = useState('')
  const [conflict, setConflict] = useState(null)
  const [newLoc, setNewLoc] = useState({ name: '', ntype: '柜', parent_id: '' })
  const [toId, setToId] = useState('')
  const [reason, setReason] = useState('装箱')
  const [borrower, setBorrower] = useState('')
  const [due, setDue] = useState('')
  const [approve, setApprove] = useState('')

  const load = () => Promise.all([archiveLocations(), archiveVolumes()]).then(([a, b]) => {
    setLocs(a.locations || []); setVols(b.volumes || []); setSel(new Set())
  }).catch(() => {})
  useEffect(() => { load() }, [])

  const shown = curLoc ? vols.filter(v => v.loc_id === curLoc) : vols
  const selArr = [...sel]
  const toggle = v => setSel(s => { const n = new Set(s); n.has(v) ? n.delete(v) : n.add(v); return n })
  const expectedOf = () => Object.fromEntries(selArr.map(v => {
    const row = vols.find(x => x.vol_no === v); return [v, row ? row.loc_id : null]
  }))

  const act = async (fn, okword) => {
    setMsg(''); setConflict(null)
    const r = await fn()
    if (r.conflict) { setConflict(r.conflicts); setMsg(r.msg); return }
    if (!r.ok) { setMsg(r.msg || '操作失败'); return }
    setMsg(''); await load()
    window.__arch_ok && window.__arch_ok(okword)
  }
  const doTransfer = () => { if (!toId) return setMsg('先选目标位置'); act(() => archiveTransfer({ vol_nos: selArr, expected: expectedOf(), to_id: +toId, reason }), '转移完成') }
  const doBorrow = () => { if (!borrower) return setMsg('填借出人'); act(() => archiveBorrow({ vol_nos: selArr, borrower, due_date: due }), '已借出') }
  const doReturn = () => act(() => archiveReturn({ vol_nos: selArr }), '已归还')
  const doApply = () => { if (!approve) return setMsg('销毁必须填审批单号'); act(() => archiveDestroyApply({ vol_nos: selArr, approve_no: approve, batch_name: '销毁批次 ' + approve }), '已提交销毁申请') }
  const doCancel = () => act(() => archiveDestroyCancel({ vol_nos: selArr }), '已撤回')
  const doExec = () => { if (!window.confirm('确认执行销毁？纸销毁后不可恢复（账永久保留）。')) return; act(() => archiveDestroyExecute({ vol_nos: selArr }), '已销毁') }

  const addLoc = async () => {
    if (!newLoc.name) return setMsg('位置名不能为空')
    const r = await archiveSaveLocation({ ...newLoc, parent_id: newLoc.parent_id ? +newLoc.parent_id : null })
    if (!r.ok) return setMsg(r.msg || '建位置失败')
    setNewLoc({ name: '', ntype: '柜', parent_id: '' }); load()
  }

  const NTYPES = ['库房', '柜', '层', '箱', '外仓', '临时点']
  return <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 14, alignItems: 'start' }}>
    {/* 位置树 */}
    <div style={{ ...card }}>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', fontSize: 13, fontWeight: 600 }}>存放位置树</div>
      <div style={{ padding: 8 }}>
        <div onClick={() => setCurLoc(null)} style={{ padding: '6px 9px', borderRadius: 7, cursor: 'pointer', fontSize: 12.5, fontWeight: curLoc === null ? 600 : 400, color: curLoc === null ? 'var(--accent)' : 'var(--ink-2)', background: curLoc === null ? 'var(--accent-soft)' : 'transparent' }}>全部册子（{vols.length}）</div>
        {locs.map(l => {
          const depth = (l.path.match(/›/g) || []).length
          return <div key={l.id} onClick={() => setCurLoc(l.id)} title={l.path}
            style={{ padding: '6px 9px', paddingLeft: 9 + depth * 14, borderRadius: 7, cursor: 'pointer', fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 7, color: l.terminal ? 'var(--ink-3)' : (curLoc === l.id ? 'var(--accent)' : 'var(--ink-2)'), background: curLoc === l.id ? 'var(--accent-soft)' : 'transparent' }}>
            <span>{l.name}</span>
            <span style={{ fontSize: 10, color: 'var(--ink-3)', border: '1px solid var(--line-strong)', borderRadius: 4, padding: '0 4px' }}>{l.ntype}</span>
            <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--ink-3)', background: 'var(--gray-bg)', borderRadius: 20, padding: '1px 8px' }}>{l.count_direct}</span>
          </div>
        })}
      </div>
      {canEdit && <div style={{ padding: 12, borderTop: '1px solid var(--line)', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 600 }}>新建位置节点</div>
        <input style={inp} placeholder="名称，如 3号柜" value={newLoc.name} onChange={e => setNewLoc({ ...newLoc, name: e.target.value })} />
        <div style={{ display: 'flex', gap: 8 }}>
          <select style={{ ...inp, flex: 1 }} value={newLoc.ntype} onChange={e => setNewLoc({ ...newLoc, ntype: e.target.value })}>{NTYPES.map(t => <option key={t}>{t}</option>)}</select>
          <select style={{ ...inp, flex: 1 }} value={newLoc.parent_id} onChange={e => setNewLoc({ ...newLoc, parent_id: e.target.value })}>
            <option value="">（根节点）</option>
            {locs.filter(l => !l.terminal).map(l => <option key={l.id} value={l.id}>{l.path}</option>)}
          </select>
        </div>
        <button style={btnSec} onClick={addLoc}>+ 建位置</button>
      </div>}
    </div>

    {/* 册子列表 + 操作 */}
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ ...card }}>
        <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{curLoc ? (locs.find(l => l.id === curLoc)?.path || '') : '全部册子'}</span>
          <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>· {shown.length} 本，已选 <b style={{ color: 'var(--accent)' }}>{sel.size}</b> 本</span>
          {canEdit && <button style={{ ...btnSec, marginLeft: 'auto', padding: '5px 10px', fontSize: 11.5 }} onClick={() => setSel(new Set(shown.map(v => v.vol_no)))}>全选本层</button>}
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead><tr>{['', '册号', '期间', '凭证号', '状态', '当前位置'].map(h => <th key={h} style={{ textAlign: 'left', fontSize: 11, color: 'var(--ink-3)', fontWeight: 600, padding: '8px 10px', borderBottom: '1px solid var(--line)', background: 'var(--bg-sub)', whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
            <tbody>
              {shown.map(v => <tr key={v.vol_no} style={{ background: sel.has(v.vol_no) ? 'var(--accent-soft)' : 'transparent' }}>
                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)' }}>{canEdit && <input type="checkbox" checked={sel.has(v.vol_no)} onChange={() => toggle(v.vol_no)} />}</td>
                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)', fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 600, color: conflict && conflict.includes(v.vol_no) ? 'var(--red)' : 'inherit' }}>{v.vol_no}</td>
                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)' }}>{v.year}-{String(v.month).padStart(2, '0')}</td>
                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)' }}>{v.no_from}–{v.no_to}</td>
                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)' }}><StatusPill s={v.display_status} />{v.status === '借出中' && v.borrow_by ? <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 6 }}>{v.borrow_by}</span> : null}</td>
                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--line)', fontSize: 11.5, color: 'var(--ink-2)' }}>{v.loc_path || '—'}</td>
              </tr>)}
              {!shown.length && <tr><td colSpan={6} style={{ padding: 14, textAlign: 'center', color: 'var(--ink-3)' }}>这里还没有册子。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {conflict && <div style={{ border: '1px solid var(--red-line)', background: 'var(--red-bg)', borderRadius: 9, padding: '12px 15px' }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--red)' }}>⚠ 已拦下：其中 {conflict.length} 本在你勾选之后被别人挪走了</div>
        <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 5 }}>若继续提交会把台账写错。请刷新后重试。冲突册号：{conflict.join('、')}</div>
        <button style={{ ...btnSec, marginTop: 8, padding: '5px 11px', fontSize: 11.5 }} onClick={load}>刷新位置</button>
      </div>}

      {canEdit && sel.size > 0 && <div style={{ ...card, borderColor: 'var(--accent)', padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent)' }}>对选中的 {sel.size} 本 —— 操作后生成转移单、永久留痕</div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', paddingBottom: 12, borderBottom: '1px solid var(--line)' }}>
          <div><span style={lb}>批量转移到</span><select style={{ ...inp, minWidth: 200 }} value={toId} onChange={e => setToId(e.target.value)}>
            <option value="">选目标位置…</option>
            {locs.filter(l => !l.terminal).map(l => <option key={l.id} value={l.id}>{l.path}</option>)}</select></div>
          <div><span style={lb}>原因</span><select style={inp} value={reason} onChange={e => setReason(e.target.value)}>{['装箱', '办公室搬迁', '送外仓', '调回'].map(r => <option key={r}>{r}</option>)}</select></div>
          <button style={btnPri} onClick={doTransfer}>确认转移</button>
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', paddingBottom: 12, borderBottom: '1px solid var(--line)' }}>
          <div><span style={lb}>借出人</span><input style={{ ...inp, width: 180 }} placeholder="天健事务所·王审计" value={borrower} onChange={e => setBorrower(e.target.value)} /></div>
          <div><span style={lb}>应还日期</span><input style={{ ...inp, width: 130 }} placeholder="2026-05-10" value={due} onChange={e => setDue(e.target.value)} /></div>
          <button style={btnSec} onClick={doBorrow}>借出</button>
          <button style={btnSec} onClick={doReturn}>归还（自动回原位）</button>
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div><span style={lb}>销毁审批单号</span><input style={{ ...inp, width: 160 }} placeholder="销毁字第7号" value={approve} onChange={e => setApprove(e.target.value)} /></div>
          <button style={{ ...btnSec, borderColor: 'var(--red-line)', background: 'var(--red-bg)', color: 'var(--red)' }} onClick={doApply}>发起销毁申请（总账会计）</button>
          <button style={btnSec} onClick={doCancel}>撤回申请</button>
          {canDestroy && <button style={{ ...btnPri, background: 'var(--red)' }} onClick={doExec}>执行销毁（财务经理）</button>}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>
          销毁两人四眼：总账会计<b>申请</b>（转「待销毁」，可撤回）→ 财务经理<b>执行</b>（转「已销毁」，只读留痕、记录永不删）。{!canDestroy && '你没有销毁执行权限，只能申请。'}
        </div>
      </div>}

      {msg && !conflict && <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--red)' }}>{msg}</div>}
    </div>
  </div>
}

function Labels({ orgs }) {
  const [vols, setVols] = useState([])
  const [org, setOrg] = useState('')
  const [year, setYear] = useState('')
  const [sel, setSel] = useState(new Set())
  const [spW, setSpW] = useState(30)   // 书脊 竖向
  const [spH, setSpH] = useState(50)
  const [shW, setShW] = useState(50)   // 外壳 横向
  const [shH, setShH] = useState(30)
  const [which, setWhich] = useState('both')   // both / spine / shell
  const colorOf = s => (orgs.find(o => o.short_name === s) || {}).color || ''
  const codeOf = s => (orgs.find(o => o.short_name === s) || {}).code || ''

  const load = () => archiveVolumes(org, year).then(r => {
    const vs = (r.volumes || []).filter(v => v.status !== '已销毁')
    setVols(vs); setSel(new Set(vs.map(v => v.vol_no)))
  }).catch(() => {})
  useEffect(() => { load() }, [org, year])

  const chosen = vols.filter(v => sel.has(v.vol_no))
  const toggle = v => setSel(s => { const n = new Set(s); n.has(v) ? n.delete(v) : n.add(v); return n })
  const perBook = which === 'both' ? 2 : 1

  // 书脊：竖向，简码放大 + 尾号 + 号段拆行（窄，一行放不下整册号）
  const SpineLabel = ({ v }) => {
    const code = codeOf(v.org)
    const tail = code && v.vol_no.startsWith(code) ? v.vol_no.slice(code.length) : v.vol_no
    const k = Math.max(0.75, Math.min(1.4, spW / 30))
    return <div className="lbl-print" style={{
      width: `${spW}mm`, height: `${spH}mm`, border: '1px dashed #bbb', borderRadius: 3,
      background: colorOf(v.org) || '#fff', padding: '2mm 1.5mm', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'space-between', textAlign: 'center', overflow: 'hidden', boxSizing: 'border-box',
    }}>
      <div style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 800, fontSize: `${8.5 * k}mm`, lineHeight: 1, letterSpacing: '0.3mm', color: '#000' }}>{code || v.vol_no.slice(0, 3)}</div>
      <div style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 700, fontSize: `${4 * k}mm`, lineHeight: 1.1, color: '#000' }}>{tail}</div>
      <div style={{ fontSize: `${3.4 * k}mm`, fontWeight: 700, borderTop: '0.4mm solid #000', borderBottom: '0.4mm solid #000', padding: '1mm 0', width: '100%', color: '#000', whiteSpace: 'nowrap' }}>{v.no_from}–{v.no_to} 号</div>
      <div style={{ fontSize: `${2.3 * k}mm`, color: '#000', lineHeight: 1.3 }}>{v.org}<br />{v.year}年{String(v.month).padStart(2, '0')}月 · 第{v.seq}册</div>
      <div style={{ fontSize: `${1.9 * k}mm`, color: '#333' }}>{v.vtype} · 书脊</div>
    </div>
  }

  // 外壳：横向，整册号一行最大，号段小字在下（宽，放得下）
  const ShellLabel = ({ v }) => {
    const inner = shW - 7                                  // 内可用宽（mm）
    const f = Math.max(2.6, Math.min(5.5, inner / (v.vol_no.length * 0.62)))   // 让整册号一行不溢出
    return <div className="lbl-print" style={{
      width: `${shW}mm`, height: `${shH}mm`, border: '1px dashed #bbb', borderRadius: 3,
      background: colorOf(v.org) || '#fff', padding: '2mm 3mm', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: '1.4mm', textAlign: 'center', overflow: 'hidden', boxSizing: 'border-box',
    }}>
      <div style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 800, fontSize: `${f}mm`, lineHeight: 1, whiteSpace: 'nowrap', color: '#000' }}>{v.vol_no}</div>
      <div style={{ fontSize: '2.6mm', color: '#000' }}>{v.org} · {v.year}年{String(v.month).padStart(2, '0')}月 · 第{v.seq}册</div>
      <div style={{ fontSize: '2.4mm', color: '#333' }}>第 {v.no_from}–{v.no_to} 号 · {v.vtype}</div>
    </div>
  }

  const numInput = (label, val, set) => <div><span style={lb}>{label}</span><input style={{ ...inp, width: '100%' }} value={val} onChange={e => set(+e.target.value || val)} /></div>

  return <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 14, alignItems: 'start' }}>
    <style>{`
      @media print {
        body * { visibility: hidden; }
        #lbl-sheet, #lbl-sheet * { visibility: visible; }
        #lbl-sheet { position: absolute; left: 0; top: 0; margin: 0; padding: 0; }
        .lbl-print { break-inside: avoid; background: #fff !important; border-color: #ddd !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
        .lbl-noprint { display: none !important; }
        .lbl-card { border: 0 !important; padding: 0 !important; }
        #lbl-sheet { gap: 0 !important; }
        .lbl-group2 { break-before: page; }
        @page { size: A4; margin: 8mm; }
      }
    `}</style>
    <div style={{ ...card, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>标签尺寸 <span style={{ fontWeight: 400, color: 'var(--ink-3)', fontSize: 11.5 }}>不写死 · 随时可调</span></div>
      <div>
        <span style={lb}>打哪种</span>
        <div style={{ display: 'inline-flex', border: '1px solid var(--line-strong)', borderRadius: 7, overflow: 'hidden', fontSize: 12 }}>
          {[['both', '两种'], ['spine', '只书脊'], ['shell', '只外壳']].map(([k, t]) =>
            <span key={k} onClick={() => setWhich(k)} style={{ padding: '5px 11px', cursor: 'pointer', background: which === k ? 'var(--accent)' : '#fff', color: which === k ? '#fff' : 'var(--ink-2)', fontWeight: which === k ? 600 : 400 }}>{t}</span>)}
        </div>
      </div>
      {which !== 'shell' && <div>
        <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 6 }}>书脊标签（竖向）</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9 }}>{numInput('宽 (mm)', spW, setSpW)}{numInput('高 (mm)', spH, setSpH)}</div>
      </div>}
      {which !== 'spine' && <div>
        <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 6 }}>外壳标签（横向）</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9 }}>{numInput('宽 (mm)', shW, setShW)}{numInput('高 (mm)', shH, setShH)}</div>
      </div>}
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 9 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>选册子</div>
        <select style={inp} value={org} onChange={e => setOrg(e.target.value)}>
          <option value="">全部主体</option>{orgs.map(o => <option key={o.short_name} value={o.short_name}>{o.short_name}</option>)}</select>
        <input style={inp} placeholder="年份筛选，如 2026（留空=全部）" value={year} onChange={e => setYear(e.target.value)} />
        <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>已选 <b style={{ color: 'var(--accent)' }}>{chosen.length}</b> 本 · 共出 <b>{chosen.length * perBook}</b> 枚标签</div>
      </div>
      <button style={btnPri} onClick={() => window.print()}>打印 {chosen.length * perBook} 枚</button>
      <div style={{ fontSize: 11.5, color: 'var(--ink-2)', border: '1px dashed var(--line-strong)', borderRadius: 8, padding: '9px 12px', background: 'var(--bg-sub)', lineHeight: 1.7 }}>
        <b>外壳横向、书脊竖向，内容同源。</b> 外壳把整册号一行放最大（贴盒子）、书脊把简码放大拆行（贴册子）。预览按主体色，打印黑字靠换纸。
      </div>
    </div>

    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ ...card, padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 10 }}>册子清单（点掉不想打的）</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {vols.map(v => <span key={v.vol_no} onClick={() => toggle(v.vol_no)} style={{
            fontSize: 11.5, fontFamily: 'ui-monospace,Consolas,monospace', padding: '4px 9px', borderRadius: 6, cursor: 'pointer',
            border: '1px solid var(--line-strong)', background: sel.has(v.vol_no) ? (colorOf(v.org) || 'var(--accent-soft)') : '#fff',
            opacity: sel.has(v.vol_no) ? 1 : 0.4, textDecoration: sel.has(v.vol_no) ? 'none' : 'line-through',
          }}>{v.vol_no}</span>)}
          {!vols.length && <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>没有可打印的册子（先去登记新册）。</span>}
        </div>
      </div>

      <div id="lbl-sheet" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {which !== 'shell' && <div className="lbl-card" style={{ ...card, padding: 16 }}>
          <div className="lbl-noprint" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 10 }}>书脊标签 · {spW}×{spH}mm（贴册子书脊）</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {chosen.map(v => <SpineLabel key={v.vol_no} v={v} />)}
            {!chosen.length && <div style={{ fontSize: 12, color: 'var(--ink-3)', padding: 8 }}>选中册子后显示标签。</div>}
          </div>
        </div>}
        {which !== 'spine' && <div className={'lbl-card' + (which === 'both' ? ' lbl-group2' : '')} style={{ ...card, padding: 16 }}>
          <div className="lbl-noprint" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 10 }}>外壳标签 · {shW}×{shH}mm（贴外壳/盒子，横向）</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {chosen.map(v => <ShellLabel key={v.vol_no} v={v} />)}
            {!chosen.length && <div style={{ fontSize: 12, color: 'var(--ink-3)', padding: 8 }}>选中册子后显示标签。</div>}
          </div>
        </div>}
      </div>
      <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>预览按真实 mm、以主体色着色（仅分纸参考）；虚线为剥离边界，打印是黑字不出线不出底。两种标签打印时各自成页。</div>
    </div>
  </div>
}

function ImportCheck({ orgs, canEdit, onImported }) {
  const [rep, setRep] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  // 号段体检
  const [org, setOrg] = useState('')
  const [year, setYear] = useState(2026)
  const [month, setMonth] = useState(1)
  const [total, setTotal] = useState('')
  const [gaps, setGaps] = useState(null)
  useEffect(() => { if (orgs.length && !org) setOrg(orgs[0].short_name) }, [orgs])

  const upload = async (file) => {
    if (!file) return
    setBusy(true); setMsg(''); setRep(null)
    const r = await archiveImport(file)
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '导入失败'); return }
    setRep(r); onImported && onImported()
  }
  const doCheck = async () => {
    setGaps(null); setMsg('')
    if (!total) { setMsg('填一下金蝶该期间共多少张凭证'); return }
    const r = await archiveCheckup(org, year, month, +total)
    if (!r.ok) { setMsg(r.msg || '体检失败'); return }
    setGaps(r)
  }

  return <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, alignItems: 'start' }}>
    {/* 批量导入 */}
    <div style={{ ...card, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 13.5, fontWeight: 600 }}>Excel 批量导入 <span style={{ fontWeight: 400, color: 'var(--ink-3)', fontSize: 11.5 }}>期初存量 · 只用一次</span></div>
      <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.75 }}>
        把柜子里现存的历史册子一次性搬进系统。下载模板，一行一本填：<b>主体、年、月、凭证号起止、存放位置</b>。
        册号系统自动生成、位置树按路径自动建、号段当场校验。<b>建议先录近 3 年，历史再分批补。</b>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <a href={archiveImportTemplateUrl} style={{ ...btnSec, textDecoration: 'none', display: 'inline-block' }}>下载模板</a>
        {canEdit && <label style={{ ...btnPri, cursor: 'pointer' }}>
          {busy ? '导入中…' : '上传填好的表'}
          <input type="file" accept=".xlsx" style={{ display: 'none' }} disabled={busy}
            onChange={e => upload(e.target.files[0])} />
        </label>}
      </div>
      {!canEdit && <div style={{ fontSize: 12, color: 'var(--amber)' }}>导入需「凭证归档·登记」权限，请联系管理员。</div>}
      {msg && <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--red)' }}>{msg}</div>}

      {rep && <div style={{ border: '1px solid var(--line)', borderRadius: 9, overflow: 'hidden' }}>
        <div style={{ padding: '10px 14px', background: 'var(--bg-sub)', borderBottom: '1px solid var(--line)', fontSize: 12.5 }}>
          导入完成：<b style={{ color: 'var(--green)' }}>成功 {rep.成功.length}</b> · <b style={{ color: rep.失败.length ? 'var(--red)' : 'var(--ink-3)' }}>失败 {rep.失败.length}</b>
          {rep.建位置数 > 0 && ` · 顺带建了 ${rep.建位置数} 个位置节点`}
        </div>
        {rep.失败.length > 0 && <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--red)', marginBottom: 6 }}>失败的行（改完这几行再重传即可，成功的不会重复）</div>
          {rep.失败.map((f, i) => <div key={i} style={{ fontSize: 12, color: 'var(--ink-2)', padding: '3px 0' }}>第 {f.行} 行：{f.原因}</div>)}
        </div>}
        {rep.成功.length > 0 && <div style={{ padding: '10px 14px', maxHeight: 200, overflowY: 'auto' }}>
          {rep.成功.map((s, i) => <div key={i} style={{ fontSize: 12, color: 'var(--ink-2)', padding: '2px 0' }}>
            <span style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 600 }}>{s.册号}</span> {s.位置 && `· ${s.位置}`}</div>)}
        </div>}
      </div>}
    </div>

    {/* 号段体检 */}
    <div style={{ ...card, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 13.5, fontWeight: 600 }}>号段体检 <span style={{ fontWeight: 400, color: 'var(--ink-3)', fontSize: 11.5 }}>找漏装订 / 丢册</span></div>
      <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.75 }}>
        金蝶该期间实际多少张凭证，系统拿它跟已登记册子的号段一比，把<b>没有任何册子覆盖到的号</b>列出来——通常就是漏装订或册子丢了。
        张数<b>可手工填</b>（你看金蝶或凭证封面数，眼见为实），也可从金蝶取。
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div><span style={lb}>主体</span><select style={{ ...inp, minWidth: 130 }} value={org} onChange={e => setOrg(e.target.value)}>
          {orgs.map(o => <option key={o.short_name} value={o.short_name}>{o.short_name}</option>)}</select></div>
        <div><span style={lb}>年</span><input style={{ ...inp, width: 74 }} value={year} onChange={e => setYear(e.target.value)} /></div>
        <div><span style={lb}>月</span><select style={{ ...inp, width: 66 }} value={month} onChange={e => setMonth(+e.target.value)}>{MONTHS.map(m => <option key={m} value={m}>{m}</option>)}</select></div>
        <div><span style={lb}>金蝶共几张</span><input style={{ ...inp, width: 90 }} value={total} onChange={e => setTotal(e.target.value)} placeholder="100" /></div>
        <button style={btnPri} onClick={doCheck}>体检</button>
      </div>

      {gaps && (gaps.covered_ok
        ? <div style={{ background: 'var(--green-bg)', border: '1px solid var(--green-line)', borderRadius: 9, padding: '11px 14px', fontSize: 12.5, color: 'var(--green)' }}>
          ✓ {org} {year}年{month}月共 {gaps.total} 张，已登记册子把 1–{gaps.total} 号全覆盖了，没有缺口。</div>
        : <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 9, padding: '12px 14px' }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--red)' }}>⚠ 发现 {gaps.gaps.length} 处缺口 —— 这些号没有任何册子收录</div>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 6, lineHeight: 1.7 }}>
            {gaps.gaps.map(([a, b], i) => <span key={i} style={{ fontFamily: 'ui-monospace,Consolas,monospace', fontWeight: 600, marginRight: 10 }}>第 {a === b ? a : `${a}–${b}`} 号</span>)}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 7 }}>通常意味着漏装订，或册子丢了。请核实后去「登记新册」补登记。</div>
        </div>)}
      {msg && !gaps && <div style={{ background: 'var(--red-bg)', border: '1px solid var(--red-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--red)' }}>{msg}</div>}
    </div>
  </div>
}

export default function Archive({ user }) {
  const [tab, setTab] = useState('find')
  const [orgs, setOrgs] = useState([])
  const [tick, setTick] = useState(0)
  const [toast, setToast] = useState('')
  const canEdit = user?.role === 'admin' || (user?.perms && user.perms.archive_edit)
  const canDestroy = user?.role === 'admin' || (user?.perms && user.perms.archive_destroy)
  useEffect(() => { archiveOrgs().then(r => setOrgs(r.orgs || [])).catch(() => {}); window.__arch_ok = m => { setToast(m); setTimeout(() => setToast(''), 2500) } }, [])

  const TABS = [['find', '找凭证'], ['new', '登记新册'], ['transfer', '位置与转移'], ['labels', '标签打印'], ['import', '批量导入 · 体检']]
  return <>
    <div className="head"><div>
      <div className="h-title">凭证归档</div>
      <div className="h-sub">按主体与凭证号定位册子 · 位置可批量转移 · 标签只印身份不印位置</div>
    </div>
      <div className="h-tools">
        <div style={{ display: 'inline-flex', border: '1px solid var(--line-strong)', borderRadius: 8, overflow: 'hidden', fontSize: 12.5 }}>
          {TABS.map(([k, t]) =>
            <span key={k} onClick={() => setTab(k)} style={{ padding: '7px 14px', cursor: 'pointer', fontWeight: tab === k ? 600 : 400, background: tab === k ? 'var(--accent)' : '#fff', color: tab === k ? '#fff' : 'var(--ink-2)' }}>{t}</span>)}
        </div>
      </div>
    </div>
    <div className="body">
      {toast && <div style={{ background: 'var(--green-bg)', border: '1px solid var(--green-line)', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: 'var(--green)' }}>✓ {toast}</div>}
      {!orgs.length && <div style={{ background: 'var(--amber-bg)', border: '1px solid var(--amber-line)', borderRadius: 8, padding: '11px 15px', fontSize: 12.5, color: 'var(--amber)', lineHeight: 1.7 }}>
        还没有可用主体。册号首段的简码来自「<b>基础数据 › 主体档案</b>」——请先给主体补上简码（如 SZL），才能登记与查找凭证册。</div>}
      {orgs.length > 0 && tab === 'find' && <Find orgs={orgs} key={tick} />}
      {orgs.length > 0 && tab === 'new' && <Register orgs={orgs} onDone={() => setTick(t => t + 1)} />}
      {tab === 'transfer' && <Transfer orgs={orgs} canEdit={canEdit} canDestroy={canDestroy} operator={user?.name} />}
      {orgs.length > 0 && tab === 'labels' && <Labels orgs={orgs} />}
      {orgs.length > 0 && tab === 'import' && <ImportCheck orgs={orgs} canEdit={canEdit} onImported={() => setTick(t => t + 1)} />}
    </div>
  </>
}
