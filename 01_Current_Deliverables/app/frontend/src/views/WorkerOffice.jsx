// [Change Log] Date: 2026-10-04 | Author: Claude Opus 5.5 | Version: V2.796（数字员工办公室）
// Description: 数字员工办公室（门户层，《数字员工办公室 需求确认书 v1.0》）。一张工位值班表，不是带小人的动画办公室：
//   · 一个工位一行：它在干什么、多久干一次、上次什么时候干的、本月干了多少、现在正常 / 出错 / 停了 / 没上岗、干完交给谁、交出去还压着几件。
//   · 登录后从门户进：点一行看这个工位最近的干活记录（出错原文只有管理员看得到）；主管理员可生成「大屏链接」。
//   · 大屏：给闲置电脑常开用的是 public/office-screen.html（等距办公室版，不登录、只认大屏口令）。
//     本文件的 kioskToken 模式是同一份数据的表格版（#/office-screen?k=…），留作备用，不在页面上给链接。
//   · 全页只有件数和状态，不显示供应商、客户、金额（放在办公室里，路过的人都看得见）。不显示工时（业务方定：暂时不用）。
//   深色配色沿用门户，变量写在本文件里：大屏模式下没有门户外壳，得自己带。
import React, { useEffect, useState, useCallback } from 'react'
import { getOfficeRoster, getOfficeScreen, getOfficeRuns, getOfficeToken, setOfficeToken } from '../api.js'

