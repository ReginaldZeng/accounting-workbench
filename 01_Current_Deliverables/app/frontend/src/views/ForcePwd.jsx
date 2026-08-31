// [Change Log] Date:2026-08-20 Author:Claude/Reginald Zeng Version:V2.330
// 首次登录强制改密页（App 层门控：user.must_change_pwd=1 时渲染，改完才放进门户）。
// 触发场景：管理员新建账号 / 管理员重置密码——初始密码管理员知道，必须换成本人自己的。
// 真闸在服务端 _auth_gate：改密前除 /api/change-pwd 外一律 403，直连 API 也绕不过；此页只是 UX。
import React, { useState } from 'react'
import { apiChangePwd } from '../api.js'

const CSS = `
.fp-root{--bg:#14101F;--line:rgba(255,255,255,.09);--line2:rgba(255,255,255,.15);
  --ink:#EDEAF6;--ink2:#B4ABD4;--ink3:#8B84AD;--brand:#7C5CFF;--brand2:#9B7BFF;--amber:#FBBF24;--red:#F87171;
  position:fixed;inset:0;overflow:auto;z-index:60;color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",Roboto,sans-serif;
  background:radial-gradient(900px 520px at 82% -12%,rgba(124,92,255,.16),transparent 62%),#14101F;
  display:flex;align-items:center;justify-content:center;padding:24px;-webkit-font-smoothing:antialiased}
.fp-root *{box-sizing:border-box}
.fp-card{width:100%;max-width:420px;border-radius:18px;padding:30px 32px 26px;
  background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015));border:1px solid var(--line2)}
.fp-badge{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:700;letter-spacing:.5px;
  color:#FBE8B0;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.35);border-radius:999px;padding:4px 12px}
.fp-badge i{width:7px;height:7px;border-radius:50%;background:var(--amber);box-shadow:0 0 8px var(--amber)}
.fp-card h1{font-size:20px;font-weight:800;margin:16px 0 8px}
.fp-sub{font-size:12.5px;color:var(--ink2);line-height:1.8;margin:0 0 22px}
.fp-lb{font-size:12px;font-weight:700;color:var(--ink2);margin:0 0 7px}
.fp-inp{width:100%;height:42px;border-radius:10px;border:1px solid var(--line2);background:rgba(255,255,255,.05);
  color:var(--ink);font-size:14px;font-family:inherit;padding:0 13px;outline:none;margin-bottom:14px;
  transition:border-color .15s,box-shadow .15s}
.fp-inp:focus{border-color:rgba(124,92,255,.6);box-shadow:0 0 0 3px rgba(124,92,255,.16)}
.fp-inp::placeholder{color:var(--ink3)}
.fp-err{color:var(--red);font-size:12.5px;min-height:18px;margin:-4px 0 10px}
.fp-btn{width:100%;height:44px;border:0;border-radius:11px;cursor:pointer;font-family:inherit;
  background:linear-gradient(180deg,#8B6BFF,#6A4CE6);color:#fff;font-size:14px;font-weight:700;letter-spacing:3px;
  box-shadow:0 10px 24px rgba(90,60,200,.35);transition:filter .15s,opacity .15s}
.fp-btn:hover{filter:brightness(1.08)}
.fp-btn:disabled{opacity:.6;cursor:default}
.fp-foot{margin-top:18px;display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--ink3)}
.fp-foot b{color:var(--ink2);font-weight:700}
.fp-out{color:var(--brand2);cursor:pointer;background:none;border:0;font-size:12px;font-family:inherit}
.fp-out:hover{text-decoration:underline}
.fp-hint{font-size:11px;color:var(--ink3);margin:-6px 0 14px}
`

export default function ForcePwd({ user, onDone, onLogout }) {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [newPwd2, setNewPwd2] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!oldPwd || !newPwd || !newPwd2) { setErr('请填写全部三项'); return }
    if (newPwd !== newPwd2) { setErr('两次输入的新密码不一致'); return }
    if (newPwd.length < 8) { setErr('新密码至少 8 位'); return }
    if (newPwd === oldPwd) { setErr('新密码不能与原密码相同'); return }
    setBusy(true); setErr('')
    try {
      const r = await apiChangePwd({ oldPwd, newPwd })
      if (r.ok) onDone()
      else setErr(r.msg || '修改失败，请重试')
    } catch (e2) {
      setErr('网络异常，请重试')
    } finally { setBusy(false) }
  }

  return (
    <div className="fp-root">
      <style>{CSS}</style>
      <form className="fp-card" onSubmit={submit}>
        <span className="fp-badge"><i></i>首次登录 · 安全设置</span>
        <h1>请设置你自己的密码</h1>
        <p className="fp-sub">你当前的密码是管理员设置的初始密码（新建账号或重置密码后）。
          为保证账号安全，需先换成只有你自己知道的密码，才能进入工作台。</p>
        <div className="fp-lb">原密码（管理员发给你的）</div>
        <input className="fp-inp" type="password" value={oldPwd} onChange={e => setOldPwd(e.target.value)}
          placeholder="输入初始密码" autoFocus autoComplete="current-password" />
        <div className="fp-lb">新密码</div>
        <input className="fp-inp" type="password" value={newPwd} onChange={e => setNewPwd(e.target.value)}
          placeholder="至少 8 位" autoComplete="new-password" />
        <div className="fp-hint">至少 8 位；不能与原密码或姓名相同。</div>
        <div className="fp-lb">确认新密码</div>
        <input className="fp-inp" type="password" value={newPwd2} onChange={e => setNewPwd2(e.target.value)}
          placeholder="再输一遍" autoComplete="new-password" />
        <div className="fp-err">{err}</div>
        <button className="fp-btn" type="submit" disabled={busy}>{busy ? '提交中…' : '设置并进入'}</button>
        <div className="fp-foot">
          <span>当前账号：<b>{user?.name}</b></span>
          <button type="button" className="fp-out" onClick={onLogout}>换个账号登录</button>
        </div>
      </form>
    </div>
  )
}
