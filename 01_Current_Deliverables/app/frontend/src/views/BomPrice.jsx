// [Change Log] Date:2026-09-02 Author:Claude/c Version:V-draft(BOM报价审核)
// 【BOM报价审核】前端：钉钉「BOM表报价」审批附件→解析→复核→定稿→BP消费。
// 三视图（台账列表 / 采购核算表详情 / 版本对比）+ 手工入账弹窗。样机布局与交互照搬，皮肤换本项目令牌。
// 含税五分项口径（元/kg），涨跌红▲绿▼（中国财务惯例），编辑态改费用参数保存留痕。
import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import {
  getBomConfig, getBomLedger, getBomEntry, bomFetchApproval, bomUpload, bomBook,
  bomReview, bomFinalize, bomUnfinalize, bomUnfinalRequest, bomUnfinalReview, bomExportPrettyUrl, bomAttachBomList, bomSetUpstream, bomSetName,
  bomStdImportTemplateUrl, bomStdImportUpload, getBomStdImportBatches, getBomStdImportBatch, bomStdImportConfirm, bomStdImportDiscard, bomOutboxRedo,
  getBomOutboxStatus,
  getBomKdPurchase, getBomMaterialUsage, bomConfirmStep, bomApplyGoods, getBomSettings, setBomSettings,
  getBomApproval, bomReplaceSheet, bomRefetchReplace, getBomCommentFiles, bomReplaceFromComment, bomClassify, getBomPending, bomAlignUpstream,
  bomIntake, bomFinalReview, bomVoidRequest, bomVoidReview, bomSetMatType, bomSetErpCode, getBomUsageSpreads, getBomErpLookup, bomLinkParallel, getBomKdBom, bomDelete,
  getBomInvoiceRules, setBomInvoiceRules, getBomDeliverStatus,
} from '../api.js'

// BOM 报价审核工具里的【只读】送达状态面板（V2.534）：通道通不通 + 会发给谁 + 触发规则；改在门户管理（联系管理员）。
function BomDeliverPanel() {
  const [s, setS] = useState(null)
  useEffect(() => { getBomDeliverStatus().then(setS).catch(() => {}) }, [])
  if (!s || !s.ok) return null
  const alive = s.alive
  const ago = s.ago_sec == null ? '' : s.ago_sec < 90 ? '刚刚' : s.ago_sec < 3600 ? Math.round(s.ago_sec / 60) + ' 分钟前' : Math.round(s.ago_sec / 3600) + ' 小时前'
  const col = alive ? 'var(--green)' : 'var(--ink-3)'
  // 压成一行（业务方定 2026-09-09：原大块占位太多）：状态灯 + 谁收 + 上次；触发规则/改法收进悬停提示
  const tip = ['核算表自动送达（只读，改请联系管理员 · 门户管理 · 取件机监控）',
    '什么情况发：成本会计「初审通过」→ 核算表自动落公盘 → 立即钉钉推送给收件人',
    '怎么发：同一产品「财务版+脱敏版」只通知一次；一轮多份合并成一条',
    '通道由常开的报表取件机负责' + (alive ? '' : '；它最近报平安 ' + (s.at || '—') + (ago ? '（' + ago + '）' : ''))].join(String.fromCharCode(10))
  return (
    <div className="card" title={tip} style={{ borderLeft: '3px solid ' + col, padding: '8px 14px', fontSize: 12.5, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <span className={alive ? 'nav-pulse' : ''} style={{ width: 9, height: 9, borderRadius: '50%', background: col, display: 'inline-block', flex: '0 0 auto' }} />
      <b style={{ fontSize: 13 }}>核算表自动送达</b>
      <span style={{ color: col, fontWeight: 600 }}>{alive ? '通道运行中' : '⚠ 通道可能中断'}</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>发给 {s.count ? <b>{s.mobiles_masked.join('、')}</b> : <span style={{ color: 'var(--ink-3)' }}>未配置（暂不推送）</span>}</span>
      {s.last && s.last.at && <><span style={{ color: 'var(--ink-3)' }}>·</span><span style={{ color: 'var(--ink-3)' }}>上次 {s.last.at} 推 {s.last.n} 项</span></>}
      {s.dingtalk_configured === false && <span style={{ color: 'var(--red)' }}>· ⚠ 服务器未配钉钉，暂发不出</span>}
      <span style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--ink-3)', cursor: 'help' }}>ⓘ 规则/改法</span>
    </div>
  )
}

const GROSS = 1.13
// 发票类型 → 单位成本不含税（镜像后端 kernel，对应采购核算表 N 列公式）。price=含税价 tax=税率 rate=扣除率
function invoiceUnitExcl(price, tax, mode, rate) {
  const p = +price || 0, t = +tax || 0, r = +rate || 0
  if (mode === '全额') return p                                   // 普票：不抵扣全额
  if (mode === '买价扣除') return p * (1 - r)                      // 自产自销农产品：价×(1−扣除率)
  if (mode === '农产品专票') return t > 0.01 ? (p - p / (1 + t) * r) : (p / (1 + t))
  return (1 + t) ? p / (1 + t) : p                               // 价税分离（专票，默认）
}
function invoiceCostExcl(qty, price, tax, invoiceType, rules) {
  const rule = (rules || []).find(x => (x.type || '') === (invoiceType || ''))
  return Math.round(invoiceUnitExcl(price, tax, rule ? rule.mode : '价税分离', rule ? rule.rate : 0) * (+qty || 0) * 1e4) / 1e4
}
const EPS = 0.005
const fmt = (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })
const pct = (v) => (v == null || isNaN(v)) ? '—' : (v * 100).toFixed(1) + '%'
const CH = { ecom: '电商', common: '通品', tob: 'TOB', toc: 'TOC' }
const SRC_LABEL = { dingtalk_form: '钉钉·表单附件', dingtalk_comment: '评论区附件', manual_upload: '手工上传' }
// 来源方（谁出的这份数据）：研发BOM/采购商务版/成本会计商品版/手工/评论区。颜色分色，一眼分角色。
const ORIGIN_STY = {
  research: { txt: '研发BOM', cls: 'bom-org-research' },
  procurement: { txt: '采购商务版', cls: 'bom-org-proc' },
  finance: { txt: '财务复核版', cls: 'bom-org-fin' },
  costacct: { txt: '成本会计商品版', cls: 'bom-org-cost' },
  manual: { txt: '手工上传', cls: 'bom-org-manual' },
  comment: { txt: '评论区上传', cls: 'bom-org-manual' },
}
function Origin({ o, small }) {
  const s = ORIGIN_STY[o]
  if (!s) return <span className="muted">—</span>
  return <span className={'bom-org ' + s.cls + (small ? ' sm' : '')}>{s.txt}</span>
}
// 全流程两个戳（业务方定 2026-09-04）：成本会计**初审** → 财务BP**终审（已审核，对外开放）**
const STATUS = {
  '未复核': { cls: 'werr', txt: '未复核' }, '已复核': { cls: 'kd', txt: '已复核' },
  '初审': { cls: 'late', txt: '初审·待终审' }, '已审核': { cls: 'ok', txt: '已审核' },
  '已定稿': { cls: 'ok', txt: '已定稿' },
}
const clean = (s) => (s || '').trim()
const modelSpec = (m) => [m.model, m.spec].filter(x => x && x !== '0').join(' / ')
// 分类标签：成品(蓝)/半成品(紫)/复配料(紫红)。编码规律 SZ→复配料、CP2→半成品、CP0→成品（后端 classify）。
// 原料/包材两表列宽统一（table-layout:fixed + 同一 colgroup）→ 两个框列对齐。
// 类型/编码/物料名/型号/单位/添加量/含税采购价/税率/发票类型/成本含税/占比/说明/核价。
const MAT_COLS = ['7%', '9%', '17%', '9%', '4%', '8%', '8%', '5%', '6%', '8%', '5%', '9%', '5%']
const MAT_COLS_EDIT = ['7%', '9%', '15%', '8%', '4%', '7%', '7%', '7%', '10%', '8%', '4%', '8%', '6%']
// 单行不转行 + 超出省略号（品牌/型号等长文本；全文进 title 悬浮看）
const NOWRAP = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }
// 复核四个页签（业务方定 2026-09-03）：①BOM清单 ②工艺流程 ③用量自洽 ④报价核算。
// ⚠ ①②**只看不确认**（研发给的参考材料，不是会计要签字的判断，业务方定 2026-09-04）；
//    要签字的只有 ③④。③④确认 + 审核定性 ＝ 定稿。
const STEP_DEFS = [['bom', 'BOM清单', false], ['craft', '工艺流程', false],
  ['qty', '用量自洽', true], ['price', '报价核算', true]]
const STEP_NO = ['①', '②', '③', '④']
const CONFIRM_STEPS = STEP_DEFS.filter(s => s[2])
function Kind({ k }) {
  if (k === '复配料') return <span className="tag" style={{ color: 'var(--purple)', background: 'var(--purple-bg)', border: '1px solid var(--purple-line)' }}>复配料</span>
  if (k === '半成品') return <span className="tag late">半成品</span>
  return <span className="tag kd">成品</span>
}
// 物料类别（审核定性，业务方 2026-09-03）：编码规律不固定 → 编码只给建议值，最终由成本会计在审核弹窗指定。
const MAT_CATS = ['复配料', '自产半成品', '自产成品', '委外半成品', '委外成品']
// 物料分类五色（V2.522）：复配料青 · 自产半成品蓝 · 自产成品绿 · 委外半成品紫 · 委外成品琥珀
const CAT_CLS = { '复配料': 'c-fp', '自产半成品': 'c-zb', '自产成品': 'c-zc', '委外半成品': 'c-wb', '委外成品': 'c-wc' }
const catCls = (cat) => 'bom-cat ' + (CAT_CLS[cat] || (cat && cat.startsWith('委外') ? 'out' : ''))
// 类别展示：已定性→显示类别（委外标灰底）；未定性→显示「建议·X」+待定性；名字与编码打架→⚠
function CatCell({ p }) {
  const doubt = p.kindDoubt ? <span className="bom-kdoubt"
    title={`疑似分类不符：按编码判「${p.kindAuto}」，但产品名像「${p.productName && p.productName.includes('半成品') ? '半成品' : '复配料'}」——请在审核弹窗定性`}>⚠</span> : null
  if (!p.matCategory) return <><span className="bom-catsug" title={'编码建议值，未定性。定稿前须在「审核」弹窗指定'}>建议·{p.kindAuto}</span>{doubt}</>
  return <><span className={catCls(p.matCategory)} title={p.outsourced ? '委外（代工厂生产）' : '自产'}>{p.matCategory}</span>
    {p.quotable === false && <span className="bom-noquote" title={'不建议对外报价：' + p.quoteReason}>禁报价</span>}</>
}

// ============ 勾稽不平·下钻明细（点未入账那行进来看「到底哪儿不对」）============
// 逐料列出该产品明细，把**没被计进申报小计的那几味料**标红——一眼看出小计公式漏到哪、好退回上游改。
function PendingDetailModal({ groupId, product, onClose, flash }) {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  useEffect(() => {
    (async () => {
      try { const r = await getBomPending(groupId, product.productKey); if (!r.ok) flash(r.msg || '打开失败'); else setD(r) }
      catch (e) { flash('打开失败：' + e.message) } setLoading(false)
    })()
  }, [groupId, product.productKey])
  const p = d?.product
  const missSet = new Set(p?.missingNames || [])
  const seg = (name) => (p?.materials || []).filter(m => m.seg === name)
  const sumOf = (name) => Math.round(seg(name).reduce((s, m) => s + (m.costExcl || 0), 0) * 1e4) / 1e4
  const block = (name, declared) => {
    const rows = seg(name)
    if (!rows.length) return null
    const sum = sumOf(name)
    const bad = Math.abs(sum - (declared || 0)) > 0.01
    let run = 0, cut = -1
    rows.forEach((m, i) => { run += (m.costExcl || 0); if (cut < 0 && Math.abs(run - (declared || 0)) < 0.01) cut = i })
    return (
      <div style={{ marginBottom: 14 }}>
        <div className="bom-secthead" style={{ padding: '0 0 6px', border: 'none' }}>
          <b style={{ fontSize: 12.5 }}>{name}明细（{rows.length} 味）</b>
          <span style={{ flex: 1 }} />
          {bad ? <span className="tag leak">申报小计 {fmt(declared, 4)} ≠ 逐料Σ {fmt(sum, 4)}（差 {fmt(sum - (declared || 0), 4)}）</span>
            : <span className="tag ok">小计相符 {fmt(sum, 4)}</span>}
        </div>
        <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
          <th className="th" style={{ width: 34 }}>#</th><th className="th">物料编码</th><th className="th">物料名称</th>
          <th className="th" style={{ textAlign: 'right' }}>添加量</th><th className="th" style={{ textAlign: 'right' }}>含税价</th>
          <th className="th" style={{ textAlign: 'right' }}>税率</th><th className="th" style={{ textAlign: 'right' }}>成本不含税</th>
          <th className="th">采购核算表「小计」算它了吗</th>
        </tr></thead><tbody>
          {rows.map((m, i) => {
            const missed = missSet.has(m.matName) || (cut >= 0 && i > cut)
            return (<tr key={i} className={missed ? 'bom-nbrow' : ''}>
              <td className="muted">{i + 1}</td>
              <td className="mono sub">{m.matCode || '—'}</td>
              <td style={{ fontWeight: 600 }}>{m.matName}</td>
              <td className="num">{(m.qtyPerKg ?? 0).toFixed(6)}</td>
              <td className="num">{fmt(m.priceIncl)}</td>
              <td className="num muted">{m.taxRate != null ? (m.taxRate * 100).toFixed(0) + '%' : '—'}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(m.costExcl, 4)}</td>
              <td>{missed ? <span className="tag leak">✗ 没算它</span> : <span className="muted" style={{ fontSize: 11 }}>✓ 算了</span>}</td>
            </tr>)
          })}
          {cut >= 0 && cut < rows.length - 1 && <tr className="bom-subrow"><td colSpan={6}>采购核算表的「小计」公式实际只加到第 {cut + 1} 味为止 → 后面 {rows.length - cut - 1} 味白填了</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(declared, 4)}</td><td /></tr>}
        </tbody></table></div>
      </div>)
  }
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(980px,100%)' }}>
        <div className="bom-mhead"><b>勾稽不平明细 · {product.productName}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        {loading && <div className="loading" style={{ padding: 24 }}>解析源文件中…</div>}
        {!loading && !p && <div className="banner err">打不开该产品明细。</div>}
        {p && <>
          <div className="bom-msub">编码 <b className="mono">{p.cpCode || '—'}</b>　·　页 {p.sheet}　·　核算日期 {p.calcDate}　·　工厂 {p.supplier || '—'}
            <br />源文件：{d.srcFile}</div>
          {/* 讲白：不是「研发有、采购核算表没有」（那是③用量自洽报的），是采购核算表**自己**的小计公式漏加了自己的料 */}
          <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 8, fontSize: 12 }}>
            ⓘ <b>这些料在采购核算表里是有的</b>（下表能看到它们的添加量和价格），<b>只是采购核算表自己的「小计」公式没把它们加进去</b>——
            就像 Excel 里 <span className="mono">=SUM(H7:H16)</span> 之后又往下加了几行料，公式没跟着往下拉。
            <br />⚠ 这个偏低的小计会<b>一路往下传</b>（变动成本→成本合计→含税→全成本），所以<b>该产品全成本是少算的</b>。
            <span className="muted">（若是「研发BOM有、采购核算表没有」那种缺料，会在③用量自洽里报「采购核算表缺料」，不是这里。）</span>
          </div>
          {(p.failedChecks || []).map((c, i) => (
            <div key={i} className="bom-chkfail" style={{ marginBottom: 8 }}>
              <b>✗ {c.check}</b>：采购核算表写的小计 <b>{fmt(c.a, 4)}</b> ≠ 逐料相加 <b>{fmt(c.b, 4)}</b>（少算 {fmt(Math.abs(c.diff || 0), 4)} 元/kg 不含税，折含税约 {fmt(Math.abs((c.diff || 0) * GROSS), 4)}）
              {(c.missing || []).length > 0 && <span>　— <b>没被加进小计的是：{c.missing.map(m => m.matName).join('、')}</b></span>}
            </div>))}
          <div style={{ maxHeight: '52vh', overflowY: 'auto', marginTop: 6 }}>
            {block('原料', p.matSubtotal)}
            {block('包材', p.packSubtotal)}
          </div>
          <div className="bom-chkfail" style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', borderColor: 'var(--line)' }}>
            <b>怎么改</b>：把上面标红「未计入」的料并进源表「小计」公式的求和范围（多半是底部新增料时没往下拉），
            让研发/工厂改好后重传 → 回处理页用「⟳重连钉钉替换 / ⬆上传替换采购核算表」补入本组。
          </div>
          <div className="bom-mfoot"><button className="btn-sec" onClick={onClose}>关闭</button></div>
        </>}
      </div>
    </div>
  )
}

// ============ 审核定性弹窗（点「审核」弹出）============
// ①物料类别五选一 ②是否建议/允许对外报价——不建议**必须写原因**（如包材不全、XX物料暂定）。定稿前置。
function AuditModal({ entry: entry0, onClose, onDone, flash }) {
  // entry 可在弹窗内被更新（采用金蝶物料编码后后端回新视图，含新的换码候选）
  const [entry, setEntry] = useState(entry0)
  const [cat, setCat] = useState(entry.matCategory || entry.catSuggest || '')
  const [q, setQ] = useState(entry.quotable === null || entry.quotable === undefined ? null : entry.quotable)
  const [reason, setReason] = useState(entry.quoteReason || '')
  const [busy, setBusy] = useState(false)
  const subRef = React.useRef(false)     // 里面套着「核对/对比」子弹窗时，Esc 只关子弹窗
  useEffect(() => { const h = (e) => { if (e.key === 'Escape' && !subRef.current) onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  const PRESET = ['包材不全', '物料暂定价', '配方未定版', '缺半成品采购核算表', '勾稽存疑']
  const missing = CONFIRM_STEPS.filter(([k]) => !entry.steps?.[k]).map(([, l]) => l)
  // 换码承接（业务方定 2026-09-05）：同CP再核算 / 不同CP同物料编码 → 定稿前必须答「原来那个是否失效」
  const cands = entry.obsoleteCandidates || []
  // 三个答案：'replace'=A 原版失效（本版替代）/ 'parallel'=B 并行但关联（都对外）/ 'historical'=C 补录历史版（只审不替代、不动指针、不对外）。null 未答→只存定性
  const [obs, setObs] = useState(null)
  const willFinalize = missing.length === 0
  // 本版核算日期早于已有审核版 → 多半是补录历史单，推荐 C
  const olderThanExisting = cands.some(c => c.calcDate && entry.calcDate && entry.calcDate < c.calcDate)
  const importedCand = cands.length > 0 && cands.every(c => c.imported)   // 候选全是导入的无明细行（V2.514）→ 本版是来补明细的，建议答 A
  // 无物料编码 → 到金蝶物料档案按 CP 反查（业务方 2026-09-05 定：检测到就提示确认）。只提示不拦：未中试的本来没编码。
  const [erpLk, setErpLk] = useState(null)
  const [erpBusy, setErpBusy] = useState(false)
  useEffect(() => { if (!entry.erpCode) getBomErpLookup(entry.id).then(setErpLk).catch(e => setErpLk({ offline: true, msg: e.message })) }, [entry.id, entry.erpCode])
  const adoptErp = async (code) => {
    setErpBusy(true)
    try { const r = await adoptErpCode(entry.id, code, flash); if (r) { flash('物料编码已采用 ' + code); setEntry(r.entry); setObs(null); setCmpFirst(null) } }
    catch (e) { flash('更新失败：' + e.message) } finally { setErpBusy(false) }
  }
  const [cmpFirst, setCmpFirst] = useState(null)   // 「对比 ›」弹窗：和哪条换码候选逐料对比
  subRef.current = !!cmpFirst
  const save = async () => {
    if (!cat) return flash('请选择物料类别')
    if (q === null) return flash('请选择是否建议对外报价')
    if (!q && !reason.trim()) return flash('不建议对外报价时必须写明原因')
    if (willFinalize && cands.length > 0 && obs === null) return flash('请先回答：原版失效 / 并行但关联 / 补录历史版？')
    setBusy(true)
    try {
      const r = await bomClassify(entry.id, cat, q, reason.trim(), obs === 'replace', obs === 'parallel', obs === 'historical')
      if (!r.ok) flash(r.msg || '保存失败')
      else {
        flash(r.finalized
          ? (r.historical
            ? `已按历史版补审：${cat}——不替代当前版、不对外，同单的下游可以定稿了`
            : `${r.backfillSealed ? '补录单初审通过·已盖「补录」戳定稿（不经财务BP终审）' : '已定稿'}${r.outbox ? (r.outbox.ok ? '　· 已落盘公盘（财务版+脱敏版）' : (r.outbox.skip ? '　· 半成品/复配料不单独落盘（随成品带出）' : `　· ⚠ 落盘公盘失败：${r.outbox.msg || ''}`)) : ''}：${cat} · ${q ? '建议报价' : '不建议报价'}${(r.obsoleted || []).length ? `　· 原版 ${r.obsoleted.map(c => c.cpCode).join('、')} 已失效` : ''}${(r.linked || []).length ? `　· 与 ${r.linked.map(c => c.cpCode).join('、')} 并行关联，都对外` : ''}　${r.affectedPricing?.note || ''}`)
          : (r.needConfirm || []).length
            ? `已存定性，未定稿：原版本 ${r.needConfirm.map(c => c.cpCode).join('、')} 保留为当前版，请先核对再定稿`
            : `已存定性，但还缺：${(r.missingSteps || []).join('、')}——补齐后自动可定稿`)
        onDone(r.entry)
      }
    } catch (e) { flash('保存失败：' + e.message) } finally { setBusy(false) }
  }
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(620px,100%)' }}>
        <div className="bom-mhead"><b>审核通过 · {entry.productName}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">编码 <b className="mono">{entry.cpCode}</b>　·　生产工厂 {entry.supplier || '—'}
          {entry.kindDoubt && <span style={{ color: 'var(--amber)' }}>　⚠ 按编码判「{entry.kindAuto}」但产品名不符，请据实指定</span>}</div>
        {missing.length > 0
          ? <div className="banner err" style={{ marginBottom: 10 }}>⚠ 还有 {missing.length} 步未确认：<b>{missing.join('、')}</b>。可以先存定性，但要 ③④ 都确认了才会定稿。</div>
          : <div className="banner" style={{ background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-line)', marginBottom: 10 }}>
            ✓ ③用量自洽、④报价核算 均已确认——保存定性即<b>定稿</b>，毕业进标准成本台账。</div>}
        {!entry.erpCode && erpLk && !erpLk.offline && (erpLk.candidates || []).length > 0 &&
          <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--ink)', border: '1px solid var(--amber-line)', marginBottom: 10 }}>
            <ErpCandidates lk={erpLk} onAdopt={adoptErp} busy={erpBusy} onCompare={(id) => setCmpFirst(id)} />
            <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>本记录尚无物料编码。建议先采用再定稿（BP 按物料编码关联）；不强制——未中试的产品可无编码定稿。</div>
          </div>}
        {/* .banner 默认是横向 flex，这里内容多行 → display:block 分三段：说明 / 候选清单 / 问句+两个按钮 */}
        {willFinalize && cands.length > 0 && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10, lineHeight: 1.6 }}>
          <div><b>⚠ 台账里已有 {cands.length} 个同CP / 同物料编码的审核版本</b>。请判断它和本版的关系：<b>A 新旧版</b>——原版失效、退出对外，引用它的 BP 定价收到「成本已更新」（终审通过那一刻切换）；<b>B 并行版本</b>——同一产品的不同版本/包装（如火腿片各版、印刷袋 vs 空白袋），都对外、互不替代，串成一组；<b>C 补录历史版</b>——本版比现有版本更早，只审不替代、不动当前对外版，让同单的半成品/成品能定稿。
            {olderThanExisting && !importedCand && <span style={{ color: 'var(--red)', fontWeight: 600 }}>　本版核算日期 {entry.calcDate} 早于已有版本——多半是补录历史单，建议答 C；答 A 会让新版失效、老成本对外。</span>}
            {importedCand && <span style={{ color: 'var(--accent)', fontWeight: 600 }}>　台账里那版是导入的无明细记录，本版带明细——建议答 A，让导入行退出、本版顶上（历史留痕；若两边全成本不同，以本版为准对外）。</span>}</div>
          <div style={{ margin: '8px 0', padding: '6px 10px', background: 'rgba(255,255,255,.55)', borderRadius: 8 }}>{cands.map(c => (
            <div key={c.entryId} style={{ fontSize: 12, display: 'flex', flexWrap: 'wrap', gap: '2px 12px', alignItems: 'baseline', color: 'var(--ink)' }}>
              <b className="mono">{c.cpCode}</b><span>{c.productName}</span>
              {c.imported && <span className="bom-gvtag">导入·无明细</span>}
              {c.erpCode && <span className="mono muted">物料编码 {c.erpCode}</span>}
              <span className="muted">{c.why}</span>
              <span className="muted">核算 {c.calcDate || '—'} · {c.status} {c.auditAt || ''}</span>
              <span>全成本 <b>¥{fmt(c.fullIncl)}</b>/kg</span>
              <a className="lk" onClick={() => setCmpFirst(c.entryId)} title="两张采购核算表逐料对比用量与价格，再决定是新旧版还是两个产品">对比 ›</a>
            </div>))}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>原来的版本是否失效？</b>
            <div className="bom-catpick">
              <button className={obs === 'replace' ? 'on no' : ''} onClick={() => setObs('replace')} style={importedCand && !obs ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined} title="本版替代原版：原版退出对外台账，BP 收到成本更新提示">A 是，原版失效</button>
              <button className={obs === 'parallel' ? 'on ok' : ''} onClick={() => setObs('parallel')} title="两条是同一产品的并行版本（不同 CP / 不同包装），都对外、互不替代；台账标「并行」并串成一组">B 否，并行但关联</button>
              <button className={obs === 'historical' ? 'on' : ''} onClick={() => setObs('historical')} style={olderThanExisting && !importedCand && !obs ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
                title="本版是更早的历史版本：只盖初审戳、不替代现有版本、不动定稿指针、不对外，让同单的半成品/成品能定稿">C 补录历史版（只审不替代）</button>
            </div>
            <span className="muted" style={{ fontSize: 11 }}>拿不准先点「对比 ›」看两张采购核算表差在哪；不答则只存定性、不定稿。</span>
          </div>
        </div>}

        <div className="bom-mstep"><span className="bom-mno">1</span><div style={{ flex: 1 }}>
          <b>物料类别</b>
          <div className="muted" style={{ fontSize: 12, margin: '3px 0 8px' }}>编码规律不固定，以此人工指定为准（决定是否作原料进上层、是否单独挂渠道）。系统建议：<b>{entry.catSuggest}</b></div>
          <div className="bom-catpick">{MAT_CATS.map(c => (
            <button key={c} className={cat === c ? 'on' : ''} onClick={() => setCat(c)}>{c}</button>))}</div>
        </div></div>

        <div className="bom-mstep"><span className="bom-mno">2</span><div style={{ flex: 1 }}>
          <b>是否建议 / 允许对外报价</b>
          <div className="muted" style={{ fontSize: 12, margin: '3px 0 8px' }}>不建议的会标「禁报价」并把原因带给 BP 定价侧，避免拿不完整的成本对外报价。</div>
          <div className="bom-catpick">
            <button className={q === true ? 'on ok' : ''} onClick={() => setQ(true)}>✓ 建议报价</button>
            <button className={q === false ? 'on no' : ''} onClick={() => setQ(false)}>✕ 不建议报价</button>
          </div>
          {q === false && <div style={{ marginTop: 10 }}>
            <b style={{ fontSize: 12 }}>原因（必填）</b>
            {/* 函数式更新：连点多个标签时不会因闭包拿到旧 state 而丢掉前面选的 */}
            <div className="bom-catpick" style={{ margin: '6px 0' }}>{PRESET.map(t => (
              <button key={t} className="sm" onClick={() => setReason(r => (r && r.trim()) ? r + '；' + t : t)}>+{t}</button>))}</div>
            <textarea className="bom-ta" rows={2} value={reason} onChange={e => setReason(e.target.value)}
              placeholder="如：包材不全；XX物料暂定价，待正式报价后重核" />
          </div>}
        </div></div>

        <div className="bom-mfoot">
          <button className="btn-sec" onClick={onClose}>取消</button>
          <button className="btn-pri" disabled={busy} onClick={save}
            style={(willFinalize && (cands.length === 0 || obs)) ? { background: 'var(--green)', borderColor: 'var(--green)' } : undefined}>
            {busy ? '保存中…' : ((willFinalize && (cands.length === 0 || obs)) ? '审核通过' : '仅保存定性')}</button>
        </div>
      </div>
      {cmpFirst && <CompareEntriesModal entry={entry} lk={erpLk} onAdopt={adoptErp} flash={flash} onClose={() => setCmpFirst(null)}
        canLink onLinked={(en) => { setEntry(en); setObs(null) }}
        others={[...cands.filter(c => c.entryId === cmpFirst), ...cands.filter(c => c.entryId !== cmpFirst)].map(c => ({ entryId: c.entryId, cpCode: c.cpCode, productName: c.productName, status: c.status }))} />}
    </div>
  )
}

export default function BomPrice({ user, mode = 'std' }) {
  if (mode === 'config') return <BomConfig />
  return <BomLedgerView user={user} mode={mode} />
}

function BomLedgerView({ user, mode = 'std' }) {
  const [cfg, setCfg] = useState(null)
  const [data, setData] = useState(null)       // {rows, all, finals}
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [view, setView] = useState('list')     // list | approval(处理页) | detail | compare
  const [curId, setCurId] = useState(null)      // 当前详情 entry id
  const [entry, setEntry] = useState(null)      // 详情完整数据
  const [toast, setToast] = useState('')
  const [manual, setManual] = useState(false)
  const [stdImp, setStdImp] = useState(null)      // 导入标准成本弹窗（V2.512）：{} 新建 / {batchId} 处理已有批次
  const [curAppr, setCurAppr] = useState('')    // 当前处理的钉钉单号
  const [finalRow, setFinalRow] = useState(null)   // 财务BP终审弹窗目标
  // 主管理员密钥删除（V2.459）：{target:{entryId|groupId+approvalNo|approvalNo}, label, after} —— 待办/处理页/详情三处入口共用一个弹窗
  const [delM, setDelM] = useState(null)
  const isSuper = user?.role === 'admin'

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [c, d] = await Promise.all([getBomConfig(), getBomLedger(mode)])
      setCfg(c); setData(d)
    } catch (e) { setErr('加载失败：' + e.message) }
    setLoading(false)
  }, [mode])
  useEffect(() => { load() }, [load])
  const flash = (t) => { setToast(t); setTimeout(() => setToast(''), 2600) }

  const openDetail = useCallback(async (id) => {
    try { const r = await getBomEntry(id); setEntry(r.entry); setCurId(id); setView('detail'); window.scrollTo(0, 0) }
    catch (e) { flash('打开失败：' + e.message) }
  }, [])
  const [cmpFromList, setCmpFromList] = useState(false)     // 版本对比从哪进的：台账左上角（V2.698）→ 两边自选 CP、返回台账；详情/深链 → 预选本条、返回详情
  const openCompare = () => { setCmpFromList(false); setView('compare'); window.scrollTo(0, 0) }
  const openComparePick = () => { setCmpFromList(true); setView('compare'); window.scrollTo(0, 0) }   // 台账左上角：两边自己挑 CP
  // 深链（V2.442，BP 只读台账 → 核算）：#/bomstd?entry=17[&compare=1] 直开详情/对比；?entry=17&final=1 直开终审弹窗（无终审权限则开详情）。
  // 只消费一次，消费后把 hash 收回到 #/bomstd，刷新不再重放。
  const deepRef = React.useRef(false)
  useEffect(() => {
    if (deepRef.current || !data) return
    const h = window.location.hash || ''
    const m = h.match(/^#\/(bomstd|bomdraft)\?(.+)$/)
    if (!m) return
    deepRef.current = true
    const p = new URLSearchParams(m[2])
    const id = parseInt(p.get('entry') || '', 10)
    ;(async () => {
      try {
        if (id) {
          if (p.get('final') === '1' && data.canFinalReview) {
            const row = (data.rows || []).find(r => r.id === id)
            if (row && row.needFinalReview) { setFinalRow(row); return }
          }
          const r = await getBomEntry(id)
          setEntry(r.entry); setCurId(id); setView(p.get('compare') === '1' ? 'compare' : 'detail'); window.scrollTo(0, 0)
        }
      } catch (e) { flash('打开失败：' + e.message) }
      finally { try { window.history.replaceState(null, '', '#/' + m[1]) } catch { /* 忽略 */ } }
    })()
  }, [data])
  const backToList = () => { setView('list'); load() }
  const openApproval = (no) => { setCurAppr(no); setView('approval'); window.scrollTo(0, 0) }
  // 从处理页点进产品详情后，返回要回处理页（不是回列表）
  const backFromDetail = () => { if (curAppr) { setView('approval') } else { backToList() } }

  if (loading) return <div className="body"><div className="loading">加载中…</div></div>
  if (err) return <div className="body"><div className="banner err">{err}</div></div>

  return (
    <div className="bomv">
      {view === 'list' && <Ledger data={data} cfg={cfg} mode={mode} onOpen={openDetail} onCompare={openComparePick} onManual={() => setManual(true)} onStdImport={(b) => setStdImp(b || {})}
        onApproval={openApproval} onRefresh={load} flash={flash}
        onFinalReview={data?.canFinalReview ? setFinalRow : null}
        isSuper={isSuper} onDelete={(target, label) => setDelM({ target, label, after: load })} />}
      {view === 'approval' && <ApprovalView no={curAppr} cfg={cfg} onBack={() => { setCurAppr(''); backToList() }}
        onOpen={openDetail} flash={flash}
        isSuper={isSuper} onDelete={(target, label, after) => setDelM({ target, label, after })} />}
      {view === 'detail' && entry && <Detail entry={entry} all={data.all} cfg={cfg} mode={mode} onBack={backFromDetail}
        onFill={(e) => setManual({ approvalNo: e.approval || '', historical: true })}
        onOpen={openDetail} onCompare={openCompare} onChanged={async () => { const r = await getBomEntry(curId); setEntry(r.entry); load() }}
        onFinalReview={data?.canFinalReview ? setFinalRow : null}
        flash={flash}
        isSuper={isSuper} onDelete={(target, label) => setDelM({ target, label, after: backFromDetail })} />}
      {delM && <DeleteModal target={delM.target} label={delM.label} flash={flash} onClose={() => setDelM(null)}
        onDone={async () => { const f = delM.after; setDelM(null); if (f) await f() }} />}
      {view === 'compare' && (cmpFromList || entry) && <Compare key={cmpFromList ? 'pick' : 'e' + (entry && entry.id)} entry={cmpFromList ? null : entry} all={data.all}
        backLabel={cmpFromList ? '返回台账' : '返回采购核算表'} onBack={() => (cmpFromList ? backToList() : setView('detail'))} flash={flash} />}
      {stdImp && <StdImportModal cfg={cfg} init={stdImp} onClose={() => setStdImp(null)} flash={flash} onDone={load} />}
      {manual && <IntakeModal cfg={cfg} init={typeof manual === 'object' ? manual : null} onClose={() => setManual(false)} flash={flash}
        onDone={(no) => { setManual(false); load(); openApproval(no) }} />}
      {finalRow && <FinalReviewModal row={finalRow} onClose={() => setFinalRow(null)}
        onDone={async () => { setFinalRow(null); load()
          if (view === 'detail' && curId) { try { const r = await getBomEntry(curId); setEntry(r.entry) } catch { /* 详情刷新失败不挡 */ } } }} flash={flash} />}
      {toast && <div className="bom-toast">{toast}</div>}
    </div>
  )
}

