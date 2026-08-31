// [Change Log] Date:2026-08-20 Author:Claude/Reginald Zeng Version:V2.329（上版 V2.328）
// V2.329：状态改四档并与工作台真实进度挂钩（业务方定）：已上线(ok·闪烁绿灯) / 人工并行(par·琥珀) /
//   开发中(beta·蓝) / 敬请期待(soon·灰)。DB 键沿用 ok/beta/soon 不迁移，新增 par。
// V2.327：验收反馈——工具目录里「能进入的」卡片高亮（紫描边+紫底晕+紫图标），进不去的图标转灰，
//   目录标题旁加一行状态图例（紫框可进入/带锁需权限/半透明未上线）。
// V2.326：验收反馈——工具卡标签行回到两行式（通用一行/AI 一行，带分类小标签）。
//   单行合并版在 1920 屏 zoom=1.23 下会把"刚好放下"的行画出卡片右缘（缩放取整），分行后余量充足。
// V2.325：门户首页两层结构改版（设计稿 03_Source_Materials/00_平台通用/设计稿_门户Hub_V2_两层结构_20260820.html 落地）——
//   上层「我的工作台」＝按权限渲染的启动器（我的/可进入/无权限锁定/规划中四态）；
//   下层「工具目录」＝可搜索（顶栏真输入框+Ctrl K）、可筛选（组别/状态）的紧凑工具卡；
//   无权限组的工具可浏览不可进（锁标记），真闸仍在服务端（/bp/ 反代 auth_request，V2.13/V2.324 语义不变）。
// V2.324：「模型配置」门平台级权限点 model_config——主管理员恒有，其他人默认不见（可在账号管理授予）。
// 工具卡片仍由「门户管理」维护、经 /api/portal/tools 读取；「常用」标签当前＝进入对应工作台
//   （BP/核算暂不支持按 URL 落到指定模块，点亮模块级直达记在 V2.325 台账遗留）。
import React, { useState, useEffect, useRef } from 'react'
import { apiLogout, getPortalTools, getLlmHubStatus } from '../api.js'
import UserAdmin from './UserAdmin.jsx'
import PortalAdmin from './PortalAdmin.jsx'
import ModelConfig from './ModelConfig.jsx'

const LOGO_B64 = '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'

// 工作台泳道=结构（标题/准入）；每个泳道里的工具卡片由「门户管理」维护、经接口读取。
// status 四档（V2.329）: ok=已上线(闪烁绿灯) / par=人工并行 / beta=开发中 / soon=敬请期待
const LANES = [
  { key: 'accounting', title: '财务核算组', en: 'ACCOUNTING', enterCap: 'enter_accounting' },
  { key: 'bp', title: '财务分析组 · BP', en: 'ANALYSIS / BP', enterCap: 'enter_bp' },
  // 法务：占位——工作台未建（敏感，待与法务部确认）
  { key: 'legal', title: '法务部', en: 'LEGAL', enterCap: 'enter_legal', blank: true },
]
const ST = { ok: '已上线', par: '人工并行', beta: '开发中', soon: '敬请期待' }
const LANE_SHORT = { accounting: '核算', bp: 'BP', legal: '法务' }
// 「我的工作台」高亮：账号分组 ↔ 泳道
const GRP_OF_LANE = { accounting: '核算组', bp: 'BP组', legal: '法务' }
// 启动器「常用」标签（当前动作＝进入对应工作台；模块级直达待 BP/核算支持 URL 落页后点亮，见台账 V2.325 遗留）
const QUICK = { accounting: ['银行稽核', '月结看板', '成本台账', '报表导出'], bp: ['驾驶舱', '销售预算', '管报分析', '定价测算'] }

// ── 上线开关 ──────────────────────────────────────────────
// BP 工作台已经反向代理接通(/bp/)并验收通过(2026-07-05, V2.13)→ 已点亮为 true：
// 门户「进入BP工作台」按钮 = 整页跳转到 /bp/（有权限的 BP 组账号/管理员可进）。
const BP_LIVE = true
const GROUP_URL = { bp: '/bp/' }   // 各组外部工作台地址（整页跳转）

