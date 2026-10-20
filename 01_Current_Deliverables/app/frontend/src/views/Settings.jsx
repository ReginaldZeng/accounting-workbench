// [Change Log] Date: 2026-09-12 | Author: Codex | Version: V2.564
// Description: Settings reuses the inline feature catalog maintenance table.
import React, { useState } from 'react'
import { setConfig, testKingdee } from '../api.js'
import SysLog from './SysLog.jsx'
import NavCatalog from '../components/NavCatalog.jsx'
import AccessRequestPanel from './AccessRequestPanel.jsx'   // V2.731 首页「申请开通」钉钉接收人
import TodoAssigneePanel from './TodoAssigneePanel.jsx'     // V2.790 首页待办区·各环节处理人

export default function Settings({ cfg, onChange, onModsChanged }) {
  const [tab, setTab] = useState('settings')   // settings=常规设置 / syslog=日志中心（V2.489）
  const [src, setSrc] = useState(cfg.source || 'sample')
  const [year, setYear] = useState(cfg.year || 2026), [period, setPeriod] = useState(cfg.period || 6)
  const [test, setTest] = useState(null), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false)
  const save = async () => { const c = await setConfig({ source: src, year: Number(year), period: Number(period) }); onChange(c); setSaved(true); setTimeout(() => setSaved(false), 1600) }
  const doTest = async () => { setBusy(true); try { setTest(await testKingdee()) } catch (e) { setTest({ ok: false, msg: String(e) }) } finally { setBusy(false) } }
  const inp = { width: 90, height: 32, borderRadius: 8, border: '1px solid var(--line-strong)', background: 'var(--bg)', color: 'var(--ink)', padding: '0 10px', fontSize: 13 }

  // 顶部两标签：常规设置 / 日志中心（都在 enter_settings 闸内，仅主管理员）
  const tabBtn = (k, label) => (
    <button type="button" onClick={() => setTab(k)} style={{
      fontFamily: 'inherit', border: 0, background: 'none', cursor: 'pointer', fontSize: 13.5, fontWeight: 600,
      color: tab === k ? 'var(--accent)' : 'var(--ink-2)', padding: '9px 16px', marginBottom: -1,
      borderBottom: '2px solid ' + (tab === k ? 'var(--accent)' : 'transparent'),
    }}>{label}</button>
  )

  return (<div>
    <div className="head"><div><div className="h-title">系统设置</div>
      <div className="h-sub">{tab === 'syslog'
        ? '日志中心 · 运维请求日志 + 业务操作留痕 · 仅主管理员可看'
        : '功能目录维护 · 数据源 · 会计期间 · 金蝶连接 · 仅主管理员可进入'}</div></div></div>
    <div className="body">
      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--line)', marginBottom: 16 }}>
        {tabBtn('settings', '常规设置')}
        {tabBtn('syslog', '日志中心')}
      </div>
      {tab === 'syslog' && <SysLog />}
      {tab === 'settings' && <>
      <NavCatalog onModsChanged={onModsChanged} />
      <div style={{ margin: '16px 0' }}><AccessRequestPanel /></div>
      <div style={{ margin: '16px 0' }}><TodoAssigneePanel /></div>
      <div style={{ maxWidth: 660 }}>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>数据源</div>
          <label className="ck" style={{ marginBottom: 8 }}><input type="radio" name="src" checked={src === 'sample'} onChange={() => setSrc('sample')} /> 样例数据（内置，离线可用，用于演示/验收界面）</label>
          <label className="ck"><input type="radio" name="src" checked={src === 'kingdee'} onChange={() => setSrc('kingdee')} /> 金蝶真数据（调金蝶云星空 WebAPI 拉取，只读）</label>
          <div className="foot" style={{ marginTop: 10 }}>conf 路径：{cfg.conf || '(未找到 conf.ini，请放到 backend/ 或设 KD_CONF_PATH)'}</div>
        </div>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>会计期间</div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span className="flabel">年</span><input type="number" value={year} onChange={e => setYear(e.target.value)} style={inp} />
            <span className="flabel">期</span><input type="number" min="1" max="12" value={period} onChange={e => setPeriod(e.target.value)} style={{ ...inp, width: 70 }} />
          </div>
        </div>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>金蝶连接测试</div>
          <button className="btn" onClick={doTest} disabled={busy}>{busy ? '测试中…' : '测试连接'}</button>
          {test && <div style={{ marginTop: 10, fontSize: 12.5, color: test.ok ? 'var(--green)' : 'var(--red)' }}>{test.ok ? '✓ 连接成功' : '⚠ 连接失败'} · {test.msg}</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button className="btn btn-pri" onClick={save}>保存设置</button>
          {saved && <span style={{ fontSize: 12.5, color: 'var(--green)' }}>✓ 已保存，切换数据源后各页会自动刷新</span>}
        </div>
      </div>
      </>}
    </div>
  </div>)
}
