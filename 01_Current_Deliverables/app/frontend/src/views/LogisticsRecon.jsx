// [Change Log] Date:2026-07-14 Author:Claude/c V2.x 物流对账·付款对账（二期）：按回填单号直查金蝶、对账组核量、差异导出
import React, { useState, useEffect, useRef } from 'react'
import { listReconCarriers, parseRecon, runRecon } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const STEPS = ['账单导入', '金蝶直查', '比对结果', '差异导出']
// 状态 → 语义色（用应用 CSS 变量，深色自适应）
const STC = {
  '核对一致': ['--green-bg', '--green'], '单号命中': ['--blue-bg', '--blue'], '数量不符': ['--red-bg', '--red'],
  '单号查无': ['--amber-bg', '--amber'], '部分查无': ['--amber-bg', '--amber'],
  '待核·金蝶无数量': ['--blue-bg', '--blue'], '待人工配对': ['--gray-bg', '--gray'],
  // 物料级核量（V2.152，天鹰等按重量计费）
  '需人工复核': ['--red-bg', '--red'], 'ERP非kg计量·重量无法核': ['--blue-bg', '--blue'],
  '金蝶无此物料': ['--amber-bg', '--amber'],
  // 少报＝承运商少收我方钱，灰底弱化：标记留痕、不抢复核注意力（V2.154 业务方定）
  '账单少报·我方有利': ['--gray-bg', '--gray'],
}
const stColor = s => STC[s] || ['--gray-bg', '--gray']
const money = n => (n == null ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))

