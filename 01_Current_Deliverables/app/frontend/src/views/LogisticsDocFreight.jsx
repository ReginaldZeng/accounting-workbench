// [Change Log] Date:2026-09-28 Author:Claude Opus 4.8 Version:V2.645
// 单据运费：两 tab（销售出库 / 其他单据登记制）统一为同一套物料级字段。
// 一行 = 单据的一个物料行；运费按基本数量在单据内摊到物料，单位运费=摊得运费/基本数量，费比=运费/销售额（有则显）。
// 列：费用主体｜费用类型｜业务线｜单据号｜客户/需求部门｜物料编码｜物料名称｜基本单位数量｜基本单位｜运费｜单位运费｜费比。
// V2.773：第一个 tab 从「销售出库」改成「账单复核（已登记）」——某家某月在复核台登记已复核后，逐单运费自动进来(所有单据类型)；
//   可按承运商/费用类型筛；还没登记复核的承运商在上方提示。分组/勾选按「承运商+单号」(同一张单可能有两家的费用)。
// V2.774：最前面加「销售出库单」页签(LogisticsOutstockFreight)＝金蝶本月全部销售出库单 × 运费，剔除内部交易；原页签改名「账单复核明细（已登记）」。
import React, { useEffect, useState, useCallback, useRef } from 'react'
import { reviewRegisterAdd, reviewRegisterDelete, reviewRegisterKingdeeCheck, reviewDocFreight, reviewRegisterImport, reviewRegisterTemplateUrl } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'
import LogisticsOutstockFreight from './LogisticsOutstockFreight.jsx'   // 销售出库单全量 × 运费(V2.774)

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const qtyfmt = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 }))
const upfmt = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 4, maximumFractionDigits: 4 }))
const pctfmt = n => (n == null ? '—' : (Number(n) * 100).toFixed(2) + '%')
const SUBJECTS = ['深圳星期零', '深圳星期九', '孝感星期九']
const BLANK = { carrier: '货拉拉', doc_no: '', amount: '', subject: '孝感星期九', annot: '成品调拨单-电商', dept: '物流部', source: '', date: '' }

