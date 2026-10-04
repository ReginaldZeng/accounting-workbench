// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.791（全成本溯源）
// 直接材料拆分（原「核算底稿对比」）：把本期净领用按子项物料汇总到产品，折成每公斤完工产品的单耗、单价、成本——
// 没有底稿也能看；选了核算底稿就并排对比，带用量 / 单价 / 整体三个差异率、差异原因和合计（版式照用户的 Excel）。
// 本期自制的半成品（如高浓豆浆）可以就地展开它自己的用料。数值全部来自后端，这里不另算口径；
// 仅有的两处加工：半成品子项按「本产品对它的单耗」折算、以及由此得到的「其余（人工制费及计价差）」。
// V2.792：单位不一致的料可以填换算系数、底稿里没有的料可以指成某颗料的替代料——都由人来定，系统不猜；设了才并，行上写明，随时可撤。
// V2.793：加「生产 BOM」一层（工单用料清单折成每公斤标准单耗），用量差异拆成 配方（报价→BOM）和 生产（BOM→实际）两段，各找各的责任人。
// 规矩（沿用交接）：多份底稿不替人挑最新；空值显示「—」不显示 0；采购价、底稿计价、出库计价分开标；来源与版本提示常显。
import React, { useEffect, useState } from 'react'
import { api, Note, Num, Seg, fmt, fmtDate, fmtFull, fmtPct, isNum } from './actualCostShared.jsx'

