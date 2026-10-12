// [Change Log] Date: 2026-10-12 | Author: Claude / c | Version: V2.915（钉钉入口二选一）
// 钉钉入口二选一：有工作台账号的同事从钉钉点开首页，先选「发票后补单 / 财务工作台」（App.jsx 决定什么时候出这一页）。
// 样机：docs/20261012_钉钉入口二选一/prototype/entry_choice.html。面向全公司，品牌素材和登录页一套——
// 紫色面板、标志、黄猫和三个小伙伴（图形同 Login.jsx，去掉了登录页专用的眨眼/捂眼部件）。
// 手机是默认版式；宽屏（电脑钉钉）换成和登录页同一个壳：左品牌面板、右选入口。
// 卡片上不放"欠几张票"的提示（用户定）：没登记的后补系统看不到，放了容易让人以为没提示就是不欠票。
import React, { useState } from 'react'
import { LOGO_B64 } from './Login.jsx'

const CSS = `
.dde{
  --deep:#2B1A46; --on-deep:#B9AEDA;
  --acc:#5C63F2; --acc-soft:#EEF0FE; --plum:#4A3A8C; --plum-soft:#EEECF7;
  --ink1:#20242E; --ink2:#5B6270; --ink3:#8A91A0; --dline:#E6E7EE; --page:#ECEDF1;
  --cat:#FFC93C; --cat-d:#F2B01E; --cat-dd:#D99B10; --ear:#FF9AA2; --nose:#F2778E; --blush:#FFB0B0; --catink:#4a3a1c;
  position:fixed; inset:0; z-index:50; overflow-y:auto; display:flex; background:var(--deep); color:var(--ink1);
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",Roboto,sans-serif; -webkit-font-smoothing:antialiased;
  line-height:1.32;   /* 全站默认行高更松；这页按样机的行高排，矮屏手机才一屏放得下 */
}
.dde *{box-sizing:border-box}
.dde h1,.dde h2,.dde p{margin:0}
.dde-shell{flex:1; display:flex; flex-direction:column; min-width:0}
.dde-hero{flex:none; position:relative; color:#fff; padding:20px 22px 0; min-height:258px}
.dde-brand{display:flex; align-items:center; gap:11px}
.dde-brand img{height:36px; width:auto; display:block}
.dde-name{font-size:14px; font-weight:800; letter-spacing:.4px}
.dde-sub{font-size:9px; color:var(--on-deep); letter-spacing:1.4px; margin-top:4px; white-space:nowrap}
.dde .dde-hi{margin-top:24px; font-size:15px; color:var(--on-deep)}
.dde .dde-q{margin-top:6px; font-size:27px; font-weight:800; letter-spacing:.5px; line-height:1.3; color:#fff}
.dde-tip{display:none}
.dde-stage{position:absolute; right:4px; bottom:-9px; width:250px; z-index:2; pointer-events:none}
.dde-stage svg{display:block; width:100%; height:auto}
.dde-stage .sh{fill:rgba(20,12,40,.16)}
.dde-stage .pp{transition:transform .28s cubic-bezier(.3,.7,.3,1); transform:translate(-1px,5px)}
.dde-stage.look-a .pp{transform:translate(-5px,5px)}
.dde-stage.look-b .pp{transform:translate(-3px,7px)}

.dde-sheet{flex:1 0 auto; position:relative; z-index:1; background:#fff; border-radius:26px 26px 0 0; padding:24px 18px 18px; display:flex; flex-direction:column}
.dde .dde-lbl{font-size:13px; font-weight:700; color:var(--ink3); letter-spacing:1px; margin:0 4px 12px}
.dde-cards{display:flex; flex-direction:column; gap:12px}
.dde-card{appearance:none; font:inherit; text-align:left; cursor:pointer; width:100%; display:flex; align-items:center; gap:14px;
  background:#fff; border:1px solid var(--dline); border-radius:18px; padding:18px 14px 18px 16px; color:inherit;
  box-shadow:0 1px 2px rgba(38,32,74,.04); transition:border-color .2s, box-shadow .2s, background .2s; -webkit-tap-highlight-color:transparent}
.dde-card:hover{border-color:#C9CCFB; box-shadow:0 12px 28px rgba(92,99,242,.14)}
.dde-card:active{background:#FAFAFE}
.dde-card:focus-visible{outline:3px solid rgba(92,99,242,.38); outline-offset:2px}
.dde-card:disabled{cursor:default; opacity:.6}
.dde-ic{flex:none; width:52px; height:52px; border-radius:15px; display:grid; place-items:center}
.dde-ic svg{width:26px; height:26px; fill:none; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round}
.dde-ic.a{background:var(--acc-soft)} .dde-ic.a svg{stroke:var(--acc)}
.dde-ic.b{background:var(--plum-soft)} .dde-ic.b svg{stroke:var(--plum)}
.dde-tx{flex:1; min-width:0; display:flex; flex-direction:column; gap:5px}
.dde-tx b{font-size:17.5px; font-weight:800; letter-spacing:.3px}
.dde-tx small{font-size:14px; line-height:1.55; color:var(--ink2)}
.dde-meta{align-self:flex-start; display:inline-flex; align-items:center; gap:6px; margin-top:3px; font-size:12.5px; font-weight:600; padding:4px 10px; border-radius:999px}
.dde-meta i{width:6px; height:6px; border-radius:50%}
.dde-meta.all{background:var(--acc-soft); color:#3F46C8} .dde-meta.all i{background:var(--acc)}
.dde-meta.ok{background:#E9F7EF; color:#17794B} .dde-meta.ok i{background:#22A565}
.dde-go{flex:none; width:32px; height:32px; border-radius:50%; display:grid; place-items:center; color:var(--ink3); transition:transform .2s, color .2s, background .2s}
.dde-go svg{width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:2.2; stroke-linecap:round; stroke-linejoin:round}
.dde-card:hover:not(:disabled) .dde-go{transform:translateX(3px); color:var(--acc); background:var(--acc-soft)}
.dde-card.busy{pointer-events:none; border-color:#C9CCFB}
.dde-card.busy .dde-go svg{display:none}
.dde-card.busy .dde-go::after{content:""; width:16px; height:16px; border-radius:50%; border:2.2px solid #D5D8FB; border-top-color:var(--acc); animation:ddespin .7s linear infinite}
@keyframes ddespin{to{transform:rotate(360deg)}}
.dde-foot{margin-top:auto; padding-top:18px; display:flex; align-items:center; justify-content:center; gap:7px; font-size:12px; color:var(--ink3); text-align:center}
.dde-foot svg{flex:none; width:14px; height:14px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round}

/* 矮屏手机（钉钉标题栏还要占一截）：品牌区收一点，两张卡和底部说明一屏放下 */
@media (max-width:859px) and (max-height:640px){
  .dde-hero{min-height:232px; padding-top:16px}
  .dde .dde-hi{margin-top:16px}
  .dde-sheet{padding-top:20px}
  .dde-card{padding-top:15px; padding-bottom:15px}
  .dde-foot{padding-top:14px}
}

/* 宽屏（电脑钉钉）：和登录页同一个壳 */
@media (min-width:860px){
  .dde{background:var(--page); align-items:center; justify-content:center; padding:28px}
  .dde-shell{flex:none; width:100%; max-width:900px; min-height:546px; flex-direction:row; background:#fff; border-radius:26px; overflow:hidden;
    box-shadow:0 24px 70px rgba(38,32,74,.16), 0 2px 8px rgba(38,32,74,.06)}
  .dde-hero{flex:1 1 47%; min-width:0; min-height:0; background:var(--deep); padding:32px 36px 0; overflow:hidden}
  .dde-brand img{height:46px}
  .dde-name{font-size:16.5px}
  .dde-sub{font-size:10px; letter-spacing:1.8px}
  .dde .dde-hi{margin-top:36px; font-size:14px}
  .dde .dde-tip{display:block; margin-top:12px; font-size:13px; line-height:2; color:var(--on-deep); max-width:300px}
  .dde-stage{left:0; right:0; bottom:30px; width:auto}
  .dde-stage .pp{transform:translate(5px,2px)}
  .dde-stage.look-a .pp{transform:translate(6px,-2px)}
  .dde-stage.look-b .pp{transform:translate(6px,4px)}
  .dde-sheet{flex:1 1 53%; border-radius:0; padding:40px 44px; justify-content:center}
  .dde .dde-lbl{margin:0 2px 18px; font-size:22px; font-weight:800; color:var(--ink1); letter-spacing:.5px}
  .dde-cards{gap:14px}
  .dde-card{padding:18px 16px 18px 18px}
  .dde-foot{margin-top:26px; padding-top:0}
}
@media (prefers-reduced-motion:reduce){
  .dde-stage .pp,.dde-card,.dde-go{transition:none}
  .dde-card.busy .dde-go::after{animation-duration:1.6s}
}
`

