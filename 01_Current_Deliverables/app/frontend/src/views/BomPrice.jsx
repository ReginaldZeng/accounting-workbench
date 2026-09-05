// [Change Log] Date:2026-09-02 Author:Claude/c Version:V-draft(BOM报价审核)
// 【BOM报价审核】前端：钉钉「BOM表报价」审批附件→解析→复核→定稿→BP消费。
// 三视图（台账列表 / 核算表详情 / 版本对比）+ 手工入账弹窗。样机布局与交互照搬，皮肤换本项目令牌。
// 含税五分项口径（元/kg），涨跌红▲绿▼（中国财务惯例），编辑态改费用参数保存留痕。
import React, { useState, useEffect, useMemo, useCallback } from 'react'
import {
  getBomConfig, getBomLedger, getBomEntry, bomFetchApproval, bomUpload, bomBook,
  bomReview, bomFinalize, bomUnfinalize, bomExportPrettyUrl, bomExportOriginalUrl, bomAttachBomList,
  getBomKdPurchase, getBomMaterialUsage, bomConfirmStep, bomApplyGoods, getBomSettings, setBomSettings,
  getBomApproval, bomReplaceSheet, bomRefetchReplace, bomClassify, getBomPending,
  bomIntake, bomFinalReview, bomVoidRequest, bomVoidReview, bomSetMatType, bomSetErpCode, getBomUsageSpreads, getBomErpLookup, bomLinkParallel,
  getBomInvoiceRules, setBomInvoiceRules,
} from '../api.js'

