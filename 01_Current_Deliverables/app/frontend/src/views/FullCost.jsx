// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 菜单「成本模块 › 全成本溯源」(fullcost) 的页面壳：页头 + 主体 + 期间，下面挂产品全成本主页（ActualCost）。
// 主体清单取平台主体档案（同存货台账）；期间默认上个月，上个月还没东西就往前找最近一个有配置或结果的月份。
import React, { useEffect, useState } from 'react'
import { getCostLedgerOrgs } from '../api.js'
import ActualCost from './ActualCost.jsx'
import { api } from './actualCostShared.jsx'

const pad = n => String(n).padStart(2, '0')
const back = ([y, p], n = 1) => { const d = new Date(y, p - 1 - n, 1); return [d.getFullYear(), d.getMonth() + 1] }

export default function FullCost({ user }) {
  const [orgs, setOrgs] = useState([]), [org, setOrg] = useState(''), [ym, setYm] = useState(null), [err, setErr] = useState('')

  useEffect(() => {
    getCostLedgerOrgs()
      .then(r => { if (r.ok) { setOrgs(r.orgs || []); setOrg(o => o || r.default) } else setErr(r.msg || '读取主体清单失败') })
      .catch(e => setErr('读取主体清单失败：' + e.message))
  }, [])
  useEffect(() => {
    if (!org || ym) return
    let live = true
    ;(async () => {
      const now = new Date(), first = back([now.getFullYear(), now.getMonth() + 1])
      let cur = first
      for (let i = 0; i < 3; i++) {
        try { const s = await api.state(org, cur[0], cur[1]); if (s.result || s.inputs) { if (live) setYm(cur); return } } catch { break }
        cur = back(cur)
      }
      if (live) setYm(first)
    })()
    return () => { live = false }
  }, [org])

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">成本模块 · 全成本溯源</div>
          <div className="h-sub">金蝶结账数 → 按本期依据分摊 → 产品全成本；每个产品可追到金蝶原单，并与核算底稿逐料对比 · 金蝶只读</div>
        </div>
        <div className="h-tools" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="selctl"><span className="k">主体</span>
            <select aria-label="主体" value={org} onChange={e => setOrg(e.target.value)} disabled={!orgs.length} style={{ border: 0, height: 28, padding: '0 4px', background: 'transparent' }}>
              {orgs.map(o => <option key={o.code} value={o.code}>{o.name}</option>)}
            </select>
          </span>
          {ym && <label className="selctl"><span className="k">期间</span>
            <input type="month" aria-label="全成本期间" value={`${ym[0]}-${pad(ym[1])}`} min="2000-01" max="2100-12"
              style={{ border: 0, background: 'transparent', color: 'inherit', font: 'inherit' }}
              onChange={e => { if (/^\d{4}-\d{2}$/.test(e.target.value)) setYm(e.target.value.split('-').map(Number)) }} />
          </label>}
        </div>
      </div>
      <div className="body">
        {err && <div className="trust" style={{ color: 'var(--red)', borderColor: 'var(--red-line)', background: 'var(--red-bg)' }}>{err}</div>}
        {org && ym && <ActualCost key={`${org}/${ym[0]}/${ym[1]}`} org={org} year={ym[0]} period={ym[1]} user={user} />}
      </div>
    </>
  )
}