// 黄猫和三个小伙伴：与登录页同一套几何；瞳孔带 class="pp"，看向正要点的那张卡
const SCENE = `
<svg viewBox="0 0 560 430" xmlns="http://www.w3.org/2000/svg">
  <ellipse class="sh" cx="350" cy="418" rx="115" ry="9"/>
  <ellipse class="sh" cx="88" cy="418" rx="42" ry="6"/>
  <ellipse class="sh" cx="153" cy="418" rx="26" ry="5"/>
  <ellipse class="sh" cx="193" cy="418" rx="18" ry="4"/>
  <g>
    <path d="M40,406 C40,360 62,340 88,340 C114,340 136,360 136,406 Q136,414 127,414 L49,414 Q40,414 40,406 Z" fill="#F08A4E"/>
    <ellipse cx="74" cy="376" rx="7.5" ry="8.5" fill="#fff"/><ellipse cx="102" cy="376" rx="7.5" ry="8.5" fill="#fff"/>
    <circle class="pp" cx="74" cy="376" r="3.2" fill="#2b2530"/><circle class="pp" cx="102" cy="376" r="3.2" fill="#2b2530"/>
    <path d="M81,398 h14" stroke="#B85C28" stroke-width="2.2" stroke-linecap="round"/>
  </g>
  <g>
    <rect x="130" y="286" width="46" height="128" rx="23" fill="#7A67D9"/>
    <ellipse cx="143" cy="324" rx="6.5" ry="7.5" fill="#fff"/><ellipse cx="163" cy="324" rx="6.5" ry="7.5" fill="#fff"/>
    <circle class="pp" cx="143" cy="324" r="2.8" fill="#2b2530"/><circle class="pp" cx="163" cy="324" r="2.8" fill="#2b2530"/>
    <path d="M147,348 Q153,353 159,348" fill="none" stroke="#4E3D9E" stroke-width="2.2" stroke-linecap="round"/>
  </g>
  <g>
    <rect x="178" y="346" width="30" height="68" rx="15" fill="#3A2B5E"/>
    <ellipse cx="186" cy="370" rx="4.5" ry="5.5" fill="#fff"/><ellipse cx="200" cy="370" rx="4.5" ry="5.5" fill="#fff"/>
    <circle class="pp" cx="186" cy="370" r="2.2" fill="#2b2530"/><circle class="pp" cx="200" cy="370" r="2.2" fill="#2b2530"/>
  </g>
  <g>
    <path d="M470,388 C506,382 512,352 490,340" fill="none" stroke="var(--cat-d)" stroke-width="16" stroke-linecap="round"/>
    <g transform="translate(300,252) rotate(-22)"><rect x="-19" y="-62" width="38" height="72" rx="19" fill="var(--cat)"/><rect x="-10" y="-48" width="20" height="46" rx="10" fill="var(--ear)"/></g>
    <g transform="translate(400,252) rotate(22)"><rect x="-19" y="-62" width="38" height="72" rx="19" fill="var(--cat)"/><rect x="-10" y="-48" width="20" height="46" rx="10" fill="var(--ear)"/></g>
    <path d="M222,396 C222,296 268,224 350,224 C432,224 478,296 478,396 Q478,410 462,410 L238,410 Q222,410 222,396 Z" fill="var(--cat)"/>
    <g stroke="var(--cat-dd)" stroke-width="2.2" stroke-linecap="round" opacity=".7">
      <path d="M288,332 L238,324"/><path d="M286,343 L234,343"/><path d="M288,354 L238,362"/>
      <path d="M412,332 L462,324"/><path d="M414,343 L466,343"/><path d="M412,354 L462,362"/>
    </g>
    <ellipse cx="297" cy="352" rx="13" ry="7" fill="var(--blush)" opacity=".5"/><ellipse cx="403" cy="352" rx="13" ry="7" fill="var(--blush)" opacity=".5"/>
    <ellipse cx="318" cy="302" rx="27" ry="24" fill="#fff"/><ellipse cx="382" cy="302" rx="27" ry="24" fill="#fff"/>
    <g class="pp"><circle cx="318" cy="302" r="8.5" fill="#2b2530"/><circle cx="382" cy="302" r="8.5" fill="#2b2530"/><circle cx="315" cy="297" r="3" fill="#fff"/><circle cx="379" cy="297" r="3" fill="#fff"/></g>
    <ellipse cx="350" cy="331" rx="4.5" ry="3.5" fill="var(--nose)"/>
    <path d="M350,334 Q344,342 337,338 M350,334 Q356,342 363,338" fill="none" stroke="var(--catink)" stroke-width="2.2" stroke-linecap="round"/>
    <g transform="translate(315,398)"><ellipse rx="30" ry="26" fill="var(--cat)" stroke="var(--cat-dd)" stroke-width="1.5"/><path d="M-9,-17 V-5 M0,-19 V-6 M9,-17 V-5" stroke="var(--cat-dd)" stroke-width="2.2" stroke-linecap="round"/></g>
    <g transform="translate(385,398)"><ellipse rx="30" ry="26" fill="var(--cat)" stroke="var(--cat-dd)" stroke-width="1.5"/><path d="M-9,-17 V-5 M0,-19 V-6 M9,-17 V-5" stroke="var(--cat-dd)" stroke-width="2.2" stroke-linecap="round"/></g>
  </g>
</svg>`

