// [Change Log] Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.731
// Description: 侧栏改「悬停展开」（移植财务BP工作台 V2.549，交接提示词 §1，口径已定）：常态 72px 图标轨，
//   移入/Tab 进入浮出 240px 盖在页面上（不推挤），图钉固定后占 240（记本机 fw_nav_pinned，默认不固定）；
//   动效在 src/sidebar.css。上下分区：顶部 logo(角标绿点＝金蝶)+标题+「数据源 · 金蝶 V2.xxx」(版本常驻)+图钉，
//   底部 基础数据/系统设置/色调 + 账号（头像姓名末字·「姓名 · 岗位」·门户·退出）。
//   无权限的菜单改为**上锁灰显**（原来是隐藏），让人知道有这个板块、可去首页申请；状态「隐藏」的仍隐藏。
//   数据仍由后端 navDef 驱动（单一真相源），三种节点/徽标/岗位标口径不变。
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 加发票图标，挂 inv/invdesk/invlater/invaudit/invledger
// [Change Log] Date: 2026-09-10 | Author: Codex | Version: V2.553
// Description: 电商月结页进入时自动展开应收模块与电商对账父级，保留完整业务承接。
// [Change Log] Date:2026-07-12 Author:Claude/c Version:V2.105  侧栏按设计稿「导航栏想法」重做
// 展开态＝白色悬浮卡片（分组胶囊带强调色条 + 二级导引条 + 选中态强调条 + 徽章/岗位标）；
// 收起态＝76px 图标轨（状态圆点 + 悬停飞出子菜单）。数据仍由后端 navDef 驱动（单一真相源）。
import React, { useState, useEffect, useRef, useMemo } from 'react'
import '../sidebar.css'

