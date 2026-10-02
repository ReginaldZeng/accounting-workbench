// [Change Log] Date: 2026-10-01 | Author: Claude Opus 5.5 | Version: V2.731
// Description: 首页按权限分层（移植财务BP工作台 V2.550/V2.552/V2.553，交接提示词 §2，口径已定）。
//   访问的人多、权限普遍小：要能区分哪些能用，又让人知道哪些没权限、可以申请。
//   · 问候区（紧凑）：问候 + 岗位；右侧「已开通页面 x / y」+ 进度条（分母只算已上线）；当前期间/封存只给开了期间类页面的人。
//   · 两个高亮按钮「我有权限的 N / 显示全部 N」，选择记本机；没记过：没开全→前者，一个没开→后者（不给空白页），全开→不显示切换。
//   · 卡片三色：绿＝已开通可点；灰紫+锁＝没权限（不用红/橙，没权限是正常状态不是报错）；未上线不成卡片，只在组标题旁一行小字。
//   · 卡片统一两行等高（标题 + 一行说明，超长省略、悬停看全文）；锁卡只靠锁角标+颜色，「申请开通」是标题行右侧小链接。
//   · 申请开通 → 小弹框（开通后能用哪些页面 + 用途选填）→ 钉钉推给接收人（routers/access_request.py）；24 小时内同权限只推一次。
//   ⚠ 首页铁律：不打任何业务数据接口（只读身份与本地态）；唯一例外是轻量的「我的申请记录」，且只在有没开通的页面时才取。
// [Change Log] Date:2026-09-06 Author:Claude/Reginald Zeng Version:V2.500
// V2.500（业务方：首页只留真的工具）：基础数据/基础资料/基础设置/系统设置这类维表·配置叶子不进首页（侧栏仍可达），计数同口径。
import React, { useEffect, useState } from 'react'
import { navIcon, LOCK_ICON } from '../components/Sidebar.jsx'
import { submitAccessRequest, getMyAccessRequests } from '../api.js'
import '../home.css'

// 配置/维表叶子（非「工具」）：按 key 认（改名也挡得住）＋按标签兜底（将来新增的同类也挡得住）。
const _CFG_KEYS = new Set(['basicdata', 'settings', 'logibase', 'clwh', 'bomconfig', 'ecombase'])
const isConfigLeaf = m => _CFG_KEYS.has(m.key) || /^(基础(数据|资料|设置)|系统设置)$/.test(m.label || '')
// 用全局「当前期间」的页面：开了这些的人才在问候区看期间/封存
const PERIOD_KEYS = ['reconcile', 'ledger', 'fundboard', 'wealth', 'periodclose', 'fisbal', 'logistics', 'logisticspay']
export const RECENT_KEY = 'fw-recent-pages'    // App 切页时写入（最近使用排前）
const VIEW_KEY = 'fw-home-view'

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
  fisbal: '科目余额解析 · 期末余额挂的是哪几笔',
  srcexport: '源单明细导出',
  logiupload: '物流部：传账单 / 长表 → 质检 → 提交',
  logistics: '物流计提 · 复核 / 去向费率 / 录金蝶',
  logisticspay: '物流账单核对 · 逐笔计提 / 逐单核价核量 / 钉钉请款进度',
  logisticscost: '单据运费 · 货拉拉等议价/报销登记',
  logisticsvoucher: '付款做账 · 红冲 / 更正 / 暂估转待认证 / 支付 合成一张凭证',
  clexport: '存货台账 · 八步工作流导出',
  cldash: '存货看板',
  bomdraft: 'BOM 报价 · 钉钉抓取 / 入账 / 复核 / 定稿',
  bomstd: '标准成本台账 · 已定稿公开可查',
  prodbrief: '生产简报复核',
  tempattrev: '临时工考勤 · 上报工时 vs 打卡逐日重算',
  tempattboard: '临时工看板 · 全年用工结构',
  revledger: '收入台账',
  custrecon: '客户对账',
  ecompromo: '电商推广',
  ecommonth: '电商月结 · 收款核销',
  ecomsettle: '电商 · 收款核销',
  archive: '凭证归档 · 标签打印',
  invdesk: '扫审批单 → 放票自动拍 → 当场查重 / 对金额 → 提交审核',
  invlater: '付款时票没到的单 · 登记预计来票 · 到了点收到',
  invaudit: '逐张核票 · 判定可否抵扣 · 通过进台账 / 退回补正',
  invledger: '发票台账 · 税局清单验真 · 抵扣勾选 · 新销方核查',
}

function greeting() {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
}
const readLS = k => { try { return localStorage.getItem(k) } catch (e) { return null } }
const writeLS = (k, v) => { try { localStorage.setItem(k, v) } catch (e) { /* 存不了就只本次有效 */ } }

