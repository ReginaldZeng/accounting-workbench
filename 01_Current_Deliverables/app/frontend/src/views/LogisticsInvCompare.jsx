// [Change Log] Date: 2026-10-03 | Author: Claude Opus 5.5 | Version: V2.768
// Description: 【物流账单复核台·第③步】发票与暂估——重排(用户「感觉有点乱」)：原来每张请款单各放两张小表、列宽不齐、还夹着和这一步无关的提示。
//   现在两张通栏表、所有请款单放一起、列对齐：① 按请款单×税率 计提(暂估) 对 发票，差额单列，结论并在右边；② 发票明细。
//   数据＝GET /api/logistics-review/invoices（口径同付款做账）。纯展示组件，自带样式。
import React from 'react'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const isZero = d => d != null && Math.abs(d) < 0.005
const pctOf = r => `${Math.round(r * 10000) / 100}%`
const KIND_CLS = { hx: 'ok', tail: 'warn', redo: 'bad', subj: 'bad', manual: 'warn', noacc: 'warn' }
const ST_CLS = { paid: 'ok', agreed: 'ok', mine: 'warn', run: 'neu', void: 'neu' }
// 和这一步无关的提示不显示(付款做账页才用得上)
const NOISE = ['还没付款', '金蝶付款单没取到', '这笔付款已经在金蝶']

function Diff({ v }) {
  if (v == null || isZero(v)) return <span className="z">—</span>
  return <b className="d">{v > 0 ? '+' : ''}{money(v)}</b>
}