const CSS = `
.pt-root{--bg:#14101F;--panel:#221A3A;--line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.14);
  --ink:#EDEAF6;--ink2:#B4ABD4;--ink3:#8B84AD;--brand:#7C5CFF;--brand2:#9B7BFF;--ai:#3FE0C8;
  --green:#34D399;--amber:#FBBF24;--gray:#8A82A8;
  --card:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012));
  --tr:180ms cubic-bezier(.2,.7,.3,1);
  position:fixed;inset:0;overflow:auto;z-index:40;color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",Roboto,sans-serif;
  background:
    radial-gradient(900px 520px at 82% -12%,rgba(124,92,255,.16),transparent 62%),
    radial-gradient(760px 460px at -6% 108%,rgba(63,224,200,.07),transparent 55%),
    var(--bg);
  -webkit-font-smoothing:antialiased}
.pt-root *{box-sizing:border-box}
.pt-stars{position:fixed;inset:0;pointer-events:none;opacity:.5;
  background-image:
    radial-gradient(1px 1px at 12% 18%,rgba(255,255,255,.25),transparent 100%),
    radial-gradient(1px 1px at 34% 6%,rgba(255,255,255,.18),transparent 100%),
    radial-gradient(1.5px 1.5px at 58% 14%,rgba(155,123,255,.35),transparent 100%),
    radial-gradient(1px 1px at 76% 4%,rgba(255,255,255,.22),transparent 100%),
    radial-gradient(1px 1px at 90% 22%,rgba(143,233,255,.28),transparent 100%),
    radial-gradient(1px 1px at 22% 30%,rgba(255,255,255,.12),transparent 100%)}
.pt-root button{font-family:inherit;border:0;background:none;color:inherit;cursor:pointer}
.pt-root :focus-visible{outline:2px solid var(--brand2);outline-offset:2px;border-radius:8px}
@keyframes ptPulse{0%,100%{opacity:1}50%{opacity:.3}}
@media (prefers-reduced-motion:reduce){.pt-root *{transition:none!important;animation:none!important}}
/* ── 顶栏 ── */
.pt-top{position:relative;display:flex;align-items:center;gap:14px;padding:12px max(30px,calc((100% - 1460px)/2 + 20px));
  border-bottom:1px solid var(--line);background:rgba(20,16,31,.72)}
.pt-top img{height:34px}.pt-nm{font-weight:800;font-size:15px;letter-spacing:.3px;white-space:nowrap}
.pt-nav{display:flex;gap:26px;margin-left:20px;font-size:13.5px;color:var(--ink3)}
.pt-nav span{cursor:pointer;position:relative;padding:8px 0;transition:color var(--tr)}
.pt-nav span:hover{color:var(--ink2)}
.pt-nav span.on{color:var(--ink);font-weight:700}
.pt-nav span.on::after{content:'';position:absolute;left:0;right:0;bottom:-13px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,var(--brand),var(--brand2))}
.pt-right{margin-left:auto;display:flex;align-items:center;gap:14px}
.pt-search{display:flex;align-items:center;gap:8px;width:250px;padding:8px 12px;border-radius:10px;
  background:rgba(255,255,255,.05);border:1px solid var(--line2);transition:border-color var(--tr),box-shadow var(--tr)}
.pt-search:focus-within{border-color:rgba(124,92,255,.55);box-shadow:0 0 0 3px rgba(124,92,255,.15)}
.pt-search svg{flex:none;color:var(--ink3)}
.pt-search input{flex:1;min-width:0;border:0;background:none;outline:none;color:var(--ink);font-size:12.5px;font-family:inherit}
.pt-search input::placeholder{color:var(--ink3)}
.pt-search kbd{flex:none;font-family:inherit;font-size:10px;color:var(--ink3);border:1px solid var(--line2);border-radius:5px;padding:1px 5px}
.pt-who{display:flex;align-items:center;gap:9px}
.pt-av{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,var(--brand),var(--brand2));display:grid;place-items:center;font-size:13px;font-weight:700;color:#fff}
.pt-info{font-size:12px;line-height:1.35}.pt-info b{font-weight:700}.pt-info span{color:var(--ink3)}
.pt-out{font-size:11.5px;color:var(--ink3);border:1px solid var(--line2);border-radius:8px;padding:6px 12px;transition:color var(--tr),border-color var(--tr)}
.pt-out:hover{color:var(--ink2);border-color:var(--ink3)}
/* ── 主容器 / Hero ── */
.pt-wrap{position:relative;max-width:1460px;margin:0 auto;padding:22px clamp(20px,3vw,34px) 46px}
.pt-hero{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px 24px;padding:4px 2px 20px}
.pt-hero h1{font-size:23px;font-weight:800;letter-spacing:.4px;margin:0}
.pt-hero h1 em{font-style:normal;background:linear-gradient(90deg,#8FE9FF,#9B7BFF);-webkit-background-clip:text;background-clip:text;color:transparent}
.pt-hero p{color:var(--ink2);font-size:12.5px;margin:7px 0 0}
.pt-airdy{display:flex;align-items:center;gap:12px;flex:none;padding:11px 16px;border-radius:13px;cursor:pointer;
  background:linear-gradient(180deg,rgba(63,224,200,.10),rgba(63,224,200,.03));
  border:1px solid rgba(63,224,200,.32);box-shadow:0 0 26px rgba(63,224,200,.10);transition:filter var(--tr)}
.pt-airdy:hover{filter:brightness(1.12)}
.pt-airdy .d{width:9px;height:9px;border-radius:50%;background:var(--ai);box-shadow:0 0 10px var(--ai),0 0 4px var(--ai)}
.pt-airdy .t b{font-size:13px;font-weight:800;display:block;color:#CFFDF6}
.pt-airdy .t span{font-size:10.5px;color:var(--ink3);letter-spacing:.5px}
.pt-airdy .v{margin-left:4px;padding-left:13px;border-left:1px solid rgba(255,255,255,.12);font-size:11px;color:var(--ink3)}
.pt-airdy.wait{background:linear-gradient(180deg,rgba(251,191,36,.10),rgba(251,191,36,.03));border-color:rgba(251,191,36,.34);box-shadow:0 0 26px rgba(251,191,36,.09)}
.pt-airdy.wait .d{background:var(--amber);box-shadow:0 0 10px var(--amber),0 0 4px var(--amber)}
.pt-airdy.wait .t b{color:#FBE8B0}
.pt-airdy.ro{cursor:default}
.pt-airdy.ro:hover{filter:none}
/* ── 区块标题 ── */
.pt-sec{display:flex;align-items:baseline;gap:12px;margin:6px 0 12px}
.pt-sec h2{font-size:15px;font-weight:800;letter-spacing:.5px;display:flex;align-items:center;gap:8px;margin:0}
.pt-sec h2 i{width:4px;height:14px;border-radius:2px;background:linear-gradient(180deg,var(--brand),var(--brand2));display:inline-block}
.pt-sec .hint{font-size:11.5px;color:var(--ink3)}
/* ── 我的工作台（启动器）── */
.pt-mine{display:grid;grid-template-columns:1fr 1fr .72fr;gap:16px;margin-bottom:30px}
.pt-mcard{position:relative;border-radius:16px;padding:18px 20px 16px;background:var(--card);border:1px solid var(--line);
  transition:transform var(--tr),border-color var(--tr),box-shadow var(--tr)}
.pt-mcard:hover{transform:translateY(-2px);border-color:rgba(124,92,255,.4);box-shadow:0 14px 34px rgba(90,60,200,.16)}
.pt-mcard.me{border-color:rgba(124,92,255,.5);
  background:linear-gradient(180deg,rgba(124,92,255,.09),rgba(124,92,255,.02));
  box-shadow:0 0 0 1px rgba(124,92,255,.18),0 18px 44px rgba(90,60,200,.14)}
.pt-mcard.ghost{border-style:dashed;background:rgba(255,255,255,.015)}
.pt-mcard.ghost:hover{transform:none;box-shadow:none;border-color:var(--line2)}
.pt-mcard.ghost .pt-mtt{color:var(--ink2)}
.pt-mtop{display:flex;align-items:center;justify-content:space-between;gap:10px}
.pt-mtt{font-size:16px;font-weight:800;display:flex;align-items:center;gap:9px}
.pt-tag{font-size:9.5px;font-weight:700;letter-spacing:.5px;padding:2.5px 9px;border-radius:999px;border:1px solid var(--line2);color:var(--ink3)}
.pt-tag.me{color:#CFC4FF;border-color:rgba(124,92,255,.5);background:rgba(124,92,255,.16)}
.pt-men{font-size:9px;letter-spacing:2.5px;color:var(--ink3);margin-top:6px}
.pt-mst{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px;font-size:10.5px;color:var(--ink2)}
.pt-mst span{display:inline-flex;align-items:center;gap:5px}
.pt-mst i{width:6px;height:6px;border-radius:50%;display:inline-block}
.pt-mst .ok i{background:var(--green);box-shadow:0 0 6px var(--green);animation:ptPulse 1.6s ease-in-out infinite}
.pt-mst .par i{background:var(--amber);box-shadow:0 0 6px var(--amber)}
.pt-mst .beta i{background:#6FA8FF;box-shadow:0 0 6px rgba(111,168,255,.7)}
.pt-mst .soon i{background:var(--gray)}
.pt-quick{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:13px;padding-top:13px;border-top:1px dashed var(--line)}
.pt-quick .lbl{font-size:10px;color:var(--ink3);letter-spacing:1px;margin-right:2px}
.pt-qk{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--ink2);padding:4.5px 11px;border-radius:999px;
  background:rgba(255,255,255,.045);border:1px solid var(--line2);transition:all var(--tr)}
.pt-qk:hover{color:#fff;border-color:rgba(124,92,255,.55);background:rgba(124,92,255,.16)}
.pt-enter{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:700;letter-spacing:.5px;
  padding:9px 16px;border-radius:10px;white-space:nowrap;transition:filter var(--tr),background var(--tr)}
.pt-enter.pri{background:linear-gradient(180deg,#8B6BFF,#6A4CE6);color:#fff;box-shadow:0 8px 20px rgba(90,60,200,.35)}
.pt-enter.pri:hover{filter:brightness(1.1)}
.pt-enter.sec{color:#D9D2F5;border:1px solid rgba(124,92,255,.45);background:rgba(124,92,255,.08)}
.pt-enter.sec:hover{background:rgba(124,92,255,.18)}
.pt-glock{display:inline-flex;align-items:center;gap:6px;font-size:10.5px;color:var(--ink3);border:1px solid var(--line2);border-radius:999px;padding:3px 10px}
.pt-gnote{margin-top:14px;font-size:11px;color:var(--ink3);line-height:1.8}
/* ── 工具目录 ── */
.pt-cat-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px 18px;margin-bottom:14px}
.pt-cat-head .pt-sec{margin:0}
.pt-cnt{font-size:11px;color:var(--ink3)}
.pt-cnt b{color:var(--ink2);font-weight:700}
.pt-filters{margin-left:auto;display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px}
.pt-fl{font-size:10px;color:var(--ink3);letter-spacing:1px;margin:0 2px 0 8px}
.pt-chip{font-size:11px;color:var(--ink2);padding:4.5px 12px;border-radius:999px;border:1px solid var(--line2);
  background:rgba(255,255,255,.03);transition:all var(--tr)}
.pt-chip:hover{color:var(--ink);border-color:var(--ink3)}
.pt-chip.on{color:#E4DDFF;border-color:rgba(124,92,255,.6);background:rgba(124,92,255,.18);font-weight:700}
.pt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:14px}
.pt-tcard{position:relative;display:flex;gap:13px;padding:15px 16px;border-radius:14px;background:var(--card);
  border:1px solid var(--line);cursor:pointer;transition:transform var(--tr),border-color var(--tr),box-shadow var(--tr)}
.pt-tcard:hover{transform:translateY(-2px);border-color:rgba(124,92,255,.45);box-shadow:0 12px 30px rgba(90,60,200,.16)}
.pt-tcard:hover .pt-go{opacity:1;transform:translate(0,0)}
.pt-tcard.still{cursor:default}
.pt-tcard.still:hover{transform:none;border-color:var(--line);box-shadow:none}
.pt-tcard.dim{opacity:.55}
/* V2.327：能进入的卡高亮——紫描边+浅紫底晕；进不去的图标转灰，一眼分清「可用/可看/未上线」 */
.pt-tcard.can{border-color:rgba(124,92,255,.30);background:linear-gradient(180deg,rgba(124,92,255,.055),rgba(255,255,255,.012))}
.pt-tcard:not(.can) .pt-tic{background:rgba(255,255,255,.05);border-color:var(--line2);color:var(--ink3)}
.pt-tic{width:38px;height:38px;flex:none;border-radius:10px;display:grid;place-items:center;font-size:16px;color:#C3B4FF;
  background:rgba(124,92,255,.14);border:1px solid rgba(124,92,255,.24)}
.pt-tbody{flex:1;min-width:0}
.pt-tname{font-size:14px;font-weight:800;display:flex;align-items:center;gap:8px}
.pt-st{font-size:9.5px;font-weight:700;padding:1.5px 8px;border-radius:999px;letter-spacing:.3px;flex:none}
.pt-st.ok{color:#4ADE9E;background:rgba(52,211,153,.14)}
.pt-st.ok::before{content:'';display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--green);
  box-shadow:0 0 6px var(--green);margin-right:4px;vertical-align:1px;animation:ptPulse 1.6s ease-in-out infinite}
.pt-st.par{color:var(--amber);background:rgba(251,191,36,.13)}
.pt-st.beta{color:#8FBAFF;background:rgba(111,168,255,.14)}
.pt-st.soon{color:var(--gray);background:rgba(138,130,168,.13)}
.pt-lane-tag{margin-left:auto;flex:none;font-size:9.5px;color:var(--ink3);letter-spacing:1px}
.pt-tdesc{font-size:11.5px;color:var(--ink2);line-height:1.6;margin:5px 0 9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pt-caps{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.pt-caps + .pt-caps{margin-top:6px}
.pt-clbl{font-size:9.5px;color:var(--ink3);letter-spacing:1px;margin-right:2px;flex:none}
.pt-clbl.ai{color:var(--ai);font-weight:800}
.pt-tg{font-size:10.5px;color:var(--ink2);background:rgba(255,255,255,.05);border:1px solid var(--line2);border-radius:6px;padding:2px 8px}
.pt-tg.ai{color:#9BF5E6;font-weight:700;background:linear-gradient(180deg,rgba(63,224,200,.16),rgba(63,224,200,.06));border:1px solid rgba(63,224,200,.4)}
.pt-go{position:absolute;right:14px;bottom:13px;display:grid;place-items:center;width:26px;height:26px;border-radius:8px;
  color:var(--brand2);background:rgba(124,92,255,.14);border:1px solid rgba(124,92,255,.3);
  opacity:0;transform:translate(-4px,4px);transition:opacity var(--tr),transform var(--tr)}
.pt-lock-go{position:absolute;right:14px;bottom:13px;display:grid;place-items:center;width:26px;height:26px;border-radius:8px;
  color:var(--ink3);background:rgba(255,255,255,.05);border:1px solid var(--line2)}
.pt-empty{padding:44px 0;text-align:center;color:var(--ink3);font-size:12.5px;letter-spacing:.5px}
.pt-more{margin-top:34px;text-align:center;color:var(--ink3);font-size:11.5px;letter-spacing:1.5px}
@media(max-width:1180px){.pt-mine{grid-template-columns:1fr 1fr}.pt-mine .pt-mcard.blankcard{grid-column:1/-1}}
@media(max-width:840px){.pt-mine{grid-template-columns:1fr}.pt-nav{display:none}.pt-search{width:170px}.pt-hero h1{font-size:20px}}
/* 门户内占位页（使用指南） */
.pt-ph{padding:60px 34px;text-align:center}
.pt-ph .box{max-width:520px;margin:40px auto;padding:44px;border:1px dashed var(--line2);border-radius:18px;background:rgba(255,255,255,.02)}
.pt-ph h2{font-size:20px;font-weight:800;margin-bottom:10px}
.pt-ph p{font-size:13px;color:var(--ink2);line-height:1.9}
.pt-ph .back{margin-top:22px;display:inline-block;font-size:12.5px;color:var(--brand2);cursor:pointer}
`

