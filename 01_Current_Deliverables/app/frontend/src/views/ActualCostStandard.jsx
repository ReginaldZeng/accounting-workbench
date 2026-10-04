// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 核算底稿 × 金蝶实际，每公斤逐料对比。数值全部来自后端 /standard-comparison，这里不另算口径；
// 「可比项小计」只是把后端算好的用量影响、计价影响逐行相加，未匹配/单位不符的行不进小计。
// 规矩（沿用交接）：底稿版本要让人看清编号、日期、审核状态、来源，多版本时不替人挑最新；
// 空值显示「—」不显示 0；采购价和出库计价分开标注；来源与版本差异提示常显不折叠。
import React, { useEffect, useState } from 'react'
import { api, Note, Num, fmt, fmtDate, fmtFull, fmtPct, isNum } from './actualCostShared.jsx'

const SECTION = { 原料: '原料', 包材: '包材', 实际新增: '实际新增或待匹配用料' }
const FEES = [['mfg', '加工费'], ['load', '装卸费'], ['adm', '管理费']]
const EFFECTS = ['quantity_effect', 'price_effect', 'cost_difference']
const statusTone = s => (s === '匹配' ? 'green' : /新增/.test(s) ? 'violet' : 'amber')
const entryTone = s => (/定稿|已审核/.test(s) ? 'green' : /本地/.test(s) ? 'amber' : 'blue')

