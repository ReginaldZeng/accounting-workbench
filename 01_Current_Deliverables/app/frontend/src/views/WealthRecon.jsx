// [Change Log] Date:2026-07-05 Author:Claude/c Version:V2.6
// 理财产品对账（产品维度聚合）：理财对账单 PDF(OCR) × 金蝶 1101/1012理财腿 + 6xxx收益。
// 一笔赎回=金蝶多腿(本金/公允价值/收益)，逐笔对不齐→按产品对：对账单赎回 ≈ 金蝶本金退回+收益。
import React, { useEffect, useState } from 'react'
import { getWealthRecon, syncWealthRecon, yuan } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

let _cache = null
const fmt = n => n == null ? '—' : yuan(n)

// 状态 → [标签类, 颜色]
const STCLS = {
  '已勾稽': ['ok', 'var(--green)'],
  '有差异': ['leak', 'var(--red)'],
}
function stateTag(st) {
  if (st === '已勾稽') return <span className="tag ok">已勾稽</span>
  if (st === '有差异') return <span className="tag leak">有差异</span>
  if (st === '持仓·无交易') return <span className="tag late">持仓·无交易</span>
  if (st.startsWith('金蝶未记')) return <span className="tag kd">金蝶未记</span>
  return <span className="tag unmap">对账单缺</span>
}

