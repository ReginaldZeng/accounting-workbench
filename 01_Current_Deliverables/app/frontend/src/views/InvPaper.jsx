// [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.740（发票管家·月末纸质件查验）
// Owner 2026-10-02 定：纸质件不卡审核，月末一次性查验；「这个月」按审核通过的月份。
// 选月份 → 列出当月审核通过的发票/收据，标纸质件到没到；扫码枪扫发票二维码＝记已到（系统按号码找，不用开票夹）；
// 没二维码的（收据、老式票）点「记已到」；导出未到清单去催。嵌在收票工作台里（页头切换），扫码框只在这页时出现，不和收票的扫码框抢焦点。
import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { invPaperList, invPaperScan, invPaperMark, invPaperExportUrl } from '../api.js'
import { money, fmtTime, ScanInput } from './invShared.jsx'

const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}` }
// 月初头几天多半在查上个月：10 号前默认上个月
const defaultMonth = () => {
  const d = new Date()
  if (d.getDate() > 10) return thisMonth()
  const p = new Date(d.getFullYear(), d.getMonth() - 1, 1)
  return `${p.getFullYear()}-${String(p.getMonth() + 1).padStart(2, '0')}`
}
const TONE = { confirm: 'ok', already: 'info', notFound: 'err', notInvoice: 'warn' }

export default function InvPaper({ canEdit }) {
  const [month, setMonth] = useState(defaultMonth)
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState('missing')
  const [q, setQ] = useState('')
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(null)

  const load = useCallback(() => {
    setErr('')
    invPaperList(month).then(r => setData(r)).catch(e => setErr(e.message || String(e)))
  }, [month])
  useEffect(() => { setData(null); load() }, [load])

  const patch = (id, paper) => setData(d => d && ({
    ...d, rows: d.rows.map(r => (r.id === id ? { ...r, paper } : r)),
    arrived: d.arrived + (paper ? 1 : -1), missing: d.missing + (paper ? -1 : 1),
  }))

  const onScan = async (code) => {
    try {
      const r = await invPaperScan(code)
      setRes({ tone: TONE[r.action] || 'info', text: r.msg })
      if (r.action === 'confirm' && r.item && data && data.rows.some(x => x.id === r.item.id)) patch(r.item.id, true)
    } catch (e) { setRes({ tone: 'err', text: '没记上：' + (e.message || e) }) }
  }
  const mark = async (row, paper) => {
    setBusy(row.id)
    try { await invPaperMark(row.id, paper); patch(row.id, paper) } catch (e) { setRes({ tone: 'err', text: '没记上：' + (e.message || e) }) } finally { setBusy(null) }
  }

  const rows = useMemo(() => {
    const all = (data && data.rows) || []
    const k = q.trim()
    return all.filter(r => (tab === 'all' || (tab === 'missing' ? !r.paper : r.paper))
      && (!k || [r.number, r.sellerName, r.applicant, r.title, r.payee, r.businessId, r.total].some(v => v !== null && v !== undefined && String(v).includes(k))))
  }, [data, tab, q])

  return (
    <div className="inv-pp">
      <div className="inv-pp-bar">
        <label className="inv-pp-m">审核通过月份
          <input type="month" className="inv-in inv-num" value={month} max={thisMonth()} onChange={e => e.target.value && setMonth(e.target.value)} />
        </label>
        {data && <span className="inv-pp-sum">共 <b>{data.total}</b> 张 · 已到 <b className="ok">{data.arrived}</b> · 未到 <b className="warn">{data.missing}</b></span>}
        <span className="inv-dk-grow" />
        <a className="btn" href={invPaperExportUrl(month, 'missing')}>导出未到清单</a>
        <a className="btn" href={invPaperExportUrl(month)}>导出全部</a>
      </div>
      {canEdit && <div className="inv-dk-scan">
        <div className="inv-dk-scan-main"><ScanInput keepFocus onScan={onScan} placeholder="扫码枪扫纸质发票上的二维码，扫到就记「纸质件已到」…" /></div>
        {res && <div className={'inv-dk-scanres ' + res.tone} role="status">{res.text}</div>}
        <div className="inv-dk-hints"><span>不用先打开票夹，系统按发票号码自己找；别的月审核的票扫到也照记，并告诉你是几月审的。收据、老式票没有二维码的，在下面那一行点「记已到」。</span></div>
      </div>}
      {err && <div className="banner err">没读出来：{err}<button type="button" className="btn" onClick={load}>重试</button></div>}
      <div className="inv-pp-tabs">
        {[['missing', '未到'], ['arrived', '已到'], ['all', '全部']].map(([k, l]) => <button type="button" key={k}
          className={'inv-pp-tab' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</button>)}
        <input className="inv-in" value={q} onChange={e => setQ(e.target.value)} placeholder="搜发票号码、销方、申请人、金额…" style={{ width: 260, marginLeft: 'auto' }} />
      </div>
      {!data ? (!err && <div className="loading"><span className="inv-spin" />&nbsp;正在读取…</div>)
        : !rows.length ? <div className="inv-dk-empty-s">{data.total ? '这一栏没有票' : '这个月还没有审核通过的发票'}</div>
        : <div className="tbl-wrap"><table className="inv-pp-tbl">
          <thead><tr><th>发票号码</th><th>票种</th><th>销方</th><th className="num">价税合计</th><th>单据</th><th>审核</th><th>纸质件</th></tr></thead>
          <tbody>{rows.map(r => <tr key={r.id}>
            <td className="inv-num">{r.number || '—'}{r.date && <div className="sub">{r.date}</div>}</td>
            <td>{r.typeLabel}</td>
            <td>{r.sellerName || '—'}</td>
            <td className="num inv-num">{money(r.total)}</td>
            <td>{r.applicant || '—'}<div className="sub">{r.payee || r.title}</div></td>
            <td>{r.reviewBy}<div className="sub">{fmtTime(r.reviewAt)}</div></td>
            <td>{r.paper
              ? <span className="inv-badge ok">已到{canEdit && <button type="button" className="inv-set-del" disabled={busy === r.id} onClick={() => mark(r, false)}>撤销</button>}</span>
              : <>{<span className="inv-badge warn">未到</span>}{canEdit && <button type="button" className="btn" style={{ marginLeft: 6 }} disabled={busy === r.id} onClick={() => mark(r, true)}>记已到</button>}</>}</td>
          </tr>)}</tbody>
        </table></div>}
    </div>
  )
}
