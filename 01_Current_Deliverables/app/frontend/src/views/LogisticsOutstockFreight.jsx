// [Change Log] Date: 2026-10-03 | Author: Claude Opus 5.5 | Version: V2.774
// Description: 【单据运费·销售出库单】金蝶当月全部销售出库单为底，各家账单的逐单运费按单号挂上去——没挂到运费的也列出来。
//   用户：「需要列出来所有的出库单，支持筛选……我需要知道这个月每一笔出库单的运费是多少（除去内部交易的部分）」。
//   内部交易口径同 BP 工作台：客户＝集团内部主体(孝感卖给深圳两家的那一段)，默认剔除，可勾选一起看。
//   运费分两种：已复核(该承运商该月在复核台登记了已复核，或登记制) / 待复核(账单已导入、还没登记)。
//   V2.775：加 品牌(照 BP 工作台的客户物料映射表，按 客户名+物料编码 对) / 发货仓库 / 收货地址·联系人(金蝶出库单)，品牌、仓库可筛。
//   OutstockView 是纯展示(好在本地用真数据渲染核版式)，默认导出的容器负责取数和筛选状态。
import React, { useEffect, useState, useCallback } from 'react'
import { reviewOutstockFreight } from '../api.js'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const pctfmt = n => (n == null ? '—' : (Number(n) * 100).toFixed(2) + '%')
const qty = n => Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
const STATES = [['', '全部'], ['has', '有运费'], ['none', '没有运费'], ['pending', '有待复核的运费'], ['ok', '运费都已复核']]
const SORTS = [['date', '按日期'], ['fee', '运费从大到小'], ['ratio', '费比从大到小'], ['amount', '销售额从大到小']]

