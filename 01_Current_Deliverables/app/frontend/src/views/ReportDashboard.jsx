// [Change Log] Date:2026-08-22 Author:Claude/c Version:V2.337
// V2.337 改版（只动第一眼，取数/口径/勾稽一律没碰）：
//   ① 主体由横排切片行（.ents）收进切片器下拉，页顶两条控制区合成一条；选项保留收入与「亏」。
//   ② 新增主题导航条：页内 01–04 四步叙事原本只能滚过去，现做成可点索引，
//      并把埋在 03 里的「八主体横比」「勾稽自检」提出来单列，跟随滚动高亮。
//   ③ KPI 基准行两行对调：基准值在上、基准名在下——基准值本身比"基准的名字"更常被用到。
//      实测（卡宽 166px）：原版六张卡的基准区都是 3 行；对调后利润表三张仍 3 行、资产负债表三张降到 2 行，
//      且数值那行不再断字。并成一行做不到——标签本身就要占两行，别再试了。
// 【报表仪表盘 · 子公司报表】。地道 React 重写自样机 子公司报表_样机.html（口径/图表/文案 1:1 还原）。
//   数据源 /api/report/dashboard（金蝶 GL_BALANCE 直取 2024-01 至今，按账簿×期间）。
//   语义/取数派生见 reportShared.js；样式见 report.css（作用域化，跟随本台明暗主题）。
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getReportDashboard, refreshReportDashboard } from '../api.js'
import {
  GROUP, unitCN, num as fnum, n0 as fn0, pct, cn, shortName,
  prevP, yearStartP, makeAccessors, runRules, defOf,
  BS_ASSET, BS_LIAB, PL_LINES,
} from './reportShared.js'
import './report.css'

/* ================= 图表（纯展示组件，吃算好的数据） ================= */
function Spark({ vals, tone }) {
  const W = 150, H = 28, n = vals.length
  if (n < 2) return null
  const hi = Math.max(...vals), lo = Math.min(...vals), sp = (hi - lo) || 1
  const X = i => i * W / (n - 1), Y = v => 3 + (hi - v) / sp * (H - 8)
  const d = vals.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1)).join(' ')
  const c = tone === 'good' ? 'var(--up)' : tone === 'bad' ? 'var(--down)' : 'var(--ink-3)'
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={d} fill="none" stroke="var(--ink-3)" strokeWidth="1.4" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <circle cx={X(n - 1)} cy={Y(vals[n - 1])} r="2.6" fill={c} />
    </svg>
  )
}

// 贡献桥（瀑布）
function BridgeSvg({ pl, kpi, U, f0, fmt, tip }) {
  const items = [{ n: '营业收入', v: pl.pl.find(x => x.n === '营业收入').v, k: 'start' }]
  pl.pl.filter(x => x.n !== '营业收入').forEach(x => {
    if (Math.abs(x.v) < 0.005) return
    items.push({ n: x.n, v: x.sign * x.v, k: x.sign * x.v >= 0 ? 'plus' : 'minus' })
  })
  items.push({ n: '净利润', v: kpi.net, k: 'end' })
  const W = 480, H = 296, ML = 6, MR = 6, MT = 20, MB = 78
  const iw = (W - ML - MR) / items.length, bw = Math.min(28, iw * .56)
  let run = 0; const segs = []
  items.forEach(it => {
    if (it.k === 'start') { segs.push({ ...it, y0: 0, y1: it.v }); run = it.v }
    else if (it.k === 'end') segs.push({ ...it, y0: 0, y1: it.v })
    else { segs.push({ ...it, y0: run, y1: run + it.v }); run += it.v }
  })
  const lo = Math.min(0, ...segs.flatMap(s => [s.y0, s.y1])), hi = Math.max(0, ...segs.flatMap(s => [s.y0, s.y1]))
  const pad = (hi - lo) * .12 || 1
  const Y = v => MT + (hi + pad - v) / (hi - lo + pad * 2) * (H - MT - MB)
  const col = k => k === 'minus' ? 'var(--crit)' : k === 'plus' ? 'var(--s1)' : 'var(--ink-1)'
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="收入到净利润的贡献桥">
      {segs.map((sg, i) => i && sg.k !== 'end' ? (
        <line key={'c' + i} x1={ML + (i - 1) * iw + (iw + bw) / 2} x2={ML + i * iw + (iw - bw) / 2}
          y1={Y(sg.y0)} y2={Y(sg.y0)} stroke="var(--axis)" strokeWidth="1" strokeDasharray="2 2" />
      ) : null)}
      <line x1={ML} x2={W - MR} y1={Y(0)} y2={Y(0)} stroke="var(--axis)" strokeWidth="1" />
      {segs.map((sg, i) => {
        const x = ML + i * iw + (iw - bw) / 2
        const y = Y(Math.max(sg.y0, sg.y1)), h = Math.max(2, Math.abs(Y(sg.y0) - Y(sg.y1)) - 2)
        return (
          <g key={i}>
            <rect x={x} y={y} width={bw} height={h} rx="4" fill={col(sg.k)} style={{ cursor: 'pointer' }}
              onMouseMove={e => tip(e, `<b>${sg.n}</b><div class="r"><span>金额</span><span>${fmt(sg.v)} ${U}</span></div>`)}
              onMouseLeave={() => tip(null)} />
            <text x={x + bw / 2} y={y - 6} textAnchor="middle" fontSize="9.5" fontWeight="600"
              fill={sg.k === 'minus' ? 'var(--crit)' : 'var(--ink-2)'}>{f0(sg.v)}</text>
            <text x={x + bw / 2 - 2} y={H - MB + 12} textAnchor="end" fontSize="9.5" fill="var(--ink-3)"
              transform={`rotate(-45 ${x + bw / 2 - 2} ${H - MB + 12})`}>{sg.n}</text>
          </g>
        )
      })}
    </svg>
  )
}

