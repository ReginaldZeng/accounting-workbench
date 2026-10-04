// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 产品全成本各页共用：接口、数字格式、成本项目定义、几个小零件。
// 只做展示：金额、分摊、差异一律用后端算好的值，这里不另算口径（每公斤＝金额÷完工量、筛选合计＝逐行相加，仅此两处）。
import React from 'react'

// ── 接口（统一前缀 /api/actual-cost，契约以后端 routers/actual_cost.py 为准）──────────────
const request = async (path, params, method = 'GET', body) => {
  const r = await fetch(`/api/actual-cost/${path}?${new URLSearchParams(params)}`, {
    method, cache: 'no-store',
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  })
  let d = null
  try { d = await r.json() } catch { /* 无 JSON 体 */ }
  if (!r.ok) {
    const detail = d && (d.detail || d.msg)
    throw Error(typeof detail === 'string' ? detail : `请求失败（${r.status}）`)
  }
  return d
}
export const api = {
  state: (org, year, period) => request('state', { org, year, period }),
  generate: (org, year, period) => request('generate', { org, year, period }, 'POST', {}),
  saveInputs: (org, year, period, body) => request('inputs', { org, year, period }, 'POST', body),
  initialize: (org, year, period, source_period) => request('initialize', { org, year, period }, 'POST', { source_period }),
  confirmClose: (org, year, period, rule_version) => request('confirm-close', { org, year, period }, 'POST', { confirmed_closed: true, rule_version }),
  revokeClose: (org, year, period) => request('revoke-close', { org, year, period }, 'POST', {}),
  sources: params => request('sources', params),
  product: params => request('product', params),
  refreshProduct: params => request('product-refresh', params, 'POST'),
  saveNote: (params, body) => request('material-note', params, 'POST', body),
  standards: (org, code) => request('standards', { org, code }),
  compare: params => request('standard-comparison', params),
  exportUrl: params => `/api/actual-cost/export?${new URLSearchParams(params)}`,
}

// ── 数字格式：空值一律显示「—」，绝不渲染成 0 ─────────────────────────────────
const nf = d => new Intl.NumberFormat('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })
const NF = { 0: nf(0), 2: nf(2), 4: nf(4), 6: nf(6) }
const precise = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 10 })
export const isNum = v => typeof v === 'number' && Number.isFinite(v)
export const fmt = (v, d = 2) => (isNum(v) ? (NF[d] || nf(d)).format(v) : '—')
export const fmtFull = v => (isNum(v) ? precise.format(v) : '')
export const fmtPct = (v, d = 2) => (isNum(v) ? (v * 100).toFixed(d) + '%' : '—')
export const fmtSigned = (v, d = 4) => (isNum(v) ? (v > 0 ? '+' : '') + (NF[d] || nf(d)).format(v) : '—')
export const fmtTime = iso => {
  if (!iso) return '—'
  const t = new Date(iso)
  return Number.isNaN(t.getTime()) ? String(iso) : t.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}
export const fmtDate = v => (v ? String(v).slice(0, 10) : '—')

// 数字单元格：显示按位数舍入，悬停能看到完整精度；0 用浅色，和「—」（没有值）区分开
export function Num({ v, d = 2, signed, tone, className = '' }) {
  if (!isNum(v)) return <span className="ac-nil">—</span>
  const cls = [className, v === 0 ? 'ac-zero' : '', tone ? (v > 0 ? 'ac-up' : v < 0 ? 'ac-down' : '') : ''].filter(Boolean).join(' ')
  return <span className={cls || undefined} title={fmtFull(v)}>{signed ? fmtSigned(v, d) : fmt(v, d)}</span>
}

// ── 成本项目（与后端 COST_FIELDS 一一对应，合计＝这 12 项相加）───────────────────
export const GROUPS = [
  { key: 'mat', label: '材料', color: 'var(--cat-1)' },
  { key: 'sub', label: '委外', color: 'var(--cat-8)' },
  { key: 'lab', label: '人工', color: 'var(--cat-3)' },
  { key: 'moh', label: '制造费用', color: 'var(--cat-2)' },
  { key: 'adj', label: '调整', color: 'var(--cat-5)' },
]
export const COMPONENTS = [
  { key: 'material', label: '直接材料', group: 'mat' },
  { key: 'packaging', label: '包材', group: 'mat' },
  { key: 'subcontract', label: '委外加工费', group: 'sub' },   // V2.789：只有车间为「委外」的产品有值
  { key: 'labor', label: '直接人工', group: 'lab' },
  { key: 'indirect', label: '间接人工', group: 'lab' },
  { key: 'water', label: '水费', group: 'moh' },
  { key: 'power', label: '电费', group: 'moh' },
  { key: 'gas', label: '燃气及氮气', group: 'moh' },
  { key: 'depreciation', label: '折旧摊销', group: 'moh' },
  { key: 'rent', label: '租金', group: 'moh' },
  { key: 'other', label: '其他制造费用', short: '其他制费', group: 'moh' },
  { key: 'wip', label: '在产调整', group: 'adj' },
]
export const groupOf = key => GROUPS.find(g => g.key === key)
export const groupSum = (p, key) => COMPONENTS.filter(c => c.group === key).reduce((s, c) => s + (isNum(p[c.key]) ? p[c.key] : 0), 0)