// ============ 台账列表 ============
function Ledger({ data, cfg, mode, onOpen, onCompare, onManual, onStdImport, onApproval, onFinalReview, onRefresh, flash, isSuper, onDelete }) {
  // 历史标准成本导入待确认批次（V2.512）：待办页顶部一块，点「处理」进导入弹窗
  const [impBatches, setImpBatches] = useState([])
  useEffect(() => { if (mode !== 'std') getBomStdImportBatches().then(r => setImpBatches(r.ok ? (r.batches || []) : [])).catch(() => {}) }, [mode, data])
  const isStd = mode === 'std'
  const [ftype, setFtype] = useState('all')     // all | fin | semi
  const [fch, setFch] = useState('all')         // all | ecom | common | tob | toc
  const [q, setQ] = useState('')
  const [showObs, setShowObs] = useState(false) // 换码承接：已失效（被已终审新版替代）的旧版默认收起
  const [onlyFinal, setOnlyFinal] = useState(false)   // 2026-10-04：点「待我终审」数字卡 → 只看已初审待终审的行
  const [showHist, setShowHist] = useState(false) // 历史版（答 C / 历史补录）：已归档不对外，默认收起
  const histRows = data.hist || []
  const rows = (data.rows || []).concat(showHist ? histRows : [])
  const versionsOf = (pk) => (data.all || []).filter(x => x.productKey === pk)
  const isDead = (r) => !!(r.obsoleteBy && r.obsoleteBy.live)
  const deadCount = rows.filter(isDead).length

  const shown = rows.filter(r => {
    if (isStd && !showObs && isDead(r)) return false
    if (isStd && onlyFinal && !r.needFinalReview) return false
    if (ftype !== 'all' && (r.kind || '成品') !== ftype) return false
    if (fch !== 'all' && r.channel !== fch) return false
    if (q.trim()) { const s = [r.cpCode, r.productName, r.customer, r.erpCode, r.approval].filter(Boolean).join('').toLowerCase(); if (!s.includes(q.trim().toLowerCase())) return false }   // V2.502：物料编码、钉钉单号也能搜
    return true
  })
  const stats = useMemo(() => {
    const semi = rows.filter(r => r.semi).length
    const dates = rows.map(r => r.calcDate).filter(Boolean).sort()
    const approvals = new Set(rows.map(r => r.approval).filter(Boolean))
    const allV = data.all || []
    const okChecks = allV.filter(e => (e.checks || []).length >= 6 && e.checks.every(c => c.ok)).length
    const finalized = rows.filter(r => r.isFinal).length
    return { total: rows.length, semi, last: dates.length ? dates[dates.length - 1] : '—',
      approvals: approvals.size, versions: allV.length, okChecks, finalized }
  }, [rows, data.all])

  // 待办＝按钉钉单号立项（后端 _approval_summ 汇总组数/产品数/进度），搜索按单号或产品名过滤。
  // 业务方定 2026-09-05：这页只管到**初审结束**——一单的产品全部初审（且无待修）＝成本会计任务完成，从待办消失（可切「显示已完成」回看）。
  const [showDone, setShowDone] = useState(false)
  const isDone = (a) => (a.pending || 0) === 0 && (a.blocked || 0) === 0
  const allAppr = data.approvals || []
  const doneCount = allAppr.filter(isDone).length
  const apprRows = allAppr.filter(a => {
    if (!showDone && isDone(a)) return false
    if (!q.trim()) return true
    const s = (a.approvalNo + ' ' + (a.products || []).map(p => p.productName + p.cpCode).join(' ')).toLowerCase()
    return s.includes(q.trim().toLowerCase())
  })
  const openAppr = allAppr.filter(a => !isDone(a))
  // 审核日期 = 定稿/审核通过日期（终审 ack.at 优先，其次初审 finalizedAt，再次复核 reviewedAt）——只取日期段
  const auditDate = (r) => (((r.ack && r.ack.at) || r.finalizedAt || r.reviewedAt || '').slice(0, 10)) || '—'
  // 补物料编码：成本会计在台账行手工补/改产品 ERP 物料编码（写库+留痕，只动标识不动成本）
  const fillErp = async (r) => {
    const cur = (r.erpCode || '').trim()
    // 先到金蝶物料档案按 CP 反查「研发编码」→ 把候选写进提示、默认值填第一个正式码（只提示，成本会计改/确认才写）
    let hint = ''
    let dflt = cur
    try {
      const lk = await getBomErpLookup(r.id)
      const cs = (lk && lk.candidates) || []
      if (lk && lk.offline) hint = '\n（金蝶未连接，无法反查）'
      else if (!cs.length) hint = '\n（金蝶物料档案未登此 CP——未中试/未建档？）'
      else {
        hint = '\n金蝶物料档案登了此 CP' + (cs[0].exact ? '' : '（前缀近似）') + '：\n' +
          cs.map(c => `  ${c.erpCode}  ${c.name}${c.spec ? ' · ' + c.spec : ''}${c.forbidden ? '（已禁用）' : ''}${(c.inLedger || []).length ? '（台账已挂 ' + c.inLedger.map(x => x.cpCode).join('、') + '）' : ''}`).join('\n')
        if (!cur) dflt = cs[0].erpCode
      }
    } catch { /* 反查失败不挡手填 */ }
    const v = window.prompt(`补 / 改 ERP物料编码\n产品：${r.productName}（${r.cpCode}）${hint}`, dflt)
    if (v == null) return
    const code = v.trim()
    if (code === cur) return
    const res = await adoptErpCode(r.id, code, flash)
    if (res) { flash && flash('物料编码已更新'); onRefresh && onRefresh() }
  }
  const renderRow = (r) => {
    const nver = versionsOf(r.productKey).length
    const dot = (v, src) => Math.abs((v || 0) - (src || 0)) > 1e-9
    const dead = isDead(r)
    return (
      <tr key={r.id} className="row" onClick={() => onOpen(r.id)} title="查看采购核算表" style={dead ? { opacity: 0.55 } : undefined}>
        <td style={{ whiteSpace: 'nowrap' }}>{r.matCategory
          ? <span className={catCls(r.matCategory)} title={r.outsourced ? '委外（代工厂生产）' : '自产'}>{r.matCategory}</span>
          : <span className="bom-catsug" title="编码建议值，未定性">建议·{r.kindAuto}</span>}</td>
        <td className="mono sub">{r.erpCode || <span className="muted">—</span>}</td>
        <td className="mono" style={{ fontWeight: 600 }}>{r.cpCode}</td>
        <td style={{ fontWeight: 600 }}>{r.productName}
          {r.quotable === false && <span className="bom-noquote" title={'不建议对外报价：' + r.quoteReason}>禁报价</span>}
          {r.historical && <span className="tag late" style={{ marginLeft: 6 }} title="历史版：审核时答 C 归档的老版本，已审但不对外、不占定稿指针">历史版·不对外</span>}
          {r.backfill && <span className="bom-gvtag" style={{ marginLeft: 6 }} title={r.status === '已审核' ? '历史补录：成本会计初审通过即定稿，终审戳为「历史补录」，未经财务BP二道审核' : '历史补录单：照常复核、成本会计初审；初审通过即盖「补录」戳定稿'}>{r.status === '已审核' ? '补录·无二审' : '补录·待初审'}</span>}
          {r.imported && <span className="bom-gvtag" style={{ marginLeft: 6 }} title="历史标准成本直接导入：只有五分项、无物料明细；成本会计批量确认即已审核，未经财务BP终审；无采购核算表可导出">导入·无明细</span>}
          {r.unfinalPending && <span className="tag late" style={{ marginLeft: 6 }} title={`${r.unfinalReq?.by} 申请撤回终审：${r.unfinalReq?.reason}——待财务BP批准，期间照常对外`}>待批撤回</span>}
          {r.recalc?.applied && <span className="tag late" style={{ marginLeft: 6 }} title={`源表小计公式漏行，已按明细重算：全成本 ${fmt(r.recalc.srcFull)} → ${fmt(r.recalc.full)}（${r.recalc.diff > 0 ? '+' : ''}${fmt(r.recalc.diff)}）`}>小计重算</span>}
          {r.obsoleteBy && (dead
            ? <span className="tag unmap" style={{ marginLeft: 6 }} title={`已被 ${r.obsoleteBy.cpCode} ${r.obsoleteBy.productName} 替代（${r.obsoleteBy.at}）——已退出对外台账，BP 不再拿到本版`}>已失效 · 被 {r.obsoleteBy.cpCode} 替代</span>
            : <span className="tag late" style={{ marginLeft: 6 }} title={`${r.obsoleteBy.cpCode} 已初审、待终审；其终审通过后本版退出对外台账。在此之前 BP 仍用本版`}>待替代 · {r.obsoleteBy.cpCode} 待终审</span>)}
          {(r.replaces || []).length > 0 && <span className="bom-gvtag" title={'本版替代了：' + r.replaces.map(c => `${c.cpCode}（${c.why || ''} 审核 ${c.auditAt || '—'}）`).join('；')}>替代 {r.replaces.map(c => c.cpCode).join('、')}</span>}
          {(r.variants || []).length > 0 && <span className="bom-gvtag" style={{ color: 'var(--green)', borderColor: 'var(--green)' }} title={'并行版本（同一产品不同版本/包装，都对外）：' + r.variants.map(v => `${v.cpCode} ${v.productName}${v.packSpec ? ' · ' + v.packSpec : ''}`).join('；')}>⇉ 并行 {r.variants.map(v => v.cpCode).join('、')}</span>}
          {nver > 1 && <a className="lk" style={{ marginLeft: 6, fontSize: 11, fontWeight: 400 }} onClick={e => { e.stopPropagation(); onOpen(r.id) }}>{nver} 版</a>}</td>
        <td className="sub" style={{ whiteSpace: 'nowrap' }}>{r.packSpec || '—'}</td>
        <td className="num">{fmt(r.comp.mat)}</td>
        <td className="num">{fmt(r.comp.pack)}</td>
        <td className="num">{fmt(r.comp.mfg)}{dot(r.fee.mfg, r.srcFee.mfg) && <b className="bom-dot" title="已调整" />}</td>
        <td className="num" title="小料类标准 0.18 元/kg 含税">{fmt(r.comp.load)}{dot(r.fee.load, r.srcFee.load) && <b className="bom-dot" title="已调整" />}</td>
        <td className="num">{fmt(r.comp.adm)}{dot(r.fee.adm, r.srcFee.adm) && <b className="bom-dot" title="已调整" />}</td>
        <td className="num" style={{ fontWeight: 700, color: 'var(--teal)' }}>{fmt(r.comp.full)}</td>
        <td className="sub" style={{ whiteSpace: 'nowrap' }} title="定稿/审核通过日期（终审优先，其次初审）">{auditDate(r)}</td>
        <td className="sub">{r.approval ? <span className="bom-apprno">{r.approval}</span> : <span className="muted">—</span>}</td>
        <td>{r.customer || <span className="muted">—</span>}</td>
        <td><span className={'tag ' + (STATUS[r.status]?.cls || 'unmap')}>{STATUS[r.status]?.txt || r.status}</span>
          {r.ack?.selfReview && <span className="bom-gvtag" style={{ color: 'var(--amber)', borderColor: 'var(--amber)' }} title="主管理员自审：初审与终审为同一人（单人模式），未经第二人把关">自审</span>}
          {r.hasGoodsVersion && <span className="bom-gvtag" title="附有成本会计商品版（脱敏公开版），已留档">＋商品版</span>}</td>
        <td className="bom-opcol" style={{ whiteSpace: 'nowrap' }} onClick={e => e.stopPropagation()}>
          {r.needFinalReview && onFinalReview &&
            <a className="lk" style={{ fontWeight: 700, color: 'var(--green)', marginRight: 10 }} onClick={() => onFinalReview(r)}>⚑ 终审 ›</a>}
          <a className="lk" style={{ marginRight: 10 }} onClick={() => onOpen(r.id)}>采购核算表 ›</a>
          <a className="lk" title="补/改本产品的 ERP 物料编码" onClick={() => fillErp(r)}>补物料编码</a>
        </td>
      </tr>
    )
  }

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">{isStd ? '标准成本台账' : '待办与复核'}　{isStd
            ? <span className="tag ok">已审核 · 公开</span> : <span className="tag werr">未审核工作台</span>}
            {isStd && onCompare && <button className="btn-sec" style={{ marginLeft: 10, fontSize: 12, padding: '3px 12px', verticalAlign: 'middle' }}
              title="任选两个已审核版本（按 CP 码挑）逐料对比：同产品新旧版、换码前后不同 CP 都可以" onClick={onCompare}>⇄ 版本对比</button>}</div>
          <div className="h-sub">{isStd
            ? '已定稿标准成本 · 含税五分项（元/kg）· 供 BP 定价消费（TOB/TOC 直连、电商/通品显式引用）'
            : '钉钉「BOM表报价」附件抓取/手工入账 → 复核（改税率费用）→ 定稿毕业进「标准成本台账」· 未审核仅本组可见'}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="pill-src" style={{ color: 'var(--amber)', borderColor: 'var(--amber-line)', background: 'var(--amber-bg)' }}
            title="本工具的数据来自钉钉「BOM表报价」审批附件（金蝶仅用于「核价」查实采价）">
            {cfg?.source === 'sample'
              ? '样例 · 真实数据'
              : '数据源 · 钉钉' + (cfg?.dingtalkConfigured ? '' : '（未连接）')}</span>
          {!isStd && cfg?.canFetch && <>
            <button className="btn-pri" onClick={onManual}
              title="录钉钉单号 → 抓附件 → 生成待办（判断留到处理页）">＋ 立项（录钉钉单号）</button>
            <button className="btn-sec" onClick={() => onStdImport && onStdImport({})}
              title="历史数据不再一张张传采购核算表：按模板导入五分项标准成本 → 成本会计批量确认即已审核（无物料明细）">⇪ 导入标准成本（历史）</button>
          </>}
        </div>
      </div>
      <div className="body">
        <BomDeliverPanel />
        <div className="card bom-stats">
          <Stat lab="台账产品" v={stats.total} suf={`个 · 含半成品 ${stats.semi}`} />
          <Stat lab="最近核算" v={stats.last} small suf="" />
          {isStd
            ? <><Stat lab="来源审批" v={stats.approvals} suf="单" />
              <Stat lab="勾稽校验" v="全平" green suf={`${stats.versions} 版 × 6 项`} />
              <Stat lab={data.canFinalReview ? '待我终审' : '待终审'} v={data.needAck || 0}
                suf={onlyFinal ? '已筛出 · 再点取消' : ((data.needAck || 0) > 0 ? '点这里只看这几条' : `/ ${stats.total} 产品`)}
                active={onlyFinal} onClick={(data.needAck || 0) > 0 || onlyFinal ? () => setOnlyFinal(v => !v) : null} /></>
            : <><Stat lab="待办单号" v={openAppr.length} suf={doneCount ? `单 · 另 ${doneCount} 单已完成初审` : '单'} />
              <Stat lab="组" v={openAppr.reduce((s, a) => s + a.groupCount, 0)}
                suf="一个采购核算表文件=一组" />
              <Stat lab="待复核" v={openAppr.reduce((s, a) => s + a.pending, 0)}
                suf={`/ ${stats.total} 产品`} /></>}
        </div>

        {!isStd && impBatches.length > 0 && <div className="card" style={{ padding: '10px 14px', borderLeft: '3px solid var(--amber)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <b>⇪ 导入待确认 {impBatches.length} 批</b>
            <span className="muted" style={{ fontSize: 12 }}>历史标准成本导入，成本会计勾选批量确认即已审核（不经财务BP终审）</span>
          </div>
          {impBatches.map(b => <div key={b.batchId} style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 6, fontSize: 12.5 }}>
            <span style={{ fontWeight: 600 }}>{b.fileName}</span>
            <span className="muted">{b.createdBy} · {b.createdAt}</span>
            <span className="tag ok">可入 {b.okCount}</span>
            {b.badCount > 0 && <span className="tag leak">有问题 {b.badCount}</span>}
            {b.needAnswer > 0 && <span className="tag late">{b.needAnswer} 行要答 A/B/C</span>}
            <a className="lk" onClick={() => onStdImport && onStdImport({ batchId: b.batchId })}>处理 ›</a>
          </div>)}
        </div>}
        <div className="card bom-filterbar">
          <input className="bom-search" placeholder={isStd ? '搜索 CP码 / 物料编码 / 产品名称 / 客户' : '搜索钉钉单号 / 产品名称 / CP码 / 物料编码'}
            value={q} onChange={e => setQ(e.target.value)} />
          {isStd && <>
            <Seg value={ftype} onChange={setFtype} opts={[['all', '全部'], ['成品', '成品'], ['半成品', '半成品'], ['复配料', '复配料']]} />
            <Seg value={fch} onChange={setFch} opts={[['all', '全部渠道'], ['ecom', '只看电商'], ['common', '只看通品'], ['tob', 'TOB'], ['toc', 'TOC']]} />
            {deadCount > 0 && <button className={'btn-sec' + (showObs ? ' on' : '')} style={{ fontSize: 11.5 }} onClick={() => setShowObs(v => !v)}
              title="被已终审新版替代（同CP重核 / 不同CP同物料编码）的旧版：记录与历史都在，只是不再对外">
              {showObs ? '隐藏' : '显示'}已失效 {deadCount}</button>}
            {histRows.length > 0 && <button className={'btn-sec' + (showHist ? ' on' : '')} style={{ fontSize: 11.5 }} onClick={() => setShowHist(v => !v)}
              title="历史版：补录/答 C 归档的老版本，已审但不对外、不占定稿指针">
              {showHist ? '隐藏' : '显示'}历史版 {histRows.length}</button>}
          </>}
          {!isStd && doneCount > 0 && <button className={'btn-sec' + (showDone ? ' on' : '')} style={{ fontSize: 11.5 }} onClick={() => setShowDone(v => !v)}
            title="产品已全部初审（成本会计任务完成）的钉钉单：默认不占待办；终审在「标准成本台账」由财务BP做">
            {showDone ? '隐藏' : '显示'}已完成 {doneCount}</button>}
          <span style={{ flex: 1 }} />
          <span className="pill-src">口径：<b style={{ color: 'var(--ink)' }}>含税 元/kg</b></span>
        </div>

        {/* 待办与复核＝按钉钉单号立项：一行一个单号，点进去在「处理页」按组处理 */}
        {!isStd
          ? <div className="tbl-wrap">
            <table className="bom-ledger">
              <thead><tr>
                <th className="th">钉钉单号</th><th className="th" style={{ textAlign: 'right' }}>组</th>
                <th className="th" style={{ textAlign: 'right' }}>产品</th><th className="th">产品明细</th>
                <th className="th">核算日期</th><th className="th">进度</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {(apprRows.length === 0) && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 30 }}>
                  {q.trim() ? `没有匹配的待办单（搜索：${q}）` : (doneCount ? `待办已清空 —— ${doneCount} 单已全部初审，成本会计任务完成；终审由财务BP在「标准成本台账」做。点上方「显示已完成」可回看。` : '暂无待办审批单')}</td></tr>}
                {apprRows.map(a => (
                  <tr key={a.approvalNo || '__manual__'} className="row" onClick={() => onApproval(a.approvalNo)} title="进入处理页">
                    <td>{a.approvalNo ? <span className="bom-apprno">{a.approvalNo}</span> : <span className="muted">手工上传 / 无单号</span>}</td>
                    <td className="num" style={{ fontWeight: 700 }}>{a.groupCount}</td>
                    <td className="num">{a.productCount}</td>
                    <td className="sub">{(a.products || []).slice(0, 4).map((p, i) =>
                      <span key={i} style={{ marginRight: 8 }}>{p.productName}<Kind k={p.kind} /></span>)}
                      {(a.products || []).length > 4 && <span className="muted">…等 {a.products.length} 个</span>}</td>
                    <td className="sub">{a.date}</td>
                    <td>{a.pending > 0
                      ? <span className="tag werr">待复核 {a.pending}</span>
                      : (a.blocked > 0 ? <span className="tag werr">待修 {a.blocked}</span> : <span className="tag ok">全部已初审 · 已完成</span>)}
                      {a.finalized > 0 && a.pending > 0 && <span className="tag ok" style={{ marginLeft: 4 }}>已初审 {a.finalized}</span>}
                      {a.blocked > 0 && a.pending > 0 && <span className="tag late" style={{ marginLeft: 4 }}>待修 {a.blocked}</span>}
                      {a.returned > 0 && <span className="tag late" style={{ marginLeft: 4 }} title="财务BP终审退回（工作台或 OA），原因见处理页 / 产品详情顶部">⟲ 终审退回 {a.returned}</span>}
                      {a.oaReturned && <span className="tag late" style={{ marginLeft: 4 }} title={`OA 单退回后再次到成本核算节点（${a.oaReturned.at}）：已自动重拉替换 ${(a.oaReturned.replaced || []).length} 组，${(a.oaReturned.held || []).length} 组已初审未动（附件可能已变，请在处理页「重连钉钉替换」）`}>⟲ OA 退回重到</span>}</td>
                    <td style={{ whiteSpace: 'nowrap' }}><a className="lk" onClick={e => { e.stopPropagation(); onApproval(a.approvalNo) }}>进入处理 ›</a>
                      {isSuper && onDelete && a.approvalNo && <a className="lk" style={{ marginLeft: 10, color: 'var(--red)' }} title="主管理员：整单永久删除（需密钥）"
                        onClick={e => { e.stopPropagation(); onDelete({ approvalNo: a.approvalNo }, `钉钉单 ${a.approvalNo}（${a.productCount} 个产品，${a.groupCount} 组）`) }}>删除</a>}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
          : <div className="tbl-wrap">
            <table className="bom-ledger">
              <thead><tr>
                <th className="th" title="审核定性时填的物料类别（复配料 / 自产半成品 / 自产成品 / 委外半成品 / 委外成品）；未定性显编码建议值">物料分类</th>
                <th className="th">物料编码</th><th className="th">CP码</th><th className="th">产品名称</th>
                <th className="th">规格</th>
                <th className="th" style={{ textAlign: 'right' }}>原料</th><th className="th" style={{ textAlign: 'right' }}>包材</th>
                <th className="th" style={{ textAlign: 'right' }}>加工费</th><th className="th" style={{ textAlign: 'right' }}>装卸费</th>
                <th className="th" style={{ textAlign: 'right' }}>管理费</th><th className="th" style={{ textAlign: 'right' }}>全成本（含税）</th>
                <th className="th">审核日期</th><th className="th">钉钉单号</th><th className="th">客户</th>
                <th className="th">当前状态</th><th className="th bom-opcol">操作</th>
              </tr></thead>
              <tbody>
                {shown.length === 0 && <tr><td colSpan={15} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 30 }}>
                  暂无匹配的台账产品{fch !== 'all' ? `（渠道：${CH[fch]}）` : ''}</td></tr>}
                {shown.map(renderRow)}
              </tbody>
            </table>
          </div>}
        <div className="foot">{isStd
          ? '原料/包材来自采购核算表逐料解析；加工费/装卸费/管理费为台账费用参数。TOB/TOC 定价默认直连台账定稿版，电商/通品需显式引用。'
          : '待办按钉钉单号立项。一个单号里可有若干「组」——一个采购核算表文件（含成品+半成品+复配料）配上它的 BOM 清单＝一组。点进单号在处理页按组复核、可替换组内文件（重连钉钉/手动上传），被替换的旧版留痕不进标准库。'}</div>
      </div>
    </>
  )
}
function Stat({ lab, v, suf, small, green, onClick, active }) {
  return <div className={'bom-stat' + (onClick ? ' bom-stat-click' : '') + (active ? ' on' : '')} onClick={onClick || undefined}
    title={onClick ? '点击筛选' : undefined}><div className="bom-stat-l">{lab}</div>
    <div className="bom-stat-v" style={{ fontSize: small ? 16 : undefined, color: green ? 'var(--green)' : undefined }}>{v}</div>
    {suf ? <small>{suf}</small> : null}</div>
}
function Seg({ value, onChange, opts }) {
  return <div className="bom-seg">{opts.map(([k, l]) => (
    <button key={k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>{l}</button>))}</div>
}

// 撞名警告（V2.537，业务方定 2026-09-09「甲」）：同一组里两个产品名完全相同 → 靠名字连不出正确上下游，红字提示、不自动连，请研发/成本会计把名字区分开。
function ClashBadge({ cps }) {
  if (!cps || !cps.length) return null
  return <span className="tag leak" style={{ marginLeft: 6 }}
    title={`本组里有另一个产品名字和它完全相同（${cps.join('、')}）——工具靠产品名连上下游，撞名就连不对，已不自动连。请把名字区分开（如成品别叫「…半成品」、补全括号），或改产品名后重连。`}>⚠ 撞名 {cps.join('/')}</span>
}

// 组内嵌套结构 / 审核顺序（业务方定 2026-09-04）：**自下而上** 复配料 → 半成品 → 成品。
// 深度由「谁把谁当原料用」算出来；上游没定稿，下游就定不了稿，所以顺序不是建议、是硬约束。
// 行内层级：第 0 层（最底：复配料）不缩进，每深一层缩进并显示 └→（它把上一层当原料用）
function Lv({ d }) {
  if (!d) return null
  return <span className="bom-lv" style={{ paddingLeft: (d - 1) * 12 }} title={`第 ${d + 1} 层：用了上一层作原料`}>└→</span>
}
function ChainStrip({ products }) {
  const byDepth = {}
  products.forEach(p => { const d = p.depth || 0; (byDepth[d] = byDepth[d] || []).push(p) })
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b)
  if (depths.length <= 1) return null
  const cls = (p) => p.notBooked ? 'bad' : (p.isFinal ? 'fin' : 'ok')
  const mark = (p) => p.notBooked ? '✗未入账' : (p.isFinal ? '✓已定稿' : '待复核')
  return (
    <div className="bom-chain">
      <span className="muted" style={{ fontSize: 11 }}>审核顺序（自下而上）：</span>
      {depths.map((d, i) => (
        <React.Fragment key={d}>
          {i > 0 && <span className="arw">→</span>}
          <span className="bom-chainlv"><em>第{i + 1}步</em>
            {byDepth[d].map(p => (
              <span key={p.productKey} className={'bom-chainitem ' + cls(p)} title={`${p.kind} · ${mark(p)}`}>
                {p.kind}｜{p.productName}<b>{p.notBooked ? '✗' : (p.isFinal ? '✓' : '…')}</b></span>))}
          </span>
        </React.Fragment>))}
      <span className="muted" style={{ fontSize: 10.5 }}>上游没定稿，下游定不了稿</span>
    </div>
  )
}