// 单月趋势双线
function Trend({ pts, curIdx, baseIdx, f0, fmt, tip }) {
  const W = 480, H = 296, ML = 46, MR = 12, MT = 12, MB = 42
  const vals = pts.flatMap(d => [d.rev, d.net])
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), pad = (hi - lo) * .1 || 1
  const X = i => ML + i * (W - ML - MR) / (pts.length - 1)
  const Y = v => MT + (hi + pad - v) / (hi - lo + pad * 2) * (H - MT - MB)
  const path = k => pts.map((d, i) => (i ? 'L' : 'M') + X(i) + ' ' + Y(d[k])).join(' ')
  const grids = [0, 1, 2, 3, 4].map(t => lo - pad + (hi - lo + pad * 2) * t / 4)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="单月营业收入与净利润趋势">
      {grids.map((v, t) => (
        <g key={t}>
          <line x1={ML} x2={W - MR} y1={Y(v)} y2={Y(v)} stroke="var(--grid)" strokeWidth="1" />
          <text x={ML - 6} y={Y(v) + 3.5} textAnchor="end" fontSize="9.5" fill="var(--ink-3)">{f0(v)}</text>
        </g>
      ))}
      <line x1={ML} x2={W - MR} y1={Y(0)} y2={Y(0)} stroke="var(--axis)" strokeWidth="1" />
      <path d={path('rev')} fill="none" stroke="var(--s1)" strokeWidth="2" strokeLinejoin="round" />
      <path d={path('net')} fill="none" stroke="var(--s2)" strokeWidth="2" strokeLinejoin="round" />
      {pts.map((d, i) => (
        <g key={'x' + i}>
          {((i % 6 === 0 && i < pts.length - 3) || i === pts.length - 1) &&
            <text x={X(i)} y={H - MB + 17} textAnchor="middle" fontSize="9.5" fill="var(--ink-3)">{d.p}</text>}
          {!d.posted && <circle cx={X(i)} cy={Y(0)} r="2.6" fill="var(--surface-1)" stroke="var(--ink-3)" strokeWidth="1.3" />}
        </g>
      ))}
      {curIdx >= 0 && <line x1={X(curIdx)} x2={X(curIdx)} y1={MT} y2={H - MB} stroke="var(--accent)" strokeWidth="1" strokeDasharray="3 3" opacity=".7" />}
      {baseIdx >= 0 && <line x1={X(baseIdx)} x2={X(baseIdx)} y1={MT} y2={H - MB} stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="2 4" opacity=".6" />}
      {pts.map((d, i) => (
        <rect key={'h' + i} x={X(i) - 6} y={MT} width="12" height={H - MT - MB} fill="transparent" style={{ cursor: 'crosshair' }}
          onMouseMove={e => tip(e, `<b>${d.p}${d.posted ? '' : ' · 本期未过账'}</b>
            <div class="r"><span style="color:var(--s1)">■ 营业收入</span><span>${fmt(d.rev)}</span></div>
            <div class="r"><span style="color:var(--s2)">■ 净利润</span><span>${fmt(d.net)}</span></div>`)}
          onMouseLeave={() => tip(null)} />
      ))}
    </svg>
  )
}

// 横向条形
function Bars({ rows, colorFn, U, f0, fmt, tip }) {
  const W = 480, rh = 30, MT = 6, ML = 100, MR = 62
  const H = MT + rows.length * rh + 6
  const mx = Math.max(...rows.map(r => Math.abs(r.v)), 1)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="横向条形图">
      {rows.map((r, i) => {
        const y = MT + i * rh, w = Math.abs(r.v) / mx * (W - ML - MR)
        return (
          <g key={i}>
            <text x={ML - 8} y={y + 15} textAnchor="end" fontSize="11" fill="var(--ink-2)">{r.n}</text>
            <rect x={ML} y={y + 5} width={Math.max(2, w)} height="14" rx="4" fill={colorFn(r, i)} style={{ cursor: 'pointer' }}
              onMouseMove={e => tip(e, `<b>${r.n}</b><div class="r"><span>金额</span><span>${fmt(r.v)} ${U}</span></div>`)}
              onMouseLeave={() => tip(null)} />
            <text x={ML + Math.max(2, w) + 7} y={y + 16} fontSize="10.5" fill="var(--ink-2)">{f0(r.v)}</text>
          </g>
        )
      })}
    </svg>
  )
}

/* ================= 主页面 ================= */
export default function ReportDashboard() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [st, setSt] = useState({ book: GROUP, period: '', basis: 'ytd', cmp: 'yoy', unit: 10000, tab: 'bs', showAcc: false })
  const [tip, setTip] = useState(null)      // {html,x,y}
  const [pop, setPop] = useState(null)      // {def,cur,x,y}

  useEffect(() => {
    let alive = true
    getReportDashboard().then(d => {
      if (!alive) return
      if (d.error) setErr(d.error)
      if (d.note && (!d.periods || !d.periods.length)) setErr(d.note)
      setData(d)
      const periods = d.periods || []
      const books = d.books || {}
      setSt(s => ({ ...s, period: periods[periods.length - 1] || '', book: Object.keys(books)[0] || GROUP }))
      setLoading(false)
    }).catch(e => { if (alive) { setErr(String(e.message || e)); setLoading(false) } })
    return () => { alive = false }
  }, [])

  const doRefresh = () => {
    setRefreshing(true)
    refreshReportDashboard().then(d => {
      if (d && d.ok) { setData(d); setErr(d.error || '') }
      else setErr((d && d.msg) || '刷新失败')
    }).catch(e => setErr(String(e.message || e))).finally(() => setRefreshing(false))
  }

  const acc = useMemo(() => data && data.periods && data.periods.length ? makeAccessors(data) : null, [data])

  // tooltip / 定义卡关闭：全局点击
  useEffect(() => {
    const close = () => setPop(null)
    window.addEventListener('click', close)
    const esc = e => { if (e.key === 'Escape') setPop(null) }
    window.addEventListener('keydown', esc)
    return () => { window.removeEventListener('click', close); window.removeEventListener('keydown', esc) }
  }, [])

  const showTip = (e, html) => {
    if (!html) { setTip(null); return }
    setTip({ html, x: e.clientX, y: e.clientY })
  }
  const openDef = (e, name, cur) => {
    e.stopPropagation()
    const d = defOf(name); if (!d) return
    setPop({ def: d, cur: cur || '', x: e.clientX, y: e.clientY })
  }

  if (loading) return <div className="rptwrap"><div className="body"><div className="loading" style={{ padding: 40, color: 'var(--ink-3)' }}>载入子公司报表…（首次从金蝶按期取数，稍候）</div></div></div>
  if (err && (!acc)) return (
    <div className="rptwrap"><h1><span>📊</span>子公司报表</h1>
      <div className="card"><div className="empty">{err}</div></div>
      <button className="pill" style={{ marginTop: 12 }} onClick={doRefresh} disabled={refreshing}>{refreshing ? '刷新中…' : '重试 · 刷新金蝶'}</button>
    </div>
  )

  return (
    <>
      {/* app 原生页头（与「报表导出」等页一致）：标题+副标题在左，期间选择器+刷新在右上 */}
      <div className="head">
        <div>
          <div className="h-title">子公司报表</div>
          <div className="h-sub">八个主体的资产负债表与利润表：KPI、贡献桥、趋势、风险规则与勾稽自检，随切片器实时刷新。
            {data.updated_at ? ` · 数据更新 ${data.updated_at}（${data.source === 'kingdee' ? '金蝶直取' : data.source}）` : ''}</div>
        </div>
        <div className="rptwrap" style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
          <div className="fl"><label>期间</label>
            <select value={st.period} onChange={e => setSt(s => ({ ...s, period: e.target.value }))}>
              {(data.periods || []).slice().reverse().map(p => <option key={p} value={p}>{cn(p)}</option>)}
            </select>
          </div>
          <button className="pill" onClick={doRefresh} disabled={refreshing} style={{ marginLeft: 0 }}>{refreshing ? '刷新中…' : '⟳ 刷新金蝶'}</button>
        </div>
      </div>
      <div className="rptwrap" style={{ padding: '16px 20px 22px' }}>
        <Report data={data} acc={acc} st={st} setSt={setSt} tip={showTip} openDef={openDef} err={err} />
        {tip && <div className="rpt-tip" style={tipStyle(tip)} dangerouslySetInnerHTML={{ __html: tip.html }} />}
        {pop && <DefCard pop={pop} unitCN={unitCN(st.unit)} />}
      </div>
    </>
  )
}