export default function LogisticsInvCompare({ data }) {
  if (data === null) return <div className="ivc"><style>{CSS}</style><div className="ivc-empty">读钉钉请款单、发票管家票夹、金蝶计提凭证…</div></div>
  if (data.err) return <div className="ivc"><style>{CSS}</style><div className="ivc-empty bad">读取失败：{data.err}</div></div>
  const blocks = data.blocks || []
  if (!blocks.length) return <div className="ivc"><style>{CSS}</style><div className="ivc-empty">这家这月还没有钉钉请款单（或都已撤回、排除），没有发票可比。</div></div>
  const sum = k => blocks.reduce((s, b) => s + (b[k] || 0), 0)
  const T = { acc: sum('acc_total'), inv: sum('inv_total'), accTax: sum('acc_tax'), invTax: sum('inv_tax') }
  const nNoInv = blocks.filter(b => !(b.invoices || []).length).length     // 有请款单还没票：合计的差额没有意义，不显示
  return (
    <div className="ivc">
      <style>{CSS}</style>
      <div className="ivc-sec">计提（暂估）对发票 <small>一行一个税率；差额＝发票−计提，为 0 不显示</small></div>
      <table className="ivc-t">
        <colgroup><col style={{ width: '20%' }} /><col style={{ width: '5%' }} /><col style={{ width: '10%' }} /><col style={{ width: '10%' }} /><col style={{ width: '8%' }} />
          <col style={{ width: '9%' }} /><col style={{ width: '9%' }} /><col style={{ width: '7%' }} /><col /></colgroup>
        <thead>
          <tr><th rowSpan="2">请款单</th><th rowSpan="2">税率</th><th colSpan="3" className="g">含税金额</th><th colSpan="3" className="g">进项税</th><th rowSpan="2">结论</th></tr>
          <tr><th className="num">计提</th><th className="num">发票</th><th className="num">差额</th><th className="num">暂估</th><th className="num">发票</th><th className="num">差额</th></tr>
        </thead>
        {blocks.map(b => {
          const rows = b.err ? [] : (b.rates || [])
          const n = Math.max(1, rows.length)
          const noInv = !(b.invoices || []).length
          const notes = noInv ? [] : (b.msgs || []).filter(m => !NOISE.some(k => m.startsWith(k)))
          const head = <td rowSpan={n} className="req">
            <div><b>{b.subject}</b><span className="amt">{money(b.amount)}</span></div>
            <div className="sub"><span className={'pill ' + (ST_CLS[b.st && b.st.key] || 'neu')}>{b.st ? b.st.label : ''}</span>
              <span className="dim" title={`审批编号 ${b.bid}`}>{b.applicant} · …{String(b.bid || '').slice(-6)}</span></div>
          </td>
          const verdict = <td rowSpan={n} className="vd">
            {b.err ? <span className="bad">{b.err}</span> : <>
              <div>{noInv ? <span className="pill warn">还没有发票</span>
                : b.kind_cn && <span className={'pill ' + (KIND_CLS[b.kind] || 'neu')}>{b.kind_cn}</span>}
                {b.posted && <span className="pill ok">已做账 记-{b.posted.vno}</span>}</div>
              {noInv && <div className="why dim">{b.folder ? '票夹里还没有发票' : '请款单还没进发票管家'}，暂时比不了</div>}
              {!noInv && b.kind && b.kind !== 'hx' && b.kind_text && <div className="why">{b.kind_text}</div>}
              {notes.map((m, i) => <div key={i} className="why dim">{m}</div>)}
              {(b.invoices || []).length > 0 && !isZero((b.inv_total || 0) - (b.amount || 0)) &&
                <div className="why bad">发票合计 {money(b.inv_total)} ≠ 请款 {money(b.amount)}</div>}
            </>}
          </td>
          return <tbody key={b.inst}>
            {rows.length === 0
              ? <tr>{head}<td colSpan="7" className="z" style={{ textAlign: 'center' }}>{b.err ? '—' : '没有计提、也没有发票'}</td>{verdict}</tr>
              : rows.map((x, i) => <tr key={x.rate}>
                {i === 0 && head}
                <td><b>{pctOf(x.rate)}</b></td>
                <td className="num">{money(x.acc)}</td><td className="num">{x.n_inv ? money(x.inv) : <span className="z">无票</span>}</td><td className="num">{noInv ? <span className="z">—</span> : <Diff v={x.inv - x.acc} />}</td>
                <td className="num">{money(x.acc_tax)}</td><td className="num">{x.n_inv ? money(x.inv_tax) : <span className="z">—</span>}</td><td className="num">{noInv ? <span className="z">—</span> : <Diff v={x.inv_tax - x.acc_tax} />}</td>
                {i === 0 && verdict}
              </tr>)}
          </tbody>
        })}
        <tfoot><tr>
          <td>合计 <span className="dim">{blocks.length} 张请款单 · 请款 {money(data.req_total)}</span></td><td></td>
          <td className="num">{money(T.acc)}</td><td className="num">{money(T.inv)}</td><td className="num">{nNoInv ? <span className="z">—</span> : <Diff v={T.inv - T.acc} />}</td>
          <td className="num">{money(T.accTax)}</td><td className="num">{money(T.invTax)}</td><td className="num">{nNoInv ? <span className="z">—</span> : <Diff v={T.invTax - T.accTax} />}</td>
          <td>{nNoInv ? <span className="warn">{nNoInv === blocks.length ? '都还没有发票' : `${nNoInv} 张请款单还没有发票`}</span>
            : isZero(T.inv - data.req_total) ? <span className="ok">发票合计＝请款合计</span> : <span className="bad">发票合计比请款 {T.inv - data.req_total > 0 ? '+' : ''}{money(T.inv - data.req_total)}</span>}</td>
        </tr></tfoot>
      </table>

      <div className="ivc-sec">发票明细 <small>{data.n_inv} 张 · 发票管家</small></div>
      <table className="ivc-t">
        <colgroup><col style={{ width: '20%' }} /><col style={{ width: '17%' }} /><col /><col style={{ width: '9%' }} /><col style={{ width: '11%' }} /><col style={{ width: '10%' }} />
          <col style={{ width: '9%' }} /><col style={{ width: '10%' }} /></colgroup>
        <thead><tr><th>请款单</th><th>发票号码</th><th>类型</th><th>税率</th><th className="num">含税</th><th className="num">税额</th><th>纸质件</th><th>做账凭证</th></tr></thead>
        {blocks.map(b => {
          const invs = b.invoices || []
          const n = Math.max(1, invs.length)
          const head = <td rowSpan={n} className="req"><div><b>{b.subject}</b></div>
            <div className="sub">{b.folder ? <a href={`#/invaudit?folder=${b.folder}`} target="_blank" rel="noopener">发票管家票夹 #{b.folder} ↗</a> : <span className="dim">还没进发票管家</span>}</div></td>
          return <tbody key={b.inst}>
            {invs.length === 0
              ? <tr>{head}<td colSpan="7" className="z" style={{ textAlign: 'center' }}>{b.folder ? '票夹里还没有发票' : '请款单走到接入审批人节点后，发票会自动进发票管家'}</td></tr>
              : invs.map((i, k) => <tr key={i.id}>
                {k === 0 && head}
                <td className="mono">{i.number}</td><td className="ell" title={i.type}>{i.type}</td>
                <td>{i.rate}{i.deduct === false && <span className="warn"> 不抵扣</span>}</td>
                <td className="num">{money(i.gross)}</td><td className="num">{money(i.tax)}</td>
                <td>{i.paper ? <span className="ok">已到</span> : <span className="warn">未到</span>}</td>
                <td>{i.booked ? (i.vouchers || []).join('、') : <span className="z">—</span>}</td>
              </tr>)}
          </tbody>
        })}
      </table>
    </div>
  )
}

const CSS = `
.ivc{padding:4px 16px 16px;font-size:13px;color:#1B2733}
.ivc-empty{padding:26px;text-align:center;color:#5E6B78}.ivc-empty.bad{color:var(--bad,#B23B2E)}
.ivc-sec{font-weight:700;font-size:13.5px;margin:14px 0 6px}.ivc-sec small{font-weight:400;color:#5E6B78;font-size:12px;margin-left:8px}
.ivc-t{width:100%;border-collapse:collapse;table-layout:fixed;border:1px solid #DCE2E7}
.ivc-t th{background:#F1F4F6;color:#4A5763;font-weight:600;font-size:12px;text-align:left;padding:6px 10px;border-bottom:1px solid #DCE2E7}
.ivc-t th.g{text-align:center;border-left:1px solid #DCE2E7;border-right:1px solid #DCE2E7}
.ivc-t thead tr:last-child th{border-top:0}
.ivc-t td{padding:7px 10px;border-bottom:1px solid #EDF0F2;vertical-align:middle;overflow-wrap:anywhere}
.ivc-t tbody+tbody tr:first-child td{border-top:1.5px solid #C5CED5}
.ivc-t tbody tr:last-child td{border-bottom:0}
.ivc-t .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ivc-t th.num{text-align:right}
.ivc-t td.req{background:#FAFBFC;border-right:1px solid #EDF0F2;vertical-align:top}
.ivc-t td.req .amt{float:right;font-variant-numeric:tabular-nums;font-weight:600}
.ivc-t td.req .sub{margin-top:5px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:11.5px}
.ivc-t td.req a{color:var(--accent,#1F6E8C);font-size:12px}
.ivc-t td.vd{border-left:1px solid #EDF0F2;vertical-align:top}
.ivc-t td.vd .pill+.pill{margin-left:5px}
.ivc-t .why{font-size:12px;line-height:1.5;margin-top:4px;color:#33414D}
.ivc-t tfoot td{background:#F1F4F6;font-weight:700;border-top:1.5px solid #C5CED5;padding:8px 10px}
.ivc-t tfoot .dim{font-weight:400}
.ivc .z{color:#A8B2BA;font-weight:400}.ivc .d{color:var(--bad,#B23B2E)}.ivc .dim{color:#7A8791}
.ivc .ok{color:var(--ok,#2E7D57)}.ivc .warn{color:var(--warn,#B06A12)}.ivc .bad{color:var(--bad,#B23B2E)}
.ivc .mono{font-family:Consolas,Menlo,monospace;font-size:12.5px}.ivc .ell{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ivc .pill{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:999px;white-space:nowrap}
.ivc .pill.ok{background:#DCEFE4;color:var(--ok,#2E7D57)}.ivc .pill.warn{background:#F7E9CF;color:var(--warn,#B06A12)}
.ivc .pill.bad{background:#F8DDD8;color:var(--bad,#B23B2E)}.ivc .pill.neu{background:#E7ECEF;color:#5E6B78}
`