const CSS = `
.wo-root{--bg:#14101F;--panel:#1D1730;--line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.14);--ink:#EDEAF6;--ink2:#B4ABD4;--ink3:#8B84AD;
  --brand:#7C5CFF;--green:#34D399;--amber:#FBBF24;--red:#F87171;--gray:#8A82A8;
  min-height:100vh;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.5;
  font-family:"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",Roboto,sans-serif}
.wo-root.kiosk{font-size:clamp(11px,calc(100vh / 62),20px)}  /* 大屏没人滚屏：字号跟着屏幕高度走，十几个工位一屏放完 */
.wo-root.kiosk .wo-wrap{max-width:none;padding:1em 1.6em 1em}
.wo-root.kiosk td{padding:.42em 1em}
.wo-root.kiosk .wo-kpi .v{font-size:2em}
.wo-root.kiosk .wo-top{margin-bottom:.8em}
.wo-unit{font-size:.84em;color:var(--ink3);margin-left:.35em}
.wo-root *{box-sizing:border-box}
.wo-wrap{max-width:1500px;margin:0 auto;padding:1.4em 1.8em 2.4em}
.wo-top{display:flex;flex-wrap:wrap;align-items:center;gap:.6em 1.2em;margin-bottom:1.2em}
.wo-top h1{margin:0;font-size:1.55em;font-weight:800;letter-spacing:.5px}
.wo-top .sub{font-size:.86em;color:var(--ink3)}
.wo-top .sp{flex:1}
.wo-top .at{font-size:.86em;color:var(--ink3);font-variant-numeric:tabular-nums}
.wo-btn{font:inherit;font-size:.9em;color:var(--ink2);background:rgba(255,255,255,.04);border:1px solid var(--line2);border-radius:9px;padding:.42em 1em;cursor:pointer}
.wo-btn:hover{color:var(--ink);border-color:var(--brand)}
.wo-btn.pri{background:var(--brand);border-color:var(--brand);color:#fff}
.wo-btn:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.wo-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.9em;margin-bottom:1.1em}
.wo-kpi>div{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:.9em 1.2em}
.wo-kpi .k{font-size:.86em;color:var(--ink3)}
.wo-kpi .v{font-size:2.3em;font-weight:800;line-height:1.2;font-variant-numeric:tabular-nums}
.wo-kpi .v small{font-size:.38em;font-weight:500;color:var(--ink3);margin-left:.5em}
.wo-kpi .warn .v{color:var(--amber)} .wo-kpi .bad .v{color:var(--red)}
.wo-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.wo-tw{overflow-x:auto}
.wo-root table{width:100%;min-width:900px;border-collapse:collapse}
.wo-root th{text-align:left;font-size:.8em;font-weight:600;color:var(--ink3);padding:.7em 1em;border-bottom:1px solid var(--line);white-space:nowrap;background:transparent}
.wo-root td{padding:.8em 1em;border:0;border-bottom:1px solid var(--line);vertical-align:top;color:var(--ink);background:transparent}
.wo-root tr:last-child td{border-bottom:0}
.wo-root tbody tr.go{cursor:pointer}
.wo-root tbody tr.go:hover td{background:rgba(255,255,255,.03)}
.wo-root tbody tr.off td{color:var(--ink3)}
.wo-nm{font-weight:700;font-size:1.04em}
.wo-dim{display:block;font-size:.84em;color:var(--ink3)}
.wo-num{font-variant-numeric:tabular-nums;white-space:nowrap}
.wo-st{display:inline-flex;align-items:center;gap:.5em;font-size:.86em;font-weight:700;padding:.18em .75em;border-radius:999px;white-space:nowrap}
.wo-st i{width:.6em;height:.6em;border-radius:50%;background:currentColor}
.wo-st.ok{color:var(--green);background:rgba(52,211,153,.12)}
.wo-st.ok i{animation:wo-pulse 2.4s ease-in-out infinite}
.wo-st.err{color:var(--amber);background:rgba(251,191,36,.12)}
.wo-st.down{color:var(--red);background:rgba(248,113,113,.14)}
.wo-st.off{color:var(--gray);background:rgba(255,255,255,.05)}
.wo-why{display:block;font-size:.8em;color:var(--ink3);margin-top:.25em}
.wo-why.bad{color:var(--red)} .wo-why.warn{color:var(--amber)}
.wo-foot{margin-top:.9em;font-size:.84em;color:var(--ink3);display:flex;flex-wrap:wrap;gap:.3em 1.6em}
.wo-msg{padding:3em 1em;text-align:center;color:var(--ink2)}
.wo-mask{position:fixed;inset:0;z-index:50;background:rgba(10,8,20,.72);display:flex;align-items:center;justify-content:center;padding:20px}
.wo-dlg{width:min(760px,100%);max-height:86vh;display:flex;flex-direction:column;background:#221A3A;border:1px solid var(--line2);border-radius:16px;overflow:hidden}
.wo-dlg-h{display:flex;align-items:center;gap:.8em;padding:1em 1.2em;border-bottom:1px solid var(--line)}
.wo-dlg-h b{font-size:1.1em} .wo-dlg-h .x{margin-left:auto;border:0;background:none;color:var(--ink3);font-size:1.1em;cursor:pointer}
.wo-dlg-b{padding:1em 1.2em;overflow:auto;font-size:.95em;color:var(--ink2);line-height:1.75}
.wo-dlg-b table{min-width:0}
.wo-link{display:flex;gap:.6em;margin:.8em 0}
.wo-link input{flex:1;min-width:0;font:inherit;font-size:.9em;color:var(--ink);background:rgba(255,255,255,.04);border:1px solid var(--line2);border-radius:9px;padding:.5em .8em}
.wo-err{color:var(--red)}
@keyframes wo-pulse{0%,100%{opacity:1}50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.wo-st.ok i{animation:none}}
`
const ST = { ok: '正常', err: '有出错', down: '停了', off: '没上岗' }

