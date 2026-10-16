// [Change Log] Date:2026-10-16 Author:Claude Opus 5.5 Version:V2.986
// 账单复核台第②步·逐单核对改版（照 2026-10-16 样机定版）：
//   · DocCards：一行 7 张数字卡（账单金额 / 可逐单复核比例 / 复核无误 / 数量有误 / 金额有误 / 分类有误 / 怀疑重复收费），点卡片筛单
//   · ClsCell：表格「③ 归类」一格
//   · DocDialog：点一张单弹窗，按「结论 → 钱＝量×价 → 金蝶出库明细 → 记到哪」排，原来展开行里的操作（改归类/核对方式/多收扣回/备注/确认）都在底部
// 「价」现在只有包天包趟报价、恒茂这类按公式核的有依据；其余写「价没依据」，等「价的依据库」那一步补
import React, { useEffect } from 'react'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const num = (v, dp = 2) => (v == null || v === '' ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: dp }))
const isZero = d => d != null && Math.abs(d) < 0.01

export const CLS_PILL = { 归类一致: 'ok', 标注不对: 'bad', 计提记错: 'warn', 判不了: 'neu' }
const CLS_TODO = new Set(['标注不对', '计提记错'])

// ---------- 七张卡的口径（每张单 x 是 /result 的 docs 一条；cls/dup 按单号查） ----------
const docOk = x => !!x.doc_no && (x.n_mat || 0) > 0
const dupOn = (x, dups) => { const u = dups && dups[x.doc_no]; return !!(u && !u.ok) }
export const KPRED = {
  nock: x => !docOk(x),
  ok: (x, cls, dups) => (x.confirmed || x.state === 'ok') && (!cls[x.doc_no] || cls[x.doc_no].v === '归类一致') && !dupOn(x, dups),
  qty: x => !x.confirmed && x.state === 'qtydiff',
  amt: x => !x.confirmed && x.state === 'price',
  cls: (x, cls) => !!(cls[x.doc_no] && CLS_TODO.has(cls[x.doc_no].v)),
  dup: (x, cls, dups) => dupOn(x, dups),
}
export const KNAME = { nock: '核不了的单', ok: '复核无误', qty: '数量有误', amt: '金额有误', cls: '分类有误', dup: '怀疑重复收费' }

// 张数按单号去重(一张调拨单账单上常有几行，一车一行)，金额按行加
export const nDocs = list => new Set(list.map(x => x.doc_no || 'l' + x.lid)).size

