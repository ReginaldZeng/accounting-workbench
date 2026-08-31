// [Change Log] Date:2026-07-14 Author:Claude/c V2.x 单据物流成本：金蝶单据端看每单的承运商/费用/单位费用(元/KG)，由对账结果派生
import React, { useState, useEffect } from 'react'
import { listReconParsers, runRecon } from '../api.js'

const money = n => (n == null ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const kgfmt = n => (n ? Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—')

export default function LogisticsCost({ user }) {
  const [file, setFile] = useState(null)
  const [rep, setRep] = useState(null)
  const [carrier, setCarrier] = useState('')
  const [parsers, setParsers] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ftype, setFtype] = useState('all')

  useEffect(() => {
    listReconParsers().then(r => {
      if (r.ok) { setParsers(r.supported || []); if (r.supported?.length) setCarrier(r.supported[0].carrier) }
    }).catch(() => {})
  }, [])

  const onFile = async e => {
    const f = e.target.files[0]; if (!f) return
    if (!carrier) { setErr('请先选择承运商'); return }
    setFile(f); setRep(null); setErr(''); setBusy(true)
    try { const r = await runRecon(carrier, f); r.ok ? setRep(r.report) : setErr(r.msg) }
    catch (x) { setErr('请求失败：' + x) } finally { setBusy(false) }
  }
  const exportCsv = () => {
    if (!rep) return
    const head = ['金蝶单号', '单据类型', '承运商', '往来', '数量', '重量KG', '费用', '元每KG']
    const csv = '﻿' + [head, ...rep.docs.map(d => [d.单号, d.单据类型, d.承运商, d.往来 || '', d.数量 ?? '', d.重量KG ?? '', d.费用 ?? '', d.元每KG ?? '缺重量'])]
      .map(a => a.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a'); a.href = url; a.download = `${carrier}_单据物流成本.csv`; a.click(); URL.revokeObjectURL(url)
  }

  const types = rep ? ['all', ...rep.summary.map(s => s.单据类型)] : ['all']
  const docs = rep ? (ftype === 'all' ? rep.docs : rep.docs.filter(d => d.单据类型 === ftype)) : []
  const tile = (label, val, unit, color) => (
    <div style={{ background: 'var(--bg-sub)', borderRadius: 9, padding: '12px 14px' }}>
      <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, marginTop: 2, color: color || 'var(--ink)' }}>{val}{unit && <span style={{ fontSize: 12, color: 'var(--ink-3)', fontWeight: 400 }}> {unit}</span>}</div>
    </div>
  )

  return (
    <>
      <div className="head"><div>
        <div className="h-title">单据物流成本</div>
        <div className="h-sub">金蝶单据端 · 每张单据的承运商 / 费用 / 单位费用（元/KG），由付款对账结果派生（只读）</div>
      </div></div>

      <div className="body">
        <div style={{ display: 'flex', gap: 10, alignItems: 'stretch', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center' }}>
            <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>承运商</span>
            <select value={carrier} onChange={e => setCarrier(e.target.value)} style={{ fontSize: 13, minWidth: 130 }}>
              {parsers.length === 0 && <option value="">加载中…</option>}
              {parsers.map(p => <option key={p.carrier} value={p.carrier}>{p.carrier}（{p.name} {p.version}）</option>)}
            </select>
          </div>
          <label style={{ flex: 1, minWidth: 220, display: 'flex', alignItems: 'center', gap: 12, border: '0.5px solid var(--line)', borderRadius: 12, padding: '12px 16px', cursor: 'pointer', background: 'var(--surface-2)' }}>
            <input type="file" accept=".xls,.xlsx" onChange={onFile} style={{ display: 'none' }} />
            <span style={{ fontSize: 20, color: 'var(--ink-3)' }}>⭳</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{file ? file.name : `上传 ${carrier || '所选承运商'} 账单，生成单据成本`}</div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 2 }}>{busy ? '直查金蝶、按重量摊费用中…' : '按所选承运商解析方案解析，直查金蝶并按单据摊分费用'}</div>
            </div>
          </label>
        </div>

        {err && <div style={{ padding: '10px 13px', borderRadius: 9, background: 'var(--red-bg)', color: 'var(--red)', fontSize: 13 }}>{err}</div>}

        {rep && <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 10 }}>
            {tile('金蝶单据', rep.total.单据数)}
            {tile('物流费用', '¥' + money(rep.total.费用))}
            {tile('计重重量', kgfmt(rep.total.KG), 'KG')}
            {tile('整体均价', rep.total.元每KG ?? '—', '元/KG', 'var(--accent)')}
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            {types.map(t => (
              <span key={t} onClick={() => setFtype(t)} style={{ fontSize: 12, padding: '5px 11px', borderRadius: 999, cursor: 'pointer',
                background: ftype === t ? 'var(--accent)' : 'transparent', color: ftype === t ? '#fff' : 'var(--ink-2)', border: ftype === t ? 0 : '0.5px solid var(--line)' }}>
                {t === 'all' ? `全部 ${rep.docs.length}` : `${t} ${rep.summary.find(s => s.单据类型 === t)?.单据数 || 0}`}
              </span>
            ))}
            <button onClick={exportCsv} style={{ marginLeft: 'auto', padding: '6px 12px', borderRadius: 8, fontSize: 12.5 }}>导出</button>
          </div>

          <div style={{ border: '0.5px solid var(--line)', borderRadius: 12, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              <thead><tr style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', textAlign: 'left' }}>
                {['金蝶单号', '单据类型', '承运商', '往来', '重量KG', '费用', '元/KG'].map((h, i) => <th key={h} style={{ padding: '9px 10px', fontWeight: 600, textAlign: i >= 4 ? 'right' : 'left' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {docs.map((d, i) => (
                  <tr key={i} style={{ borderTop: '0.5px solid var(--line)' }}>
                    <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{d.单号}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--ink-2)' }}>{d.单据类型}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--ink-2)' }}>{d.承运商}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 140, whiteSpace: 'nowrap' }}>{d.往来 || '—'}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>{kgfmt(d.重量KG)}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{money(d.费用)}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>{d.元每KG != null ? <span style={{ fontFamily: 'var(--font-mono)' }}>{d.元每KG}</span> : <span style={{ fontSize: 11, color: 'var(--amber)' }}>缺重量</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>费用按金蝶重量摊到每张单据，摊完＝账单合计无损。分步式调出单金蝶重量字段待补，元/KG 暂标「缺重量」。</div>
        </>}
      </div>
    </>
  )
}
