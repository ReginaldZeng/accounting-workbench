// [Change Log] Date: 2026-10-03 | Author: Claude Opus 5.5 | Version: V2.774
// Description: 【单据运费·销售出库单】金蝶当月全部销售出库单为底，各家账单的逐单运费按单号挂上去——没挂到运费的也列出来。
//   用户：「需要列出来所有的出库单，支持筛选……我需要知道这个月每一笔出库单的运费是多少（除去内部交易的部分）」。
//   内部交易口径同 BP 工作台：客户＝集团内部主体(孝感卖给深圳两家的那一段)，默认剔除，可勾选一起看。
//   运费分两种：已复核(该承运商该月在复核台登记了已复核，或登记制) / 待复核(账单已导入、还没登记)。
//   V2.775：加 品牌(照 BP 工作台的客户物料映射表，按 客户名+物料编码 对) / 发货仓库 / 收货地址·联系人(金蝶出库单)，品牌、仓库可筛。
//   V2.776：列顺序按用户定＝销售组织 / 日期 / 出库单号 / 品牌 / 客户 / 发货仓库 / 收货地址 …；「含内部交易」勾选框不再上下折行。
//   V2.777：点表头排序(每一列都能排，再点一次反向；数字列先从大到小；空值永远排最后)，原来的排序下拉去掉。
//   V2.778：费用拆三列＝运费 / 装卸费 / 其他(仓储·操作·包材)＋合计；「没有运费」按运费列判(只有装卸费的也算没运费)，筛选可单挑「只有装卸费」。
//     合计标橙色＝其中有待复核的(原「其中待复核」一列并进来，鼠标放上去看金额)。
//   V2.780：V2.779 把最小宽定到 1900 结果用户屏上要左右滑——压到 1720(日期只显示月-日、列间距收窄、固定列各减几像素)，1900 宽的屏不用滑。
//   V2.779：修收货地址列被挤没(加了费用列后固定宽度超了，地址列是「剩多少给多少」)：每列都给明确宽度，表格最小宽 1900。
//   V2.781：销售退货单也进这张表(类型标「退货」，销售额/数量为负＝冲减；不算费比；「有/没有运费」只对出库单说)。
//   OutstockView 是纯展示(好在本地用真数据渲染核版式)，默认导出的容器负责取数和筛选状态。
import React, { useEffect, useState, useCallback } from 'react'
import { reviewOutstockFreight } from '../api.js'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const pctfmt = n => (n == null ? '—' : (Number(n) * 100).toFixed(2) + '%')
const qty = n => Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
const STATES = [['', '全部'], ['has', '有运费'], ['none', '没有运费'], ['ldonly', '只有装卸费、没有运费'], ['nofee', '什么费用都没有'], ['pending', '有待复核的费用'], ['ok', '费用都已复核']]
// 表头：[排序键, 列名, 是否数字列]。点表头排序，再点一次反过来；数字列第一次点是从大到小
const COLS = [['org', '销售组织'], ['date', '日期'], ['no', '出库单号'], ['brand', '品牌'], ['customer', '客户'], ['stock', '发货仓库'], ['addr', '收货地址'], ['btype', '类型'],
  ['kg', '数量', 1], ['amount', '销售额', 1], ['fee_tr', '运费', 1], ['fee_ld', '装卸费', 1], ['fee_ot', '其他', 1], ['fee', '合计', 1], ['ratio', '费比', 1], ['carrier', '承运商']]
const zero = v => !v || Math.abs(v) < 0.005

