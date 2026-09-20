// [Change Log] Date:2026-09-20 Author:Claude/c Version:V2.588
// 科目余额表 · 解析与核对（物流科目段）：财务报表下的独立页——科目余额这笔数据的“生产/落地入口”。
// 系统取数(金蝶报表口径，样例先种子物流分录) + 上传解析(泛化认列，全科目容错) + 质检勾稽(期末=期初+借-贷) + 逐科目核对。
import React, { useEffect, useState, useRef } from 'react'
import { getFiSubjectOrgs, getFiSubjectBalance, syncFiSubjectBalance, getFiSubjectCheck, uploadFiSubjectReport, getFiSubjectDetail, getFiSubjectTrace } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

let _cache = null
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const ITEMS = ['期初', '本期借方', '本期贷方', '期末']

export default function FiSubjectBalance({ cfg, onPeriod }) {
  const [d, setD] = useState(_cache), [busy, setBusy] = useState(false)
  const [chk, setChk] = useState(null), [upBusy, setUpBusy] = useState(false), [upMsg, setUpMsg] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [openKey, setOpenKey] = useState(null), [detail, setDetail] = useState(null), [detailBusy, setDetailBusy] = useState(false)
  const [trace, setTrace] = useState(null), [traceBusy, setTraceBusy] = useState(false)
  const [orgs, setOrgs] = useState([]), [org, setOrg] = useState('')
  const fileRef = useRef(null)
  const drill = async (code, dimc) => {
    const key = code + '|' + dimc
    if (openKey === key) { setOpenKey(null); setDetail(null); setTrace(null); return }
    setOpenKey(key); setDetail(null); setTrace(null); setDetailBusy(true)
    try { setDetail(await getFiSubjectDetail(code, dimc, org)) } catch (e) { setDetail({ ok: false, note: '取明细失败：' + e.message }) } finally { setDetailBusy(false) }
  }
  const doTrace = async (code, dimc) => {
    setTraceBusy(true)
    try { setTrace(await getFiSubjectTrace(code, dimc, org)) } catch (e) { setTrace({ ok: false, note: '追溯失败：' + e.message }) } finally { setTraceBusy(false) }
  }
  // 逐笔凭证小表（本期下钻 / 追溯历史期共用）
  const voucherTable = (det) => (det && det.lines && det.lines.length > 0) ? (
    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
      <thead><tr>{['日期', '凭证', '摘要', '借方', '贷方', '制单人'].map((h, hi) =>
        <th key={h} style={{ textAlign: (hi === 3 || hi === 4) ? 'right' : 'left', padding: '5px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
      <tbody>
        {det.lines.map((ln, li) => <tr key={li}>
          <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' }}>{ln['日期']}</td>
          <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' }}>{ln['凭证']}</td>
          <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--line)' }}>{ln['摘要']}</td>
          <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--line)', textAlign: 'right', color: ln['借'] < 0 ? 'var(--red)' : undefined }}>{ln['借'] ? fmt(ln['借']) : ''}</td>
          <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--line)', textAlign: 'right', color: ln['贷'] < 0 ? 'var(--red)' : undefined }}>{ln['贷'] ? fmt(ln['贷']) : ''}</td>
          <td style={{ padding: '5px 8px', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' }}>{ln['制单人']}</td>
        </tr>)}
        <tr><td colSpan={3} style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600 }}>本期发生合计</td>
          <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600 }}>{fmt(det['借合计'])}</td>
          <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600 }}>{fmt(det['贷合计'])}</td><td></td></tr>
      </tbody>
    </table>
  ) : <div className="foot">本期无逐笔凭证。</div>
  const loadFor = async (o) => {
    setOpenKey(null); setDetail(null); setTrace(null)
    try { const x = await getFiSubjectBalance(o); _cache = x; setD(x) } catch (e) {}
    try { setChk(await getFiSubjectCheck(o)) } catch (e) {}
  }
  useEffect(() => {
    let live = true
    getFiSubjectOrgs().then(r => {
      if (!live) return
      const list = (r && r.orgs) || []
      setOrgs(list)
      const o = list.length ? list[0].org : ''
      setOrg(o)
      loadFor(o)
    }).catch(() => { setOrg(''); loadFor('') })
    return () => { live = false }
  }, [cfg.source, cfg.year, cfg.period])
  const onOrg = (o) => { setOrg(o); setD(null); loadFor(o) }
  const sync = async () => {
    setBusy(true)
    try { const x = await syncFiSubjectBalance(org); _cache = x; setD(x); setOpenKey(null); setDetail(null); setTrace(null); setChk(await getFiSubjectCheck(org)) } finally { setBusy(false) }
  }
  const upload = async (f) => {
    if (!f) return
    setUpBusy(true); setUpMsg('')
    try {
      const r = await uploadFiSubjectReport(f, org)
      if (r.ok) { setChk(r) } else { setUpMsg(r.msg || '解析失败') }
    } catch (e) { setUpMsg('上传失败：' + e.message) } finally { setUpBusy(false); if (fileRef.current) fileRef.current.value = '' }
  }
  if (!d) return <div className="loading">加载中…</div>

  const rows = d.rows || []
  const codes = [...new Set(rows.map(r => r['科目编码']))]
  const groups = codes.map(c => {
    const rs = rows.filter(r => r['科目编码'] === c)
    const sum = k => rs.reduce((s, r) => s + (r[k] || 0), 0)
    return { code: c, name: rs[0]['科目名称'], cat: rs[0]['科目大类'], rows: rs, 期初: sum('期初'), 借: sum('本期借方'), 贷: sum('本期贷方'), 期末: sum('期末') }
  })
  const total = k => groups.reduce((s, g) => s + g[k], 0)
  const qc = d.qc
  const cmp = chk && chk.compare
  const parsed = chk && chk.parsed
  const cmpRows = cmp ? (showAll ? cmp.rows : cmp.rows.filter(r => r['结果'] !== '一致')) : []

  return (<div>
    <div className="head">
      <div><div className="h-title">科目余额表 · 解析与核对（物流）</div>
        <div className="h-sub">物流相关科目段：销售费用 6601 / 研发费用 6604 / 主营业务成本 6401 / 制造费用 5101 · 其他应付款 2241 · 应交税费—进项税 2221 —— 系统取数 + 手工上传两条路都可核对</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        {orgs.length > 0 && <select value={org} onChange={e => onOrg(e.target.value)} title="选择主体（账簿）"
          style={{ height: 32, borderRadius: 8, border: '1px solid var(--line)', background: 'var(--bg-sub)', color: 'var(--ink)', padding: '0 8px', maxWidth: 240, fontSize: 13 }}>
          {orgs.map(o => <option key={o.org} value={o.org}>{o.org_name || o.org}</option>)}
        </select>}
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
        <button className="btn primary" onClick={sync} disabled={busy}>{busy ? '取数中…' : '从金蝶刷新'}</button>
      </div>
    </div>
    <div className="body">
      {d.error && <div className="banner err">金蝶取数失败：{d.error}</div>}
      {d.note && d.source === 'kingdee' && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>{d.note}</div>}
      <div className="foot">数据来源：{d.source === 'kingdee' ? '金蝶《科目余额表》报表接口（物流科目段，借−贷有符号口径）' : (d.note || '样例数据')} · 主体 {d.org_name || '—'} · {d.period} · 更新于 {d.updated_at}</div>

      {/* 质检勾稽：逐科目 期末 = 期初 + 本期借方 − 本期贷方 */}
      {qc && qc['行数'] > 0 && <div className={'banner' + (qc['全部通过'] ? '' : ' err')} style={qc['全部通过'] ? { marginTop: 8, background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-line)' } : { marginTop: 8 }}>
        {qc['全部通过']
          ? `质检勾稽：${qc['行数']} 行全部平 ✓ —— 每一行都满足 期末 = 期初 + 本期借方 − 本期贷方`
          : `质检勾稽：${qc['行数']} 行中 ${qc['行数'] - qc['通过数']} 行不平（期末 ≠ 期初 + 借 − 贷）：` + qc['异常'].map(x => `${x['科目编码']} ${x['账户'] || ''} 差 ${fmt(x['差'])}`).join('；')}
      </div>}

      {/* 上传解析 + 逐科目核对（系统数 vs 上传数，人工核对） */}
      <div className="cat" style={{ marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>上传科目余额表 → 解析 + 和系统数对一遍（人工核对）</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {cmp && <label className="ck"><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> 显示全部科目</label>}
            <button className="btn" onClick={() => fileRef.current && fileRef.current.click()} disabled={upBusy}>{upBusy ? '解析中…' : '上传金蝶导出的科目余额表'}</button>
            <input ref={fileRef} type="file" accept=".xlsx" style={{ display: 'none' }} onChange={e => upload(e.target.files && e.target.files[0])} />
          </div>
        </div>
        <div className="foot" style={{ margin: '6px 0 0' }}>从任意系统（金蝶等）导出《科目余额表》Excel 传上来：工具自动认列、只挑出物流相关科目，逐科目对期初 / 本期借方 / 本期贷方 / 期末——是不是同一个数，一眼可见。表头是「期初余额 借/贷」两行、或单列「期初原币」都能认。</div>
        {upMsg && <div className="banner err" style={{ marginTop: 8 }}>{upMsg}</div>}

        {parsed && parsed.qc && parsed.qc['行数'] > 0 && <div className={'banner' + (parsed.qc['全部通过'] ? '' : ' err')} style={parsed.qc['全部通过'] ? { marginTop: 8, background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-line)' } : { marginTop: 8 }}>
          上传件质检：共 {parsed.qc['行数']} 个科目，{parsed.qc['全部通过'] ? '全部勾稽通过 ✓' : `${parsed.qc['行数'] - parsed.qc['通过数']} 个不平`}（上传于 {chk.uploaded_at || ''}）
        </div>}

        {cmp && <>
          <div className={'banner' + (cmp['全部一致'] ? '' : ' err')} style={cmp['全部一致'] ? { marginTop: 8, background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-line)' } : { marginTop: 8 }}>
            {!chk['有系统数']
              ? `已解析上传件 ${cmp['科目数']} 个科目（本期系统侧暂无物流数可对，见上方黄条；先看上传件质检）`
              : (cmp['全部一致']
                ? `共核对 ${cmp['科目数']} 个科目，全部一致 ✓ —— 系统取的数和上传报表是同一个数`
                : `共核对 ${cmp['科目数']} 个科目，一致 ${cmp['一致数']} 个、有出入 ${cmp['科目数'] - cmp['一致数']} 个，见下表`)}
          </div>
          {(cmpRows.length > 0) && <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table style={{ width: '100%', minWidth: 900, fontSize: 12.5, borderCollapse: 'collapse' }}>
              <thead><tr>{['科目', '项目', '系统取的数', '上传报表的数', '差多少', '结果'].map((h, i) =>
                <th key={h} style={{ textAlign: i <= 1 ? 'left' : (i === 5 ? 'center' : 'right'), padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
              <tbody>{cmpRows.map((r, i) => ITEMS.map((k, ki) => (
                <tr key={i + '-' + k}>
                  {ki === 0 && <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' }} rowSpan={4}><b>{r['科目编码']}</b> {r['科目名称']}<div className="sub">{r['科目大类']}</div></td>}
                  <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>{k}</td>
                  <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', textAlign: 'right' }}>{fmt(r[k + '_工具'])}</td>
                  <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', textAlign: 'right' }}>{fmt(r[k + '_报表'])}</td>
                  <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', textAlign: 'right', color: r[k + '_差'] ? 'var(--red)' : 'var(--ink-3)' }}>{r[k + '_差'] ? fmt(r[k + '_差']) : '—'}</td>
                  {ki === 0 && <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', textAlign: 'center', color: r['结果'] === '一致' ? 'var(--green)' : 'var(--red)', whiteSpace: 'nowrap' }} rowSpan={4}>{r['结果'] === '一致' ? '✓ 一致' : r['结果']}</td>}
                </tr>)))}</tbody>
            </table>
          </div>}
          {cmp && cmpRows.length === 0 && !showAll && chk['有系统数'] && <div className="foot" style={{ marginTop: 6 }}>没有出入的科目。勾选「显示全部科目」可查看每一项对照。</div>}
        </>}
      </div>

      {/* 系统数主表：按科目分组，维度明细 + 科目小计 + 总计。维度行可点开下钻。 */}
      <div className="foot" style={{ marginTop: 10 }}>👉 点开任意<b>维度行</b>，看这笔余额是怎么构成的：期末＝期初＋本期借－本期贷 的勾稽拆解 + 本期发生的逐笔凭证（日期/凭证号/摘要/借贷/制单人）。</div>
      <div className="tbl-wrap"><table style={{ minWidth: 920 }}>
        <thead><tr>{['科目 / 维度', '大类', '期初余额', '本期借方', '本期贷方', '期末余额'].map((h, i) =>
          <th className="th" key={h} style={i >= 2 ? { textAlign: 'right' } : null}>{h}</th>)}</tr></thead>
        <tbody>
          {groups.map(g => (<React.Fragment key={g.code}>
            <tr style={{ background: 'var(--bg)' }}>
              <td style={{ fontWeight: 600 }}>{g.code} {g.name}<span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>{g.rows.length} 个维度</span></td>
              <td className="muted" style={{ fontSize: 12 }}>{g.cat}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['期初'])}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['借'])}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['贷'])}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['期末'])}</td>
            </tr>
            {g.rows.map((r, i) => {
              const k = r['科目编码'] + '|' + (r['维度编码'] || '')
              const isOpen = openKey === k
              return (<React.Fragment key={g.code + i}>
                <tr onClick={() => drill(r['科目编码'], r['维度编码'] || '')} style={{ cursor: 'pointer', background: isOpen ? 'var(--accent-soft)' : undefined }} title="点开看这笔余额的构成（勾稽拆解 + 逐笔凭证）">
                  <td style={{ paddingLeft: 26 }} className="acct"><span style={{ display: 'inline-block', width: 12, color: 'var(--ink-3)', transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>▸</span> {r['账户']}</td>
                  <td></td>
                  <td className="num">{fmt(r['期初'])}</td>
                  <td className="num">{fmt(r['本期借方'])}</td>
                  <td className="num">{fmt(r['本期贷方'])}</td>
                  <td className="num" style={{ color: r['期末'] < 0 ? 'var(--red)' : undefined }}>{fmt(r['期末'])}</td>
                </tr>
                {isOpen && <tr><td colSpan={6} style={{ background: 'var(--bg-sub)', padding: '10px 14px 12px 26px' }}>
                  {detailBusy && <div className="muted">加载明细中…</div>}
                  {!detailBusy && detail && <>
                    <div style={{ marginBottom: 8, fontSize: 12.5 }}>
                      本期({d.period}) 期末余额 <b style={{ color: detail['期末'] < 0 ? 'var(--red)' : 'var(--ink)' }}>{fmt(detail['期末'])}</b> 的构成：
                      <span style={{ marginLeft: 6 }}>期初 {fmt(detail['期初'])} ＋ 本期借 {fmt(detail['本期借方'])} － 本期贷 {fmt(detail['本期贷方'])} ＝ <b>{fmt(detail['期末'])}</b></span>
                    </div>
                    {detail.note && <div className="foot" style={{ marginBottom: 6 }}>{detail.note}</div>}
                    {voucherTable(detail.detail)}
                    {Math.abs(detail['期初'] || 0) > 0.005 && <div style={{ marginTop: 10 }}>
                      {!trace && <button className="btn" onClick={() => doTrace(detail.code, detail.dim)} disabled={traceBusy}>{traceBusy ? '追溯中…' : '↑ 追溯期初 ' + fmt(detail['期初']) + ' 的来源'}</button>}
                      {trace && <div style={{ borderLeft: '2px solid var(--accent)', paddingLeft: 12, marginTop: 2 }}>
                        <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>期初 {fmt(detail['期初'])} 的来源 —— 逐期往前翻（期初 ＝ 上期期末）</div>
                        {trace.note && <div className="foot" style={{ marginBottom: 6 }}>{trace.note}</div>}
                        {(trace.chain || []).map((c, ci) => <div key={ci} style={{ marginBottom: 12 }}>
                          <div style={{ fontSize: 12.5, marginBottom: 4 }}>
                            <span style={{ fontWeight: 600 }}>{c.ym}</span> · 期末 {fmt(c['期末'])} ＝ 期初 {fmt(c['期初'])} ＋ 本期借 {fmt(c['本期借方'])} － 本期贷 {fmt(c['本期贷方'])}
                          </div>
                          {voucherTable(c.detail)}
                        </div>)}
                        {trace.reached_zero && <div className="foot" style={{ color: 'var(--green)' }}>✓ 已追溯到期初为 0 —— 该维度此前无结转（建账起点），到此为止。</div>}
                      </div>}
                    </div>}
                  </>}
                </td></tr>}
              </React.Fragment>)
            })}
          </React.Fragment>))}
          {groups.length > 0 && <tr style={{ background: 'var(--bg)' }}>
            <td style={{ fontWeight: 700 }}>合计</td><td></td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('期初'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('借'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('贷'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('期末'))}</td>
          </tr>}
          {groups.length === 0 && <tr><td colSpan="6" className="muted">系统侧暂无物流科目数据。样例模式已内置演示数据；金蝶模式请用上方「上传」解析核对。</td></tr>}
        </tbody>
      </table></div>
      <div className="foot">口径说明：余额取【借 − 贷】有符号口径，费用类为正、其他应付款（负债）为负，勾稽恒等式 期末 = 期初 + 本期借方 − 本期贷方 恒成立。物流科目段 = 物流计提工具入账落到的科目（销售费用出库运费/仓储费、主营业务成本/制造费用入库运费、研发费用搬运费、其他应付款—供应商往来、暂估进项税）。</div>
    </div>
  </div>)
}