function tipStyle(tip) {
  // 光标右上，越界翻转（近似样机行为）
  let x = tip.x + 14, y = tip.y - 60
  if (x > window.innerWidth - 220) x = tip.x - 220
  if (y < 8) y = tip.y + 16
  return { left: x, top: Math.max(8, y) }
}

function DefCard({ pop, unitCN }) {
  const d = pop.def
  const x = Math.min(pop.x, window.innerWidth - 362), y = Math.min(pop.y + 8, window.innerHeight - 220)
  return (
    <div className="rpt-pop" style={{ left: Math.max(8, x), top: Math.max(8, y) }} onClick={e => e.stopPropagation()}>
      <h4>{d.name}<em>{d.kind}</em></h4>
      <dl>
        <dt>口径公式</dt><dd>{d.f}</dd>
        <dt>取数来源</dt><dd><code>{d.src}</code></dd>
        <dt>时间属性</dt><dd>{d.tt}</dd>
        <dt>适用边界</dt><dd>{d.edge}</dd>
        {pop.cur ? <><dt>本期取值</dt><dd className="cur">{pop.cur}</dd></> : null}
      </dl>
      <div className="whr">维护位置：<b>报表模块 › 财务报表 › 指标中心 › {d.where}</b><br />当前为草案口径，待成本会计／总账会计确认后锁定。</div>
    </div>
  )
}

/* 可点定义的名称 */
function Def({ name, cur, openDef, children }) {
  return <span className="def" onClick={e => openDef(e, name, cur)}>{children || name}</span>
}

/* 主体选择器（V2.337）：原为一排切片按钮，现收进筛选器。
   选项保留营业收入与「亏」——挪进下拉不能把这两个状态丢了。
   口径提示是**动态**的：数字跟着 st.basis／st.unit 走，写死成"本年累计·万元"在切到单月或元时就是错的。 */
function EntPick({ rows, groupRev, value, onPick, f0, U, basisTxt }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    // 用 mousedown 而非 click：本页有个全局 click 监听在关"定义卡"，
    // 用 click 会和触发按钮自身的 onClick 撞车，点开即关。PeriodPicker 也是这么绕的。
    const out = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const esc = e => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', out)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc) }
  }, [open])
  const isG = value === GROUP
  const curRow = rows.find(r => r.b === value)
  const Row = ({ b, nm, rev, lo, grp }) => (
    <button type="button" role="option" aria-current={value === b} className={grp ? 'grp' : undefined}
      onClick={() => { setOpen(false); onPick(b) }}>
      <span className="nm">{nm}</span><span className="e2">{f0(rev)}</span>
      {lo ? <span className="lo">亏</span> : null}</button>
  )
  return (
    <div className="entpick" ref={ref}>
      <button type="button" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(o => !o)}
        title="选择主体（选项里带本期收入与盈亏）">
        <span className="nm">{isG ? '集团合计（未抵消）' : shortName(curRow ? curRow.n : '')}</span>
        <span className="e2">{f0(isG ? groupRev : (curRow ? curRow.rev : null))}</span>
        {!isG && curRow && curRow.net < 0 ? <span className="lo">亏</span> : null}
        <span className="caret">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="entmenu" role="listbox" aria-label="主体">
          <div className="hint">数字＝{basisTxt}营业收入（{U}）·「亏」＝{basisTxt}净利润为负</div>
          <Row b={GROUP} nm="集团合计（未抵消）" rev={groupRev} lo={false} grp />
          {rows.map(r => <Row key={r.b} b={r.b} nm={shortName(r.n)} rev={r.rev} lo={r.net < 0} />)}
        </div>
      )}
    </div>
  )
}

/* 主题导航（V2.337）：页内本就有 01–04 四步叙事，但只能滚过去、点不到；
   这里做成可点索引，并把埋在 03 里的两个高频入口（八主体横比／勾稽自检）提出来单列。
   六个主题是从我们自己已有的内容长出来的——不照抄同业那套"盈利/偿债/营运/成长/现金流/杜邦"，
   那套要三大报表加多年数据，我们只有资产负债表与利润表，照搬会做出几个空页。 */
const THEMES = [
  ['rpt-kpi', '总览', '这个月怎么样？'],
  ['rpt-s1', '发生了什么', '收入到净利，钱去哪了？'],
  ['rpt-s2', '为什么会这样', '费用和占用压在哪？'],
  ['rpt-cmp', '八主体横比', '哪几家在亏？'],
  ['rpt-tie', '勾稽自检', '这批数能不能用？'],
  ['rpt-s4', '三大报表', '我要看原表'],
]
// 选谁：最后一个已滑过判定线的锚点。判定线在视口上沿下方 108px——
// 主题条自身吸顶约 48px，锚点刚露到条底下就该算「到了」。
// 抽成模块级纯函数：选谁 与 何时重算 分开，前者可单独推敲。
function pickTheme(top = 108) {
  let cur = THEMES[0][0]
  for (const [id] of THEMES) {
    const el = document.getElementById(id)
    if (el && el.getBoundingClientRect().top <= top) cur = id
  }
  return cur
}