export default function LogisticsDocFreight({ cfg, onPeriod }) {
  const period = `${cfg.year}-${String(cfg.period).padStart(2, '0')}`
  const [tab, setTab] = useState('out')         // out 销售出库单(全量) / sales 账单复核明细(已登记) / other 其他单据(登记制)
  const [data, setData] = useState(null)
  const [f, setF] = useState(BLANK)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [sel, setSel] = useState(() => new Set())
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [fc, setFc] = useState('')        // 承运商筛选(账单复核 tab)
  const [ff, setFf] = useState('')        // 费用类型筛选
  const fileRef = useRef(null)

  const load = useCallback(() => {
    if (tab === 'out') return                    // 销售出库单页签自己取数
    setData(null); setSel(new Set())
    reviewDocFreight(period, tab, q, page, tab === 'sales' ? fc : '', tab === 'sales' ? ff : '').then(setData).catch(() => setData({ rows: [], count: 0, total: 0, doc_count: 0, pages: 1 }))
  }, [period, tab, q, page, fc, ff])
  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1) }, [tab, period, q, fc, ff])
  useEffect(() => { setFc(''); setFf('') }, [period])
  const flash = t => { setMsg(t); setTimeout(() => setMsg(''), 6000) }
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))

  const add = () => {
    if (!f.carrier || f.amount === '') { flash('承运商、金额必填'); return }
    setBusy('add')
    reviewRegisterAdd({ ...f, period }).then(() => { setF({ ...BLANK, carrier: f.carrier, subject: f.subject }); load() })
      .catch(e => flash('登记失败：' + e.message)).finally(() => setBusy(''))
  }
  const del = id => { reviewRegisterDelete(id).then(load).catch(e => flash(e.message)) }
  const doImport = e => {
    const file = e.target.files && e.target.files[0]
    if (fileRef.current) fileRef.current.value = ''
    if (!file) return
    setBusy('imp')
    reviewRegisterImport(period, file)
      .then(r => { flash(`导入完成：新增 ${r.added} 笔${r.skipped ? ` · 跳过 ${r.skipped}` : ''}${r.errs && r.errs.length ? '（' + r.errs.join('；') + '）' : ''}`); load() })
      .catch(e => flash('导入失败：' + e.message)).finally(() => setBusy(''))
  }
  const check = () => {
    setBusy('kd')
    reviewRegisterKingdeeCheck(period).then(r => { flash(`单号真实 ${r.real} / 查无 ${r.miss}（金蝶只读）`); load() })
      .catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }

  const rows = (data && data.rows ? data.rows : []).map(r => ({ ...r, dk: r.key || r.doc_no }))   // dk=分组键(承运商+单号)
  const docNos = [...new Set(rows.map(r => r.dk))]
  const toggle = no => setSel(p => { const n = new Set(p); n.has(no) ? n.delete(no) : n.add(no); return n })
  const toggleAll = () => setSel(p => p.size === docNos.length ? new Set() : new Set(docNos))
  // 勾选汇总：整单运费合计、基本单位数量合计（同单位才可加总）、平均单位运费=运费/数量
  const picked = rows.filter(r => sel.has(r.dk))
  const selFee = picked.reduce((s, r) => s + (r.fee || 0), 0)
  const selQty = picked.reduce((s, r) => s + (Number(r.baseqty) || 0), 0)
  const selUnits = [...new Set(picked.map(r => r.baseunit).filter(Boolean))]
  const selUnit = selUnits.length === 1 ? selUnits[0] : (selUnits.length ? '多种单位' : '')
  const avgUnitFee = selQty ? selFee / selQty : null

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
      .ldf td[rowspan]{vertical-align:middle;background:#FBFCFD}
      .ldf tr.band td{background:#F6F9FA}.ldf tr.band td[rowspan]{background:#EEF4F6}
      .ldf td.docno .dn{font-family:ui-monospace,monospace;font-size:12px;font-weight:600}
      .ldf td.docno .dnsub{display:block;font-size:10.5px;color:#8A96A2;margin-top:2px}
      .ldf tr.picked td{background:#EAF4EE !important}
      .ldf .selbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 15px;background:#EAF4EE;border-bottom:1px solid #C6E2D2;font-size:13px;color:#2E5544}
      .ldf .selbar b{font-family:ui-monospace,monospace;font-size:14px;color:#1E7A4C}
      .ldf .selbar .sb-n{font-weight:700}.ldf .selbar .sb-sep{color:#A8C5B6}
      .ldf .qbox{font:inherit;font-size:13px;padding:4px 9px;border:1px solid #DCE2E7;border-radius:6px;width:150px}
      .ldf .pager{display:flex;align-items:center;justify-content:center;gap:12px;padding:12px 15px;border-top:1px solid #DCE2E7}
      .ldf .pager .pg{font-size:12.5px;color:#5E6B78}
      .ldf .tw{overflow-x:auto}
      .ldf .msg{background:#FEF7E6;border:1px solid #F0DCA8;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:10px;color:#5C4A00}
      .ldf .del{color:var(--bad);cursor:pointer;font-size:12px}
      .ldf .note{color:#8A96A2;font-size:11.5px}
      .ldf .empty{text-align:center;color:#8A96A2;padding:18px}
      `}</style>

      <div className="head">
        <div><div className="h-title">单据运费</div>
          <div className="h-sub">一行一个物料行 · 运费按基本数量摊到物料 · 账单在复核台登记已复核后自动进来 / 没有账单的走登记制</div></div>
        <div style={{ flex: 1 }} />
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
      </div>

      <div className="tabs">
        <button className={'tab' + (tab === 'out' ? ' on' : '')} onClick={() => setTab('out')}>
          <div className="t">销售出库单</div><div className="s">本月全部销售出库单、退货单（剔除内部交易）· 每张单的运费</div></button>
        <button className={'tab' + (tab === 'sales' ? ' on' : '')} onClick={() => setTab('sales')}>
          <div className="t">账单复核明细（已登记）</div><div className="s">已登记复核的账单 · 所有单据类型 · 摊到物料</div></button>
        <button className={'tab' + (tab === 'other' ? ' on' : '')} onClick={() => setTab('other')}>
          <div className="t">其他单据（登记制）</div><div className="s">议价/报销/调拨 · 只登记＋轻核单号真实</div></button>
      </div>

      {msg && <div className="msg">{msg}</div>}

      {tab === 'out' && <LogisticsOutstockFreight period={period} />}

      {tab === 'sales' && data && data.facets && (
        <div className="card" style={{ padding: '10px 14px', fontSize: 13 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ color: '#5E6B78' }}>已登记复核：</span>
            {data.facets.carriers.length === 0 && <span style={{ color: '#8A96A2' }}>本月还没有承运商登记已复核</span>}
            <button className={'btn' + (!fc ? ' pri' : '')} onClick={() => setFc('')}>全部</button>
            {data.facets.carriers.map(x => <button key={x.carrier} className={'btn' + (fc === x.carrier ? ' pri' : '')} title={`${x.by} ${x.at} 登记已复核`}
              onClick={() => setFc(fc === x.carrier ? '' : x.carrier)}>{x.carrier} <b style={{ fontWeight: 600 }}>{money(x.amount)}</b></button>)}
            <span style={{ flex: 1 }} />
            <select value={ff} onChange={e => setFf(e.target.value)} style={{ padding: '5px 8px', border: '1px solid #DCE2E7', borderRadius: 6 }}>
              <option value="">全部费用类型</option>{data.facets.fees.map(([k, v]) => <option key={k} value={k}>{k}（{money(v)}）</option>)}</select>
          </div>
          {data.pending && data.pending.length > 0 && <div style={{ marginTop: 8, color: 'var(--warn)' }}>
            还没登记复核、暂时没进来的：{data.pending.map(x => `${x.carrier} ${money(x.amount)}`).join('　·　')}
            <span style={{ color: '#8A96A2' }}>　到「账单核对」第③步登记已复核后自动进来</span></div>}
          {data.nodoc && data.nodoc.n > 0 && <div style={{ marginTop: 6, color: '#5E6B78' }}>
            另有 {data.nodoc.n} 笔没有金蝶单号的费用 {money(data.nodoc.amount)}（仓储费、账单调整等），到不了单据，不在下表。</div>}
        </div>
      )}

      {tab !== 'out' && <div className="stats">
        <div className="stat accent"><div className="v">{data ? data.doc_count : '—'}</div><div className="l">单据张数</div></div>
        <div className="stat accent"><div className="v">{data ? data.count : '—'}</div><div className="l">物料明细行</div></div>
        <div className="stat ok"><div className="v">{data ? money(data.total) : '—'}</div><div className="l">运费合计（元·含税）</div></div>
      </div>}

      {tab === 'other' && (
        <div className="card">
          <h3 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span>登记单据运费（议价/报销：货拉拉、零星件、调拨）</span>
            <span style={{ flex: 1 }} />
            <a className="btn" style={{ textDecoration: 'none' }} href={reviewRegisterTemplateUrl()}>下载导入模板</a>
            <button className="btn pri" disabled={busy === 'imp'} onClick={() => fileRef.current && fileRef.current.click()}>
              {busy === 'imp' ? '导入中…' : '批量导入 Excel'}</button>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={doImport} />
          </h3>
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

      {tab !== 'out' && <div className="card">
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span>{tab === 'sales' ? '已复核账单 · 物料级运费明细' : '其他单据 · 物料级运费明细'}
            {data ? `　${data.doc_count} 张单据 · 运费合计 ${money(data.total)} 元` : ''}</span>
          <span style={{ flex: 1 }} />
          <input className="qbox" value={qInput} placeholder="搜单据号…" onChange={e => setQInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') setQ(qInput.trim()) }} />
          <button className="btn" onClick={() => setQ(qInput.trim())}>搜索</button>
          {q && <button className="btn" onClick={() => { setQInput(''); setQ('') }}>清除</button>}
        </h3>
        {sel.size > 0 && (
          <div className="selbar">
            <span className="sb-n">已勾选 {sel.size} 张单</span>
            <span className="sb-sep">·</span>
            <span>运费合计 <b>{money(selFee)}</b> 元</span>
            <span className="sb-sep">·</span>
            <span>基本单位数量 <b>{qtyfmt(selQty)}</b> {selUnit}</span>
            <span className="sb-sep">·</span>
            <span>平均单位运费 <b>{avgUnitFee == null ? '—' : upfmt(avgUnitFee)}</b> 元/{selUnit || '单位'}</span>
            <span className="sb-sep">·</span>
            <span>平均每票 <b>{sel.size ? money(selFee / sel.size) : '—'}</b> 元/票</span>
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={() => setSel(new Set())}>清空勾选</button>
          </div>
        )}
        <div className="tw"><table>
          <thead><tr>
            <th style={{ width: 30, textAlign: 'center' }}>
              <input type="checkbox" checked={docNos.length > 0 && sel.size === docNos.length}
                ref={el => { if (el) el.indeterminate = sel.size > 0 && sel.size < docNos.length }}
                onChange={toggleAll} title="全选/全不选" /></th>
            <th>费用主体</th><th>承运商</th><th>费用类型</th><th>业务线</th><th>单据号</th><th>客户/需求部门</th>
            <th>物料编码</th><th>物料名称</th><th className="num">基本单位数量</th><th>基本单位</th>
            <th className="num">运费</th><th className="num">单位运费</th><th className="num">销售额</th><th className="num">费比</th>
            {tab === 'other' && <th></th>}
          </tr></thead>
          <tbody>
            {data === null && <tr><td colSpan="16" className="empty">加载中…（接金蝶取物料明细，可能稍慢）</td></tr>}
            {data && !rows.length && <tr><td colSpan="16" className="empty">
              {tab === 'sales'
                ? <>本月还没有登记已复核的账单。<br />各家账单在「账单核对·复核台」核完，第③步点「确认通过并登记已复核」后，逐单运费自动在此按物料摊列。</>
                : <>本月还没有登记的其他单据运费。上方登记一笔（如货拉拉报销的 FBDR 运费），接金蝶取物料明细后在此按物料摊列。</>}
            </td></tr>}
            {rows.map((r, i) => {
              const first = i === 0 || rows[i - 1].dk !== r.dk
              // 该单据跨几行（物料行数）＋整单运费合计，用于合并单元格显示「这是同一张单」
              let span = 1, docFee = r.fee || 0
              if (first) { for (let k = i + 1; k < rows.length && rows[k].dk === r.dk; k++) { span++; docFee += rows[k].fee || 0 } }
              // 交替底色按单据分组
              let gi = 0; for (let k = 1; k <= i; k++) { if (rows[k].dk !== rows[k - 1].dk) gi++ }
              const band = gi % 2 === 1 ? ' band' : ''
              const multi = span > 1
              return (
                <tr key={i} className={(first ? 'docstart' : '') + band + (sel.has(r.dk) ? ' picked' : '')}>
                  {first && <td rowSpan={span} style={{ textAlign: 'center' }}>
                    <input type="checkbox" checked={sel.has(r.dk)} onChange={() => toggle(r.dk)} /></td>}
                  {first && <>
                    <td rowSpan={span}>{r.subject}</td>
                    <td rowSpan={span}>{r.carrier || <span className="note">—</span>}</td>
                    <td rowSpan={span}>{r.fee_type || r.fee_item}{r.fee_type && r.fee_item && r.fee_item !== r.fee_type && <div style={{ fontSize: 11.5, color: '#8A96A2' }}>{r.fee_item}</div>}</td>
                    <td rowSpan={span}>{r.bizline || <span className="note">—</span>}</td>
                    <td rowSpan={span} className="docno">
                      <span className="dn">{r.doc_no}</span>
                      {multi && <span className="dnsub">共 {span} 个物料 · 整单 {money(docFee)}</span>}
                    </td>
                  </>}
                  {tab === 'other'
                    ? (first && <td rowSpan={span}>{r.party || <span className="note">—</span>}</td>)
                    : <td>{r.party || <span className="note">—</span>}</td>}
                  <td style={{ fontFamily: 'ui-monospace', fontSize: 12 }}>{r.code || <span className="note">—</span>}</td>
                  <td>{r.name}</td>
                  <td className="num">{qtyfmt(r.baseqty)}</td><td>{r.baseunit || <span className="note">—</span>}</td>
                  <td className="num">{money(r.fee)}</td><td className="num">{upfmt(r.unitfee)}</td><td className="num">{money(r.sales)}</td><td className="num">{pctfmt(r.ratio)}</td>
                  {tab === 'other' && (first && <td rowSpan={span}>{r.reg_id ? <span className="del" onClick={() => del(r.reg_id)}>删除</span> : ''}</td>)}
                </tr>
              )
            })}
          </tbody>
        </table></div>
        {data && data.pages > 1 && (
          <div className="pager">
            <button className="btn" disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>上一页</button>
            <span className="pg">第 {page} / {data.pages} 页（共 {data.doc_count} 张单，每页 {data.size} 张）</span>
            <button className="btn" disabled={page >= data.pages} onClick={() => setPage(p => Math.min(data.pages, p + 1))}>下一页</button>
          </div>
        )}
      </div>}
    </div>
  )
}