export function DocCards({ docs, cls, dups, totalBill, nodoc = [], cur, onPick, clsErr }) {
  const nodocSum = nodoc.reduce((t, x) => t + Math.max(0, x.amount || 0), 0)       // 没单号的行(仓储费、搬运费…)；扣款是负数，不算进分母
  const sum = (a, f) => a.reduce((s, x) => s + (f(x) || 0), 0)
  const pick = k => docs.filter(x => KPRED[k](x, cls, dups))
  const ok = pick('ok'), q = pick('qty'), a = pick('amt'), c = pick('cls'), d = pick('dup'), nock = pick('nock')
  const okFee = sum(docs.filter(docOk), x => x.doc_fee), base = sum(docs, x => x.doc_fee) + (nodocSum || 0)   // 分母＝逐单运费＋没单号的行
  const rate = base ? Math.min(1, okFee / base) : 0
  const N = nDocs(docs), cn = list => nDocs(list) + ' 张'
  const card = (k, tone, label, kv, ks) => (
    <button key={k} className={'ldd-kpi ' + tone + (cur === k ? ' on' : '')} title={'只看：' + KNAME[k]} onClick={() => onPick(cur === k ? null : k)}>
      <span className="kl">{label}</span><span className="kv">{kv}</span>{ks}</button>)
  const miss = docs.filter(x => x.state === 'miss' && !x.confirmed), free = docs.filter(x => x.state === 'info' && !x.confirmed)
  return <div className="ldd-kpis">
    <div className="ldd-kpi total"><span className="kl">账单金额</span><span className="kv">{money(totalBill)}</span><span className="ks">{N} 张单{nodoc.length ? ` · 另 ${nodoc.length} 笔没单号` : ''}</span></div>
    {card('nock', 'rate', '可逐单复核比例', (rate * 100).toFixed(1) + '%', <>
      <span className="bar"><i style={{ width: Math.min(100, rate * 100).toFixed(1) + '%' }} /></span>
      <span className="ks" title="按金额算：查得到金蝶单的运费 ÷（逐单运费＋没单号的费用）">{N - nDocs(nock)} / {N} 张查得到金蝶单{nock.length ? ` · 核不了 ${nDocs(nock)} 张` : ''}{nodoc.length ? ` · 没单号的 ${nodoc.length} 笔算核不了` : ''}</span></>)}
    {card('ok', 'good' + (ok.length ? '' : ' zero'), '复核无误', cn(ok), <>
      <span className="ks">涉及 {money(sum(ok, x => x.doc_fee))}</span>
      <span className="ks">另：量核不了 {nDocs(miss)} 张 · 免核 {nDocs(free)} 张</span></>)}
    {card('qty', 'bad' + (q.length ? '' : ' zero'), '数量有误', cn(q), <>
      <span className="ks">涉及 {money(sum(q, x => x.doc_fee))}</span><span className="ks">账单的量和金蝶对不上</span></>)}
    {card('amt', 'bad' + (a.length ? '' : ' zero'), '金额有误', cn(a), <>
      <span className="ks">涉及 {money(sum(a, x => x.doc_fee))}</span><span className="ks">报价 / 公式核价对不上</span></>)}
    {card('cls', 'bad' + (c.length ? '' : ' zero'), '分类有误', clsErr ? '—' : cn(c), clsErr
      ? <span className="ks" title={clsErr}>归类没取到</span>
      : <><span className="ks">涉及 {money(sum(c, x => x.doc_fee))}</span>
        <span className="ks">标注不对 {nDocs(c.filter(x => cls[x.doc_no].v === '标注不对'))} · 计提记错 {nDocs(c.filter(x => cls[x.doc_no].v === '计提记错'))}</span></>)}
    {card('dup', 'bad' + (d.length ? '' : ' zero'), '怀疑重复收费', cn(d), <>
      <span className="ks">涉及 {money(sum(d, x => x.doc_fee))}</span><span className="ks">同一张单收了两次运输费</span></>)}
  </div>
}

export function ClsCell({ c, onOpen }) {
  if (!c) return <span className="dim">—</span>
  const short = c.v === '标注不对' ? '应记 ' + c.sys : c.v === '计提记错' ? `计提没有「${c.sys}」` : c.v === '判不了' ? (c.unk || '').replace(/，.*$/, '') : (c.txt || '按金蝶也是 ' + c.sys)
  return <div className="ldd-cls"><span className={'pill ' + CLS_PILL[c.v]}>{c.v}</span>
    {c.v === '标注不对' && c.diff && c.diff.length > 0 && <small className="ldd-diff">{c.diff.join('、')}</small>}
    <button className="ldd-lk" title={c.txt + (c.hint ? '\n' + c.hint : '')} onClick={e => { e.stopPropagation(); onOpen() }}>{short}</button></div>
}

// ---------- 弹窗 ----------
const VCOL = { miss: ['量核不了', 'warn'], qtydiff: ['数量有误', 'bad'], price: ['金额有误', 'bad'], info: ['免核', 'neu'], ok: ['量价对上', 'ok'] }
const TODO_TXT = { miss: '核对账单上登记的金蝶单号（单号填错 / 未审核 / 拆单后缀）', qtydiff: '核对账单的量；确认是供应商多收的，在下面登记「多收扣回」', price: '核对报价或公式；多收的登记「多收扣回」', info: '', ok: '' }

function Mk({ k }) { return <span className={'ldd-mk ' + k}>{k === 'ok' ? '✔' : k === 'bad' ? '✘' : k === 'q' ? '？' : '—'}</span> }

