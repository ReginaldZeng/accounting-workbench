// [Change Log] Date: 2026-10-13 | Author: Claude Opus 5.5 | Version: V2.936
// Description: 应付板块 › 物流对账 › 付款做账 · 总表改成「四步」的表(样机 docs/20261013_付款做账四步闸/prototype，用户 2026-10-13「可以了」「开发吧」)。
//   用户：「我们是工作台审核账单，工作台收票，付款成功，才执行自动化做账程序」——一张请款单一行，从左到右就是这四步：
//   ① 账单复核(整月登记 / 本主体通过) ② 收票(发票管家审核通过才算；发票后补已确认也算) ③ 付款(金蝶付款单) ④ 做账；
//   前一步没过后面不做，卡在哪一步那一格标黄。每格两行：上面结论、下面灰字是依据(② 票种＋审核状态 ③ 付款单号 ④ 制单人 · 期间 凭证号)。
//   「计提」一列只放计提含税合计和它跟请款的差异；怎么做(只核销 / 红冲更正…)点每行最后的「做账步骤」，弹窗里一步一步放一遍。
//   这里只管画和算「卡在哪一步」；取数、勾选、批量保存、凭证预览都还在 LogisticsVoucher.jsx。
import React, { useEffect, useRef, useState } from 'react'
import { voucherPreview } from '../api.js'

const money = n => (n == null || n === '' ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const r2 = x => Math.round((Number(x) + 1e-9) * 100) / 100
const md = s => String(s || '').slice(5, 10)                       // 2026-10-06 16:05 → 10-06
const REV_CN = { approved: '已审核', pending: '待审核', draft: '草稿' }
const revCn = st => REV_CN[st] || '待审核'

// 这张请款单卡在第几步：0＝四步都过了(可以做账)，1/2/3＝卡在那一步，9＝工作台已做账，8＝别人早就在金蝶记过支付凭证
export const stageOf = r => {
  if (r.posted) return 9
  if (r.paid_voucher) return 8
  if (!r.review) return 1
  if (!(r.later_ok || r.inv_state === 'ok')) return 2
  if (!r.paid) return 3
  return 0
}
const invOk = r => !!(r.later_ok || r.inv_state === 'ok')
// 灰字：票种＋审核状态，如「专票已审核」「专票 2 张待审核」；还没审核通过的那一段标黄
const invParts = r => {
  const by = {}
  ;(r.invs || []).forEach(([t, st]) => { const k = t + '|' + st; by[k] = (by[k] || 0) + 1 })
  return Object.entries(by).map(([k, n]) => { const [t, st] = k.split('|'); return { txt: t + (n > 1 ? ` ${n} 张` : '') + revCn(st), ok: st === 'approved', st, n } })
}
const invPlain = r => invParts(r).map(x => x.txt).join('、')
// V2.961(用户看禾享那一行「已收 1 张 / 专票已审核」：「这里应该是后补单已审核？红字」)：这张单当初是没票先付款、登记了发票后补单的，
// 票是后来经后补单到的——收票那一格要看得出来，不能和当场带票的写成一样。later_state 只有做付款凭证时走了发票后补的才有。
const viaLater = r => !!r.later_state
const laterTxt = x => '后补单' + (x.n > 1 ? ` ${x.n} 张` : '') + revCn(x.st)
const periodOf = r => {                                              // 凭证所在期间：金蝶读到的优先，其次做账记录里记的，最后按付款日
  const v = r.vinfo || {}, p = r.posted || {}
  const y = v.year || p.vyear, m = v.period || p.vperiod || p.vmonth
  return y && m ? `${y}-${String(m).padStart(2, '0')}` : String(r.paid || '').slice(0, 7)
}
const lateReview = r => !!(r.posted && r.review && String(r.posted.at || '') < String(r.review.at || ''))       // 先做的账、后来才复核
// 搜索：全称 / 简称 / 编码、主体、金额(带不带千分位)、计提数、付款单号、凭证号、审批编号、计提凭证号
export const flowHay = (r, plan) => [r.sup_full, r.code, r.carrier, r.payee, r.subject, r.bid, r.fkd, r.paid_voucher,
  r.amount != null ? Number(r.amount).toFixed(2) : '', money(r.amount), plan && plan.acc_sum != null ? Number(plan.acc_sum).toFixed(2) : '', plan ? money(plan.acc_sum) : '',
  r.posted ? '记-' + r.posted.vno : '', r.posted ? r.posted.vno : '', ...((plan && plan.acc) || []).map(a => `${a.month}/${a.vno}#`)].join(' ').toLowerCase()

function Cell({ kind, ic, main, grey, title }) {
  return <div className={'fc ' + kind} title={title || undefined}><span className="ic">{ic}</span>
    <span className="tx"><span className="m">{main}</span>{grey != null && grey !== '' && <span className="g">{grey}</span>}</span></div>
}
const Dots = () => <><i /><i /><i /></>

export function FlowRule() {
  return <div className="lvf-rule">{['账单复核', '收票', '付款', '做账'].map((t, i) => <React.Fragment key={t}>
    {i > 0 && <span className="ar">→</span>}<span className="st"><i>{'①②③④'[i]}</i>{t}</span></React.Fragment>)}
    <span>前一步没过，后面不做。卡在哪一步，那一格标黄。</span></div>
}

// 上面那一排数字：写法照「银行–金蝶稽核」的切片卡(全站共用的 .kpis / .kpi)——圆点＋名称在上、大数字在下，点一下只看那一类(V2.940，用户贴那一页：「参考一下吧」)
// 顺序照四步走(用户看原来「可以做账」排最前：「这个顺序好像也不太对」)：全部 → 卡在① → 卡在② → 等③付款 → 可以做账 → 已做账
export const FLOW_KPI = [['', '全部', 'var(--gray)', ''],
  ['1', '卡在 ① 账单复核', 'var(--amber)', '整月没登记，这个主体也没标通过'], ['2', '卡在 ② 收票', 'var(--amber)', '没票，或发票管家还没审核通过'],
  ['3', '等 ③ 付款', 'var(--blue)', '账单、票都好了，金蝶还没有付款单'], ['0', '④ 可以做账', 'var(--green)', '前三步都过了'], ['9', '已做账', 'var(--accent)', '']]
export function FlowTiles({ rows, value, onChange }) {
  const other = rows.filter(r => stageOf(r) === 8).length
  return <div className="kpis">{FLOW_KPI.map(([k, label, color, tip]) => {
    const n = k === '' ? rows.length : rows.filter(r => flowKey(r) === k).length
    return <div key={k || 'all'} className={'kpi slice' + (value === k ? ' on' : '')} role="button" tabIndex={0}
      title={(k === '9' && other ? `其中 ${other} 张是别人早在金蝶做的` : tip) || undefined}
      onClick={() => onChange(k)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onChange(k) } }}>
      <div className="kl"><span className="dot" style={{ background: color }} />{label}</div><div className="kv">{n}</div></div>
  })}</div>
}

