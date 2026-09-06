// [Change Log] Date:2026-09-06 Author:Claude/Reginald Zeng Version:V2.499
// 登录验收弹窗（系统语气）。你在门户「验收台账」发起验收、指派给某账号后，该账号登录门户就弹这个：
//   系统提示「X 已就绪，邀您验收」+ 发起时附的"要看的点"，验收人选 通过/打回、注明"需要完善的点"。
//   点「稍后再说」只关掉、不改状态 → 下次登录再弹（一直缠到验完）。不摆发起人是谁（系统口气，不当面催人）。
// 渲染在 Portal（.pt-root）内，沿用门户深色变量（--panel/--brand/--ink…）。
import React, { useState, useEffect } from 'react'
import { getVerifyPopup, actVerify } from '../api.js'

const CSS = `
.vp-mask{position:fixed;inset:0;z-index:120;background:rgba(10,8,20,.72);backdrop-filter:blur(2px);
  display:flex;align-items:center;justify-content:center;padding:20px}
.vp-card{width:min(520px,94vw);background:var(--panel,#221A3A);border:1px solid var(--line2,rgba(255,255,255,.14));
  border-radius:16px;box-shadow:0 24px 70px rgba(0,0,0,.5);overflow:hidden;
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",Roboto,sans-serif}
.vp-hd{display:flex;align-items:center;gap:9px;padding:14px 18px;border-bottom:1px solid var(--line,rgba(255,255,255,.08))}
.vp-hd .d{width:9px;height:9px;border-radius:50%;background:var(--brand,#7C5CFF);box-shadow:0 0 10px var(--brand,#7C5CFF)}
.vp-hd b{font-size:14px;font-weight:800;color:var(--ink,#EDEAF6);letter-spacing:.3px}
.vp-hd .cnt{margin-left:auto;font-size:11.5px;color:var(--ink3,#8B84AD)}
.vp-bd{padding:16px 18px}
.vp-tool{font-size:17px;font-weight:800;color:var(--ink,#EDEAF6)}
.vp-lead{font-size:13px;color:var(--ink2,#B4ABD4);margin-top:5px;line-height:1.6}
.vp-note{margin-top:12px;background:rgba(255,255,255,.04);border:1px solid var(--line,rgba(255,255,255,.08));
  border-left:3px solid var(--brand,#7C5CFF);border-radius:8px;padding:9px 12px;font-size:12.5px;color:var(--ink2,#B4ABD4);line-height:1.6}
.vp-note .lb{color:var(--ink3,#8B84AD);font-size:11px;display:block;margin-bottom:3px}
.vp-row{margin-top:16px}
.vp-lbl{font-size:12px;color:var(--ink3,#8B84AD);margin-bottom:7px}
.vp-seg{display:flex;gap:10px}
.vp-opt{flex:1;display:flex;align-items:center;justify-content:center;gap:7px;cursor:pointer;user-select:none;
  border:1px solid var(--line2,rgba(255,255,255,.14));border-radius:10px;padding:10px 0;font-size:13.5px;font-weight:700;
  color:var(--ink2,#B4ABD4);background:rgba(255,255,255,.03);transition:all .15s}
.vp-opt.pass.on{color:#0f2f22;background:var(--green,#34D399);border-color:var(--green,#34D399)}
.vp-opt.rej.on{color:#3a2606;background:var(--amber,#FBBF24);border-color:var(--amber,#FBBF24)}
.vp-ta{width:100%;margin-top:8px;min-height:64px;resize:vertical;border:1px solid var(--line2,rgba(255,255,255,.14));
  border-radius:10px;background:rgba(255,255,255,.03);color:var(--ink,#EDEAF6);font-family:inherit;font-size:13px;padding:9px 11px;line-height:1.6}
.vp-ta:focus{outline:none;border-color:var(--brand,#7C5CFF)}
.vp-ta::placeholder{color:var(--ink3,#8B84AD)}
.vp-err{color:#F87171;font-size:12px;margin-top:8px}
.vp-ft{display:flex;align-items:center;gap:10px;padding:14px 18px;border-top:1px solid var(--line,rgba(255,255,255,.08))}
.vp-later{font-size:12.5px;color:var(--ink3,#8B84AD);background:none;border:0;cursor:pointer;font-family:inherit}
.vp-later:hover{color:var(--ink2,#B4ABD4)}
.vp-submit{margin-left:auto;font-family:inherit;border:0;cursor:pointer;font-size:13.5px;font-weight:700;color:#fff;
  padding:9px 20px;border-radius:10px;background:linear-gradient(180deg,#8B6BFF,#6A4CE6)}
.vp-submit:hover{filter:brightness(1.08)}
.vp-submit:disabled{opacity:.5;cursor:default;filter:none}
`

export default function VerifyPopup({ onDone }) {
  const [tasks, setTasks] = useState(null)   // null=未取 / []=无 / [..]=待验收队列
  const [idx, setIdx] = useState(0)
  const [verdict, setVerdict] = useState('')
  const [improve, setImprove] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { getVerifyPopup().then(r => setTasks(r.tasks || [])).catch(() => setTasks([])) }, [])

  if (!tasks || tasks.length === 0 || idx >= tasks.length) return null
  const t = tasks[idx]

  const submit = async () => {
    if (verdict !== 'pass' && verdict !== 'reject') { setErr('请先选「通过」或「暂不验收」'); return }
    setBusy(true); setErr('')
    try {
      await actVerify({ toolId: t.toolId, verdict, improve })
      const next = idx + 1
      setVerdict(''); setImprove('')
      if (next >= tasks.length) { onDone && onDone(); setTasks([]) }   // 全部处理完 → 关掉 + 刷新
      else setIdx(next)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy(false) }
  }
  const later = () => { setTasks([]); onDone && onDone() }   // 稍后：只关，不改状态，下次登录再弹

  return (
    <div className="vp-mask">
      <style>{CSS}</style>
      <div className="vp-card">
        <div className="vp-hd">
          <span className="d" /><b>系统 · 验收提示</b>
          {tasks.length > 1 && <span className="cnt">{idx + 1} / {tasks.length}</span>}
        </div>
        <div className="vp-bd">
          <div className="vp-tool">「{t.toolName}」已就绪，邀您验收</div>
          <div className="vp-lead">请您确认这个功能是否达标；如有还能更好的地方，请一并注明。</div>
          {t.note && <div className="vp-note"><span className="lb">要看的点</span>{t.note}</div>}

          <div className="vp-row">
            <div className="vp-lbl">验收结论</div>
            <div className="vp-seg">
              <div className={'vp-opt pass' + (verdict === 'pass' ? ' on' : '')} onClick={() => setVerdict('pass')}>✓ 通过</div>
              <div className={'vp-opt rej' + (verdict === 'reject' ? ' on' : '')} onClick={() => setVerdict('reject')}>◔ 暂不验收</div>
            </div>
          </div>

          <div className="vp-row">
            <div className="vp-lbl">需要完善的点（{verdict === 'reject' ? '暂不验收：请写明还差什么' : '选填'}）</div>
            <textarea className="vp-ta" value={improve} onChange={e => setImprove(e.target.value)}
              placeholder={verdict === 'reject' ? '还差什么、要怎么完善？写清楚方便这边改…' : '有哪些还能更好的地方？没有就留空'} />
          </div>
          {err && <div className="vp-err">{err}</div>}
        </div>
        <div className="vp-ft">
          <button className="vp-later" onClick={later}>稍后再说</button>
          <button className="vp-submit" onClick={submit} disabled={busy}>{busy ? '提交中…' : '提交验收'}</button>
        </div>
      </div>
    </div>
  )
}
