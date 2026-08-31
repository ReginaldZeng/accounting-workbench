// [Change Log] Date:2026-08-09 Author:Claude/c Version:V2.248
// 报表板块（子公司报表 + 指标中心）共享语义层。地道 React 重写自样机 子公司报表_样机.html。
//   · 取数派生：R / plOf / agg / baseInfo / seriesOf —— 纯函数，吃 {books,periods,rpt} 数据。
//   · 语义常量：DICT 指标字典 / MAPDEF 报表项映射 / RULEMETA 诊断规则 / DIMS 金蝶核算维度。
//   · runRules 规则引擎；DEF 定义卡文案生成。
// ReportDashboard.jsx 与 MetricCenter.jsx 都从这里取，口径只有一处真相源。
export const GROUP = '__ALL__'

/* ---------- 格式化（单位随 st.unit：10000=万元 / 1=元） ---------- */
export const unitCN = u => (u === 10000 ? '万元' : '元')
export const num = (v, unit, dp) => v == null ? '—'
  : (v / unit).toLocaleString('zh-CN', { minimumFractionDigits: dp ?? (unit === 10000 ? 1 : 2), maximumFractionDigits: dp ?? (unit === 10000 ? 1 : 2) })
export const n0 = (v, unit) => v == null ? '—' : (v / unit).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
export const pct = v => v == null ? '—' : (Math.abs(v) > 999 ? (v > 0 ? '>999%' : '<-999%') : v.toFixed(2) + '%')
export const cn = p => `${p.slice(0, 4)} 年 ${+p.slice(5)} 月`
export const shortName = n => (n || '').replace(/(有限公司|Limited|,Inc)$/, '')

export const prevP = (periods, p) => { const i = periods.indexOf(p); return i > 0 ? periods[i - 1] : null }
export const yoyP = (periods, p) => { const [y, m] = p.split('-'); const c = (+y - 1) + '-' + m; return periods.includes(c) ? c : null }
export const yearStartP = (periods, p) => { const c = (+p.slice(0, 4) - 1) + '-12'; return periods.includes(c) ? c : null }

/* ---------- 取数派生：绑到一份数据集，返回一组闭包工具 ---------- */
export function makeAccessors(data) {
  const { books, periods, rpt } = data
  const CACHE = {}
  function agg(per) {
    const ks = Object.keys(books).map(b => rpt[per][b]).filter(Boolean)
    const out = { bs: [], pl: [], detail: {}, pdetail: {}, kpi: {}, tie: { balance: 0, rev: 0, exp: 0, p4103: 0, posted: false } }
    if (!ks.length) return out
    ks[0].bs.forEach((r, i) => out.bs.push({ g: r.g, n: r.n, v: ks.reduce((s, k) => s + k.bs[i].v, 0) }))
    ks[0].pl.forEach((r, i) => out.pl.push({ n: r.n, sign: r.sign, v: ks.reduce((s, k) => s + k.pl[i].v, 0) }))
    ;['revenue', 'gross', 'op', 'net', 'assets', 'liab', 'equity', 'cash', 'inv', 'ar'].forEach(k =>
      out.kpi[k] = ks.reduce((s, x) => s + (x.kpi[k] || 0), 0))
    out.kpi.gm = out.kpi.revenue ? +(out.kpi.gross / out.kpi.revenue * 100).toFixed(2) : null
    out.kpi.dar = out.kpi.assets ? +(out.kpi.liab / out.kpi.assets * 100).toFixed(2) : null
    ;['balance', 'rev', 'exp', 'p4103'].forEach(k => out.tie[k] = ks.reduce((s, x) => s + x.tie[k], 0))
    out.tie.posted = ks.some(x => x.tie.posted)
    const merge = (src, dst, key) => ks[0][key].forEach(row => {
      dst[row.n] = []
      ks.forEach(k => (k[src][row.n] || []).forEach(a => {
        const e = dst[row.n].find(z => z.c === a.c)
        e ? e.v += a.v : dst[row.n].push({ c: a.c, n: a.n, v: a.v })
      }))
    })
    merge('detail', out.detail, 'bs'); merge('pdetail', out.pdetail, 'pl')
    return out
  }
  function R(per, book) {
    if (!per) return null
    if (book === GROUP) { const k = 'G' + per; return CACHE[k] || (CACHE[k] = agg(per)) }
    return rpt[per] && rpt[per][book]
  }
  function plOf(per, book, basis) {
    const cur = R(per, book)
    if (!cur || basis === 'ytd' || per.endsWith('-01')) return cur
    const pv = R(prevP(periods, per), book)
    if (!pv) return cur
    const o = JSON.parse(JSON.stringify(cur))
    o.pl.forEach((r, i) => r.v = +(r.v - pv.pl[i].v).toFixed(2))
    Object.keys(o.pdetail).forEach(k => o.pdetail[k].forEach(a => {
      const b = (pv.pdetail[k] || []).find(z => z.c === a.c); if (b) a.v = +(a.v - b.v).toFixed(2)
    }))
    ;['revenue', 'gross', 'op', 'net'].forEach(k => o.kpi[k] = +(cur.kpi[k] - pv.kpi[k]).toFixed(2))
    o.kpi.gm = o.kpi.revenue ? +(o.kpi.gross / o.kpi.revenue * 100).toFixed(2) : null
    return o
  }
  /* 利润表/资产负债表基准分别算：一个期间数、一个时点数 */
  function baseInfo(period, cmp, basis) {
    const p = period, y = +p.slice(0, 4), m = +p.slice(5)
    const bsPer = cmp === 'yoy' ? yoyP(periods, p) : prevP(periods, p)
    const bsLabel = cmp === 'yoy' ? `上年同月末 ${bsPer ? cn(bsPer) : ''}` : `上月末 ${bsPer ? cn(bsPer) : ''}`
    let plPer, plLabel
    if (basis === 'ytd') { plPer = yoyP(periods, p); plLabel = plPer ? `上年同期累计 ${y - 1} 年 1–${m} 月` : null }
    else if (cmp === 'yoy') { plPer = yoyP(periods, p); plLabel = plPer ? `上年同月 ${plPer ? cn(plPer) : ''}` : null }
    else { plPer = prevP(periods, p); plLabel = plPer ? `上月 ${plPer ? cn(plPer) : ''}` : null }
    return { plPer, plLabel: plLabel || '无基准期', bsPer, bsLabel: bsPer ? bsLabel : '无基准期' }
  }
  function seriesOf(book, key, period, isPL) {
    const i = periods.indexOf(period), from = Math.max(0, i - 11)
    return periods.slice(from, i + 1).map(p => {
      const o = isPL ? plOf(p, book, 'ytd') : R(p, book)
      return !o || o.kpi[key] == null ? 0 : o.kpi[key]
    })
  }
  return { R, plOf, agg, baseInfo, seriesOf, books, periods }
}

