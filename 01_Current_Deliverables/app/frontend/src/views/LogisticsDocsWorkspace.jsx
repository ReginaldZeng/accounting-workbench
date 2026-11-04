// [Change Log] Date:2026-11-04 Author:Kimi Version:V2.1047 第②步十二列逐单工作区，支持全量与服务端分页两种口径。
import React, { Fragment, useEffect, useMemo, useState } from 'react'
import { annotSourceText, Cell2, DiffCell, judge } from './LogisticsDocPanel.jsx'
import './logisticsDocsWorkspace.css'

const PAGE_SIZE = 50
const EMPTY_FACETS = { fsub: '', ffee: '', fbiz: '' }
const money = value => value == null || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const num = (value, digits = 3) => value == null || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits })
const text = value => value == null || String(value).trim() === '' ? '—' : String(value)
const standardOf = row => row.qty_standard || row.mode_short || ''
const productOf = row => row.bbiz ?? row.bizline ?? row.biz_bill ?? ''
const facetsOf = dfilt => ({ fsub: dfilt?.fsub || '', ffee: dfilt?.ffee || '', fbiz: dfilt?.fbiz || '' })

function searchText(row) {
  return [
    row.doc_no, row.subject, row.fee_item, productOf(row), row.note, row.mode_cn, row.mode_short,
    ...(row.parties || []),
    ...(row.materials || []).flatMap(material => [material.name, material.code, material.party]),
  ].filter(Boolean).join(' ').toLowerCase()
}

function matchesFacet(row, key, value) {
  if (!value) return true
  if (key === 'fsub') return (row.subject || '') === value
  if (key === 'ffee') return (row.fee_item || '') === value
  const selected = value.split('|').map(item => item === '-' ? '' : item)
  return selected.includes(String(productOf(row) || ''))
}

function facetOptions(rows, key, serverFacets, current) {
  const field = { fsub: 'subject', ffee: 'fee', fbiz: 'biz' }[key]
  const counts = new Map()
  if (serverFacets) {
    ;(serverFacets || []).forEach(([value, count]) => counts.set(key === 'fbiz' ? (value || '-') : (value || ''), count))
  } else {
    rows.forEach(({ row, noDoc }) => {
      if (noDoc) return
      const raw = key === 'fbiz' ? productOf(row) : key === 'fsub' ? row.subject : row.fee_item
      const value = key === 'fbiz' ? (raw || '-') : (raw || '')
      counts.set(value, (counts.get(value) || 0) + 1)
    })
  }
  if (current && !counts.has(current)) counts.set(current, null)
  return [...counts.entries()].map(([value, count]) => {
    const base = value || (key === 'fbiz' ? '无产品分类' : '（空）')
    const label = key === 'fbiz' && value.includes('|') ? value.split('|').map(item => item === '-' ? '无产品分类' : item).join('、') : base
    return { value, label: `${label}${count == null ? '' : `（${count}）`}`, field }
  })
}

function wrapDocs(rows, fullMode, serverPage) {
  return (rows || []).map((row, index) => ({
    row,
    noDoc: !row.doc_no,
    key: row.lid != null ? `l${row.lid}` : `doc|${fullMode ? 'full' : serverPage}|${row.doc_no || 'none'}|${index}`,
  }))
}

function wrapNoDocs(rows, fullMode, serverPage) {
  return (rows || []).map((source, index) => {
    const row = { ...source, doc_no: '', doc_fee: source.amount, bill_amt: source.bill_amt ?? source.qty, kd_sum: null, confirmed: null }
    return {
      row,
      noDoc: true,
      key: source.lid != null ? `nodoc-l${source.lid}` : `nodoc|${fullMode ? 'full' : serverPage}|${index}|${source.subject || ''}|${source.fee_item || ''}`,
    }
  })
}

function legacyDocsOf(rows) {
  return (rows || []).map((row, index) => ({
    ...row,
    lid: row.lid ?? `legacy-${index}`,
    doc_fee: row.amount,
    doc_kg: row.kd_qty,
    bill_amt: row.charge_wt ?? row.qty,
    bill_unit: '千克',
    kd_sum: row.kd_qty,
    kd_unit: '千克',
    qty_standard: row.qty_standard || '按重量',
    fee_item: row.fee_item || '物流费用',
    state: row.state || row.qty_state,
    n_mat: row.n_mat || 0,
    materials: row.materials || [],
    parties: row.parties || [row.prov].filter(Boolean),
    sub_fees: row.sub_fees || (row.std_amount != null ? { 运费: row.amount, 标准: row.std_amount, 核价差: row.price_diff } : null),
  }))
}