// ============ 处理页：一个钉钉单号 → 若干「组」（一个采购核算表文件=一组）============
// 组内：当前版产品（成品/半成品/复配料）+ 各自 BOM 校验 + 可替换组内文件（重连钉钉/手动上传）+ 被替换旧版留痕。
// 从评论区替换（V2.839）：列出这张钉钉单评论区里取到的采购核算表 / BOM 清单，选了替换本组。
// 研发每改一次 BOM 换一个 CP 后缀（-2→-3），立项时评论区的新版会自成一组、表单里的旧版原样留着——由人在这里点替换，旧版标「被替换」留痕。
function CommentReplaceModal({ no, g, idx, onClose, onDone, flash }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [sheet, setSheet] = useState('')     // 选中的采购核算表 fileId（''＝不换）
  const [bom, setBom] = useState('')         // 选中的 BOM 清单 fileId（''＝不换）
  const [bomTouched, setBomTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const bomFor = (data, sid) => {            // 默认 BOM＝与所选核算表 CP 相同的那份（最新）
    const s = (data.sheets || []).find(x => x.fileId === sid)
    const cps = new Set(((s && s.products) || []).map(p => p.cpCode))
    const hit = (data.boms || []).find(b => (b.lists || []).some(l => cps.has(l.cpCode)))
    return hit ? hit.fileId : ''
  }
  const loadFiles = async (fresh) => {
    setD(null); setErr('')
    try {
      const r = await getBomCommentFiles(no, g.groupId, fresh)
      if (!r.ok) { setErr(r.msg || '取评论区附件失败'); return }
      setD(r)
      const def = (r.sheets || []).find(s => !(s.products || []).every(p => p.inThisGroup))
      const sid = def ? def.fileId : ''
      setSheet(sid); setBom(sid ? bomFor(r, sid) : ''); setBomTouched(false)
    } catch (e) { setErr('取评论区附件失败：' + e.message) }
  }
  useEffect(() => { loadFiles(false) }, [no, g.groupId])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  const pickSheet = (sid) => { setSheet(sid); if (!bomTouched && d) setBom(sid ? bomFor(d, sid) : bom) }
  const go = async () => {
    setBusy(true)
    try { onDone(await bomReplaceFromComment(g.groupId, no, sheet, bom)) }
    catch (e) { flash('替换失败：' + e.message); setBusy(false) }
  }
  const S = d && (d.sheets || []).find(x => x.fileId === sheet)
  const B = d && (d.boms || []).find(x => x.fileId === bom)
  const cur = (d && d.group) || []
  const sameFile = S && (S.products || []).every(p => p.inThisGroup)
  const opt = (on) => ({ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '9px 12px', border: '1px solid ' + (on ? 'var(--accent)' : 'var(--line)'),
    background: on ? 'var(--accent-soft)' : 'transparent', borderRadius: 8, cursor: 'pointer', marginBottom: 6 })
  const meta = (x) => <span className="muted" style={{ fontSize: 11 }}>{x.by || '评论人未知'} · {x.at || '—'} 传</span>
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(760px,100%)' }}>
        <div className="bom-mhead"><b>从评论区替换 · 组 {idx}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div style={{ maxHeight: '68vh', overflowY: 'auto', marginTop: 12, paddingRight: 4 }}>
          {err && <div className="banner" style={{ background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red)' }}>{err}</div>}
          {!d && !err && <div className="muted" style={{ padding: 24, textAlign: 'center' }}>正在从钉钉取这张单评论区的附件…（几秒钟）</div>}
          {d && <>
            <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
              本组现在：{cur.length ? cur.map(c => `${c.cpCode} ${c.productName}（${fmt(c.full)}，${c.status}）`).join('；') : '（没有已入账的产品）'}
            </div>
            {(d.sheets || []).length === 0 && (d.boms || []).length === 0
              ? <div className="muted" style={{ padding: 20, textAlign: 'center' }}>这张单的评论区里没有取到采购核算表或 BOM 清单。</div>
              : <>
                <div style={{ fontWeight: 700, fontSize: 13, margin: '4px 0 6px' }}>① 采购核算表（评论区）</div>
                <label style={opt(sheet === '')}><input type="radio" checked={sheet === ''} onChange={() => pickSheet('')} />
                  <span>不换采购核算表<span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>只换 BOM 清单</span></span></label>
                {(d.sheets || []).map(s => { const mine = (s.products || []).every(p => p.inThisGroup); const other = (s.products || []).some(p => p.bookedId && !p.inThisGroup); return (
                  <label key={s.fileId} style={opt(sheet === s.fileId)}><input type="radio" checked={sheet === s.fileId} onChange={() => pickSheet(s.fileId)} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, wordBreak: 'break-all' }}>{s.fileName}
                        {mine && <span className="bom-gvtag" style={{ marginLeft: 6 }}>本组当前就是这份</span>}
                        {other && <span className="bom-gvtag" style={{ marginLeft: 6, color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>已在本单另一组入账 · 替换后并入本组</span>}</div>
                      {(s.products || []).map((p, i) => <div key={i} style={{ fontSize: 12 }}>
                        <b className="mono">{p.cpCode}</b> {p.productName} · 全成本 ¥{fmt(p.full)} · {p.matCount} 行料 ·
                        {p.checksOk ? <span style={{ color: 'var(--green)' }}> 勾稽全平</span> : <span style={{ color: 'var(--red)' }}> 勾稽不平（不会入账）</span>}</div>)}
                      {meta(s)}
                    </span></label>) })}
                {(d.sheets || []).length === 0 && <div className="muted" style={{ fontSize: 12, margin: '0 0 8px 4px' }}>评论区没有采购核算表。</div>}

                <div style={{ fontWeight: 700, fontSize: 13, margin: '12px 0 6px' }}>② 研发 BOM 清单（评论区）</div>
                <label style={opt(bom === '')}><input type="radio" checked={bom === ''} onChange={() => { setBom(''); setBomTouched(true) }} />
                  <span>不指定 BOM 清单<span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>沿用按 CP 码自动配到的那份</span></span></label>
                {(d.boms || []).map(b => (
                  <label key={b.fileId} style={opt(bom === b.fileId)}><input type="radio" checked={bom === b.fileId} onChange={() => { setBom(b.fileId); setBomTouched(true) }} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, wordBreak: 'break-all' }}>{b.fileName}</div>
                      {(b.lists || []).map((l, i) => <div key={i} style={{ fontSize: 12 }}><b className="mono">{l.cpCode}</b> {l.productName} · {l.rows} 行</div>)}
                      {meta(b)}
                    </span></label>))}
                {(d.boms || []).length === 0 && <div className="muted" style={{ fontSize: 12, margin: '0 0 8px 4px' }}>评论区没有 BOM 清单。</div>}

                {(d.pending || []).length > 0 && <div className="muted" style={{ fontSize: 11.5, marginTop: 10, lineHeight: 1.6 }}>
                  另有 {d.pending.length} 个评论区附件没取到：{d.pending.map((p, i) => <span key={i}>{i ? '；' : ''}{p.fileName}{p.replacedBy ? '（重传前的旧件，可忽略）' : '（钉钉回无访问权限）'}</span>)}</div>}

                <div className="banner" style={{ display: 'block', background: 'var(--bg-sub)', color: 'var(--ink-2)', border: '1px solid var(--line)', marginTop: 12, lineHeight: 1.7 }}>
                  {!sheet && !bom ? '请选一份采购核算表或 BOM 清单。'
                    : sameFile && !bom ? '本组当前已经是这份采购核算表，没有要换的。'
                    : <>确认后：
                      {S && !sameFile && <span>本组现有的 <b>{cur.map(c => c.cpCode).join('、') || '（无）'}</b> 标「被替换」留痕、退出当前版；换成评论区的 <b>{(S.products || []).filter(p => p.checksOk).map(p => `${p.cpCode}（¥${fmt(p.full)}）`).join('、') || '（无勾稽平的产品）'}</b>，状态从「未复核」重新走。</span>}
                      {B && <span>{S && !sameFile ? ' ' : ''}BOM 清单用 <b>{(B.lists || []).map(l => l.cpCode).join('、')}</b>（{B.at}），用量自洽要重新确认。</span>}
                      {' '}已财务BP终审的旧版不会被直接顶掉。</>}
                </div>
              </>}
          </>}
        </div>
        <div className="bom-mfoot">
          <a className="lk" style={{ marginRight: 'auto', fontSize: 12 }} onClick={() => loadFiles(true)}>重新从钉钉取 ›</a>
          <button className="btn-sec" onClick={onClose}>取消</button>
          <button className="btn-pri" disabled={busy || !d || (!sheet && !bom) || (sameFile && !bom)} onClick={go}>{busy ? '替换中…' : '确认替换'}</button>
        </div>
      </div>
    </div>
  )
}

function ApprovalView({ no, cfg, onBack, onOpen, flash, isSuper, onDelete }) {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [rep, setRep] = useState(null)      // 最近一次替换结果（含仍不平清单）
  const [auditP, setAuditP] = useState(null)   // 审核定性弹窗的目标产品
  const [pendP, setPendP] = useState(null)     // 不平下钻明细：{groupId, product}
  const [cmtG, setCmtG] = useState(null)       // 「从评论区替换」弹窗目标：{g, idx}
  const load = useCallback(async () => {
    setLoading(true)
    try { setD(await getBomApproval(no)) } catch (e) { flash('加载失败：' + e.message) }
    setLoading(false)
  }, [no])
  useEffect(() => { load() }, [load])

  const afterRep = async (r, how) => {
    if (!r || !r.ok) return flash((r && r.msg) || '替换失败')
    setRep({ ...r, how })
    const st = (r.staleDownstream || []).length
    flash(`${how}：替换 ${r.replaced.length}、新增 ${r.added.length}` + (r.stillBad.length ? `，仍不平 ${r.stillBad.length}` : '') + (st ? `，下游 ${st} 个受影响已打回未复核` : '') + (r.backfill ? '　· 补录组：新版承接「补录」标记，初审通过即定稿' : ''))
    await load()
  }
  const afterCmt = async (r) => {      // 从评论区替换的结果
    setCmtG(null)
    if (r.bomOnly) {
      flash(`已换 BOM 清单：${(r.bomUpdated || []).map(x => x.productName).join('、')}（用量自洽需重新确认）` + ((r.bomMissed || []).length ? `；未换：${r.bomMissed.map(x => x.productName + '·' + x.msg).join('；')}` : ''))
      return load()
    }
    if (!r.replaced.length && !r.added.length && !r.stillBad.length && (r.same || []).length) { flash('本组当前已经是评论区这份文件，无需替换'); return load() }
    await afterRep(r, '从评论区替换')
  }
  const doRefetch = async (gid) => {
    setBusy(gid + ':dt')
    try { await afterRep(await bomRefetchReplace(gid, no), '重连钉钉替换') }
    catch (e) { flash('替换失败：' + e.message) } finally { setBusy('') }
  }
  const doUpload = async (gid, files) => {
    if (!files || !files.length) return
    setBusy(gid + ':up')
    try { await afterRep(await bomReplaceSheet(gid, no, files[0]), '上传替换') }
    catch (e) { flash('替换失败：' + e.message) } finally { setBusy('') }
  }
  const doBom = async (entryId, files) => {
    if (!files || !files.length) return
    setBusy('bom' + entryId)
    try {
      const r = await bomAttachBomList(entryId, files[0])
      if (!r.ok) flash(r.msg || '挂载失败'); else { flash('已挂/替换 BOM清单并重校验'); await load() }
    } catch (e) { flash('挂载失败：' + e.message) } finally { setBusy('') }
  }

  const groups = d?.groups || []
  const liveGroups = groups.filter(g => !g.historyOnly)   // 纯历史组(0 active、无待修，只剩作废/被替换)不占组号
  const deadHist = groups.filter(g => g.historyOnly).flatMap(g => g.superseded)
  const prodCount = liveGroups.reduce((s, g) => s + g.products.length, 0)
  const histCount = groups.reduce((s, g) => s + g.superseded.length, 0)

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">处理审批单　<span className="bom-apprno">{no || '（手工/无单号）'}</span>
            {liveGroups.length === 0 && <span className="tag unmap">无记录</span>}</div>
          <div className="h-sub">一个采购核算表文件（含成品+半成品+复配料）＋ 它的 BOM 清单 ＝ <b>一组</b>；本单共 {liveGroups.length} 组 · {prodCount} 个产品{histCount ? ` · ${histCount} 条替换留痕` : ''}</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-sec" onClick={onBack}>返回待办</button>
          <button className="btn-sec" onClick={load}>⟳ 刷新</button>
          {isSuper && onDelete && no && <button className="btn-sec" style={{ color: 'var(--red)', borderColor: 'var(--red)' }}
            title="主管理员：删除本单全部组、记录、待修批次与留档文件（需密钥，留全局审计）"
            onClick={() => onDelete({ approvalNo: no }, `钉钉单 ${no}（整单）`, onBack)}>🗑 删除整单</button>}
        </div>
      </div>
      <div className="body">
        <div className="bom-crumbs"><a className="lk" onClick={onBack}>待办与复核</a> / 单号 {no}</div>

        {cmtG && <CommentReplaceModal no={no} g={cmtG.g} idx={cmtG.idx} onClose={() => setCmtG(null)} onDone={afterCmt} flash={flash} />}
        {rep && (rep.stillBad || []).length > 0 && <div className="card bom-sect" style={{ borderLeft: '3px solid var(--amber)' }}>
          <div className="bom-secthead"><span className="bom-no" style={{ background: 'var(--amber-bg)', color: 'var(--amber)' }}>!</span>
            <b>{rep.how}后仍有 {rep.stillBad.length} 个产品没换成（勾稽不平或被拦）</b>
            <span style={{ flex: 1 }} /><a className="lk" onClick={() => setRep(null)}>关闭</a></div>
          <div style={{ padding: '10px 14px' }}>
            {rep.stillBad.map((b, i) => <div key={i} className="bom-chkfail">
              <b>{b.productName}（{b.cpCode || '无编码'}）</b>
              {b.msg && <div style={{ color: 'var(--red)' }}>{b.msg}</div>}
              {b.recalcBlocked && <div style={{ color: 'var(--red)' }}>不能按明细重算：{b.recalcBlocked}</div>}
              {(b.failedChecks || []).map((c, j) => <div key={j}>✗ {c.check}：申报 {fmt(c.a, 4)} ≠ 逐料Σ {fmt(c.b, 4)}（差 {c.diff > 0 ? '+' : ''}{fmt(c.diff, 4)}）
                {(c.missing || []).length > 0 && <span>　— 疑源表小计漏加：<b>{c.missing.map(m => m.matName).join('、')}</b></span>}</div>)}
            </div>)}
          </div>
        </div>}

        {liveGroups.length === 0 && deadHist.length === 0 && <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--ink-3)' }}>
          该单号下暂无已入账记录。请回待办用「手工入账 / 从钉钉取数」先入账。</div>}

        {liveGroups.map((g, gi) => (
          <div key={g.groupId} className="card bom-sect bom-grp">
            <div className="bom-secthead">
              <span className="bom-no">{gi + 1}</span>
              <b>组 {gi + 1}：{g.coreName || '（未命名）'}</b>
              <span className="mono muted" style={{ fontSize: 11 }}>{g.coreCp}</span>
              {g.anyFinal && <span className="tag ok">含已定稿</span>}
              {g.pendingCount > 0 && <span className="tag leak" title="同一采购核算表里勾稽不平、未入账的产品，需退回研发/工厂修源表">{g.pendingCount} 个待修未入账</span>}
              {g.returnCount > 0 && <span className="tag late" title="财务BP终审退回，原因见产品详情顶部横幅">⟲ 终审退回 {g.returnCount}</span>}
              {g.allOk ? <span className="tag ok">四步已确认</span> : <span className="tag werr">待复核</span>}
              <span className="muted" style={{ fontSize: 11 }}>{g.bookedCount}/{g.products.length} 已入账</span>
              <span style={{ flex: 1 }} />
              <span className="muted" style={{ fontSize: 11 }} title={g.coreFile}>采购核算表：{(g.coreFile || '—').slice(0, 34)}{(g.coreFile || '').length > 34 ? '…' : ''}</span>
            </div>

            {/* 组内嵌套结构与审核顺序 */}
            <ChainStrip products={g.products} />

            {/* 组内产品（已按依赖深度自下而上排：先复配料、再半成品、最后成品）*/}
            <div className="tbl-wrap" style={{ border: 'none' }}>
              <table><thead><tr>
                <th className="th">物料类别</th><th className="th">CP码</th><th className="th">产品名称</th>
                <th className="th" style={{ textAlign: 'right' }}>全成本（含税）</th>
                <th className="th">勾稽</th><th className="th">用量自洽</th><th className="th">来源方</th>
                <th className="th">复核确认</th><th className="th">状态</th>
                <th className="th">BOM清单</th><th className="th"></th>
              </tr></thead><tbody>
                {g.products.map((p, pi) => {
                  // 未入账（勾稽不平）也要列出来：否则会计只看到入账成功的那个，看不出差异在哪、没法找上游改
                  if (p.notBooked) return (
                    <React.Fragment key={'nb' + pi}>
                      <tr className="bom-nbrow row" onClick={() => setPendP({ groupId: g.groupId, product: p })}
                        title="点开看逐料明细：到底哪几味料没被算进小计">
                        <td><span className="bom-catsug">建议·{p.kindAuto}</span></td>
                        <td className="mono sub">{p.cpCode || '—'}</td>
                        <td style={{ fontWeight: 600 }}><Lv d={p.depth} />{p.productName}<span className="tag leak" style={{ marginLeft: 4 }}>未入账</span><ClashBadge cps={p.nameClash} /></td>
                        <td className="num muted">{fmt(p.comp?.full)}</td>
                        <td>{p.checksOk ? <span className="tag ok">全平</span> : <span className="tag leak">不平</span>}</td>
                        <td colSpan={4} className="muted" style={{ fontSize: 11 }}>
                          {(p.blockedBy || []).length > 0 && p.checksOk
                            ? <><b style={{ color: 'var(--red)' }}>自身全平，但上游「{p.blockedBy.join('、')}」不平 → 连带拦下</b>：本品用的是它的价，成本建在错数上</>
                            : p.recalcBlocked
                              ? <><b style={{ color: 'var(--red)' }}>勾稽不平，且不能按明细重算</b>：{p.recalcBlocked}；修好源表后用下方「替换采购核算表」补入</>
                              : <>勾稽不平 → 不予入账（红线）{(p.blockedBy || []).length > 0 ? `；且上游「${p.blockedBy.join('、')}」也不平` : ''}；修好源表后用下方「替换采购核算表」补入</>}</td>
                        <td className="muted" style={{ fontSize: 11 }}>{p.matCount} 味料</td>
                        <td><a className="lk" onClick={e => { e.stopPropagation(); setPendP({ groupId: g.groupId, product: p }) }}>查明细 ›</a></td>
                      </tr>
                      <tr><td /><td colSpan={9} style={{ paddingTop: 0 }}>
                        <div className="bom-chkfail">
                          {(p.failedChecks || []).map((c, j) => <div key={j}>
                            <b>✗ {c.check}</b>：采购核算表写的小计 <b>{fmt(c.a, 4)}</b> ≠ 逐料相加 <b>{fmt(c.b, 4)}</b>（少算 {fmt(Math.abs(c.diff || 0), 4)} 元/kg 不含税）
                            {(c.missing || []).length > 0 && <span>　— <b>没被加进小计的是：{c.missing.map(m => m.matName).join('、')}</b>（料是有的，是小计公式没框到），
                              <a className="lk" onClick={() => setPendP({ groupId: g.groupId, product: p })}>点开逐料看 ›</a></span>}
                          </div>)}
                          {(p.blockedBy || []).length > 0 && <div>
                            <b>⛓ 上游链路不通</b>：本品用「{p.blockedBy.join('、')}」当原料，而它<b>自身勾稽不平、成本未经确认</b> —
                            半成品的「全成本含税」就是本品料行里的「含税价」，上游错了本品必然跟着错，故一并拦下。<b>先修上游，再一起重传。</b>
                          </div>}
                        </div>
                      </td></tr>
                    </React.Fragment>)
                  const ck = (p.checks || []).length >= 6 && p.checks.every(c => c.ok)
                  const bc = p.bomCheck && p.bomCheck.summary
                  return (<tr key={p.id}>
                    <td><CatCell p={p} /></td>
                    <td className="mono sub">{p.cpCode}</td>
                    <td style={{ fontWeight: 600 }}><Lv d={p.depth} /><a className="lk" onClick={() => onOpen(p.id)}>{p.productName}</a><ClashBadge cps={p.nameClash} /></td>
                    <td className="num" style={{ fontWeight: 700, color: 'var(--teal)' }}>{fmt(p.comp.full)}</td>
                    <td>{ck ? (p.recalc ? <span className="tag ok" title={`小计按明细重算：源表全成本 ${fmt(p.recalc.srcFull)} → ${fmt(p.recalc.full)}（${p.recalc.diff > 0 ? '+' : ''}${fmt(p.recalc.diff)}）${(p.recalc.missing || []).length ? '；疑似漏加 ' + p.recalc.missing.join('、') : ''}；③确认即认可`}>全平·已重算</span> : <span className="tag ok">全平</span>) : <span className="tag leak">不平</span>}
                      {p.recalc && <div className="muted" style={{ fontSize: 10.5, whiteSpace: 'nowrap' }}>{fmt(p.recalc.srcFull)}→{fmt(p.recalc.full)}</div>}</td>
                    <td>{!bc ? <span className="muted" style={{ fontSize: 11 }}>无清单</span>
                      : bc.ok ? <span className="tag ok">自洽</span>
                        : <span className="tag werr">{[bc.qtyMismatch && '用量' + bc.qtyMismatch, bc.missing && '缺' + bc.missing, bc.extra && '多' + bc.extra].filter(Boolean).join('/')}</span>}
                      {p.bomInherited && <span className="tag late" style={{ marginLeft: 3 }}>沿用</span>}</td>
                    <td><Origin o={p.origin} small />{p.hasGoodsVersion && <span className="bom-gvtag">＋商品版</span>}</td>
                    <td className="bom-step4c">{CONFIRM_STEPS.map(([k, lab]) => (
                      <span key={k} title={lab + (p.steps?.[k] ? ' 已确认' : ' 待确认')}
                        className={p.steps?.[k] ? 'ok' : 'wait'}>{k === 'qty' ? '③' : '④'}</span>))}</td>
                    <td><span className={'tag ' + (STATUS[p.status]?.cls || 'unmap')}>{p.status}</span>
                      {p.quotable === false && <span className="bom-noquote" title={p.quoteReason}>禁报价</span>}
                      {p.staleNote && <span className="tag late" style={{ marginLeft: 4 }} title="上游采购核算表被替换、成本可能变了——本品已打回未复核，请重新复核">⚠ {p.staleNote}</span>}</td>
                    <td>{cfg?.canAttach
                      ? <label className="bom-minifile">{busy === 'bom' + p.id ? '解析中…' : (p.hasBomList ? '替换' : '补挂')}
                        <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => doBom(p.id, e.target.files)} /></label>
                      : <span className="muted" style={{ fontSize: 11 }}>{p.hasBomList ? '已挂' : '未挂'}</span>}</td>
                    <td>{cfg?.canAudit && !p.isFinal && p.stepsOk
                      ? <a className="lk" style={{ fontWeight: 700, color: 'var(--green)' }} onClick={() => setAuditP(p)}>⚑ 审核定稿 ›</a>
                      : <a className="lk" onClick={() => onOpen(p.id)}>复核 ›</a>}</td>
                  </tr>)
                })}
              </tbody></table>
            </div>

            {/* 组内文件替换 */}
            {cfg?.canFetch && <div className="bom-grpact">
              <span className="muted" style={{ fontSize: 11.5 }}>组内采购核算表有错（如小计漏加料）→ 让研发/工厂改好后在此替换；<b>旧版留痕、不进标准成本库</b></span>
              <span style={{ flex: 1 }} />
              <button className="btn-sec" disabled={!!busy || !cfg?.dingtalkConfigured} onClick={() => doRefetch(g.groupId)}
                title={cfg?.dingtalkConfigured ? '重连钉钉重拉商务版采购核算表替换' : '本机未配置钉钉，请用上传替换'}>
                {busy === g.groupId + ':dt' ? '重拉中…' : '⟳ 重连钉钉替换'}</button>
              <button className="btn-sec" disabled={!!busy || !cfg?.dingtalkConfigured} onClick={() => setCmtG({ g, idx: gi + 1 })}
                title={cfg?.dingtalkConfigured ? '研发/采购在钉钉评论区补传了更新版时用：列出评论区的采购核算表和 BOM 清单，选了替换本组；旧版留痕' : '本机未配置钉钉'}>
                💬 从评论区替换</button>
              <label className="bom-minifile pri">{busy === g.groupId + ':up' ? '上传中…' : '⬆ 上传替换采购核算表'}
                <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => doUpload(g.groupId, e.target.files)} /></label>
              {isSuper && onDelete && <button className="btn-sec" style={{ color: 'var(--red)', borderColor: 'var(--red)' }}
                title="主管理员：删除本组全部记录（含被替换旧版）与留档文件（需密钥）"
                onClick={() => onDelete({ groupId: g.groupId, approvalNo: no }, `组 ${g.groupId.slice(0, 8)} · ${(g.products || []).map(p => p.productName).join('、')}`, load)}>🗑 删除本组</button>}
            </div>}

            {/* 被替换留痕 */}
            {g.superseded.length > 0 && <details style={{ padding: '0 14px 12px' }}>
              <summary className="bom-ah" style={{ marginTop: 4, cursor: 'pointer' }}>替换留痕 · 审核历史（{g.superseded.length}）<span className="muted" style={{ fontSize: 10.5, fontWeight: 400 }}> · 点开查看</span></summary>
              {g.superseded.map(h => (
                <div key={h.id} className="bom-audit">
                  <div className="bom-audit-h">#{h.id} {h.cpCode} · {h.productName}　<span className="tag unmap">已被替换</span></div>
                  <div className="muted" style={{ fontSize: 11 }}>{h.supersededAt}　{h.reason}</div>
                  <div className="muted" style={{ fontSize: 10.5 }}>原文件：{h.srcFile || '—'}　·　入账 {h.createdAt} by {h.createdBy}</div>
                </div>))}
            </details>}
          </div>))}

        {deadHist.length > 0 && (
          <details className="card bom-sect">
            <summary className="bom-secthead" style={{ cursor: 'pointer' }}>
              <span className="bom-no" style={{ background: 'var(--ink-3)', color: '#fff' }}>史</span>
              <b>历史留痕（已全部作废 / 被替换的旧版）</b>
              <span className="mono muted" style={{ fontSize: 11, marginLeft: 6 }}>{deadHist.length} 条 · 不在台账、不占组号与产品数 · 点开查看</span>
            </summary>
            <div style={{ padding: '4px 14px 12px' }}>
              {deadHist.slice().sort((a, b) => a.id - b.id).map(h => (
                <div key={h.id} className="bom-audit">
                  <div className="bom-audit-h">#{h.id} {h.cpCode} · {h.productName}　<span className="tag unmap">已被替换 / 作废</span></div>
                  <div className="muted" style={{ fontSize: 11 }}>{h.supersededAt}　{h.reason}</div>
                  <div className="muted" style={{ fontSize: 10.5 }}>原文件：{h.srcFile || '—'}　·　入账 {h.createdAt} by {h.createdBy}</div>
                </div>))}
            </div>
          </details>
        )}

        <div className="foot">组＝一个采购核算表文件（成品+半成品+复配料）＋ 匹配的 BOM 清单。替换采购核算表时：新文件里勾稽平的产品顶替同组同产品旧版（旧版标「已被替换」留痕、退出标准成本库与定稿指针）；仍不平的不入账并回报原因；原先因不平未入的产品（如半成品）修好后会作「组内新增」补入。<b>定性</b>＝物料类别（复配料/自产·委外 半成品·成品）+ 是否建议对外报价（不建议须写原因），定稿前必须完成。</div>
      </div>
      {auditP && <AuditModal entry={auditP} onClose={() => setAuditP(null)}
        onDone={async () => { setAuditP(null); await load() }} flash={flash} />}
      {pendP && <PendingDetailModal groupId={pendP.groupId} product={pendP.product}
        onClose={() => setPendP(null)} flash={flash} />}
    </>
  )
}