/* ---------- 规则引擎（五条草案规则；阈值待财务确认） ---------- */
export function runRules(cur, pl, baseNet, period, basis) {
  const K = pl.kpi, B = cur.kpi
  const mo = basis === 'ytd' ? (+period.slice(5) || 1) : 1
  const outflow = ['营业成本', '税金及附加', '销售费用', '管理费用', '研发费用']
    .reduce((s, n) => s + (pl.pl.find(x => x.n === n) || { v: 0 }).v, 0)
  return [
    { c: 'R-01', n: '毛利率为负或低于 5%', hit: K.revenue > 0 && K.gm != null && K.gm < 5,
      na: !(K.revenue > 0), why: K.revenue > 0 ? `毛利率 ${pct(K.gm)}` : '本期无收入，无法判定' },
    { c: 'R-02', n: '资产负债率高于 80%', hit: B.dar != null && B.dar > 80,
      na: B.dar == null, why: `资产负债率 ${pct(B.dar)}` },
    { c: 'R-03', n: '本期亏损且较基准扩大', hit: K.net < 0 && baseNet != null && K.net < baseNet,
      na: baseNet == null, why: baseNet == null ? '无基准期' : `净利 ${K.net.toFixed(0)} · 基准 ${baseNet.toFixed(0)}` },
    { c: 'R-04', n: '货币资金不足三个月营业总支出', hit: outflow > 0 && B.cash < outflow / mo * 3,
      na: !(outflow > 0), why: outflow > 0 ? `可覆盖 ${(B.cash / (outflow / mo)).toFixed(1)} 个月` : '本期无营业支出' },
    { c: 'R-05', n: '应收＋存货占总资产超过 50%', hit: B.assets > 0 && (B.ar + B.inv) / B.assets > .5,
      na: !(B.assets > 0), why: B.assets > 0 ? `应收＋存货占比 ${((B.ar + B.inv) / B.assets * 100).toFixed(1)}%` : '总资产为 0' },
  ]
}