export default function WorkerOffice({ user, onBack, kioskToken = '' }) {
  const kiosk = !!kioskToken
  const [d, setD] = useState(null)             // 值班表；null=还没取到
  const [err, setErr] = useState('')
  const [desk, setDesk] = useState(null)       // 正在看的工位详情 {desk, runs…} / {loading:true}
  const [link, setLink] = useState(null)       // 大屏链接弹框的数据 {token, by, at}
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => (kiosk ? getOfficeScreen(kioskToken) : getOfficeRoster())
    .then(r => { setD(r); setErr('') })
    .catch(e => setErr(e.message || '取不到值班表')), [kiosk, kioskToken])
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load])
  // 整页深色：内容不满一屏时，下面露出来的也得是深色（大屏上一截白底很扎眼）。离开时还原。
  useEffect(() => { const old = document.body.style.background; document.body.style.background = '#14101F'; return () => { document.body.style.background = old } }, [])

  const openDesk = x => {
    if (kiosk || !x.detail) return
    setDesk({ loading: true, desk: x })
    getOfficeRuns(x.key).then(r => setDesk(r)).catch(e => setDesk({ desk: x, runs: [], error: e.message }))
  }
  const openLink = () => getOfficeToken().then(setLink).catch(e => setErr(e.message))
  const changeLink = async action => {
    setBusy(true)
    try { setLink(await setOfficeToken(action)); setCopied(false) } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  // 大屏用的是等距办公室那一版（public/office-screen.html，业务方 7 月的原型重新设计、接真数据）；口令放 # 后面，不会发给服务器
  const url = link?.token ? `${window.location.origin}/office-screen.html#k=${link.token}` : ''
  const copy = async () => {
    try { await navigator.clipboard.writeText(url) } catch (e) {
      const i = document.getElementById('wo-url'); if (i) { i.select(); document.execCommand('copy') }
    }
    setCopied(true)
  }
  const k = d?.kpi

  return (
    <div className={'wo-root' + (kiosk ? ' kiosk' : '')}>
      <style>{CSS}</style>
      <div className="wo-wrap">
        <div className="wo-top">
          <div><h1>数字员工办公室</h1><div className="sub">星期零 · 财务部 · 一个工位是一件自己在跑的活</div></div>
          <span className="sp" />
          {d && <span className="at">更新于 {d.asOf} · 每分钟自动刷新</span>}
          {!kiosk && d?.canManage && <button type="button" className="wo-btn" onClick={openLink}>大屏链接</button>}
          {!kiosk && onBack && <button type="button" className="wo-btn" onClick={onBack}>← 返回门户</button>}
        </div>

        {err && !d && <div className="wo-card wo-msg wo-err">{err}</div>}
        {!err && !d && <div className="wo-card wo-msg">正在取值班表…</div>}
        {d && (<>
          <div className="wo-kpi">
            <div className={k.down ? 'bad' : ''}><div className="k">在岗工位</div>
              <div className="v">{k.on}<small>个{k.down ? ` · ${k.down} 个停了` : ''}{k.off ? ` · ${k.off} 个没上岗` : ''}</small></div></div>
            <div><div className="k">本月干完</div><div className="v">{k.monthN}<small>件</small></div></div>
            <div className={k.waiting ? 'warn' : ''}><div className="k">交出去还没人接</div><div className="v">{k.waiting}<small>件</small></div></div>
            <div className={k.err ? 'warn' : ''}><div className="k">上一轮出了错的工位</div><div className="v">{k.err}<small>个</small></div></div>
          </div>
          <div className="wo-card"><div className="wo-tw"><table>
            <thead><tr><th>工位 · 它在干什么</th><th>多久干一次</th><th>上次干活</th><th>本月</th><th>状态</th><th>干完交给谁</th></tr></thead>
            <tbody>{d.desks.map(x => (
              <tr key={x.key} className={(x.status === 'off' ? 'off' : '') + (!kiosk && x.detail ? ' go' : '')}
                onClick={() => openDesk(x)} title={!kiosk && x.detail ? '点开看这个工位最近的干活记录' : undefined}>
                <td><span className="wo-nm">{x.name}</span><span className="wo-dim">{x.what}</span></td>
                <td className="wo-num">{x.cadence}</td>
                <td>{x.lastAt ? <><span className="wo-num">{x.lastAt}</span>
                  <span className={'wo-dim' + (x.lastOk ? '' : ' wo-err')}>{x.lastText}</span></> : <span className="wo-dim">还没有记录</span>}</td>
                <td className="wo-num">{x.monthN == null ? '—' : <>{x.monthN}<span className="wo-unit">{x.unit}</span></>}</td>
                <td><span className={'wo-st ' + x.status}><i />{ST[x.status] || x.status}</span>
                  {x.status !== 'ok' && <span className={'wo-why' + (x.status === 'down' ? ' bad' : x.status === 'err' ? ' warn' : '')}>{x.statusText}</span>}</td>
                <td>{x.handoff}
                  {x.waiting > 0 && <span className={'wo-why' + (x.waitingLate ? ' warn' : '')}>
                    {x.waiting} 件还没人接{x.waitingLate ? ` · ${x.waitingLate} 件超过 3 个工作日` : ''}</span>}</td>
              </tr>))}</tbody>
          </table></div></div>
          <div className="wo-foot">
            <span>本页只显示件数和状态，不显示供应商、客户和金额。</span>
            <span>{d.since ? `件数从 ${d.since} 开始记，更早的没有记录。` : '上线后还没有工位干过活，件数从第一笔开始记。'}</span>
            {err && <span className="wo-err">刚才那次刷新没成功：{err}（显示的是上一次的）</span>}
          </div>
        </>)}
      </div>

      {desk && (
        <div className="wo-mask" onMouseDown={e => { if (e.target === e.currentTarget) setDesk(null) }}>
          <div className="wo-dlg" role="dialog" aria-label={`${desk.desk.name} 的干活记录`}>
            <div className="wo-dlg-h"><b>{desk.desk.name}</b><span className="wo-dim">{desk.desk.what}</span>
              <button type="button" className="x" onClick={() => setDesk(null)} aria-label="关闭">✕</button></div>
            <div className="wo-dlg-b">
              {desk.loading ? '正在取…' : desk.error ? <span className="wo-err">{desk.error}</span> : (<>
                <div>{desk.off ? `没上岗：${desk.off}` : desk.beatAt ? `上次报到 ${desk.beatAt}（报到＝后台任务还活着；空转不记干活记录）` : '后台任务还没报到过。'}</div>
                {desk.runs.length === 0 ? <div style={{ marginTop: '.8em' }}>还没有干活记录——上线后它还没真干过活，或者每一轮都没活可干。</div> : (
                  <table style={{ marginTop: '.6em' }}>
                    <thead><tr><th>什么时候</th><th>结果</th><th>干了什么</th></tr></thead>
                    <tbody>{desk.runs.map((r, i) => (
                      <tr key={i}><td className="wo-num">{r.at}</td>
                        <td><span className={'wo-st ' + (r.ok ? 'ok' : 'err')}>{r.ok ? '干成了' : '出错'}</span></td>
                        <td>{r.summary}{(r.refs || []).length > 0 && <span className="wo-why">单号 {r.refs.join('、')}</span>}{r.error && <span className="wo-why warn">{r.error}</span>}</td></tr>))}</tbody>
                  </table>)}
              </>)}
            </div>
          </div>
        </div>
      )}

      {link && (
        <div className="wo-mask" onMouseDown={e => { if (e.target === e.currentTarget && !busy) setLink(null) }}>
          <div className="wo-dlg" role="dialog" aria-label="大屏链接" style={{ width: 'min(620px,100%)' }}>
            <div className="wo-dlg-h"><b>大屏链接</b>
              <button type="button" className="x" onClick={() => setLink(null)} aria-label="关闭">✕</button></div>
            <div className="wo-dlg-b">
              <div>在闲置电脑的浏览器里打开这个链接，就是办公室的大屏版（等距办公室，工位亮不亮一眼看得出）：<b style={{ color: 'var(--ink)' }}>不用登录</b>、每分钟自己刷新、不会过期。
                它只能看到件数和状态，进不了工作台，也看不到任何明细。</div>
              {url ? (<>
                <div className="wo-link"><input id="wo-url" readOnly value={url} onFocus={e => e.target.select()} />
                  <button type="button" className="wo-btn pri" onClick={copy}>{copied ? '已复制' : '复制'}</button></div>
                <div className="wo-dim">{link.by} 于 {link.at} 生成。链接等于钥匙：别发到群里；不想让某台电脑再看了，点「换一个」，旧链接立刻失效。</div>
              </>) : <div style={{ margin: '.8em 0' }}>现在没有可用的大屏链接{link.at ? `（${link.by} 于 ${link.at} 停用）` : ''}。</div>}
              <div style={{ display: 'flex', gap: '.6em', marginTop: '1em' }}>
                <button type="button" className={'wo-btn' + (url ? '' : ' pri')} disabled={busy} onClick={() => changeLink('new')}>{url ? '换一个（旧的失效）' : '生成链接'}</button>
                {url && <button type="button" className="wo-btn" disabled={busy} onClick={() => changeLink('off')}>停用</button>}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
