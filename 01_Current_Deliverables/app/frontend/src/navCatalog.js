// [Change Log] Date: 2026-09-12 | Author: Codex | Version: V2.564
// Description: Shared delivery labels and display defaults for catalog, home and sidebar.
export const STAGES = [
  { value: '敬请期待', label: '规划中', color: 'var(--ink-2)', bg: 'var(--bg-rail)' },
  { value: '开发中', label: '开发中', color: 'var(--blue)', bg: 'var(--blue-bg)' },
  { value: '测试验证', label: '测试中', color: 'var(--purple)', bg: 'var(--purple-bg)' },
  { value: '人工并行', label: '并行试运行', color: 'var(--amber)', bg: 'var(--amber-bg)' },
  { value: '引擎正常', label: '已验收', color: 'var(--green)', bg: 'var(--green-bg)' },
]
export function stageOf(status) {
  const aliases = { '已上线': '引擎正常', '等待部署': '测试验证', '待验收': '人工并行', '隐藏': '敬请期待' }
  return STAGES.find(s => s.value === (aliases[status] || status)) || STAGES[0]
}
const CONFIG_KEYS = new Set(['basicdata', 'settings', 'logibase', 'clwh', 'bomconfig', 'ecombase'])
export function visibleAt(m, state, position) {
  if (typeof m['show_' + position] === 'boolean') return m['show_' + position]
  if (state?.status === '隐藏') return false
  return position === 'sidebar' ? m.key !== 'ecomsettle' : !m.group_only && !CONFIG_KEYS.has(m.key) && !/^(基础(数据|资料|设置)|系统设置)$/.test(m.label || '')
}
export const sectionCode = (s, sections) => ({ report: 'A1', gl: 'B1', ap: 'C1', cost: 'D1', ar: 'E1', misc: 'F1', common: 'S1' }[s] || `X${sections.findIndex(x => x.key === s) + 1}`)
