// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 单个产品的成本追溯：成本构成 → 核算底稿对比 → 工单用量 / 领补退料原单 / 采购参考价 / 工单配方 / 成本计算单。
// 原先是弹窗，现在整页打开：顶部固定产品身份和成本构成，下面页签切换；可上一个/下一个连着看，返回后主表状态不丢。
// 金蝶明细（领补退料、工单配方、采购参考价）按需读取并留存在工作台，金蝶端只读。
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api, COMPONENTS, GROUPS, groupOf, groupSum, LABELS, Note, Num, Pager, Tabs, cellText, fmt, fmtFull, fmtPct, fmtTime, isNum } from './actualCostShared.jsx'
import ActualCostStandard from './ActualCostStandard.jsx'

// 各明细页：列、列名改写、数字列小数位、页头说明
const VIEWS = {
  materials: {
    label: '工单用量参考', cols: ['wo', 'code', 'name', 'unit', 'qty', 'unit_cost', 'amount', 'bom', 'standard_qty', 'variance_qty', 'variance_rate', 'comparison_note'],
    rename: { qty: '净领用数量', amount: '净领用成本' }, tone: ['variance_qty', 'variance_rate'],
    hint: '实际用本期净领用，标准参考量取工单用料清单、按本期完工量折算。差异含在产和跨期因素，只供核查，不等于已判定的超耗或节约。点物料编码看它的采购记录。',
  },
  movements: { label: '领补退料原单', cols: ['kind', 'bill', 'date', 'wo', 'code', 'name', 'unit', 'net_qty', 'net_amount', 'status', 'entry_id'],
    hint: '净领用 ＝ 生产领料 ＋ 生产补料 − 生产退料；金额是库存出库成本。' },
  prices: { label: '采购参考价', cols: ['code', 'name', 'date', 'supplier', 'unit', 'qty', 'price', 'tax_price', 'tax_rate', 'currency', 'bill', 'entry_id'],
    hint: '同组织、本期已审核应付单的价格，按原币和计价单位显示。这是采购价，不是库存出库计价，不能直接代替耗用单价；没有记录不等于价格为零。' },
  bom: { label: '工单配方', cols: ['wo', 'bill', 'bom', 'code', 'name', 'unit', 'standard_qty', 'product_qty', 'product_unit', 'picked', 'repicked', 'returned', 'consumed', 'wip', 'dosage_type', 'fixed_scrap', 'modified_at', 'entry_id'],
    hint: '金蝶工单用料清单（PPBOM）原值，标准参考量对应「用料单产品数量」，未折算到本期完工量。它是独立参考，不是本次指定的标准——标准看「核算底稿对比」。' },
  cost_rows: { label: '成本计算单', cols: ['source_row', 'wo', 'level', 'item', 'expense', 'input_amount', 'qty', 'amount'],
    hint: '随月度快照保存的金蝶成本计算单。工单汇总、成本项目、子层是逐层展开的关系，各层金额不能相加。' },
}
const TEXT_KEYS = /^(wo|code|name|unit|bom|bill|kind|supplier|currency|status|cancel|level|item|expense|product|product_unit|dosage_type|comparison_note|date|modified_at|entry_id|source_row)$/
const SIZE = 100
const keyOf = p => `${p.cc}|${p.code}`

