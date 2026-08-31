// [Change Log] Date:2026-08-10 Author:Claude/c Version:V2.254
// 存货看板（cldash）——存货台账组下的第二个三级页。补上 V2.53 出了样机却一直没做进工具的那一块。
// 图表沿用资金看板的做法：手写内联 SVG + --cat-* 分类色，不引第三方图表库（全站零 UI 依赖）。
//
// ⚠两个口径同页出现，页面上必须写明，否则同一个月两个数没人对得上：
//   · 结存/构成 ＝【科目余额】口径，含在途物资/委托加工物资/材料采购；
//   · 收发流量/周转 ＝【收发存表】口径，不含上面那几个。
//   107 的 2026-5 实测 10,994,036.02 vs 10,958,051.26，差额就是在途与委托加工。并排标注、不做加减。
import React, { useEffect, useState } from 'react'
import { getCostLedgerDash, getCostLedgerOrgs } from '../api'

const wan = n => (Number(n || 0) / 10000).toLocaleString('en-US', { maximumFractionDigits: 0 }) + ' 万'
const yuan = n => '¥' + Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })
const CAT_VARS = ['--cat-1', '--cat-2', '--cat-3', '--cat-4', '--cat-5', '--cat-6', '--cat-7', '--cat-8']

// 折线图：一条线 + 面积 + 端点值。x 均分、y 从 0.9*min 起（不从 0 起，否则波动被压平看不出）
function Line({ data, fmt = wan, height = 230, color = 'var(--accent)' }) {
  const pts = data.filter(d => d.v !== null && d.v !== undefined)
  if (pts.length < 2) return <Empty />
  const W = 640, H = height, padL = 58, padR = 18, padT = 26, padB = 26
  const vs = pts.map(d => d.v)
  const lo = Math.min(...vs) * 0.9, hi = Math.max(...vs) * 1.06
  const x = i => padL + i * (W - padL - padR) / (pts.length - 1)
  const y = v => padT + (hi - v) / (hi - lo || 1) * (H - padT - padB)
  const line = pts.map((d, i) => `${x(i)},${y(d.v)}`).join(' ')
  const area = `${padL},${y(lo)} ${line} ${x(pts.length - 1)},${y(lo)}`
  const ticks = [lo, lo + (hi - lo) / 3, lo + (hi - lo) * 2 / 3, hi]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" style={{ overflow: 'visible' }}>
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth="0.5" />
          <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">{fmt(t)}</text>
        </g>
      ))}
      <polygon points={area} fill={color} opacity="0.08" />
      <polyline points={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
      {pts.map((d, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(d.v)} r="3.5" fill="var(--bg-card, #fff)" stroke={color} strokeWidth="2">
            <title>{d.label}：{fmt(d.v)}</title>
          </circle>
          <text x={x(i)} y={y(d.v) - 11} textAnchor="middle" fontSize="11" fontWeight="600"
            fill="var(--ink)" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(d.v)}</text>
          <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--ink-3)">{d.label}</text>
        </g>
      ))}
    </svg>
  )
}

