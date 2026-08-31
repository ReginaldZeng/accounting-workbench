// [Change Log] Date:2026-07-17 Author:Claude/c Version:V2.53
// 账号管理·权限中枢：全屏三工作台统一发证。
//  左＝账号列表，按 5 类账号分区、可折叠、搜索置顶、新建含岗位（下拉选，V2.52）。
//  右＝选中账号的权限，每个工作台内**三栏**（先答「能不能进」、再答「进去能干什么」）：
//    ① 块头两个开关＝能不能进工作台（准入总闸 + 任命权，敏感）
//    ② tier=nav 准入点＝看得见哪些菜单，按业务板块分
//    ③ tier=act 动作点＝进去能干什么，**只出第②栏勾了的菜单**、按菜单归堆并标出处（gate/modLabel 由后端算）
//  取消某菜单准入 → 它底下动作跟着收回（前端即时 + 后端 _cascade_revoke 兜底，绕不过）。
//  常规/敏感由后端 sensitive 标记驱动；tier/mod 也在后端注册表，此页自动渲染。加权限点只改后端。
import React, { useEffect, useMemo, useState } from 'react'
import { listUsers, createUser, setUserActive, resetPwd, deleteUser, getPermCaps, getBpPermDrift, setUserPerms, setCapSensitivity, getNavModules, setUserPost, saveNavTemplates, saveNavPosts } from '../api.js'

// V2.324 加 platform：平台级权限点（如 model_config「模型配置」）的渲染区块。它不是工作台——
// 不在后端 WS_LABEL 里、没有准入/任命开关，子管理员不可授（assignable 过滤会对其隐藏整块）。
// ⚠ 不加进 WS_ORDER 的 ws 会被 wsList 静默吞掉，变成「勾不到的幽灵权限」（V2.240 同款坑）。
const WS_ORDER = ['accounting', 'bp', 'legal', 'platform']
const WS_META = {
  accounting: { icon: '🧮', en: 'ACCOUNTING', cls: 'acc' },
  bp: { icon: '📊', en: 'ANALYSIS / BP', cls: 'bp' },
  legal: { icon: '⚖️', en: 'LEGAL', cls: 'leg' },
  platform: { icon: '🛰️', en: 'PLATFORM · PORTAL', cls: 'plat', name: '平台 · 门户' },
}
const WS_LABEL_SHORT = { accounting: '核算', bp: 'BP', legal: '法务', platform: '平台' }
const MANAGE_CAPS = ['manage_accounting', 'manage_bp', 'manage_legal']
const MANAGE_WS_LABEL = { manage_accounting: '核算', manage_bp: 'BP', manage_legal: '法务' }
// 账号分区（顺序即展示顺序）
const CATS = [
  { key: 'super', label: '主管理员', note: '含系统管理员', color: 'var(--brand2)' },
  { key: 'subadmin', label: '子管理员', color: '#9B7BFF' },
  { key: 'normal', label: '普通账号', color: 'var(--green)' },
  { key: 'external', label: '外部门账号', note: '分组=外部协作', color: 'var(--ai)' },
  { key: 'disabled', label: '禁用账号', note: '任何类型一禁用即归此', color: 'var(--ink3)' },
]
const GRP_OPTS_SUPER = ['核算组', 'BP组', '法务', '外部协作', '管理']
// V2.52：岗位不再是手打的自由文本，改为从「系统设置 › 岗位名单」里下拉选（D11），故这里的建议表退休。

// 一个权限点 chip。第②③栏共用——两栏长得一样，区别只在「摆在哪、什么时候出现」
function Chip({ c, pd, isSens, escSet, assignable, onToggle }) {
  const locked = !c.plan && !assignable.has(c.key)
  return (
    <div className={'ua-chip' + (pd[c.key] ? ' on' : '') + (isSens(c) ? ' sens' : '')
      + (c.plan ? ' plan' : '') + (locked ? ' lock' : '')}
      onClick={() => onToggle(c)}>
      <span className="bx">✓</span>{c.label}
      {c.sensitive ? <span className="ua-mini">🔒敏</span>
        : escSet.has(c.key) && <span className="ua-mini esc">⤴敏</span>}
      {c.plan && <span className="ua-plantag">规划</span>}
    </div>
  )
}

function categoryOf(u) {
  if (!u.active) return 'disabled'
  if (u.role === 'admin') return 'super'
  const p = u.perms || {}
  if (MANAGE_CAPS.some(k => p[k])) return 'subadmin'
  if (u.grp === '外部协作') return 'external'
  return 'normal'
}

