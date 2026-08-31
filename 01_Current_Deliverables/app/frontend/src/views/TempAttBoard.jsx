// [Change Log] Date:2026-08-18 Author:Claude/c Version:V2.318
// 【临时工看板】数据源＝历次复核的留档结果（V2.334 起），**不再上传《临工结构》**。
// 结构表要有人按月手工维护，维护的人一停看板就悄悄过期；而复核每月都要做，结果本来就在库里。
// 画法沿用配色校验过的那套（三色分类板 #2a78d6/#eb6834/#1baf7a，明暗两套步值，见 styles.css 的 .ta-board）：
// 细描边、数据末端 4px 圆角、堆叠段之间留 2px 表面缝、只对峰值与异常做直接标注、悬停出明细、每图可展开数据表。
// 图表用命令式建 SVG（useEffect + ref）而不是 JSX：几何代码是已验证过的原样搬来，改写成 JSX 只会平添出错机会。
import React, { useEffect, useRef, useState } from 'react'
import { tempattBoard } from '../api'

const NS = 'http://www.w3.org/2000/svg'
const wan = v => (v / 10000).toFixed(1)
const num = v => Math.round(v).toLocaleString('zh-CN')
const pct = v => (v * 100).toFixed(1) + '%'
const el = (t, a = {}, p) => { const n = document.createElementNS(NS, t); for (const k in a) if (a[k] != null) n.setAttribute(k, a[k]); if (p) p.appendChild(n); return n }

