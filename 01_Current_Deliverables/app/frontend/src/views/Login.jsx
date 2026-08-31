// [Change Log] Date:2026-07-04 Author:Claude/c Version:V2.3
// 登录页（品牌化重设计）：星期零风控中心AI赋能中台
// 左=品牌紫面板(官方LOGO图案+Ø · 大黄猫+三伙伴全员互动) 右=登录表单(接真 /api/login)
// 猫戏三幕：输用户名→倾身+逐字追光标+瞳孔放大；输密码→举爪捂眼(伙伴闭眼)；点显示密码→扒开偷看(全员围观)
import React, { useEffect, useRef, useState } from 'react'
import { login } from '../api.js'

const LOGO_B64 = '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'

const CSS = `
.lg-wrap{
  --lbrand:#3E2C63; --lmuted:#B9AEDA;
  --lacc:#5C63F2; --lacc-d:#4A51E0;
  --link1:#20242E; --link2:#5B6270; --link3:#9AA1AE;
  --lline:#E6E7EE;
  --cat:#FFC93C; --cat-d:#F2B01E; --cat-dd:#D99B10;
  --ear:#FF9AA2; --nose:#F2778E; --blush:#FFB0B0; --catink:#4a3a1c;
  --lred:#e5484d;
  position:fixed; inset:0; overflow:auto; z-index:50;
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",Roboto,sans-serif;
  background:#ECEDF1; color:var(--link1); -webkit-font-smoothing:antialiased;
  display:flex; align-items:center; justify-content:center; padding:28px; box-sizing:border-box;
}
.lg-wrap *{box-sizing:border-box}
.lg-shell{
  width:100%; max-width:1060px; min-height:640px; margin:auto;
  background:#fff; border-radius:26px; overflow:hidden;
  box-shadow:0 24px 70px rgba(38,32,74,.16), 0 2px 8px rgba(38,32,74,.06);
  display:flex;
}
.lg-panel{
  flex:1 1 52%; min-width:0; position:relative; overflow:hidden;
  background:#2B1A46;
  color:#fff; display:flex; flex-direction:column; padding:32px 38px 0;
}
.lg-brand{display:flex; align-items:center; gap:14px}
.lg-brand .lg-logo{display:block; height:48px; width:auto}
.lg-brand .lg-name{font-size:17px;font-weight:800;letter-spacing:.5px}
.lg-brand .lg-sub{font-size:10px;color:var(--lmuted);letter-spacing:1px;margin-top:5px;line-height:1.6;white-space:nowrap}
.lg-panel h2{font-size:27px;font-weight:800;margin:34px 0 12px;letter-spacing:.5px}
.lg-copy{margin:0;color:var(--lmuted);font-size:13px;line-height:2.0;max-width:360px}
.lg-stage{flex:1; position:relative; min-height:240px}
.lg-stage svg{position:absolute; left:0; right:0; bottom:38px; width:100%; height:auto; display:block}
.lg-foot{
  position:absolute; left:38px; bottom:14px; z-index:2;
  color:rgba(255,255,255,.38); font-size:11px; letter-spacing:.4px; pointer-events:none;
}
.lg-side{flex:1 1 48%; display:flex; align-items:center; justify-content:center; padding:44px}
.lg-card{width:100%; max-width:346px}
.lg-toprow{display:flex; align-items:center; justify-content:space-between; margin-bottom:26px}
.lg-badge{
  display:inline-flex; align-items:center; gap:7px;
  background:#EEF0FE; color:var(--lacc); font-size:12px; font-weight:700;
  padding:6px 12px; border-radius:999px;
}
.lg-badge i{width:6px;height:6px;border-radius:50%;background:var(--lacc)}
.lg-help{font-size:12px;color:var(--link3)}
.lg-help b{color:var(--link2);font-weight:600}
.lg-card h1{font-size:26px;font-weight:800;margin:0 0 8px}
.lg-card .lg-hsub{color:var(--link3);font-size:13px;margin:0 0 26px}
.lg-field{margin-bottom:16px}
.lg-lbl{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px}
.lg-lbl label{font-size:13px;font-weight:700;color:var(--link1)}
.lg-lbl .lg-hint{font-size:11.5px;color:var(--link3)}
.lg-inpwrap{position:relative;display:flex;align-items:center}
.lg-inp{
  width:100%;height:46px;border:1px solid var(--lline);border-radius:12px;
  background:#F7F7FA;color:var(--link1);font-size:14.5px;font-family:inherit;
  padding:0 42px 0 14px; outline:none; transition:border-color .15s, box-shadow .15s, background .15s;
}
.lg-inp:focus{border-color:var(--lacc); background:#fff; box-shadow:0 0 0 4px rgba(92,99,242,.14)}
.lg-inp::placeholder{color:var(--link3)}
.lg-inp.lg-user{padding-right:14px}
.lg-eye{
  position:absolute;right:6px;width:34px;height:34px;border:0;background:transparent;cursor:pointer;
  display:grid;place-items:center;color:var(--link3);border-radius:9px;
}
.lg-eye:hover{color:var(--link2);background:#F0F1F6}
.lg-eye svg{width:19px;height:19px}
.lg-row{display:flex;align-items:center;justify-content:space-between;margin:6px 0 22px}
.lg-remember{display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;color:var(--link2);user-select:none;position:relative}
.lg-remember input{position:absolute;opacity:0;width:0;height:0}
.lg-box{width:17px;height:17px;border:1.5px solid #D6D8E2;border-radius:5px;display:grid;place-items:center;transition:all .15s;background:#fff}
.lg-remember input:checked + .lg-box{background:var(--lacc);border-color:var(--lacc)}
.lg-box svg{width:11px;height:11px;color:#fff;opacity:0;transform:scale(.6);transition:all .15s}
.lg-remember input:checked + .lg-box svg{opacity:1;transform:scale(1)}
.lg-link{font-size:12.5px;color:var(--lacc);cursor:pointer;text-decoration:none;font-weight:600}
.lg-link:hover{text-decoration:underline}
.lg-btn{
  width:100%;height:48px;border:0;border-radius:13px;cursor:pointer;
  background:var(--lacc);color:#fff;
  font-size:15px;font-weight:700;font-family:inherit;letter-spacing:6px;text-indent:6px;
  box-shadow:0 10px 24px rgba(92,99,242,.35); transition:transform .08s, box-shadow .15s, background .15s, opacity .15s;
}
.lg-btn:hover{background:var(--lacc-d); box-shadow:0 12px 28px rgba(92,99,242,.42)}
.lg-btn:active{transform:translateY(1px)}
.lg-btn:disabled{opacity:.6;cursor:default}
.lg-err{color:var(--lred);font-size:12.5px;margin:-6px 0 12px;min-height:16px}
.lg-err.show{animation:lgshake .3s}
@keyframes lgshake{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}75%{transform:translateX(5px)}}
.lg-cardfoot{margin-top:24px;text-align:center;color:var(--link3);font-size:12px}
.lg-cardfoot b{color:var(--link2);font-weight:600}
@media (max-width:860px){
  .lg-wrap{padding:14px}
  .lg-shell{flex-direction:column}
  .lg-panel{padding:24px 26px 0; min-height:420px}
  .lg-brand .lg-sub{white-space:normal}
  .lg-panel h2{font-size:22px; margin-top:24px}
  .lg-side{padding:30px 26px 36px}
  .lg-foot{left:26px}
}

/* ---------- 皮肤切换按钮 ---------- */
.lg-skin{
  position:absolute; top:16px; right:22px; z-index:60;
  font-size:11.5px; color:#8A8F9C; background:rgba(255,255,255,.78);
  border:1px solid #DDDFE8; border-radius:999px; padding:5px 13px; cursor:pointer;
  font-family:inherit; letter-spacing:1px;
}
.lg-skin:hover{color:#5B6070; background:#fff}

/* ---------- 正式版皮肤（.lg-formal 覆盖层） ---------- */
.lg-formal .lg-shell{border-radius:20px}
.lg-formal .lg-panel{background:linear-gradient(160deg,#261C4B 0%,#1C1338 58%,#170F2E 100%); padding:40px 48px 28px}
.lg-formal .lg-panel::before{
  content:''; position:absolute; inset:0; pointer-events:none;
  background-image:linear-gradient(rgba(255,255,255,.028) 1px, transparent 1px),
                   linear-gradient(90deg, rgba(255,255,255,.028) 1px, transparent 1px);
  background-size:52px 52px;
}
.lg-fart{position:absolute; inset:0; pointer-events:none; overflow:hidden}
.lg-fart .lg-bigO{position:absolute; right:-110px; bottom:-140px; width:560px; height:auto; opacity:.05}
.lg-fart .lg-star{position:absolute; top:118px; right:44px; width:250px; height:auto; opacity:.6}
.lg-formal .lg-brand .lg-logo{height:46px}
.lg-vline{width:1px; height:40px; background:rgba(255,255,255,.16)}
.lg-formal .lg-brand .lg-name{font-size:16px}
.lg-formal .lg-brand .lg-sub{letter-spacing:1.6px; font-size:9.5px; color:#9C8FD0}
.lg-mid{position:relative; z-index:1; margin-top:110px; max-width:430px}
.lg-over{font-size:11px; letter-spacing:4px; color:#9C8FD0; font-weight:600}
.lg-mid h1{font-size:30px; font-weight:800; letter-spacing:1px; margin:14px 0 12px; line-height:1.45; color:#fff}
.lg-tags{font-size:12.5px; color:#9C8FD0; letter-spacing:1px}
.lg-pillars{margin-top:36px; display:flex; flex-direction:column; gap:22px}
.lg-pl{display:flex; gap:14px; align-items:flex-start}
.lg-pl .lg-ic{width:34px; height:34px; flex:none; border-radius:9px; border:1px solid rgba(255,255,255,.14); background:rgba(255,255,255,.05); display:grid; place-items:center}
.lg-pl .lg-ic svg{width:16px; height:16px; stroke:#C9BFF2}
.lg-pl b{font-size:13.5px; font-weight:700; letter-spacing:.5px; color:#fff}
.lg-pl p{font-size:12px; color:#9C8FD0; margin:4px 0 0; line-height:1.7}
.lg-legal{position:relative; z-index:1; margin-top:auto; padding:18px 0 14px; border-top:1px solid rgba(255,255,255,.09); font-size:10.5px; color:rgba(255,255,255,.34); line-height:1.9; letter-spacing:.3px}
.lg-formal .lg-side{background:#FBFBFD}
.lg-chip{display:inline-flex; align-items:center; gap:7px; font-size:12px; font-weight:700; color:#4A3A8C; background:#EEECF7; border:1px solid #E0DCF0; padding:6px 12px; border-radius:8px}
.lg-chip svg{width:12px; height:12px; stroke:#4A3A8C}
.lg-ver{font-size:11px; color:var(--link3); letter-spacing:1px}
.lg-formal .lg-inp{border-radius:10px; background:#fff}
.lg-formal .lg-inp:focus{border-color:#4A3A8C; box-shadow:0 0 0 3px rgba(74,58,140,.13)}
.lg-formal .lg-btn{border-radius:10px; letter-spacing:8px; text-indent:8px; background:linear-gradient(180deg,#4A3A8C 0%,#3E2F78 100%); box-shadow:0 8px 20px rgba(62,47,120,.30)}
.lg-formal .lg-btn:hover{background:linear-gradient(180deg,#52419B 0%,#453583 100%)}
.lg-formal .lg-link{color:#4A3A8C}
.lg-formal .lg-remember input:checked + .lg-box{background:#4A3A8C;border-color:#4A3A8C}
.lg-agree{font-size:11.5px; color:var(--link3); text-align:center; margin-top:14px}
.lg-agree a{color:#4A3A8C; text-decoration:none; border-bottom:1px solid rgba(74,58,140,.35); cursor:pointer}
.lg-div{height:1px; background:#ECEDF2; margin:22px 0 12px}
.lg-note{font-size:11.5px; color:var(--link3); text-align:center; line-height:2.0}
.lg-note b{color:var(--link2); font-weight:600}
`

