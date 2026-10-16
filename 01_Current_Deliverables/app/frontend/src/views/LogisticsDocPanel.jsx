// [Change Log] Date:2026-10-17 Author:Claude Opus 5.5 Version:V2.987
// 账单复核台第②步·逐单核对改版（照 2026-10-16 样机定版，10-17 用户「样机更清晰，执行」对齐样机）：
//   · judge(x)：每张单的结论（多收/少收/价待确认/量核不了/只核了价/一致）、差额、复核金额，以及 ①量 ②价 两格的一句话＋依据——表格、卡片、弹窗共用这一套
//   · DocCards：一行 7 张数字卡（用户 10-16 定），点卡片筛单
//   · ClsCell：「③ 归类」一格
//   · DocDialog：点一张单弹窗：结论 → 钱＝量×价 → 金蝶出库明细 → 记到哪 → 处理
//   · RulesDialog / AccrTabDialog / DupRuleDialog：归类规则（可改）、和本月计提对照、重复收费口径
// 「价」现在只有包天包趟报价、恒茂这类按公式核的有依据；其余是「价待确认」，等「价的依据库」那一步补
import React, { useEffect, useState } from 'react'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const num = (v, dp = 2) => (v == null || v === '' ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: dp }))
const isZero = d => d != null && Math.abs(d) < 0.01
const r2 = v => Math.round(v * 100) / 100

export const CLS_PILL = { 归类一致: 'ok', 标注不对: 'bad', 计提记错: 'warn', 判不了: 'neu' }
const CLS_TODO = new Set(['标注不对', '计提记错'])
export const V_PILL = { 多收: 'bad', 少收: 'blue', 价待确认: 'warn', 量核不了: 'orange', 只核了价: 'neu', 一致: 'ok' }

// ---------- 一张单怎么判（量 → 价 → 钱） ----------
export function judge(x) {
  const sf = x.sub_fees || {}, fee = x.doc_fee || 0, u = x.kd_unit || x.bill_unit || ''
  // ① 量
  let q
  if (!x.doc_no) q = { top: '没有金蝶单号', sub: '只登记不核量', k: 'miss' }
  else if (x.trip) q = { top: `${num(x.trip.n || x.bill_amt)} ${x.trip.unit || x.bill_unit || '趟'}`, sub: `按${x.trip.unit || '趟'}收钱，不比金蝶量`, k: 'ok' }
  else if (x.state === 'miss') q = { top: '金蝶查无', sub: '金蝶里没找到这张单的出库单', k: 'miss' }
  else if (x.state === 'info') q = { top: '免核', sub: x.mode_short || x.mode_cn || '不核数量', k: 'na' }
  else if (x.state === 'qtydiff' && !x.kd_sum) q = { top: '量核不了', sub: `金蝶这张单没有可比的${u === '千克' ? '重量' : '量'}（按个计等）；账单 ${num(x.bill_amt)} ${x.bill_unit || ''}`, k: 'miss' }
  else if (x.state === 'qtydiff') {
    // 按重量核的有合理区间(换算系数＝账单重量÷金蝶净重)：只有超出上限的部分算多报、低于下限的部分算少报(用户 10-14 定)
    const rg = x.chk && x.chk.kind === 'weight' && x.chk.rng ? x.chk.rng : null
    const b = x.bill_amt || 0, k = x.kd_sum || 0
    const d = rg ? (b > k * rg[1] ? b - k * rg[1] : b < k * rg[0] ? b - k * rg[0] : 0) : b - k
    const sub = rg ? `系数 ${x.conv != null ? x.conv : num(b / k, 3)} ${d > 0 ? '超出' : '低于'} ${rg[0]}～${rg[1]}；账单 ${num(b)} / 金蝶 ${num(k)} ${u}` : `账单 ${num(b)} / 金蝶 ${num(k)} ${u}`
    q = { top: `${d >= 0 ? '多报' : '少报'} ${num(Math.abs(d))} ${u}`, sub, k: d >= 0 ? 'bad' : 'low', d: r2(d) }
  } else q = { top: u === '箱' ? '箱数对上' : u === '千克' ? '重量对上' : '量对上', sub: `账单 ${num(x.bill_amt)} / 金蝶 ${num(x.kd_sum)} ${u}` + (x.conv != null && x.conv !== 1 ? ` · 系数 ${x.conv}` : ''), k: 'ok' }
  // ② 价
  let p, pd = null
  const tgt = Object.keys(sf).find(k => !['标准', '公式', '核价差', '箱型'].includes(k) && typeof sf[k] === 'number')
  if (x.trip && x.trip.price != null && x.trip.std != null) {
    pd = r2((x.trip.run || 0) - x.trip.std)
    p = Math.abs(pd) < 0.005
      ? { top: `按报价 · ${num(x.trip.price)}`, sub: `报价 ${num(x.trip.price)} × ${num(x.trip.n)} ＝ ${money(x.trip.std)}`, k: x.trip.ot ? 'tbd' : 'ok' }
      : { top: `${pd > 0 ? '比报价高' : '比报价低'} · ${money(x.trip.run)}`, sub: `报价 ${num(x.trip.price)} × ${num(x.trip.n)} ＝ ${money(x.trip.std)}`, k: pd > 0 ? 'bad' : 'low' }
    if (x.trip.ot) p.sub += `；加班 ${money(x.trip.ot)} 报价未列价`
  } else if (x.trip) p = { top: '报价里没有这个车型', sub: x.trip.msg || '', k: 'tbd' }
  else if ('标准' in sf) {
    pd = tgt ? r2(sf[tgt] - sf['标准']) : null
    p = '核价差' in sf
      ? { top: `和公式对不上 · ${money(sf[tgt])}`, sub: `${sf['公式'] || '按公式'} ＝ ${money(sf['标准'])}`, k: pd != null && pd < 0 ? 'low' : 'bad' }
      : { top: '按公式对上', sub: `${sf['公式'] || '按公式'} ＝ ${money(sf['标准'])}`, k: 'ok' }
  } else p = { top: '价没依据', sub: '没有合同价可比（价的依据库还没做）', k: 'tbd' }
  // 钱：差额＝量差折钱（账单金额 ÷ 账单量 倒推单价）＋价差
  let diff = null
  if (q.d != null && x.bill_amt) diff = r2(q.d * fee / x.bill_amt)
  if (pd != null && Math.abs(pd) >= 0.005) diff = r2((diff || 0) + pd)
  let v
  if (!x.doc_no) v = '只核了价'
  else if (diff != null && diff > 0.005) v = '多收'
  else if (q.k === 'bad') v = '多收'
  else if (x.state === 'miss' || q.k === 'miss') v = '量核不了'
  else if (diff != null && diff < -0.005) v = '少收'
  else if (q.k === 'low' || p.k === 'low') v = '少收'
  else if (p.k === 'tbd') v = '价待确认'
  else v = '一致'
  const rv = v === '量核不了' || (v === '多收' && diff == null) ? null : r2(fee - (diff || 0))
  const why = v === '一致' ? (q.k === 'na' ? '免核数量，价对上' : '量价都对上')
    : v === '只核了价' ? '没标金蝶单号'
      : v === '量核不了' ? (x.state === 'miss' ? '金蝶查不到这张单' : '金蝶这张单没有可比的量')
        : v === '价待确认' ? p.top
          : [q.k === 'bad' || q.k === 'low' ? q.top : '', p.k === 'bad' || p.k === 'low' ? p.top : ''].filter(Boolean).join('；')
  return { v, diff, rv, q, p, why }
}

