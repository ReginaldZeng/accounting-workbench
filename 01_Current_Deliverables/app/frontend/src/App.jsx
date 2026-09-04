import React, { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar.jsx'
import DataImport from './views/DataImport.jsx'
import FundDashboard from './views/FundDashboard.jsx'
import AccountLedger from './views/AccountLedger.jsx'
import WealthRecon from './views/WealthRecon.jsx'
import LogisticsAccrual from './views/LogisticsAccrual.jsx'
import LogisticsBasicData from './views/LogisticsBasicData.jsx'
import LogisticsBillUpload from './views/LogisticsBillUpload.jsx'
import LogisticsRecon from './views/LogisticsRecon.jsx'
import LogisticsCost from './views/LogisticsCost.jsx'
import PeriodClose from './views/PeriodClose.jsx'
import FundBoard from './views/FundBoard.jsx'
import CostLedger from './views/CostLedger.jsx'
import CostLedgerWh from './views/CostLedgerWh.jsx'
import CostLedgerDash from './views/CostLedgerDash.jsx'
import TempAttendance from './views/TempAttendance.jsx'
import BomPrice from './views/BomPrice.jsx'
import TempAttBoard from './views/TempAttBoard.jsx'
import Reconcile from './views/Reconcile.jsx'
import ResultExport from './views/ResultExport.jsx'
import Settings from './views/Settings.jsx'
import BasicData from './views/BasicData.jsx'
import Archive from './views/Archive.jsx'
import FxRate from './views/FxRate.jsx'
import RptExport from './views/RptExport.jsx'
import EcomSettle from './views/EcomSettle.jsx'
import EcomBasicData from './views/EcomBasicData.jsx'
import ReportDashboard from './views/ReportDashboard.jsx'
import Login from './views/Login.jsx'
import ForcePwd from './views/ForcePwd.jsx'
import Portal from './views/Portal.jsx'
import { getConfig, setConfig, getMe, apiLogout, getNavModules } from './api.js'

export default function App() {
  const [user, setUser] = useState(undefined)   // undefined=检查登录中 / null=未登录 / {..}=已登录
  const [zone, setZone] = useState('portal')     // portal=门户 / accounting=核算工作台 / bp / legal
  const [view, setView] = useState('reconcile')
  const [cfg, setCfg] = useState({ source: 'sample', year: 2026, period: 6 })
  const [mods, setMods] = useState(null)          // 导航模块上线开关（null=未加载，按全开渲染）
  const [navDef, setNavDef] = useState(null)      // 模块清单+分组（内置+自建），驱动侧栏渲染
  // 某个模块这个人能不能进＝【上线状态开着】且【有准入点】。准入点 cap 由后端算好放在模块上，
  // 前端别自己拼 "enter:"+key——拼错就是静默放行。
  const modOf = k => (navDef?.modules || []).find(m => m.key === k)
  const canView = k => {
    const key = { import: 'reconcile', fund: 'reconcile', result: 'reconcile' }[k] || k
    if (!mods || mods[key]?.['可进入'] === false) return false
    const m = modOf(key)
    if (!m) return false
    return !m.cap || user?.role === 'admin' || !!user?.perms?.[m.cap]
  }
  useEffect(() => { getMe().then(r => setUser(r.user)).catch(() => setUser(null)) }, [])
  useEffect(() => { if (user) getConfig().then(setCfg).catch(() => {}) }, [user])
  useEffect(() => { if (user) getNavModules().then(r => { setMods(r.state); setNavDef({ modules: r.modules, sections: r.sections, posts: r.posts }) }).catch(() => {}) }, [user])
  // 落地页：既要模块开着，**也要这个人进得去**（V2.52 准入点）。
  // 不看准入点的话，一个没有任何菜单权限的账号会直接落在「对账程序」上——侧栏空空如也，正文却把整页
  // 渲染给他看（实测抓到）。没有一个能进的 → 落到「无权限」占位，别白屏也别越权。
  useEffect(() => {
    if (!mods || !navDef) return
    const cur = { import: 'reconcile', fund: 'reconcile', result: 'reconcile' }[view] || view
    if (canView(cur)) return
    const first = (navDef.modules || []).find(m => !m.group_only && canView(m.key))
    setView(first ? first.key : '__noperm__')
  }, [mods, navDef])

  if (user === undefined) return <div className="loading" style={{ padding: 40 }}>加载中…</div>
  if (!user) return <Login onLogin={u => { setZone('portal'); setUser(u) }} />

  // V2.330 首登强制改密：新建/重置后的账号先改密才放进门户（服务端 _auth_gate 同步硬拦，此处是 UX）
  if (user.must_change_pwd) return <ForcePwd user={user}
    onDone={() => getMe().then(r => setUser(r.user)).catch(() => setUser(null))}
    onLogout={async () => { try { await apiLogout() } catch (e) {} setUser(null); setZone('portal') }} />

  // 登录后先落门户；选组进入某工作台
  if (zone === 'portal') return <Portal user={user} onEnter={setZone} />

  const changePeriod = async (year, period) => { const c = await setConfig({ year, period }); setCfg(prev => ({ ...prev, ...c })) }
  const refreshCfg = () => getConfig().then(setCfg).catch(() => {})   // 封存/解封后刷新全局封存态（侧栏徽标）
  const refreshMods = () => getNavModules().then(r => { setMods(r.state); setNavDef({ modules: r.modules, sections: r.sections, posts: r.posts }) }).catch(() => {})
  const modOn = k => !mods || mods[k]?.['可进入'] !== false
  const modSt = k => mods?.[k]?.status || ''
  const canSettings = user?.role === 'admin' || !!user?.perms?.enter_settings   // 系统设置：默认仅主管理员，可由主管理员授权
  const logout = async () => { try { await apiLogout() } catch (e) {} setUser(null); setZone('portal'); setView('reconcile') }
  const backToPortal = () => { setZone('portal'); setView('reconcile') }

  // 目前仅核算组工作台已建成；BP/法务进入先给建设中占位
  if (zone !== 'accounting') {
    const label = zone === 'bp' ? '财务分析组 · BP 工作台' : '法务部工作台'
    return (
      <div className="shell"><main className="main" style={{ padding: 40 }}>
        <div className="head"><div><div className="h-title">{label}</div>
          <div className="h-sub">该组工作台建设中。</div></div></div>
        <div className="body"><div className="loading">敬请期待 —— 建设中。
          <div style={{ marginTop: 18 }}><a onClick={backToPortal} style={{ color: 'var(--accent)', cursor: 'pointer' }}>← 返回工作台门户</a></div>
        </div></div>
      </main></div>
    )
  }

  return (
    <div className="shell">
      <Sidebar view={view} onSelect={setView} source={cfg.source} user={user} onLogout={logout} onHome={backToPortal}
        closed={!!cfg['封存']?.['已封存']} mods={mods} navDef={navDef} ver={cfg['版本']} />
      <main className="main">
        {/* 模块未开放时，正停在该页的人不该继续看到旧内容（四部曲三个子视图都算「银行对账」这个模块） */}
        {(() => {
          const k = { import: 'reconcile', fund: 'reconcile', result: 'reconcile' }[view] || view
          return !modOn(k) && <Placeholder title={`${modSt(k) || '该模块尚未开放'}`}
            hint="本模块当前不可进入。状态由主管理员在「系统设置 › 导航模块上线管理」维护。" />
        })()}
        {/* 一个菜单都进不去（准入点全没给）→ 明确告诉他找谁，别甩个空白工作台 */}
        {view === '__noperm__' && <Placeholder title="还没有分配任何菜单权限"
          hint="你的账号能进核算工作台，但还没开通任何菜单。请联系主管理员在「账号管理」里按你的岗位一键套用权限。"
          body="这不是功能没做，是权限还没开——找主管理员即可。" />}
        {/* 模块开着、但这个人没这个菜单的准入点 → 说清是权限问题，别跟「未上线」混为一谈，也别白屏 */}
        {(() => {
          const k = { import: 'reconcile', fund: 'reconcile', result: 'reconcile' }[view] || view
          return view !== '__noperm__' && modOn(k) && !canView(k) && modOf(k) && <Placeholder title="没有这个菜单的权限"
            hint="该模块已上线，但你的账号没有它的准入权限。请联系主管理员在「账号管理」里开通。"
            body="这不是功能没做，是权限还没开——找主管理员即可。" />
        })()}
        {view === 'import' && canView('reconcile') && <DataImport cfg={cfg} onChange={setCfg} onPeriod={changePeriod} onNav={setView} user={user} />}
        {view === 'fund' && canView('reconcile') && <FundDashboard cfg={cfg} onPeriod={changePeriod} onNav={setView} user={user} />}
        {view === 'fundboard' && canView('fundboard') && <FundBoard cfg={cfg} onPeriod={changePeriod} onNav={setView} />}
        {view === 'reconcile' && canView('reconcile') && <Reconcile cfg={cfg} onPeriod={changePeriod} onNav={setView} user={user} />}
        {view === 'result' && canView('reconcile') && <ResultExport cfg={cfg} onNav={setView} />}
        {view === 'ledger' && canView('ledger') && <AccountLedger cfg={cfg} onPeriod={changePeriod} />}
        {/* V2.240 报表板块重排：「科目余额」菜单被撤下（业务方定），此处路由随之摘掉。
            views/SubjectBalance.jsx 与后端 /api/subject-balance* 原样留着、只是不可达——
            页面代码没删、接口没删（治理红线：删文件/删既有 API 须先出影响分析）。
            报表仪表盘(rptdash) 建好后要接回来的话，把这行改成 view === 'rptdash' 即可。 */}
        {view === 'rptdash' && canView('rptdash') && <ReportDashboard />}
        {view === 'wealth' && canView('wealth') && <WealthRecon cfg={cfg} onPeriod={changePeriod} />}
        {view === 'fxrate' && canView('fxrate') && <FxRate user={user} />}
        {view === 'periodclose' && canView('periodclose') && <PeriodClose cfg={cfg} onPeriod={changePeriod} user={user} onChanged={refreshCfg} />}
        {view === 'logistics' && canView('logistics') && <LogisticsAccrual user={user} />}
        {view === 'logibase' && canView('logibase') && <LogisticsBasicData user={user} />}
        {view === 'logiupload' && canView('logiupload') && <LogisticsBillUpload user={user} />}
        {/* V2.52：原「月结核对」(clrecon) 这一层取消，页面内容上提——点「成本台账」直接进七步工作流页。
            V2.254：成本台账→**存货台账**，二级改回纯分组，八步工作流页下沉成三级「台账导出」(clexport)。
            页面代码一行没动，还是同一个 CostLedger.jsx；变的只是它挂在树上的位置。
            `costledger` 保留为分组 key（权限/审计历史都绑在它上面），但**不再对应任何页面**。 */}
        {view === 'clexport' && canView('clexport') && <CostLedger user={user} />}
        {view === 'cldash' && canView('cldash') && <CostLedgerDash user={user} />}
        {view === 'clwh' && canView('clwh') && <CostLedgerWh user={user} />}
        {view === 'rptexport' && canView('rptexport') && <RptExport user={user} />}
        {view === 'tempattrev' && canView('tempattrev') && <TempAttendance user={user} />}
        {view === 'bomdraft' && canView('bomdraft') && <BomPrice user={user} mode="draft" />}
        {view === 'bomstd' && canView('bomstd') && <BomPrice user={user} mode="std" />}
        {view === 'bomconfig' && canView('bomconfig') && <BomPrice user={user} mode="config" />}
        {view === 'tempattboard' && canView('tempattboard') && <TempAttBoard user={user} />}
        {view === 'archive' && canView('archive') && <Archive user={user} />}
        {view === 'basicdata' && canView('basicdata') && <BasicData user={user} />}
        {view === 'settings' && (canSettings
          ? <Settings cfg={cfg} onChange={setCfg} onModsChanged={refreshMods} />
          : <Placeholder title="系统设置" hint="仅主管理员可进入。如需授权，请主管理员在「账号管理」勾选「进入系统设置」权限点。" />)}
        {/* 开着但还没开发的模块 → 规划中占位页 */}
        {view === 'ecomsettle' && canView('ecomsettle') && <EcomSettle user={user} />}
        {view === 'ecombase' && canView('ecombase') && <EcomBasicData user={user} />}
        {view === 'logisticspay' && canView('logisticspay') && <LogisticsRecon user={user} cfg={cfg} onPeriod={changePeriod} />}
        {view === 'logisticscost' && canView('logisticscost') && <LogisticsCost user={user} />}
        {/* 自建但还没接代码的模块（key 不在已编码集合里）：即便被设为可进入，也给规划中占位而非白屏 */}
        {!CODED_VIEWS.has({ import: 'reconcile', fund: 'reconcile', result: 'reconcile' }[view] || view) && canView(view) &&
          <Placeholder title={(navDef?.modules || []).find(m => m.key === view)?.label || view}
            hint="这个模块还没接上功能——是在「系统设置 › 导航模块上线管理」自建的规划占位。开发接上同名标识后即可使用。" />}
      </main>
    </div>
  )
}
// 已接代码的视图集合——不在此集合、又被设为可进入的模块（内置规划占位 or 自建占位）走「规划中」占位，不白屏。
// 内置占位（报表仪表盘/源单导出/BOM报价审核/生产简报复核/临时工考勤/收入台账/客户对账/电商推广）
// 默认状态是「敬请期待」＝不可进入，正常走上面的「尚未开放」占位，不会落到这里。
// 三条线合并（报表板块 V2.240-252 × 存货台账 V2.253-259 × 电商对账 V2.260-276）：
// 这个集合是唯一的三方交汇点。合并口径＝**各线的增删各自生效，谁也不覆盖谁**：
//   报表线：撤 'sbal'（科目余额菜单被撤下）、加 'rptexport' 与 'rptdash'（报表仪表盘 V2.248 入库）
//   存货线：'costledger' 换成三个三级 'clexport'/'cldash'/'clwh'
//   电商线：'ecom' 升级为纯分组父项、不再是视图，换成 'ecomsettle'/'ecombase'
//   临工线：'tempatt' 同样升为纯分组父项，换成 'tempattrev'/'tempattboard'（V2.318）
// 三个被摘掉的 key（sbal / costledger / ecom）都已不对应任何页面——留着的话，
// 万一有人把它配成可进入，会去找一个不存在的视图而不是走占位页。
const CODED_VIEWS = new Set(['reconcile', 'ledger', 'wealth', 'fxrate', 'periodclose', 'fundboard',
  'rptexport', 'rptdash',
  'logistics', 'logibase', 'logiupload', 'logisticspay', 'logisticscost',
  'clexport', 'cldash', 'clwh', 'bomdraft', 'bomstd', 'bomconfig',
  'ecomsettle', 'ecombase',
  'tempattrev', 'tempattboard',
  'archive', 'basicdata', 'settings'])
// body 默认是「二期开发」——但权限类占位不能这么说，那会让人以为是功能没做，跑去催开发而不是找管理员开权限
function Placeholder({ title, hint, body = '敬请期待 —— 二期开发。' }) {
  return <div><div className="head"><div><div className="h-title">{title}</div>
    <div className="h-sub">{hint}</div></div></div>
    <div className="body"><div className="loading">{body}</div></div></div>
}
