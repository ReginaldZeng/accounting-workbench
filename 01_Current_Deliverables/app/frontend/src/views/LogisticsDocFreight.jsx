// [Change Log] Date:2026-09-28 Author:Claude Opus 4.8 Version:V2.639
// 单据运费：两 tab（销售出库 / 其他单据登记制）统一为同一套物料级字段。
// 一行 = 单据的一个物料行；运费按基本数量在单据内摊到物料，单位运费=摊得运费/基本数量，费比=运费/销售额（有则显）。
// 列：费用主体｜费用类型｜业务线｜单据号｜客户/需求部门｜物料编码｜物料名称｜基本单位数量｜基本单位｜运费｜单位运费｜费比。
import React, { useEffect, useState, useCallback } from 'react'
import { reviewRegisterAdd, reviewRegisterDelete, reviewRegisterKingdeeCheck, reviewDocFreight } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const qtyfmt = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 }))
const upfmt = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 4, maximumFractionDigits: 4 }))
const pctfmt = n => (n == null ? '—' : (Number(n) * 100).toFixed(2) + '%')
const SUBJECTS = ['深圳星期零', '深圳星期九', '孝感星期九']
const BLANK = { carrier: '货拉拉', doc_no: '', amount: '', subject: '孝感星期九', annot: '成品调拨单-电商', dept: '物流部', source: '', date: '' }