const GROSS = 1.13
// 发票类型 → 单位成本不含税（镜像后端 kernel，对应核算表 N 列公式）。price=含税价 tax=税率 rate=扣除率
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
// 原料/包材两表列宽统一（table-layout:fixed + 同一 colgroup）→ 两个框列对齐。11 列，和 ≈100%。
// 类型/编码/物料名/型号/单位/添加量/含税采购价/税率/发票类型/成本不含税/成本含税/占比/说明/核价（业务方 2026-09-04 定列）
const MAT_COLS = ['7%', '9%', '15%', '8%', '4%', '7%', '7%', '4%', '5%', '7%', '7%', '4%', '9%', '4%']
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
// 类别展示：已定性→显示类别（委外标灰底）；未定性→显示「建议·X」+待定性；名字与编码打架→⚠
function CatCell({ p }) {
  const doubt = p.kindDoubt ? <span className="bom-kdoubt"
    title={`疑似分类不符：按编码判「${p.kindAuto}」，但产品名像「${p.productName && p.productName.includes('半成品') ? '半成品' : '复配料'}」——请在审核弹窗定性`}>⚠</span> : null
  if (!p.matCategory) return <><span className="bom-catsug" title={'编码建议值，未定性。定稿前须在「审核」弹窗指定'}>建议·{p.kindAuto}</span>{doubt}</>
  return <><span className={'bom-cat' + (p.outsourced ? ' out' : '')} title={p.outsourced ? '委外（代工厂生产）' : '自产'}>{p.matCategory}</span>
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
          <th className="th">核算表「小计」算它了吗</th>
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
          {cut >= 0 && cut < rows.length - 1 && <tr className="bom-subrow"><td colSpan={6}>核算表的「小计」公式实际只加到第 {cut + 1} 味为止 → 后面 {rows.length - cut - 1} 味白填了</td>
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
          {/* 讲白：不是「研发有、核算表没有」（那是③用量自洽报的），是核算表**自己**的小计公式漏加了自己的料 */}
          <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 8, fontSize: 12 }}>
            ⓘ <b>这些料在核算表里是有的</b>（下表能看到它们的添加量和价格），<b>只是核算表自己的「小计」公式没把它们加进去</b>——
            就像 Excel 里 <span className="mono">=SUM(H7:H16)</span> 之后又往下加了几行料，公式没跟着往下拉。
            <br />⚠ 这个偏低的小计会<b>一路往下传</b>（变动成本→成本合计→含税→全成本），所以<b>该产品全成本是少算的</b>。
            <span className="muted">（若是「研发BOM有、核算表没有」那种缺料，会在③用量自洽里报「核算表缺料」，不是这里。）</span>
          </div>
          {(p.failedChecks || []).map((c, i) => (
            <div key={i} className="bom-chkfail" style={{ marginBottom: 8 }}>
              <b>✗ {c.check}</b>：核算表写的小计 <b>{fmt(c.a, 4)}</b> ≠ 逐料相加 <b>{fmt(c.b, 4)}</b>（少算 {fmt(Math.abs(c.diff || 0), 4)} 元/kg 不含税，折含税约 {fmt(Math.abs((c.diff || 0) * GROSS), 4)}）
              {(c.missing || []).length > 0 && <span>　— <b>没被加进小计的是：{c.missing.map(m => m.matName).join('、')}</b></span>}
            </div>))}
          <div style={{ maxHeight: '52vh', overflowY: 'auto', marginTop: 6 }}>
            {block('原料', p.matSubtotal)}
            {block('包材', p.packSubtotal)}
          </div>
          <div className="bom-chkfail" style={{ background: 'var(--bg-sub)', color: 'var(--ink-2)', borderColor: 'var(--line)' }}>
            <b>怎么改</b>：把上面标红「未计入」的料并进源表「小计」公式的求和范围（多半是底部新增料时没往下拉），
            让研发/工厂改好后重传 → 回处理页用「⟳重连钉钉替换 / ⬆上传替换核算表」补入本组。
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
  const PRESET = ['包材不全', '物料暂定价', '配方未定版', '缺半成品核算表', '勾稽存疑']
  const missing = CONFIRM_STEPS.filter(([k]) => !entry.steps?.[k]).map(([, l]) => l)
  // 换码承接（业务方定 2026-09-05）：同CP再核算 / 不同CP同物料编码 → 定稿前必须答「原来那个是否失效」
  const cands = entry.obsoleteCandidates || []
  // 两个答案（业务方定 2026-09-05）：'replace'=A 原版失效（本版替代）/ 'parallel'=B 并行但关联（都对外，同一产品不同版本/包装）。null 未答→只存定性不定稿
  const [obs, setObs] = useState(null)
  const willFinalize = missing.length === 0
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
    if (willFinalize && cands.length > 0 && obs === null) return flash('请先回答：原来的版本是失效，还是并行但关联？')
    setBusy(true)
    try {
      const r = await bomClassify(entry.id, cat, q, reason.trim(), obs === 'replace', obs === 'parallel')
      if (!r.ok) flash(r.msg || '保存失败')
      else {
        flash(r.finalized
          ? `已定稿：${cat} · ${q ? '建议报价' : '不建议报价'}${(r.obsoleted || []).length ? `　· 原版 ${r.obsoleted.map(c => c.cpCode).join('、')} 已失效` : ''}${(r.linked || []).length ? `　· 与 ${r.linked.map(c => c.cpCode).join('、')} 并行关联，都对外` : ''}　${r.affectedPricing?.note || ''}`
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
        <div className="bom-mhead"><b>审核定稿 · {entry.productName}</b><span className="bom-x" onClick={onClose}>✕</span></div>
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
          <div><b>⚠ 台账里已有 {cands.length} 个同CP / 同物料编码的审核版本</b>。请判断它和本版的关系：<b>A 新旧版</b>——原版失效、退出对外，引用它的 BP 定价收到「成本已更新」（终审通过那一刻切换）；<b>B 并行版本</b>——同一产品的不同版本/包装（如火腿片各版、印刷袋 vs 空白袋），都对外、互不替代，串成一组。</div>
          <div style={{ margin: '8px 0', padding: '6px 10px', background: 'rgba(255,255,255,.55)', borderRadius: 8 }}>{cands.map(c => (
            <div key={c.entryId} style={{ fontSize: 12, display: 'flex', flexWrap: 'wrap', gap: '2px 12px', alignItems: 'baseline', color: 'var(--ink)' }}>
              <b className="mono">{c.cpCode}</b><span>{c.productName}</span>
              {c.erpCode && <span className="mono muted">物料编码 {c.erpCode}</span>}
              <span className="muted">{c.why}</span>
              <span className="muted">{c.status} {c.auditAt || ''}</span>
              <span>全成本 <b>¥{fmt(c.fullIncl)}</b>/kg</span>
              <a className="lk" onClick={() => setCmpFirst(c.entryId)} title="两张核算表逐料对比用量与价格，再决定是新旧版还是两个产品">对比 ›</a>
            </div>))}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>原来的版本是否失效？</b>
            <div className="bom-catpick">
              <button className={obs === 'replace' ? 'on no' : ''} onClick={() => setObs('replace')} title="本版替代原版：原版退出对外台账，BP 收到成本更新提示">A 是，原版失效</button>
              <button className={obs === 'parallel' ? 'on ok' : ''} onClick={() => setObs('parallel')} title="两条是同一产品的并行版本（不同 CP / 不同包装），都对外、互不替代；台账标「并行」并串成一组">B 否，并行但关联</button>
            </div>
            <span className="muted" style={{ fontSize: 11 }}>拿不准先点「对比 ›」看两张核算表差在哪；不答则只存定性、不定稿。</span>
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
            {busy ? '保存中…' : ((willFinalize && (cands.length === 0 || obs)) ? '✓ 保存定性并定稿' : '仅保存定性')}</button>
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
  const [curAppr, setCurAppr] = useState('')    // 当前处理的钉钉单号
  const [finalRow, setFinalRow] = useState(null)   // 财务BP终审弹窗目标

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
  const openCompare = () => { setView('compare'); window.scrollTo(0, 0) }
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
      {view === 'list' && <Ledger data={data} cfg={cfg} mode={mode} onOpen={openDetail} onManual={() => setManual(true)}
        onApproval={openApproval} onRefresh={load} flash={flash}
        onFinalReview={data?.canFinalReview ? setFinalRow : null} />}
      {view === 'approval' && <ApprovalView no={curAppr} cfg={cfg} onBack={() => { setCurAppr(''); backToList() }}
        onOpen={openDetail} flash={flash} />}
      {view === 'detail' && entry && <Detail entry={entry} all={data.all} cfg={cfg} mode={mode} onBack={backFromDetail}
        onOpen={openDetail} onCompare={openCompare} onChanged={async () => { const r = await getBomEntry(curId); setEntry(r.entry); load() }}
        flash={flash} />}
      {view === 'compare' && entry && <Compare entry={entry} all={data.all} onBack={() => setView('detail')} flash={flash} />}
      {manual && <IntakeModal cfg={cfg} onClose={() => setManual(false)} flash={flash}
        onDone={(no) => { setManual(false); load(); openApproval(no) }} />}
      {finalRow && <FinalReviewModal row={finalRow} onClose={() => setFinalRow(null)}
        onDone={() => { setFinalRow(null); load() }} flash={flash} />}
      {toast && <div className="bom-toast">{toast}</div>}
    </div>
  )
}

// ============ 台账列表 ============
function Ledger({ data, cfg, mode, onOpen, onManual, onApproval, onFinalReview, onRefresh, flash }) {
  const isStd = mode === 'std'
  const [ftype, setFtype] = useState('all')     // all | fin | semi
  const [fch, setFch] = useState('all')         // all | ecom | common | tob | toc
  const [q, setQ] = useState('')
  const [showObs, setShowObs] = useState(false) // 换码承接：已失效（被已终审新版替代）的旧版默认收起
  const rows = data.rows || []
  const versionsOf = (pk) => (data.all || []).filter(x => x.productKey === pk)
  const isDead = (r) => !!(r.obsoleteBy && r.obsoleteBy.live)
  const deadCount = rows.filter(isDead).length

  const shown = rows.filter(r => {
    if (isStd && !showObs && isDead(r)) return false
    if (ftype !== 'all' && (r.kind || '成品') !== ftype) return false
    if (fch !== 'all' && r.channel !== fch) return false
    if (q.trim()) { const s = (r.cpCode + r.productName + r.customer).toLowerCase(); if (!s.includes(q.trim().toLowerCase())) return false }
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
      <tr key={r.id} className="row" onClick={() => onOpen(r.id)} title="查看成本核算表" style={dead ? { opacity: 0.55 } : undefined}>
        <td className="mono sub">{r.erpCode || <span className="muted">—</span>}</td>
        <td className="mono" style={{ fontWeight: 600 }}>{r.cpCode}</td>
        <td style={{ fontWeight: 600 }}>{r.productName}
          {r.quotable === false && <span className="bom-noquote" title={'不建议对外报价：' + r.quoteReason}>禁报价</span>}
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
        <td style={{ whiteSpace: 'nowrap' }} onClick={e => e.stopPropagation()}>
          <a className="lk" style={{ marginRight: 10 }} onClick={() => onOpen(r.id)}>核算表 ›</a>
          <a className="lk" style={{ marginRight: 10 }} title="补/改本产品的 ERP 物料编码" onClick={() => fillErp(r)}>补物料编码</a>
          {r.needFinalReview && onFinalReview &&
            <a className="lk" style={{ fontWeight: 700, color: 'var(--green)' }} onClick={() => onFinalReview(r)}>⚑ 终审 ›</a>}
        </td>
      </tr>
    )
  }

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">{isStd ? '标准成本台账' : '待办与复核'}　{isStd
            ? <span className="tag ok">已审核 · 公开</span> : <span className="tag werr">未审核工作台</span>}</div>
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
          </>}
        </div>
      </div>
      <div className="body">
        <div className="card bom-stats">
          <Stat lab="台账产品" v={stats.total} suf={`个 · 含半成品 ${stats.semi}`} />
          <Stat lab="最近核算" v={stats.last} small suf="" />
          {isStd
            ? <><Stat lab="来源审批" v={stats.approvals} suf="单" />
              <Stat lab="勾稽校验" v="全平" green suf={`${stats.versions} 版 × 6 项`} />
              <Stat lab={data.canFinalReview ? '待我终审' : '待终审'} v={data.needAck || 0}
                suf={`/ ${stats.total} 产品`} /></>
            : <><Stat lab="待办单号" v={openAppr.length} suf={doneCount ? `单 · 另 ${doneCount} 单已完成初审` : '单'} />
              <Stat lab="组" v={openAppr.reduce((s, a) => s + a.groupCount, 0)}
                suf="一个核算表文件=一组" />
              <Stat lab="待复核" v={openAppr.reduce((s, a) => s + a.pending, 0)}
                suf={`/ ${stats.total} 产品`} /></>}
        </div>

        <div className="card bom-filterbar">
          <input className="bom-search" placeholder={isStd ? '搜索 CP码 / 产品名称 / 客户' : '搜索钉钉单号 / 产品名称 / CP码'}
            value={q} onChange={e => setQ(e.target.value)} />
          {isStd && <>
            <Seg value={ftype} onChange={setFtype} opts={[['all', '全部'], ['成品', '成品'], ['半成品', '半成品'], ['复配料', '复配料']]} />
            <Seg value={fch} onChange={setFch} opts={[['all', '全部渠道'], ['ecom', '只看电商'], ['common', '只看通品'], ['tob', 'TOB'], ['toc', 'TOC']]} />
            {deadCount > 0 && <button className={'btn-sec' + (showObs ? ' on' : '')} style={{ fontSize: 11.5 }} onClick={() => setShowObs(v => !v)}
              title="被已终审新版替代（同CP重核 / 不同CP同物料编码）的旧版：记录与历史都在，只是不再对外">
              {showObs ? '隐藏' : '显示'}已失效 {deadCount}</button>}
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
                      {a.blocked > 0 && a.pending > 0 && <span className="tag late" style={{ marginLeft: 4 }}>待修 {a.blocked}</span>}</td>
                    <td><a className="lk" onClick={e => { e.stopPropagation(); onApproval(a.approvalNo) }}>进入处理 ›</a></td>
                  </tr>))}
              </tbody>
            </table>
          </div>
          : <div className="tbl-wrap">
            <table className="bom-ledger">
              <thead><tr>
                <th className="th">物料编码</th><th className="th">CP码</th><th className="th">产品名称</th>
                <th className="th">规格</th>
                <th className="th" style={{ textAlign: 'right' }}>原料</th><th className="th" style={{ textAlign: 'right' }}>包材</th>
                <th className="th" style={{ textAlign: 'right' }}>加工费</th><th className="th" style={{ textAlign: 'right' }}>装卸费</th>
                <th className="th" style={{ textAlign: 'right' }}>管理费</th><th className="th" style={{ textAlign: 'right' }}>全成本（含税）</th>
                <th className="th">审核日期</th><th className="th">钉钉单号</th><th className="th">客户</th>
                <th className="th">当前状态</th><th className="th">操作</th>
              </tr></thead>
              <tbody>
                {shown.length === 0 && <tr><td colSpan={15} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 30 }}>
                  暂无匹配的台账产品{fch !== 'all' ? `（渠道：${CH[fch]}）` : ''}</td></tr>}
                {shown.map(renderRow)}
              </tbody>
            </table>
          </div>}
        <div className="foot">{isStd
          ? '原料/包材来自核算表逐料解析；加工费/装卸费/管理费为台账费用参数。TOB/TOC 定价默认直连台账定稿版，电商/通品需显式引用。'
          : '待办按钉钉单号立项。一个单号里可有若干「组」——一个成本核算表文件（含成品+半成品+复配料）配上它的 BOM 清单＝一组。点进单号在处理页按组复核、可替换组内文件（重连钉钉/手动上传），被替换的旧版留痕不进标准库。'}</div>
      </div>
    </>
  )
}
function Stat({ lab, v, suf, small, green }) {
  return <div className="bom-stat"><div className="bom-stat-l">{lab}</div>
    <div className="bom-stat-v" style={{ fontSize: small ? 16 : undefined, color: green ? 'var(--green)' : undefined }}>{v}</div>
    {suf ? <small>{suf}</small> : null}</div>
}
function Seg({ value, onChange, opts }) {
  return <div className="bom-seg">{opts.map(([k, l]) => (
    <button key={k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>{l}</button>))}</div>
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

// ============ 处理页：一个钉钉单号 → 若干「组」（一个核算表文件=一组）============
// 组内：当前版产品（成品/半成品/复配料）+ 各自 BOM 校验 + 可替换组内文件（重连钉钉/手动上传）+ 被替换旧版留痕。
function ApprovalView({ no, cfg, onBack, onOpen, flash }) {
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [rep, setRep] = useState(null)      // 最近一次替换结果（含仍不平清单）
  const [auditP, setAuditP] = useState(null)   // 审核定性弹窗的目标产品
  const [pendP, setPendP] = useState(null)     // 不平下钻明细：{groupId, product}
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
    flash(`${how}：替换 ${r.replaced.length}、新增 ${r.added.length}` + (r.stillBad.length ? `，仍不平 ${r.stillBad.length}` : '') + (st ? `，下游 ${st} 个受影响已打回未复核` : ''))
    await load()
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
          <div className="h-sub">一个成本核算表文件（含成品+半成品+复配料）＋ 它的 BOM 清单 ＝ <b>一组</b>；本单共 {liveGroups.length} 组 · {prodCount} 个产品{histCount ? ` · ${histCount} 条替换留痕` : ''}</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-sec" onClick={onBack}>返回待办</button>
          <button className="btn-sec" onClick={load}>⟳ 刷新</button>
        </div>
      </div>
      <div className="body">
        <div className="bom-crumbs"><a className="lk" onClick={onBack}>待办与复核</a> / 单号 {no}</div>

        {rep && (rep.stillBad || []).length > 0 && <div className="card bom-sect" style={{ borderLeft: '3px solid var(--amber)' }}>
          <div className="bom-secthead"><span className="bom-no" style={{ background: 'var(--amber-bg)', color: 'var(--amber)' }}>!</span>
            <b>{rep.how}后仍有 {rep.stillBad.length} 个产品勾稽不平，未入账</b>
            <span style={{ flex: 1 }} /><a className="lk" onClick={() => setRep(null)}>关闭</a></div>
          <div style={{ padding: '10px 14px' }}>
            {rep.stillBad.map((b, i) => <div key={i} className="bom-chkfail">
              <b>{b.productName}（{b.cpCode || '无编码'}）</b>
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
              {g.pendingCount > 0 && <span className="tag leak" title="同一核算表里勾稽不平、未入账的产品，需退回研发/工厂修源表">{g.pendingCount} 个待修未入账</span>}
              {g.allOk ? <span className="tag ok">四步已确认</span> : <span className="tag werr">待复核</span>}
              <span className="muted" style={{ fontSize: 11 }}>{g.bookedCount}/{g.products.length} 已入账</span>
              <span style={{ flex: 1 }} />
              <span className="muted" style={{ fontSize: 11 }} title={g.coreFile}>核算表：{(g.coreFile || '—').slice(0, 34)}{(g.coreFile || '').length > 34 ? '…' : ''}</span>
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
                        <td style={{ fontWeight: 600 }}><Lv d={p.depth} />{p.productName}<span className="tag leak" style={{ marginLeft: 4 }}>未入账</span></td>
                        <td className="num muted">{fmt(p.comp?.full)}</td>
                        <td>{p.checksOk ? <span className="tag ok">全平</span> : <span className="tag leak">不平</span>}</td>
                        <td colSpan={4} className="muted" style={{ fontSize: 11 }}>
                          {(p.blockedBy || []).length > 0 && p.checksOk
                            ? <><b style={{ color: 'var(--red)' }}>自身全平，但上游「{p.blockedBy.join('、')}」不平 → 连带拦下</b>：本品用的是它的价，成本建在错数上</>
                            : <>勾稽不平 → 不予入账（红线）{(p.blockedBy || []).length > 0 ? `；且上游「${p.blockedBy.join('、')}」也不平` : ''}；修好源表后用下方「替换核算表」补入</>}</td>
                        <td className="muted" style={{ fontSize: 11 }}>{p.matCount} 味料</td>
                        <td><a className="lk" onClick={e => { e.stopPropagation(); setPendP({ groupId: g.groupId, product: p }) }}>查明细 ›</a></td>
                      </tr>
                      <tr><td /><td colSpan={9} style={{ paddingTop: 0 }}>
                        <div className="bom-chkfail">
                          {(p.failedChecks || []).map((c, j) => <div key={j}>
                            <b>✗ {c.check}</b>：核算表写的小计 <b>{fmt(c.a, 4)}</b> ≠ 逐料相加 <b>{fmt(c.b, 4)}</b>（少算 {fmt(Math.abs(c.diff || 0), 4)} 元/kg 不含税）
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
                    <td style={{ fontWeight: 600 }}><Lv d={p.depth} /><a className="lk" onClick={() => onOpen(p.id)}>{p.productName}</a></td>
                    <td className="num" style={{ fontWeight: 700, color: 'var(--teal)' }}>{fmt(p.comp.full)}</td>
                    <td>{ck ? <span className="tag ok">全平</span> : <span className="tag leak">不平</span>}</td>
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
                      {p.staleNote && <span className="tag late" style={{ marginLeft: 4 }} title="上游核算表被替换、成本可能变了——本品已打回未复核，请重新复核">⚠ {p.staleNote}</span>}</td>
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
              <span className="muted" style={{ fontSize: 11.5 }}>组内核算表有错（如小计漏加料）→ 让研发/工厂改好后在此替换；<b>旧版留痕、不进标准成本库</b></span>
              <span style={{ flex: 1 }} />
              <button className="btn-sec" disabled={!!busy || !cfg?.dingtalkConfigured} onClick={() => doRefetch(g.groupId)}
                title={cfg?.dingtalkConfigured ? '重连钉钉重拉商务版核算表替换' : '本机未配置钉钉，请用上传替换'}>
                {busy === g.groupId + ':dt' ? '重拉中…' : '⟳ 重连钉钉替换'}</button>
              <label className="bom-minifile pri">{busy === g.groupId + ':up' ? '上传中…' : '⬆ 上传替换核算表'}
                <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => doUpload(g.groupId, e.target.files)} /></label>
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

        <div className="foot">组＝一个成本核算表文件（成品+半成品+复配料）＋ 匹配的 BOM 清单。替换核算表时：新文件里勾稽平的产品顶替同组同产品旧版（旧版标「已被替换」留痕、退出标准成本库与定稿指针）；仍不平的不入账并回报原因；原先因不平未入的产品（如半成品）修好后会作「组内新增」补入。<b>定性</b>＝物料类别（复配料/自产·委外 半成品·成品）+ 是否建议对外报价（不建议须写原因），定稿前必须完成。</div>
      </div>
      {auditP && <AuditModal entry={auditP} onClose={() => setAuditP(null)}
        onDone={async () => { setAuditP(null); await load() }} flash={flash} />}
      {pendP && <PendingDetailModal groupId={pendP.groupId} product={pendP.product}
        onClose={() => setPendP(null)} flash={flash} />}
    </>
  )
}

// ============ 核算表详情 ============
function Detail({ entry, all, cfg, mode, onBack, onOpen, onCompare, onChanged, flash }) {
  const isStd = mode === 'std'
  const [edit, setEdit] = useState(false)
  const [fee, setFee] = useState(entry.fee)
  const [saving, setSaving] = useState(false)
  const [expMenu, setExpMenu] = useState(false)
  const [auditM, setAuditM] = useState(false)      // 审核定性弹窗（物料类别 + 是否允许报价）
  const [voidM, setVoidM] = useState('')           // 作废弹窗：'' | 'request'(成本会计申请) | 'review'(财务BP终审)
  const [priceMat, setPriceMat] = useState(null)   // 价格校验弹窗（内联在明细行触发）
  const [matDraft, setMatDraft] = useState(null)   // 编辑态可改税率的物料副本
  const [step, setStep] = useState('price')        // 四步页签：bom/craft/qty/price（默认落在④报价核算）
  useEffect(() => { setFee(entry.fee); setEdit(false); setMatDraft(null) }, [entry.id])
  const startEdit = () => { setStep('price'); setMatDraft((entry.materials || []).map(m => ({ ...m }))); setEdit(true) }
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
  const unfinalize = async () => { try { await bomUnfinalize(entry.id); flash('已撤销定稿'); await onChanged() } catch (e) { flash(e.message) } }
  const confirmStep = async (s, on) => {
    try { await bomConfirmStep(entry.id, s, on); flash(on ? '已确认' : '已撤销确认'); await onChanged() }
    catch (e) { flash('操作失败：' + e.message) }
  }
  const applyGoods = async () => {
    try { const r = await bomApplyGoods(entry.id); if (!r.ok) return flash(r.msg || '采纳失败')
      flash(`已采纳商品版 ${r.changed} 项价/税调整并留痕`); await onChanged() }
    catch (e) { flash('采纳失败：' + e.message) }
  }

  const mats = curMats.filter(m => m.seg === '原料')
  const packs = curMats.filter(m => m.seg === '包材')
  // 上一版标识：识别到上一版就把它的 CP码 + 钉钉单号 + 核算日期一并显示，供追溯
  const prevTag = prev
    ? `上一版 ${prev.cpCode}${prev.approval ? ' · 钉钉' + prev.approval : ''} · ${prev.calcDate}`
    : ''
  const segDelta = (list) => {
    if (!prev) return null
    let up = 0, dn = 0, add = 0
    list.forEach(m => { const p = prevMat(m.matName); if (!p) { add++; return }
      const d = ((m.costExcl || 0) - (p.costExcl || 0)) * GROSS; if (d > EPS) up++; else if (d < -EPS) dn++ })
    if (!up && !dn && !add) return <span className="muted" title={prevTag}>　·　较{prevTag}无变化</span>
    return <span className="muted" title={prevTag}>　·　较{prevTag} {up > 0 && <b className="up">▲{up}</b>} {dn > 0 && <b className="down">▼{dn}</b>} {add > 0 && <b style={{ color: 'var(--teal)' }}>新增{add}</b>}</span>
  }

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">成本核算表 · {entry.productName}
            <Kind k={entry.kind} />{entry.kind !== '成品' && <span className="muted" style={{ fontSize: 11 }}> 作原料进入上层</span>}
            {edit ? <span className="tag werr">编辑中</span> : <span className="tag unmap">只读</span>}</div>
          <div className="h-sub">来源：钉钉审批 {entry.approval || '—'} · {entry.srcFile} [{entry.sheet}] · 程序解析
            {versions.length > 1 ? `　·　共 ${versions.length} 个版本` : ''}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', position: 'relative' }}>
          <button className="btn-sec" onClick={onBack}>返回台账</button>
          {versions.length > 1 && <button className="btn-sec" onClick={onCompare}>⇄ 版本对比</button>}
          {cfg?.canExport && <><button className="btn-sec" onClick={() => setExpMenu(m => !m)}>导出 ▾</button>
          {expMenu && <div className="bom-menu" onMouseLeave={() => setExpMenu(false)}>
            <a href={bomExportOriginalUrl(entry.id)}><b>原版核算表（源附件）</b><span>审批附件 xlsx 原样下载，供留档核对</span></a>
            <a href={bomExportOriginalUrl(entry.id) + '&preview=1'} target="_blank" rel="noreferrer"><b>　🔍 预览原版</b><span>不下载，在新标签页查看</span></a>
            <a href={bomExportPrettyUrl(entry.id)}><b>重排版核算表（美化）</b><span>台账口径重排版，含费用参数与勾稽说明</span></a>
            <a href={bomExportPrettyUrl(entry.id) + '&preview=1'} target="_blank" rel="noreferrer"><b>　🔍 预览重排版</b><span>不下载，在新标签页查看</span></a>
          </div>}</>}
          {!isStd && !edit && cfg?.canAudit && <button className="btn-sec" onClick={startEdit}>✎ 复核（改税率/费用）</button>}
          {edit && <><button className="btn-pri" disabled={saving} onClick={save}>保存并留痕</button>
            <button className="btn-sec" onClick={cancelEdit}>取消</button></>}
          {/* 审核定性＝定稿（业务方定：不做成两个动作）。四步未齐时按钮可点但弹窗内会拦并提示缺哪步 */}
          {!isStd && !edit && cfg?.canAudit && !entry.isFinal && <button className="btn-pri" onClick={() => setAuditM(true)}
            disabled={!entry.stepsOk}
            title={entry.stepsOk ? '填物料类别+是否允许报价，保存即定稿' : '请先确认四步：①BOM清单 ②工艺流程 ③用量自洽 ④报价核算'}
            style={{ background: entry.stepsOk ? 'var(--green)' : undefined, borderColor: entry.stepsOk ? 'var(--green)' : undefined }}>
            ⚑ 审核定稿（定性+毕业进标准台账）</button>}
          {!isStd && !edit && cfg?.canAudit && entry.isFinal && <button className="btn-sec" onClick={() => setAuditM(true)}>⚑ 改定性</button>}
          {!edit && cfg?.canAudit && entry.isFinal && <button className="btn-sec" onClick={unfinalize}>撤销定稿</button>}
          {/* 作废：申请（成本会计）/ 终审批准（财务BP）——作废=标记不删除，两步防一人闭环 */}
          {/* 申请作废：V2.431 恢复（业务方要清残留单）——作废=标记不删、须终审批准；主管理员可自批（同终审口径） */}
          {!edit && entry.active && !entry.voidPending && cfg?.canAudit &&
            <button className="btn-sec" onClick={() => setVoidM('request')} title="申请作废本版（留痕不删除，须财务BP终审批准；主管理员可自批）">⌦ 申请作废</button>}
          {!edit && entry.voidPending && cfg?.canFinalReview &&
            <button className="btn-pri" style={{ background: 'var(--red)', borderColor: 'var(--red)' }}
              onClick={() => setVoidM('review')}>⌦ 作废终审（有待批准）</button>}
        </div>
      </div>
      <div className="body">
        <div className="bom-crumbs"><a className="lk" onClick={onBack}>成本台账</a> / {entry.productName}</div>
        {entry.staleNote && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', marginBottom: 10 }}>
          ⚠ <b>{entry.staleNote}</b>：本品所依赖的上游核算表被替换过，成本可能已变——已把本品打回<b>未复核</b>，请重新走 ③用量自洽 / ④报价核算 确认。确认后此提醒自动消失。</div>}
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
                ['数据来源', entry.origin ? <Origin o={entry.origin} /> : '—'],
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

            {/* ③ 用量自洽（核算表添加量 vs BOM清单用量 逐料比对）*/}
            {step === 'qty' && <>
              <BomCheckSection entry={entry} cfg={cfg} onChanged={onChanged} flash={flash} />
              {!isStd && cfg?.canAudit && <StepConfirm okState={entry.steps?.qty} info={entry.stepsInfo?.qty}
                label="用量自洽无误" onConfirm={(on) => confirmStep('qty', on)} />}
            </>}

            {/* ④ 报价核算（逐料成本 + 「核价」金蝶实采 + 编辑态改税率 + 商品版价税差异）*/}
            {step === 'price' && <>
              {(entry.upstream || []).length > 0 && <UpstreamSection entry={entry} onOpen={onOpen} />}
              {entry.hasGoodsVersion && <GoodsSection entry={entry} isStd={isStd} canAudit={cfg?.canAudit}
                onApply={applyGoods} />}
              <MatSection no={1} title="原料明细" hint="成本不含税 = 添加量 × 含税价 ÷ (1+税率)　·　类型可点改（原料内部）　·　「核价」查金蝶实采" rows={mats}
                seg="原料" prev={prev} prevMat={prevMat} subtotal={matSub} fullIncl={full} onDrill={onOpen} all={all}
                delta={segDelta(mats)} onPrice={cfg?.canPrice ? setPriceMat : null} edit={edit} onTax={setTax}
                spreads={spreads} onSetType={!isStd && cfg?.canAudit && !edit ? setMatType : null}
                invoiceRules={cfg?.invoiceRules} onInvoice={edit ? setInvoice : null} />
              <MatSection no={2} title="包材明细" hint="「核价」查金蝶实采" rows={packs} seg="包材" prev={prev} prevMat={prevMat}
                subtotal={packSub} fullIncl={full} onDrill={onOpen} all={all} delta={segDelta(packs)} onPrice={cfg?.canPrice ? setPriceMat : null} edit={edit} onTax={setTax}
                spreads={spreads} onSetType={null} invoiceRules={cfg?.invoiceRules} onInvoice={edit ? setInvoice : null} />
              {!isStd && !edit && cfg?.canAudit && <StepConfirm okState={entry.steps?.price} info={entry.stepsInfo?.price}
                label="报价核算无误" onConfirm={(on) => confirmStep('price', on)} />}
            </>}
            <div className="foot">①BOM清单、②工艺流程＝研发给的<b>参考材料，只看不确认</b>；要签字的是 ③用量自洽（核算表 vs BOM清单逐料）和 ④报价核算（逐料核价，可改税率，成本与全成本随之重算并留痕）。<b>③④确认后点右上「审核定稿」填物料类别+是否允许报价，保存即定稿</b>。</div>
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
                <div className="bom-rline"><span>源表全成本</span><b>¥ {fmt(entry.comp.srcFull)}</b></div>
                <div className="bom-rline"><span>差异</span>{Math.abs(diff) < EPS
                  ? <b className="ok" style={{ color: 'var(--green)' }}>0.00 · 一致</b>
                  : <b style={{ color: 'var(--amber)' }}>{diff > 0 ? '+' : ''}{fmt(diff)} · 参数已调整</b>}</div>
              </div>
              <div className="bom-rdiv" />
              <div className="bom-ah">变更记录</div>
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
// 「核对」弹窗（业务方 2026-09-05 提）：上半＝金蝶物料档案反查候选；下半＝与台账里同物料编码 / 同 CP 的另一条核算表**逐料对比**
// （核算表添加量 + 含税采购价 + 成本，都有 BOM 清单时再并 BOM 用量），五分项汇总也并排。判断"是同一个东西的新旧版，还是两个不同产品"就看这张表。
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
    getBomEntry(sel).then(r => setOther(r.entry)).catch(e => flash('打不开对方核算表：' + e.message))
  }, [sel])
  const adopt = async (code) => { setBusy(true); try { await onAdopt(code) } finally { setBusy(false) } }
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
            <b style={{ fontSize: 12 }}>② 台账里同物料编码 / 同 CP 的其它核算表——逐料对比</b>
            {list.length === 0 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>台账里没有别的记录挂同一物料编码或同一 CP，无需对比。</div>}
            {list.length > 1 && <div className="bom-catpick" style={{ margin: '6px 0' }}>{list.map(o => (
              <button key={o.entryId} className={sel === o.entryId ? 'on' : ''} onClick={() => setSel(o.entryId)}>{o.cpCode} {o.productName}{o.status ? ` · ${o.status}` : ''}</button>))}</div>}
            {list.length === 1 && <div className="muted" style={{ fontSize: 12, margin: '4px 0' }}>对方：<b>{list[0].cpCode} {list[0].productName}</b>{list[0].status ? ` · ${list[0].status}` : ''}{list[0].calcDate ? ` · ${list[0].calcDate}` : ''}</div>}
            {sel && !other && <div className="loading" style={{ padding: 16 }}>读取对方核算表…</div>}
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
              <div className="muted" style={{ fontSize: 11.5, margin: '8px 0 4px' }}>逐料 {rows.length} 项，<b style={{ color: nDiff ? 'var(--amber)' : 'var(--green)' }}>{nDiff ? `${nDiff} 项有差异` : '全部一致'}</b>；差异排前。添加量＝核算表 kg/kg；{showBom ? 'BOM 用量＝研发清单；' : ''}含税价＝研发填的采购价。</div>
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
            title={`台账里 ${x.cpCode} ${x.productName}（${x.status}）已挂此编码——采用后按「后审核的替代先审核的」提示确认${onCompare ? '；点击看两张核算表逐料对比' : ''}`}
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
        {canEdit && <button className="btn-sec" disabled={busy} style={{ padding: '0 8px' }} title="弹窗：金蝶物料档案按 CP 反查 + 与台账里同编码/同CP的核算表逐料对比用量与价格"
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
              {t !== '原辅料' && <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>自产·尝试下钻子核算表</span>}</label>))}
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

function MatSection({ no, title, hint, rows, seg, prev, prevMat, subtotal, fullIncl, onDrill, all, delta, onPrice, edit, onTax, spreads, onSetType, invoiceRules, onInvoice }) {
  return (
    <div className="card bom-sect">
      <div className="bom-secthead"><span className="bom-no">{no}</span><b>{title}</b>
        {hint && <span className="muted" style={{ fontSize: 11 }}>{hint}</span>}<span style={{ flex: 1 }} />
        <span className="muted" style={{ fontSize: 12 }}><b>{rows.length}</b> 条明细{delta}</span></div>
      <div className="tbl-wrap" style={{ border: 'none', overflowX: 'auto' }}>
        <table style={{ tableLayout: 'fixed', minWidth: 940 }}>
          <colgroup>{MAT_COLS.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead><tr>
          <th className="th">类型</th><th className="th">{seg === '包材' ? '包材编码' : '物料编码'}</th><th className="th">物料名称</th>
          <th className="th">型号</th><th className="th">单位</th>
          <th className="th" style={{ textAlign: 'right' }}>{seg === '包材' ? '用量' : '添加量 kg/kg'}</th>
          <th className="th" style={{ textAlign: 'right' }}>含税采购价</th><th className="th" style={{ textAlign: 'right' }}>税率</th>
          <th className="th">发票类型</th>
          <th className="th" style={{ textAlign: 'right' }}>成本不含税</th><th className="th" style={{ textAlign: 'right' }}>成本含税</th>
          <th className="th" style={{ textAlign: 'right' }}>占比</th><th className="th">说明</th>
          <th className="th">核价</th>
        </tr></thead><tbody>
          {rows.map((m, i) => {
            const p = prevMat(m.matName)
            const isPack = seg === '包材'
            // 物料子类：人工覆盖(m.subType)优先，否则按名字/编码建议——名带半成品/复配料/复合 或 SZF 码 → 复配料，否则原辅料。
            const autoNested = /半成品|复配料|复合/.test(m.matName || '') || (m.matCode || '').startsWith('SZF')
            const subType = isPack ? '包材' : (m.subType || (autoNested ? '复配料' : '原辅料'))
            const nested = !isPack && subType !== '原辅料'      // 复配料/自产半成品 → 尝试下钻子核算表
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
                  ? <a className="lk" onClick={() => onDrill(semiEntry.id)}>{m.matName} ↗ 子核算表</a>
                  : <>{m.matName}{nested && <span className="muted" style={{ fontSize: 10, marginLeft: 6 }}>{subType}·台账无子表</span>}</>}</td>
                <td className="muted" style={NOWRAP} title={m.model}>{m.model && m.model !== '0' ? m.model : '—'}</td>
                <td className="muted">{m.unit || '—'}</td>
                <td className="num">{(m.qtyPerKg ?? 0).toFixed(4)}{qMark && <Tri d={qMark} title={`添加量较上一版（${prev?.calcDate}）：${(p.qtyPerKg ?? 0).toFixed(4)} → ${(m.qtyPerKg ?? 0).toFixed(4)}`} />}</td>
                <td className="num">{fmt(m.priceIncl)}{pMark && <Tri d={pMark} title={`含税价较上一版（${prev?.calcDate}）：${fmt(p.priceIncl)} → ${fmt(m.priceIncl)}`} />}</td>
                <td className="num">{edit && onTax
                  ? <span className="bom-taxedit"><input type="number" step="1" min="0" value={m.taxRate != null ? +(m.taxRate * 100).toFixed(2) : ''} onChange={e => onTax(m, e.target.value)} />%</span>
                  : <span className="muted">{m.taxRate != null ? (m.taxRate * 100).toFixed(0) + '%' : '—'}</span>}</td>
                <td>{edit && onInvoice
                  ? <select value={m.invoiceType || ''} onChange={e => onInvoice(m, e.target.value)} title="改发票类型→按其算法重算成本不含税（基础数据可维护）"
                      style={{ fontSize: 11, maxWidth: '100%', padding: '1px 2px' }}>
                      {m.invoiceType && !(invoiceRules || []).some(r => r.type === m.invoiceType) && <option value={m.invoiceType}>{m.invoiceType}</option>}
                      {(invoiceRules || []).map(r => <option key={r.type} value={r.type}>{r.type}</option>)}
                    </select>
                  : <span className="muted" style={{ fontSize: 12 }}>{m.invoiceType || '—'}</span>}</td>
                <td className="num" style={{ fontWeight: 600 }}>{fmt(m.costExcl, 4)}
                  {p && Math.abs(dCost) > EPS && <span className={'bom-cbadge ' + (dCost > 0 ? 'up' : 'down')} title={`成本含税较上一版（${prev?.calcDate}）${dCost > 0 ? '+' : ''}${fmt(dCost, 4)} 元/kg`}>{dCost > 0 ? '▲' : '▼'}{fmt(Math.abs(dCost))}</span>}
                  {!p && prev && <span className="bom-cbadge new" title="较上一版新增物料">新增</span>}</td>
                <td className="num" style={{ fontWeight: 600 }}>{fmt((m.costExcl || 0) * GROSS, 4)}</td>
                <td className="num muted">{fullIncl ? pct(((m.costExcl || 0) * GROSS) / fullIncl) : '—'}</td>
                <td style={NOWRAP} title={note ? specBrand + '　' + note : specBrand}>{specBrand ? <span>{specBrand}{note && <span className="bom-noteic"> ⓘ</span>}</span>
                  : (note ? <span className="bom-noteic">ⓘ</span> : '—')}</td>
                <td>{(m.matCode || '').trim() && onPrice
                  ? <a className="lk" onClick={() => onPrice(m)} title={bigSpread ? `⚠ 同编码在别的产品里研发定价差异较大：${fmt(sp.min)}~${fmt(sp.max)}（跨 ${sp.count} 处，差 ${(sp.spread * 100).toFixed(0)}%）——点开 BOM反查看` : '查金蝶实采价 / BOM反查同编码'}>核价 ↗{bigSpread && <span style={{ color: 'var(--stop, #a83529)', marginLeft: 3 }} title="研发跨产品同料定价差异大">●</span>}</a>
                  : <span className="muted" style={{ fontSize: 11 }}>—</span>}</td>
              </tr>
            )
          })}
          <tr className="bom-subrow"><td colSpan={9}>{seg}小计</td>
            <td className="num" style={{ fontWeight: 700 }} title="不含税">{fmt(subtotal, 4)}</td>
            <td className="num" style={{ fontWeight: 700 }} title="含税">{fmt((subtotal || 0) * GROSS, 4)}</td>
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
  const [cfg, setCfg] = useState(null)
  const [inv, setInv] = useState(null)               // 发票规则 {rules, modes, canConfig, hint}
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savingInv, setSavingInv] = useState(false)
  const [toast, setToast] = useState('')
  const flash = (t) => { setToast(t); setTimeout(() => setToast(''), 2400) }
  useEffect(() => {
    (async () => {
      try { setCfg(await getBomSettings()) } catch (e) { flash('加载失败：' + e.message) }
      try { setInv(await getBomInvoiceRules()) } catch (e) { /* 发票规则失败不挡脱敏页 */ }
      setLoading(false)
    })()
  }, [])
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
      <div className="body">
        <div className="card bom-sect">
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
        </div>

        <div className="card bom-sect">
          <div className="bom-secthead"><span className="bom-no">票</span><b>发票类型 → 成本不含税 算法</b>
            <span className="muted" style={{ fontSize: 11 }}>{inv?.hint || '对应成本核算表 N 列公式，可维护'}</span></div>
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
        </div>
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
      <div className="foot" style={{ padding: '0 14px 12px' }}>此步只看研发清单原样（不确认）；逐料用量与核算表的比对在③用量自洽。</div>
    </div>
  )
}