// ============ 采购核算表详情 ============
function Detail({ entry, all, cfg, mode, onBack, onOpen, onCompare, onChanged, onFinalReview, flash, isSuper, onDelete, onFill }) {
  const isStd = mode === 'std'
  const [edit, setEdit] = useState(false)
  const [fee, setFee] = useState(entry.fee)
  const [saving, setSaving] = useState(false)
  const [sourceBusy, setSourceBusy] = useState('')
  const bomFileRef = useRef(null)
  const sheetFileRef = useRef(null)
  const auditRef = useRef(null)
  const [auditM, setAuditM] = useState(false)      // 审核定性弹窗（物料类别 + 是否允许报价）
  const [voidM, setVoidM] = useState('')           // 作废弹窗：'' | 'request'(成本会计申请) | 'review'(财务BP终审)
  const [priceMat, setPriceMat] = useState(null)   // 价格校验弹窗（内联在明细行触发）
  const [matDraft, setMatDraft] = useState(null)   // 编辑态可改税率的物料副本
  const [step, setStep] = useState('price')        // 四步页签：bom/craft/qty/price（默认落在④报价核算）
  useEffect(() => { setFee(entry.fee); setEdit(false); setMatDraft(null) }, [entry.id])
  const startEdit = () => { setStep('price'); setMatDraft((entry.materials || []).map(m => ({ ...m }))); setEdit(true) }
  const archived = ['初审', '已审核'].includes(entry.status)   // 初审/终审戳在 → 改价税费、改定性、采纳商品版都要先撤销归档（V2.528）
  const peerProducts = (all || []).filter(p => p.id !== entry.id && p.active !== false && (entry.groupId ? p.groupId === entry.groupId : p.approval === entry.approval))
  const approvalGroups = [...new Set((all || []).filter(p => p.approval === entry.approval).map(p => p.groupId).filter(Boolean))]
  const groupNo = Math.max(1, approvalGroups.indexOf(entry.groupId) + 1)
  const cancelEdit = () => { setFee(entry.fee); setMatDraft(null); setEdit(false) }
  // 改税率 → 按发票类型算法现算该料成本不含税（保存时后端权威重算，口径一致）
  const setTax = (mat, v) => {
    let t = parseFloat(v); if (isNaN(t)) t = 0; t = Math.max(0, t) / 100
    setMatDraft(draft => draft.map(m => m === mat
      ? { ...m, taxRate: t, costExcl: (m.qtyPerKg != null && m.priceIncl != null) ? invoiceCostExcl(m.qtyPerKg, m.priceIncl, t, m.invoiceType, cfg?.invoiceRules) : m.costExcl }
      : m))
  }
  // 改发票类型 → 按该发票的算法重算成本不含税（专票价税分离/普票全额/农产品扣除…，基础数据可维护）
  const setInvoice = (mat, invoiceType) => {
    setMatDraft(draft => draft.map(m => m === mat
      ? { ...m, invoiceType, costExcl: (m.qtyPerKg != null && m.priceIncl != null && m.taxRate != null) ? invoiceCostExcl(m.qtyPerKg, m.priceIncl, m.taxRate, invoiceType, cfg?.invoiceRules) : m.costExcl }
      : m))
  }
  // BOM反查·批量价差（④行上红点）：本单每个真实编码物料，研发在别的产品里定价的最高/最低差多少（>15% 标红点）
  const [spreads, setSpreads] = useState(null)
  useEffect(() => {
    let alive = true
    if (!isStd) getBomUsageSpreads(entry.id).then(r => { if (alive && r && r.ok) setSpreads(r.spreads || {}) }).catch(() => { })
    return () => { alive = false }
  }, [entry.id, isStd])
  // ④报价·改物料子类（原辅料/复配料/自产半成品，二次确认；只改原料内部、不动成本）
  const setUpstreamMat = async (matName, targetProductKey) => {   // V2.540 手动指认上游
    try { const r = await bomSetUpstream(entry.id, matName, targetProductKey)
      if (!r.ok) return flash(r.msg || '指认失败')
      flash('已更新上游连线' + (r.resetNote ? '（' + r.resetNote + '）' : '')); setEntry(r.entry); load()
    } catch (e) { flash('指认失败：' + e.message) }
  }
  const setMatType = async (mat, subType) => {
    try { const r = await bomSetMatType(entry.id, mat, subType); if (!r.ok) return flash(r.msg || '改类型失败'); flash(`已把「${mat.matName}」改为「${subType}」`); await onChanged() }
    catch (e) { flash('改类型失败：' + e.message) }
  }

  const versions = (entry.versions || []).slice().sort((a, b) => (a.calcDate || '').localeCompare(b.calcDate || '') || (a.id - b.id))
  const myIx = versions.findIndex(v => v.id === entry.id)
  const prev = myIx > 0 ? versions[myIx - 1] : null
  const hasNewer = (myIx >= 0 && myIx < versions.length - 1) || (!entry.active && entry.inactiveKind === 'replaced')
  const prevMat = (name) => prev ? (prev.materials || []).find(m => clean(m.matName) === clean(name)) : null

  const curMats = (edit && matDraft) ? matDraft : (entry.materials || [])
  const live = edit && matDraft
  const sumCost = (seg) => Math.round(curMats.filter(m => m.seg === seg).reduce((s, m) => s + (m.costExcl || 0), 0) * 1e4) / 1e4
  const matSub = live ? sumCost('原料') : entry.matSubtotal
  const packSub = live ? sumCost('包材') : entry.packSubtotal
  const compMat = Math.round((matSub || 0) * GROSS * 1e4) / 1e4
  const compPack = Math.round((packSub || 0) * GROSS * 1e4) / 1e4
  const comp = { ...entry.comp, mat: compMat, pack: compPack, ...fee }
  const full = compMat + compPack + (fee.mfg || 0) + (fee.load || 0) + (fee.adm || 0)
  const diff = full - (entry.comp.srcFull || 0)
  const dot = (k) => Math.abs((fee[k] || 0) - (entry.srcFee[k] || 0)) > 1e-9

  const setF = (k, v) => setFee(f => ({ ...f, [k]: Math.max(0, parseFloat(v) || 0) }))
  const save = async () => {
    setSaving(true)
    try { const r = await bomReview(entry.id, fee, undefined, matDraft || undefined); flash(r.changed ? `已保存 · ${r.changed} 项变更已留痕` : '无变更'); setMatDraft(null); setEdit(false); await onChanged() }
    catch (e) { flash('保存失败：' + e.message) } finally { setSaving(false) }
  }
  const finalize = async () => {
    try {
      let r = await bomFinalize(entry.id)
      if (!r.ok && r.needConfirm) {          // 换码承接：先答「原版是否失效」
        if (!window.confirm(r.msg + '\n\n确定 = 原版失效、本版定稿；取消 = 不定稿')) return
        r = await bomFinalize(entry.id, true)
      }
      if (!r.ok) return flash(r.msg || '定稿失败')
      flash('已定稿 · ' + (r.affectedPricing?.note || '')); await onChanged()
    } catch (e) { flash('定稿失败：' + e.message) }
  }
  const unfinalize = async () => { try { await bomUnfinalize(entry.id); flash('已撤销审核，退回复核'); await onChanged() } catch (e) { flash(e.message) } }
  // V2.585 撤回终审两步：已终审对外的版本，成本会计只能「申请」，财务BP批准＝撤出对外（默认）/ 驳回
  const unfinalRequest = async () => {
    const reason = window.prompt('申请撤回终审：写明要改什么、为什么（财务BP据此批准；批准即撤出对外、退回复核）')
    if (!reason || !reason.trim()) return
    try { const r = await bomUnfinalRequest(entry.id, reason.trim()); if (!r.ok) return flash(r.msg || '申请失败'); flash(r.msg || '已提交撤回申请'); await onChanged() } catch (e) { flash(e.message) }
  }
  const unfinalReview = async (approve) => {
    const note = approve ? window.prompt('批准撤回（本版立即撤出对外、退回复核）——备注可空', '') : window.prompt('驳回理由（必填）')
    if (note === null) return
    if (!approve && !note.trim()) return flash('驳回请写明理由')
    try { const r = await bomUnfinalReview(entry.id, approve, note.trim()); if (!r.ok) return flash(r.msg || '操作失败'); flash(r.msg); await onChanged() } catch (e) { flash(e.message) }
  }
  const confirmStep = async (s, on) => {
    try { await bomConfirmStep(entry.id, s, on); flash(on ? '已确认' : '已撤销确认'); await onChanged() }
    catch (e) { flash('操作失败：' + e.message) }
  }
  // V2.875 上游链路价格对不上 → 把这味料的含税价改成上游核算表的全成本（只能改成这一个数，留痕；④要重新确认）
  const alignUpstream = async (up) => {
    if (!window.confirm(`把「${up.matName}」的含税价从 ${fmt(up.priceUsed, 4)} 改成它自己那张核算表的全成本 ${fmt(up.upFull, 4)}？\n\n改后本品成本会重算，④报价核算要重新确认；这次改动会留痕。`)) return
    try { const r = await bomAlignUpstream(entry.id, up.matName); if (!r.ok) return flash(r.msg || '改价失败')
      flash(`已把「${r.matName}」的含税价改为 ${fmt(r.price, 4)} 并留痕，④报价核算请重新确认`); await onChanged() }
    catch (e) { flash('改价失败：' + e.message) }
  }
  const applyGoods = async () => {
    try { const r = await bomApplyGoods(entry.id); if (!r.ok) return flash(r.msg || '采纳失败')
      flash(`已采纳商品版 ${r.changed} 项价/税调整并留痕`); await onChanged() }
    catch (e) { flash('采纳失败：' + e.message) }
  }
  const updateBom = async (files) => {
    if (!files?.length) return
    setSourceBusy('bom')
    try { const r = await bomAttachBomList(entry.id, files[0]); if (!r.ok) return flash(r.msg || '更新失败'); flash('研发 BOM 表已更新'); await onChanged() }
    catch (e) { flash('更新失败：' + e.message) } finally { setSourceBusy('') }
  }
  const updateSheet = async (files) => {
    if (!files?.length || !entry.groupId) return
    setSourceBusy('sheet')
    try {
      const r = await bomReplaceSheet(entry.groupId, entry.approval, files[0])
      if (!r?.ok) return flash(r?.msg || '更新失败')
      flash(`采购核算表已更新：替换 ${r.replaced?.length || 0}、新增 ${r.added?.length || 0}`)
      const next = (r.replaced || []).find(x => x.old === entry.id)
      if (next?.id) await onOpen(next.id); else await onChanged()
    } catch (e) { flash('更新失败：' + e.message) } finally { setSourceBusy('') }
  }
  const renameProduct = async () => {
    const name = window.prompt('修改物料名称', entry.productName)
    if (name == null || !name.trim() || name.trim() === entry.productName) return
    try {
      const r = await bomSetName(entry.id, name.trim())
      if (!r.ok) return flash(r.msg || '修改失败')
      flash(`物料名称已修改${r.renamed > 1 ? `（连 ${r.renamed} 版）` : ''}`)
      await onChanged()
    }
    catch (e) { flash('修改失败：' + e.message) }
  }

  const mats = curMats.filter(m => m.seg === '原料')
  const packs = curMats.filter(m => m.seg === '包材')
  return (
    <>
      <div className="head">
        <div>
          <div className="h-sub" style={{ marginBottom: 6 }}><a className="lk" onClick={onBack}>‹ 处理审批单</a>　{entry.approval || '—'} / 组{groupNo}</div>
          <div className="h-title bom-detail-title"><span className="bom-product-name">{entry.productName}</span>
            <span className="mono muted bom-cp-code">{entry.cpCode}</span>
            <Kind k={entry.kind} />
            {edit ? <span className="tag werr">编辑中</span> : <span className={archived ? 'tag ok' : 'tag late'}>{archived ? '审核通过' : '复核中'}</span>}
            <select className="bom-product-switch" aria-label="切换产品" value={entry.id} onChange={e => onOpen(+e.target.value)} disabled={!peerProducts.length}>
              <option value={entry.id}>{entry.cpCode}丨{entry.productName}</option>
              {peerProducts.map(p => <option key={p.id} value={p.id}>{p.cpCode}丨{p.productName}</option>)}
            </select>
            {entry.historical && <span className="tag late" title="审核时答 C 归档的历史版本：已初审但不替代当前版、不对外、不动定稿指针；只为让同单的下游能定稿">历史版·不对外</span>}
            {entry.backfill && <span className="bom-gvtag" title={entry.status === '已审核' ? '历史补录：成本会计初审通过即定稿，终审戳为「历史补录」，未经财务BP二道审核' : '历史补录单：照常复核、成本会计初审；初审通过即盖「补录」戳定稿，不经财务BP终审'}>{entry.status === '已审核' ? '补录·无二审' : '补录·待初审'}</span>}
            {entry.imported && <span className="bom-gvtag" title="历史标准成本直接导入：只有五分项、无物料明细；批量确认即已审核，未经财务BP终审">导入·无明细</span>}</div>
          {entry.imported && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', margin: '6px 0' }}>
            这是历史标准成本直接导入的记录：只有五分项（原料/包材/加工费/装卸费/管理费 → 全成本），没有物料明细，所以下面的料表、逐料对比、金蝶用量核对、采购核算表导出都没有内容。数据由 {entry.ack?.reviewer || entry.finalizedBy || '成本会计'} 于 {entry.finalizedAt || ''} 批量确认。</div>}
          {entry.recalc?.applied && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', margin: '6px 0', lineHeight: 1.6 }}>
            <b>✎ 小计已按明细重算</b>（源表小计公式漏行，明细是对的）：
            {(entry.recalc.items || []).map((x, i) => <span key={i}>{i ? '；' : ''}{x.check} 源表 {fmt(x.src, 4)} → 重算 {fmt(x.recalc, 4)}</span>)}
            {(entry.recalc.missing || []).length > 0 && <span>；<b>疑似没被加进小计的料：{entry.recalc.missing.join('、')}</b></span>}
            {(entry.recalc.cascade || []).length > 0 && <span>；{entry.recalc.cascade.map((c, i) => <span key={i}>{i ? '，' : ''}料行「{c.matName}」含税价随上游「{c.upName}」重算 {fmt(c.from)} → {fmt(c.to)}</span>)}</span>}
            。全成本 <b>{fmt(entry.recalc.srcFull)} → {fmt(entry.recalc.full)}</b>（{entry.recalc.diff > 0 ? '+' : ''}{fmt(entry.recalc.diff)}，{(entry.recalc.pct * 100).toFixed(1)}%）。
            台账、财务版/脱敏版导出、对外都用重算值；OA 里采购原表仍是旧数，志鹏传回的财务版才是对的。<b>复核③「确认用量自洽」即认可本次重算。</b></div>}
          {(() => {
            // 落盘公盘状态（V2.524/527）：初审过的记录都显示——落过显文件名；落盘功能上线前初审的老记录显「未落盘」并给「重落公盘」入口
            if (!['初审', '已审核'].includes(entry.status) || entry.imported || entry.historical) return null
            // 只有成品主动落盘（V2.548，甲案）：半成品/复配料随成品文件的上游页带出，不单独落
            if (entry.kind && entry.kind !== '成品') return <div className="h-sub muted">○ {entry.kind}不单独落盘公盘——已随成品文件的上游页带出</div>
            const ob = (entry.audits || []).find(a => (a.field || '').startsWith('落盘公盘'))   // audits 最近在前
            const failed = ob && ob.field === '落盘公盘失败'
            const txt = ob ? (ob.new ?? ob.newValue ?? ob.new_value ?? ob.to ?? '') : ''
            const redo = cfg?.canAudit && <a className="lk" style={{ marginLeft: 8 }} onClick={async () => { const r = await bomOutboxRedo(entry.id); flash(r.ok ? `已落盘：${(r.files || []).join('、')}` : (r.msg || '落盘失败')); await onChanged() }}>{ob ? '重落公盘 ›' : '现在落盘 ›'}</a>
            if (!ob) return <div className="h-sub" style={{ color: 'var(--amber)' }}>○ 未落盘公盘（初审早于落盘功能上线，或落盘未开启）{redo}</div>
            return <div className="h-sub" style={{ color: failed ? 'var(--red)' : 'var(--green)' }}>
              {failed ? '⚠ 落盘公盘失败：' : '✓ 已落盘公盘：'}{txt}{ob.ts || ob.at ? `　${ob.ts || ob.at}` : ''}{redo}
            </div>
          })()}
        </div>
        <div className="bom-detail-actions">
          {!archived && !edit && cfg?.canAudit && !entry.imported && <>
            <details className="bom-action-menu bom-source-menu"><summary>{sourceBusy ? '更新中…' : '更新源表'}</summary><div>
              <button disabled={!!sourceBusy} onClick={e => { e.currentTarget.closest('details').removeAttribute('open'); bomFileRef.current?.click() }}>更新研发 BOM 表</button>
              <button disabled={!!sourceBusy || !entry.groupId} onClick={e => { e.currentTarget.closest('details').removeAttribute('open'); sheetFileRef.current?.click() }}>更新采购核算表</button>
            </div></details>
            <input ref={bomFileRef} type="file" accept=".xlsx,.xls" hidden onChange={e => { updateBom(e.target.files); e.target.value = '' }} />
            <input ref={sheetFileRef} type="file" accept=".xlsx,.xls" hidden onChange={e => { updateSheet(e.target.files); e.target.value = '' }} />
          </>}
          {!archived && !isStd && !edit && cfg?.canAudit && <button className="btn-sec" style={{ color: 'var(--accent)', borderColor: 'var(--accent)' }} onClick={startEdit}>修改价税费</button>}
          {edit && <><button className="btn-pri" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存并留痕'}</button><button className="btn-sec" onClick={cancelEdit}>取消</button></>}
          {!archived && !isStd && !edit && cfg?.canAudit && !entry.isFinal && !entry.historical && <button className="btn-pri" onClick={() => setAuditM(true)} disabled={!entry.stepsOk}
            title={entry.stepsOk ? '完成定性并审核通过' : '请先确认 ③用量自洽 ④报价核算'}>审核通过</button>}
          {!archived && !edit && <details className="bom-action-menu bom-more-menu"><summary aria-label="更多操作">···</summary><div>
            {cfg?.canAudit && <button onClick={e => { e.currentTarget.closest('details').removeAttribute('open'); renameProduct() }}>修改物料名称</button>}
            <button onClick={e => { e.currentTarget.closest('details').removeAttribute('open'); auditRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>查看变更记录</button>
            {entry.active && !entry.voidPending && cfg?.canAudit && <button onClick={e => { e.currentTarget.closest('details').removeAttribute('open'); setVoidM('request') }}>申请作废</button>}
            {isSuper && onDelete && <button className="danger" onClick={e => { e.currentTarget.closest('details').removeAttribute('open'); onDelete({ entryId: entry.id }, `记录 #${entry.id} · ${entry.cpCode} ${entry.productName}`) }}>删除记录</button>}
          </div></details>}
          {entry.needFinalReview && onFinalReview && !edit &&
            <button className="btn-pri" onClick={() => onFinalReview(entry)}
              title="财务BP终审：通过＝盖已审核戳、对外给 BP 报价；也可退回成本会计（须写原因）">⚑ 终审</button>}
          {archived && !edit && cfg?.canAudit && (entry.status === '已审核' && !entry.backfill && !entry.imported && !isSuper
            ? <button className="btn-sec" disabled={entry.unfinalPending} onClick={unfinalRequest}
                title={entry.unfinalPending ? '已申请，待财务BP批准' : '本版已终审对外：撤回要财务BP批准，批准即撤出对外、退回复核'}>{entry.unfinalPending ? '撤回申请待批' : '申请撤回终审'}</button>
            : <button className="btn-sec" onClick={unfinalize} title={entry.status === '已审核' ? '主管理员直接撤回终审（撤出对外、退回复核）' : '撤销初审，退回复核'}>撤销审核</button>)}
          {archived && !edit && cfg?.canExport && !entry.imported && <a className="btn-sec" href={bomExportPrettyUrl(entry.id)}>导出核算表</a>}
          {entry.imported && entry.active && cfg?.canFetch && !edit && <button className="btn-sec" onClick={() => onFill && onFill(entry)}>补明细</button>}
        </div>
      </div>
      <div className="body">
        <div className="bom-crumbs"><a className="lk" onClick={onBack}>成本台账</a> / {entry.productName}</div>
        {entry.staleNote && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10 }}>
          ⚠ <b>{entry.staleNote}</b>：本品所依赖的上游采购核算表被替换过，成本可能已变——已把本品打回<b>未复核</b>，请重新走 ③用量自洽 / ④报价核算 确认。确认后此提醒自动消失。</div>}
        {entry.finalReturn && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10, lineHeight: 1.6 }}>
          ⟲ <b>终审退回</b>（{entry.finalReturn.by} · {entry.finalReturn.at}{entry.finalReturn.via === 'OA' ? ' · 在 OA 退回' : ''}）：<b>{entry.finalReturn.note}</b>
          —— 按原因改好后重新走 ③用量自洽 / ④报价核算，再点「审核通过」；重新初审后此提醒自动消失。</div>}
        {/* 换码承接（V2.440）：本版被新版替代 / 本版替代了旧版 */}
        {entry.obsoleteBy && <div className="banner" style={entry.obsoleteBy.live
          ? { display: 'block', background: 'var(--bg-sub)', color: 'var(--ink-2)', border: '1px solid var(--line)', marginBottom: 10 }
          : { display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10 }}>
          {entry.obsoleteBy.live ? '⊘ ' : '⏳ '}<b>{entry.obsoleteBy.live ? '本版已失效' : '本版待替代'}</b>：被 <a className="lk" onClick={() => onOpen(entry.obsoleteBy.entryId)}>{entry.obsoleteBy.cpCode} {entry.obsoleteBy.productName}</a> 替代（{entry.obsoleteBy.at}，{entry.obsoleteBy.note}）。
          {entry.obsoleteBy.live ? '已退出对外台账，BP 不再拿到本版；记录与留痕照常可查。' : `新版当前「${entry.obsoleteBy.status}」，其终审通过后本版退出对外台账；在此之前 BP 仍用本版。`}</div>}
        {(entry.variants || []).length > 0 && <div className="banner" style={{ display: 'block', background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-line)', marginBottom: 10 }}>
          ⇉ <b>并行版本</b>：与 {entry.variants.map((v, i) => (
            <span key={v.entryId}>{i > 0 ? '、' : ''}<a className="lk" onClick={() => onOpen(v.entryId)}>{v.cpCode} {v.productName}</a>{v.packSpec ? `（${v.packSpec}）` : ''}</span>))}
          是同一产品的不同版本/包装——都对外、互不替代；BP 按 CP 区分。</div>}
        {(entry.replaces || []).length > 0 && <div className="banner" style={{ display: 'block', background: 'var(--bg-sub)', color: 'var(--ink-2)', border: '1px solid var(--line)', marginBottom: 10 }}>
          ⇄ <b>本版替代了 {entry.replaces.length} 个旧版</b>：{entry.replaces.map((c, i) => (
            <span key={c.entryId}>{i > 0 ? '；' : ''}<a className="lk" onClick={() => onOpen(c.entryId)}>{c.cpCode}</a>（{c.why || '—'} · 审核 {c.auditAt || '—'} · 全成本 ¥{fmt(c.fullIncl)}/kg）</span>))}
          。{entry.finalPassed ? '本版已终审，旧版已退出对外台账；引用旧版的 BP 定价方案会收到「成本已更新」提示。' : '本版终审通过后旧版才退出对外台账；BP 那边随之收到「成本已更新」提示。'}</div>}
        {entry.unfinalPending && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10, lineHeight: 1.6 }}>
          ⟲ <b>待批撤回终审</b>：{entry.unfinalReq?.by} 于 {entry.unfinalReq?.at} 申请，理由「{entry.unfinalReq?.reason}」——申请期间本版<b>照常对外</b>；财务BP批准即撤出对外、退回复核。
          {cfg?.canFinalReview && <span style={{ marginLeft: 10 }}><button className="btn-pri" style={{ marginRight: 6 }} onClick={() => unfinalReview(true)}>批准撤出</button><button className="btn-sec" onClick={() => unfinalReview(false)}>驳回</button></span>}</div>}
        {entry.voidPending && <div className="banner" style={{ background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red)', marginBottom: 10 }}>
          ⌦ <b>有待终审的作废申请</b>：{entry.voidReq?.by} 于 {entry.voidReq?.at} 申请作废，理由「{entry.voidReq?.reason}」——
          申请期间本版<b>照常有效</b>，须财务BP终审批准才真作废。{cfg?.canFinalReview
            ? <a className="lk" style={{ marginLeft: 6, fontWeight: 700 }} onClick={() => setVoidM('review')}>去终审 ›</a>
            : <span className="muted"> 等财务BP终审。</span>}</div>}
        <div className="bom-detwrap">
          <div className="bom-detmain">
            <div className="card bom-fgrid">
              {[['研发编码（CP码）', entry.cpCode], ['产品名称', entry.productName],
                ['产品规格', entry.packSpec || '—'], ['ERP物料编码', entry.erpCode || '—'],
                ['客户', entry.customer || '—'],
                ['订单量', entry.orderQty ? fmt(entry.orderQty, 0) + ' kg' : '—'],
                ['生产工厂', entry.supplier || '—'], ['物料类别', <CatCell p={entry} />],
                ['数据来源', entry.origin
                  ? (() => { const full = entry.comp?.full || 0, raw = entry.rawVersion?.full || 0
                    return <span><Origin o={entry.origin} />{entry.rawVersion && <div className="bom-srcline" title="本单财务复核发现问题、改了商务输出的数，台账以财务复核版为准；商务输出留作采购原始参考（原件下载里仍有）">
                      采购原始 {fmt(raw)} → 财务复核 {fmt(full)}
                      {Math.abs(full - raw) > 1e-4
                        ? <span style={{ color: 'var(--stop, #a83529)' }}>（成本会计已改 {(full - raw) > 0 ? '+' : ''}{fmt(full - raw)}）</span>
                        : '（数值未变，仅版本口径）'}</div>}</span> })()
                  : '—'],
                ['初审 / 终审', <span style={{ fontSize: 12, lineHeight: 1.5 }}>
                  初审 {entry.finalizedBy ? entry.finalizedBy + ' · ' : ''}{entry.finalizedAt || '—'}<br />
                  终审 {entry.ack?.by ? entry.ack.by + ' · ' : ''}{entry.ack?.at || '—'}</span>],
                ['对外报价', entry.quotable === null || entry.quotable === undefined
                  ? <span className="muted">待定性</span>
                  : entry.quotable ? <span className="tag ok">建议报价</span>
                    : <span className="tag leak" title={entry.quoteReason}>不建议 · {entry.quoteReason}</span>],
                ['是否有更新版本', hasNewer
                  ? <span className="tag late" title="本产品有更新的版本，本版可能已过时">有更新版（本版第 {myIx + 1}/{versions.length}）</span>
                  : <span className="muted">无 · 当前最新</span>]].map(([k, v]) => (
                <div key={k} className="bom-fcell"><div className="bom-flab">{k}</div>
                  <div className="bom-fval" title={typeof v === 'string' ? v : ''}>{v}</div></div>))}
            </div>

            {/* 四个页签：①②只看不确认（参考材料）；③④要确认。③④确认+定性＝定稿 */}
            <div className="bom-steps4">
              {STEP_DEFS.map(([k, label, needConfirm], i) => (
                <React.Fragment key={k}>
                  {i > 0 && <span className="arw">→</span>}
                  <button className={step === k ? 'on' : ''} onClick={() => setStep(k)}
                    title={needConfirm ? '需确认' : '参考材料，只看不确认'}>{STEP_NO[i]} {label}
                    {k === 'price' && edit ? '（编辑中）' : ''}
                    {needConfirm
                      ? (entry.steps?.[k] ? <span className="bom-stepok" title="已确认">✓</span> : <b className="bom-warndot" title="待确认" />)
                      : <span className="muted" style={{ fontSize: 10, marginLeft: 4 }}>参考</span>}</button>
                </React.Fragment>))}
            </div>

            {/* ① BOM清单：研发给的参考材料，**只看不确认**（业务方定 2026-09-04）*/}
            {step === 'bom' && <BomListSection entry={entry} cfg={cfg} onChanged={onChanged} flash={flash} />}

            {/* ② 工艺流程：同样**只看不确认**；费用参数右栏已有，此处不重复 */}
            {step === 'craft' && <CraftSection entry={entry} />}

            {/* ③ 用量自洽（采购核算表添加量 vs BOM清单用量 逐料比对）*/}
            {step === 'qty' && <>
              {entry.recalc?.applied && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 8 }}>
                ✎ 本表小计已按明细重算（全成本 {fmt(entry.recalc.srcFull)} → {fmt(entry.recalc.full)}{(entry.recalc.missing || []).length ? `，疑似漏加 ${entry.recalc.missing.join('、')}` : ''}）。
                请核一遍料表：漏加的料确实该算进去，就点下方「确认用量自洽」，即认可重算；若那几味料不该在表里，退回研发/采购改表后「替换采购核算表」。</div>}
              <BomCheckSection entry={entry} cfg={cfg} onChanged={onChanged} flash={flash} />
              {!isStd && cfg?.canAudit && <StepConfirm okState={entry.steps?.qty} info={entry.stepsInfo?.qty}
                label="用量自洽无误" onConfirm={(on) => confirmStep('qty', on)} />}
            </>}

            {/* ④ 报价核算（逐料成本 + 「核价」金蝶实采 + 编辑态改税率 + 商品版价税差异）*/}
            {step === 'price' && <>
              {(entry.upstream || []).length > 0 && <UpstreamSection entry={entry} onOpen={onOpen}
                onAlign={!archived && !isStd && !edit && cfg?.canAudit ? alignUpstream : null} />}
              {entry.hasGoodsVersion && <GoodsSection entry={entry} isStd={isStd} canAudit={cfg?.canAudit}
                onApply={applyGoods} />}
              <MatSection no={1} title="原料明细" rows={mats}
                seg="原料" prev={prev} prevMat={prevMat} subtotal={matSub} fullIncl={full} onDrill={onOpen} all={all}
                onPrice={cfg?.canPrice ? setPriceMat : null} edit={edit} onTax={setTax}
                spreads={spreads} onSetType={!isStd && cfg?.canAudit && !edit ? setMatType : null}
                manualUpstream={entry.manualUpstream} upCands={entry.upstreamCandidates}
                onSetUpstream={!isStd && cfg?.canAudit && !edit ? setUpstreamMat : null}
                invoiceRules={cfg?.invoiceRules} onInvoice={edit ? setInvoice : null} />
              <MatSection no={2} title="包材明细" rows={packs} seg="包材" prev={prev} prevMat={prevMat}
                subtotal={packSub} fullIncl={full} onDrill={onOpen} all={all} onPrice={cfg?.canPrice ? setPriceMat : null} edit={edit} onTax={setTax}
                spreads={spreads} onSetType={null} invoiceRules={cfg?.invoiceRules} onInvoice={edit ? setInvoice : null} />
              {!isStd && !edit && cfg?.canAudit && <StepConfirm okState={entry.steps?.price} info={entry.stepsInfo?.price}
                label="报价核算无误" onConfirm={(on) => confirmStep('price', on)} />}
            </>}
            <div className="foot">①BOM清单、②工艺流程＝研发给的<b>参考材料，只看不确认</b>；要签字的是 ③用量自洽（采购核算表 vs BOM清单逐料）和 ④报价核算（逐料核价，可改税率，成本与全成本随之重算并留痕）。<b>③④确认后点右上「审核定稿」填物料类别+是否允许报价，保存即定稿</b>。</div>
          </div>

          <div className="bom-rail">
            <div className="card bom-railcard">
              {/* 两个戳：成本会计初审 → 财务BP终审（已审核，对外开放）*/}
              {entry.firstPassed && <div className={'bom-stamp' + (entry.finalPassed ? ' first' : '')}
                title={'初审：' + entry.finalizedBy + ' ' + entry.finalizedAt}>初审
                <span>{(entry.finalizedAt || '').slice(0, 10)}</span></div>}
              {entry.finalPassed && <div className="bom-stamp fin2" title={'终审：' + (entry.ack?.by || '') + ' ' + (entry.ack?.at || '')}>已审核
                <span>{(entry.ack?.at || '').slice(0, 10)}</span></div>}
              <div className="bom-rh">全成本（含税）<span>{entry.cpCode}</span></div>
              <div className="bom-bigprice">¥ {fmt(full)} <small>/kg</small></div>
              <div className="bom-rspec">{entry.packSpec}　·　核算日期 {entry.calcDate}</div>
              <ErpCodeRow entry={entry} canEdit={!!cfg?.canAudit} onChanged={onChanged} flash={flash} />
              {/* 单位净重不在核算侧体现（业务方定 2026-09-05）：最小销售单元/净重是 BP 定价的事；接口仍带规格解析参考值给 BP */}
              <div className="bom-rlines">
                <RLine k={entry.semi ? '原料' : '原料（含复配料）'} v={`¥ ${fmt(comp.mat)}`} />
                <RLine k="包材" v={`¥ ${fmt(comp.pack)}`} />
                <FeeRow label="加工费" k="mfg" fee={fee} edit={edit} setF={setF} dot={dot('mfg')} src={`源表 ${fmt(entry.srcFee.mfg)}`} />
                <FeeRow label="装卸费" k="load" fee={fee} edit={edit} setF={setF} dot={dot('load')} src={entry.semi ? '小料标准 0.18' : `源表 ${fmt(entry.srcFee.load)}`} />
                <FeeRow label="管理费" k="adm" fee={fee} edit={edit} setF={setF} dot={dot('adm')} src={`源表 ${fmt(entry.srcFee.adm)}`} />
                <div className="bom-rline total"><span>合计</span><b>¥ {fmt(full)}</b></div>
              </div>
              <div className="bom-rdiv" />
              <div className="bom-check">
                <div className="bom-rline"><span>{entry.recalc?.applied ? '源表全成本（已重算）' : '源表全成本'}</span><b>¥ {fmt(entry.comp.srcFull)}</b></div>
                {entry.recalc?.applied && <div className="bom-rline"><span>采购原表写的</span><b style={{ color: 'var(--amber)' }}>¥ {fmt(entry.recalc.srcFull)} <span style={{ fontWeight: 400, fontSize: 11 }}>（{entry.recalc.diff > 0 ? '+' : ''}{fmt(entry.recalc.diff)} 已重算）</span></b></div>}
                <div className="bom-rline"><span>差异</span>{Math.abs(diff) < EPS
                  ? <b className="ok" style={{ color: 'var(--green)' }}>0.00 · 一致</b>
                  : <b style={{ color: 'var(--amber)' }}>{diff > 0 ? '+' : ''}{fmt(diff)} · 参数已调整</b>}</div>
              </div>
              <div className="bom-rdiv" />
              <div className="bom-ah" ref={auditRef}>变更记录</div>
              {(entry.audits || []).length === 0 && <div className="muted" style={{ fontSize: 12 }}>无修改 · 与源表一致</div>}
              {(entry.audits || []).slice(0, 5).map(a => (
                <div key={a.id} className="bom-audit"><div className="bom-audit-h">{a.user} · {a.ts}</div>
                  <div>{a.field}：{a.old_value} → {a.new_value}</div></div>))}
              {entry.isFinal && <><div className="bom-rdiv" />
                <div className="bom-refline">本版为定稿生效版（{entry.finalizedBy} · {entry.finalizedAt}）</div></>}
              {/* 「引用到XX定价」是 BP 工作台的事，核算工作台不放（业务方 2026-09-04） */}
            </div>
          </div>
        </div>
      </div>
      {priceMat && <PriceModal mat={priceMat} entry={entry} cfg={cfg} onClose={() => setPriceMat(null)} flash={flash} />}
      {auditM && <AuditModal entry={entry} onClose={() => setAuditM(false)}
        onDone={async () => { setAuditM(false); await onChanged() }} flash={flash} />}
      {voidM && <VoidModal mode={voidM === 'review' ? 'review' : 'request'}
        target={{ entryId: entry.id, label: entry.productName, voidReq: entry.voidReq }}
        onClose={() => setVoidM('')} onDone={async () => { setVoidM(''); await onChanged() }} flash={flash} />}
    </>
  )
}
function RLine({ k, v }) { return <div className="bom-rline"><span>{k}</span><b>{v}</b></div> }
// 采用某物料编码（补物料编码 + 换码承接确认一条龙）：撞码 → 后端回 needConfirm → 人确认 → 带 confirmObsolete 重发
async function adoptErpCode(entryId, code, flash) {
  let r = await bomSetErpCode(entryId, code)
  if (r && !r.ok && r.needConfirm) {
    if (!window.confirm(r.msg + '\n\n确定 = 写入编码并使旧版失效；取消 = 不改')) return null
    r = await bomSetErpCode(entryId, code, true)
  }
  if (!r || !r.ok) { flash && flash((r && r.msg) || '更新失败'); return null }
  return r
}
// 「核对」弹窗（业务方 2026-09-05 提）：上半＝金蝶物料档案反查候选；下半＝与台账里同物料编码 / 同 CP 的另一条采购核算表**逐料对比**
// （采购核算表添加量 + 含税采购价 + 成本，都有 BOM 清单时再并 BOM 用量），五分项汇总也并排。判断"是同一个东西的新旧版，还是两个不同产品"就看这张表。
function CompareEntriesModal({ entry, lk, others, onAdopt, onClose, flash, canLink, onLinked }) {
  const list = useMemo(() => { const seen = new Set(); return (others || []).filter(o => o && o.entryId && !seen.has(o.entryId) && seen.add(o.entryId)) }, [others])
  const [sel, setSel] = useState(list[0]?.entryId || null)
  const [other, setOther] = useState(null)
  const [busy, setBusy] = useState(false)
  // 并行关联（V2.449）：对比完认定是同一产品的并行版本 → 直接在这里标；已同组 → 可解除
  const linked = !!(other && entry.variantGroup && other.variantGroup === entry.variantGroup)
  const link = async (on) => {
    setBusy(true)
    try { const r = await bomLinkParallel(entry.id, other.id, on); if (!r.ok) return flash(r.msg || '操作失败'); flash(r.msg); onLinked && onLinked(r.entry) }
    catch (e) { flash('操作失败：' + e.message) } finally { setBusy(false) }
  }
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  useEffect(() => {
    if (!sel) return
    setOther(null)
    getBomEntry(sel).then(r => setOther(r.entry)).catch(e => flash('打不开对方采购核算表：' + e.message))
  }, [sel])
  const adopt = async (code) => { setBusy(true); try { await onAdopt(code) } finally { setBusy(false) } }
  // ③ 金蝶 ERP BOM 用量对比（业务方提 2026-09-06）：有物料编码就自动拉；没有就用金蝶候选里第一个正式码试比
  const [kd, setKd] = useState(null)
  const kdCode = entry.erpCode || ((lk && (lk.candidates || []).find(c => !c.erpCode.toUpperCase().startsWith('T')) || (lk && lk.candidates && lk.candidates[0]) || {}).erpCode) || ''
  useEffect(() => {
    setKd(null)
    if (!kdCode) return
    getBomKdBom(entry.id, entry.erpCode ? undefined : kdCode).then(setKd).catch(e => setKd({ ok: false, offline: true, msg: e.message }))
  }, [entry.id, kdCode])
  // 对齐口径同后端 compare_bom：真实编码优先（「XX系列」占位不算），退名字
  const keyOf = (m) => { const c = clean(m.matCode); return (c && !c.includes('系列') && c !== '0') ? 'c:' + c : 'n:' + clean(m.matName) }
  const rows = useMemo(() => {
    if (!other) return []
    const A = entry.materials || [], B = other.materials || []
    const bByKey = new Map(), bByName = new Map()
    B.forEach(m => { bByKey.set(keyOf(m), m); bByName.set(clean(m.matName), m) })
    const used = new Set(), out = []
    const bomQ = (e, m) => { const bl = e.bomList || []; const k = keyOf(m); const hit = bl.find(b => keyOf(b) === k) || bl.find(b => clean(b.matName) === clean(m.matName)); return hit ? hit.qty : null }
    A.forEach(a => {
      let b = bByKey.get(keyOf(a)); if (!b || used.has(b)) b = bByName.get(clean(a.matName))
      if (b && used.has(b)) b = null
      if (b) used.add(b)
      const qa = a.qtyPerKg ?? null, qb = b ? (b.qtyPerKg ?? null) : null
      const pa = a.priceIncl ?? null, pb = b ? (b.priceIncl ?? null) : null
      let st = '仅本单'
      if (b) { const dq = qa != null && qb != null && Math.abs(qa - qb) > 1e-6; const dp = pa != null && pb != null && Math.abs(pa - pb) > 0.005; st = dq && dp ? '用量·价格不同' : dq ? '用量不符' : dp ? '价格不同' : '一致' }
      out.push({ seg: a.seg, name: a.matName, code: a.matCode, qa, qb, pa, pb, ca: a.costExcl, cb: b ? b.costExcl : null, bqa: bomQ(entry, a), bqb: b ? bomQ(other, b) : null, st })
    })
    B.forEach(b => { if (!used.has(b)) out.push({ seg: b.seg, name: b.matName, code: b.matCode, qa: null, qb: b.qtyPerKg ?? null, pa: null, pb: b.priceIncl ?? null, ca: null, cb: b.costExcl, bqa: null, bqb: bomQ(other, b), st: '仅对方' }) })
    const rank = { '仅本单': 0, '仅对方': 0, '用量·价格不同': 1, '用量不符': 2, '价格不同': 3, '一致': 9 }
    return out.sort((x, y) => (rank[x.st] - rank[y.st]) || (x.seg === '包材') - (y.seg === '包材'))
  }, [entry, other])
  const nDiff = rows.filter(r => r.st !== '一致').length
  const showBom = !!(entry.hasBomList && other && other.hasBomList)
  const cls = (st) => st === '一致' ? 'ok' : (st.startsWith('仅') ? 'werr' : 'late')
  const d = (a, b, dec = 4) => (a == null || b == null) ? '' : (Math.abs(a - b) < 1e-9 ? '' : (a - b > 0 ? '▲' : '▼') + fmt(Math.abs(a - b), dec))
  const SUM = [['mat', '原料'], ['pack', '包材'], ['mfg', '加工费'], ['load', '装卸费'], ['adm', '管理费'], ['full', '全成本']]
  return (
    // stopPropagation：本弹窗可能套在「审核定稿」弹窗里，点背景关自己就好，别把外层也关了
    <div className="bom-mask" onClick={e => { e.stopPropagation(); if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(1120px,100%)' }}>
        <div className="bom-mhead"><b>核对 · {entry.productName} <span className="mono">{entry.cpCode}</span></b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">物料编码 <b className="mono">{entry.erpCode || '（未建档）'}</b>　·　规格 {entry.packSpec || '—'}　·　核算日期 {entry.calcDate}　·　{entry.status}</div>
        <div style={{ maxHeight: '70vh', overflowY: 'auto', paddingRight: 4 }}>
          <div className="bom-chkfail" style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', borderColor: 'var(--line)', marginTop: 8 }}>
            <b style={{ fontSize: 12 }}>① 金蝶物料档案（按 CP 反查研发编码）</b>
            <div style={{ marginTop: 6 }}><ErpCandidates lk={lk} onAdopt={adopt} busy={busy} onCompare={(id) => setSel(id)} /></div>
          </div>
          <div style={{ marginTop: 12 }}>
            <b style={{ fontSize: 12 }}>② 金蝶 ERP BOM 用量 vs 采购核算表添加量{kdCode ? <span className="mono muted" style={{ fontWeight: 400 }}>　物料 {kdCode}{!entry.erpCode ? '（按金蝶候选试比，未采用）' : ''}</span> : ''}</b>
            {!kdCode && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>没有物料编码，也没有金蝶候选——无法定位金蝶 BOM。</div>}
            {kdCode && !kd && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>读取金蝶 BOM…</div>}
            {kd && kd.offline && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>金蝶未连接：{kd.msg}</div>}
            {kd && !kd.offline && kd.ok && !kd.hasBom && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{kd.msg}</div>}
            {kd && kd.ok && kd.hasBom && <>
              <div className="muted" style={{ fontSize: 11.5, margin: '4px 0' }}>
                金蝶 BOM <b className="mono">{kd.bom.bomNo}</b>{kd.bom.forbidden ? <span className="tag werr" style={{ marginLeft: 4 }}>已禁用</span> : ''} · 母件单位 {kd.bom.unit || '—'} · 成品率 {kd.bom.yieldRate ?? '—'}%
                {(kd.bom.versions || []).length > 1 ? ` · 共 ${kd.bom.versions.length} 版（取启用最新）` : ''} · 子项 {kd.itemCount} 项，
                <b style={{ color: kd.diffCount ? 'var(--amber)' : 'var(--green)' }}>{kd.diffCount ? `${kd.diffCount} 项有差异` : '全部一致'}</b>
                {kd.note ? <span style={{ color: 'var(--amber)' }}>　⚠ {kd.note}</span> : ''}
                　·　容差 0.0005 kg/kg；金蝶 BOM 里的半成品子项对应采购核算表里「作原料进上层」的行。
              </div>
              <div className="tbl-wrap">
                <table className="bom-ledger" style={{ fontSize: 12 }}>
                  <thead><tr>
                    <th className="th">段</th><th className="th">物料（采购核算表）</th><th className="th">编码</th><th className="th">金蝶子项</th>
                    <th className="th" style={{ textAlign: 'right' }}>采购核算表添加量</th><th className="th" style={{ textAlign: 'right' }}>研发BOM用量</th>
                    <th className="th" style={{ textAlign: 'right' }}>金蝶BOM用量</th><th className="th" style={{ textAlign: 'right' }}>Δ(核算−金蝶)</th><th className="th">判定</th>
                  </tr></thead>
                  <tbody>{kd.rows.map((r, i) => (
                    <tr key={i} className={r.st === '一致' ? '' : 'bom-nbrow'}>
                      <td className="sub">{r.seg || '—'}</td><td>{r.name || <span className="muted">—</span>}</td><td className="mono sub">{r.code || '—'}</td>
                      <td className="sub">{r.kdCode ? <><span className="mono">{r.kdCode}</span> {r.kdName !== r.name ? r.kdName : ''}</> : '—'}</td>
                      <td className="num">{r.ours != null ? Number(r.ours).toFixed(4) : '—'}</td>
                      <td className="num sub">{r.rd != null ? Number(r.rd).toFixed(4) : '—'}</td>
                      <td className="num">{r.kd != null ? Number(r.kd).toFixed(4) : '—'}{r.kdUnit && r.kdUnit !== '千克' ? <span className="muted"> {r.kdUnit}</span> : ''}</td>
                      <td className="num sub" style={{ color: r.delta > 0.0005 ? 'var(--red)' : (r.delta < -0.0005 ? 'var(--green)' : undefined) }}>{r.delta == null ? '' : (r.delta > 0 ? '▲' : r.delta < 0 ? '▼' : '') + Math.abs(r.delta).toFixed(4)}</td>
                      <td><span className={'tag ' + (r.st === '一致' ? 'ok' : (r.st.startsWith('仅') ? 'werr' : 'late'))}>{r.st}</span></td>
                    </tr>))}</tbody>
                </table>
              </div>
            </>}
          </div>
          <div style={{ marginTop: 12 }}>
            <b style={{ fontSize: 12 }}>③ 台账里同物料编码 / 同 CP 的其它采购核算表——逐料对比</b>
            {list.length === 0 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>台账里没有别的记录挂同一物料编码或同一 CP，无需对比。</div>}
            {list.length > 1 && <div className="bom-catpick" style={{ margin: '6px 0' }}>{list.map(o => (
              <button key={o.entryId} className={sel === o.entryId ? 'on' : ''} onClick={() => setSel(o.entryId)}>{o.cpCode} {o.productName}{o.status ? ` · ${o.status}` : ''}</button>))}</div>}
            {list.length === 1 && <div className="muted" style={{ fontSize: 12, margin: '4px 0' }}>对方：<b>{list[0].cpCode} {list[0].productName}</b>{list[0].status ? ` · ${list[0].status}` : ''}{list[0].calcDate ? ` · ${list[0].calcDate}` : ''}</div>}
            {sel && !other && <div className="loading" style={{ padding: 16 }}>读取对方采购核算表…</div>}
            {other && <>
              <div className="tbl-wrap" style={{ marginTop: 6 }}>
                <table className="bom-ledger" style={{ fontSize: 12 }}>
                  <thead><tr><th className="th">含税五分项 元/kg</th>{SUM.map(([k, l]) => <th key={k} className="th" style={{ textAlign: 'right' }}>{l}</th>)}</tr></thead>
                  <tbody>
                    <tr><td><b>本单</b> {entry.cpCode}</td>{SUM.map(([k]) => <td key={k} className="num">{fmt(entry.comp?.[k])}</td>)}</tr>
                    <tr><td><b>对方</b> {other.cpCode}</td>{SUM.map(([k]) => <td key={k} className="num">{fmt(other.comp?.[k])}</td>)}</tr>
                    <tr><td className="muted">本单 − 对方</td>{SUM.map(([k]) => <td key={k} className="num" style={{ color: (entry.comp?.[k] || 0) - (other.comp?.[k] || 0) > 0.005 ? 'var(--red)' : ((entry.comp?.[k] || 0) - (other.comp?.[k] || 0) < -0.005 ? 'var(--green)' : 'var(--ink-3)') }}>{d(entry.comp?.[k], other.comp?.[k], 2) || '—'}</td>)}</tr>
                  </tbody>
                </table>
              </div>
              <div className="muted" style={{ fontSize: 11.5, margin: '8px 0 4px' }}>逐料 {rows.length} 项，<b style={{ color: nDiff ? 'var(--amber)' : 'var(--green)' }}>{nDiff ? `${nDiff} 项有差异` : '全部一致'}</b>；差异排前。添加量＝采购核算表 kg/kg；{showBom ? 'BOM 用量＝研发清单；' : ''}含税价＝研发填的采购价。</div>
              <div className="tbl-wrap">
                <table className="bom-ledger" style={{ fontSize: 12 }}>
                  <thead><tr>
                    <th className="th">段</th><th className="th">物料</th><th className="th">编码</th>
                    <th className="th" style={{ textAlign: 'right' }}>本单添加量</th><th className="th" style={{ textAlign: 'right' }}>对方添加量</th><th className="th" style={{ textAlign: 'right' }}>Δ量</th>
                    {showBom && <><th className="th" style={{ textAlign: 'right' }}>本单BOM</th><th className="th" style={{ textAlign: 'right' }}>对方BOM</th></>}
                    <th className="th" style={{ textAlign: 'right' }}>本单含税价</th><th className="th" style={{ textAlign: 'right' }}>对方含税价</th><th className="th" style={{ textAlign: 'right' }}>Δ价</th>
                    <th className="th" style={{ textAlign: 'right' }}>本单成本</th><th className="th" style={{ textAlign: 'right' }}>对方成本</th><th className="th">判定</th>
                  </tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i} className={r.st === '一致' ? '' : 'bom-nbrow'}>
                      <td className="sub">{r.seg}</td><td>{r.name}</td><td className="mono sub">{r.code || '—'}</td>
                      <td className="num">{r.qa != null ? Number(r.qa).toFixed(4) : '—'}</td><td className="num">{r.qb != null ? Number(r.qb).toFixed(4) : '—'}</td><td className="num sub">{d(r.qa, r.qb, 4)}</td>
                      {showBom && <><td className="num sub">{r.bqa != null ? Number(r.bqa).toFixed(4) : '—'}</td><td className="num sub">{r.bqb != null ? Number(r.bqb).toFixed(4) : '—'}</td></>}
                      <td className="num">{fmt(r.pa)}</td><td className="num">{fmt(r.pb)}</td><td className="num sub">{d(r.pa, r.pb, 2)}</td>
                      <td className="num">{fmt(r.ca, 4)}</td><td className="num">{fmt(r.cb, 4)}</td>
                      <td><span className={'tag ' + cls(r.st)}>{r.st}</span></td>
                    </tr>))}</tbody>
                </table>
              </div>
            </>}
          </div>
        </div>
        <div className="bom-mfoot">
          <span className="muted" style={{ fontSize: 11.5, marginRight: 'auto' }}>换编码点上面「采用」；判定新旧版在「审核定稿」弹窗答 A；认定是并行版本可直接在此标。</span>
          {canLink && other && (linked
            ? <button className="btn-sec" disabled={busy} onClick={() => link(false)} title="解除后再初审会重新问「原版是否失效」">解除并行关联</button>
            : <button className="btn-sec" disabled={busy} style={{ color: 'var(--green)', borderColor: 'var(--green)' }} onClick={() => link(true)}
              title="两条是同一产品的并行版本（不同 CP / 包装），都对外、互不替代">⇉ 标为并行关联</button>)}
          <button className="btn-sec" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