export const flowKey = r => { const x = stageOf(r); return String(x === 8 ? 9 : x) }

export function FlowTable({ rows, loading, plans, sel, setSel, canBatch, onSteps, fixPaycode, emptyText }) {
  const groups = []
  const by = new Map()
  rows.forEach(r => { const k = r.code || r.sup_full || r.carrier; if (!by.has(k)) { by.set(k, { key: k, full: r.sup_full || r.payee || r.carrier, code: r.code, short: r.carrier, all: [] }); groups.push(by.get(k)) } by.get(k).all.push(r) })
  const able = rows.filter(canBatch)
  return <div className="tbl-wrap lvf"><table className="lv-t lvf-t">
    <thead><tr>
      <th className="ck"><input type="checkbox" aria-label="勾选能批量做账的" title="勾选当前筛选里能批量做账的" disabled={!able.length} checked={able.length > 0 && able.every(r => sel[r.inst])}
        onChange={e => { const on = e.target.checked; setSel(o => { const x = { ...o }; able.forEach(r => { x[r.inst] = on }); return x }) }} /></th>
      <th>主体</th><th className="num">请款金额</th><th className="num">计提</th>
      {['账单复核', '收票', '付款', '做账'].map((t, i) => <th key={t}><span className="hd"><span className="no">{i + 1}</span>{t}</span></th>)}
      <th /></tr></thead>
    <tbody>
      {loading && <tr><td colSpan="9" className="lv-empty">读取中…</td></tr>}
      {!loading && !rows.length && <tr><td colSpan="9" className="lv-empty">{emptyText || '没有请款单'}</td></tr>}
      {groups.map(g => <React.Fragment key={g.key}>
        <tr className="grp"><td colSpan="9"><div className="gh"><span>{g.full}</span>{g.code && <span className="code mono">{g.code}</span>}
          {g.short && g.short !== g.full && <span className="short">{g.short}</span>}
          <span className="cnt">{g.all.length} 张请款单 · {money(g.all.reduce((a, r) => a + (r.amount || 0), 0))}</span></div></td></tr>
        {g.all.map(r => {
          const s = stageOf(r), done = s >= 8, plan = plans[r.inst]
          const at = n => !done && s === n                         // 卡在这一步
          const after = n => !done && s !== 0 && s < n             // 前面有没过的，这一步先不看
          // 计提：含税合计；灰字只写和请款的差异
          const acc = plan && plan.acc_n ? plan.acc_sum : null
          const dif = acc == null ? 0 : r2(acc - (r.amount || 0))
          const cA = !r.period ? <span className="dim">未认账单月</span>
            : !plan ? <span className="dim">…</span>
              : plan.kind === 'err' || acc == null ? <span className="dim" title={plan.text || ''}>{plan.kind === 'noacc' || !plan.acc_n ? '没找到计提' : '没读到'}</span>
                : <div className="a" title={`${plan.acc_n} 张计提：${(plan.acc || []).map(a => `${a.month}/${a.vno}#`).join('、')}`}><span className="mono">{money(acc)}</span>
                  <span className="g">{dif ? <span className="w">比请款{dif > 0 ? '多' : '少'} {money(Math.abs(dif))}</span> : '和请款一致'}</span></div>
          // ① 账单复核
          const rv = r.review
          const c1 = rv ? <Cell kind="ok" ic="✓" main="已复核" grey={(rv.how === 'month' ? `整月登记 ${md(rv.at)}` : `本主体通过 ${md(rv.at)} ${rv.by || ''}`) + (lateReview(r) ? ' · 做账后才复核' : '')} />
            : done ? <Cell kind={s === 9 ? 'stop' : 'wait'} ic={s === 9 ? '!' : '○'} main="没复核" grey={s === 9 ? '做账时就没复核' : ''} />
              : <Cell kind="stop" ic="!" main="还没复核" grey={r.period ? '到账单复核台登记，或标本主体通过' : '请款单还没认出是哪个月的账单'} />
          // ② 收票：灰字＝票种＋发票管家审核状态
          const parts = invParts(r)
          const grey2 = !parts.length ? null
            : viaLater(r) ? parts.map((x, i) => <React.Fragment key={i}>{i > 0 && '、'}<span className={x.ok ? 'lt' : 'w'} title={x.txt + '（发票后补单到的票）'}>{laterTxt(x)}</span></React.Fragment>)
              : parts.map((x, i) => <React.Fragment key={i}>{i > 0 && '、'}{x.ok ? x.txt : <span className="w">{x.txt}</span>}</React.Fragment>)
          const main2 = `已收 ${(r.invs || []).length} 张`
          const c2 = r.later_ok ? <Cell kind="later" ic="补" main="票后到" grey="发票后补已确认" />
            : !(r.invs || []).length ? ((after(2) || done) ? <Cell kind="wait" ic="○" main="没票" grey={r.later_slip ? `已登记后补单 #${r.later_slip.id}，待确认` : ''} />
              : <Cell kind="stop" ic="!" main="没票" grey={r.later_slip ? `已登记后补单 #${r.later_slip.id}，到复核台第③步确认` : '票夹里还没有发票'} />)
              : r.inv_state === 'ok' ? <Cell kind="ok" ic="✓" main={main2} grey={grey2} />
                : r.inv_state === 'diff' ? ((after(2) || done) ? <Cell kind="wait" ic="○" main={main2} grey={`合计 ${money(r.inv_total)} ≠ 请款`} /> : <Cell kind="stop" ic="!" main="发票≠请款" grey={`发票合计 ${money(r.inv_total)}`} />)
                  : (after(2) || done) ? <Cell kind="pend" ic={<Dots />} main={main2} grey={grey2} />
                    : <Cell kind="stop" ic="!" main={main2} grey={<>{grey2} · 到发票管家审核</>} />
          // ③ 付款：灰字＝金蝶付款单号
          const c3 = r.status === 'paycode' ? <Cell kind="stop" ic="!" main="付款单往来单位选错" title={(r.paywarn || {}).text}
            grey={r.paywarn ? <button className="lnk" onClick={() => fixPaycode(r)}>改成「{r.paywarn.want}」</button> : ''} />
            : r.paid ? <Cell kind="ok" ic="✓" main="已付款" title={`付款日期 ${r.paid}`} grey={r.paid_voucher ? `金蝶已记 ${r.paid_voucher}` : r.fkd ? <span className="mono">{r.fkd}</span> : '付款单还是暂存，没编号'} />
              : <Cell kind={at(3) ? 'stop' : 'wait'} ic={at(3) ? '⋯' : '○'} main={at(3) ? '等付款' : '没付款'} grey={r.dt_status === 'RUNNING' ? '钉钉还在审批' : '审批通过了，金蝶还没有付款单'} />
          // ④ 做账：灰字＝制单人 · 期间 凭证号
          const vi = r.vinfo || {}, p = r.posted
          const c4 = s === 9 ? <Cell kind="ok" ic="✓" title={`${p.by === '系统自动' ? '系统自动做的' : `工作台里 ${p.by || ''} 点的`}，${p.at || ''}`}
            main={<>已做账{vi.status ? <span className={'tag ' + (vi.status === 'C' ? 'ok' : 'warn')}>{vi.status === 'C' ? '已审核' : '等审核'}</span> : null}
              {r.later_state === 'todo' && <span className="tag warn" title="发票后补的单：发票到了，还差一张「暂估转待认证」，点「做账步骤」→ 凭证预览 里做">票到了·待转待认证</span>}</>}
            grey={<>{vi.maker ? vi.maker + ' · ' : ''}<span className="mono">{periodOf(r)} 记-{p.vno}</span>{r.later_state === 'wait' ? ' · 等发票' : r.later_state === 'done' ? ` · 暂估已转 记-${r.later3_vno}` : ''}</>} />
            : s === 8 ? <Cell kind="wait" ic="✓" main="金蝶已记账" grey={<>不是工作台做的 · <span className="mono">{r.paid_voucher}</span></>} />
              : s === 0 ? (!plan ? <Cell kind="wait" ic="○" main="…" />
                : canBatch(r) || plan.kind === 'later' ? <Cell kind="go" ic="→" main="可以做账" grey="开着自动做账的话，下一轮会做" />
                  : <Cell kind="stop" ic="!" main="要人来做" grey={(plan.text || '').slice(0, 22)} title={plan.text || ''} />)
                : <Cell kind="wait" ic="○" main="还不能做" grey={`卡在 ${'①②③'[s - 1]}`} />
          return <tr key={r.inst}>
            <td className="ck"><input type="checkbox" aria-label="勾选批量做账" disabled={!canBatch(r)} checked={!!sel[r.inst] && canBatch(r)}
              title={canBatch(r) ? '勾选批量做账' : done ? '已做账' : s !== 0 ? '四步还没过完，不能做账' : '这种要点开单张做，不进批量'}
              onChange={e => setSel(o => ({ ...o, [r.inst]: e.target.checked }))} /></td>
            <td className="sj">{r.subject}{r.period && <div className="dim per">{r.period.slice(5)} 月账单</div>}</td>
            <td className="num mono">{money(r.amount)}</td><td className="acc">{cA}</td>
            <td className={at(1) ? 'stopcell' : ''}>{c1}</td><td className={at(2) ? 'stopcell' : ''}>{c2}</td><td className={at(3) ? 'stopcell' : ''}>{c3}</td><td>{c4}</td>
            <td><button className={'btn sm' + (s === 0 ? ' btn-pri' : '')} disabled={!r.period} title={r.period ? '' : '请款单还没认出是哪个月的账单'} onClick={() => onSteps(r)}>做账步骤</button></td>
          </tr>
        })}
      </React.Fragment>)}
    </tbody></table></div>
}

// —— 做账步骤弹窗：先过三道闸，再是系统自动做的那几步；每一步写这张单自己的数。动效只放一遍(用户「播放一遍就好啦」)。——
function buildSteps(r, d) {
  const s = stageOf(r), done = s === 9
  const g1 = !!r.review, g2 = invOk(r), g3 = !!r.paid
  const G = [
    { ok: g1, t: '① 账单复核', d: g1 ? (r.review.how === 'month' ? `整月登记 ${md(r.review.at)}` : `本主体通过 ${md(r.review.at)} ${r.review.by || ''}`) : done ? '做账时没复核（那时还没有这道闸）' : '整月没登记，这个主体也没标通过' },
    { ok: g2, t: '② 收票', d: r.later_ok ? '发票后补已确认：先付款，票到了再转税' : (r.invs || []).length ? `已收 ${r.invs.length} 张，${invPlain(r)}${viaLater(r) ? '（发票后补单到的票）' : ''}` + (r.inv_state === 'diff' ? `；合计 ${money(r.inv_total)} 和请款对不上` : '') : '票夹里没票' },
    { ok: g3, t: '③ 付款', d: g3 ? `已付款 ${r.paid}` + (r.fkd ? `，付款单 ${r.fkd}` : r.paid_voucher ? `，金蝶已记 ${r.paid_voucher}` : '，付款单还是暂存') : '金蝶还没有付款单' },
  ]
  const A = []
  if (s === 8) return { G, A: [{ skip: 1, t: '不是工作台做的', d: `这笔付款早就在金蝶记过支付凭证（${r.paid_voucher}），工作台不再做` }], done: false, s }
  if (!d) return { G, A, done, s }
  const plan = d.plan || {}, req = d.req || {}
  if (plan.status !== 'ok' && !done) { A.push({ bad: 1, t: '系统做不了，要人来', d: (plan.msgs || [])[0] || '计提和发票对不上' }); return { G, A, done, s } }
  const lines = (d.voucher || {}).lines || []
  const later = plan.pay_only === 'later'
  A.push({ t: '审核金蝶付款单', d: (r.fkd ? `付款单 ${r.fkd}：` : '付款单：') + '保存、提交、审核，都是系统做' })
  A.push({ t: '金蝶自动出付款凭证', d: `借 供应商往来 / 贷 银行存款 ${money(req.amount)}` + (done ? `。${periodOf(r)} 记-${r.posted.vno}` + ((r.vinfo || {}).maker ? `，制单人 ${r.vinfo.maker}` : '') : '') })
  ;(d.adjust || []).forEach(a => {
    const o = a.old || {}, n = a.new || {}
    if (a.mode === 'move') A.push({ t: `补提 ${a.ref}`, d: `这笔计提记在了${a.from || '别的主体'}账上：本张凭证里按 ${money(n.gross)} 补提到${req.subject}` })
    else if (a.mode === 'xout') A.push({ t: `红冲 ${a.ref}`, d: `记错主体的计提，整笔红冲 ${money(o.gross)}` })
    else A.push({ t: `红冲更正 ${a.ref}`, d: (a.why || '') + (r2(o.gross) !== r2(n.gross) ? `。含税 ${money(o.gross)} → ${money(n.gross)}` : r2(o.tax) !== r2(n.tax) ? `。税额 ${money(o.tax)} → ${money(n.tax)}` : '') + '。原计提整笔红冲，再按对的重提' })
  })
  const tax06 = r2(lines.filter(l => l.acct === '2221.01.06').reduce((x, l) => x + (l.dr || 0), 0)), ninv = (d.invoices || []).length
  if (later) A.push({ skip: 1, t: '核销', d: '发票后补：这一步先不做，暂估进项税挂着；票到了另做一张「暂估转待认证」' })
  else if (tax06 > 0) A.push({ t: '核销，暂估转待认证', d: `按 ${ninv} 张发票，暂估进项税 ${money(tax06)} 转到待认证` })
  else A.push({ skip: 1, t: '核销', d: ninv ? '发票不能抵扣，不转待认证' : '没有暂估进项税要转' })
  A.push({ t: '提交凭证', d: done ? ((r.vinfo || {}).status === 'C' ? '已提交，已经审核了' : '已提交，等人在金蝶审核') : '提交，不审核；审核留给人在金蝶做' })
  const cf = d.cashflow || {}
  const cfNow = cf.now && Object.keys(cf.now).length ? Object.entries(cf.now).map(([k, v]) => `${k} ${money(v)}`).join(' ＋ ') : ''
  if (done && cfNow) A.push({ t: '指定现金流量', d: cfNow })
  else if ((cf.rows || []).length) A.push({ t: '指定现金流量', d: cf.rows.map(x => `${x.name} ${money(x.amount)}`).join(' ＋ ') })
  else A.push({ bad: 1, t: '指定现金流量', d: (cf.why || '拆不出来') + '：系统不猜，要人到金蝶指定' })
  ;(d.xbook || []).forEach(x => A.push({ t: `${x.short}账上红冲 ${x.ref}`, d: x.reversed ? `${x.short}那边已经红冲过了（${money(x.gross)}），不重复做` : x.via ? `放在${x.short}自己的付款凭证里一起做` : `到${x.short}账簿建红冲凭证 ${money(x.gross)}，提交不审核` }))
  if (later && done) A.push(r.later_state === 'done' ? { t: '票到了，暂估转待认证', d: `已另做一张凭证 记-${r.later3_vno}` } : { skip: 1, t: '票到了，暂估转待认证', d: r.later_state === 'todo' ? '发票到了，这一张还没做：在凭证预览里做' : '发票还没到' })
  A.push({ t: '收尾', d: '记做账记录；首页待办转给审核人；发钉钉提醒' })
  return { G, A, done, s }
}

export function StepsDlg({ row, plan, onClose, onPreview }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [shown, setShown] = useState(0)          // 亮到第几步(只往前走一遍)
  const need = stageOf(row) !== 8
  useEffect(() => {
    let off = false
    setD(null); setErr(''); setShown(0)
    if (need) voucherPreview(row.inst).then(x => { if (!off) setD(x) }).catch(e => { if (!off) setErr(e.message) })
    return () => { off = true }
  }, [row.inst])
  useEffect(() => { const k = e => { if (e.key === 'Escape') onClose() }; document.addEventListener('keydown', k); return () => document.removeEventListener('keydown', k) }, [])
  const ready = !need || !!d || !!err
  const { G, A, done, s } = buildSteps(row, d)
  const all = [...G.map(x => ({ ...x, gate: 1 })), ...(ready ? A : [])]
  const timer = useRef(null)
  useEffect(() => {                               // 一步一步亮：卡住的那一步多停一会儿；系统设了「减少动效」的直接全亮
    if (!ready) { if (shown < G.length) timer.current = setTimeout(() => setShown(n => n + 1), 420); return () => clearTimeout(timer.current) }
    if (shown >= all.length) return
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setShown(all.length); return }
    const prev = all[shown - 1]
    timer.current = setTimeout(() => setShown(n => n + 1), shown === 0 ? 150 : prev && (prev.bad || (prev.gate && !prev.ok)) ? 1000 : 480)
    return () => clearTimeout(timer.current)
  }, [shown, ready, all.length])
  const manual = !done && d && (d.plan || {}).status !== 'ok'
  const dif = plan && plan.acc_n ? r2(plan.acc_sum - (row.amount || 0)) : null
  const pill = done ? <span className="pill done">已做账 记-{row.posted.vno}</span> : s === 8 ? <span className="pill done">金蝶已记账</span>
    : manual ? <span className="pill stop">要人工</span> : s === 0 ? <span className="pill go">可以做账</span> : <span className="pill stop">卡在 {'①②③'[s - 1]}</span>
  const li = (x, i, cls, ic) => <li key={i} className={cls + (i < shown ? ' on' : '') + (i === shown - 1 && shown < all.length ? ' run' : '')}>
    <span className="dot">{ic}</span><div className="bx"><b>{x.t}</b><span>{x.d}</span></div></li>
  return <div className="lv-mask lvf-mask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <div className="lvf-dlg" role="dialog" aria-modal="true" aria-label="做账步骤">
      <div className="dh"><div className="t1">做账步骤 {pill}</div>
        <div className="t2">{row.sup_full || row.carrier} · {row.subject}　请款 <span className="mono">{money(row.amount)}</span>
          {dif != null && <>　计提 <span className="mono">{money(plan.acc_sum)}</span>{dif ? `（比请款${dif > 0 ? '多' : '少'} ${money(Math.abs(dif))}）` : '（一致）'}</>}</div></div>
      <div className="db">
        <div className="sec">先过三道闸</div>
        <ol className="lvf-steps">{G.map((x, i) => li(x, i, x.ok ? 'done' : 'bad', x.ok ? '✓' : '!'))}</ol>
        <div className="sec s2">{s === 8 ? '然后' : done ? '系统当时是这样做的' : manual ? '然后' : s !== 0 ? '过了以后，系统会这样做' : '系统会这样做'}</div>
        {!ready && <div className="dim">正在读金蝶的计提、付款单…</div>}
        {err && <div className="lv-msg bad">没读到：{err}</div>}
        {ready && !err && <ol className="lvf-steps">{A.map((x, j) => li(x, G.length + j, x.bad ? 'bad' : x.skip ? 'skip' : done ? 'done' : 'plan', x.bad ? '!' : x.skip ? '–' : done ? '✓' : j + 1))}</ol>}
      </div>
      <div className="df"><span style={{ flex: 1 }} />{need && <button className="btn" onClick={onPreview}>凭证预览</button>}<button className="btn btn-pri" onClick={onClose}>关闭</button></div>
    </div></div>
}