export default function TempAttBoard() {
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState({ d6: true })   // 第⑥段是纯表格，默认展开
  const [empty, setEmpty] = useState('')
  const [cached, setCached] = useState(false)
  const refs = { c1: useRef(), c2: useRef(), c3: useRef(), c4: useRef(), c5: useRef() }
  const tips = { t1: useRef(), t2: useRef(), t3: useRef(), t4: useRef(), t5: useRef() }

  // 进页面就取，不需要任何人先喂数据。fresh=1 只在手动点「重算」时用——
  // 缓存按期次签名失效，跑完一期新的会自动重算，平时不必刷
  const load = async (fresh) => {
    setBusy(true); setErr(''); setEmpty('')
    try {
      const r = await tempattBoard(fresh)
      if (!r.ok) { setErr(r.msg || '取数失败'); setRes(null); return }
      if (r.空) { setRes(null); setEmpty(r.msg || '还没有已核期次'); return }
      setRes(r); setCached(!!r.命中缓存)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  useEffect(() => { load(false) }, [])

  useEffect(() => {
    if (!res) return
    Object.values(refs).forEach(r => { if (r.current) r.current.innerHTML = '' })
    drawDept(refs.c1.current, tips.t1.current, res)
    drawMonthly(refs.c2.current, tips.t2.current, res)
    drawNight(refs.c3.current, tips.t3.current, res)
    drawHeat(refs.c4.current, tips.t4.current, res)
    drawRate(refs.c5.current, tips.t5.current, res)
  }, [res])

  const K = res?.kpi, thin = res?.残缺月 || []
  const tog = k => setOpen(o => ({ ...o, [k]: !o[k] }))

  return (
    <div className="ta-board">
      <div className="head">
        <div>
          <div className="h-title">临时工看板 · 用工结构</div>
          <div className="h-sub">钱花在哪个车间、每月多少、夜班占多少、跟谁在做、贵不贵。
            <b>数据来自历次复核的留档，不用上传任何东西</b>——复核工具每跑一期，这里自动多一个月。</div>
        </div>
      </div>

      <div className="body">
        <div className="card ta-src">
          <span><b>数据源</b>　已核期次 {res ? res.期次.length : 0} 期
            {res && <>（{res.months[0]} – {res.months[res.months.length - 1]}）</>}
            {cached && <span className="note">　·　读自缓存</span>}
          </span>
          <button className="btn" onClick={() => load(true)} disabled={busy}>{busy ? '汇总中…' : '重算'}</button>
          <span className="note">
            缓存按期次签名自动失效——新核一期、改参数重跑、删掉一期，下次进来就是新的，平时不用点「重算」。
          </span>
        </div>

        {err && <div className="card" style={{ padding: 12, marginBottom: 14, color: '#b91c1c', background: '#fef2f2' }}>{err}</div>}

        {!res && !busy && <div className="card" style={{ padding: 24, color: 'var(--ink-3)', lineHeight: 1.9 }}>
          {empty || '暂无数据。'}<br />
          本看板<b>不需要上传任何文件</b>：到「复核工具」跑一期，这里就会自动出现一个月。
        </div>}
        {!res && busy && <div className="card" style={{ padding: 24, color: 'var(--ink-3)' }}>汇总中…</div>}

        {res && <>
          <div className="tiles">
            <Tile k={`${K.期数} 期用工成本`} v={'¥' + wan(K.全年金额) + ' 万'} n={`${K.派遣方家数} 家派遣公司 · ${res.depts.length} 个车间`} />
            <Tile k="累计工时" v={num(K.全年工时) + ' 小时'} n="白班 + 夜班，补贴/奖/罚不计工时" />
            <Tile k="综合有效单价" v={'¥' + K.有效单价 + ' /小时'} n="金额 ÷ 工时（加权，非合同价）" />
            <Tile k="夜班工时占比" v={pct(K.夜班工时占比)} n={'夜班金额占比 ' + pct(K.夜班金额占比)} />
            <Tile k="头部集中度" v={pct(K.头部占比)} n={K.头部 + ' 一家占累计金额'} />
            {thin.length > 0 && <Tile flag k="数据完整性" v={thin.join('、') + ' 偏低'}
              n="金额不足各期均值的四分之一，与其它月不可比——多半是那期只核了部分车间；图上已单独标色" />}
          </div>

          <Sec n="①" t="各车间的用工金额"
            lead={`先看钱花在哪个车间。${res.depts.map(d => `${d} ${wan(res.monthly.reduce((s, m) => s + (m.部门[d] || 0), 0))} 万`).join('、')}。`}
            cref={refs.c1} tref={tips.t1} id="d1" open={open} tog={tog}
            table={[['月份', ...res.depts, '合计'], ...res.monthly.map(m => [m.m, ...res.depts.map(d => num(m.部门[d] || 0)), num(res.depts.reduce((s, d) => s + (m.部门[d] || 0), 0))])]} />

          <Sec n="②" t="每月用工成本与构成"
            lead="按金额拆白班 / 夜班 / 补贴奖罚三段。每核一期，这里自动多一根柱子。"
            cref={refs.c2} tref={tips.t2} id="d2" open={open} tog={tog}
            note={thin.length > 0 ? `${thin.join('、')}这根柱子不能按业务量读——它只有 ¥${num(res.monthly.find(m => thin.includes(m.m)).合计金额)}、${num(res.monthly.find(m => thin.includes(m.m)).总工时)} 小时，而相邻月份是它的十倍上下。原因通常是那一期的结算表只并了部分车间（比如只有植物肉、小料没进来）。` : ''}
            table={[['月份', '白班金额', '夜班金额', '补贴', '合计', '总工时', '有效单价'],
              ...res.monthly.map(m => [m.m, num(m.白班金额), num(m.夜班金额), num(m.补贴金额), num(m.合计金额), num(m.总工时), m.有效单价])]} />

          <Sec n="③" t="夜班工时占比"
            lead="夜班比白班贵，所以这条线基本决定了当月的综合有效单价。占比 0 不一定是真的没排夜班——也可能那期的结算表没覆盖夜班车间。"
            cref={refs.c3} tref={tips.t3} id="d3" open={open} tog={tog}
            table={[['月份', '白班工时', '夜班工时', '夜班占比', '有效单价'],
              ...res.monthly.map(m => [m.m, num(m.白班工时), num(m.夜班工时), pct(m.夜班工时占比), m.有效单价])]} />

          <Sec n="④" t="派遣方 × 月份 金额热力图"
            lead="颜色深浅＝该月金额，空白＝当月没有发生。供应商进出一目了然。"
            cref={refs.c4} tref={tips.t4} id="d4" open={open} tog={tog}
            table={[['派遣方', ...res.months, '合计', '出现期数'],
              ...res.company.map(c => [c.c, ...c.月.map(v => v > 0 ? num(v) : '—'), num(c.合计), c.活跃月数])]} />

          <Sec n="⑤" t="各派遣方：有效单价 ≈ 白班价 + 价差 × 夜班占比"
            lead="横轴＝该家累计夜班工时占比，纵轴＝累计有效单价。灰线是理论价。落在线上说明单价结构自洽——各家“贵不贵”由夜班多少决定，不是价格差异；明显低于线的通常是掺了保洁这类单独定价的岗位。"
            cref={refs.c5} tref={tips.t5} id="d5" open={open} tog={tog}
            note={rateNote(res)}
            table={[['派遣方', '累计金额', '工时', '夜班占比', '有效单价', '理论价', '偏离'],
              ...res.company.map(c => { const th = theo(res, c.夜班占比); return [c.c, num(c.合计), num(c.工时), pct(c.夜班占比), c.有效单价, th.toFixed(2), (c.有效单价 - th >= 0 ? '+' : '') + (c.有效单价 - th).toFixed(2)] })]} />

          <Sec n="⑥" t="各期复核状态" lead="看板不藏异常——把出过问题的月份盖掉，比看不到还糟。这一段列出每期复核的判定结果，数字不为 0 的月份，图上的钱要连着这里一起读。"
            id="d6" open={open} tog={tog} forceTable
            table={[['月份', '复核结论', '人数', '比对人日', '⚠异常多记(日次)', '金额核对(处)', '奖罚异常(笔)', '同名跨派遣方', '缺合同价(人)', '金额来源'],
              ...res.期次.map(s => [s.m, s.结论 || '—', s.人数, s.比对人日, s.异常多记日次, s.金额核对条数, s.奖罚异常, s.同名跨派遣方,
                s.缺合同价人数 ?? '—', s.金额来源 + (s.未计价工时 ? `（${s.未计价工时} 小时拆不出金额）` : '')])]} />

          <div style={{ marginTop: 26, paddingTop: 12, borderTop: '1px solid var(--line)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.9 }}>
            口径说明：金额取<b>结算表自己的金额（＝各家请款额）</b>，与复核结论页主列一致；该期若无金额列才回落到按合同单价重算，
            已在第⑥段逐期标明。白/夜班金额按各人各自的单价拆算，补贴/奖/罚单列且不计工时。
            「有效单价」＝该维度金额 ÷ 工时，是加权结果，不是合同价。<br />
            本看板只做结构呈现、不判断对错；工时准不准去「复核工具」。<b>数据随复核自动累积，无需上传。</b>
          </div>
        </>}
      </div>
    </div>
  )
}

/* ── 理论价 ──
   白班价与白夜价差取自后端从历次留档各人单价按金额加权取的众数，**前端不写死 19/22**：
   调价之后理论线要跟着走，写死就会悄悄画错一条线还看不出来。 */
function theo(res, ratio) {
  const d = res.标准单价?.白班 || 0, n = res.标准单价?.夜班 || 0
  return d + (n - d) * ratio
}
function rateNote(res) {
  if (!(res.标准单价?.夜班 > res.标准单价?.白班)) return '本期各家都没有夜班（或取不到夜班标准单价），没有价差就画不出理论线——这不是异常。'
  const off = res.company.filter(c => Math.abs(c.有效单价 - theo(res, c.夜班占比)) > 0.05)
  if (!off.length) return `${res.company.length} 家全部落在理论线上（白班 ¥${res.标准单价.白班}、夜班 ¥${res.标准单价.夜班}），单价结构完全自洽。`
  return `偏离理论线的有 ${off.length} 家：${off.map(c => `${c.c} ${(c.有效单价 - theo(res, c.夜班占比)).toFixed(2)}`).join('、')}。` +
    '偏低通常是掺了保洁这类单独定价、无管理费的岗位，把加权价拉了下来。' +
    '提醒一句：拿「有效单价」横向比价没有意义，比之前要先剔除夜班结构和特殊岗位。'
}

/* ── 版式小件 ── */
function Tile({ k, v, n, flag }) {
  return <div className={'tile' + (flag ? ' flag' : '')}>
    <div className="k">{k}</div><div className="v">{v}</div><div className="n">{n}</div></div>
}
/* forceTable：这一段没有图，只有表（如「各期复核状态」）——不渲染空的画布位，表默认展开 */
function Sec({ n, t, lead, cref, tref, id, open, tog, table, note, forceTable }) {
  const show = forceTable ? open[id] !== false : !!open[id]
  return <section>
    <h3>{n} {t}</h3>
    <p className="lead">{lead}</p>
    <div className="panel">
      {!forceTable && <>
        <div ref={cref} />
        <div className="tip" ref={tref} />
      </>}
      <button className="tblbtn" onClick={() => tog(id)}>显示/隐藏数据表</button>
      {show && <table><thead><tr>{table[0].map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>{table.slice(1).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table>}
    </div>
    {note && <div className="vnote">{note}</div>}
  </section>
}

/* ── tooltip ── */
function tipper(tipEl) {
  const host = tipEl.parentElement
  return {
    show(html, ev) {
      tipEl.innerHTML = html; tipEl.style.opacity = 1
      const b = host.getBoundingClientRect()
      let x = ev.clientX - b.left + 14, y = ev.clientY - b.top - 10
      if (x + tipEl.offsetWidth > b.width - 6) x = ev.clientX - b.left - tipEl.offsetWidth - 14
      tipEl.style.left = Math.max(4, x) + 'px'; tipEl.style.top = Math.max(4, y) + 'px'
    },
    hide() { tipEl.style.opacity = 0 },
  }
}
const CS = k => `var(--tb-${k})`

/* ── ① 车间分组柱 ── */
function drawDept(host, tipEl, res) {
  const M = res.monthly, D = res.depts
  const W = 1060, H = 250, mL = 60, mR = 16, mT = 12, mB = 38
  const iw = W - mL - mR, ih = H - mT - mB
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': '各车间每月用工金额' }, host)
  const max = Math.max(...M.flatMap(m => D.map(d => m.部门[d] || 0))) * 1.14 || 1
  const y = v => mT + ih - v / max * ih
  const slot = iw / M.length, bw = Math.min(20, slot * 0.34 / Math.max(1, D.length / 2))
  const g = el('g', { class: 'grid' }, svg)
  for (let i = 0; i <= 4; i++) {
    const v = max / 4 * i
    el('line', { x1: mL, x2: mL + iw, y1: y(v), y2: y(v) }, g)
    el('text', { x: mL - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'ax' }, svg).textContent = (v / 10000).toFixed(0) + '万'
  }
  const tp = tipper(tipEl)
  legend(host, D.map((d, i) => [d, CS(i + 1)]))
  M.forEach((m, i) => {
    D.forEach((d, o) => {
      const v = m.部门[d] || 0; if (v <= 0) return
      const x = mL + slot * i + slot / 2 - (bw * D.length + 2 * (D.length - 1)) / 2 + o * (bw + 2)
      const r = el('rect', { x, y: y(v), width: bw, height: Math.max(1, y(0) - y(v)), fill: CS(o + 1), rx: 4 }, svg)
      r.addEventListener('mousemove', e => tp.show(`<b>${m.m} · ${d}</b><span>¥${num(v)}（${wan(v)} 万）</span>` +
        (res.残缺月.includes(m.m) ? '<span style="color:var(--tb-warn)">⚠ 本月数据残缺</span>' : ''), e))
      r.addEventListener('mouseleave', tp.hide)
    })
    el('text', { x: mL + slot * i + slot / 2, y: H - mB + 16, 'text-anchor': 'middle', class: 'axl',
      fill: res.残缺月.includes(m.m) ? CS('warn') : null }, svg).textContent = m.m
  })
}

/* ── ② 月度堆叠柱 ── */
function drawMonthly(host, tipEl, res) {
  const M = res.monthly
  const W = 1060, H = 300, mL = 60, mR = 16, mT = 14, mB = 40
  const iw = W - mL - mR, ih = H - mT - mB
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': '每月用工成本堆叠柱' }, host)
  const max = Math.max(...M.map(d => d.合计金额)) * 1.12 || 1
  const y = v => mT + ih - v / max * ih
  const bw = Math.min(46, iw / M.length * 0.62)
  const g = el('g', { class: 'grid' }, svg)
  for (let i = 0; i <= 5; i++) {
    const v = max / 5 * i
    el('line', { x1: mL, x2: mL + iw, y1: y(v), y2: y(v) }, g)
    el('text', { x: mL - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'ax' }, svg).textContent = (v / 10000).toFixed(0) + '万'
  }
  const tp = tipper(tipEl)
  const SER = [['白班金额', 1, '白班'], ['夜班金额', 2, '夜班'], ['补贴金额', 3, '补贴']]
  legend(host, SER.map(([, i, n]) => [n, CS(i)]).concat(res.残缺月.length ? [['数据残缺月', CS('warn')]] : []))
  M.forEach((d, i) => {
    const x = mL + iw / M.length * i + (iw / M.length - bw) / 2
    let acc = 0
    SER.forEach(([key, ci], si) => {
      const v = d[key]; if (v <= 0) return
      const y0 = y(acc + v), y1 = y(acc)
      const top = acc + v >= d.合计金额 - 1
      const r = el('rect', { x, y: y0, width: bw, height: Math.max(1, y1 - y0 - (si ? 2 : 0)),
        fill: CS(ci), rx: top ? 4 : 0, ry: top ? 4 : 0 }, svg)
      r.addEventListener('mousemove', e => tp.show(
        `<b>${d.m}</b><span>白班 ¥${num(d.白班金额)}<br>夜班 ¥${num(d.夜班金额)}<br>补贴 ¥${num(d.补贴金额)}<br>
         合计 ¥${num(d.合计金额)}<br>工时 ${num(d.总工时)} h · 有效单价 ¥${d.有效单价}</span>` +
        (res.残缺月.includes(d.m) ? '<span style="color:var(--tb-warn)">⚠ 本月数据残缺</span>' : ''), e))
      r.addEventListener('mouseleave', tp.hide)
      acc += v
    })
    const bad = res.残缺月.includes(d.m)
    el('text', { x: x + bw / 2, y: H - mB + 17, 'text-anchor': 'middle', class: 'axl', fill: bad ? CS('warn') : null }, svg).textContent = d.m
    if (d.合计金额 === res.kpi.峰值金额 || bad)
      el('text', { x: x + bw / 2, y: y(d.合计金额) - 7, 'text-anchor': 'middle', class: 'dl', fill: bad ? CS('warn') : null }, svg)
        .textContent = wan(d.合计金额) + '万'
  })
}

/* ── ③ 夜班占比折线 ── */
function drawNight(host, tipEl, res) {
  const M = res.monthly
  const W = 1060, H = 220, mL = 60, mR = 46, mT = 16, mB = 34
  const iw = W - mL - mR, ih = H - mT - mB
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': '夜班工时占比' }, host)
  const max = Math.max(0.5, ...M.map(m => m.夜班工时占比)) * 1.05
  const y = v => mT + ih - v / max * ih
  const x = i => mL + iw / Math.max(1, M.length - 1) * i
  const g = el('g', { class: 'grid' }, svg)
  const step = max > 0.6 ? 0.2 : 0.1
  for (let v = 0; v <= max; v += step) {
    el('line', { x1: mL, x2: mL + iw, y1: y(v), y2: y(v) }, g)
    el('text', { x: mL - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'ax' }, svg).textContent = (v * 100).toFixed(0) + '%'
  }
  // 残缺月把线断开，并用虚线跨过去——占比 0 不是「真的没排夜班」，连成实线会误导
  let seg = []
  M.forEach((d, i) => {
    if (res.残缺月.includes(d.m)) { if (seg.length > 1) line(seg); seg = []; return }
    seg.push([x(i), y(d.夜班工时占比)])
  })
  if (seg.length > 1) line(seg)
  function line(pts) {
    el('path', { d: 'M' + pts.map(p => p.join(' ')).join('L'), fill: 'none', stroke: CS(1), 'stroke-width': 2, 'stroke-linejoin': 'round' }, svg)
  }
  res.残缺月.forEach(mm => {
    const i = M.findIndex(d => d.m === mm)
    if (i > 0 && i < M.length - 1)
      el('line', { x1: x(i - 1), y1: y(M[i - 1].夜班工时占比), x2: x(i + 1), y2: y(M[i + 1].夜班工时占比),
        stroke: 'var(--ink-3)', 'stroke-width': 1.5, 'stroke-dasharray': '4 4' }, svg)
  })
  const tp = tipper(tipEl)
  M.forEach((d, i) => {
    const bad = res.残缺月.includes(d.m)
    el('circle', { cx: x(i), cy: y(d.夜班工时占比), r: bad ? 5 : 4.5, fill: bad ? 'var(--bg-sub)' : CS(1),
      stroke: bad ? CS('warn') : 'var(--bg-sub)', 'stroke-width': 2 }, svg)
    const hit = el('circle', { cx: x(i), cy: y(d.夜班工时占比), r: 14, fill: 'transparent' }, svg)
    hit.addEventListener('mousemove', e => tp.show(
      `<b>${d.m}</b><span>夜班 ${num(d.夜班工时)} h / 总 ${num(d.总工时)} h<br>占比 ${pct(d.夜班工时占比)} · 有效单价 ¥${d.有效单价}</span>` +
      (bad ? '<span style="color:var(--tb-warn)">⚠ 本月数据残缺，0% 不代表真的没排夜班</span>' : ''), e))
    hit.addEventListener('mouseleave', tp.hide)
    el('text', { x: x(i), y: H - mB + 16, 'text-anchor': 'middle', class: 'axl', fill: bad ? CS('warn') : null }, svg).textContent = d.m
  })
  ;[0, M.length - 1].forEach(i => el('text', { x: x(i), y: y(M[i].夜班工时占比) - 12, 'text-anchor': 'middle', class: 'dl' }, svg)
    .textContent = pct(M[i].夜班工时占比))
}

/* ── ④ 热力图 ── */
function drawHeat(host, tipEl, res) {
  const C = res.company, M = res.months
  const cw = 74, ch = 30, mL = 66, mT = 26
  const W = mL + cw * M.length + 70, H = mT + ch * C.length + 12
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': '派遣方与月份金额热力图' }, host)
  const max = Math.max(...C.flatMap(c => c.月)) || 1
  const steps = [1, 2, 3, 4, 5, 6].map(i => `var(--tb-seq-${i})`)
  const tp = tipper(tipEl)
  M.forEach((m, j) => el('text', { x: mL + cw * j + cw / 2, y: mT - 9, 'text-anchor': 'middle', class: 'axl',
    fill: res.残缺月.includes(m) ? CS('warn') : null }, svg).textContent = m)
  C.forEach((c, i) => {
    el('text', { x: mL - 10, y: mT + ch * i + ch / 2 + 4, 'text-anchor': 'end', class: 'axl' }, svg).textContent = c.c
    c.月.forEach((v, j) => {
      const k = v <= 0 ? -1 : Math.min(steps.length - 1, Math.floor(v / max * steps.length))
      const r = el('rect', { x: mL + cw * j + 1, y: mT + ch * i + 1, width: cw - 2, height: ch - 2, rx: 4,
        fill: k < 0 ? 'transparent' : steps[k], stroke: k < 0 ? 'var(--line)' : null, 'stroke-dasharray': k < 0 ? '3 3' : null }, svg)
      if (v > 0) el('text', { x: mL + cw * j + cw / 2, y: mT + ch * i + ch / 2 + 4, 'text-anchor': 'middle',
        class: 'ax', fill: k >= 4 ? '#fff' : 'var(--ink-2)' }, svg).textContent = wan(v)
      r.addEventListener('mousemove', e => tp.show(v > 0
        ? `<b>${c.c} · ${M[j]}</b><span>¥${num(v)}（${wan(v)} 万）</span>`
        : `<b>${c.c} · ${M[j]}</b><span>本月无发生</span>`, e))
      r.addEventListener('mouseleave', tp.hide)
    })
    el('text', { x: mL + cw * M.length + 10, y: mT + ch * i + ch / 2 + 4, class: 'dl' }, svg).textContent = wan(c.合计) + '万'
  })
}

/* ── ⑤ 有效单价 vs 夜班占比 ── */
function drawRate(host, tipEl, res) {
  const C = res.company
  const W = 1060, H = 330, mL = 62, mR = 26, mT = 18, mB = 46
  const iw = W - mL - mR, ih = H - mT - mB
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': '各派遣方有效单价与夜班占比' }, host)
  const vs = C.map(c => c.有效单价)
  const lo = Math.floor(Math.min(...vs) * 2) / 2 - 0.4, hi = Math.ceil(Math.max(...vs) * 2) / 2 + 0.4
  const y = v => mT + ih - (v - lo) / (hi - lo) * ih
  const x = v => mL + v * iw
  const g = el('g', { class: 'grid' }, svg)
  for (let v = Math.ceil(lo * 2) / 2; v <= hi; v += 0.5) {
    el('line', { x1: mL, x2: mL + iw, y1: y(v), y2: y(v) }, g)
    el('text', { x: mL - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'ax' }, svg).textContent = '¥' + v.toFixed(1)
  }
  for (let p = 0; p <= 1.0001; p += 0.2)
    el('text', { x: x(p), y: H - mB + 18, 'text-anchor': 'middle', class: 'axl' }, svg).textContent = (p * 100).toFixed(0) + '%'
  const t0 = theo(res, 0), t1 = theo(res, 1)
  const hasLine = t1 > t0                       // 表里没取到标准单价就不画这条线，免得画出一条平的误导人
  if (hasLine) {
    el('line', { x1: x(0), y1: y(t0), x2: x(1), y2: y(t1), stroke: 'var(--ink-3)', 'stroke-width': 1.5, 'stroke-dasharray': '5 4' }, svg)
    el('text', { x: x(0.62), y: y(theo(res, 0.62)) - 9, class: 'ax', 'text-anchor': 'middle' }, svg)
      .textContent = `理论价 = ${t0.toFixed(2)} + ${(t1 - t0).toFixed(2)} × 夜班占比`
  }
  const rMax = Math.max(...C.map(c => c.合计)) || 1
  // 标注避让：横轴挨太近的把标签甩到气泡下面，免得两个名字叠在一起
  const sorted = [...C].sort((a, b) => a.夜班占比 - b.夜班占比)
  const below = new Set()
  sorted.forEach((c, i) => {
    const p = sorted[i - 1]
    if (p && Math.abs(x(c.夜班占比) - x(p.夜班占比)) < 62 && !below.has(p.c)) below.add(c.c)
  })
  const tp = tipper(tipEl)
  C.forEach(c => {
    const th = theo(res, c.夜班占比), dev = c.有效单价 - th, off = hasLine && Math.abs(dev) > 0.05
    const rr = 7 + Math.sqrt(c.合计 / rMax) * 17
    const n = el('circle', { cx: x(c.夜班占比), cy: y(c.有效单价), r: rr, fill: off ? CS(2) : CS(1),
      'fill-opacity': .55, stroke: 'var(--bg-sub)', 'stroke-width': 2 }, svg)
    n.addEventListener('mousemove', e => tp.show(
      `<b>${c.c}</b><span>全年 ¥${num(c.合计)}（${wan(c.合计)} 万）· ${num(c.工时)} h<br>
       夜班占比 ${pct(c.夜班占比)}<br>有效单价 ¥${c.有效单价}　理论 ¥${th.toFixed(2)}<br>
       偏离 ${dev >= 0 ? '+' : ''}${dev.toFixed(2)}</span>`, e))
    n.addEventListener('mouseleave', tp.hide)
    el('text', { x: x(c.夜班占比), y: y(c.有效单价) + (below.has(c.c) ? rr + 15 : -rr - 6), 'text-anchor': 'middle', class: 'dl' }, svg)
      .textContent = c.c
  })
  el('text', { x: mL + iw / 2, y: H - 5, 'text-anchor': 'middle', class: 'ax' }, svg).textContent = '全年夜班工时占比 →'
}

function legend(host, items) {
  // 换一份文件重画时，面板里上一次的图例还在——先清掉，否则每传一次多一行
  host.parentElement.querySelectorAll('.vlegend').forEach(x => x.remove())
  const d = document.createElement('div')
  d.className = 'vlegend'
  d.innerHTML = items.map(([n, c]) => `<span><i style="background:${c}"></i>${n}</span>`).join('')
  host.parentElement.insertBefore(d, host)
}