const CSS = `
.ua-root{--panel:#221A3A;--line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.14);
  --ink:#EDEAF6;--ink2:#B4ABD4;--ink3:#7E76A0;--brand:#7C5CFF;--brand2:#9B7BFF;--ai:#3FE0C8;
  --green:#34D399;--amber:#FBBF24;--red:#F87171;
  color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",sans-serif;
  padding:8px clamp(16px,3vw,40px) 120px}
.ua-root *{box-sizing:border-box}
.ua-h1{font-size:22px;font-weight:800;display:flex;align-items:center;gap:10px}
.ua-tier{font-size:11.5px;font-weight:600;border:1px solid var(--line2);border-radius:999px;padding:2px 11px}
.ua-sub{font-size:12.5px;color:var(--ink3);margin-top:8px;max-width:820px;line-height:1.7}
.ua-msg{margin-left:2px}
.ua-legend{display:flex;align-items:center;gap:16px;margin-top:14px;flex-wrap:wrap;font-size:12px;color:var(--ink2)}
.ua-lg{display:flex;align-items:center;gap:7px}
.ua-sw0{width:26px;height:16px;border-radius:5px;border:1px solid var(--line2);background:rgba(255,255,255,.05)}
.ua-sw0.on{background:rgba(124,92,255,.18);border-color:var(--brand)}
.ua-sw0.sens{background:rgba(251,191,36,.12);border-color:rgba(251,191,36,.5)}
.ua-filter{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink2);cursor:pointer;user-select:none;border:1px solid var(--line2);padding:6px 12px;border-radius:9px}
.ua-filter:hover{background:rgba(255,255,255,.04)}
.ua-filter.on{border-color:var(--amber);color:var(--amber);background:rgba(251,191,36,.08)}

.ua-cols{display:flex;gap:18px;align-items:flex-start;margin-top:16px}
.ua-left{flex:0 0 306px;position:sticky;top:12px}
.ua-right{flex:1 1 0;min-width:0}
@media(max-width:1000px){.ua-cols{flex-wrap:wrap}.ua-left{position:static;flex:1 1 100%}}

.ua-card{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.005));border:1px solid var(--line);border-radius:14px;padding:14px}
.ua-ct{font-size:13px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between}
.ua-lk{color:var(--brand2);cursor:pointer;font-weight:600;font-size:12.5px}
.ua-lk:hover{text-decoration:underline}
.ua-inp{width:100%;height:32px;border-radius:8px;border:1px solid var(--line2);background:rgba(255,255,255,.05);color:var(--ink);padding:0 11px;font-size:12.5px;outline:none;font-family:inherit}
.ua-inp:focus{border-color:var(--brand)}
.ua-inp::placeholder{color:var(--ink3)}
.ua-root select.ua-inp option{background:#221A3A;color:var(--ink)}
.ua-search{position:relative;margin-bottom:8px}
.ua-search .ic{position:absolute;left:11px;top:50%;transform:translateY(-50%);font-size:12px;color:var(--ink3);pointer-events:none}
.ua-search .ua-inp{padding-left:31px}
.ua-newf{display:flex;flex-direction:column;gap:8px;margin-bottom:10px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.ua-hint{font-size:11px;color:var(--ink3);line-height:1.6}
.ua-btn{height:32px;padding:0 12px;border-radius:8px;border:1px solid var(--line2);background:rgba(255,255,255,.06);color:var(--ink);font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;white-space:nowrap}
.ua-btn:hover{background:rgba(255,255,255,.1)}
.ua-btn.pri{background:linear-gradient(180deg,var(--brand),#6A4CE6);border-color:transparent;color:#fff}
.ua-btn.pri:hover{filter:brightness(1.08)}
.ua-btn.red{color:var(--red)}

.ua-grp-h{display:flex;align-items:center;gap:7px;font-size:10.5px;letter-spacing:.6px;color:var(--ink3);margin:14px 2px 5px;font-weight:700;cursor:pointer;user-select:none}
.ua-grp-h:first-child{margin-top:2px}
.ua-grp-h:hover{color:var(--ink2)}
.ua-caret{font-size:8px;width:9px;text-align:center;display:inline-block;transition:transform .15s}
.ua-cg.col .ua-caret{transform:rotate(-90deg)}
.ua-cg.col .ua-grp-items{display:none}
.ua-gd{width:6px;height:6px;border-radius:50%;flex:none}
.ua-c{background:rgba(255,255,255,.08);border-radius:999px;padding:0 6px;font-size:9.5px;font-weight:600}
.ua-note{font-weight:400;color:var(--ink3);opacity:.75}
.ua-ln{flex:1;height:1px;background:var(--line);margin-left:4px}

.ua-item{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:10px;cursor:pointer;border:1px solid transparent}
.ua-item:hover{background:rgba(255,255,255,.04)}
.ua-item.sel{background:rgba(124,92,255,.16);border-color:rgba(124,92,255,.45)}
.ua-item.sub{background:rgba(124,92,255,.08);border-color:rgba(124,92,255,.3)}
.ua-item.sub.sel{background:rgba(124,92,255,.16)}
.ua-item.dis{opacity:.6}
.ua-dot{width:7px;height:7px;border-radius:50%;flex:none}
.ua-nm{font-weight:700;font-size:13px;display:flex;align-items:center;gap:6px}
.ua-tag{font-size:9.5px;border-radius:4px;padding:0 5px}
.ua-tag.pp{color:var(--brand2);border:1px solid rgba(124,92,255,.5);background:rgba(124,92,255,.14)}
.ua-tag.ext{color:var(--ai);border:1px solid rgba(63,224,200,.45);background:rgba(63,224,200,.1)}
.ua-meta{font-size:11px;color:var(--ink3);margin-top:2px}
.ua-badge{font-size:10px;padding:1px 7px;border-radius:999px;border:1px solid var(--line2);color:var(--ink2);margin-left:auto;white-space:nowrap}
.ua-badge.ext{color:var(--ai);border-color:rgba(63,224,200,.4);background:rgba(63,224,200,.08)}

.ua-empty{border:1px dashed var(--line2);border-radius:12px;padding:40px 20px;text-align:center;color:var(--ink3);font-size:13px}
.ua-dhead{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:16px 18px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,rgba(124,92,255,.06),rgba(255,255,255,0))}
.ua-dname{font-size:18px;font-weight:800;display:flex;align-items:center;gap:9px}
/* 岗位内联下拉（V2.143）：嵌在名字行里＝「李志鹏 · 成本会计」。默认长得像文字，hover 才露出是控件 */
.ua-postsel{font-size:15px;font-weight:600;color:var(--ink2);background:transparent;border:1px solid transparent;
  border-radius:7px;padding:2px 4px;cursor:pointer;font-family:inherit;max-width:180px}
.ua-postsel:hover{border-color:var(--line2);background:rgba(124,92,255,.06)}
.ua-postsel:disabled{opacity:.5;cursor:default}
.ua-dmeta{font-size:12px;color:var(--ink3);margin-top:5px}
.ua-acts{display:flex;gap:8px;flex-wrap:wrap}

.ua-ws{border:1px solid var(--line);border-radius:16px;margin-top:16px;overflow:hidden}
.ua-whead{display:flex;align-items:center;gap:12px;padding:13px 18px;border-bottom:1px solid var(--line)}
.ua-ws.acc .ua-whead{background:linear-gradient(90deg,rgba(124,92,255,.14),rgba(124,92,255,.02))}
.ua-ws.bp .ua-whead{background:linear-gradient(90deg,rgba(63,224,200,.13),rgba(63,224,200,.02))}
.ua-ws.leg .ua-whead{background:linear-gradient(90deg,rgba(138,130,168,.14),rgba(138,130,168,.02))}
.ua-ws.plat .ua-whead{background:linear-gradient(90deg,rgba(143,233,255,.13),rgba(143,233,255,.02))}
.ua-wic{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;font-size:15px;flex:none}
.ua-ws.acc .ua-wic{background:rgba(124,92,255,.18)}.ua-ws.bp .ua-wic{background:rgba(63,224,200,.16)}.ua-ws.leg .ua-wic{background:rgba(138,130,168,.18)}.ua-ws.plat .ua-wic{background:rgba(143,233,255,.16)}
.ua-wname{font-size:15px;font-weight:800}
.ua-wen{font-size:9px;letter-spacing:1.5px;color:var(--ink3);margin-top:2px}
.ua-wctrls{margin-left:auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ua-sw{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink2);cursor:pointer;user-select:none;border:1px solid var(--line2);border-radius:9px;padding:5px 11px;background:rgba(255,255,255,.03)}
.ua-sw .tk{width:34px;height:19px;border-radius:999px;background:rgba(255,255,255,.14);position:relative;transition:.15s;flex:none}
.ua-sw .tk::after{content:"";position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;background:#fff;transition:.15s}
.ua-sw.on .tk{background:var(--brand)}.ua-sw.on .tk::after{left:17px}
.ua-sw.on{color:var(--ink);border-color:var(--brand)}
.ua-sw.sens{border-color:rgba(251,191,36,.35)}
.ua-sw.sens.on .tk{background:var(--amber)}
.ua-sw.sens.on{color:var(--amber);border-color:var(--amber);background:rgba(251,191,36,.08)}
.ua-sw.dis{opacity:.4;cursor:not-allowed}
.ua-stag{font-size:9px;font-weight:700;color:var(--amber);border:1px solid rgba(251,191,36,.5);border-radius:4px;padding:0 4px}

.ua-whint{display:flex;align-items:center;gap:8px;padding:12px 18px;font-size:12px;color:var(--ink3)}
.ua-wbody{padding:14px 18px;display:flex;flex-direction:column;gap:15px}
.ua-gt{font-size:11.5px;color:var(--ink3);letter-spacing:.6px;margin-bottom:9px;display:flex;align-items:center;gap:8px}
.ua-gt .l{flex:1;height:1px;background:var(--line)}
.ua-chips{display:flex;flex-wrap:wrap;gap:9px}

/* V2.53 三栏：② 看得见哪些菜单 | ③ 进去能干什么（只出进得去的）。① 进不进得来＝块头那两个开关 */
.ua-t2{flex-direction:row;gap:16px;align-items:stretch}
.ua-tcol{flex:1 1 0;min-width:0;border:1px solid var(--line);border-radius:12px;padding:13px;
  background:rgba(255,255,255,.02);display:flex;flex-direction:column;gap:13px}
.ua-tcol.act{border-color:rgba(124,92,255,.36);background:rgba(124,92,255,.05)}
.ua-th{font-size:12.5px;font-weight:800;display:flex;align-items:center;gap:9px}
.ua-th .no{width:19px;height:19px;border-radius:5px;background:var(--brand);color:#fff;font-size:11px;
  display:flex;align-items:center;justify-content:center;font-weight:800;flex:0 0 auto}
.ua-ths{font-size:10.5px;color:var(--ink3);margin:-9px 0 0 28px;line-height:1.6}
.ua-heap{}
.ua-hh{display:flex;align-items:center;gap:8px;font-size:11.5px;font-weight:800;color:var(--brand2);margin-bottom:7px}
.ua-hh .dot{width:5px;height:5px;border-radius:50%;background:var(--brand2);flex:0 0 auto}
.ua-hh .from{font-size:9.5px;font-weight:600;color:var(--ink3);background:rgba(255,255,255,.05);border-radius:4px;padding:1px 6px}
.ua-chips.hb{padding-left:13px}
.ua-none{font-size:10.5px;color:var(--ink3);font-style:italic;padding-left:13px}
@media(max-width:1400px){.ua-t2{flex-direction:column}}
.ua-chip{display:inline-flex;align-items:center;gap:8px;padding:8px 13px;border-radius:10px;border:1px solid var(--line2);background:rgba(255,255,255,.04);cursor:pointer;font-size:12.5px;color:var(--ink2);user-select:none;transition:.12s}
.ua-chip:hover{background:rgba(255,255,255,.07)}
.ua-chip .bx{width:15px;height:15px;border-radius:4px;border:1.6px solid var(--ink3);display:grid;place-items:center;font-size:11px;color:transparent;flex:none}
.ua-chip.on{border-color:var(--brand);background:rgba(124,92,255,.16);color:#fff}
.ua-chip.on .bx{background:var(--brand);border-color:var(--brand);color:#fff}
.ua-chip.sens{border-color:rgba(251,191,36,.4);background:rgba(251,191,36,.06)}
.ua-chip.sens .bx{border-color:var(--amber)}
.ua-chip.sens.on{border-color:var(--amber);background:rgba(251,191,36,.18);color:#FBE8B0}
.ua-chip.sens.on .bx{background:var(--amber);border-color:var(--amber);color:#3a2c05}
.ua-chip.plan{opacity:.4;cursor:not-allowed;border-style:dashed}
.ua-chip.lock{opacity:.5;cursor:not-allowed}
.ua-mini{font-size:9px;color:var(--amber);border:1px solid rgba(251,191,36,.45);border-radius:4px;padding:0 4px}
.ua-mini.esc{color:#FCA55D;border-color:rgba(252,165,93,.6);background:rgba(252,165,93,.1)}
.ua-mask{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50}
.ua-drawer{position:fixed;top:0;right:0;bottom:0;width:min(480px,94vw);background:var(--panel);border-left:1px solid var(--line2);z-index:51;overflow:auto;padding:22px}
.ua-drawer h3{font-size:16px;font-weight:800;display:flex;align-items:center;gap:8px}
.ua-drawer .x{margin-left:auto;cursor:pointer;color:var(--ink3);font-size:18px}
.ua-dsub{font-size:12px;color:var(--ink3);line-height:1.7;margin:8px 0 6px}
.ua-ratchet{font-size:11.5px;color:var(--amber);background:rgba(251,191,36,.07);border:1px solid rgba(251,191,36,.25);border-radius:9px;padding:9px 11px;line-height:1.6;margin:12px 0 4px}
.ua-lvlg{font-size:11px;color:var(--ink3);letter-spacing:.5px;margin:16px 0 8px}
.ua-lvl{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:7px;font-size:12.5px}
.ua-lvl.fixed{background:rgba(251,191,36,.05);border-color:rgba(251,191,36,.22)}
.ua-lvl .n{flex:1}.ua-lvl .n small{color:var(--ink3);font-size:10.5px;margin-left:4px}
.ua-lvl .lock{font-size:11px;color:var(--amber);white-space:nowrap}
.ua-msw{width:34px;height:19px;border-radius:999px;background:rgba(255,255,255,.14);position:relative;cursor:pointer;flex:none;transition:.15s}
.ua-msw::after{content:"";position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;background:#fff;transition:.15s}
.ua-msw.on{background:#FCA55D}.ua-msw.on::after{left:17px}
.ua-plantag{font-size:9px;color:var(--ink3);border:1px solid var(--line2);border-radius:4px;padding:0 4px}

/* V2.322 数据范围（驾驶舱按销售团队分权）：从属于「驾驶舱看板」的模式+团队控件，不摊平成一排勾选框 */
.ua-scope{border:1px solid rgba(251,191,36,.28);background:rgba(251,191,36,.05);border-radius:11px;padding:11px 13px;margin:2px 0 10px 13px}
.ua-scope-t{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:800;color:#FBE8B0}
.ua-scope-m{display:flex;gap:16px;margin:8px 0 2px;font-size:12.5px;color:var(--ink2)}
.ua-scope-m label{display:flex;align-items:center;gap:6px;cursor:pointer}
.ua-scope-m label.dis{opacity:.5;cursor:not-allowed}
.ua-scope-m input{accent-color:var(--amber)}
.ua-scope-teams{display:flex;flex-wrap:wrap;gap:8px;margin-top:9px}
.ua-scope-hint{font-size:10.5px;color:var(--ink3);margin-top:8px;line-height:1.6}
/* V2.323 二级动作（parent 从属）：父项勾了才展开的附加权限行 */
.ua-subrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:7px 0 3px 26px;padding-left:10px;border-left:2px solid rgba(255,255,255,.1)}
.ua-subhint{font-size:10.5px;color:var(--ink3);white-space:nowrap}
.ua-subwarn{font-size:10px;color:var(--amber)}

.ua-savebar{position:fixed;left:0;right:0;bottom:0;z-index:30;display:flex;align-items:center;gap:14px;padding:13px clamp(16px,3vw,40px);border-top:1px solid var(--line);background:rgba(20,16,31,.92);backdrop-filter:blur(8px)}
.ua-savebar .sum{font-size:12.5px;color:var(--ink2)}
.ua-savebar .sum b{color:var(--ink)}
`

