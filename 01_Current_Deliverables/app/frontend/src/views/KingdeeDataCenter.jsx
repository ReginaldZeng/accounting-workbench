// [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 公共金蝶上传维护表与五类单据列表。
import React, { useEffect, useRef, useState } from 'react'
import { kingdeeDataStatus, kingdeeDataRefresh, kingdeeDataTemplateUrl, kingdeeDataUpload, kingdeeDataDownloadUrl, kingdeeDataDocuments } from '../api.js'
import './kingdeeDataCenter.css'

const TYPES = [
  ['SAL_OUTSTOCK', '销售出库单'], ['SAL_RETURNSTOCK', '销售退货单'], ['STK_MisDelivery', '其它出库单'],
  ['STK_TransferOut', '分布式调出单'], ['STK_TransferIn', '分布式调入单'],
  ['PUR_PurchaseOrder', '采购订单'], ['STK_InStock', '采购入库单'], ['PUR_MRB', '采购退料单'],
]
const TABS = [
  ['upload', '上传维护', []], ['sales', '销售出库单', ['SAL_OUTSTOCK', 'SAL_RETURNSTOCK']],
  ['other', '其它出库单', ['STK_MisDelivery']], ['tout', '分布式调出单', ['STK_TransferOut']],
  ['tin', '分布式调入单', ['STK_TransferIn']], ['purchase', '采购订单', ['PUR_PurchaseOrder', 'STK_InStock', 'PUR_MRB']],
  ['v1', '物流表 V1', []],
]
const labelOf = form => (TYPES.find(([key]) => key === form) || [form, form])[1]
const sourceName = source => source === 'manual_attachment' ? '手工上传' : '金蝶刷新'
const timeOf = value => String(value || '').replace('T', ' ').slice(0, 16) || '—'
const money = n => n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const pct = n => n == null ? '—' : (Number(n) * 100).toFixed(2) + '%'

function UploadMaintenance({ period, data, busy, onRefresh, onUpload, onTemplate, onDownload }) {
  const files = (data && data.files) || []
  return <>
    <div className="kdc-toolbar">
      <div><b>单据源表维护</b><span>每类独立更新，其他类型不受影响</span></div>
      <button className="kdc-btn" disabled={!!busy} onClick={() => onRefresh(TYPES.map(([key]) => key))}>刷新全部</button>
      <a className="kdc-btn" href={onTemplate}>下载公共补核模板</a>
    </div>
    <div className="kdc-table-wrap"><table className="kdc-table"><thead><tr><th>单据源表</th><th>更新日期</th><th>更新账号</th><th>状态</th><th className="ops">操作</th></tr></thead>
      <tbody>{TYPES.map(([form, label]) => {
        const meta = data && data.sources && data.sources[form]
        const required = !!(data && data.required.includes(form))
        return <tr key={form}><td><b>{label}</b><small>{meta ? `${meta.docs} 张快照 · ${sourceName(meta.source)}` : required ? '本期涉及，尚未取数' : '本期未涉及，可按需取数'}</small></td>
          <td>{meta ? timeOf(meta.at) : '—'}</td><td>{meta && meta.by ? meta.by : '—'}</td>
          <td><span className={'kdc-status ' + (meta ? 'ok' : required ? 'pending' : '')}>{meta ? '已更新' : required ? '待取数' : '未涉及'}</span></td>
          <td className="ops"><button className="kdc-btn" disabled={!!busy} onClick={() => onRefresh([form])}>{busy === 'refresh' ? '处理中…' : '重取金蝶'}</button><button className="kdc-btn" disabled={!!busy} onClick={onUpload}>手工上传</button></td></tr>
      })}</tbody></table></div>
    {data && data.missing.length > 0 && <div className="kdc-warning">待补类型：{data.missing.map(labelOf).join('、')}。对应复核标待取数，不按零数量或一致处理。</div>}
    {files.length > 0 && <div className="kdc-table-wrap kdc-files"><table className="kdc-table"><thead><tr><th>公共补核附件</th><th>更新日期</th><th>更新账号</th><th className="ops">操作</th></tr></thead><tbody>{files.map(file => <tr key={file.id}><td>{file.name}</td><td>{timeOf(file.at)}</td><td>{file.by || '—'}</td><td className="ops"><a className="kdc-btn" href={onDownload(file.id)}>下载</a></td></tr>)}</tbody></table></div>}
  </>
}