export function OutstockView({ d, f, setF, open, toggle, onSearch, qInput, setQInput, onFresh, busy }) {
  const filtered = !!(f.org || f.btype || f.carrier || f.state || f.q || f.brand || f.stock)
  const S = d ? (filtered ? d.cur : d.all) : null
  const sel = (k, opts, all) => <select value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value, page: 1 })}>
    <option value="">{all}</option>{opts.map(o => Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o} value={o}>{o}</option>)}</select>
  return (
    <div className="lof">
      <style>{CSS}</style>
      <div className="lof-stats">
        <div className="st"><div className="v">{S ? S.n : '—'}</div><div className="l">销售出库单（张）{filtered && <span className="tag">当前筛选</span>}
          {d && <div className="s">{d.internal ? `含内部交易 ${d.n_internal} 张` : `已剔除内部交易 ${d.n_internal} 张`}</div>}</div></div>
        <div className="st"><div className="v">{S ? S.n_has : '—'}<small> / {S ? S.n_none : '—'}</small></div><div className="l">有运费 / 没有运费（张）
          {S && S.n_none > 0 && <div className="s">没运费的销售额 {money(S.amount_none)}</div>}</div></div>
        <div className="st ok"><div className="v">{S ? money(S.fee) : '—'}</div><div className="l">运费合计（含税）
          {S && <div className="s">已复核 {money(S.fee_ok)}{S.fee_pending ? <span className="warn"> · 待复核 {money(S.fee_pending)}</span> : ''}</div>}</div></div>
        <div className="st"><div className="v">{S ? pctfmt(S.ratio) : '—'}</div><div className="l">费比（运费 ÷ 销售额）
          {S && <div className="s">销售额 {money(S.amount)}（价税合计）</div>}</div></div>
      </div>

      <div className="lof-bar">
        <input className="q" value={qInput} placeholder="搜单号 / 客户 / 品牌 / 收货地址" onChange={e => setQInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') onSearch() }} />
        <button className="btn" onClick={onSearch}>搜索</button>
        {d && sel('org', d.facets.orgs, '全部销售组织')}
        {d && sel('btype', d.facets.btypes, '全部单据类型')}
        {d && sel('brand', (d.facets.brands || []).map(([k, n]) => [k, `${k}（${n}）`]), '全部品牌')}
        {d && sel('stock', d.facets.stocks || [], '全部发货仓库')}
        {d && sel('carrier', d.facets.carriers, '全部承运商')}
        {sel('state', STATES.slice(1), '运费：全部')}
        <select value={f.sort} onChange={e => setF({ ...f, sort: e.target.value, page: 1 })}>{SORTS.map(o => <option key={o[0]} value={o[0]}>{o[1]}</option>)}</select>
        <label className="ck"><input type="checkbox" checked={!!f.internal} onChange={e => setF({ ...f, internal: e.target.checked ? 1 : 0, page: 1 })} />含内部交易</label>
        {filtered && <button className="btn" onClick={() => { setQInput(''); setF({ ...f, org: '', btype: '', carrier: '', state: '', q: '', brand: '', stock: '', page: 1 }) }}>清空筛选</button>}
        <span style={{ flex: 1 }} />
        {d && <span className="dim">金蝶取数 {d.fetched_at}</span>}
        <button className="btn" disabled={busy} onClick={onFresh} title="重新从金蝶取本月销售出库单（只读）">{busy ? '取数中…' : '刷新金蝶'}</button>
      </div>
      {d && d.bp_err && <div className="lof-note">{d.bp_err}</div>}
      {d && d.orphan && d.orphan.n > 0 && <div className="lof-note">另有 {d.orphan.n} 笔运费 {money(d.orphan.amount)}：账单上写了销售出库单号，但本月的销售出库单里没有这张单
        （发货在别的月份、单号填错，或它其实是其他出库单），不在下表。</div>}

      <div className="lof-tw"><table>
        <colgroup><col style={{ width: 28 }} /><col style={{ width: 92 }} /><col style={{ width: 150 }} /><col style={{ width: 88 }} /><col style={{ width: '14%' }} /><col style={{ width: '10%' }} />
          <col style={{ width: 112 }} /><col /><col style={{ width: 62 }} />
          <col style={{ width: 118 }} /><col style={{ width: 108 }} /><col style={{ width: 96 }} /><col style={{ width: 92 }} /><col style={{ width: 70 }} /><col style={{ width: '11%' }} /></colgroup>
        <thead><tr><th></th><th>日期</th><th>出库单号</th><th>销售组织</th><th>客户</th><th>品牌</th><th>发货仓库</th><th>收货地址</th><th>类型</th><th className="num">数量</th>
          <th className="num">销售额</th><th className="num">运费</th><th className="num">其中待复核</th><th className="num">费比</th><th>承运商</th></tr></thead>
        <tbody>
          {d === null && <tr><td colSpan="15" className="empty">从金蝶取本月全部销售出库单…</td></tr>}
          {d && d.rows.length === 0 && <tr><td colSpan="15" className="empty">没有符合条件的出库单</td></tr>}
          {d && d.rows.map(r => {
            const isOpen = open.has(r.no)
            const base = r.lines.reduce((s, x) => s + (x.baseqty || 0), 0)
            return <React.Fragment key={r.no}>
              <tr className={(r.fee ? '' : 'nofee') + (r.internal ? ' inner' : '')} onClick={() => toggle(r.no)}>
                <td className="tg">{isOpen ? '▾' : '▸'}</td>
                <td>{r.date}</td><td className="mono">{r.no}</td><td>{r.org}</td>
                <td className="ell" title={r.customer}>{r.customer}{r.internal && <span className="pill neu">内部</span>}</td>
                <td className="ell" title={[r.brand, r.bu].filter(Boolean).join(' · ')}>{r.brand || <span className="z">{r.internal ? '—' : '未映射'}</span>}</td>
                <td className="ell" title={r.stock}>{r.stock || <span className="z">—</span>}</td>
                <td className="ell" title={[r.linkman, r.addr].filter(Boolean).join(' · ')}>{r.addr || r.linkman ? <>{r.linkman && <b style={{ fontWeight: 600 }}>{r.linkman} </b>}{r.addr}</> : <span className="z">—</span>}</td>
                <td className="ell" title={r.btype}>{String(r.btype || '').replace('线上销售出库单', '').replace('销售出库单', '')}</td>
                <td className="num ell" title={r.qty_txt}>{r.qty_txt || '—'}</td>
                <td className="num">{money(r.amount)}</td>
                <td className="num">{r.fee ? <b>{money(r.fee)}</b> : <span className="z">没有运费</span>}</td>
                <td className="num">{r.fee_pending ? <span className="warn">{money(r.fee_pending)}</span> : <span className="z">—</span>}</td>
                <td className="num">{pctfmt(r.ratio)}</td>
                <td className="ell" title={r.carriers.map(x => `${x.carrier} ${money(x.amount)}（${x.period}${x.signed ? '' : '·待复核'}）`).join('\n')}>
                  {r.carriers.length ? r.carriers.map((x, i) => <span key={i} className={x.signed ? '' : 'warn'}>{i > 0 && '、'}{x.carrier}{r.carriers.length > 1 && <small> {money(x.amount)}</small>}</span>) : <span className="z">—</span>}</td>
              </tr>
              {isOpen && <tr className="sub"><td></td><td colSpan="14">
                <div className="src" style={{ marginTop: 0, marginBottom: 6 }}>
                  {r.bu && <span>事业单元：{r.bu}　·　</span>}收货：{[r.linkman, r.addr].filter(Boolean).join(' ') || '—'}　·　单据类型：{r.btype}</div>
                <table className="in"><thead><tr><th>物料编码</th><th>物料名称</th><th>品牌</th><th>产品系列</th><th>发货仓库</th><th className="num">基本单位数量</th><th>单位</th><th className="num">销售额</th><th className="num">摊得运费</th><th className="num">单位运费</th></tr></thead>
                  <tbody>{r.lines.map((x, i) => {
                    const share = base ? (x.baseqty || 0) / base : 1 / r.lines.length
                    const fl = r.fee * share
                    return <tr key={i}><td className="mono">{x.code}</td><td>{x.name}</td><td>{x.brand || '—'}</td><td>{x.series || '—'}</td><td>{x.stock || '—'}</td><td className="num">{qty(x.baseqty)}</td><td>{x.baseunit}</td>
                      <td className="num">{money(x.amount)}</td><td className="num">{r.fee ? money(fl) : '—'}</td>
                      <td className="num">{r.fee && x.baseqty ? (fl / x.baseqty).toFixed(4) : '—'}</td></tr>
                  })}</tbody></table>
                {r.carriers.length > 0 && <div className="src">运费来源：{r.carriers.map((x, i) => <span key={i}>{i > 0 && '　·　'}{x.carrier} {money(x.amount)}（{x.period} 账单，{x.signed ? '已复核' : <span className="warn">待复核</span>}）</span>)}</div>}
              </td></tr>}
            </React.Fragment>
          })}
        </tbody>
      </table></div>
      {d && d.pages > 1 && <div className="lof-pager">
        <button className="btn" disabled={d.page <= 1} onClick={() => setF({ ...f, page: d.page - 1 })}>上一页</button>
        <span>第 {d.page} / {d.pages} 页（{d.cur.n} 张，每页 {d.size} 张）</span>
        <button className="btn" disabled={d.page >= d.pages} onClick={() => setF({ ...f, page: d.page + 1 })}>下一页</button>
      </div>}
    </div>
  )
}

