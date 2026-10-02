// [Change Log] Date: 2026-10-02 | Author: Claude Opus 5.5 | Version: V2.749
// Description: 应付板块 › 物流对账 › 付款做账。一张物流请款单付款后合成一张凭证：红冲 → 更正 → 核销（暂估转待认证）→ 支付。
//   上：请款单清单（按能不能做账分组：可做账 / 纸质件未到 / 票不齐 / 发票≠请款 / 未付款 / 已做账）。
//   点开：计提 vs 发票逐张比（核销 / 红冲更正 + 原因）+ 整张凭证预览（带全部核算维度，借贷平衡）。
//   第一版只预览、不写金蝶；纸质件没到可手动放行（用户 2026-10-02 定）。
import React, { useEffect, useMemo, useState } from 'react'
import { voucherList, voucherPreview, voucherPaperOverride } from '../api.js'

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

function Detail({ inst, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => { setD(null); setErr(''); voucherPreview(inst).then(setD).catch(e => setErr(e.message)) }
  useEffect(load, [inst])
  const ovr = on => {
    let note = ''
    if (on) { note = window.prompt('纸质件还没到，确定先做账？写一句原因（会留痕）', '') ; if (note === null) return }
    setBusy(true)
    voucherPaperOverride(inst, on, note).then(() => { load(); onChanged() }).catch(e => alert(e.message)).finally(() => setBusy(false))
  }
  const lines = d?.voucher?.lines || []
  let lastBlock = ''
  return (
    <div className="lv-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="lv-dlg" role="dialog" aria-label="付款做账">
        <div className="lv-dh">
          <b>付款做账</b>
          {d && <span>{d.req.carrier} · {d.req.subject} · <b className="mono">{money(d.req.amount)}</b> · {d.req.period} 账单</span>}
          <span style={{ flex: 1 }} />
          <button className="lv-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        {err && <div className="lv-msg bad">{err}</div>}
        {!d && !err && <div className="lv-empty">读金蝶计提凭证、发票管家票夹…</div>}
        {d && <>
          <div className="lv-meta">
            <span>审批 <span className="mono">{d.req.bid}</span> · {d.req.applicant}</span>
            <span>{d.req.paid ? `付款 ${d.req.paid}${d.req.bank ? ' · ' + d.req.bank : ''}` : '未付款'}</span>
            {d.req.folder && <a href={`#/invaudit?folder=${d.req.folder}`} target="_blank" rel="noopener">发票管家票夹 #{d.req.folder} ↗</a>}
            <span className={'lv-pill ' + (ST[d.req.status] || [])[1]}>{(ST[d.req.status] || [d.req.status])[0]}</span>
            {d.req.paper_ovr
              ? <span className="lv-ovr">纸质件已手动放行（{d.req.paper_ovr.by} {d.req.paper_ovr.at}{d.req.paper_ovr.note ? '：' + d.req.paper_ovr.note : ''}）<button className="lnk" disabled={busy} onClick={() => ovr(false)}>撤销</button></span>
              : d.req.status === 'paper' && <button className="btn" disabled={busy} onClick={() => ovr(true)}>纸质件没到，先做账（手动放行）</button>}
          </div>
          {d.plan.msgs.length > 0 && <ul className={'lv-msgs ' + (d.plan.status === 'ok' ? '' : 'bad')}>{d.plan.msgs.map((m, i) => <li key={i}>{m}</li>)}</ul>}

          <div className="lv-sec">① 计提 vs 发票</div>
          <div className="lv-two">
            <table className="lv-t">
              <thead><tr><th>计提凭证</th><th>摘要</th><th className="num">含税</th><th>税率</th><th className="num">暂估税</th><th>结论</th></tr></thead>
              <tbody>{d.accruals.map(a => <tr key={a.vno}>
                <td className="mono">记-{a.vno}</td><td className="ell" title={a.expl}>{a.expl}</td>
                <td className="num">{money(a.gross)}</td><td>{pct(a.rate)}{a.mode === 'rate' && <> → <b>{pct(a.new_rate)}</b></>}</td>
                <td className="num">{money(a.tax)}</td>
                <td>{a.mode ? <span className={'lv-pill ' + MODE[a.mode][1]} title={a.why}>{MODE[a.mode][0]}</span> : '—'}{a.why && <div className="dim">{a.why}</div>}</td>
              </tr>)}
              {!d.accruals.length && <tr><td colSpan="6" className="lv-empty">金蝶本期没找到这家的计提凭证</td></tr>}
              <tr className="tot"><td colSpan="2">合计</td><td className="num">{money(d.accruals.reduce((s, a) => s + a.gross, 0))}</td><td></td>
                <td className="num">{money(d.accruals.reduce((s, a) => s + a.tax, 0))}</td><td></td></tr></tbody>
            </table>
            <table className="lv-t">
              <thead><tr><th>发票号</th><th>类型</th><th className="num">含税</th><th>税率</th><th className="num">税额</th><th>纸质件</th></tr></thead>
              <tbody>{d.invoices.map(i => <tr key={i.id}>
                <td className="mono">{i.number}{i.booked && <div className="dim">已做账 {(i.vouchers || []).join('、')}</div>}</td>
                <td className="ell" title={i.type}>{i.type}</td><td className="num">{money(i.gross)}</td><td>{i.rate}</td>
                <td className="num">{money(i.tax)}</td><td>{i.paper ? <span className="ok">已到</span> : <span className="warn">未到</span>}</td>
              </tr>)}
              {!d.invoices.length && <tr><td colSpan="6" className="lv-empty">票夹里还没有发票</td></tr>}
              <tr className="tot"><td colSpan="2">合计</td><td className="num">{money(d.invoices.reduce((s, i) => s + i.gross, 0))}</td><td></td>
                <td className="num">{money(d.invoices.reduce((s, i) => s + i.tax, 0))}</td><td></td></tr></tbody>
            </table>
          </div>

          <div className="lv-sec">② 凭证预览 <span className="dim">{d.voucher.book} · 日期 {d.voucher.date} · 记-□（保存到金蝶后分配；核销摘要里的「□」回填本张凭证号）· 第一版只预览、不写金蝶</span></div>
          {d.plan.status !== 'ok'
            ? <div className="lv-msg bad">计提和发票对不上，这张先人工处理（原因见上），不出凭证。</div>
            : <table className="lv-t lv-v">
              <thead><tr><th>#</th><th>摘要</th><th>科目</th><th className="num">借方</th><th className="num">贷方</th><th>核算维度</th></tr></thead>
              <tbody>{lines.map((l, i) => {
                const head = l.block !== lastBlock
                lastBlock = l.block
                return <React.Fragment key={i}>
                  {head && <tr className={'blk ' + BLOCK_CLS[l.block]}><td colSpan="6">{{ 红冲: '一、红冲', 更正: '二、更正', 核销: '三、核销（暂估转待认证）', 支付: '四、支付' }[l.block]}</td></tr>}
                  <tr><td className="dim">{i + 1}</td><td className="expl">{l.expl}</td><td className="nw">{l.acct} {l.acct_name}</td>
                    <td className="num">{l.dr ? money(l.dr) : ''}</td><td className="num">{l.cr ? money(l.cr) : ''}</td>
                    <td className="dims">{dimText(l.dims).map((t, k) => <div key={k}>{t}</div>)}</td></tr>
                </React.Fragment>
              })}
                <tr className="tot"><td colSpan="3">合计 {Math.abs(d.voucher.dr - d.voucher.cr) < 0.005 ? '· 借贷平衡 ✓' : '· ⚠ 借贷不平'}</td>
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
  const load = () => voucherList().then(r => { setRows(r.rows || []); setErr('') }).catch(e => { setErr(e.message); setRows([]) })
  useEffect(() => { load() }, [])
  const cnt = useMemo(() => { const c = {}; (rows || []).forEach(r => { c[r.status] = (c[r.status] || 0) + 1 }); return c }, [rows])
  const shown = (rows || []).filter(r => (!f || r.status === f) &&
    (!q || [r.carrier, r.payee, r.subject, r.bid, r.code].some(x => String(x || '').includes(q))))
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
        {err && <div className="lv-msg bad">{err}</div>}
        <div className="tbl-wrap"><table className="lv-t">
          <thead><tr><th>承运商</th><th>主体</th><th className="num">请款金额</th><th>账单月</th><th>付款</th><th className="num">发票</th><th>纸质件</th><th>状态</th><th></th></tr></thead>
          <tbody>
            {rows === null && <tr><td colSpan="9" className="lv-empty">读取中…</td></tr>}
            {rows && !shown.length && <tr><td colSpan="9" className="lv-empty">没有</td></tr>}
            {shown.map(r => <tr key={r.inst}>
              <td><b>{r.carrier}</b><div className="dim">{r.code}</div></td>
              <td>{r.subject}</td><td className="num">{money(r.amount)}</td><td>{r.period || <span className="warn">未认月份</span>}</td>
              <td>{r.paid || <span className="dim">未付</span>}{r.paid_voucher && <div className="dim">已记 {r.paid_voucher}</div>}</td>
              <td className="num">{r.n_inv ? <>{r.n_inv} 张 · {money(r.inv_total)}</> : <span className="dim">—</span>}</td>
              <td>{r.n_inv ? `${r.n_paper}/${r.n_inv}` : '—'}{r.paper_ovr && <div className="dim">已放行</div>}</td>
              <td><span className={'lv-pill ' + ST[r.status][1]}>{ST[r.status][0]}</span>{r.booked.length > 0 && <div className="dim">{r.booked.join('、')}</div>}</td>
              <td><button className="btn btn-pri" disabled={!r.period} onClick={() => setOpen(r.inst)}>{r.status === 'booked' ? '查看' : '凭证预览'}</button></td>
            </tr>)}
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
.lv .lv-msg{padding:9px 12px;border-radius:8px;font-size:12.5px;margin:8px 0}.lv .lv-msg.bad{background:var(--red-bg);color:var(--red)}
.lv .lv-mask{position:fixed;inset:0;z-index:1000;background:rgba(20,28,40,.38);display:flex;align-items:flex-start;justify-content:center;padding:28px 16px;overflow:auto}
.lv .lv-dlg{width:min(1180px,100%);background:var(--bg);border:1px solid var(--line);border-radius:12px;box-shadow:0 16px 44px rgba(20,28,58,.22);padding:16px 20px 22px}
.lv .lv-dh{display:flex;align-items:center;gap:12px;font-size:15px}
.lv .lv-x{border:0;background:none;font-size:16px;color:var(--ink-3);cursor:pointer}
.lv .lv-meta{display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--ink-2);margin:10px 0}
.lv .lv-ovr{color:var(--amber)}.lv .lnk{border:0;background:none;color:var(--accent);cursor:pointer;font:inherit;font-size:12px;margin-left:6px}
.lv .lv-msgs{margin:6px 0 4px;padding:8px 12px 8px 28px;background:var(--amber-bg);color:var(--amber);border-radius:8px;font-size:12.5px}
.lv .lv-msgs.bad{background:var(--red-bg);color:var(--red)}
.lv .lv-sec{font-weight:700;font-size:13.5px;margin:16px 0 8px}
.lv .lv-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:1100px){.lv .lv-two{grid-template-columns:1fr}}
.lv .lv-v td.expl{max-width:360px}.lv .lv-v td.nw{white-space:nowrap}.lv .lv-v td.dims{font-size:11.5px;color:var(--ink-2);min-width:220px}
.lv .lv-v tr.blk td{font-weight:700;font-size:12px;padding:6px 10px}
.lv .lv-v tr.b-red td{background:var(--red-bg);color:var(--red)}.lv .lv-v tr.b-fix td{background:var(--amber-bg);color:var(--amber)}
.lv .lv-v tr.b-hx td{background:var(--accent-soft);color:var(--accent)}.lv .lv-v tr.b-pay td{background:var(--green-bg);color:var(--green)}
`
