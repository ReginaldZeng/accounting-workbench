// [Change Log] Date: 2026-10-04 | Author: Claude / c | Version: V2.787（全成本溯源）
// 本期依据：口径、共享分摊依据、租金比例、试产工单逐单确认、依据说明。
// 字段和保存规则沿用 Codex V-draft（保存后须重新取数试算；确认勾选需「确认结账」权限），这里只改排布：
// 分节平铺不再折叠，试产工单提到显眼位置并支持批量确认，底部固定一条「有未保存修改 / 保存」。
import React, { useState } from 'react'
import { Note, Num, Seg, fmt, fmtPct, fmtTime } from './actualCostShared.jsx'

const POLICY = {
  wip_policy: ['在产调整口径', [['reverse_addback', '按原表反向加回'], ['exclude', '不加回在产调整']]],
  unallocated_wip: ['无产出调整处理', [['separate', '单独列示'], ['block', '有未分配调整时不发布']]],
}
const POOLS = { water: '水费', power: '电费', depreciation: '折旧摊销', rent: '租金' }
const DECISION = [['pending', '待确认'], ['include', '计入'], ['exclude', '不计入']]
// 费用项目归到哪：前四个可以在页面上选；后三个要和凭证口径逐项对上，是固定的，页面只读
const TARGET = {
  other: '其他制造费用', indirect: '间接人工', gas: '燃气', gold_depreciation: '折旧摊销',
  gold_utilities: '水电费（按凭证重分）', gold_rent: '租金（按凭证重分）', nitrogen: '氮气（按凭证分到植物肉车间）',
}