export default function LogisticsOutstockFreight({ period }) {
  const [d, setD] = useState(null)
  const [f, setF] = useState({ internal: 0, q: '', org: '', btype: '', carrier: '', state: '', brand: '', stock: '', sort: 'date', page: 1 })
  const [qInput, setQInput] = useState('')
  const [open, setOpen] = useState(() => new Set())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const load = useCallback((fresh = 0) => {
    setErr(''); setD(null); setOpen(new Set())
    if (fresh) setBusy(true)
    const Z = { n: 0, n_has: 0, n_none: 0, fee: 0, fee_ok: 0, fee_pending: 0, amount: 0, ratio: null, amount_none: 0 }
    reviewOutstockFreight({ period, ...f, fresh }).then(setD)
      .catch(e => { setErr(e.message); setD({ rows: [], facets: { orgs: [], btypes: [], carriers: [] }, all: Z, cur: Z, pages: 1, page: 1, n_internal: 0, orphan: { n: 0 }, fetched_at: '—' }) })
      .finally(() => setBusy(false))
  }, [period, f])
  useEffect(() => { load(0) }, [load])
  useEffect(() => { setF(x => ({ ...x, page: 1 })) }, [period])
  const toggle = no => setOpen(p => { const n = new Set(p); n.has(no) ? n.delete(no) : n.add(no); return n })
  return <>
    {err && <div className="msg" style={{ color: '#B23B2E' }}>取数失败：{err}</div>}
    <OutstockView d={d} f={f} setF={setF} open={open} toggle={toggle} qInput={qInput} setQInput={setQInput}
      onSearch={() => setF({ ...f, q: qInput.trim(), page: 1 })} onFresh={() => load(1)} busy={busy} />
  </>
}

