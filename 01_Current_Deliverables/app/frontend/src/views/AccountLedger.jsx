// [Change Log] Date:2026-07-03 Author:Claude/c Version:V1.1
// 账户台账：列重定义(主体/开户行·渠道/账号/稽核方案/状态/操作/来源) + 家底汇总带 + 手工标失效(金蝶不维护此步)
import React, { useEffect, useState } from 'react'
import { getLedger, syncLedger, setLedgerOverride } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

let _cache = null
const CATS = ['全部', '库存现金', '银行存款', '其它货币资金', '交易性金融资产']

export default function AccountLedger({ cfg, onPeriod }) {
  const [recs, setRecs] = useState(_cache), [ent, setEnt] = useState('all'), [cat, setCat] = useState('全部')
  const [onlyNew, setOnlyNew] = useState(false), [incl, setIncl] = useState(false)
  const [busy, setBusy] = useState(false), [msg, setMsg] = useState(''), [err, setErr] = useState('')

  const load = () => getLedger().then(x => {
    _cache = x.records; setRecs(x.records)
    const kd = x.source === 'kingdee' || String(x.source).includes('金蝶')
    setMsg(`当前 ${x.records.length} 户 · 生效 ${x.records.filter(r => r['状态'] === '生效').length} · 源：${kd ? '金蝶' : '样例'}`)
  }).catch(() => {})
  useEffect(() => { load() }, [cfg.source, cfg.year, cfg.period])

  const sync = async () => {
    setBusy(true); setErr(''); setMsg('同步中…')
    try { const x = await syncLedger(); if (x.error) { setErr(x.error); setMsg('同步失败') } else { await load() } }
    finally { setBusy(false) }
  }
  const toggleOff = async (账号, 失效) => {
    if (!账号) return
    await setLedgerOverride({ 账号, 失效 }); await load()
    setMsg(`已${失效 ? '标记失效' : '恢复生效'} · ${账号}` + (失效 ? '（勾选"含失效/已销户"可查看或恢复）' : ''))
  }
  const setScheme = async (账号, 稽核方案) => {
    if (!账号) return
    await setLedgerOverride({ 账号, 稽核方案 }); await load()
    setMsg(`已将 ${账号} 稽核方案改为「${稽核方案}」` + (稽核方案 === '余额' ? '：不走逐笔，只需 金蝶余额 = 银行余额' : '：走逐笔明细稽核'))
  }
  if (!recs) return <div className="loading">加载中…</div>
  const ents = ['all', ...[...new Set(recs.map(r => r['主体']))]]
  const rows = recs.filter(r => (ent === 'all' || r['主体'] === ent) && (cat === '全部' || r['科目大类'] === cat) && (!onlyNew || r['本月新增']) && (incl || r['状态'] === '生效'))

  return (<div>
    <div className="head">
      <div><div className="h-title">账户台账</div>
        <div className="h-sub">银行对账的匹配地基，从金蝶自动同步。稽核方案可点击调整（明细=走逐笔稽核 / 余额=只需金蝶余额对上银行余额）；失效需手工维护（金蝶不维护此步）</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
        <button className="btn btn-pri" onClick={sync} disabled={busy}>{busy ? '同步中…' : '从金蝶同步'}</button></div>
    </div>
    <div className="body">
      {err && <div className="banner err">金蝶同步失败：{err}</div>}
      <div className="foot">{msg}</div>
      {/* 台账家底汇总带（只有"银行账户"进匹配桥） */}
      <div className="kpis">
        {[['账户总数', recs.length, 'var(--ink-3)'],
          ['银行账户', recs.filter(r => r['类别'] === '银行账户').length, 'var(--blue)'],
          ['理财产品', recs.filter(r => r['类别'] === '理财产品').length, 'var(--violet)'],
          ['电商渠道', recs.filter(r => r['类别'] === '电商渠道').length, 'var(--amber)'],
          ['主体', new Set(recs.map(r => r['主体'])).size, 'var(--green)'],
          ['生效', recs.filter(r => r['状态'] === '生效').length, 'var(--green)']].map(([l, v, c]) =>
          <div className="kpi" key={l}><div className="kl"><span className="dot" style={{ background: c }} />{l}{l === '银行账户' ? ' · 进匹配桥' : ''}</div><div className="kv">{v}</div></div>)}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span className="flabel">主体</span>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>{ents.map(e => <button key={e} className={'chip' + (ent === e ? ' active' : '')} onClick={() => setEnt(e)}>{e === 'all' ? '全部' : e}</button>)}</div></div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span className="flabel">科目</span>
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>{CATS.map(c => <button key={c} className={'chip' + (cat === c ? ' active' : '')} onClick={() => setCat(c)}>{c}</button>)}</div></div>
        <div style={{ display: 'flex', gap: 16 }}>
          <label className="ck"><input type="checkbox" checked={onlyNew} onChange={e => setOnlyNew(e.target.checked)} /> <span style={{ color: 'var(--red)' }}>*</span>本月新增</label>
          <label className="ck"><input type="checkbox" checked={incl} onChange={e => setIncl(e.target.checked)} /> 含失效/已销户</label></div>
      </div>
      <div className="tbl-wrap"><table style={{ minWidth: 900 }}>
        <thead><tr>{['主体', '开户行/渠道', '账号', '稽核方案', '状态', '操作', '来源'].map(h => <th className="th" key={h}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => {
          const st = r['状态'] || (r['_active'] ? '生效' : '已销户')
          const stCls = st === '生效' ? { background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-line)' }
            : st === '失效' ? { background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }
              : { background: 'var(--gray-bg)', color: 'var(--ink-3)' }
          const scheme = r['稽核方案'] || (r['类别'] === '银行账户' ? '明细' : '余额')
          return <tr key={i} style={st === '生效' ? null : { opacity: .7 }}>
            <td>{r['主体']}</td>
            <td>{r['开户行'] || '—'}</td>
            <td><span className="acct">{r['账号'] || '—'}</span>{r['本月新增'] && <span className="newtag">*New</span>}</td>
            <td>{r['账号']
              ? <span className={'tag ' + (scheme === '明细' ? 'kd' : 'unmap')} style={{ cursor: 'pointer' }} title="点击切换：明细(走逐笔稽核) / 余额(只需金蝶余额=银行余额)" onClick={() => setScheme(r['账号'], scheme === '明细' ? '余额' : '明细')}>{scheme} ⇄{r['稽核方案_手工'] ? <span style={{ color: 'var(--amber)' }}> *</span> : ''}</span>
              : <span className={'tag ' + (scheme === '明细' ? 'kd' : 'unmap')}>{scheme}</span>}</td>
            <td><span className="badge" style={stCls}>{st}</span></td>
            <td>{st === '已销户'
              ? <span className="muted">—</span>
              : st === '失效'
                ? <span className="lk" onClick={() => toggleOff(r['账号'], false)}>恢复生效</span>
                : <span className="lk" style={{ color: 'var(--red)' }} onClick={() => toggleOff(r['账号'], true)}>标记失效</span>}</td>
            <td className="muted">{r['来源'] || '金蝶同步'}</td>
          </tr>
        })}{rows.length === 0 && <tr><td colSpan="7" className="muted">无匹配账户。</td></tr>}</tbody>
      </table></div>
    </div>
  </div>)
}