export default function WealthRecon({ cfg, onPeriod }) {
  const [d, setD] = useState(_cache), [busy, setBusy] = useState(false), [exp, setExp] = useState(null)
  useEffect(() => { getWealthRecon().then(x => { _cache = x; setD(x) }).catch(() => {}) }, [cfg.source, cfg.year, cfg.period])
  const run = async () => {
    setBusy(true)
    try { const x = await syncWealthRecon(); _cache = x; setD(x) } finally { setBusy(false) }
  }
  if (!d) return <div className="loading">加载中…</div>
  const rows = d.rows || []
  const sample = d.source !== 'kingdee'

  return (<div>
    <div className="head">
      <div><div className="h-title">理财产品对账</div>
        <div className="h-sub">理财对账单（PDF·OCR识别）× 金蝶 交易性金融资产(1101)/其它货币资金(1012)理财腿 + 投资收益(6xxx) · 按产品维度勾稽</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
        <button className="btn btn-pri" onClick={run} disabled={busy || sample}>{busy ? 'OCR识别+对账中…（约1分钟）' : (d['未对账'] ? '开始理财对账' : '重新对账')}</button>
      </div>
    </div>
    <div className="body">
      {sample && <div className="banner info">理财对账仅金蝶模式可用（需真金蝶序时账 + 理财对账单）。到「设置」切数据源为金蝶。</div>}
      {d.error && <div className="banner err">对账失败：{d.error}</div>}
      {d['未对账'] && !d.error && !sample &&
        <div className="banner info">{d.note || '点右上「开始理财对账」运行。'}理财对账含对账单 PDF 的 OCR 识别，较慢（每份约 10–40 秒），故不自动跑。</div>}

      {!d['未对账'] && !d.error && <>
        {/* 汇总带 */}
        <div className="kpis">
          <div className="kpi"><div className="kl">理财产品</div><div className="kv">{d['产品数'] || 0}</div></div>
          <div className="kpi"><div className="kl"><span className="dot" style={{ background: 'var(--green)' }} />已勾稽</div><div className="kv" style={{ color: 'var(--green)' }}>{d['已勾稽'] || 0}</div></div>
          <div className={'kpi' + ((d['有差异'] || 0) ? ' prio' : '')}><div className="kl"><span className="dot" style={{ background: 'var(--red)' }} />有差异</div><div className="kv">{d['有差异'] || 0}</div></div>
          <div className="kpi"><div className="kl">对账单赎回合计</div><div className="kv" style={{ fontSize: 15 }}>{fmt(d['对账单赎回合计'])}</div></div>
          <div className="kpi"><div className="kl">金蝶本金退回合计</div><div className="kv" style={{ fontSize: 15 }}>{fmt(d['金蝶本金退回合计'])}</div></div>
          <div className="kpi"><div className="kl">金蝶投资收益合计</div><div className="kv" style={{ fontSize: 15 }}>{fmt(d['金蝶投资收益合计'])}</div></div>
        </div>
        <div className="foot">数据源：金蝶 · {d.period} · 更新于 {d.updated_at}
          {d['对账单文件'] && d['对账单文件'].length ? ` · 已识别理财对账单 ${d['对账单文件'].length} 份（${d['对账单文件'].join('、')}）` : ' · 未在流水目录找到理财对账单 PDF'}</div>
        {d.parse_errors && d.parse_errors.length > 0 &&
          <div className="banner err">部分对账单解析失败：{d.parse_errors.map(e => `${e['文件']}（${e['错误']}）`).join('；')}</div>}

        {/* 对账表 */}
        <div className="tbl-wrap"><table style={{ minWidth: 1100 }}>
          <thead><tr>{['状态', '产品 / 主体 · 机构', '对账单赎回', '对账单申购', '金蝶本金退回', '金蝶投资收益', '公允价值净', '差额(含收益)', ''].map((h, i) =>
            <th className="th" key={h} style={(i >= 2 && i <= 7) ? { textAlign: 'right' } : null}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => {
            const diff = r['差额_含收益']
            const bad = r['状态'] === '有差异'
            const open = exp === i
            const hasLegs = (r.legs && r.legs.length) || (r.txns && r.txns.length)
            return <React.Fragment key={i}>
              <tr className={'row' + (bad ? ' prio' : '')} onClick={() => hasLegs && setExp(open ? null : i)}>
                <td>{stateTag(r['状态'])}</td>
                <td><div style={{ fontWeight: 500 }}>{r['产品名称'] ? String(r['产品名称']).slice(0, 28) : (r['金蝶维度'] || '—')}</div>
                  <div className="sub">{[r['主体'], r['机构']].filter(Boolean).join(' · ') || r['金蝶维度']}</div></td>
                <td className="num">{fmt(r['赎回'])}</td>
                <td className="num">{r['申购'] ? fmt(r['申购']) : '—'}</td>
                <td className="num">{fmt(r['金蝶本金退回'])}</td>
                <td className="num">{r['金蝶投资收益'] != null ? fmt(r['金蝶投资收益']) : '—'}</td>
                <td className="num" style={{ color: r['金蝶公允价值净'] ? 'var(--violet)' : 'var(--ink-3)' }}>{r['金蝶公允价值净'] != null ? fmt(r['金蝶公允价值净']) : '—'}</td>
                <td className="num" style={{ fontWeight: 600, color: bad ? 'var(--red)' : (diff != null ? 'var(--green)' : 'var(--ink-3)') }}>{diff != null ? (Math.abs(diff) < 0.01 ? '0.00 ✓' : fmt(diff)) : '—'}</td>
                <td>{hasLegs ? <span className="lk">{open ? '收起 ▴' : '看凭证 ▾'}</span> : <span className="muted">—</span>}</td>
              </tr>
              {open && hasLegs && <tr className="exp"><td colSpan="9">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
                  <div className="pane"><div className="ph">对账单侧 · 交易（OCR）</div>
                    {(r.txns || []).length === 0 && <div className="muted" style={{ fontSize: 12 }}>无（金蝶有腿、对账单未覆盖）</div>}
                    {(r.txns || []).map((t, k) => <div className="kv-row" key={k}><span className="kk">{t['日期']} · {t['类型']}</span><span className="vv">{yuan(t['确认金额'])}</span></div>)}
                  </div>
                  <div className="pane"><div className="ph">金蝶侧 · 理财各腿凭证</div>
                    {(r.legs || []).length === 0 && <div className="muted" style={{ fontSize: 12 }}>无（对账单有赎回、金蝶未记）</div>}
                    {(r.legs || []).map((g, k) => <div className="kv-row" key={k}>
                      <span className="kk">{g['科目']} · {g['凭证']} <span style={{ color: 'var(--ink-3)' }}>{String(g['摘要'] || '').slice(0, 22)}</span></span>
                      <span className="vv">{g['借'] ? '借' + yuan(g['借']) : ''}{g['贷'] ? '贷' + yuan(g['贷']) : ''}</span></div>)}
                    {r['金蝶纯估值确认'] ? <div className="diffbox" style={{ marginTop: 8 }}>其中纯月末公允价值变动确认 {yuan(r['金蝶纯估值确认'])}（账上有、无资金流，不算赎回差异）</div> : null}
                  </div>
                </div>
              </td></tr>}
            </React.Fragment>
          })}
            {rows.length === 0 && <tr><td colSpan="9" className="muted" style={{ padding: 20 }}>本期无理财对账数据。确认流水目录含理财对账单 PDF、且金蝶有 1101/1012 理财分录。</td></tr>}
          </tbody>
        </table></div>

        <div className="foot">口径：一笔理财赎回在金蝶拆成 本金(1101.01/1012理财腿) + 公允价值变动(1101.02) + 投资收益(6xxx) 多条，逐笔对不齐 → 按<b>产品维度聚合</b>勾稽。<b>差额(含收益)</b> = 对账单赎回 − 金蝶本金退回 − 投资收益，≈0 即账实相符(已勾稽)；不为 0 即该产品存在未解释差异(多为赎回损益/公允价值时间差)，交核算组核。纯月末公允价值变动确认分录不算赎回差异（下钻可见）。</div>
      </>}
    </div>
  </div>)
}