// 上游链路（半成品/复配料作原料进上层）：口径 quirk#5——下层「全成本含税」＝本品料行的「含税价」。
// 上游未定稿 / 价格对不上 → 本品**不许先定稿**（成本建在未经确认的数上）；台账里找不到 → 链路不通，警告。
function UpstreamSection({ entry, onOpen }) {
  const ups = entry.upstream || []
  const block = entry.upstreamBlock || []
  if (!ups.length) return null
  return (
    <div className="card bom-sect" style={{ borderLeft: '3px solid ' + (block.length ? 'var(--red)' : 'var(--green)') }}>
      <div className="bom-secthead"><span className="bom-no">⛓</span><b>上游链路（半成品 / 复配料）</b>
        <span className="muted" style={{ fontSize: 11 }}>下层「全成本含税」＝本品料行的「含税价」</span>
        <span style={{ flex: 1 }} />
        {block.length ? <span className="tag leak">上游未就绪 · 不能定稿</span> : <span className="tag ok">链路已通</span>}</div>
      <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
        <th className="th">本品料行</th><th className="th" style={{ textAlign: 'right' }}>本品用的含税价</th>
        <th className="th" style={{ textAlign: 'right' }}>上游全成本含税</th><th className="th">上游状态</th><th className="th"></th>
      </tr></thead><tbody>
        {ups.map((u, i) => (<tr key={i} className={(!u.isFinal || !u.priceOk) ? 'bom-nbrow' : ''}>
          <td style={{ fontWeight: 600 }}>{u.matName}</td>
          <td className="num">{fmt(u.priceUsed)}</td>
          <td className="num">{fmt(u.upFull)}</td>
          <td>{!u.priceOk ? <span className="tag leak">价格对不上（差 {fmt(Math.abs((u.priceUsed || 0) - (u.upFull || 0)), 4)}）</span>
            : u.isFinal ? <span className="tag ok">已定稿</span>
              : <span className="tag werr">{u.status || '未复核'}·未定稿</span>}</td>
          <td><a className="lk" onClick={() => onOpen(u.entryId)}>看子核算表 ›</a></td>
        </tr>))}
      </tbody></table></div>
      {block.length > 0 && <div className="bom-chkfail" style={{ margin: '0 14px 12px' }}>
        <b>⛔ 上游未就绪，本品不能定稿</b>：{block.join('；')}。<br />
        半成品的成本没确认，成品的成本就是建在未确认的数上——先把上游复核定稿，再回来定本品。</div>}
      <div className="foot" style={{ padding: '0 14px 10px' }}>只列**台账里真有同名子核算表**的料行；外购原料/包材不在此列（名字带「复合/复配料」的外购件不算上游）。</div>
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

// 研发两表自洽校验：成本核算表(添加量) vs 研发BOM清单(用量)。缺料/多料/用量不符=BOM结构或用量不一致。
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
          <span className="muted" style={{ fontSize: 11 }}>成本核算表 vs 研发 BOM清单：用量 / 缺料 / 多料</span></div>
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
  const STAT = { '一致': 'ok', '用量不符': 'werr', '核算表缺料': 'leak', '核算表多料': 'late' }
  return (
    <div className="card bom-sect">
      <div className="bom-secthead"><span className="bom-no">✓</span><b>用量自洽校验</b>
        <span className="muted" style={{ fontSize: 11 }}>成本核算表 vs 研发 BOM清单：<b>只核对用量</b>（按编码对齐，不判类型）</span>
        <span style={{ flex: 1 }} />
        {inherited && <span className="tag late" style={{ marginRight: 6 }}>沿用历史清单</span>}
        {s.ok ? <span className="tag ok">用量全平 · {s.total} 料</span>
          : <span className="tag werr">{[s.qtyMismatch && `用量不符 ${s.qtyMismatch}`, s.missing && `核算表缺料 ${s.missing}`, s.extra && `核算表多料 ${s.extra}`].filter(Boolean).join(' · ')}</span>}
        <Seg value={onlyDiff ? 'diff' : 'all'} onChange={v => setOnlyDiff(v === 'diff')} opts={[['diff', '只看差异'], ['all', '全部']]} />
      </div>
      {inherited && <div style={{ padding: '12px 14px 0' }}><div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)' }}>
        ⓘ 本单未附 BOM清单，按产品编码 <b className="mono">{inherited.fromCp}</b> 沿用历史清单校验（第 {inherited.fromEntryId} 号 · {inherited.fromApproval ? '审批…' + String(inherited.fromApproval).slice(-4) + ' · ' : ''}{inherited.fromDate}）——改配方必换编码，同编码=配方未变。研发补传本单清单后可上传替换。</div></div>}
      {s.ok && onlyDiff
        ? <div style={{ padding: 14 }}><div className="banner" style={{ background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-line)' }}>✓ 核算表与研发 BOM清单逐料用量一致（{s.total} 味料全平）——两份表用量对得上。</div></div>
        : <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
          <th className="th">物料</th>
          <th className="th" style={{ textAlign: 'right' }}>核算表 添加量</th>
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

// ============ 版本对比 ============
function Compare({ entry, all, onBack, flash }) {
  const versions = (all || []).filter(x => x.productKey === entry.productKey)
    .slice().sort((a, b) => (a.calcDate || '').localeCompare(b.calcDate || '') || (a.id - b.id))
  const [aId, setAId] = useState(versions[0]?.id)
  const [bId, setBId] = useState(versions[versions.length - 1]?.id)
  const [mode, setMode] = useState('diff')
  const A = versions.find(v => v.id === aId) || versions[0]
  const B = versions.find(v => v.id === bId) || versions[versions.length - 1]
  const verLabel = (v) => `${v.cpCode} · ${v.calcDate} · ¥${fmt(v.comp.full)} · 审批…${(v.approval || '').slice(-4)}`
  const matOf = (v, name) => (v.materials || []).find(m => clean(m.matName) === clean(name))

  const num = [], info = []
  const bMats = B.materials || [], aMats = A.materials || []
  bMats.forEach(m => {
    const a = matOf(A, m.matName)
    if (!a) { num.push({ cat: '新增', catCls: 'ok', name: m.matName, field: '整行', va: null, vb: (m.costExcl || 0) * GROSS, d: (m.costExcl || 0) * GROSS }); return }
    if (Math.abs((m.priceIncl || 0) - (a.priceIncl || 0)) > 1e-9)
      num.push({ cat: '调价', catCls: 'teal', name: m.matName, field: '含税价', va: a.priceIncl, vb: m.priceIncl, d: ((m.costExcl || 0) - (a.costExcl || 0)) * GROSS })
    else if (Math.abs((m.qtyPerKg || 0) - (a.qtyPerKg || 0)) > 1e-9)
      num.push({ cat: '调量', catCls: 'kd', name: m.matName, field: '添加量', va: (a.qtyPerKg ?? 0).toFixed(4), vb: (m.qtyPerKg ?? 0).toFixed(4), d: ((m.costExcl || 0) - (a.costExcl || 0)) * GROSS })
    else if (Math.abs((m.costExcl || 0) - (a.costExcl || 0)) > 1e-9)
      num.push({ cat: '变更', catCls: 'kd', name: m.matName, field: '成本', va: fmt(a.costExcl, 4), vb: fmt(m.costExcl, 4), d: ((m.costExcl || 0) - (a.costExcl || 0)) * GROSS })
    if (Math.abs((m.taxRate || 0) - (a.taxRate || 0)) > 1e-9)
      num.push({ cat: '税率', catCls: 'late', name: m.matName, field: '税率', va: (a.taxRate * 100).toFixed(0) + '%', vb: (m.taxRate * 100).toFixed(0) + '%', d: 0 });
    ['matCode:物料编码', 'model:型号', 'spec:规格', 'brand:品牌'].forEach(f => {
      const [k, lab] = f.split(':'); const av = a[k] === '0' ? '' : (a[k] || ''); const bv = m[k] === '0' ? '' : (m[k] || '')
      if (av !== bv) info.push({ name: m.matName, field: lab, va: av || '—', vb: bv || '—' })
    })
  })
  aMats.forEach(m => { if (!matOf(B, m.matName)) num.push({ cat: '移除', catCls: 'werr', name: m.matName, field: '整行', va: (m.costExcl || 0) * GROSS, vb: null, d: -(m.costExcl || 0) * GROSS }) })
  ;[['mfg', '加工费'], ['load', '装卸费'], ['adm', '管理费']].forEach(([k, lab]) => {
    if (Math.abs((B.fee[k] || 0) - (A.fee[k] || 0)) > 1e-9)
      num.push({ cat: '费用', catCls: 'late', name: lab, field: '含税', va: fmt(A.fee[k]), vb: fmt(B.fee[k]), d: (B.fee[k] || 0) - (A.fee[k] || 0) })
  })
  if (A.cpCode !== B.cpCode) info.push({ name: '表头', field: '产品编号', va: A.cpCode, vb: B.cpCode })
  num.sort((x, y) => Math.abs(y.d) - Math.abs(x.d))
  const dd = (B.comp.full || 0) - (A.comp.full || 0)

  return (
    <>
      <div className="head">
        <div><div className="h-title">版本对比 · {entry.productName}</div>
          <div className="h-sub">同一产品不同入账版本的差异识别 · 数值差异 / 信息差异，逐行标注成本影响</div></div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-sec" onClick={onBack}>返回核算表</button>
          <button className="btn-sec" onClick={() => flash('导出差异清单 xlsx')}>导出差异清单</button>
        </div>
      </div>
      <div className="body">
        <div className="bom-crumbs"><a className="lk" onClick={onBack}>成本台账</a> / {entry.productName} / 版本对比</div>
        <div className="card bom-filterbar">
          <span className="flabel">对比版本</span>
          <select value={aId} onChange={e => setAId(+e.target.value)} style={{ minWidth: 280 }}>{versions.map(v => <option key={v.id} value={v.id}>{verLabel(v)}</option>)}</select>
          <b>vs</b>
          <span className="flabel">当前版本</span>
          <select value={bId} onChange={e => setBId(+e.target.value)} style={{ minWidth: 280 }}>{versions.map(v => <option key={v.id} value={v.id}>{verLabel(v)}</option>)}</select>
          <span style={{ flex: 1 }} />
          <Seg value={mode} onChange={setMode} opts={[['diff', '只看差异'], ['full', '完整对比']]} />
        </div>
        <div className="card bom-stats">
          <Stat lab="全成本（含税）对比版 → 当前版" v={`${fmt(A.comp.full)} → ${fmt(B.comp.full)}`} small suf="" />
          <div className="bom-stat"><div className="bom-stat-l">变化</div>
            <div className="bom-stat-v" style={{ fontSize: 18, color: dd > 0 ? 'var(--red)' : 'var(--green)' }}>{dd > 0 ? '▲ +' : '▼ '}{fmt(Math.abs(dd))}</div>
            <small>元/kg · {A.comp.full ? ((dd / A.comp.full) * 100).toFixed(1) : '0'}%</small></div>
          <Stat lab="数值差异" v={num.length} suf="处" />
          <Stat lab="信息差异" v={info.length} suf="处" />
        </div>

        {mode === 'diff' ? <>
          <div className="card bom-sect">
            <div className="bom-secthead"><span className="bom-no">1</span><b>数值差异</b>
              <span className="muted" style={{ fontSize: 11 }}>单价 / 用量 / 税率 / 费用 / 新增移除</span>
              <span style={{ flex: 1 }} /><span className="muted"><b>{num.length}</b> 处</span></div>
            <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
              <th className="th">类别</th><th className="th">物料 / 项目</th><th className="th">字段</th>
              <th className="th" style={{ textAlign: 'right' }}>对比版 · {A.calcDate.slice(5)}</th>
              <th className="th" style={{ textAlign: 'right' }}>当前版 · {B.calcDate.slice(5)}</th>
              <th className="th" style={{ textAlign: 'right' }}>Δ成本（含税）元/kg</th>
            </tr></thead><tbody>
              {num.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 22 }}>无数值差异 —— 两版金额完全一致</td></tr>}
              {num.map((r, i) => (<tr key={i}>
                <td><span className={'tag ' + (r.catCls === 'teal' ? 'ok' : r.catCls)} style={r.catCls === 'teal' ? { color: 'var(--teal)', background: 'var(--teal-bg)', borderColor: 'var(--teal)' } : {}}>{r.cat}</span></td>
                <td style={{ fontWeight: 600 }}>{r.name}</td><td className="muted">{r.field}</td>
                <td className="num bom-old">{r.va == null ? '—' : (typeof r.va === 'number' ? fmt(r.va) : r.va)}</td>
                <td className={'num ' + (r.d > 0 ? 'bom-up' : r.d < 0 ? 'bom-down' : '')}>{r.vb == null ? '—' : (r.d > 0 ? '▲ ' : r.d < 0 ? '▼ ' : '') + (typeof r.vb === 'number' ? fmt(r.vb) : r.vb)}</td>
                <td className="num">{Math.abs(r.d) < 1e-6 ? <span className="muted">—</span> : <span className={r.d > 0 ? 'bom-up' : 'bom-down'}>{r.d > 0 ? '▲ +' : '▼ -'}{fmt(Math.abs(r.d), 4)}</span>}</td>
              </tr>))}
            </tbody></table></div>
          </div>
          <div className="card bom-sect">
            <div className="bom-secthead"><span className="bom-no">2</span><b>信息差异</b>
              <span className="muted" style={{ fontSize: 11 }}>编码 / 型号 / 规格 / 品牌 的补全或变化（不影响金额）</span>
              <span style={{ flex: 1 }} /><span className="muted"><b>{info.length}</b> 处</span></div>
            <div className="tbl-wrap" style={{ border: 'none' }}><table><thead><tr>
              <th className="th">物料</th><th className="th">字段</th><th className="th">版本A</th><th className="th">版本B</th>
            </tr></thead><tbody>
              {info.length === 0 && <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 22 }}>无信息差异</td></tr>}
              {info.map((r, i) => <tr key={i}><td style={{ fontWeight: 600 }}>{r.name}</td><td className="muted">{r.field}</td>
                <td className="bom-old">{r.va}</td><td>{r.vb}</td></tr>)}
            </tbody></table></div>
          </div>
        </> : <FullCompare A={A} B={B} matOf={matOf} />}
        <div className="foot">匹配规则：明细按物料名称对齐（编码缺失时仍可比）；Δ与成本列均为含税口径（不含税×1.13，与台账一致），逐行 Δ 相加＝全成本变化。红▲=涨 绿▼=跌 灰=旧值。</div>
      </div>
    </>
  )
}