export default function ActualCostTrace({ org, year, period, runId, target, siblings, onNavigate, onClose, canFetch, trial, sourceTime, choice, onChoice }) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState('')
  const [tab, setTab] = useState('standard'), [q, setQ] = useState(''), [wo, setWo] = useState(''), [page, setPage] = useState(0)
  const [fromCompare, setFromCompare] = useState(false)
  const serial = useRef(0), top = useRef()
  const params = { org, year, period, run_id: runId, cc: target.cc, code: target.code }

  useEffect(() => {
    const ticket = ++serial.current; setBusy('load'); setError(''); setData(null); setQ(''); setWo(''); setPage(0); setFromCompare(false)
    api.product(params).then(d => { if (ticket === serial.current) setData(d) })
      .catch(e => { if (ticket === serial.current) setError(e.message) })
      .finally(() => { if (ticket === serial.current) setBusy('') })
    top.current?.scrollIntoView({ block: 'nearest' })
    return () => { serial.current++ }
  }, [org, year, period, runId, target.cc, target.code])
  useEffect(() => { const onKey = e => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) onClose() }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey) }, [onClose])

  const refresh = async () => {
    const ticket = ++serial.current; setBusy('fetch'); setError('')
    try { const r = await api.refreshProduct(params); if (ticket === serial.current) setData(d => ({ ...d, detail: r.detail })) }
    catch (e) { if (ticket === serial.current) setError(e.message) } finally { if (ticket === serial.current) setBusy('') }
  }
  const go = (key, filter = '', back = false) => { setTab(key); setQ(filter); setWo(''); setPage(0); setFromCompare(back) }

  const product = data?.product || target, detail = data?.detail
  const index = siblings.findIndex(p => keyOf(p) === keyOf(target))
  const view = VIEWS[tab]
  // 「比较说明」若每行都是同一句话，就不占一列，挪到表头说明里
  const sameNote = tab === 'materials' && detail?.materials?.length && new Set(detail.materials.map(r => r.comparison_note)).size === 1 ? detail.materials[0].comparison_note : ''
  const cols = view ? view.cols.filter(k => !(sameNote && k === 'comparison_note')) : []
  const raw = tab === 'cost_rows' ? data?.cost_rows || [] : tab === 'standard' ? [] : detail?.[tab] || []
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return raw.filter(r => (!wo || !r.wo || r.wo === wo) && (!needle || Object.values(r).some(v => String(v ?? '').toLowerCase().includes(needle))))
  }, [raw, q, wo])
  const orders = useMemo(() => [...new Set((data?.cost_rows || []).map(r => r.wo))], [data])
  const base = COMPONENTS.reduce((s, c) => s + Math.max(isNum(product[c.key]) ? product[c.key] : 0, 0), 0)
  const diff = detail?.controls.input_difference

  return (
    <div className="ac-trace" ref={top}>
      {/* ── 产品身份 + 上下一个 ── */}
      <div className="ac-trace-head">
        <button className="btn-sec" onClick={onClose} aria-label="返回全成本表">← 全成本表</button>
        <div className="ac-trace-title">
          <h2><span className="mono">{product.code}</span>{product.name}</h2>
          <div className="sub">{year}年{period}月 · 账簿 {org} · {product.cc}{product.group ? ' · ' + product.group : ''}{product.spec ? ' · ' + product.spec : ''} · 金蝶数据时点 {fmtTime(data?.source_time || sourceTime)}
            {trial && <span className="ac-tag amber">试算口径，尚未正式出表</span>}</div>
        </div>
        <div className="ac-trace-nav">
          <button className="btn-sec" disabled={index <= 0} onClick={() => onNavigate(siblings[index - 1])} title={index > 0 ? siblings[index - 1].name : ''}>‹ 上一个</button>
          <span className="muted">{index + 1} / {siblings.length}</span>
          <button className="btn-sec" disabled={index < 0 || index + 1 >= siblings.length} onClick={() => onNavigate(siblings[index + 1])} title={siblings[index + 1]?.name || ''}>下一个 ›</button>
        </div>
      </div>

      {/* ── 成本构成：总数 + 四段条 + 11 项逐项 ── */}
      <div className="ac-comp">
        <div className="ac-comp-nums">
          <div className="ac-fig strong"><span>单位成本 元/kg</span><b title={fmtFull(product.unit)}>{fmt(product.unit, 4)}</b></div>
          <div className="ac-fig"><span>全成本</span><b title={fmtFull(product.total)}>{fmt(product.total)}</b></div>
          <div className="ac-fig"><span>完工数量 kg</span><b>{fmt(product.qty)}</b></div>
          <div className="ac-fig"><span>金蝶完工成本（分摊前）</span><b>{fmt(product.gold_total)}</b></div>
        </div>
        <div className="ac-comp-bar" role="img" aria-label="成本构成占比">
          {GROUPS.map(g => { const v = groupSum(product, g.key); return v > 0 && base > 0 && <i key={g.key} style={{ width: (v / base * 100) + '%', background: g.color }} title={`${g.label} ${fmt(v)}（${fmtPct(v / base, 1)}）`}><span>{g.label} {fmtPct(v / base, 1)}</span></i> })}
        </div>
        <div className="ac-comp-items">
          {COMPONENTS.map(c => { const v = product[c.key]
            return <div key={c.key} className="ac-comp-item" style={{ '--g': groupOf(c.group).color }}>
              <span>{c.label}</span>
              <b><Num v={product.qty ? v / product.qty : null} d={4} /></b>
              <small title={fmtFull(v)}>{fmt(v)}{base > 0 && v > 0 ? ` · ${fmtPct(v / base, 1)}` : ''}</small>
            </div> })}
        </div>
      </div>

      {error && <Note tone="bad">{error}</Note>}

      {/* ── 金蝶明细取数状态 ── */}
      <div className="ac-fetch">
        {detail ? <>
          <span className={'ac-check-pill ' + (Math.abs(diff) > 0.01 ? 'bad' : 'ok')} title={`净领补退料 ${fmt(detail.controls.movement_amount, 4)} − 成本计算单材料投入 ${fmt(detail.controls.input_material, 4)} ＝ ${fmt(diff, 4)}`}>
            {Math.abs(diff) > 0.01 ? `材料投入对不上，差 ${fmt(diff)} 元` : `材料投入已对平 ${fmt(detail.controls.movement_amount)} 元 ✓`}</span>
          <span className="muted">明细取数 {fmtTime(detail.fetched_at)} · 领补退料 {detail.movements.length} 行 · 工单配方 {detail.bom.length} 行 · 采购参考 {detail.prices.length} 行</span>
        </> : <span className="muted">{busy === 'load' ? '正在读取…' : '成本计算单已随月度快照保存。逐料的领补退料、工单配方、采购参考价还没读取，读一次后会留在工作台。'}</span>}
        <button className={detail ? 'btn-sec' : 'btn-pri'} disabled={!!busy || !canFetch} onClick={refresh} title={canFetch ? '只读金蝶，不回写' : '当前账号没有取数权限'}>
          {busy === 'fetch' ? '正在读取金蝶…' : detail ? '重新读取金蝶明细' : '读取金蝶明细'}</button>
      </div>

      <Tabs label="成本明细类别" value={tab} onChange={k => go(k)} tabs={[
        ['standard', '核算底稿对比'],
        ...Object.entries(VIEWS).map(([k, v]) => [k, v.label, k === 'cost_rows' ? data?.cost_rows?.length : detail?.[k]?.length]),
      ]} />

      {tab === 'standard'
        ? <ActualCostStandard org={org} year={year} period={period} runId={runId} target={target} detail={detail} choice={choice} onChoice={onChoice}
            canFetch={canFetch} fetching={busy === 'fetch'} onFetch={refresh} onPrices={code => go('prices', code, true)} />
        : <div className="ac-pane">
          <div className="ac-toolbar">
            <div className="ac-toolbar-l">
              {fromCompare && <button className="btn-sec" onClick={() => go('standard')}>← 回底稿对比</button>}
              <input className="ac-input" type="search" placeholder="搜物料编码、工单或单据号" aria-label="搜索明细" value={q} onChange={e => { setQ(e.target.value); setPage(0) }} />
              <select aria-label="按工单筛选" value={wo} onChange={e => { setWo(e.target.value); setPage(0) }}><option value="">全部工单（{orders.length}）</option>{orders.map(x => <option key={x}>{x}</option>)}</select>
            </div>
            <span className="muted">{rows.length === raw.length ? `${raw.length} 行` : `${rows.length} / ${raw.length} 行`}</span>
          </div>
          <p className="ac-p muted">{view.hint}</p>
          <div className="ac-data-wrap" tabIndex={0} role="region" aria-label={view.label + '，可横向滚动'}>
            <table className="ac-data">
              <thead><tr>{cols.map(k => <th key={k} className={TEXT_KEYS.test(k) ? '' : 'r'}>{view.rename?.[k] || LABELS[k] || k}</th>)}</tr></thead>
              <tbody>
                {rows.slice(page * SIZE, (page + 1) * SIZE).map((r, i) => <tr key={i} className={tab === 'cost_rows' ? 'lv-' + ['工单汇总', '成本项目'].indexOf(r.level) : ''}>
                  {cols.map(k => {
                    const v = r[k], text = TEXT_KEYS.test(k)
                    if (tab === 'materials' && k === 'code') return <td key={k}><button className="lk mono" onClick={() => go('prices', r.code)}>{r.code}</button></td>
                    if (k === 'kind') return <td key={k}><span className={'ac-tag ' + (v === '生产退料' ? 'violet' : v === '生产补料' ? 'blue' : 'gray')}>{v}</span></td>
                    const tone = view.tone?.includes(k) && isNum(v) ? (v > 0 ? ' ac-up' : v < 0 ? ' ac-down' : '') : ''
                    return <td key={k} className={(text ? (k === 'comparison_note' ? 'wrap' : /code|bill|wo|entry_id|source_row|bom/.test(k) ? 'mono' : '') : 'num') + tone}
                      title={isNum(v) && !text ? fmtFull(v) : undefined}>{cellText(v, k, !text)}</td>
                  })}</tr>)}
                {!rows.length && <tr><td colSpan={cols.length} className="ac-empty">{busy ? '正在读取…' : detail || tab === 'cost_rows' ? '没有符合条件的记录。' : '请先点上方「读取金蝶明细」。'}</td></tr>}
              </tbody>
            </table>
          </div>
          <Pager page={page} size={SIZE} total={rows.length} onPage={setPage} busy={!!busy} />
        </div>}
      {!!detail?.notes?.length && <details className="explain"><summary>取数及差异口径说明</summary><div className="explain-in">{detail.notes.map((n, i) => <p key={i}>{n}</p>)}</div></details>}
    </div>
  )
}