function ThemeNav() {
  const [act, setAct] = useState(THEMES[0][0])
  useEffect(() => {
    let raf = 0
    // IO 只当触发器，选谁由 pickTheme 定：长小节里可能一个锚点都不在观察带内，
    // 直接拿 IO 的 entry 判断会让高亮瞬间变空。
    const scan = () => { raf = 0; setAct(pickTheme()) }
    const kick = () => { if (!raf) raf = requestAnimationFrame(scan) }
    scan()
    // 主用 IntersectionObserver：它由合成器驱动，与「滚动事件有没有派发」无关
    // （脚本调 scrollTo 时某些环境不派发 scroll，只挂 scroll 监听会整个失灵）。
    const io = new IntersectionObserver(kick, { rootMargin: '-100px 0px -55% 0px', threshold: [0, 1] })
    THEMES.forEach(([id]) => { const el = document.getElementById(id); if (el) io.observe(el) })
    // scroll/resize 作为补充：小节很长时 IO 不再回调，但那时答案本来也没变
    window.addEventListener('scroll', kick, { passive: true })
    window.addEventListener('resize', kick)
    return () => {
      if (raf) cancelAnimationFrame(raf)
      io.disconnect()
      window.removeEventListener('scroll', kick)
      window.removeEventListener('resize', kick)
    }
  }, [])
  return (
    <nav className="themenav" aria-label="主题导航">
      {THEMES.map(([id, t, q]) => (
        <button key={id} type="button" aria-current={act === id}
          onClick={() => {
            setAct(id)   // 立刻高亮，别等滚动事件——平滑滚动期间也要有反馈
            document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }}>
          <span className="t">{t}</span><span className="q">{q}</span>
        </button>
      ))}
    </nav>
  )
}