export default function LogisticsRecon({ user, cfg, onPeriod }) {
  const [file, setFile] = useState(null)
  const [parsed, setParsed] = useState(null)
  const [recon, setRecon] = useState(null)
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [filt, setFilt] = useState('all')
  const [mfilt, setMfilt] = useState('review')       // 物料级默认只看「需人工复核」——差异清单是给人复核用的
  const [carriers, setCarriers] = useState(null)
  const [carrier, setCarrier] = useState('')
  const [doneCarrier, setDoneCarrier] = useState('')
  const fileRef = useRef(null)
  const year = cfg?.year, period = cfg?.period

  useEffect(() => {                                  // 换账期 → 承运商列表与已对账结果全部重置
    setCarriers(null); setRecon(null); setParsed(null); setDoneCarrier(''); setCarrier(''); setFile(null); setStep(0); setErr('')
    listReconCarriers(year, period).then(r => { if (r.ok) setCarriers(r.carriers || []) }).catch(() => setCarriers([]))
  }, [year, period])

  const pickCarrier = row => {
    if (!row.parser) return
    setCarrier(row.parser); setErr(''); setParsed(null); setRecon(null); setFile(null)
    // 天鹰等 multi 方案允许一次选多个账单文件（每月拆多份，文件数不定）；单文件方案只收一个
    if (fileRef.current) { fileRef.current.multiple = !!row.multi; fileRef.current.value = ''; fileRef.current.click() }
  }
  const onFile = async e => {                        // 选完文件：自动解析 + 对账，直落结果屏
    const fs = Array.from(e.target.files || []); if (!fs.length || !carrier) return
    setFile(fs); setParsed(null); setRecon(null); setErr(''); setBusy('parse')
    try {
      const pr = await parseRecon(carrier, fs)
      if (!pr.ok) { setErr(pr.msg); setBusy(''); return }
      setParsed(pr); setBusy('run'); setStep(1)
      const rr = await runRecon(carrier, fs)
      if (rr.ok) { setRecon(rr); setDoneCarrier(carrier); setStep(2) }
      else { setErr(rr.msg); setStep(0) }
    } catch (x) { setErr('请求失败：' + x); setStep(0) } finally { setBusy('') }
  }
  const curRow = (carriers || []).find(c => c.parser === carrier)
  const exportCsv = () => {
    if (!recon) return
    const head = ['状态', '账单行', '主体', '寄件人', 'KY运单号', '金蝶单号', '客户/往来', '货物', '类型', '金蝶重量KG', '计费重量', '毛重净重比', '每公斤费用', '账单金额']
    const lines = recon.rows.map(r => [r.state, r.lines, r.主体 || '', r.寄件人 || '', r.运单号 || '', r.nos, r.客户 || '', r.货物 || '', r.类型 || '', r.kd_kg ?? '', r.bill_wt ?? '', r.gn_ratio ?? '', r.per_kg ?? '', r.billed ?? ''])
    const csv = '﻿' + [head, ...lines].map(a => a.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a'); a.href = url; a.download = `${recon.carrier}_差异清单.csv`; a.click(); URL.revokeObjectURL(url)
  }

  const rows = recon ? (filt === 'all' ? recon.rows
    : filt === 'diff' ? recon.rows.filter(r => ['数量不符', '单号查无', '部分查无'].includes(r.state))
      : recon.rows.filter(r => r.state === '待人工配对')) : []

  // ---- 物料级核量（天鹰等按重量计费：单号×物料编码 比 kg，金蝶为准）----
  const mat = recon?.matrecon || null
  const mpick = !mat ? [] : (
    mfilt === 'all' ? mat.rows
      : mfilt === 'review' ? mat.rows.filter(r => r.state === '需人工复核')
        : mfilt === 'under' ? mat.rows.filter(r => r.state === '账单少报·我方有利')
          : mfilt === 'unit' ? mat.rows.filter(r => r.state === 'ERP非kg计量·重量无法核')
            : mat.rows.filter(r => ['单号查无', '金蝶无此物料', '待人工配对'].includes(r.state)))
  // 多报＝多付运费的钱，永远置顶；其次按差异绝对值降序
  const mrows = [...mpick].sort((a, b) =>
    (a.方向 === '账单多报' ? 0 : 1) - (b.方向 === '账单多报' ? 0 : 1)
    || Math.abs(b.差异kg || 0) - Math.abs(a.差异kg || 0))
  const mOver = mat ? mat.rows.filter(r => r.方向 === '账单多报') : []
  const mUnder = mat ? mat.rows.filter(r => r.state === '账单少报·我方有利') : []
  const mUnderKg = mUnder.reduce((s, r) => s + Math.abs(r.差异kg || 0), 0)
  const exportMatCsv = () => {
    if (!mat) return
    const head = ['状态', '金蝶单号', '单据类型', '物料编码', '物料', '基本单位', '账单kg', '金蝶kg', '差异kg', '方向', '账单金额', '账单行号']
    const lines = mat.rows.map(r => [r.state, r.单号, r.单据类型, r.物料编码, r.物料, r.基本单位,
      r.账单kg ?? '', r.金蝶kg ?? '', r.差异kg ?? '', r.方向, r.账单金额 ?? '', r.行号])
    const csv = '﻿' + [head, ...lines].map(a => a.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a'); a.href = url; a.download = `${recon.carrier}_物料级核量清单.csv`; a.click(); URL.revokeObjectURL(url)
  }

  const chip = (bg, fg, t, active, on) => (
    <span onClick={on} style={{ fontSize: 12, padding: '5px 11px', borderRadius: 999, cursor: on ? 'pointer' : 'default',
      background: active ? 'var(--accent)' : (bg ? `var(${bg})` : 'transparent'),
      color: active ? '#fff' : (fg ? `var(${fg})` : 'var(--ink-2)'), border: bg || active ? 0 : '0.5px solid var(--line)' }}>{t}</span>
  )

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">付款对账</div>
          <div className="h-sub">物流对账 · 按物流部门回填的金蝶单据号直查金蝶，逐行核价核量（只读）</div>
        </div>
        <PeriodPicker year={cfg?.year} period={cfg?.period} onChange={onPeriod} status={cfg?.['数据状态']} />
      </div>

      <div className="body">
        <div className="steps">
          {STEPS.map((s, i) => (
            <div key={s} className={'step' + (i === step ? ' cur' : '')} onClick={() => (i < 2 || recon) && setStep(i)} style={{ cursor: 'pointer' }}>
              <span className="num" style={{ background: i < step || (i < 2 && parsed) ? 'var(--green-bg)' : i === step ? 'var(--accent)' : 'var(--gray-bg)', color: i < step ? 'var(--green)' : i === step ? '#fff' : 'var(--ink-3)' }}>{i < step ? '✓' : i + 1}</span>
              <div><div className="sn">{s}</div><div className="sd">{['上传账单', '直查金蝶单据', '对账组核量', '差异清单'][i]}</div></div>
            </div>
          ))}
        </div>

        {err && <div style={{ padding: '10px 13px', borderRadius: 9, background: 'var(--red-bg)', color: 'var(--red)', fontSize: 13 }}>{err}</div>}

        {/* 解析提示：跳过的重复表、账单自身不自洽等。不阻断解析，但绝不能不告诉人——
            重复表若静默算两遍，金额与重量双双翻倍而勾稽照样"通过"（合计行同步翻倍）。 */}
        {((recon?.notices?.length) || (parsed?.notices?.length)) > 0 && (
          <div style={{ padding: '10px 13px', borderRadius: 9, background: 'var(--amber-bg)', color: 'var(--amber)', fontSize: 12.5, lineHeight: 1.8 }}>
            {(recon?.notices || parsed?.notices || []).map((n, i) => <div key={i}>⚠ {n}</div>)}
          </div>
        )}

        {step === 0 && (
          <div style={{ padding: 16, background: 'var(--surface-2)', border: '0.5px solid var(--line)', borderRadius: 12 }}>
            <input ref={fileRef} type="file" accept=".xls,.xlsx" onChange={onFile} style={{ display: 'none' }} />
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
              <div style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>本期承运商与计提数（{year || '—'}年{period || '—'}期）——已做解析方案的可上传对账，未做的灰显：</div>
            </div>
            <div style={{ border: '0.5px solid var(--line)', borderRadius: 12, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead><tr style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', textAlign: 'left' }}>
                  <th style={{ padding: '9px 12px', fontWeight: 600 }}>承运商</th>
                  <th style={{ padding: '9px 12px', fontWeight: 600 }}>解析方案</th>
                  <th style={{ padding: '9px 12px', fontWeight: 600, textAlign: 'right' }}>本期计提数</th>
                  <th style={{ padding: '9px 12px', fontWeight: 600, textAlign: 'right' }}>操作</th>
                </tr></thead>
                <tbody>
                  {carriers === null && <tr><td colSpan={4} style={{ padding: 16, textAlign: 'center', color: 'var(--ink-3)' }}>加载承运商与计提数中…</td></tr>}
                  {carriers && carriers.length === 0 && <tr><td colSpan={4} style={{ padding: 16, textAlign: 'center', color: 'var(--ink-3)' }}>本期无物流计提记录</td></tr>}
                  {(carriers || []).map((c, i) => {
                    const sel = c.parser && c.parser === carrier
                    return <tr key={i} style={{ borderTop: '0.5px solid var(--line)', background: sel ? 'var(--accent-soft)' : undefined, opacity: c.parser ? 1 : .6 }}>
                      <td style={{ padding: '9px 12px' }}><span style={{ fontWeight: 600 }}>{c.carrier}</span>
                        {c.multi && <span style={{ marginLeft: 6, fontSize: 10.5, padding: '1px 6px', borderRadius: 999, background: 'var(--blue-bg)', color: 'var(--blue)' }}>多文件</span>}</td>
                      <td style={{ padding: '9px 12px' }}>{c.parser
                        ? <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: 'var(--green-bg)', color: 'var(--green)' }}>{c.scheme}</span>
                        : <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>未做解析方案</span>}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 12 }}>{c.计提数 ? '¥' + money(c.计提数) : '—'}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                        {!c.parser
                          ? <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>—</span>
                          : (doneCarrier === c.parser && recon)
                            ? <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', justifyContent: 'flex-end' }}>
                                <span style={{ fontSize: 11, color: 'var(--green)' }}>✓ 已对账</span>
                                <button onClick={() => setStep(2)} style={{ padding: '5px 10px', borderRadius: 8, fontSize: 12 }}>查看结果</button>
                                <span onClick={() => pickCarrier(c)} style={{ fontSize: 11.5, color: 'var(--accent)', cursor: 'pointer' }}>重新上传</span>
                              </span>
                            : <button onClick={() => pickCarrier(c)} disabled={!!busy && sel} style={{ padding: '5px 12px', borderRadius: 8, fontSize: 12 }}>{sel && busy ? (busy === 'parse' ? '解析中…' : '对账中…') : '上传对账'}</button>}
                      </td>
                    </tr>
                  })}
                </tbody>
              </table>
            </div>
            {carrier && curRow && (
              <div style={{ marginTop: 12, padding: '10px 13px', borderRadius: 9, background: 'var(--accent-soft)', fontSize: 12, color: 'var(--ink-2)' }}>
                <b style={{ color: 'var(--accent)' }}>{curRow.carrier}</b> · 依据「{curRow.scheme}」解析　{busy === 'parse' ? '· 解析中…' : (file && file.length ? (file.length === 1 ? `· 已选 ${file[0].name}` : `· 已选 ${file.length} 个文件`) : (curRow.multi ? '· 弹窗中，请选账单文件（可多选）' : '· 弹窗中，请选账单文件'))}
                {curRow.计提数 ? <span> · 本期计提数 ¥{money(curRow.计提数)}</span> : null}
              </div>
            )}
          </div>
        )}

        {step === 1 && (
          <div style={{ padding: 26, textAlign: 'center', color: 'var(--ink-2)', background: 'var(--surface-2)', border: '0.5px solid var(--line)', borderRadius: 12 }}>
            <div style={{ fontSize: 14 }}>正在按回填单号直查金蝶（只读）…</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 6 }}>7 类单据 · 大账套可能需数十秒</div>
          </div>
        )}

        {step >= 2 && recon && (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 18px', alignItems: 'center', padding: '11px 14px', borderRadius: 12, background: 'var(--bg-rail)', fontSize: 13 }}>
              <b>{recon.carrier}</b>
              <span style={{ color: 'var(--ink-2)' }}>金蝶取回 {recon.kd_docs} 分录</span>
              {recon.tieout?.勾稽
                ? <span style={{ marginLeft: 'auto', color: 'var(--green)', fontWeight: 600 }}>✓ 三方勾稽通过 ¥{money(recon.tieout.账单合计)}</span>
                : <span style={{ marginLeft: 'auto', color: 'var(--amber)', fontWeight: 600 }}>⚠ 勾稽不平</span>}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(102px,1fr))', gap: 10 }}>
              {Object.entries(mat ? mat.stats : recon.stats).filter(([k, v]) => k !== '组数' && v > 0).map(([k, v]) => {
                const [bg, fg] = stColor(k === '一致' ? '核对一致' : k === '待人工' ? '待人工配对' : k)
                return <div key={k} style={{ background: 'var(--bg-sub)', borderRadius: 9, padding: '12px 14px', borderLeft: `3px solid var(${fg})` }}>
                  <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{k}</div>
                  <div style={{ fontSize: 24, fontWeight: 600, color: `var(${fg})`, marginTop: 2 }}>{v}</div>
                </div>
              })}
            </div>

            {mat && (
              <div style={{ padding: '10px 13px', borderRadius: 9, background: 'var(--accent-soft)', fontSize: 12.5, color: 'var(--ink-2)' }}>
                <b style={{ color: 'var(--accent)' }}>物料级核量</b> · 按「金蝶单号 × 物料编码」逐行比重量，
                <b>以金蝶为准 · 容差 0</b>——只要账单kg 与金蝶kg 对不上就落「需人工复核」。
                可核口径：账单 {Number(mat.tieout.可核账单kg).toLocaleString('zh-CN', { maximumFractionDigits: 0 })} kg
                {' vs '}金蝶 {Number(mat.tieout.可核金蝶kg).toLocaleString('zh-CN', { maximumFractionDigits: 0 })} kg
                （差 {Number(mat.tieout.可核差异kg).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} kg）。
                <div style={{ marginTop: 6 }}>
                  <b style={{ color: 'var(--red)' }}>需人工复核＝只看「账单多报」{mOver.length} 条</b>（承运商多算重量，可能多付运费）。
                  <span style={{ color: 'var(--ink-3)' }}>
                    　账单少报 {mUnder.length} 条（共 {mUnderKg.toLocaleString('zh-CN', { maximumFractionDigits: 1 })} kg，
                    多为承运商把吨数截到两位小数）＝<b>我方有利，只标记留痕不必追</b>。
                  </span>
                </div>
                <span style={{ color: 'var(--ink-3)' }}>基本单位非千克的物料（如 Pcs／包）无法用重量核，已单列不计入差异。</span>
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {mat ? <>
                {chip('--red-bg', '--red', `需人工复核 ${mat.rows.filter(r => r.state === '需人工复核').length}`, mfilt === 'review', () => setMfilt('review'))}
                {chip('--amber-bg', '--amber', `查无/未匹配 ${mat.rows.filter(r => ['单号查无', '金蝶无此物料', '待人工配对'].includes(r.state)).length}`, mfilt === 'miss', () => setMfilt('miss'))}
                {chip('--gray-bg', '--gray', `账单少报 ${mUnder.length}`, mfilt === 'under', () => setMfilt('under'))}
                {chip('--blue-bg', '--blue', `非kg计量 ${mat.rows.filter(r => r.state === 'ERP非kg计量·重量无法核').length}`, mfilt === 'unit', () => setMfilt('unit'))}
                {chip('', '', `全部 ${mat.rows.length}`, mfilt === 'all', () => setMfilt('all'))}
                <button onClick={exportMatCsv} style={{ marginLeft: 'auto', padding: '6px 12px', borderRadius: 8, fontSize: 12.5 }}>导出物料级核量清单</button>
              </> : <>
                {chip('', '', `全部 ${recon.rows.length}`, filt === 'all', () => setFilt('all'))}
                {chip('--red-bg', '--red', '仅差异', filt === 'diff', () => setFilt('diff'))}
                {chip('', '', '待人工', filt === 'manual', () => setFilt('manual'))}
                <button onClick={exportCsv} style={{ marginLeft: 'auto', padding: '6px 12px', borderRadius: 8, fontSize: 12.5 }}>导出差异清单</button>
              </>}
            </div>

            {mat ? (
              <div style={{ border: '0.5px solid var(--line)', borderRadius: 12, overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 1080 }}>
                  <thead><tr style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', textAlign: 'left' }}>
                    {['状态', '金蝶单号', '单据类型', '物料编码', '物料', '基本单位', '账单kg', '金蝶kg', '差异kg', '方向', '账单金额', '账单行'].map((h, i) => (
                      <th key={h} style={{ padding: '9px 10px', fontWeight: 600, textAlign: i >= 6 && i <= 8 ? 'right' : 'left', whiteSpace: 'nowrap' }}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {mrows.length === 0 && <tr><td colSpan={12} style={{ padding: 20, textAlign: 'center', color: 'var(--ink-3)' }}>
                      {mfilt === 'review' ? '✓ 本期没有「账单多报」——承运商没有多算重量' : '无记录'}</td></tr>}
                    {mrows.map((r, i) => {
                      const [bg, fg] = stColor(r.state)
                      const bad = r.state === '需人工复核'
                      const num = { padding: '8px 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11.5 }
                      return (
                        <tr key={i} style={{ borderTop: '0.5px solid var(--line)', background: bad ? 'var(--red-bg)' : undefined }}>
                          <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                            <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: `var(${bg})`, color: `var(${fg})` }}>{r.state}</span></td>
                          <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{r.单号 || <span style={{ color: 'var(--ink-3)' }}>（无单号）</span>}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{r.单据类型 || '—'}</td>
                          <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{r.物料编码 || '—'}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-2)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.物料}>{r.物料 || '—'}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{r.基本单位 || '—'}</td>
                          <td style={num}>{r.账单kg != null ? Number(r.账单kg).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—'}</td>
                          <td style={num}>{r.金蝶kg != null ? Number(r.金蝶kg).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—'}</td>
                          <td style={{ ...num, fontWeight: bad ? 600 : 400, color: bad ? 'var(--red)' : undefined }}>
                            {r.差异kg != null ? Number(r.差异kg).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—'}</td>
                          <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: r.方向 === '账单多报' ? 'var(--red)' : 'var(--ink-2)', fontWeight: r.方向 === '账单多报' ? 600 : 400 }}>{r.方向 || '—'}</td>
                          <td style={{ ...num, fontWeight: 500 }}>{r.账单金额 != null ? money(r.账单金额) : '—'}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-3)', fontSize: 11 }}>{r.行号}</td>
                        </tr>)
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
            <div style={{ border: '0.5px solid var(--line)', borderRadius: 12, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 1320 }}>
                <thead><tr style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', textAlign: 'left' }}>
                  {['状态', '主体', '寄件人', 'KY运单号', '类型', '金蝶单号', '客户/往来', '货物', '金蝶重量KG', '费用分摊', '计费重量', '毛重净重比', '每公斤费用', '账单费用'].map((h, i) => <th key={h} style={{ padding: '9px 10px', fontWeight: 600, textAlign: i >= 8 ? 'right' : 'left', whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r, gi) => {
                    const [bg, fg] = stColor(r.state)
                    const bad = r.state === '数量不符'
                    const dl = (r.docs && r.docs.length) ? r.docs : [null]
                    const span = dl.length
                    const num = { padding: '8px 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11.5 }
                    const mrg = { padding: '8px 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11.5, verticalAlign: 'top', background: bad ? undefined : 'var(--bg-sub)', borderLeft: '0.5px solid var(--line)' }
                    return dl.map((d, di) => (
                      <tr key={gi + '-' + di} style={{ borderTop: di === 0 ? '0.5px solid var(--line)' : '0.5px dashed var(--line)', background: bad ? 'var(--red-bg)' : undefined }}>
                        {di === 0 && <td rowSpan={span} style={{ padding: '8px 10px', whiteSpace: 'nowrap', verticalAlign: 'top' }}><span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: `var(${bg})`, color: `var(${fg})` }}>{r.state}</span></td>}
                        {di === 0 && <td rowSpan={span} style={{ padding: '8px 10px', color: 'var(--ink-2)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{r.主体 || '—'}</td>}
                        {di === 0 && <td rowSpan={span} style={{ padding: '8px 10px', color: 'var(--ink-2)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{r.寄件人 || '—'}</td>}
                        {di === 0 && <td rowSpan={span} style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', fontSize: 11, whiteSpace: 'nowrap', verticalAlign: 'top' }}>{r.运单号 || '—'}</td>}
                        {di === 0 && <td rowSpan={span} style={{ padding: '8px 10px', color: 'var(--ink-2)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{r.类型 || '—'}</td>}
                        <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{d ? d.单号 : (r.nos || <span style={{ color: 'var(--ink-3)' }}>（无单号）</span>)}</td>
                        <td style={{ padding: '8px 10px', color: 'var(--ink-2)', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d?.客户}>{d ? (d.客户 || '—') : '—'}</td>
                        <td style={{ padding: '8px 10px', color: 'var(--ink-2)', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d?.货物}>{d ? (d.货物 || '—') : '—'}</td>
                        <td style={num}>{d && d.金蝶重量 ? d.金蝶重量.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—'}</td>
                        <td style={num}>{d && d.费用分摊 != null ? money(d.费用分摊) : '—'}</td>
                        {di === 0 && <td rowSpan={span} style={mrg}>{r.bill_wt != null ? r.bill_wt.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—'}</td>}
                        {di === 0 && <td rowSpan={span} style={mrg}>{r.gn_ratio != null ? r.gn_ratio.toFixed(2) : '—'}</td>}
                        {di === 0 && <td rowSpan={span} style={mrg}>{r.per_kg != null ? r.per_kg.toFixed(2) : '—'}</td>}
                        {di === 0 && <td rowSpan={span} style={{ ...mrg, fontWeight: 500 }}>{money(r.billed)}</td>}
                      </tr>
                    ))
                  })}
                </tbody>
              </table>
            </div>
            )}
            {mat ? (
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.7 }}>
                一行＝<b>一个金蝶单号 × 一个物料</b>。账单kg 由承运商吨数×1000 得来，与金蝶该单该物料的<b>基本单位数量(千克)</b>比对，
                <b>以金蝶为准 · 容差 0</b>：只要对不上就分方向落态——
                <b style={{ color: 'var(--red)' }}>账单多报→「需人工复核」</b>（承运商多算重量，可能多付运费，要追）；
                <b>账单少报→「我方有利」</b>（承运商少收我方钱，灰底标记留痕、不必追，多为把吨数截到两位小数所致）。
                （比对按克级取整，仅为避开浮点计算尾巴，不是容差。）
                「ERP非kg计量」＝该物料金蝶基本单位是 Pcs／包等，重量无从比对，<b>不判差异</b>、如实单列。
                「金蝶无此物料」＝账单声称的物料不在该单据里，需查回填单号是否填错。结算定稿与钉钉付款为三/四期，二期不含。
              </div>
            ) : (
            <div style={{ fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.7 }}>
              一个运单挂多个金蝶单号时<b>拆成多行</b>（金蝶量/金蝶重量/费用分摊逐单号列，费用按金蝶重量→缺则数量占比分摊、分摊无损）；
              <b>灰底列＝运单级合并单元格</b>（计费重量/毛重净重比/每公斤费用/账单费用）。客户·货物取自金蝶单据。
              账单计费重量与金蝶(ERP)重量天然有差异，不判「不符」——看 <b>毛重净重比（计费重量÷金蝶数量）</b> 与 <b>每公斤费用</b> 判断是否合理。
              结算定稿与钉钉付款为三/四期，二期不含。
            </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