const CSS = `
.lof{font-size:13px;color:#1B2733}
.lof-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}
.lof-stats .st{background:#fff;border:1px solid #DCE2E7;border-radius:10px;padding:12px 16px}
.lof-stats .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;color:#1F6E8C}.lof-stats .v small{font-size:15px;color:#B06A12;font-weight:600}
.lof-stats .st.ok .v{color:#2E7D57}
.lof-stats .l{font-size:12px;color:#5E6B78;margin-top:2px}.lof-stats .s{font-size:11.5px;color:#8A96A2;margin-top:3px}
.lof-stats .tag{margin-left:6px;background:#E1EEF3;color:#1F6E8C;border-radius:999px;padding:1px 7px;font-size:11px}
.lof-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#fff;border:1px solid #DCE2E7;border-radius:10px;padding:10px 12px;margin-bottom:10px}
.lof-bar select,.lof-bar .q{padding:6px 8px;border:1px solid #DCE2E7;border-radius:6px;font-size:13px;background:#fff}
.lof-bar .q{width:240px}.lof-bar select{max-width:190px}.lof-bar .ck{display:flex;gap:4px;align-items:center;font-size:13px;white-space:nowrap}
.lof .btn{border:1px solid #DCE2E7;background:#fff;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}.lof .btn:disabled{opacity:.5;cursor:default}
.lof-note{background:#FFFBF2;border:1px solid #EADFC6;border-radius:8px;padding:8px 12px;margin-bottom:10px;font-size:12.5px;color:#6B5320}
.lof-tw{background:#fff;border:1px solid #DCE2E7;border-radius:10px;overflow:auto}
.lof-tw table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:1560px}
.lof-tw th{background:#F1F4F6;color:#4A5763;font-weight:600;font-size:12px;text-align:left;padding:7px 9px;border-bottom:1px solid #DCE2E7;position:sticky;top:0}
.lof-tw td{padding:6px 9px;border-bottom:1px solid #EDF0F2;white-space:nowrap}
.lof-tw tbody>tr:not(.sub){cursor:pointer}.lof-tw tbody>tr:not(.sub):hover td{background:#F7FAFC}
.lof-tw tr.nofee td{background:#FFFCF5}.lof-tw tr.inner td{color:#8A96A2}
.lof .num{text-align:right;font-variant-numeric:tabular-nums}.lof th.num{text-align:right}
.lof .ell{overflow:hidden;text-overflow:ellipsis}.lof .mono{font-family:Consolas,Menlo,monospace;font-size:12.5px}
.lof .tg{color:#8A96A2;text-align:center;padding-right:0}
.lof .z{color:#A8B2BA}.lof .warn{color:#B06A12}.lof .dim{color:#8A96A2;font-size:12px}
.lof .empty{text-align:center;color:#8A96A2;padding:22px}
.lof .pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;margin-left:6px}.lof .pill.neu{background:#E7ECEF;color:#5E6B78}
.lof-tw tr.sub td{background:#F7F9FA;white-space:normal;padding:8px 12px 10px}
.lof-tw table.in{min-width:0;table-layout:auto;border:1px solid #DCE2E7;background:#fff}
.lof-tw table.in th{position:static;background:#F1F4F6}.lof-tw table.in td{background:#fff;white-space:normal}
.lof .src{margin-top:6px;font-size:12px;color:#5E6B78}
.lof-pager{display:flex;gap:12px;align-items:center;justify-content:center;padding:12px;font-size:13px;color:#5E6B78}
@media(max-width:1100px){.lof-stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
`
