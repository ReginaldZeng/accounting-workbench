// [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
// Description: 应付板块 › 物流对账 › 付款做账。一张物流请款单付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。
//   上：请款单清单（按能不能做账分组：可做账 / 纸质件未到 / 票不齐 / 发票≠请款 / 未付款 / 已做账）。
//   点开：计提 vs 发票逐张比（核销 / 红冲更正 + 原因）+ 整张凭证预览（带全部核算维度，借贷平衡）。
//   第一版只预览、不写金蝶；纸质件没到可手动放行（用户 2026-10-02 定）。
import React, { useEffect, useMemo, useState } from 'react'
import { voucherList, voucherPreview, voucherPaperOverride, voucherPlans, voucherPost } from '../api.js'

const money = n => (n == null || n === '' ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const pct = r => (r == null ? '—' : `${Math.round(r * 10000) / 100}%`)
const ST = {
  ready: ['可做账', 'ok', '已付款、发票齐、纸质件已到（或已放行）'],
  paper: ['纸质件未到', 'warn', '纸质付款单/发票还没交到财务；可手动放行'],
  invdiff: ['发票≠请款', 'bad', '票夹里发票含税合计和请款金额对不上'],
  noinv: ['票不齐', 'warn', '票夹里还没有发票（流程里没传，要财务在收票台补）'],
  unpaid: ['未付款', 'neu', '金蝶还没有付款单'],
  booked: ['已做账', 'done', '发票都已被金蝶凭证引用（发票管家同步）'],
}
const ORDER = ['ready', 'paper', 'invdiff', 'noinv', 'unpaid', 'booked']
// 做账类型(要读金蝶计提，列表出来后再逐张补)：计提与发票一致只核销 / 含尾差 / 要红冲更正 / 计提记错主体 / 金额不符 / 没有计提
const KIND = {
  hx: ['一致·只核销', 'ok'], tail: ['一致·核销含尾差', 'ok'], redo: ['需红冲更正', 'bad'],
  subj: ['计提记错主体', 'bad'], manual: ['金额不符·人工', 'warn'], noacc: ['没有计提', 'warn'], err: ['读取失败', 'neu'],
}
const KIND_ORDER = ['hx', 'tail', 'redo', 'subj', 'manual', 'noacc']
const BLOCK_CLS = { 红冲: 'b-red', 更正: 'b-fix', 核销: 'b-hx', 支付: 'b-pay' }
const MODE = { hx: ['核销', 'ok'], rate: ['红冲+更正', 'bad'], fix: ['红冲+更正', 'bad'] }

function dimText(d) {
  if (!d) return ''
  const cn = (c, n) => [c, n].filter(Boolean).join(' ')
  return [d.sup_code || d.sup_name ? '供应商 ' + cn(d.sup_code, d.sup_name) : '',
    d.dept_code || d.dept ? '部门 ' + cn(d.dept_code, d.dept) : '',
    d.fee_code || d.fee ? '费用项目 ' + cn(d.fee_code, d.fee) : '',
    d.biz_code || d.biz ? '产品分类 ' + cn(d.biz_code, d.biz) : '',
    d.proj_code || d.proj ? '产品项目 ' + cn(d.proj_code, d.proj) : '',
    d.bank !== undefined ? '银行账号 ' + (d.bank || '（待补）') : ''].filter(Boolean)
}

// 预览弹窗（V2.752 重排，用户「有点乱了，重新设计下」）：从上往下读——头部 → 一句结论 → 按税率核对 → 计提凭证 → 发票 → 凭证预览。
// 两张表上下排满宽(不再并排挤爆)；计提表不放长摘要(放悬停)；凭证里同一块后面相同摘要写「同上」，核销待认证行只写「发票号 + 同上」。
const rateOf = s => { const m = String(s ?? '').match(/[\d.]+/); if (!m) return null; const f = parseFloat(m[0]); return f > 1 ? f / 100 : f }
const r2 = x => Math.round((x + 1e-9) * 100) / 100

function rateCheck(acc, inv) {
  // 每个税率：计提(原) / 更正后 / 发票，更正后 = 发票 才算对上
  const g = {}
  const put = (r, k, v) => { const key = Math.round(r * 10000); (g[key] = g[key] || { r, acc: 0, fixed: 0, inv: 0 })[k] += v }
  acc.forEach(a => { put(a.rate, 'acc', a.gross); put(a.mode === 'rate' || a.mode === 'fix' ? (a.new_rate ?? a.rate) : a.rate, 'fixed', a.gross) })
  inv.forEach(i => { const r = rateOf(i.rate); if (r != null) put(r, 'inv', i.gross) })
  return Object.values(g).sort((a, b) => a.r - b.r).map(x => ({ ...x, acc: r2(x.acc), fixed: r2(x.fixed), inv: r2(x.inv) }))
}

function dimLine(d) {
  return dimText(d).join(' · ')
}

function Detail({ inst, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => { setD(null); setErr(''); voucherPreview(inst).then(setD).catch(e => setErr(e.message)) }
  useEffect(load, [inst])
  const ovr = on => {
    let note = ''
    if (on) { note = window.prompt('纸质件还没到，确定先做账？写一句原因（会留痕）', ''); if (note === null) return }
    setBusy(true)
    voucherPaperOverride(inst, on, note).then(() => { load(); onChanged() }).catch(e => alert(e.message)).finally(() => setBusy(false))
  }
  const [posting, setPosting] = useState(null)   // 写金蝶结果 {ok, msg, steps}
  const post = () => {
    if (!window.confirm(`保存到金蝶：
① 系统提交并审核这张金蝶付款单（审核人显示「系统操作员」）
② 金蝶自动生成付款凭证后，往里补红冲/更正/核销分录、改支付摘要
凭证本身不审核，留给你在金蝶核对后审核。

确定？`)) return
    setPosting({ busy: true })
    voucherPost(inst).then(r => { setPosting({ ok: true, ...r }); load(); onChanged() })
      .catch(e => setPosting({ ok: false, msg: e.message }))
  }
  const lines = d?.voucher?.lines || []
  const rc = d ? rateCheck(d.accruals, d.invoices) : []
  const accSum = k => r2((d?.accruals || []).reduce((s, a) => s + (a[k] || 0), 0))
  const invSum = k => r2((d?.invoices || []).reduce((s, i) => s + (i[k] || 0), 0))
  return (
    <div className="lv-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="lv-dlg" role="dialog" aria-label="付款做账">
        <div className="lv-dh">
          <div className="lv-title">
            {d ? <><span className="cd">{d.req.code}</span><b>{d.req.payee}</b><span className="sep">·</span>{d.req.subject}
              <span className="sep">·</span><b className="mono">{money(d.req.amount)}</b><span className="sep">·</span>{d.req.period} 账单</> : <b>付款做账</b>}
          </div>
          <button className="lv-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        {err && <div className="lv-msg bad">{err}</div>}
        {!d && !err && <div className="lv-empty">读金蝶计提凭证、发票管家票夹…</div>}
        {d && <>
          <div className="lv-meta">
            <span>审批 <span className="mono">{d.req.bid}</span> · {d.req.applicant}</span>
            <span>{d.req.paid ? <>付款 {d.req.paid}{d.req.bank && <> · {d.req.bank}</>}</> : '未付款'}</span>
            {d.req.folder && <a href={`#/invaudit?folder=${d.req.folder}`} target="_blank" rel="noopener">发票管家票夹 #{d.req.folder} ↗</a>}
            <span style={{ flex: 1 }} />
            <span className={'lv-pill ' + (ST[d.req.status] || [])[1]}>{(ST[d.req.status] || [d.req.status])[0]}</span>
            {d.req.paper_ovr
              ? <span className="lv-ovr">已手动放行（{d.req.paper_ovr.by} {d.req.paper_ovr.at}{d.req.paper_ovr.note ? '：' + d.req.paper_ovr.note : ''}）<button className="lnk" disabled={busy} onClick={() => ovr(false)}>撤销</button></span>
              : d.req.status === 'paper' && <button className="btn sm" disabled={busy} onClick={() => ovr(true)}>纸质件没到，先做账</button>}
          </div>

          {d.req.posted && <div className="lv-verdict ok"><span className="lv-pill ok">已写入金蝶</span>
            付款单 {d.req.posted.bill_no} 已审核 · 凭证 <b>记-{d.req.posted.vno}</b>（{d.req.posted.book}）已补分录，借 {money(d.req.posted.dr)} = 贷 {money(d.req.posted.cr)} · {d.req.posted.by} {d.req.posted.at} · 凭证请在金蝶核对后审核</div>}
          {posting && !posting.busy && <div className={'lv-msg ' + (posting.ok ? 'okb' : 'bad')}>{posting.ok ? `已写入：记-${posting.vno}（${(posting.steps || []).join(' → ')}）` : posting.msg}</div>}
          {d.kind && KIND[d.kind] && <div className={'lv-verdict ' + KIND[d.kind][1]}><span className={'lv-pill ' + KIND[d.kind][1]}>{KIND[d.kind][0]}</span>{d.kind_text}</div>}
          {d.plan.msgs.filter(m => m !== d.kind_text).length > 0 &&
            <ul className="lv-notes">{d.plan.msgs.filter(m => m !== d.kind_text).map((m, i) => <li key={i}>{m}</li>)}</ul>}

          <div className="lv-sec">① 按税率核对 <span className="dim">计提按税率合计，红冲更正后要等于同税率的发票</span></div>
          <table className="lv-t lv-rc">
            <thead><tr><th>税率</th><th className="num">计提</th><th className="num">更正后</th><th className="num">发票</th><th></th></tr></thead>
            <tbody>{rc.map(x => {
              const ok = Math.abs(x.fixed - x.inv) < 0.005
              return <tr key={x.r}><td><b>{pct(x.r)}</b></td><td className="num">{money(x.acc)}</td>
                <td className="num">{Math.abs(x.acc - x.fixed) < 0.005 ? <span className="dim">同左</span> : <b>{money(x.fixed)}</b>}</td>
                <td className="num">{money(x.inv)}</td><td>{ok ? <span className="ok">✓ 对上</span> : <span className="bad">差 {money(r2(x.inv - x.fixed))}</span>}</td></tr>
            })}</tbody>
          </table>

          <div className="lv-sec">② 计提凭证 <span className="dim">{d.accruals.length} 张 · 金蝶 {d.req.period}</span></div>
          <table className="lv-t lv-fix">
            <colgroup><col style={{ width: 110 }} /><col /><col style={{ width: 130 }} /><col style={{ width: 120 }} /><col style={{ width: 110 }} /><col style={{ width: '30%' }} /></colgroup>
            <thead><tr><th>凭证</th><th>费用项目</th><th className="num">含税</th><th>税率</th><th className="num">暂估税</th><th>处理</th></tr></thead>
            <tbody>
              {d.accruals.map(a => <tr key={a.vno} title={a.expl}>
                <td className="mono">{a.month}/{a.vno}#</td><td>{a.fee || '—'}</td>
                <td className="num">{money(a.gross)}</td>
                <td className="nw">{a.mode === 'rate' ? <>{pct(a.rate)} → <b className="bad">{pct(a.new_rate)}</b></> : pct(a.rate)}</td>
                <td className="num">{money(a.tax)}</td>
                <td>{a.mode === 'hx' ? <span className="ok">核销</span> : a.mode ? <><span className="bad">红冲 + 更正</span><span className="dim"> · {a.why}</span></> : <span className="dim">—</span>}</td>
              </tr>)}
              {!d.accruals.length && <tr><td colSpan="6" className="lv-empty">金蝶本期没找到这家的计提凭证</td></tr>}
              <tr className="tot"><td colSpan="2">合计</td><td className="num">{money(accSum('gross'))}</td><td></td><td className="num">{money(accSum('tax'))}</td><td></td></tr>
            </tbody>
          </table>

          <div className="lv-sec">③ 发票 <span className="dim">{d.invoices.length} 张 · 发票管家</span></div>
          <table className="lv-t lv-fix">
            <colgroup><col style={{ width: 200 }} /><col /><col style={{ width: 130 }} /><col style={{ width: 120 }} /><col style={{ width: 110 }} /><col style={{ width: '30%' }} /></colgroup>
            <thead><tr><th>发票号</th><th>类型</th><th className="num">含税</th><th>税率</th><th className="num">税额</th><th>纸质件 / 做账</th></tr></thead>
            <tbody>
              {d.invoices.map(i => <tr key={i.id}>
                <td className="mono">{i.number}</td><td className="ell" title={i.type}>{i.type}</td>
                <td className="num">{money(i.gross)}</td><td>{i.rate}</td><td className="num">{money(i.tax)}</td>
                <td>{i.paper ? <span className="ok">纸质件已到</span> : <span className="warn">纸质件未到</span>}
                  {i.booked && <span className="dim"> · 已做账 {(i.vouchers || []).join('、')}</span>}</td>
              </tr>)}
              {!d.invoices.length && <tr><td colSpan="6" className="lv-empty">票夹里还没有发票</td></tr>}
              <tr className="tot"><td colSpan="2">合计</td><td className="num">{money(invSum('gross'))}</td><td></td><td className="num">{money(invSum('tax'))}</td><td></td></tr>
            </tbody>
          </table>

          <div className="lv-sec">④ 凭证预览 <span className="dim">{d.voucher.book} · {d.voucher.date} · 写入时并进金蝶付款单自动生成的那张凭证（支付两行在前），核销摘要里的 □ 填该凭证号</span>
            <span style={{ flex: 1 }} />
            {!d.req.posted && d.plan.status === 'ok' && (d.req.status === 'ready'
              ? <button className="btn btn-pri" disabled={!!posting?.busy || !d.req.bill_id} title={d.req.bill_id ? '' : '没有金蝶付款单'} onClick={post}>{posting?.busy ? '写金蝶中…（约半分钟）' : '保存到金蝶'}</button>
              : <span className="dim">（{ST[d.req.status]?.[0]}，还不能写金蝶）</span>)}</div>
          {d.plan.status !== 'ok'
            ? <div className="lv-msg bad">计提和发票对不上，这张先人工处理（原因见上），不出凭证。</div>
            : <table className="lv-t lv-v">
              <colgroup><col style={{ width: 34 }} /><col /><col style={{ width: 170 }} /><col style={{ width: 120 }} /><col style={{ width: 120 }} /><col style={{ width: '32%' }} /></colgroup>
              <thead><tr><th>#</th><th>摘要</th><th>科目</th><th className="num">借方</th><th className="num">贷方</th><th>核算维度</th></tr></thead>
              <tbody>{(() => {
                const out = []
                let blk = '', base = ''
                lines.forEach((l, i) => {
                  if (l.block !== blk) {
                    blk = l.block; base = ''
                    out.push(<tr key={'h' + i} className={'blk ' + BLOCK_CLS[l.block]}><td colSpan="6">{{ 红冲: '一、红冲', 更正: '二、更正', 核销: '三、核销（暂估转待认证）', 支付: '四、支付' }[l.block]}</td></tr>)
                  }
                  // 同一块里摘要相同写「同上」；核销待认证行＝发票号 + 共同摘要
                  const m = l.expl.match(/^(\d{8,20})(核销.*)$/)
                  let txt
                  if (m && base === m[2]) txt = <><span className="mono">{m[1]}</span> <span className="dim">+ 同上</span></>
                  else if (!m && base === l.expl) txt = <span className="dim">同上</span>
                  else { txt = l.expl; base = m ? m[2] : l.expl }
                  out.push(<tr key={i}><td className="dim">{i + 1}</td><td className="expl" title={l.expl}>{txt}</td>
                    <td className="nw">{l.acct} {l.acct_name}</td>
                    <td className="num">{l.dr ? money(l.dr) : ''}</td><td className="num">{l.cr ? money(l.cr) : ''}</td>
                    <td className="dims">{dimLine(l.dims)}</td></tr>)
                })
                return out
              })()}
                <tr className="tot"><td colSpan="3">合计　{Math.abs(d.voucher.dr - d.voucher.cr) < 0.005 ? <span className="ok">借贷平衡 ✓</span> : <span className="bad">借贷不平</span>}</td>
                  <td className="num">{money(d.voucher.dr)}</td><td className="num">{money(d.voucher.cr)}</td><td></td></tr>
              </tbody>
            </table>}
        </>}
      </div>
    </div>
  )
}

export default function LogisticsVoucher() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [f, setF] = useState('ready')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const [plans, setPlans] = useState({})        // inst → {kind, text}
  const [kf, setKf] = useState('')
  const load = () => voucherList().then(r => {
    const rs = r.rows || []
    setRows(rs); setErr('')
    // 做账类型：有账单月、有发票的才算；分几批取，先出的先显示
    const ids = rs.filter(x => x.period && x.n_inv && x.status !== 'booked').map(x => x.inst)
    const batches = []
    for (let i = 0; i < ids.length; i += 5) batches.push(ids.slice(i, i + 5))
    batches.reduce((p, b) => p.then(() => voucherPlans(b).then(o => setPlans(old => ({ ...old, ...(o.plans || {}) }))).catch(() => {})), Promise.resolve())
  }).catch(e => { setErr(e.message); setRows([]) })
  useEffect(() => { load() }, [])
  const cnt = useMemo(() => { const c = {}; (rows || []).forEach(r => { c[r.status] = (c[r.status] || 0) + 1 }); return c }, [rows])
  const kcnt = useMemo(() => { const c = {}; Object.values(plans).forEach(p => { c[p.kind] = (c[p.kind] || 0) + 1 }); return c }, [plans])
  const shown = (rows || []).filter(r => (!f || r.status === f) && (!kf || (plans[r.inst] || {}).kind === kf) &&
    (!q || [r.carrier, r.payee, r.sup_full, r.subject, r.bid, r.code].some(x => String(x || '').includes(q))))
  return (
    <div className="lv">
      <style>{CSS}</style>
      <div className="head"><div><div className="h-title">付款做账 · 物流请款单</div>
        <div className="h-sub">付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。计提取金蝶、发票取发票管家、维度更正取复核台登记。第一版只预览，不写金蝶。</div></div></div>
      <div className="body">
        <div className="lv-bar">
          {ORDER.map(k => <button key={k} className={'lv-chip ' + ST[k][1] + (f === k ? ' on' : '')} title={ST[k][2]} onClick={() => setF(f === k ? '' : k)}>{ST[k][0]}<b>{cnt[k] || 0}</b></button>)}
          <button className={'lv-chip' + (!f ? ' on' : '')} onClick={() => setF('')}>全部<b>{(rows || []).length}</b></button>
          <span style={{ flex: 1 }} />
          <input type="search" placeholder="搜承运商/主体/审批编号" value={q} onChange={e => setQ(e.target.value)} />
          <button className="btn" onClick={load}>刷新</button>
        </div>
        <div className="lv-bar">
          <span className="dim">做账类型</span>
          {KIND_ORDER.map(k => <button key={k} className={'lv-chip ' + KIND[k][1] + (kf === k ? ' on' : '')} onClick={() => setKf(kf === k ? '' : k)}>{KIND[k][0]}<b>{kcnt[k] || 0}</b></button>)}
          {kf && <button className="lnk" onClick={() => setKf('')}>不限</button>}
        </div>
        {err && <div className="lv-msg bad">{err}</div>}
        <div className="tbl-wrap"><table className="lv-t lv-list">
          <thead><tr><th>主体</th><th>物流商</th><th>费用类型</th><th className="num">计提金额</th><th>税率</th><th>计提凭证</th>
            <th>审核结果</th><th className="num">付款金额</th><th>付款单状态</th><th></th></tr></thead>
          <tbody>
            {rows === null && <tr><td colSpan="10" className="lv-empty">读取中…</td></tr>}
            {rows && !shown.length && <tr><td colSpan="10" className="lv-empty">没有</td></tr>}
            {shown.map(r => {
              const p = plans[r.inst]
              const acc = (p && p.acc) || []
              const calc = !!r.period && !!r.n_inv && r.status !== 'booked'
              const pending = calc && !p
              // 一张请款单对应几张计提：费用类型/金额/税率/凭证号/审核结果 逐张上下对齐
              const stack = (fn, cls = '') => acc.length
                ? <div className={'ml ' + cls}>{acc.map(a => <div key={a.vno} title={a.expl}>{fn(a)}</div>)}</div>
                : <span className="dim">{pending ? '计算中…' : '—'}</span>
              const [klb, kcl] = p ? (KIND[p.kind] || [p.kind, 'neu']) : ['', '']
              const pay = r.paid
                ? (r.paid_voucher ? <>已记支付凭证 <b>{r.paid_voucher}</b></> : <>付款单 · {r.pay_st || '已生成'}<div className="dim">{r.paid}</div></>)
                : (r.dt_status === 'RUNNING' ? <span className="dim">钉钉审批中</span> : <span className="dim">已通过 · 待付款</span>)
              return <tr key={r.inst}>
                <td className="nw"><span className="cd">{r.book}</span>{r.subject}</td>
                <td className="sup"><span className="cd">{r.code}</span>{r.sup_full}</td>
                <td>{stack(a => a.fee || '—')}</td>
                <td className="num">{stack(a => money(a.gross))}</td>
                <td>{stack(a => a.mode === 'rate' ? <>{pct(a.rate)} → <b className="bad">{pct(a.new_rate)}</b></> : pct(a.rate))}</td>
                <td>{stack(a => <span className="mono">{a.month}/{a.vno}#</span>)}</td>
                <td className="kd">
                  {acc.length > 0 && <div className="ml">{acc.map(a => <div key={a.vno} title={a.why}>{a.mode === 'hx' ? <span className="ok">核销</span>
                    : a.mode ? <span className="bad">红冲更正</span> : <span className="dim">—</span>}</div>)}</div>}
                  {p && <div className="kres"><span className={'lv-pill ' + kcl} title={p.text}>{klb}</span>
                    {['subj', 'manual', 'noacc', 'err'].includes(p.kind) && <div className="dim kt">{p.text}</div>}</div>}
                  {!calc && <span className="dim">{!r.period ? '未认账单月' : !r.n_inv ? '票夹没有发票' : '已做账'}</span>}
                  {pending && <span className="dim">计算中…</span>}
                </td>
                <td className="num">{money(r.amount)}<div className="dim">{r.n_inv ? `发票 ${r.n_inv} 张 · ${money(r.inv_total)}` : '没有发票'}</div></td>
                <td className="nw">{pay}<div className="dim">纸质件 {r.n_inv ? `${r.n_paper}/${r.n_inv}` : '—'}{r.paper_ovr ? ' · 已放行' : ''}</div>
                  <span className={'lv-pill ' + ST[r.status][1]}>{ST[r.status][0]}</span>{r.posted && <div className="dim">已写金蝶 记-{r.posted.vno}</div>}</td>
                <td><button className="btn btn-pri" disabled={!r.period} onClick={() => setOpen(r.inst)}>{r.status === 'booked' ? '查看' : '凭证预览'}</button></td>
              </tr>
            })}
          </tbody>
        </table></div>
      </div>
      {open && <Detail inst={open} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  )
}

const CSS = `
.lv .lv-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.lv .lv-bar input{font:inherit;font-size:12.5px;padding:5px 9px;border:1px solid var(--line-strong);border-radius:7px;background:var(--bg);color:var(--ink);width:200px}
.lv .lv-chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:12.5px;color:var(--ink)}
.lv .lv-chip b{font-variant-numeric:tabular-nums}
.lv .lv-chip.ok b{color:var(--green)}.lv .lv-chip.warn b{color:var(--amber)}.lv .lv-chip.bad b{color:var(--red)}.lv .lv-chip.done b{color:var(--accent)}
.lv .lv-chip.on{background:var(--accent);border-color:var(--accent);color:#fff}.lv .lv-chip.on b{color:#fff}
.lv .lv-t{width:100%;border-collapse:collapse;font-size:12.5px}
.lv .lv-t th{text-align:left;font-size:11.5px;color:var(--ink-3);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap;background:var(--bg-sub)}
.lv .lv-t td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.lv .lv-t .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.lv .lv-t tr.tot td{font-weight:700;background:var(--bg-sub)}
.lv .dim{color:var(--ink-3);font-size:11.5px}.lv .ok{color:var(--green)}.lv .warn{color:var(--amber)}
.lv .mono{font-family:var(--font-mono)}
.lv .ell{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lv .lv-pill{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:999px;white-space:nowrap}
.lv .lv-pill.ok{background:var(--green-bg);color:var(--green)}.lv .lv-pill.warn{background:var(--amber-bg);color:var(--amber)}
.lv .lv-pill.bad{background:var(--red-bg);color:var(--red)}.lv .lv-pill.neu{background:var(--gray-bg);color:var(--ink-2)}.lv .lv-pill.done{background:var(--accent-soft);color:var(--accent)}
.lv .lv-empty{color:var(--ink-3);text-align:center;padding:18px}
.lv .lv-msg{padding:9px 12px;border-radius:8px;font-size:12.5px;margin:8px 0}.lv .lv-msg.bad{background:var(--red-bg);color:var(--red)}.lv .lv-msg.okb{background:var(--green-bg);color:var(--green)}
.lv .lv-sec{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.lv .lv-mask{position:fixed;inset:0;z-index:1000;background:rgba(20,28,40,.38);display:flex;align-items:flex-start;justify-content:center;padding:28px 16px;overflow:auto}
.lv .lv-dlg{width:min(1100px,100%);background:var(--bg);border:1px solid var(--line);border-radius:12px;box-shadow:0 16px 44px rgba(20,28,58,.22);padding:16px 20px 22px}
.lv .lv-dh{display:flex;align-items:flex-start;gap:12px}
.lv .lv-title{flex:1;min-width:0;font-size:15.5px;line-height:1.5}.lv .lv-title .sep{color:var(--ink-3);margin:0 6px}
.lv .lv-verdict{display:flex;gap:10px;align-items:center;padding:10px 14px;border-radius:9px;font-size:13.5px;margin:4px 0 2px;background:var(--bg-sub);border:1px solid var(--line)}
.lv .lv-verdict.bad{background:var(--red-bg);border-color:var(--red-line)}.lv .lv-verdict.ok{background:var(--green-bg);border-color:var(--green-line)}.lv .lv-verdict.warn{background:var(--amber-bg);border-color:var(--amber-line)}
.lv .lv-notes{margin:6px 0 0;padding:0 0 0 20px;font-size:12.5px;color:var(--ink-2)}
.lv .lv-rc{width:auto;min-width:520px}
.lv .lv-fix,.lv .lv-v{table-layout:fixed}.lv .lv-fix td,.lv .lv-v td{overflow-wrap:anywhere}
.lv .btn.sm{padding:3px 10px;font-size:12px}
.lv .lv-x{border:0;background:none;font-size:16px;color:var(--ink-3);cursor:pointer}
.lv .lv-meta{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--ink-2);margin:10px 0}
.lv .lv-ovr{color:var(--amber)}.lv .lnk{border:0;background:none;color:var(--accent);cursor:pointer;font:inherit;font-size:12px;margin-left:6px}
.lv .lv-kind{font-size:13px;margin:6px 0}
.lv .lv-list td{vertical-align:top}.lv .lv-list .ml>div{height:21px;line-height:21px;white-space:nowrap}
.lv .lv-list td.nw{white-space:nowrap}.lv .lv-list td.sup{min-width:190px;max-width:240px}
.lv .cd{font-family:var(--font-mono);font-size:10.5px;color:var(--ink-2);background:var(--gray-bg);border-radius:3px;padding:0 4px;margin-right:5px;white-space:nowrap}.lv .bad{color:var(--red)}.lv .lv-list .kres{margin-top:4px}.lv .lv-list .lv-pill{margin-top:3px}.lv td.kd{max-width:280px}.lv td.kd .kt{white-space:normal;line-height:1.4;margin-top:3px}
.lv .lv-msgs{margin:6px 0 4px;padding:8px 12px 8px 28px;background:var(--amber-bg);color:var(--amber);border-radius:8px;font-size:12.5px}
.lv .lv-msgs.bad{background:var(--red-bg);color:var(--red)}
.lv .lv-sec{font-weight:700;font-size:13.5px;margin:16px 0 8px}
.lv .lv-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:1100px){.lv .lv-two{grid-template-columns:1fr}}
.lv .lv-v td.expl{line-height:1.45}.lv td.nw{white-space:nowrap}.lv .lv-v td.dims{font-size:11.5px;color:var(--ink-2);line-height:1.45}
.lv .lv-v tr.blk td{font-weight:700;font-size:12px;padding:6px 10px}
.lv .lv-v tr.b-red td{background:var(--red-bg);color:var(--red)}.lv .lv-v tr.b-fix td{background:var(--amber-bg);color:var(--amber)}
.lv .lv-v tr.b-hx td{background:var(--accent-soft);color:var(--accent)}.lv .lv-v tr.b-pay td{background:var(--green-bg);color:var(--green)}
`