function Report({ data, acc, st, setSt, tip, openDef, onRefresh, refreshing, err }) {
  const { R, plOf, baseInfo, seriesOf, books, periods } = acc
  const U = unitCN(st.unit)
  const f = (v, dp) => fnum(v, st.unit, dp)
  const f0 = v => fn0(v, st.unit)
  const set = patch => setSt(s => ({ ...s, ...patch }))

  // 本年累计禁用环比：若已选拉回同比
  const cmp = (st.basis === 'ytd' && st.cmp === 'mom') ? 'yoy' : st.cmp
  useEffect(() => { if (st.basis === 'ytd' && st.cmp === 'mom') set({ cmp: 'yoy' }) }, [st.basis])

  const cur = R(st.period, st.book), pl = plOf(st.period, st.book, st.basis)
  const BI = baseInfo(st.period, cmp, st.basis)
  const bpl = BI.plPer ? plOf(BI.plPer, st.book, st.basis) : null
  const bbs = BI.bsPer ? R(BI.bsPer, st.book) : null
  if (!cur || !pl) return <div className="card"><div className="empty">本期（{st.period}）无该主体数据。</div></div>
  const t = cur.tie, ok = v => Math.abs(v) < 1
  const nOK = [t.balance, t.rev, t.exp].filter(ok).length
  const basisTxt = st.basis === 'ytd' ? '本年累计' : '单月'
  const K = pl.kpi, B = cur.kpi

  // 主体切换条（按收入降序）
  const entRows = Object.keys(books).map(b => {
    const p = plOf(st.period, b, st.basis), c = R(st.period, b)
    return { b, n: books[b], rev: p.kpi.revenue, net: p.kpi.net, posted: c.tie.posted }
  }).sort((x, y) => y.rev - x.rev)
  const groupRev = entRows.reduce((s, r) => s + r.rev, 0)

  // KPI 变化：金额指标 → 「-320万元（-XX%）」带符号变化额＋带符号百分比；比率指标 → 「+X.XX 个百分点」
  const delta = (now, was, isPct) => {
    if (was == null || now == null) return <div className="d flat">无基准期</div>
    const chg = now - was, up = chg >= 0, sign = up ? '+' : '-'
    if (isPct) return <div className={'d ' + (up ? 'up' : 'down')}>{sign}{Math.abs(chg).toFixed(2)} 个百分点</div>
    const amt = sign + f0(Math.abs(chg)) + U
    const tail = !was ? '基准为0' : sign + Math.abs(chg / Math.abs(was) * 100).toFixed(1) + '%'
    return <div className={'d ' + (up ? 'up' : 'down')}>{amt}（{tail}）</div>
  }
  const tone = (now, was, hb) => (was == null || now == null || hb == null) ? 'neutral' : ((now >= was) === hb ? 'good' : 'bad')
  const Tile = ({ k, v, u, dHtml, baseLbl, baseVal, sVals, tn }) => (
    <div className="kpi">
      <div className="k"><Def name={k} cur={`${v} ${u}`} openDef={openDef} /></div>
      <div className="v">{v}{u ? <span className="u">{u}</span> : null}</div>
      {dHtml}
      <div className="base">基准 <b>{baseVal}</b><br /><span className="bl">{baseLbl}</span></div>
      <Spark vals={sVals} tone={tn} />
    </div>
  )

  // 01 贡献桥结论
  const segCuts = pl.pl.filter(x => x.n !== '营业收入' && x.sign * x.v < 0 && Math.abs(x.v) >= 0.005)
    .map(x => ({ n: x.n, v: x.sign * x.v })).sort((a, b) => a.v - b.v)
  // 趋势点
  const pts = periods.map(p => {
    const c = R(p, st.book), pv = p.endsWith('-01') ? null : R(prevP(periods, p), st.book)
    const d = kk => pv ? c.kpi[kk] - pv.kpi[kk] : c.kpi[kk]
    return { p, rev: d('revenue'), net: d('net'), posted: c.tie.posted }
  })
  const curIdx = periods.indexOf(st.period), baseIdx = periods.indexOf(BI.plPer)
  const last12 = pts.slice(Math.max(0, curIdx - 11), curIdx + 1)
  const avg = last12.reduce((s, d) => s + d.rev, 0) / (last12.length || 1)
  const cp = pts.find(d => d.p === st.period) || { rev: 0 }
  const lossN = pts.filter(d => d.net < 0).length

  // 风险信号
  const rules = runRules(cur, pl, bpl && bpl.kpi.net, st.period, st.basis)
  const hitRules = rules.filter(r => !r.na && r.hit), naRules = rules.filter(r => r.na)

  // 02 成本/资金
  const costRows = ['营业成本', '销售费用', '管理费用', '研发费用', '财务费用', '税金及附加']
    .map(n => ({ n, v: (pl.pl.find(x => x.n === n) || { v: 0 }).v }))
    .filter(r => Math.abs(r.v) > 0.005).sort((a, b) => Math.abs(b.v) - Math.abs(a.v))
  const period3 = ['销售费用', '管理费用', '研发费用'].reduce((s, n) => s + (pl.pl.find(x => x.n === n) || { v: 0 }).v, 0)
  const wcRows = [{ n: '货币资金', v: B.cash }, { n: '应收账款', v: B.ar }, { n: '存货', v: B.inv }]
  const wcSum = wcRows.reduce((s, r) => s + r.v, 0)
  const mo = st.basis === 'ytd' ? (+st.period.slice(5) || 1) : 1
  const outflow = ['营业成本', '税金及附加', '销售费用', '管理费用', '研发费用'].reduce((s, n) => s + (pl.pl.find(x => x.n === n) || { v: 0 }).v, 0)

  // 03 八主体横比
  const mxr = Math.max(...entRows.map(r => Math.abs(r.rev)), 1)
  const cmpRows = entRows.map(r => {
    const p = plOf(st.period, r.b, st.basis), c = R(st.period, r.b)
    return { ...r, gm: p.kpi.gm, assets: c.kpi.assets, dar: c.kpi.dar, cash: c.kpi.cash, tieOK: [c.tie.balance, c.tie.rev, c.tie.exp].filter(ok).length }
  })
  const loss = cmpRows.filter(r => r.net < 0).sort((a, b) => a.net - b.net)

  return (
    <div>
      {err ? <div className="basisnote" style={{ marginBottom: 10, color: 'var(--serious)' }}>{err}</div> : null}

      {/* 切片器（V2.337：主体由横排切片行并入本条，页面顶部两条控制区合成一条） */}
      <div className="bar">
        <div className="fl"><label>主体</label>
          <EntPick rows={entRows} groupRev={groupRev} value={st.book} onPick={b => set({ book: b })}
            f0={f0} U={U} basisTxt={basisTxt} />
        </div>
        <Seg label="利润表口径" value={st.basis} onSet={v => set({ basis: v })}
          opts={[['ytd', '本年累计'], ['mtd', '单月']]} />
        <Seg label="对比基准" value={cmp} onSet={v => set({ cmp: v })}
          opts={[['yoy', '同比'], ['mom', '环比', st.basis === 'ytd']]}
          disabledTip="本年累计口径没有环比——相邻两期只差一个月发生额，跨年更不可比。请切到「单月」口径。" />
        <Seg label="金额单位" value={String(st.unit)} onSet={v => set({ unit: +v })}
          opts={[['10000', '万元'], ['1', '元']]} />
        <button className={'pill' + (nOK === 3 && t.posted ? '' : ' warnp')}
          onClick={() => document.getElementById('rpt-s3')?.scrollIntoView({ behavior: 'smooth' })}>
          勾稽 {nOK}/3 通过 · {t.posted ? '本期已过账' : '本期未过账'} · 科目 51 个</button>
        <div className="basisnote" dangerouslySetInnerHTML={{
          __html: `<b>本期</b>：${cn(st.period)}（利润表口径 ${basisTxt}${st.basis === 'ytd' ? `＝${st.period.slice(0, 4)} 年 1–${+st.period.slice(5)} 月累计` : ''}）　｜　`
            + `<b>利润表基准</b>：${BI.plLabel}　｜　<b>资产负债表基准</b>：${BI.bsLabel}（时点数，与利润表口径无关）`
            + (st.basis === 'ytd' ? `　｜　<span style="color:var(--serious)">本年累计无环比可言，该选项已禁用</span>` : '')
        }} />
      </div>

      <ThemeNav />

      {/* KPI */}
      <div className="kpistrip" id="rpt-kpi">
        <Tile k="营业收入" v={f(K.revenue)} u={U} dHtml={delta(K.revenue, bpl && bpl.kpi.revenue)} baseLbl={BI.plLabel}
          baseVal={bpl ? f(bpl.kpi.revenue) + ' ' + U : '—'} sVals={seriesOf(st.book, 'revenue', st.period, true)} tn={tone(K.revenue, bpl && bpl.kpi.revenue, true)} />
        <Tile k="毛利润" v={<>{f(K.gross)}<span className="u">{U}</span><span className="rate">（{pct(K.gm)}）</span></>} u=""
          dHtml={delta(K.gross, bpl && bpl.kpi.gross)} baseLbl={BI.plLabel}
          baseVal={bpl ? f(bpl.kpi.gross) + ' ' + U : '—'} sVals={seriesOf(st.book, 'gross', st.period, true)} tn={tone(K.gross, bpl && bpl.kpi.gross, true)} />
        <Tile k="净利润" v={<>{f(K.net)}<span className="u">{U}</span><span className="rate">（{K.revenue ? pct(K.net / K.revenue * 100) : '—'}）</span></>} u=""
          dHtml={delta(K.net, bpl && bpl.kpi.net)} baseLbl={BI.plLabel}
          baseVal={bpl ? f(bpl.kpi.net) + ' ' + U : '—'} sVals={seriesOf(st.book, 'net', st.period, true)} tn={tone(K.net, bpl && bpl.kpi.net, true)} />
        <Tile k="总资产" v={f(B.assets)} u={U} dHtml={delta(B.assets, bbs && bbs.kpi.assets)} baseLbl={BI.bsLabel}
          baseVal={bbs ? f(bbs.kpi.assets) + ' ' + U : '—'} sVals={seriesOf(st.book, 'assets', st.period, false)} tn="neutral" />
        <Tile k="资产负债率" v={pct(B.dar)} u="" dHtml={delta(B.dar, bbs && bbs.kpi.dar, true)} baseLbl={BI.bsLabel}
          baseVal={bbs ? pct(bbs.kpi.dar) : '—'} sVals={seriesOf(st.book, 'dar', st.period, false)} tn={tone(B.dar, bbs && bbs.kpi.dar, false)} />
        <Tile k="货币资金" v={f(B.cash)} u={U} dHtml={delta(B.cash, bbs && bbs.kpi.cash)} baseLbl={BI.bsLabel}
          baseVal={bbs ? f(bbs.kpi.cash) + ' ' + U : '—'} sVals={seriesOf(st.book, 'cash', st.period, false)} tn={tone(B.cash, bbs && bbs.kpi.cash, true)} />
      </div>

      {/* 01 */}
      <div className="step" id="rpt-s1"><span className="no">01</span><h3>发生了什么</h3><span className="q">贡献桥 · 三十一期趋势 · 风险信号</span></div>
      <div className="grid3">
        <div className="card">
          <div className="chead"><div><h2>收入 → 净利润 贡献桥</h2><p className="cap">口径：{cn(st.period)} · {basisTxt} · {U}</p></div>
            <span className="vs">基准：{BI.plLabel}</span></div>
          <BridgeSvg pl={pl} kpi={K} U={U} f0={f0} fmt={f} tip={tip} />
          <div className="legend">
            <span><i className="sq" style={{ background: 'var(--ink-1)' }} />起点／结果</span>
            <span><i className="sq" style={{ background: 'var(--s1)' }} />加项</span>
            <span><i className="sq" style={{ background: 'var(--crit)' }} />减项</span>
          </div>
          <div className="concl" dangerouslySetInnerHTML={{
            __html: K.revenue
              ? `收入 <b>${f(K.revenue)}</b>、净利润 <b>${f(K.net)}</b> ${U}；净利率 <b>${(K.net / K.revenue * 100).toFixed(1)}%</b>。`
                + (segCuts.length ? `最大减项是<b>${segCuts[0].n}</b>（${f(Math.abs(segCuts[0].v))}），占收入 ${(Math.abs(segCuts[0].v) / K.revenue * 100).toFixed(1)}%。` : '')
              : `本期无营业收入，净利润 <b>${f(K.net)}</b> ${U}，全部来自费用与营业外项目。`
          }} />
        </div>
        <div className="card">
          <div className="chead"><div><h2>单月营业收入与净利润</h2><p className="cap">由本年累计逐期相减还原单月数 · 1 月＝当期累计</p></div>
            <span className="vs">实线＝本期 {st.period}，虚线＝基准期</span></div>
          <Trend pts={pts} curIdx={curIdx} baseIdx={baseIdx} f0={f0} fmt={f} tip={tip} />
          <div className="legend">
            <span><i style={{ background: 'var(--s1)' }} />营业收入</span>
            <span><i style={{ background: 'var(--s2)' }} />净利润</span>
            <span><i className="sq" style={{ background: 'none', border: '1.5px solid var(--ink-3)', borderRadius: '50%' }} />本期未过账</span>
          </div>
          <div className="concl" dangerouslySetInnerHTML={{
            __html: `近 12 期单月收入均值 <b>${f(avg)}</b>；本期 <b>${f(cp.rev)}</b>，${cp.rev >= avg ? '高于' : '低于'}均值 ${f(Math.abs(cp.rev - avg))}。 ${pts.length} 期中有 <b>${lossN}</b> 期单月净利为负。`
          }} />
        </div>
        <div className="card">
          <div className="chead"><div><h2>风险信号</h2><p className="cap">规则引擎判定 · 五条草案规则</p></div>
            <span className="vs">基准：{BI.plLabel}</span></div>
          <div>
            {rules.map(r => {
              const stc = r.na ? ['var(--ink-3)', '无法判定'] : r.hit ? ['var(--crit)', '命中'] : ['var(--good)', '未命中']
              return (
                <div className="sig" key={r.c}>
                  <span className="dot" style={{ background: stc[0] }} />
                  <span><b style={{ fontWeight: 600 }}><Def name={r.c} cur={r.na ? '无法判定' : (r.hit ? '命中' : '未命中')} openDef={openDef} /></b> {r.n}</span>
                  <span className="why">{stc[1]}<br />{r.why}</span>
                </div>
              )
            })}
          </div>
          <div className="concl" dangerouslySetInnerHTML={{
            __html: `命中 <b>${hitRules.length}</b> 项、未命中 <b>${rules.length - hitRules.length - naRules.length}</b> 项、无法判定 <b>${naRules.length}</b> 项。`
              + (hitRules.length ? ` 优先处理：<b>${hitRules.map(r => r.c).join('、')}</b>。` : ' 本期未触发任何风险规则。')
          }} />
        </div>
      </div>

      {/* 02 */}
      <div className="step" id="rpt-s2"><span className="no">02</span><h3>为什么会这样</h3><span className="q">费用结构与营运占用</span></div>
      <div className="grid2">
        <div className="card">
          <div className="chead"><div><h2>成本费用构成</h2><p className="cap">口径：{cn(st.period)} · {basisTxt} · {U} · 按金额排序</p></div>
            <span className="vs">{K.revenue ? `合计占收入 ${(costRows.reduce((s, r) => s + r.v, 0) / K.revenue * 100).toFixed(1)}%` : '本期无收入'}</span></div>
          {costRows.length ? <Bars rows={costRows} colorFn={(r, i) => r.v < 0 ? 'var(--good)' : i === 0 ? 'var(--crit)' : 'var(--s1)'} U={U} f0={f0} fmt={f} tip={tip} />
            : <div className="empty">本期无成本费用发生额。</div>}
          <div className="concl" dangerouslySetInnerHTML={{
            __html: costRows.length
              ? `最大一项是<b>${costRows[0].n}</b> ${f(costRows[0].v)}。三项期间费用（销售＋管理＋研发）合计 <b>${f(period3)}</b>`
                + (K.revenue ? `，占收入 <b>${(period3 / K.revenue * 100).toFixed(1)}%</b>。` : '。')
                + (costRows.some(r => r.v < 0) ? ' 绿色条为本期净冲减项。' : '')
              : '本期无成本费用发生额。'
          }} />
        </div>
        <div className="card">
          <div className="chead"><div><h2>资金压在哪里 · 货币资金／应收／存货</h2><p className="cap">口径：{cn(st.period)} 时点数 · {U}</p></div>
            <span className="vs">{B.assets ? `三项合计占总资产 ${(wcSum / B.assets * 100).toFixed(1)}%` : '—'}</span></div>
          <Bars rows={wcRows} colorFn={r => r.n === '货币资金' ? 'var(--s1)' : 'var(--s2)'} U={U} f0={f0} fmt={f} tip={tip} />
          <div className="concl" dangerouslySetInnerHTML={{
            __html: B.assets
              ? `应收＋存货占用 <b>${f(B.ar + B.inv)}</b>，占总资产 <b>${((B.ar + B.inv) / B.assets * 100).toFixed(1)}%</b>；货币资金 <b>${f(B.cash)}</b>`
                + (outflow > 0 ? `，可覆盖 <b>${(B.cash / (outflow / mo)).toFixed(1)}</b> 个月营业总支出。` : '。')
              : '本期总资产为 0。'
          }} />
        </div>
      </div>

      {/* 03 */}
      <div className="step" id="rpt-s3"><span className="no">03</span><h3>下一步看哪里</h3><span className="q">八主体横比 · 勾稽自检</span></div>
      <div className="card" id="rpt-cmp">
        <div className="chead"><div><h2>八个主体横比</h2>
          <p className="cap">口径：{cn(st.period)} · 利润表为{basisTxt} · 资产为时点数 · {U} · 按营业收入排序 · 分部列示未抵消</p></div>
          <span className="vs">点任一行切换主体</span></div>
        <div className="tw"><table>
          <thead><tr><th>主体</th><th>营业收入</th><th style={{ width: 84 }}></th><th>净利润</th><th>毛利率</th><th>总资产</th><th>资产负债率</th><th>货币资金</th><th>勾稽</th></tr></thead>
          <tbody>
            {cmpRows.map(r => (
              <tr key={r.b} style={{ cursor: 'pointer' }} onClick={() => set({ book: r.b })}>
                <td>{r.n}{r.posted ? '' : <span style={{ color: 'var(--warn)' }}> ·未过账</span>}</td>
                <td>{f(r.rev)}</td><td><div className="mini"><i style={{ width: Math.abs(r.rev) / mxr * 100 + '%' }} /></div></td>
                <td className={r.net >= 0 ? 'up' : 'down'}>{f(r.net)}</td>
                <td>{pct(r.gm)}</td><td>{f(r.assets)}</td><td>{pct(r.dar)}</td><td>{f(r.cash)}</td>
                <td className={r.tieOK === 3 ? 'up' : 'down'}>{r.tieOK}/3</td>
              </tr>
            ))}
            <tr className="tot" style={{ fontWeight: 600, borderTop: '1.5px solid var(--axis)' }}>
              <td>集团合计（未抵消）</td><td>{f(cmpRows.reduce((s, r) => s + r.rev, 0))}</td><td></td>
              <td>{f(cmpRows.reduce((s, r) => s + r.net, 0))}</td><td>—</td>
              <td>{f(cmpRows.reduce((s, r) => s + r.assets, 0))}</td><td>—</td>
              <td>{f(cmpRows.reduce((s, r) => s + r.cash, 0))}</td><td></td></tr>
          </tbody>
        </table></div>
        <div className="concl" dangerouslySetInnerHTML={{
          __html: `收入最高：<b>${cmpRows[0].n}</b>（${f(cmpRows[0].rev)}）。`
            + (loss.length ? ` <b>${loss.length}</b> 家亏损，亏损最大：<b>${loss[0].n}</b>（${f(loss[0].net)}）。` : ' 无主体亏损。')
            + ` 勾稽未全通过：<b>${cmpRows.filter(r => r.tieOK < 3).map(r => r.n).join('、') || '无'}</b>。`
        }} />
      </div>
      <div className="card" id="rpt-tie">
        <div className="chead"><div><h2>勾稽自检</h2><p className="cap">三道全自动校验 · 不依赖人工核对 · 任何一道不为零都会亮出来</p></div></div>
        <div className="tie">
          <TieItem title="会计恒等式" formula="资产 − 负债 − 所有者权益" val={t.balance} extra={Math.abs(t.balance) >= 1 ? '差额＝尚未结转到本年利润的损益净额' : ''} />
          <TieItem title="收入锚" formula="Σ 收入类累计贷方 − 4103 本年利润累计贷方" val={t.rev} />
          <TieItem title="费用锚" formula="Σ 费用类累计借方 − 4103 本年利润累计借方" val={t.exp} extra={Math.abs(t.exp) >= 1 ? '存在未结转到本年利润的费用，需回金蝶查结转凭证' : ''} />
        </div>
      </div>

      {/* 04 三大报表 */}
      <div className="step" id="rpt-s4"><span className="no">04</span><h3>三大报表</h3><span className="q">按企业会计准则格式列示</span></div>
      <div className="card">
        <div className="tabs">
          {[['bs', '资产负债表'], ['pl', '利润表'], ['cf', '现金流量表']].map(([v, lbl]) => (
            <button key={v} aria-selected={st.tab === v} onClick={() => set({ tab: v })}>{lbl}</button>
          ))}
          <label className="sw"><input type="checkbox" checked={st.showAcc} onChange={e => set({ showAcc: e.target.checked })} /> 显示构成科目</label>
        </div>
        <Statements st={st} cur={cur} pl={pl} BI={BI} acc={acc} U={U} f={f} openDef={openDef} />
      </div>

      <Footer />
    </div>
  )
}

