// [Change Log] Date:2026-07-10 Author:Claude/c Version:V2.41
// 月结看板 · 期间封存：本期做到哪一步了(清单) + 封存/解封。
// 封存 = 把本期对账结果拍照存死、转为只读；之后重开读的是快照，数字不会再变（底稿可复现、可审计）。
// 没有"开启新一期"的动作——上一期封存后，下一期天然是进行中（与金蝶期末结账同理）。
import React, { useEffect, useState } from 'react'
import { getPeriod, closePeriod, reopenPeriod } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const td = { padding: '6px 8px', borderBottom: '1px solid var(--line)' }
const MARK = { ok: '✓', warn: '!', fail: '✗' }
const CLR = { ok: 'var(--green)', warn: 'var(--amber,var(--amber))', fail: 'var(--red,var(--red))' }
const BG = { ok: 'var(--green-bg)', warn: 'var(--amber-bg)', fail: 'var(--red-bg)' }

export default function PeriodClose({ cfg, onPeriod, user, onChanged }) {
  const [d, setD] = useState(null), [busy, setBusy] = useState(false), [msg, setMsg] = useState('')
  const [force, setForce] = useState(false), [note, setNote] = useState(''), [reason, setReason] = useState('')

  const load = () => { setMsg(''); getPeriod().then(setD).catch(e => setMsg('加载失败：' + e.message)) }
  useEffect(() => { setD(null); setForce(false); setNote(''); setReason(''); load() }, [cfg.source, cfg.year, cfg.period])
  if (!d) return <div className="loading">加载中…（要跑一遍稽核和余额调节，稍候）</div>

  const st = d['状态'] || {}, cl = d['清单'] || {}, items = cl.items || []
  const closed = !!st['已封存']
  // 按环分组，顺序以后端「分组」为准；老快照没有「环」字段时全归一组（向后兼容）
  const order = cl['分组'] || []
  const groups = (order.length ? order : [...new Set(items.map(i => i['环'] || '月结'))])
    .map(g => [g, items.filter(i => (i['环'] || '月结') === g)]).filter(([, v]) => v.length)
  const canClose = !!(user?.perms?.close_period)
  const canReopen = !!(user?.is_super || user?.perms?.manage_accounting)

  const doClose = async () => {
    setBusy(true); setMsg('')
    const r = await closePeriod({ force, note })
    setBusy(false); setMsg(r.msg || '')
    if (r.ok) { setForce(false); setNote(''); load(); onChanged?.() }
  }
  const doReopen = async () => {
    setBusy(true); setMsg('')
    const r = await reopenPeriod({ reason })
    setBusy(false); setMsg(r.msg || '')
    if (r.ok) { setReason(''); load(); onChanged?.() }
  }

  return (<div>
    <div className="head">
      <div><div className="h-title">月结看板 · 期间封存</div>
        <div className="h-sub">月结是总纲，银行对账、物流计提都是它下面的一环。各环做完 → 封存本期，结果拍照存死、全站转为只读，之后底稿随时可复现。
          顺序：<b>各环做完对平 → 差异去金蝶改干净 → 出底稿 → 工作台封存 → 金蝶期末结账</b>（改差异要金蝶可写，故金蝶结账放最后）</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
        <button className="btn" onClick={load} disabled={busy}>刷新看板</button>
      </div>
    </div>

    <div className="body">
      {/* 本期状态 */}
      <div style={{
        padding: '12px 16px', marginBottom: 14, borderRadius: 8,
        border: '1px solid ' + (closed ? 'var(--green-line)' : 'var(--line)'), background: closed ? 'var(--green-bg)' : 'var(--bg)',
      }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: closed ? 'var(--green)' : 'var(--ink-1)' }}>
          {d.period_str} · {closed ? '已封存（本期只读）' : '进行中'}
        </div>
        {closed
          ? <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 6, lineHeight: 1.8 }}>
            封存人 <b>{st['封存人']}</b> ｜ 封存时间 {st['封存时间']} ｜ 金蝶取数时点 {st['金蝶取数时点'] || '—'}<br />
            {st['封存说明'] && <>说明：{st['封存说明']}<br /></>}
            本期不再从金蝶取数，认领、未达原因、计提凭证撤销全部锁定。导出的底稿就是封存那一刻的结果。
          </div>
          : <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 6 }}>
            数据随金蝶变动，尚未固化。清单全绿后即可封存本期。
          </div>}
      </div>

      {/* 月结清单：按环分组（月结是总纲，银行对账 / 物流计提 各是其中一环） */}
      <div style={{ fontSize: 13, fontWeight: 600, margin: '0 0 8px 2px' }}>
        月结清单
        <span style={{ fontWeight: 400, color: 'var(--ink-3)', marginLeft: 8, fontSize: 12 }}>
          ✗ 未完成（挡住封存）· ! 提示（不挡封存）· ✓ 已完成
        </span>
      </div>
      {groups.map(([grp, gits]) => {
        const bad = gits.filter(x => x['状态'] === 'fail').length
        return (<div key={grp} style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', margin: '0 0 6px 2px', display: 'flex', gap: 8 }}>
            <b style={{ color: 'var(--ink-1)' }}>{grp}</b>
            <span style={{ color: bad ? CLR.fail : 'var(--green)' }}>
              {bad ? `${bad} 项未完成` : '全部完成'}
            </span>
          </div>
          <div style={{ border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden' }}>
            {gits.map((it, i) => (
              <div key={it.key} style={{
                display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 14px',
                borderBottom: i < gits.length - 1 ? '1px solid var(--line)' : 'none',
              }}>
                <span style={{
                  flex: '0 0 20px', width: 20, height: 20, borderRadius: 10, background: BG[it['状态']],
                  color: CLR[it['状态']], fontSize: 12, fontWeight: 700, textAlign: 'center', lineHeight: '20px', marginTop: 1,
                }}>{MARK[it['状态']]}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: 'var(--ink-1)' }}>
                    {it['标题']}
                    {it['阻断封存'] && <span style={{ marginLeft: 8, fontSize: 11, color: CLR.fail }}>· 未完成前不能封存</span>}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>{it['说明']}</div>
                </div>
              </div>
            ))}
          </div>
        </div>)
      })}
      <div style={{ fontSize: 12, color: 'var(--ink-3)', margin: '0 0 16px 2px' }}>
        成本台账属结账<b>后</b>的报表系列，不列入封账前必做项。
      </div>

      {msg && <div style={{
        padding: '10px 14px', marginBottom: 14, borderRadius: 6, fontSize: 13,
        background: msg.includes('已封存') || msg.includes('已解封') ? 'var(--green-bg)' : 'var(--amber-bg)',
        color: 'var(--ink-1)', border: '1px solid var(--line)',
      }}>{msg}</div>}

      {/* 封存 / 解封 */}
      {!closed && <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>封存本期</div>
        {!canClose
          ? <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>你没有「月结看板·封存本期」权限，请联系管理员开通。</div>
          : <>
            {!cl['可封存'] && <div style={{ fontSize: 12.5, color: CLR.fail, marginBottom: 10 }}>
              还有未完成项：{(cl['未完成项'] || []).join('、')}
            </div>}
            {!cl['可封存'] && user?.can_admin && <div style={{ marginBottom: 10 }}>
              <label style={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />
                强制封存（清单未全绿也封，须填理由，供领导与审计核查）
              </label>
              {force && <textarea value={note} onChange={e => setNote(e.target.value)} rows={2}
                placeholder="例：0308 账户有 1 分钱舍入差，已确认无需调账。"
                style={{ width: '100%', marginTop: 8, padding: 8, fontSize: 12.5, border: '1px solid var(--line)', borderRadius: 6 }} />}
            </div>}
            <button className="btn primary" disabled={busy || (!cl['可封存'] && !force)} onClick={doClose}>
              {busy ? '封存中…' : '封存本期'}
            </button>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 8 }}>
              封存后本期只读：不再从金蝶取数，不能改认领 / 未达原因，不能撤销计提凭证。要改必须先解封。
            </div>
          </>}
      </div>}

      {closed && <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>解封本期</div>
        {!canReopen
          ? <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>解封是高危操作，只有主管理员或核算工作台子管理员可以执行。</div>
          : <>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={2}
              placeholder="解封理由（至少5个字），例：6月漏记一笔结息，需补记后重新对账。"
              style={{ width: '100%', marginBottom: 10, padding: 8, fontSize: 12.5, border: '1px solid var(--line)', borderRadius: 6 }} />
            <button className="btn" disabled={busy || reason.trim().length < 5} onClick={doReopen}>
              {busy ? '解封中…' : '解封本期'}
            </button>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 8 }}>
              解封会留痕（谁、何时、什么理由）。若金蝶已对该月期末结账，解封后也改不动金蝶——那要先在金蝶反结账。
            </div>
          </>}
      </div>}

      {/* 历史封存 */}
      <div style={{ fontSize: 13, fontWeight: 600, margin: '0 0 8px 2px' }}>已封存期间</div>
      {(d['历史'] || []).length === 0
        ? <div className="loading" style={{ padding: 20, fontSize: 12.5 }}>还没有封存过任何期间。</div>
        : <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead><tr style={{ background: 'var(--bg)', textAlign: 'left' }}>
            <th style={td}>期间</th><th style={td}>封存人</th><th style={td}>封存时间</th>
            <th style={td}>金蝶取数时点</th><th style={td}>说明</th>
          </tr></thead>
          <tbody>{d['历史'].map(h => (
            <tr key={h['期间']}>
              <td style={{ ...td, fontWeight: 600 }}>{h['期间']}</td>
              <td style={td}>{h['封存人']}</td>
              <td style={td}>{h['封存时间']}</td>
              <td style={td}>{h['金蝶取数时点'] || '—'}</td>
              <td style={{ ...td, color: 'var(--ink-2)' }}>{h['封存说明'] || '清单全绿'}</td>
            </tr>))}</tbody>
        </table>}
    </div>
  </div>)
}
