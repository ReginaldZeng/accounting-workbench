// [Change Log] Date:2026-10-26 Author:Kimi Code Version:V-draft
// 逐单运费 V1：多文件按批次预览，逐文件确认、替换、撤回、恢复与原件下载；确认前检查异常和重叠。
import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  logisticsFreightV1Batches, logisticsFreightV1Batch, logisticsFreightV1Preview,
  logisticsFreightV1Update, logisticsFreightV1Confirm, logisticsFreightV1Withdraw,
  logisticsFreightV1Restore, logisticsFreightV1FileUrl, logisticsFreightV1Carriers,
  logisticsFreightV1BulkSupplement,
} from '../api.js'

const money = n => n == null || n === '' ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const STATUS = { preview: '待确认', pending: '待确认', draft: '待确认', uploaded: '待确认', confirmed: '已生效', effective: '已生效', active: '已生效', withdrawn: '已撤回', cancelled: '已取消', canceled: '已取消', blocked: '有阻断', failed: '解析失败' }
const SUBJECTS = ['深圳星期零', '深圳星期九', '孝感星期九']
const asList = value => Array.isArray(value) ? value : value == null ? [] : [value]
const batchOf = payload => payload && payload.batch ? payload.batch : payload
export const freightV1BatchesOf = payload => {
  if (Array.isArray(payload)) return payload
  if (!payload) return []
  const rows = payload.batches || payload.items || payload.rows || payload.results
  if (Array.isArray(rows)) return rows.map(batchOf).filter(Boolean)
  return payload.batch ? [payload.batch] : []
}
const field = (b, names, fallback) => {
  for (const source of [b, b && b.summary, b && b.preview, b && b.preview && b.preview.summary, b && b.reconciliation]) {
    if (!source) continue
    for (const name of names) if (source[name] !== undefined && source[name] !== null) return source[name]
  }
  return fallback
}
const issueText = x => typeof x === 'string' ? x : x && (x.msg || x.message || x.reason || x.description || x.doc_no || x.code) || JSON.stringify(x)
const issueCode = x => String(x && (x.code || x.type || x.kind) || '').toLowerCase()
const isOverlapIssue = x => issueCode(x).includes('overlap') || issueText(x).includes('重叠')
const categoriesOf = value => {
  if (Array.isArray(value)) return value.map(x => [x.name || x.category || x.label || x.fee_type || '未分类', x.amount ?? x.total ?? 0])
  return Object.entries(value || {})
}