// 金蝶物料档案反查候选列表（按 CP 码 → 研发编码字段）。只展示，成本会计点「采用」才写。onCompare(entryId)：点「已挂 CPxx」跳到与那条的逐料对比。
function ErpCandidates({ lk, onAdopt, busy, compact, onCompare }) {
  if (!lk) return <div className="muted" style={{ fontSize: 11 }}>查金蝶物料档案中…</div>
  if (lk.offline) return <div className="muted" style={{ fontSize: 11 }}>金蝶未连接，无法反查（可手填）</div>
  const cs = lk.candidates || []
  if (!cs.length) return <div className="muted" style={{ fontSize: 11 }}>金蝶物料档案未登此 CP 的研发编码——多为<b>未中试 / 未建档</b>，可先无编码定稿，建档后再补</div>
  return <div style={{ fontSize: 11.5 }}>
    <div style={{ color: 'var(--amber)', marginBottom: 4 }}>⚑ 金蝶物料档案登了此 CP{cs[0].exact ? '' : '（前缀近似，请核对括号后缀）'}，{cs.length > 1 ? `有 ${cs.length} 个物料编码，请选一个：` : '请确认采用：'}</div>
    {cs.map(c => (
      <div key={c.erpCode} style={{ display: 'flex', gap: 6, alignItems: 'center', margin: '3px 0', flexWrap: compact ? 'wrap' : 'nowrap' }}>
        <button className="btn-sec" disabled={busy} style={{ padding: '0 8px', fontFamily: 'monospace' }} onClick={() => onAdopt(c.erpCode)}>采用 {c.erpCode}</button>
        <span>{c.name}{c.spec ? ' · ' + c.spec : ''}{c.category ? ' · ' + c.category : ''}
          {c.rdCode && !c.exact && <span className="mono muted"> · 研发编码 {c.rdCode}</span>}
          {c.erpCode.toUpperCase().startsWith('T') && <span className="tag late" style={{ marginLeft: 4 }}>T 开头</span>}
          {c.forbidden && <span className="tag werr" style={{ marginLeft: 4 }}>金蝶已禁用</span>}
          {(c.inLedger || []).map(x => <span key={x.entryId} className="tag late" style={{ marginLeft: 4, cursor: onCompare ? 'pointer' : undefined }}
            title={`台账里 ${x.cpCode} ${x.productName}（${x.status}）已挂此编码——采用后按「后审核的替代先审核的」提示确认${onCompare ? '；点击看两张采购核算表逐料对比' : ''}`}
            onClick={onCompare ? () => onCompare(x.entryId) : undefined}>已挂 {x.cpCode}{onCompare ? ' ›' : ''}</span>)}
        </span>
      </div>))}
  </div>
}
// 物料编码行（右栏）：有码显示可改；无码 → 自动到金蝶物料档案按 CP 反查候选，成本会计确认采用（业务方 2026-09-05 定）。
// 不是定稿闸：未中试的产品本来就没有编码。
function ErpCodeRow({ entry, canEdit, onChanged, flash }) {
  const [lk, setLk] = useState(null)
  const [busy, setBusy] = useState(false)
  const [cmp, setCmp] = useState(null)      // 「核对」弹窗：null 关 / {first: entryId} 开（first=优先对比哪条）
  const lookup = () => getBomErpLookup(entry.id).then(r => { setLk(r); return r }).catch(e => { const r = { offline: true, msg: e.message }; setLk(r); return r })
  useEffect(() => {
    setLk(null); setCmp(null)
    if (canEdit && !entry.erpCode) lookup()
  }, [entry.id, entry.erpCode, canEdit])
  const adopt = async (code) => {
    setBusy(true)
    try { const r = await adoptErpCode(entry.id, code, flash); if (r) { flash('物料编码已采用 ' + code); setCmp(null); await onChanged() } }
    catch (e) { flash('更新失败：' + e.message) } finally { setBusy(false) }
  }
  // 对比对象＝台账里同物料编码的（sameCode）+ 金蝶候选已挂的（inLedger）+ 同 CP 的换码候选（obsoleteCandidates）
  const others = (first) => {
    const o = [...((lk && lk.sameCode) || []), ...(((lk && lk.candidates) || []).flatMap(c => c.inLedger || [])),
               ...((entry.obsoleteCandidates || []).map(c => ({ entryId: c.entryId, cpCode: c.cpCode, productName: c.productName, status: c.status })))]
    if (first) { const i = o.findIndex(x => x.entryId === first); if (i > 0) o.unshift(o.splice(i, 1)[0]) }
    return o
  }
  const openCmp = async (first) => { if (!lk) await lookup(); setCmp({ first: first || null }) }
  const manual = async () => {
    const v = window.prompt(`补 / 改 ERP物料编码\n产品：${entry.productName}（${entry.cpCode}）`, entry.erpCode || '')
    if (v == null || v.trim() === (entry.erpCode || '')) return
    await adopt(v.trim())
  }
  return <>
    <div className="bom-rline" style={{ margin: '4px 0 2px' }} title="ERP 物料编码＝BP 定价侧关联键；未中试的产品可能尚无编码">
      <span>物料编码{!entry.erpCode && <em className="bom-srcv" style={{ color: 'var(--amber)' }}>未建档</em>}</span>
      <b style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
        <span className="mono">{entry.erpCode || '—'}</span>
        {canEdit && <button className="btn-sec" disabled={busy} onClick={manual} style={{ padding: '0 8px' }}>{entry.erpCode ? '改' : '手填'}</button>}
        {canEdit && <button className="btn-sec" disabled={busy} style={{ padding: '0 8px' }} title="弹窗：金蝶物料档案按 CP 反查 + 与台账里同编码/同CP的采购核算表逐料对比用量与价格"
          onClick={() => openCmp()}>核对</button>}
      </b>
    </div>
    {canEdit && !entry.erpCode && <div style={{ margin: '0 0 8px', padding: '6px 8px', background: 'var(--bg-sub)', borderRadius: 6 }}>
      <ErpCandidates lk={lk} onAdopt={adopt} busy={busy} compact onCompare={(id) => openCmp(id)} />
    </div>}
    {cmp && <CompareEntriesModal entry={entry} lk={lk} others={others(cmp.first)} onAdopt={adopt} onClose={() => setCmp(null)} flash={flash}
      canLink onLinked={async () => { setCmp(null); await onChanged() }} />}
  </>
}
function FeeRow({ label, k, fee, edit, setF, dot, src }) {
  return <div className="bom-rline">
    <span>{label}{dot && <b className="bom-dot" title="已调整，见变更记录" />}</span>
    {edit ? <input className="bom-feeinp" type="number" step="0.01" min="0" value={fee[k]} onChange={e => setF(k, e.target.value)} />
      : <b>¥ {fmt(fee[k])}<em className="bom-srcv">{src}</em></b>}
  </div>
}

// ④报价·物料子类可改（业务方 2026-09-04）：点「类型」→ 弹窗选 原辅料/复配料/自产半成品 → 二次确认。只改原料内部子类。
function MatTypeCell({ m, subType, editable, onSetType }) {
  const [open, setOpen] = useState(false)
  const [pick, setPick] = useState(subType)
  const cls = subType === '包材' ? 'werr' : (subType === '原辅料' ? 'ok' : 'late')
  if (!editable) return <span className={'tag ' + cls}>{subType}</span>   // 不可改时显真实子类，不再写死「包材」
  return (<>
    <span className={'tag ' + cls} style={{ cursor: 'pointer' }} title="点击可改物料子类（只改原料内部：原辅料/复配料/自产半成品）"
      onClick={() => { setPick(subType); setOpen(true) }}>{subType} ▾</span>
    {open && <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) setOpen(false) }}>
      <div className="bom-modal" style={{ width: 'min(440px,100%)' }}>
        <div className="bom-mhead"><b>改物料子类 · {m.matName}</b><span className="bom-x" onClick={() => setOpen(false)}>✕</span></div>
        <div className="bom-msub">编码 <b className="mono">{m.matCode || '—'}</b>　·　当前 <b>{subType}</b>　·　只改原料内部子类，<b>不动成本金额</b></div>
        <div style={{ padding: '10px 2px 4px' }}>
          {['原辅料', '复配料', '自产半成品'].map(t => (
            <label key={t} style={{ display: 'block', padding: '6px 4px', cursor: 'pointer', fontSize: 14 }}>
              <input type="radio" checked={pick === t} onChange={() => setPick(t)} style={{ marginRight: 8 }} />{t}
              {t === subType && <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>（当前）</span>}
              {t === '原辅料' && <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>普通采购料·不下钻</span>}
              {t !== '原辅料' && <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>自产·尝试下钻子采购核算表</span>}</label>))}
        </div>
        {pick !== subType && <div className="banner err" style={{ margin: '8px 0' }}>二次确认：把「{m.matName}」从「{subType}」改为「<b>{pick}</b>」？只改分类、五分项成本不变。</div>}
        <div className="bom-mfoot">
          <button className="btn-sec" onClick={() => setOpen(false)}>取消</button>
          <button className="btn-pri" disabled={pick === subType} onClick={() => { onSetType(m, pick); setOpen(false) }}>确认改为「{pick}」</button>
        </div>
      </div>
    </div>}
  </>)
}

