// [Change Log] Date:2026-09-30 Author:Claude Opus 5.5 Version:V2.699
// V2.697：逐笔计提每笔可「改维度」＝登记计提更正(应改为 费用项目/科目/部门/产品线+说明)，不改金蝶、不影响对账，
//   导出复核表多一页《计提更正单》打印交专人改。V2.699：改成弹窗、只改点的那一笔。V2.695：加「可逐单」列。
// V2.693：总表金蝶数据后端缓存30分(可刷新)、返回总表不再清空重拉；逐单核价核量改"一单一行、点开看物料"，
//   筛选/计数按真实核对结果(金蝶查无/数量不符/打托·免核/一致)，改归类挪进展开区。
// 物流账单复核台（三步流）：总表(承运商×主体，计提出发)
//   → ① 逐笔计提复核：顶部结论格；复核要点一行(点编辑展开)；表按「主体·费用类型」分组，组头即小计；
//        每笔=产品线(产品类型·部门小字)/凭证号/含税计提(税率标签)/账单/差异/差异解释，有差异未解释的行淡红底
//   → ② 逐单核价核量：账单每张单据核数量/重量，可手改归类
//   → ③ 确认通过 → 登记已复核(整月一家一次，登记后锁当月归类/备注) → 导出复核表
import React, { useEffect, useState, useCallback, useRef } from 'react'
import LogisticsInvCompare from './LogisticsInvCompare.jsx'   // 第③步·发票与暂估(V2.768)
import { reviewResult, reviewImportPriceCard, reviewParseBill, reviewKingdeeQty, reviewOverview, reviewExportUrl, reviewDocNote, reviewDocClassify, reviewDocConfirm, reviewSubjectMark, reviewPayreqScan, reviewPayreqPull, reviewPayreqAssign, reviewPayreqExclude, reviewPayreqFileUrl, reviewLines, reviewLineNote, reviewLineFix, reviewDimOptions, reviewCarrierPointsSet, reviewSign, reviewUnsign, reviewWtRange, reviewInvoices } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const money = n => (n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const pct = r => (r == null ? '—' : (Number(r) * 100).toFixed(r * 100 % 1 ? 1 : 0) + '%')
const isZero = d => d != null && Math.abs(d) < 0.01
const dcls = d => (d == null ? '' : isZero(d) ? 'diffok' : 'diffbad')
const dtxt = d => (d == null ? '—' : isZero(d) ? '0 · 平' : (d > 0 ? '+' : '') + money(d))
const PS = { ok: ['通过', 'ok'], over: ['多收', 'bad'], under: ['账单少收', 'neu'], free: ['账单未收·我方有利', 'neu'], gap: ['价卡缺·待确认', 'warn'], na: ['待补价卡', 'neu'] }
const STEPS = [['lines', '逐笔计提复核'], ['docs', '逐单核价核量'], ['sign', '确认与登记']]
// 逐单·单据核对结果（后端 doc.state）：筛选条与结论 pill 共用
const DOC_Q = [
  { f: 'ex', n: '待核', k: 'ex', cls: 'warn', dd: '金蝶查无 + 数量不符 + 核价不符' },
  { f: 'miss', n: '金蝶查无', k: 'miss', cls: 'bad', dd: '账单单号在金蝶没找到出库单' },
  { f: 'qtydiff', n: '数量不符', k: 'qtydiff', cls: 'warn', dd: '账单量与金蝶核对量超过容差(2%或1)' },
  { f: 'price', n: '核价不符', k: 'price', cls: 'bad', dd: '包天包趟按报价核：运费≠数量×单价，或有报价未列的加班费' },
  { f: 'info', n: '免核', k: 'info', cls: 'neu', dd: '打托倒算托规 / 整车包车议价 / 调拨包天包趟 / 无单据调整，不核数量，仅提示' },
  { f: 'ok', n: '一致', k: 'ok', cls: 'ok', dd: '账单量＝金蝶核对量' },
  { f: 'feediff', n: '费用类型不一致', k: 'feediff', cls: 'bad', dd: '物流部填的费用类型 ≠ 系统按金蝶单据判的（其他出库单看领料部门）；人工定过费用类型的（特批）不算' },
  { f: 'done', n: '已确认', k: 'done', cls: 'ok', dd: '复核人核过没问题、已点「确认无误」的单据（不再算待核）' },
  { f: 'all', n: '全部', k: 'all', cls: '', dd: '' },
]
// 总表复核状态：已登记 > 全部主体通过 > 复核中(标过主体/写过解释/确认过单据/登记过更正) > 账单已传 > 未传账单 > 未配
const OV_ST = { signed: ['已登记', 'ok'], allfix: ['主体全通过·计提需更正', 'bad'], allok: ['主体全通过·待登记', 'ok'], doing: ['复核中', 'warn'], billed: ['账单已就绪', 'neu'],
  billarrived: ['账单已到·待配置', 'warn'], noaccr: ['有请款·无计提', 'bad'], nobill: ['未传账单', 'neu'], nospec: ['未配', 'neu'],
  register: ['登记制·待登记', 'neu'] }   // V2.826 登记制(路凯卡板租赁、禾享国际寄样、货拉拉)：没有可逐单核的账单，只核对计提和请款，点「登记」
// 钉钉请款单(V2.730)：进度标颜色 / 总表筛选 / 附件角色 / 账单导入状态 / 月份怎么认出来的
const DT_CLS = { mine: 'dtmine', run: 'dtrun', agreed: 'dtok', paid: 'dtpaid', void: 'dtvoid' }
const DT_F = [['dt:mine', '待我审批', 'warn', r => r.dt && r.dt.mine], ['dt:any', '已提交请款', 'neu', r => r.dt && r.dt.n], ['dt:paid', '已付款', 'ok', r => r.dt && r.dt.paid]]
const ROLE_LB = [['bill', '账单'], ['stamp', '账单盖章件'], ['invoice', '发票'], ['review', '审核留档']]
const BILL_LB = { imported: ['账单已就绪', 'ok'], register: ['登记制·不导账单', 'neu'], exists: ['复核台已有账单·没覆盖', 'warn'], nospec: ['账单已到·待配置解析', 'warn'], parsefail: ['账单解析失败', 'bad'], nofile: ['请款单没附账单', 'neu'] }
const PSRC = { amount: '请款金额正好＝该月计提，自动认出', text: '按事由/附件名里写的月份认出', manual: '手工认领' }

function DtChip({ reqs, onOpen }) {
  if (!reqs || !reqs.length) return null
  const v = reqs[0], more = reqs.length - 1
  return <button className={'dtchip ' + (DT_CLS[v.st.key] || '')} title={`钉钉请款 ${money(v.amount)} · ${v.applicant || ''} ${v.created || ''}（点开看节点/附件）`}
    onClick={() => onOpen(v)}>{v.st.key === 'run' && v.cur && v.cur.length > 1 ? `待${v.cur[0].name}等审批` : v.st.label}{v.st.date ? ' ' + v.st.date.slice(5, 10) : ''}{more > 0 ? ` +${more}` : ''}</button>
}

function PayReqDlg({ v, onClose, onChanged, flash }) {
  const [p, setP] = useState(v.period || '')
  const [busy, setBusy] = useState('')
  const pull = force => {
    if (force && !window.confirm('用钉钉请款单里这份账单替换复核台现有的账单？\n现有账单明细会被换掉。')) return
    setBusy('pull')
    reviewPayreqPull(v.inst, force).then(r => { flash(r.msg || '已拉进来'); onChanged() }).catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }
  const assign = () => {
    setBusy('as')
    reviewPayreqAssign(v.inst, p).then(() => { flash(p ? `已归到 ${p}` : '已清空归属月份'); onChanged() }).catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }
  // 不属于物流账单（办公室快递月结等，V2.733）：排除后不进总表格子/待我审批/待认领；可恢复
  const exclude = on => {
    setBusy('ex')
    reviewPayreqExclude(v.inst, on).then(() => { flash(on ? '已排除：不再算进物流复核' : '已恢复'); onChanged() })
      .catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }
  const [bl, bc] = BILL_LB[v.bill_state] || ['账单还没导', 'neu']
  return (
    <div className="fxmask" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="fxdlg prdlg" role="dialog" aria-label="钉钉请款单">
        <div className="fxhead"><b>钉钉请款单</b><span>{v.carrier} · {v.subject}</span><b className="mono">{money(v.amount)}</b><span className="sp" />
          <button className="fxx" onClick={onClose} aria-label="关闭">✕</button></div>
        <div className="fxsub">审批编号 <span className="mono">{v.bid}</span> · {v.applicant} 提交于 {v.created}{v.finished ? ` · 流程结束 ${v.finished}` : ''}</div>
        <div className="prrow"><span className={'dtchip ' + (DT_CLS[v.st.key] || '')}>{v.st.label}{v.st.date ? ' ' + v.st.date : ''}</span>
          {v.cur && v.cur.length > 0 && <span className="dim">当前在办：{v.cur.map(x => x.name).join('、')}</span>}
          {v.paid && <span className="dim">{(v.paid.split('|')[1] || '').startsWith('记') ? `金蝶支付凭证 ${v.paid.split('|')[1]}` : '金蝶付款单'} {v.paid.split('|')[0]}（进金蝶＝已付款）</span>}</div>
        {v.reason && <div className="prreason">{v.reason}</div>}
        {v.ops && v.ops.length > 0 && <table className="fxtbl prtbl"><thead><tr><th>节点</th><th>人</th><th>结果</th><th>时间</th><th>意见</th></tr></thead>
          <tbody>{v.ops.map((o, i) => <tr key={i}><td>{o.type}</td><td>{o.name}</td><td>{o.result}</td><td className="mono">{o.date}</td><td>{o.remark}</td></tr>)}</tbody></table>}
        <div className="prfiles">{ROLE_LB.map(([k, lb]) => {
          const fs = (v.files || []).filter(f => f.role === k)
          return fs.length ? <div key={k}><b>{lb}</b>{fs.map(f => <a key={f.id} href={reviewPayreqFileUrl(v.inst, f.id)}>{f.name}</a>)}</div> : null
        })}</div>
        <div className="prrow"><b>发票</b>{v.folder ? <a href={`#/invdesk?folder=${v.folder}`} target="_blank" rel="noopener">在发票管家打开票夹#{v.folder} ↗</a>
          : <span className="dim">还没进发票管家{v.auto ? '（下一轮扫描会自动拉）' : '（上线前的老单，点下面「拉进来」）'}</span>}</div>
        <div className="prrow"><b>账单</b><span className={'pill ' + bc}>{bl}</span><span className="dim">{v.bill_msg}</span></div>
        <div className="prrow"><b>归属月份</b><input type="month" value={p} onChange={e => setP(e.target.value)} />
          <button className="btn sm" disabled={busy !== '' || p === (v.period || '')} onClick={assign}>保存</button>
          <span className="dim">{v.period ? (PSRC[v.period_src] || '') : '没认出归哪个月：按账单月份选一下'}</span></div>
        {v.excluded && <div className="prrow"><span className="pill neu">已排除</span><span className="dim">{v.excluded}（不进总表、不自动拉发票和账单）</span></div>}
        <div className="prrow">
          {!v.excluded && (!v.folder || !['imported', 'exists', 'nofile'].includes(v.bill_state)) &&
            <button className="btn pri" disabled={busy !== ''} onClick={() => pull(false)}>{busy === 'pull' ? '拉取中…' : '拉进来（发票进发票管家、账单进复核台）'}</button>}
          {v.bill_state === 'exists' && <button className="btn" disabled={busy !== ''} onClick={() => pull(true)}>用这份账单替换复核台现有账单</button>}
          <span style={{ flex: 1 }} />
          {v.excluded
            ? <button className="btn" disabled={busy !== ''} onClick={() => exclude(false)}>恢复：算物流账单</button>
            : <button className="btn" disabled={busy !== ''} onClick={() => exclude(true)} title="如办公室快递月结、非物流计提的请款：排除后不再显示在总表">不属于物流账单（排除）</button>}
        </div>
      </div>
    </div>
  )
}
const STATE_PILL = { miss: ['金蝶查无', 'bad'], qtydiff: ['数量不符', 'warn'], price: ['核价不符', 'bad'], info: [null, 'neu'], ok: ['一致', 'ok'] }
const num = (v, dp = 2) => (v == null || v === '' ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: dp }))
const EMPTY_L = { rows: [], accr_total: 0, bill_total: 0, diff_total: 0, adj: [], points: '', signed: null, n_unexplained: 0 }

// 后端 rows 是「若干笔 + 一行 gtotal」循环；这里切成组，组头用 gtotal 的小计数
function toGroups(rows) {
  const out = []; let cur = []
  for (const r of rows || []) {
    if (r.kind === 'gtotal') { out.push({ head: r, lines: cur }); cur = [] } else cur.push(r)
  }
  if (cur.length) out.push({ head: null, lines: cur })
  return out
}

