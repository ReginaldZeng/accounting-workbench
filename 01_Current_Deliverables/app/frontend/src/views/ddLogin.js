// [Change Log] Date: 2026-10-12 | Author: Claude / c | Version: V2.915（钉钉入口二选一）| 加入口分流的几个小函数：该不该问、选了工作台、去后补单、回工作台
// 钉钉免登（V2.884）：从钉钉工作台点开时自动认人登录。App 开页发现没登录 → 调 tryDdLogin()。
// 回 {user}＝认出来且认过账号，直接进；{dd:{ticket,name}}＝钉钉认出是谁、但还没认过账号（登录页提示输一次密码）；
// {dd:{note}}＝在钉钉里但没认成（登录页说明原因，照常用密码登录）；{}＝不在钉钉里，什么都不做。
import { inDingTalk, getAuthCode } from './ddBridge.js'
import { ddHello, loginDd } from '../api.js'

const SKIP = 'fw_dd_skip'   // 这次打开期间主动退出过 → 不再自动免登，否则在钉钉里一退出就又进去了，换不了账号

export function markDdSkip() { try { sessionStorage.setItem(SKIP, '1') } catch { /* 存不了就算了 */ } }

// 钉钉入口二选一（V2.915）：从钉钉点开首页时，有工作台账号的同事先选「发票后补单 / 财务工作台」；
// 没有账号的同事不选，直接进发票后补单（那一页本来就是靠钉钉认人、不用账号）。
// 选过「财务工作台」后这次打开期间不再问（刷新、退出重登都不问）；重新从钉钉点开算新的一次，会再问。
// 带着具体去处的链接（#/某一页）不问，直接去那一页。
const ENTRY = 'fw_dd_entry'
const hasDeepLink = () => /^#\/[a-z]/.test(window.location.hash || '')
export function shouldAskEntry() {
  if (!inDingTalk() || hasDeepLink()) return false
  try { return sessionStorage.getItem(ENTRY) !== 'wb' } catch { return false }   // 记不住选择就别问：不能把人卡在这一页
}
export function chooseWorkbench() { try { sessionStorage.setItem(ENTRY, 'wb') } catch { /* 存不了就算了 */ } }
// 两个页面是按地址里 # 后面那段在开页时分流的（见 main.jsx），所以换页要整页重开
export function goInvSelf() { window.location.hash = '#/invself'; window.location.reload() }
export function goWorkbench() {
  chooseWorkbench()
  try { window.history.replaceState(null, '', window.location.pathname + window.location.search) } catch { window.location.hash = '' }
  window.location.reload()
}

// 企业编号：服务器记着就用服务器的；没记着时认首页地址里带的（钉钉后台首页地址可写 ?corpId=$CORPID$）
function corpFromUrl() {
  const m = (window.location.search || '').match(/[?&]corpid=([\w-]{4,64})/i)
  return m ? m[1] : ''
}

export async function tryDdLogin() {
  if (!inDingTalk()) return {}
  try { if (sessionStorage.getItem(SKIP)) return {} } catch { /* 读不了照常试 */ }
  try {
    const h = await ddHello()
    if (!h.dingtalk) return {}
    const corpId = h.corpId || corpFromUrl()
    if (!corpId) return { dd: { note: '系统还不知道公司的钉钉企业编号，请联系管理员' } }
    const code = await getAuthCode(corpId, h.clientId)
    if (!code) return { dd: { note: '钉钉没给出身份' } }
    const r = await loginDd(code)
    if (r.ok) return { user: r.user }
    if (r.code === 'unbound') return { dd: { ticket: r.ticket, name: r.ddName || '' } }
    return { dd: { note: r.msg || '钉钉没认出你是谁' } }
  } catch (e) {
    return { dd: { note: (e && e.message) || '钉钉免登没成功' } }
  }
}