function FullCompare({ A, B, matOf }) {
  const seg = (label) => {
    const bRows = (B.materials || []).filter(m => m.seg === label)
    const aOnly = (A.materials || []).filter(m => m.seg === label && !matOf(B, m.matName))
    const rows = [...bRows.map(m => ({ m, a: matOf(A, m.matName), removed: false })), ...aOnly.map(m => ({ m, a: m, removed: true }))]
    return { label, rows }
  }
  const cell = (cur, prev, pick, dec = 4) => {
    const cv = pick(cur), pv = prev ? pick(prev) : null
    const d = pv == null ? 0 : (cv || 0) - (pv || 0)
    return { cv, pv, d, dec }
  }
  const Row = ({ m, a, removed }) => {
    const nested = m.matName === '复合调味料'
    const q = cell(m, a, x => x.qtyPerKg)
    const p = cell(m, a, x => x.priceIncl, 2)
    const c = { cv: (m.costExcl || 0) * GROSS, pv: a && !removed ? (a.costExcl || 0) * GROSS : (removed ? (m.costExcl || 0) * GROSS : null) }
    c.d = removed ? -(m.costExcl || 0) * GROSS : (a ? ((m.costExcl || 0) - (a.costExcl || 0)) * GROSS : (m.costExcl || 0) * GROSS)
    const oc = (v, dec = 4) => <td className="num bom-old">{v == null ? '—' : fmt(v, dec)}</td>
    const nc = (v, d, dec = 4) => <td className={'num ' + (d > 0 ? 'bom-up' : d < 0 ? 'bom-down' : '')}>{v == null ? '—' : (d > 0 ? '▲ ' : d < 0 ? '▼ ' : '') + fmt(v, dec)}</td>
    return <tr>
      <td>{nested ? <span className="tag late">复配料</span> : (m.seg === '原料' ? <span className="tag ok">原辅料</span> : <span className="tag werr">包材</span>)}</td>
      <td style={{ fontWeight: 600 }}>{m.matName} {removed && <span className="tag werr">移除</span>}{!a && !removed && <span className="tag ok">新增</span>}</td>
      {oc(removed ? m.qtyPerKg : q.pv)}{nc(removed ? null : q.cv, q.d)}
      {oc(removed ? m.priceIncl : p.pv, 2)}{nc(removed ? null : p.cv, p.d, 2)}
      {oc(removed ? c.pv : (a ? (a.costExcl || 0) * GROSS : null))}{nc(removed ? null : c.cv, c.d)}
      <td className="num">{Math.abs(c.d) < 1e-6 ? <span className="muted">—</span> : <span className={c.d > 0 ? 'bom-up' : 'bom-down'}>{c.d > 0 ? '▲ +' : '▼ -'}{fmt(Math.abs(c.d), 4)}</span>}</td>
    </tr>
  }
  const subRow = (label, av, bv) => { const d = (bv || 0) - (av || 0); return (
    <tr className="bom-subrow"><td colSpan={2}>{label}</td>
      <td colSpan={2} className="num bom-old">{fmt(av)}</td>
      <td colSpan={2} className={'num ' + (d > 0 ? 'bom-up' : d < 0 ? 'bom-down' : '')}>{d > 0 ? '▲ ' : d < 0 ? '▼ ' : ''}{fmt(bv)}</td>
      <td className="num">{Math.abs(d) < 1e-6 ? <span className="muted">—</span> : <span className={d > 0 ? 'bom-up' : 'bom-down'}>{d > 0 ? '▲ +' : '▼ -'}{fmt(Math.abs(d), 4)}</span>}</td></tr>) }
  return (
    <div className="card bom-sect">
      <div className="bom-secthead"><span className="bom-no">≡</span><b>完整对比</b>
        <span className="muted" style={{ fontSize: 11 }}>全部明细并排 · 红▲=上涨 绿▼=下降 灰=旧值 · 含小计与费用链</span></div>
      <div className="tbl-wrap" style={{ border: 'none' }}><table><thead>
        <tr><th className="th" rowSpan={2}>类型</th><th className="th" rowSpan={2}>物料</th>
          <th className="th" colSpan={3} style={{ textAlign: 'center', borderLeft: '1px solid var(--line)' }}>对比版 · {A.cpCode} · {A.calcDate.slice(5)}</th>
          <th className="th" style={{ display: 'none' }} />
          <th className="th" rowSpan={2} style={{ textAlign: 'right' }}>Δ成本（含税）</th></tr>
        <tr><th className="th" style={{ textAlign: 'right', borderLeft: '1px solid var(--line)' }}>添加量</th><th className="th" style={{ textAlign: 'right' }}>含税价</th><th className="th" style={{ textAlign: 'right' }}>成本(含税)</th>
          <th className="th" style={{ textAlign: 'right' }}>添加量</th><th className="th" style={{ textAlign: 'right' }}>含税价</th><th className="th" style={{ textAlign: 'right' }}>成本(含税)</th></tr>
      </thead><tbody>
        {['原料', '包材'].map(label => { const s = seg(label); return (
          <React.Fragment key={label}>
            {s.rows.map((r, i) => <Row key={label + i} {...r} />)}
            {subRow(`${label}小计（含税）`, (A[label === '原料' ? 'matSubtotal' : 'packSubtotal'] || 0) * GROSS, (B[label === '原料' ? 'matSubtotal' : 'packSubtotal'] || 0) * GROSS)}
          </React.Fragment>) })}
        {subRow('加工费（含税）', A.fee.mfg, B.fee.mfg)}
        {subRow('装卸费（含税）', A.fee.load, B.fee.load)}
        {subRow('管理费（含税）', A.fee.adm, B.fee.adm)}
        {subRow('全成本（含税）', A.comp.full, B.comp.full)}
      </tbody></table></div>
    </div>
  )
}