const SECTION = { 原料: '原料', 包材: '包材', 实际新增: '底稿里没有、本期实际领用的', 实际用料: '本期实际用料', 仅生产BOM: '只在生产 BOM 里、底稿和本期领用都没有的' }
const TAIL = ['实际新增', '仅生产BOM']
const FEES = [['mfg', '加工费'], ['load', '装卸费'], ['adm', '管理费']]
const QUIET = ['匹配', '实际用料']
const statusTone = s => (/新增/.test(s) ? 'violet' : 'amber')
const entryTone = s => (/定稿|已审核/.test(s) ? 'green' : /本地/.test(s) ? 'amber' : 'blue')
const Rate = ({ v }) => (isNum(v) ? <span className={v > 0 ? 'ac-up' : v < 0 ? 'ac-down' : 'ac-zero'} title={fmtFull(v)}>{(v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%'}</span> : <span className="ac-nil">—</span>)

export default function ActualCostStandard({ org, year, period, runId, target, detail, breakdown, notes, products, choice, onChoice,
  canFetch, canNote, fetching, onFetch, onPrices, onNotes }) {
  const [entries, setEntries] = useState(null), [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(''), [incl, setIncl] = useState(false)
  const [semi, setSemi] = useState({})          // 半成品展开：物料编码 → { busy, error, res }
  const [drafts, setDrafts] = useState({}), [saving, setSaving] = useState('')
  const [rev, setRev] = useState(0), [factor, setFactor] = useState(''), [aliasTo, setAliasTo] = useState(''), [mapBusy, setMapBusy] = useState(false)
  const base = { org, year, period, run_id: runId }

  useEffect(() => {
    let live = true; setEntries(null); setData(null); setError(''); setOpen(''); setSemi({}); setDrafts({})
    api.standards(org, target.code).then(d => {
      if (!live) return
      setEntries(d.rows)
      if (choice == null && d.rows.length === 1) onChoice(d.rows[0].id)   // 只有一份时直接用它（照样写明是哪一份）；多份必须由人来选
    }).catch(e => { if (live) { setEntries([]); if (!/没有核算底稿查阅权限/.test(e.message)) setError(e.message) } })
    return () => { live = false }
  }, [org, target.code, target.cc])
  useEffect(() => {
    let live = true; setData(null)
    if (choice == null) return
    setBusy(true); setError('')
    api.compare({ ...base, cc: target.cc, code: target.code, entry_id: choice })
      .then(d => { if (live) setData(d) }).catch(e => { if (live) setError(e.message) }).finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [choice, org, year, period, runId, target.code, target.cc, detail, rev])

  const view = data || breakdown || { rows: [] }, overlay = !!data
  const sections = [...new Set(view.rows.map(r => r.section))].sort((a, b) => TAIL.indexOf(a) - TAIL.indexOf(b))
  const draftNotes = data ? (data.issues || []).filter(s => s !== data.standard.provenance_note && /定稿/.test(s)) : []
  const method = data ? (data.issues || []).filter(s => s !== data.standard.provenance_note && !/定稿/.test(s)) : []
  const prices = code => (detail?.prices || []).filter(p => p.code === code)
  // 这颗料本期是不是本账簿自己做的（半成品）：是的话可以展开它自己的用料。同编码多个车间时取产量最大的那个。
  const makerOf = code => (products || []).filter(p => p.code === code && p.code !== target.code && p.qty > 0).sort((a, b) => b.qty - a.qty)[0]
  const price = r => (incl ? r.actual_price_incl : r.actual_price), cost = r => (incl ? r.actual_cost_incl : r.actual_cost)
  const sPrice = r => (incl ? r.quote_price : r.standard_price), sCost = r => (incl ? r.standard_cost_incl : r.standard_cost)
  const cols = overlay ? 15 : 8, gap = overlay ? 9 : 2    // gap＝实际四列之后、备注之前的列数（半成品子行用来占位）

  const loadSemi = async (code, maker, refresh) => {
    setSemi(s => ({ ...s, [code]: { ...(s[code] || {}), busy: true, error: '' } }))
    try {
      const p = { ...base, cc: maker.cc, code }
      if (refresh) await api.refreshProduct(p)
      const res = await api.product(p)
      setSemi(s => ({ ...s, [code]: { busy: false, res } }))
    } catch (e) { setSemi(s => ({ ...s, [code]: { ...(s[code] || {}), busy: false, error: e.message } })) }
  }
  const toggle = (id, r) => {
    const next = open === id ? '' : id
    setOpen(next); setFactor(''); setAliasTo('')
    const maker = makerOf(r.code)
    if (next && maker && !semi[r.code]?.res) loadSemi(r.code, maker, false)
  }
  // 保存 / 撤销一条匹配设定（单位换算或替代料），成功后重新对比
  const saveMap = async body => {
    setMapBusy(true); setError('')
    try { await api.saveMap(org, body); setFactor(''); setAliasTo(''); setRev(v => v + 1) }
    catch (e) { setError('匹配设定没存上：' + e.message) } finally { setMapBusy(false) }
  }
  const noteKey = r => `${r.code}|${r.unit}`
  const saveNote = async r => {
    const key = noteKey(r), text = drafts[key]
    if (text === undefined || text.trim() === (notes?.[key]?.text || '')) return
    setSaving(key)
    try { const res = await api.saveNote({ ...base, cc: target.cc, code: target.code }, { material: key, text }); onNotes(res.notes); setDrafts(d => { const n = { ...d }; delete n[key]; return n }) }
    catch (e) { setError('差异原因没存上：' + e.message) } finally { setSaving('') }
  }

  return (
    <div className="ac-pane ac-std">
      {/* ── 选底稿（可不选）+ 含税/不含税 ── */}
      <div className="ac-std-pick">
        <div className="ac-toolbar">
          <div className="ac-label">对比哪份核算底稿 <span className="muted">不选也能看本期实际的拆分；选了只用于本次查看，不是月度标准冻结，也不改变底稿的审核状态</span></div>
          <Seg label="金额口径" value={incl ? 'incl' : 'excl'} onChange={v => setIncl(v === 'incl')}
            options={[['excl', '不含税'], ['incl', '含税', '底稿用含税采购价；实际成本按这颗料在底稿里的「含税价 ÷ 不含税计价」折成含税，底稿里没有的按本期应付单税率折']]} />
        </div>
        {entries === null && !error && <span className="muted">正在查找对应底稿…</span>}
        {entries?.length === 0 && <span className="muted">这个产品还没有可查看的核算底稿（要在「BOM 报价核算」里关联同一个 ERP 产品编码，且当前账号有底稿查阅权限）。下面是本期实际的拆分。</span>}
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
        {entries?.length > 1 && choice == null && <span className="ac-amber">有 {entries.length} 份底稿，请选一份再对比（不会自动取最新的）。</span>}
      </div>

      {error && <Note tone="bad">{error}</Note>}
      {busy && <div className="ac-loading" role="status">正在逐项关联…</div>}
      {data && (data.standard.provenance_note || !!draftNotes.length) && <Note tone="warn"><div>
        {data.standard.provenance_note && <div><b>底稿来源提示　</b>{data.standard.provenance_note}</div>}
        {draftNotes.map((s, i) => <div key={i}>{s}</div>)}
      </div></Note>}
      {view.issue && <Note tone="bad">{view.issue}</Note>}
      {!view.ready && !view.issue && <Note tone="info" action={<button className="btn-pri" disabled={fetching || !canFetch} onClick={onFetch}>{fetching ? '正在读取金蝶…' : '读取金蝶明细'}</button>}>
        还没有读取这个产品的领料明细{overlay ? '，下面只有底稿一侧的数' : ''}。读取一次后留在工作台，就能按子项物料拆分。</Note>}

      {/* ── 直接材料拆分表 ── */}
      {!!view.rows.length && <div className="ac-data-wrap fit" tabIndex={0} role="region" aria-label="直接材料按子项物料拆分，可横向滚动">
        <table className="ac-data ac-cmp">
          <thead>
            <tr className="g">
              <th rowSpan={2} className="pin-l">子项物料 <span className="ac-hint">点一行看采购记录{overlay ? '和影响金额' : ''}</span></th>
              <th colSpan={4}>本期实际（金蝶）· 每公斤产品</th>
              <th title="金蝶工单用料清单的标准用量，按本期完工量折成每公斤；只有用量，没有价格">生产 BOM</th>
              {overlay && <th colSpan={3}>底稿（报价）· 每公斤产品</th>}
              <th colSpan={overlay ? 5 : 1}>差异率</th>
              <th rowSpan={2} className="note">差异原因 / 备注</th>
            </tr>
            <tr className="c">
              <th className="r">单耗</th><th className="r">{incl ? '含税单价' : '出库单价'}</th><th className="r">{incl ? '含税成本' : '单位成本'}</th><th className="r">占比</th>
              <th className="r">标准单耗</th>
              {overlay && <><th className="r">单耗</th><th className="r">{incl ? '含税单价' : '计价单价'}</th><th className="r">{incl ? '含税成本' : '成本'}</th>
                <th className="r" title="生产 BOM 单耗 ÷ 底稿单耗 − 1：配方或报价假设变了多少（找研发、BP）">配方<small>报价→BOM</small></th></>}
              <th className="r" title="实际单耗 ÷ 生产 BOM 单耗 − 1：车间超耗还是节约（找生产）。含在产和跨期影响">生产<small>BOM→实际</small></th>
              {overlay && <><th className="r" title="实际单耗 ÷ 底稿单耗 − 1，等于前两段连乘">用量合计</th>
                <th className="r" title="实际出库单价 ÷ 底稿计价单价 − 1：采购价变了多少（找采购）">单价</th><th className="r" title="实际成本 ÷ 底稿成本 − 1">整体</th></>}
            </tr>
          </thead>
          <tbody>
            {sections.map(section => {
              const rows = view.rows.filter(r => r.section === section)
              return <React.Fragment key={section}>
                {(overlay || sections.length > 1) && <tr className="ac-sec-row"><td className="pin-l">{SECTION[section] || section} <span className="muted">{rows.length} 项</span></td><td colSpan={cols - 1} /></tr>}
                {rows.map(r => {
                  const id = section + r.code + r.unit, shown = open === id, list = shown ? prices(r.code) : []
                  const maker = makerOf(r.code), sm = semi[r.code], kids = shown && maker && sm?.res?.breakdown?.ready ? sm.res.breakdown.rows : null
                  const scale = r.unit === 'kg' && isNum(r.actual_qty) ? r.actual_qty : null     // 每公斤本产品用了多少公斤半成品
                  const kidCost = k => (isNum(incl ? k.actual_cost_incl : k.actual_cost) && scale != null ? (incl ? k.actual_cost_incl : k.actual_cost) * scale : null)
                  const rest = kids && scale != null && isNum(cost(r)) && kids.every(k => isNum(kidCost(k))) ? cost(r) - kids.reduce((s, k) => s + kidCost(k), 0) : null
                  const key = noteKey(r), saved = notes?.[key]
                  return <React.Fragment key={id}>
                    <tr className={'ac-row' + (shown ? ' open' : '') + (!QUIET.includes(r.status) ? ' ac-pending' : '')} onClick={() => toggle(id, r)}>
                      <td className="pin-l">
                        <button className="ac-prod" aria-expanded={shown} aria-label={`${r.code} ${r.name}：展开明细`}><span className="mono">{r.code}</span><b>{r.name}</b></button>
                        <div className="sub">单位 {r.unit}{maker && <span className="ac-tag blue" title={`本期 ${maker.cc} 自制，点开可看它自己的用料`}>本期自制</span>}
                          {!QUIET.includes(r.status) && <span className={'ac-tag ' + statusTone(r.status)}>{r.status}</span>}
                          {(r.merged || []).map(m => <span key={m.code + m.unit} className="ac-tag green" title="人工设定的匹配，点开这一行可以撤销">
                            {m.alias ? `含替代料 ${m.code}` : `已换算 ${m.unit}→${m.to_unit}`}</span>)}</div>
                      </td>
                      <td className="num"><Num v={r.actual_qty} d={4} /></td><td className="num"><Num v={price(r)} d={4} /></td>
                      <td className="num strong"><Num v={cost(r)} d={4} /></td><td className="num">{isNum(r.actual_share) ? fmtPct(r.actual_share, 1) : <span className="ac-nil">—</span>}</td>
                      <td className="num" title={r.bom_note || (r.bom_orders ? `${r.bom_orders} 张工单的用料清单` : '')}><Num v={r.bom_qty} d={4} /></td>
                      {overlay && <><td className="num"><Num v={r.standard_qty} d={4} /></td><td className="num"><Num v={sPrice(r)} d={4} /></td><td className="num strong"><Num v={sCost(r)} d={4} /></td>
                        <td className="num"><Rate v={r.design_rate} /></td></>}
                      <td className="num"><Rate v={r.bom_rate} /></td>
                      {overlay && <><td className="num strong"><Rate v={r.qty_rate} /></td><td className="num"><Rate v={r.price_rate} /></td><td className="num strong"><Rate v={r.cost_rate} /></td></>}
                      <td className="note" onClick={e => e.stopPropagation()}>
                        <input className="ac-note-in" aria-label={`${r.code} 差异原因`} disabled={!canNote || saving === key} maxLength={500}
                          placeholder={canNote ? '写原因，回车或点别处保存' : ''} title={saved ? `${saved.by} · ${fmtDate(saved.at)}` : ''}
                          value={drafts[key] ?? saved?.text ?? ''} onChange={e => setDrafts(d => ({ ...d, [key]: e.target.value }))}
                          onBlur={() => saveNote(r)} onKeyDown={e => { if (e.key === 'Enter') e.target.blur() }} />
                      </td>
                    </tr>
                    {/* 半成品自己的用料：按「每公斤本产品用了多少公斤半成品」折算后列在下面 */}
                    {kids && kids.map(k => <tr className="ac-child" key={id + k.code + k.unit}>
                      <td className="pin-l"><span className="mono">{k.code}</span> {k.name}<div className="sub">单位 {k.unit}</div></td>
                      <td className="num"><Num v={scale != null && isNum(k.actual_qty) ? k.actual_qty * scale : null} d={4} /></td>
                      <td className="num"><Num v={incl ? k.actual_price_incl : k.actual_price} d={4} /></td>
                      <td className="num"><Num v={kidCost(k)} d={4} /></td><td className="num"><span className="ac-nil">—</span></td>
                      <td colSpan={gap} /><td className="note muted">{r.name} 的用料</td>
                    </tr>)}
                    {kids && <tr className="ac-child rest">
                      <td className="pin-l">其余：{r.name} 的人工、制造费用及计价差<div className="sub">本产品领用成本 − 上面各料折算成本</div></td>
                      <td /><td /><td className="num"><Num v={rest} d={4} /></td><td /><td colSpan={gap} /><td className="note muted">{scale == null ? '领用单位不是千克，无法折算' : `含 ${r.name} 的加工费`}</td>
                    </tr>}
                    {shown && <tr className="ac-open"><td colSpan={cols}>
                      <div className="ac-drill">
                        <div>
                          {/* 匹配设定：单位换算 / 替代料。都由人定，系统不猜 */}
                          {overlay && (() => {
                            const std = view.rows.filter(x => x.section !== '实际新增')
                            const twin = view.rows.find(x => x.code === r.code && x.unit !== r.unit && (x.section === '实际新增') !== (r.section === '实际新增'))
                            const act = r.section === '实际新增' ? r : twin, base0 = r.section === '实际新增' ? twin : r
                            const targets = std.filter(x => x.unit === r.unit && x.code !== r.code)
                            return <>
                              {(r.merged || []).map(m => <div className="ac-map-set done" key={m.code + m.unit}>
                                <span>{m.alias && <>已把 <b className="mono">{m.code}</b> {m.name} 当作这颗料的<b>替代料</b>并进来</>}
                                  {m.alias && m.factor != null && '，并'}{m.factor != null && <>按 <b>1 {m.unit} ＝ {m.factor} {m.to_unit}</b> 换算</>}
                                  ：原 {fmt(m.qty_per_kg, 4)} {m.unit}/kg，{fmt(m.cost_per_kg, 4)} 元/kg。</span>
                                {canNote && m.alias && <button className="btn-sec" disabled={mapBusy} onClick={e => { e.stopPropagation(); saveMap({ kind: 'alias', code: m.code, to: '' }) }}>撤销替代料</button>}
                                {canNote && m.factor != null && <button className="btn-sec" disabled={mapBusy} onClick={e => { e.stopPropagation(); saveMap({ kind: 'unit', code: m.code, factor: '' }) }}>撤销换算</button>}
                              </div>)}
                              {r.status === '单位不一致，待换算' && act && base0 && <div className="ac-map-set">
                                <span><b>单位不一致</b>：底稿按 <b>{base0.unit}</b> 记，金蝶领料按 <b>{act.unit}</b> 记，系统不知道怎么折，所以没比。你填一个换算系数，两行就并成一行再比：</span>
                                <span className="ac-map-form">1 {act.unit} ＝
                                  <input className="ac-input sm" type="number" min="0" step="any" aria-label="换算系数" disabled={!canNote || mapBusy} value={factor}
                                    onClick={e => e.stopPropagation()} onChange={e => setFactor(e.target.value)} /> {base0.unit}
                                  <button className="btn-pri" disabled={!canNote || mapBusy || !(Number(factor) > 0)}
                                    onClick={e => { e.stopPropagation(); saveMap({ kind: 'unit', code: r.code, from: act.unit, to: base0.unit, factor: Number(factor) }) }}>保存换算</button>
                                </span>
                                <span className="muted">这个系数跟物料走，对本账簿所有产品、所有月份都生效；之后可以撤销。</span>
                              </div>}
                              {r.section === '实际新增' && r.status === '实际新增用料' && <div className="ac-map-set">
                                <span><b>底稿里没有这颗料</b>。如果它其实是底稿里某颗料的替代料（比如同一种包装袋换了编码），指给它，用量和成本就合并后再比：</span>
                                <span className="ac-map-form">
                                  <select aria-label="选择它替代的是哪颗料" disabled={!canNote || mapBusy} value={aliasTo} onClick={e => e.stopPropagation()} onChange={e => setAliasTo(e.target.value)}>
                                    <option value="">它替代的是…</option>
                                    {targets.map(x => <option key={x.code} value={x.code}>{x.code} {x.name}（{x.unit}）</option>)}
                                  </select>
                                  <button className="btn-pri" disabled={!canNote || mapBusy || !aliasTo}
                                    onClick={e => { e.stopPropagation(); saveMap({ kind: 'alias', code: r.code, to: aliasTo }) }}>设为替代料</button>
                                </span>
                                <span className="muted">{targets.length ? '只列出单位相同的底稿物料。对本账簿所有产品、所有月份都生效；之后可以撤销。' : '底稿里没有单位相同的物料可以并。'}</span>
                              </div>}
                            </>
                          })()}
                          {maker && <div className="ac-semi">
                            <b>本期自制：{maker.cc}</b>　完工 {fmt(maker.qty)} kg，单位成本 {fmt(maker.unit, 4)} 元/kg。
                            {sm?.busy && <span className="muted">　正在读取它的用料…</span>}
                            {sm?.error && <span className="ac-up">　{sm.error}</span>}
                            {sm?.res && !sm.res.breakdown?.ready && !sm.busy && <>　它的领料明细还没读取。
                              <button className="btn-sec" disabled={!canFetch} onClick={e => { e.stopPropagation(); loadSemi(r.code, maker, true) }}>读取它的金蝶明细</button></>}
                            {kids && <span className="muted">　它的 {kids.length} 颗用料已列在上面。</span>}
                          </div>}
                          <div className="ac-label">三种价格，别混着比</div>
                          {overlay && <div className="ac-kv"><span>底稿含税采购价{isNum(r.tax_rate) ? `（税率 ${fmtPct(r.tax_rate, 0)}）` : ''}</span><b><Num v={r.quote_price} d={4} /></b></div>}
                          {overlay && <div className="ac-kv"><span>底稿计价单价（不含税）</span><b><Num v={r.standard_price} d={4} /></b></div>}
                          <div className="ac-kv"><span>实际出库单价（库存计价，不含税）</span><b><Num v={r.actual_price} d={4} /></b></div>
                          <div className="ac-kv"><span>生产 BOM 标准单耗{r.bom_orders ? `（${r.bom_orders} 张工单）` : ''}</span><b>{isNum(r.bom_qty) ? `${fmt(r.bom_qty, 4)} ${r.unit}/kg` : <span className="ac-nil">{r.bom_note || '—'}</span>}</b></div>
                          <div className="ac-kv"><span>本期净领用</span><b>{isNum(r.net_qty) ? `${fmt(r.net_qty)} ${r.unit} · ${fmt(r.net_amount)} 元` : '—'}</b></div>
                          {overlay && <><div className="ac-kv"><span>用量影响（元/kg）</span><b><Num v={r.quantity_effect} d={4} signed tone /></b></div>
                            <div className="ac-kv"><span>计价影响（元/kg）</span><b><Num v={r.price_effect} d={4} signed tone /></b></div>
                            <div className="ac-kv"><span>成本差额（元/kg）</span><b><Num v={r.cost_difference} d={4} signed tone /></b></div></>}
                          <div className="ac-kv"><span>本期涉及领补退料单</span><b>{r.bills?.length || 0} 张</b></div>
                        </div>
                        <div>
                          <div className="ac-label">本期采购参考价 <span className="muted">已审核应付单 · {list.length} 条{list.length > 6 ? '，先列 6 条' : ''}</span>
                            {!!list.length && <button className="lk" onClick={e => { e.stopPropagation(); onPrices(r.code) }}>到「采购参考价」看全部 →</button>}</div>
                          {list.length ? <table className="ac-plain">
                            <thead><tr><th>日期</th><th>供应商</th><th className="r">数量</th><th>单位</th><th className="r">不含税单价</th><th className="r">含税单价</th><th className="r">税率</th></tr></thead>
                            <tbody>{list.slice(0, 6).map((p, i) => <tr key={i}><td>{fmtDate(p.date)}</td><td>{p.supplier}</td><td className="num"><Num v={p.qty} /></td><td>{p.unit}</td>
                              <td className="num"><Num v={p.price} d={4} /></td><td className="num"><Num v={p.tax_price} d={4} /></td><td className="num">{isNum(p.tax_rate) ? p.tax_rate + '%' : '—'}</td></tr>)}</tbody>
                          </table> : <p className="ac-p muted">{!detail ? '读取金蝶明细后显示。' : maker ? '本期自制的半成品，没有采购记录。' : '本期没有这颗物料的已审核采购记录——没有记录不等于价格为零。'}</p>}
                        </div>
                      </div>
                    </td></tr>}
                  </React.Fragment>
                })}
              </React.Fragment>
            })}
          </tbody>
          <tfoot><tr>
            <td className="pin-l">合计（直接材料 · 元/kg）</td><td /><td />
            <td className="num"><Num v={incl ? view.actual_material_incl : view.actual_material_input_per_kg} d={4} /></td><td className="num">{view.ready ? '100%' : ''}</td>
            <td className="muted" title="生产 BOM 只有用量、没有价格，所以没有成本合计" />
            {overlay ? <><td /><td /><td className="num"><Num v={incl ? view.standard_material_incl : view.standard_material_cost} d={4} /></td>
              <td /><td /><td /><td /><td className="num" title="按不含税口径：本期材料投入 ÷ 底稿材料成本 − 1"><Rate v={view.material_cost_rate} /></td></> : <td />}
            <td className="note muted">{incl && view.actual_incl_unknown ? `有 ${view.actual_incl_unknown} 颗料没有税率依据，没计入含税合计` : ''}</td>
          </tr></tfoot>
        </table>
      </div>}

      {/* ── 材料投入 → 完工材料的衔接；底稿含税口径 ── */}
      {view.ready && <div className="ac-std-top">
        <div className="ac-card">
          <div className="ac-label">从材料投入到完工材料 <span className="muted">元/kg，不含税 · 完工 {fmt(view.product_qty)} kg</span></div>
          <div className="ac-bridge flat">
            <div className="ac-fig"><span>本期材料投入</span><b title={fmtFull(view.actual_material_input_per_kg)}>{fmt(view.actual_material_input_per_kg, 4)}</b></div>
            <i>＋</i>
            <div className="ac-fig"><span>投入转完工差额</span><b title={fmtFull(view.material_timing_bridge)}>{fmt(view.material_timing_bridge, 4)}</b></div>
            <i>＝</i>
            <div className="ac-fig strong"><span>完工材料</span><b title={fmtFull(view.completed_material_per_kg)}>{fmt(view.completed_material_per_kg, 4)}</b></div>
          </div>
          <p className="ac-p muted">上表的「实际」是本期净领用，合计等于「本期材料投入」。「投入转完工差额」是期初、期末在产和跨期带来的影响，单独列示，不摊到某颗物料，也不算配方超耗。</p>
        </div>
        {overlay && <div className="ac-card ac-std-incl">
          <div className="ac-label">底稿含税口径 <span className="muted">参考 · 元/kg</span></div>
          <div className="ac-kv2">
            <div className="ac-kv"><span>底稿全成本（含税）</span><b><Num v={data.standard_full_incl} d={4} /></b></div>
            <div className="ac-kv"><span>本期实际全成本（不含税）</span><b><Num v={data.actual_unit_cost} d={4} /></b></div>
            {FEES.map(([k, label]) => <div className="ac-kv" key={k}><span>其中 {label}</span><b><Num v={data.fees_incl?.[k]} d={4} /></b></div>)}
          </div>
          <p className="ac-p muted">底稿的加工费、管理费等和金蝶费用项目、税口径还没建立对应，<b>全成本差额暂不计算</b>。</p>
        </div>}
      </div>}

      <details className="explain">
        <summary>口径说明{overlay ? '、计算方法与底稿指纹' : ''}</summary>
        <div className="explain-in">
          <p>单耗 ＝ 本期净领用数量 ÷ 本期完工公斤；单位成本 ＝ 本期净领用金额 ÷ 本期完工公斤；出库单价 ＝ 单位成本 ÷ 单耗（库存出库计价，不是采购价）。含在产和跨期影响，不直接判定超耗。</p>
          <p>含税口径：底稿用含税采购价；实际成本按这颗料在底稿里的「含税采购价 ÷ 不含税计价」折成含税（专票约为 1＋税率，普票为 1），底稿里没有的料按本期应付单税率折，都没有就不折。差异率在含税、不含税下是一样的。</p>
          <p>生产 BOM ＝ 金蝶各工单用料清单的标准用量，按「÷ 用料单产品数量 × 本期完工量」逐工单折算后汇总，再除以完工公斤；只有用量，没有价格。用量差异拆两段：配方（报价→BOM）＝ BOM 单耗 ÷ 底稿单耗 − 1，生产（BOM→实际）＝ 实际单耗 ÷ BOM 单耗 − 1，两段连乘等于用量合计。某张工单的用料清单是固定用量、带固定损耗、未审核或一单多版本时，这颗料的 BOM 不出数。主料和替代料在用料清单里各列足量，设了替代料后每张工单取较大的一行，不相加。</p>
          {method.map((s, i) => <p key={i}>{s}</p>)}
          {overlay && <p>用量影响 ＝（实际单耗 − 底稿单耗）× 底稿计价单价。计价影响 ＝ 实际单耗 ×（实际出库单价 − 底稿计价单价）。只有编码、单位唯一匹配且数量有效的行才计算差异。</p>}
          {overlay && <p className="ex-foot mono" style={{ overflowWrap: 'anywhere' }}>底稿指纹 {data.fingerprint}</p>}
        </div>
      </details>
    </div>
  )
}