const IcArrow = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><line x1="4" y1="12" x2="19" y2="12" /><polyline points="13 6 19 12 13 18" /></svg>
)
const IcGo = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><line x1="6" y1="18" x2="18" y2="6" /><polyline points="9 6 18 6 18 15" /></svg>
)
const IcLock = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
)
const IcSearch = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></svg>
)

export default function Portal({ user, onEnter }) {
  const [tab, setTab] = useState('home')
  const [dbTools, setDbTools] = useState([])
  const grp = user?.grp || ''
  const isAdmin = user?.role === 'admin'                 // 主管理员
  const canAdmin = !!(user?.can_admin || isAdmin)        // 能进账号管理：主管理员 或 工作台子管理员
  // V2.324：「模型配置」门平台级权限点 model_config（主管理员恒有；其他人默认无，由账号管理显式授予）。
  // 这里藏入口只是 UX——真闸在服务端：llm-hub 全部配置接口同码 403。
  const canModel = isAdmin || !!(user?.perms && user.perms.model_config)
  const loadTools = () => getPortalTools().then(r => setDbTools(r.tools || [])).catch(() => {})
  useEffect(() => { loadTools() }, [])
  const toolsOf = (key) => dbTools.filter(t => t.lane === key)
  // 准入=按权限：管理员放行全部；否则看该账号是否有对应「进入X工作台」权限
  const canEnter = (lane) => !lane.blank && (isAdmin || !!(user?.perms && user.perms[lane.enterCap]))
  const enterLane = (lane) => {
    if (!canEnter(lane)) return
    // 接通后：外部工作台整页跳转；否则走 App 内 zone
    if (BP_LIVE && GROUP_URL[lane.key]) { window.location.href = GROUP_URL[lane.key]; return }
    onEnter(lane.key)
  }
  // AI 模型就绪状态（V2.301）：由 /api/llm-hub/status 驱动——任一工作台已接入+可达+配了 key 即就绪。
  // 接口 60s 缓存 + 2.5s 探测超时，失败静默保持「待配置」，绝不拖慢门户首屏。
  const [aiStat, setAiStat] = useState(null)
  useEffect(() => { getLlmHubStatus().then(setAiStat).catch(() => {}) }, [])
  const aiReady = !!aiStat?.aiReady

  // 工具目录：搜索（顶栏输入框，Ctrl+K 唤起）+ 组别/状态筛选（V2.325）
  const [q, setQ] = useState('')
  const [fLane, setFLane] = useState('all')
  const [fSt, setFSt] = useState('all')
  const searchRef = useRef(null)
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); searchRef.current?.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  const qq = q.trim().toLowerCase()
  const hay = (t) => [t.name, t.desc, ...(t.gen || []), ...(t.ai || [])].join(' ').toLowerCase()
  const catTools = dbTools.filter(t =>
    (fLane === 'all' || t.lane === fLane) && (fSt === 'all' || t.status === fSt) && (!qq || hay(t).includes(qq)))

  const logout = async () => { try { await apiLogout() } catch (e) {} location.reload() }
  const initial = (user?.name || '·').slice(0, 1)

  const nav = ['工作台门户', '使用指南', ...(canModel ? ['模型配置'] : [])]
  // 账号管理：主管理员+子管理员都可进；门户管理：仅主管理员
  const myNav = [...nav, ...(canAdmin ? ['账号管理'] : []), ...(isAdmin ? ['门户管理'] : [])]
  const tabOf = { '工作台门户': 'home', '使用指南': 'guide', '模型配置': 'model', '账号管理': 'admin', '门户管理': 'cms' }
  const onNavClick = (n) => setTab(tabOf[n])

  // 缩放策略：窗口比设计基准(1560)宽 → 整体等比放大填满(zoom,文字重渲染保持清晰)；
  // 比基准窄(半屏/小本) → 宽度回到 100%，交给响应式栅格重排。
  useEffect(() => {
    const DW = 1560
    const el = document.querySelector('.pt-scale')
    const fit = () => {
      if (!el) return
      const w = window.innerWidth
      if (w >= DW) { el.style.width = DW + 'px'; el.style.zoom = String(w / DW) }
      else { el.style.width = '100%'; el.style.zoom = '1' }
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [tab])

  const counts = (tools) => {
    const c = { ok: 0, par: 0, beta: 0, soon: 0 }
    tools.forEach(t => { if (t.status in c) c[t.status]++ })
    return c
  }

  return (
    <div className="pt-root">
      <style>{CSS}</style>
      <div className="pt-stars"></div>
      <div className="pt-scale">
      <div className="pt-top">
        <img src={'data:image/png;base64,' + LOGO_B64} alt="星期零 STARFIELD" />
        <div className="pt-nm">星期零风控中心AI赋能中台</div>
        <div className="pt-nav">
          {myNav.map(n =>
            <span key={n} className={tab === tabOf[n] ? 'on' : ''} onClick={() => onNavClick(n)}>{n}</span>)}
        </div>
        <div className="pt-right">
          <label className="pt-search">
            <IcSearch />
            <input ref={searchRef} type="search" value={q} placeholder="搜索工具 / 能力，试试「稽核」"
              aria-label="搜索工具或能力"
              onChange={e => { setQ(e.target.value); if (tab !== 'home') setTab('home') }} />
            <kbd>Ctrl K</kbd>
          </label>
          <div className="pt-who">
            <div className="pt-av">{initial}</div>
            <div className="pt-info"><b>{user?.name}</b><br /><span>{grp || '—'} · {isAdmin ? '主管理员' : (canAdmin ? '工作台子管理员' : '组员')}</span></div>
          </div>
          <button className="pt-out" onClick={logout}>退出</button>
        </div>
      </div>

      {tab === 'home' && (
        <div className="pt-wrap">
          <div className="pt-hero">
            <div>
              <h1>你好，{user?.name} — <em>让每一笔业务，都经得起审计</em></h1>
              <p>有权限的工作台一键进入；下方工具目录可搜索、可筛选。其他组的工具可浏览，进入需相应权限。</p>
            </div>
            <div className={'pt-airdy' + (aiReady ? '' : ' wait') + (canModel ? '' : ' ro')}
              onClick={canModel ? () => setTab('model') : undefined}
              title={canModel ? '前往模型配置' : undefined}>
              <div className="d"></div>
              <div className="t">
                <b>{aiReady ? 'AI 模型已就绪' : 'AI 模型 · 待配置'}</b>
                <span>{aiReady
                  ? `已接入 ${aiStat.readyCount} 个工作台 · ${(aiStat.workbenches || []).filter(w => w.configured && w.reachable).map(w => (w.provider || '').toUpperCase()).filter(Boolean).join(' / ') || 'STANDBY'}`
                  : (canModel ? '点此前往「模型配置」接入大模型' : '未接入 · 请联系主管理员配置')}</span>
              </div>
              {canModel && <div className="v">配置 ›</div>}
            </div>
          </div>

          <div className="pt-sec"><h2><i></i>我的工作台</h2><span className="hint">常用入口 · 一键进入工作台</span></div>
          <div className="pt-mine">
            {LANES.map(lane => {
              if (lane.blank) {
                return (
                  <div key={lane.key} className="pt-mcard ghost blankcard">
                    <div className="pt-mtop">
                      <div className="pt-mtt">{lane.title}</div>
                      <span className="pt-glock"><IcLock />规划中</span>
                    </div>
                    <div className="pt-men">{lane.en}</div>
                    <div className="pt-gnote">工具规划中，待与法务部确认后开放。</div>
                  </div>
                )
              }
              if (!canEnter(lane)) {
                return (
                  <div key={lane.key} className="pt-mcard ghost">
                    <div className="pt-mtop">
                      <div className="pt-mtt">{lane.title}</div>
                      <span className="pt-glock"><IcLock />无权限</span>
                    </div>
                    <div className="pt-men">{lane.en}</div>
                    <div className="pt-gnote">你没有该组的进入权限。该组工具可在下方目录浏览；需要使用请找主管理员开通。</div>
                  </div>
                )
              }
              const mine = grp === GRP_OF_LANE[lane.key]
              const c = counts(toolsOf(lane.key))
              return (
                <div key={lane.key} className={'pt-mcard' + (mine ? ' me' : '')}>
                  <div className="pt-mtop">
                    <div className="pt-mtt">{lane.title}
                      <span className={'pt-tag' + (mine ? ' me' : '')}>{mine ? '我的工作台' : '可进入'}</span>
                    </div>
                    <button className="pt-enter pri" onClick={() => enterLane(lane)}>
                      进入工作台<IcArrow />
                    </button>
                  </div>
                  <div className="pt-men">{lane.en}</div>
                  <div className="pt-mst">
                    {c.ok > 0 && <span className="ok"><i></i>{ST.ok} {c.ok}</span>}
                    {c.par > 0 && <span className="par"><i></i>{ST.par} {c.par}</span>}
                    {c.beta > 0 && <span className="beta"><i></i>{ST.beta} {c.beta}</span>}
                    {c.soon > 0 && <span className="soon"><i></i>{ST.soon} {c.soon}</span>}
                  </div>
                  {(QUICK[lane.key] || []).length > 0 && (
                    <div className="pt-quick"><span className="lbl">常用</span>
                      {QUICK[lane.key].map(k =>
                        <button key={k} className="pt-qk" title="进入工作台后在左侧选择该功能"
                          onClick={() => enterLane(lane)}>{k}</button>)}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          <div className="pt-cat-head">
            <div className="pt-sec"><h2><i></i>工具目录</h2></div>
            <span className="pt-cnt">共 <b>{catTools.length}</b> 个工具 · 紫框可进入 · 带锁需权限 · 半透明敬请期待</span>
            <div className="pt-filters" role="group" aria-label="筛选">
              <span className="pt-fl">组别</span>
              {[['all', '全部'], ['accounting', '核算'], ['bp', '分析 BP'], ['legal', '法务']].map(([v, l]) =>
                <button key={v} className={'pt-chip' + (fLane === v ? ' on' : '')} onClick={() => setFLane(v)}>{l}</button>)}
              <span className="pt-fl">状态</span>
              {[['all', '全部'], ['ok', ST.ok], ['par', ST.par], ['beta', ST.beta], ['soon', ST.soon]].map(([v, l]) =>
                <button key={v} className={'pt-chip' + (fSt === v ? ' on' : '')} onClick={() => setFSt(v)}>{l}</button>)}
            </div>
          </div>

          {catTools.length > 0 ? (
            <div className="pt-grid">
              {catTools.map((t, i) => {
                const lane = LANES.find(l => l.key === t.lane)
                const enterable = lane && canEnter(lane)
                const dim = t.status === 'soon'
                const clickable = enterable && !dim
                return (
                  <div key={t.id || i}
                    className={'pt-tcard' + (dim ? ' dim' : '') + (clickable ? ' can' : ' still')}
                    onClick={clickable ? () => enterLane(lane) : undefined}
                    title={!enterable ? `需${lane ? lane.title : ''}权限，请找主管理员开通` : (clickable ? `进入${lane.title}` : undefined)}>
                    <div className="pt-tic">{t.icon}</div>
                    <div className="pt-tbody">
                      <div className="pt-tname">{t.name}<span className={'pt-st ' + t.status}>{ST[t.status]}</span>
                        <span className="pt-lane-tag">{LANE_SHORT[t.lane] || ''}</span>
                      </div>
                      <div className="pt-tdesc">{t.desc}</div>
                      {(t.gen || []).length > 0 && (
                        <div className="pt-caps"><span className="pt-clbl">通用</span>
                          {(t.gen || []).map(g => <span key={g} className="pt-tg">{g}</span>)}
                        </div>
                      )}
                      {(t.ai || []).length > 0 && (
                        <div className="pt-caps"><span className="pt-clbl ai">AI</span>
                          {(t.ai || []).map(a => <span key={a} className="pt-tg ai">✦ {a}</span>)}
                        </div>
                      )}
                    </div>
                    {clickable && <span className="pt-go"><IcGo /></span>}
                    {!enterable && <span className="pt-lock-go"><IcLock /></span>}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="pt-empty">没有匹配的工具 — 换个关键词，或清掉筛选条件试试</div>
          )}

          <div className="pt-more">其它功能陆续上新中，敬请期待</div>
        </div>
      )}

      {tab === 'admin' && canAdmin && (
        <div style={{ padding: '18px 0 8px' }}>
          <UserAdmin me={user} />
        </div>
      )}

      {tab === 'cms' && isAdmin && (
        <div style={{ padding: '18px 0 8px' }}>
          <PortalAdmin onChange={loadTools} />
        </div>
      )}

      {tab === 'model' && canModel && (
        <ModelConfig user={user} onBack={() => setTab('home')} />
      )}

      {tab === 'guide' && (
        <div className="pt-ph">
          <div className="box">
            <h2>使用指南</h2>
            <p>各工具的操作说明与常见问题，建设中。</p>
            <span className="back" onClick={() => setTab('home')}>← 返回工作台门户</span>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