export default function LogisticsDocFreight({ cfg, onPeriod }) {
  const period = `${cfg.year}-${String(cfg.period).padStart(2, '0')}`
  const [tab, setTab] = useState('sales')
  const [data, setData] = useState(null)
  const [f, setF] = useState(BLANK)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    setData(null)
    reviewDocFreight(period, tab).then(setData).catch(() => setData({ rows: [], count: 0, total: 0, doc_count: 0 }))
  }, [period, tab])
  useEffect(() => { load() }, [load])
  const flash = t => { setMsg(t); setTimeout(() => setMsg(''), 6000) }
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))

  const add = () => {
    if (!f.carrier || f.amount === '') { flash('承运商、金额必填'); return }
    setBusy('add')
    reviewRegisterAdd({ ...f, period }).then(() => { setF({ ...BLANK, carrier: f.carrier, subject: f.subject }); load() })
      .catch(e => flash('登记失败：' + e.message)).finally(() => setBusy(''))
  }
  const del = id => { reviewRegisterDelete(id).then(load).catch(e => flash(e.message)) }
  const check = () => {
    setBusy('kd')
    reviewRegisterKingdeeCheck(period).then(r => { flash(`单号真实 ${r.real} / 查无 ${r.miss}（金蝶只读）`); load() })
      .catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }

  const rows = data && data.rows ? data.rows : []

  return (
    <div className="ldf">
      <style>{`
      .ldf{--ok:#2E7D57;--warn:#B06A12;--bad:#B23B2E;--neu:#5E6B78;--accent:#1F6E8C;--soft:#E1EEF3;font-size:14px}
      .ldf .head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
      .ldf .h-title{font-size:18px;font-weight:700}.ldf .h-sub{color:#5E6B78;font-size:12.5px;margin-top:2px}
      .ldf .tabs{display:flex;gap:10px;margin-bottom:12px}
      .ldf .tab{border:1px solid #DCE2E7;background:#fff;border-radius:10px;padding:12px 18px;cursor:pointer;text-align:left;min-width:220px}
      .ldf .tab.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset;background:#F5FAFC}
      .ldf .tab .t{font-weight:700;font-size:14px}.ldf .tab .s{font-size:11.5px;color:#8A96A2;margin-top:2px}
      .ldf .card{background:#fff;border:1px solid #DCE2E7;border-radius:12px;overflow:hidden;margin-bottom:12px}
      .ldf .card h3{margin:0;padding:11px 15px;font-size:13px;border-bottom:1px solid #DCE2E7;color:#5E6B78}
      .ldf .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px}
      @media(max-width:760px){.ldf .stats{grid-template-columns:1fr 1fr}}
      .ldf .stat{background:#fff;border:1px solid #DCE2E7;border-radius:12px;padding:13px 16px}
      .ldf .stat .v{font-family:ui-monospace,monospace;font-size:22px;font-weight:600}
      .ldf .stat .l{font-size:11.5px;color:#8A96A2;margin-top:3px}
      .ldf .stat.ok .v{color:var(--ok)}.ldf .stat.bad .v{color:var(--bad)}.ldf .stat.accent .v{color:var(--accent)}
      .ldf .form{display:grid;grid-template-columns:repeat(8,1fr);gap:8px;padding:13px 15px;align-items:end}
      @media(max-width:1100px){.ldf .form{grid-template-columns:repeat(4,1fr)}}
      @media(max-width:640px){.ldf .form{grid-template-columns:1fr 1fr}}
      .ldf label{font-size:11.5px;color:#5E6B78;display:flex;flex-direction:column;gap:3px}
      .ldf input,.ldf select{font:inherit;font-size:13px;padding:5px 8px;border:1px solid #DCE2E7;border-radius:6px;background:#fff}
      .ldf .btn{font-size:13px;padding:6px 14px;border-radius:7px;border:1px solid #DCE2E7;background:#fff;cursor:pointer}
      .ldf .btn.pri{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
      .ldf .btn[disabled]{opacity:.5;cursor:default}
      .ldf table{border-collapse:collapse;width:100%;font-size:13px}
      .ldf th,.ldf td{padding:8px 11px;text-align:left;border-bottom:1px solid #DCE2E7;white-space:nowrap}
      .ldf th{font-size:11px;color:#8A96A2;background:#F7F9F9}
      .ldf td.num,.ldf th.num{text-align:right;font-family:ui-monospace,monospace}
      .ldf tr.docstart td{border-top:2px solid #CBD5Dc}
      .ldf .tw{overflow-x:auto}
      .ldf .msg{background:#FEF7E6;border:1px solid #F0DCA8;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:10px;color:#5C4A00}
      .ldf .del{color:var(--bad);cursor:pointer;font-size:12px}
      .ldf .note{color:#8A96A2;font-size:11.5px}
      .ldf .empty{text-align:center;color:#8A96A2;padding:18px}
      `}</style>

      <div className="head">
        <div><div className="h-title">单据运费</div>
          <div className="h-sub">一行一个物料行 · 运费按基本数量摊到物料 · 销售出库走核价核量 / 其他单据走登记制</div></div>
        <div style={{ flex: 1 }} />
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
      </div>

      <div className="tabs">
        <button className={'tab' + (tab === 'sales' ? ' on' : '')} onClick={() => setTab('sales')}>
          <div className="t">销售出库</div><div className="s">销售出库单挂的运费 · 核价核量在「付款对账」</div></button>
        <button className={'tab' + (tab === 'other' ? ' on' : '')} onClick={() => setTab('other')}>
          <div className="t">其他单据（登记制）</div><div className="s">议价/报销/调拨 · 只登记＋轻核单号真实</div></button>
      </div>

      {msg && <div className="msg">{msg}</div>}

      <div className="stats">
        <div className="stat accent"><div className="v">{data ? data.doc_count : '—'}</div><div className="l">单据张数</div></div>
        <div className="stat accent"><div className="v">{data ? data.count : '—'}</div><div className="l">物料明细行</div></div>
        <div className="stat ok"><div className="v">{data ? money(data.total) : '—'}</div><div className="l">运费合计（元·含税）</div></div>
      </div>

      {tab === 'other' && (
        <div className="card">
          <h3>登记一笔单据运费（议价/报销：货拉拉、零星件、调拨）</h3>
          <div className="form">
            <label>承运商<input value={f.carrier} onChange={e => set('carrier', e.target.value)} /></label>
            <label>单据号（FBDR/CGRK…）<input value={f.doc_no} onChange={e => set('doc_no', e.target.value)} placeholder="多单用 +" /></label>
            <label>金额（含税）<input value={f.amount} onChange={e => set('amount', e.target.value)} placeholder="1918.42" /></label>
            <label>费用主体<select value={f.subject} onChange={e => set('subject', e.target.value)}>{SUBJECTS.map(s => <option key={s}>{s}</option>)}</select></label>
            <label>费用标注<input value={f.annot} onChange={e => set('annot', e.target.value)} /></label>
            <label>需求部门<input value={f.dept} onChange={e => set('dept', e.target.value)} /></label>
            <label>来源（钉钉审批号）<input value={f.source} onChange={e => set('source', e.target.value)} placeholder="202609231455…" /></label>
            <label>日期<input value={f.date} onChange={e => set('date', e.target.value)} placeholder="2026-08-29" /></label>
          </div>
          <div style={{ padding: '0 15px 13px', display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn pri" disabled={busy === 'add'} onClick={add}>登记</button>
            <button className="btn" disabled={busy === 'kd'} onClick={check}>{busy === 'kd' ? '金蝶查单号中…' : '接金蝶查单号真实性＋物料明细'}</button>
            <span className="note">议价/报销制无合同价目表、不按件重计费，故不核价核量；核的是单号真实＋需求部门＋审批。</span>
          </div>
        </div>
      )}

      <div className="card">
        <h3>{tab === 'sales' ? '销售出库单 · 物料级运费明细' : '其他单据 · 物料级运费明细'}
          {data ? `　${data.doc_count} 张单据 / ${data.count} 行 · 运费合计 ${money(data.total)} 元` : ''}</h3>
        <div className="tw"><table>
          <thead><tr>
            <th>费用主体</th><th>费用类型</th><th>业务线</th><th>单据号</th><th>客户/需求部门</th>
            <th>物料编码</th><th>物料名称</th><th className="num">基本单位数量</th><th>基本单位</th>
            <th className="num">运费</th><th className="num">单位运费</th><th className="num">费比</th>
            {tab === 'other' && <th></th>}
          </tr></thead>
          <tbody>
            {data === null && <tr><td colSpan="13" className="empty">加载中…（接金蝶取物料明细，可能稍慢）</td></tr>}
            {data && !rows.length && <tr><td colSpan="13" className="empty">
              {tab === 'sales'
                ? <>本月还没有销售出库单据运费。<br />销售出库的账单在「账单上传」传入、「付款对账·复核台」做核价核量后，在此按物料摊列。</>
                : <>本月还没有登记的其他单据运费。上方登记一笔（如货拉拉报销的 FBDR 运费），接金蝶取物料明细后在此按物料摊列。</>}
            </td></tr>}
            {rows.map((r, i) => {
              const first = i === 0 || rows[i - 1].doc_no !== r.doc_no
              return (
                <tr key={i} className={first ? 'docstart' : ''}>
                  <td>{r.subject}</td><td>{r.fee_item}</td><td>{r.bizline || <span className="note">—</span>}</td>
                  <td style={{ fontFamily: 'ui-monospace', fontSize: 12 }}>{first ? r.doc_no : ''}</td>
                  <td>{r.party || <span className="note">—</span>}</td>
                  <td style={{ fontFamily: 'ui-monospace', fontSize: 12 }}>{r.code || <span className="note">—</span>}</td>
                  <td>{r.name}</td>
                  <td className="num">{qtyfmt(r.baseqty)}</td><td>{r.baseunit || <span className="note">—</span>}</td>
                  <td className="num">{money(r.fee)}</td><td className="num">{upfmt(r.unitfee)}</td><td className="num">{pctfmt(r.ratio)}</td>
                  {tab === 'other' && <td>{first && r.reg_id ? <span className="del" onClick={() => del(r.reg_id)}>删除</span> : ''}</td>}
                </tr>
              )
            })}
          </tbody>
        </table></div>
      </div>
    </div>
  )
}
