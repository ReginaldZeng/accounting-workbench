// [Change Log] Date: 2026-10-03 | Author: Claude Opus 5.5 | Version: V2.769
// Description: 【物流账单复核台·第③步】发票与暂估。
//   V2.768 重排成两张通栏表；V2.769(用户「看不出来费用类型」)：上表改成一行一张计提凭证——费用类型·产品线 / 凭证号 / 税率 / 含税 / 暂估税，
//   右边的发票按税率合并对比(发票是按税率开的，对不到单张计提)：同税率的几张计提并在一格里比 发票含税、发票税额，差额单列。
//   税率要改的计提(计提 6%、发票 9%)排到发票那个税率下面，税率格写「6% → 9%」。数据＝GET /api/logistics-review/invoices（口径同付款做账）。
//   V2.770(用户「看不出是哪里导致的差异」)：发票对不到单张计提，能定位的是账单——每张计提旁边加「账单金额 / 账单−计提」两列，
//   取第①步逐笔计提复核的结果(同主体×费用类型×产品线的账单金额；几张凭证共用一笔账单的并在一起比)；账单有、计提没有的单列一行。
//   结论的长文字挪到每张请款单最后一行通栏显示，腾出列宽。
//   V2.771：共用一笔账单的几张计提排到相邻行，账单两格用合并单元格(不再写「并在…一起比」，排不到一起时才退回文字)。
import React from 'react'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const isZero = d => d != null && Math.abs(d) < 0.005
const r2 = x => Math.round((x + 1e-9) * 100) / 100
const pctOf = r => `${Math.round(r * 10000) / 100}%`
const rkey = r => Math.round((r || 0) * 10000)
const KIND_CLS = { hx: 'ok', tail: 'warn', redo: 'bad', subj: 'bad', manual: 'warn', noacc: 'warn' }
const ST_CLS = { paid: 'ok', agreed: 'ok', mine: 'warn', run: 'neu', void: 'neu' }
// 和这一步无关的提示不显示(付款做账页才用得上)
const NOISE = ['还没付款', '金蝶付款单没取到', '这笔付款已经在金蝶']

function Diff({ v }) {
  if (v == null || isZero(v)) return <span className="z">—</span>
  return <b className="d">{v > 0 ? '+' : ''}{money(v)}</b>
}

// 一张请款单 → 按税率分组：[{rate, accs:[计提…], inv, inv_tax, n_inv}]。要改税率的计提归到改后的税率(＝发票的税率)下面。
function groupsOf(b) {
  const g = {}
  const at = k => (g[k] = g[k] || { rate: k / 10000, accs: [], inv: 0, inv_tax: 0, n_inv: 0 })
  ;(b.accruals || []).forEach(a => {
    const moved = (a.mode === 'rate' || a.mode === 'fix') && a.new_rate != null && rkey(a.new_rate) !== rkey(a.rate)
    at(rkey(moved ? a.new_rate : a.rate)).accs.push({ ...a, moved })
  })
  ;(b.rates || []).forEach(x => { if (x.n_inv) { const o = at(rkey(x.rate)); o.inv = x.inv; o.inv_tax = x.inv_tax; o.n_inv = x.n_inv } })
  return Object.keys(g).map(Number).sort((x, y) => x - y).map(k => g[k])
}

// 第①步逐笔结果 → 每个主体：{byVno: {凭证号: 组}, extra: [账单有计提无]}。组＝共用同一笔账单的几张凭证(逐笔里 anc 相同)，
// 一张凭证跨几笔账单的把这几组并起来。组.bill＝账单金额，组.diff＝账单−计提。
function billMap(lines) {
  const out = {}
  if (!lines || !lines.rows) return null
  const subj = s => (out[s] = out[s] || { anc: {}, extra: [] })
  lines.rows.forEach(r => {
    if (r.kind === 'accr') {
      const o = subj(r.subject)
      const g = (o.anc[r.anc || r.key] = o.anc[r.anc || r.key] || { vnos: new Set(), bill: 0, diff: 0, has: false })
      g.vnos.add(String(r.vno || '').replace(/^记-/, ''))
      if (r.bill != null) { g.bill += r.bill; g.diff += -(r.diff || 0); g.has = true }
    } else if (r.kind === 'bill_only' && !isZero(r.bill || 0)) {
      subj(r.subject).extra.push({ fee: r.fee_type || r.fee || '', biz: r.biz || '', bill: r.bill })
    }
  })
  Object.values(out).forEach(o => {
    o.byVno = {}
    Object.values(o.anc).forEach(g => {            // 有共同凭证的组并成一个
      let m = g
      ;[...g.vnos].forEach(v => {
        const e = o.byVno[v]
        if (e && e !== m) { e.vnos.forEach(x => m.vnos.add(x)); m.bill += e.bill; m.diff += e.diff; m.has = m.has || e.has; e.vnos.forEach(x => { o.byVno[x] = m }) }
        o.byVno[v] = m
      })
    })
  })
  return out
}