export default function UserAdmin({ me }) {
  const [rows, setRows] = useState([])
  const [caps, setCaps] = useState([])
  const [bpDrift, setBpDrift] = useState(null)   // V2.106 BP 码表对账（null=未查/不可用）
  const [scope, setScope] = useState({ is_super: true, assignable: [], manageable_grps: null })
  const [sel, setSel] = useState(null)
  const [pd, setPd] = useState({})
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState(null)
  const [q, setQ] = useState('')
  const [onlySens, setOnlySens] = useState(false)
  const [showLevels, setShowLevels] = useState(false)
  const [colCats, setColCats] = useState({})
  const [showNew, setShowNew] = useState(false)
  const [nn, setNn] = useState(''), [np, setNp] = useState(''), [npost, setNpost] = useState('')
  const [ng, setNg] = useState('核算组'), [nr, setNr] = useState('normal')

  const [postList, setPostList] = useState([])    // 岗位名单 [{key,label}]，主管理员在系统设置里维护
  const [postBusy, setPostBusy] = useState(false)
  const [navDef, setNavDef] = useState(null)      // 完整菜单树+岗位模板（V2.143：套用预览要用）
  const [tplOpen, setTplOpen] = useState(false)   // 「按岗位套用」预览面板
  // 岗位模板设置抽屉（V2.144，业务方定：在顶部工具栏给「套用什么」一个维护入口）
  const [showTpl, setShowTpl] = useState(false)   // 抽屉开关
  const [tplSel, setTplSel] = useState('')        // 抽屉里正在编辑的岗位 key
  const [tplDraft, setTplDraft] = useState(null)  // 编辑副本 {post:{secs,mods,acts}}；null=未加载
  const [tplSaving, setTplSaving] = useState(false)
  const load = () => listUsers().then(r => setRows(r.users || [])).catch(() => {})
  const loadCaps = () => getPermCaps().then(r => { setCaps(r.caps || []); setScope(r.scope || { is_super: true }) }).catch(() => {})
  useEffect(() => { load(); loadCaps() }, [])
  // 岗位名单：拉不到就静默降级成空（岗位只是标签+模板，不该拖垮整个账号管理页）
  useEffect(() => { getNavModules().then(r => { setPostList(r.posts || []); setNavDef(r) }).catch(() => {}) }, [])
  // V2.106：BP 权限码表对账。**单独异步拉、失败静默** —— 账号管理页绝不能因 BP 不可达而变慢/报错
  useEffect(() => { getBpPermDrift().then(r => { if (r && r.ok && r.available) setBpDrift(r) }).catch(() => {}) }, [])
  const flash = (ok, t) => { setMsg({ ok, t }); setTimeout(() => setMsg(null), 2400) }

  const isSuper = !!scope.is_super
  const assignable = useMemo(() => new Set(scope.assignable || []), [scope])
  const escSet = useMemo(() => new Set(scope.escalated || []), [scope])
  const isSens = (c) => !!c.sensitive || escSet.has(c.key)     // 有效敏感 = 代码定死 ∪ 主管理员加严
  const toggleEsc = async (c, on) => {
    const r = await setCapSensitivity({ cap: c.key, on })
    if (r.ok) { await loadCaps(); flash(true, (on ? '已升为敏感：' : '已降回常规：') + c.label) }
    else flash(false, r.msg)
  }
  const grpOpts = isSuper ? GRP_OPTS_SUPER : (scope.manageable_grps || [])
  const tierText = isSuper ? '主管理员' : ((me.managed_ws_label || []).join('、') + ' 子管理员')

  const cur = rows.find(u => u.name === sel) || null
  useEffect(() => { setPd({ ...((cur && cur.perms) || {}) }); setDirty(false); setTplOpen(false) }, [sel, rows.length])

  // 岗位：存量账号存的是手打的中文（"总账"/"人力资源经理"…），认不出的原样留着显示成「待认领」，不静默清空
  const postKnown = k => !!k && postList.some(p => p.key === k)
  const postLabel = k => (postList.find(p => p.key === k) || {}).label || k || ''
  const capLabel = k => (caps.find(c => c.key === k) || {}).label || k
  const changePost = async (u, post, apply) => {
    setPostBusy(true)
    const r = await setUserPost({ name: u.name, post, apply })
    setPostBusy(false)
    if (!r.ok) { flash(false, r.msg || '改岗位失败'); return }
    await load()
    if (apply) {
      setTplOpen(false)
      // 套完把【实际新勾的点】列出来（≤5 个点名，多的归总）——"勾了 N 个"没人知道是哪 N 个
      const names = (r.granted || []).map(capLabel)
      flash(true, names.length
        ? `已按「${postLabel(post)}」套用，新勾上 ${names.length} 项：${names.slice(0, 5).join('、')}${names.length > 5 ? ` 等 ${names.length} 项` : ''}`
        : `「${postLabel(post)}」模板里的权限点他已经都有了，没有新增`)
    } else flash(true, post ? '岗位已改为 ' + postLabel(post) : '岗位已清空')
  }
  // ── 岗位模板设置抽屉（V2.144）──
  // 打开时从 navDef.templates 建编辑副本；保存走 /api/nav-modules/save {templates}（只发模板，
  // state 缺席后端不再重置——V2.144 修的雷）。敏感点不列：后端 _template_caps 本来就会滤掉，
  // UI 列了也是骗人的勾。
  const openTplDrawer = () => {
    const t = navDef?.templates || {}
    setTplDraft(Object.fromEntries(postList.map(p => {
      const v = t[p.key] || {}
      return [p.key, { secs: [...(v.secs || [])], mods: [...(v.mods || [])], acts: [...(v.acts || [])] }]
    })))
    setTplSel(postList[0]?.key || '')
    setShowTpl(true)
  }
  const tplToggle = (post, field, val) => setTplDraft(d => {
    const cur = d[post] || { secs: [], mods: [], acts: [] }
    const arr = cur[field] || []
    return { ...d, [post]: { ...cur, [field]: arr.includes(val) ? arr.filter(x => x !== val) : [...arr, val] } }
  })
  const saveTpl = async () => {
    setTplSaving(true)
    const r = await saveNavTemplates(tplDraft)
    setTplSaving(false)
    if (!r.ok) { flash(false, r.msg || '保存模板失败'); return }
    setNavDef(n => ({ ...n, templates: r.templates || tplDraft }))
    setShowTpl(false)
    flash(true, '岗位模板已保存——之后「按岗位一键套用」就按这份新模板勾')
  }
  // 岗位名单维护（V2.145 就近新增 → V2.146 升级为完整维护，业务方定「岗位名单应该放到门户」）。
  // 原核算工作台›系统设置里的岗位名单区块已撤（不能两个地方都能改）；
  // 删除的连带清理（从各模块挂载与模板里摘掉）V2.146 起由后端做，这里只发全量名单。
  const [npNew, setNpNew] = useState('')
  const pushPosts = async (next, okMsg) => {
    const r = await saveNavPosts(next)
    if (!r.ok) { flash(false, r.msg || '保存岗位名单失败'); return null }
    setPostList(r.posts || [])
    setNavDef(n => n ? { ...n, posts: r.posts || [] } : n)
    if (okMsg) flash(true, okMsg)
    return r.posts || []
  }
  const addPostInline = async () => {
    const label = npNew.trim().slice(0, 10)
    if (!label) return
    if (postList.some(p => p.label === label)) { flash(false, `岗位「${label}」已存在`); return }
    const key = 'p' + Math.random().toString(36).slice(2, 8)     // 与原系统设置页同一套 key 生成规则
    const ok = await pushPosts([...postList, { key, label }], `已新增岗位「${label}」——先在下面配它的模板，再到账号上选用`)
    if (!ok) return
    setTplDraft(d => d ? { ...d, [key]: { secs: [], mods: [], acts: [] } } : d)  // 新岗位=空模板，接着就能配
    setTplSel(key)
    setNpNew('')
  }
  const renamePostInline = async (key, label) => {
    const lb = (label || '').trim().slice(0, 10)
    const cur = postList.find(p => p.key === key)
    if (!lb || !cur || cur.label === lb) return
    await pushPosts(postList.map(p => p.key === key ? { ...p, label: lb } : p),
      `岗位已改名为「${lb}」（改名不丢绑定：账号与模板都跟着走）`)
  }
  const delPostInline = async (key) => {
    const cur = postList.find(p => p.key === key)
    if (!cur) return
    if (postList.length <= 1) { flash(false, '岗位名单至少保留 1 个'); return }
    if (!window.confirm(`确认删除岗位「${cur.label}」？\n· 会自动从各模块的岗位挂载与套用模板里摘掉\n· 已选此岗位的账号会显示「待认领」，需重新选岗位`)) return
    const ok = await pushPosts(postList.filter(p => p.key !== key), `已删除岗位「${cur.label}」`)
    if (!ok) return
    setTplDraft(d => { if (!d) return d; const n = { ...d }; delete n[key]; return n })
    if (tplSel === key) setTplSel(postList.filter(p => p.key !== key)[0]?.key || '')
  }

  // 套用预览（V2.143）：把该岗位模板翻成人话——点之前就看得见会勾什么，而不是套完才知道。
  // 展示口径与后端 _template_caps 一致：板块整给 + 单点菜单 + 非敏感动作点；敏感点永不进模板。
  const tplPreview = (postKey) => {
    const t = (navDef?.templates || {})[postKey]
    if (!t) return null
    const secLabel = k => (navDef?.sections || []).find(s => s.key === k)?.label || k
    const modLabel = k => (navDef?.modules || []).find(m => m.key === k)?.label || k
    return {
      secs: (t.secs || []).map(secLabel),
      mods: (t.mods || []).map(modLabel),
      acts: (t.acts || []).map(capLabel),
    }
  }

  // 把 caps 组织成三工作台 × 三栏（V2.53）：
  //   ① enter/manage = 能不能进工作台   ② tier=nav = 看得见哪些菜单   ③ tier=act = 进去能干什么
  // 后端已算好每个动作点的 gate（＝门控它的准入点），第③栏据此只显示「进得去的菜单」的动作。
  const wsList = useMemo(() => {
    const map = {}
    for (const c of caps) {
      const ws = c.ws || 'other'
      if (!map[ws]) map[ws] = { enter: null, manage: null, navGroups: [], acts: [] }
      if (c.kind === 'enter') map[ws].enter = c
      else if (c.kind === 'manage') map[ws].manage = c
      else if (c.tier === 'act') map[ws].acts.push(c)
      else {
        let g = map[ws].navGroups.find(x => x.name === (c.group || '其他'))
        if (!g) { g = { name: c.group || '其他', items: [] }; map[ws].navGroups.push(g) }
        g.items.push(c)
      }
    }
    return WS_ORDER.filter(w => map[w]).map(w => ({ ws: w, ...map[w] }))
      .filter(w => isSuper || [w.enter, ...w.navGroups.flatMap(g => g.items), ...w.acts]
        .some(c => c && assignable.has(c.key)))
  }, [caps, isSuper, assignable])

  // 第③栏：把动作点按【所属菜单】归堆，只留进得去的那些菜单。
  // gate 为空 = 不受菜单门控的全台通用动作（如 BP 的编辑权）→ 单独一堆，恒显示。
  const actsFor = (w) => {
    const heaps = [], generic = [], scopeCaps = []
    for (const c of w.acts) {
      // V2.322 数据域码（BP registry 动态下发，scope=True）不进摊平列表——
      // 在「驾驶舱看板」堆里渲染成专属「数据范围」控件（业务方：勾了驾驶舱再选团队，别摊成一排框）
      if (c.scope || (c.key || '').startsWith('perf:team:')) {
        if (pd[c.gate || 'bp:board:budgetCockpit']) scopeCaps.push(c)
        continue
      }
      if (onlySens && !isSens(c)) continue
      if (!c.gate) { generic.push(c); continue }
      if (!pd[c.gate]) continue                 // 进不去这个菜单 → 它的动作根本不渲染
      let h = heaps.find(x => x.gate === c.gate)
      if (!h) { h = { gate: c.gate, name: c.modLabel || '', sec: c.secLabel || '', items: [] }; heaps.push(h) }
      h.items.push(c)
    }
    return { heaps, generic, scopeCaps }
  }
  // 进得去、但没有任何动作权限可配的菜单（如理财对账）——列一行说明，免得管理员以为漏了
  const navNoActs = (w) => w.navGroups.flatMap(g => g.items)
    .filter(c => pd[c.key] && !w.acts.some(a => a.gate === c.key))
    .map(c => c.label.replace(/^进入\s*/, ''))

  const totalCap = caps.filter(c => c.kind !== 'enter' && c.kind !== 'manage').length

  // 按 5 类账号分区
  const byCat = useMemo(() => {
    const kw = q.trim()
    // 岗位存的是 key，搜"成本会计"要能搜到 → 连中文名一起比（存量未认领的原样值也留在比对里）
    const list = rows.filter(u => !kw || [u.name, u.post, postLabel(u.post), u.grp].some(s => (s || '').includes(kw)))
    const m = {}; CATS.forEach(c => m[c.key] = [])
    list.forEach(u => m[categoryOf(u)].push(u))
    return m
  }, [rows, q, postList])

  const add = async () => {
    if (!nn.trim() || !np) { flash(false, '姓名和密码都要填'); return }
    const grp = (isSuper || (scope.manageable_grps || []).includes(ng)) ? ng : (scope.manageable_grps || [])[0]
    const r = await createUser({ name: nn.trim(), password: np, grp, post: npost.trim(), role: isSuper ? nr : 'normal' })
    if (r.ok) { const name = nn.trim(); setNn(''); setNp(''); setNpost(''); setShowNew(false); flash(true, '已建账号 ' + name); await load(); setSel(name) }
    else flash(false, r.msg)
  }
  const toggleActive = async (u) => { await setUserActive({ name: u.name, active: !u.active }); load() }
  const reset = async (u) => { const p = window.prompt(`给「${u.name}」设置新密码：`, ''); if (!p) return; const r = await resetPwd({ name: u.name, password: p }); flash(r.ok, r.ok ? '已重置密码 · 对方下次登录需自行改密' : r.msg) }
  const del = async (u) => { if (!window.confirm(`确认删除账号「${u.name}」？历史认领记录仍保留。`)) return; const r = await deleteUser({ name: u.name }); if (r.ok) { if (sel === u.name) setSel(null); load() } else flash(false, r.msg) }
  // V2.322 数据范围：模式由勾选**派生**（勾了任一团队码=只看指定团队），scopeOn 只是
  // "刚点了『指定』、还没勾到团队"的过渡态；切换选中账号时清掉，不入库
  const [scopeOn, setScopeOn] = useState(false)
  useEffect(() => { setScopeOn(false) }, [sel])
  const scopeClear = (list) => {              // 回「全部团队」＝清掉全部团队码
    setPd(p => { const n = { ...p }; list.forEach(c => { n[c.key] = false }); return n })
    setScopeOn(false); setDirty(true)
  }
  const togglePerm = (c) => {
    if (c.plan || !assignable.has(c.key)) return
    setPd(p => {
      const on = !p[c.key]
      const n = { ...p, [c.key]: on }
      // 取消菜单准入 → 它底下的动作跟着收回（业务方定）。后端保存时还会再兜一遍（_cascade_revoke），
      // 这里同步是为了屏幕上立刻就对得上——不然管理员会以为那些动作还留着。
      if (!on && c.tier === 'nav') caps.forEach(a => { if (a.gate === c.key) n[a.key] = false })
      // V2.323 二级：取消父动作 → 子动作跟着收（发送周报←周报查看、导出Excel←项目视图）
      if (!on) caps.forEach(a => { if (a.parent === c.key) n[a.key] = false })
      return n
    })
    setDirty(true)
  }
  const savePerms = async () => {
    const r = await setUserPerms({ name: cur.name, perms: pd })
    if (!r.ok) { flash(false, r.msg); return }
    setDirty(false); load()
    flash(true, r.cascadeRevoked?.length
      ? `已保存「${cur.name}」的权限；另有 ${r.cascadeRevoked.length} 项动作权限因无对应菜单准入被一并收回`
      : '已保存「' + cur.name + '」的权限')
  }

  const grantedCount = cur ? caps.filter(c => c.kind !== 'enter' && c.kind !== 'manage' && pd[c.key]).length : 0

  const catNote = (u) => `${postLabel(u.post) || '—'} · ${u.grp}${u.active ? '' : ' · 已禁用'}`
  const subTags = (u) => MANAGE_CAPS.filter(k => u.perms && u.perms[k]).map(k => MANAGE_WS_LABEL[k]).join('/')

  return (
    <div className="ua-root">
      <style>{CSS}</style>
      <div className="ua-h1">账号管理 · 权限中枢
        <span className="ua-tier" style={{ color: isSuper ? 'var(--brand2)' : 'var(--amber)' }}>{tierText}</span></div>
      <div className="ua-sub">{isSuper
        ? '一处发证，统管三个工作台的准入与板块权限。准入是总闸——开了才配下面的板块；敏感权限高亮、默认不给。'
        : '你是工作台子管理员：只能建/管自己工作台的账号与权限（删除账号请找主管理员）。'}
        {msg && <span className="ua-msg" style={{ color: msg.ok ? 'var(--green)' : 'var(--red)' }}>　{msg.t}</span>}</div>
      {bpDrift && !bpDrift.ok && (
        <div style={{ margin: '8px 0', padding: '8px 12px', borderRadius: 6, background: '#fff3f0',
                      border: '1px solid #ffccc7', color: '#a8071a', fontSize: 13, lineHeight: 1.7 }}>
          <b>⚠ BP 权限码表未对齐</b>（BP v{bpDrift.bpVersion}）——码表真相源是 BP 的 <code>auth.py::PERMISSIONS</code>，
          此处 <code>CAP_META</code> 需与之一致；不一致不会报错，只会让某些功能<b>对某些人静默不可用</b>。
          {bpDrift.missing?.length > 0 && <div>· <b>BP 有、这里没登记 {bpDrift.missing.length} 个</b>（下方勾不到 → 除主管理员外无人能被授予）：
            {bpDrift.missing.map(m => m.code).join('、')}</div>}
          {bpDrift.extra?.length > 0 && <div>· <b>这里有、BP 不认 {bpDrift.extra.length} 个</b>（死码，勾了也没用）：{bpDrift.extra.join('、')}</div>}
          {bpDrift.superSensitiveGap?.length > 0 && <div>· <b>主管理员透传串漏了 {bpDrift.superSensitiveGap.length} 个敏感码</b>
            （<code>db.py::BP_SENSITIVE_CODES</code> 要补，否则主管理员在 BP 侧会失权）：{bpDrift.superSensitiveGap.join('、')}</div>}
        </div>
      )}
      <div className="ua-legend">
        <div className="ua-lg"><span className="ua-sw0 on"></span>常规权限</div>
        <div className="ua-lg"><span className="ua-sw0 sens"></span>敏感（🔒系统固定 / ⤴经理加严·默认不给）</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10 }}>
          {isSuper && <div className="ua-filter" onClick={openTplDrawer}
            title="配每个岗位「按岗位一键套用」时勾哪些权限">📋 岗位模板设置</div>}
          {isSuper && <div className="ua-filter" onClick={() => setShowLevels(true)}>⚙ 敏感级别设置</div>}
          <div className={'ua-filter' + (onlySens ? ' on' : '')} onClick={() => setOnlySens(v => !v)}>⚠ 只看敏感权限</div>
        </div>
      </div>

      <div className="ua-cols">
        {/* 左：账号列表（5类分区·可折叠） */}
        <div className="ua-left">
          <div className="ua-card">
            <div className="ua-ct">账号（{rows.length}）
              <span className="ua-lk" onClick={() => { const o = !showNew; setShowNew(o); if (o && grpOpts.length && !grpOpts.includes(ng)) setNg(grpOpts[0]) }}>{showNew ? '收起' : '+ 新建'}</span></div>

            {showNew && (
              <div className="ua-newf">
                <input className="ua-inp" placeholder="姓名（= 登录名）" value={nn} onChange={e => setNn(e.target.value)} />
                {/* 岗位改成下拉选（V2.52 D11）：以前是手打自由文本，"总账"/"总账会计"/"总帐岗" 各写各的，
                    岗位一旦要驱动模板就全对不上号。名单在「系统设置 › 岗位名单」维护。 */}
                <select className="ua-inp" value={npost} onChange={e => setNpost(e.target.value)}>
                  <option value="">岗位（选填）</option>
                  {postList.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
                </select>
                <div style={{ display: 'flex', gap: 8 }}>
                  <select className="ua-inp" value={grpOpts.includes(ng) ? ng : (grpOpts[0] || '')} onChange={e => setNg(e.target.value)}>
                    {grpOpts.map(g => <option key={g}>{g}</option>)}</select>
                  {isSuper
                    ? <select className="ua-inp" value={nr} onChange={e => setNr(e.target.value)}><option value="normal">普通</option><option value="admin">管理员</option></select>
                    : <div className="ua-inp" style={{ display: 'flex', alignItems: 'center', color: 'var(--ink3)' }}>普通用户</div>}
                </div>
                <input className="ua-inp" type="text" placeholder="初始密码" value={np} onChange={e => setNp(e.target.value)} />
                <button className="ua-btn pri" style={{ height: 34 }} onClick={add}>新建账号</button>
                <div className="ua-hint">岗位只用于识别人，不影响权限；分组仍决定工作台归属与子管理员范围。</div>
              </div>
            )}

            <div className="ua-search"><span className="ic">🔍</span>
              <input className="ua-inp" placeholder="搜索 姓名 / 岗位 / 分组" value={q} onChange={e => setQ(e.target.value)} /></div>

            <div>
              {CATS.map(cat => {
                const us = byCat[cat.key] || []
                if (!us.length) return null
                const col = !!colCats[cat.key]
                return (
                  <div key={cat.key} className={'ua-cg' + (col ? ' col' : '')}>
                    <div className="ua-grp-h" onClick={() => setColCats(m => ({ ...m, [cat.key]: !m[cat.key] }))}>
                      <span className="ua-caret">▾</span><span className="ua-gd" style={{ background: cat.color }}></span>
                      {cat.label} <span className="ua-c">{us.length}</span>
                      {cat.note && <span className="ua-note">{cat.note}</span>}<span className="ua-ln"></span></div>
                    <div className="ua-grp-items">
                      {us.map(u => {
                        const c = categoryOf(u)
                        return (
                          <div key={u.id} className={'ua-item' + (sel === u.name ? ' sel' : '') + (c === 'subadmin' ? ' sub' : '') + (c === 'disabled' ? ' dis' : '')}
                            onClick={() => setSel(u.name)}>
                            <span className="ua-dot" style={{ background: u.active ? 'var(--green)' : 'var(--ink3)' }} />
                            <div style={{ minWidth: 0 }}>
                              <div className="ua-nm">{u.name}{u.name === me.name ? <span style={{ color: 'var(--ink3)', fontWeight: 400 }}>(我)</span> : null}
                                {c === 'super' && <span className="ua-tag pp">系统管理员</span>}
                                {c === 'subadmin' && <span className="ua-tag pp">子管理·{subTags(u)}</span>}
                                {c === 'external' && <span className="ua-tag ext">外部</span>}</div>
                              <div className="ua-meta">{catNote(u)}</div>
                            </div>
                            {/* V2.149：子管理员在管辖工作台内恒全权，逐点计数会误导 → 显示「管辖全权」 */}
                            {u.role !== 'admin' && !MANAGE_CAPS.some(k => u.perms && u.perms[k]) &&
                              <span className={'ua-badge' + (c === 'external' ? ' ext' : '')}>{caps.filter(x => x.kind !== 'enter' && x.kind !== 'manage' && u.perms && u.perms[x.key]).length}/{totalCap}</span>}
                            {u.role !== 'admin' && MANAGE_CAPS.some(k => u.perms && u.perms[k]) &&
                              <span className="ua-badge">管辖全权</span>}
                            {u.role === 'admin' && <span className="ua-badge">全权</span>}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        {/* 右：选中账号的三工作台权限 */}
        <div className="ua-right">
          {!cur ? (
            <div className="ua-empty">← 选择左侧一个账号，查看并配置其三工作台权限</div>
          ) : (
            <>
              {/* V2.143（业务方定）：岗位并进名字行＝「李志鹏 · 成本会计」，套用按钮挪进右侧动作区。
                  原来的独立岗位行取消——它把"人是谁"劈成了两行。 */}
              <div className="ua-dhead">
                <div>
                  <div className="ua-dname">{cur.name}
                    <span style={{ color: 'var(--ink3)', fontWeight: 400 }}>·</span>
                    {/* 岗位内联下拉：看着是文字、点开能改（无边框、hover 才显出是控件） */}
                    <select className="ua-postsel" value={postKnown(cur.post) ? cur.post : ''} disabled={postBusy}
                      onChange={e => changePost(cur, e.target.value, false)} title="岗位（点击可改）">
                      <option value="">未设岗位</option>
                      {postList.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
                    </select>
                    {cur.post && !postKnown(cur.post) &&
                      <span className="ua-tag ext" title="这是改造前手打的岗位，对不上现在的名单——请在下拉里选一个">
                        待认领：{cur.post}</span>}
                    {cur.grp === '外部协作' && <span className="ua-tag ext">外部</span>}</div>
                  <div className="ua-dmeta">分组：{cur.grp} · {cur.role === 'admin' ? '管理员' : '普通'} · {cur.active ? '启用中' : '已禁用'} · 创建 {cur.created_at}</div>
                </div>
                <div className="ua-acts">
                  {cur.role !== 'admin' && postKnown(cur.post) &&
                    <button className="ua-btn" disabled={postBusy} onClick={() => setTplOpen(o => !o)}
                      title="按该岗位模板把该勾的权限点勾上（只加不减；敏感点永远不会被模板给出）">
                      按岗位一键套用权限{tplOpen ? ' ▴' : ''}</button>}
                  <button className="ua-btn" onClick={() => toggleActive(cur)}>{cur.active ? '禁用' : '启用'}</button>
                  <button className="ua-btn" onClick={() => reset(cur)}>重置密码</button>
                  {cur.name !== me.name && isSuper && <button className="ua-btn red" onClick={() => del(cur)}>删除</button>}
                </div>
              </div>

              {/* 套用预览（V2.143）：点按钮先看会套什么、再确认——不是闭着眼睛套。
                  套用只勾上、不取消——静默收回管理员手工开过的点比少给更危险。 */}
              {tplOpen && cur.role !== 'admin' && postKnown(cur.post) && (() => {
                const pv = tplPreview(cur.post)
                return (
                  <div style={{ margin: '10px 0 2px', padding: '12px 14px', border: '1px solid var(--accent)', borderRadius: 12, fontSize: 12.5, lineHeight: 1.9 }}>
                    <b>按「{postLabel(cur.post)}」模板将勾上：</b>
                    {!pv ? <span style={{ color: 'var(--ink3)' }}>（该岗位还没配模板——到「系统设置 › 岗位名单」配）</span> : <>
                      {pv.secs.length > 0 && <div>· 整个板块的菜单准入：{pv.secs.join('、')}</div>}
                      {pv.mods.length > 0 && <div>· 另加单个菜单：{pv.mods.join('、')}</div>}
                      {pv.acts.length > 0 && <div>· 动作权限：{pv.acts.join('、')}</div>}
                      <div style={{ color: 'var(--ink3)' }}>只加不减（不会收回已有的点）；敏感权限永远不会被模板给出，需手工显式授予。</div>
                    </>}
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      <button className="ua-btn" disabled={postBusy || !pv} onClick={() => changePost(cur, cur.post, true)}>
                        {postBusy ? '套用中…' : '确认套用'}</button>
                      <button className="ua-btn" onClick={() => setTplOpen(false)}>取消</button>
                    </div>
                  </div>
                )
              })()}

              {cur.role === 'admin' ? (
                <div className="ua-empty" style={{ marginTop: 16, padding: '30px 20px' }}>管理员拥有全部权限，无需逐项配置。</div>
              ) : wsList.map(w => {
                const meta = WS_META[w.ws]
                const entered = w.enter && !!pd[w.enter.key]
                const enterAssignable = w.enter && (isSuper || assignable.has(w.enter.key))
                const manageAssignable = w.manage && isSuper && assignable.has(w.manage.key)
                // V2.149：他是本工作台的子管理员 → 在此工作台内【恒全权（系统设置除外）】，
                // 逐点勾选与实际不符会误导（后端 user_can 不看这些勾）——整块换成说明卡。
                const isSubOfThis = w.manage && !!pd[w.manage.key]
                return (
                  <div key={w.ws} className={'ua-ws ' + meta.cls}>
                    <div className="ua-whead">
                      <div className="ua-wic">{meta.icon}</div>
                      <div><div className="ua-wname">{w.enter ? w.enter.label.replace('进入', '') : (meta.name || w.ws)}</div><div className="ua-wen">{meta.en}</div></div>
                      <div className="ua-wctrls">
                        {w.enter && !isSubOfThis && <div className={'ua-sw' + (entered ? ' on' : '') + (enterAssignable ? '' : ' dis')}
                          onClick={() => enterAssignable && togglePerm(w.enter)}><span>进入本工作台</span><span className="tk"></span></div>}
                        {w.manage && <div className={'ua-sw sens' + (pd[w.manage.key] ? ' on' : '') + (manageAssignable ? '' : ' dis')}
                          onClick={() => manageAssignable && togglePerm(w.manage)}><span>管理本工作台账号</span><span className="ua-stag">敏</span><span className="tk"></span></div>}
                      </div>
                    </div>
                    {isSubOfThis ? (
                      <div className="ua-whint">
                        👑 <b>本工作台子管理员＝默认拥有本工作台全部权限（含敏感点；「系统设置」除外，需另行显式授予）。</b>
                        无需逐项配置；要收权请由主管理员关闭上方「管理本工作台账号」（撤销任命）。
                      </div>
                    ) : !entered ? (
                      <div className="ua-whint">🔒 开启「进入本工作台」后，在此配置菜单与动作权限</div>
                    ) : w.navGroups.length === 0 && w.acts.length === 0 ? (
                      <div className="ua-wbody"><div className="ua-empty">工具规划中 · 待开放板块</div></div>
                    ) : (() => {
                      const { heaps, generic, scopeCaps } = actsFor(w)
                      const noActs = navNoActs(w)
                      const navCount = w.navGroups.flatMap(g => g.items).filter(c => pd[c.key]).length
                      // V2.322 数据范围控件（驾驶舱按销售团队分权）：默认=全部团队（一个码不勾）；
                      // 「只看指定团队」勾团队码。团队清单是动态 caps（BP registry），新增团队自动出现。
                      const teamName = (c) => (c.label || c.key).replace(/^数据域·只看\s*/, '')
                      const scopeChosen = scopeCaps.filter(c => pd[c.key])
                      const scopeRestrict = scopeChosen.length > 0 || scopeOn
                      const scopeLocked = scopeCaps.some(c => !assignable.has(c.key))
                      const scopeBlock = scopeCaps.length > 0 ? (
                        <div className="ua-scope">
                          <div className="ua-scope-t">数据范围 · 按销售团队<span className="ua-mini">🔒敏</span></div>
                          <div className="ua-scope-m">
                            <label className={scopeLocked ? 'dis' : ''}>
                              <input type="radio" checked={!scopeRestrict} disabled={scopeLocked}
                                onChange={() => scopeClear(scopeCaps)} />全部团队（默认）
                            </label>
                            <label className={scopeLocked ? 'dis' : ''}>
                              <input type="radio" checked={scopeRestrict} disabled={scopeLocked}
                                onChange={() => setScopeOn(true)} />只看指定团队
                            </label>
                          </div>
                          {scopeRestrict && (
                            <div className="ua-scope-teams">
                              {scopeCaps.map(c => {
                                const locked = !assignable.has(c.key)
                                return (
                                  <div key={c.key}
                                    className={'ua-chip sens' + (pd[c.key] ? ' on' : '') + (locked ? ' lock' : '')}
                                    onClick={() => !locked && togglePerm(c)}>
                                    <span className="bx">✓</span>{teamName(c)}
                                  </div>
                                )
                              })}
                            </div>
                          )}
                          <div className="ua-scope-hint">
                            {scopeRestrict
                              ? (scopeChosen.length
                                ? `驾驶舱只显示：${scopeChosen.map(teamName).join('、')}（目标与达成率同步只算这些团队；未归属/周报仅全视野账号可见）`
                                : '再勾至少一个团队；一个不勾，保存后仍是全部团队')
                              : '全公司视野。'}
                            团队清单来自 BP 基础数据「销售团队」——新增团队自动出现在这里，无需登记代码。
                          </div>
                        </div>
                      ) : null
                      return (
                        <div className="ua-wbody ua-t2">
                          {/* ② 看得见哪些菜单 */}
                          <div className="ua-tcol">
                            <div className="ua-th"><span className="no">2</span>看得见哪些菜单</div>
                            <div className="ua-ths">准入点 · 勾了哪个，右边就出哪个的动作</div>
                            {w.navGroups.map(g => {
                              const items = onlySens ? g.items.filter(c => isSens(c)) : g.items
                              if (!items.length) return null
                              return (
                                <div key={g.name}>
                                  <div className="ua-gt">{g.name}<span className="l"></span></div>
                                  <div className="ua-chips">
                                    {items.map(c => <Chip key={c.key} c={c} pd={pd} isSens={isSens} escSet={escSet}
                                      assignable={assignable} onToggle={togglePerm} />)}
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                          {/* ③ 进去能干什么 —— 只出进得去的 */}
                          <div className="ua-tcol act">
                            <div className="ua-th"><span className="no">3</span>进去能干什么</div>
                            <div className="ua-ths">只列他进得去的 {navCount} 个菜单 · 按菜单归堆</div>
                            {!heaps.length && !generic.length && (
                              <div className="ua-empty" style={{ padding: '22px 12px', fontSize: 12 }}>
                                {navCount ? '这些菜单没有单独的动作权限——能进就能用' : '← 先在左边勾选他能进的菜单'}
                              </div>
                            )}
                            {heaps.map(h => (
                              <div key={h.gate} className="ua-heap">
                                <div className="ua-hh"><span className="dot"></span>{h.name}
                                  {h.sec && <span className="from">{h.sec}</span>}</div>
                                {h.gate === 'bp:board:budgetCockpit' && scopeBlock}
                                <div className="ua-chips hb">
                                  {h.items.filter(c => !c.parent).map(c => <Chip key={c.key} c={c} pd={pd} isSens={isSens} escSet={escSet}
                                    assignable={assignable} onToggle={togglePerm} />)}
                                </div>
                                {/* V2.323 二级动作（c.parent，注册表驱动）：勾了父项才出子项——
                                    「项目视图→导出 Excel」「周报查看→发送周报」。父项没勾但子项仍带着授权时
                                    也要露出来并提示，不能藏成幽灵权限（保存时后端会级联收回）。 */}
                                {h.items.filter(p => !p.parent && h.items.some(k => k.parent === p.key)
                                  && (pd[p.key] || h.items.some(k => k.parent === p.key && pd[k.key]))).map(p => (
                                    <div key={p.key + ':kids'} className="ua-subrow">
                                      <span className="ua-subhint">↳ {(p.label || '').replace(/^驾驶舱·/, '').replace(/（[^）]*）/g, '')} 附加</span>
                                      <div className="ua-chips">
                                        {h.items.filter(k => k.parent === p.key).map(k => <Chip key={k.key} c={k} pd={pd}
                                          isSens={isSens} escSet={escSet} assignable={assignable} onToggle={togglePerm} />)}
                                      </div>
                                      {!pd[p.key] && <span className="ua-subwarn">父项未勾——保存时子项会一并收回</span>}
                                    </div>
                                  ))}
                              </div>
                            ))}
                            {/* 驾驶舱堆因筛选没渲染、但数据范围仍需可配（如「只看敏感」视图下无敏感动作时）*/}
                            {scopeBlock && !heaps.some(h => h.gate === 'bp:board:budgetCockpit') && (
                              <div className="ua-heap">
                                <div className="ua-hh"><span className="dot"></span>驾驶舱看板
                                  <span className="from">销售预算</span></div>
                                {scopeBlock}
                              </div>
                            )}
                            {generic.length > 0 && (
                              <div className="ua-heap">
                                <div className="ua-hh"><span className="dot"></span>不限菜单 · 全台通用</div>
                                <div className="ua-chips hb">
                                  {generic.map(c => <Chip key={c.key} c={c} pd={pd} isSens={isSens} escSet={escSet}
                                    assignable={assignable} onToggle={togglePerm} />)}
                                </div>
                              </div>
                            )}
                            {noActs.length > 0 && (
                              <div className="ua-heap">
                                <div className="ua-hh"><span className="dot"></span>{noActs.join(' · ')}</div>
                                <div className="ua-none">没有单独的动作权限——能进就能用</div>
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })()}
                  </div>
                )
              })}
            </>
          )}
        </div>
      </div>

      {cur && cur.role !== 'admin' && (
        <div className="ua-savebar">
          <div className="sum">正在编辑 <b>{cur.name}</b> 的权限　·　已授 <b>{grantedCount}</b> 项板块权限
            {dirty && <span style={{ color: 'var(--amber)' }}>　● 有未保存的改动</span>}</div>
          <button className="ua-btn pri" style={{ marginLeft: 'auto', height: 36, padding: '0 20px' }} onClick={savePerms} disabled={!dirty}>保存权限</button>
        </div>
      )}

      {/* 岗位模板设置抽屉（V2.144）：配「按岗位一键套用」时每个岗位勾哪些权限 */}
      {showTpl && isSuper && tplDraft && (
        <>
          <div className="ua-mask" onClick={() => setShowTpl(false)}></div>
          <div className="ua-drawer">
            <h3>📋 岗位模板设置<span className="x" onClick={() => setShowTpl(false)}>✕</span></h3>
            <div className="ua-dsub">这里配的是"按岗位一键套用时勾哪些权限"（模板），不是直接给谁授权。
              套用永远<b>只加不减</b>；<b>敏感权限进不了模板</b>（后端强制过滤），故下面不列敏感点。</div>
            {/* 岗位名单维护（V2.146 完整版，业务方定「放到门户」）：chips 选岗位；
                选中的可就地改名/删除；行尾新增。原核算系统设置里的维护区块已撤。 */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '12px 0', alignItems: 'center' }}>
              {postList.map(p => (
                <div key={p.key} className={'ua-filter' + (tplSel === p.key ? ' on' : '')}
                  onClick={() => setTplSel(p.key)}>{p.label}</div>
              ))}
              <input value={npNew} onChange={e => setNpNew(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPostInline()}
                placeholder="＋新增岗位，如 出纳" maxLength={10}
                style={{ height: 26, width: 132, borderRadius: 13, border: '1px solid var(--line2)',
                  background: 'transparent', color: 'var(--ink)', padding: '0 10px', fontSize: 12, fontFamily: 'inherit' }} />
              {npNew.trim() && <button className="ua-btn" style={{ height: 26, padding: '0 10px', fontSize: 12 }}
                onClick={addPostInline}>添加</button>}
            </div>
            {tplSel && (() => {
              const p = postList.find(x => x.key === tplSel)
              if (!p) return null
              return (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '-4px 0 8px', fontSize: 12 }}>
                  <span style={{ color: 'var(--ink3)' }}>选中岗位：</span>
                  {/* 改名就地编辑：失焦/回车提交。改名不丢绑定（key 稳定，账号与模板都跟 key 走） */}
                  <input defaultValue={p.label} key={p.key} maxLength={10}
                    onBlur={e => renamePostInline(p.key, e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && e.target.blur()}
                    title={'标识 ' + p.key + '（改名不影响绑定）'}
                    style={{ height: 26, width: 110, borderRadius: 7, border: '1px solid var(--line2)',
                      background: 'transparent', color: 'var(--ink)', padding: '0 8px', fontSize: 12, fontFamily: 'inherit' }} />
                  <button className="ua-btn red" style={{ height: 26, padding: '0 10px', fontSize: 12 }}
                    onClick={() => delPostInline(p.key)}>删除该岗位</button>
                  <span style={{ color: 'var(--ink3)', fontSize: 11 }}>删除会自动从各模块挂载与模板里摘掉；已选此岗位的账号变「待认领」</span>
                </div>
              )
            })()}
            {tplSel && (() => {
              const d = tplDraft[tplSel] || { secs: [], mods: [], acts: [] }
              const secs = (navDef?.sections || [])
              // 单个菜单：group_only 没准入点不列；already 整板块给了的，单点勾选多余但无害，照列（看得清就行）
              const mods = (navDef?.modules || []).filter(m => !m.group_only)
              const acts = caps.filter(c => c.tier === 'act' && !c.sensitive && c.ws === 'accounting')
              const row = (checked, label, sub, onClick) => (
                <div key={label + sub} className="ua-lvl" style={{ cursor: 'pointer' }} onClick={onClick}>
                  <div className="n">{label}<small>{sub}</small></div>
                  <div className={'ua-msw' + (checked ? ' on' : '')}></div>
                </div>
              )
              return (<>
                <div className="ua-lvlg">整个板块的菜单准入（该板块现有及将来新增的菜单都给）</div>
                {secs.map(s => row(d.secs.includes(s.key), s.label, '', () => tplToggle(tplSel, 'secs', s.key)))}
                <div className="ua-lvlg">另加单个菜单准入</div>
                {mods.map(m => row(d.mods.includes(m.key), m.label,
                  (secs.find(s => s.key === m.sec) || {}).label || '',
                  () => tplToggle(tplSel, 'mods', m.key)))}
                <div className="ua-lvlg">动作权限（仅非敏感——敏感点必须手工显式授予）</div>
                {acts.map(c => row(d.acts.includes(c.key), c.label, c.group || '', () => tplToggle(tplSel, 'acts', c.key)))}
              </>)
            })()}
            <div style={{ display: 'flex', gap: 8, marginTop: 14, position: 'sticky', bottom: 0,
              background: 'var(--panel)', padding: '10px 0' }}>
              <button className="ua-btn" disabled={tplSaving} onClick={saveTpl}>
                {tplSaving ? '保存中…' : '保存全部岗位模板'}</button>
              <button className="ua-btn" onClick={() => setShowTpl(false)}>取消</button>
            </div>
          </div>
        </>
      )}

      {showLevels && isSuper && (
        <>
          <div className="ua-mask" onClick={() => setShowLevels(false)}></div>
          <div className="ua-drawer">
            <h3>⚙ 敏感级别设置<span className="x" onClick={() => setShowLevels(false)}>✕</span></h3>
            <div className="ua-dsub">这里管的是"哪些权限算敏感"（分类，全平台生效），不是"给谁"。给谁在左边逐人勾。</div>
            <div className="ua-ratchet">棘轮规则：🔒 系统固定项是代码定死的地板，改不了；你只能把常规权限<b>单向升为敏感</b>（更严），升上去的可再降回常规，但<b>永远不能把系统固定项降级</b>。</div>

            <div className="ua-lvlg">🔒 系统固定 · 敏感（不可更改）</div>
            {caps.filter(c => c.sensitive).map(c => (
              <div key={c.key} className="ua-lvl fixed">
                <div className="n">{c.label}<small>{WS_LABEL_SHORT[c.ws]}{c.group ? ' · ' + c.group : (c.kind === 'manage' ? ' · 任命权' : '')}</small></div>
                <div className="lock">🔒 固定</div>
              </div>
            ))}

            <div className="ua-lvlg">⤴ 常规权限 · 可由你单向升为敏感</div>
            {caps.filter(c => c.kind !== 'enter' && c.kind !== 'manage' && !c.sensitive).map(c => (
              <div key={c.key} className="ua-lvl">
                <div className="n">{c.label}<small>{WS_LABEL_SHORT[c.ws]}{c.group ? ' · ' + c.group : ''}</small></div>
                <div className={'ua-msw' + (escSet.has(c.key) ? ' on' : '')} onClick={() => toggleEsc(c, !escSet.has(c.key))}></div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