export const FLOW_CSS = `
.lv .lvf-rule{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;font-size:12.5px;color:var(--ink-2)}
.lv .lvf-rule .st{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;background:var(--bg);border:1px solid var(--line)}
.lv .lvf-rule .st i{font-style:normal;font-weight:700;color:var(--accent)}.lv .lvf-rule .ar{color:var(--ink-3)}
.lv .lvf-tiles{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
.lv .lvf-tiles .tile{text-align:left;border:1px solid var(--line);background:var(--bg);border-radius:10px;padding:10px 12px;cursor:pointer;font:inherit;color:var(--ink)}
.lv .lvf-tiles .tile[aria-pressed="true"]{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.lv .lvf-tiles .n{font-size:22px;font-weight:700;line-height:1.2;font-variant-numeric:tabular-nums}
.lv .lvf-tiles .t{font-size:12.5px;color:var(--ink-2)}.lv .lvf-tiles .s{font-size:11.5px;color:var(--ink-3);margin-top:2px;min-height:17px}
.lv .lvf-tiles .tile.ok .n{color:var(--green)}.lv .lvf-tiles .tile.warn .n{color:var(--amber)}.lv .lvf-tiles .tile.neu .n{color:var(--ink-2)}.lv .lvf-tiles .tile.done .n{color:var(--accent)}
.lv .lvf-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.lv .lvf-bar .q{position:relative;flex:0 1 380px}
.lv .lvf-bar .q input{width:100%;height:34px;padding:0 30px 0 32px;border:1px solid var(--line-strong);border-radius:8px;background:var(--bg);font:inherit;color:var(--ink);outline:none}
.lv .lvf-bar .q input:focus{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.lv .lvf-bar .q input::-webkit-search-cancel-button{display:none}
.lv .lvf-bar .q svg{position:absolute;left:10px;top:50%;transform:translateY(-50%);width:15px;height:15px;stroke:var(--ink-3);fill:none;stroke-width:2;stroke-linecap:round}
.lv .lvf-bar .q .x{position:absolute;right:6px;top:50%;transform:translateY(-50%);border:0;background:none;color:var(--ink-3);cursor:pointer;font-size:15px;line-height:1;padding:4px}
.lv .lvf-bar .n{font-size:12px;color:var(--ink-3)}
.lv .lvf-t th,.lv .lvf-t td{vertical-align:middle;padding:9px 8px}
.lv .lvf-t th .hd{display:inline-flex;align-items:center;gap:6px}
.lv .lvf-t th .no{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:var(--accent-soft);color:var(--accent);font-weight:700;font-size:11.5px;line-height:1}
.lv .lvf-t th.num,.lv .lvf-t td.num{text-align:right;white-space:nowrap}
.lv .lvf-t th.ck,.lv .lvf-t td.ck{width:30px;padding-right:0}
.lv .lvf-t tr.grp td{background:var(--bg-rail);font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line)}
.lv .lvf-t .gh{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.lv .lvf-t .gh .code{font-weight:400;color:var(--ink-2);font-size:12px}
.lv .lvf-t .gh .short{font-weight:400;font-size:12px;color:var(--accent);background:var(--accent-soft);border-radius:999px;padding:0 8px;line-height:18px}
.lv .lvf-t .gh .cnt{font-weight:400;color:var(--ink-3);margin-left:auto;font-size:12px}
.lv .lvf-t td.sj{white-space:nowrap}.lv .lvf-t td.sj .per{font-size:11.5px}
.lv .lvf-t td.acc .a{display:flex;flex-direction:column;align-items:flex-end;justify-content:center;gap:1px;min-height:36px}
.lv .lvf-t td.acc .g{color:var(--ink-3);font-size:11.5px;line-height:1.4;white-space:nowrap}
.lv .lvf-t .w{color:var(--amber);font-weight:600}.lv .lvf-t .lt{color:var(--red);font-weight:600}
.lv .lvf-t td.stopcell{background:color-mix(in srgb,var(--amber-bg) 55%,var(--bg))}
.lv .fc{display:flex;gap:8px;align-items:center;min-height:36px}
.lv .fc .ic{flex:none;display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;font-size:12px;font-weight:700;line-height:1}
.lv .fc .tx{min-width:0;display:flex;flex-direction:column;justify-content:center;gap:1px}
.lv .fc .tx .m{display:flex;align-items:center;gap:6px;line-height:1.4;white-space:nowrap}
.lv .fc .tx .g{color:var(--ink-3);font-size:11.5px;line-height:1.4;white-space:nowrap}
.lv .fc.ok .ic{background:var(--green-bg);color:var(--green)}
.lv .fc.stop .ic{background:var(--amber-bg);color:var(--amber)}.lv .fc.stop .tx .m{color:var(--amber);font-weight:600}
.lv .fc.wait .ic{background:var(--gray-bg);color:var(--ink-3)}.lv .fc.wait .tx .m{color:var(--ink-3)}
.lv .fc.go .ic{background:var(--green);color:#fff}.lv .fc.go .tx .m{font-weight:600;color:var(--green)}
.lv .fc.pend .ic{background:var(--bg);box-shadow:inset 0 0 0 1.5px var(--amber);gap:2px}.lv .fc.pend .ic i{width:3px;height:3px;border-radius:50%;background:var(--amber)}
.lv .fc.later .ic{background:var(--accent-soft);color:var(--accent);font-size:11px}
.lv .fc .tag{display:inline-flex;align-items:center;padding:0 7px;height:18px;border-radius:999px;font-size:11.5px;font-weight:400;white-space:nowrap}
.lv .fc .tag.ok{background:var(--green-bg);color:var(--green)}.lv .fc .tag.warn{background:var(--amber-bg);color:var(--amber)}
.lv .fc .lnk{font-size:11.5px}
.lv .lvf-mask{align-items:flex-start;padding:6vh 16px;overflow:auto}
.lv .lvf-dlg{width:min(640px,100%);background:var(--bg);border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.25);overflow:hidden;color:var(--ink)}
.lv .lvf-dlg .dh{padding:14px 18px 12px;border-bottom:1px solid var(--line)}
.lv .lvf-dlg .t1{font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.lv .lvf-dlg .t2{font-size:12px;color:var(--ink-2);margin-top:3px}
.lv .lvf-dlg .pill{display:inline-flex;align-items:center;height:20px;padding:0 9px;border-radius:999px;font-size:11.5px;font-weight:600}
.lv .lvf-dlg .pill.done{background:var(--accent-soft);color:var(--accent)}.lv .lvf-dlg .pill.go{background:var(--green-bg);color:var(--green)}.lv .lvf-dlg .pill.stop{background:var(--amber-bg);color:var(--amber)}
.lv .lvf-dlg .db{padding:14px 18px 6px;max-height:62vh;overflow:auto}
.lv .lvf-dlg .df{display:flex;align-items:center;gap:8px;padding:10px 18px 14px}
.lv .lvf-dlg .sec{font-size:11.5px;color:var(--ink-3);margin:2px 0 8px}.lv .lvf-dlg .sec.s2{margin-top:14px}
.lv .lvf-dlg ol.lvf-steps{list-style:none;margin:0;padding:0}
.lv .lvf-dlg ol.lvf-steps li{position:relative;display:flex;gap:12px;padding:0 0 14px 0;opacity:.28;transition:opacity .35s ease}
.lv .lvf-dlg ol.lvf-steps li::before{content:'';position:absolute;left:10px;top:22px;bottom:0;width:2px;background:var(--line);transition:background .35s ease}
.lv .lvf-dlg ol.lvf-steps li:last-child::before{display:none}
.lv .lvf-dlg ol.lvf-steps .dot{flex:none;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700;line-height:1;
  background:var(--bg);color:var(--ink-3);box-shadow:inset 0 0 0 1.5px var(--line-strong);transition:all .3s ease;z-index:1}
.lv .lvf-dlg ol.lvf-steps .bx{min-width:0;padding-top:2px}
.lv .lvf-dlg ol.lvf-steps .bx b{display:block;font-weight:600;line-height:1.4}
.lv .lvf-dlg ol.lvf-steps .bx span{display:block;color:var(--ink-2);font-size:12px;line-height:1.55}
.lv .lvf-dlg ol.lvf-steps li.on{opacity:1}
.lv .lvf-dlg ol.lvf-steps li.on.done .dot{background:var(--green);color:#fff;box-shadow:none}.lv .lvf-dlg ol.lvf-steps li.on.done::before{background:var(--green)}
.lv .lvf-dlg ol.lvf-steps li.on.plan .dot{background:var(--accent-soft);color:var(--accent);box-shadow:inset 0 0 0 1.5px var(--accent)}
.lv .lvf-dlg ol.lvf-steps li.on.bad .dot{background:var(--amber);color:#fff;box-shadow:none}.lv .lvf-dlg ol.lvf-steps li.on.bad .bx b{color:var(--amber)}
.lv .lvf-dlg ol.lvf-steps li.on.skip .dot{background:var(--gray-bg);color:var(--ink-3);box-shadow:none}
.lv .lvf-dlg ol.lvf-steps li.run .dot{animation:lvfpulse .5s ease-out 1}
@keyframes lvfpulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 45%,transparent)}100%{box-shadow:0 0 0 7px transparent}}
@media (prefers-reduced-motion: reduce){.lv .lvf-dlg ol.lvf-steps li,.lv .lvf-dlg ol.lvf-steps .dot{transition:none}.lv .lvf-dlg ol.lvf-steps li.run .dot{animation:none}}
@media (max-width:900px){.lv .lvf-tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
`