/**
 * @param {object} p
 * @param {{name?: string}} p.user 已登录的工作台账号
 * @param {() => void} p.onLater 选了「发票后补单」
 * @param {() => void} p.onWorkbench 选了「财务工作台」
 */
export default function DdEntry({ user, onLater, onWorkbench }) {
  const [look, setLook] = useState('')
  const [busy, setBusy] = useState('')
  const go = (k, fn) => { if (busy) return; setBusy(k); fn() }
  const eyes = k => ({ onPointerEnter: () => setLook(k), onPointerLeave: () => setLook(''), onFocus: () => setLook(k), onBlur: () => setLook('') })
  const card = k => ({ className: 'dde-card' + (busy === k ? ' busy' : ''), 'aria-busy': busy === k || undefined, disabled: !!busy && busy !== k, ...eyes(k) })
  return (
    <div className="dde">
      <style>{CSS}</style>
      <div className="dde-shell">
        <section className="dde-hero">
          <div className="dde-brand">
            <img alt="星期零 STARFIELD" src={'data:image/png;base64,' + LOGO_B64} />
            <div>
              <div className="dde-name">星期零风控中心AI赋能中台</div>
              <div className="dde-sub">STARFIELD RISK CONTROL CENTER</div>
            </div>
          </div>
          <p className="dde-hi">你好，{user?.name || '同事'}</p>
          <h1 className="dde-q">今天要办哪件事？</h1>
          <p className="dde-tip">钉钉已经认出你了，不用再输账号密码。<br />选右边一个入口就行。</p>
          <div className={'dde-stage' + (look ? ' look-' + look : '')} aria-hidden="true" dangerouslySetInnerHTML={{ __html: SCENE }} />
        </section>
        <section className="dde-sheet">
          <h2 className="dde-lbl">选一个进入</h2>
          <div className="dde-cards">
            <button type="button" {...card('a')} onClick={() => go('a', onLater)}>
              <span className="dde-ic a"><svg viewBox="0 0 24 24"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" /><path d="M9 8h6M9 12h6M9 16h3" /></svg></span>
              <span className="dde-tx">
                <b>发票后补单</b>
                <small>付了款，发票还没到？<br />先登记，票到了再交给财务。</small>
                <span className="dde-meta all"><i />全公司同事都能用</span>
              </span>
              <span className="dde-go"><svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" /></svg></span>
            </button>
            <button type="button" {...card('b')} onClick={() => go('b', onWorkbench)}>
              <span className="dde-ic b"><svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="7" height="7" rx="1.8" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.8" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.8" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.8" /></svg></span>
              <span className="dde-tx">
                <b>财务工作台</b>
                <small>对账、核算、报表都在这里。</small>
                <span className="dde-meta ok"><i />已认出你的账号，直接进</span>
              </span>
              <span className="dde-go"><svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" /></svg></span>
            </button>
          </div>
          <div className="dde-foot"><svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" /><path d="M9 12l2 2 4-4" /></svg>钉钉已确认你是本公司同事 · 仅限授权人员访问</div>
        </section>
      </div>
    </div>
  )
}
