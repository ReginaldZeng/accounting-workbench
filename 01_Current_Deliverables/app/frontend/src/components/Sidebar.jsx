// [Change Log] Date:2026-07-12 Author:Claude/c Version:V2.105  侧栏按设计稿「导航栏想法」重做
// 展开态＝白色悬浮卡片（分组胶囊带强调色条 + 二级导引条 + 选中态强调条 + 徽章/岗位标）；
// 收起态＝76px 图标轨（状态圆点 + 悬停飞出子菜单）。数据仍由后端 navDef 驱动（单一真相源）。
import React, { useState, useEffect, useRef } from 'react'

const S = (d, sw = 1.75) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">{d}</svg>
const IC = {
  bank: S(<><path d="M3 10l9-6 9 6" /><path d="M4 10v9M20 10v9M8 10v9M16 10v9M12 10v9M3 21h18" /></>),
  back: S(<path d="M15 18l-6-6 6-6" />),
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
  basicdata: S(<><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>),
  settings: S(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8 2 2 0 1 1-2.8 2.8 1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 0 1-4 0 1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3 2 2 0 1 1-2.8-2.8 1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4 1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8 2 2 0 1 1 2.8-2.8 1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0 1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3 2 2 0 1 1 2.8 2.8 1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></>),
}
const ICON_BY_KEY = {
  periodclose: IC.month, bankrecon: IC.bank, reconcile: IC.reconcile, fundboard: IC.fund, ledger: IC.ledger,
  wealth: IC.wealth, logisticsrecon: IC.logistics, logistics: IC.logistics, logisticspay: IC.reconcile, logisticscost: IC.sbal,
  ecom: IC.ecom, costledger: IC.cost, clwh: IC.basicdata, archive: IC.archive,
  // 报表板块（V2.240）：sbal/journal 两个 key 已退出菜单树，图标随之撤走
  fiacc: IC.sbal, rptdash: IC.fund, rptexport: IC.dl, srcbill: IC.archive, srcexport: IC.dl, fxrate: IC.wealth,
  bomprice: IC.cost, prodbrief: IC.month, revledger: IC.ledger, custrecon: IC.reconcile, ecompromo: IC.ecom,
  // 临工线（V2.318）：tempatt 是纯分组父项，两个三级各给一个图标
  tempatt: IC.user, tempattrev: IC.reconcile, tempattboard: IC.fund,
  basicdata: IC.basicdata, settings: IC.settings,
}
const RECON_VIEWS = ['import', 'reconcile', 'fund', 'result']
const VIEWS_BY_KEY = { reconcile: RECON_VIEWS }
// 设计令牌（用应用 CSS 变量，深色自动适配）
const AMBER = 'var(--amber)', AMBER_BG = 'var(--amber-bg)'
const TEAL = 'var(--teal)', TEAL_BG = 'var(--teal-bg)'       // 「人工并行」标签色
const VIOLET = 'var(--purple)', VIOLET_BG = 'var(--purple-bg)'   // 「测试验证」标签色（V2.174 起可进入，须有标记）
const pillC = bg => (bg.teal ? TEAL : bg.violet ? VIOLET : AMBER)
const pillBG = bg => (bg.teal ? TEAL_BG : bg.violet ? VIOLET_BG : AMBER_BG)
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