// 构成条：材料 / 委外 / 人工 / 制造费用 / 调整 五段。只画正数部分的占比（在产调整可能为负，负数不占条，悬停看实数）
export function MixBar({ p, height = 8 }) {
  const parts = GROUPS.map(g => ({ ...g, v: groupSum(p, g.key) }))
  const base = parts.reduce((s, x) => s + Math.max(x.v, 0), 0)
  const tip = parts.filter(x => x.v).map(x => `${x.label} ${fmt(x.v)}${base > 0 && x.v > 0 ? `（${fmtPct(x.v / base, 1)}）` : ''}`).join('　')
  return (
    <span className="ac-mix" style={{ height }} title={tip || '本期无成本'} role="img" aria-label={tip || '本期无成本'}>
      {base > 0 && parts.map(x => x.v > 0 && <i key={x.key} style={{ width: (x.v / base * 100) + '%', background: x.color }} />)}
    </span>
  )
}

// 分段切换（两三个互斥选项）
export function Seg({ value, onChange, options, disabled, label }) {
  return (
    <span className="ac-seg" role="radiogroup" aria-label={label}>
      {options.map(([v, text, title]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} title={title} disabled={disabled}
          className={value === v ? 'on' : ''} onClick={() => onChange(v)}>{text}</button>
      ))}
    </span>
  )
}

// 页签
export function Tabs({ value, onChange, tabs, label }) {
  return (
    <nav className="ac-tabs" aria-label={label}>
      {tabs.map(([key, text, badge, tone]) => (
        <button key={key} type="button" className={value === key ? 'on' : ''} aria-current={value === key ? 'page' : undefined} onClick={() => onChange(key)}>
          {text}{badge != null && badge !== 0 && badge !== '' && <span className={'ac-badge ' + (tone || '')}>{badge}</span>}
        </button>
      ))}
    </nav>
  )
}

// 提示条：info / warn / bad / ok
export function Note({ tone = 'info', children, action }) {
  return <div className={'ac-note ' + tone} role={tone === 'bad' ? 'alert' : 'note'}><div>{children}</div>{action}</div>
}

// 翻页
export function Pager({ page, size, total, onPage, busy }) {
  const pages = Math.max(1, Math.ceil(total / size))
  return (
    <div className="ac-pager">
      <span>共 {fmt(total, 0)} 行</span>
      <span className="ac-pager-ctl">
        <button className="btn-sec" disabled={busy || page === 0} onClick={() => onPage(page - 1)}>上一页</button>
        <span>第 {page + 1} / {pages} 页 · 每页 {size} 行</span>
        <button className="btn-sec" disabled={busy || page + 1 >= pages} onClick={() => onPage(page + 1)}>下一页</button>
      </span>
    </div>
  )
}

// ── 明细表字段的中文名 + 单元格文字 ─────────────────────────────────────────
export const LABELS = {
  source_row: '原始行', wo: '工单号', level: '层级', item: '成本项目', expense: '费用项目', input_amount: '本期投入金额', qty: '数量', amount: '金额',
  bill: '单据编号', entry_id: '分录ID', date: '单据日期', code: '物料编码', name: '物料名称', unit: '单位', kind: '单据类型', net_qty: '净数量', net_amount: '净金额',
  supplier: '供应商', price: '不含税单价', tax_price: '含税单价', tax_rate: '税率 %', currency: '币别', org: '组织', status: '单据状态', cancel: '作废',
  unit_cost: '净领用单位成本', standard_qty: '标准参考量', variance_qty: '数量差异', variance_rate: '差异率', bom: '工单BOM版本', comparison_note: '比较说明',
  product: '产品编码', product_qty: '用料单产品数量', product_unit: '产品单位', need_qty: '需求量', picked: '已领量', repicked: '补领量', returned: '良品退料',
  consumed: '单据已消耗', wip: '单据在制量', dosage_type: '用量类型', fixed_scrap: '固定损耗', modified_at: '修改日期', spec: '规格', group: '产品分组',
  base_qty: '基本数量', category: '领用类别编码', category_name: '领用类别', dept: '部门', direction: '出库方向', warehouse: '仓库',
  voucher: '凭证字号', account: '科目', note: '摘要', debit: '借方金额', credit: '贷方金额', year: '年度', period: '期间', form: '单据',
}
const DOC_STATUS = { A: '创建', B: '审核中', C: '已审核', D: '重新审核', Z: '暂存' }
const plain = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 })
// 金额列统一两位、单价列四位，小数点对齐好比对（悬停看完整精度）；数量等其余列按金蝶原值
const FIXED = { amount: 2, net_amount: 2, input_amount: 2, debit: 2, credit: 2, unit_cost: 4, price: 4, tax_price: 4 }
// 原始数据按原值显示：空串留空、null 显示「—」；编号类数字不加千分位；金蝶状态码翻成中文（悬停仍可见原码）
export const cellText = (v, key, numeric) => {
  if (v == null) return '—'
  if (key === 'status') return DOC_STATUS[v] || String(v)
  if (key === 'cancel') return v === 'A' ? '否' : v === 'B' ? '已作废' : String(v)
  if (key === 'dosage_type') return String(v) === '2' ? '变动' : String(v) === '1' ? '固定' : String(v)
  if (key === 'variance_rate' && isNum(v)) return fmtPct(v)
  if (isNum(v)) return numeric === false ? String(v) : FIXED[key] ? fmt(v, FIXED[key]) : plain.format(v)
  const s = String(v)
  return /^\d{4}-\d{2}-\d{2}T/.test(s) ? (s.slice(11, 19) === '00:00:00' ? s.slice(0, 10) : s.slice(0, 16).replace('T', ' ')) : s
}
