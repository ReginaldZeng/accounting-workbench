// [Change Log] Date:2026-09-06 Author:Claude/Reginald Zeng Version:V2.500
// 核算工作台首页（轻量落地页，参照 BP 工作台 Home）。进核算工作台先落这里，别一进来就落在「对账程序」
// 那种会取数的重页上。**本页刻意不发任何业务请求**：问候/期间来自已在内存的全局态（cfg），板块卡片来自
// 侧栏同一份 navDef+mods（App 早已拉好），点开具体板块时才真正取数。
// 卡片三态（与侧栏口径一致）：① 可用=正常可点；② 未上线=灰显+状态字（模块开关没开）；
//   ③ 🔒无权限=灰显+锁（模块开着但这个账号没准入点）。全部展示、不过滤——让人知道有这么个板块可去申请。
// V2.500（业务方：首页只留真的工具）：首页是「工具」落地页，不摆配置页。基础数据/基础资料/基础设置/系统设置
//   这类维表·配置叶子从卡片里剔掉（侧栏仍可达）；一览计数同口径，不含配置页。
import React from 'react'

// 配置/维表叶子（非「工具」）：按 key 认（改名也挡得住）＋按标签兜底（将来新增的同类也挡得住）。
const _CFG_KEYS = new Set(['basicdata', 'settings', 'logibase', 'clwh', 'bomconfig', 'ecombase'])
const isConfigLeaf = m => _CFG_KEYS.has(m.key) || /^(基础(数据|资料|设置)|系统设置)$/.test(m.label || '')

// 一句话板块说明（财务白话，只给主力叶子；缺省回退空）
const HINT = {
  reconcile: '银行流水 vs 金蝶 · 逐笔稽核认领',
  fxrate: '人行中间价 → 建汇率规则 → 写金蝶',
  wealth: '理财对账单 OCR → 对金蝶交易性金融资产',
  fundboard: '各账户余额 · 资金分布看板',
  ledger: '账户台账 · 期初期末与发生额',
  periodclose: '月结看板 · 封存 / 解封本期',
  rptdash: '报表仪表盘 · 三视角下钻反查凭证',
  rptexport: '一键导出报表 · 内网取件通道',
  srcexport: '源单明细导出',
  logibase: '物流共用维表 · 供应商 / 费用归属 / 税率',
  logiupload: '物流部：传账单 / 长表 → 质检 → 提交',
  logistics: '物流计提 · 复核 / 去向费率 / 录金蝶',
  logisticspay: '账单核对',
  logisticscost: '单据运费',
  clexport: '存货台账 · 八步工作流导出',
  cldash: '存货看板',
  clwh: '基础资料 · 仓库类型 / 类别科目对照',
  bomdraft: 'BOM 报价 · 钉钉抓取 / 入账 / 复核 / 定稿',
  bomstd: '标准成本台账 · 已定稿公开可查',
  bomconfig: 'BOM 基础设置',
  prodbrief: '生产简报复核',
  tempattrev: '临时工考勤 · 上报工时 vs 打卡逐日重算',
  tempattboard: '临时工看板 · 全年用工结构',
  revledger: '收入台账',
  custrecon: '客户对账',
  ecompromo: '电商推广',
  ecomsettle: '电商 · 收款核销',
  ecombase: '电商 · 基础资料',
  archive: '凭证归档 · 标签打印',
  basicdata: '主体档案 / 数据源 / 金蝶连接',
  settings: '导航模块上线 · 数据源 · 期间 · 金蝶 · 日志中心',
}