/* ---------- 报表准则格式结构 ---------- */
export const BS_ASSET = [
  ['流动资产', ['货币资金', '交易性金融资产', '应收账款', '预付款项', '其他应收款', '存货']],
  ['非流动资产', ['长期股权投资', '固定资产', '固定资产清理', '无形资产', '长期待摊费用', '待处理财产损溢']],
]
export const BS_LIAB = [
  ['流动负债', ['应付账款', '预收款项', '应付职工薪酬', '应交税费', '其他应付款']],
  ['非流动负债', ['递延收益']],
  ['所有者权益', ['实收资本', '资本公积', '未分配利润']],
]
export const PL_LINES = [
  ['lead', '一、营业收入', '营业收入'], ['item', '减：营业成本', '营业成本'],
  ['item', '　　税金及附加', '税金及附加'], ['item', '　　销售费用', '销售费用'],
  ['item', '　　管理费用', '管理费用'], ['item', '　　研发费用', '研发费用'],
  ['item', '　　财务费用', '财务费用'], ['item', '加：其他收益', '其他收益'],
  ['item', '　　投资收益', '投资收益'], ['item', '　　公允价值变动收益', '公允价值变动收益'],
  ['item', '　　资产处置收益', '资产处置收益'], ['item', '减：资产减值损失', '资产减值损失'],
  ['lead', '二、营业利润', '__OP__'], ['item', '加：营业外收入', '营业外收入'],
  ['item', '减：营业外支出', '营业外支出'], ['lead', '三、利润总额', '__TP__'],
  ['item', '减：所得税费用', '__TAX__'], ['lead', '四、净利润', '__NET__'],
]