export default function Home({ user, cfg = {}, navDef, mods, onNav }) {
  const modules = navDef?.modules || []
  const sections = (navDef?.sections || []).filter(s => !s.bottom).sort((a, b) => (a.order || 0) - (b.order || 0))
  const hasCap = c => user?.role === 'admin' || !!user?.perms?.[c]
  // 与侧栏完全同口径：可进入(准入点+动作点)、上线开关、状态字
  const canSee = m => (!m.cap || hasCap(m.cap)) && (!m.act || hasCap(m.act))
  const isOn = m => !mods || mods[m.key]?.['可进入'] !== false
  const statusOf = k => mods?.[k]?.status || ''
  const leavesOf = secKey => modules
    .filter(m => m.sec === secKey && !m.group_only && !isConfigLeaf(m) && statusOf(m.key) !== '隐藏' && m.key !== 'ecomsettle')
    .sort((a, b) => (a.order || 0) - (b.order || 0))
  const groups = sections.map(s => ({ key: s.key, label: s.label, kids: leavesOf(s.key) })).filter(g => g.kids.length)
  const leaves = groups.flatMap(g => g.kids)
  const live = leaves.filter(isOn)                         // 已上线（未上线另算，不进分母）
  const mine = live.filter(canSee)
  const allOpen = mine.length === live.length
  const showPeriod = mine.some(l => PERIOD_KEYS.includes(l.key))
  // 岗位存的是岗位 key（改名不丢绑定），显示翻成中文名
  const postName = (user?.post && ((navDef?.posts || []).find(p => p.key === user.post)?.label || user.post)) || (user?.role === 'admin' ? '管理员' : '')

  // 视图：记本机；没记过 → 没开全默认「我有权限的」，一个没开默认「显示全部」（免得空白页）
  const [view, setView] = useState(() => readLS(VIEW_KEY))
  const v = allOpen ? 'all' : (view === 'mine' || view === 'all' ? view : (mine.length ? 'mine' : 'all'))
  const pickView = x => { setView(x); writeLS(VIEW_KEY, x) }

  let recent = []
  try { recent = JSON.parse(readLS(RECENT_KEY) || '[]') } catch (e) { recent = [] }
  const recentSet = new Set(recent.slice(0, 3))

  // 申请开通（V2.731）：只在有没开通的页面时才取「我的申请记录」（轻量、非业务数据）
  const hasLocked = mine.length < live.length
  const [requested, setRequested] = useState({})
  const [askFor, setAskFor] = useState(null)      // {cap, unit, pages}
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const [toast, setToast] = useState(null)        // {ok, text}
  useEffect(() => { if (hasLocked) getMyAccessRequests().then(r => setRequested(r.requested || {})).catch(() => {}) }, [hasLocked])
  const sibsOf = leaf => live.filter(x => x.key !== leaf.key && leaf.cap && x.cap === leaf.cap)
  const openAsk = leaf => {
    const sibs = sibsOf(leaf)
    setNote('')
    setAskFor({ cap: leaf.cap, unit: leaf.label, pages: [leaf, ...sibs].map(x => x.label) })
  }
  const send = async () => {
    if (!askFor) return
    setSending(true)
    try {
      const r = await submitAccessRequest(askFor.cap, askFor.unit, askFor.pages, note)
      setRequested(p => ({ ...p, [askFor.cap]: r.at }))
      setToast({ ok: true, text: `已通过钉钉推送给管理员（${r.to} 人），开通后刷新页面即可使用` })
      setAskFor(null)
    } catch (e) {
      setToast({ ok: false, text: e.message })
    } finally {
      setSending(false)
    }
  }
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(null), 6000); return () => clearTimeout(t) }, [toast])

  const card = leaf => {
    const hint = HINT[leaf.key] || ''
    const st = statusOf(leaf.key)
    if (canSee(leaf)) {
      const beta = st && !['已上线', '引擎正常'].includes(st)
      return (
        <button key={leaf.key} type="button" className="card ok" title={[hint, beta ? st : ''].filter(Boolean).join(' · ') || leaf.label}
          onClick={() => onNav && onNav(leaf.key)}>
          <span className="ci">{navIcon(leaf.key)}</span>
          <b><span className="nm">{leaf.label}</span>
            {recentSet.has(leaf.key) && <span className="recent">最近使用</span>}
            {beta && <span className="beta">{st === '开发中' ? '在建' : st}</span>}</b>
          <span className="hint">{hint || ' '}</span>
        </button>
      )
    }
    const sibs = sibsOf(leaf)
    const tip = [hint, '没权限', sibs.length ? `同一权限还含：${sibs.map(x => x.label).join('、')}` : ''].filter(Boolean).join(' · ')
    const at = leaf.cap && requested[leaf.cap]
    return (
      <div key={leaf.key} className="card lock" title={tip}>
        <span className="ci">{navIcon(leaf.key)}<span className="lk">{LOCK_ICON}</span></span>
        <b><span className="nm">{leaf.label}</span>
          {at ? <span className="asked" title="24 小时内已推送给管理员">已申请 · {String(at).slice(5, 16)}</span>
            : leaf.cap && <button type="button" className="ask" onClick={() => openAsk(leaf)}>申请开通</button>}</b>
        <span className="hint">{hint || ' '}</span>
      </div>
    )
  }

  // 组内：已上线的才成卡（有权限的排前）；未上线的只在组标题旁列名字
  const shownGroups = groups
    .map(g => {
      const liveKids = g.kids.filter(isOn)
      const items = liveKids.filter(k => v === 'all' || canSee(k)).sort((a, b) => canSee(b) - canSee(a))
      return { ...g, items, liveN: liveKids.length, soon: g.kids.filter(k => !isOn(k)) }
    })
    .filter(g => g.items.length || (v === 'all' && g.soon.length))
  const pct = live.length ? Math.round((mine.length / live.length) * 100) : 0
  const period = cfg['期间'] || (cfg.year && cfg.period ? `${cfg.year}-${String(cfg.period).padStart(2, '0')}` : '')
  const closed = !!cfg['封存']?.['已封存']

  let body
  if (v === 'mine' && !mine.length) {
    body = (
      <div className="empty">
        <b>你的账号还没有开通任何页面</b>
        <span>点「显示全部」看看工作台有哪些页面，在需要的页面上点「申请开通」。</span>
      </div>
    )
  } else if (v === 'mine' && mine.length <= 6) {
    // 开得少：平铺，最近使用的排前
    const flat = shownGroups.flatMap(g => g.items).sort((a, b) => recentSet.has(b.key) - recentSet.has(a.key))
    body = <div className="cards">{flat.map(card)}</div>
  } else {
    body = (
      <div>
        {shownGroups.map(g => {
          const okN = g.items.filter(canSee).length
          return (
            <section key={g.key} className="hgrp">
              <div className="grp-h">
                <b>{g.label}</b>
                {v === 'all' && !allOpen && g.liveN > 0 && <span className={'cnt' + (okN ? ' some' : '')}>{okN ? `已开通 ${okN} / ${g.liveN}` : '未开通'}</span>}
                {v === 'all' && g.soon.length > 0 && <span className="soon-note">未上线：{g.soon.map(k => k.label).join('、')}</span>}
              </div>
              {g.items.length > 0 && <div className="cards">{g.items.map(card)}</div>}
            </section>
          )
        })}
      </div>
    )
  }

  return (
    <div className="kd-home">
      {/* 问候 + 我是谁 + 开通进度。不发业务请求：期间来自已在内存的全局态 cfg，身份由 App 传入 */}
      <section className="hero">
        <div>
          <h2>{greeting()}{user?.name ? `，${user.name}` : ''}</h2>
          <div className="id">
            {postName && <span>{postName}</span>}
            <span>财务核算工作台 · 进入页面后才取数</span>
          </div>
        </div>
        <div className="meter">
          <div className="t"><span>已开通页面</span><b>{allOpen ? `全部 ${live.length} 个` : `${mine.length} / ${live.length}`}</b></div>
          <div className="bar"><i style={{ width: `${pct}%` }} /></div>
          {showPeriod && period && (
            <div className="row2">
              <span className="chip per">当前期间 {period}</span>
              <span className={'chip ' + (closed ? 'closed' : 'open')}>{closed ? '本期已封存' : '本期未封存'}</span>
            </div>
          )}
        </div>
      </section>

      <div className="viewbar">
        {allOpen ? (
          <span className="chip per">全部 {live.length} 个页面已开通</span>
        ) : (
          <div className="vt" role="group" aria-label="显示范围">
            <button type="button" className="mine" aria-pressed={v === 'mine'} onClick={() => pickView('mine')}>
              我有权限的<span className="c">{mine.length}</span>
            </button>
            <button type="button" className="all" aria-pressed={v === 'all'} onClick={() => pickView('all')}>
              显示全部<span className="c">{live.length}</span>
            </button>
          </div>
        )}
        {v === 'all' && !allOpen && (
          <div className="legend">
            <span><i className="l-ok" />已开通</span>
            <span><i className="l-lock" />没权限 · 可申请</span>
          </div>
        )}
      </div>

      {body}

      {toast && <div className={'toast ' + (toast.ok ? 'ok' : 'bad')} role="status">{toast.text}</div>}

      {askFor && (
        <div className="mask" onMouseDown={e => { if (e.target === e.currentTarget && !sending) setAskFor(null) }}>
          <div className="dlg" role="dialog" aria-label={`申请开通「${askFor.unit}」`}>
            <div className="dlg-h"><b>申请开通「{askFor.unit}」</b>
              <button type="button" className="x" onClick={() => setAskFor(null)} aria-label="关闭">✕</button></div>
            <div className="dlg-b">
              <div>开通后可以使用：<b>{askFor.pages.join('、')}</b></div>
              <textarea value={note} onChange={e => setNote(e.target.value.slice(0, 200))} rows={3}
                placeholder="用途（选填）：比如「月结时要看本主体的银行对账」" />
              <div className="cnt-r">{note.length} / 200</div>
              <div className="fine">点「发送给管理员」后，钉钉会把你的姓名、岗位和申请内容推给管理员。管理员在门户「账号管理」开通后，刷新页面即可使用。</div>
            </div>
            <div className="dlg-f">
              <button type="button" className="btn" onClick={() => setAskFor(null)} disabled={sending}>取消</button>
              <button type="button" className="btn btn-pri" onClick={send} disabled={sending}>{sending ? '发送中…' : '发送给管理员'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