// ============ 作废：申请 / 终审批准（业务方定 2026-09-04）============
// 作废＝**标记**不是删除：记录留着、留痕，只是退出工作区与标准成本库。
// 两步走防一人闭环：成本会计**申请**（理由必填）→ 财务经理**批准**才真作废；申请人不得自批。
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
function IntakeModal({ cfg, onClose, onDone, flash }) {
  const [appno, setAppno] = useState('')
  const [busy, setBusy] = useState('')
  const [res, setRes] = useState(null)
  useEffect(() => { const h = (e) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])

  const doIntake = async () => {
    if (!appno.trim()) return flash('请填钉钉审批编号')
    setBusy('dt')
    try {
      const r = await bomIntake(appno.trim())
      if (!r.ok) { flash(r.msg || '立项失败'); setRes(r.commentPending ? r : null) }
      else setRes(r)
    } catch (e) { flash('立项失败：' + e.message) } finally { setBusy('') }
  }
  const doUpload = async (files) => {
    if (!files || !files.length) return
    setBusy('up')
    try {
      const up = await bomUpload([...files], appno.trim())
      if (!up.ok) return flash(up.msg || '上传失败')
      const bk = await bomBook(up.stagingId, up.records.map(x => x.idx))   // 全量交给后端判，不在这勾选
      setRes({ ok: true, approvalNo: appno.trim(), booked: bk.booked, rejected: bk.rejected,
               skipped: bk.skipped, warnings: up.warnings || [] })
    } catch (e) { flash('上传失败：' + e.message) } finally { setBusy('') }
  }

  return (
    <div className="bom-mask" onClick={e => { if (e.target.classList.contains('bom-mask')) onClose() }}>
      <div className="bom-modal" style={{ width: 'min(620px,100%)' }}>
        <div className="bom-mhead"><b>立项（生成待办）</b><span className="bom-x" onClick={onClose}>✕</span></div>
        <div className="bom-msub">录入钉钉审批编号即可立项。系统会抓附件、解析、**能入账的自动入账，不能入的记为「待修」**——
          具体哪些能入、哪里不对、怎么修，都在<b>处理页</b>里看。</div>

        <div className="bom-mstep"><span className="bom-mno">1</span><div style={{ flex: 1 }}>
          <b>钉钉审批编号</b>
          <div className="muted" style={{ fontSize: 12, margin: '3px 0 7px' }}>表单附件 + 评论区附件都会扫。</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="bom-search" style={{ flex: 1 }} placeholder="如 202609011316000251965"
              value={appno} onChange={e => setAppno(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') doIntake() }} />
            <button className="btn-pri" disabled={!!busy || !cfg?.dingtalkConfigured} onClick={doIntake}
              title={cfg?.dingtalkConfigured ? '' : '本机未配置钉钉，用下方上传'}>
              {busy === 'dt' ? '立项中…' : '立项'}</button>
          </div>
          {!cfg?.dingtalkConfigured && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>本机未配置钉钉应用——请用下方上传。</div>}
        </div></div>

        <div className="bom-mor">钉钉扫不到 / 无审批单据时</div>
        <div className="bom-mstep"><span className="bom-mno">2</span><div style={{ flex: 1 }}>
          <b>上传成本核算表</b>
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
          </div>
          {(res.rejected || []).length > 0 && <div className="bom-chkfail">
            {res.rejected.map((r, i) => <div key={i}>· <b>{r.productName}</b>：{r.reason}</div>)}
            <div style={{ marginTop: 4 }}>→ 这些<b>不进台账</b>（红线），但已记为「待修」留在待办里；进处理页可看逐料差异、替换修好的核算表。</div>
          </div>}
          {(res.commentPending || []).length > 0 && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-line)', fontSize: 11.5, marginTop: 6 }}>
            ⚠ 评论区补传了 {res.commentPending.length} 个附件，钉钉权限取不到——请手工下载后用上方上传补入：{res.commentPending.map(c => c.fileName).join('、')}</div>}
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