function Seg({ label, value, onSet, opts, disabledTip }) {
  return (
    <div className="fl"><label>{label}</label>
      <div className="seg">
        {opts.map(([v, lbl, disabled]) => (
          <button key={v} aria-pressed={value === v} disabled={disabled} title={disabled ? disabledTip : ''}
            onClick={() => !disabled && onSet(v)}>{lbl}</button>
        ))}
      </div>
    </div>
  )
}

function TieItem({ title, formula, val, extra }) {
  const good = Math.abs(val) < 1
  return (
    <div className="tieitem">
      <div className="t"><span style={{ width: 8, height: 8, borderRadius: '50%', background: good ? 'var(--good)' : 'var(--serious)' }} />{good ? '✓' : '⚠'} {title}</div>
      <div className="f">{formula}</div>
      <div className="r" style={{ color: good ? 'var(--up)' : 'var(--serious)' }}>差额 {val.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 元</div>
      {extra ? <div className="f" style={{ marginTop: 4 }}>{extra}</div> : null}
    </div>
  )
}

function Statements({ st, cur, pl, BI, acc, U, f, openDef }) {
  const { R, plOf, periods } = acc
  if (st.tab === 'cf') {
    return (
      <div>
        <div className="stmttitle">现金流量表</div>
        <div className="stmtmeta"><span>编制单位：{st.book === GROUP ? '集团合计（未抵消）' : cur.__name || ''}</span><span>{cn(st.period)}</span><span>金额单位：{U}</span></div>
        <div className="empty"><b style={{ color: 'var(--ink-2)' }}>这张表做不出来，不是没做。</b><br />
          现金流量表要按「经营／投资／筹资」给每一笔现金收支打标，这个标在金蝶挂在<b>凭证的现金流量项目</b>上，GL_BALANCE 只有科目余额，没有这一层。<br />
          两条可行路子：① 取 GL_VOUCHER 序时账的现金流量项目字段自行归集；② 由人工从金蝶导出后上传。<br />
          在没打通之前，这里宁可空着，也不拿「货币资金期末－期初」冒充现金流量表。</div>
      </div>
    )
  }
  if (st.tab === 'bs') {
    const ysP = yearStartP(periods, st.period), ys = ysP ? R(ysP, st.book) : null
    const bm = {}; cur.bs.forEach(r => bm[r.n] = r.v)
    const ym = {}; if (ys) ys.bs.forEach(r => ym[r.n] = r.v)
    const dm = cur.detail, ydm = ys ? ys.detail : {}
    const Side = ({ groups, isAsset }) => {
      let liabTotal = 0, liabTotalY = 0
      const body = []
      groups.forEach(([g, items]) => {
        body.push(<tr className="hd" key={'g' + g}><td>{g}：</td><td></td><td></td></tr>)
        let sum = 0, sumY = 0
        items.forEach(n => {
          const v = bm[n] || 0, vy = ym[n]; sum += v; sumY += (vy || 0)
          body.push(<tr className="it" key={g + n}><td><Def name={n} cur={`${f(v)} ${U}`} openDef={openDef} /></td><td>{f(v)}</td><td>{ys ? f(vy || 0) : '—'}</td></tr>)
          if (st.showAcc) (dm[n] || []).filter(a => Math.abs(a.v) > 0.005).forEach(a => {
            const ay = (ydm[n] || []).find(z => z.c === a.c)
            body.push(<tr className="sm" key={g + n + a.c}><td>{a.c} {a.n}</td><td>{f(a.v)}</td><td>{ys ? f(ay ? ay.v : 0) : '—'}</td></tr>)
          })
        })
        body.push(<tr className="sum" key={'s' + g}><td>{g}合计</td><td>{f(sum)}</td><td>{ys ? f(sumY) : '—'}</td></tr>)
        if (!isAsset && g !== '所有者权益') { liabTotal += sum; liabTotalY += sumY }
        if (!isAsset && g === '非流动负债') body.push(<tr className="sum" key="liabtot"><td>负债合计</td><td>{f(liabTotal)}</td><td>{ys ? f(liabTotalY) : '—'}</td></tr>)
      })
      const tot = isAsset ? cur.kpi.assets : cur.kpi.liab + cur.kpi.equity
      const totY = ys ? (isAsset ? ys.kpi.assets : ys.kpi.liab + ys.kpi.equity) : null
      return (
        <div className="tw"><table className="stmt">
          <thead><tr><th>{isAsset ? '资　产' : '负债和所有者权益'}</th><th>期末余额</th><th>年初余额</th></tr></thead>
          <tbody>{body}
            <tr className="grand"><td>{isAsset ? '资产总计' : '负债和所有者权益总计'}</td><td>{f(tot)}</td><td>{ys ? f(totY) : '—'}</td></tr>
          </tbody>
        </table></div>
      )
    }
    const gap = cur.kpi.assets - (cur.kpi.liab + cur.kpi.equity)
    return (
      <div>
        <div className="stmttitle">资产负债表</div>
        <div className="stmtmeta"><span>编制单位：{st.book === GROUP ? '集团合计（未抵消）' : acc.books[st.book]}</span><span>{cn(st.period)}　年初＝{ysP ? cn(ysP) : '无数据'}</span><span>金额单位：{U}</span></div>
        <div className="bs2"><Side groups={BS_ASSET} isAsset={true} /><Side groups={BS_LIAB} isAsset={false} /></div>
        <p className="note" dangerouslySetInnerHTML={{
          __html: `时点数取 <code>FEndBalance</code>（期末本位币），负债与权益已由负转正。年初余额＝上年 12 月末。`
            + (Math.abs(gap) >= 1 ? `<br><b style="color:var(--serious)">两侧差 ${gap.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 元</b>——即尚未结转到本年利润的损益净额，勾稽面板已标出。` : `<br>两侧平衡，差额为 0。`)
        }} />
      </div>
    )
  }
  // 利润表
  const bpl = BI.plPer ? plOf(BI.plPer, st.book, st.basis) : null
  const g = (o, n) => o ? (o.pl.find(x => x.n === n) || { v: 0 }).v : null
  const val = (o, key) => {
    if (!o) return null
    if (key === '__OP__') return o.kpi.op
    if (key === '__TP__') return o.kpi.net
    if (key === '__TAX__') return 0
    if (key === '__NET__') return o.kpi.net
    return g(o, key)
  }
  return (
    <div>
      <div className="stmttitle">利润表</div>
      <div className="stmtmeta"><span>编制单位：{st.book === GROUP ? '集团合计（未抵消）' : acc.books[st.book]}</span><span>{cn(st.period)}　本期口径＝{st.basis === 'ytd' ? '本年累计' : '单月'}</span><span>金额单位：{U}</span></div>
      <div className="tw"><table className="stmt">
        <thead><tr><th>项　目</th>
          <th>本期金额<br /><span style={{ fontWeight: 400, color: 'var(--ink-3)' }}>{st.basis === 'ytd' ? `${st.period.slice(0, 4)} 年 1–${+st.period.slice(5)} 月` : cn(st.period)}</span></th>
          <th>上期金额<br /><span style={{ fontWeight: 400, color: 'var(--ink-3)' }}>{BI.plLabel}</span></th></tr></thead>
        <tbody>
          {PL_LINES.map(([kind, label, key]) => {
            const v = val(pl, key), bv = val(bpl, key)
            const isCalc = key.startsWith('__')
            return (
              <React.Fragment key={label}>
                <tr className={kind === 'lead' ? 'lead sum' : 'it'}>
                  <td>{isCalc ? label : <>{label.replace(key, '')}<Def name={key} cur={`${f(v)} ${U}`} openDef={openDef} /></>}</td>
                  <td>{f(v)}</td><td>{bpl ? f(bv) : '—'}</td>
                </tr>
                {st.showAcc && !isCalc && (pl.pdetail[key] || []).filter(a => Math.abs(a.v) > 0.005).map(a => {
                  const ab = bpl ? (bpl.pdetail[key] || []).find(z => z.c === a.c) : null
                  return <tr className="sm" key={key + a.c}><td>{a.c} {a.n}</td><td>{f(a.v)}</td><td>{bpl ? f(ab ? ab.v : 0) : '—'}</td></tr>
                })}
              </React.Fragment>
            )
          })}
          <tr className="sm"><td>对照锚点：4103 本年利润净额（恒为本年累计）</td><td>{f(cur.tie.p4103)}</td><td>—</td></tr>
        </tbody>
      </table></div>
      <p className="note" dangerouslySetInnerHTML={{
        __html: `损益已按月结转、净额被抹平，故一律取<b>单边</b>：收入类取 <code>FYtdCredit</code>，成本费用类取 <code>FYtdDebit</code>。账套<b>无所得税费用科目</b>，故「减：所得税费用」恒为 0、利润总额＝净利润。最后一行是独立锚点——本年累计口径下应与「四、净利润」一致，对不上即有未结转项。`
      }} />
    </div>
  )
}

function Footer() {
  return (
    <div className="foot">
      <b>口径说明（全部经活账套实测）</b><br />
      · <b>去重</b>：GL_BALANCE 同一科目沿核算维度轴与币别轴各返回重复行，取 <code>FDetailID=0</code> ＋ 币别为空 ＋ 一级科目；不去重直接加总会虚增约 4.6 倍。<br />
      · <b>利润表</b>：账套每月做损益结转，损益类科目净额被抹平，故一律取<b>单边</b>——收入类累计贷方、成本费用类累计借方。<br />
      · <b>对比基准</b>：本年累计口径下<b>没有环比</b>，该选项禁用。资产负债表项目是时点数，基准恒为上年同月末／上月末。<br />
      · <b>年初余额</b>＝上年 12 月末；<b>上期金额</b>＝上年同期同口径。2024 年 1 月起有数。<br />
      · <b>方向</b>：负债与所有者权益的 <code>FEndBalance</code> 为负，报表展示已取正。　· <b>合并</b>：分部列示，未抵消内部往来，不是合并报表。<br />
      · <b>币种</b>：各账簿本位币直接相加，境外主体本位币非人民币，「集团合计」严格不成立，正式版须接汇率工具折算。<br />
      · 五条风险规则为草案，阈值待财务确认。全程只读，未向金蝶写入。带虚线下划线的名称可点开定义卡（口径落「指标中心」页）。
    </div>
  )
}