// 指认上游选择器（V2.543）：点开菜单指认这行复配料/半成品对应台账里的哪个子表产品——候选只列同组产品，选后按其现全成本重算本品成本
function UpItem({ active, onClick, children }) {
  return <div onMouseDown={onClick} className="bom-upitem"
    style={{ padding: '4px 8px', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      background: active ? 'var(--accent-weak, #eef4ff)' : 'transparent', color: active ? 'var(--accent)' : 'inherit' }}>
    {active ? '✓ ' : ''}{children}
  </div>
}
function UpstreamPicker({ matName, cur, cands, onPick }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const curCand = cands.find(c => c.productKey === cur)
  const linked = cur && cur !== '__none__'
  const label = cur === '__none__' ? '外购·非上游'
    : curCand ? '↗ ' + curCand.cpCode + ' ' + curCand.productName
    : '⚠ 指认上游'
  const pick = v => { setOpen(false); onPick(matName, v) }
  return (
    <div ref={ref} className="bom-uppick" style={{ marginTop: 3, position: 'relative', display: 'inline-block' }}>
      <a className="lk" onClick={() => setOpen(o => !o)}
        style={{ fontSize: 10.5, color: linked ? 'var(--accent)' : (cur === '__none__' ? 'var(--ink-3)' : 'var(--stop, #a83529)') }}
        title="这行复配料/半成品自动没连上台账里的子采购核算表——点开从同组产品里指认它对应哪个，连上后按其现全成本重算本品成本">
        {label} ▾
      </a>
      {open && (
        <div style={{ position: 'absolute', zIndex: 30, top: '100%', left: 0, minWidth: 220, maxWidth: 320,
          background: 'var(--card, #fff)', border: '1px solid var(--line, #ddd)', borderRadius: 6,
          boxShadow: '0 4px 16px rgba(0,0,0,.14)', padding: 4, fontSize: 11.5 }}>
          <UpItem active={!cur} onClick={() => pick('')}>自动匹配（按名/CP）</UpItem>
          <UpItem active={cur === '__none__'} onClick={() => pick('__none__')}>外购·非上游（不重算）</UpItem>
          <div style={{ borderTop: '1px solid var(--line,#eee)', margin: '3px 0' }} />
          {cands.length === 0
            ? <div className="muted" style={{ padding: '4px 8px', fontSize: 10.5 }}>同组没有可指认的子表产品</div>
            : cands.map(c => (
              <UpItem key={c.productKey} active={cur === c.productKey} onClick={() => pick(c.productKey)}>
                {c.cpCode} {c.productName}<span className="muted" style={{ marginLeft: 6 }}>¥{Number(c.fullIncl || 0).toFixed(2)}</span>
              </UpItem>
            ))}
        </div>
      )}
    </div>
  )
}
function MatSection({ no, title, rows, seg, prev, prevMat, subtotal, fullIncl, onDrill, all, onPrice, edit, onTax, spreads, onSetType, manualUpstream, upCands, onSetUpstream, invoiceRules, onInvoice }) {
  return (
    <div className="card bom-sect">
      <div className="bom-secthead"><span className="bom-no">{no}</span><b>{title}</b></div>
      <div className="tbl-wrap" style={{ border: 'none', overflowX: 'auto' }}>
        <table style={{ tableLayout: 'fixed', minWidth: edit ? 1040 : 940 }}>
          <colgroup>{(edit ? MAT_COLS_EDIT : MAT_COLS).map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead><tr>
          <th className="th">类型</th><th className="th">{seg === '包材' ? '包材编码' : '物料编码'}</th><th className="th">物料名称</th>
          <th className="th">型号</th><th className="th">单位</th>
          <th className="th" style={{ textAlign: 'right' }}>{seg === '包材' ? '用量' : '添加量 kg/kg'}</th>
          <th className="th" style={{ textAlign: 'right' }}>含税采购价</th><th className="th" style={{ textAlign: 'right' }}>税率</th>
          <th className="th">发票类型</th>
          <th className="th" style={{ textAlign: 'right' }}>成本含税</th>
          <th className="th" style={{ textAlign: 'right' }}>占比</th><th className="th">说明</th>
          <th className="th">核价</th>
        </tr></thead><tbody>
          {rows.map((m, i) => {
            const p = prevMat(m.matName)
            const isPack = seg === '包材'
            // 物料子类：人工覆盖(m.subType)优先，否则按名字/编码建议——名带半成品/复配料/复合 或 SZF 码 → 复配料，否则原辅料。
            const autoNested = /半成品|复配料|复合/.test(m.matName || '') || (m.matCode || '').startsWith('SZF')
            const subType = isPack ? '包材' : (m.subType || (autoNested ? '复配料' : '原辅料'))
            const nested = !isPack && subType !== '原辅料'      // 复配料/自产半成品 → 尝试下钻子采购核算表
            const semiEntry = nested ? (all || []).find(x => (m.priceIncl > 0) && Math.abs((x.comp?.full || 0) - m.priceIncl) < 0.02) : null
            const qMark = p && Math.abs((m.qtyPerKg || 0) - (p.qtyPerKg || 0)) > 1e-9 ? ((m.qtyPerKg > p.qtyPerKg) ? 'up' : 'down') : null
            const pMark = p && Math.abs((m.priceIncl || 0) - (p.priceIncl || 0)) > 1e-9 ? ((m.priceIncl > p.priceIncl) ? 'up' : 'down') : null
            const dCost = p ? ((m.costExcl || 0) - (p.costExcl || 0)) * GROSS : 0
            const specBrand = [m.spec && m.spec !== '0' ? m.spec : '', m.brand && m.brand !== '0' ? m.brand : ''].filter(Boolean).join(' · ')
            const note = [m.moq ? '起订：' + m.moq : '', (m.priceNote || '').replace(/\|/g, '；')].filter(Boolean).join('　')
            const sp = spreads && (m.matCode || '').trim() ? spreads[(m.matCode || '').trim()] : null
            const bigSpread = sp && sp.spread > 0.15
            return (
              <tr key={i}>
                <td><MatTypeCell m={m} subType={subType} editable={!isPack && !!onSetType} onSetType={onSetType} /></td>
                <td className="mono">{m.matCode || '—'}</td>
                <td style={{ fontWeight: 600, ...NOWRAP }} title={m.matName}>{semiEntry
                  ? <a className="lk" onClick={() => onDrill(semiEntry.id)}>{m.matName} ↗ 子采购核算表</a>
                  : <>{m.matName}{nested && <span className="muted" style={{ fontSize: 10, marginLeft: 6 }}>{subType}·台账无子表</span>}</>}
                  {/* 手动指认上游（V2.543）：复配料/半成品行自动没连上台账子表时才提示指认；已自动连上(semiEntry)且无人工指认就不打扰。候选只列同组产品 */}
                  {onSetUpstream && nested && !(semiEntry && !(manualUpstream || {})[m.matName]) && (
                    <UpstreamPicker matName={m.matName} cur={(manualUpstream || {})[m.matName] || ''}
                      cands={upCands || []} onPick={onSetUpstream} />
                  )}</td>
                <td className="muted" style={NOWRAP} title={m.model}>{m.model && m.model !== '0' ? m.model : '—'}</td>
                <td className="muted">{m.unit || '—'}</td>
                <td className="num">{(m.qtyPerKg ?? 0).toFixed(4)}{qMark && <Tri d={qMark} title={`添加量较上一版（${prev?.calcDate}）：${(p.qtyPerKg ?? 0).toFixed(4)} → ${(m.qtyPerKg ?? 0).toFixed(4)}`} />}</td>
                <td className="num">{fmt(m.priceIncl)}{pMark && <Tri d={pMark} title={`含税价较上一版（${prev?.calcDate}）：${fmt(p.priceIncl)} → ${fmt(m.priceIncl)}`} />}</td>
                <td className={'num' + (edit ? ' bom-editcell' : '')}>{edit && onTax
                  ? <span className="bom-taxedit"><input type="number" step="1" min="0" value={m.taxRate != null ? +(m.taxRate * 100).toFixed(2) : ''} onChange={e => onTax(m, e.target.value)} />%</span>
                  : <span className="muted">{m.taxRate != null ? (m.taxRate * 100).toFixed(0) + '%' : '—'}</span>}</td>
                <td className={edit ? 'bom-editcell' : ''}>{edit && onInvoice
                  ? <select className="bom-invoice-select" value={m.invoiceType || ''} onChange={e => onInvoice(m, e.target.value)} title="改发票类型→按其算法重算成本不含税（基础数据可维护）">
                      {m.invoiceType && !(invoiceRules || []).some(r => r.type === m.invoiceType) && <option value={m.invoiceType}>{m.invoiceType}</option>}
                      {(invoiceRules || []).map(r => <option key={r.type} value={r.type}>{r.type}</option>)}
                    </select>
                  : <span className="muted" style={{ fontSize: 12 }}>{m.invoiceType || '—'}</span>}</td>
                <td className="num" style={{ fontWeight: 600 }}>{fmt((m.costExcl || 0) * GROSS)}
                  {!edit && p && Math.abs(dCost) > EPS && <span className={'bom-cbadge ' + (dCost > 0 ? 'up' : 'down')} title={`成本含税较上一版（${prev?.calcDate}）${dCost > 0 ? '+' : ''}${fmt(dCost, 4)} 元/kg`}>{dCost > 0 ? '▲' : '▼'}{fmt(Math.abs(dCost))}</span>}
                  {!edit && !p && prev && <span className="bom-cbadge new" title="较上一版新增物料">新增</span>}</td>
                <td className="num muted">{fullIncl ? pct(((m.costExcl || 0) * GROSS) / fullIncl) : '—'}</td>
                <td style={NOWRAP} title={note ? specBrand + '　' + note : specBrand}>{specBrand ? <span>{specBrand}{note && <span className="bom-noteic"> ⓘ</span>}</span>
                  : (note ? <span className="bom-noteic">ⓘ</span> : '—')}</td>
                <td>{(m.matCode || '').trim() && onPrice
                  ? <a className="lk" style={{ whiteSpace: 'nowrap' }} onClick={() => onPrice(m)} title={bigSpread ? `⚠ 同编码在别的产品里研发定价差异较大：${fmt(sp.min)}~${fmt(sp.max)}（跨 ${sp.count} 处，差 ${(sp.spread * 100).toFixed(0)}%）——点开 BOM反查看` : '查金蝶实采价 / BOM反查同编码'}>核价 ↗{bigSpread && <span style={{ color: 'var(--stop, #a83529)', marginLeft: 3 }} title="研发跨产品同料定价差异大">●</span>}</a>
                  : <span className="muted" style={{ fontSize: 11 }}>—</span>}</td>
              </tr>
            )
          })}
          <tr className="bom-subrow"><td colSpan={9}>{seg}小计</td>
            <td className="num" style={{ fontWeight: 700 }} title="含税">{fmt((subtotal || 0) * GROSS)}</td>
            <td className="num muted">{fullIncl ? pct(((subtotal || 0) * GROSS) / fullIncl) : ''}</td>
            <td colSpan={2}></td></tr>
        </tbody></table>
      </div>
    </div>
  )
}
function Tri({ d, title }) { return <span className={'bom-tri ' + d} title={title}>{d === 'up' ? '▲' : '▼'}</span> }

// 复核步骤确认条：核对无误后确认，两步都确认才能定稿。
function StepConfirm({ okState, info, label, onConfirm }) {
  return (
    <div className="bom-stepconfirm">
      {okState
        ? <><span className="tag ok">✓ 已确认 · {label}</span>
          <span className="muted" style={{ fontSize: 11 }}>{info?.by} · {info?.at}</span>
          <span style={{ flex: 1 }} />
          <button className="btn-sec" onClick={() => onConfirm(false)}>撤销确认</button></>
        : <><span className="tag werr">待确认</span>
          <span className="muted" style={{ fontSize: 11 }}>核对无误后点右侧确认（两步都确认才能定稿）</span>
          <span style={{ flex: 1 }} />
          <button className="btn-pri" onClick={() => onConfirm(true)}>确认「{label}」</button></>}
    </div>
  )
}

// 成本会计商品版面板：脱敏公开版（删 型号/规格/供应商三列），可能调过价/税。
// 商品版不缺料（删的是列不是行）；此处只呈现它相对采购商务版底稿的价/税差异，供成本会计核对后「采纳」覆盖到底稿。
function GoodsSection({ entry, isStd, canAudit, onApply }) {
  const gv = entry.goodsVersion || {}
  return (
    <div className="card bom-sect bom-goods">
      <div className="bom-secthead"><span className="bom-no" style={{ background: 'var(--purple-bg)', color: 'var(--purple)' }}>版</span>
        <b>成本会计商品版</b>
        <span className="muted" style={{ fontSize: 11 }}>脱敏公开版（删 型号/规格/供应商三列）· 已留档 · 底稿仍以采购商务版为准</span>
        <span style={{ flex: 1 }} /><span className="muted" style={{ fontSize: 11 }}>{gv.srcLabel}　·　{gv.matCount} 料</span>
      </div>
      <div style={{ padding: '12px 14px' }}>
        {!gv.hasDiff
          ? <div className="banner" style={{ background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-line)' }}>
            ✓ 商品版与商务版底稿的价/税<b>完全一致</b>，成本会计未调整——仅作留档，无需采纳。</div>
          : <>
            <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10 }}>
              ⚠ 成本会计商品版对 <b>{gv.diffCount}</b> 处价/税做了调整（相对采购商务版底稿）。核对下表，确认后采纳——覆盖到底稿、重算成本、逐项留痕。</div>
            <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
              <th className="th">物料</th><th className="th">字段</th>
              <th className="th" style={{ textAlign: 'right' }}>商务版（采购）</th>
              <th className="th" style={{ textAlign: 'right' }}>商品版（成本会计）</th></tr></thead><tbody>
              {(gv.diffRows || []).map((r, i) => (<tr key={i}>
                <td style={{ fontWeight: 600 }}>{r.matName}</td><td className="muted">{r.fieldLabel}</td>
                <td className="num bom-old">{r.field === 'taxRate' ? (r.from * 100).toFixed(0) + '%' : fmt(r.from, 4)}</td>
                <td className="num" style={{ fontWeight: 600, color: 'var(--purple)' }}>{r.field === 'taxRate' ? (r.to * 100).toFixed(0) + '%' : fmt(r.to, 4)}</td>
              </tr>))}
            </tbody></table></div>
            {gv.applied
              ? <div className="banner" style={{ background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-line)', marginTop: 10 }}>✓ 已采纳商品版调整（底稿已按上表覆盖，见右栏变更记录）。</div>
              : (!isStd && canAudit && <div style={{ marginTop: 10, textAlign: 'right' }}>
                <button className="btn-pri" onClick={onApply}>采纳商品版 {gv.diffCount} 项价/税调整</button></div>)}
          </>}
      </div>
    </div>
  )
}

// ============ 基础设置（第三页）：公开版脱敏规则 ============
function BomConfig() {
  const [tab, setTab] = useState('mask')             // 三页：脱敏设置 / 发票设置 / 取件机状态（V2.529）
  const [cfg, setCfg] = useState(null)
  const [inv, setInv] = useState(null)               // 发票规则 {rules, modes, canConfig, hint}
  const [outbox, setOutbox] = useState(null)         // 落盘/取件机状态
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savingInv, setSavingInv] = useState(false)
  const [toast, setToast] = useState('')
  const flash = (t) => { setToast(t); setTimeout(() => setToast(''), 2400) }
  useEffect(() => {
    (async () => {
      try { setCfg(await getBomSettings()) } catch (e) { flash('加载失败：' + e.message) }
      try { setInv(await getBomInvoiceRules()) } catch (e) { /* 发票规则失败不挡脱敏页 */ }
      try { setOutbox(await getBomOutboxStatus()) } catch (e) { /* 取件机状态失败不挡别的页 */ }
      setLoading(false)
    })()
  }, [])
  const reloadOutbox = async () => { try { setOutbox(await getBomOutboxStatus()) } catch (e) { flash('刷新失败：' + e.message) } }
  if (loading) return <div className="body"><div className="loading">加载中…</div></div>
  const c = cfg?.config || {}, canConfig = cfg?.canConfig
  const invModes = inv?.modes || ['价税分离', '全额', '买价扣除', '农产品专票']
  const invRules = inv?.rules || []
  const usesRate = (mode) => mode === '买价扣除' || mode === '农产品专票'
  const setInvRow = (i, k, v) => setInv(s => ({ ...s, rules: s.rules.map((r, ix) => ix === i ? { ...r, [k]: v } : r) }))
  const addInvRow = () => setInv(s => ({ ...s, rules: [...(s.rules || []), { type: '', mode: '价税分离', rate: 0 }] }))
  const delInvRow = (i) => setInv(s => ({ ...s, rules: s.rules.filter((_, ix) => ix !== i) }))
  const saveInv = async () => {
    const rows = (inv.rules || []).filter(r => (r.type || '').trim())
      .map(r => ({ type: r.type.trim(), mode: r.mode, rate: usesRate(r.mode) ? (parseFloat(r.rate) || 0) : 0 }))
    if (!rows.length) return flash('请至少配一条发票规则')
    setSavingInv(true)
    try { const r = await setBomInvoiceRules(rows); if (r.ok) { setInv(s => ({ ...s, rules: r.rules })); flash('发票规则已保存') } else flash(r.msg || '保存失败') }
    catch (e) { flash('保存失败：' + e.message) } setSavingInv(false)
  }
  const MODE_FORMULA = { '价税分离': '成本 = 价 ÷ (1+税率) × 添加量', '全额': '成本 = 价 × 添加量', '买价扣除': '成本 = 价 × (1−扣除率) × 添加量', '农产品专票': '有税率则 (价 − 价÷(1+税率)×扣除率)×量，否则 价÷(1+税率)×量' }
  const items = [
    ['hideMatCode', '物料编码', '公开版隐藏金蝶物料编码（给商品经理看时不必带内部编码）'],
    ['hideSupplier', '供应商 / 品牌', '公开版隐藏「谁供的」（成本会计商品版默认删这列）'],
    ['hideModel', '型号', '公开版隐藏物料型号（商品版默认删）'],
    ['hideSpec', '规格', '公开版隐藏物料规格（商品版默认删）'],
    ['hidePriceNote', '报价说明 / 起订量', '公开版隐藏采购备注、阶梯报价'],
  ]
  const toggle = (k) => setCfg(s => ({ ...s, config: { ...s.config, [k]: !s.config[k] } }))
  const save = async () => { setSaving(true); try { const r = await setBomSettings(cfg.config); if (r.ok) flash('已保存') } catch (e) { flash('保存失败：' + e.message) } setSaving(false) }
  return (
    <div className="bomv">
      <div className="head"><div>
        <div className="h-title">BOM报价审核 · 基础设置</div>
        <div className="h-sub">公开版（标准成本台账 / 给 BP 消费）的脱敏规则等全局配置</div></div></div>
      <div className="bom-tabs">
        {[['mask', '脱敏设置'], ['invoice', '发票设置'], ['fetcher', '取件机状态']].map(([k, l]) => (
          <div key={k} className={'bom-tab' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</div>))}
      </div>
      <div className="body">
        {tab === 'mask' && <div className="card bom-sect">
          <div className="bom-secthead"><span className="bom-no">遮</span><b>公开版脱敏（隐藏敏感列）</b>
            <span className="muted" style={{ fontSize: 11 }}>{cfg?.hint}</span></div>
          <div style={{ padding: '6px 14px 14px' }}>
            {!canConfig && <div className="banner info" style={{ marginBottom: 10 }}>只读——需「基础设置」权限方可修改。</div>}
            <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 12 }}>
              ⓘ 现阶段<b>默认全不遮</b>（占位）。开启某列后，标准成本台账与 BP 消费口对外<b>隐藏该列</b>，复核底稿始终保留全量。默认口径＝成本会计手工「商品版」删的三列：型号 / 规格 / 供应商。</div>
            {items.map(([k, lab, desc]) => (
              <label key={k} className="bom-cfgrow">
                <input type="checkbox" checked={!!c[k]} disabled={!canConfig} onChange={() => toggle(k)} />
                <div><b>{lab}</b><div className="muted" style={{ fontSize: 12 }}>{desc}</div></div>
                <span style={{ flex: 1 }} />
                <span className={c[k] ? 'tag werr' : 'tag ok'}>{c[k] ? '公开版隐藏' : '公开版显示'}</span>
              </label>))}
            {canConfig && <div style={{ marginTop: 14, textAlign: 'right' }}>
              <button className="btn-pri" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存设置'}</button></div>}
          </div>
        </div>}

        {tab === 'invoice' && <div className="card bom-sect">
          <div className="bom-secthead"><span className="bom-no">票</span><b>发票类型 → 成本不含税 算法</b>
            <span className="muted" style={{ fontSize: 11 }}>{inv?.hint || '对应采购核算表 N 列公式，可维护'}</span></div>
          <div style={{ padding: '6px 14px 14px' }}>
            {!inv?.canConfig && <div className="banner info" style={{ marginBottom: 10 }}>只读——需「基础设置」权限方可修改。</div>}
            <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 12 }}>
              ⓘ ④报价里改「发票类型」就按这里的算法重算成本不含税。<b>专票</b>价税分离、<b>普票</b>全额、<b>自产自销农产品</b>按买价扣除率、<b>农产品专票</b>有税率则先价税分离再计算抵扣。扣除率（农产品类）默认 9%，可改。</div>
            <div className="tbl-wrap" style={{ border: 'none', overflowX: 'auto' }}><table><thead><tr>
              <th className="th">发票类型</th><th className="th">算法</th><th className="th">扣除率</th><th className="th">公式 · 举例（价税合计 113 元 / 税率 13%）</th><th className="th"></th>
            </tr></thead><tbody>
              {invRules.map((r, i) => (<tr key={i}>
                <td><input value={r.type || ''} disabled={!inv?.canConfig} onChange={e => setInvRow(i, 'type', e.target.value)}
                  placeholder="如 专票" style={{ width: '95%', fontSize: 13, padding: '2px 4px' }} /></td>
                <td><select value={r.mode} disabled={!inv?.canConfig} onChange={e => setInvRow(i, 'mode', e.target.value)} style={{ fontSize: 12 }}>
                  {invModes.map(m => <option key={m} value={m}>{m}</option>)}</select></td>
                <td>{usesRate(r.mode)
                  ? <span><input type="number" step="1" min="0" value={r.rate != null ? +(r.rate * 100).toFixed(2) : ''} disabled={!inv?.canConfig}
                      onChange={e => setInvRow(i, 'rate', (parseFloat(e.target.value) || 0) / 100)} style={{ width: 52, fontSize: 12, padding: '2px 4px' }} />%</span>
                  : <span className="muted" style={{ fontSize: 12 }}>—</span>}</td>
                <td className="muted" style={{ fontSize: 11 }}>{MODE_FORMULA[r.mode]}
                  <div style={{ color: 'var(--go, #1e5945)', marginTop: 3 }}>例：价税合计 <b>113</b> 元 → 成本不含税 <b>{invoiceUnitExcl(113, 0.13, r.mode, r.rate).toFixed(2)}</b> 元{(113 - invoiceUnitExcl(113, 0.13, r.mode, r.rate)) > 0.01 ? `（可抵进项 ${(113 - invoiceUnitExcl(113, 0.13, r.mode, r.rate)).toFixed(2)} 元）` : '（不抵扣）'}</div></td>
                <td>{inv?.canConfig && <a className="lk" style={{ color: 'var(--stop, #a83529)' }} onClick={() => delInvRow(i)}>删除</a>}</td>
              </tr>))}
            </tbody></table></div>
            {inv?.canConfig && <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="btn-sec" onClick={addInvRow}>＋ 加一种发票</button>
              <span style={{ flex: 1 }} />
              <button className="btn-pri" disabled={savingInv} onClick={saveInv}>{savingInv ? '保存中…' : '保存发票规则'}</button></div>}
          </div>
        </div>}

        {tab === 'fetcher' && <div className="card bom-sect">
          <div className="bom-secthead"><span className="bom-no">盘</span><b>取件机状态（初审通过即落盘公盘）</b>
            <span className="muted" style={{ fontSize: 11 }}>{outbox?.note || '成本会计初审通过 → 服务器出财务版/脱敏版 → 取件机同步到公盘'}</span>
            <span style={{ flex: 1 }} /><a className="lk" onClick={reloadOutbox}>↻ 刷新</a></div>
          <div style={{ padding: '6px 14px 14px' }}>
            {!outbox ? <div className="muted">取件机状态取不到（可能无权限或接口异常）。</div> : <>
              <div className="bom-rline"><span>服务器落盘目录</span><b className="mono" style={{ fontSize: 12 }}>{outbox.dir || '—'}</b></div>
              <div className="bom-rline"><span>待取文件数（服务器 outbox 现存）</span><b>{outbox.count ?? 0} 个</b></div>
              <div className="banner info" style={{ marginTop: 10 }}>
                取件机装在办公室常开电脑上（报表取件机同一台），每分钟把这些文件同步到公盘的「3.1 成本核算表\年\月」。落盘目录里有文件、公盘里对应有，就是通的。
                单条记录的落盘/重落，在该记录详情页顶部操作。</div>
              {Object.keys(outbox.fails || {}).length > 0 && <div className="bom-chkfail" style={{ marginTop: 10 }}>
                <b>⚠ 有 {Object.keys(outbox.fails).length} 条落盘失败</b>：
                {Object.entries(outbox.fails).map(([id, f]) => <div key={id}>· 记录 #{id}：{f.msg}（{f.at}，{f.by}）——到该记录详情页点「重落公盘」重试</div>)}</div>}
            </>}
          </div>
        </div>}
      </div>
      {toast && <div className="bom-toast">{toast}</div>}
    </div>
  )
}

// ① BOM清单：研发出品的物料清单**本身**（到位没、哪一版、结构与用量）。③用量自洽才做逐料比对。
function BomListSection({ entry, cfg, onChanged, flash }) {
  const [busy, setBusy] = useState(false)
  const upload = async (files) => {
    if (!files || !files.length) return
    setBusy(true)
    try { const r = await bomAttachBomList(entry.id, files[0]); if (!r.ok) flash(r.msg || '挂载失败'); else { flash('已挂载 BOM清单'); await onChanged() } }
    catch (e) { flash('挂载失败：' + e.message) } finally { setBusy(false) }
  }
  const inh = entry.bomInherited
  // 优先整表原样(bomList含型号/规格/单位/供应商)；沿用历史清单时 bomList 为空，退回用③比对行(只有名/码/用量)兜底列出
  const list = (entry.bomList && entry.bomList.length) ? entry.bomList
    : (entry.bomCheck?.rows || []).filter(r => r.bomQty != null).map(r => ({ seg: r.seg, matCode: r.matCode, matName: r.matName, qty: r.bomQty }))
  const head = <div className="bom-secthead"><span className="bom-no">①</span><b>BOM清单（研发出品）</b>
    <span className="muted" style={{ fontSize: 11 }}>清单到位没 · 哪一版 · 结构与用量（研发原样·只看不确认）</span>
    <span style={{ flex: 1 }} />
    {entry.hasBomList ? <span className="tag ok">本单已附 · {list.length} 味料</span>
      : inh ? <span className="tag late">沿用历史清单 · {list.length} 味料</span>
        : <span className="tag leak">未附清单</span>}</div>
  if (!entry.hasBomList && !list.length) return (
    <div className="card bom-sect">{head}
      <div style={{ padding: 14 }}>
        <div className="banner err" style={{ marginBottom: cfg?.canAttach ? 10 : 0 }}>
          ⚠ 本单未附研发 BOM清单，且按产品编码 <b className="mono">{entry.cpCode || '（无编码）'}</b> 在台账历史也没查到——<b>疑似漏传</b>，请向研发核实补传。</div>
        {cfg?.canAttach && <label className="bom-drop" style={{ maxWidth: 460 }}>{busy ? '解析中…' : '上传该产品的 BOM清单（xlsx）'}
          <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => upload(e.target.files)} /></label>}
      </div>
    </div>)
  return (
    <div className="card bom-sect">{head}
      {inh && <div style={{ padding: '12px 14px 0' }}><div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }}>
        ⓘ 本单未附清单，按产品编码 <b className="mono">{inh.fromCp}</b> 沿用历史清单（第 {inh.fromEntryId} 号 · {inh.fromApproval ? '审批…' + String(inh.fromApproval).slice(-4) + ' · ' : ''}{inh.fromDate}）——改配方必换编码，同编码=配方未变。研发补传本单清单后可上传替换。</div></div>}
      <div className="tbl-wrap" style={{ border: 'none', overflowX: 'auto' }}><table><thead><tr>
        <th className="th">类型</th><th className="th">编码</th><th className="th">物料</th>
        <th className="th">型号</th><th className="th">规格</th><th className="th">单位</th><th className="th">供应商</th>
        <th className="th" style={{ textAlign: 'right' }}>用量</th>
      </tr></thead><tbody>
        {list.map((r, i) => (<tr key={i}>
          <td><span className={'tag ' + (r.seg === '包材' ? 'werr' : 'ok')}>{r.matType || r.seg || '原料'}</span></td>
          <td className="mono">{r.matCode || '—'}{r.codeTBD && <span style={{ color: 'var(--amber)', fontSize: 10 }} title="研发BOM该行是占位码(XX系列)，请研发补真实编码"> ·待补</span>}</td>
          <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{r.matName}</td>
          <td className="muted" style={{ whiteSpace: 'nowrap' }} title={r.model}>{r.model && r.model !== '0' ? r.model : '—'}</td>
          <td className="muted" style={{ whiteSpace: 'nowrap' }} title={r.spec}>{r.spec && r.spec !== '0' ? r.spec : '—'}</td>
          <td className="muted" style={{ whiteSpace: 'nowrap' }}>{r.unit || '—'}</td>
          <td className="muted" style={{ whiteSpace: 'nowrap' }} title={r.brand}>{r.brand && r.brand !== '0' ? r.brand : '—'}</td>
          <td className="num">{r.qty != null ? r.qty.toFixed(6) : '—'}</td>
        </tr>))}
      </tbody></table></div>
      {cfg?.canAttach && <div className="bom-grpact"><span className="muted" style={{ fontSize: 11 }}>清单版本不对/研发重发了 → 可上传替换</span>
        <span style={{ flex: 1 }} />
        <label className="bom-minifile">{busy ? '解析中…' : '⬆ 上传替换 BOM清单'}
          <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => upload(e.target.files)} /></label></div>}
      <div className="foot" style={{ padding: '0 14px 12px' }}>此步只看研发清单原样（不确认）；逐料用量与采购核算表的比对在③用量自洽。</div>
    </div>
  )
}

// 上游链路（半成品/复配料作原料进上层）：口径 quirk#5——下层「全成本含税」＝本品料行的「含税价」。
// 上游未定稿 / 价格对不上 → 本品**不许先定稿**（成本建在未经确认的数上）；台账里找不到 → 链路不通，警告。
function UpstreamSection({ entry, onOpen, onAlign }) {
  const ups = entry.upstream || []
  const block = entry.upstreamBlock || []
  if (!ups.length) return null
  return (
    <div className="card bom-sect" style={{ borderLeft: '3px solid ' + (block.length ? 'var(--red)' : 'var(--green)') }}>
      <div className="bom-secthead"><span className="bom-no">⛓</span><b>上游链路（半成品 / 复配料）</b>
        <span className="muted" style={{ fontSize: 11 }}>下层「全成本含税」＝本品料行的「含税价」</span>
        <span style={{ flex: 1 }} />
        {block.length ? <span className="tag leak">上游未就绪 · 不能定稿</span> : <span className="tag ok">链路已通</span>}</div>
      {/* 列序按业务方定（2026-09-06，V2.479）：编号 · 物料名称 · 上游含税价 · 本批含税价 · 差异 · 看子采购核算表；上游审核状态并入「差异」列 */}
      <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
        <th className="th">编号</th><th className="th">物料名称</th>
        <th className="th" style={{ textAlign: 'right' }}>上游含税价</th><th className="th" style={{ textAlign: 'right' }}>本批含税价</th>
        <th className="th">差异</th><th className="th"></th>
      </tr></thead><tbody>
        {ups.map((u, i) => {
          const diff = (u.priceUsed || 0) - (u.upFull || 0)
          const st = u.isFinal ? <span className="tag ok">已定稿</span>
            : (u.reviewed ? <span className="tag ok">{u.historical ? '已审·历史版' : (u.backfill ? '已审核·补录' : u.status)}</span>
              : <span className="tag werr">{u.status || '未复核'}·未审核</span>)
          return (<tr key={i} className={(!(u.reviewed ?? u.isFinal) || !u.priceOk) ? 'bom-nbrow' : ''}>
            <td className="mono" title="复配料/半成品的研发编码（台账 CP）">{u.upCp || '—'}</td>
            <td style={{ fontWeight: 600 }}>{u.matName}{u.matchBy === 'CP码' && <div className="muted" style={{ fontSize: 10.5, fontWeight: 400 }} title="料行名字与台账产品名不一致，按型号栏的研发码对上的">台账名「{u.upName}」（按 CP 配上）</div>}</td>
            <td className="num">{fmt(u.upFull)}{u.versions > 1 && <div className="muted" style={{ fontSize: 10.5, fontWeight: 400 }} title="台账里同名多版时的取法：同组 › 同钉钉单 › 定稿版 › 不晚于本单 › 最新版">取{u.pick}{u.upCalcDate ? ` · ${u.upCalcDate}` : ''} · 共 {u.versions} 版</div>}</td>
            <td className="num">{fmt(u.priceUsed)}</td>
            <td>{!u.priceOk
              ? <><span className="num" style={{ color: 'var(--red)', fontWeight: 600 }}>{diff > 0 ? '+' : ''}{fmt(diff, 4)}</span> <span className="tag leak">价格对不上</span> {st}</>
              : <><span className="muted">0.00</span> {st}</>}</td>
            <td style={{ whiteSpace: 'nowrap' }}>{onAlign && !u.priceOk && (u.reviewed ?? u.isFinal) &&
              <button className="btn-sec" style={{ marginRight: 10, padding: '2px 8px', fontSize: 11.5, color: 'var(--accent)', borderColor: 'var(--accent)' }}
                title="把本品这味料的含税价改成上游那张核算表的全成本（留痕；④报价核算要重新确认）" onClick={() => onAlign(u)}>按上游改为 {fmt(u.upFull, 4)}</button>}
              <a className="lk" onClick={() => onOpen(u.entryId)}>看子采购核算表 ›</a></td>
          </tr>)
        })}
      </tbody></table></div>
      {block.length > 0 && <div className="bom-chkfail" style={{ margin: '0 14px 12px' }}>
        <b>⛔ 上游未就绪，本品不能定稿</b>：{block.join('；')}。<br />
        半成品的成本没确认，成品的成本就是建在未确认的数上——先把上游复核定稿，再回来定本品。
        {onAlign && ups.some(u => !u.priceOk && (u.reviewed ?? u.isFinal)) && <><br />价格对不上、而上游已审核的：点该行的「按上游改为…」可把本品用价改成上游全成本（留痕）。</>}</div>}
      <div className="foot" style={{ padding: '0 14px 10px' }}>只列**台账里真有同名（或型号栏研发码同 CP）子采购核算表**的料行；外购原料/包材不在此列（名字带「复合/复配料」的外购件不算上游）。</div>
    </div>
  )
}

// ② 工艺流程：显示 BOM 文件里的「工艺流程」页（工序+细节）+ 工艺决定的费用口径。
function CraftSection({ entry }) {
  const c = entry.craft
  return (
    <div className="card bom-sect">
      <div className="bom-secthead"><span className="bom-no">②</span><b>工艺流程</b>
        <span className="muted" style={{ fontSize: 11 }}>研发 BOM 文件的「工艺流程」页 · 只看不确认</span>
        <span style={{ flex: 1 }} />
        {c ? <span className="tag ok">{c.steps.length} 道工序{c.imageCount ? ` · ${c.imageCount} 图` : ''}</span>
          : <span className="tag werr">未解析到工艺流程</span>}</div>
      <div style={{ padding: '12px 14px' }}>
        {/* 工艺流程来自 BOM 文件里的「工艺流程」页（研发出品，含工序与细节）*/}
        {c ? <>
          <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>
            来自研发 BOM 文件的「工艺流程」页　·　{c.steps.length} 道工序
            {c.head?.reviewer ? `　·　审核人 ${c.head.reviewer}` : ''}{c.head?.approver ? `　·　批准人 ${c.head.approver}` : ''}
            {c.head?.writtenAt ? `　·　编写 ${c.head.writtenAt}` : ''}</div>
          {c.imageCount > 0 && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 8, fontSize: 11.5 }}>
            ⓘ 该工艺流程页含 <b>{c.imageCount}</b> 张图片（工艺照片/示意图）——文字工序已解析如下，<b>图片请下载 BOM 原件查看</b>。</div>}
          <div className="tbl-wrap" style={{ border: 'none', marginBottom: 12 }}><table><thead><tr>
            <th className="th" style={{ width: '18%' }}>工序</th><th className="th">工艺细节</th>
          </tr></thead><tbody>
            {c.steps.map((s, i) => (<tr key={i}>
              <td style={{ fontWeight: 600 }}><span className="bom-craftno">{i + 1}</span>{s.step}</td>
              <td style={{ whiteSpace: 'pre-wrap', fontSize: 12.5 }}>{s.detail || <span className="muted">—</span>}</td>
            </tr>))}
          </tbody></table></div>
        </> : <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }}>
          ⓘ 本单的 BOM 文件里没解析到「工艺流程」页（可能研发没附、或页名/格式不同）。可在①BOM清单上传正确的 BOM 文件后重看。</div>}
        <div className="foot" style={{ padding: '4px 0 0' }}>只看不确认。生产工厂：<b>{entry.supplier || '—'}</b>　·　加工费/装卸费/管理费见右栏。</div>
      </div>
    </div>
  )
}

// 研发两表自洽校验：采购核算表(添加量) vs 研发BOM清单(用量)。缺料/多料/用量不符=BOM结构或用量不一致。
function BomCheckSection({ entry, cfg, onChanged, flash }) {
  const [onlyDiff, setOnlyDiff] = useState(true)
  const [busy, setBusy] = useState(false)
  const ck = entry.bomCheck
  const upload = async (files) => {
    if (!files || !files.length) return
    setBusy(true)
    try { const r = await bomAttachBomList(entry.id, files[0]); if (!r.ok) flash(r.msg || '挂载失败'); else { flash('已挂载 BOM清单，完成自洽校验'); await onChanged() } }
    catch (e) { flash('挂载失败：' + e.message) } finally { setBusy(false) }
  }
  const inherited = entry.bomInherited
  if (!ck) {
    // 本单未附、且按产品编码在台账历史也查不到 → 疑似漏传
    return (
      <div className="card bom-sect">
        <div className="bom-secthead"><span className="bom-no">✓</span><b>用量自洽校验</b>
          <span className="muted" style={{ fontSize: 11 }}>采购核算表 vs 研发 BOM清单：用量 / 缺料 / 多料</span></div>
        <div style={{ padding: '14px' }}>
          <div className="banner err" style={{ marginBottom: cfg?.canAttach ? 10 : 0 }}>
            ⚠ 本单未附研发 BOM清单，且按产品编码 <b className="mono">{entry.cpCode || '（无编码）'}</b> 在台账历史里也没查到——<b>疑似漏传</b>，建议向研发核实补传。（改配方必换编码，同编码历史里能找到就会自动沿用。）</div>
          {cfg?.canAttach && <label className="bom-drop" style={{ maxWidth: 460 }}>{busy ? '解析中…' : '研发补传后，上传该产品的 BOM清单（xlsx）挂载并校验'}
            <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => upload(e.target.files)} /></label>}
        </div>
      </div>
    )
  }
  const s = ck.summary
  const rows = onlyDiff ? ck.rows.filter(r => r.status !== '一致') : ck.rows
  const STAT = { '一致': 'ok', '用量不符': 'werr', '采购核算表缺料': 'leak', '采购核算表多料': 'late' }
  return (
    <div className="card bom-sect">
      <div className="bom-secthead"><span className="bom-no">✓</span><b>用量自洽校验</b>
        <span className="muted" style={{ fontSize: 11 }}>采购核算表 vs 研发 BOM清单：<b>只核对用量</b>（按编码对齐，不判类型）</span>
        <span style={{ flex: 1 }} />
        {inherited && <span className="tag late" style={{ marginRight: 6 }}>沿用历史清单</span>}
        {s.ok ? <span className="tag ok">用量全平 · {s.total} 料</span>
          : <span className="tag werr">{[s.qtyMismatch && `用量不符 ${s.qtyMismatch}`, s.missing && `采购核算表缺料 ${s.missing}`, s.extra && `采购核算表多料 ${s.extra}`].filter(Boolean).join(' · ')}</span>}
        <Seg value={onlyDiff ? 'diff' : 'all'} onChange={v => setOnlyDiff(v === 'diff')} opts={[['diff', '只看差异'], ['all', '全部']]} />
      </div>
      {inherited && <div style={{ padding: '12px 14px 0' }}><div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }}>
        ⓘ 本单未附 BOM清单，按产品编码 <b className="mono">{inherited.fromCp}</b> 沿用历史清单校验（第 {inherited.fromEntryId} 号 · {inherited.fromApproval ? '审批…' + String(inherited.fromApproval).slice(-4) + ' · ' : ''}{inherited.fromDate}）——改配方必换编码，同编码=配方未变。研发补传本单清单后可上传替换。</div></div>}
      {s.ok && onlyDiff
        ? <div style={{ padding: 14 }}><div className="banner" style={{ background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-line)' }}>✓ 采购核算表与研发 BOM清单逐料用量一致（{s.total} 味料全平）——两份表用量对得上。</div></div>
        : <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
          <th className="th">物料</th>
          <th className="th" style={{ textAlign: 'right' }}>采购核算表 添加量</th>
          <th className="th" style={{ textAlign: 'right' }}>BOM清单 用量</th>
          <th className="th" style={{ textAlign: 'right' }}>差</th><th className="th">状态</th>
        </tr></thead><tbody>
          {rows.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 20 }}>无差异</td></tr>}
          {rows.map((r, i) => (<tr key={i}>
            <td style={{ fontWeight: 600 }}>{r.matName}
              {r.matCode && <span className="mono muted" style={{ fontSize: 10, fontWeight: 400, marginLeft: 8 }}>{r.matCode}</span>}</td>
            <td className="num">{r.calcQty == null ? '—' : r.calcQty.toFixed(6)}</td>
            <td className="num">{r.bomQty == null ? '—' : r.bomQty.toFixed(6)}</td>
            <td className="num">{r.diff == null ? '—' : (r.diff > 0 ? '+' : '') + r.diff.toFixed(6)}</td>
            <td><span className={'tag ' + (STAT[r.status] || 'unmap')}>{r.status}</span></td>
          </tr>))}
        </tbody></table></div>}
      <div className="foot" style={{ padding: '0 14px 12px' }}>此步只核对<b>用量对不对得上</b>（按物料编码对齐、不判原料/包材类型）；物料类型的判定与调整在④报价核算。</div>
    </div>
  )
}