// 小猫 + 三个几何小伙伴（圆润落地）：与批准样机 login.html 同一套几何
const SCENE = `
<svg id="scene" viewBox="0 0 560 430" xmlns="http://www.w3.org/2000/svg">
  <ellipse cx="350" cy="418" rx="115" ry="9"  fill="rgba(0,0,0,.16)"/>
  <ellipse cx="88"  cy="418" rx="42"  ry="6"  fill="rgba(0,0,0,.15)"/>
  <ellipse cx="153" cy="418" rx="26"  ry="5"  fill="rgba(0,0,0,.15)"/>
  <ellipse cx="193" cy="418" rx="18"  ry="4"  fill="rgba(0,0,0,.15)"/>
  <g>
    <path d="M40,406 C40,360 62,340 88,340 C114,340 136,360 136,406 Q136,414 127,414 L49,414 Q40,414 40,406 Z" fill="#F08A4E"/>
    <ellipse cx="74" cy="376" rx="7.5" ry="8.5" fill="#fff"/>
    <ellipse cx="102" cy="376" rx="7.5" ry="8.5" fill="#fff"/>
    <circle id="pa1" cx="74" cy="376" r="3.2" fill="#2b2530"/>
    <circle id="pa2" cx="102" cy="376" r="3.2" fill="#2b2530"/>
    <rect id="la1" x="66.5" y="367.5" width="15" height="0" rx="7" fill="#F08A4E"/>
    <rect id="la2" x="94.5" y="367.5" width="15" height="0" rx="7" fill="#F08A4E"/>
    <path id="sa1" d="M67,377 Q74,371 81,377" fill="none" stroke="#B85C28" stroke-width="2" stroke-linecap="round" opacity="0"/>
    <path id="sa2" d="M95,377 Q102,371 109,377" fill="none" stroke="#B85C28" stroke-width="2" stroke-linecap="round" opacity="0"/>
    <path d="M81,398 h14" stroke="#B85C28" stroke-width="2.2" stroke-linecap="round"/>
  </g>
  <g>
    <rect x="130" y="286" width="46" height="128" rx="23" fill="#7A67D9"/>
    <ellipse cx="143" cy="324" rx="6.5" ry="7.5" fill="#fff"/>
    <ellipse cx="163" cy="324" rx="6.5" ry="7.5" fill="#fff"/>
    <circle id="pb1" cx="143" cy="324" r="2.8" fill="#2b2530"/>
    <circle id="pb2" cx="163" cy="324" r="2.8" fill="#2b2530"/>
    <rect id="lb1" x="136.5" y="316.5" width="13" height="0" rx="6" fill="#7A67D9"/>
    <rect id="lb2" x="156.5" y="316.5" width="13" height="0" rx="6" fill="#7A67D9"/>
    <path id="sb1" d="M137,325 Q143,320 149,325" fill="none" stroke="#4E3D9E" stroke-width="2" stroke-linecap="round" opacity="0"/>
    <path id="sb2" d="M157,325 Q163,320 169,325" fill="none" stroke="#4E3D9E" stroke-width="2" stroke-linecap="round" opacity="0"/>
    <path d="M147,348 Q153,353 159,348" fill="none" stroke="#4E3D9E" stroke-width="2.2" stroke-linecap="round"/>
  </g>
  <g>
    <rect x="178" y="346" width="30" height="68" rx="15" fill="#2C2148"/>
    <ellipse cx="186" cy="370" rx="4.5" ry="5.5" fill="#fff"/>
    <ellipse cx="200" cy="370" rx="4.5" ry="5.5" fill="#fff"/>
    <circle id="pc1" cx="186" cy="370" r="2.2" fill="#2b2530"/>
    <circle id="pc2" cx="200" cy="370" r="2.2" fill="#2b2530"/>
    <rect id="lc1" x="181.5" y="364.5" width="9" height="0" rx="4.5" fill="#2C2148"/>
    <rect id="lc2" x="195.5" y="364.5" width="9" height="0" rx="4.5" fill="#2C2148"/>
    <path id="sc1" d="M182,371 Q186,367 190,371" fill="none" stroke="#CBC3E8" stroke-width="1.6" stroke-linecap="round" opacity="0"/>
    <path id="sc2" d="M196,371 Q200,367 204,371" fill="none" stroke="#CBC3E8" stroke-width="1.6" stroke-linecap="round" opacity="0"/>
  </g>
  <g id="catG">
    <path d="M470,388 C506,382 512,352 490,340" fill="none" stroke="var(--cat-d)" stroke-width="16" stroke-linecap="round"/>
    <g transform="translate(300,252) rotate(-22)">
      <rect x="-19" y="-62" width="38" height="72" rx="19" fill="var(--cat)"/>
      <rect x="-10" y="-48" width="20" height="46" rx="10" fill="var(--ear)"/>
    </g>
    <g transform="translate(400,252) rotate(22)">
      <rect x="-19" y="-62" width="38" height="72" rx="19" fill="var(--cat)"/>
      <rect x="-10" y="-48" width="20" height="46" rx="10" fill="var(--ear)"/>
    </g>
    <path d="M222,396 C222,296 268,224 350,224 C432,224 478,296 478,396 Q478,410 462,410 L238,410 Q222,410 222,396 Z" fill="var(--cat)"/>
    <g stroke="var(--cat-dd)" stroke-width="2.2" stroke-linecap="round" opacity=".7">
      <path d="M288,332 L238,324"/><path d="M286,343 L234,343"/><path d="M288,354 L238,362"/>
      <path d="M412,332 L462,324"/><path d="M414,343 L466,343"/><path d="M412,354 L462,362"/>
    </g>
    <ellipse cx="297" cy="352" rx="13" ry="7" fill="var(--blush)" opacity=".5"/>
    <ellipse cx="403" cy="352" rx="13" ry="7" fill="var(--blush)" opacity=".5"/>
    <ellipse cx="318" cy="302" rx="27" ry="24" fill="#fff"/>
    <ellipse cx="382" cy="302" rx="27" ry="24" fill="#fff"/>
    <circle id="pcl" cx="318" cy="302" r="8.5" fill="#2b2530"/>
    <circle id="pcr" cx="382" cy="302" r="8.5" fill="#2b2530"/>
    <circle id="hcl" cx="315" cy="297" r="3" fill="#fff"/>
    <circle id="hcr" cx="379" cy="297" r="3" fill="#fff"/>
    <rect id="lcl" x="291" y="278" width="54" height="0" rx="20" fill="var(--cat)"/>
    <rect id="lcr" x="355" y="278" width="54" height="0" rx="20" fill="var(--cat)"/>
    <path id="scl" d="M305,306 Q318,292 331,306" fill="none" stroke="var(--catink)" stroke-width="3" stroke-linecap="round" opacity="0"/>
    <path id="scr" d="M369,306 Q382,292 395,306" fill="none" stroke="var(--catink)" stroke-width="3" stroke-linecap="round" opacity="0"/>
    <ellipse cx="350" cy="331" rx="4.5" ry="3.5" fill="var(--nose)"/>
    <path d="M350,334 Q344,342 337,338 M350,334 Q356,342 363,338" fill="none" stroke="var(--catink)" stroke-width="2.2" stroke-linecap="round"/>
    <path id="armL" d="" fill="none" stroke="var(--cat-d)" stroke-width="20" stroke-linecap="round" opacity="0"/>
    <path id="armR" d="" fill="none" stroke="var(--cat-d)" stroke-width="20" stroke-linecap="round" opacity="0"/>
    <g id="pawL">
      <ellipse rx="30" ry="26" fill="var(--cat)" stroke="var(--cat-dd)" stroke-width="1.5"/>
      <path d="M-9,-17 V-5 M0,-19 V-6 M9,-17 V-5" stroke="var(--cat-dd)" stroke-width="2.2" stroke-linecap="round"/>
    </g>
    <g id="pawR">
      <ellipse rx="30" ry="26" fill="var(--cat)" stroke="var(--cat-dd)" stroke-width="1.5"/>
      <path d="M-9,-17 V-5 M0,-19 V-6 M9,-17 V-5" stroke="var(--cat-dd)" stroke-width="2.2" stroke-linecap="round"/>
    </g>
  </g>
</svg>`

