// [Change Log] Date:2026-08-06 Author:Claude/c Version:V2.198
// 「物流基础数据」独立页（业务方定的信息架构：物流对账板块二级=基础数据/物流计提/账单核对/单据运费）。
// 物流计提原步②「映射与税率」四 tab 整体搬到这里重组为四块：
//   ① 供应商列表——名称/代码/简称/渠道 + 各费用类型税率，供应商身份列【合并单元格】(rowSpan)呈现（业务方指定格式）
//   ② 费用归属映射表（13类闭环+例外行，四级取数）
//   ③ 业务线分类表（产品分类/产品项目编码，TO C 挂接）
//   ④ 账单标注翻译表（账单"类型"→费用归属×业务线）
// 权限：查看=登录即可；维护=logistics_upload（与税率维护同权）。
import React, { useEffect, useState } from 'react'
import {
  getLogisticsRates, saveLogisticsRate, deleteLogisticsRate,
  getFeeMap, deleteFeeMap, getBizlines, saveBizline,
  getTypeMap, saveTypeMap, deleteTypeMap,
  getLogiSuppliers, saveLogiSupplier, deleteLogiSupplier,
} from '../api.js'

const pct = r => (r == null ? '' : (Math.round(r * 10000) / 100) + '%')
const FEE13 = ['销售出库费用', '成品入库费用', '原料入库费用', '成品仓储费用', '原料仓储费用',
  '成品调拨费用', '原料调拨费用', '出库装卸费用', '成品入库装卸费用', '原料入库装卸费用',
  '研发设备采购', '设备调拨费用', '其它']
const BIZ10 = ['植物肉', '鲜食', '零售', '小料', '豆蛋制品', '电商', '山姆零售', 'kikiherb', '海外', '—']

const cth = { border: '0.5px solid #b9c2d6', padding: '6px 10px', background: '#e8ecf5', color: '#20304d', fontWeight: 600, whiteSpace: 'nowrap' }
const ctd = { border: '0.5px solid #d8d8d8', padding: '5px 10px', whiteSpace: 'nowrap', verticalAlign: 'middle' }
const btn = { padding: '6px 14px', borderRadius: 7, border: '0.5px solid #cfcdc4', background: '#fff', cursor: 'pointer', fontSize: 12.5 }