function greeting() {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

const CSS = `
.hm{padding:20px 20px 44px}
.hm-hero{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px 24px;
  padding:22px 26px;border-radius:16px;border:1px solid var(--line);
  background:linear-gradient(120deg,var(--accent-soft) 0%,var(--bg-sub) 60%,var(--bg-sub) 100%)}
.hm-hi{font-size:26px;font-weight:800;letter-spacing:.4px;color:var(--ink);margin:0}
.hm-hi em{font-style:normal;color:var(--accent)}
.hm-sub{font-size:12.5px;color:var(--ink-2);margin:7px 0 0}
.hm-chips{display:flex;flex-wrap:wrap;gap:8px}
.hm-chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:5px 12px;border-radius:999px;
  border:1px solid var(--line-strong);background:var(--bg);color:var(--ink-2);white-space:nowrap}
.hm-chip .dot{width:7px;height:7px;border-radius:50%;background:var(--ink-3)}
.hm-chip.ok{color:var(--green);border-color:var(--green-line);background:var(--green-bg)}
.hm-chip.ok .dot{background:var(--green)}
.hm-chip.warn{color:var(--amber);border-color:var(--amber-line);background:var(--amber-bg)}
.hm-chip.warn .dot{background:var(--amber)}
.hm-glance{display:flex;flex-wrap:wrap;gap:18px;margin:20px 2px 4px;font-size:12px;color:var(--ink-2)}
.hm-nudge{display:flex;align-items:center;gap:10px;margin-top:14px;padding:11px 15px;border-radius:12px;cursor:pointer;
  background:var(--accent-soft);border:1px solid var(--accent-soft);font-size:13px;color:var(--ink);transition:filter .15s}
.hm-nudge:hover{filter:brightness(.98)}
.hm-nudge b{color:var(--accent);font-weight:700}
.hm-nudge .go{margin-left:auto;color:var(--accent);font-weight:600;white-space:nowrap}
.hm-glance b{color:var(--ink);font-weight:700}
.hm-sec{margin-top:22px}
.hm-sec-h{display:flex;align-items:center;gap:9px;margin:0 0 11px;font-size:13.5px;font-weight:800;letter-spacing:.02em;color:var(--ink)}
.hm-sec-h i{width:3px;height:15px;border-radius:2px;background:var(--accent);display:inline-block}
.hm-sec-h span{font-size:11px;font-weight:600;color:var(--ink-3);letter-spacing:.06em}
.hm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:12px}
.hm-card{position:relative;text-align:left;display:flex;flex-direction:column;gap:6px;min-height:78px;
  padding:14px 15px;border-radius:12px;border:1px solid var(--line);background:var(--bg);
  transition:transform .16s,border-color .16s,box-shadow .16s;font-family:inherit}
.hm-card.can{cursor:pointer}
.hm-card.can:hover{transform:translateY(-2px);border-color:var(--accent);box-shadow:0 10px 24px rgba(75,83,196,.12)}
.hm-card.grey{opacity:.62;cursor:not-allowed}
.hm-card-top{display:flex;align-items:center;gap:7px}
.hm-name{font-size:14px;font-weight:700;color:var(--ink)}
.hm-card.grey .hm-name{color:var(--ink-2)}
.hm-tag{margin-left:auto;flex:none;font-size:10px;font-weight:700;padding:1.5px 8px;border-radius:999px;letter-spacing:.02em}
.hm-tag.soon{color:var(--ink-3);background:var(--bg-rail);border:1px solid var(--line)}
.hm-tag.lock{color:var(--amber);background:var(--amber-bg);border:1px solid var(--amber-line)}
.hm-tag.beta{color:var(--accent);background:var(--accent-soft);border:1px solid var(--accent-soft)}
.hm-hint{font-size:11.5px;line-height:1.5;color:var(--ink-2)}
.hm-card.grey .hm-hint{color:var(--ink-3)}
.hm-go{position:absolute;right:12px;bottom:11px;opacity:0;transform:translateX(-4px);transition:opacity .16s,transform .16s;color:var(--accent)}
.hm-card.can:hover .hm-go{opacity:1;transform:translateX(0)}
.hm-legend{margin-top:26px;font-size:11px;color:var(--ink-3);display:flex;flex-wrap:wrap;gap:14px}
.hm-legend span{display:inline-flex;align-items:center;gap:5px}
.hm-legend i{width:8px;height:8px;border-radius:2px;display:inline-block}
`

const IcGo = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"
    strokeLinecap="round" strokeLinejoin="round"><line x1="5" y1="12" x2="18" y2="12" /><polyline points="12 6 18 12 12 18" /></svg>
)
const IcLock = () => (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"
    strokeLinecap="round"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
)