// 堆叠柱：每期一根，按 keys 顺序自下而上堆。顶上标的合计＝**后端给的权威合计**，不是各段之和。
// 为什么：负数的科目段画不出来（堆叠没法表达负值），若用"各段之和"当合计，
// 这张图的合计就会跟旁边折线图的同月数字对不上——同一页两个数打架，最招人问。
function Stacked({ data, keys, height = 230 }) {
  if (!data.length) return <Empty />
  const W = 640, H = height, padL = 58, padR = 18, padT = 30, padB = 26
  const tot = d => (d.total !== undefined && d.total !== null)
    ? d.total : keys.reduce((a, k) => a + (d.vals[k] || 0), 0)
  const hi = Math.max(...data.map(tot)) * 1.12 || 1
  const bw = Math.min(56, (W - padL - padR) / data.length * 0.55)
  const x = i => padL + (i + 0.5) * (W - padL - padR) / data.length
  const y = v => padT + (1 - v / hi) * (H - padT - padB)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" style={{ overflow: 'visible' }}>
      {[0, hi / 3, hi * 2 / 3, hi].map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth="0.5" />
          <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">{wan(t)}</text>
        </g>
      ))}
      {data.map((d, i) => {
        let acc = 0
        const neg = keys.filter(k => (d.vals[k] || 0) < 0)
        return (
          <g key={i}>
            {keys.map((k, ki) => {
              const v = d.vals[k] || 0
              if (v <= 0) return null
              const h = v / hi * (H - padT - padB)
              const yy = y(acc + v)
              acc += v
              return <rect key={k} x={x(i) - bw / 2} y={yy} width={bw} height={Math.max(h, 0.6)}
                fill={`var(${CAT_VARS[ki % CAT_VARS.length]})`}><title>{d.label} · {k}：{yuan(v)}</title></rect>
            })}
            <text x={x(i)} y={y(Math.max(tot(d), keys.reduce((a, k) => a + Math.max(d.vals[k] || 0, 0), 0))) - 7}
              textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--ink)"
              style={{ fontVariantNumeric: 'tabular-nums' }}>{wan(tot(d))}
              {neg.length ? <title>{neg.map(k => `${k} 为负数（${yuan(d.vals[k])}），已计入合计但画不出色块`).join('；')}</title> : null}</text>
            <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--ink-3)">{d.label}</text>
          </g>
        )
      })}
    </svg>
  )
}

// 并排双柱：入库 vs 出库
function Paired({ data, height = 230, labels = ['本期入库', '本期出库'] }) {
  if (!data.length) return <Empty />
  const W = 640, H = height, padL = 58, padR = 18, padT = 26, padB = 26
  const hi = Math.max(...data.flatMap(d => [d.a, d.b])) * 1.12 || 1
  const slot = (W - padL - padR) / data.length
  const bw = Math.min(26, slot * 0.3)
  const x = i => padL + (i + 0.5) * slot
  const y = v => padT + (1 - v / hi) * (H - padT - padB)
  const bar = (cx, v, fill, tip) => <rect x={cx} y={y(v)} width={bw} height={Math.max(H - padB - y(v), 0.6)}
    fill={fill} rx="2"><title>{tip}</title></rect>
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" style={{ overflow: 'visible' }}>
      {[0, hi / 3, hi * 2 / 3, hi].map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth="0.5" />
          <text x={padL - 8} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">{wan(t)}</text>
        </g>
      ))}
      {data.map((d, i) => (
        <g key={i}>
          {bar(x(i) - bw - 2, d.a, 'var(--cat-4)', `${d.label} · ${labels[0]}：${yuan(d.a)}`)}
          {bar(x(i) + 2, d.b, 'var(--cat-3)', `${d.label} · ${labels[1]}：${yuan(d.b)}`)}
          <text x={x(i)} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--ink-3)">{d.label}</text>
        </g>
      ))}
    </svg>
  )
}

const Empty = () => <div style={{ padding: 30, color: 'var(--ink-3)', fontSize: 12 }}>本期间范围内没有可画的数据。</div>

// ⚠`.card` 在 styles.css 里【没有定义】——全站是靠各页自己写一份内联 cardS 撑起来的。
// 照抄这个惯例，但背景用 var(--bg) 而不是像老页面那样写死 '#fff'：写死的在深色主题下是白块。
const cardS = { border: '1px solid var(--line)', borderRadius: 9, background: 'var(--bg)', padding: '16px 18px' }

function Card({ title, value, sub, tone }) {
  return (
    <div className="card" style={{
      ...cardS, padding: '14px 16px', flex: '1 1 180px', minWidth: 170,
      borderColor: tone === 'amber' ? 'var(--amber-line)' : undefined,
      background: tone === 'amber' ? 'var(--amber-bg)' : 'var(--bg)',
    }}>
      <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums', lineHeight: 1.2, color: tone === 'amber' ? 'var(--amber)' : 'var(--ink)' }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.6 }}>{sub}</div>
    </div>
  )
}

const Panel = ({ title, note, children }) => (
  <div className="card" style={{ ...cardS, flex: '1 1 460px', minWidth: 380 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10, gap: 12 }}>
      <div style={{ fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'right' }}>{note}</div>
    </div>
    {children}
  </div>
)