function qPart(x) {
  if (!x.doc_no) return [<>按账单 <Mk k="na" /></>, '没有金蝶单号，只登记不核量']
  if (x.state === 'miss') return [<><span className="ldd-q">金蝶查无</span> <Mk k="q" /></>, '金蝶里没找到这张单的出库单']
  if (x.trip) return [<>{num(x.bill_amt)} {x.bill_unit} <Mk k="ok" /></>, `按${x.bill_unit || '趟'}收钱，不比金蝶量；这张单金蝶 ${num(x.kd_sum)} ${x.kd_unit || ''}`]
  if (x.state === 'info') return [<>{num(x.bill_amt)} {x.bill_unit} <Mk k="na" /></>, x.mode_short || x.mode_cn || '免核']
  const ok = x.state !== 'qtydiff'
  return [<><span className={ok ? '' : 'ldd-bad'}>{num(x.kd_sum)} {x.kd_unit}</span> <Mk k={ok ? 'ok' : 'bad'} /></>,
    (x.conv != null && !x.trip ? `换算系数 ${x.conv}；` : '') + (x.mode_short || x.mode_cn || '')]
}
function pPart(x) {
  const sf = x.sub_fees || {}
  if (x.trip) return x.trip.verdict === 'ok' ? [<>按报价 <Mk k="ok" /></>, x.trip.msg] : [<><span className="ldd-bad">和报价对不上</span> <Mk k="bad" /></>, x.trip.msg]
  if ('标准' in sf) return '核价差' in sf ? [<><span className="ldd-bad">{money(sf['标准'])}</span> <Mk k="bad" /></>, `${sf['公式'] || '按公式'} ＝ ${money(sf['标准'])}`]
    : [<>按公式 <Mk k="ok" /></>, `${sf['公式'] || '按公式'} ＝ ${money(sf['标准'])}`]
  return [<><span className="ldd-q">价没依据</span> <Mk k="q" /></>, '没有合同价可比（价的依据库还没做）']
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
  const [vt, vc] = x.confirmed ? ['已确认', 'ok'] : (VCOL[x.state] || ['—', 'neu'])
  const [qv, qs] = qPart(x), [pv, ps] = pPart(x)
  const dupOn = dup && !dup.ok
  const todo = x.confirmed ? '' : (TODO_TXT[x.state] || '')
  const ctodo = !c ? '' : c.v === '归类一致' ? '归类没问题。' : c.v === '判不了' ? '归类系统判不了，按物流部标注放（见「记到哪」）。' : '归类有问题，见「记到哪」。'
  const sf = Object.entries(x.sub_fees || {}).filter(([k, v]) => typeof v === 'number' && v && !['标准', '核价差'].includes(k))
  const it = carryOf(x.doc_no), f = carryForm[x.doc_no] || {}
  return <div className="ldd-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <div className="ldd-dlg" role="dialog" aria-modal="true" aria-label={'单据 ' + (x.doc_no || '')}>
      <header>
        <h2><span className="mono">{x.doc_no || '（无单号）'}</span>　·　{x.subject}　·　{(x.parties || [])[0] || '—'}　·　运费 {money(x.doc_fee)}</h2>
        <button className="ldd-x" aria-label="关闭" onClick={onClose}>×</button>
      </header>
      <div className="ldd-body">
        <div className={'ldd-vbar ' + vc}>
          <div className="l1"><b>{vt}</b>{x.confirmed ? `${x.confirmed.by} ${x.confirmed.at || ''} 确认` : (x.mode_cn || '')}</div>
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
            <thead><tr><th>账单</th><th>复核：量（和金蝶比）</th><th>复核：价</th><th className="num">运费</th></tr></thead>
            <tbody><tr>
              <td><span className="f">{num(x.bill_amt)} {x.bill_unit}{x.trips > 1 ? `（本单账单 ${x.trips} 行）` : ''}</span>
                {sf.length > 1 && <span className="sub">{sf.map(([k, v]) => `${k} ${money(v)}`).join(' ＋ ')}</span>}</td>
              <td><span className="f">{qv}</span><span className="sub">{qs}</span>{x.q_diff != null && !isZero(x.q_diff) && <span className={'sub' + (x.state === 'qtydiff' ? ' ldd-bad' : '')}>账单比金蝶 {x.q_diff > 0 ? '多' : '少'} {num(Math.abs(x.q_diff))} {x.kd_unit}</span>}</td>
              <td><span className="f">{pv}</span><span className="sub">{ps}</span></td>
              <td className="num"><b>{money(x.doc_fee)}</b>{x.trips > 1 && <span className="sub">本单合计 {money(x.doc_fee_all)}</span>}
                {x.unit_fee != null && <span className="sub">{x.unit_fee} 元/kg</span>}{x.ratio != null && <span className="sub">费比 {(x.ratio * 100).toFixed(2)}%</span>}</td>
            </tr></tbody></table></div></section>

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
                ? <><span className="nd"><span className="h">金蝶推</span><b className="ldd-q">判不了</b> <Mk k="q" /></span><span className="ar">→</span>
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
              : <><label>多收 <input className="clsinp" style={{ width: 90, textAlign: 'right' }} disabled={locked} placeholder="含税金额" value={f.amount || ''}
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

export const LDD_CSS = `
.lrv .ldd-kpis{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;padding:10px 15px;border-bottom:1px solid #DCE2E7}
@media (max-width:1100px){.lrv .ldd-kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}}
.lrv .ldd-kpi{border:1px solid #DCE2E7;border-radius:8px;padding:8px 11px;background:#fff;display:flex;flex-direction:column;gap:2px;text-align:left;cursor:pointer;font:inherit;color:#1B2733;min-width:0}
.lrv .ldd-kpi:hover{border-color:var(--accent)}.lrv .ldd-kpi.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset;background:#F5FAFC}
.lrv .ldd-kpi.total{cursor:default;background:#F5F7F8}.lrv .ldd-kpi.total:hover{border-color:#DCE2E7}
.lrv .ldd-kpi .kl{font-size:11.5px;color:#5E6B78}.lrv .ldd-kpi .kv{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.lrv .ldd-kpi .ks{font-size:11px;color:#8A96A2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrv .ldd-kpi.bad .kv{color:var(--bad)}.lrv .ldd-kpi.good .kv{color:var(--ok)}.lrv .ldd-kpi.zero .kv{color:#8A96A2}
.lrv .ldd-kpi .bar{height:4px;border-radius:2px;background:#E7ECEF;overflow:hidden;margin:2px 0}.lrv .ldd-kpi .bar i{display:block;height:100%;background:var(--accent)}
.lrv .ldd-cls{display:flex;flex-direction:column;gap:2px;align-items:flex-start;min-width:150px;max-width:220px}
.lrv .ldd-cls .pill{white-space:nowrap}.lrv .ldd-diff{font-size:10.5px;color:var(--bad);margin-left:2px}
.lrv .ldd-lk{border:0;background:none;padding:0;font:inherit;font-size:11px;color:#8A96A2;cursor:pointer;text-align:left;max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lrv .ldd-lk:hover{color:var(--accent);text-decoration:underline}
.lrv .ldd-mask{position:fixed;inset:0;background:rgba(10,20,30,.42);z-index:60;display:flex;align-items:flex-start;justify-content:center;padding:5vh 16px 16px;overflow-y:auto}
.lrv .ldd-dlg{background:#fff;color:#1B2733;border-radius:12px;box-shadow:0 10px 40px rgba(20,30,40,.25);width:min(1180px,100%);display:grid;grid-template-rows:auto 1fr auto;max-height:90vh}
.lrv .ldd-dlg header{display:flex;align-items:center;gap:10px;padding:13px 18px;border-bottom:1px solid #DCE2E7}
.lrv .ldd-dlg header h2{margin:0;font-size:14px;flex:1;min-width:0;overflow-wrap:anywhere}
.lrv .ldd-x{border:0;background:none;font-size:20px;color:#8A96A2;cursor:pointer;width:28px;height:28px;border-radius:6px}.lrv .ldd-x:hover{background:#EEF1F3;color:#1B2733}
.lrv .ldd-body{overflow:auto;padding:14px 18px;display:grid;gap:14px;align-content:start}
.lrv .ldd-vbar{border-left:4px solid var(--neu);background:#F5F7F8;border-radius:0 8px 8px 0;padding:10px 14px;display:grid;gap:4px}
.lrv .ldd-vbar.bad{border-color:var(--bad);background:#FBEDEA}.lrv .ldd-vbar.warn{border-color:var(--warn);background:#FCF6EC}.lrv .ldd-vbar.ok{border-color:var(--ok);background:#EEF7F1}
.lrv .ldd-vbar .l1{font-size:13.5px}.lrv .ldd-vbar .l1 b{margin-right:8px}
.lrv .ldd-vbar.bad .l1 b{color:var(--bad)}.lrv .ldd-vbar.warn .l1 b{color:var(--warn)}.lrv .ldd-vbar.ok .l1 b{color:var(--ok)}
.lrv .ldd-vbar .l2{font-size:12.5px;color:#5E6B78}.lrv .ldd-vbar .l2 b{color:#1B2733}
.lrv .ldd-vbar .l3{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:12px;color:#5E6B78}
.lrv .ldd-sec{display:grid;gap:6px}.lrv .ldd-sec>h3{margin:0;font-size:12.5px;color:#8A96A2;font-weight:600}
.lrv .ldd-tw{overflow-x:auto}
.lrv .ldd-calc{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
.lrv .ldd-calc th{text-align:left;font-size:11.5px;color:#8A96A2;font-weight:600;padding:6px 8px;border-bottom:1px solid #DCE2E7;white-space:nowrap}
.lrv .ldd-calc td{padding:8px;border-bottom:1px solid #EEF1F3;vertical-align:top}.lrv .ldd-calc .num{text-align:right;white-space:nowrap}
.lrv .ldd-calc .f{font-weight:500;white-space:nowrap}
.lrv .ldd-mk{font-weight:700;margin-left:3px}.lrv .ldd-mk.ok{color:var(--ok)}.lrv .ldd-mk.bad{color:var(--bad)}.lrv .ldd-mk.q{color:var(--warn)}.lrv .ldd-mk.na{color:#8A96A2;font-weight:400}
.lrv .ldd-bad{color:var(--bad);font-weight:600}.lrv .ldd-q{color:var(--warn);font-weight:600}
.lrv .ldd-tot td{font-weight:600;border-top:1px solid #DCE2E7}
.lrv .ldd-none{font-size:12.5px;color:#8A96A2;padding:9px 12px;border:1px dashed #DCE2E7;border-radius:8px}
.lrv .ldd-chain{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 8px;font-size:12.5px;padding:10px 12px;border:1px solid #DCE2E7;border-radius:8px}
.lrv .ldd-chain.bad{border-color:#E6B7B0}
.lrv .ldd-chain .nd .h{color:#8A96A2;font-size:11.5px;margin-right:4px}.lrv .ldd-chain .ar{color:#8A96A2}.lrv .ldd-chain .ar.bad{color:var(--bad);font-weight:700}
.lrv .ldd-chain .acct{flex-basis:100%;font-size:11.5px;color:#8A96A2}.lrv .ldd-chain .todo{flex-basis:100%;font-size:12px;color:var(--bad)}
.lrv .ldd-how{flex-basis:100%;font-size:11.5px;color:#5E6B78}.lrv .ldd-how summary{cursor:pointer;color:var(--accent)}.lrv .ldd-how ul{margin:4px 0;padding-left:1.2em}
.lrv .ldd-ops .xcls{padding:6px 0}
.lrv .ldd-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 18px;border-top:1px solid #DCE2E7}
.lrv .ldd-foot .sp{flex:1}.lrv .ldd-foot small{color:#8A96A2}
.lrv tr.drow.ldd-cur td{background:#F5FAFC}
`