function QuantityDiff({ row, result }) {
  if (row.q_diff != null && Number.isFinite(Number(row.q_diff))) {
    const value = Number(row.q_diff)
    return <span className={value > 0 ? 'ldx-diff-pos' : value < 0 ? 'ldx-diff-neg' : ''}>{num(value)}</span>
  }
  if (result.q.d != null && Number.isFinite(Number(result.q.d))) {
    return <>{num(result.q.d)}<span className="lrx-cell-meta">区间口径</span></>
  }
  const tone = result.q.k === 'ok' ? 'ok' : result.q.k === 'bad' || result.q.k === 'miss' ? 'bad' : result.q.k === 'na' ? 'info' : 'pending'
  return <span className={'lrx-tag ' + tone}>{text(result.q.top)}</span>
}

function ClassificationSummary({ value }) {
  if (!value) return <div className="ldx-readonly-empty">归类结果未取到，不以账单名称推测金蝶归类。</div>
  const tone = value.v === '归类一致' ? 'ok' : value.v === '判不了' ? 'info' : 'pending'
  return <div className="ldx-classification">
    <div><span className={'lrx-tag ' + tone}>{text(value.v)}</span><b>金蝶推：{text(value.sys)}</b></div>
    <span>账单侧：{text(value.annot)}{value.bill ? ` → ${value.bill}` : ''}</span>
    <span className="lrx-cell-meta">标注来源：{annotSourceText(value.annot_source)}{value.acc_miss ? ' · 本月计提缺少系统归类列' : ''}</span>
    {(value.txt || value.unk) && <span className="lrx-cell-meta">{value.txt || value.unk}</span>}
  </div>
}

function InlineDetail({ row, result, classification, onOpen }) {
  const materials = row.materials || []
  const hasMaterials = Number(row.n_mat || 0) > 0 || materials.some(material => material.code)
  const fees = Object.entries(row.sub_fees || {}).filter(([name, value]) => typeof value === 'number' && value && !['标准', '核价差'].includes(name))
  const feeRows = fees.length ? fees : [[row.fee_item || '费用', row.doc_fee]]
  return <div className="ldx-inline">
    <div className="ldx-inline-head"><div><b>{text(result.v)}</b><span>{text(result.why)}</span></div><button type="button" className="lrx-btn primary" onClick={() => onOpen?.(row)}>打开完整处理</button></div>
    <div className="ldx-judge-grid">
      <section><h4>① 数量</h4><Cell2 {...result.q} title={row.mode_tip || row.mode_cn} /></section>
      <section><h4>② 价格</h4><Cell2 {...result.p} /></section>
      <section><h4>复核差额</h4><DiffCell j={result} /><span className="lrx-cell-meta">复核金额：{result.rv == null ? '待定' : money(result.rv)}</span></section>
      <section><h4>数量标准</h4><b>{text(standardOf(row))}</b><span className="lrx-cell-meta">{text(row.bill_unit)} · 账单数量 {num(row.bill_amt)}</span></section>
    </div>
    <section className="ldx-inline-section"><h4>费用拆分</h4><div className="ldx-inline-scroll"><table className="lrx-table ldx-fee-table"><thead><tr><th>费用项</th><th className="num">账单金额</th></tr></thead><tbody>{feeRows.map(([name, value]) => <tr key={name}><td>{text(name)}</td><td className="num">{money(value)}</td></tr>)}</tbody><tfoot><tr><td>账单合计</td><td className="num">{money(row.doc_fee)}</td></tr></tfoot></table></div></section>
    <section className="ldx-inline-section"><h4>金蝶物料 <span>{hasMaterials ? `${materials.length} 行 · 只读 WebAPI 结果` : '查无可展示物料'}</span></h4>
      {hasMaterials ? <div className="ldx-inline-scroll"><table className="lrx-table ldx-material-table"><thead><tr><th>物料编码 / 名称</th><th>规格</th><th className="num">基本单位数量</th><th className="num">核对量</th><th>折合信息</th><th className="num">分摊运费</th><th className="num">销售额</th></tr></thead><tbody>{materials.map((material, index) => <tr key={`${material.code || 'material'}-${index}`}>
        <td><span className="lrx-code">{text(material.code)}</span><b className="ldx-material-name">{text(material.name)}</b>{material.is_pack && <span className="lrx-cell-meta">包材 · 不计核对量</span>}</td>
        <td>{text(material.spec)}</td><td className="num">{num(material.base_kg)} {material.kg_unit || ''}</td><td className="num">{num(material.kd)} {row.kd_unit || material.out_unit || ''}</td>
        <td>{material.kg_eq != null ? <span>折 {num(material.kg_eq)} 千克</span> : material.box_n != null ? <span>{num(material.out_qty)} {material.out_unit} ÷ {num(material.box_div)} ＝ {num(material.box_n)} 箱</span> : '—'}{material.unit_gap && <span className="lrx-cell-meta ldx-warning">缺单位换算，未计入</span>}{material.bw_gap && <span className="lrx-cell-meta ldx-warning">缺每箱计费重量</span>}</td>
        <td className="num">{money(material.fee)}</td><td className="num">{money(material.sales)}</td></tr>)}</tbody></table></div>
        : <div className="ldx-readonly-empty">金蝶未查到这张单的物料明细；不根据账单名称编造物料或数量。</div>}
    </section>
    <section className="ldx-inline-section"><h4>归类摘要</h4><ClassificationSummary value={classification} /></section>
  </div>
}

