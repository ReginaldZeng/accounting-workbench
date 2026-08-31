// [Change Log] Date:2026-07-04 Author:Claude/c Version:V2.4
// 科目余额表 · 账面核对：API 取数(期初+本期序时账=实时期末) + 手工上传金蝶导出报表逐项核对（人眼核对双模式）。
import React, { useEffect, useState, useRef } from 'react'
import { getSubjectBalance, syncSubjectBalance, getSubjectCheck, uploadSubjectReport } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

let _cache = null
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const td = { padding: '6px 8px', borderBottom: '1px solid var(--line)' }
const tdr = { ...td, textAlign: 'right' }

export default function SubjectBalance({ cfg, onPeriod }) {
  const [d, setD] = useState(_cache), [busy, setBusy] = useState(false)
  const [chk, setChk] = useState(null), [upBusy, setUpBusy] = useState(false), [upMsg, setUpMsg] = useState('')
  const [showAll, setShowAll] = useState(false)
  const fileRef = useRef(null)
  useEffect(() => {
    getSubjectBalance().then(x => { _cache = x; setD(x) }).catch(() => {})
    getSubjectCheck().then(setChk).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  const sync = async () => {
    setBusy(true)
    try { const x = await syncSubjectBalance(); _cache = x; setD(x); const c = await getSubjectCheck(); setChk(c) } finally { setBusy(false) }
  }
  const upload = async (f) => {
    if (!f) return
    setUpBusy(true); setUpMsg('')
    try {
      const r = await uploadSubjectReport(f)
      if (r.ok) { setChk(r) } else { setUpMsg(r.msg || '解析失败') }
    } catch (e) { setUpMsg('上传失败：' + e.message) } finally { setUpBusy(false); if (fileRef.current) fileRef.current.value = '' }
  }
  if (!d) return <div className="loading">加载中…</div>

  const rows = d.rows || []
  // 按科目分组 + 小计
  const codes = [...new Set(rows.map(r => r['科目编码']))]
  const groups = codes.map(c => {
    const rs = rows.filter(r => r['科目编码'] === c)
    const sum = k => rs.reduce((s, r) => s + (r[k] || 0), 0)
    return { code: c, name: rs[0]['科目名称'], cat: rs[0]['科目大类'], rows: rs, 期初: sum('期初'), 借: sum('本期借方'), 贷: sum('本期贷方'), 期末: sum('期末') }
  })
  const total = k => groups.reduce((s, g) => s + g[k], 0)
  const cmp = chk && chk.compare
  const cmpRows = cmp ? (showAll ? cmp.rows : cmp.rows.filter(r => r['结果'] !== '一致')) : []

  return (<div>
    <div className="head">
      <div><div className="h-title">科目余额表 · 账面核对</div>
        <div className="h-sub">四类资金科目（库存现金 1001 / 银行存款 1002 / 其它货币资金 1012 / 交易性金融资产 1101）· 期初＋本期发生＝期末，与金蝶报表同一个算法</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
        <button className="btn primary" onClick={sync} disabled={busy}>{busy ? '取数中…' : '从金蝶刷新'}</button>
      </div>
    </div>
    <div className="body">
      {d.error && <div className="banner err">金蝶取数失败：{d.error}</div>}
      {d['未取数'] && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>
        本期未取数：请先到<b>「数据接入」</b>点<b>「从金蝶更新」</b>取回本月金蝶数据。
      </div>}
      <div className="foot">数据来源：{d.source === 'kingdee' ? '金蝶（期初取自科目余额表接口，本期发生取自序时账逐笔加总）' : '样例数据'} · {d.period} · 更新于 {d.updated_at}</div>

      {/* 人眼核对：上传金蝶导出的科目余额表，与工具数逐科目对照 */}
      <div className="cat" style={{ marginTop: 4 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>和金蝶报表对一遍（人工核对）</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {cmp && <label className="ck"><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> 显示全部科目</label>}
            <button className="btn" onClick={() => fileRef.current && fileRef.current.click()} disabled={upBusy}>{upBusy ? '核对中…' : '上传金蝶导出的科目余额表'}</button>
            <input ref={fileRef} type="file" accept=".xlsx" style={{ display: 'none' }} onChange={e => upload(e.target.files && e.target.files[0])} />
          </div>
        </div>
        <div className="foot" style={{ margin: '6px 0 0' }}>不放心工具取的数？在金蝶里把《科目余额表》导出成 Excel 传上来，工具逐科目对期初、本期借方、本期贷方、期末——是不是同一个数，一眼可见。</div>
        {upMsg && <div className="banner err" style={{ marginTop: 8 }}>{upMsg}</div>}
        {cmp && <>
          <div className={'banner' + (cmp['全部一致'] ? '' : ' err')} style={cmp['全部一致'] ? { marginTop: 8, background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-line)' } : { marginTop: 8 }}>
            {cmp['全部一致']
              ? `共核对 ${cmp['科目数']} 个科目，全部一致 ✓ —— 工具取的数和金蝶报表是同一个数（上传于 ${chk.uploaded_at || ''}）`
              : `共核对 ${cmp['科目数']} 个科目，一致 ${cmp['一致数']} 个、有出入 ${cmp['科目数'] - cmp['一致数']} 个，见下表（上传于 ${chk.uploaded_at || ''}）`}
          </div>
          {(cmpRows.length > 0) && <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table style={{ width: '100%', minWidth: 900, fontSize: 12.5, borderCollapse: 'collapse' }}>
              <thead><tr>{['科目', '项目', '工具取的数', '金蝶报表的数', '差多少', '结果'].map((h, i) =>
                <th key={h} style={{ textAlign: i <= 1 ? 'left' : (i === 5 ? 'center' : 'right'), padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
              <tbody>{cmpRows.map((r, i) => ['期初', '本期借方', '本期贷方', '期末'].map((k, ki) => (
                <tr key={i + '-' + k}>
                  {ki === 0 && <td style={{ ...td, whiteSpace: 'nowrap' }} rowSpan={4}><b>{r['科目编码']}</b> {r['科目名称']}<div className="sub">{r['科目大类']}</div></td>}
                  <td style={td}>{k}</td>
                  <td style={tdr}>{fmt(r[k + '_工具'])}</td>
                  <td style={tdr}>{fmt(r[k + '_报表'])}</td>
                  <td style={{ ...tdr, color: r[k + '_差'] ? 'var(--red)' : 'var(--ink-3)' }}>{r[k + '_差'] ? fmt(r[k + '_差']) : '—'}</td>
                  {ki === 0 && <td style={{ ...td, textAlign: 'center', color: r['结果'] === '一致' ? 'var(--green)' : 'var(--red)', whiteSpace: 'nowrap' }} rowSpan={4}>{r['结果'] === '一致' ? '✓ 一致' : r['结果']}</td>}
                </tr>)))}</tbody>
            </table>
          </div>}
          {cmp && cmpRows.length === 0 && !showAll && <div className="foot" style={{ marginTop: 6 }}>没有出入的科目。勾选「显示全部科目」可查看每一项对照。</div>}
        </>}
      </div>

      {/* 科目余额表主表：按科目分组，账户级明细 + 科目小计 + 总计 */}
      <div className="tbl-wrap"><table style={{ minWidth: 920 }}>
        <thead><tr>{['科目 / 账户', '币别', '期初余额', '本期借方', '本期贷方', '期末余额'].map((h, i) =>
          <th className="th" key={h} style={i >= 2 ? { textAlign: 'right' } : null}>{h}</th>)}</tr></thead>
        <tbody>
          {groups.map(g => (<React.Fragment key={g.code}>
            <tr style={{ background: 'var(--bg)' }}>
              <td style={{ fontWeight: 600 }}>{g.code} {g.name}<span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>{g.cat} · {g.rows.length} 个账户</span></td>
              <td></td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['期初'])}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['借'])}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['贷'])}</td>
              <td className="num" style={{ fontWeight: 600 }}>{fmt(g['期末'])}</td>
            </tr>
            {g.rows.map((r, i) => <tr key={g.code + i}>
              <td style={{ paddingLeft: 26 }} className="acct">{r['账户']}</td>
              <td>{r['币别']}</td>
              <td className="num">{fmt(r['期初'])}</td>
              <td className="num">{fmt(r['本期借方'])}</td>
              <td className="num">{fmt(r['本期贷方'])}</td>
              <td className="num">{fmt(r['期末'])}</td>
            </tr>)}
          </React.Fragment>))}
          {groups.length > 0 && <tr style={{ background: 'var(--bg)' }}>
            <td style={{ fontWeight: 700 }}>总计</td><td></td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('期初'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('借'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('贷'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('期末'))}</td>
          </tr>}
          {groups.length === 0 && <tr><td colSpan="6" className="muted">无数据。点右上「从金蝶刷新」取数。</td></tr>}
        </tbody>
      </table></div>
      <div className="foot">口径说明：期初取自金蝶科目余额表接口（重复行已去重）；本期借方/贷方由本期序时账逐笔加总；期末＝期初＋本期借方－本期贷方——与金蝶《科目余额表》报表同公式。金蝶接口的"期末"字段在凭证未过账时停在期初，所以不直接用它。外币账户金额为原币口径。</div>
    </div>
  </div>)
}
