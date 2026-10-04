// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 产品全成本主页。沿用 Codex V-draft 的接口与出表规则（金蝶入账金额为准、由用户确认已结账后出表、试产工单单列确认），只重做界面：
//   · 顶部一条「本期依据 → 取数试算 → 确认已结账 → 正式全成本表」，当前卡在哪一步、下一步点什么一眼可见；
//   · 待办逐条列出并能直接跳到处理的地方，不再是一段文字；
//   · 全成本表产品列、合计列两头固定，中间按 材料/人工/制造费用 分组横向滚动，可切「金额 / 每公斤」；
//   · 本期依据、在产调整、金蝶原始数据各占一个页签；点产品进整页明细（不再是弹窗），返回时筛选和底稿选择都还在。
import React, { useEffect, useMemo, useRef, useState } from 'react'
import './actualCost.css'
import { api, COMPONENTS, GROUPS, groupOf, MixBar, Note, Num, Seg, Tabs, fmt, fmtPct, fmtTime, isNum } from './actualCostShared.jsx'
import ActualCostInputs from './ActualCostInputs.jsx'
import ActualCostSources from './ActualCostSources.jsx'
import ActualCostTrace from './ActualCostTrace.jsx'

const STATUS = {
  not_generated: ['尚未取数', 'gray'], running: ['正在生成', 'blue'], waiting_close: ['待结账', 'amber'], reopened: ['已撤销结账确认', 'amber'],
  needs_inputs: ['待补充或重算', 'amber'], needs_confirmation: ['试算 · 待确认', 'amber'], ready: ['正式 · 校验通过', 'green'],
  failed: ['生成失败', 'red'], unverified: ['结账状态待核实', 'amber'],
}
const pct = v => String(+(v * 100).toFixed(8))   // 0.3973 → "39.73"，避免浮点尾巴进输入框
const keyOf = p => `${p.cc}|${p.code}`