export default function LogisticsDocsWorkspace({
  d = null,
  allD = null,
  full = false,
  docs: docsProp,
  q = '',
  onQuery,
  dfilt = null,
  onFacet,
  onClearFilters,
  onBackLines,
  locked = false,
  busy = false,
  onOpen,
  onConfirm,
  advanced,
  page = 1,
  onPage,
  cls = {},
}) {
  const fullMode = Boolean(full && allD && Array.isArray(allD.docs))
  const serverPage = Math.max(1, Number(page) || Number(d?.page) || 1)
  const currentDocs = d?.by_box === false ? legacyDocsOf(d?.detail) : Array.isArray(docsProp) ? docsProp : (d?.docs || [])
  const [segment, setSegment] = useState('all')
  const [view, setView] = useState('modal')
  const [query, setQuery] = useState(q || '')
  const [localFacets, setLocalFacets] = useState(() => facetsOf(dfilt))
  const [standard, setStandard] = useState('')
  const [status, setStatus] = useState('')
  const [localPage, setLocalPage] = useState(1)
  const [expanded, setExpanded] = useState(() => new Set())
  const isBusy = Boolean(busy)
  const confirming = busy === true || busy === 'confirm'

  useEffect(() => { setQuery(q || '') }, [q])
  useEffect(() => { setLocalFacets(facetsOf(dfilt)) }, [dfilt?.fsub, dfilt?.ffee, dfilt?.fbiz])

  const fetchedRows = useMemo(() => {
    const sourceDocs = fullMode ? allD.docs : currentDocs
    const sourceNoDocs = (fullMode && Array.isArray(allD.nodoc) ? allD.nodoc : d?.nodoc) || []
    return [...wrapDocs(sourceDocs, fullMode, serverPage), ...wrapNoDocs(sourceNoDocs, fullMode, serverPage)]
  }, [allD, currentDocs, d?.nodoc, fullMode, serverPage])

  const activeFacets = fullMode ? localFacets : facetsOf(dfilt)
  const optionRows = fullMode ? fetchedRows : wrapDocs(currentDocs, false, serverPage)
  const subjectOptions = useMemo(() => facetOptions(optionRows, 'fsub', fullMode ? null : d?.facets?.subject, activeFacets.fsub), [optionRows, fullMode, d?.facets, activeFacets.fsub])
  const feeOptions = useMemo(() => facetOptions(optionRows, 'ffee', fullMode ? null : d?.facets?.fee, activeFacets.ffee), [optionRows, fullMode, d?.facets, activeFacets.ffee])
  const bizOptions = useMemo(() => facetOptions(optionRows, 'fbiz', fullMode ? null : d?.facets?.biz, activeFacets.fbiz), [optionRows, fullMode, d?.facets, activeFacets.fbiz])
  const standardOptions = useMemo(() => [...new Set(fetchedRows.map(({ row }) => standardOf(row)).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN')), [fetchedRows])

  const filteredBeforeSegment = useMemo(() => fetchedRows.filter(item => {
    const { row, noDoc } = item
    const needle = query.trim().toLowerCase()
    if ((fullMode || noDoc) && needle && !searchText(row).includes(needle)) return false
    if (fullMode && !Object.keys(EMPTY_FACETS).every(key => matchesFacet(row, key, activeFacets[key]))) return false
    if (standard && standardOf(row) !== standard) return false
    if (status && (noDoc || (status === 'confirmed') !== Boolean(row.confirmed))) return false
    return true
  }), [fetchedRows, fullMode, query, activeFacets.fsub, activeFacets.ffee, activeFacets.fbiz, standard, status])

  const counts = useMemo(() => ({
    all: filteredBeforeSegment.length,
    pending: filteredBeforeSegment.filter(item => !item.noDoc && !item.row.confirmed).length,
    nodoc: filteredBeforeSegment.filter(item => item.noDoc).length,
  }), [filteredBeforeSegment])

  const filteredRows = useMemo(() => filteredBeforeSegment.filter(item => {
    if (segment === 'pending' && (item.noDoc || item.row.confirmed)) return false
    if (segment === 'nodoc' && !item.noDoc) return false
    if (!fullMode && item.noDoc && segment === 'all' && serverPage !== 1) return false
    return true
  }), [filteredBeforeSegment, segment, fullMode, serverPage])

  const paginatedLocally = fullMode || segment === 'nodoc'
  const serverSize = Math.max(1, Number(d?.size) || PAGE_SIZE)
  const serverTotal = Math.max(0, Number(d?.detail_total ?? currentDocs.length) || 0)
  const pageCount = paginatedLocally ? Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE)) : Math.max(1, Math.ceil(serverTotal / serverSize))
  const visiblePage = paginatedLocally ? Math.min(localPage, pageCount) : Math.min(serverPage, pageCount)
  const pageRows = paginatedLocally ? filteredRows.slice((visiblePage - 1) * PAGE_SIZE, visiblePage * PAGE_SIZE) : filteredRows
  const confirmNos = [...new Set(pageRows.filter(item => !item.noDoc && !item.row.confirmed && item.row.doc_no).map(item => item.row.doc_no))]
  const sourceLabel = dfilt?.label || [dfilt?.fsub, dfilt?.ffee, dfilt?.fbiz].filter(Boolean).join(' · ')

  const resetLocalPage = () => setLocalPage(1)
  const changeQuery = value => {
    setQuery(value)
    resetLocalPage()
    if (!fullMode) { onQuery?.(value); onPage?.(1) }
  }
  const changeFacet = (key, value) => {
    setLocalFacets(current => ({ ...current, [key]: value }))
    resetLocalPage()
    if (!fullMode) { onFacet?.(key, value); onPage?.(1) }
  }
  const clearFilters = () => {
    setQuery('')
    setLocalFacets(EMPTY_FACETS)
    setStandard('')
    setStatus('')
    setSegment('all')
    setExpanded(new Set())
    resetLocalPage()
    if (!fullMode) onQuery?.('')
    onClearFilters?.()
    onPage?.(1)
  }
  const changeSegment = value => { setSegment(value); resetLocalPage(); setExpanded(new Set()) }
  const changeView = value => { setView(value); setExpanded(new Set()) }
  const toggleExpanded = key => setExpanded(current => {
    const next = new Set(current)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })
  const goPage = next => {
    if (next < 1 || next > pageCount || isBusy) return
    if (paginatedLocally) setLocalPage(next)
    else onPage?.(next)
  }

  return <div className="ldx">
    <div className="lrx-toolbar ldx-toolbar">
      <div className="lrx-seg" aria-label="逐单任务">
        <button type="button" aria-pressed={segment === 'all'} className={segment === 'all' ? 'active' : ''} onClick={() => changeSegment('all')}>全部逐笔 <b>{counts.all}</b></button>
        <button type="button" aria-pressed={segment === 'pending'} className={segment === 'pending' ? 'active' : ''} onClick={() => changeSegment('pending')}>待确认 <b>{counts.pending}</b></button>
        <button type="button" aria-pressed={segment === 'nodoc'} className={segment === 'nodoc' ? 'active' : ''} onClick={() => changeSegment('nodoc')}>无金蝶单号 <b>{counts.nodoc}</b></button>
      </div>
      <div className="lrx-seg" aria-label="明细展示">
        <button type="button" aria-pressed={view === 'modal'} className={view === 'modal' ? 'active' : ''} onClick={() => changeView('modal')}>弹窗明细</button>
        <button type="button" aria-pressed={view === 'inline'} className={view === 'inline' ? 'active' : ''} onClick={() => changeView('inline')}>展开明细</button>
      </div>
      <span className="lrx-spacer" />
      <button type="button" className="lrx-btn" disabled={locked || isBusy || !confirmNos.length} onClick={() => onConfirm?.(confirmNos, true)}>{confirming ? '确认中…' : `确认本页${confirmNos.length ? `（${confirmNos.length}）` : ''}`}</button>
    </div>

    <section className="lrx-panel ldx-panel">
      {(dfilt && (dfilt.from || sourceLabel)) && <div className="ldx-source-filter"><span>来源回跳筛选：<b>{text(sourceLabel)}</b>{dfilt.from === 'lines' && '（来自逐笔计提复核）'}</span><div>{onBackLines && <button type="button" className="lrx-btn subtle" onClick={onBackLines}>‹ 回逐笔计提复核</button>}<button type="button" className="lrx-btn subtle" onClick={clearFilters}>清除筛选</button></div></div>}
      {!fullMode && <div className="ldx-scope-note" role="note">{full ? '全量清单尚未取到，当前仅展示父级已取列表。' : '服务端分页：关键字、主体、费用类型和产品分类由后端查询。'}分段、数量标准和确认状态只筛选当前已取列表，不代表全月数量；无单号费用在“全部逐笔”第 1 页或“无金蝶单号”分段查看。</div>}
      <div className="ldx-filter-grid">
        <label>单号 / 客户<input aria-label="单号 / 客户" type="search" value={query} placeholder="输入单号、客户或物料关键字" onChange={event => changeQuery(event.target.value)} /></label>
        <label>主体<select aria-label="主体" value={activeFacets.fsub} onChange={event => changeFacet('fsub', event.target.value)}><option value="">全部主体</option>{subjectOptions.map(option => <option key={option.value || 'empty'} value={option.value}>{option.label}</option>)}</select></label>
        <label>费用类型<select aria-label="费用类型" value={activeFacets.ffee} onChange={event => changeFacet('ffee', event.target.value)}><option value="">全部费用类型</option>{feeOptions.map(option => <option key={option.value || 'empty'} value={option.value}>{option.label}</option>)}</select></label>
        <label>产品分类<select aria-label="产品分类" value={activeFacets.fbiz} onChange={event => changeFacet('fbiz', event.target.value)}><option value="">全部产品分类</option>{bizOptions.map(option => <option key={option.value || 'empty'} value={option.value}>{option.label}</option>)}</select></label>
        <label>数量标准<select aria-label="数量标准" value={standard} onChange={event => { setStandard(event.target.value); resetLocalPage() }}><option value="">全部数量标准</option>{standardOptions.map(value => <option key={value} value={value}>{value}</option>)}{standard && !standardOptions.includes(standard) && <option value={standard}>{standard}</option>}</select></label>
        <label>确认状态<select aria-label="确认状态" value={status} onChange={event => { setStatus(event.target.value); resetLocalPage() }}><option value="">全部确认状态</option><option value="pending">待确认</option><option value="confirmed">已确认</option></select></label>
        <button type="button" className="lrx-btn ldx-clear" onClick={clearFilters}>清除筛选</button>
      </div>

      <div className="lrx-table-scroll ldx-table-scroll">
        <table className="lrx-table ldx-table">
          <thead><tr><th>金蝶单号 / 客户</th><th>主体</th><th>费用类型</th><th>产品分类</th><th>数量标准</th><th className="num">账单数量</th><th>金蝶数量</th><th className="num">数量差异</th><th className="num">账单金额</th><th>来源</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{pageRows.length ? pageRows.map(item => {
            const { row, key, noDoc } = item
            const result = noDoc ? null : judge(row)
            const isExpanded = view === 'inline' && expanded.has(key)
            const parties = (row.parties || []).join('、')
            const action = () => {
              if (noDoc) return
              if (view === 'modal') onOpen?.(row)
              else toggleExpanded(key)
            }
            return <Fragment key={key}>
              <tr className={'ldx-row ' + (noDoc ? 'ldx-row-nodoc' : row.confirmed ? 'ldx-row-confirmed' : 'ldx-row-pending')}>
                <td><b className="ldx-doc-no">{noDoc ? '无金蝶单号' : text(row.doc_no)}</b><span className="lrx-cell-meta">{noDoc ? text(row.note) : text(parties)}{!noDoc && row.trips > 1 ? ` · 账单 ${row.trips} 行` : ''}</span></td>
                <td>{text(row.subject)}{row.subject_pending && <span className="lrx-cell-meta ldx-warning">主体待指定</span>}</td>
                <td>{text(row.fee_item)}</td>
                <td>{text(productOf(row))}{row.biz_ovr && <span className="lrx-cell-meta">人工归类{row.biz_bill ? ` · 账单原值 ${row.biz_bill || '空'}` : ''}</span>}</td>
                <td>{text(standardOf(row))}</td>
                <td className="num">{num(row.bill_amt)} {row.bill_unit || ''}</td>
                <td>{noDoc ? '—' : row.kd_sum != null && Number.isFinite(Number(row.kd_sum)) ? <>{num(row.kd_sum)} {row.kd_unit || ''}<span className="lrx-cell-meta">{Number(row.n_mat || 0)} 行物料</span></> : <><span className={row.state === 'miss' ? 'ldx-warning' : ''}>{row.state === 'miss' ? '查无' : '—'}</span><span className="lrx-cell-meta">{text(result.q.top)}</span></>}</td>
                <td className="num">{noDoc ? '—' : <QuantityDiff row={row} result={result} />}</td>
                <td className="num"><b>{money(row.doc_fee)}</b>{(row.subj_ovr || row.fee_ovr || row.biz_ovr) && <span className="lrx-cell-meta">人工调整</span>}</td>
                <td>{noDoc ? <><span className="lrx-muted">标准账单</span><span className="lrx-cell-meta">汇总核 · 只读</span></> : <button type="button" className="lrx-btn subtle" aria-expanded={view === 'inline' ? isExpanded : undefined} onClick={action}>{view === 'modal' ? '明细' : isExpanded ? '收起' : '展开'}</button>}</td>
                <td>{noDoc ? <><span className="lrx-tag info">无单号 · 只读</span><span className="lrx-cell-meta">不参与单据确认</span></> : <><span className={'lrx-tag ' + (row.confirmed ? 'ok' : 'pending')}>{row.confirmed ? '已确认' : '待确认'}</span><span className="lrx-cell-meta" title={row.confirmed ? `${row.confirmed.by || ''} ${row.confirmed.at || ''}` : result.why}>{result.v}</span></>}</td>
                <td>{noDoc ? <span className="lrx-muted">—</span> : <div className="ldx-row-actions">{view === 'modal' && <button type="button" className="lrx-btn subtle" onClick={() => onOpen?.(row)}>处理</button>}<button type="button" className="lrx-btn subtle" disabled={locked || isBusy} onClick={() => onConfirm?.([row.doc_no], !row.confirmed)}>{row.confirmed ? '撤销' : '确认'}</button></div>}</td>
              </tr>
              {isExpanded && <tr className="ldx-expanded-row"><td colSpan="12"><InlineDetail row={row} result={result} classification={cls?.[row.doc_no] || row.classification || row.cls} onOpen={onOpen} /></td></tr>}
            </Fragment>
          }) : <tr><td colSpan="12" className="lrx-empty">{!d && !fullMode ? '读取逐单结果中…' : d?.err || '当前筛选下没有逐笔行'}</td></tr>}</tbody>
        </table>
      </div>
      <div className="ldx-pagination"><span>{paginatedLocally ? `${filteredRows.length} 行 · 每页 ${PAGE_SIZE} 行 · 第 ${visiblePage}/${pageCount} 页` : `服务端共 ${serverTotal} 行 · 当前可见 ${pageRows.length} 行 · 第 ${visiblePage}/${pageCount} 页`}</span><span className="lrx-spacer" /><button type="button" className="lrx-btn" disabled={visiblePage <= 1 || isBusy} onClick={() => goPage(visiblePage - 1)}>上一页</button><button type="button" className="lrx-btn" disabled={visiblePage >= pageCount || isBusy} onClick={() => goPage(visiblePage + 1)}>下一页</button></div>
    </section>

    {advanced != null && advanced !== false && <details className="lrx-panel lrx-detail-toggle ldx-advanced"><summary>高级功能 <small>规则、批量操作与旧版核对入口</small></summary><div className="ldx-advanced-body">{advanced}</div></details>}
  </div>
}