// 正式版装饰：超大 Ø 水印 + 星图（六个品牌图案=星座点，连线=风险图谱）
const FORMAL_ART = `
<svg class="lg-bigO" viewBox="0 0 100 140" fill="none" stroke="#fff">
  <ellipse cx="50" cy="70" rx="30" ry="42" stroke-width="14"/>
  <line x1="8" y1="126" x2="92" y2="14" stroke-width="13" stroke-linecap="round"/>
</svg>
<svg class="lg-star" viewBox="0 0 250 170">
  <g stroke="rgba(255,255,255,.14)" stroke-width="1" fill="none">
    <path d="M28,38 L96,20 L170,44 L224,24 M96,20 L118,92 L60,128 M118,92 L196,120 L224,24 M170,44 L196,120"/>
  </g>
  <g opacity=".78">
    <g stroke="#E3B13B" stroke-width="1.6" stroke-linecap="round" opacity=".8">
      <path d="M28,31 v14 M21,38 h14 M23,33 l10,10 M33,33 l-10,10"/>
    </g>
    <path d="M96,14 L101,23 L91,23 Z" fill="#EFB0CF" opacity=".8"/>
    <circle cx="170" cy="44" r="5" fill="none" stroke="#DE6A4A" stroke-width="2" opacity=".8"/>
    <rect x="112" y="86" width="11" height="11" fill="none" stroke="#9DC6A3" stroke-width="1.8" opacity=".75"/>
    <path d="M53,122 L67,122 L60,133 Z" fill="#30508F" opacity=".85"/>
    <path d="M190,114 L202,114 L196,121 Z M192,123 L200,123 L196,128 Z" fill="#C3B59B" opacity=".75"/>
    <circle cx="224" cy="24" r="2.2" fill="#fff" opacity=".7"/>
    <circle cx="146" cy="150" r="1.6" fill="#fff" opacity=".4"/>
    <circle cx="16" cy="88" r="1.6" fill="#fff" opacity=".35"/>
  </g>
</svg>`