export default function Home({ user, cfg = {}, navDef, mods, onNav }) {
  const modules = navDef?.modules || []
  const sections = navDef?.sections || []
  const hasCap = c => user?.role === 'admin' || !!user?.perms?.[c]
  // 与侧栏完全同口径：可进入(准入点+动作点)、上线开关、状态字
  const permOf = m => (!m.cap || hasCap(m.cap)) && (!m.act || hasCap(m.act))
  const onOf = k => !mods || mods[k]?.['可进入'] !== false
  const statusOf = k => mods?.[k]?.status || ''
  // 叶子＝非纯分组的模块（含挂在分组父项下的三级）；按 section 归组。配置/维表叶子不进首页。
  const leavesOf = secKey => modules
    .filter(m => m.sec === secKey && !m.group_only && !isConfigLeaf(m))
    .sort((a, b) => (a.order || 0) - (b.order || 0))

  const stateOf = m => {
    if (!onOf(m.key)) return 'soon'        // 未上线（开关没开 / 敬请期待）
    if (!permOf(m)) return 'lock'          // 上线了但没准入权限
    return 'can'                           // 可用
  }

  // 一览计数（全部工具叶子，不含配置页——与卡片同口径）
  let nCan = 0, nSoon = 0, nLock = 0
  modules.filter(m => !m.group_only && !isConfigLeaf(m)).forEach(m => {
    const s = stateOf(m)
    if (s === 'can') nCan++; else if (s === 'lock') nLock++; else nSoon++
  })

  const period = cfg['期间'] || (cfg.year && cfg.period ? `${cfg.year}-${String(cfg.period).padStart(2, '0')}` : '')
  const closed = !!cfg['封存']?.['已封存']
  const kd = cfg.source === 'kingdee'

  const card = (m) => {
    const st = stateOf(m)
    const can = st === 'can'
    const status = statusOf(m.key)
    return (
      <button key={m.key} type="button" className={'hm-card ' + (can ? 'can' : 'grey')}
        onClick={can ? () => onNav && onNav(m.key) : undefined}
        title={can ? '进入 ' + m.label : (st === 'lock' ? '你的账号没有这个板块的权限，找主管理员开通' : '该板块' + (status || '未上线'))}>
        <div className="hm-card-top">
          <span className="hm-name">{m.label}</span>
          {st === 'soon' && <span className="hm-tag soon">{status || '未上线'}</span>}
          {st === 'lock' && <span className="hm-tag lock"><IcLock /> 无权限</span>}
          {can && status && status !== '已上线' && status !== '引擎正常' && <span className="hm-tag beta">{status}</span>}
        </div>
        <div className="hm-hint">{HINT[m.key] || ' '}</div>
        {can && <span className="hm-go"><IcGo /></span>}
      </button>
    )
  }

  const topSecs = sections.filter(s => !s.bottom).sort((a, b) => (a.order || 0) - (b.order || 0))
  const botSecs = sections.filter(s => s.bottom).sort((a, b) => (a.order || 0) - (b.order || 0))
  const renderSec = (s) => {
    const leaves = leavesOf(s.key)
    if (!leaves.length) return null
    return (
      <div className="hm-sec" key={s.key}>
        <div className="hm-sec-h"><i></i>{s.label}<span>{leaves.length} 项</span></div>
        <div className="hm-grid">{leaves.map(card)}</div>
      </div>
    )
  }

  return (
    <div className="hm">
      <style>{CSS}</style>
      <div className="hm-hero">
        <div>
          <h1 className="hm-hi">{greeting()}{user?.name ? '，' + user.name : ''} <em>👋</em></h1>
          <p className="hm-sub">财务核算工作台 · 选一个板块进入；首页不加载业务数据，进板块后才取数。</p>
        </div>
        <div className="hm-chips">
          {period && <span className="hm-chip"><span className="dot" />当前期间 {period}</span>}
          <span className={'hm-chip ' + (closed ? 'ok' : 'warn')}><span className="dot" />{closed ? '本期已封存' : '本期未封存'}</span>
          <span className={'hm-chip ' + (kd ? 'ok' : '')}><span className="dot" />数据源 · {kd ? '金蝶' : '样例'}</span>
        </div>
      </div>

      <div className="hm-glance">
        <span>可进入 <b>{nCan}</b> 个板块</span>
        {nSoon > 0 && <span>未上线 <b>{nSoon}</b></span>}
        {nLock > 0 && <span>待开通权限 <b>{nLock}</b></span>}
      </div>

      {topSecs.map(renderSec)}
      {botSecs.map(renderSec)}

      <div className="hm-legend">
        <span><i style={{ background: 'var(--accent)' }} />可用</span>
        <span><i style={{ background: 'var(--ink-3)' }} />未上线</span>
        <span><i style={{ background: 'var(--amber)' }} />无权限（可申请）</span>
      </div>
    </div>
  )
}