// 计提更正：应改为(空=不变)的一句话。值存「编码 名称」(改账按编码找，用户 2026-09-30 定)
const FIX_F = [['to_acct', '科目', 'acct'], ['to_fee', '费用项目', 'fee'], ['to_dept', '部门', 'dept'], ['to_biz', '产品分类', 'biz'], ['to_proj', '产品项目', 'proj']]
const FIX_EMPTY = { to_acct: '', to_fee: '', to_dept: '', to_biz: '', to_proj: '', to_amt_tax: '', to_rate: '', to_amt: '', memo: '' }
const amtNum = v => { const n = Number(String(v || '').replace(/[,，\s]/g, '')); return String(v || '').trim() && isFinite(n) ? n : null }
const CLEAR = '（清空）'
const nm = v => (v && v.includes(' ') ? v.slice(v.indexOf(' ') + 1) : v)          // 「编码 名称」只取名称(页面标签用)
const cn = (code, name) => [code, name && name !== code ? name : ''].filter(Boolean).join(' ')
// 金额三项(后端存：含税/不含税两位小数，税率小数 0.09)
const fixAmts = f => [f.to_amt_tax && '金额 ' + money(f.to_amt_tax), f.to_rate && '税率 ' + pct(Number(f.to_rate)), f.to_amt && '不含税 ' + money(f.to_amt)]
const fixShort = f => [...FIX_F.map(([k]) => nm(f[k])), ...fixAmts(f)].filter(Boolean).join(' · ') || (f.memo ? '见原因' : '')
const fixTxt = f => [...FIX_F.map(([k, lb]) => f[k] && lb + ' ' + f[k]), ...fixAmts(f)].filter(Boolean).join(' · ') + (f.memo ? '；原因：' + f.memo : '')
const r2 = x => Math.round(x * 100) / 100
const Cd = ({ c }) => (c ? <span className="cd">{c}</span> : null)                // 金蝶编码小标签(主体/产品分类/产品项目/部门/费用项目)
const nextPeriod = p => { const [y, m] = String(p || '').split('-').map(Number); return y && m ? (m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`) : p }
let DIMOPT = null                                                                  // 金蝶主数据下拉，页面内只拉一次
const loadDimOpt = () => (DIMOPT = DIMOPT || reviewDimOptions().catch(() => { DIMOPT = null; return null }))

// 计提更正弹窗：只改点的那一笔(用户 2026-09-30：同一账单金额下常常只有一笔记错)；只登记，打印交专人去金蝶改
function FixEditor({ at, rows, period, onSave, onCancel }) {
  const accr = rows.filter(r => r.kind === 'accr')
  const me = accr.find(r => r.key === at.key) || {}
  const init = me.fix || {}
  const [f, setF] = useState(Object.fromEntries(Object.keys(FIX_EMPTY).map(k => [k,
    k === 'to_rate' && init[k] ? String(r2(Number(init[k]) * 100)) : (init[k] || '')])))   // 税率页面按百分数填(9)
  const [drv, setDrv] = useState(init.to_amt && !init.to_amt_tax ? 'N' : 'T')   // 金额联动以哪项为准：T 含税 / N 不含税
  const [dim, setDim] = useState(null)
  const [adj, setAdj] = useState(init.adj_period || nextPeriod(period))   // 调账月份：默认归属月份的下个月
  useEffect(() => { loadDimOpt().then(d => setDim(d || { ok: false })) }, [])
  useEffect(() => {
    const esc = e => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', esc); return () => window.removeEventListener('keydown', esc)
  }, [onCancel])
  // 下拉=金蝶主数据「编码 名称」；产品分类/产品项目多一个「（清空）」
  const opts = Object.fromEntries(FIX_F.map(([, , ok]) => [ok,
    [...(ok === 'biz' || ok === 'proj' ? [CLEAR] : []), ...((dim && dim[ok]) || []).map(o => cn(o.code, o.name))]]))
  const orig = { acct: cn(me.acct, me.acct_name), fee: cn(me.fee_code, me.fee), dept: cn(me.dept_code, me.dept),
    biz: cn(me.biz_code, (me.biz || '').startsWith('（') ? '' : me.biz), proj: cn(me.proj_code, me.proj) }
  const bad = FIX_F.filter(([k, , ok]) => f[k].trim() && dim && dim[ok] && dim[ok].length && !opts[ok].includes(f[k].trim())).map(([, lb]) => lb)
  const inHint = nm(f.to_fee.trim()) === '入库运费' && String(me.acct || '').startsWith('6601') && !f.to_acct.trim()
  const any = Object.values(f).some(v => v.trim())
  // 金额联动：含税 = 不含税 ×(1+税率)。改含税→算不含税；改不含税→算含税；改税率→按上次改的那项为准重算(默认保持含税，账单金额是事实)
  const rateOf = v => (amtNum(v) != null ? amtNum(v) / 100 : (me.tax_rate || 0))
  const onTax = v => { setDrv('T'); const t = amtNum(v); setF({ ...f, to_amt_tax: v, to_amt: t != null ? String(r2(t / (1 + rateOf(f.to_rate)))) : '' }) }
  const onNet = v => { setDrv('N'); const n = amtNum(v); setF({ ...f, to_amt: v, to_amt_tax: n != null ? String(r2(n * (1 + rateOf(f.to_rate)))) : '' }) }
  const onRate = v => {
    const r = rateOf(v)
    if (drv === 'N' && amtNum(f.to_amt) != null) return setF({ ...f, to_rate: v, to_amt_tax: String(r2(amtNum(f.to_amt) * (1 + r))) })
    const t = amtNum(f.to_amt_tax) != null ? amtNum(f.to_amt_tax) : me.amt
    if (!String(v).trim() && !f.to_amt_tax.trim()) return setF({ ...f, to_rate: v, to_amt: '' })
    setF({ ...f, to_rate: v, to_amt_tax: String(t), to_amt: String(r2(t / (1 + r))) })
  }
  const amtBad = ['to_amt_tax', 'to_rate', 'to_amt'].some(k => f[k].trim() && amtNum(f[k]) == null)
  const amtDiff = amtNum(f.to_amt_tax) != null ? r2(amtNum(f.to_amt_tax) - (me.amt || 0)) : null
  const newNet = amtNum(f.to_amt), newRate = amtNum(f.to_rate)
  const clean = { ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim()])), adj_period: adj }
  const acctOpt = code => opts.acct.find(o => o.startsWith(code + ' ')) || code
  return (
    <div className="fxmask" onMouseDown={e => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="fxdlg" role="dialog" aria-label="计提更正">
        <div className="fxhead"><b>计提更正</b><span className="mono">{cn(me.book_code, me.subject)} · {me.vno}</span><span className="sp" />
          <button className="fxx" onClick={onCancel} aria-label="关闭">✕</button></div>
        <div className="fxsub">只登记这一笔应改为什么，不改金蝶、不影响本页对账；导出复核表时进《计提更正单》，打印交专人按编码改。</div>
        <div className="fxamt">金额 <b className="mono">{money(me.amt_net)}</b> 不含税 · 含税 <span className="mono">{money(me.amt)}</span>{me.tax_rate != null && <> · 税率 {pct(me.tax_rate)}</>}</div>
        <div className="fxper">费用归属月份 <b className="mono">{period}</b>
          <label>调账月份 <input type="month" value={adj} onChange={e => setAdj(e.target.value || nextPeriod(period))} /></label>
          <span className="dim">{adj === period ? '＝归属月份：直接改原凭证' : '晚于归属月份：在调账月份做调整凭证'}</span></div>
        <table className="fxtbl">
          <thead><tr><th>项目</th><th>金蝶原记账 <small>编码 名称</small></th><th>应改为 <small>（从下拉选，空白＝不变）</small></th></tr></thead>
          <tbody>
            {FIX_F.map(([k, lb, ok]) => <tr key={k}><td>{lb}</td><td className="mono">{orig[ok] || <span className="dim">空</span>}</td>
              <td><input list={'fx-' + ok} value={f[k]} placeholder={dim ? '不变' : '读金蝶主数据中…'} onChange={e => setF({ ...f, [k]: e.target.value })} />
                <datalist id={'fx-' + ok}>{opts[ok].map(o => <option key={o} value={o} />)}</datalist></td></tr>)}
            <tr className="fxsep"><td>金额<small className="dim">含税</small></td><td className="mono">{money(me.amt)}</td>
              <td><input inputMode="decimal" value={f.to_amt_tax} placeholder="不变（部分调走或金额记错时填）" onChange={e => onTax(e.target.value)} /></td></tr>
            <tr><td>税率</td><td className="mono">{me.tax_rate != null ? pct(me.tax_rate) : '—'}</td>
              <td><span className="fxpct"><input inputMode="decimal" value={f.to_rate} placeholder="不变（如 9）" onChange={e => onRate(e.target.value)} />%</span></td></tr>
            <tr><td>不含税金额</td><td className="mono">{money(me.amt_net)}</td>
              <td><input inputMode="decimal" value={f.to_amt} placeholder="不变" onChange={e => onNet(e.target.value)} /></td></tr>
            <tr className="fxsep"><td>原因</td><td colSpan="2"><input value={f.memo} placeholder="写给改账的人，例：这笔是孝感工厂小料入库的装卸费，应记入库运费" onChange={e => setF({ ...f, memo: e.target.value })} /></td></tr>
          </tbody>
        </table>
        {dim && dim.ok === false && <div className="fxhint">金蝶主数据没取到，下拉是空的；可以手填「编码 名称」。</div>}
        {amtBad && <div className="fxhint">金额、税率只能填数字（金额可带千分位逗号，税率填 9 表示 9%）。</div>}
        {!amtBad && newRate != null && Math.abs(newRate / 100 - (me.tax_rate || 0)) >= 0.00005 && newNet != null &&
          <div className="fxhint">税率改为 {r2(newRate)}%：含税 <b className="mono">{money(amtNum(f.to_amt_tax))}</b> ＝ 不含税 <b className="mono">{money(newNet)}</b> ＋ 进项税 <b className="mono">{money(r2(amtNum(f.to_amt_tax) - newNet))}</b>（原进项税 {money(me.tax)}）。</div>}
        {amtDiff != null && Math.abs(amtDiff) >= 0.005 && <div className="fxhint">应改为含税金额比原来{amtDiff > 0 ? '多' : '少'} <b className="mono">{money(Math.abs(amtDiff))}</b>，差额怎么处理请写在原因里（例：其余仍按原维度 / 多提冲回）。</div>}
        {bad.length > 0 && <div className="fxhint">{bad.join('、')} 在金蝶主数据里找不到这个编码，改账的人可能对不上——请从下拉里选。</div>}
        {inHint && <div className="fxhint">入库运费一般不记 6601 销售费用。{String(me.subject || '').includes('孝感') && '孝感历来：工厂/小料入库记 5101 制造费用，代工厂/零售/鲜食入库记 6401 主营业务成本。'}科目要一起改就点：
          <button className="lnk" onClick={() => setF({ ...f, to_acct: acctOpt('5101') })}>5101 制造费用</button>　
          <button className="lnk" onClick={() => setF({ ...f, to_acct: acctOpt('6401') })}>6401 主营业务成本</button></div>}
        <div className="fxbtns">
          {me.fix && <button className="btn sm" onClick={() => onSave([me.key], FIX_EMPTY)}>撤掉这笔更正</button>}
          <span className="sp" />
          <button className="btn sm" onClick={onCancel}>取消</button>
          <button className="btn sm pri" disabled={!any || amtBad} onClick={() => onSave([me.key], clean)}>保存更正</button>
        </div>
      </div>
    </div>)
}

export default function LogisticsReview({ cfg, onPeriod }) {
  const period = `${cfg.year}-${String(cfg.period).padStart(2, '0')}`
  const [carrier, setCarrier] = useState('迅鸽')
  const [group, setGroup] = useState('ex')
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [supq, setSupq] = useState('')
  const [ovf, setOvf] = useState('')             // 总表按复核状态筛
  const [pr, setPr] = useState(null)             // 打开的钉钉请款单
  const [dtBusy, setDtBusy] = useState(false)
  const [ovBusy, setOvBusy] = useState(false)     // 总表「刷新」进行中
  const [open, setOpen] = useState({})            // 逐单：展开的单据号
  const [sel, setSel] = useState({})              // 逐单：勾选的单据(可跨页) key → 统计用快照
  const [dfilt, setDfilt] = useState(null)        // 逐单：从第①步「可逐单」点进来的组 {fsub,ffee,fbiz,label}
  const [mode, setMode] = useState('overview')   // overview 总表 / detail 单承运商三步流
  const [step, setStep] = useState('lines')      // lines / docs / sign
  // 第③步·发票与暂估：这家这月的钉钉请款单 → 发票管家的发票 vs 金蝶计提暂估(进第③步才取，要读金蝶)
  const [inv3, setInv3] = useState(null)
  useEffect(() => {
    if (step !== 'sign' || !carrier || !period) return
    setInv3(null)
    let off = false
    reviewInvoices(carrier, period).then(r => { if (!off) setInv3(r) }).catch(e => { if (!off) setInv3({ err: e.message, blocks: [] }) })
    return () => { off = true }
  }, [step, carrier, period])
  const [ov, setOv] = useState(null)
  const [L, setL] = useState(null)               // 逐笔计提复核结果（异步，金蝶慢）
  const [pts, setPts] = useState('')             // 供应商复核要点（编辑中）
  const [ptsSaved, setPtsSaved] = useState('')   // 已保存值
  const [ptsEdit, setPtsEdit] = useState(false)  // 要点是否展开编辑

  // 总表：承运商 × 主体 计提/付款/差异 + 复核状态。金蝶部分后端缓存30分；返回总表时同账期不清空，后台静默刷新复核状态
  useEffect(() => {
    if (mode !== 'overview') return
    let alive = true
    setOv(o => (o && o.period === period ? o : null))
    reviewOverview(period).then(r => { if (alive) setOv(r) }).catch(() => { if (alive) setOv(o => o || { rows: [], subjects: [] }) })
    return () => { alive = false }
  }, [period, mode])
  useEffect(() => {
    const j = jump.current
    if (!j || !ov || ov.period !== j.period) return
    jump.current = null
    const row = (ov.rows || []).find(r => r.code === j.code)
    if (row && row.has_spec) enterReview(row.short || row.carrier)
  }, [ov])
  // 登记制的登记：把各主体 计提 / 请款 / 差 列给人看一眼，写一句备注就登记(有差异时备注必填)
  const regSign = r => {
    const ls = Object.entries(r.cells || {}).filter(([, c]) => c.accr || c.paid).map(([sj, c]) => `${sj}：计提 ${money(c.accr || 0)} / 请款 ${money(c.paid || 0)} / 差 ${money(c.diff || 0)}`)
    const bad = Object.values(r.cells || {}).some(c => Math.abs(c.diff || 0) >= 0.01)
    const note = window.prompt(`${r.carrier} · ${period} · 登记制（不逐单核价核量）\n\n${ls.join('\n') || '（没有金额）'}\n\n${bad ? '⚠ 计提和请款有差异，请写明原因：' : '备注（可改）：'}`, bad ? '' : '登记制：计提与请款一致')
    if (note === null) return
    if (bad && !note.trim()) { flash('计提和请款有差异，要写原因才能登记'); return }
    reviewSign(r.short || r.carrier, period, note.trim()).then(refreshOv).catch(e => flash('登记失败：' + e.message))
  }
  const refreshOv = () => {
    setOvBusy(true)
    reviewOverview(period, true).then(setOv).catch(e => flash('刷新失败：' + e.message)).finally(() => setOvBusy(false))
  }
  // 「别的月份的账单」芯片：点了切到那个账期，总表回来后直接进那家的复核(V2.822，用户在 8 期找不到诚煜 6 月账单从哪进)
  const jump = useRef(null)
  const enterReview = sc => { setCarrier(sc); setGroup('ex'); setPage(1); setStep('lines'); setPtsEdit(false); setOpen({}); setSel({}); setDfilt(null); setMode('detail') }
  // 第①步「可逐单」→ 第②步只看这一组单据
  const goDocs = od => {
    setDfilt({ fsub: od.fsub, ffee: od.ffee, fbiz: od.fbiz, from: 'lines', label: [od.fsub, od.ffee, od.blabel].filter(Boolean).join(' · ') })
    setGroup('all'); setPage(1); setQ(''); setOpen({}); setStep('docs')
  }
  const clearDfilt = () => { setDfilt(null); setGroup('ex'); setPage(1); setOpen({}) }
  // 第②步下拉筛选(主体/费用类型/产品线)：和「可逐单」点进来共用 dfilt，可与待核/已确认等分组叠加
  const setFacet = (k, v) => {
    const nx = { fsub: '', ffee: '', fbiz: '', ...(dfilt || {}), [k]: v }
    setDfilt(!nx.fsub && !nx.ffee && !nx.fbiz ? null : { fsub: nx.fsub, ffee: nx.ffee, fbiz: nx.fbiz, from: '',
      label: [nx.fsub, nx.ffee, nx.fbiz && nx.fbiz.split('|').map(b => (b === '-' ? '无产品线' : b)).join('、')].filter(Boolean).join(' · ') })
    setPage(1); setOpen({})
  }

  const load = useCallback(() => {
    reviewResult(carrier, period, group, page, q, dfilt).then(setD).catch(e => setMsg(e.message))
  }, [carrier, period, group, page, q, dfilt])
  useEffect(() => { load() }, [load])
  // 逐笔：等主表 d 到了再拉（避免与逐单取数并发争用金蝶会话把主表拖住）；后端带缓存
  const applyL = r => { setL(r); setPts(r.points || ''); setPtsSaved(r.points || '') }
  useEffect(() => {
    if (mode !== 'detail' || !carrier || !d) return
    let alive = true; setL(null)
    const t = setTimeout(() => {
      reviewLines(carrier, period).then(r => { if (alive) applyL(r) }).catch(e => { if (alive) setL({ ...EMPTY_L, err: e.message }) })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [carrier, period, mode, d && d.carrier])
  const flash = t => { setMsg(t); setTimeout(() => setMsg(''), 6000) }
  // 钉钉请款单：重读总表(不重取金蝶，弹窗跟着换新数据) / 手动扫一轮
  const reloadOv = () => reviewOverview(period).then(r => {
    setOv(r)
    setPr(cur => {
      if (!cur) return cur
      const pq = r.payreq || {}
      const all = [...(pq.mine || []), ...(pq.unassigned || []), ...(pq.excluded || []), ...(r.rows || []).flatMap(x => Object.values(x.cells || {}).flatMap(c => c.reqs || []))]
      return all.find(x => x.inst === cur.inst) || null
    })
  }).catch(() => {})
  const scanDt = () => {
    setDtBusy(true)
    reviewPayreqScan().then(r => { flash(r.ok ? `扫完：新增 ${r.new} 张、更新 ${r.updated} 张、配上付款 ${r.paid} 张（${r.sec}s）` : (r.msg || '扫描失败')); reloadOv() })
      .catch(e => flash('扫描失败：' + e.message)).finally(() => setDtBusy(false))
  }
  const ovMatch = r => {
    if (!ovf) return true
    const f = DT_F.find(x => x[0] === ovf)
    return f ? !!f[3](r) : r.status === ovf
  }
  const refetchL = () => { setL(null); reviewLines(carrier, period).then(applyL).catch(e => setL({ ...EMPTY_L, err: e.message })) }
  const onFile = (fn, ...args) => e => {
    const f = e.target.files && e.target.files[0]; e.target.value = ''
    if (!f) return
    // 账单解析回显说人话：这次是哪一份(货主)、读了多少行、本月现在有哪几份账单(一家一月可有几份，只替换同一份)
    const say = r => (r && r.sources
      ? `已解析「${r.bill_src || '账单'}」：明细 ${r.detail} 行、汇总 ${r.accrual} 行${r.skipped && r.skipped.length ? `（跳过 ${r.skipped.length} 张价目/明细表）` : ''}。` +
        `本月现有账单 ${r.sources.length} 份：` + r.sources.map(x => `${x.src} ${money(x.amount)}`).join('；') + '。'
      : JSON.stringify(r))
    setBusy(fn.name); fn(...args, f).then(r => { flash(say(r)); setGroup('ex'); setPage(1); load(); refetchL() })
      .catch(e => flash('失败：' + e.message)).finally(() => setBusy(''))
  }
  const kingdee = () => { setBusy('kd'); reviewKingdeeQty(carrier, period).then(r => { flash(`金蝶出库单 ${r.kd_docs} 单，回填 ${r.filled} 行`); load() }).catch(e => flash('失败：' + e.message)).finally(() => setBusy('')) }
  // 毛重比允许范围(基础设置，一家一档)：账单重量÷金蝶净重 落在范围内算重量一致；留空=默认差 2% 以内
  const editWt = () => {
    const cur = d && d.wt_range
    const lo = window.prompt(`「${carrier}」毛重比下限（账单重量 ÷ 金蝶净重）\n快递/快运含包装、抛重，一般填 1；整车留空（默认差 2% 以内算一致）`, cur ? cur[0] : '')
    if (lo === null) return
    const hi = lo.trim() === '' ? '' : window.prompt('毛重比上限（快递/快运一般填 2）', cur ? cur[1] : '2')
    if (hi === null) return
    reviewWtRange(carrier, lo.trim(), String(hi).trim()).then(() => { flash('毛重比范围已保存，按新范围重判'); load() }).catch(e => flash('保存失败：' + e.message))
  }
  const saveNote = (doc_no, note) => { reviewDocNote(carrier, period, doc_no, note).catch(e => flash('备注保存失败：' + e.message)) }
  // 逐单手改归类(主体/费用类型)→存账单侧覆盖列→逐笔复核按新归类重算
  const saveClass = (doc_no, patch) => {
    reviewDocClassify(carrier, period, doc_no, patch)
      .then(() => { load(); refetchL(); flash('已改归类，逐笔复核重算') })
      .catch(e => flash('归类保存失败：' + e.message))
  }
  // 逐笔差异解释：存后本地回填（含未解释计数），不整表重拉
  const saveLineNote = (key, note) => {
    reviewLineNote(carrier, period, key, note)
      .then(() => setL(l => {
        if (!l) return l
        const rows = l.rows.map(r => (r.key === key ? { ...r, note } : r))
        const n = rows.filter(r => r.kind !== 'gtotal' && r.diff != null && !isZero(r.diff) && !(r.note || '').trim()).length
        return { ...l, rows, n_unexplained: n }
      }))
      .catch(e => flash('差异解释保存失败：' + e.message))
  }
  // 计提更正：只登记"应改为"，不改金蝶、不影响对账；导出《计提更正单》打印交专人改
  const [fixAt, setFixAt] = useState(null)       // 正在编辑更正的行 {key}（弹窗，只改这一笔）
  const saveFix = (keys, fix) => {
    reviewLineFix(carrier, period, keys, fix)
      .then(r => {
        const by = {}; (r.fixes || []).forEach(f => { by[f.key] = f })
        setL(l => l && ({ ...l, fixes: r.fixes || [], rows: l.rows.map(x => (x.kind === 'accr' ? { ...x, fix: by[x.key] || null } : x)) }))
        setFixAt(null)
        flash(Object.values(fix).some(v => v) ? `已登记计提更正 ${keys.length} 笔，导出复核表时单独一页《计提更正单》` : '已撤掉更正')
      })
      .catch(e => flash('更正保存失败：' + e.message))
  }
  // 按主体标复核结论：通过 / 有疑问(写一句疑问)；只改组头，不重读金蝶
  const markSubj = (subject, status, note = '') => {
    reviewSubjectMark(carrier, period, subject, status, note).then(() => {
      setL(l => l && ({ ...l, rows: l.rows.map(x => (x.kind === 'gtotal' && x.subject === subject
        ? { ...x, mark: status ? { status, note, by: '我', at: '' } : null } : x)) }))
      flash(status === 'ok' ? `${subject} 已标通过` : status === 'question' ? `${subject} 已标有疑问` : `${subject} 已撤销标记`)
    }).catch(e => flash('标记失败：' + e.message))
  }
  const askSubj = (subject, old) => {
    const v = window.prompt(`${subject} 还有什么疑问？（会显示在总表上）`, old || '')
    if (v !== null) markSubj(subject, 'question', v.trim())
  }
  const savePts = () => { reviewCarrierPointsSet(carrier, pts).then(() => { setPtsSaved(pts); setPtsEdit(false); flash('复核要点已保存') }).catch(e => flash('保存失败：' + e.message)) }
  const doSign = () => {
    if (!window.confirm(`确认 ${carrier} ${period} 复核通过并登记？登记后当月的归类与备注将锁定（可撤销）。`)) return
    setBusy('sign'); reviewSign(carrier, period).then(() => { flash('已登记复核'); refetchL() }).catch(e => flash('登记失败：' + e.message)).finally(() => setBusy(''))
  }
  const doUnsign = () => {
    if (!window.confirm('撤销本月复核登记？撤销后才能修改归类/备注。')) return
    setBusy('sign'); reviewUnsign(carrier, period).then(() => { flash('已撤销登记'); refetchL() }).catch(e => flash('撤销失败：' + e.message)).finally(() => setBusy(''))
  }

  const locked = !!(L && L.signed)
  const c = (d && d.counts) || {}
  const dc = (d && d.by_box && d.doc_counts) || null        // 逐单按真实核对结果的计数（物料模板承运商）
  const toggle = no => setOpen(o => ({ ...o, [no]: !o[no] }))
  const docs = (d && d.docs) || []
  // 勾选/展开按账单行认(lid)：同一张调拨单账单上常有几行(按车次收费)，按单号认会把几行并成一行、漏算运费。快照存统计要用的量，翻页不丢
  // 逐单页主体/费用类型/产品线带金蝶编码，和第①步逐笔计提同一套(后端 codes，用户 2026-10-01「产品线应该和外面对齐」)
  const cds = (d && d.codes) || {}
  const cdBook = s => (cds.book || {})[s] || ''
  const cdBiz = b => { const m = cds.biz || {}; return m[b] || m[(b || '').toLowerCase()] || '' }
  const cdFee = (s, f) => { const m = cds.fee || {}; return m[s + '|' + f] || m[f] || null }
  const dkey = (x, i) => (x.lid != null ? 'l' + x.lid : x.doc_no ? `${x.doc_no}|${page}|${i}` : `nd|${x.subject}|${x.fee_item}|${x.doc_fee}|${page}|${i}`)
  const snap = x => ({ doc_no: x.doc_no || '', fee: x.doc_fee || 0, kg: x.doc_kg || 0, bill: x.bill_amt, unit: x.bill_unit || '', sales: x.sales, confirmed: !!x.confirmed })
  const toggleSel = (x, i) => setSel(o => { const k = dkey(x, i); const n = { ...o }; if (n[k]) delete n[k]; else n[k] = snap(x); return n })
  const pageAllOn = docs.length > 0 && docs.every((x, i) => sel[dkey(x, i)])
  const pageSomeOn = docs.some((x, i) => sel[dkey(x, i)])
  const togglePage = () => setSel(o => { const n = { ...o }; docs.forEach((x, i) => { const k = dkey(x, i); if (pageAllOn) delete n[k]; else n[k] = snap(x) }); return n })
  const selList = Object.values(sel)
  const selStat = (() => {
    const fee = selList.reduce((a, x) => a + (x.fee || 0), 0)
    const once = (arr, f) => { const m = {}; arr.forEach((x, i) => { m[x.doc_no || '#' + i] = f(x) }); return Object.values(m).reduce((a, v) => a + v, 0) }
    const wk = selList.filter(x => x.kg > 0), kg = once(wk, x => x.kg), feeKg = wk.reduce((a, x) => a + (x.fee || 0), 0)
    const units = [...new Set(selList.map(x => x.unit).filter(Boolean))]
    const billSum = selList.reduce((a, x) => a + (Number(x.bill) || 0), 0)
    const bill = units.length === 1 && billSum > 0 ? billSum : null
    const ws = selList.filter(x => x.sales > 0), sales = once(ws, x => x.sales), feeS = ws.reduce((a, x) => a + (x.fee || 0), 0)
    return { n: selList.length, fee, kg, unitFee: kg > 0 ? feeKg / kg : null, nKg: wk.length, bill, unit: units[0] || '', multiUnit: units.length > 1,
      sales, ratio: sales > 0 ? feeS / sales : null, nSales: ws.length,
      toConfirm: [...new Set(selList.filter(x => x.doc_no && !x.confirmed).map(x => x.doc_no))], toUndo: [...new Set(selList.filter(x => x.doc_no && x.confirmed).map(x => x.doc_no))],
      nConfirmRows: selList.filter(x => x.doc_no && !x.confirmed).length, nUndoRows: selList.filter(x => x.doc_no && x.confirmed).length,
      nNoDoc: selList.filter(x => !x.doc_no).length }
  })()
  useEffect(() => { setSel({}) }, [carrier, period])
  const doConfirm = (nos, on) => {
    if (!nos.length) return
    setBusy('confirm')
    reviewDocConfirm(carrier, period, nos, on).then(r => { flash(on ? `已确认 ${r.n} 张单据` : `已取消确认 ${r.n} 张`); setSel({}); load() })
      .catch(e => flash('操作失败：' + e.message)).finally(() => setBusy(''))
  }
  const allOpen = docs.length > 0 && docs.every((x, i) => open[dkey(x, i)])
  const setAll = v => setOpen(v ? Object.fromEntries(docs.map((x, i) => [dkey(x, i), true])) : {})
  const lrows = (L && L.rows) || []
  // 归类下拉(V2.788)：主体＝固定三家(总览表头那三家)＋本家账单里出现过的；费用类型＝规范六类。原来是带提示的输入框，只会提示当前那个值，选不到别的主体
  const subjOpts = [...new Set([...((ov && ov.subjects) || []), ...lrows.filter(r => r.kind !== 'gtotal').map(r => r.subject)].filter(Boolean))]
  const FEE_CANON = ['出库运费', '入库运费', '退货运费', '仓储费', '研发外购', '搬运费']
  const withCur = (arr, cur) => (cur && !arr.includes(cur) ? [cur, ...arr] : arr)
  const QUEUE = [
    { f: 'miss', sw: 'warn', n: '核量 · 金蝶查无出库单', dd: '账单单号在金蝶未匹配（拆单后缀/未审核）', c: c.miss || 0, u: '笔' },
    { f: 'qtydiff', sw: 'warn', n: '核量 · 账单数量≠金蝶出库数量', dd: '账单件数与金蝶出库数量不符', c: c.qtydiff || 0, u: '笔' },
    { f: 'gap', sw: 'warn', n: '核价 · 价格卡缺口', dd: '空运/快运/自提/同城等合同未覆盖', c: c.gap || 0, u: '笔' },
    { f: 'free', sw: 'neu', n: '核价 · 账单未收费（我方有利）', dd: '标准应收但账单未计，不追', c: c.free || 0, u: '笔' },
    { f: 'pass', sw: 'ok', n: '两轴均通过', dd: '单价＝合同 且 金蝶匹配', c: c.pass || 0, u: '笔' },
  ]
  const stepIdx = STEPS.findIndex(s => s[0] === step)
  const goStep = k => setStep(k)
  const groups = toGroups(lrows)
  // 同一账单金额(合并格)下的几笔共用一个差异：锚点行键 → 差异，让每一行都能标「平」(用户 2026-09-30：平也按行看)
  const ancDiff = {}
  for (const x of lrows || []) if (x.bill != null) ancDiff[x.anc || x.key] = x.diff

  return (
    <div className="lrv">
      <style>{`
      .lrv{--ok:#2E7D57;--warn:#B06A12;--bad:#B23B2E;--neu:#5E6B78;--accent:#1F6E8C;--soft:#E1EEF3;font-size:14px}
      .lrv .head{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin-bottom:14px}
      .lrv .h-title{font-size:18px;font-weight:700}.lrv .h-sub{color:#5E6B78;font-size:12.5px;margin-top:2px}
      .lrv .ovhead{display:flex;align-items:center;gap:10px;padding:11px 15px;border-bottom:1px solid #DCE2E7;font-size:13px;color:#1B2733;flex-wrap:wrap}
      .lrv .ovsub{font-size:12px;color:#8A96A2}
      .lrv .ovtable th.subjgrp{text-align:center;background:var(--soft);color:#0F4A60;border-left:1px solid #DCE2E7}
      .lrv .ovtable th,.lrv .ovtable td{border-right:1px solid #EEF1F0}
      .lrv .ovtable td.ovcar{font-weight:600;white-space:nowrap;position:sticky;left:0;background:#fff}
      .lrv .ovtable td.ovcar .ovcode{font-family:ui-monospace,Consolas,monospace;font-size:10.5px;color:#8A96A2;font-weight:400;margin-top:1px}
      .lrv .ovtable td.paid{color:#5E6B78}
      .lrv .ovtable td.ovst{white-space:normal;min-width:150px;max-width:230px}
      .lrv .ovtable td.diffpos{color:var(--bad);font-weight:600}
      .lrv .ovtable td.diffneg{color:var(--ok)}
      .lrv .nospectag{font-style:normal;font-size:10px;color:var(--warn);background:#F7E9CF;border-radius:4px;padding:0 4px;margin-left:5px}
      .lrv .ovempty{text-align:center;color:#8A96A2;padding:16px}
      .lrv .ovfoot{padding:9px 15px;font-size:11.5px;color:#8A96A2;border-top:1px solid #DCE2E7;line-height:1.6}
      .lrv .btn{font-size:12.5px;padding:6px 12px;border-radius:7px;border:1px solid #DCE2E7;background:#fff;cursor:pointer;display:inline-block;text-decoration:none;color:#1B2733}
      .lrv .btn.pri{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
      .lrv .btn.sm{font-size:12px;padding:4px 10px}
      .lrv .btn[disabled]{opacity:.5;cursor:default}
      .lrv .steps{display:flex;align-items:stretch;background:#fff;border:1px solid #DCE2E7;border-radius:12px;overflow:hidden;margin-bottom:12px}
      .lrv .stepbtn{flex:1;display:flex;align-items:center;gap:10px;padding:11px 16px;border:0;background:#fff;cursor:pointer;font:inherit;font-size:13px;color:#5E6B78;text-align:left;min-width:0}
      .lrv .stepbtn+.stepbtn{border-left:1px solid #DCE2E7}
      .lrv .stepbtn:hover{background:#F7F9F9}
      .lrv .stepbtn .no{width:22px;height:22px;border-radius:50%;border:1px solid #C6D0D6;display:inline-flex;align-items:center;justify-content:center;font-family:ui-monospace,monospace;font-size:12px;flex:none;color:#5E6B78}
      .lrv .stepbtn.on{background:#F5FAFC;color:#1B2733;font-weight:600;box-shadow:inset 0 -3px 0 var(--accent)}
      .lrv .stepbtn.on .no{background:var(--accent);border-color:var(--accent);color:#fff}
      .lrv .stepbtn.done .no{background:#DCEFE4;border-color:#DCEFE4;color:var(--ok)}
      .lrv .stepbtn .st{margin-left:auto;font-size:12px;font-weight:500;white-space:nowrap}
      .lrv .lock{background:#FDF3E2;border:1px solid #F0D9A8;border-radius:10px;padding:8px 14px;font-size:12.5px;color:#6B4E00;margin-bottom:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
      .lrv .sumstrip{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
      .lrv .tile{background:#fff;border:1px solid #DCE2E7;border-radius:12px;padding:11px 16px}
      .lrv .tile .v{font-family:ui-monospace,monospace;font-size:21px;font-weight:600;color:#1B2733}
      .lrv .tile .l{font-size:11px;color:#8A96A2;margin-top:2px}
      .lrv .tile.accent .v{color:var(--accent)}.lrv .tile.ok .v{color:var(--ok)}.lrv .tile.bad .v{color:var(--bad)}.lrv .tile.warn .v{color:var(--warn)}
      .lrv .verdict{background:#fff;border:1px solid #DCE2E7;border-radius:12px;padding:14px 18px;display:grid;grid-template-columns:1.5fr repeat(4,1fr);gap:6px 20px;align-items:end;margin-bottom:12px}
      .lrv .verdict .lead{grid-column:1/-1;color:#5E6B78;font-size:12.5px}
      .lrv .stat .v{font-family:ui-monospace,monospace;font-size:21px;font-weight:600}
      .lrv .stat .l{font-size:11px;color:#8A96A2}
      .lrv .stat.ok .v{color:var(--ok)}.lrv .stat.accent .v{color:var(--accent)}.lrv .stat.warn .v{color:var(--warn)}
      .lrv .card{background:#fff;border:1px solid #DCE2E7;border-radius:12px;overflow:hidden;margin-bottom:12px}
      .lrv .card h3{margin:0;padding:11px 15px;font-size:13px;border-bottom:1px solid #DCE2E7;color:#5E6B78;display:flex;align-items:center;gap:8px}
      .lrv .card h3 .sp{flex:1}
      .lrv .ptsbar{display:flex;gap:12px;align-items:flex-start;padding:10px 15px;font-size:13px}
      .lrv .ptsbar .pin{color:var(--accent);flex:none}
      .lrv .ptsbar .ptxt{flex:1;white-space:pre-wrap;line-height:1.6;color:#1B2733}
      .lrv .ptsbar .ptxt.empty{color:#8A96A2;cursor:pointer}
      .lrv textarea.pts{font:inherit;font-size:13px;width:100%;box-sizing:border-box;border:1px solid #DCE2E7;border-radius:8px;padding:8px 10px;resize:vertical;min-height:64px}
      .lrv textarea.pts:focus{border-color:var(--accent);outline:none}
      .lrv .qbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
      .lrv .qbar-lb{font-size:12.5px;color:#5E6B78;margin-right:2px}
      .lrv .qbar-sum{font-size:12.5px;color:#5E6B78}.lrv .qbar-sum b{font-family:ui-monospace,monospace;font-size:16px;color:var(--accent)}
      .lrv .qchip{display:inline-flex;align-items:center;gap:7px;border:1px solid #DCE2E7;background:#fff;border-radius:10px;padding:7px 12px;cursor:pointer;font:inherit;font-size:13px}
      .lrv .qchip:hover{background:#F7F9F9}
      .lrv .qchip.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset;background:#F5FAFC}
      .lrv .qchip .sw{width:9px;height:9px;border-radius:3px}.lrv .sw.ok{background:var(--ok)}.lrv .sw.warn{background:var(--warn)}.lrv .sw.neu{background:var(--neu)}
      .lrv .qchip .qn{color:#1B2733}.lrv .qchip .qc{font-family:ui-monospace,monospace;font-size:15px}.lrv .qchip small{color:#8A96A2}
      .lrv .qchip.warn.on{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn) inset;background:#FCF6EC}
      .lrv .mtbl td[rowspan]{vertical-align:middle;background:#FBFCFD}
      .lrv .mtbl tr.band td{background:#F6F9FA}.lrv .mtbl tr.band td[rowspan]{background:#EEF4F6}
      .lrv .mtbl tr.docstart td{border-top:2px solid #CBD5DC}
      .lrv .ltbl tr.ghead td{background:#EEF4F6;font-weight:600;color:#1B2733;border-top:2px solid #CBD5DC;padding:8px 10px}
      .lrv .ltbl tr.ghead td.gname::before{content:"";display:inline-block;width:4px;height:13px;background:var(--accent);border-radius:2px;margin-right:8px;vertical-align:-1px}
      .lrv .ltbl tr.ghead td.gsub{font-weight:500;color:#5E6B78;font-size:12px}
      .lrv .ltbl tr.rowbad td{background:#FFF7F5}.lrv .ltbl tr.rowbad td[rowspan]{background:#FFF1EE}
      .lrv .ltbl tr.total td{font-weight:700;border-top:2px solid #CBD5DC;background:#E1EEF3}
      .lrv .ltbl tr.fsep td{border-top:1px dashed #CBD5DC}
      .lrv .notecell{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
      .lrv .fixlnk{font:inherit;font-size:11.5px;color:#7A8791;background:none;border:1px dashed #CBD5DC;border-radius:4px;padding:1px 7px;cursor:pointer;opacity:.5}
      .lrv tr:hover .fixlnk{opacity:1}.lrv .fixlnk:hover{color:#8A5A00;border-color:#D9A441}
      .lrv .fixtag{font:inherit;font-size:12px;background:#FBF0DA;color:#8A5A00;border:1px solid #F0D9A8;border-radius:999px;padding:2px 10px;cursor:pointer;white-space:nowrap;max-width:280px;overflow:hidden;text-overflow:ellipsis}
      .lrv .fixtag[disabled]{cursor:default}
      .lrv tr.rowfix td{background:#FFFBF2}
      .lrv .fchk{color:#8A5A00}.lrv .fchk.bad{color:var(--bad);font-weight:600}.lrv .fchk.man{color:#6B4FA0}
      .lrv select.clsinp{width:auto;min-width:104px;padding:2px 4px;cursor:pointer}.lrv .clsinp.wide{width:300px}
      .lrv .xcls .ovr{color:#6B4FA0;font-size:12px}.lrv .xcls .lk{border:0;background:none;color:#1F6E8C;cursor:pointer;font-size:12px;text-decoration:underline;padding:0}
      .lrv .dtchip{display:inline-block;margin-top:3px;font:inherit;font-size:11px;line-height:1.6;padding:0 8px;border-radius:999px;border:1px solid transparent;cursor:pointer;white-space:nowrap}
      .lrv .dtchip.dtmine{background:#FDE7C8;color:#9A5200;border-color:#F2C27A;font-weight:600}.lrv .dtchip.dtrun{background:#E3EEF8;color:#2F5E8A}.lrv .xmn{font-size:10.5px;color:#5B3FA0;white-space:nowrap}.lrv .dtchip.dtother{background:#F1EAFB;color:#5B3FA0;border-color:#D5C6F0}
      .lrv .dtchip.dtok{background:#E6F4EC;color:#2E7A50}.lrv .dtchip.dtpaid{background:#2E8B57;color:#fff}.lrv .dtchip.dtvoid{background:#EEF0F2;color:#8A96A2;text-decoration:line-through}
      .lrv .prbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;padding:8px 15px;border-bottom:1px solid #E6EBEF;font-size:12.5px;color:#1B2733}
      .lrv .prgrp{display:inline-flex;gap:5px;align-items:center;flex-wrap:wrap}.lrv .prgrp .dtchip{margin-top:0}
      .lrv .fxdlg.prdlg{width:min(760px,100%)}
      .lrv .prrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12.5px}
      .lrv .prrow input{font:inherit;font-size:12.5px;border:1px solid #DCE2E7;border-radius:5px;padding:3px 6px}
      .lrv .prreason{white-space:pre-wrap;background:#F6F8FA;border-radius:6px;padding:8px 10px;font-size:12.5px;color:#1B2733}
      .lrv .prtbl td,.lrv .prtbl th{font-size:12px}
      .lrv .prfiles{display:flex;flex-direction:column;gap:4px;font-size:12.5px}.lrv .prfiles div{display:flex;gap:10px;flex-wrap:wrap}.lrv .prfiles b{min-width:72px}
      .lrv .fxmask{position:fixed;inset:0;background:rgba(20,30,40,.38);display:flex;align-items:center;justify-content:center;z-index:1000;padding:16px}
      .lrv .fxdlg{background:#fff;border-radius:10px;width:min(620px,100%);max-height:90vh;overflow:auto;box-shadow:0 12px 40px rgba(0,0,0,.22);
        padding:16px 18px;display:flex;flex-direction:column;gap:10px;border-top:4px solid #D9A441}
      .lrv .fxhead{font-size:15px;color:#1B2733;display:flex;gap:10px;align-items:center}
      .lrv .fxhead .sp{flex:1}
      .lrv .fxx{font:inherit;background:none;border:none;font-size:16px;color:#7A8791;cursor:pointer;padding:0 4px}
      .lrv .fxsub{font-size:12px;color:#6B7A86}
      .lrv .fxamt{font-size:13px;color:#1B2733}
      .lrv .fxper{font-size:13px;color:#1B2733;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
      .lrv .fxper label{display:flex;gap:6px;align-items:center}
      .lrv .fxper input{font:inherit;font-size:12.5px;border:1px solid #DCE2E7;border-radius:5px;padding:3px 6px}
      .lrv .fxper .dim{font-size:12px}
      .lrv .fxtbl{width:100%;border-collapse:collapse;font-size:13px}
      .lrv .fxtbl th,.lrv .fxtbl td{border:1px solid #DCE2E7;padding:6px 8px;text-align:left;vertical-align:middle}
      .lrv .fxtbl th{background:#E7ECEF;font-weight:600;color:#1B2733}
      .lrv .fxtbl th:last-child{background:#FBF0DA;color:#8A5A00}
      .lrv .fxtbl th small{font-weight:400;color:#8A96A2}
      .lrv .fxtbl td:first-child{width:72px;color:#5E6B78;white-space:nowrap}
      .lrv .fxtbl td:first-child small{display:block;font-size:11px}
      .lrv .fxtbl tr.fxsep td{border-top:2px solid #CBD5DC}
      .lrv .cd{font-family:ui-monospace,Consolas,monospace;font-size:10.5px;color:#6B7A86;background:#EEF2F4;border-radius:3px;padding:0 4px;margin-right:4px;font-weight:400;white-space:nowrap}
      .lrv .fxpct{display:flex;align-items:center;gap:4px;color:#5E6B78}.lrv .fxpct input{width:90px}
      .lrv .fxtbl td:nth-child(2){width:38%}
      .lrv .fxtbl input{font:inherit;width:100%;box-sizing:border-box;border:1px solid #DCE2E7;border-radius:5px;padding:4px 7px}
      .lrv .fxtbl input:focus{border-color:#D9A441;outline:none;background:#FFFCF5}
      .lrv .fxhint{font-size:12px;color:#6B4E00;background:#FDF3E2;border-radius:5px;padding:6px 10px}
      .lrv .lnk{font:inherit;background:none;border:none;color:var(--accent);text-decoration:underline;cursor:pointer;padding:0}
      .lrv .fxbtns{display:flex;gap:8px;align-items:center}.lrv .fxbtns .sp{flex:1}
      .lrv .odbtn{font:inherit;font-size:12px;border-radius:999px;padding:2px 10px;cursor:pointer;border:1px solid transparent;white-space:nowrap}
      .lrv .odbtn.ok{background:#DCEFE4;color:var(--ok)}.lrv .odbtn.ok:hover{border-color:var(--ok)}
      .lrv .odbtn.part{background:#F7E9CF;color:var(--warn)}.lrv .odbtn.part:hover{border-color:var(--warn)}
      .lrv .dfilt{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 15px;background:#F5FAFC;border-bottom:1px solid #DCE2E7;font-size:12.5px;color:#1B2733}
      .lrv .ltbl td.pl{line-height:1.25}
      .lrv .sub{display:block;font-size:11px;color:#8A96A2;margin-top:2px;font-weight:400}
      .lrv .tag{display:inline-block;font-size:10.5px;color:#5E6B78;background:#EEF1F3;border-radius:4px;padding:0 5px;margin-left:5px;vertical-align:1px;font-family:inherit;font-weight:500}
      .lrv .tag.ok{color:var(--ok);background:#DCEFE4}
      .lrv .diffok{color:var(--ok);font-weight:600}.lrv .diffbad{color:var(--bad);font-weight:600}
      .lrv .mono{font-family:ui-monospace,monospace;font-size:12px}
      .lrv .dim{color:#8A96A2}
      .lrv .mtbl th,.lrv .mtbl td{padding:6px 8px}
      .lrv .mtbl small{font-size:10px}
      .lrv .noteinp{font:inherit;font-size:12px;border:1px solid #DCE2E7;border-radius:5px;padding:3px 6px;width:100px}
      .lrv .noteinp.wide{width:250px}
      .lrv .noteinp:focus{border-color:var(--accent);outline:none}
      .lrv .noteinp[disabled],.lrv .clsinp[disabled]{background:#F3F5F6;color:#8A96A2;cursor:not-allowed}
      .lrv .clsinp{font:inherit;font-size:12px;border:1px dashed #C6D0D6;border-radius:5px;padding:2px 5px;width:86px;background:#FbFdFe}
      .lrv .clsinp:hover{border-color:var(--accent)}
      .lrv .clsinp:focus{border-color:var(--accent);border-style:solid;outline:none}
      .lrv details.spec{padding:8px 15px;font-size:11.5px;color:#8A96A2;border-top:1px solid #DCE2E7;line-height:1.7}
      .lrv details.spec summary{cursor:pointer;color:var(--accent);user-select:none}
      .lrv .adjnote{padding:8px 15px;font-size:12px;color:#6B4E00;background:#FDF3E2;border-top:1px solid #F0D9A8}
      .lrv table{border-collapse:collapse;width:100%;font-size:13px}
      .lrv th,.lrv td{padding:7px 11px;text-align:left;border-bottom:1px solid #DCE2E7;white-space:nowrap}
      .lrv th{font-size:11px;color:#8A96A2;background:#F7F9F9}
      .lrv td.num,.lrv th.num{text-align:right;font-family:ui-monospace,monospace}
      .lrv .tw{overflow-x:auto}
      .lrv .pill{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:999px}
      .lrv .pill.ok{background:#DCEFE4;color:var(--ok)}.lrv .pill.warn{background:#F7E9CF;color:var(--warn)}
      .lrv .pill.bad{background:#F8DDD8;color:var(--bad)}.lrv .pill.neu{background:#E7ECEF;color:var(--neu)}
      .lrv .toolbar{display:flex;gap:8px;align-items:center;padding:9px 15px;border-bottom:1px solid #DCE2E7;flex-wrap:wrap}
      .lrv .navbar{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin:4px 0 14px}
      .lrv input[type=search]{font:inherit;font-size:13px;padding:5px 10px;border:1px solid #DCE2E7;border-radius:7px}
      .lrv .msg{background:#FEF7E6;border:1px solid #F0DCA8;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:10px;color:#5C4A00;word-break:break-all}
      .lrv .dq{display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:10px 15px;border-bottom:1px solid #DCE2E7}
      .lrv .dqchip{display:inline-flex;align-items:center;gap:6px;border:1px solid #DCE2E7;background:#fff;border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:12.5px;color:#1B2733}
      .lrv .dqchip:hover{border-color:var(--accent)}
      .lrv .dqchip b{font-family:ui-monospace,monospace;font-size:13px}
      .lrv .dqchip.bad b{color:var(--bad)}.lrv .dqchip.warn b{color:var(--warn)}.lrv .dqchip.ok b{color:var(--ok)}.lrv .dqchip.neu b{color:var(--neu)}
      .lrv .dqchip.on{background:var(--accent);border-color:var(--accent);color:#fff}.lrv .dqchip.on b{color:#fff}
      .lrv .dtbl td{vertical-align:top;padding:8px 10px}
      .lrv .dtbl tr.drow{cursor:pointer}
      .lrv .dtbl tr.drow:hover td{background:#F7FAFB}
      .lrv .dtbl tr.drow.st-miss td{background:#FFF7F5}
      .lrv .dtbl tr.drow.st-qtydiff td{background:#FFFBF2}
      .lrv .dtbl tr.drow.open td{background:#EEF5F8;border-bottom-color:transparent}
      .lrv .dtbl td.caret{color:#8A96A2;width:14px;padding-right:0}
      .lrv .dtbl td.party{max-width:220px;overflow:hidden;text-overflow:ellipsis}
      .lrv small.u{font-size:10px;color:#8A96A2;margin-left:2px}
      .lrv .dtbl tr.xrow td{background:#EEF5F8;padding:0 12px 12px 30px;white-space:normal}
      .lrv .dtbl th.ck,.lrv .dtbl td.ck{width:28px;padding-left:10px;padding-right:0;text-align:center}
      .lrv .dtbl tr.drow.rowok td{background:#F3FAF6}
      .lrv .dtbl tr.drow.rowsel td{background:#EAF3FB}
      .lrv .selbar{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;padding:9px 15px;
        background:#E8F2EC;border-top:1px solid #BFDCCB;border-bottom:1px solid #BFDCCB;font-size:13px;color:#1B2733}
      .lrv .selbar b{color:#1E6B43}.lrv .selbar .sp{flex:1}
      .lrv .fsel{font:inherit;font-size:12.5px;border:1px solid #DCE2E7;border-radius:6px;padding:4px 6px;max-width:170px;background:#fff;color:#1B2733}
      .lrv .fsel.on{border-color:var(--accent);background:#F0F7FA;font-weight:600}
      .lrv .ovtable td.mkok{background:#EAF6EF}.lrv .ovtable td.mkq{background:#FDF3E2}.lrv .ovtable td.mkfix{background:#FBEAE7}
      .lrv .mkdot.fix{color:#B23B2E}.lrv .smkfix{color:#B23B2E;font-weight:600}
      .lrv .ovtable tr.ovsigned td{background:#F1F8F4}
      .lrv .mkdot{display:inline-block;font-size:11px;font-weight:700;margin-right:4px}.lrv .mkdot.ok{color:var(--ok)}.lrv .mkdot.question{color:var(--warn)}
      .lrv .ovchips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:8px 15px;border-bottom:1px solid #E6EBEF}
      .lrv .smk{font-size:12px;font-weight:600;display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap}.lrv .smk.ok{color:var(--ok)}.lrv .smk.q{color:#8A5A00}
      .lrv .smk .lnk{font-weight:400;font-size:12px}
      .lrv .smkbtns{display:inline-flex;gap:6px}.lrv .btn.okb{color:var(--ok);border-color:#BFDCCB}
      .lrv .xfee{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 2px;font-size:12.5px}
      .lrv .xpanel{background:#fff;border:1px solid #DCE2E7;border-radius:10px;overflow:hidden}
      .lrv .xcls{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid #EEF1F3;font-size:12.5px}
      .lrv .xcls label{display:inline-flex;gap:6px;align-items:center;color:#5E6B78}
      .lrv table.mini{font-size:12.5px}.lrv table.mini th{background:#FAFBFC}.lrv table.mini td,.lrv table.mini th{padding:5px 10px}
      .lrv table.mini tr.pack td{color:#8A96A2}
      @media(max-width:900px){.lrv .verdict{grid-template-columns:1fr 1fr}.lrv .sumstrip{grid-template-columns:1fr 1fr}}
      `}</style>

      <div className="head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {mode === 'detail' && <button className="btn" onClick={() => setMode('overview')}>‹ 返回总表</button>}
          <div><div className="h-title">物流账单复核台{mode === 'detail' ? ` · ${carrier}` : ''}</div>
            <div className="h-sub">从计提出发：逐笔计提复核 → 逐单核价核量 → 确认通过·登记已复核</div></div>
        </div>
        <div style={{ flex: 1 }} />
        {mode === 'detail' && <a className="btn pri" href={reviewExportUrl(carrier, period)}>导出复核表</a>}
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
      </div>

      {mode === 'overview' && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="ovhead">
            <div><b>本月有计提的承运商</b>　计提 vs 付款（金蝶 2241 供应商往来，按主体拆）　<span className="ovsub">点「开始复核」进三步流</span></div>
            <div style={{ flex: 1 }} />
            {ov && ov.fetched_at && <span className="ovsub" title="金蝶计提数据缓存30分钟；复核状态、账单应付每次现算">金蝶数据取于 {ov.fetched_at.slice(5)}</span>}
            <button className="btn sm" disabled={ovBusy} onClick={refreshOv} title="重新从金蝶读取计提">{ovBusy ? '刷新中…' : '刷新'}</button>
            <input type="search" placeholder="搜承运商/编码" value={supq} onChange={e => setSupq(e.target.value)} style={{ width: 130 }} />
          </div>
          {ov && ov.rows && ov.rows.length > 0 && <div className="ovchips">
            <button className={'dqchip' + (!ovf ? ' on' : '')} onClick={() => setOvf('')}>全部<b>{ov.rows.length}</b></button>
            {Object.entries(OV_ST).map(([k, [lb, cl]]) => {
              const n = ov.rows.filter(r => r.status === k).length
              return n ? <button key={k} className={'dqchip ' + cl + (ovf === k ? ' on' : '')} onClick={() => setOvf(ovf === k ? '' : k)}>{lb}<b>{n}</b></button> : null
            })}
            {DT_F.map(([k, lb, cl, fn]) => {
              const n = ov.rows.filter(fn).length
              return n ? <button key={k} className={'dqchip ' + cl + (ovf === k ? ' on' : '')} onClick={() => setOvf(ovf === k ? '' : k)}>{lb}<b>{n}</b></button> : null
            })}
            <span className="ovsub" style={{ marginLeft: 8 }}>主体格浅绿＝该主体已通过，浅橙＝有疑问（鼠标停留看疑问）</span>
          </div>}
          {ov && ov.payreq && !ov.payreq.err && <div className="prbar">
            <b>钉钉请款单</b>
            {(ov.payreq.mine || []).length > 0 && <span className="prgrp">待我审批 {ov.payreq.mine.map(v =>
              <button key={v.inst} className="dtchip dtmine" onClick={() => setPr(v)}>{v.carrier}·{v.subject} {money(v.amount)}</button>)}</span>}
            {(ov.payreq.unassigned || []).length > 0 && <span className="prgrp">没认出月份 {ov.payreq.unassigned.slice(0, 8).map(v =>
              <button key={v.inst} className="dtchip dtrun" onClick={() => setPr(v)}>{v.carrier}·{v.subject} {money(v.amount)}</button>)}
              {ov.payreq.unassigned.length > 8 && <span className="dim">等 {ov.payreq.unassigned.length} 张</span>}</span>}
            {(ov.payreq.other || []).length > 0 && <span className="prgrp" title="账单是别的月份、还没登记已复核的请款单：下表按「本月有计提」列承运商，它们不在里面。点一下切到账单那个月去复核">别的月份的账单
              {ov.payreq.other.slice(0, 8).map(v => <button key={v.inst} className="dtchip dtother" title={`${v.payee} · ${v.period} 账单 · 点击去 ${v.period} 期复核这一家`}
                onClick={() => { if (!onPeriod) return; jump.current = { period: v.period, code: v.code }; onPeriod(Number(v.period.slice(0, 4)), Number(v.period.slice(5))) }}>
                {v.carrier}·{v.subject} {money(v.amount)} · {Number(v.period.slice(5))} 月账单{v.has_pay ? ' · 已付款待做账' : ''} · 去复核 →</button>)}
              {ov.payreq.other.length > 8 && <span className="dim">等 {ov.payreq.other.length} 张</span>}</span>}
            {(ov.payreq.excluded || []).length > 0 && <span className="prgrp"><span className="dim">本月已排除</span>{ov.payreq.excluded.map(v =>
              <button key={v.inst} className="dtchip dtvoid" title={v.excluded} onClick={() => setPr(v)}>{v.carrier}·{v.subject} {money(v.amount)}</button>)}</span>}
            <span style={{ flex: 1 }} />
            <span className="ovsub">{ov.payreq.last && ov.payreq.last.at ? `每 20 分钟自动扫；上次 ${ov.payreq.last.at.slice(5)}（${ov.payreq.last.trigger}）` : '还没扫过钉钉'}</span>
            <button className="btn sm" disabled={dtBusy} onClick={scanDt} title="扫钉钉「付款申请（公对公）」：收款方是物流供应商的落到下表各格">{dtBusy ? '扫钉钉中…' : '扫钉钉'}</button>
          </div>}
          <div className="tw"><table className="ovtable">
            <thead>
              <tr><th rowSpan="2">承运商</th>{(ov && ov.subjects || []).map(s => <th key={s} colSpan="3" className="subjgrp">{s}</th>)}<th rowSpan="2">复核状态</th><th rowSpan="2"></th></tr>
              <tr>{(ov && ov.subjects || []).map(s => [<th key={s + 'a'} className="num">计提</th>, <th key={s + 'p'} className="num">付款(复核)</th>, <th key={s + 'd'} className="num">差异</th>])}</tr>
            </thead>
            <tbody>
              {ov === null && <tr><td colSpan="12" className="ovempty">读金蝶计提凭证中…</td></tr>}
              {ov && ov.rows && ov.rows.filter(r => ovMatch(r) && (!supq || (r.carrier || '').includes(supq) || (r.full || '').includes(supq) || (r.code || '').includes(supq))).map(r =>
                <tr key={r.code || r.full} className={r.status === 'signed' ? 'ovsigned' : ''}>
                  <td className="ovcar" title={[r.code, r.full].filter(Boolean).join(' ')}>{r.carrier}{!r.has_spec && <i className="nospectag">未配</i>}{r.code && <div className="ovcode">{r.code}</div>}</td>
                  {ov.subjects.map(s => {
                    const cc = (r.cells && r.cells[s]) || { accr: 0, paid: 0, diff: 0 }
                    const mk = cc.mark, fix = mk && mk.status === 'ok' && Math.abs(cc.diff || 0) >= 0.01
                    const mc = mk ? (fix ? ' mkfix' : mk.status === 'ok' ? ' mkok' : ' mkq') : ''
                    const mt = mk ? (fix ? `${s} 已通过，但计提与复核应付差 ${money(cc.diff)}，计提有误，需红冲更正（${mk.by}）`
                      : mk.status === 'ok' ? `${s} 已通过 · ${mk.by} ${mk.at}` : `${s} 有疑问：${mk.note || ''}（${mk.by}）`) : undefined
                    return [
                      <td key={s + 'a'} className={'num' + mc} title={mt}>{cc.accr ? money(cc.accr) : ''}
                        {(cc.xm || []).map((x, i) => <div key={i} className="xmn" title="付款做账里选定的跨月核销：复核按「一张请款单 + 它核销的计提」看">
                          {x.dir === 'in' ? `含 ${Number(String(x.month).slice(5))} 月 记-${x.vno} ${money(x.amt)}` : `已减 记-${x.vno} ${money(x.amt)} → ${Number(String(x.month).slice(5))} 月账单`}</div>)}</td>,
                      <td key={s + 'p'} className={'num paid' + mc} title={mt}>{cc.paid ? money(cc.paid) : ''}{cc.reqs && <div><DtChip reqs={cc.reqs} onOpen={setPr} /></div>}</td>,
                      <td key={s + 'd'} className={'num ' + (cc.diff > 0.01 ? 'diffpos' : cc.diff < -0.01 ? 'diffneg' : '') + mc} title={mt}>
                        {mk && <span className={'mkdot ' + (fix ? 'fix' : mk.status)}>{fix ? '⚠ 需更正' : mk.status === 'ok' ? '✓' : '?'}</span>}{cc.diff ? money(cc.diff) : ''}</td>
                    ]
                  })}
                  <td className="ovst">{(() => {
                    const pg = r.progress || {}
                    const sub = [r.bill_from && `账单来自钉钉请款 ${(r.bill_from.date || '').slice(5)}`, r.dt && r.dt.n && `钉钉 ${r.dt.paid}/${r.dt.n} 已付款`,
                      pg.subj_n && `${pg.subj_ok || 0}/${pg.subj_n} 主体通过`, pg.subj_fix && `${pg.subj_fix} 个主体计提需红冲更正`, pg.subj_q && `${pg.subj_q} 个有疑问`, pg.docs_ok && `确认 ${pg.docs_ok} 单`,
                      pg.notes && `解释 ${pg.notes} 笔`, pg.fixes && `更正 ${pg.fixes} 笔`].filter(Boolean).join(' · ')
                    const [lb, cl] = OV_ST[r.status] || ['待复核', 'neu']
                    return <>{r.signed ? <span className="pill ok" title={r.signed.signed_at}>已登记 · {r.signed.reviewer}</span> : <span className={'pill ' + cl}>{lb}</span>}
                      {sub && <span className="sub">{sub}</span>}</>
                  })()}</td>
                  <td>{r.register
                    ? (r.signed
                      ? <button className="btn" title="撤销这家这月的登记" onClick={() => { if (window.confirm(`撤销 ${r.carrier} ${period} 的登记？`)) reviewUnsign(r.short || r.carrier, period).then(refreshOv).catch(e => flash('撤销失败：' + e.message)) }}>撤销登记</button>
                      : <button className="btn pri" disabled={r.status === 'noaccr'} title={r.status === 'noaccr' ? '这家这月有请款、金蝶没有计提，先处理计提' : '登记制：没有可逐单核的账单，只核对计提和请款两个数'} onClick={() => regSign(r)}>登记（不逐单）</button>)
                    : <button className="btn pri" disabled={!r.has_spec} title={r.has_spec ? '进逐笔复核' : '该承运商未配取数说明'} onClick={() => enterReview(r.short || r.carrier)}>开始复核</button>}</td>
                </tr>)}
              {ov && ov.rows && !ov.rows.length && <tr><td colSpan="12" className="ovempty">本月金蝶暂无物流计提（2241 供应商往来无「计提…运费/仓储费」贷方）</td></tr>}
            </tbody>
          </table></div>
          {pr && <PayReqDlg key={pr.inst} v={pr} onClose={() => setPr(null)} onChanged={reloadOv} flash={flash} />}
          <div className="ovfoot">付款(复核)下的小标＝钉钉请款单进度（待谁审批 / 已通过·待付款 / 已付款＝金蝶出现付款单），点开看节点和附件。承运商＝金蝶全称。计提＝2241 本期贷方；付款(复核)＝本月该承运商账单复核后应付合计（同期间口径）；差异＝计提−复核应付。复核状态＝该承运商本月是否已「确认通过并登记」。只有已配取数说明的承运商可「开始复核」。</div>
        </div>
      )}

      {mode === 'detail' && (<>
      <div className="steps">
        {STEPS.map(([k, name], i) =>
          <button key={k} className={'stepbtn' + (k === step ? ' on' : '') + (i < stepIdx ? ' done' : '')} onClick={() => goStep(k)}>
            <span className="no">{i < stepIdx ? '✓' : i + 1}</span><span>{name}</span>
            {k === 'lines' && L && !L.err && <span className={'st ' + dcls(L.diff_total)}>{dtxt(L.diff_total)}</span>}
            {k === 'docs' && d && (() => { const n = d.ex_all != null ? d.ex_all : dc ? dc.ex : (c.miss || 0) + (c.qtydiff || 0); return <span className={'st ' + (n ? 'diffbad' : 'diffok')}>{n ? `${n} 张待核` : '无异常'}</span> })()}
            {k === 'sign' && <span className={'st ' + (locked ? 'diffok' : 'dim')}>{locked ? '已登记' : '待登记'}</span>}
          </button>)}
      </div>
      {msg && <div className="msg">{msg}</div>}
      {locked && <div className="lock">🔒 本月已登记复核 · {L.signed.reviewer} · {L.signed.signed_at}　归类与备注已锁定，要修改请先在第③步撤销登记。</div>}

      {step === 'lines' && (<>
        {L && !L.err && (
          <div className="sumstrip">
            <div className="tile accent"><div className="v">{money(L.accr_total)}</div><div className="l">计提合计（含税）</div></div>
            <div className="tile"><div className="v">{money(L.bill_total)}</div><div className="l">账单合计{L.bill_src === 'accrual' ? '（费用项汇总）' : '（逐单汇总）'}{L.od_total && <> · 可逐单 {Math.round(L.od_total.ratio * 100)}%</>}</div></div>
            <div className={'tile ' + (isZero(L.diff_total) ? 'ok' : 'bad')}><div className="v">{dtxt(L.diff_total)}</div><div className="l">差异（计提 − 账单）</div></div>
            <div className={'tile ' + (L.n_unexplained ? 'warn' : 'ok')}><div className="v">{L.n_unexplained || 0}</div><div className="l">有差异、还没写解释（笔）</div></div>
          </div>)}

        <div className="card">
          <h3>复核要点 <span className="dim">· {carrier} 这家怎么核（按重量还是件数、哪些费用不逐单）</span><span className="sp" />
            {!ptsEdit && <button className="btn sm" onClick={() => setPtsEdit(true)}>编辑</button>}</h3>
          {!ptsEdit
            ? <div className="ptsbar"><span className="pin">📌</span>
              <div className={'ptxt' + (ptsSaved ? '' : ' empty')} onClick={() => { if (!ptsSaved) setPtsEdit(true) }}>{ptsSaved || '还没写，点此填写'}</div></div>
            : <div style={{ padding: '10px 15px' }}>
              <textarea className="pts" autoFocus value={pts} onChange={e => setPts(e.target.value)} placeholder={`${carrier} 的复核要点…`} />
              <div style={{ display: 'flex', gap: 8, marginTop: 6, justifyContent: 'flex-end' }}>
                <button className="btn sm" onClick={() => { setPts(ptsSaved); setPtsEdit(false) }}>取消</button>
                <button className="btn sm pri" disabled={pts === ptsSaved} onClick={savePts}>保存</button>
              </div>
            </div>}
        </div>

        <div className="card">
          <h3>逐笔计提复核 <span className="dim">· 按主体分组，组头即小计；每笔下方小字是费用类型；有差异的行请写差异解释</span></h3>
          {L === null && <div className="ovempty">读金蝶计提分录中…</div>}
          {L && L.err && <div className="msg" style={{ margin: 12 }}>逐笔取数失败：{L.err}</div>}
          {L && !L.err && (
            <div className="tw"><table className="mtbl ltbl">
              <thead><tr>
                <th>费用项目 <small className="dim">/ 产品分类 · 产品项目 · 部门（均带金蝶编码）</small></th><th>凭证号</th>
                <th className="num">计提金额<small>含税</small></th><th className="num">账单金额</th><th className="num">差异</th>
                <th title="账单金额里有多少是带金蝶单号的单据撑着的；点一下去第②步只看这一组单据">可逐单</th><th>差异解释</th>
              </tr></thead>
              <tbody>
                {groups.map((g, gi) => {
                  const h = g.head
                  const lines = g.lines
                  return [
                    h && <tr key={'h' + gi} className="ghead">
                      <td className="gname" colSpan="2"><Cd c={h.book_code} />{h.subject}{h.fee_type ? ' · ' + h.fee_type : ''}<span className="tag">{lines.filter(x => x.kind === 'accr').length} 笔</span></td>
                      <td className="num">{money(h.amt)}</td><td className="num">{money(h.bill)}</td>
                      <td className={'num ' + dcls(h.diff)}>{dtxt(h.diff)}</td><td></td>
                      <td className="gsub">{(() => {
                        const mk = h.mark
                        if (mk && mk.status === 'ok') return <span className="smk ok" title={`${mk.by} ${mk.at}`}>✓ 本主体通过 · {mk.by}
                          {!isZero(h.diff) && <span className="smkfix" title="账单核过了，计提与账单还有差，说明计提记错，需红冲更正">⚠ 计提差 {dtxt(h.diff)}，需红冲更正</span>}
                          {!locked && <button className="lnk" onClick={() => markSubj(h.subject, '')}>撤销</button>}</span>
                        if (mk && mk.status === 'question') return <span className="smk q" title={`${mk.by} ${mk.at}`}>? 有疑问：{mk.note || '（未写）'}
                          {!locked && <><button className="lnk" onClick={() => askSubj(h.subject, mk.note)}>改</button><button className="lnk" onClick={() => markSubj(h.subject, 'ok')}>改为通过</button><button className="lnk" onClick={() => markSubj(h.subject, '')}>撤销</button></>}</span>
                        return !locked && <span className="smkbtns"><button className="btn sm okb" onClick={() => markSubj(h.subject, 'ok')}>✓ 本主体通过</button>
                          <button className="btn sm" onClick={() => askSubj(h.subject, '')}>? 有疑问</button></span>
                      })()}</td>
                    </tr>,
                    ...lines.flatMap(r => {
                      const anchor = r.bill != null
                      const bad = anchor && r.diff != null && !isZero(r.diff)
                      const unexpl = bad && !(r.note || '').trim()
                      const gdiff = anchor ? r.diff : ancDiff[r.anc]          // 本行所在账单金额的差异
                      const flat = gdiff != null && isZero(gdiff)
                      // 第一行=费用项目(FYXM编码+金蝶名称)；小字=产品分类 · 产品项目 · 部门，都带金蝶编码(用户 2026-09-30)
                      const bizEl = (r.biz || '').startsWith('（') ? <span className="dim">无产品分类</span> : <><Cd c={r.biz_code} />{r.biz}</>
                      const subline = [<>{bizEl}{r.bill_biz && <span className="dim"> (账单:{r.bill_biz})</span>}</>,
                        r.proj && <><Cd c={r.proj_code} />{r.proj}</>, r.dept && <><Cd c={r.dept_code} />{r.dept}</>]
                        .filter(Boolean).map((x, i) => <React.Fragment key={i}>{i > 0 && ' · '}{x}</React.Fragment>)
                      const accr = r.kind === 'accr'
                      const fixCell = accr && (r.fix
                        ? <button className="fixtag" disabled={locked} onClick={() => setFixAt({ key: r.key })}
                            title={'待更正：改为 ' + fixTxt(r.fix)}>待更正 → {fixShort(r.fix)}</button>
                        : (!locked && <button className="fixlnk" onClick={() => setFixAt({ key: r.key })}
                            title="这笔计提的科目/费用项目/部门/产品分类/产品项目/金额要更正：登记应改为什么，打印交专人去金蝶改（不影响本页对账）">改维度</button>))
                      return [
                        <tr key={r.key} className={(unexpl ? 'rowbad' : '') + (r.ffirst && !r.gfirst ? ' fsep' : '') + (r.fix ? ' rowfix' : '')}>
                          <td className="pl">
                            {r.kind === 'bill_only'
                              ? <><span className="dim">{r.fee_type}</span><span className="sub">{bizEl} · 账单有、计提无</span></>
                              : <>{r.fee_code ? <><Cd c={r.fee_code} />{r.fee}</> : r.fee_type}<span className="sub">{subline}</span></>}
                          </td>
                          <td className="mono">{r.vno || <span className="dim">—</span>}</td>
                          <td className="num">{r.amt == null ? <span className="dim">—</span> : <>{money(r.amt)}{r.tax_rate != null && <span className="tag">{pct(r.tax_rate)}</span>}</>}</td>
                          {anchor && <td rowSpan={r.bill_span || 1} className="num">{money(r.bill)}<span className="tag">{r.level === 'biz' ? '按产品线' : '按组'}</span></td>}
                          {anchor && <td rowSpan={r.bill_span || 1} className={'num ' + dcls(r.diff)}>{dtxt(r.diff)}</td>}
                          {anchor && <td rowSpan={r.bill_span || 1}>{(() => {
                            const od = r.od
                            if (!od) return null
                            if (!od.n) return isZero(r.bill) ? <span className="dim">—</span>
                              : <span className="tag" title="这部分账单没有金蝶单号（仓储、调整、月结汇总等），只能看汇总差异">否 · 汇总核</span>
                            const full = od.ratio >= 0.995
                            const qonly = od.ratio < 0.005
                            return <button className={'odbtn ' + (full ? 'ok' : 'part')} onClick={() => goDocs(od)}
                              title={qonly ? `${od.n} 单有金蝶单号但逐单没有金额（钱在汇总行），逐单只能核数量；点击去第②步只看这一组`
                                : `${od.n} 单带金蝶单号，覆盖账单 ${money(od.amt)}；点击去第②步只看这一组`}>
                              {full ? `✓ ${od.n} 单` : qonly ? `${od.n} 单 · 只核量` : `部分 ${Math.round(od.ratio * 100)}% · ${od.n} 单`} ›</button>
                          })()}</td>}
                          <td><div className="notecell">{bad || (r.note || '').trim()
                            ? <input className="noteinp wide" disabled={locked} defaultValue={r.note || ''} key={r.key + '|' + (r.note || '')}
                              placeholder="为什么差…" onBlur={e => { const v = e.target.value.trim(); if (v !== (r.note || '')) saveLineNote(r.key, v) }} />
                            : flat ? <span className="tag ok">平</span>
                              : (!anchor && gdiff != null ? <span className="dim" style={{ fontSize: 12 }} title="这几笔共用一个账单金额，差异解释写在本组第一笔">差异见本组首笔</span> : null)}{fixCell}</div></td>
                        </tr>
                      ]
                    })
                  ]
                })}
                <tr className="total">
                  <td colSpan="2">合计</td>
                  <td className="num">{money(L.accr_total)}</td><td className="num">{money(L.bill_total)}</td>
                  <td className={'num ' + dcls(L.diff_total)}>{dtxt(L.diff_total)}</td>
                  <td>{L.od_total && <span className="dim" style={{ fontWeight: 500, fontSize: 12 }}>{Math.round(L.od_total.ratio * 100)}%</span>}</td>
                  <td>{L.n_unexplained ? <span className="pill warn">{L.n_unexplained} 笔有差异未解释</span> : <span className="pill ok">差异均已解释</span>}</td>
                </tr>
              </tbody>
            </table></div>)}
          {fixAt && L && L.rows && <FixEditor key={fixAt.key} at={fixAt} rows={L.rows} period={period} onSave={saveFix} onCancel={() => setFixAt(null)} />}
          {L && !L.err && L.fixes && L.fixes.length > 0 && <div className="adjnote fixnote">
            <b>计提待更正 {L.fixes.length} 笔</b>（登记不影响本页对账；导出复核表时单独一页《计提更正单》，打印交专人在金蝶改）：
            {L.fixes.map(f => `${f.snap.vno || ''} ${money(f.snap.amt_net)} → ${fixShort(f)}${f.live ? '' : '（金蝶里原分录已变，可能已改好）'}`).join('；')}
          </div>}
          {L && !L.err && L.prior && L.prior.length > 0 && <div className="adjnote">上期计提的红冲 / 更正（本月做的账，属于上个月）不计入本月，净额 <b className="mono">{money(L.prior_total)}</b>：{L.prior.map(p => `${p.vno} ${p.subject} ${money(p.net)}`).join('；')}</div>}
          {L && !L.err && L.xin && L.xin.length > 0 && <div className="adjnote">跨月核销：这张账单在付款做账里选定同时核销别的月份的计提，已并进下表——
            {L.xin.map(x => `${x.subject} ${Number(String(x.month).slice(5))} 月 ${x.vno} ${money(x.amt)}`).join('；')}</div>}
          {L && !L.err && L.xmoved && L.xmoved.length > 0 && <div className="adjnote">本月有计提已被别的月份的账单选定核销，不计入本月——
            {L.xmoved.map(x => `${x.subject} ${x.vno} ${money(x.amt)} → ${x.to} 的账单`).join('；')}</div>}
          {L && !L.err && L.adj && L.adj.length > 0 && <div className="adjnote">另有 {L.adj.length} 张只有税额调整科目的凭证未计入：{L.adj.map(a => `${a.vno} ${a.acct} ${money(a.amt)}`).join('、')}</div>}
          {L && !L.err && <details className="spec"><summary>口径说明</summary>
            计提＝金蝶费用借方(6*/5*)逐分录，产品线/产品类型/部门取凭证核算维度；税率按凭证（同凭证进项税÷费用，税额按分录精确分摊），计提金额已含税与账单同口径。
            账单先按 主体×费用类型×产品线 配到笔（标"按产品线"），产品线对不上的退回按 主体×费用类型 挂该组首笔（标"按组"，跨行合并）；账单有计提无的单独一行。
            {L.bill_src === 'accrual' ? '账单取费用项汇总行（月结清单口径）。' : '账单取逐单明细汇总。'}仓储费等无单据的费用只在本页看差异、写解释，不进逐单。
            「可逐单」＝这笔账单金额里带金蝶单号的逐单明细占多少（✓ 全覆盖 / 部分 xx% / 否＝只能按汇总核），点一下去第②步只看这一组单据。
            「改维度」＝金蝶计提的科目/费用项目/部门/产品分类/产品项目/金额要更正时登记应改为什么（只登记，不影响本页对账），导出复核表时单独一页《计提更正单》，打印交专人在金蝶改。
          </details>}
        </div>
        <div className="navbar"><button className="btn pri" onClick={() => goStep('docs')}>下一步：逐单核价核量 ›</button></div>
      </>)}

      {step === 'docs' && (<>
        {!(d && d.by_box) && <div className="qbar">
          <span className="qbar-sum"><b>{d ? money(d.total_bill) : '—'}</b> 元 账单合计</span>
          <span className="qbar-lb">· 待处理（点一个筛下方明细）</span>
          {QUEUE.map(g =>
            <button key={g.f} className={'qchip ' + g.sw + (g.f === group ? ' on' : '')} title={g.dd} onClick={() => { setGroup(g.f); setPage(1) }}>
              <span className={'sw ' + g.sw} /><span className="qn">{g.n}</span><b className="qc">{g.c}</b><small>{g.u}</small>
            </button>)}
        </div>}
        <div className="card">
          <h3>逐单核价核量 <span className="dim">· 一单一行，点行展开看物料、改归类；仓储费等无单据的费用在第①步看差异</span></h3>
          {dfilt && dfilt.from === 'lines' && <div className="dfilt">只看：<b>{dfilt.label}</b>（从逐笔计提复核点进来）
            <button className="btn sm" onClick={clearDfilt}>✕ 看全部单据</button>
            <button className="btn sm" onClick={() => setStep('lines')}>‹ 回逐笔计提复核</button></div>}
          {d && d.by_box && dc && <div className="dq">
            {DOC_Q.map(g =>
              <button key={g.f} className={'dqchip ' + g.cls + (g.f === group ? ' on' : '')} title={g.dd}
                onClick={() => { setGroup(g.f); setPage(1); setOpen({}) }}>{g.n}<b>{dc[g.k] || 0}</b></button>)}
            <div style={{ flex: 1 }} />
            <span className="dim" style={{ fontSize: 12 }}>账单合计 <b className="mono" style={{ color: 'var(--accent)', fontSize: 13 }}>{money(d.total_bill)}</b></span>
          </div>}
          <div className="toolbar">
            <span style={{ fontSize: 12.5, color: '#5E6B78' }}>共 <b>{d ? d.detail_total : 0}</b> {d && d.by_box ? '张单据' : '行'}</span>
            {d && d.by_box && docs.length > 0 && <button className="btn sm" onClick={() => setAll(!allOpen)}>{allOpen ? '全部收起' : '全部展开'}</button>}
            {d && d.by_box && d.facets && <>
              {[['fsub', 'subject', '全部主体'], ['ffee', 'fee', '全部费用类型'], ['fbiz', 'biz', '全部产品线']].map(([k, fk, all]) => {
                const cur = (dfilt && dfilt[k]) || ''
                const opts = (d.facets[fk] || []).map(([v, n]) => [fk === 'biz' ? (v || '-') : v, (v || (fk === 'biz' ? '无产品线' : '（空）')) + '（' + n + '）'])
                if (cur && !opts.some(o => o[0] === cur)) opts.unshift([cur, cur.split('|').map(b => (b === '-' ? '无产品线' : b)).join('、')])
                return <select key={k} className={'fsel' + (cur ? ' on' : '')} value={cur} onChange={e => setFacet(k, e.target.value)}>
                  <option value="">{all}</option>{opts.map(([v, lb]) => <option key={v} value={v}>{lb}</option>)}</select>
              })}
              {dfilt && <button className="btn sm" onClick={() => { setDfilt(null); setPage(1); setOpen({}) }}>清空筛选</button>}
            </>}
            <div style={{ flex: 1 }} />
            {d && d.by_box && <button className="btn sm" onClick={editWt} title="按重量核量时，账单重量÷金蝶净重 落在这个范围内算一致。快递/快运含包装、抛重，一般 1～2；整车用默认（差 2% 以内）。一家一档，各月通用">
              毛重比 {d.wt_range ? `${d.wt_range[0]}～${d.wt_range[1]}` : '默认±2%'} ⚙</button>}
            <label className="btn sm">上传账单解析<input type="file" accept=".xlsx,.xls" hidden onChange={onFile(reviewParseBill, carrier, period)} /></label>
            <button className="btn sm" disabled={busy === 'kd'} onClick={kingdee} title="重新从金蝶取出库单物料，刷新核量">{busy === 'kd' ? '金蝶取数中…' : '接金蝶核量'}</button>
            <label className="btn sm">导入价格卡<input type="file" accept=".xlsx,.xls" hidden onChange={onFile(reviewImportPriceCard, carrier)} /></label>
            <input type="search" placeholder="搜单号/客户/物料" value={q} onChange={e => { setQ(e.target.value); setPage(1) }} />
          </div>
          {selStat.n > 0 && <div className="selbar">
            <span>已选 <b>{selStat.n}</b> 张</span>
            <span>运费 <b className="mono">{money(selStat.fee)}</b></span>
            {selStat.bill != null && <span>账单量 <b className="mono">{num(selStat.bill)}</b> {selStat.unit}</span>}
            {selStat.multiUnit && <span className="dim">账单量单位不一，不合计</span>}
            {selStat.kg > 0 && <span>金蝶重量 <b className="mono">{num(selStat.kg)}</b> 千克</span>}
            {selStat.unitFee != null && <span title={selStat.nKg < selStat.n ? `按有金蝶重量的 ${selStat.nKg} 张算` : ''}>单位运费 <b className="mono">{selStat.unitFee.toFixed(2)}</b> 元/千克{selStat.nKg < selStat.n && <small className="dim">（{selStat.nKg} 张）</small>}</span>}
            {selStat.ratio != null && <span title={`按有销售额的 ${selStat.nSales} 张算`}>销售额 <b className="mono">{money(selStat.sales)}</b> · 费比 <b className="mono">{(selStat.ratio * 100).toFixed(2)}%</b>{selStat.nSales < selStat.n && <small className="dim">（{selStat.nSales} 张）</small>}</span>}
            <span className="sp" />
            {selStat.toConfirm.length > 0 && <button className="btn sm pri" disabled={locked || busy === 'confirm'} onClick={() => doConfirm(selStat.toConfirm, true)}>✓ 确认无误（{selStat.nConfirmRows}）</button>}
            {selStat.toUndo.length > 0 && <button className="btn sm" disabled={locked || busy === 'confirm'} onClick={() => doConfirm(selStat.toUndo, false)}>取消确认（{selStat.nUndoRows}）</button>}
            <button className="btn sm" onClick={() => setSel({})}>清空选择</button>
            {selStat.nNoDoc > 0 && <small className="dim" style={{ width: '100%' }}>其中 {selStat.nNoDoc} 张无单据的调整行只参与统计，不打确认。</small>}
          </div>}
          {(() => {     // 没有金蝶单号的账单行：不列成单据，只留一行数。金额为 0 的(迅鸽逐单表里不带钱的仓储行，钱在汇总行)不提
            const nd = ((d && d.nodoc) || []).filter(x => Math.abs(x.amount || 0) >= 0.005)
            return nd.length > 0 && <div className="adjnote">另有 <b>{nd.length}</b> 笔没有金蝶单号、不逐单（合计 {money(nd.reduce((s, x) => s + (x.amount || 0), 0))}）：
              {nd.slice(0, 8).map(x => `${x.subject} ${x.fee_item}${x.bizline ? '·' + x.bizline : ''} ${money(x.amount)}${x.note ? `（${x.note}）` : ''}`).join('；')}{nd.length > 8 ? ' 等' : ''}
              　—— 这些在第①步按汇总核（那边「可逐单」一栏写「否·汇总核」），这里不列。</div>
          })()}
          {d === null && <div className="ovempty">读账单与金蝶出库单中…</div>}
          {d && d.by_box && (docs.length === 0
            ? <div className="ovempty">{group === 'ex' && !q
              ? <>没有待核单据 ✓　<button className="btn sm" onClick={() => { setGroup('all'); setPage(1) }}>看全部单据</button></>
              : '没有符合条件的单据'}</div>
            : <div className="tw">
              <table className="dtbl">
                <thead><tr>
                  <th className="ck"><input type="checkbox" title="全选本页" checked={pageAllOn}
                    ref={el => { if (el) el.indeterminate = !pageAllOn && pageSomeOn }} onChange={togglePage} /></th>
                  <th></th><th>单据号</th><th>主体 · 费用类型</th><th>产品线</th><th>往来（客户/仓库）</th>
                  <th className="num">账单量 <small>/ 金蝶</small></th><th className="num">换算系数</th><th>核对结论</th>
                  <th className="num">运费</th><th className="num">单位运费<small>元/kg</small></th><th className="num">费比<small> / 销售额</small></th><th>备注</th>
                </tr></thead>
                <tbody>
                  {docs.map((x, i) => {
                    const rk = dkey(x, i)
                    const isO = !!open[rk]
                    const on = !!sel[dkey(x, i)]
                    const cf = x.confirmed
                    const [pl, pc] = STATE_PILL[x.state] || [null, 'neu']
                    const qd = x.q_diff
                    const ps = x.parties || []
                    return [
                      <tr key={rk + '|r'} className={'drow st-' + x.state + (isO ? ' open' : '') + (cf ? ' rowok' : '') + (on ? ' rowsel' : '')} onClick={() => toggle(rk)}>
                        <td className="ck" onClick={e => e.stopPropagation()}><input type="checkbox" checked={on} onChange={() => toggleSel(x, i)} /></td>
                        <td className="caret">{isO ? '▾' : '▸'}</td>
                        <td><span className="mono">{x.doc_no || '—'}</span><span className="sub">{x.n_mat ? `${x.n_mat} 个物料` : (x.doc_no ? '金蝶无此单据' : '无单据')}</span></td>
                        <td><Cd c={cdBook(x.subject)} />{x.subject}<span className="sub">{(() => { const f = cdFee(x.subject, x.fee_item); return f ? <><Cd c={f[0]} />{f[1]}</> : x.fee_item })()}</span>
                          {x.fee_chk === 'diff' && <span className="sub fchk bad" title="物流部填的费用类型和系统按金蝶单据判的不一样，请双方核对">物流部填 {x.fee_fill} ≠ 系统判 {x.fee_sys}</span>}
                          {x.fee_chk === 'nofill' && <span className="sub fchk" title="规范第4列「费用类型」每行必填">物流部未填 · 系统判 {x.fee_sys}</span>}
                          {(x.subj_ovr || x.fee_chk === 'manual') && <span className="sub fchk man" title="这张单的归类是人工定的（特批等），以人工定的为准，不再和系统判的比">
                            人工定{x.subj_ovr ? ` · 主体账单原为 ${x.subj_bill}` : ''}{x.fee_chk === 'manual' && x.fee_sys && x.fee_sys !== x.fee_ovr ? ` · 系统判 ${x.fee_sys}` : ''}</span>}
                          {x.ovr_reason && <span className="sub fchk man" title={x.ovr_reason}>原因：{x.ovr_reason}</span>}</td>
                        <td>{x.bizline ? <><Cd c={cdBiz(x.bizline)} />{x.bizline}</> : <span className="dim">—</span>}</td>
                        <td className="party" title={ps.join('\n')}>{ps[0] || <span className="dim">—</span>}{ps.length > 1 && <span className="tag">+{ps.length - 1}</span>}</td>
                        <td className="num">{num(x.bill_amt)}<small className="u">{x.bill_unit}</small>
                          <span className="sub">金蝶 {num(x.kd_sum)}{x.kd_unit}{qd != null && !isZero(qd) && <span className={x.state === 'qtydiff' ? 'diffbad' : ''}> · 差{qd > 0 ? '+' : ''}{num(qd)}</span>}</span></td>
                        <td className="num">{x.conv == null ? '—' : x.conv}</td>
                        <td>{cf
                          ? <><span className="pill ok" title={`确认人 ${cf.by}　${cf.at}`}>✓ 已确认</span><span className="sub">{pl || x.mode_cn} · {cf.by}</span></>
                          : <><span className={'pill ' + pc}>{pl || x.mode_cn}</span>{pl && <span className="sub">{x.mode_cn}</span>}
                            {x.trip && <span className={'sub' + (x.trip.verdict === 'ok' ? '' : ' diffbad')} title={x.trip.msg}>{x.trip.msg}</span>}</>}</td>
                        <td className="num">{money(x.doc_fee)}{x.trips > 1 && <span className="sub" title="同一单号账单上有几行(包天包趟一车一行)，单位运费/费比按本单合计算">共 {x.trips} 行 · 合计 {money(x.doc_fee_all)}</span>}
                          {x.sub_fees && Object.keys(x.sub_fees).filter(k => typeof x.sub_fees[k] === 'number').length > 1 &&
                            <span className="sub" title={'费用构成：' + Object.entries(x.sub_fees).map(([k, v]) => `${k} ${typeof v === 'number' ? money(v) : v}`).join('，')}>
                              {Object.entries(x.sub_fees).filter(([, v]) => typeof v === 'number' && v).map(([k, v]) => `${k.replace('快递费', '快递').replace('操作费', '操作')} ${v}`).join(' + ')}</span>}</td>
                        <td className="num">{x.unit_fee == null ? '—' : x.unit_fee}</td>
                        <td className="num">{x.ratio == null ? '—' : (x.ratio * 100).toFixed(2) + '%'}{x.sales != null && <span className="sub">{money(x.sales)}</span>}</td>
                        <td onClick={e => e.stopPropagation()}><input className="noteinp" disabled={locked} defaultValue={x.note || ''} key={rk + '|n|' + (x.note || '')} placeholder="备注…"
                          onBlur={e => { const v = e.target.value.trim(); if (v !== (x.note || '')) saveNote(x.doc_no, v) }} /></td>
                      </tr>,
                      isO && <tr key={rk + '|x'} className="xrow"><td colSpan="13">
                        <div className="xpanel">
                          {x.doc_no && <div className="xcls">
                            <span className="dim">归类</span>
                            <label>主体 <select className="clsinp" disabled={locked} value={x.subject || ''}
                              onChange={e => saveClass(x.doc_no, { subject: e.target.value })}>
                              {withCur(subjOpts, x.subject).map(s => <option key={s} value={s}>{s}{s === x.subj_bill ? '（账单原值）' : ''}</option>)}</select></label>
                            <label>费用类型 <select className="clsinp" disabled={locked} value={x.fee_item || ''}
                              onChange={e => saveClass(x.doc_no, { fee_item: e.target.value })}>
                              {withCur(FEE_CANON, x.fee_item).map(s => <option key={s} value={s}>{s}{s === x.fee_sys ? '（系统判）' : ''}</option>)}</select></label>
                            <label>原因 <input className="clsinp wide" disabled={locked} defaultValue={x.ovr_reason || ''} key={'r' + x.doc_no + (x.ovr_reason || '')}
                              placeholder="特批/调整的原因，如：经××特批，由深圳星期零承担"
                              onBlur={e => { const v = e.target.value.trim(); if (v !== (x.ovr_reason || '')) saveClass(x.doc_no, { reason: v }) }} /></label>
                            {(x.subj_ovr || x.fee_ovr || x.ovr_reason) && !locked &&
                              <button className="lk" title="清掉人工定的主体、费用类型和原因，回到账单原来的归类" onClick={() => saveClass(x.doc_no, { restore: true })}>恢复账单原值</button>}
                            <span className="dim" style={{ fontSize: 11.5 }}>选完即保存，逐笔复核按新归类重算{x.subj_ovr || x.fee_ovr ? '；重新导入账单也会保留' : ''}</span>
                          </div>}
                          {x.sub_fees && <div className="xfee"><span className="dim">费用构成</span>
                            {Object.entries(x.sub_fees).map(([k, v]) => <span key={k} className="tag">{k} {typeof v === 'number' ? money(v) : v}</span>)}
                            <b className="mono">= {money(x.doc_fee)}</b></div>}
                          {x.n_mat > 0 ? <table className="mini">
                            <thead><tr><th>物料编码</th><th>物料名称</th><th>往来</th><th className="num">基本单位数量</th><th className="num">金蝶数量</th>
                              <th className="num">核对量<small>{x.kd_unit}</small></th><th className="num">分摊运费</th><th className="num">单位运费</th><th className="num">销售额</th><th className="num">费比</th></tr></thead>
                            <tbody>{x.materials.map((m, i) =>
                              <tr key={i} className={m.is_pack ? 'pack' : ''}>
                                <td className="mono">{m.code || '—'}</td>
                                <td>{m.name}{m.is_pack && <span className="tag">包材·不摊运费</span>}</td>
                                <td>{m.party || <span className="dim">—</span>}</td>
                                <td className="num">{num(m.base_kg)}<small className="u">{m.kg_unit}</small></td>
                                <td className="num">{num(m.base_qty)}<small className="u">{m.base_unit}</small></td>
                                <td className="num">{num(m.kd)}</td>
                                <td className="num">{money(m.fee)}</td>
                                <td className="num">{m.unit_fee == null ? '—' : m.unit_fee}</td>
                                <td className="num">{m.sales == null ? '—' : money(m.sales)}</td>
                                <td className="num">{m.ratio == null ? '—' : (m.ratio * 100).toFixed(2) + '%'}</td>
                              </tr>)}</tbody>
                          </table> : <div className="dim" style={{ padding: '10px 12px', fontSize: 12.5 }}>{x.doc_no ? '金蝶里没找到这张单据的物料（单号填错 / 未审核 / 拆单后缀），核对账单登记的金蝶单号。' : '账单里的调整行（无单据），只登记不核量。'}</div>}
                        </div>
                      </td></tr>
                    ]
                  })}
                </tbody>
              </table></div>)}
          {d && !d.by_box && <div className="tw"><table>
            <thead><tr><th>金蝶单号</th><th>快递</th><th>省</th><th className="num">计费kg</th><th className="num">账单数量</th><th className="num">金蝶数量</th><th className="num">账单</th><th className="num">标准</th><th className="num">差</th><th>计价档</th><th>归一态</th></tr></thead>
            <tbody>{(d.detail || []).map((r, i) => {
              const [nm, cl] = PS[r.price_state] || ['—', 'neu']
              const qmk = r.qty_state === 'miss' ? ' ✕' : r.qty_state === 'qtydiff' ? ' ▲' : ''
              return <tr key={i}><td className="mono">{r.doc_no}</td><td>{r.carrier_sub}</td><td>{r.prov}</td>
                <td className="num">{r.charge_wt == null ? '—' : r.charge_wt}</td><td className="num">{r.qty == null ? '—' : r.qty}</td>
                <td className="num" style={{ color: qmk ? 'var(--warn)' : '' }}>{r.kd_qty == null ? '—' : r.kd_qty}{qmk}</td>
                <td className="num">{money(r.amount)}</td><td className="num">{r.std_amount == null ? '—' : money(r.std_amount)}</td>
                <td className="num">{r.price_diff == null ? '?' : money(r.price_diff)}</td><td style={{ color: '#5E6B78', fontSize: 12 }}>{r.tier}</td>
                <td><span className={'pill ' + cl}>{nm}</span></td></tr>
            })}</tbody>
          </table></div>}
          {d && d.detail_total > d.size && <div className="toolbar">
            <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ 上一页</button>
            <span style={{ fontSize: 12.5 }}>第 {page} / {Math.ceil(d.detail_total / d.size)} 页</span>
            <button className="btn" disabled={page >= Math.ceil(d.detail_total / d.size)} onClick={() => setPage(page + 1)}>下一页 ›</button>
          </div>}
        </div>
        <div className="navbar">
          <button className="btn" onClick={() => goStep('lines')}>‹ 上一步：逐笔计提复核</button>
          <button className="btn pri" onClick={() => goStep('sign')}>下一步：确认与登记 ›</button>
        </div>
      </>)}

      {step === 'sign' && (<>
        <div className="card">
          <h3>③ 确认与登记 · {carrier} · {period}</h3>
          {L === null && <div className="ovempty">读逐笔结果中…</div>}
          {L && (<>
            <div className="verdict" style={{ border: 0, borderRadius: 0, marginBottom: 0 }}>
              <div className="lead">逐笔：计提 vs 账单；逐单：核量结果。确认通过后登记，当月归类与备注锁定，总表显示已复核。</div>
              <div className="stat accent"><div className="v">{money(L.accr_total)}</div><div className="l">计提合计（含税）</div></div>
              <div className="stat"><div className="v">{money(L.bill_total)}</div><div className="l">账单合计</div></div>
              <div className={'stat ' + (isZero(L.diff_total) ? 'ok' : 'warn')}><div className="v">{dtxt(L.diff_total)}</div><div className="l">差异（计提−账单）</div></div>
              <div className={'stat ' + (L.n_unexplained ? 'warn' : 'ok')}><div className="v">{L.n_unexplained || 0}</div><div className="l">有差异未写解释（笔）</div></div>
            </div>
            <div className="toolbar" style={{ borderTop: '1px solid #DCE2E7', borderBottom: 0 }}>
              <span style={{ fontSize: 12.5, color: '#5E6B78' }}>{dc
                ? <>逐单：金蝶查无 <b>{dc.miss}</b> · 数量不符 <b>{dc.qtydiff}</b> · 打托/免核 <b>{dc.info}</b> · 一致 <b>{dc.ok}</b>（共 {dc.all} 张）</>
                : <>逐单：金蝶查无 <b>{c.miss || 0}</b> · 数量不符 <b>{c.qtydiff || 0}</b> · 两轴通过 <b>{c.pass || 0}</b></>}</span>
              <div style={{ flex: 1 }} />
              {L.signed
                ? <><span className="pill ok">已复核 · {L.signed.reviewer} · {L.signed.signed_at}</span>
                  <button className="btn" disabled={busy === 'sign'} onClick={doUnsign}>撤销登记</button></>
                : <button className="btn pri" disabled={busy === 'sign'} onClick={doSign}>确认通过并登记已复核</button>}
              <a className="btn" href={reviewExportUrl(carrier, period)}>导出复核表</a>
            </div>
          </>)}
        </div>
        <div className="card">
          <h3 style={{ display: 'flex', alignItems: 'center' }}>发票与暂估 · {carrier} · {period}
            <small style={{ fontWeight: 400, color: '#5E6B78', marginLeft: 10, fontSize: 12 }}>钉钉请款单的发票（发票管家）对金蝶计提的暂估，口径同付款做账</small>
            <span style={{ flex: 1 }} /><a href="#/logisticsvoucher" target="_blank" rel="noopener" style={{ fontSize: 12, fontWeight: 400, color: 'var(--accent)' }}>付款做账 ↗</a></h3>
          <LogisticsInvCompare data={inv3} lines={L} />
        </div>
        <div className="navbar"><button className="btn" onClick={() => goStep('docs')}>‹ 上一步：逐单核价核量</button></div>
      </>)}
      </>)}
    </div>
  )
}