// ---------- 七张卡的口径 ----------
const docOk = x => !!x.doc_no && (x.n_mat || 0) > 0
const dupOn = (x, dups) => { const u = dups && dups[x.doc_no]; return !!(u && !u.ok) }
export const KPRED = {
  nock: x => !docOk(x),
  ok: (x, cls, dups) => (x.confirmed || judge(x).v === '一致') && (!cls[x.doc_no] || cls[x.doc_no].v === '归类一致') && !dupOn(x, dups),
  qty: x => !x.confirmed && x.state === 'qtydiff' && !!x.kd_sum,      // 金蝶没量的(按个计)算量核不了，不算数量有误
  amt: x => { if (x.confirmed) return false; const k = judge(x).p.k; return k === 'bad' || k === 'low' },
  cls: (x, cls) => !!(cls[x.doc_no] && CLS_TODO.has(cls[x.doc_no].v)),
  dup: (x, cls, dups) => dupOn(x, dups),
}
export const KNAME = { nock: '核不了的单', ok: '复核无误', qty: '数量有误', amt: '金额有误', cls: '分类有误', dup: '怀疑重复收费' }
export const nDocs = list => new Set(list.map(x => x.doc_no || 'l' + x.lid)).size   // 张数按单号去重(一张调拨单账单上常有几行)

export function DocCards({ docs, cls, dups, totalBill, nodoc = [], cur, onPick, clsErr }) {
  const nodocSum = nodoc.reduce((t, x) => t + Math.max(0, x.amount || 0), 0)       // 没单号的行；扣款是负数，不算进分母
  const sum = (a, f) => a.reduce((s, x) => s + (f(x) || 0), 0)
  const pick = k => docs.filter(x => KPRED[k](x, cls, dups))
  const ok = pick('ok'), q = pick('qty'), a = pick('amt'), c = pick('cls'), d = pick('dup'), nock = pick('nock')
  const okFee = sum(docs.filter(docOk), x => x.doc_fee), base = sum(docs, x => x.doc_fee) + nodocSum
  const rate = base ? Math.min(1, okFee / base) : 0
  const N = nDocs(docs), cn = list => nDocs(list) + ' 张'
  const J = docs.filter(x => !x.confirmed).map(x => [x, judge(x)])
  const noBasis = nDocs(J.filter(([, j]) => j.v === '价待确认').map(([x]) => x)), qMiss = nDocs(J.filter(([, j]) => j.v === '量核不了').map(([x]) => x))
  const qd = sum(q, x => { const j = judge(x); return j.q.d != null && x.bill_amt ? j.q.d * x.doc_fee / x.bill_amt : 0 })
  const ad = sum(a, x => { const j = judge(x); return (j.diff || 0) - (j.q.d != null && x.bill_amt ? j.q.d * x.doc_fee / x.bill_amt : 0) })
  const sg = v => (v > 0.005 ? '+' + money(v) : v < -0.005 ? '−' + money(-v) : '0')
  const card = (k, tone, label, kv, ks) => (
    <button key={k} className={'ldd-kpi ' + tone + (cur === k ? ' on' : '')} title={'只看：' + KNAME[k]} onClick={() => onPick(cur === k ? null : k)}>
      <span className="kl">{label}</span><span className="kv">{kv}</span>{ks}</button>)
  return <div className="ldd-kpis">
    <div className="ldd-kpi total"><span className="kl">账单金额</span><span className="kv">{money(totalBill)}</span><span className="ks">{N} 张单{nodoc.length ? ` · 另 ${nodoc.length} 笔没单号` : ''}</span></div>
    {card('nock', 'rate', '可逐单复核比例', (rate * 100).toFixed(1) + '%', <>
      <span className="bar"><i style={{ width: (rate * 100).toFixed(1) + '%' }} /></span>
      <span className="ks" title="按金额算：查得到金蝶单的运费 ÷（逐单运费＋没单号的费用）">{N - nDocs(nock)} / {N} 张查得到金蝶单{nock.length ? ` · 核不了 ${nDocs(nock)} 张` : ''}{nodoc.length ? ` · 没单号 ${nodoc.length} 笔` : ''}</span></>)}
    {card('ok', 'good' + (ok.length ? '' : ' zero'), '复核无误', cn(ok), <>
      <span className="ks" title="量、价、分类都对上；价没依据、量核不了的不算">涉及 {money(sum(ok, x => x.doc_fee))}</span>
      <span className="ks">另：价没依据 {noBasis} 张 · 量核不了 {qMiss} 张</span></>)}
    {card('qty', 'bad' + (q.length ? '' : ' zero'), '数量有误', cn(q), <>
      <span className="ks">量差折钱 {sg(qd)}</span><span className="ks">账单的量和金蝶对不上</span></>)}
    {card('amt', 'bad' + (a.length ? '' : ' zero'), '金额有误', cn(a), <>
      <span className="ks">价差 {sg(ad)}</span><span className="ks">和报价 / 公式对不上</span></>)}
    {card('cls', 'bad' + (c.length ? '' : ' zero'), '分类有误', clsErr ? '—' : cn(c), clsErr
      ? <span className="ks" title={clsErr}>归类没取到</span>
      : <><span className="ks">涉及 {money(sum(c, x => x.doc_fee))}</span>
        <span className="ks">标注不对 {nDocs(c.filter(x => cls[x.doc_no].v === '标注不对'))} · 计提记错 {nDocs(c.filter(x => cls[x.doc_no].v === '计提记错'))}</span></>)}
    {card('dup', 'bad' + (d.length ? '' : ' zero'), '怀疑重复收费', cn(d), <>
      <span className="ks">涉及 {money(sum(d, x => x.doc_fee))}</span><span className="ks">同一张单收了两次运输费</span></>)}
  </div>
}