function BatchPreview({ batch, busy, onConfirm, onCancel, onSupplement, onClose }) {
  const [allowOverlap, setAllowOverlap] = useState(false)
  const p = batch.preview || {}
  const validRows = field(batch, ['line_count', 'valid_rows', 'valid_row_count', 'valid_count'], 0)
  const docCount = field(batch, ['doc_count', 'document_count', 'valid_doc_count'], 0)
  const amount = field(batch, ['total_amount', 'amount', 'valid_amount', 'classified_amount'], 0)
  const original = field(batch, ['source_total', 'original_amount', 'source_amount', 'raw_amount', 'sheet_total'], null)
  const recalculated = field(batch, ['total_amount', 'recalculated_amount', 'recalc_amount', 'calculated_amount'], null)
  const statedDiff = field(batch, ['difference', 'reconciliation_diff', 'amount_diff', 'diff'], null)
  const diff = statedDiff != null ? statedDiff : original != null && recalculated != null ? Number(recalculated) - Number(original) : null
  const allIssues = asList(field(batch, ['issues', 'exceptions', 'anomalies', 'warnings'], []))
  const errors = allIssues.filter(x => x && x.severity === 'error')
  const exceptions = allIssues.filter(x => !x || x.severity !== 'error')
  const overlaps = asList(field(batch, ['overlaps', 'overlap', 'duplicates'], []))
  const nonOverlapErrors = errors.filter(x => !isOverlapIssue(x))
  const summaryBlocked = ['blocked', 'failed'].includes(String(batch.summary && batch.summary.status || ''))
  const explicitBlock = batch.blocked === true || p.blocked === true || summaryBlocked
  const explicitCannotConfirm = (batch.can_confirm === false || p.can_confirm === false) && !overlaps.length && !errors.some(isOverlapIssue)
  const blocked = explicitBlock || explicitCannotConfirm || nonOverlapErrors.length > 0 || (overlaps.length > 0 && !allowOverlap)
  const status = String(batch.status || 'preview').toLowerCase()
  const canAction = status === 'draft'
  const categories = categoriesOf(field(batch, ['category_amounts', 'amount_by_category', 'categories', 'classified'], {}))
  const lines = asList(batch.lines)
  const unique = key => [...new Set(lines.map(x => x[key]).filter(Boolean))]
  const initialMeta = () => ({
    carrier: batch.carrier || (unique('carrier').length === 1 ? unique('carrier')[0] : ''),
    subject: batch.subject || (unique('subject').length === 1 ? unique('subject')[0] : SUBJECTS[2]),
    biz_line: batch.biz_line || (unique('biz_line').length === 1 ? unique('biz_line')[0] : ''),
  })
  const [meta, setMeta] = useState(initialMeta)
  useEffect(() => { setMeta(initialMeta()) }, [batch.id, batch.carrier, batch.subject, batch.biz_line, batch.lines])
  const docRows = Object.values(lines.reduce((acc, line) => {
    const key = [line.doc_no, line.carrier, line.fee_type, line.subject].join('|')
    if (!acc[key]) acc[key] = { ...line, amount: 0, sourceRows: [] }
    acc[key].amount += Number(line.amount || 0)
    acc[key].sourceRows.push(`${line.sheet || ''} 第${line.row || '—'}行`)
    return acc
  }, {}))

  return <div className="lv1-preview">
    <div className="lv1-preview-head">
      <div><b>预览与勾稽</b><span>　{batch.filename || batch.file_name || batch.name || '未命名文件'}</span></div>
      <div className="actions"><button className="btn" onClick={onClose}>收起</button>{canAction && <button className="btn" disabled={!!busy} onClick={onCancel}>取消批次</button>}</div>
    </div>
    {canAction && <div className="lv1-supplement">
      <div className="lv1-supplement-title"><b>补充归属</b><span>只填原表空缺，不覆盖表内已有的物流商 / 主体</span></div>
      <label>承运商<input list="lv1-carrier-catalog" value={meta.carrier} onChange={e => setMeta({ ...meta, carrier: e.target.value })} placeholder="天鹰等表没有供应商时在这里补" /></label>
      <label>费用主体<select value={meta.subject} onChange={e => setMeta({ ...meta, subject: e.target.value })}>{SUBJECTS.map(x => <option key={x}>{x}</option>)}</select></label>
      <label>业务线<input value={meta.biz_line} onChange={e => setMeta({ ...meta, biz_line: e.target.value })} placeholder="如：植物肉 / 小料" /></label>
      <button className="btn" disabled={!!busy || !(meta.carrier || meta.subject || meta.biz_line)} onClick={() => onSupplement(meta)}>{busy === 'supplement' ? '保存中…' : '保存补充并重新校验'}</button>
    </div>}
    <div className="lv1-mini-stats">
      <div><b>{validRows}</b><span>有效行</span></div>
      <div><b>{docCount}</b><span>单据数</span></div>
      <div><b>{money(amount)}</b><span>有效金额（元）</span></div>
      <div><b>{money(original)}</b><span>原表金额（元）</span></div>
      <div><b>{money(recalculated)}</b><span>重算金额（元）</span></div>
      <div className={Math.abs(Number(diff) || 0) >= 0.005 ? 'bad' : ''}><b>{money(diff)}</b><span>重算－原表</span></div>
    </div>
    {categories.length > 0 && <div className="lv1-categories">分类金额：{categories.map(([name, value]) => <span key={name}>{name} <b>{money(value)}</b></span>)}</div>}
    {docRows.length > 0 && <div className="lv1-doc-preview"><table>
      <thead><tr><th>单据号</th><th>日期</th><th>承运商</th><th>费用类型</th><th>费用主体</th><th className="num">金额（元）</th><th>来源</th></tr></thead>
      <tbody>{docRows.slice(0, 10).map((row, i) => <tr key={i} className={!row.carrier || !row.subject ? 'missing' : ''}>
        <td>{row.doc_no || '缺单号'}</td><td>{row.doc_date || '—'}</td><td>{row.carrier || <b className="bad">待补充</b>}</td>
        <td>{row.fee_type || '—'}</td><td>{row.subject || <b className="bad">待补充</b>}</td><td className="num">{money(row.amount)}</td>
        <td title={row.sourceRows.join('\n')}>{row.sourceRows[0]}{row.sourceRows.length > 1 ? ` 等${row.sourceRows.length}行` : ''}</td>
      </tr>)}</tbody>
    </table>{docRows.length > 10 && <div className="lv1-doc-more">前 10 组，共 {docRows.length} 组逐单费用；确认前请结合下方异常检查。</div>}</div>}
    <div className="lv1-checks">
      <section className={nonOverlapErrors.length || explicitBlock || explicitCannotConfirm ? 'bad' : ''}>
        <h4>异常 / 阻断（{exceptions.length + nonOverlapErrors.length}）</h4>
        {exceptions.length + nonOverlapErrors.length === 0 && !explicitBlock && !explicitCannotConfirm ? <p>未发现阻断异常</p> : <ul>
          {[...nonOverlapErrors, ...exceptions].map((x, i) => <li key={i}>{issueText(x)}</li>)}
          {(explicitBlock || explicitCannotConfirm) && !nonOverlapErrors.length && <li>后端标记当前批次不可确认，请检查预览明细</li>}
        </ul>}
      </section>
      <section className={overlaps.length ? 'warn' : ''}>
        <h4>单据重叠（{overlaps.length}）</h4>
        {!overlaps.length ? <p>未发现与有效批次重叠</p> : <ul>{overlaps.map((x, i) => <li key={i}>{issueText(x)}</li>)}</ul>}
        {overlaps.length > 0 && <label className="allow"><input type="checkbox" checked={allowOverlap} onChange={e => setAllowOverlap(e.target.checked)} /> 已核对，允许重叠（allow_overlap）</label>}
      </section>
    </div>
    {canAction && <div className="lv1-confirmbar">
      <span className={blocked ? 'bad' : 'ok'}>{blocked ? '存在阻断，不能确认；取消预览不会写入有效 V1。' : '勾稽与异常检查通过，可确认进入有效 V1。'}</span>
      <button className="btn pri" disabled={blocked || !!busy} onClick={() => onConfirm(allowOverlap)}>{busy === 'confirm' ? '确认中…' : '确认此文件'}</button>
    </div>}
  </div>
}