export default function Login({ onLogin }) {
  const rootRef = useRef(null)
  const userRef = useRef(null)
  const passRef = useRef(null)
  const [name, setName] = useState(() => { try { return localStorage.getItem('fw_login_name') || '' } catch (e) { return '' } })
  const [pwd, setPwd] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [remember, setRemember] = useState(() => { try { return !!localStorage.getItem('fw_login_name') } catch (e) { return false } })
  const [err, setErr] = useState('')
  const [shakeKey, setShakeKey] = useState(0)
  const [busy, setBusy] = useState(false)
  // 双皮肤：轻松版(小猫) / 正式版(风控) —— 记住上次选择
  const [formal, setFormal] = useState(() => { try { return localStorage.getItem('fw_login_theme') === 'formal' } catch (e) { return false } })
  const toggleTheme = () => setFormal(f => {
    const nf = !f
    try { localStorage.setItem('fw_login_theme', nf ? 'formal' : 'cute') } catch (e) {}
    return nf
  })

  const showErr = (msg) => { setErr(msg); setShakeKey(k => k + 1) }

  const submit = async (e) => {
    e.preventDefault()
    if (!name.trim() || !pwd) { showErr('请输入用户名和密码'); return }
    setBusy(true); setErr('')
    try {
      const r = await login({ name: name.trim(), password: pwd })
      try {
        if (remember) localStorage.setItem('fw_login_name', name.trim())
        else localStorage.removeItem('fw_login_name')
      } catch (e2) {}
      onLogin(r.user)
    } catch (e3) {
      showErr('姓名或密码错误，或账号已被禁用')
    } finally { setBusy(false) }
  }

  // ---------- 小猫动画引擎（与批准样机同一套逻辑） ----------
  useEffect(() => {
    const prevTitle = document.title
    document.title = '星期零风控中心AI赋能中台 · 登录'
    const root = rootRef.current
    const $ = (id) => root.querySelector('#' + id)
    const svg = $('scene')
    // 正式版无小猫场景 → 只管标题，不跑动画
    if (!svg) return () => { document.title = prevTitle }
    const catG = $('catG'), armL = $('armL'), armR = $('armR'), pawL = $('pawL'), pawR = $('pawR')
    const hcl = $('hcl'), hcr = $('hcr')
    const panel = root.querySelector('.lg-panel')
    const userIn = userRef.current, passIn = passRef.current
    const clamp = (v, a, b) => v < a ? a : (v > b ? b : v)
    const lerp = (a, b, t) => a + (b - a) * t

    const WATCHERS = [
      { pup: [$('pcl'), $('pcr')], lid: [$('lcl'), $('lcr')], shut: [$('scl'), $('scr')], ex: [318, 382], ey: 302, r: 9, lidFull: 48, cat: true },
      { pup: [$('pa1'), $('pa2')], lid: [$('la1'), $('la2')], shut: [$('sa1'), $('sa2')], ex: [74, 102], ey: 376, r: 3.4, lidFull: 17 },
      { pup: [$('pb1'), $('pb2')], lid: [$('lb1'), $('lb2')], shut: [$('sb1'), $('sb2')], ex: [143, 163], ey: 324, r: 3, lidFull: 15 },
      { pup: [$('pc1'), $('pc2')], lid: [$('lc1'), $('lc2')], shut: [$('sc1'), $('sc2')], ex: [186, 200], ey: 370, r: 2.2, lidFull: 11 },
    ]
    WATCHERS.forEach(w => { w.ox = 0; w.oy = 0; w.lidV = 0 })

    const SHO = { lx: 302, ly: 396, rx: 398, ry: 396 }
    const REST = { lx: 315, ly: 398, rx: 385, ry: 398 }
    const COVER = { lx: 318, ly: 302, rx: 382, ry: 302 }
    const PEEK_DX = 30, PEEK_DY = 6
    const cur = { lean: 0, cover: 0, peek: 0 }

    const toSvg = (cx, cy) => {
      const pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy
      const m = svg.getScreenCTM(); if (!m) return null
      return pt.matrixTransform(m.inverse())
    }
    const meas = document.createElement('canvas').getContext('2d')
    const caretPoint = (inp) => {
      const cs = getComputedStyle(inp)
      meas.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily
      let end = inp.selectionEnd; if (end == null) end = inp.value.length
      const w = meas.measureText(inp.value.slice(0, end)).width
      const r = inp.getBoundingClientRect()
      const x = Math.min(r.left + 15 + w, r.right - 12)
      return { pt: toSvg(x, r.top + r.height / 2), frac: clamp((x - r.left) / (r.width || 1), 0, 1) }
    }

    const st = { mx: 0, my: 0, hovering: false, uFocus: false, pFocus: false }
    const ac = new AbortController(), sig = { signal: ac.signal }
    panel.addEventListener('mousemove', e => { st.mx = e.clientX; st.my = e.clientY; st.hovering = true }, sig)
    panel.addEventListener('mouseleave', () => { st.hovering = false }, sig)
    userIn.addEventListener('focus', () => { st.uFocus = true }, sig)
    userIn.addEventListener('blur', () => { st.uFocus = false }, sig)
    passIn.addEventListener('focus', () => { st.pFocus = true }, sig)
    passIn.addEventListener('blur', () => { st.pFocus = false }, sig)

    let nextBlink = performance.now() + 2600 + Math.random() * 3000, blinkStart = -1
    let raf = 0

    const frame = (now) => {
      let blink = 0
      if (blinkStart < 0 && now > nextBlink) blinkStart = now
      if (blinkStart >= 0) {
        const bt = (now - blinkStart) / 150
        if (bt >= 1) { blinkStart = -1; nextBlink = now + 2600 + Math.random() * 3500 }
        else blink = Math.sin(bt * Math.PI)
      }

      const showing = passIn.type === 'text'
      let mode, T = null, frac = 0
      if (st.pFocus && !showing) { mode = 'hide' }
      else if (st.pFocus && showing) { mode = 'peek'; const c1 = caretPoint(passIn); T = c1.pt; frac = c1.frac }
      else if (st.uFocus) { mode = 'watch'; const c2 = caretPoint(userIn); T = c2.pt; frac = c2.frac }
      else if (st.hovering) { mode = 'mouse'; T = toSvg(st.mx, st.my) }
      else mode = 'idle'
      // 测试钩子：无头验证时窗口失焦会清 focus，用它强制指定场景
      const fm = window.__forceMode
      if (fm) {
        mode = fm
        if (fm === 'watch') { const c3 = caretPoint(userIn); T = c3.pt; frac = c3.frac }
        else if (fm === 'peek') { const c4 = caretPoint(passIn); T = c4.pt; frac = c4.frac }
        else T = null
      }

      const tLean = (mode === 'watch') ? 1 : 0
      const tCover = (mode === 'hide' || mode === 'peek') ? 1 : 0
      const tPeek = (mode === 'peek') ? 1 : 0
      cur.lean += (tLean - cur.lean) * 0.12
      cur.cover += (tCover - cur.cover) * 0.16
      cur.peek += (tPeek - cur.peek) * 0.20

      WATCHERS.forEach(w => {
        let tox = 0, toy = 0
        if (T) {
          const ecx = (w.ex[0] + w.ex[1]) / 2, ecy = w.ey
          const dx = T.x - ecx, dy = T.y - ecy, d = Math.hypot(dx, dy) || 1
          if (mode === 'watch' || mode === 'peek') {
            tox = w.r * (0.4 + 0.55 * frac) * (dx >= 0 ? 1 : -1)
            toy = dy / d * w.r * 0.7
          } else { tox = dx / d * w.r; toy = dy / d * w.r }
        }
        let tl = blink
        if (!w.cat && mode === 'hide') tl = 1
        w.ox += (tox - w.ox) * 0.22; w.oy += (toy - w.oy) * 0.22
        w.lidV += (tl - w.lidV) * 0.35
        for (let i = 0; i < 2; i++) {
          w.pup[i].setAttribute('cx', (w.ex[i] + w.ox).toFixed(2))
          w.pup[i].setAttribute('cy', (w.ey + w.oy).toFixed(2))
          w.lid[i].setAttribute('height', (w.lidFull * w.lidV).toFixed(2))
          w.shut[i].setAttribute('opacity', w.lidV > 0.6 ? ((w.lidV - 0.6) / 0.4).toFixed(2) : '0')
        }
      })
      const cw = WATCHERS[0]
      const pr = (8.5 + 2.2 * cur.lean).toFixed(2)
      cw.pup[0].setAttribute('r', pr); cw.pup[1].setAttribute('r', pr)
      const vis = (1 - clamp(cur.cover - cur.peek, 0, 1)).toFixed(2)
      cw.pup[0].setAttribute('opacity', vis); cw.pup[1].setAttribute('opacity', vis)
      hcl.setAttribute('cx', (315 + cw.ox).toFixed(2)); hcl.setAttribute('cy', (297 + cw.oy).toFixed(2))
      hcr.setAttribute('cx', (379 + cw.ox).toFixed(2)); hcr.setAttribute('cy', (297 + cw.oy).toFixed(2))
      const hlo = Math.min(+vis, 1 - cw.lidV).toFixed(2)
      hcl.setAttribute('opacity', hlo); hcr.setAttribute('opacity', hlo)

      const k = cur.lean
      catG.setAttribute('transform',
        'translate(' + (k * 30).toFixed(2) + ',' + (-k * 6).toFixed(2) + ') rotate(' + (k * 3.5).toFixed(2) +
        ' 350 410) translate(350,410) scale(1,' + (1 + 0.055 * k).toFixed(4) + ') translate(-350,-410)')

      const c = cur.cover, p = cur.peek
      const plx = lerp(REST.lx, COVER.lx, c) - PEEK_DX * p
      const ply = lerp(REST.ly, COVER.ly, c) + PEEK_DY * p
      const prx = lerp(REST.rx, COVER.rx, c) + PEEK_DX * p
      const pry = lerp(REST.ry, COVER.ry, c) + PEEK_DY * p
      const sq = lerp(0.72, 1, c)
      pawL.setAttribute('transform', 'translate(' + plx.toFixed(1) + ',' + ply.toFixed(1) + ') scale(1,' + sq.toFixed(3) + ')')
      pawR.setAttribute('transform', 'translate(' + prx.toFixed(1) + ',' + pry.toFixed(1) + ') scale(1,' + sq.toFixed(3) + ')')
      armL.setAttribute('d', 'M' + SHO.lx + ',' + SHO.ly + ' Q' + (SHO.lx - 42 * c).toFixed(1) + ',' + ((SHO.ly + ply) / 2).toFixed(1) + ' ' + plx.toFixed(1) + ',' + ply.toFixed(1))
      armR.setAttribute('d', 'M' + SHO.rx + ',' + SHO.ry + ' Q' + (SHO.rx + 42 * c).toFixed(1) + ',' + ((SHO.ry + pry) / 2).toFixed(1) + ' ' + prx.toFixed(1) + ',' + pry.toFixed(1))
      const ao = Math.min(1, c * 4).toFixed(2)
      armL.setAttribute('opacity', ao); armR.setAttribute('opacity', ao)

      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => { cancelAnimationFrame(raf); ac.abort(); document.title = prevTitle }
  }, [formal])

  return (
    <div className={'lg-wrap' + (formal ? ' lg-formal' : '')} ref={rootRef}>
      <style>{CSS}</style>
      <button type="button" className="lg-skin" onClick={toggleTheme}>{formal ? '切换 · 轻松版' : '切换 · 正式版'}</button>
      <div className="lg-shell">
        <section className="lg-panel">
          {formal && <div className="lg-fart" dangerouslySetInnerHTML={{ __html: FORMAL_ART }} />}
          <div className="lg-brand">
            <img className="lg-logo" alt="星期零 STARFIELD" src={'data:image/png;base64,' + LOGO_B64} />
            {formal && <div className="lg-vline"></div>}
            <div>
              <div className="lg-name">星期零风控中心AI赋能中台</div>
              <div className="lg-sub">STARFIELD RISK CONTROL CENTER · AI-POWERED MIDDLE OFFICE</div>
            </div>
          </div>
          {formal ? (
            <>
              <div className="lg-mid">
                <div className="lg-over">内部风控作业平台</div>
                <h1>让每一笔业务，<br />都经得起审计。</h1>
                <div className="lg-tags">多组协同 · 数据全程留痕 · 权限最小授权</div>
                <div className="lg-pillars">
                  <div className="lg-pl">
                    <div className="lg-ic">
                      <svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
                        <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
                      </svg>
                    </div>
                    <div><b>一站式工具集</b><p>财务核算、经营分析（BP）、法务合规，各组工具统一集成、持续上新。</p></div>
                  </div>
                  <div className="lg-pl">
                    <div className="lg-ic">
                      <svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 8v4l2.5 2.5" /><circle cx="12" cy="12" r="9" />
                      </svg>
                    </div>
                    <div><b>审计留痕</b><p>每一次登录与操作，可追溯到人、到时间、到数据。</p></div>
                  </div>
                  <div className="lg-pl">
                    <div className="lg-ic">
                      <svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="4" y="10" width="16" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" />
                      </svg>
                    </div>
                    <div><b>分级授权</b><p>按部门与岗位授权，组间数据隔离，最小授权原则。</p></div>
                  </div>
                </div>
              </div>
              <div className="lg-legal">本系统为公司内部系统，仅限授权人员访问；登录与操作将被记录并用于审计。<br />© 2026 星期零 STARFIELD · 风控中心</div>
            </>
          ) : (
            <>
              <h2>欢迎回来</h2>
              <p className="lg-copy">让风控工作，更高效，更智能；<br />让数据产生价值，让经验持续沉淀；<br />让AI成为每一位风控同事的得力助手！</p>
              <div className="lg-stage" dangerouslySetInnerHTML={{ __html: SCENE }} />
              <div className="lg-foot">© 2026 星期零 STARFIELD · 仅限授权人员访问</div>
            </>
          )}
        </section>

        <section className="lg-side">
          <form className="lg-card" onSubmit={submit} autoComplete="on">
            <div className="lg-toprow">
              {formal ? (
                <span className="lg-chip">
                  <svg viewBox="0 0 24 24" fill="none" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z" />
                  </svg>
                  安全登录 · 全程加密
                </span>
              ) : (
                <span className="lg-badge"><i></i>安全登录</span>
              )}
              {formal ? <span className="lg-ver">V2.4</span> : <span className="lg-help">需要帮助？<b>找管理员</b></span>}
            </div>
            <h1>登录</h1>
            <p className="lg-hsub">{formal ? '请使用管理员开通的账号登录。' : '请输入用户名与密码以继续。'}</p>

            <div className="lg-field">
              <div className="lg-lbl"><label htmlFor="lg-username">用户名</label><span className="lg-hint">姓名</span></div>
              <div className="lg-inpwrap">
                <input ref={userRef} className="lg-inp lg-user" id="lg-username" name="username" type="text"
                  placeholder="请输入用户名" autoComplete="username"
                  value={name} onChange={e => setName(e.target.value)} autoFocus />
              </div>
            </div>

            <div className="lg-field">
              <div className="lg-lbl"><label htmlFor="lg-password">密码</label><span className="lg-hint">区分大小写</span></div>
              <div className="lg-inpwrap">
                <input ref={passRef} className="lg-inp" id="lg-password" name="password"
                  type={showPwd ? 'text' : 'password'} placeholder="请输入密码" autoComplete="current-password"
                  value={pwd} onChange={e => setPwd(e.target.value)} />
                <button className="lg-eye" type="button" title={showPwd ? '隐藏密码' : '显示密码'}
                  onMouseDown={e => e.preventDefault()} onClick={() => setShowPwd(s => !s)}>
                  {showPwd ? (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 12s3.6-7 10-7c2 0 3.8.6 5.3 1.5" /><path d="M22 12s-3.6 7-10 7c-2 0-3.8-.6-5.3-1.5" />
                      <path d="M9.5 9.5a3 3 0 0 0 4.2 4.2" /><path d="M3 3l18 18" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
            </div>

            <div className="lg-row">
              <label className="lg-remember">
                <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
                <span className="lg-box">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                </span>
                记住我
              </label>
              <a className="lg-link" onClick={() => showErr('请联系管理员重置密码')}>忘记密码？</a>
            </div>

            <div className={err ? 'lg-err show' : 'lg-err'} key={shakeKey}>{err}</div>
            <button className="lg-btn" type="submit" disabled={busy}>{busy ? '登录中…' : '登录'}</button>
            {formal ? (
              <>
                <div className="lg-agree">登录即代表同意<a>《内部数据安全与保密规范》</a></div>
                <div className="lg-div"></div>
                <div className="lg-note">账号由管理员统一开通 · <b>异常登录将被记录</b></div>
              </>
            ) : (
              <div className="lg-cardfoot">没有账号？<b>由管理员统一开通</b></div>
            )}
          </form>
        </section>
      </div>
    </div>
  )
}