// 价格校验弹窗（由原料/包材明细行的「核价」触发）：研发填价 vs 金蝶实采（应付单·近一年）+ BOM反查同编码。
// 分页每页 10 行、不换行；模糊搜索框跨两页签过滤。
const PRICE_PAGE = 10
function PriceModal({ mat, entry, cfg, onClose, flash }) {
  const [tab, setTab] = useState('kd')       // kd=应付单 | bom=BOM反查
  const [kd, setKd] = useState(null)
  const [usage, setUsage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const code = (mat.matCode || '').trim()
  const dev = mat.priceIncl
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  useEffect(() => {
    let live = true; setBusy(true)
    Promise.all([getBomKdPurchase(code, 12).catch(e => ({ ok: false, msg: e.message })),
      getBomMaterialUsage(code, entry.id).catch(e => ({ ok: false, rows: [] }))])
      .then(([k, u]) => { if (!live) return; setKd(k); setUsage(u); setBusy(false) })
    return () => { live = false }
  }, [code, entry.id])
  useEffect(() => { setPage(1) }, [tab, q])
  const devCmp = (v) => { if (v == null || dev == null) return null; const d = v - dev; return Math.abs(d) < 1e-6 ? 'eq' : (d > 0 ? 'up' : 'down') }
  const hit = (arr) => !q.trim() || arr.some(x => String(x == null ? '' : x).toLowerCase().includes(q.trim().toLowerCase()))
  const kdRows = (kd?.rows || []).filter(r => hit([r['单号'], r['供应商'], r['规格'], r['型号'], r['日期'], r.form_name]))
  const usRows = (usage?.rows || []).filter(r => hit([r.productName, r.cpCode, r.matName, r.calcDate]))
  const rows = tab === 'kd' ? kdRows : usRows
  const pages = Math.max(1, Math.ceil(rows.length / PRICE_PAGE))
  const pg = Math.min(page, pages)
  const shown = rows.slice((pg - 1) * PRICE_PAGE, pg * PRICE_PAGE)
  const vsCell = (v) => { const c = devCmp(v); return c == null ? '—' : c === 'eq' ? <span className="tag ok">一致</span> : <span className={c === 'up' ? 'bom-up' : 'bom-down'}>{c === 'up' ? '▲高' : '▼低'} {fmt(Math.abs(v - dev), 2)}</span> }
  // BOM反查·较大差异提示（业务方 2026-09-04）：本料研发填价 + 别的产品同编码的研发价，最高/最低差 >15% → 红条提醒
  const usPrices = [dev, ...(usage?.rows || []).map(r => r.priceIncl)].filter(v => v != null && v > 0)
  const usLo = usPrices.length ? Math.min(...usPrices) : 0
  const usHi = usPrices.length ? Math.max(...usPrices) : 0
  const usSpread = (usPrices.length >= 2 && usLo) ? (usHi - usLo) / usLo : 0
  const bomBigDiff = usSpread > 0.15

  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(820px,100%)' }}>
        <div className="bom-mhead"><b>价格对比 · {mat.matName}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">编码 <b className="mono">{code}</b>　·　研发填含税价 <b style={{ color: 'var(--accent)' }}>{fmt(dev)}</b> 元
          {mat.taxRate != null ? `（税率 ${(mat.taxRate * 100).toFixed(0)}%）` : ''}
          {mat.brand && mat.brand !== '0' ? `　·　研发填品牌 ${mat.brand}` : ''}</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '4px 0 10px', flexWrap: 'wrap' }}>
          <div className="bom-seg">
            <button className={tab === 'kd' ? 'on' : ''} onClick={() => setTab('kd')}>应付单列表{kd?.rows ? `（${kd.rows.length}）` : ''}</button>
            <button className={tab === 'bom' ? 'on' : ''} onClick={() => setTab('bom')}>BOM反查{usage?.rows ? `（${usage.rows.length}）` : ''}</button>
          </div>
          <span style={{ flex: 1 }} />
          <input className="bom-search" style={{ minWidth: 200 }} value={q} onChange={e => setQ(e.target.value)}
            placeholder={tab === 'kd' ? '搜供应商 / 单号 / 规格' : '搜产品 / 物料 / CP码'} />
        </div>
        {busy && <div className="loading" style={{ padding: 20 }}>查询中…</div>}
        {!busy && tab === 'kd' && kd?.offline && <div className="banner err" style={{ marginBottom: 10 }}>金蝶未连接：{kd.msg || '需在服务器连账套后可用'}。可先用「BOM反查」页看研发自身定价一致性。</div>}
        {!busy && tab === 'bom' && bomBigDiff && <div className="banner err" style={{ marginBottom: 10 }}>⚠ 同编码 <b className="mono">{code}</b> 在本台账里<b>研发跨产品定价差异较大</b>：{fmt(usLo)} ~ {fmt(usHi)} 元（差 <b>{(usSpread * 100).toFixed(0)}%</b>，跨 {usPrices.length} 处）——请核实研发填价是否不一致、该以哪个价为准。</div>}
        {!busy && rows.length === 0 && <div className="banner info">{tab === 'kd'
          ? (q.trim() ? '没有匹配的应付记录。' : '近一年金蝶无该编码的已审核应付记录。')
          : (q.trim() ? '没有匹配的记录。' : `本台账里没有别的产品用到编码 ${code}。`)}</div>}
        {!busy && rows.length > 0 && <div className="tbl-wrap"><table className="bom-nowrap">
          {tab === 'kd' ? <>
            <thead><tr><th className="th">单据</th><th className="th">日期</th><th className="th">供应商</th><th className="th">规格/型号</th>
              <th className="th" style={{ textAlign: 'right' }}>数量</th><th className="th" style={{ textAlign: 'right' }}>含税单价</th><th className="th" style={{ textAlign: 'right' }}>vs研发</th></tr></thead>
            <tbody>{shown.map((r, i) => (<tr key={i}>
              <td className="sub">{r.form_name} {r['单号']}</td><td className="sub">{String(r['日期'] || '').slice(0, 10)}</td>
              <td>{r['供应商'] || '—'}</td><td className="muted">{[r['型号'], r['规格']].filter(x => x && x !== '0').join(' / ') || '—'}</td>
              <td className="num">{r['数量'] != null ? fmt(r['数量'], 2) : '—'}</td>
              <td className="num" style={{ fontWeight: 600 }}>{r['含税单价'] != null ? fmt(r['含税单价'], 4) : '—'}</td>
              <td className="num">{vsCell(r['含税单价'])}</td></tr>))}</tbody>
          </> : <>
            <thead><tr><th className="th">产品</th><th className="th">CP码</th><th className="th">核算日期</th><th className="th">物料名称</th>
              <th className="th" style={{ textAlign: 'right' }}>研发含税价</th><th className="th" style={{ textAlign: 'right' }}>税率</th><th className="th" style={{ textAlign: 'right' }}>vs本单</th></tr></thead>
            <tbody>{shown.map((r, i) => (<tr key={i}>
              <td style={{ fontWeight: 600 }}>{r.productName}</td><td className="mono sub">{r.cpCode}</td><td className="sub">{r.calcDate}</td>
              <td className="muted">{r.matName}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(r.priceIncl)}</td>
              <td className="num muted">{r.taxRate != null ? (r.taxRate * 100).toFixed(0) + '%' : '—'}</td>
              <td className="num">{vsCell(r.priceIncl)}</td></tr>))}</tbody>
          </>}
        </table></div>}
        {!busy && rows.length > PRICE_PAGE && <div className="bom-pager">
          <button className="btn-sec" disabled={pg <= 1} onClick={() => setPage(pg - 1)}>‹ 上一页</button>
          <span className="muted">第 {pg} / {pages} 页 · 共 {rows.length} 条</span>
          <button className="btn-sec" disabled={pg >= pages} onClick={() => setPage(pg + 1)}>下一页 ›</button>
        </div>}
        <div className="bom-mfoot"><button className="btn-sec" onClick={onClose}>关闭</button></div>
      </div>
    </div>
  )
}


// ============ 历史标准成本直接导入（V2.512）============
// 业务方定 2026-09-07：历史数据不再一张张传采购核算表，按模板导五分项标准成本；先落「导入待确认」批次，成本会计勾选批量确认即已审核。
function StdImportModal({ cfg, init, onClose, flash, onDone }) {
  const [batch, setBatch] = useState(null)
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState({})
  const [ans, setAns] = useState({})
  const [result, setResult] = useState(null)
  const fileRef = useRef(null)
  const load = (b) => {
    setBatch(b); const s = {}, a = {}
    ;(b?.rows || []).forEach(r => { s[r.row] = !!r.ok; a[r.row] = r.suggest || '' })
    setSel(s); setAns(a)
  }
  useEffect(() => {
    if (init?.batchId) getBomStdImportBatch(init.batchId).then(r => { if (r.ok) load(r.batch); else flash(r.msg || '批次打不开') }).catch(e => flash('打开失败：' + e.message))
  }, [init?.batchId])
  const upload = async (f) => {
    if (!f) return
    setBusy(true); setResult(null)
    try { const r = await bomStdImportUpload(f); if (!r.ok) flash(r.msg || '上传失败'); else { load(r.batch); flash(`已解析 ${r.brief.total} 行：可入 ${r.brief.okCount}${r.brief.badCount ? `，有问题 ${r.brief.badCount}` : ''}${r.brief.needAnswer ? `，${r.brief.needAnswer} 行要答 A/B/C` : ''}`) } }
    catch (e) { flash('上传失败：' + e.message) } finally { setBusy(false) }
  }
  const okRows = (batch?.rows || []).filter(r => r.ok)
  const chosen = okRows.filter(r => sel[r.row])
  const confirm = async () => {
    if (!chosen.length) return flash('先勾选要入台账的行')
    const need = chosen.filter(r => (r.candidates || []).length && !['replace', 'parallel', 'historical'].includes(ans[r.row]))
    if (need.length) return flash(`第 ${need.map(r => r.row).join('、')} 行台账已有同CP/同物料编码的审核版，请先答 A/B/C`)
    setBusy(true)
    try {
      const a = {}; chosen.forEach(r => { if (ans[r.row]) a[r.row] = ans[r.row] })
      const r = await bomStdImportConfirm(batch.batchId, chosen.map(x => x.row), a)
      if (!r.ok) { flash(r.msg || '确认失败'); return }
      setResult(r); flash(`已入台账 ${r.done.length} 条（已审核）${r.failed.length ? `，失败 ${r.failed.length}` : ''}`)
      onDone && onDone()
      if (r.batch) load(r.batch); else setBatch(null)
    } catch (e) { flash('确认失败：' + e.message) } finally { setBusy(false) }
  }
  const discard = async () => {
    if (!batch) return
    if (!window.confirm(`作废这批未确认的 ${batch.rows.length} 行？（不影响已入台账的）`)) return
    const r = await bomStdImportDiscard(batch.batchId); if (!r.ok) flash(r.msg || '作废失败'); else { flash('已作废'); setBatch(null); onDone && onDone() }
  }
  const fmt2 = (v) => (v == null || v === '' ? '—' : Number(v).toFixed(2))
  const allOn = okRows.length > 0 && okRows.every(r => sel[r.row])
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(1180px,100%)', maxHeight: '92vh', overflow: 'auto' }}>
        <div className="bom-mhead"><b>⇪ 导入标准成本（历史数据）</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">历史单不再一张张传采购核算表：按模板填<b>五分项（含税 元/kg）</b>一行一个产品 → 上传成「待确认」批次 → 成本会计勾选<b>批量确认即已审核</b>（终审戳「标准成本导入」，不经财务BP终审）。
          导入行<b>没有物料明细</b>，台账标「导入·无明细」，无采购核算表可导出。勾稽红线：五分项之和必须等于全成本。</div>
        {!batch && <div className="bom-mstep"><span className="bom-mno">1</span><div style={{ flex: 1 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <a className="btn-sec" href={bomStdImportTemplateUrl} title="含示例行与说明页">⤓ 下载模板</a>
            <input ref={fileRef} type="file" accept=".xlsx" style={{ display: 'none' }} onChange={e => { upload(e.target.files?.[0]); e.target.value = '' }} />
            <button className="btn-pri" disabled={busy || !cfg?.canFetch} onClick={() => fileRef.current?.click()} title={cfg?.canFetch ? '' : '需「抓取/录入」权限'}>{busy ? '解析中…' : '上传填好的模板'}</button>
            <span className="muted" style={{ fontSize: 12 }}>必填：CP码、产品名称、原料、包材、加工费、装卸费、管理费、全成本；物料编码/客户/规格/渠道/核算日期/来源单号可选</span>
          </div></div></div>}
        {batch && <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', margin: '8px 0', flexWrap: 'wrap', fontSize: 12.5 }}>
            <b>{batch.fileName}</b><span className="muted">{batch.createdBy} · {batch.createdAt}</span>
            <span className="tag ok">可入 {okRows.length}</span>
            {batch.rows.length - okRows.length > 0 && <span className="tag leak">有问题 {batch.rows.length - okRows.length}（不入）</span>}
            <span style={{ flex: 1 }} />
            <label style={{ cursor: 'pointer' }}><input type="checkbox" checked={allOn} onChange={e => { const s = { ...sel }; okRows.forEach(r => { s[r.row] = e.target.checked }); setSel(s) }} /> 全选可入行</label>
          </div>
          <div className="tbl-wrap" style={{ maxHeight: '52vh', overflow: 'auto' }}><table style={{ fontSize: 12 }}><thead><tr>
            <th className="th"></th><th className="th">行</th><th className="th">CP码</th><th className="th">物料编码</th><th className="th">产品名称</th><th className="th">客户</th><th className="th">渠道</th><th className="th">核算日期</th>
            <th className="th" style={{ textAlign: 'right' }}>原料</th><th className="th" style={{ textAlign: 'right' }}>包材</th><th className="th" style={{ textAlign: 'right' }}>加工费</th><th className="th" style={{ textAlign: 'right' }}>装卸费</th><th className="th" style={{ textAlign: 'right' }}>管理费</th><th className="th" style={{ textAlign: 'right' }}>全成本</th>
            <th className="th">校验</th><th className="th">台账已有同CP/同编码 → 答</th>
          </tr></thead><tbody>
            {batch.rows.map(r => (<tr key={r.row} className={r.ok ? '' : 'bom-nbrow'}>
              <td>{r.ok && <input type="checkbox" checked={!!sel[r.row]} onChange={e => setSel({ ...sel, [r.row]: e.target.checked })} />}</td>
              <td className="mono">{r.row}</td><td className="mono">{r.cpCode || '—'}</td><td className="mono">{r.erpCode || '—'}</td>
              <td style={{ fontWeight: 600 }}>{r.productName}</td><td>{r.customer || '—'}</td><td>{({ ecom: '电商', common: '通品', tob: 'TOB', toc: 'TOC' })[r.channel] || '—'}</td><td className="mono">{r.calcDate || '导入日'}</td>
              <td className="num">{fmt2(r.mat)}</td><td className="num">{fmt2(r.pack)}</td><td className="num">{fmt2(r.mfg)}</td><td className="num">{fmt2(r.load)}</td><td className="num">{fmt2(r.adm)}</td><td className="num" style={{ fontWeight: 600 }}>{fmt2(r.full)}</td>
              <td>{r.ok ? <span className="tag ok">五分项=全成本</span> : <span className="tag leak" title={r.reason}>{r.reason}</span>}</td>
              <td>{(r.candidates || []).length
                ? <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <span className="muted" style={{ fontSize: 11 }}>{r.candidates.map(c => `${c.cpCode} ${c.calcDate || c.auditAt} ¥${Number(c.fullIncl || 0).toFixed(2)}（${c.why}）`).join('；')}</span>
                  <select value={ans[r.row] || ''} onChange={e => setAns({ ...ans, [r.row]: e.target.value })} style={{ fontSize: 11.5 }}>
                    <option value="">请选择…</option>
                    <option value="replace">A 原版失效，本行替代</option>
                    <option value="parallel">B 并行但关联，都对外</option>
                    <option value="historical">C 历史版，只入不对外</option>
                  </select>
                  {r.suggest && !ans[r.row] && <span className="muted" style={{ fontSize: 10.5 }}>建议 {r.suggest === 'historical' ? 'C（本行核算日期更早）' : 'A（本行更新）'}</span>}
                </div>
                : <span className="muted">—</span>}</td>
            </tr>))}
          </tbody></table></div>
          {result && result.failed?.length > 0 && <div className="bom-chkfail" style={{ marginTop: 8 }}>{result.failed.map((f, i) => <div key={i}>· 第 {f.row} 行 {f.cpCode}：{f.msg}</div>)}</div>}
          <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn-pri" disabled={busy || !cfg?.canAudit || !chosen.length} onClick={confirm}
              title={cfg?.canAudit ? '勾选行入台账，直接已审核（终审戳「标准成本导入」）' : '需「审核」权限（成本会计）；批次已保存，成本会计在待办页「导入待确认」里处理'}>
              {busy ? '入账中…' : `确认入台账 ${chosen.length} 行（已审核）`}</button>
            <button className="btn-sec" onClick={() => { setBatch(null); setResult(null) }}>重新上传</button>
            <button className="btn-sec" style={{ color: 'var(--amber)', borderColor: 'var(--amber)' }} onClick={discard}>作废本批</button>
            <span className="muted" style={{ fontSize: 12 }}>{cfg?.canAudit ? '' : '你没有审核权限：批次已保存，等成本会计确认。'}</span>
          </div>
        </>}
      </div>
    </div>
  )
}
// ============ 版本对比 ============
// 版本选择器（V2.698）：输 CP 码 / 产品名 / 物料编码 / 单号搜台账里的已审核版本，点选；与「当前版本」同产品/同物料编码/同名的排最前并标注
function VerPicker({ pool, value, onChange, exclude, rel, placeholder }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const cur = pool.find(v => v.id === value)
  const qq = q.trim().toLowerCase()
  const rk = { '同产品': 0, '同物料编码': 1, '同名': 2 }
  const list = pool.filter(v => v.id !== exclude && (!qq || [v.cpCode, v.productName, v.erpCode, v.approval].some(s => String(s || '').toLowerCase().includes(qq))))
    .map(v => ({ v, r: rel ? rel(v) : null }))
    .sort((a, b) => ((rk[a.r] ?? 9) - (rk[b.r] ?? 9)) || (b.v.calcDate || '').localeCompare(a.v.calcDate || '') || (b.v.id - a.v.id))
    .slice(0, 40)
  return (
    <div ref={ref} style={{ position: 'relative', minWidth: 320 }}>
      <input value={open ? q : (cur ? `${cur.cpCode} · ${cur.productName} · 核算 ${cur.calcDate || '—'}` : '')} placeholder={placeholder}
        onFocus={() => { setOpen(true); setQ('') }} onChange={e => { setQ(e.target.value); setOpen(true) }} style={{ width: '100%' }} />
      {open && <div style={{ position: 'absolute', zIndex: 40, top: '100%', left: 0, right: 0, marginTop: 2, maxHeight: 360, overflowY: 'auto',
        background: 'var(--card, #fff)', border: '1px solid var(--line, #ddd)', borderRadius: 6, boxShadow: '0 4px 16px rgba(0,0,0,.14)', padding: 4 }}>
        {list.length === 0 && <div className="muted" style={{ padding: '6px 8px', fontSize: 12 }}>没有匹配的已审核版本</div>}
        {list.map(({ v, r }) => (
          <div key={v.id} onMouseDown={() => { onChange(v.id); setOpen(false); setQ('') }}
            style={{ padding: '5px 8px', borderRadius: 4, cursor: 'pointer', background: v.id === value ? 'var(--accent-soft)' : 'transparent' }}>
            <div style={{ fontSize: 12.5 }}><b className="mono">{v.cpCode}</b> · {v.productName}{r && <span className="bom-gvtag" style={{ marginLeft: 6 }}>{r}</span>}</div>
            <div className="muted" style={{ fontSize: 11 }}>核算 {v.calcDate || '—'} · {STATUS[v.status]?.txt || v.status} · ¥{fmt(v.comp?.full)}/kg{v.erpCode ? ' · 编码 ' + v.erpCode : ''} · 单号…{(v.approval || '').slice(-6)}</div>
          </div>))}
      </div>}
    </div>
  )
}

// ============ 版本对比（V2.700 重做）============
// 业务方反馈「这个页面不太清晰」。原「完整对比」表头按版本分组、数据却按字段新旧交错排，对不上；同名物料按名字配（两行「桃胶」互相配错，
// 出现 70→78、-2.31 的假差异）；「只看差异/完整对比」两套表来回切；导出按钮不导出。重做为：
//   ① 成本总览：五分项 + 全成本两版并排，变化额与主要原因，一句话结论；
//   ② 逐料对比一张表：每行一种物料，每格「旧 → 新」（没变只写一个数），变化标签，信息变化（编码/型号/规格/品牌/名称）写在物料名下；
//      配对＝真实物料编码 › 名称+型号 › 名称 › 型号+用量（换料），一对一不重复；
//   ③「只看有变化」开关；④ 导出差异清单（CSV，Excel 直接打开）。
const _realCode = c => { const s = String(c || '').trim(); return s && s.length >= 6 && !/系列/.test(s) ? s : '' }
const _eqn = (x, y, eps = 1e-9) => Math.abs((x || 0) - (y || 0)) <= eps
const _blank = v => (v == null || v === '0' || v === 0) ? '' : String(v).trim()
const _norm = s => String(s || '').replace(/\s+/g, '').toLowerCase()

function _cmpRow(seg, a, b) {
  const cA = a ? (a.costExcl || 0) * GROSS : null
  const cB = b ? (b.costExcl || 0) * GROSS : null
  const d = (cB || 0) - (cA || 0)
  const tags = [], info = []
  if (!a) tags.push('新增')
  else if (!b) tags.push('移除')
  else {
    if (_norm(a.matName) !== _norm(b.matName)) { tags.push('换料'); info.push({ lab: '名称', va: a.matName || '—', vb: b.matName || '—' }) }
    if (!_eqn(a.priceIncl, b.priceIncl)) tags.push('调价')
    if (!_eqn(a.qtyPerKg, b.qtyPerKg)) tags.push('调量')
    if (!_eqn(a.taxRate, b.taxRate)) tags.push('税率')
    if (!tags.length && !_eqn(cA, cB, 1e-6)) tags.push('成本变')
    ;[['matCode', '编码'], ['model', '型号'], ['spec', '规格'], ['brand', '品牌']].forEach(([k, lab]) => {
      const va = _blank(a[k]), vb = _blank(b[k])
      if (va !== vb) info.push({ lab, va: va || '—', vb: vb || '—' })
    })
  }
  return { seg, a, b, cA, cB, d, tags, info, changed: tags.length > 0 || info.length > 0 }
}

function pairVersionMats(A, B) {
  const aMats = A.materials || [], bMats = B.materials || []
  const segOf = m => m.seg || '原料'
  const usedA = new Set(), match = new Map()
  const passes = [
    (a, b) => !!_realCode(a.matCode) && _realCode(a.matCode) === _realCode(b.matCode),
    (a, b) => _norm(a.matName) === _norm(b.matName) && _norm(a.model) === _norm(b.model),
    (a, b) => _norm(a.matName) === _norm(b.matName),
    (a, b) => !!_norm(a.model) && _norm(a.model) === _norm(b.model) && _eqn(a.qtyPerKg, b.qtyPerKg),   // 换料：同型号同用量、名称编码变了
  ]
  passes.forEach(ok => bMats.forEach((b, bi) => {
    if (match.has(bi)) return
    const ai = aMats.findIndex((a, i) => !usedA.has(i) && segOf(a) === segOf(b) && ok(a, b))
    if (ai >= 0) { match.set(bi, ai); usedA.add(ai) }
  }))
  const rows = []
  ;['原料', '包材'].forEach(seg => {
    bMats.forEach((b, bi) => { if (segOf(b) === seg) rows.push(_cmpRow(seg, match.has(bi) ? aMats[match.get(bi)] : null, b)) })
    aMats.forEach((a, ai) => { if (segOf(a) === seg && !usedA.has(ai)) rows.push(_cmpRow(seg, a, null)) })
  })
  return rows
}

const _TAG_STY = {
  新增: { color: 'var(--green)', background: 'var(--green-bg)', borderColor: 'var(--green-line)' },
  移除: { color: 'var(--red)', background: 'var(--red-bg)', borderColor: 'var(--red)' },
  调价: { color: 'var(--teal)', background: 'var(--teal-bg)', borderColor: 'var(--teal)' },
  调量: { color: 'var(--accent)', background: 'var(--accent-soft)', borderColor: 'var(--accent)' },
  税率: { color: 'var(--amber)', background: 'var(--amber-bg)', borderColor: 'var(--amber-line)' },
  换料: { color: 'var(--purple)', background: 'var(--purple-bg)', borderColor: 'var(--purple-line)' },
  成本变: { color: 'var(--ink-2)', background: 'var(--bg-sub)', borderColor: 'var(--line)' },
}
const _upC = d => (d > 0 ? 'var(--red)' : 'var(--green)')
const _dTxt = (d, dec = 2) => (Math.abs(d) < Math.pow(10, -dec) / 2 ? '—' : (d > 0 ? '▲ +' : '▼ −') + fmt(Math.abs(d), dec))

function CmpPair({ a, b, dec = 4, pctFmt }) {
  const f = v => (v == null ? '—' : (pctFmt ? (v * 100).toFixed(0) + '%' : fmt(v, dec)))
  if (a != null && b != null && _eqn(a, b, Math.pow(10, -dec) / 2)) return <td className="num">{f(b)}</td>
  return <td className="num" style={{ whiteSpace: 'nowrap' }}>
    <span style={{ color: 'var(--ink-3)', textDecoration: a != null && b != null ? 'line-through' : 'none' }}>{f(a)}</span>
    <span style={{ color: 'var(--ink-3)', margin: '0 5px' }}>→</span>
    <b style={{ color: b == null ? 'var(--ink-3)' : (a == null ? 'var(--ink)' : _upC((b || 0) - (a || 0))) }}>{f(b)}</b></td>
}

function Compare({ entry, all, onBack, backLabel, flash }) {
  // V2.698：台账左上角进入，左「对比版本」右「当前版本」都按 CP 码挑；不限同一产品（换码前后不同 CP 也能比）。
  // 候选池＝台账可见的已初审/已审核版本（历史标准成本导入没有物料明细，不入池）。从详情/深链进来：当前版本＝本条，对比版本＝同产品上一版（没有就同物料编码最近一版）。
  const pool = (all || []).filter(x => !x.imported && (x.materials || []).length)
  const byNewest = (a, b) => (b.calcDate || '').localeCompare(a.calcDate || '') || (b.id - a.id)
  const seedPrev = (e) => {
    if (!e) return null
    const older = x => (x.calcDate || '') < (e.calcDate || '') || ((x.calcDate || '') === (e.calcDate || '') && x.id < e.id)
    const same = pool.filter(x => x.id !== e.id && x.productKey === e.productKey && older(x)).sort(byNewest)
    if (same.length) return same[0].id
    const code = (e.erpCode || '').trim()
    const alt = code ? pool.filter(x => x.id !== e.id && (x.erpCode || '').trim() === code).sort(byNewest) : []
    return alt.length ? alt[0].id : null
  }
  const [bId, setBId] = useState(entry ? entry.id : null)
  const [aId, setAId] = useState(() => seedPrev(entry))
  const [onlyChanged, setOnlyChanged] = useState(true)
  const A = pool.find(v => v.id === aId) || null
  const B = pool.find(v => v.id === bId) || null
  const rel = (x) => {
    if (!B || x.id === B.id) return null
    if (x.productKey === B.productKey) return '同产品'
    if ((B.erpCode || '').trim() && (x.erpCode || '').trim() === (B.erpCode || '').trim()) return '同物料编码'
    if (clean(x.productName) === clean(B.productName)) return '同名'
    return null
  }
  const pickB = (id) => { setBId(id); if (!aId || aId === id) setAId(seedPrev(pool.find(v => v.id === id))) }
  const ready = !!(A && B)
  const rows = useMemo(() => (A && B ? pairVersionMats(A, B) : []), [aId, bId, all])   // eslint-disable-line react-hooks/exhaustive-deps
  const crossCp = ready && A.productKey !== B.productKey
  const verHead = (v, lab) => <span>{lab}<br /><b className="mono" style={{ color: 'var(--ink)' }}>{v.cpCode}</b>
    <span className="muted" style={{ fontWeight: 400 }}> · {(v.calcDate || '').slice(0, 10)}</span></span>

  // 成本总览：五分项 + 全成本；原料/包材的「主要原因」取该段对全成本影响最大的两行
  const topOf = (seg) => rows.filter(r => r.seg === seg && Math.abs(r.d) >= 0.005).sort((x, y) => Math.abs(y.d) - Math.abs(x.d)).slice(0, 2)
  const rowName = r => { const m = r.b || r.a; return (m.matName || '') + (_blank(m.model) ? `（${m.model}）` : '') }
  const overview = !ready ? [] : [
    { lab: '原料（含复配料）', key: 'mat', why: topOf('原料').map(r => `${rowName(r)} ${r.tags.join('/')} ${_dTxt(r.d)}`).join('；') },
    { lab: '包材', key: 'pack', why: topOf('包材').map(r => `${rowName(r)} ${r.tags.join('/')} ${_dTxt(r.d)}`).join('；') },
    { lab: '加工费', key: 'mfg' }, { lab: '装卸费', key: 'load' }, { lab: '管理费', key: 'adm' },
  ].map(o => ({ ...o, va: A.comp[o.key] || 0, vb: B.comp[o.key] || 0, d: (B.comp[o.key] || 0) - (A.comp[o.key] || 0) }))
  const fullA = ready ? A.comp.full || 0 : 0, fullB = ready ? B.comp.full || 0 : 0, dd = fullB - fullA
  const drivers = !ready ? [] : [
    ...overview.filter(o => o.key !== 'mat' && o.key !== 'pack' && Math.abs(o.d) >= 0.005).map(o => ({ txt: o.lab, d: o.d })),
    ...rows.filter(r => Math.abs(r.d) >= 0.005).map(r => ({ txt: `${rowName(r)} ${r.tags.join('/')}`, d: r.d })),
  ].sort((x, y) => Math.abs(y.d) - Math.abs(x.d)).slice(0, 3)
  const nChanged = rows.filter(r => r.changed).length
  const nHidden = onlyChanged ? rows.length - nChanged : 0

  const exportCsv = () => {
    if (!ready) return
    const esc = v => { const s = v == null ? '' : String(v); return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s }
    const n = (v, dec = 4) => (v == null ? '' : Number(v).toFixed(dec))
    const L = []
    L.push(['版本对比', `对比版 ${A.cpCode} ${A.productName} 核算${A.calcDate}`, `当前版 ${B.cpCode} ${B.productName} 核算${B.calcDate}`].map(esc).join(','))
    L.push('')
    L.push(['成本总览（含税 元/kg）', '对比版', '当前版', '变化', '主要原因'].join(','))
    overview.forEach(o => L.push([o.lab, n(o.va), n(o.vb), n(o.d), o.why || ''].map(esc).join(',')))
    L.push(['全成本', n(fullA), n(fullB), n(dd), ''].map(esc).join(','))
    L.push('')
    L.push(['分段', '变化', '物料名称', '物料编码', '型号', '添加量·对比版', '添加量·当前版', '含税价·对比版', '含税价·当前版',
      '税率·对比版', '税率·当前版', '成本含税·对比版', '成本含税·当前版', '对全成本影响', '信息变化'].join(','))
    rows.forEach(r => {
      const m = r.b || r.a
      L.push([r.seg, r.tags.join('/') || (r.info.length ? '信息变化' : '一致'), m.matName, m.matCode, m.model,
        n(r.a?.qtyPerKg), n(r.b?.qtyPerKg), n(r.a?.priceIncl, 2), n(r.b?.priceIncl, 2),
        r.a ? (r.a.taxRate * 100).toFixed(0) + '%' : '', r.b ? (r.b.taxRate * 100).toFixed(0) + '%' : '',
        n(r.cA), n(r.cB), n(r.d), r.info.map(i => `${i.lab}:${i.va}→${i.vb}`).join('；')].map(esc).join(','))
    })
    const blob = new Blob(['﻿' + L.join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const el = document.createElement('a'); el.href = url; el.download = `版本对比_${A.cpCode}_vs_${B.cpCode}.csv`
    document.body.appendChild(el); el.click(); el.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000)
    flash && flash('已导出差异清单（CSV，Excel 可直接打开）')
  }

  const th = (t, right) => <th className="th" style={right ? { textAlign: 'right' } : undefined}>{t}</th>
  return (
    <>
      <div className="head">
        <div><div className="h-title">版本对比{B ? ' · ' + B.productName : ''}</div>
          <div className="h-sub">任选两个已审核版本（按 CP 码挑）· 同产品新旧版、换码前后不同 CP 都能比 · 含税 元/kg</div></div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-sec" onClick={onBack}>{backLabel || '返回采购核算表'}</button>
          <button className="btn-sec" disabled={!ready} onClick={exportCsv} title="导出成本总览 + 逐料对比（CSV，Excel 可直接打开）">导出差异清单</button>
        </div>
      </div>
      <div className="body">
        <div className="bom-crumbs"><a className="lk" onClick={onBack}>成本台账</a> / {B ? B.productName + ' / ' : ''}版本对比</div>
        <div className="card bom-filterbar" style={{ flexWrap: 'wrap', gap: 10 }}>
          <span className="flabel">对比版本</span>
          <VerPicker pool={pool} value={aId} onChange={setAId} exclude={bId} rel={rel} placeholder={B ? '输 CP 码 / 产品名 / 物料编码（同产品的排最前）' : '先选右边的当前版本'} />
          <b style={{ color: 'var(--ink-3)' }}>→</b>
          <span className="flabel">当前版本</span>
          <VerPicker pool={pool} value={bId} onChange={pickB} exclude={aId} placeholder="输 CP 码 / 产品名 / 物料编码" />
          <span style={{ flex: 1 }} />
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', userSelect: 'none' }}>
            <input type="checkbox" checked={onlyChanged} onChange={e => setOnlyChanged(e.target.checked)} />只看有变化</label>
        </div>

        {!ready ? <div className="card" style={{ padding: 28, textAlign: 'center', color: 'var(--ink-3)' }}>
          {B ? '再在左边选一个「对比版本」——同产品、同物料编码的会排在最前。' : '先在右边选「当前版本」（输 CP 码搜），再在左边选要对比的版本。'}</div> : <>
          {crossCp && <div className="banner" style={{ display: 'block', background: 'var(--bg-sub)', color: 'var(--ink-2)', border: '1px solid var(--line)', marginBottom: 10 }}>
            ⇄ <b>跨 CP 对比</b>：{A.cpCode} {A.productName} → {B.cpCode} {B.productName}
            {rel(A) ? `（${rel(A)}）` : '——两条不是同一产品，也没有共用物料编码，确认选对了'}。</div>}

          {/* ① 成本总览 */}
          <div className="card bom-sect">
            <div className="bom-secthead"><span className="bom-no">1</span><b>成本总览</b>
              <span className="muted" style={{ fontSize: 11 }}>五分项 · 含税 元/kg</span></div>
            <div style={{ padding: '4px 16px 12px', fontSize: 14, lineHeight: 1.8 }}>
              全成本 <b>{fmt(fullA)}</b> → <b>{fmt(fullB)}</b>，
              {Math.abs(dd) < 0.005 ? <b>基本不变</b> : <b style={{ color: _upC(dd) }}>{dd > 0 ? '上涨' : '下降'} {fmt(Math.abs(dd))} 元/kg（{fullA ? (dd > 0 ? '+' : '') + ((dd / fullA) * 100).toFixed(1) : '0'}%）</b>}
              {drivers.length > 0 && <span className="muted">　主要来自：{drivers.map((x, i) => <span key={i}>{i ? '；' : ''}{x.txt} <b style={{ color: _upC(x.d) }}>{_dTxt(x.d)}</b></span>)}</span>}
            </div>
            <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
              {th('项目')}
              <th className="th" style={{ textAlign: 'right' }}>{verHead(A, '对比版')}</th>
              <th className="th" style={{ textAlign: 'right' }}>{verHead(B, '当前版')}</th>
              {th('变化', true)}{th('主要原因')}
            </tr></thead><tbody>
              {overview.map(o => (
                <tr key={o.key}>
                  <td style={{ fontWeight: 600 }}>{o.lab}</td>
                  <td className="num" style={{ color: 'var(--ink-3)' }}>{fmt(o.va, 2)}</td>
                  <td className="num">{fmt(o.vb, 2)}</td>
                  <td className="num"><b style={{ color: Math.abs(o.d) < 0.005 ? 'var(--ink-3)' : _upC(o.d) }}>{_dTxt(o.d)}</b></td>
                  <td className="muted" style={{ fontSize: 12 }}>{o.why || ''}</td>
                </tr>))}
              <tr className="bom-subrow">
                <td>全成本（含税）</td>
                <td className="num">{fmt(fullA, 2)}</td><td className="num">{fmt(fullB, 2)}</td>
                <td className="num"><b style={{ color: Math.abs(dd) < 0.005 ? 'var(--ink-3)' : _upC(dd) }}>{_dTxt(dd)}</b></td>
                <td className="muted" style={{ fontSize: 12 }}>{fullA ? `${dd > 0 ? '+' : ''}${((dd / fullA) * 100).toFixed(1)}%` : ''}</td>
              </tr>
            </tbody></table></div>
          </div>

          {/* ② 逐料对比 */}
          <div className="card bom-sect">
            <div className="bom-secthead"><span className="bom-no">2</span><b>逐料对比</b>
              <span className="muted" style={{ fontSize: 11 }}>每格「对比版 → 当前版」，没变的只写一个数 · ▲红＝成本上升 ▼绿＝下降</span>
              <span style={{ flex: 1 }} />
              <span className="muted" style={{ fontSize: 12 }}><b>{nChanged}</b> 行有变化{nHidden > 0 && <>，已隐藏 {nHidden} 行未变化</>}</span></div>
            <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
              {th('变化')}{th('物料')}{th('添加量 kg/kg', true)}{th('含税价', true)}{th('税率', true)}{th('成本（含税）', true)}{th('对全成本影响', true)}
            </tr></thead><tbody>
              {['原料', '包材'].map(seg => {
                const segRows = rows.filter(r => r.seg === seg)
                const shown = onlyChanged ? segRows.filter(r => r.changed) : segRows
                const sA = segRows.reduce((s, r) => s + (r.cA || 0), 0), sB = segRows.reduce((s, r) => s + (r.cB || 0), 0)
                const segChanged = segRows.filter(r => r.changed).length
                return (
                  <React.Fragment key={seg}>
                    <tr><td colSpan={7} style={{ background: 'var(--bg-sub)', fontWeight: 700, fontSize: 12.5 }}>
                      {seg}<span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>{segRows.length} 行 · {segChanged} 行有变化</span></td></tr>
                    {shown.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 12, fontSize: 12 }}>{seg}没有变化</td></tr>}
                    {shown.map((r, i) => {
                      const m = r.b || r.a
                      return (
                        <tr key={seg + i} style={r.tags.includes('移除') ? { opacity: 0.85 } : undefined}>
                          <td style={{ whiteSpace: 'nowrap' }}>{r.tags.length
                            ? r.tags.map(t => <span key={t} className="tag" style={{ ..._TAG_STY[t], marginRight: 4 }}>{t}</span>)
                            : (r.info.length ? <span className="tag" style={_TAG_STY['成本变']}>信息变化</span> : <span className="muted" style={{ fontSize: 12 }}>一致</span>)}</td>
                          <td>
                            <div style={{ fontWeight: 600, textDecoration: r.tags.includes('移除') ? 'line-through' : 'none' }}>{m.matName}</div>
                            <div className="muted mono" style={{ fontSize: 11 }}>{[_blank(m.matCode), _blank(m.model)].filter(Boolean).join(' · ') || '—'}</div>
                            {r.info.map((x, j) => (
                              <div key={j} style={{ fontSize: 11, color: 'var(--amber)' }}>{x.lab}：<span style={{ textDecoration: 'line-through', color: 'var(--ink-3)' }}>{x.va}</span> → {x.vb}</div>))}
                          </td>
                          <CmpPair a={r.a?.qtyPerKg} b={r.b?.qtyPerKg} dec={4} />
                          <CmpPair a={r.a?.priceIncl} b={r.b?.priceIncl} dec={2} />
                          <CmpPair a={r.a?.taxRate} b={r.b?.taxRate} dec={2} pctFmt />
                          <CmpPair a={r.cA} b={r.cB} dec={4} />
                          <td className="num"><b style={{ color: Math.abs(r.d) < 5e-5 ? 'var(--ink-3)' : _upC(r.d) }}>{_dTxt(r.d, 4)}</b></td>
                        </tr>)
                    })}
                    <tr className="bom-subrow">
                      <td colSpan={5}>{seg}小计（含税）</td>
                      <CmpPair a={sA} b={sB} dec={4} />
                      <td className="num"><b style={{ color: Math.abs(sB - sA) < 5e-5 ? 'var(--ink-3)' : _upC(sB - sA) }}>{_dTxt(sB - sA, 4)}</b></td>
                    </tr>
                  </React.Fragment>)
              })}
            </tbody></table></div>
          </div>
          <div className="foot">配对规则：先按物料编码（「XX系列」这类占位码不算），再按名称+型号，再按名称，最后同型号同用量视为「换料」；一对一，不会两行配同一行。成本均为含税口径（不含税 × 1.13，与台账一致）；各行「对全成本影响」相加＝原料/包材小计变化。</div>
        </>}
      </div>
    </>
  )
}