export default function ActualCostStandard({ org, year, period, runId, target, detail, choice, onChoice, canFetch, fetching, onFetch, onPrices }) {
  const [entries, setEntries] = useState(null), [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [open, setOpen] = useState('')

  useEffect(() => {
    let live = true; setEntries(null); setData(null); setError(''); setOpen('')
    api.standards(org, target.code).then(d => {
      if (!live) return
      setEntries(d.rows)
      if (choice == null && d.rows.length === 1) onChoice(d.rows[0].id)   // 只有一份时直接用它（照样写明是哪一份）；多份必须由人来选
    }).catch(e => { if (live) setError(e.message) })
    return () => { live = false }
  }, [org, target.code])
  useEffect(() => {
    let live = true; setData(null); setError('')
    if (choice == null) return
    setBusy(true)
    api.compare({ org, year, period, run_id: runId, cc: target.cc, code: target.code, entry_id: choice })
      .then(d => { if (live) setData(d) }).catch(e => { if (live) setError(e.message) }).finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [choice, org, year, period, runId, target.code, target.cc, detail])

  const sections = data ? [...new Set(data.rows.map(r => r.section))].sort((a, b) => (a === '实际新增') - (b === '实际新增')) : []
  const scale = data ? Math.max(0, ...data.rows.map(r => Math.abs(r.cost_difference || 0))) : 0
  const notes = data ? (data.issues || []).filter(s => s !== data.standard.provenance_note) : []
  const draft = notes.filter(s => /定稿/.test(s)), method = notes.filter(s => !/定稿/.test(s))
  const prices = code => (detail?.prices || []).filter(p => p.code === code)

  return (
    <div className="ac-pane ac-std">
      {/* ── 选底稿 ── */}
      <div className="ac-std-pick">
        <div className="ac-label">比较底稿 <span className="muted">只用于本次查看，不是月度标准冻结，也不改变底稿的审核状态</span></div>
        {entries === null && !error && <span className="muted">正在查找对应底稿…</span>}
        {entries?.length === 0 && <Note tone="info">这个产品还没有可查看的核算底稿。需要在「BOM 报价核算」里关联同一个 ERP 产品编码，并且当前账号有底稿查阅权限。</Note>}
        <div className="ac-std-entries" role="radiogroup" aria-label="选择核算底稿">
          {(entries || []).map(e => (
            <button key={e.id} type="button" role="radio" aria-checked={choice === e.id} className={'ac-entry' + (choice === e.id ? ' on' : '')} onClick={() => onChoice(e.id)}>
              <span className="ac-radio" />
              <span><b className="mono">{e.cp_code}</b><span className={'ac-tag ' + entryTone(e.status)}>{e.status}</span></span>
              <span className="muted">核算日期 {fmtDate(e.calc_date)}</span>
              <span className="muted ac-ellipsis" title={e.src_file}>来源 {e.src_file || '—'}</span>
            </button>
          ))}
        </div>
        {entries?.length > 1 && choice == null && <span className="ac-amber">有 {entries.length} 份底稿，请选一份再比较（不会自动取最新的）。</span>}
      </div>

      {error && <Note tone="bad">{error}</Note>}
      {busy && <div className="ac-loading" role="status">正在逐项关联…</div>}

      {data && <>
        {/* 来源与版本差异：常显 */}
        {(data.standard.provenance_note || !!draft.length) && <Note tone="warn"><div>
          {data.standard.provenance_note && <div><b>底稿来源提示　</b>{data.standard.provenance_note}</div>}
          {draft.map((s, i) => <div key={i}>{s}</div>)}
        </div></Note>}
        {data.issue && <Note tone="bad">{data.issue}</Note>}
        {!data.ready && !data.issue && <Note tone="info" action={<button className="btn-pri" disabled={fetching || !canFetch} onClick={onFetch}>{fetching ? '正在读取金蝶…' : '读取金蝶明细'}</button>}>
          还没有这个产品的实际领料明细，下面只有底稿一侧的数。读取金蝶明细后才能逐料对比。</Note>}

        {/* ── 材料衔接 + 底稿含税口径 ── */}
        <div className="ac-std-top">
          <div className="ac-card">
            <div className="ac-label">材料成本 <span className="muted">元/kg，不含税 · 完工 {fmt(data.product_qty)} kg</span></div>
            <div className="ac-bridge flat">
              <div className="ac-fig"><span>底稿材料成本</span><b title={fmtFull(data.standard_material_cost)}>{fmt(data.standard_material_cost, 4)}</b></div>
              <i className="vs">对比</i>
              <div className="ac-fig"><span>本期材料投入</span><b title={fmtFull(data.actual_material_input_per_kg)}>{fmt(data.actual_material_input_per_kg, 4)}</b></div>
              <i>＋</i>
              <div className="ac-fig"><span>投入转完工差额</span><b title={fmtFull(data.material_timing_bridge)}>{fmt(data.material_timing_bridge, 4)}</b></div>
              <i>＝</i>
              <div className="ac-fig strong"><span>完工材料</span><b title={fmtFull(data.completed_material_per_kg)}>{fmt(data.completed_material_per_kg, 4)}</b></div>
            </div>
            <p className="ac-p muted">「投入转完工差额」是期初、期末在产和跨期带来的影响，单独列示，不摊到某颗物料，也不算配方超耗。</p>
          </div>
          <div className="ac-card ac-std-incl">
            <div className="ac-label">底稿含税口径 <span className="muted">参考 · 元/kg</span></div>
            <div className="ac-kv2">
              <div className="ac-kv"><span>底稿全成本（含税）</span><b><Num v={data.standard_full_incl} d={4} /></b></div>
              <div className="ac-kv"><span>本期实际全成本（不含税）</span><b><Num v={data.actual_unit_cost} d={4} /></b></div>
              {FEES.map(([k, label]) => <div className="ac-kv" key={k}><span>其中 {label}</span><b><Num v={data.fees_incl?.[k]} d={4} /></b></div>)}
            </div>
            <p className="ac-p muted">底稿的加工费、管理费等和金蝶费用项目、税口径还没建立对应，<b>全成本差额暂不计算</b>。</p>
          </div>
        </div>

        {/* ── 逐料对比 ── */}
        {sections.map((section, n) => {
          const rows = data.rows.filter(r => r.section === section)
          const comparable = rows.filter(r => EFFECTS.every(k => isNum(r[k])))
          const sum = k => comparable.reduce((s, r) => s + r[k], 0)
          return <section key={section}>
            <div className="ac-h3">{'①②③④⑤⑥'[n]} {SECTION[section] || section}明细 <span className="muted">{rows.length} 项{rows.length - comparable.length ? ` · ${rows.length - comparable.length} 项暂不可比` : ''}</span></div>
            <div className="ac-data-wrap fit" tabIndex={0} role="region" aria-label={section + '标准与实际对比，可横向滚动'}>
              <table className="ac-data ac-cmp">
                <thead>
                  <tr className="g"><th rowSpan={2} className="pin-l">物料</th><th colSpan={2}>每公斤用量</th><th colSpan={2}>单价（不含税）</th><th colSpan={2}>每公斤成本</th><th colSpan={4}>差异拆解（实际 − 底稿）</th></tr>
                  <tr className="c"><th className="r">底稿</th><th className="r">实际净领用</th><th className="r">底稿计价</th><th className="r">实际出库</th><th className="r">底稿</th><th className="r">实际投入</th><th className="r">用量影响</th><th className="r">计价影响</th><th className="r">成本差额</th><th /></tr>
                </thead>
                <tbody>
                  {rows.map(r => { const id = section + r.code + r.unit, shown = open === id, list = shown ? prices(r.code) : []
                    return <React.Fragment key={id}>
                      <tr className={'ac-row' + (shown ? ' open' : '') + (r.status !== '匹配' ? ' ac-pending' : '')} onClick={() => setOpen(shown ? '' : id)}>
                        <td className="pin-l">
                          <button className="ac-prod" aria-expanded={shown} aria-label={`${r.code} ${r.name}：展开采购参考与涉及单据`}><span className="mono">{r.code}</span><b>{r.name}</b></button>
                          <div className="sub">单位 {r.unit}{r.status !== '匹配' && <span className={'ac-tag ' + statusTone(r.status)}>{r.status}</span>}</div>
                        </td>
                        <td className="num"><Num v={r.standard_qty} d={4} /></td><td className="num"><Num v={r.actual_qty} d={4} /></td>
                        <td className="num"><Num v={r.standard_price} d={4} /></td><td className="num"><Num v={r.actual_price} d={4} /></td>
                        <td className="num"><Num v={r.standard_cost} d={4} /></td><td className="num"><Num v={r.actual_cost} d={4} /></td>
                        <td className="num"><Num v={r.quantity_effect} d={4} signed tone /></td><td className="num"><Num v={r.price_effect} d={4} signed tone /></td>
                        <td className="num strong"><Num v={r.cost_difference} d={4} signed tone /></td>
                        <td className="ac-div">{isNum(r.cost_difference) && scale > 0 && <i className={r.cost_difference > 0 ? 'up' : 'down'} style={{ width: Math.abs(r.cost_difference) / scale * 50 + '%' }} />}</td>
                      </tr>
                      {shown && <tr className="ac-open"><td colSpan={11}>
                        <div className="ac-drill">
                          <div>
                            <div className="ac-label">三种价格，别混着比</div>
                            <div className="ac-kv"><span>底稿含税采购价{isNum(r.tax_rate) ? `（税率 ${fmtPct(r.tax_rate, 0)}）` : ''}</span><b><Num v={r.quote_price} d={4} /></b></div>
                            <div className="ac-kv"><span>底稿计价单价（不含税）</span><b><Num v={r.standard_price} d={4} /></b></div>
                            <div className="ac-kv"><span>实际出库单价（库存计价）</span><b><Num v={r.actual_price} d={4} /></b></div>
                            <div className="ac-kv"><span>本期涉及领补退料单</span><b>{r.bills?.length || 0} 张</b></div>
                            {!!r.bills?.length && <p className="ac-p muted mono ac-bills">{r.bills.slice(0, 10).join('、')}{r.bills.length > 10 ? ` 等 ${r.bills.length} 张` : ''}</p>}
                          </div>
                          <div>
                            <div className="ac-label">本期采购参考价 <span className="muted">已审核应付单 · {list.length} 条{list.length > 6 ? '，先列 6 条' : ''}</span>
                              {!!list.length && <button className="lk" onClick={e => { e.stopPropagation(); onPrices(r.code) }}>到「采购参考价」看全部 →</button>}</div>
                            {list.length ? <table className="ac-plain">
                              <thead><tr><th>日期</th><th>供应商</th><th className="r">数量</th><th>单位</th><th className="r">不含税单价</th><th className="r">含税单价</th><th className="r">税率</th></tr></thead>
                              <tbody>{list.slice(0, 6).map((p, i) => <tr key={i}><td>{fmtDate(p.date)}</td><td>{p.supplier}</td><td className="num"><Num v={p.qty} /></td><td>{p.unit}</td>
                                <td className="num"><Num v={p.price} d={4} /></td><td className="num"><Num v={p.tax_price} d={4} /></td><td className="num">{isNum(p.tax_rate) ? p.tax_rate + '%' : '—'}</td></tr>)}</tbody>
                            </table> : <p className="ac-p muted">{detail ? '本期没有这颗物料的已审核采购记录——没有记录不等于价格为零。' : '读取金蝶明细后显示。'}</p>}
                          </div>
                        </div>
                      </td></tr>}
                    </React.Fragment> })}
                </tbody>
                {!!comparable.length && <tfoot><tr>
                  <td className="pin-l">可比 {comparable.length} 项小计</td><td colSpan={6} className="muted">{rows.length - comparable.length ? '未匹配、单位不一致的行不进小计' : ''}</td>
                  {EFFECTS.map(k => <td key={k} className="num"><Num v={sum(k)} d={4} signed tone /></td>)}<td />
                </tr></tfoot>}
              </table>
            </div>
          </section>
        })}

        <details className="explain">
          <summary>比较口径、计算方法与底稿指纹</summary>
          <div className="explain-in">
            {method.map((s, i) => <p key={i}>{s}</p>)}
            <p>用量影响 ＝（实际每公斤净领用 − 底稿每公斤用量）× 底稿计价单价。计价影响 ＝ 实际每公斤净领用 ×（实际出库单价 − 底稿计价单价）。底稿计价单价 ＝ 底稿不含税行成本 ÷ 底稿用量。只有编码、单位唯一匹配且数量有效的行才计算差异。</p>
            <p className="ex-foot mono" style={{ overflowWrap: 'anywhere' }}>底稿指纹 {data.fingerprint}</p>
          </div>
        </details>
      </>}
    </div>
  )
}