export default function Sidebar({ view, onSelect, source, user, onLogout, onHome, closed, mods, navDef, ver }) {
  const kd = source === 'kingdee'
  // 主题档位；auto 档要监听系统切换（白天↔夜间自动跟）
  const [theme, setTheme] = React.useState(() => localStorage.getItem('fw_theme') || 'auto')
  React.useEffect(() => {
    applyTheme(theme)
    localStorage.setItem('fw_theme', theme)
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
  // 本实例身份行（V2.176，V2.187 收敛）：正常情况（主干+默认端口）只显版本号——
  // 分支名/端口对业务方是噪音；只在「不寻常」时才亮出来（开发分支实例、非默认端口），
  // 那正是需要分清「在看哪条线」的时候（实战教训：盯着 main 找别的分支的功能）。
  // 完整明细（分支/提交/端口/有无未提交改动）始终在悬停提示里。
  const port = window.location.port || (window.location.protocol === 'https:' ? '443' : '80')
  const branchShort = ver && ver.branch ? ver.branch.replace(/^claude\//, '') : ''
  const oddBranch = branchShort && branchShort !== 'main'         // 非主干才值得亮分支
  const oddPort = !['8000', '80', '443'].includes(port)           // 非默认端口才值得亮端口
  const verLine = ver ? [ver.ver || '', oddBranch ? branchShort : '', oddPort ? ':' + port : '']
    .filter(Boolean).join(' · ') : ''
  const verFull = ver ? `版本 ${ver.ver || '未知'}${ver.dirty ? '（有未提交改动）' : ''} · 分支 ${ver.branch || '—'} · 提交 ${ver.commit || '—'} · 端口 ${port}` : ''
  const on = k => !mods || mods[k]?.['可进入'] !== false
  const stat = k => mods?.[k]?.status || ''
  const allMods = navDef?.modules || []
  // 岗位标签：后端存的是 key（改名不丢绑定），显示要翻成中文名
  const postLabel = {}; (navDef?.posts || []).forEach(p => { postLabel[p.key] = p.label })
  const posts = k => (mods?.[k]?.posts || []).map(p => postLabel[p] || p)
  // 每个菜单的准入点 cap 由后端算好（_enter_cap）：无 cap＝纯分组父项(可见性看子项)；
  // 有 cap 则主管理员或已授权者才见。**别在前端拼 "enter:"+key**——拼错就是静默放行。
  // V2.142 组内第二道门 act：第三种节点的子项（如 成本台账›仓库类型）与父项共用一个准入闸，
  // 进组后见不见还要看 act（动作点，如「维护仓库类型」）——两道都过才显示。
  const hasCap = c => user?.role === 'admin' || !!user?.perms?.[c]
  const canEnter = m => (!m.cap || hasCap(m.cap)) && (!m.act || hasCap(m.act))
  const subOf = {}; allMods.forEach(m => { if (m.parent) subOf[m.key] = m.parent })
  const iconOf = k => ICON_BY_KEY[k] || IC.soon
  const viewsOf = k => VIEWS_BY_KEY[k] || [k]
  const isActive = k => viewsOf(k).includes(view)
  const childrenOf = k => allMods.filter(m => subOf[m.key] === k && canEnter(m) && stat(m.key) !== '隐藏')
  // 纯分组父项（group_only）本身没页面、没准入点：子项全看不见时它就该整个消失，不留空壳
  const canSee = m => m.group_only ? childrenOf(m.key).length > 0 : canEnter(m)
  const allSecs = navDef?.sections || []
  const sections = allSecs.filter(s => !s.bottom)
  const bottomKeys = new Set(allSecs.filter(s => s.bottom).map(s => s.key))
  const bottomMods = allMods.filter(m => bottomKeys.has(m.sec) && canSee(m) && stat(m.key) !== '隐藏')
  const itemsOf = s => allMods.filter(m => m.sec === s.key && !subOf[m.key] && canSee(m) && stat(m.key) !== '隐藏')
  const badgeOf = it => {
    if (it.key === 'periodclose' && closed) return { t: '已封存', pill: true }
    if (!on(it.key)) return { t: stat(it.key) || '未开放', pill: false }   // 未开放：灰字
    const s = stat(it.key)
    if (s === '待验收') return { t: '待验收', pill: true }
    if (s === '人工并行') return { t: '人工并行', pill: true, teal: true }
    if (s === '测试验证') return { t: '测试验证', pill: true, violet: true }  // V2.174 起可进入，配紫标
    // 「开发中」只有 conf.ini [nav] dev_users 名单里的人进得来（别人在上面那行就被判成不可进入、灰字）。
    // 必须给个显眼标记：否则进得去的人看到的是个"跟正常模块一样"的入口，会忘了同事其实看不见它，
    // 进而在会上拿它当已上线的东西讲。琥珀＝在建（V2.242）
    if (s === '开发中') return { t: '开发中·仅你可见', pill: true }
    if (s === '引擎正常') return { t: '引擎正常', live: true }             // 呼吸绿灯（V2.173）
    return null
  }

  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('fw_nav_collapsed') === '1' } catch (e) { return false } })
  const [open, setOpen] = useState(() => { try { return JSON.parse(localStorage.getItem('fw_nav_groups') || '{}') } catch (e) { return {} } })
  const [expand, setExpand] = useState(() => { try { return JSON.parse(localStorage.getItem('fw_nav_expand') || '{}') } catch (e) { return {} } })
  const [hovered, setHovered] = useState(null)
  const isOpen = g => open[g] !== false
  const isExp = k => expand[k] !== false
  const save = (key, v) => { try { localStorage.setItem(key, JSON.stringify(v)) } catch (e) {} }
  const toggleGroup = g => setOpen(o => { const n = { ...o, [g]: !(o[g] !== false) }; save('fw_nav_groups', n); return n })
  const toggleExp = k => setExpand(x => { const n = { ...x, [k]: !(x[k] !== false) }; save('fw_nav_expand', n); return n })
  const setColl = v => { setCollapsed(v); try { localStorage.setItem('fw_nav_collapsed', v ? '1' : '0') } catch (e) {}; if (v) setHovered(null) }
  // 当前所在板块自动展开
  useEffect(() => {
    const s = sections.find(sec => itemsOf(sec).some(it => isActive(it.key) || childrenOf(it.key).some(c => isActive(c.key))))
    if (s && !isOpen(s.key)) setOpen(o => ({ ...o, [s.key]: true }))
  }, [view, navDef])

  // —— 展开态：一个导航行 ——
  // 三种节点（V2.52）：
  //   ① 叶子             → 整行点＝进页面
  //   ② 纯分组父项 group_only → 整行点＝展开/收起（它没页面）
  //   ③ 可进入且有子项（成本台账）→ 点文字/图标＝进页面，点右侧箭头＝展开（箭头要 stopPropagation）
  const Row = (it, level, inFlyout) => {
    const kids = childrenOf(it.key)
    const disabled = !on(it.key)
    const groupOnly = !!it.group_only
    const expandable = kids.length > 0 && !inFlyout
    const enterable = !groupOnly
    const active = isActive(it.key) && enterable
    const bg = badgeOf(it)
    const ps = disabled ? [] : posts(it.key)
    const h = level ? 36 : 40
    const base = {
      position: 'relative', display: 'flex', alignItems: 'center', gap: 11, height: h, padding: '0 12px',
      borderRadius: 10, fontSize: level ? 13 : 14, cursor: disabled ? 'default' : 'pointer', userSelect: 'none',
      color: disabled ? 'var(--ink-3)' : (active ? 'var(--accent)' : 'var(--ink)'),
      fontWeight: active ? 600 : 400, background: active ? 'var(--accent-soft)' : undefined,
    }
    const onClick = disabled ? undefined
      : () => { if (enterable) onSelect(it.key); else if (expandable) toggleExp(it.key) }
    return (<React.Fragment key={it.key}>
      <div className={'navrow' + (disabled ? ' navrow-dis' : '')} style={base} onClick={onClick} title={disabled ? `该模块：${stat(it.key)}` : undefined}>
        {active && <span style={{ position: 'absolute', left: level ? -13 : -4, top: 9, bottom: 9, width: 3, borderRadius: level ? '3px' : '0 3px 3px 0', background: 'var(--accent)' }} />}
        <span style={{ flex: '0 0 auto', width: level ? 17 : 18, height: level ? 17 : 18, display: 'inline-flex', color: 'inherit' }}>{iconOf(it.key)}</span>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label}</span>
        {ps.map(p => <span key={p} style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--accent)', background: 'var(--accent-soft)', padding: '2px 6px', borderRadius: 5, whiteSpace: 'nowrap' }}>{p}</span>)}
        {bg && (bg.live
          ? <span className="nav-live-dot" title="引擎正常" />
          : bg.pill
            ? <span title={bg.t} style={{ fontSize: 10.5, fontWeight: 600, color: pillC(bg), background: pillBG(bg), padding: '2px 6px', borderRadius: 5, whiteSpace: 'nowrap' }}>{PILL_SHORT[bg.t] || bg.t}</span>
            : <span title={bg.t} style={{ fontSize: 10.5, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{bg.t}</span>)}
        {expandable && <span
          onClick={enterable && !disabled ? (e => { e.stopPropagation(); toggleExp(it.key) }) : undefined}
          title={enterable ? (isExp(it.key) ? '收起子菜单' : '展开子菜单') : undefined}
          style={{
            flex: '0 0 auto', width: 15, height: 15, color: 'var(--ink-3)', borderRadius: 4,
            cursor: enterable && !disabled ? 'pointer' : 'inherit',
            transform: isExp(it.key) ? 'none' : 'rotate(-90deg)', transition: 'transform .18s',
          }}>{IC.chevDown}</span>}
      </div>
      {expandable && isExp(it.key) &&
        <div style={{ margin: '3px 0 3px 24px', paddingLeft: 13, borderLeft: '1px solid var(--line)' }}>
          {kids.map(c => Row(c, 1))}
        </div>}
    </React.Fragment>)
  }

  // —— 图标轨：一个图标按钮（含飞出） ——
  const Rail = (it, groupStart) => {
    const kids = childrenOf(it.key)
    const disabled = !on(it.key)
    const active = (isActive(it.key) || kids.some(c => isActive(c.key)))
    const bg = badgeOf(it)
    const dot = bg ? (bg.live ? 'var(--green)' : bg.pill ? pillC(bg) : 'var(--ink-3)') : null
    return (<div key={it.key} style={{ position: 'relative' }}
      onMouseEnter={() => setHovered(it.key)} onMouseLeave={() => setHovered(h => h === it.key ? null : h)}>
      {groupStart && <div style={{ width: 34, height: 1, background: 'var(--line)', margin: '7px auto' }} />}
      <div style={{
        width: 44, height: 44, margin: '2px auto', borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: disabled ? 'default' : 'pointer', color: disabled ? 'var(--ink-3)' : (active ? 'var(--accent)' : 'var(--ink-2)'),
        background: active ? 'var(--accent-soft)' : undefined, position: 'relative',
      }} className={'railbtn' + (disabled ? ' navrow-dis' : '')}
        onClick={disabled || it.group_only ? undefined : () => onSelect(it.key)}>
        <span style={{ width: 20, height: 20, display: 'inline-flex' }}>{iconOf(it.key)}</span>
        {dot && <span className={bg.live ? 'nav-pulse' : undefined} style={{ position: 'absolute', top: 7, right: 7, width: 7, height: 7, borderRadius: '50%', background: dot, border: '1.6px solid var(--bg)' }} />}
      </div>
      {hovered === it.key && <div style={{
        position: 'absolute', left: '100%', top: 0, marginLeft: 12, zIndex: 60, minWidth: 190,
        background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 13, boxShadow: '0 16px 44px rgba(28,32,58,.18)', padding: 7,
      }}>
        {/* 飞出菜单的标题：可进入的父项（如成本台账）点标题就进页面，别让收起态成为进不去的死角 */}
        <div className={!it.group_only && !disabled ? 'navrow' : undefined}
          onClick={!it.group_only && !disabled ? () => onSelect(it.key) : undefined}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '5px 9px', borderRadius: 8,
            fontSize: 13.5, fontWeight: 700, color: 'var(--ink)',
            cursor: !it.group_only && !disabled ? 'pointer' : 'default',
          }}>
          {it.label}
          {posts(it.key).map(p => <span key={p} style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent)', background: 'var(--accent-soft)', padding: '2px 7px', borderRadius: 6 }}>{p}</span>)}
          {bg && (bg.live
            ? <span className="nav-live-dot" style={{ marginLeft: 'auto' }} title="引擎正常" />
            : <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 600, color: bg.pill ? pillC(bg) : 'var(--ink-3)', background: bg.pill ? pillBG(bg) : 'transparent', padding: bg.pill ? '2px 7px' : 0, borderRadius: 6 }}>{bg.t}</span>)}
        </div>
        {kids.length > 0 && <>
          <div style={{ height: 1, background: 'var(--line)', margin: '4px 2px' }} />
          {kids.map(c => Row(c, 1, true))}
        </>}
      </div>}
    </div>)
  }

  // ============ 收起：图标轨 ============
  if (collapsed) {
    const flat = []
    sections.forEach((s, si) => itemsOf(s).forEach((it, ii) => flat.push({ it, groupStart: si > 0 && ii === 0 })))
    return (<aside className="aside" style={{ width: 72 }}>
      <div style={{ padding: '14px 0 6px', textAlign: 'center' }}>
        <div onClick={onHome} title={onHome ? '返回门户' : undefined} style={{ width: 42, height: 42, margin: '0 auto', borderRadius: 13, background: 'var(--accent)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 7px 18px rgba(75,83,196,.30)', cursor: onHome ? 'pointer' : 'default' }}>
          <span style={{ width: 22, height: 22, display: 'inline-flex' }}>{IC.bank}</span>
        </div>
        <div onClick={() => setColl(false)} title="展开" style={{ width: 34, height: 30, margin: '6px auto 0', borderRadius: 8, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }} className="railbtn">
          <span style={{ width: 18, height: 18, display: 'inline-flex' }}>{IC.expand}</span>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'visible', padding: '2px 0' }}>
        {flat.map(({ it, groupStart }) => Rail(it, groupStart))}
      </div>
      {/* 基础数据/基础设置：钉在底部（滚动区之外），参照 BP 工作台 */}
      <div style={{ padding: '2px 0 4px', overflow: 'visible' }}>
        <div style={{ width: 34, height: 1, background: 'var(--line)', margin: '4px auto 6px' }} />
        {bottomMods.map(m => Rail(m, false))}
      </div>
      <div style={{ borderTop: '1px solid var(--line)', padding: '10px 0', textAlign: 'center' }}>
        <div onClick={onLogout} title={(user ? user.name : '') + ' · 退出'} style={{ width: 34, height: 34, margin: '0 auto', borderRadius: '50%', background: 'var(--bg-rail)', color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
          <span style={{ width: 17, height: 17, display: 'inline-flex' }}>{IC.user}</span>
        </div>
        <span style={{ display: 'inline-block', marginTop: 6, width: 7, height: 7, borderRadius: '50%', background: kd ? 'var(--green)' : 'var(--ink-3)', boxShadow: kd ? '0 0 0 3px rgba(31,122,85,.14)' : 'none' }} title={'数据源 · ' + (kd ? '金蝶' : '样例')} />
      </div>
    </aside>)
  }

  // ============ 展开：卡片 ============
  return (<aside className="aside" style={{ width: 264 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '18px 14px 12px' }}>
      <div onClick={onHome} title={onHome ? '返回门户' : undefined} style={{ width: 42, height: 42, borderRadius: 13, background: 'var(--accent)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 7px 18px rgba(75,83,196,.30)', flex: '0 0 auto', cursor: onHome ? 'pointer' : 'default' }}>
        <span style={{ width: 22, height: 22, display: 'inline-flex' }}>{IC.bank}</span>
      </div>
      <div style={{ lineHeight: 1.25, flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--ink)', letterSpacing: '.01em' }}>财务核算工作台</div>
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>核算组 · 稽核提效</div>
      </div>
      <div onClick={() => setColl(true)} title="收起" className="railbtn" style={{ width: 30, height: 30, borderRadius: 9, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flex: '0 0 auto' }}>
        <span style={{ width: 18, height: 18, display: 'inline-flex' }}>{IC.collapse}</span>
      </div>
    </div>

    <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 6px' }}>
      {onHome && <div className="navrow" onClick={onHome} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 38, borderRadius: 10, color: 'var(--ink-2)', fontSize: 13.5, cursor: 'pointer', padding: '0 6px' }}>
        <span style={{ width: 18, height: 18, display: 'inline-flex' }}>{IC.back}</span> 返回门户
      </div>}

      {sections.map(s => {
        const items = itemsOf(s)
        if (!items.length) return null      // 该板块下一个都看不见 → 整个板块不显示（别留空标题）
        return (<div key={s.key}>
          <div className="grphdr" onClick={() => toggleGroup(s.key)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 40, padding: '0 13px', margin: '10px 0 4px', borderRadius: 10, cursor: 'pointer', background: 'var(--bg-rail)', border: '1px solid var(--line)' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 13, fontWeight: 700, letterSpacing: '.02em', color: 'var(--ink)' }}>
              <span style={{ width: 3, height: 14, borderRadius: 2, background: 'var(--accent)', flex: '0 0 auto' }} />{s.label}
            </span>
            <span style={{ width: 16, height: 16, color: 'var(--ink-3)', transform: isOpen(s.key) ? 'none' : 'rotate(-90deg)', transition: 'transform .18s' }}>{IC.chevDown}</span>
          </div>
          {isOpen(s.key) && <div style={{ borderLeft: '2px solid var(--accent)', marginLeft: 5, paddingLeft: 9, display: 'flex', flexDirection: 'column', gap: 2 }}>
            {items.map(it => Row(it, 0))}
          </div>}
        </div>)
      })}
    </div>

    {/* 基础数据/基础设置：钉在底部（滚动区之外，紧贴页脚上方），参照 BP 工作台 */}
    <div style={{ padding: '4px 12px 6px' }}>
      <div style={{ height: 1, background: 'var(--line)', margin: '0 4px 6px' }} />
      {bottomMods.map(m => Row(m, 0))}
    </div>

    <div style={{ borderTop: '1px solid var(--line)', padding: '14px 16px 16px' }}>
      {user && <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ width: 17, height: 17, color: 'var(--accent)', display: 'inline-flex' }}>{IC.user}</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={user.name + (user.role === 'admin' ? ' · 管理员' : '')}>{user.name}{user.role === 'admin' ? ' · 管理员' : ''}</span>
        <span onClick={onLogout} style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 600, color: 'var(--accent)', cursor: 'pointer' }}>退出</span>
      </div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, border: '1px solid var(--line)', background: 'var(--bg-sub)', borderRadius: 999, padding: '5px 11px', fontSize: 12, color: 'var(--ink-2)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: kd ? 'var(--green)' : 'var(--ink-3)', boxShadow: kd ? '0 0 0 3px rgba(31,122,85,.14)' : 'none' }} />
          数据源 · {kd ? '金蝶' : '样例'}
        </span>
        {/* 双色调开关：点击轮换 跟随系统→浅色→深色（系统黑的同事可强制浅色） */}
        <span onClick={cycleTheme} title={'色调：' + themeMode.label + '（点击切换）'}
          style={{ width: 26, height: 26, borderRadius: '50%', border: '1px solid var(--line)', background: 'var(--bg-sub)', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', fontSize: 13, flexShrink: 0, userSelect: 'none' }}>
          {themeMode.icon}
        </span>
        {/* 版本号与数据源同行右侧（业务方指定位置），不再单占一行 */}
        {verLine && <span title={verFull} style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'default' }}>
          {verLine}
        </span>}
      </div>
    </div>
  </aside>)
}