export default function LogisticsInvCompare({ data, lines }) {
  if (data === null) return <div className="ivc"><style>{CSS}</style><div className="ivc-empty">读钉钉请款单、发票管家票夹、金蝶计提凭证…</div></div>
  if (data.err) return <div className="ivc"><style>{CSS}</style><div className="ivc-empty bad">读取失败：{data.err}</div></div>
  const blocks = data.blocks || []
  if (!blocks.length) return <div className="ivc"><style>{CSS}</style><div className="ivc-empty">这家这月还没有钉钉请款单（或都已撤回、排除），没有发票可比。</div></div>
  const sum = k => blocks.reduce((s, b) => s + (b[k] || 0), 0)
  const T = { acc: sum('acc_total'), inv: sum('inv_total'), accTax: sum('acc_tax'), invTax: sum('inv_tax') }
  const nNoInv = blocks.filter(b => !(b.invoices || []).length).length     // 有请款单还没票：合计的差额没有意义，不显示
  const BM = billMap(lines)
  const billTot = lines && lines.bill_total != null ? lines.bill_total : null
  return (
    <div className="ivc">
      <style>{CSS}</style>
      <div className="ivc-sec">计提对账单、对发票 <small>一行一张计提凭证。先看蓝底的「账单」两列：差额落在哪张计提上一眼能看到；发票是按税率开的，同税率的几张计提合在一起比。差额为 0 不显示</small></div>
      <table className="ivc-t">
        <colgroup><col style={{ width: '15%' }} /><col style={{ width: '14%' }} /><col style={{ width: '6%' }} /><col style={{ width: '6.5%' }} /><col style={{ width: '9%' }} />
          <col style={{ width: '9.5%' }} /><col style={{ width: '8%' }} /><col style={{ width: '7.5%' }} />
          <col style={{ width: '9%' }} /><col style={{ width: '7.5%' }} /><col /></colgroup>
        <thead>
          <tr><th rowSpan="2">请款单</th><th colSpan="4" className="g">金蝶计提（一行一张凭证）</th><th colSpan="2" className="g hl">账单（同费用类型·产品线）</th><th rowSpan="2" className="num gl">暂估税</th>
            <th colSpan="3" className="g">发票（按税率合计）</th></tr>
          <tr><th>费用类型 · 产品线</th><th>计提凭证</th><th>税率</th><th className="num">计提含税</th>
            <th className="num gl hl">账单金额</th><th className="num hl">账单−计提</th>
            <th className="num gl">发票含税</th><th className="num">发票−计提</th><th className="num">发票税额</th></tr>
        </thead>
        {blocks.map(b => {
          const noInv = !(b.invoices || []).length
          const gs = b.err ? [] : groupsOf(b)
          const bm = BM && BM[b.subject]
          const notes = noInv ? [] : (b.msgs || []).filter(m => !NOISE.some(k => m.startsWith(k)))
          const why = [
            !noInv && b.kind && b.kind !== 'hx' && b.kind_text ? b.kind_text : '',
            noInv ? `${b.folder ? '票夹里还没有发票' : '请款单还没进发票管家'}，发票那组暂时比不了` : '',
            ...notes,
            !noInv && !isZero((b.inv_total || 0) - (b.amount || 0)) ? `发票合计 ${money(b.inv_total)} ≠ 请款 ${money(b.amount)}` : '',
          ].filter(Boolean)
          const extra = (bm && bm.extra) || []
          const nRows = Math.max(1, gs.reduce((t, g) => t + Math.max(1, g.accs.length), 0)) + extra.length + (why.length ? 1 : 0)
          const head = <td rowSpan={nRows} className="req">
            <div><b>{b.subject}</b><span className="amt">{money(b.amount)}</span></div>
            <div className="sub"><span className={'pill ' + (ST_CLS[b.st && b.st.key] || 'neu')}>{b.st ? b.st.label : ''}</span></div>
            <div className="sub">{noInv ? <span className="pill warn">还没有发票</span>
              : b.kind_cn && <span className={'pill ' + (KIND_CLS[b.kind] || 'neu')}>{b.kind_cn}</span>}
              {b.posted && <span className="pill ok">已做账 记-{b.posted.vno}</span>}</div>
            <div className="sub"><span className="dim" title={`审批编号 ${b.bid}`}>{b.applicant} · …{String(b.bid || '').slice(-6)}</span></div>
          </td>
          if (b.err || !gs.length) return <tbody key={b.inst}><tr>{head}<td colSpan="10" className={b.err ? 'bad' : 'z'} style={{ textAlign: 'center' }}>{b.err || '没有计提、也没有发票'}</td></tr></tbody>
          // 排行：税率组按税率升序；共用同一笔账单的几张计提要挨着，才能把账单两格合并(用户 2026-10-03「一起比的就用合并单元格」)。
          // 同一笔账单跨两个税率组的(如 6% 一张 + 9% 一张)：前一组里排到最后、后一组里排到最前，正好相邻。
          const grpOf = a => (a && bm && bm.byVno ? bm.byVno[String(a.vno)] : null)
          const gIdx = new Map()           // 账单组 → 出现在哪些税率组(序号)
          gs.forEach((g, gi) => g.accs.forEach(a => { const x = grpOf(a); if (x && x.has) gIdx.set(x, [...(gIdx.get(x) || []), gi]) }))
          const rows = []
          gs.forEach((g, gi) => {
            const rank = a => { const x = grpOf(a); const ix = x && gIdx.get(x); if (!ix) return 1; return Math.min(...ix) < gi ? 0 : Math.max(...ix) > gi ? 2 : 1 }
            const order = new Map()        // 同一账单组的排在一起：按组第一次出现的位置
            g.accs.forEach((a, k) => { const x = grpOf(a) || a; if (!order.has(x)) order.set(x, k) })
            const accs = g.accs.map((a, k) => ({ a, k })).sort((p, q) => rank(p.a) - rank(q.a) || order.get(grpOf(p.a) || p.a) - order.get(grpOf(q.a) || q.a) || p.k - q.k).map(x => x.a)
            ;(accs.length ? accs : [null]).forEach((a, i) => rows.push({ a, g, gi, i, n: Math.max(1, accs.length), accs }))
          })
          // 账单格的合并段：相邻且同一账单组的行并成一段
          const shown = new Set()
          rows.forEach((r, k) => {
            const x = grpOf(r.a)
            const prev = k > 0 ? rows[k - 1] : null
            r.grp = x && x.has ? x : null
            r.runStart = !(r.grp && prev && prev.grp === r.grp)
          })
          rows.forEach((r, k) => { if (r.runStart) { let n = 1; while (k + n < rows.length && !rows[k + n].runStart) n++; r.run = n } })
          return <tbody key={b.inst}>
            {rows.map((r, k) => {
              const { a, g, i, n } = r
              const gAcc = r2(r.accs.reduce((t, x) => t + (x.gross || 0), 0)), gTax = r2(r.accs.reduce((t, x) => t + (x.tax || 0), 0))
              const invCells = i === 0 && <>
                <td rowSpan={n} className="num gl">{g.n_inv ? money(g.inv) : <span className="z">无票</span>}
                  {n > 1 && <div className="subt">{n} 张计提合计 {money(gAcc)}</div>}</td>
                <td rowSpan={n} className="num">{noInv ? <span className="z">—</span> : <Diff v={g.inv - gAcc} />}</td>
                <td rowSpan={n} className="num">{g.n_inv ? money(g.inv_tax) : <span className="z">—</span>}
                  {!noInv && !isZero(g.inv_tax - gTax) && <div className="subt d">比暂估 {g.inv_tax - gTax > 0 ? '+' : ''}{money(g.inv_tax - gTax)}</div>}</td>
              </>
              let billCells = null
              if (!a) billCells = <><td className="gl hl"></td><td className="hl"></td></>
              else if (!BM) billCells = <><td className="num gl hl z">…</td><td className="num hl z">…</td></>
              else if (!r.grp) billCells = <><td className="num gl hl"><span className="z">账单没有这一类</span></td><td className="num hl"><Diff v={-(a.gross || 0)} /></td></>
              else if (r.runStart) {
                const grp = r.grp
                if (shown.has(grp)) billCells = <td colSpan="2" rowSpan={r.run} className="gl hl z" style={{ textAlign: 'right' }}>↑ 并在 {grp.first} 一起比</td>
                else {
                  shown.add(grp); grp.first = `${a.month}/${a.vno}#`
                  const gsum = r2((b.accruals || []).filter(x => grp.vnos.has(String(x.vno))).reduce((t, x) => t + (x.gross || 0), 0))
                  billCells = <><td rowSpan={r.run} className="num gl hl">{money(grp.bill)}{grp.vnos.size > 1 && <div className="subt">{grp.vnos.size} 张计提合计 {money(gsum)}</div>}</td>
                    <td rowSpan={r.run} className="num hl"><Diff v={grp.diff} /></td></>
                }
              }
              return <tr key={k} className={i === 0 ? 'gfirst' : ''}>
                {k === 0 && head}
                {a ? <>
                  <td><b>{a.fee || '—'}</b>{a.biz && <span className="biz"> · {a.biz}</span>}</td>
                  <td className="mono nw">{a.month}/{a.vno}#</td>
                  <td className="nw">{a.moved ? <>{pctOf(a.rate)} <span className="d">→ <b>{pctOf(a.new_rate)}</b></span></> : <b>{pctOf(a.rate)}</b>}</td>
                  <td className="num">{money(a.gross)}</td>
                </> : <td colSpan="4" className="d">这个税率（{pctOf(g.rate)}）只有发票、没有计提</td>}
                {billCells}
                <td className="num gl">{a ? money(a.tax) : ''}</td>
                {invCells}
              </tr>
            })}
            {extra.map((x, k) => <tr key={'x' + k} className="gfirst">
              <td colSpan="4" className="d"><b>{x.fee || '—'}</b>{x.biz && <span> · {x.biz}</span>} <span className="dim">账单有这一类，金蝶没有对应的计提</span></td>
              <td className="num gl hl">{money(x.bill)}</td><td className="num hl"><Diff v={x.bill} /></td>
              <td className="gl"></td><td className="gl"></td><td></td><td></td>
            </tr>)}
            {why.length > 0 && <tr className="whyrow"><td colSpan="10">{why.map((m, k) => <span key={k} className={k === 0 && !noInv ? '' : 'dim'}>{k > 0 && '；'}{m}</span>)}</td></tr>}
          </tbody>
        })}
        <tfoot><tr>
          <td colSpan="4">合计 <span className="dim">{blocks.length} 张请款单 · 请款 {money(data.req_total)}{nNoInv ? `　·　${nNoInv === blocks.length ? '都还没有发票' : nNoInv + ' 张请款单还没有发票'}` : ''}</span></td>
          <td className="num">{money(T.acc)}</td>
          <td className="num gl">{billTot == null ? <span className="z">…</span> : money(billTot)}</td><td className="num">{billTot == null ? <span className="z">…</span> : <Diff v={billTot - T.acc} />}</td>
          <td className="num gl">{money(T.accTax)}</td>
          <td className="num gl">{money(T.inv)}</td><td className="num">{nNoInv ? <span className="z">—</span> : <Diff v={T.inv - T.acc} />}</td>
          <td className="num">{money(T.invTax)}{!nNoInv && !isZero(T.invTax - T.accTax) && <div className="subt d">比暂估 {T.invTax - T.accTax > 0 ? '+' : ''}{money(T.invTax - T.accTax)}</div>}</td>
        </tr></tfoot>
      </table>

      <div className="ivc-sec">发票明细 <small>{data.n_inv} 张 · 发票管家</small></div>
      <table className="ivc-t">
        <colgroup><col style={{ width: '14%' }} /><col style={{ width: '18%' }} /><col /><col style={{ width: '9%' }} /><col style={{ width: '11%' }} /><col style={{ width: '10%' }} />
          <col style={{ width: '8%' }} /><col style={{ width: '10%' }} /></colgroup>
        <thead><tr><th>请款单</th><th>发票号码</th><th>票种 <span style={{ fontWeight: 400 }}>· 特定业务</span></th><th>税率</th><th className="num">含税</th><th className="num">税额</th><th>纸质件</th><th>做账凭证</th></tr></thead>
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
                <td className="mono">{i.number}</td><td className="ell" title={i.type}>{i.type_short || i.type}{i.type_tag && <span className="dim"> · {i.type_tag}</span>}</td>
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
.ivc .ivc-t{width:100%;border-collapse:collapse;table-layout:fixed;border:1px solid #DCE2E7}
.ivc .ivc-t th{background:#F1F4F6;color:#4A5763;font-weight:600;font-size:12px;text-align:left;padding:6px 10px;border-bottom:1px solid #DCE2E7;white-space:normal}
.ivc .ivc-t th.g{text-align:center;border-left:1px solid #DCE2E7}
.ivc .ivc-t td{padding:7px 10px;border-bottom:1px solid #EDF0F2;vertical-align:middle;white-space:normal;overflow-wrap:anywhere;font-size:13px}
.ivc .ivc-t tr.gfirst td{border-top:1px solid #DCE2E7}
.ivc .ivc-t tbody+tbody tr:first-child td{border-top:1.5px solid #B9C3CB}
.ivc .ivc-t .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ivc .ivc-t .gl{border-left:1px solid #DCE2E7}
.ivc .ivc-t td.req{background:#FAFBFC;border-right:1px solid #DCE2E7;vertical-align:top}
.ivc .ivc-t td.req .amt{float:right;font-variant-numeric:tabular-nums;font-weight:600}
.ivc .ivc-t td.req .sub{margin-top:5px;font-size:11.5px}
.ivc .ivc-t td.req a{color:var(--accent,#1F6E8C);font-size:12px}
.ivc .ivc-t td.vd{border-left:1px solid #DCE2E7;vertical-align:top}
.ivc .ivc-t td.vd .pill+.pill{margin-left:5px}
.ivc .ivc-t .why{font-size:12px;line-height:1.5;margin-top:4px;color:#33414D}
.ivc .ivc-t .subt{font-size:11px;color:#7A8791;font-weight:400;margin-top:2px}
.ivc .ivc-t .biz{color:#5E6B78}
.ivc .ivc-t th.hl{background:#E4EFF4;color:#1F5F78}.ivc .ivc-t td.hl{background:#F5FAFC}
.ivc .ivc-t td[rowspan].hl{border-top:1px solid #DCE2E7;border-bottom:1px solid #DCE2E7}
.ivc .ivc-t tr.whyrow td{background:#FFFBF2;font-size:12px;line-height:1.6;color:#33414D;border-top:1px dashed #E3D7BC}
.ivc .ivc-t .subt.d{color:var(--bad,#B23B2E);font-weight:600}
.ivc .ivc-t tfoot td{background:#F1F4F6;font-weight:700;border-top:1.5px solid #B9C3CB;padding:8px 10px}
.ivc .ivc-t tfoot .dim{font-weight:400}
.ivc .nw{white-space:nowrap}
.ivc .z{color:#A8B2BA;font-weight:400}.ivc .d{color:var(--bad,#B23B2E)}.ivc .dim{color:#7A8791}
.ivc .ok{color:var(--ok,#2E7D57)}.ivc .warn{color:var(--warn,#B06A12)}.ivc .bad{color:var(--bad,#B23B2E)}
.ivc .mono{font-family:Consolas,Menlo,monospace;font-size:12.5px}.ivc .ell{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ivc .pill{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:999px;white-space:nowrap}
.ivc .pill.ok{background:#DCEFE4;color:var(--ok,#2E7D57)}.ivc .pill.warn{background:#F7E9CF;color:var(--warn,#B06A12)}
.ivc .pill.bad{background:#F8DDD8;color:var(--bad,#B23B2E)}.ivc .pill.neu{background:#E7ECEF;color:#5E6B78}
`