function DocumentList({ period, forms }) {
  const [data, setData] = useState(null), [error, setError] = useState('')
  const [qInput, setQInput] = useState(''), [q, setQ] = useState(''), [form, setForm] = useState('')
  const [page, setPage] = useState(1), [size, setSize] = useState(25)
  const seq = useRef(0)
  const selected = form ? [form] : forms
  useEffect(() => {
    const current = ++seq.current
    setData(null); setError('')
    kingdeeDataDocuments(period, selected, q, page, size).then(result => { if (current === seq.current) setData(result) })
      .catch(e => { if (current === seq.current) { setError(e.message); setData({ rows: [], total: 0, pages: 1, sources: {}, missing: selected }) } })
  }, [period, selected.join(','), q, page, size])
  useEffect(() => { setPage(1) }, [period, forms.join(','), q, form, size])
  const search = e => { e.preventDefault(); setQ(qInput.trim()); setPage(1) }
  return <div className="kdc-doclist">
    <form className="kdc-toolbar" onSubmit={search}><input className="kdc-search" aria-label="搜索单据" placeholder="单号 / 往来 / 物料编码或名称" value={qInput} onChange={e => setQInput(e.target.value)} />
      {forms.length > 1 && <select aria-label="单据类型" value={form} onChange={e => setForm(e.target.value)}><option value="">全部类型</option>{forms.map(key => <option key={key} value={key}>{labelOf(key)}</option>)}</select>}
      <button className="kdc-btn primary" type="submit">查询</button><button className="kdc-btn" type="button" onClick={() => { setQInput(''); setQ(''); setForm('') }}>清空</button>
      <span className="kdc-spacer" /><select aria-label="每页条数" value={size} onChange={e => setSize(Number(e.target.value))}><option value={25}>25 条/页</option><option value={50}>50 条/页</option><option value={100}>100 条/页</option></select>
    </form>
    {error && <div className="kdc-warning" role="alert">{error}</div>}
    {data && data.missing.length > 0 && <div className="kdc-warning">以下类型待取数：{data.missing.map(labelOf).join('、')}。可先到上传维护刷新或手工上传。</div>}
    <div className="kdc-table-wrap"><table className="kdc-table"><thead><tr><th>主体</th><th>类型</th><th>单号</th><th>往来 / 领用部门</th><th>物料分类</th><th>物料编码</th><th>物料名称</th><th className="num">基本数量</th><th>基本数量单位</th><th className="num">销售额</th><th>承运商</th><th className="num">运输费用</th><th className="num">装卸费用</th><th className="num">合计单位费用</th><th className="num">费比</th></tr></thead>
      <tbody>{!data && <tr><td colSpan="15" className="kdc-empty">读取公共快照…</td></tr>}{data && data.rows.length === 0 && <tr><td colSpan="15" className="kdc-empty">没有符合条件的单据</td></tr>}
        {data && data.rows.map(row => <tr key={row.row_key}><td>{row.subject || '—'}</td><td>{row.form_label}</td><td><b className="kdc-code">{row.doc_no}</b>{row.date && <small>{row.date}</small>}</td><td>{row.party || '—'}{row.org && <small>{row.org}</small>}</td><td>{row.material_group || '—'}</td><td className="kdc-code">{row.code || '—'}</td><td>{row.name || '—'}</td>
          <td className="num">{row.base_qty}</td><td>{row.base_unit || '—'}</td><td className="num">{money(row.sales)}</td><td>{row.carrier || '—'}</td><td className="num">{money(row.fee_tr)}</td><td className="num">{money(row.fee_ld)}</td><td className="num">{money(row.unit_fee)}</td><td className="num">{pct(row.ratio)}</td></tr>)}</tbody></table></div>
    {data && <div className="kdc-pager"><button className="kdc-btn" disabled={data.page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>上一页</button><span>共 {data.total} 条物料 · 第 {data.page} / {data.pages} 页</span><button className="kdc-btn" disabled={data.page >= data.pages} onClick={() => setPage(p => Math.min(data.pages, p + 1))}>下一页</button></div>}
  </div>
}

export default function KingdeeDataCenter({ period, flash }) {
  const [tab, setTab] = useState('upload'), [data, setData] = useState(null), [busy, setBusy] = useState(''), [error, setError] = useState('')
  const input = useRef(null)
  const load = async () => { setError(''); try { setData(await kingdeeDataStatus(period)) } catch (e) { setError(e.message); flash('公共快照状态读取失败：' + e.message) } }
  useEffect(() => { setData(null); load() }, [period])
  const refresh = async forms => {
    if (busy) return
    setBusy('refresh')
    try {
      const result = await kingdeeDataRefresh(period, forms)
      const errors = Object.values(result.errors || {})
      flash(errors.length ? `部分类型刷新失败：${errors.join('；')}。其他类型已保留原快照` : `已刷新 ${forms.length} 类公共单据并写入 PGSQL`)
      await load()
    } catch (e) { flash('公共刷新失败，原有快照未清除：' + e.message) } finally { setBusy('') }
  }
  const upload = async event => {
    const file = event.target.files && event.target.files[0]
    event.target.value = ''
    if (!file) return
    setBusy('upload')
    try {
      const result = await kingdeeDataUpload(period, file)
      flash(`公共补核附件已入库：${(result.refreshed || []).map(x => `${x.label} ${x.n} 单`).join('、')}${result.unknown && result.unknown.length ? `；${result.unknown.length} 个单号未匹配，已单列` : ''}`)
      await load()
    } catch (e) { flash('公共补核上传失败：' + e.message) } finally { setBusy('') }
  }
  const active = TABS.find(([key]) => key === tab) || TABS[0]
  return <div className="kdc">
    <div className="kdc-subtabs" role="tablist" aria-label="金蝶数据视图">{TABS.map(([key, label]) => <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? 'on' : ''} onClick={() => setTab(key)}>{label}</button>)}</div>
    {error && <div className="kdc-warning" role="alert">{error}<button className="kdc-btn" onClick={load}>重试</button></div>}
    {tab === 'upload' ? <UploadMaintenance period={period} data={data} busy={busy} onRefresh={refresh} onUpload={() => input.current && input.current.click()} onTemplate={kingdeeDataTemplateUrl(period)} onDownload={kingdeeDataDownloadUrl} /> : <DocumentList key={tab + period} period={period} forms={active[2]} />}
    <input ref={input} type="file" accept=".xlsx" hidden onChange={upload} />
  </div>
}