export default function ActualCost({ org, year, period, user }) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState('')
  const [form, setForm] = useState(null)
  const [tab, setTab] = useState('table')
  const [target, setTarget] = useState(null)             // 正在看的产品 "车间|编码"
  const [standardChoice, setStandardChoice] = useState({}) // 每个产品本次选了哪份底稿：只在本页记着，不落库、不是月度标准
  const [cc, setCc] = useState(''), [query, setQuery] = useState(''), [output, setOutput] = useState('all')
  const [mode, setMode] = useState('amount'), [sort, setSort] = useState(null)
  const [sourcePeriod, setSourcePeriod] = useState(`${period === 1 ? year - 1 : year}-${String(period === 1 ? 12 : period - 1).padStart(2, '0')}`)
  const serial = useRef(0), savedForm = useRef(''), confirmDialog = useRef()
  const can = permission => user?.role === 'admin' || !!user?.perms?.[permission]
  const periodText = `${year}年${period}月`

  const load = async () => {
    const ticket = ++serial.current
    try {
      const r = await api.state(org, year, period)
      if (ticket !== serial.current) return
      if (r.org !== org || r.period !== `${year}-${String(period).padStart(2, '0')}`) throw Error('返回期间不符，请重新读取')
      setData(r)
      if (r.inputs) {
        const { rules, supplement } = r.inputs
        const groups = [...new Set((r.result?.products || []).map(p => p.group))].sort()
        const next = {
          ...rules,
          sharedPercent: supplement.shared_tea_ratio == null ? '' : pct(supplement.shared_tea_ratio),
          rentPercent: pct(rules.rent_plant_ratio),
          shared_group_weights: { ...Object.fromEntries(groups.map(g => [g, { tea: null, total: null }])), ...rules.shared_group_weights },
          trial_shared_decisions: supplement.trial_shared_decisions || {},
          reference_pools: supplement.reference_pools, note: supplement.note || '', confirmed: false,
        }
        setForm(next); savedForm.current = JSON.stringify(next)
      } else setForm(null)
    } catch (e) { if (ticket === serial.current) setError(e.message) }
  }
  useEffect(() => { setTarget(null); setData(null); setForm(null); setError(''); load(); return () => { serial.current++ } }, [org, year, period])

  const act = async (name, fn, reloadOnError) => {
    setBusy(name); setError('')
    try { await fn(); await load() } catch (e) { setError(e.message); if (reloadOnError) await load() } finally { setBusy('') }
  }
  const run = () => act('run', () => api.generate(org, year, period))
  const initialize = () => act('init', () => api.initialize(org, year, period, sourcePeriod))
  const revokeClose = () => act('revoke', () => api.revokeClose(org, year, period))
  const confirmClose = () => { confirmDialog.current?.close(); return act('confirm', () => api.confirmClose(org, year, period, data.inputs.rules.version), true) }
  const save = e => {
    e?.preventDefault()
    return act('save', async () => {
      if (form.shared_basis === 'supplement' && (!form.sharedPercent.trim() || !Number.isFinite(Number(form.sharedPercent)))) throw Error('请填写有效的小料共享比例')
      if (!form.rentPercent.trim() || !Number.isFinite(Number(form.rentPercent))) throw Error('请填写有效的厂房租金比例')
      await api.saveInputs(org, year, period, {
        wip_policy: form.wip_policy, unallocated_wip: form.unallocated_wip, shared_basis: form.shared_basis,
        ...(form.shared_basis === 'supplement' ? { shared_tea_ratio: Number(form.sharedPercent) / 100 } : {}),
        rent_plant_ratio: Number(form.rentPercent) / 100,
        ...(form.shared_basis === 'completed_quantity' ? { shared_group_weights: form.shared_group_weights, ...(form.shared_centre_weights ? { shared_centre_weights: form.shared_centre_weights } : {}) } : {}),
        ...(form.basis === 'reference' ? { reference_pools: form.reference_pools } : {}),
        trial_shared_decisions: form.trial_shared_decisions, note: form.note, confirmed: form.confirmed,
      })
    })
  }
  const download = async () => {
    setBusy('export'); setError('')
    try {
      const formal = data.latest.status === 'ready'
      const response = await fetch(api.exportUrl({ org, year, period, run_id: data.latest.run_id, mode: formal ? 'formal' : 'trial' }), { cache: 'no-store' })
      if (!response.ok) { const e = await response.json().catch(() => ({})); throw Error(e.detail || '导出失败') }
      const url = URL.createObjectURL(await response.blob()), link = document.createElement('a')
      link.href = url; link.download = `全成本表_${org}_${year}-${String(period).padStart(2, '0')}_${formal ? '正式' : '试算-待确认'}.xlsx`
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  // ── 派生状态 ─────────────────────────────────────────────────────────
  const latest = data?.latest, result = data?.result, rules = data?.inputs?.rules
  const products = result?.products || [], counts = result?.controls, trials = result?.trial_orders || []
  const status = latest?.status, [statusText, statusTone] = STATUS[status] || ['状态待核实', 'gray']
  const dirty = !!form && JSON.stringify(form) !== savedForm.current
  const byQuantity = rules?.shared_basis === 'completed_quantity'
  // 试产工单：出表卡口按「上次试算时的结论」（与后端一致）；流程条按「已保存的选择」，这样保存后会提示去重新试算而不是还停在第一步
  const pendingTrials = byQuantity ? trials.filter(o => o.qty > 0 && o.decision === 'pending').length : 0
  const pendingSaved = byQuantity ? trials.filter(o => o.qty > 0 && (data.inputs.supplement.trial_shared_decisions?.[o.key] || 'pending') === 'pending').length : 0
  const closed = !!data?.close_confirmation?.id
  const trialDone = !!latest?.run_id && ['needs_confirmation', 'ready', 'unverified'].includes(status)
  const done = [!!rules?.confirmed && !dirty && !pendingSaved, trialDone, closed && status === 'ready', status === 'ready' && !!latest?.export_ready]
  const current = !data?.inputs ? 0 : done.findIndex(x => !x)
  const stepDesc = [
    !data?.inputs ? '尚未建立本期配置' : dirty ? '有未保存的修改' : pendingSaved ? `${pendingSaved} 张试产工单待确认` : !rules.confirmed ? '待核对并保存' : `已核对${data.inputs.updated_by ? ' · ' + data.inputs.updated_by : ''}`,
    status === 'needs_inputs' ? '依据已改，需重新试算' : status === 'failed' ? '上次生成失败' : data?.source_time ? `金蝶数据时点 ${fmtTime(data.source_time)}` : '尚未取数',
    closed ? `${data.close_confirmation.confirmed_by} · ${fmtTime(data.close_confirmation.confirmed_at)}` : '由你确认本期已结账',
    done[3] ? '可导出带公式的正式表' : '确认结账后自动生成',
  ]
  // 「确认已结账」为什么点不了——逐条说清楚
  const blockers = !data?.inputs ? [] : [
    dirty && '本期依据有未保存的修改',
    !rules.confirmed && '本期依据还没有勾选「已核对」并保存',
    pendingTrials > 0 && `${pendingTrials} 张有产量的试产工单还没确认是否计入共享分摊（保存后需重新试算）`,
    !can('cost_ledger_close') && '当前账号没有「确认结账」权限',
    !can('cost_ledger_fetch') && '当前账号没有取数权限',
  ].filter(Boolean)
  const canConfirm = !!data?.inputs && !blockers.length && !busy
  const exportable = ['ready', 'needs_confirmation'].includes(status)
  const issues = latest?.issues || []
  const issueTarget = text => (/试产|分摊|依据|规则|比例|配置|口径/.test(text) && data?.inputs ? 'inputs' : null)

  const centres = useMemo(() => { const m = new Map(); products.forEach(p => m.set(p.cc, (m.get(p.cc) || 0) + 1)); return [...m] }, [products])
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = products.filter(p => (!cc || p.cc === cc) && `${p.code} ${p.name}`.toLowerCase().includes(q) && (output === 'all' || (output === 'positive' ? p.qty > 0 : !p.qty)))
    if (!sort) return list
    const value = p => (mode === 'unit' && sort.key !== 'qty' && sort.key !== 'unit' ? (p.qty ? p[sort.key] / p.qty : null) : p[sort.key])
    return [...list].sort((a, b) => { const x = value(a), y = value(b); return (x == null) - (y == null) || (sort.dir * ((x || 0) - (y || 0))) })
  }, [products, cc, query, output, sort, mode])
  const totals = useMemo(() => Object.fromEntries(['qty', 'total', ...COMPONENTS.map(c => c.key)].map(k => [k, rows.reduce((s, p) => s + (isNum(p[k]) ? p[k] : 0), 0)])), [rows])
  const zeroOutput = products.filter(p => !p.qty).length
  const unallocated = result?.unallocated_wip || [], unallocatedSum = unallocated.reduce((s, r) => s + r.amount, 0)
  const cell = (p, k) => (mode === 'unit' ? (p.qty ? p[k] / p.qty : null) : p[k])
  const digits = mode === 'unit' ? 4 : 2
  const toggleSort = key => setSort(s => (s?.key !== key ? { key, dir: -1 } : s.dir === -1 ? { key, dir: 1 } : null))
  const arrow = key => (sort?.key === key ? (sort.dir === -1 ? ' ▾' : ' ▴') : '')
  const targetProduct = target && products.find(p => keyOf(p) === target)

  if (!data && !error) return <div className="ac" role="status"><div className="ac-loading">正在读取 {periodText} 的全成本结果…</div></div>

  return (
    <section className="ac" aria-label="全成本溯源">
      {/* 产品明细整页打开；主表只是藏起来，返回时筛选、排序、滚动位置都还在 */}
      {targetProduct && latest?.run_id && <ActualCostTrace org={org} year={year} period={period} runId={latest.run_id} target={targetProduct}
        siblings={rows.some(p => keyOf(p) === target) ? rows : products} onNavigate={p => setTarget(keyOf(p))} onClose={() => setTarget(null)}
        canFetch={can('cost_ledger_fetch')} trial={status !== 'ready'} sourceTime={data.source_time}
        choice={standardChoice[target]} onChoice={id => setStandardChoice(c => ({ ...c, [target]: id }))} />}

      <div className="ac-main" hidden={!!targetProduct}>
        {/* ── 流程条：状态 + 四步 + 下一步动作 ── */}
        <div className="ac-run">
          <div className="ac-run-top">
            <div className="ac-run-state">
              <span className={'ac-status ' + statusTone}>{statusText}</span>
              <span className="ac-run-meta">{periodText} · 账簿 {org} · 金额元 · 数量千克{data?.historical && <b className="ac-amber">　下方为上一版历史快照，本次尚未通过校验</b>}</span>
            </div>
            <div className="ac-run-acts">
              {closed && <button className="btn-sec" disabled={!!busy || !can('cost_ledger_close')} onClick={revokeClose} title="保留历史快照，停止正式导出；之后可重新确认">撤销结账确认</button>}
              <button className={done[3] ? 'btn-pri' : 'btn-sec'} disabled={!!busy || !exportable} onClick={download}>{busy === 'export' ? '正在导出…' : status === 'ready' ? '导出正式表（带公式）' : '导出试算表（带公式）'}</button>
              <button className={current === 1 ? 'btn-pri' : 'btn-sec'} disabled={!!busy || !data?.inputs || !can('cost_ledger_fetch')} onClick={run}
                title="重新读取金蝶并试算；试算后须再次确认结账才能出正式表">{busy === 'run' ? '正在取数试算…' : trialDone ? '重新取数并试算' : '取数并试算'}</button>
              {current === 0 && data?.inputs && <button className="btn-pri" onClick={() => setTab('inputs')}>核对本期依据</button>}
              {/* 试算是可选的预览：依据核对完就可以直接确认结账，后端会自己重新取数 */}
              {data?.inputs && current !== 0 && !done[3] &&
                <button className={current >= 2 ? 'btn-pri' : 'btn-sec'} disabled={!canConfirm} onClick={() => confirmDialog.current.showModal()} title={blockers.join('；')}>{busy === 'confirm' ? '正在生成…' : '确认已结账并出表'}</button>}
            </div>
          </div>
          <div className="steps ac-steps">
            {['本期依据', '取数试算', '确认已结账', '正式全成本表'].map((name, i) => (
              <div key={name} className={'step' + (done[i] ? ' done' : '') + (i === current ? ' cur' : '') + (i === 0 && data?.inputs ? ' ac-click' : '')}
                onClick={i === 0 && data?.inputs ? () => setTab('inputs') : undefined}>
                <span className="num">{done[i] ? '✓' : i + 1}</span>
                <div><div className="sn">{name}</div><div className="sd">{stepDesc[i]}</div></div>
              </div>
            ))}
          </div>
          {(issues.length > 0 || dirty) && <ul className="ac-todo" aria-label="出表前待办">
            {dirty && <li><span className="ac-dot" />本期依据有未保存的修改<button className="lk" onClick={() => setTab('inputs')}>去保存</button></li>}
            {issues.map((text, i) => <li key={i}><span className="ac-dot" />{text}{issueTarget(text) && <button className="lk" onClick={() => setTab(issueTarget(text))}>去处理</button>}</li>)}
          </ul>}
        </div>

        {error && <Note tone="bad">{error}</Note>}

        {/* ── 本期还没有配置：沿用历史月份的规则结构 ── */}
        {data && !data.inputs && <div className="ac-card ac-init">
          <div><b>建立 {periodText} 的配置</b><p>选一个同账簿的历史月份沿用分摊规则。本期的金额、共享比例和确认状态不会沿用，建立后需重新填写并核对。</p></div>
          <label className="selctl"><span className="k">规则来源月份</span>
            <input type="month" value={sourcePeriod} onChange={e => setSourcePeriod(e.target.value)} /></label>
          <button className="btn-pri" disabled={!!busy || !sourcePeriod || !can('cost_ledger_wh')} onClick={initialize}>{busy === 'init' ? '正在建立…' : '沿用规则建立本期配置'}</button>
        </div>}

        {/* ── 合计衔接：金蝶完工成本 ＋ 在产调整 ＝ 全成本合计（后端已校验此等式）── */}
        {counts && <div className="ac-bridge">
          <div className="ac-fig"><span>金蝶完工成本</span><b title={String(counts.gold_total)}>{fmt(counts.gold_total)}</b></div>
          <i>＋</i>
          <div className="ac-fig"><span>在产调整（已分到产品）</span><b>{fmt(counts.wip_allocated)}</b></div>
          <i>＝</i>
          <div className="ac-fig strong"><span>全成本合计{status !== 'ready' && <em>试算</em>}</span><b>{fmt(counts.total)}</b></div>
          <div className="ac-fig-side">
            <div className={'ac-fig sm ' + (Math.abs(counts.source_difference) > 0.01 ? 'bad' : 'ok')}><span>来源差异</span><b>{fmt(counts.source_difference)}{Math.abs(counts.source_difference) <= 0.01 && ' ✓'}</b></div>
            <div className="ac-fig sm"><span>无产出在产调整（单列）</span><b>{fmt(unallocatedSum)}</b></div>
            {counts.shared_quantity
              ? <div className="ac-fig sm" title={`小料产量 ${fmt(counts.shared_quantity.tea)} ÷ 纳入分摊产量 ${fmt(counts.shared_quantity.total)} kg，按本期金蝶完工量自动计算`}><span>共享领用小料比例</span><b>{fmtPct(counts.shared_ratio, 4)}</b></div>
              : isNum(counts.shared_ratio) && <div className="ac-fig sm"><span>共享领用小料比例（填写）</span><b>{fmtPct(counts.shared_ratio, 4)}</b></div>}
            <div className="ac-fig sm"><span>产品对象</span><b>{products.length}<small>　其中零产量 {zeroOutput}</small></b></div>
          </div>
        </div>}

        {(data?.inputs || result) && <div className="ac-tabbar">
          <Tabs label="全成本视图" value={tab} onChange={setTab} tabs={[
            ['table', '全成本表', products.length || null],
            ...(data?.inputs ? [['inputs', '本期依据', dirty ? '未保存' : pendingSaved || (!rules.confirmed ? '待核对' : null), 'warn']] : []),
            ...(result ? [['wip', '在产调整', unallocated.length || null]] : []),
            ...(latest?.run_id ? [['sources', '金蝶原始数据']] : []),
          ]} />
          {tab === 'table' && result && <div className="ac-toolbar-r">
            <input className="ac-input" type="search" placeholder="搜物料编码或名称" aria-label="搜索产品" value={query} onChange={e => setQuery(e.target.value)} />
            <select aria-label="产量筛选" value={output} onChange={e => setOutput(e.target.value)}><option value="all">全部产量</option><option value="positive">有完工产量</option><option value="zero">零完工产量</option></select>
            <Seg label="显示口径" value={mode} onChange={setMode} options={[['amount', '金额'], ['unit', '每公斤', '各成本项目 ÷ 完工数量（元/kg）']]} />
          </div>}
        </div>}

        {/* ── 全成本表 ── */}
        {tab === 'table' && (result ? <>
          <div className="ac-toolbar">
            <div className="ac-chips">
              <button className={'chip' + (!cc ? ' active' : '')} onClick={() => setCc('')}>全部车间 <span className="c-n">{products.length}</span></button>
              {centres.map(([name, n]) => <button key={name} className={'chip' + (cc === name ? ' active' : '')} onClick={() => setCc(cc === name ? '' : name)}>{name} <span className="c-n">{n}</span></button>)}
            </div>
          </div>
          <div className="ac-grid-wrap" tabIndex={0} role="region" aria-label="全成本明细，可横向滚动">
            <table className="ac-grid">
              <thead>
                <tr className="g">
                  <th className="pin-l" rowSpan={2}>产品 <span className="ac-hint">点一行看成本构成与底稿对比</span></th>
                  <th rowSpan={2} className="r sortable" onClick={() => toggleSort('qty')}>完工数量 kg{arrow('qty')}</th>
                  <th rowSpan={2}>构成</th>
                  {GROUPS.map(g => <th key={g.key} colSpan={COMPONENTS.filter(c => c.group === g.key).length} className="ac-grp" style={{ '--g': g.color }}>{g.label}</th>)}
                  <th rowSpan={2} className="r pin-r2 sortable" onClick={() => toggleSort('total')}>全成本合计{arrow('total')}</th>
                  <th rowSpan={2} className="r pin-r1 sortable" onClick={() => toggleSort('unit')}>单位成本 元/kg{arrow('unit')}</th>
                </tr>
                <tr className="c">
                  {COMPONENTS.map(c => <th key={c.key} className="r sortable" style={{ '--g': groupOf(c.group).color }} onClick={() => toggleSort(c.key)}>{c.short || c.label}{arrow(c.key)}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map(p => (
                  <tr key={keyOf(p)} className="ac-row" onClick={() => setTarget(keyOf(p))}>
                    <td className="pin-l">
                      <button className="ac-prod" aria-label={`查看 ${p.code} ${p.name} 的成本构成`}>
                        <span className="mono">{p.code}</span><b>{p.name}</b>
                      </button>
                      <div className="sub">{p.cc}{p.spec ? ' · ' + p.spec : ''}{!p.qty && <span className="ac-tag amber">零产量</span>}</div>
                    </td>
                    <td className="num"><Num v={p.qty} /></td>
                    <td><MixBar p={p} /></td>
                    {COMPONENTS.map(c => <td key={c.key} className="num"><Num v={cell(p, c.key)} d={digits} /></td>)}
                    <td className="num pin-r2 strong"><Num v={p.total} /></td>
                    <td className="num pin-r1"><Num v={p.unit} d={4} /></td>
                  </tr>
                ))}
                {!rows.length && <tr><td className="pin-l ac-empty" colSpan={3}>没有符合筛选条件的产品。</td><td colSpan={COMPONENTS.length + 2} /></tr>}
              </tbody>
              {!!rows.length && <tfoot>
                <tr>
                  <td className="pin-l">{rows.length === products.length ? `全部 ${products.length} 个产品合计` : `当前筛选 ${rows.length} / ${products.length} 个产品合计`}</td>
                  <td className="num"><Num v={totals.qty} /></td>
                  <td><MixBar p={totals} /></td>
                  {COMPONENTS.map(c => <td key={c.key} className="num">{mode === 'unit' ? <span className="ac-nil">—</span> : <Num v={totals[c.key]} />}</td>)}
                  <td className="num pin-r2 strong"><Num v={totals.total} /></td>
                  <td className="num pin-r1"><span className="ac-nil">—</span></td>
                </tr>
              </tfoot>}
            </table>
          </div>
          <div className="ac-legend">
            {GROUPS.map(g => <span key={g.key}><i style={{ background: g.color }} />{g.label}</span>)}
            <span className="muted">「—」表示没有值，浅色 0.00 表示金额为零；数字悬停可看完整精度。{mode === 'unit' && '每公斤＝该项金额 ÷ 完工数量，零产量不计算。'}</span>
          </div>
        </> : data?.inputs && <div className="ac-card ac-empty">本期还没有试算结果。核对「本期依据」后点右上角「取数并试算」。</div>)}

        {tab === 'inputs' && form && <ActualCostInputs data={data} form={form} setForm={setForm} dirty={dirty} busy={busy} can={can}
          trials={trials} counts={counts} onSave={save} onReset={() => setForm(JSON.parse(savedForm.current))} />}

        {/* ── 在产调整 ── */}
        {tab === 'wip' && result && <div className="ac-two">
          <div>
            <div className="ac-h3">无产出在产调整 <span className="muted">这些产品本期没有完工产量，调整额单独列示、不进任何产品</span></div>
            <div className="tbl-wrap"><table className="ac-plain">
              <thead><tr><th>车间</th><th>产品</th><th className="r">金额</th></tr></thead>
              <tbody>
                {unallocated.map(r => { const [c, code] = r.key.split('|'); const p = products.find(x => x.cc === c && x.code === code)
                  return <tr key={r.key}><td>{c}</td><td><span className="mono">{code}</span> {p?.name}</td><td className="num"><Num v={r.amount} d={4} /></td></tr> })}
                {!unallocated.length && <tr><td colSpan={3} className="ac-empty">无未分配调整。</td></tr>}
              </tbody>
              {!!unallocated.length && <tfoot><tr><td colSpan={2}>合计</td><td className="num"><Num v={unallocatedSum} d={4} /></td></tr></tfoot>}
            </table></div>
          </div>
          <div>
            <div className="ac-h3">在产调整来源凭证 <span className="muted">{(result.wip_detail || []).length} 行 · 金蝶费用凭证分录</span></div>
            <div className="tbl-wrap ac-scroll-y"><table className="ac-plain">
              <thead><tr><th>车间</th><th>产品</th><th>凭证号</th><th>分录ID</th><th className="r">入账金额</th></tr></thead>
              <tbody>
                {(result.wip_detail || []).map((r, i) => { const p = products.find(x => x.cc === r.cc && x.code === r.code)
                  return <tr key={i} className={p ? 'ac-row' : ''} onClick={p ? () => setTarget(keyOf(p)) : undefined}><td>{r.cc}</td><td><span className="mono">{r.code}</span> {p?.name}</td><td className="mono">{r.voucher}</td><td className="mono">{r.entry_id}</td><td className="num"><Num v={r.ledger} d={4} /></td></tr> })}
                {!(result.wip_detail || []).length && <tr><td colSpan={5} className="ac-empty">本期没有在产调整凭证。</td></tr>}
              </tbody>
            </table></div>
          </div>
        </div>}

        {tab === 'sources' && latest?.run_id && <ActualCostSources org={org} year={year} period={period} runId={latest.run_id} sourceTime={data.source_time} />}
      </div>

      {/* ── 出表口令：由用户明确确认本期已结账 ── */}
      <dialog ref={confirmDialog} className="ac-dialog" aria-labelledby="ac-confirm-title">
        <h3 id="ac-confirm-title">确认 {periodText}（账簿 {org}）已结账？</h3>
        <p>确认后系统会重新读取金蝶本期数据，按已保存的本期依据分摊、校验；校验通过即生成正式全成本表和带公式的 Excel。</p>
        <p className="muted">费用总额以金蝶结账入账金额为准。金蝶端只读，不会回写。之后如发现反结账，可点「撤销结账确认」。</p>
        <div className="ac-dialog-acts">
          <button className="btn-sec" onClick={() => confirmDialog.current.close()}>再看看</button>
          <button className="btn-pri" onClick={confirmClose}>本期已结账，生成全成本表</button>
        </div>
      </dialog>
    </section>
  )
}