export default function CostLedgerDash() {
  const [orgs, setOrgs] = useState([])
  const [org, setOrg] = useState('')
  const [year, setYear] = useState(2026)
  const [period, setPeriod] = useState(5)
  const [d, setD] = useState(null)
  const [basis, setBasis] = useState('current')   // current=按当前分类重算 / closed=按各月落库口径
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    (async () => {
      try {
        const r = await getCostLedgerOrgs()
        if (r.ok) { setOrgs(r.orgs || []); setOrg(o => o || r.default) }
        else setErr(r.msg || '读取主体清单失败')
      } catch (e) { setErr('读取主体清单失败：' + e.message) }
    })()
  }, [])

  const load = async () => {
    if (!org) return
    setBusy(true); setErr('')
    try {
      const r = await getCostLedgerDash(year, period, org, period, basis)
      if (!r.ok) { setErr(r.msg || '取数失败'); setD(null) }
      else { setD(r); if (r.gl_msg) setErr(r.gl_msg); else if (r.flow_msg) setErr(r.flow_msg) }
    } catch (e) { setErr('取数失败：' + e.message); setD(null) }
    setBusy(false)
  }
  useEffect(() => { load() }, [org, year, period, basis])

  const s = (d && d.series) || []
  // 科目构成：先按对照表顺序（顺序固定，颜色才不会随金额跳来跳去），
  // **再把对照表里没有、但金蝶实际有余额的科目补在后面**。
  // 原先只画对照表内的 → 107 的「材料采购」(1401) 被静默丢掉，这张图的合计比旁边折线图少一万多
  // （1 月 10,513.27、4 月 -10,733.86）。同一页两个数不一样，是最没法解释的那种 bug。
  const inData = [...new Set(s.flatMap(x => Object.keys(x.subjects || {})))]
  const ordered = (d && d.subject_order || []).filter(k => inData.includes(k))
  const subjKeys = [...ordered, ...inData.filter(k => !ordered.includes(k))]
    .filter(k => s.some(x => (x.subjects || {})[k]))
  const last = s.length ? s[s.length - 1] : null

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">存货台账 · 存货看板</div>
          <div className="h-sub">
            {d ? `${d.org_name} · ${d.year} 年 ${d.from_period}–${d.period} 月存货类科目余额（金蝶只读）` : '金蝶只读'}
            · 结存与构成为<b>科目余额口径</b>，含在途物资／委托加工物资／材料采购
          </div>
        </div>
        <div className="h-tools" style={{ gap: 8 }}>
          <select value={org} onChange={e => setOrg(e.target.value)} style={sel}>
            {orgs.map(o => <option key={o.code} value={o.code}>{o.name}（{o.code}）</option>)}
          </select>
          <select value={period} onChange={e => setPeriod(Number(e.target.value))} style={sel}>
            {Array.from({ length: 12 }, (_, i) => i + 1).map(p =>
              <option key={p} value={p}>{year} 年 1–{p} 月</option>)}
          </select>
          <select value={basis} onChange={e => setBasis(e.target.value)} style={sel}
            title="趋势/环比用「按当前分类重算」；与台账导出对账用「按各月落库口径」">
            <option value="current">按当前分类重算（可比）</option>
            <option value="closed">按各月落库口径（对账）</option>
          </select>
          <button className="btn-sec" disabled={busy} onClick={load}>{busy ? '取数中…' : '刷新'}</button>
        </div>
      </div>

      <div className="scroll" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {err && <div className="trust" style={{ color: 'var(--amber)', borderColor: 'var(--amber-line)', background: 'var(--amber-bg)', whiteSpace: 'pre-line', alignItems: 'flex-start', lineHeight: 1.7 }}>⚠ {err}</div>}
        {busy && !d && <div className="card" style={{ ...cardS, color: 'var(--ink-3)' }}>正在从金蝶取 {period} 期数据…</div>}

        {d && <>
          {/* 两套口径必须明写在屏幕上（V2.281）——不写清楚，拿两个数来问是迟早的事 */}
          <div className="trust" style={{ fontSize: 12, lineHeight: 1.8, alignItems: 'flex-start',
            color: 'var(--ink-2)', background: 'var(--bg-sub)' }}>
            <div>
              {d.basis === 'closed'
                ? <><b>按各月落库口径</b>：各月用当月取数/封存时的分类，<b>与「台账导出」对得上</b>；
                  但各月口径可能不同，<b>跨月不可比</b>。没有落库数据的月份会退回实时取数并在下表标明。</>
                : <><b>按当前分类重算</b>：所有月份统一按<b>当前</b>物料分类归集，趋势与环比可比；
                  <b>与「台账导出」按当月口径出的数可能不同</b>——差异通常来自物料重分类
                  （2026-3 实际发生过：18 个物料改了存货类别，总额未变但类别归属变了）。</>}
            </div>
          </div>
          {d.series.some(s => s.basis && s.basis !== '当前分类') && (
            <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
              各期数据来源：{d.series.map(s => `${s.label} ${s.basis}`).join('　')}
            </div>
          )}

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Card title={`${d.period} 月存货结存`} value={'¥' + wan(d.cur_total)} sub="全部存货类科目合计" />
            <Card title={`环比 ${d.period - 1} 月`}
              value={d.mom === null ? '—' : (d.mom > 0 ? '+' : '') + Number(d.mom).toFixed(1) + '%'}
              sub={d.mom === null ? '无上期可比' : d.mom < 0 ? '较上月下降，资金占用回落' : '较上月上升'} />
            <Card title={`${s.length} 期均值`} value={'¥' + wan(d.avg_total)} sub={`${d.from_period}–${d.period} 月月末结存均值`} />
            <Card title="库存商品周转" value={d.goods_turn_days === null || d.goods_turn_days === undefined ? '—' : d.goods_turn_days + ' 天'}
              sub={`${d.period} 月 · 平均库存÷本期发出×30 · 收发存表口径`} />
          </div>

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <Panel title="存货结存趋势" note="月末结存合计 · 科目余额口径">
              <Line data={s.map(x => ({ label: x.label, v: x.subject_total }))} />
            </Panel>
            <Panel title="科目构成" note="月末结存按总账科目堆叠 · 科目余额口径">
              <Stacked data={s.map(x => ({ label: x.label, vals: x.subjects || {}, total: x.subject_total }))} keys={subjKeys} />
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 10, fontSize: 11, color: 'var(--ink-2)' }}>
                {subjKeys.map((k, i) => (
                  <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <i style={{ width: 9, height: 9, borderRadius: 2, background: `var(${CAT_VARS[i % CAT_VARS.length]})`, display: 'inline-block' }} />{k}
                  </span>
                ))}
              </div>
            </Panel>
          </div>

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <Panel title="库存商品 · 收发流量" note="本期入库 vs 本期发出 · 收发存表口径">
              <Paired data={s.map(x => ({ label: x.label, a: x.goods_in, b: x.goods_out }))} />
            </Panel>
            <Panel title="周转天数趋势" note="平均库存 ÷ 本期发出 × 30 天 · 仅库存商品">
              <Line data={s.map(x => ({ label: x.label, v: x.goods_turn_days }))}
                fmt={v => Number(v).toFixed(1)} color="var(--cat-2)" />
              {s.some(x => x.turn_first) && <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>
                首期没有上期期末可用，期初按本期期末代入（会略微低估）。
              </div>}
            </Panel>
          </div>

          {last && <div className="trust" style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.8, alignItems: 'flex-start' }}>
            <div>
              <b>两个口径的差额</b>：{d.period} 月科目余额合计 {yuan(last.subject_total)}，
              收发存表结存 {yuan(last.flow_end)}，差 {yuan(last.subject_total - last.flow_end)}
              —— 这不是错，是在途物资／委托加工物资／材料采购不走收发存表。
              上方「结存」「构成」用科目余额，「收发流量」「周转」用收发存表，各自标注、不做加减。
              逐笔核对请到<b>台账导出</b>那一页的三道勾稽。
            </div>
          </div>}
        </>}
      </div>
    </>
  )
}

const sel = { padding: '4px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12 }