// ---------- 表格单元格 ----------
const QK_CLS = { ok: '', bad: 'ldd-bad', low: 'ldd-low', miss: 'ldd-orange', na: 'dim', tbd: 'ldd-q' }
export function Cell2({ top, sub, k, title }) {
  return <div className="ldd-c2"><span className={QK_CLS[k] || ''}>{top}</span><small title={title || sub}>{sub}</small></div>
}
export function ClsCell({ c, onOpen }) {
  if (!c) return <span className="dim">—</span>
  const short = c.v === '标注不对' ? '应记 ' + c.sys : c.v === '计提记错' ? `计提没有「${c.sys}」` : c.v === '判不了' ? (c.unk || '').replace(/，.*$/, '') : (c.txt || '按金蝶也是 ' + c.sys)
  return <div className="ldd-cls"><span><span className={'pill ' + CLS_PILL[c.v]}>{c.v}</span>
    {c.v === '标注不对' && c.diff && c.diff.length > 0 && <small className="ldd-diff">{c.diff.join('、')}</small>}</span>
    <button className="ldd-lk" title={c.txt + (c.hint ? '\n' + c.hint : '')} onClick={e => { e.stopPropagation(); onOpen() }}>{short}</button></div>
}
export function DiffCell({ j }) {
  if (j.diff == null || isZero(j.diff)) return ['价待确认', '量核不了'].includes(j.v) || (j.v === '多收' && j.diff == null) ? <span className="ldd-tbd">待定</span> : <span className="dim">0</span>
  return j.diff > 0 ? <b className="ldd-bad">+{money(j.diff)}</b> : <b className="ldd-low">−{money(-j.diff)}</b>
}

// ---------- 弹窗 ----------
const TODO_TXT = {
  多收: '和物流部、承运商核实；确认多收的，在下面登记「多收扣回」', 少收: '我方有利，留个记号就行，不用追',
  价待确认: '找物流部要这项的报价或合同；确实是这个价，就点「确认无误」', 量核不了: '核对账单上登记的金蝶单号（单号填错 / 未审核 / 拆单后缀）',
  只核了价: '让物流部在账单上补金蝶单号', 一致: '',
}
function Mk({ k }) {
  const t = { ok: '✔', bad: '✘', low: '✘', tbd: '？', miss: '？', na: '—' }[k] || '—'
  return <span className={'ldd-mk ' + k}>{t}</span>
}

