// [Change Log] Date:2026-09-28 Author:Claude Opus 4.8 Version:V2.637
// 单据运费 · 其他单据（登记制）：议价/报销制运费（货拉拉、零星件）不核价核量——只登记 单据号+费用+主体+需求部门，
// 轻核单号在金蝶是否真实。链盟调拨、货拉拉报销这类都归这里，跟走核价核量的销售出库分开。
import React, { useEffect, useState, useCallback } from 'react'
import { reviewRegisterList, reviewRegisterAdd, reviewRegisterDelete, reviewRegisterKingdeeCheck } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const SUBJECTS = ['深圳星期零', '深圳星期九', '孝感星期九']
const BLANK = { carrier: '货拉拉', doc_no: '', amount: '', subject: '孝感星期九', annot: '成品调拨单-电商', dept: '物流部', source: '', date: '' }

export default function LogisticsDocFreight({ cfg, onPeriod }) {
  const period = `${cfg.year}-${String(cfg.period).padStart(2, '0')}`
  const [d, setD] = useState(null)
  const [f, setF] = useState(BLANK)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => { reviewRegisterList(period).then(setD).catch(e => setMsg(e.message)) }, [period])
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
  const statePill = r => {
    if (r.qty_state === 'ok') return <span className="pill ok">已登记 · 单号真实</span>
    if (r.qty_state === 'miss') return <span className="pill bad">单号查无 · 待核</span>
    return <span className="pill neu">已登记 · 单号待核</span>
  }

  return (
    <div className="ldf">
      <style>{`
      .ldf{--ok:#2E7D57;--warn:#B06A12;--bad:#B23B2E;--neu:#5E6B78;--accent:#1F6E8C;font-size:14px}
      .ldf .head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
      .ldf .h-title{font-size:18px;font-weight:700}.ldf .h-sub{color:#5E6B78;font-size:12.5px;margin-top:2px}
      .ldf .card{background:#fff;border:1px solid #DCE2E7;border-radius:12px;overflow:hidden;margin-bottom:12px}
      .ldf .card h3{margin:0;padding:11px 15px;font-size:13px;border-bottom:1px solid #DCE2E7;color:#5E6B78}
      .ldf .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px}
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
      .ldf .tw{overflow-x:auto}
      .ldf .pill{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:999px}
      .ldf .pill.ok{background:#DCEFE4;color:var(--ok)}.ldf .pill.bad{background:#F8DDD8;color:var(--bad)}.ldf .pill.neu{background:#E7ECEF;color:var(--neu)}
      .ldf .msg{background:#FEF7E6;border:1px solid #F0DCA8;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:10px;color:#5C4A00}
      .ldf .del{color:var(--bad);cursor:pointer;font-size:12px}
      .ldf .note{color:#8A96A2;font-size:11.5px}
      `}</style>

      <div className="head">
        <div><div className="h-title">单据运费 · 其他单据（登记制）</div>
          <div className="h-sub">议价 / 报销制运费（货拉拉、零星件、调拨）不核价核量 —— 只登记 单据号＋费用＋主体＋需求部门，轻核单号在金蝶是否真实</div></div>
        <div style={{ flex: 1 }} />
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
      </div>

      <div className="stats">
        <div className="stat accent"><div className="v">{d ? d.count : '—'}</div><div className="l">登记笔数</div></div>
        <div className="stat accent"><div className="v">{d ? money(d.total) : '—'}</div><div className="l">费用合计（元·含税）</div></div>
        <div className="stat ok"><div className="v">{d ? d.doc_real : '—'}</div><div className="l">单号真实（金蝶查到）</div></div>
        <div className="stat bad"><div className="v">{d ? d.doc_miss : '—'}</div><div className="l">单号查无（待核）</div></div>
      </div>

      {msg && <div className="msg">{msg}</div>}

      <div className="card">
        <h3>登记一笔单据运费</h3>
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
          <button className="btn" disabled={busy === 'kd'} onClick={check}>{busy === 'kd' ? '金蝶查单号中…' : '接金蝶查单号真实性'}</button>
          <span className="note">议价/报销制无合同价目表、不按件重计费，故不核价核量；核的是单号真实＋需求部门＋审批。</span>
        </div>
      </div>

      <div className="card">
        <h3>本月其他单据 · 物流费用</h3>
        <div className="tw"><table>
          <thead><tr><th>单据号</th><th>承运商</th><th>费用标注</th><th>费用主体</th><th className="num">金额</th><th>需求部门 / 来源</th><th>核对状态</th><th></th></tr></thead>
          <tbody>
            {d && d.rows && d.rows.map(r =>
              <tr key={r.id}>
                <td style={{ fontFamily: 'ui-monospace', fontSize: 12 }}>{r.doc_no || <span className="note">无单号</span>}</td>
                <td>{r.carrier}</td><td>{r.annot}<div className="note">{r.fee_item}</div></td><td>{r.subject}</td>
                <td className="num">{money(r.amount)}</td><td className="note">{r.note}</td>
                <td>{statePill(r)}</td>
                <td><span className="del" onClick={() => del(r.id)}>删除</span></td>
              </tr>)}
            {d && d.rows && !d.rows.length && <tr><td colSpan="8" style={{ textAlign: 'center', color: '#8A96A2', padding: 16 }}>本月还没有登记的其他单据运费。上方登记一笔（如货拉拉报销的 FBDR 运费）。</td></tr>}
          </tbody>
        </table></div>
      </div>
    </div>
  )
}