export default function ActualCostInputs({ data, form, setForm, dirty, busy, can, trials, counts, missing, onSave, onReset }) {
  const [show, setShow] = useState('all'), [newName, setNewName] = useState('')
  const { rules, supplement } = data.inputs
  const editable = can('cost_ledger_wh')
  const set = patch => setForm({ ...form, ...patch })
  const byQuantity = form.shared_basis === 'completed_quantity'
  const decisionOf = o => form.trial_shared_decisions[o.key] || 'pending'
  const decide = (keys, value) => set({ trial_shared_decisions: { ...form.trial_shared_decisions, ...Object.fromEntries(keys.map(k => [k, value])) } })
  const withOutput = trials.filter(o => o.qty > 0)
  const pending = withOutput.filter(o => decisionOf(o) === 'pending')
  const shown = trials.filter(o => show === 'all' || (show === 'output' ? o.qty > 0 : o.qty > 0 && decisionOf(o) === 'pending'))
  // 费用项目归类：上次试算报「没归类」的排最前，选了去向才算补上
  const targets = data.expense_targets || ['other', 'indirect']
  const map = form.expense_map || {}
  const lacking = missing?.kind === 'expense' ? missing.names.filter(n => !map[n]) : []
  const lackingGroups = missing?.kind === 'group' ? missing.names : []
  const setTarget = (name, target) => set({ expense_map: { ...map, [name]: target } })
  const removeItem = name => { const next = { ...map }; delete next[name]; set({ expense_map: next }) }
  const added = Object.keys(map).filter(n => !(n in (rules.expense_map || {})))
  const addItem = () => { const n = newName.trim(); if (n && !map[n]) { setTarget(n, 'other'); setNewName('') } }
  const provenance = Object.entries(supplement.provenance || {}).filter(([key]) => key !== 'reference_pools' || form.basis === 'reference')

  return (
    <form className="ac-inputs" onSubmit={onSave}>
      <fieldset disabled={!!busy || !editable}>
        {!editable && <Note tone="info">当前账号只能查看本期依据，修改需要「成本台账维护」权限。</Note>}

        {/* ① 已定口径 + 可选口径 */}
        <section className="ac-sec">
          <header><b>① 口径</b><span className="muted">规则版本 {rules.version}{data.inputs.updated_by ? ` · ${data.inputs.updated_by} ${fmtTime(data.inputs.updated_at)} 保存` : ''}</span></header>
          <div className="ac-fields">
            <div className="ac-field fixed"><span>费用金额口径</span><b>金蝶结账入账金额</b><small>已定，不可改</small></div>
            <div className="ac-field fixed"><span>出表节点</span><b>由你在工作台确认已结账</b><small>已定，不可改</small></div>
            {Object.entries(POLICY).map(([key, [label, options]]) => (
              <div className="ac-field" key={key}><span>{label}</span><Seg label={label} value={form[key]} onChange={v => set({ [key]: v })} options={options} disabled={!!busy || !editable} /></div>
            ))}
            <label className="ac-field"><span>厂房租金 · 植物肉比例</span>
              <span className="ac-unit"><input className="ac-input" required type="number" min="0" max="100" step="any" value={form.rentPercent} onChange={e => set({ rentPercent: e.target.value })} />%</span>
            </label>
          </div>
        </section>

        {/* ② 费用项目归类 */}
        <section className="ac-sec">
          <header><b>② 费用项目归类</b>
            <span className="muted">金蝶成本计算单里「制造费用」下的每个费用项目归到哪一列 · 共 {Object.keys(map).length} 项
              {lacking.length ? <b className="ac-amber"> · {lacking.length} 项还没归类</b> : ''}</span></header>
          {!!lacking.length && <div className="ac-lack">
            <div className="ac-label">本期出现了下面这些费用项目，规则里还没有，选好归到哪一列再保存、重新试算</div>
            {lacking.map(n => <div className="ac-lack-row" key={n}><b>{n}</b>
              <Seg label={`${n} 归到`} value="" onChange={v => setTarget(n, v)} options={targets.map(t => [t, TARGET[t] || t])} disabled={!!busy || !editable} /></div>)}
          </div>}
          <details className="explain" open={!!added.length}>
            <summary>查看全部归类{added.length ? `（本次新加 ${added.length} 项，未保存）` : ''}</summary>
            <div className="explain-in">
              <div className="ac-map">
                {Object.entries(map).sort((a, b) => (added.includes(b[0]) - added.includes(a[0])) || a[0].localeCompare(b[0], 'zh')).map(([n, t]) => (
                  <div className={'ac-map-row' + (added.includes(n) ? ' new' : '')} key={n}><span title={n}>{n}</span>
                    {targets.includes(t)
                      ? <select aria-label={`${n} 归到`} value={t} onChange={e => setTarget(n, e.target.value)}>{targets.map(x => <option key={x} value={x}>{TARGET[x] || x}</option>)}</select>
                      : <em title="要和凭证口径逐项对上，页面不开放修改">{TARGET[t] || t}</em>}
                    {added.includes(n) && <button type="button" className="lk q" onClick={() => removeItem(n)}>撤销</button>}
                  </div>))}
              </div>
              <div className="ac-toolbar-l" style={{ marginTop: 10 }}>
                <input className="ac-input" placeholder="手工添加一个费用项目（名称须与金蝶一致）" style={{ width: 300 }} value={newName} onChange={e => setNewName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addItem() } }} />
                <button type="button" className="btn-sec" disabled={!newName.trim() || !!map[newName.trim()]} onClick={addItem}>添加（先归到其他制造费用）</button>
              </div>
              <p className="ac-p muted" style={{ marginTop: 8 }}>水电费、租金、氮气三类是固定归类：它们的金额要按凭证重新分到车间，改了会和凭证对不平，所以页面不开放。</p>
            </div>
          </details>
        </section>

        {/* ③ 共享领用分摊依据 */}
        <section className="ac-sec">
          <header><b>③ 共享领用分摊依据</b><span className="muted">劳动用品等共享领用（金蝶其他出库）里，小料承担的比例</span></header>
          <div className="ac-fields">
            <div className="ac-field"><span>分摊依据</span>
              <Seg label="共享领用分摊依据" value={form.shared_basis} onChange={v => set({ shared_basis: v })} disabled={!!busy || !editable}
                options={[['completed_quantity', '按本月完工产量自动算'], ['supplement', '填写本期比例']]} /></div>
            {!byQuantity && <label className="ac-field"><span>共享领用 · 小料比例</span>
              <span className="ac-unit"><input className="ac-input" required type="number" min="0" max="100" step="any" value={form.sharedPercent} onChange={e => set({ sharedPercent: e.target.value })} />%</span></label>}
            {counts?.shared_quantity && <div className="ac-field fixed wide"><span>上次试算得到的比例</span>
              <b>{fmtPct(counts.shared_ratio, 6)}</b><small>＝ 小料产量 {fmt(counts.shared_quantity.tea)} ÷ 纳入分摊产量 {fmt(counts.shared_quantity.total)} kg</small></div>}
          </div>
          {byQuantity && <div className="ac-weights">
            {form.shared_centre_weights && <div>
              <div className="ac-label">计入共享分摊产量的车间 <span className="muted">按金蝶本期完工量，含试产；请核对范围</span></div>
              <div className="ac-checks">{Object.entries(form.shared_centre_weights).map(([centre, weight]) => (
                <label key={centre} className={'ac-check' + (Number(weight) === 1 ? ' on' : '')}>
                  <input type="checkbox" checked={Number(weight) === 1} onChange={e => set({ shared_centre_weights: { ...form.shared_centre_weights, [centre]: e.target.checked ? 1 : 0 } })} />{centre}</label>
              ))}</div>
            </div>}
            <div>
              <div className="ac-label">产品分组系数 {!!lackingGroups.length && <b className="ac-amber">本期新出现分组「{lackingGroups.join('、')}」，请补上系数</b>}<span className="muted">小料比例 ＝ Σ(完工千克×小料系数) ÷ Σ(完工千克×总量系数)；须满足 0 ≤ 小料 ≤ 总量 ≤ 1</span></div>
              <div className="tbl-wrap ac-fit"><table className="ac-plain">
                <thead><tr><th>产品分组</th><th>计入总产量系数</th><th>计入小料产量系数</th></tr></thead>
                <tbody>
                  {Object.entries(form.shared_group_weights || {}).map(([group, weights]) => <tr key={group} className={weights.total == null || weights.tea == null || weights.total === '' || weights.tea === '' ? 'ac-pending' : ''}><td>{group}</td>
                    {['total', 'tea'].map(k => <td key={k}><input className="ac-input sm" aria-label={`${group}${k === 'total' ? '总量' : '小料'}系数`} type="number" min="0" max="1" step="any" required value={weights[k] ?? ''}
                      onChange={e => set({ shared_group_weights: { ...form.shared_group_weights, [group]: { ...weights, [k]: e.target.value } } })} /></td>)}</tr>)}
                  {!Object.keys(form.shared_group_weights || {}).length && <tr><td colSpan={3} className="ac-empty">还没有产品分组。请先用「填写本期比例」试算一次取得分组，再回来配系数。</td></tr>}
                </tbody>
              </table></div>
            </div>
          </div>}
          {form.basis === 'reference' && <div className="tbl-wrap"><table className="ac-plain">
            <thead><tr><th>本期台账费用池（元）</th>{Object.values(POOLS).map(x => <th key={x}>{x}</th>)}</tr></thead>
            <tbody>{Object.entries(form.reference_pools || {}).map(([centre, pool]) => <tr key={centre}><td>{centre}</td>
              {Object.keys(POOLS).map(k => <td key={k}><input className="ac-input sm" aria-label={`${centre}${POOLS[k]}`} type="number" step="any" required value={pool[k] ?? ''}
                onChange={e => set({ reference_pools: { ...form.reference_pools, [centre]: { ...pool, [k]: e.target.value } } })} /></td>)}</tr>)}</tbody>
          </table></div>}
        </section>

        {/* ④ 试产工单 */}
        {!!trials.length && <section className="ac-sec">
          <header><b>④ 试产工单 · 是否计入共享分摊产量</b>
            <span className="muted">共 {trials.length} 张 · 有产量 {withOutput.length} 张{pending.length ? <b className="ac-amber"> · 待确认 {pending.length} 张</b> : ' · 已全部确认'}</span></header>
          <p className="ac-p">逐单确认后保存，再重新试算。有产量的工单没确认完，「按产量自动算」不能正式出表；零产量工单仍列示。工单成本已在全成本表里，不会重复加总。
            {!byQuantity && <b> 当前是「填写本期比例」，这里的选择暂不参与计算，切到「按产量自动算」后生效。</b>}</p>
          <div className="ac-toolbar">
            <div className="ac-chips">
              {[['all', '全部', trials.length], ['output', '有产量', withOutput.length], ['pending', '待确认', pending.length]].map(([k, t, n]) =>
                <button type="button" key={k} className={'chip' + (show === k ? ' active' : '')} onClick={() => setShow(k)}>{t} <span className="c-n">{n}</span></button>)}
            </div>
            <div className="ac-toolbar-r">
              <span className="muted">有产量的 {withOutput.length} 张：</span>
              <button type="button" className="btn-sec" onClick={() => decide(withOutput.map(o => o.key), 'include')}>全部计入</button>
              <button type="button" className="btn-sec" onClick={() => decide(withOutput.map(o => o.key), 'exclude')}>全部不计入</button>
            </div>
          </div>
          <div className="tbl-wrap ac-scroll-y"><table className="ac-plain">
            <thead><tr><th>工单号</th><th>车间</th><th>产品</th><th>单据类型</th><th className="r">完工 kg</th><th className="r">金蝶完工成本</th><th>上次试算</th><th>共享分摊确认</th></tr></thead>
            <tbody>
              {shown.map(o => <tr key={o.key} className={o.qty > 0 && decisionOf(o) === 'pending' ? 'ac-pending' : ''}>
                <td className="mono">{o.wo}</td><td>{o.cc}</td><td><span className="mono">{o.code}</span> {o.name}</td><td>{o.bill_type}</td>
                <td className="num"><Num v={o.qty} /></td><td className="num"><Num v={o.gold_total} /></td>
                <td>{o.effective_weight ? '计入' : '不计入'}</td>
                <td><Seg label={`${o.wo} 共享分摊确认`} value={decisionOf(o)} onChange={v => decide([o.key], v)} options={DECISION} disabled={!!busy || !editable} /></td>
              </tr>)}
              {!shown.length && <tr><td colSpan={8} className="ac-empty">没有符合条件的试产工单。</td></tr>}
            </tbody>
          </table></div>
        </section>}

        {/* ⑤ 说明与确认 */}
        <section className="ac-sec">
          <header><b>{trials.length ? '⑤' : '④'} 依据说明与核对</b></header>
          {!!provenance.length && <ul className="ac-prov">{provenance.map(([key, text]) => <li key={key}>{text}</li>)}</ul>}
          <label className="ac-label" htmlFor="ac-note">本期依据说明 <span className="muted">5 至 1000 字，写清比例和范围的来历</span></label>
          <textarea id="ac-note" className="ac-input" required minLength={5} maxLength={1000} rows={3} value={form.note} onChange={e => set({ note: e.target.value })} />
        </section>

        <div className="ac-savebar">
          <label className="ck"><input type="checkbox" disabled={!can('cost_ledger_close')} checked={form.confirmed} onChange={e => set({ confirmed: e.target.checked })} />
            我已核对上述本期口径和依据{!can('cost_ledger_close') && <span className="muted">（需要「确认结账」权限）</span>}</label>
          <span className="ac-savebar-r">
            <span className={dirty ? 'ac-amber' : 'muted'}>{dirty ? '有未保存的修改 · 保存后需重新取数试算' : rules.confirmed ? '已保存并核对' : '已保存，尚未核对'}</span>
            <button type="button" className="btn-sec" disabled={!dirty} onClick={onReset}>放弃修改</button>
            <button type="submit" className="btn-pri" disabled={!dirty}>{busy === 'save' ? '正在保存…' : '保存本期依据'}</button>
          </span>
        </div>
      </fieldset>
    </form>
  )
}
