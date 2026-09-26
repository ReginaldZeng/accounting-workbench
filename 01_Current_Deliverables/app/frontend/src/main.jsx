// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| #/invpair 分流到手机配对页（不走 App 的 getMe/登录闸）
// [Change Log] Date: 2026-09-25 | Author: Claude / c | Version: V2.621 | #/invself 分流到申请人自助登记发票后补页（业务同事没有工作台账号）
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import InvPhone from './views/InvPhone.jsx'
import InvSelf from './views/InvSelf.jsx'
import './styles.css'
// 手机钉钉扫配对码打开的是 /#/invpair?t=令牌：手机上没有工作台登录态，必须在 App 之前分流，否则会落到登录页
const isInvPair = (window.location.hash || '').startsWith('#/invpair')
const isInvSelf = (window.location.hash || '').startsWith('#/invself')
createRoot(document.getElementById('root')).render(isInvPair ? <InvPhone /> : isInvSelf ? <InvSelf /> : <App />)