export default function LogisticsBasicData({ user }) {
  const can = k => !!(user && (user.role === 'admin' || (user.perms || {})[k]))
  const canUp = can('logistics_upload')

  const [tab, setTab] = useState('sup')     // sup / fee / biz / type
  const [sups, setSups] = useState([])
  const [rates, setRates] = useState([])
  const [feeMap, setFeeMap] = useState([])
  const [bizlines, setBizlines] = useState([])
  const [typeMap, setTypeMap] = useState([])
  const [err, setErr] = useState('')

  const loadAll = () => {
    getLogiSuppliers().then(r => setSups(r.rows || [])).catch(() => {})
    getLogisticsRates().then(r => setRates(r.rates || [])).catch(() => {})
    getFeeMap().then(r => setFeeMap(r.rows || [])).catch(() => {})
    getBizlines().then(r => setBizlines(r.rows || [])).catch(() => {})
    getTypeMap().then(r => setTypeMap(r.rows || [])).catch(() => {})
  }
  useEffect(() => { loadAll() }, [])
  const guard = async (fn) => { try { setErr(''); await fn(); loadAll() } catch (e) { setErr(String(e.message || e)) } }

  // ── ① 供应商列表（合并单元格：身份 4 列 rowSpan = 该商税率行数）──
  const addRate = (sp) => guard(async () => {
    const ft = window.prompt(`给「${sp.short}」加一条税率\n费用类型（留空=该商默认档；13类：${FEE13.join('/')}）`, '')
    if (ft === null) return
    const rt = window.prompt('税率%（如 9 或 6）', '9')
    if (rt === null || rt === '') return
    const r = await saveLogisticsRate({ supplier: sp.full, fee_type: ft.trim(), rate: rt })
    if (!r.ok) throw new Error(r.msg || '保存失败')
  })
  const editSup = (sp) => guard(async () => {
    const full = window.prompt(`「${sp.short}」全名（与金蝶档案一致）`, sp.full); if (full === null) return
    const code = window.prompt('金蝶供应商编码（可空，录入时以档案实查为准）', sp.kd_code || ''); if (code === null) return
    const chan = window.prompt('渠道（线下/线上，进摘要）', sp.channel || '线下'); if (chan === null) return
    const r = await saveLogiSupplier({ short: sp.short, full, kd_code: code, channel: chan, note: sp.note || '' })
    if (!r.ok) throw new Error(r.msg || '保存失败')
  })
  const addSup = () => guard(async () => {
    const short = window.prompt('新物流商·简称（唯一查找键，账单文件名里认得出；同名两法人须拆不同简称）'); if (!short) return
    const full = window.prompt('全名（与金蝶档案一致）', short); if (full === null) return
    const chan = window.prompt('渠道（线下/线上）', '线下'); if (chan === null) return
    const r = await saveLogiSupplier({ short, full, kd_code: '', channel: chan, note: '' })
    if (!r.ok) throw new Error(r.msg || '保存失败')
  })

  const supBlock = () => {
    const byFull = {}
    rates.forEach(r => { (byFull[r.supplier] = byFull[r.supplier] || []).push(r) })
    return <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '14px 16px', background: '#fff' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, color: '#77756e' }}>一行一物流商；右侧=该商各费用类型的<b>不含税</b>税率（默认档兜底，仓储 6% 等特例单列）。税率来源=报价单专票口径；账单解析、模板签发都吃这里。</span>
        {canUp && <button style={{ ...btn, marginLeft: 'auto' }} onClick={addSup}>＋ 新增物流商</button>}
      </div>
      <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, minWidth: 860 }}>
        <thead><tr>
          <th style={cth}>供应商名称（金蝶档案）</th><th style={cth}>金蝶代码</th><th style={cth}>简称</th><th style={cth}>渠道</th>
          <th style={cth}>费用类型</th><th style={cth}>税率</th><th style={cth}>维护</th><th style={cth}></th>
        </tr></thead>
        <tbody>
          {sups.map(sp => {
            const rs = (byFull[sp.full] || []).sort((a, b) => (a.fee_type || '').localeCompare(b.fee_type || ''))
            const rows = rs.length ? rs : [null]
            const n = rows.length
            return rows.map((r, i) => <tr key={sp.id + '-' + i}>
              {i === 0 && <>
                <td rowSpan={n} style={{ ...ctd, fontWeight: 600, maxWidth: 240, whiteSpace: 'normal' }}>{sp.full}
                  {canUp && <span onClick={() => editSup(sp)} style={{ marginLeft: 6, color: '#305496', cursor: 'pointer', fontSize: 11.5 }}>改</span>}</td>
                <td rowSpan={n} style={{ ...ctd, fontFamily: 'Consolas,monospace', color: sp.kd_code ? undefined : '#b9b7ae' }}>{sp.kd_code || '（录入时实查）'}</td>
                <td rowSpan={n} style={{ ...ctd, fontWeight: 600 }}>{sp.short}</td>
                <td rowSpan={n} style={{ ...ctd, color: sp.channel === '线上' ? '#2c6bcf' : undefined }}>{sp.channel}</td>
              </>}
              <td style={ctd}>{r ? (r.fee_type || <span style={{ color: '#8a8880' }}>（默认·全部类型）</span>) : <span style={{ color: 'var(--red)' }}>未维护税率</span>}</td>
              <td style={{ ...ctd, textAlign: 'right', fontWeight: r && r.fee_type ? 600 : 400 }}>{r ? pct(r.rate) : '—'}</td>
              <td style={{ ...ctd, color: '#8a8880', fontSize: 11.5 }}>{r ? `${r.updated_by} ${r.updated_at}` : ''}</td>
              <td style={ctd}>
                {canUp && r && <span onClick={() => guard(async () => { if (window.confirm(`删除税率：${sp.short} × ${r.fee_type || '(默认)'} = ${pct(r.rate)}？`)) { await deleteLogisticsRate({ id: r.id }) } })} style={{ color: 'var(--red)', cursor: 'pointer', fontSize: 12 }}>删</span>}
                {canUp && i === 0 && <span onClick={() => addRate(sp)} style={{ marginLeft: 8, color: '#305496', cursor: 'pointer', fontSize: 12 }}>＋税率</span>}
              </td>
            </tr>)
          })}
        </tbody>
      </table></div>
    </div>
  }

  // ── ② 费用归属映射表 ──
  const feeBlock = () => <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '14px 16px', background: '#fff' }}>
    <div style={{ fontSize: 12.5, color: '#77756e', marginBottom: 10 }}>每个费用归属怎么做账（科目/部门/费用项目/摘要用语）。取数：<b>费用归属×主体×业务线 精确 → 主体例外 → 业务线例外 → 默认</b>。🖐=人工核对类（设备调拨/其它，科目部门在计提活表里逐笔定）。活表「采纳入维表」的例外行会出现在这。</div>
    <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, minWidth: 900 }}>
      <thead><tr>{['费用归属', '主体(空=不限)', '业务线(空=不限)', '借方科目', '部门', '费用项目', '摘要用语', '维护', ''].map(h => <th key={h} style={cth}>{h}</th>)}</tr></thead>
      <tbody>{feeMap.map(r => <tr key={r.id} style={{ background: (r.subject || r.bizline) ? '#f4f5fd' : '#fff' }}>
        <td style={{ ...ctd, fontWeight: 600 }}>{r.fee}{r.manual ? ' 🖐' : ''}</td>
        <td style={{ ...ctd, color: r.subject ? '#305496' : '#b9b7ae' }}>{r.subject || '—'}</td>
        <td style={{ ...ctd, color: r.bizline ? '#305496' : '#b9b7ae' }}>{r.bizline || '—'}</td>
        <td style={ctd}>{r.account || (r.manual ? '🖐逐笔人工' : '')}</td>
        <td style={ctd}>{r.dept || (r.manual ? '🖐逐笔人工' : '')}</td>
        <td style={ctd}>{r.item}</td>
        <td style={ctd}>{r.sword}</td>
        <td style={{ ...ctd, color: '#8a8880', fontSize: 11.5 }}>{r.updated_by} {r.updated_at}</td>
        <td style={ctd}>{canUp && (r.subject || r.bizline) && <span onClick={() => guard(async () => { if (window.confirm(`删除例外行 ${r.fee}×${r.subject || r.bizline}？删后回落默认行`)) { await deleteFeeMap({ id: r.id }) } })} style={{ color: 'var(--red)', cursor: 'pointer', fontSize: 12 }}>删</span>}</td>
      </tr>)}</tbody>
    </table></div>
  </div>

  // ── ③ 业务线分类表 ──
  const bizBlock = () => <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '14px 16px', background: '#fff' }}>
    <div style={{ fontSize: 12.5, color: '#77756e', marginBottom: 10 }}>业务线 → 金蝶产品分类 / 产品项目编码。产品项目非空=该业务线费用行自动挂（山姆 TO C=CPXM017、kikiherb=CPXM022，2026 序时账实证；只有 6601/6401 科目挂产品维度）。</div>
    <table style={{ borderCollapse: 'collapse', fontSize: 12.5, minWidth: 560 }}>
      <thead><tr>{['业务线', '产品分类编码', '产品项目编码', '维护', ''].map(h => <th key={h} style={cth}>{h}</th>)}</tr></thead>
      <tbody>{bizlines.map(r => <tr key={r.id}>
        <td style={{ ...ctd, fontWeight: 600 }}>{r.name}</td>
        <td style={{ ...ctd, fontFamily: 'Consolas,monospace' }}>{r.cpfl || '—'}</td>
        <td style={{ ...ctd, fontFamily: 'Consolas,monospace' }}>{r.cpxm || '—'}</td>
        <td style={{ ...ctd, color: '#8a8880', fontSize: 11.5 }}>{r.updated_by} {r.updated_at}</td>
        <td style={ctd}>{canUp && <span onClick={() => guard(async () => {
          const cpfl = window.prompt(`${r.name} 产品分类编码`, r.cpfl || ''); if (cpfl === null) return
          const cpxm = window.prompt(`${r.name} 产品项目编码(空=不挂)`, r.cpxm || ''); if (cpxm === null) return
          await saveBizline({ name: r.name, cpfl, cpxm })
        })} style={{ color: '#305496', cursor: 'pointer', fontSize: 12 }}>改</span>}</td>
      </tr>)}</tbody>
    </table>
  </div>

  // ── ④ 账单标注翻译表 ──
  const typeBlock = () => <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '14px 16px', background: '#fff' }}>
    <div style={{ fontSize: 12.5, color: '#77756e', marginBottom: 10 }}>物流部在账单上写的「类型」怎么翻译成 费用归属×业务线。精确匹配优先，查不到走规则兜底，再不行进"待人工"。账单出现新写法→在这加一条，下月自动认。</div>
    {canUp && <div style={{ marginBottom: 10 }}><button style={btn} onClick={() => guard(async () => {
      const p = window.prompt('账单标注原文（如 销售单-植物肉）'); if (!p) return
      const f = window.prompt(`翻译成哪个费用归属？\n${FEE13.join(' / ')}`, '销售出库费用'); if (!f) return
      const b = window.prompt(`业务线（${BIZ10.join('/')}，留空=—）`, '') ?? ''
      const d = window.prompt('业务描述（进摘要，可空，如 样品）', '') ?? ''
      await saveTypeMap({ pattern: p, fee: f, bizline: b, descr: d })
    })}>+ 新增翻译</button></div>}
    <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, minWidth: 700 }}>
      <thead><tr>{['账单标注原文', '费用归属', '业务线', '业务描述', '维护', ''].map(h => <th key={h} style={cth}>{h}</th>)}</tr></thead>
      <tbody>{typeMap.map(r => <tr key={r.id}>
        <td style={{ ...ctd, fontWeight: 600 }}>{r.pattern}</td>
        <td style={ctd}>{r.fee}</td>
        <td style={ctd}>{r.bizline || '—'}</td>
        <td style={ctd}>{r.descr}</td>
        <td style={{ ...ctd, color: '#8a8880', fontSize: 11.5 }}>{r.updated_by} {r.updated_at}</td>
        <td style={ctd}>{canUp && <span onClick={() => guard(async () => { if (window.confirm(`删除翻译 ${r.pattern}？`)) { await deleteTypeMap({ id: r.id }) } })} style={{ color: 'var(--red)', cursor: 'pointer', fontSize: 12 }}>删</span>}</td>
      </tr>)}</tbody>
    </table></div>
  </div>

  return <div>
    <div className="head">
      <div><div className="h-title">物流基础数据</div>
        <div className="h-sub">物流线共用维表 · 单一事实源：供应商与税率、费用归属映射、业务线分类、账单标注翻译——计提解析、模板签发、对账都吃这里；改口径当场生效，不用找开发</div></div>
    </div>
    <div className="body">
      <div style={{ display: 'inline-flex', border: '0.5px solid #cfcdc4', borderRadius: 8, overflow: 'hidden', fontSize: 13 }}>
        {[['sup', `供应商列表 ${sups.length}`], ['fee', `费用归属映射表 ${feeMap.length}`], ['biz', `业务线分类表 ${bizlines.length}`], ['type', `标注翻译表 ${typeMap.length}`]].map(([k, lb]) =>
          <span key={k} onClick={() => setTab(k)} style={{ padding: '8px 18px', cursor: 'pointer', background: tab === k ? '#edeefb' : '#fff', color: tab === k ? '#305496' : '#77756e', fontWeight: tab === k ? 600 : 400, borderRight: '0.5px solid #e6e4dc' }}>{lb}</span>)}
      </div>
      {err && <div style={{ background: '#fcebeb', color: '#a32d2d', border: '0.5px solid #f0c4c4', borderRadius: 8, padding: '8px 12px', fontSize: 13 }}>{err}</div>}
      {!canUp && <div style={{ fontSize: 12, color: 'var(--amber)' }}>你的账号只能查看；维护需要「上传物流计提表·维护税率」权限，请联系管理员。</div>}
      {tab === 'sup' && supBlock()}
      {tab === 'fee' && feeBlock()}
      {tab === 'biz' && bizBlock()}
      {tab === 'type' && typeBlock()}
    </div>
  </div>
}
