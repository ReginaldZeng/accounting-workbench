// [Change Log] Date:2026-09-26 Author:Claude Opus 4.8 Version:V2.632
// 物流账单复核台：核价(合同价格卡) × 核量(金蝶数量) → 归一态。异常优先——不摆全量，只把不对的顶上来。
// pilot=迅鸽：导入《附件二》价格卡 → 上传账单解析落中间表 → 接金蝶回填出库数量 → 逐单复核。
import React, { useEffect, useState, useCallback } from 'react'
import { reviewResult, reviewImportPriceCard, reviewParseBill, reviewKingdeeQty, reviewCarriers } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const PS = { ok: ['通过', 'ok'], over: ['多收', 'bad'], under: ['账单少收', 'neu'], free: ['账单未收·我方有利', 'neu'], gap: ['价卡缺·待确认', 'warn'] }

export default function LogisticsReview({ cfg, onPeriod }) {
  const period = `${cfg.year}-${String(cfg.period).padStart(2, '0')}`
  const [carrier, setCarrier] = useState('迅鸽')
  const [group, setGroup] = useState('ex')
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [sups, setSups] = useState(null)
  const [supq, setSupq] = useState('')

  const load = useCallback(() => {
    reviewResult(carrier, period, group, page, q).then(setD).catch(e => setMsg(e.message))
  }, [carrier, period, group, page, q])
  useEffect(() => { load() }, [load])
  // 本月有计提的承运商（金蝶 2241 计提凭证）——随账期变。金蝶取数慢，防串更新：只认最新账期的响应，
  // 否则快速切月时先发的旧月响应后到会覆盖新月（曾出现 9 期显示 8 期承运商）。
  useEffect(() => {
    let alive = true
    setSups(null)
    reviewCarriers(period).then(r => { if (alive) setSups(r.carriers || []) }).catch(() => { if (alive) setSups([]) })
    return () => { alive = false }
  }, [period])

  const flash = t => { setMsg(t); setTimeout(() => setMsg(''), 6000) }
  const onFile = (fn, ...args) => e => {
    const f = e.target.files && e.target.files[0]; e.target.value = ''
    if (!f) return
    setBusy(fn.name); fn(...args, f).then(r => { flash(JSON.stringify(r)); setGroup('ex'); setPage(1); load() })
      .catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }
  const kingdee = () => { setBusy('kd'); reviewKingdeeQty(carrier, period).then(r => { flash(`金蝶出库单 ${r.kd_docs} 单，回填 ${r.filled} 行`); load() }).catch(e => flash('失败：' + e.message)).finally(() => setBusy('')) }

  const c = (d && d.counts) || {}
  const QUEUE = [
    { f: 'miss', sw: 'warn', n: '核量 · 金蝶查无出库单', dd: '账单单号在金蝶未匹配（拆单后缀/未审核）', c: c.miss || 0, u: '笔' },
    { f: 'qtydiff', sw: 'warn', n: '核量 · 账单数量≠金蝶出库数量', dd: '账单件数与金蝶出库数量不符', c: c.qtydiff || 0, u: '笔' },
    { f: 'gap', sw: 'warn', n: '核价 · 价格卡缺口', dd: '空运/快运/自提/同城等合同未覆盖', c: c.gap || 0, u: '笔' },
    { f: 'free', sw: 'neu', n: '核价 · 账单未收费（我方有利）', dd: '标准应收但账单未计，不追', c: c.free || 0, u: '笔' },
    { f: 'pass', sw: 'ok', n: '两轴均通过', dd: '单价＝合同 且 金蝶匹配', c: c.pass || 0, u: '笔' },
  ]

  return (
    <div className="lrv">
      <style>{`
      .lrv{--ok:#2E7D57;--warn:#B06A12;--bad:#B23B2E;--neu:#5E6B78;--accent:#1F6E8C;--soft:#E1EEF3;font-size:14px}
      .lrv .head{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin-bottom:14px}
      .lrv .h-title{font-size:18px;font-weight:700}.lrv .h-sub{color:#5E6B78;font-size:12.5px;margin-top:2px}
      .lrv .chip{font-size:12.5px;padding:4px 11px;border-radius:999px;border:1px solid #DCE2E7;background:#fff;color:#5E6B78;cursor:pointer}
      .lrv .chip.on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
      .lrv .supbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:#fff;border:1px solid #DCE2E7;border-radius:12px;padding:9px 14px;margin-bottom:12px}
      .lrv .supbar-lb{font-size:12.5px;color:#5E6B78;font-weight:600;white-space:nowrap}
      .lrv .supchips{display:flex;gap:6px;flex-wrap:wrap;flex:1}
      .lrv .supchip{font-size:12.5px;padding:4px 10px;border-radius:999px;border:1px solid #DCE2E7;background:#fff;color:#1B2733;cursor:pointer;display:inline-flex;align-items:center;gap:5px}
      .lrv .supchip:hover{border-color:var(--accent)}
      .lrv .supchip.on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
      .lrv .supchip em{font-style:normal;font-family:ui-monospace,monospace;font-size:11px;opacity:.75}
      .lrv .supchip i{font-style:normal;font-size:10.5px;color:var(--warn);background:#F7E9CF;border-radius:4px;padding:0 4px}
      .lrv .supchip.on i{color:#fff;background:rgba(255,255,255,.25)}
      .lrv .supchip.nospec{color:#8A96A2}
      .lrv .supempty{font-size:12px;color:#8A96A2}
      .lrv .btn{font-size:12.5px;padding:6px 12px;border-radius:7px;border:1px solid #DCE2E7;background:#fff;cursor:pointer;display:inline-block}
      .lrv .btn.pri{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
      .lrv .btn[disabled]{opacity:.5;cursor:default}
      .lrv .verdict{background:#fff;border:1px solid #DCE2E7;border-radius:12px;padding:14px 18px;display:grid;grid-template-columns:1.5fr repeat(4,1fr);gap:6px 20px;align-items:end;margin-bottom:12px}
      .lrv .verdict .lead{grid-column:1/-1;color:#5E6B78;font-size:12.5px}
      .lrv .stat .v{font-family:ui-monospace,monospace;font-size:21px;font-weight:600}
      .lrv .stat .l{font-size:11px;color:#8A96A2}
      .lrv .stat.ok .v{color:var(--ok)}.lrv .stat.accent .v{color:var(--accent)}.lrv .stat.warn .v{color:var(--warn)}
      .lrv .cols{display:grid;grid-template-columns:1fr 2fr;gap:12px;align-items:start}
      .lrv .card{background:#fff;border:1px solid #DCE2E7;border-radius:12px;overflow:hidden;margin-bottom:12px}
      .lrv .card h3{margin:0;padding:11px 15px;font-size:13px;border-bottom:1px solid #DCE2E7;color:#5E6B78}
      .lrv .qrow{display:flex;gap:11px;align-items:center;padding:10px 15px;border-bottom:1px solid #DCE2E7;cursor:pointer;width:100%;text-align:left;background:none;border-left:0;border-right:0;border-top:0;font:inherit}
      .lrv .qrow:hover{background:#F7F9F9}.lrv .qrow.on{background:var(--soft)}
      .lrv .qrow .sw{width:9px;height:9px;border-radius:3px}.lrv .sw.ok{background:var(--ok)}.lrv .sw.warn{background:var(--warn)}.lrv .sw.neu{background:var(--neu)}
      .lrv .qrow .t{flex:1}.lrv .qrow .t .n{font-weight:600;font-size:13px}.lrv .qrow .t .dd{font-size:11.5px;color:#5E6B78}
      .lrv .qrow .cc{font-family:ui-monospace,monospace;font-weight:600;font-size:15px}
      .lrv table{border-collapse:collapse;width:100%;font-size:13px}
      .lrv th,.lrv td{padding:7px 11px;text-align:left;border-bottom:1px solid #DCE2E7;white-space:nowrap}
      .lrv th{font-size:11px;color:#8A96A2;background:#F7F9F9}
      .lrv td.num,.lrv th.num{text-align:right;font-family:ui-monospace,monospace}
      .lrv .tw{overflow-x:auto}
      .lrv .pill{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:999px}
      .lrv .pill.ok{background:#DCEFE4;color:var(--ok)}.lrv .pill.warn{background:#F7E9CF;color:var(--warn)}
      .lrv .pill.bad{background:#F8DDD8;color:var(--bad)}.lrv .pill.neu{background:#E7ECEF;color:var(--neu)}
      .lrv .toolbar{display:flex;gap:8px;align-items:center;padding:9px 15px;border-bottom:1px solid #DCE2E7;flex-wrap:wrap}
      .lrv input[type=search]{font:inherit;font-size:13px;padding:5px 10px;border:1px solid #DCE2E7;border-radius:7px}
      .lrv .msg{background:#FEF7E6;border:1px solid #F0DCA8;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:10px;color:#5C4A00;word-break:break-all}
      @media(max-width:900px){.lrv .cols{grid-template-columns:1fr}.lrv .verdict{grid-template-columns:1fr 1fr}}
      `}</style>

      <div className="head">
        <div><div className="h-title">物流账单复核台</div>
          <div className="h-sub">核价（合同价格卡）× 核量（金蝶出库数量）→ 归一态 · 异常优先</div></div>
        <div style={{ flex: 1 }} />
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
      </div>

      <div className="supbar">
        <span className="supbar-lb">本月有计提的承运商{sups && sups.length ? `（${sups.filter(s => s.accrued != null).length}）` : ''}</span>
        <input type="search" placeholder="搜承运商" value={supq} onChange={e => setSupq(e.target.value)} style={{ width: 120 }} />
        <div className="supchips">
          {sups === null && <span className="supempty">读金蝶计提凭证中…</span>}
          {sups !== null && sups.filter(s => !supq || (s.short || '').includes(supq) || (s.full || '').includes(supq)).map(s =>
            <button key={s.short} className={'supchip' + (s.short === carrier ? ' on' : '') + (s.has_spec ? '' : ' nospec')}
              title={(s.full || s.short) + (s.has_spec ? '（已配取数说明，可复核）' : '（未配取数说明）')}
              onClick={() => { setCarrier(s.short); setGroup('ex'); setPage(1) }}>
              {s.short}{s.accrued != null && <em>{money(s.accrued)}</em>}{!s.has_spec && <i>未配</i>}
            </button>)}
          {sups !== null && !sups.some(s => s.accrued != null) &&
            <span className="supempty">本月金蝶暂无物流计提（2241 供应商往来无「计提…运费/仓储费」贷方）——下方 pilot 迅鸽可试跑</span>}
        </div>
      </div>

      <div className="toolbar" style={{ border: '1px solid #DCE2E7', borderRadius: 12, marginBottom: 12 }}>
        <span style={{ fontSize: 12.5, color: '#5E6B78' }}>当前 <b>{carrier}</b> · {period}</span>
        <div style={{ flex: 1 }} />
        <label className="btn">导入价格卡（合同价目表）<input type="file" accept=".xlsx,.xls" hidden onChange={onFile(reviewImportPriceCard, carrier)} /></label>
        <label className="btn">上传账单解析<input type="file" accept=".xlsx,.xls" hidden onChange={onFile(reviewParseBill, carrier, period)} /></label>
        <button className="btn" disabled={busy === 'kd'} onClick={kingdee}>{busy === 'kd' ? '金蝶取数中…' : '接金蝶核量'}</button>
      </div>
      {msg && <div className="msg">{msg}</div>}

      <div className="verdict">
        <div className="lead">价格卡 {d ? d.price_card_rows : '—'} 行 · 明细 {d ? d.detail_total : '—'} 行（当前筛选）。复核=核价×核量两轴，归一态见下。</div>
        <div className="stat accent"><div className="v">{d ? money(d.total_bill) : '—'}</div><div className="l">账单合计（元）</div></div>
        <div className="stat ok"><div className="v">{(c.pass || 0)}</div><div className="l">两轴均通过（笔）</div></div>
        <div className="stat warn"><div className="v">{(c.miss || 0) + (c.qtydiff || 0)}</div><div className="l">核量待处理（笔）</div></div>
        <div className="stat warn"><div className="v">{(c.gap || 0) + (c.free || 0)}</div><div className="l">核价待处理（笔）</div></div>
      </div>

      <div className="cols">
        <div className="card">
          <h3>待处理（点开筛下方明细）</h3>
          {QUEUE.map(g =>
            <button key={g.f} className={'qrow' + (g.f === group ? ' on' : '')} onClick={() => { setGroup(g.f); setPage(1) }}>
              <span className={'sw ' + g.sw} /><div className="t"><div className="n">{g.n}</div><div className="dd">{g.dd}</div></div>
              <div className="cc">{g.c}<small style={{ fontWeight: 400, color: '#8A96A2', marginLeft: 2 }}>{g.u}</small></div>
            </button>)}
        </div>

        <div>
          <div className="card">
            <h3>费用项汇总 · 核价 × 核量</h3>
            <div className="tw"><table>
              <thead><tr><th>费用项</th><th className="num">账单额</th><th className="num">标准额</th><th className="num">差</th><th>核价</th><th>核量</th></tr></thead>
              <tbody>{(d && d.summary || []).map((f, i) =>
                <tr key={i}><td style={{ fontWeight: 600 }}>{f.fee_item}</td><td className="num">{money(f.bill)}</td>
                  <td className="num">{f.std == null ? '—' : money(f.std)}</td><td className="num">{f.diff == null ? '?' : money(f.diff)}</td>
                  <td><span className={'pill ' + (f.over ? 'bad' : f.gap ? 'warn' : 'ok')}>{f.price_ok}一致{f.over ? ` · ${f.over}多收` : ''}{f.gap ? ` · ${f.gap}缺价` : ''}</span></td>
                  <td><span className={'pill ' + ((f.qtydiff || f.miss) ? 'warn' : 'neu')}>{f.qty_ok ? `一致${f.qty_ok}` : '—'}{f.qtydiff ? ` · ${f.qtydiff}不符` : ''}{f.miss ? ` · ${f.miss}查无` : ''}</span></td>
                </tr>)}</tbody>
            </table></div>
          </div>

          <div className="card">
            <h3>逐单核价核量</h3>
            <div className="toolbar">
              <span style={{ fontSize: 12.5, color: '#5E6B78' }}>共 <b>{d ? d.detail_total : 0}</b> 行</span>
              <div style={{ flex: 1 }} />
              <input type="search" placeholder="搜单号/省份" value={q} onChange={e => { setQ(e.target.value); setPage(1) }} />
            </div>
            <div className="tw"><table>
              <thead><tr><th>金蝶单号</th><th>快递</th><th>省</th><th className="num">计费kg</th><th className="num">账单数量</th><th className="num">金蝶数量</th><th className="num">账单</th><th className="num">标准</th><th className="num">差</th><th>计价档</th><th>归一态</th></tr></thead>
              <tbody>{(d && d.detail || []).map((r, i) => {
                const [nm, cl] = PS[r.price_state] || ['—', 'neu']
                const qmk = r.qty_state === 'miss' ? ' ✕' : r.qty_state === 'qtydiff' ? ' ▲' : ''
                return <tr key={i}><td style={{ fontFamily: 'ui-monospace', fontSize: 12 }}>{r.doc_no}</td><td>{r.carrier_sub}</td><td>{r.prov}</td>
                  <td className="num">{r.charge_wt == null ? '—' : r.charge_wt}</td><td className="num">{r.qty == null ? '—' : r.qty}</td>
                  <td className="num" style={{ color: qmk ? 'var(--warn)' : '' }}>{r.kd_qty == null ? '—' : r.kd_qty}{qmk}</td>
                  <td className="num">{money(r.amount)}</td><td className="num">{r.std_amount == null ? '—' : money(r.std_amount)}</td>
                  <td className="num">{r.price_diff == null ? '?' : money(r.price_diff)}</td><td style={{ color: '#5E6B78', fontSize: 12 }}>{r.tier}</td>
                  <td><span className={'pill ' + cl}>{nm}</span></td></tr>
              })}</tbody>
            </table></div>
            {d && d.detail_total > d.size && <div className="toolbar">
              <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ 上一页</button>
              <span style={{ fontSize: 12.5 }}>第 {page} / {Math.ceil(d.detail_total / d.size)} 页</span>
              <button className="btn" disabled={page >= Math.ceil(d.detail_total / d.size)} onClick={() => setPage(page + 1)}>下一页 ›</button>
            </div>}
          </div>
        </div>
      </div>
    </div>
  )
}