/* ---------- 指标字典 / 报表项映射 / 诊断规则 / 核算维度（正式版落「指标中心」页维护） ---------- */
export const DICT = {
  '营业收入': { kind: '指标', f: '6001 主营业务收入 + 6051 其他业务收入', src: 'GL_BALANCE · FYtdCredit（本年累计贷方）', tt: '期间数 · 可累计', edge: '损益按月结转、净额被抹平，故只取贷方单边，不减结转借方' },
  '净利润': { kind: '指标', f: '营业利润 + 营业外收入 − 营业外支出', src: '由本表各行程序计算', tt: '期间数 · 可累计', edge: '账套无所得税费用科目，故净利润＝利润总额；应与 4103 本年利润净额一致' },
  '毛利润': { kind: '指标', f: '营业收入 − 营业成本', src: '由本表两项计算', tt: '期间数 · 可累计', edge: '括号内为毛利率＝毛利润÷营业收入；收入为 0 时毛利率显示「—」' },
  '毛利率': { kind: '指标', f: '（营业收入 − 营业成本）÷ 营业收入 × 100%', src: '同上两项', tt: '比率 · 不可累计', edge: '收入为 0 时不计算（显示「—」），不按 0 处理' },
  '总资产': { kind: '指标', f: 'Σ 1 类科目（去重后一级科目）期末余额', src: 'GL_BALANCE · FEndBalance（期末本位币）', tt: '时点数 · 不可累计', edge: '含备抵科目（1231 坏账准备、1602 累计折旧、1702 累计摊销）自然冲减' },
  '资产负债率': { kind: '指标', f: '负债合计 ÷ 资产总计 × 100%', src: '同上', tt: '比率 · 时点', edge: '权益为负（资不抵债）时会超过 100%，属真实情况不做截断' },
  '货币资金': { kind: '指标', f: '1001 库存现金 + 1002 银行存款 + 1012 其他货币资金', src: 'GL_BALANCE · FEndBalance', tt: '时点数 · 不可累计', edge: '不含 1101 交易性金融资产（理财单列）' },
  'R-01': { kind: '规则', f: '毛利率 < 5%', src: '指标：毛利率', tt: '按期判定', edge: '本期无营业收入时判为「无法判定」，不判为命中' },
  'R-02': { kind: '规则', f: '资产负债率 > 80%', src: '指标：资产负债率', tt: '按期判定', edge: '阈值 80% 为草案，待财务确认；不同业态应可分别设阈' },
  'R-03': { kind: '规则', f: '净利润 < 0 且 净利润 < 基准期净利润', src: '指标：净利润 + 当前对比基准', tt: '按期判定', edge: '基准随切片器的「对比基准」变；无基准期时判为「无法判定」' },
  'R-04': { kind: '规则', f: '货币资金 < 月均营业总支出 × 3', src: '货币资金 ÷（营业成本＋税金及附加＋三项期间费用）÷ 月数', tt: '按期判定', edge: '用营业总支出而非仅期间费用——制造主体支出几乎全在营业成本，只看三费会算出上百个月' },
  'R-05': { kind: '规则', f: '（应收账款 + 存货）÷ 总资产 > 50%', src: '资产负债表三项', tt: '按期判定', edge: '阈值 50% 为草案；贸易型与生产型主体合理区间不同' },
}
export const MAPDEF = { '货币资金': '1001 库存现金 + 1002 银行存款 + 1012 其他货币资金', '交易性金融资产': '1101 交易性金融资产', '应收账款': '1122 应收账款 + 1231 坏账准备（备抵，贷方冲减）', '预付款项': '1123 预付账款', '其他应收款': '1221 其他应收款 + 1132 应收利息', '存货': '1401 材料采购 + 1402 在途物资 + 1403 原材料 + 1405 库存商品 + 1408 委托加工物资 + 1411 周转材料', '长期股权投资': '1511 长期股权投资', '固定资产': '1601 固定资产 + 1602 累计折旧（备抵）', '固定资产清理': '1606 固定资产清理', '无形资产': '1701 无形资产 + 1702 累计摊销（备抵）', '长期待摊费用': '1801 长期待摊费用', '待处理财产损溢': '1901 待处理财产损溢', '应付账款': '2202 应付账款', '预收款项': '2203 预收账款', '应付职工薪酬': '2211 应付职工薪酬', '应交税费': '2221 应交税费', '其他应付款': '2241 其他应付款', '递延收益': '2401 递延收益', '实收资本': '4001 实收资本', '资本公积': '4002 资本公积', '未分配利润': '4104 利润分配 + 4103 本年利润', '营业成本': '6401 主营业务成本 + 6402 其他业务支出（累计借方）', '税金及附加': '6403 税金及附加（累计借方）', '销售费用': '6601 销售费用（累计借方）', '管理费用': '6602 管理费用（累计借方）', '研发费用': '6604 研发费用（累计借方）', '财务费用': '6603 财务费用（累计借方，可为负＝净冲减）', '其他收益': '6117 其他收益（累计贷方）', '投资收益': '6111 投资收益（累计贷方）', '公允价值变动收益': '6101 公允价值变动损益（累计贷方）', '资产处置收益': '6115 资产处置损益（累计贷方）', '资产减值损失': '6701 资产减值损失（累计借方）', '营业外收入': '6301 营业外收入（累计贷方）', '营业外支出': '6711 营业外支出（累计借方）' }
export const RULEMETA = {
  'R-01': ['盈利承压', 'mid', 'gross_margin < 5%', '毛利率过低意味着主业几乎不赚钱。制造型主体要结合代工模式看，贸易型主体要查售价与采购价的匹配。'],
  'R-02': ['偿债压力', 'hi', 'debt_ratio > 80%', '资产负债率过高，关注短期偿债能力与股东借款结构。权益为负时会超过 100%，属资不抵债。'],
  'R-03': ['亏损扩大', 'hi', 'net_profit < 0 AND net_profit < base_net', '亏损较基准期进一步扩大，须区分是收入下滑还是费用失控，看贡献桥的最大减项。'],
  'R-04': ['资金链承压', 'hi', 'cash < monthly_outflow × 3', '货币资金覆盖不足三个月营业总支出。用总支出而非仅三费——制造主体支出几乎全在营业成本。'],
  'R-05': ['营运占用', 'mid', '(AR + INV) / total_assets > 50%', '应收与存货占用过半资产，关注回款周期与库存周转，可能存在放宽信用或备货过量。'],
}
export const DIMS = [
  ['FF100002', '银行账号', ['资金看板', '账户台账', '对账程序', '科目余额', '理财对账']],
  ['FF100003', '办公地点', []], ['FF100004', '结算单位', []],
  ['FF100005', '供应商发票', ['物流计提']], ['FF100006', '产品项目(TO C)', ['物流计提', '账单核对']],
  ['FF100007', '品牌项目(TO B)', []], ['FF100008', '营销活动', []], ['FF100009', '市场推广费', []],
  ['FF100010', '产品线', ['物流计提', '账单核对']], ['FFLEX4', '供应商', ['物流计提']],
  ['FFLEX5', '部门', ['物流计提']], ['FFLEX6', '客户', []], ['FFLEX7', '员工', []],
  ['FFLEX8', '物料', []], ['FFLEX9', '费用项目', ['物流计提', '账单核对']], ['FFLEX10', '资产类别', []],
  ['FFLEX11', '组织机构', []], ['FFLEX12', '物料分组', []], ['FFLEX13', '客户分组', []],
]

/* ---------- 定义卡文案：给名称（指标/报表项/规则码）返回结构化定义 ---------- */
export function defOf(name) {
  const d = DICT[name]
  const f = d ? d.f : MAPDEF[name]
  if (!f) return null
  const kind = d ? d.kind : '报表项目'
  const isPoint = /^[124]/.test(f)
  return {
    name, kind, f,
    src: d ? d.src : (isPoint ? 'GL_BALANCE · FEndBalance（期末本位币）' : 'GL_BALANCE · FYtdDebit / FYtdCredit（本年累计单边）'),
    tt: d ? d.tt : (isPoint ? '时点数 · 不可累计' : '期间数 · 可累计'),
    edge: d ? d.edge : '去重口径＝维度合计行＋币别为空＋一级科目；负债与权益的金蝶余额为负，报表展示已取正',
    where: kind === '规则' ? '诊断规则' : '指标字典',
  }
}