export function OutstockView({ d, f, setF, open, toggle, onSearch, qInput, setQInput, onFresh, busy }) {
  const filtered = !!(f.org || f.btype || f.carrier || f.state || f.q || f.brand || f.stock)
  const S = d ? (filtered ? d.cur : d.all) : null
  const sel = (k, opts, all) => <select value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value, page: 1 })}>
    <option value="">{all}</option>{opts.map(o => Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o} value={o}>{o}</option>)}</select>
  return (
    <div className="lof">
      <style>{CSS}</style>
      <div className="lof-stats">
        <div className="st"><div className="v">{S ? S.n_out : '—'}{S && S.n_ret > 0 && <small style={{ color: '#8A96A2' }}> ＋退货 {S.n_ret}</small>}</div><div className="l">销售出库单（张）{filtered && <span className="tag">当前筛选</span>}
          {S && S.n_ret > 0 && <div className="s">销售退货单 {S.n_ret} 张 · 冲减销售额 {money(S.amount_ret)}{S.fee_ret ? ` · 退货费用 ${money(S.fee_ret)}` : ''}</div>}
          {d && <div className="s">{d.internal ? `含内部交易 ${d.n_internal} 张` : `已剔除内部交易 ${d.n_internal} 张`}</div>}</div></div>
        <div className="st"><div className="v">{S ? S.n_has : '—'}<small> / {S ? S.n_none : '—'}</small></div><div className="l">有运费 / 没有运费（张）
          {S && S.n_none > 0 && <div className="s">没运费的：只有装卸费 {S.n_ldonly} 张 · 什么费用都没有 {S.n_nofee} 张 · 销售额 {money(S.amount_none)}</div>}</div></div>
        <div className="st ok"><div className="v">{S ? money(S.fee) : '—'}</div><div className="l">物流费用合计（含税）
          {S && <div className="s">运费 {money(S.fee_tr)} · 装卸费 {money(S.fee_ld)}{S.fee_ot ? ` · 其他 ${money(S.fee_ot)}` : ''}</div>}
          {S && <div className="s">已复核 {money(S.fee_ok)}{S.fee_pending ? <span className="warn"> · 待复核 {money(S.fee_pending)}</span> : ''}</div>}</div></div>
        <div className="st"><div className="v">{S ? pctfmt(S.ratio) : '—'}</div><div className="l">费比（费用合计 ÷ 销售额）
          {S && <div className="s">销售额 {money(S.amount)}（价税合计{S.n_ret > 0 ? '，已减退货' : ''}）</div>}</div></div>
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
        <colgroup><col style={{ width: 24 }} /><col style={{ width: 80 }} /><col style={{ width: 54 }} /><col style={{ width: 142 }} /><col style={{ width: '9%' }} /><col style={{ width: '13%' }} />
          <col style={{ width: 96 }} /><col style={{ width: '13%' }} /><col style={{ width: 44 }} />
          <col style={{ width: 92 }} /><col style={{ width: 96 }} /><col style={{ width: 84 }} /><col style={{ width: 76 }} /><col style={{ width: 62 }} /><col style={{ width: 88 }} /><col style={{ width: 62 }} /><col style={{ width: '7%' }} /></colgroup>
        <thead><tr><th></th>{COLS.map(([k, name, num]) => {
          const on = f.sort === k
          const next = on ? (f.dir === 'desc' ? 'asc' : 'desc') : (num ? 'desc' : 'asc')
          return <th key={k} className={'sortable' + (num ? ' num' : '') + (on ? ' on' : '')} title={`点击按「${name}」排序`}
            onClick={() => setF({ ...f, sort: k, dir: next, page: 1 })}>{name}<span className="ar">{on ? (f.dir === 'desc' ? '▼' : '▲') : '↕'}</span></th>
        })}</tr></thead>
        <tbody>
          {d === null && <tr><td colSpan="17" className="empty">从金蝶取本月全部销售出库单…</td></tr>}
          {d && d.rows.length === 0 && <tr><td colSpan="17" className="empty">没有符合条件的出库单</td></tr>}
          {d && d.rows.map(r => {
            const isOpen = open.has(r.no)
            const base = r.lines.reduce((s, x) => s + (x.baseqty || 0), 0)
            return <React.Fragment key={r.no}>
              <tr className={(r.kind === 'ret' ? 'ret' : zero(r.fee_tr) ? 'nofee' : '') + (r.internal ? ' inner' : '')} onClick={() => toggle(r.no)}>
                <td className="tg">{isOpen ? '▾' : '▸'}</td>
                <td>{r.org}</td><td title={r.date}>{String(r.date || '').slice(5)}</td><td className="mono">{r.no}</td>
                <td className="ell" title={[r.brand, r.bu].filter(Boolean).join(' · ')}>{r.brand || <span className="z">{r.internal ? '—' : '未映射'}</span>}</td>
                <td className="ell" title={r.customer}>{r.customer}{r.internal && <span className="pill neu">内部</span>}</td>
                <td className="ell" title={r.stock}>{r.stock || <span className="z">—</span>}</td>
                <td className="ell" title={[r.linkman, r.addr].filter(Boolean).join(' · ')}>{r.addr || r.linkman ? <>{r.linkman && <b style={{ fontWeight: 600 }}>{r.linkman} </b>}{r.addr}</> : <span className="z">—</span>}</td>
                <td className="ell" title={r.btype}>{r.kind === 'ret' ? <span className="rt">退货</span> : String(r.btype || '').replace('线上销售出库单', '').replace('销售出库单', '')}</td>
                <td className="num ell" title={r.qty_txt}>{r.qty_txt || '—'}</td>
                <td className="num">{money(r.amount)}</td>
                <td className="num">{zero(r.fee_tr) ? (r.kind === 'ret' ? <span className="z">—</span> : <span className="warn">没有运费</span>) : money(r.fee_tr)}</td>
                <td className="num">{zero(r.fee_ld) ? <span className="z">—</span> : money(r.fee_ld)}</td>
                <td className="num">{zero(r.fee_ot) ? <span className="z">—</span> : money(r.fee_ot)}</td>
                <td className="num" title={r.fee_pending ? `其中待复核 ${money(r.fee_pending)}` : '都已复核'}>{zero(r.fee) ? <span className="z">—</span> : <b className={r.fee_pending ? 'warn' : ''}>{money(r.fee)}</b>}</td>
                <td className="num">{pctfmt(r.ratio)}</td>
                <td className="ell" title={r.carriers.map(x => `${x.carrier} ${money(x.amount)}（${x.period}${x.signed ? '' : '·待复核'}）`).join('\n')}>
                  {r.carriers.length ? r.carriers.map((x, i) => <span key={i}>{i > 0 && '、'}{x.carrier}</span>) : <span className="z">—</span>}</td>
              </tr>
              {isOpen && <tr className="sub"><td></td><td colSpan="16">
                <div className="src" style={{ marginTop: 0, marginBottom: 6 }}>
                  {r.bu && <span>事业单元：{r.bu}　·　</span>}收货：{[r.linkman, r.addr].filter(Boolean).join(' ') || '—'}　·　单据类型：{r.btype}</div>
                <table className="in"><thead><tr><th>物料编码</th><th>物料名称</th><th>品牌</th><th>产品系列</th><th>发货仓库</th><th className="num">基本单位数量</th><th>单位</th><th className="num">销售额</th><th className="num">摊得费用</th><th className="num">单位费用</th></tr></thead>
                  <tbody>{r.lines.map((x, i) => {
                    const share = base ? (x.baseqty || 0) / base : 1 / r.lines.length
                    const fl = r.fee * share
                    return <tr key={i}><td className="mono">{x.code}</td><td>{x.name}</td><td>{x.brand || '—'}</td><td>{x.series || '—'}</td><td>{x.stock || '—'}</td><td className="num">{qty(x.baseqty)}</td><td>{x.baseunit}</td>
                      <td className="num">{money(x.amount)}</td><td className="num">{r.fee ? money(fl) : '—'}</td>
                      <td className="num">{r.fee && x.baseqty ? (fl / x.baseqty).toFixed(4) : '—'}</td></tr>
                  })}</tbody></table>
                {r.carriers.length > 0 && <div className="src">费用来源：{r.carriers.map((x, i) => <span key={i}>{i > 0 && '　·　'}<b style={{ fontWeight: 600 }}>{x.carrier}</b>
                  {[['运费', x.tr], ['装卸费', x.ld], ['其他', x.ot]].filter(([, v]) => !zero(v)).map(([n, v]) => ` ${n} ${money(v)}`).join('，')}（{x.period} 账单，{x.signed ? '已复核' : <span className="warn">待复核</span>}）</span>)}</div>}
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
  const [f, setF] = useState({ internal: 0, q: '', org: '', btype: '', carrier: '', state: '', brand: '', stock: '', sort: 'date', dir: 'asc', page: 1 })
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
.lof-bar .q{width:240px}.lof-bar select{max-width:190px}.lof-bar .ck{display:inline-flex;flex-direction:row;gap:4px;align-items:center;font-size:13px;white-space:nowrap}.lof-bar .ck input{margin:0;width:auto}
.lof .btn{border:1px solid #DCE2E7;background:#fff;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}.lof .btn:disabled{opacity:.5;cursor:default}
.lof-note{background:#FFFBF2;border:1px solid #EADFC6;border-radius:8px;padding:8px 12px;margin-bottom:10px;font-size:12.5px;color:#6B5320}
.lof-tw{background:#fff;border:1px solid #DCE2E7;border-radius:10px;overflow:auto}
.lof-tw table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:1720px}
.lof-tw th{background:#F1F4F6;color:#4A5763;font-weight:600;font-size:12px;text-align:left;padding:7px 6px;border-bottom:1px solid #DCE2E7;position:sticky;top:0}
.lof-tw td{padding:6px 6px;border-bottom:1px solid #EDF0F2;white-space:nowrap}
.lof-tw th.sortable{cursor:pointer;user-select:none;white-space:nowrap}.lof-tw th.sortable:hover{background:#E4EBEF}
.lof-tw th .ar{margin-left:3px;font-size:10px;color:#B8C2CA}.lof-tw th.on{color:#1F6E8C}.lof-tw th.on .ar{color:#1F6E8C}
.lof-tw tbody>tr:not(.sub){cursor:pointer}.lof-tw tbody>tr:not(.sub):hover td{background:#F7FAFC}
.lof-tw tr.nofee td{background:#FFFCF5}.lof-tw tr.ret td{background:#FBF7FB}.lof .rt{color:#8E4A8A;font-weight:600}.lof-tw tr.inner td{color:#8A96A2}
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