export function DocDialog({ x, c, dup, pos, total, onPrev, onNext, onClose, locked, ctx }) {
  useEffect(() => {
    const h = e => {
      if (e.key === 'Escape') onClose()
      else if (!(e.target && e.target.matches && e.target.matches('input, select, textarea'))) {
        if (e.key === 'ArrowLeft' && onPrev) { e.preventDefault(); onPrev() }
        if (e.key === 'ArrowRight' && onNext) { e.preventDefault(); onNext() }
      }
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onPrev, onNext, onClose])
  if (!x) return null
  const { saveClass, saveMode, saveNote, doConfirm, carryOf, carryForm, setCarryForm, addCarry, delCarry, subjOpts, FEE_CANON, withCur, onDupOk } = ctx
  const j = judge(x)
  const dupOn = dup && !dup.ok
  const todo = x.confirmed ? '' : TODO_TXT[j.v]
  const ctodo = !c ? '' : c.v === '归类一致' ? '归类没问题。' : c.v === '判不了' ? '归类系统判不了，按物流部标注放（见「记到哪」）。' : '归类有问题，见「记到哪」。'
  const sf = Object.entries(x.sub_fees || {}).filter(([k, v]) => typeof v === 'number' && v && !['标准', '核价差'].includes(k))
  const it = carryOf(x.doc_no), f = carryForm[x.doc_no] || {}
  const ps = x.parties || []
  return <div className="ldd-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <div className="ldd-dlg" role="dialog" aria-modal="true" aria-label={'单据 ' + (x.doc_no || '')}>
      <header>
        <h2><span className="mono">{x.doc_no || '（无单号）'}</span>　·　{ps[0] || '—'}　·　{x.subject}　·　账单 {money(x.doc_fee)}</h2>
        <button className="ldd-x" aria-label="关闭" onClick={onClose}>×</button>
      </header>
      <div className="ldd-body">
        <div className={'ldd-vbar ' + (V_PILL[j.v] || 'neu')}>
          <div className="l1"><b>{j.v}</b>{j.diff != null && !isZero(j.diff) ? (j.diff > 0 ? ` +${money(j.diff)}　` : ` −${money(-j.diff)}　`) : ''}{j.why}
            {x.confirmed && <span className="pill ok" style={{ marginLeft: 8 }}>✓ {x.confirmed.by} {x.confirmed.at || ''} 已确认</span>}</div>
          {(todo || ctodo) && <div className="l2">{todo && <><b>要做：</b>{todo}。</>}{ctodo}</div>}
          {x.suggest && x.state === 'qtydiff' && <div className="l2">建议{x.suggest.mode === 'box' ? '按箱数核' : '按重量核'}：{x.suggest.txt}
            {!locked && <button className="lnk" style={{ marginLeft: 6 }} onClick={() => saveMode(x.doc_no, x.suggest.mode)}>采纳</button>}</div>}
          {dup && <div className="l3"><b className={dupOn ? 'ldd-bad' : ''}>{dupOn ? '怀疑重复收费' : '已标不是重复'}</b>
            <span>{[...(dup.others || []).map(o => `${o.carrier} ${o.period} ${o.fee_item || '运输费'} ${money(o.amount)}`), ...(dup.same && dup.same.length ? [`本家这张单收了 ${dup.same.length} 行运输费：${dup.same.map(money).join('、')}`] : [])].join('；')}
              {dup.ok ? `（${dup.ok.by} ${dup.ok.at}${dup.ok.reason ? '：' + dup.ok.reason : ''}）` : ''}</span>
            {!locked && <button className="btn sm" onClick={() => onDupOk(x.doc_no, !dup.ok)}>{dup.ok ? '撤销' : '不是重复（服务不同 / 确实分两车）'}</button>}</div>}
        </div>

        <section className="ldd-sec"><h3>钱 ＝ 量 × 价</h3>
          <div className="ldd-tw"><table className="ldd-calc">
            <thead><tr><th>账单</th><th>① 量（和金蝶比）</th><th>② 价</th><th className="num">账单金额</th><th className="num">复核金额</th><th className="num">差额</th></tr></thead>
            <tbody><tr>
              <td><span className="f">{num(x.bill_amt)} {x.bill_unit}{x.bill_amt ? <span className="op"> × </span> : ''}{x.bill_amt ? num(x.doc_fee / x.bill_amt, 4) : ''}{x.bill_amt ? <span className="op"> ＝ </span> : ''}<b>{money(x.doc_fee)}</b></span>
                {sf.length > 1 && <span className="sub">{sf.map(([k, v]) => `${k} ${money(v)}`).join(' ＋ ')}</span>}
                {x.bill_amt ? <span className="sub">单价＝账单金额 ÷ 账单量（账单没写单价，倒推的）</span> : null}
                {x.trips > 1 && <span className="sub">这张单账单上 {x.trips} 行，合计 {money(x.doc_fee_all)}</span>}</td>
              <td><span className="f"><span className={QK_CLS[j.q.k]}>{j.q.top}</span> <Mk k={j.q.k} /></span><span className="sub">{j.q.sub}</span>
                {x.mode_cn && x.mode_cn !== j.q.sub && <span className="sub">{x.mode_cn}</span>}</td>
              <td><span className="f"><span className={QK_CLS[j.p.k]}>{j.p.top}</span> <Mk k={j.p.k} /></span><span className="sub">{j.p.sub}</span></td>
              <td className="num">{money(x.doc_fee)}</td>
              <td className="num">{j.rv == null ? <span className="dim">—</span> : money(j.rv)}</td>
              <td className="num"><DiffCell j={j} /></td>
            </tr></tbody></table></div>
          <div className="ldd-facts">
            <span><i>产品线</i>{x.bizline || '—'}</span><span><i>费用类型</i>{x.fee_item || '—'}</span>
            <span><i>往来</i>{ps.join('、') || '—'}</span>
            <span><i>单位运费</i>{x.unit_fee == null ? '—' : x.unit_fee + ' 元/kg'}</span>
            <span><i>费比</i>{x.ratio == null ? '—' : (x.ratio * 100).toFixed(2) + '%'}{x.sales != null ? `（销售额 ${money(x.sales)}）` : ''}</span>
          </div></section>

        <section className="ldd-sec"><h3>金蝶出库明细 · 复核的量从这里来（只读）</h3>
          {x.n_mat > 0 ? <div className="ldd-tw"><table className="mini">
            <thead><tr><th>物料编码</th><th>物料名称</th><th>规格</th><th>往来</th><th className="num">基本单位数量</th><th className="num">金蝶数量</th>
              <th className="num">核对量<small>{x.kd_unit}</small></th><th className="num">分摊运费</th><th className="num">销售额</th></tr></thead>
            <tbody>{x.materials.map((m, i) => <tr key={i} className={m.is_pack ? 'pack' : ''}>
              <td className="mono">{m.code || '—'}</td>
              <td>{m.name}{m.is_pack && <span className="tag">包材·不计</span>}</td>
              <td className="spec">{m.spec || <span className="dim">—</span>}</td>
              <td>{m.party || <span className="dim">—</span>}</td>
              <td className="num">{num(m.base_kg)}<small className="u">{m.kg_unit}</small>{m.kg_eq != null && <span className="sub">折 {num(m.kg_eq)} 千克</span>}{m.unit_gap && <span className="sub diffbad">没填单位换算，未计入</span>}</td>
              <td className="num">{num(m.base_qty)}<small className="u">{m.base_unit}</small>{m.box_n != null && m.out_unit !== '箱' && <span className="sub">{num(m.out_qty)}{m.out_unit} ÷ {num(m.box_div)} ＝ {num(m.box_n)} 箱</span>}</td>
              <td className="num">{num(m.kd)}{m.vol_per != null && m.box_n != null && <span className="sub">{num(m.box_n)} 箱 × {m.vol_per}</span>}{m.bw_gap && <span className="sub diffbad">没填每箱计费重量</span>}</td>
              <td className="num">{money(m.fee)}</td>
              <td className="num">{m.sales == null ? '—' : money(m.sales)}</td></tr>)}
              <tr className="ldd-tot"><td colSpan="6">合计（不含包材）</td><td className="num"><b>{num(x.kd_sum)} {x.kd_unit}</b></td><td className="num">{money(x.doc_fee)}</td><td className="num">{x.sales == null ? '—' : money(x.sales)}</td></tr>
            </tbody></table></div>
            : <div className="ldd-none">{x.doc_no ? '金蝶里没找到这张单据的物料（单号填错 / 未审核 / 拆单后缀），核对账单登记的金蝶单号。' : '账单里的调整行（无单据），只登记不核量。'}</div>}
        </section>

        <section className="ldd-sec"><h3>记到哪</h3>
          {!c ? <div className="ldd-none">归类还在取（要读金蝶单据头和物料分组），或者这张单没有金蝶单号。</div>
            : <div className={'ldd-chain' + (CLS_TODO.has(c.v) ? ' bad' : '')}>
              {c.v === '判不了'
                ? <><span className="nd"><span className="h">金蝶推</span><b className="ldd-q">判不了</b> <Mk k="tbd" /></span><span className="ar">→</span>
                  <span className="nd"><span className="h">认物流部标注</span><b>{c.annot || '（没标）'}</b>{c.bill ? ' → ' + c.bill : ''}</span>
                  <span className="acct">原因：{c.unk}</span></>
                : <><span className="nd"><span className="h">金蝶推</span><b>{c.sys}</b> <Mk k="ok" /></span>
                  <span className={'ar' + (c.v === '标注不对' ? ' bad' : '')}>{c.v === '标注不对' ? '≠' : '＝'}</span>
                  <span className="nd"><span className="h">物流部标注</span><b>{c.annot || '（没标）'}</b>{c.v === '标注不对' ? <> → {c.bill} <Mk k="bad" /></> : <> <Mk k="ok" /></>}</span>
                  <span className={'ar' + (c.acc_miss ? ' bad' : '')}>→</span>
                  <span className="nd"><span className="h">本月计提</span>{c.acc_miss ? <>没有「{c.sys}」这一列 <Mk k="bad" /></> : <>有「{c.sys}」这一列 <Mk k="ok" /></>}</span>
                  <span className="acct">{c.sys_d}</span>
                  {c.v === '标注不对' && <span className="todo">要改：物流部标注改成「{c.sys}」{c.acc_miss ? `；本月计提多半跟着标注记在「${c.bill}」，到第①步登记计提更正 ${money(c.amount)}` : '；本月计提已经有这一列，计提不用动'}</span>}
                  {c.v === '计提记错' && <span className="todo">要改：本月计提没有「{c.sys}」，到第①步登记计提更正 {money(c.amount)}</span>}
                  {c.v === '归类一致' && c.txt && <span className="acct">{c.txt}</span>}</>}
              {(c.how || []).length > 0 && <details className="ldd-how"><summary>怎么推的</summary><ul>{c.how.map((h, i) => <li key={i}>{h}</li>)}</ul>{c.subj_note && <div>{c.subj_note}</div>}</details>}
            </div>}
        </section>

        {x.doc_no && <section className="ldd-sec ldd-ops"><h3>处理</h3>
          <div className="xcls">
            <span className="dim">归类</span>
            <label>主体 <select className="clsinp" disabled={locked} value={x.subject || ''} onChange={e => saveClass(x.doc_no, { subject: e.target.value })}>
              {withCur(subjOpts, x.subject).map(s => <option key={s} value={s}>{s}{s === x.subj_bill ? '（账单原值）' : ''}</option>)}</select></label>
            <label>费用类型 <select className="clsinp" disabled={locked} value={x.fee_item || ''} onChange={e => saveClass(x.doc_no, { fee_item: e.target.value })}>
              {withCur(FEE_CANON, x.fee_item).map(s => <option key={s} value={s}>{s}{s === x.fee_sys ? '（系统判）' : ''}</option>)}</select></label>
            <label>原因 <input className="clsinp wide" disabled={locked} defaultValue={x.ovr_reason || ''} key={'r' + x.doc_no + (x.ovr_reason || '')}
              placeholder="特批/调整的原因" onBlur={e => { const v = e.target.value.trim(); if (v !== (x.ovr_reason || '')) saveClass(x.doc_no, { reason: v }) }} /></label>
            {(x.subj_ovr || x.fee_ovr || x.ovr_reason) && !locked && <button className="lk" onClick={() => saveClass(x.doc_no, { restore: true })}>恢复账单原值</button>}
          </div>
          {x.can_mode && <div className="xcls"><span className="dim">核对方式</span>
            <label><select className="clsinp" disabled={locked} value={x.force_mode || ''} onChange={e => saveMode(x.doc_no, e.target.value)}>
              <option value="">系统自动</option>
              <option value="weight" disabled={!x.can_weight}>按重量核：账单重量 对 金蝶重量{x.can_weight ? '' : '（账单这行没写重量）'}</option>
              <option value="box" disabled={!x.can_box}>按箱数核：账单件数 对 金蝶箱数{x.can_box ? '' : '（账单这行没写件数）'}</option>
            </select></label></div>}
          <div className="xcls"><span className="dim">多收扣回</span>
            {it ? <><span>已登记：供应商多收 <b>{money(it.amount)}</b> 元（含税），等后面月份账单扣回　<span className="dim">{it.reason} · {it.by} {String(it.at || '').slice(0, 10)}</span></span>
              {it.done ? <span className="diffok">　✓ {it.done.period} 账单已扣回</span> : (!locked && <button className="lk" onClick={() => delCarry(it)}>撤销</button>)}</>
              : <><label>多收 <input className="clsinp" style={{ width: 90, textAlign: 'right' }} disabled={locked} placeholder={j.diff > 0 ? String(j.diff) : '含税金额'} value={f.amount || ''}
                  onChange={e => setCarryForm(o => ({ ...o, [x.doc_no]: { ...f, amount: e.target.value } }))} /> 元</label>
                <label>原因 <input className="clsinp wide" disabled={locked} placeholder="多收了什么、和谁确认的" value={f.reason || ''}
                  onChange={e => setCarryForm(o => ({ ...o, [x.doc_no]: { ...f, reason: e.target.value } }))} /></label>
                <button className="btn sm" disabled={locked || !String(f.amount || '').trim() || !String(f.reason || '').trim()} onClick={() => addCarry(x.doc_no)}>登记待扣回</button></>}
          </div>
          <div className="xcls"><span className="dim">备注</span>
            <input className="clsinp wide" disabled={locked} defaultValue={x.note || ''} key={'n' + x.doc_no + (x.note || '')} placeholder="备注…"
              onBlur={e => { const v = e.target.value.trim(); if (v !== (x.note || '')) saveNote(x.doc_no, v) }} /></div>
        </section>}
      </div>
      <footer className="ldd-foot">
        <button className="btn sm" disabled={!onPrev} onClick={onPrev}>‹ 上一张</button>
        <small>{pos} / {total}（按当前筛选）· 键盘 ← → 也能翻</small>
        <button className="btn sm" disabled={!onNext} onClick={onNext}>下一张 ›</button>
        <span className="sp" />
        {x.doc_no && <button className={'btn sm' + (x.confirmed ? '' : ' pri')} disabled={locked} onClick={() => doConfirm([x.doc_no], !x.confirmed)}>{x.confirmed ? '取消确认' : '✓ 确认无误'}</button>}
        <button className="btn sm" onClick={onClose}>关闭</button>
      </footer>
    </div>
  </div>
}

// ---------- 规则与对照三个入口 ----------
function Dlg({ title, onClose, children, wide }) {
  useEffect(() => { const h = e => { if (e.key === 'Escape') onClose() }; document.addEventListener('keydown', h); return () => document.removeEventListener('keydown', h) }, [onClose])
  return <div className="ldd-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <div className={'ldd-dlg' + (wide ? '' : ' narrow')} role="dialog" aria-modal="true" aria-label={title}>
      <header><h2>{title}</h2><button className="ldd-x" aria-label="关闭" onClick={onClose}>×</button></header>
      <div className="ldd-body">{children}</div>
    </div></div>
}

export function AccrTabDialog({ tab, carrier, period, onClose }) {
  const T = tab || []
  const sum = k => T.reduce((a, x) => a + (x[k] || 0), 0)
  const sg = v => (Math.abs(v) < 0.005 ? <span className="dim">0</span> : v > 0 ? <span className="ldd-bad">+{money(v)}</span> : <span className="ldd-low">−{money(-v)}</span>)
  return <Dlg title={`和本月计提对照 · ${carrier} ${period}`} onClose={onClose} wide>
    <p className="ldd-p">同一家、同一个月，按计提的列（凭证行）加总。「账单 · 按系统判」那一列和计提差得多、又不是金额估算差的（标黄），就是计提归错了列——到第①步登记计提更正。</p>
    <div className="ldd-tw"><table className="ldd-ctab">
      <thead><tr><th>计提列（凭证行）</th><th className="num">本月计提</th><th className="num">账单 · 按物流部标注</th><th className="num">账单 · 按系统判</th><th className="num">系统 − 计提</th></tr></thead>
      <tbody>{T.map((x, i) => <tr key={i} className={x.hot ? 'hot' : ''}><td>{x.name}<div className="d">{x.desc}</div></td>
        <td className="num">{x.accr ? money(x.accr) : <span className="dim">—</span>}</td>
        <td className="num">{x.by_bill ? <>{money(x.by_bill)} <span className="d">{x.n_bill} 张</span></> : <span className="dim">—</span>}</td>
        <td className="num">{x.by_sys ? <>{money(x.by_sys)} <span className="d">{x.n_sys} 张</span></> : <span className="dim">—</span>}</td>
        <td className="num">{sg(x.d_sys)}</td></tr>)}</tbody>
      <tfoot><tr><td>合计</td><td className="num">{money(sum('accr'))}</td><td className="num">{money(sum('by_bill'))}</td><td className="num">{money(sum('by_sys'))}</td><td className="num">{sg(sum('by_sys') - sum('accr'))}</td></tr></tfoot>
    </table></div>
    <p className="ldd-p dim">「判不了」的单按物流部标注放。计提和账单合计的差是估算差，归第①步管。张数按账单行数。</p>
  </Dlg>
}

export function DupRuleDialog({ onClose }) {
  return <Dlg title="怀疑重复收费 · 怎么判" onClose={onClose}>
    <ul className="ldd-ul">
      <li>同一张金蝶单，本月或上个月<b>别的承运商也收了运输费</b>；</li>
      <li>或者<b>同一家同一张单有两行金额一模一样的运输费</b>（包天包趟一车一行、车型不同金额不同的不算）。</li>
      <li>装卸、仓储、冷藏、处置、保费、提货、送货这些不算运输费，服务范围不同不算重复（天鹰只装卸）。</li>
      <li>真不是重复的（确实分两车、服务不同），点开那张单点「不是重复」，写一句原因，留痕。</li>
    </ul>
  </Dlg>
}

const SIDE = { in: '调入仓', out: '调出仓', any: '两头' }
export function RulesDialog({ load, save, onClose, onSaved, locked }) {
  const [R, setR] = useState(null), [err, setErr] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => { load().then(r => setR(r)).catch(e => setErr(e.message)) }, [])
  if (!R) return <Dlg title="归类规则" onClose={onClose}>{err ? <div className="ldd-bad">{err}</div> : <div className="dim">读取中…</div>}</Dlg>
  const c = R.cfg, IN_COLS = ['小料入库', '孝感工厂入库', '代工厂入库', '山姆鲜食入库', '零售入库']
  const set = patch => setR(o => ({ ...o, cfg: { ...o.cfg, ...patch } }))
  const gb = Object.entries(c.group_biz), ow = Object.entries(c.outside_wh || {})
  const doSave = cfg => { setBusy(true); setErr(''); save(cfg).then(r => { setR(o => ({ ...o, cfg: r.cfg, customized: !!cfg })); onSaved() }).catch(e => setErr(e.message)).finally(() => setBusy(false)) }
  return <Dlg title="归类规则" onClose={onClose} wide>
    <p className="ldd-p">每张单按金蝶单据推「应记哪一行」，再和物流部标注、本月计提比。比的是凭证上落哪一行：主体＋科目＋部门＋费用项目＋产品分类。改了这里，各家各月的归类马上按新规则重算。{R.customized ? '（现在用的是改过的规则）' : '（现在用的是默认规则）'}</p>
    <div className="ldd-rule"><h4>主体、费用类型（固定口径）</h4>
      <ul className="ldd-ul">
        <li>销售出库单 → 出库运费，主体看<b>销售组织</b>；销售退货单按出库运费。</li>
        <li>其他出库单 → 领料部门是研发中心算<b>研发外购</b>，其余出库运费；主体<b>金蝶判不了</b>（都开在孝感库存组织下），认物流部填的。</li>
        <li>采购入库 / 退料 → 入库运费，主体看需求组织；分步式调拨 → 入库运费，主体看调入组织（下面的仓库规则可以改）。</li>
        <li>孝感入库记哪一列：小料→小料入库(5101 茶饮小料部)；植物肉→孝感工厂入库(5101 仓储物流部)；鲜食→山姆鲜食入库(6401)；零售→零售入库(6401)。</li>
      </ul></div>
    <div className="ldd-rule"><h4>物料分组 → 业务线</h4>
      <table className="ldd-ctab"><thead><tr><th>金蝶物料分组</th><th>业务线</th><th /></tr></thead><tbody>
        {gb.map(([g, b], i) => <tr key={i}>
          <td><input className="clsinp" value={g} disabled={locked} onChange={e => { const n = [...gb]; n[i] = [e.target.value, b]; set({ group_biz: Object.fromEntries(n) }) }} /></td>
          <td><input className="clsinp" value={b} disabled={locked} onChange={e => { const n = [...gb]; n[i] = [g, e.target.value]; set({ group_biz: Object.fromEntries(n) }) }} /></td>
          <td>{!locked && <button className="lk" onClick={() => set({ group_biz: Object.fromEntries(gb.filter((_, k) => k !== i)) })}>删</button>}</td></tr>)}
      </tbody></table>
      {!locked && <button className="btn sm" onClick={() => set({ group_biz: { ...c.group_biz, '': '' } })}>＋ 加一行</button>}
      <div className="ldd-row"><label>不算业务线的分组（跟着货走）<input className="clsinp wide" disabled={locked} value={(c.skip_groups || []).join('、')} onChange={e => set({ skip_groups: e.target.value.split(/[、,，\s]+/).filter(Boolean) })} /></label></div>
      <div className="ldd-row"><label>看不出业务线的分组<input className="clsinp wide" disabled={locked} value={(c.unknown_groups || []).join('、')} onChange={e => set({ unknown_groups: e.target.value.split(/[、,，\s]+/).filter(Boolean) })} /></label></div>
    </div>
    <div className="ldd-rule"><h4>工厂外仓承运商（仓储、装卸都算入库运费）</h4>
      <table className="ldd-ctab"><thead><tr><th>承运商简称</th><th>原料默认算哪条业务线</th><th>说明</th><th /></tr></thead><tbody>
        {ow.map(([k, v], i) => <tr key={i}>
          <td><input className="clsinp" value={k} disabled={locked} onChange={e => { const n = [...ow]; n[i] = [e.target.value, v]; set({ outside_wh: Object.fromEntries(n) }) }} /></td>
          <td><input className="clsinp" value={v.raw_biz || ''} disabled={locked} placeholder="空＝照样判不了" onChange={e => { const n = [...ow]; n[i] = [k, { ...v, raw_biz: e.target.value }]; set({ outside_wh: Object.fromEntries(n) }) }} /></td>
          <td><input className="clsinp wide" value={v.note || ''} disabled={locked} onChange={e => { const n = [...ow]; n[i] = [k, { ...v, note: e.target.value }]; set({ outside_wh: Object.fromEntries(n) }) }} /></td>
          <td>{!locked && <button className="lk" onClick={() => set({ outside_wh: Object.fromEntries(ow.filter((_, z) => z !== i)) })}>删</button>}</td></tr>)}
      </tbody></table>
      {!locked && <button className="btn sm" onClick={() => set({ outside_wh: { ...(c.outside_wh || {}), '': { raw_biz: '', note: '' } } })}>＋ 加一家</button>}
    </div>
    <div className="ldd-rule"><h4>调拨单按仓库认（从上往下，第一条命中的算）</h4>
      <table className="ldd-ctab"><thead><tr><th>仓库名关键字（| 分隔）</th><th>看哪头</th><th>费用类型</th><th>主体</th><th>记哪一列</th><th>说明</th><th /></tr></thead><tbody>
        {(c.stock_rules || []).map((r, i) => { const up = p => { const n = [...c.stock_rules]; n[i] = { ...r, ...p }; set({ stock_rules: n }) }
          return <tr key={i}>
            <td><input className="clsinp" value={r.match || ''} disabled={locked} onChange={e => up({ match: e.target.value })} /></td>
            <td><select className="clsinp" value={r.side || 'any'} disabled={locked} onChange={e => up({ side: e.target.value })}>{Object.entries(SIDE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></td>
            <td><select className="clsinp" value={r.fee || '入库运费'} disabled={locked} onChange={e => up({ fee: e.target.value })}><option>入库运费</option><option>出库运费</option></select></td>
            <td><select className="clsinp" value={r.subj || ''} disabled={locked} onChange={e => up({ subj: e.target.value })}><option value="">按调入组织</option><option>深圳星期零</option><option>深圳星期九</option><option>孝感星期九</option></select></td>
            <td><select className="clsinp" value={r.col || ''} disabled={locked} onChange={e => up({ col: e.target.value })}><option value="">按业务线</option>{IN_COLS.map(x => <option key={x}>{x}</option>)}</select></td>
            <td><input className="clsinp wide" value={r.note || ''} disabled={locked} onChange={e => up({ note: e.target.value })} /></td>
            <td>{!locked && <button className="lk" onClick={() => set({ stock_rules: c.stock_rules.filter((_, z) => z !== i) })}>删</button>}</td></tr> })}
      </tbody></table>
      {!locked && <button className="btn sm" onClick={() => set({ stock_rules: [...(c.stock_rules || []), { match: '', side: 'in', fee: '入库运费', subj: '', col: '', note: '' }] })}>＋ 加一条</button>}
    </div>
    {err && <div className="ldd-bad">{err}</div>}
    <div className="ldd-foot2">
      {!locked && <button className="btn sm" disabled={busy} onClick={() => { if (window.confirm('恢复成默认规则？改过的都会丢掉。')) doSave(null) }}>恢复默认</button>}
      <span className="sp" />
      <button className="btn sm" onClick={onClose}>关闭</button>
      {!locked && <button className="btn sm pri" disabled={busy} onClick={() => doSave({ ...c, group_biz: Object.fromEntries(Object.entries(c.group_biz).filter(([g]) => g.trim())), outside_wh: Object.fromEntries(Object.entries(c.outside_wh || {}).filter(([k]) => k.trim())) })}>{busy ? '保存中…' : '保存并重算'}</button>}
    </div>
  </Dlg>
}

export const LDD_CSS = `
.lrv .ldd-kpis{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;padding:10px 15px 6px}
@media (max-width:1100px){.lrv .ldd-kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}}
.lrv .ldd-kpi{border:1px solid #DCE2E7;border-radius:8px;padding:8px 11px;background:#fff;display:flex;flex-direction:column;gap:2px;text-align:left;cursor:pointer;font:inherit;color:#1B2733;min-width:0}
.lrv .ldd-kpi:hover{border-color:var(--accent)}.lrv .ldd-kpi.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset;background:#F5FAFC}
.lrv .ldd-kpi.total{cursor:default;background:#F5F7F8}.lrv .ldd-kpi.total:hover{border-color:#DCE2E7}
.lrv .ldd-kpi .kl{font-size:11.5px;color:#5E6B78}.lrv .ldd-kpi .kv{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.lrv .ldd-kpi .ks{font-size:11px;color:#8A96A2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrv .ldd-kpi.bad .kv{color:var(--bad)}.lrv .ldd-kpi.good .kv{color:var(--ok)}.lrv .ldd-kpi.zero .kv{color:#8A96A2}
.lrv .ldd-kpi .bar{height:4px;border-radius:2px;background:#E7ECEF;overflow:hidden;margin:2px 0}.lrv .ldd-kpi .bar i{display:block;height:100%;background:var(--accent)}
.lrv .ldd-rules{display:flex;gap:6px;flex-wrap:wrap;padding:0 15px 10px;border-bottom:1px solid #DCE2E7}
.lrv .ldd-rule-btn{border:1px solid #DCE2E7;background:#fff;border-radius:999px;padding:3px 11px;font:inherit;font-size:12px;color:#5E6B78;cursor:pointer;display:inline-flex;gap:5px;align-items:center}
.lrv .ldd-rule-btn b{color:#1B2733;font-weight:600}.lrv .ldd-rule-btn:hover{border-color:var(--accent);color:var(--accent)}
.lrv .pill.blue{background:#E1ECF7;color:#2C6BCF}.lrv .pill.orange{background:#FBE9DD;color:#B4501D}
.lrv .ldd-c2{display:flex;flex-direction:column;gap:1px;min-width:120px;max-width:200px}
.lrv .ldd-c2>span{font-size:12.5px;font-weight:500;white-space:nowrap}.lrv .ldd-c2>small{font-size:11px;color:#8A96A2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrv .ldd-cls{display:flex;flex-direction:column;gap:2px;align-items:flex-start;min-width:140px;max-width:210px}
.lrv .ldd-cls .pill{white-space:nowrap}.lrv .ldd-diff{font-size:10.5px;color:var(--bad);margin-left:4px}
.lrv .ldd-lk{border:0;background:none;padding:0;font:inherit;font-size:11px;color:#8A96A2;cursor:pointer;text-align:left;max-width:210px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrv .ldd-lk:hover{color:var(--accent);text-decoration:underline}
.lrv .ldd-why{display:block;font-size:11px;color:#8A96A2;margin-top:2px;max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrv .ldd-tbd{color:var(--warn);font-size:12px}
.lrv .ldd-mask{position:fixed;inset:0;background:rgba(10,20,30,.42);z-index:60;display:flex;align-items:flex-start;justify-content:center;padding:5vh 16px 16px;overflow-y:auto}
.lrv .ldd-dlg{background:#fff;color:#1B2733;border-radius:12px;box-shadow:0 10px 40px rgba(20,30,40,.25);width:min(1180px,100%);display:grid;grid-template-rows:auto 1fr auto;max-height:90vh}
.lrv .ldd-dlg.narrow{width:min(640px,100%);grid-template-rows:auto 1fr}
.lrv .ldd-dlg header{display:flex;align-items:center;gap:10px;padding:13px 18px;border-bottom:1px solid #DCE2E7}
.lrv .ldd-dlg header h2{margin:0;font-size:14px;flex:1;min-width:0;overflow-wrap:anywhere}
.lrv .ldd-x{border:0;background:none;font-size:20px;color:#8A96A2;cursor:pointer;width:28px;height:28px;border-radius:6px}.lrv .ldd-x:hover{background:#EEF1F3;color:#1B2733}
.lrv .ldd-body{overflow:auto;padding:14px 18px;display:grid;gap:14px;align-content:start}
.lrv .ldd-p{margin:0;font-size:12.5px;color:#5E6B78}
.lrv .ldd-vbar{border-left:4px solid var(--neu);background:#F5F7F8;border-radius:0 8px 8px 0;padding:10px 14px;display:grid;gap:4px}
.lrv .ldd-vbar.bad{border-color:var(--bad);background:#FBEDEA}.lrv .ldd-vbar.warn{border-color:var(--warn);background:#FCF6EC}.lrv .ldd-vbar.ok{border-color:var(--ok);background:#EEF7F1}
.lrv .ldd-vbar.blue{border-color:#2C6BCF;background:#EAF1FA}.lrv .ldd-vbar.orange{border-color:#B4501D;background:#FBEEE4}
.lrv .ldd-vbar .l1{font-size:13.5px}.lrv .ldd-vbar .l1 b{margin-right:8px}
.lrv .ldd-vbar.bad .l1 b{color:var(--bad)}.lrv .ldd-vbar.warn .l1 b{color:var(--warn)}.lrv .ldd-vbar.ok .l1 b{color:var(--ok)}.lrv .ldd-vbar.blue .l1 b{color:#2C6BCF}.lrv .ldd-vbar.orange .l1 b{color:#B4501D}
.lrv .ldd-vbar .l2{font-size:12.5px;color:#5E6B78}.lrv .ldd-vbar .l2 b{color:#1B2733}
.lrv .ldd-vbar .l3{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:12px;color:#5E6B78}
.lrv .ldd-sec{display:grid;gap:6px}.lrv .ldd-sec>h3{margin:0;font-size:12.5px;color:#8A96A2;font-weight:600}
.lrv .ldd-tw{overflow-x:auto}
.lrv .ldd-calc{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
.lrv .ldd-calc th{text-align:left;font-size:11.5px;color:#8A96A2;font-weight:600;padding:6px 8px;border-bottom:1px solid #DCE2E7;white-space:nowrap}
.lrv .ldd-calc td{padding:8px;border-bottom:1px solid #EEF1F3;vertical-align:top}.lrv .ldd-calc .num{text-align:right;white-space:nowrap}
.lrv .ldd-calc .f{font-weight:500;white-space:nowrap}.lrv .ldd-calc .op{color:#8A96A2}
.lrv .ldd-facts{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:12px;color:#1B2733}.lrv .ldd-facts i{font-style:normal;color:#8A96A2;margin-right:6px}
.lrv .ldd-mk{font-weight:700;margin-left:3px}.lrv .ldd-mk.ok{color:var(--ok)}.lrv .ldd-mk.bad{color:var(--bad)}.lrv .ldd-mk.low{color:#2C6BCF}.lrv .ldd-mk.tbd,.lrv .ldd-mk.miss{color:var(--warn)}.lrv .ldd-mk.na{color:#8A96A2;font-weight:400}
.lrv .ldd-bad{color:var(--bad);font-weight:600}.lrv .ldd-low{color:#2C6BCF;font-weight:600}.lrv .ldd-q{color:var(--warn);font-weight:600}.lrv .ldd-orange{color:#B4501D;font-weight:600}
.lrv .ldd-tot td{font-weight:600;border-top:1px solid #DCE2E7}
.lrv .ldd-none{font-size:12.5px;color:#8A96A2;padding:9px 12px;border:1px dashed #DCE2E7;border-radius:8px}
.lrv .ldd-chain{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 8px;font-size:12.5px;padding:10px 12px;border:1px solid #DCE2E7;border-radius:8px}
.lrv .ldd-chain.bad{border-color:#E6B7B0}
.lrv .ldd-chain .nd .h{color:#8A96A2;font-size:11.5px;margin-right:4px}.lrv .ldd-chain .ar{color:#8A96A2}.lrv .ldd-chain .ar.bad{color:var(--bad);font-weight:700}
.lrv .ldd-chain .acct{flex-basis:100%;font-size:11.5px;color:#8A96A2}.lrv .ldd-chain .todo{flex-basis:100%;font-size:12px;color:var(--bad)}
.lrv .ldd-how{flex-basis:100%;font-size:11.5px;color:#5E6B78}.lrv .ldd-how summary{cursor:pointer;color:var(--accent)}.lrv .ldd-how ul{margin:4px 0;padding-left:1.2em}
.lrv .ldd-ops .xcls{padding:6px 0}
.lrv .ldd-foot,.lrv .ldd-foot2{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 18px;border-top:1px solid #DCE2E7}
.lrv .ldd-foot2{padding:10px 0 0}
.lrv .ldd-foot .sp,.lrv .ldd-foot2 .sp{flex:1}.lrv .ldd-foot small{color:#8A96A2}
.lrv .ldd-ctab{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
.lrv .ldd-ctab th{text-align:left;font-weight:600;color:#8A96A2;padding:6px 8px;border-bottom:1px solid #DCE2E7;white-space:nowrap;font-size:11.5px}
.lrv .ldd-ctab td{padding:6px 8px;border-bottom:1px solid #EEF1F3;vertical-align:top}.lrv .ldd-ctab .num{text-align:right;white-space:nowrap}
.lrv .ldd-ctab tr.hot td{background:#FCF6EC}.lrv .ldd-ctab .d{font-size:11px;color:#8A96A2}.lrv .ldd-ctab tfoot td{font-weight:600;border-top:1px solid #DCE2E7}
.lrv .ldd-rule{display:grid;gap:6px}.lrv .ldd-rule h4{margin:0;font-size:13px}
.lrv .ldd-ul{margin:0;padding-left:1.2em;display:grid;gap:4px;font-size:12.5px;color:#1B2733}
.lrv .ldd-row label{display:flex;gap:8px;align-items:center;font-size:12.5px;color:#5E6B78}
.lrv tr.drow.ldd-cur td{background:#F5FAFC}
.lrv .dtbl.ldd-tbl td{vertical-align:top}
`