// ============ 作废：申请 / 终审批准（业务方定 2026-09-04）============
// 作废＝**标记**不是删除：记录留着、留痕，只是退出工作区与标准成本库。
// 两步走防一人闭环：成本会计**申请**（理由必填）→ 财务经理**批准**才真作废；申请人不得自批。
// ============ 主管理员密钥删除弹窗（V2.459）============
// 先 dryRun 拿影响面（会删哪些记录/待修/文件、谁的上游会断、谁会恢复、并行组变化），再要理由 + 密钥才真删。
function DeleteModal({ target, label, onClose, onDone, flash }) {
  const [imp, setImp] = useState(null)
  const [err, setErr] = useState('')
  const [reason, setReason] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  useEffect(() => { bomDelete(target, '', '', true).then(r => { if (r.ok) setImp(r); else setErr(r.msg || '取影响面失败') }).catch(e => setErr(e.message)) }, [])
  const go = async () => {
    if (!reason.trim()) return flash('请写删除理由')
    if (!key) return flash('请输入删除密钥')
    if (!window.confirm(`永久删除：${label}\n\n记录 ${imp?.impact?.entries?.length || 0} 条、待修 ${imp?.impact?.pendings?.length || 0}、留档文件 ${imp?.impact?.files || 0}。\n此操作不可恢复，确定？`)) return
    setBusy(true)
    try {
      const r = await bomDelete(target, key, reason.trim())
      if (!r.ok) return flash(r.msg || '删除失败')
      flash(r.msg); await onDone()
    } catch (e) { flash('删除失败：' + e.message) } finally { setBusy(false) }
  }
  const I = imp?.impact
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(760px,100%)' }}>
        <div className="bom-mhead"><b style={{ color: 'var(--red)' }}>🗑 永久删除 · {label}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">主管理员专用。<b>作废是标记不删</b>，这里是真删：记录、留痕、定稿指针、留档文件一起清，只在全局审计留一条「谁删了什么、为什么」。</div>
        {err && <div className="banner err" style={{ marginBottom: 10 }}>{err}</div>}
        {!imp && !err && <div className="loading" style={{ padding: 16 }}>计算影响面…</div>}
        {I && <>
          {imp.keyConfigured === false && <div className="banner err" style={{ display: 'block', marginBottom: 10 }}>服务器未配置删除密钥（conf.ini <span className="k">[bom] delete_key</span>），删除通道关闭。配好后再来。</div>}
          <div style={{ maxHeight: '46vh', overflowY: 'auto' }}>
            <b style={{ fontSize: 12 }}>将删除记录 {I.entries.length} 条{I.publicCount ? <span style={{ color: 'var(--red)' }}>（含 {I.publicCount} 条已审核·对外版！BP 那边会少掉这些成本）</span> : ''}</b>
            <div className="tbl-wrap" style={{ margin: '4px 0 8px' }}>
              <table className="bom-ledger" style={{ fontSize: 12 }}>
                <thead><tr><th className="th">#</th><th className="th">CP码</th><th className="th">产品</th><th className="th">状态</th><th className="th">钉钉单</th></tr></thead>
                <tbody>{I.entries.map(e => (
                  <tr key={e.entryId}><td className="mono sub">{e.entryId}</td><td className="mono">{e.cpCode}</td><td>{e.productName}</td>
                    <td><span className={'tag ' + (e.public ? 'ok' : (e.active ? 'late' : 'unmap'))}>{e.status}{!e.active ? '·已退出' : ''}{e.isFinal ? '·定稿' : ''}</span></td>
                    <td className="sub">{e.approvalNo}</td></tr>))}</tbody>
              </table>
            </div>
            {I.pendings.length > 0 && <div style={{ fontSize: 12, marginBottom: 6 }}><b>待修批次 {I.pendings.length} 个</b>：{I.pendings.map((p, i) => <span key={i}>{i ? '；' : ''}{(p.products || []).join('、') || p.groupId}</span>)}</div>}
            <div style={{ fontSize: 12, marginBottom: 6 }}><b>留档源文件</b> {I.files} 个一并删除（钉钉里的原件不受影响，可重新立项拉回）。</div>
            {I.dependents.length > 0 && <div className="banner" style={{ display: 'block', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 6, fontSize: 12 }}>
              ⚠ <b>{I.dependents.length} 条其它记录把它当上游</b>（半成品/复配料被引用）：{I.dependents.map(d => `${d.cpCode} ${d.productName}（用 ${d.uses.join('、')}）`).join('；')}。删掉后它们的上游链路会显示「台账里无此上游」，不影响其成本数字。</div>}
            {I.restored.length > 0 && <div style={{ fontSize: 12, marginBottom: 6 }}>⇄ 被它替代而失效的旧版将<b>恢复为当前版</b>：{I.restored.map(x => x.cpCode).join('、')}</div>}
            {I.variants.length > 0 && <div style={{ fontSize: 12, marginBottom: 6 }}>⇉ 并行组里的其它版本保留：{I.variants.map(x => x.cpCode).join('、')}（组内只剩一条时自动解除并行标记）</div>}
          </div>
          <div style={{ marginTop: 10 }}>
            <b style={{ fontSize: 12 }}>删除理由（必填，进全局审计）</b>
            <textarea className="bom-ta" rows={2} value={reason} onChange={e => setReason(e.target.value)} placeholder="如：测试数据 / 重复立项 / 研发撤回该单" style={{ marginTop: 4 }} />
          </div>
          <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <b style={{ fontSize: 12, whiteSpace: 'nowrap' }}>删除密钥</b>
            <input className="bom-feeinp" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} placeholder="服务器 conf.ini 里配的密钥" style={{ width: 260 }} />
          </div>
        </>}
        <div className="bom-mfoot">
          <button className="btn-sec" onClick={onClose}>取消</button>
          <button className="btn-pri" disabled={busy || !I || imp?.keyConfigured === false} style={{ background: 'var(--red)', borderColor: 'var(--red)' }} onClick={go}>{busy ? '删除中…' : '永久删除'}</button>
        </div>
      </div>
    </div>
  )
}

function VoidModal({ target, mode, onClose, onDone, flash }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  const PRESET = ['研发重发了新版，本版作废', '这单报价取消', '重复提交的版本', '走了别的流程']
  const isReview = mode === 'review'
  const vr = target.voidReq || {}
  const submit = async (approve) => {
    if (!isReview && !reason.trim()) return flash('请写明作废理由')
    if (isReview && !approve && !reason.trim()) return flash('驳回请写明理由')
    setBusy(true)
    try {
      const payload = target.entryId ? { entryId: target.entryId } : { groupId: target.groupId, approvalNo: target.approvalNo }
      const r = isReview
        ? await bomVoidReview({ ...payload, approve, note: reason.trim() })
        : await bomVoidRequest({ ...payload, reason: reason.trim() })
      if (!r.ok) flash(r.msg || '操作失败'); else { flash(r.msg); onDone() }
    } catch (e) { flash('操作失败：' + e.message) } finally { setBusy(false) }
  }
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(560px,100%)' }}>
        <div className="bom-mhead"><b>{isReview ? '作废终审' : '申请作废'} · {target.label}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">作废<b>不是删除</b>——记录留着、留痕，只是退出工作区与标准成本库，随时可查。</div>
        {isReview
          ? <div className="bom-mstep"><span className="bom-mno">!</span><div style={{ flex: 1 }}>
            <b>成本会计的作废申请</b>
            <div className="bom-chkfail" style={{ marginTop: 6 }}>
              申请人 <b>{vr.by}</b>　{vr.at}<br />理由：<b>{vr.reason}</b></div>
            <div style={{ marginTop: 10 }}><b style={{ fontSize: 12 }}>终审意见（驳回必填）</b>
              <textarea className="bom-ta" rows={2} value={reason} onChange={e => setReason(e.target.value)}
                placeholder="如：这版还要用，别废" style={{ marginTop: 5 }} /></div>
          </div></div>
          : <div className="bom-mstep"><span className="bom-mno">1</span><div style={{ flex: 1 }}>
            <b>作废理由（必填）</b>
            <div className="muted" style={{ fontSize: 12, margin: '3px 0 7px' }}>提交后记录<b>照常有效</b>，要等财务BP终审批准才真作废。</div>
            <div className="bom-catpick" style={{ marginBottom: 7 }}>{PRESET.map(t => (
              <button key={t} className="sm" onClick={() => setReason(t)}>{t}</button>))}</div>
            <textarea className="bom-ta" rows={2} value={reason} onChange={e => setReason(e.target.value)}
              placeholder="写清为什么作废，终审人要据此判断" />
          </div></div>}
        <div className="bom-mfoot">
          <button className="btn-sec" onClick={onClose}>取消</button>
          {isReview
            ? <><button className="btn-sec" disabled={busy} onClick={() => submit(false)}>驳回</button>
              <button className="btn-pri" disabled={busy} style={{ background: 'var(--red)', borderColor: 'var(--red)' }}
                onClick={() => submit(true)}>{busy ? '处理中…' : '批准作废'}</button></>
            : <button className="btn-pri" disabled={busy} onClick={() => submit()}>{busy ? '提交中…' : '提交作废申请'}</button>}
        </div>
      </div>
    </div>
  )
}

// ============ 财务BP终审弹窗（第二个戳）============
// 全流程就两个戳：成本会计**初审** → 财务BP**终审（已审核）**。**只有终审通过的才对外开放**
// （BP 拿终审版去报价）；终审也能**退回**给成本会计重做（退回会撤下定稿指针）。
function FinalReviewModal({ row, onClose, onDone, flash }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  const go = async (approve) => {
    if (!approve && !note.trim()) return flash('退回请写明原因')
    setBusy(true)
    try {
      const r = await bomFinalReview(row.id, approve, note.trim())
      if (!r.ok) flash(r.msg || '操作失败'); else { flash(r.msg); onDone() }
    } catch (e) { flash('操作失败：' + e.message) } finally { setBusy(false) }
  }
  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(560px,100%)' }}>
        <div className="bom-mhead"><b>终审 · {row.productName}</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">编码 <b className="mono">{row.cpCode}</b>　·　全成本（含税）<b style={{ color: 'var(--teal)' }}>¥ {fmt(row.comp?.full)}</b>/kg
          　·　{row.matCategory || '（未定类别）'}{row.quotable === false ? ' · 不建议报价' : ''}</div>
        <div className="bom-mstep"><span className="bom-mno">1</span><div style={{ flex: 1 }}>
          <b>成本会计的初审</b>
          <div className="bom-chkfail" style={{ marginTop: 6, background: 'var(--bg-sub)', color: 'var(--ink-2)', borderColor: 'var(--line)' }}>
            初审人 <b>{row.finalizedBy}</b>　{row.finalizedAt}<br />
            物料类别 <b>{row.matCategory || '—'}</b>　·　对外报价 <b>{row.quotable === false ? '不建议：' + (row.quoteReason || '') : '建议'}</b>
          </div>
          {row.quotable === false && <div className="banner err" style={{ marginTop: 8, fontSize: 11.5 }}>
            ⚠ 成本会计标了<b>不建议对外报价</b>——理由：{row.quoteReason}。报价前请留意。</div>}
          {(row.replaces || []).length > 0 && <div className="banner" style={{ display: 'block', marginTop: 8, fontSize: 11.5, background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }}>
            ⇄ <b>本版通过后将替代 {row.replaces.length} 个旧版</b>：{row.replaces.map(c => `${c.cpCode}（${c.why || ''} · 审核 ${c.auditAt || '—'} · 全成本 ¥${fmt(c.fullIncl)}/kg）`).join('；')}
            ——旧版退出对外台账，<b>引用旧版的 BP 定价方案会收到「成本已更新」提示</b>，需要重新确认。</div>}
        </div></div>
        <div className="bom-mstep"><span className="bom-mno">2</span><div style={{ flex: 1 }}>
          <b>终审意见（退回必填）</b>
          <textarea className="bom-ta" rows={2} value={note} onChange={e => setNote(e.target.value)}
            placeholder="通过=对外开放给BP报价；退回请写明要成本会计改什么" style={{ marginTop: 5 }} />
        </div></div>
        <div className="bom-mfoot">
          <button className="btn-sec" onClick={onClose}>取消</button>
          <button className="btn-sec" disabled={busy} onClick={() => go(false)}>退回成本会计</button>
          <button className="btn-pri" disabled={busy} style={{ background: 'var(--green)', borderColor: 'var(--green)' }}
            onClick={() => go(true)}>{busy ? '处理中…' : '✓ 终审通过（盖已审核戳·对外开放）'}</button>
        </div>
      </div>
    </div>
  )
}

// ============ 立项弹窗（业务方定 2026-09-04）============
// 这一步**只负责把单立起来**：录钉钉单号 → 抓附件 → 能入的入账、不能入的记「待修」→ 生成待办。
// 哪些能入账、哪里不对、怎么修，统统到「处理页」去看去办——不在这个录入框里判。
function IntakeModal({ cfg, onClose, onDone, flash, init }) {
  const [appno, setAppno] = useState(init?.approvalNo || '')
  const [hist, setHist] = useState(!!init?.historical)     // 历史补录（V2.472 口径）：照常复核+初审，初审通过即盖「补录」戳定稿，不经BP终审；init 可预填（V2.514 导入行「补明细」）
  const [err, setErr] = useState(null)     // 立项没成的原因，留在输入框下方（V2.872：以前只闪一下提示条，长一点的原因来不及看）
  const [busy, setBusy] = useState('')
  const [res, setRes] = useState(null)
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const doIntake = async () => {
    if (!appno.trim()) return flash('请填钉钉审批编号')
    setBusy('dt'); setErr(null)
    try {
      const r = await bomIntake(appno.trim(), hist)
      if (!r.ok) { setErr({ msg: r.msg || '立项失败', tooOld: !!r.tooOld }); flash(r.tooOld ? '这张单超过一年，按编号取不到——原因和办法见输入框下方' : '立项没成，原因见输入框下方'); setRes(r.commentPending ? r : null) }
      else setRes(r)
    } catch (e) { setErr({ msg: '立项失败：' + e.message }); flash('立项失败：' + e.message) } finally { setBusy('') }
  }
  const doUpload = async (files) => {
    if (!files || !files.length) return
    if (/https?:|=/.test(appno)) return flash('上传立项时，上面的框请填审批编号（数字），不要留着链接')   // V2.872：框里可能贴了审批单链接，别把链接当单号存
    setBusy('up')
    try {
      const up = await bomUpload([...files], appno.trim())
      if (!up.ok) return flash(up.msg || '上传失败')
      const bk = await bomBook(up.stagingId, up.records.map(x => x.idx), hist)   // 全量交给后端判，不在这勾选
      setRes({ ok: true, approvalNo: appno.trim(), booked: bk.booked, rejected: bk.rejected,
               skipped: bk.skipped, warnings: up.warnings || [] })
    } catch (e) { flash('上传失败：' + e.message) } finally { setBusy('') }
  }

  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(620px,100%)' }}>
        <div className="bom-mhead"><b>立项（生成待办）</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">录入钉钉审批编号即可立项。系统会抓附件、解析、<b>能入账的自动入账，不能入的记为「待修」</b>——
          具体哪些能入、哪里不对、怎么修，都在<b>处理页</b>里看。</div>
        {/* 历史补录（业务方定 2026-09-06「历史补录也是要审核的，只是这时候盖补录戳」）：照常复核+初审，初审通过即定稿，不经BP终审 */}
        <label className="banner" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer', margin: '8px 0 4px',
          background: hist ? 'var(--amber-bg)' : 'var(--bg-sub)', color: hist ? 'var(--amber)' : 'var(--ink-2)', border: '1px solid ' + (hist ? 'var(--amber-line)' : 'var(--line)') }}>
          <input type="checkbox" checked={hist} onChange={e => setHist(e.target.checked)} style={{ marginTop: 3 }} />
          <span><b>历史补录</b>——照常进待办：复核四步、成本会计审核定性都要做；<b>初审通过那一刻直接定稿·对外</b>，终审戳盖「历史补录」，
            不再进财务BP终审。台账上先标「补录·待初审」、定稿后标「补录·无二审」。勾稽不平的照旧拦下记待修。
            <span className="muted">需要财务BP把关的单不要勾，走正常两道审核。</span></span>
        </label>

        <div className="bom-mstep"><span className="bom-mno">1</span><div style={{ flex: 1 }}>
          <b>钉钉审批编号</b>
          <div className="muted" style={{ fontSize: 12, margin: '3px 0 7px' }}>表单附件 + 评论区补传的附件都会取（评论区后传的更新版采购核算表也会入账）。<b>一年以前发起的老单</b>钉钉不让按编号查，请贴这张审批单的链接。</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="bom-search" style={{ flex: 1 }} placeholder="如 202609011316000251965；一年以前的老单贴审批单链接"
              value={appno} onChange={e => { setAppno(e.target.value); setErr(null) }}
              onKeyDown={e => { if (e.key === 'Enter') doIntake() }} />
            <button className="btn-pri" disabled={!!busy || !cfg?.dingtalkConfigured} onClick={doIntake}
              title={cfg?.dingtalkConfigured ? '' : '本机未配置钉钉，用下方上传'}>
              {busy === 'dt' ? '立项中…' : '立项'}</button>
          </div>
          {!cfg?.dingtalkConfigured && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>本机未配置钉钉应用——请用下方上传。</div>}
          {err && <div className="banner" style={{ display: 'block', marginTop: 8, fontSize: 12, lineHeight: 1.7, background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }}><b>{err.tooOld ? '这张单超过一年，按编号取不到' : '立项没成'}</b>　{err.msg}</div>}
        </div></div>

        <div className="bom-mor">钉钉扫不到 / 无审批单据时</div>
        <div className="bom-mstep"><span className="bom-mno">2</span><div style={{ flex: 1 }}>
          <b>上传采购核算表</b>
          <div className="muted" style={{ fontSize: 12, margin: '3px 0 7px' }}>走同一套解析与勾稽/上游校验；单号填在上面可溯源。</div>
          <label className="bom-drop">{busy === 'up' ? '解析中…' : '点击或拖拽上传　·　支持 .xlsx'}
            <input type="file" accept=".xlsx,.xls" multiple style={{ display: 'none' }} onChange={e => doUpload(e.target.files)} /></label>
        </div></div>

        {res && <div className="bom-mstep"><span className="bom-mno">✓</span><div style={{ flex: 1 }}>
          <b>立项结果</b>
          <div style={{ margin: '6px 0' }}>
            <span className="tag ok">已入账 {(res.booked || []).length}</span>{' '}
            {(res.rejected || []).length > 0 && <span className="tag leak">待修 {res.rejected.length}</span>}{' '}
            {(res.skipped || []).length > 0 && <span className="tag unmap">跳过 {res.skipped.length}</span>}
            {res.historical && <span className="tag ok" style={{ marginLeft: 4 }}>历史补录 · 已进待办，初审通过即定稿（不经BP终审）</span>}
          </div>
          {(res.rejected || []).length > 0 && <div className="bom-chkfail">
            {res.rejected.map((r, i) => <div key={i}>· <b>{r.productName}</b>：{r.reason}</div>)}
            <div style={{ marginTop: 4 }}>→ 这些<b>不进台账</b>（红线），但已记为「待修」留在待办里；进处理页可看逐料差异、替换修好的采购核算表。</div>
          </div>}
          {(res.commentPending || []).length > 0 && (() => {
            // 评论区附件现在能取到；这里只列没取到的少数，并说明是不是重传前的旧件（2026-10-06）
            const need = res.commentPending.filter(c => !c.replacedBy)
            const sty = need.length ? { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }
              : { background: 'var(--bg-sub)', color: 'var(--ink-2)', border: '1px solid var(--line)' }
            return <div className="banner" style={{ display: 'block', ...sty, fontSize: 11.5, marginTop: 6, lineHeight: 1.6 }}>
              {need.length ? '⚠ ' : 'ⓘ '}评论区有 {res.commentPending.length} 个附件没取到（其余评论区附件已取到并参与立项）：
              {res.commentPending.map((c, i) => <div key={i}>· {c.fileName}{c.at ? `（${c.at}）` : ''}——{c.replacedBy
                ? <>重传前的旧件，随后重传的「{c.replacedBy}」已取到，<b>可忽略</b></>
                : <>钉钉回「无访问权限」（多见于评论人删除/撤回了文件）；需要的话请从钉钉下载后用上方上传补入</>}</div>)}
            </div>
          })()}
          {(res.warnings || []).map((w, i) => <div key={i} className="muted" style={{ fontSize: 11, marginTop: 4 }}>{w}</div>)}
        </div></div>}

        <div className="bom-mfoot">
          <button className="btn-sec" onClick={onClose}>关闭</button>
          {res && res.approvalNo && <button className="btn-pri" onClick={() => onDone(res.approvalNo)}>进入处理页 ›</button>}
        </div>
      </div>
    </div>
  )
}