export default function LogisticsV1Upload({ period }) {
  const [batches, setBatches] = useState([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState({})
  const [previews, setPreviews] = useState({})
  const [activePreview, setActivePreview] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [carriers, setCarriers] = useState([])
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkCarrier, setBulkCarrier] = useState('')
  const [customCarrier, setCustomCarrier] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const [withdraw, setWithdraw] = useState(null)
  const [reason, setReason] = useState('')
  const [replaceId, setReplaceId] = useState(null)
  const uploadRef = useRef(null)
  const replaceRef = useRef(null)

  const load = useCallback(() => {
    setLoading(true); setError('')
    logisticsFreightV1Batches(period).then(data => setBatches(freightV1BatchesOf(data)))
      .catch(e => setError('批次列表加载失败：' + e.message)).finally(() => setLoading(false))
  }, [period])
  useEffect(() => { setPreviews({}); setActivePreview(null); setSelected(new Set()); load() }, [load])
  useEffect(() => {
    logisticsFreightV1Carriers().then(data => setCarriers(data.carriers || [])).catch(() => setCarriers([]))
  }, [])
  const setBusyKey = (id, action) => setBusy(x => {
    const next = { ...x }
    if (action) next[id] = action; else delete next[id]
    return next
  })
  const applyPayload = payload => {
    const incoming = freightV1BatchesOf(payload)
    if (!incoming.length && payload && (payload.id || payload.batch_id)) incoming.push(batchOf(payload))
    if (!incoming.length) return []
    setBatches(old => {
      const map = new Map(old.map(x => [String(x.id ?? x.batch_id), x]))
      incoming.forEach(x => map.set(String(x.id ?? x.batch_id), x))
      return [...map.values()]
    })
    setPreviews(old => {
      const next = { ...old }
      incoming.forEach(x => {
        const id = String(x.id ?? x.batch_id)
        if (next[id]) next[id] = { ...next[id], ...x }
      })
      return next
    })
    return incoming
  }
  const upload = async (files, replacing = null) => {
    if (!files.length) return
    setError(''); setMsg(''); setBusyKey(replacing || '__upload__', 'preview')
    const old = replacing && batches.find(x => String(x.id ?? x.batch_id) === String(replacing))
    try {
      const data = await logisticsFreightV1Preview(files, {
        period, carrier: old ? old.carrier : '', subject: old ? old.subject : '',
        biz_line: old ? (old.biz_line || old.bizLine) : '',
        operation: replacing ? 'replace' : 'new', replace_id: replacing || undefined,
      })
      const incoming = applyPayload(data)
      if (!incoming.length) throw new Error('服务端未返回预览批次')
      setPreviews(current => {
        const next = { ...current }
        incoming.forEach(x => { next[String(x.id ?? x.batch_id)] = x })
        return next
      })
      setMsg(`${replacing ? '替换预览' : '上传预览'}完成：${incoming.length} 个文件，请逐文件检查后确认`)
    } catch (e) { setError(`${replacing ? '替换' : '上传'}失败：${e.message}`) } finally { setBusyKey(replacing || '__upload__', '') }
  }
  const preview = async batch => {
    const id = String(batch.id ?? batch.batch_id)
    setActivePreview(id)
    if (previews[id]) return
    setBusyKey(id, 'preview'); setError('')
    try {
      const data = await logisticsFreightV1Batch(id)
      const detail = batchOf(data)
      setPreviews(x => ({ ...x, [id]: detail }))
      setBatches(old => old.map(x => String(x.id ?? x.batch_id) === id ? { ...x, ...detail } : x))
    } catch (e) { setError('预览加载失败：' + e.message) } finally { setBusyKey(id, '') }
  }
  const mutate = async (batch, action, fn) => {
    const id = String(batch.id ?? batch.batch_id)
    setBusyKey(id, action); setError(''); setMsg('')
    try {
      const data = await fn(id)
      const updated = batchOf(data)
      if (updated && typeof updated === 'object') {
        setBatches(old => old.map(x => String(x.id ?? x.batch_id) === id ? { ...x, ...updated } : x))
        setPreviews(old => old[id] ? { ...old, [id]: { ...old[id], ...updated } } : old)
      }
      setMsg({ confirm: '已确认并进入有效 V1', withdraw: '批次已撤回', restore: '批次已恢复', cancel: '预览已取消，未写入有效 V1', supplement: '补充信息已保存，已重新校验' }[action] || '操作完成')
      if (action === 'cancel') {
        setPreviews(old => { const n = { ...old }; delete n[id]; return n })
        setBatches(old => old.filter(x => String(x.id ?? x.batch_id) !== id))
        setSelected(old => { const n = new Set(old); n.delete(id); return n })
      }
      if (action === 'confirm' || action === 'cancel') setActivePreview(null)
      if (action === 'withdraw') { setWithdraw(null); setReason('') }
      load()
    } catch (e) { setError(`${{ confirm: '确认', withdraw: '撤回', restore: '恢复', cancel: '取消', supplement: '保存补充' }[action] || '操作'}失败：${e.message}`) } finally { setBusyKey(id, '') }
  }
  const openReplace = batch => { setReplaceId(String(batch.id ?? batch.batch_id)); if (replaceRef.current) replaceRef.current.click() }
  const statusOf = b => STATUS[String(b.status || 'preview').toLowerCase()] || b.status || '待确认'
  const draftBatches = batches.filter(x => String(x.status).toLowerCase() === 'draft')
  const toggleBatch = id => setSelected(old => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next })
  const toggleAll = () => setSelected(old => old.size === draftBatches.length && draftBatches.length ? new Set() : new Set(draftBatches.map(x => String(x.id ?? x.batch_id))))
  const openBulk = () => { setBulkCarrier(carriers.length ? carriers[0].name : '__custom'); setCustomCarrier(''); setBulkOpen(true) }
  const bulkApply = async () => {
    const carrier = (bulkCarrier === '__custom' ? customCarrier : bulkCarrier).trim()
    if (!carrier || !selected.size) return
    setBulkBusy(true); setError(''); setMsg('')
    try {
      const data = await logisticsFreightV1BulkSupplement([...selected], carrier)
      applyPayload(data)
      setMsg(`已为 ${selected.size} 个待确认批次指定物流商：${carrier}`)
      setSelected(new Set()); setBulkOpen(false); load()
    } catch (e) { setError('批量指定失败：' + e.message) } finally { setBulkBusy(false) }
  }

  return <div className="lv1">
    <style>{`
      .lv1{font-size:13px;color:#1B2733}.lv1 *{box-sizing:border-box}.lv1 .btn{border:1px solid #DCE2E7;background:#fff;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer;color:#33414D}.lv1 .btn.pri{background:#1F6E8C;border-color:#1F6E8C;color:#fff}.lv1 .btn:disabled{opacity:.45;cursor:default}
      .lv1-card{background:#fff;border:1px solid #DCE2E7;border-radius:12px;margin-bottom:12px;overflow:hidden}.lv1-card>h3{font-size:13px;color:#5E6B78;margin:0;padding:11px 15px;border-bottom:1px solid #E5EAED}
      .lv1-upload{display:grid;grid-template-columns:160px auto;gap:10px;align-items:end;padding:14px 15px;justify-content:start}.lv1-upload label{font-size:11.5px;color:#5E6B78;display:flex;flex-direction:column;gap:4px}.lv1 input,.lv1 select{border:1px solid #DCE2E7;border-radius:6px;background:#fff;padding:6px 8px;font:inherit}.lv1 input[type=checkbox]{width:auto;margin:0}.lv1 .hint{padding:0 15px 12px;color:#8A96A2;font-size:11.5px}
      .lv1 .notice{padding:9px 12px;border-radius:8px;margin-bottom:10px;line-height:1.6}.lv1 .notice.error{background:#FEF1EF;border:1px solid #F0C7C0;color:#A53125}.lv1 .notice.ok{background:#EEF8F1;border:1px solid #CBE4D3;color:#256445}
      .lv1-table{width:100%;border-collapse:collapse}.lv1 th{background:#F5F7F8;color:#7A8792;font-size:11px;text-align:left;padding:8px 10px;border-bottom:1px solid #DCE2E7;white-space:nowrap}.lv1 td{padding:9px 10px;border-bottom:1px solid #EDF0F2;vertical-align:middle}.lv1 td.num{text-align:right;font-variant-numeric:tabular-nums}.lv1 .filename{font-weight:600;max-width:280px;overflow-wrap:anywhere}.lv1 .meta{display:block;color:#8A96A2;font-size:11px;margin-top:3px}.lv1 .status{display:inline-block;border-radius:999px;padding:2px 8px;background:#E8F1F5;color:#1F6E8C;font-size:11px}.lv1 .status.off{background:#EEF0F2;color:#6F7B85}.lv1 .actions{display:flex;gap:5px;flex-wrap:wrap}.lv1 .actions .btn{padding:4px 8px;font-size:12px}.lv1 .actions a{text-decoration:none}.lv1 .danger{color:#A53125!important}.lv1 .empty{text-align:center;color:#8A96A2;padding:24px!important}
      .lv1-preview{background:#F8FAFB;border-top:1px solid #DCE2E7;padding:14px 16px}.lv1-preview-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.lv1-preview-head span{color:#8A96A2}.lv1-mini-stats{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.lv1-mini-stats>div{background:#fff;border:1px solid #E1E7EA;border-radius:8px;padding:9px 11px}.lv1-mini-stats b{display:block;font-size:16px;font-variant-numeric:tabular-nums}.lv1-mini-stats span{display:block;color:#8A96A2;font-size:11px;margin-top:3px}.lv1-categories{display:flex;gap:14px;flex-wrap:wrap;background:#EEF4F6;border-radius:7px;padding:8px 11px;margin-top:10px;color:#52616D}.lv1-checks{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.lv1-checks section{background:#fff;border:1px solid #E1E7EA;border-radius:8px;padding:9px 12px}.lv1-checks section.bad{border-color:#E6B4AC}.lv1-checks section.warn{border-color:#E7CB97}.lv1-checks h4{font-size:12px;margin:0 0 5px}.lv1-checks p{color:#7B8892;margin:4px 0}.lv1-checks ul{margin:4px 0;padding-left:20px;max-height:140px;overflow:auto}.lv1-checks li{margin:3px 0;overflow-wrap:anywhere}.lv1 .allow{display:flex;gap:6px;align-items:center;color:#8A5A13;margin-top:7px}.lv1-confirmbar{display:flex;align-items:center;gap:12px;margin-top:12px}.lv1-confirmbar span{flex:1}.lv1 .bad{color:#B23B2E!important}.lv1 .ok{color:#2E7D57!important}
      .lv1-supplement{display:grid;grid-template-columns:1.2fr 1fr 1fr auto;gap:10px;align-items:end;background:#FFFDF6;border:1px solid #EADFC6;border-radius:8px;padding:10px 12px;margin-bottom:10px}.lv1-supplement-title{grid-column:1/-1;display:flex;gap:12px;align-items:center}.lv1-supplement-title span{font-size:11px;color:#8A96A2}.lv1-supplement label{font-size:11px;color:#5E6B78;display:flex;flex-direction:column;gap:4px}.lv1-doc-preview{margin-top:10px;background:#fff;border:1px solid #E1E7EA;border-radius:8px;overflow:auto}.lv1-doc-preview table{width:100%;border-collapse:collapse;min-width:900px}.lv1-doc-preview td{font-size:12px;white-space:nowrap}.lv1-doc-preview tr.missing td{background:#FFF9F0}.lv1-doc-more{padding:7px 10px;color:#8A96A2;font-size:11px;border-top:1px solid #EDF0F2}
      .lv1-mask{position:fixed;inset:0;background:rgba(18,31,40,.42);z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px}.lv1-dialog{width:480px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 16px 50px rgba(0,0,0,.2)}.lv1-dialog h3{margin:0 0 8px}.lv1-dialog p{color:#687681;line-height:1.6}.lv1-dialog textarea{width:100%;min-height:90px;resize:vertical;border:1px solid #DCE2E7;border-radius:7px;padding:8px;font:inherit}.lv1-dialog footer{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
      .lv1-preview-dialog{width:min(1500px,96vw);max-height:92vh;overflow:auto;padding:0}.lv1-preview-dialog .lv1-preview{border:0;border-radius:12px}.lv1-preview-dialog .lv1-preview-head{position:sticky;top:0;z-index:3;background:#F8FAFB;padding:4px 0 10px}.lv1-preview-dialog .lv1-confirmbar{position:sticky;bottom:0;background:#F8FAFB;padding:10px 0 2px;border-top:1px solid #DCE2E7}.lv1-bulkbar{display:flex;gap:10px;align-items:center;padding:10px 15px;background:#F5F9FA;border-bottom:1px solid #DCE2E7}.lv1-bulkbar span{color:#5E6B78;font-size:12px}.lv1-bulk-choice{display:flex;flex-direction:column;gap:10px;margin:12px 0}.lv1-bulk-choice select,.lv1-bulk-choice input{width:100%}.lv1-catalog-note{font-size:11px;color:#8A96A2}
      @media(max-width:1000px){.lv1-upload{grid-template-columns:1fr 1fr}.lv1-mini-stats{grid-template-columns:repeat(3,1fr)}.lv1-checks{grid-template-columns:1fr}.lv1-table-wrap{overflow-x:auto}.lv1-table{min-width:1080px}.lv1-supplement{grid-template-columns:1fr}.lv1-preview-dialog{width:98vw}}
    `}</style>
    <div className="lv1-card">
      <h3>物流表 V1 · 上传与预览</h3>
      <div className="lv1-upload">
        <label>期间<input value={period} readOnly /></label>
        <button className="btn pri" disabled={busy.__upload__ === 'preview'} onClick={() => uploadRef.current && uploadRef.current.click()}>{busy.__upload__ ? '解析中…' : '选择物流表（可多选）'}</button>
      </div>
      <input ref={uploadRef} type="file" multiple accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ''; upload(files) }} />
      <input ref={replaceRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ''; upload(files.slice(0, 1), replaceId) }} />
      <div className="hint">先上传原表，再在每个文件的预览里核对逐单明细、补充缺失的承运商和归属；只有“确认此文件”才进入有效 V1。</div>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {msg && <div className="notice ok">{msg}</div>}
    <div className="lv1-card">
      <h3>批次列表 · {period} <button className="btn" style={{ float: 'right', padding: '2px 9px' }} onClick={load}>刷新</button></h3>
      {draftBatches.length > 0 && <div className="lv1-bulkbar"><button className="btn" disabled={!selected.size} onClick={openBulk}>批量指定物流商{selected.size ? `（${selected.size}）` : ''}</button><span>仅待确认批次可勾选；只补原表缺失承运商，已生效批次不受影响。</span></div>}
      <div className="lv1-table-wrap"><table className="lv1-table"><thead><tr><th style={{ width: 30 }}><input type="checkbox" aria-label="选择全部待确认批次" checked={draftBatches.length > 0 && selected.size === draftBatches.length} onChange={toggleAll} /></th><th>文件名 / 上传人时间</th><th>模板</th><th>状态</th><th className="num">单据数</th><th className="num">行数</th><th>分类金额（元）</th><th>操作</th></tr></thead><tbody>
        {loading && <tr><td colSpan="8" className="empty">批次加载中…</td></tr>}
        {!loading && !batches.length && <tr><td colSpan="8" className="empty">本期还没有物流表 V1 批次</td></tr>}
        {!loading && batches.map((batch, index) => {
          const id = String(batch.id ?? batch.batch_id ?? index)
          const working = busy[id]
          const status = String(batch.status || 'preview').toLowerCase()
          const withdrawn = ['withdrawn', 'cancelled', 'canceled'].includes(status)
          const effective = ['confirmed', 'effective', 'active'].includes(status)
          const categories = categoriesOf(field(batch, ['category_amounts', 'amount_by_category', 'categories'], {}))
          return <React.Fragment key={id}><tr>
            <td><input type="checkbox" aria-label={`选择 ${batch.filename}`} disabled={status !== 'draft'} checked={selected.has(id)} onChange={() => toggleBatch(id)} /></td>
            <td><div className="filename">{batch.filename || batch.file_name || batch.name || '未命名文件'}</div><span className="meta">{batch.uploader || batch.uploaded_by || batch.created_by || '—'} · {batch.uploaded_at || batch.created_at || batch.upload_time || '—'}</span></td>
            <td>{batch.template || batch.template_name || batch.template_code || '—'}</td>
            <td><span className={'status' + (withdrawn ? ' off' : '')}>{statusOf(batch)}</span>{batch.withdraw_reason && <span className="meta">原因：{batch.withdraw_reason}</span>}</td>
            <td className="num">{field(batch, ['doc_count', 'document_count'], '—')}</td>
            <td className="num">{field(batch, ['row_count', 'valid_rows', 'line_count'], '—')}</td>
            <td>{categories.length ? categories.map(([name, value]) => <span key={name} style={{ marginRight: 9 }}>{name} <b>{money(value)}</b></span>) : money(field(batch, ['amount', 'total_amount'], null))}</td>
            <td><div className="actions">
              <button className="btn" disabled={!!working} onClick={() => preview(batch)}>{working === 'preview' ? '加载中…' : '预览'}</button>
              {effective && <button className="btn" disabled={!!working} onClick={() => openReplace(batch)}>替换</button>}
              {effective && <button className="btn danger" disabled={!!working} onClick={() => { setWithdraw(batch); setReason('') }}>撤回</button>}
              {withdrawn && <button className="btn" disabled={!!working} onClick={() => mutate(batch, 'restore', logisticsFreightV1Restore)}>{working === 'restore' ? '恢复中…' : '恢复'}</button>}
              <a className="btn" href={logisticsFreightV1FileUrl(batch.id ?? batch.batch_id)}>原件下载</a>
            </div></td>
          </tr></React.Fragment>
        })}
      </tbody></table></div>
    </div>
    <datalist id="lv1-carrier-catalog">{carriers.map(x => <option key={x.name} value={x.name}>{x.full_name}</option>)}</datalist>
    {activePreview && previews[activePreview] && <div className="lv1-mask" onMouseDown={e => { if (e.target === e.currentTarget) setActivePreview(null) }}>
      <div className="lv1-dialog lv1-preview-dialog" role="dialog" aria-modal="true" aria-label="逐单明细预览与确认">
        <BatchPreview batch={previews[activePreview]} busy={busy[activePreview]}
          onClose={() => setActivePreview(null)}
          onConfirm={allow => mutate(previews[activePreview], 'confirm', bid => logisticsFreightV1Confirm(bid, allow))}
          onSupplement={meta => mutate(previews[activePreview], 'supplement', bid => logisticsFreightV1Update(bid, { operation: 'supplement', ...meta }))}
          onCancel={() => mutate(previews[activePreview], 'cancel', bid => logisticsFreightV1Update(bid, { operation: 'cancel' }))} />
      </div>
    </div>}
    {bulkOpen && <div className="lv1-mask" onMouseDown={e => { if (e.target === e.currentTarget && !bulkBusy) setBulkOpen(false) }}>
      <div className="lv1-dialog" role="dialog" aria-modal="true" aria-label="批量指定物流商"><h3>批量指定物流商</h3>
        <p>已选 {selected.size} 个待确认批次。只填补原表缺失的承运商，不覆盖表内已有物流商。</p>
        <div className="lv1-bulk-choice"><label>物流商档案<select value={bulkCarrier} onChange={e => setBulkCarrier(e.target.value)}>
          {carriers.map(x => <option key={x.name} value={x.name}>{x.name}{x.full_name ? ` · ${x.full_name}` : ''}</option>)}
          <option value="__custom">手工输入其他物流商</option>
        </select></label>
        {bulkCarrier === '__custom' && <input autoFocus value={customCarrier} onChange={e => setCustomCarrier(e.target.value)} placeholder="输入物流商简称，如：天鹰" />}
        <span className="lv1-catalog-note">候选来自现有物流商档案；档案没有时可手工补充。</span></div>
        {error && <p className="bad">{error}</p>}
        <footer><button className="btn" disabled={bulkBusy} onClick={() => setBulkOpen(false)}>取消</button><button className="btn pri" disabled={bulkBusy || !(bulkCarrier === '__custom' ? customCarrier.trim() : bulkCarrier)} onClick={bulkApply}>{bulkBusy ? '指定中…' : `确认指定 ${selected.size} 个批次`}</button></footer>
      </div>
    </div>}
    {withdraw && <div className="lv1-mask" onMouseDown={e => { if (e.target === e.currentTarget && !busy[String(withdraw.id ?? withdraw.batch_id)]) setWithdraw(null) }}>
      <div className="lv1-dialog" role="dialog" aria-modal="true" aria-label="撤回物流表批次"><h3>撤回「{withdraw.filename || withdraw.file_name || withdraw.name}」</h3>
        <p>撤回后该文件不再计入有效 V1。请填写原因，操作记录会保留。</p>
        <textarea autoFocus value={reason} onChange={e => setReason(e.target.value)} placeholder="撤回原因（必填）" />
        <footer><button className="btn" onClick={() => setWithdraw(null)}>取消</button><button className="btn pri danger" disabled={!reason.trim() || !!busy[String(withdraw.id ?? withdraw.batch_id)]} onClick={() => mutate(withdraw, 'withdraw', id => logisticsFreightV1Withdraw(id, reason.trim()))}>确认撤回</button></footer>
      </div>
    </div>}
  </div>
}