const S = (d, sw = 1.75) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">{d}</svg>
const IC = {
  bank: S(<><path d="M3 10l9-6 9 6" /><path d="M4 10v9M20 10v9M8 10v9M16 10v9M12 10v9M3 21h18" /></>),
  back: S(<path d="M15 18l-6-6 6-6" />),
  home: S(<><path d="M3 11l9-8 9 8" /><path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10" /></>),
  accept: S(<><path d="M4 5h10M4 10h10M4 15h6" /><path d="M14 16l2 2 4-4" /></>),
  chevDown: S(<path d="M6 9l6 6 6-6" />),
  collapse: S(<path d="M11 17l-5-5 5-5M18 17l-5-5 5-5" />),
  expand: S(<path d="M13 17l5-5-5-5M6 17l5-5-5-5" />),
  user: S(<><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 4-6 8-6s8 2 8 6" /></>),
  month: S(<><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M3 9h18M8 2v4M16 2v4M9 15l2 2 4-4" /></>),
  reconcile: S(<><path d="M7 8h13M7 8l3-3M7 8l3 3" /><path d="M17 16H4m13 0l-3-3m3 3l-3 3" /></>),
  ledger: S(<><ellipse cx="12" cy="6" rx="8" ry="3" /><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" /><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>),
  sbal: S(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M9 9v11M15 9v11" /></>),
  wealth: S(<><path d="M3 17l5-5 4 3 6-7" /><path d="M17 8h4v4" /><path d="M3 21h18" /></>),
  logistics: S(<><path d="M3 7h10v9H3z" /><path d="M13 10h4l3 3v3h-7z" /><circle cx="7" cy="18" r="1.6" /><circle cx="17" cy="18" r="1.6" /></>),
  fund: S(<><path d="M3 3v18h18" /><path d="M7 14l3-4 3 3 5-7" /></>),
  ecom: S(<><circle cx="9" cy="20" r="1.4" /><circle cx="18" cy="20" r="1.4" /><path d="M2 3h3l2.4 12.2a1 1 0 0 0 1 .8h8.7a1 1 0 0 0 1-.8L21 7H6" /></>),
  cost: S(<><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M4 7.5l8 4.5 8-4.5M12 12v9" /></>),
  soon: S(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  archive: S(<><rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8" /><path d="M10 12h4" /></>),
  dl: S(<><path d="M12 3v11" /><path d="M8 10l4 4 4-4" /><path d="M4 17v2a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2" /></>),   // 导出
  invoice: S(<><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" /><path d="M9 8h6M9 12h6M9 16h3" /></>),   // 发票（锯齿底的票据）
  basicdata: S(<><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>),
  pin: S(<><path d="M9 4h6l-1 6 3 3H7l3-3z" /><path d="M12 13v7" /></>),
  pinOn: <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3z" /><path d="M12 13v7" fill="none" strokeLinecap="round" /></svg>,
  logout: S(<><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" /><path d="M10 17l-5-5 5-5M5 12h11" /></>),
  portal: S(<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>),
  lock: S(<><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>, 2.2),
  settings: S(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8 2 2 0 1 1-2.8 2.8 1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 0 1-4 0 1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3 2 2 0 1 1-2.8-2.8 1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4 1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8 2 2 0 1 1 2.8-2.8 1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0 1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3 2 2 0 1 1 2.8 2.8 1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></>),
}
const ICON_BY_KEY = {
  periodclose: IC.month, bankrecon: IC.bank, reconcile: IC.reconcile, fundboard: IC.fund, ledger: IC.ledger,
  wealth: IC.wealth, logisticsrecon: IC.logistics, logistics: IC.logistics, logisticspay: IC.reconcile, logisticscost: IC.sbal, logisticsvoucher: IC.ledger,
  ecom: IC.ecom, ecommonth: IC.month, ecomsettle: IC.reconcile, ecombase: IC.basicdata,
  costledger: IC.cost, clwh: IC.basicdata, archive: IC.archive,
  // 报表板块（V2.240）：sbal/journal 两个 key 已退出菜单树，图标随之撤走
  fiacc: IC.sbal, rptdash: IC.fund, rptexport: IC.dl, fisbal: IC.sbal, srcbill: IC.archive, srcexport: IC.dl, fxrate: IC.wealth,
  bomprice: IC.cost, prodbrief: IC.month, revledger: IC.ledger, custrecon: IC.reconcile, ecompromo: IC.ecom,
  // 临工线（V2.318）：tempatt 是纯分组父项，两个三级各给一个图标
  tempatt: IC.user, tempattrev: IC.reconcile, tempattboard: IC.fund,
  // 发票管家（V-draft）：板块与收票用票据图标；后补池=待办时钟、审核=勾选、台账=账本
  inv: IC.invoice, invdesk: IC.invoice, invlater: IC.soon, invaudit: IC.accept, invledger: IC.ledger,
  basicdata: IC.basicdata, settings: IC.settings, acceptance: IC.accept,
}
// 首页卡片与侧栏同一套图标（V2.731）
export const navIcon = k => ICON_BY_KEY[k] || IC.soon
export const LOCK_ICON = IC.lock
const RECON_VIEWS = ['import', 'reconcile', 'fund', 'result']
const VIEWS_BY_KEY = { reconcile: RECON_VIEWS }
// 设计令牌（用应用 CSS 变量，深色自动适配）
const AMBER = 'var(--amber)', AMBER_BG = 'var(--amber-bg)'
const TEAL = 'var(--teal)', TEAL_BG = 'var(--teal-bg)'       // 「人工并行」标签色
const VIOLET = 'var(--purple)', VIOLET_BG = 'var(--purple-bg)'   // 「测试验证」标签色（V2.174 起可进入，须有标记）
const RED = 'var(--red)', RED_BG = 'var(--red-bg)'                 // 验收台账提醒红点（V2.492）
const pillC = bg => (bg.red ? RED : bg.teal ? TEAL : bg.violet ? VIOLET : AMBER)
const pillBG = bg => (bg.red ? RED_BG : bg.teal ? TEAL_BG : bg.violet ? VIOLET_BG : AMBER_BG)
// V2.175：4字状态在窄行会把模块名挤成省略号——侧栏行内用缩写（悬停见全名；飞出菜单/设置页仍全名）
const PILL_SHORT = { '人工并行': '并行', '测试验证': '测试', '开发中·仅你可见': '在建' }

// ── 双色调（V-draft）──────────────────────────────────────────
// 三档：跟随系统(auto,默认) / 浅色 / 深色。存 localStorage fw_theme（按人按浏览器记）。
// 真正生效靠 html[data-theme]（index.html 头部脚本首屏就设好，这里只负责切换与跟随系统变化）。
const THEME_MODES = [
  { key: 'auto', label: '跟随系统', icon: '◐' },
  { key: 'light', label: '浅色', icon: '☀' },
  { key: 'dark', label: '深色', icon: '☾' },
]
function applyTheme(mode) {
  const dark = mode === 'dark' || (mode === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

export default function Sidebar({ view, onSelect, source, user, onLogout, onHome, closed, mods, navDef, ver, focusSection = '', focusParent = '' }) {
  const kd = source === 'kingdee'
  // 主题档位；auto 档要监听系统切换（白天↔夜间自动跟）
  const [theme, setTheme] = React.useState(() => { try { return localStorage.getItem('fw_theme') || 'auto' } catch (e) { return 'auto' } })
  React.useEffect(() => {
    applyTheme(theme)
    try { localStorage.setItem('fw_theme', theme) } catch (e) {}
    if (theme !== 'auto') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const fn = () => applyTheme('auto')
    mq.addEventListener('change', fn)
    return () => mq.removeEventListener('change', fn)
  }, [theme])
  const themeMode = THEME_MODES.find(m => m.key === theme) || THEME_MODES[0]
  const cycleTheme = () => {
    const i = THEME_MODES.findIndex(m => m.key === theme)
    setTheme(THEME_MODES[(i + 1) % THEME_MODES.length].key)
  }
  // 版本行（V2.176/V2.187）：正常只显版本号；开发分支实例、非默认端口才亮出分支/端口。完整明细在悬停提示。
  // 交接口径：版本号**常驻可见**（Owner 靠它确认线上是哪一版）——放在标题下一行。
  const port = window.location.port || (window.location.protocol === 'https:' ? '443' : '80')
  const branchShort = ver && ver.branch ? ver.branch.replace(/^claude\//, '') : ''
  const oddBranch = branchShort && branchShort !== 'main'
  const oddPort = !['8000', '80', '443'].includes(port)
  const verLine = ver ? [ver.ver || '', oddBranch ? branchShort : '', oddPort ? ':' + port : ''].filter(Boolean).join(' · ') : ''
  const verFull = ver ? `版本 ${ver.ver || '未知'}${ver.dirty ? '（有未提交改动）' : ''} · 分支 ${ver.branch || '—'} · 提交 ${ver.commit || '—'} · 端口 ${port}` : ''

  const on = k => !mods || mods[k]?.['可进入'] !== false
  const stat = k => mods?.[k]?.status || ''
  // Legacy settlement deep links still resolve, but the duplicated menu is consolidated.
  const allMods = (navDef?.modules || []).filter(m => m.key !== 'ecomsettle')
  // 岗位标签：后端存的是 key（改名不丢绑定），显示要翻成中文名
  const postLabel = {}; (navDef?.posts || []).forEach(p => { postLabel[p.key] = p.label })
  const posts = k => (mods?.[k]?.posts || []).map(p => postLabel[p] || p)
  // 准入点 cap 由后端算好（_enter_cap）——别在前端拼 "enter:"+key。act＝组内第二道门（V2.142）。
  const hasCap = c => user?.role === 'admin' || !!user?.perms?.[c]
  const canEnter = m => (!m.cap || hasCap(m.cap)) && (!m.act || hasCap(m.act))
  const subOf = {}; allMods.forEach(m => { if (m.parent) subOf[m.key] = m.parent })
  const iconOf = k => ICON_BY_KEY[k] || IC.soon
  const viewsOf = k => VIEWS_BY_KEY[k] || [k]
  const isActive = k => viewsOf(k).includes(view)
  // V2.731：无权限不再隐藏、改上锁灰显（交接口径「无权限页面：上锁灰显（不隐藏）」）；状态「隐藏」的仍不显示
  const shown = m => stat(m.key) !== '隐藏'
  const childrenOf = k => allMods.filter(m => subOf[m.key] === k && shown(m))
  const canSee = m => m.group_only ? childrenOf(m.key).length > 0 : true
  const allSecs = navDef?.sections || []
  const sections = allSecs.filter(s => !s.bottom)
  const bottomKeys = new Set(allSecs.filter(s => s.bottom).map(s => s.key))
  const bottomMods = allMods.filter(m => bottomKeys.has(m.sec) && canSee(m) && shown(m))
  const itemsOf = s => allMods.filter(m => m.sec === s.key && !subOf[m.key] && canSee(m) && shown(m))
  const badgeOf = it => {
    if (it.key === 'periodclose' && closed) return { t: '已封存', pill: true }
    if (!on(it.key)) return { t: stat(it.key) || '未开放', pill: false }   // 未开放：灰字
    const s = stat(it.key)
    if (s === '待验收') return { t: '待验收', pill: true }
    if (s === '人工并行') return { t: '人工并行', pill: true, teal: true }
    if (s === '测试验证') return { t: '测试验证', pill: true, violet: true }
    // 「开发中」只有 dev_users 名单里的人进得来——给显眼标记，免得拿它当已上线的东西讲（V2.242）
    if (s === '开发中') return { t: '开发中·仅你可见', pill: true }
    if (s === '引擎正常') return { t: '引擎正常', live: true }
    return null
  }

  const [open, setOpen] = useState(() => { try { return JSON.parse(localStorage.getItem('fw_nav_groups') || '{}') } catch (e) { return {} } })
  const [expand, setExpand] = useState(() => { try { return JSON.parse(localStorage.getItem('fw_nav_expand') || '{}') } catch (e) { return {} } })
  const isOpen = g => open[g] !== false
  const isExp = k => expand[k] !== false
  const save = (key, v) => { try { localStorage.setItem(key, JSON.stringify(v)) } catch (e) {} }
  const toggleGroup = g => setOpen(o => { const n = { ...o, [g]: !(o[g] !== false) }; save('fw_nav_groups', n); return n })
  const toggleExp = k => setExpand(x => { const n = { ...x, [k]: !(x[k] !== false) }; save('fw_nav_expand', n); return n })

  // —— 悬停展开（V2.731）：常态 72，移入/键盘进入浮出 240；图钉＝固定展开（记本机，默认不固定）——
  const [pinned, setPinned] = useState(() => { try { return localStorage.getItem('fw_nav_pinned') === '1' } catch (e) { return false } })
  const togglePin = () => setPinned(p => { try { localStorage.setItem('fw_nav_pinned', p ? '0' : '1') } catch (e) {} return !p })
  const [hover, setHover] = useState(false)
  const [kbFocus, setKbFocus] = useState(false)
  const [touchOpen, setTouchOpen] = useState(false)
  const expanded = pinned || hover || kbFocus || touchOpen
  const panelRef = useRef(null)
  const canHover = useMemo(() => typeof window === 'undefined' || !window.matchMedia || window.matchMedia('(hover: hover)').matches, [])
  // 触屏没有悬停：点板块/分组展开，点侧栏外收起
  useEffect(() => {
    if (!touchOpen) return undefined
    const off = e => { if (panelRef.current && !panelRef.current.contains(e.target)) setTouchOpen(false) }
    document.addEventListener('pointerdown', off)
    return () => document.removeEventListener('pointerdown', off)
  }, [touchOpen])
  const go = k => { setTouchOpen(false); onSelect(k) }

  // 从逐单工作台进入时，把完整的「应收模块 › 电商对账 › 当前页」链路带到首屏。
  useEffect(() => {
    if (!focusSection || !sections.length) return
    setOpen(old => {
      const next = { ...old }
      sections.forEach(sec => { next[sec.key] = sec.key === focusSection })
      save('fw_nav_groups', next)
      return next
    })
    if (focusParent) setExpand(old => {
      const next = { ...old, [focusParent]: true }
      save('fw_nav_expand', next)
      return next
    })
  }, [focusSection, focusParent, navDef])

  useEffect(() => {
    if (!focusSection || !view) return
    const timer = window.setTimeout(() => {
      document.querySelector(`[data-nav-key="${view}"]`)?.scrollIntoView({ block: 'nearest' })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [view, focusSection, focusParent, navDef, open[focusSection], expand[focusParent]])

  // 当前所在板块自动展开
  useEffect(() => {
    const s = sections.find(sec => itemsOf(sec).some(it => isActive(it.key) || childrenOf(it.key).some(c => isActive(c.key))))
    if (s && !isOpen(s.key)) setOpen(o => ({ ...o, [s.key]: true }))
  }, [view, navDef])

  const tailOf = (it, perm, disabled) => {
    if (!perm) return <span className="tail fx"><span className="lock" title="没有权限，可在首页申请开通">{IC.lock}</span></span>
    const bg = badgeOf(it)
    const ps = disabled ? [] : posts(it.key)
    if (!bg && !ps.length) return null
    return <span className="tail fx">
      {ps.map(p => <span key={p} className="pill" style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }}>{p}</span>)}
      {bg && (bg.live ? <span className="nav-live-dot" title="引擎正常" />
        : bg.pill ? <span className="pill" title={bg.t} style={{ color: pillC(bg), background: pillBG(bg) }}>{PILL_SHORT[bg.t] || bg.t}</span>
          : <span title={bg.t} style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{bg.t}</span>)}
    </span>
  }
  // 收起态图标角上的状态点（展开态看右侧标签）
  const dotOf = (it, perm) => {
    if (!perm) return null
    const bg = badgeOf(it)
    if (!bg) return null
    const c = bg.live ? 'var(--green)' : bg.pill ? pillC(bg) : 'var(--ink-3)'
    return <span className={'bd' + (bg.live ? ' nav-pulse' : '')} style={{ background: c }} />
  }

  // —— 一行 ——（三种节点 V2.52：①叶子 点＝进页面 ②纯分组父项 点＝展开/收起 ③可进入且有子项 点文字＝进页面、点箭头＝展开）
  const Row = (it, level) => {
    const kids = level ? [] : childrenOf(it.key)
    const perm = it.group_only ? true : canEnter(it)
    const disabled = !on(it.key) || !perm
    const groupOnly = !!it.group_only
    const expandable = kids.length > 0
    const active = isActive(it.key) && !groupOnly
    const hasOn = !active && kids.some(c => isActive(c.key))
    const tip = !perm ? `${it.label}（没有权限，可在首页申请开通）` : !on(it.key) ? `${it.label}（${stat(it.key) || '未开放'}）` : undefined
    const click = () => {
      if (groupOnly) {
        if (!expanded && !canHover) { setTouchOpen(true); setExpand(x => ({ ...x, [it.key]: true })); return }
        toggleExp(it.key); return
      }
      if (!disabled) go(it.key)
    }
    return (<React.Fragment key={it.key}>
      <button type="button" className={'row' + (level ? ' kid' : '') + (active ? ' on' : '') + (hasOn ? ' has-on' : '') + (disabled && !groupOnly ? ' dis' : '')}
        data-nav-key={it.key} aria-current={active ? 'page' : undefined} aria-expanded={expandable ? isExp(it.key) : undefined}
        title={tip} onClick={click}>
        <span className="ic">{level ? <i className="dot" /> : iconOf(it.key)}{!level && dotOf(it, perm)}</span>
        <span className="lb fx">{it.label}</span>
        {tailOf(it, perm, disabled)}
        {expandable && <span className={'caret fx' + (isExp(it.key) ? '' : ' shut')} title={isExp(it.key) ? '收起子菜单' : '展开子菜单'}
          onClick={groupOnly ? undefined : e => { e.stopPropagation(); toggleExp(it.key) }}>{IC.chevDown}</span>}
      </button>
      {/* 子项收起态也占位（树线上的点）：鼠标停在哪一项，展开后还是那一项，不跳 */}
      {expandable && isExp(it.key) && <div className="kids">{kids.map(c => Row(c, 1))}</div>}
    </React.Fragment>)
  }

  const meName = user?.name || ''
  // 账号的岗位存的是岗位 key（改名不丢绑定），显示翻成中文名（同上面菜单岗位标的 postLabel）
  const mePost = (user?.post && (postLabel[user.post] || user.post)) || (user?.role === 'admin' ? '管理员' : '')

  return (
    <div className="kd-sbslot" style={{ width: pinned ? 240 : 72 }}>
      <aside ref={panelRef} className={'kd-sb' + (expanded ? ' open' : '') + (pinned ? ' pinned' : '')}
        onMouseEnter={() => canHover && setHover(true)}
        onMouseLeave={() => canHover && setHover(false)}
        onFocus={e => { if (e.target.matches && e.target.matches(':focus-visible')) setKbFocus(true) }}
        onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setKbFocus(false) }}>

        {/* 顶部：logo/标题回首页；logo 角上绿点＝数据源金蝶；标题下「数据源 · 金蝶 V2.xxx」常驻 */}
        <div className="brand">
          <button type="button" className={'logo' + (kd ? ' kd' : '')} title={'回到首页 · 数据源：' + (kd ? '金蝶' : '样例')} aria-label="回到首页" onClick={() => go('home')}>
            <span>{IC.bank}</span>
          </button>
          <div className="title fx" onClick={() => go('home')} title={verFull}>
            <b>财务核算工作台</b>
            <small><i className={kd ? 'kd' : ''} />数据源 · {kd ? '金蝶' : '样例'}{verLine && <em>{verLine}</em>}</small>
          </div>
          <button type="button" className="pin fx" aria-pressed={pinned} title={pinned ? '取消固定（悬停展开）' : '固定展开'}
            aria-label={pinned ? '取消固定' : '固定展开'} onClick={togglePin}>{pinned ? IC.pinOn : IC.pin}</button>
        </div>

        <nav>
          <button type="button" className={'row' + (view === 'home' ? ' on' : '')} aria-current={view === 'home' ? 'page' : undefined} onClick={() => go('home')}>
            <span className="ic">{IC.home}</span><span className="lb fx">首页</span>
          </button>
          {sections.map(s => {
            const items = itemsOf(s)
            if (!items.length) return null
            return (<div key={s.key}>
              <button type="button" className="section" aria-expanded={isOpen(s.key)}
                onClick={() => { if (!expanded && !canHover) { setTouchOpen(true); setOpen(o => ({ ...o, [s.key]: true })); return } toggleGroup(s.key) }}>
                <span className="lb fx">{s.label}</span>
                <span className={'caret fx' + (isOpen(s.key) ? '' : ' shut')}>{IC.chevDown}</span>
              </button>
              {isOpen(s.key) && items.map(it => Row(it, 0))}
            </div>)
          })}
        </nav>

        {/* 底部固定区：基础数据/系统设置 + 色调开关 */}
        <div className="foot">
          {bottomMods.map(m => Row(m, 0))}
          <button type="button" className="row" onClick={cycleTheme} title={'色调：' + themeMode.label + '（点击切换 跟随系统→浅色→深色）'}>
            <span className="ic" style={{ fontSize: 16 }}>{themeMode.icon}</span><span className="lb fx">色调 · {themeMode.label}</span>
          </button>
        </div>

        {/* 账号区：头像（姓名末字，收起态也认得出）+「姓名 · 岗位」（同字号同色，姓名永远完整、只省略岗位）+ 门户 / 退出 */}
        {user && <div className="acct" title={meName + (mePost ? '｜' + mePost : '')}>
          <span className="av"><span>{meName.slice(-1)}</span></span>
          <span className="who fx"><span className="n">{meName}</span>{mePost && <span className="s">· {mePost}</span>}</span>
          {onHome && <button type="button" className="exit fx" title="回到工作台门户" aria-label="回到门户" onClick={onHome}>{IC.portal}</button>}
          <button type="button" className="exit fx" title="退出登录" onClick={onLogout}>{IC.logout}退出</button>
        </div>}
      </aside>
    </div>
  )
}
