// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 金蝶原始数据：本期取数快照的四张来源表，保留金蝶返回的原值和原始行号，可搜索、翻页。
// 原先放在弹窗里，现在是主页的一个页签，和全成本表来回切不丢位置。
import React, { useEffect, useRef, useState } from 'react'
import { api, LABELS, Note, Pager, Seg, cellText, fmtFull, fmtTime, isNum } from './actualCostShared.jsx'

const KINDS = [['cost', '成本计算单'], ['outbound', '其他出库'], ['ledger', '费用凭证'], ['materials', '物料档案']]
const HINT = {
  cost: '成本计算单是多层结构：工单汇总、成本项目、费用/材料子层逐层展开，各层不能相加。材料子层暂无物料编码，逐料身份请到产品明细的「领补退料原单」看。',
  outbound: '本期金蝶其他出库单分录，共享领用从这里按领用类别筛出后分摊。',
  ledger: '本期金蝶费用凭证分录，费用总额以这里的入账金额为准。',
  materials: '本期涉及产品的物料档案，产品分组取自这里。',
}
const SIZE = 100

export default function ActualCostSources({ org, year, period, runId, sourceTime }) {
  const [kind, setKind] = useState('cost'), [input, setInput] = useState(''), [q, setQ] = useState(''), [page, setPage] = useState(0)
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const serial = useRef(0)
  useEffect(() => { const t = setTimeout(() => { setQ(input.trim()); setPage(0) }, 300); return () => clearTimeout(t) }, [input])
  useEffect(() => {
    const ticket = ++serial.current; setBusy(true); setError('')
    api.sources({ org, year, period, run_id: runId, kind, q, offset: page * SIZE, limit: SIZE })
      .then(d => { if (ticket === serial.current) setData(d) })
      .catch(e => { if (ticket === serial.current) setError(e.message) })
      .finally(() => { if (ticket === serial.current) setBusy(false) })
    return () => { serial.current++ }
  }, [org, year, period, runId, kind, q, page])
  const fields = data?.kind === kind ? data.fields : [], rows = data?.kind === kind ? data.rows : []
  const numeric = Object.fromEntries(fields.map(f => [f, rows.some(r => isNum(r[f])) && !/id|行号|凭证|year|period|编码|code|bill|voucher|account/i.test(f)]))

  return (
    <div className="ac-pane">
      <div className="ac-toolbar">
        <Seg label="来源表" value={kind} onChange={k => { setKind(k); setInput(''); setQ(''); setPage(0) }} options={KINDS} />
        <div className="ac-toolbar-r">
          <span className="muted">金蝶数据时点 {fmtTime(data?.fetched_at || sourceTime)}{data?.kind === kind && ` · 本表共 ${data.source_total} 行`}</span>
          <input className="ac-input" type="search" placeholder="搜编码、工单、单据号、摘要" aria-label="搜索原始数据" value={input} onChange={e => setInput(e.target.value)} />
        </div>
      </div>
      <p className="ac-p muted">{HINT[kind]}</p>
      {error && <Note tone="bad">{error}</Note>}
      <div className="ac-data-wrap" tabIndex={0} role="region" aria-label="金蝶原始数据，可横向滚动" aria-busy={busy}>
        <table className="ac-data">
          <thead><tr>{fields.map(f => <th key={f} className={numeric[f] ? 'r' : ''}>{LABELS[f] || f}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => <tr key={i} className={kind === 'cost' && r['工单原值'] ? 'ac-lead' : ''}>
              {fields.map(f => <td key={f} className={numeric[f] ? 'num' : ''} title={isNum(r[f]) ? fmtFull(r[f]) : undefined}>{cellText(r[f], f, numeric[f])}</td>)}</tr>)}
            {!rows.length && <tr><td colSpan={fields.length || 1} className="ac-empty">{busy ? '正在读取…' : '没有符合条件的记录。'}</td></tr>}
          </tbody>
        </table>
      </div>
      <Pager page={page} size={SIZE} total={data?.kind === kind ? data.total : 0} onPage={setPage} busy={busy} />
    </div>
  )
}
