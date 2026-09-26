// [Change Log] Date:2026-09-20 Author:Claude/c Version:V2.588
// 科目余额表 · 解析与核对（物流科目段）：财务报表下的独立页——科目余额这笔数据的“生产/落地入口”。
// 系统取数(金蝶报表口径，样例先种子物流分录) + 上传解析(泛化认列，全科目容错) + 质检勾稽(期末=期初+借-贷) + 逐科目核对。
import React, { useEffect, useState, useRef } from 'react'
import { getFiSubjectOrgs, getFiSubjectBalance, syncFiSubjectBalance, getFiSubjectCheck, uploadFiSubjectReport, getFiSubjectDetail, getFiSubjectTrace } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'
import './fisbal.css'

let _cache = null
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const ITEMS = ['期初', '本期借方', '本期贷方', '期末']
const PAGE = 15   // 明细账弹窗每页笔数

// 期末构成清单（按"这笔账"归集抵消，而非按时间 FIFO）：
// 把追溯各期 + 当期的每一条腿，按【业务摘要】归集——同一笔账的 计提 / 红冲 / 核销 / 付款
// 摘要归一后落到同一个 key，互相加减；抵平（净额≈0）的就消失，剩下没抵平的才是构成期末的。
// 之所以不用 FIFO：遇到"冲暂估+按票重估+付款"三合一凭证时，FIFO 会把凭证内的付款拿去冲最早的
// 期初，反把凭证自己重估那笔当成还挂着（张冠李戴）。按业务归集则该单四条腿自相抵平、正确消失。
// 摘要归一：去掉前缀动词与"5/498#"这类单号引用——从供应商名开始截取（同一供应商同月同业务=同一笔账）。
function endComposition(periodsChrono, startOpening, ending, supplierCore) {
  const r2 = n => Math.round((Number(n) || 0) * 100) / 100
  ending = r2(ending); startOpening = r2(startOpening)
  const sign = ending >= 0 ? 1 : -1               // 借方余额+1 / 贷方余额-1（期末方向）
  const core = String(supplierCore || '').replace(/\s+/g, '').trim()
  const norm = memo => {                          // 摘要归一 → 归集 key
    let s = String(memo || '').replace(/\s+/g, '')
    if (core && s.includes(core)) s = s.slice(s.indexOf(core))   // 从供应商名起（丢掉"红冲5/498#计提""黄一飞提起支付"等前缀）
    return s
  }
  const groups = new Map(); let seq = 0
  periodsChrono.forEach((per, pi) => (per.lines || []).forEach(ln => {
    const delta = r2((ln['借'] || 0) - (ln['贷'] || 0))     // 原始有符号净变动（借−贷）
    if (Math.abs(delta) < 0.005) return
    const key = norm(ln['摘要']) || ('#' + seq)
    let g = groups.get(key)
    if (!g) { g = { net: 0, accr: 0, order: pi * 100000 + seq, accrYm: null, accrVou: '', accrMemo: '' }; groups.set(key, g) }
    g.net = r2(g.net + delta)
    if (sign * delta > 0) {                        // 该腿是"计提"方向（把余额往期末方向推）→ 记为原计提代表
      g.accr = r2(g.accr + delta)
      if (g.accrYm == null) { g.accrYm = per.ym; g.accrVou = ln['凭证']; g.accrMemo = ln['摘要'] }
    }
    seq++
  }))
  // 计提 lots（含期初 lump 作最早一笔）＋ 剩余核销（归集内没配平的红冲/付款）
  const lots = []
  if (sign * startOpening > 0.005) lots.push({ kind: '期初', ym: (periodsChrono[0] || {}).ym || '', 原额: startOpening, 仍挂: startOpening, order: -1 })
  let clearing = 0                                 // 剩余核销累计（与期末反向）
  for (const g of groups.values()) {
    if (Math.abs(g.net) < 0.005) continue                        // 这笔账已抵平 → 不构成期末
    if (sign * g.net > 0) lots.push({ kind: '计提', ym: g.accrYm || '', 凭证: g.accrVou || '', 摘要: g.accrMemo || '', 原额: g.accr || g.net, 仍挂: g.net, order: g.order })
    else clearing = r2(clearing + g.net)                          // 净核销、但范围内没配到对应计提 → 待 FIFO 冲抵开项
  }
  // 剩余核销 FIFO 冲抵开项（最早优先）——不倒进更早结转，否则会凭空造出正的“未核销”
  lots.sort((a, b) => a.order - b.order)
  let clr = Math.abs(clearing)
  for (const lot of lots) {
    if (clr <= 0.005) break
    const take = Math.min(Math.abs(lot['仍挂']), clr)
    lot['仍挂'] = r2(sign * (Math.abs(lot['仍挂']) - take)); clr = r2(clr - take)
  }
  const items = lots.filter(lot => Math.abs(lot['仍挂']) > 0.005)
  return { items, 合计: r2(items.reduce((s, it) => s + it['仍挂'], 0)) }
}

export default function FiSubjectBalance({ cfg, onPeriod }) {
  const [d, setD] = useState(_cache), [busy, setBusy] = useState(false)
  const [chk, setChk] = useState(null), [upBusy, setUpBusy] = useState(false), [upMsg, setUpMsg] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [modal, setModal] = useState(null), [md, setMd] = useState(null), [mdBusy, setMdBusy] = useState(false), [mpage, setMpage] = useState(0)
  const [trace, setTrace] = useState(null), [traceBusy, setTraceBusy] = useState(false)
  const [fullDet, setFullDet] = useState(null), [fullBusy, setFullBusy] = useState(false)
  const [orgs, setOrgs] = useState([]), [org, setOrg] = useState('')
  const [q, setQ] = useState('')
  const [hideZero, setHideZero] = useState(true)   // 默认隐藏期末为 0 的维度（已结清、无余额可解析）
  const [checks, setChecks] = useState({})   // 勾选核对：{ gid: Set(行键) }；'op'=期初行，数字=det.lines 全局下标
  const fileRef = useRef(null)
  const openModal = async (code, dimc, kmName, dimName) => {
    setModal({ code, dim: dimc, 科目名: kmName, 维度名: dimName }); setMd(null); setTrace(null); setFullDet(null); setMpage(0); setChecks({}); setMdBusy(true)
    try { setMd(await getFiSubjectDetail(code, dimc, org)) } catch (e) { setMd({ ok: false, note: '取明细失败：' + e.message }) } finally { setMdBusy(false) }
  }
  const closeModal = () => { setModal(null); setMd(null); setTrace(null); setFullDet(null); setChecks({}) }
  // ── 勾选核对（工作台上验算核销，不用计算器）──
  const r2 = n => Math.round((Number(n) || 0) * 100) / 100
  const toggleCheck = (gid, key) => setChecks(p => {
    const s = new Set(p[gid] || []); s.has(key) ? s.delete(key) : s.add(key); return { ...p, [gid]: s }
  })
  const autoCheckCleared = (gid, det, opening) => {   // 一键勾掉系统判定已核销的行（留下标绿「未核销」的＝期末）
    const s = new Set(); (det.lines || []).forEach((ln, i) => { if (!ln['开项']) s.add(i) })
    if (Math.abs(det['期初开项'] || 0) < 0.005 && Math.abs(opening || 0) > 0.005) s.add('op')
    setChecks(p => ({ ...p, [gid]: s }))
  }
  const doTrace = async (code, dimc) => {
    setTraceBusy(true)
    try { setTrace(await getFiSubjectTrace(code, dimc, org)) } catch (e) { setTrace({ ok: false, note: '追溯失败：' + e.message }) } finally { setTraceBusy(false) }
  }
  const loadFull = async (code, dimc) => {
    setFullBusy(true)
    try { setFullDet(await getFiSubjectDetail(code, dimc, org, true)) } catch (e) { setFullDet({ ok: false, note: '取全部凭证失败：' + e.message }) } finally { setFullBusy(false) }
  }
  // 明细账小表：期初行 + 滚动余额 + 未核销开项高亮 + 逐行勾选核对（工作台上验算核销，不用计算器）。
  // opts.gid=勾选分组键；opts.opening=期初；opts.showOpening=是否放期初行(分页时仅首页)；opts.offset=本页首行在 det.lines 的下标。
  const ledgerTable = (det, showLines, opts = {}) => {
    if (!(det && det.lines && det.lines.length > 0)) return <div className="foot">本期无逐笔凭证 —— 本期该维度没有新增计提、也没有核销（本期借/贷为 0），期末余额全部是往期结转下来的。</div>
    const { gid, opening, showOpening, offset = 0, code = '', subjName = '', dimName = '' } = opts
    const chk = gid ? (checks[gid] || new Set()) : null
    const ls = showLines || det.lines
    const ending = det['余额末']
    let checkedNet = 0, checkedCnt = 0
    if (chk) {
      if (chk.has('op')) { checkedNet += (opening || 0); checkedCnt++ }
      det.lines.forEach((ln, i) => { if (chk.has(i)) { checkedNet += (ln['借'] || 0) - (ln['贷'] || 0); checkedCnt++ } })
    }
    checkedNet = r2(checkedNet)
    const uncheckedNet = r2((ending || 0) - checkedNet)
    const balanced = checkedCnt > 0 && Math.abs(checkedNet) < 0.005
    const chkTd = key => <td className="ck-td"><input type="checkbox" checked={chk.has(key)} onChange={() => toggleCheck(gid, key)} style={{ cursor: 'pointer' }} /></td>
    const table = (
      <table className="fsb-tbl">
        <thead><tr>
          {chk && <th className="ck-td">核对</th>}
          <th>日期</th><th>凭证</th><th>摘要</th>
          <th>科目编码</th><th>科目名称</th><th>核算维度</th>
          <th className="r">借方</th><th className="r">贷方</th><th className="r">余额</th><th>制单人</th>
        </tr></thead>
        <tbody>
          {chk && showOpening && opening != null && <tr className={chk.has('op') ? 'struck' : 'opening'}>
            {chkTd('op')}
            <td></td>
            <td style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>期初</td>
            <td>上期结转（期初余额）</td>
            <td className="subj-c">{code}</td><td className="subj-c">{subjName}</td><td className="subj-c">{dimName}</td>
            <td></td><td></td>
            <td className={'n' + ((opening || 0) < 0 ? ' neg' : '')} style={{ fontWeight: 600 }}>{fmt(opening)}</td>
            <td></td>
          </tr>}
          {ls.map((ln, li) => {
            const gi = offset + li, g = chk && chk.has(gi)
            return <tr key={li} className={g ? 'struck' : (ln['开项'] ? 'open' : '')} title={ln['开项'] ? '未核销的开项（构成期末余额）' : undefined}>
              {chk && chkTd(gi)}
              <td style={{ whiteSpace: 'nowrap' }}>{ln['日期']}</td>
              <td style={{ whiteSpace: 'nowrap', fontWeight: (ln['开项'] && !g) ? 700 : 400 }}>{ln['凭证']}{ln['开项'] && !g ? <span className="fsb-tag">未核销</span> : ''}</td>
              <td>{ln['摘要']}</td>
              <td className="subj-c" style={{ whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>{code}</td>
              <td className="subj-c">{subjName}</td>
              <td className="subj-c">{dimName}</td>
              <td className={'n' + (ln['借'] < 0 ? ' neg' : '')}>{ln['借'] ? fmt(ln['借']) : ''}</td>
              <td className={'n' + (ln['贷'] < 0 ? ' neg' : '')}>{ln['贷'] ? fmt(ln['贷']) : ''}</td>
              <td className={'n' + (ln['余额'] < 0 ? ' neg' : '')} style={{ fontWeight: 600 }}>{fmt(ln['余额'])}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{ln['制单人']}</td>
            </tr>
          })}
          <tr className="sum">{chk && <td></td>}<td colSpan={6} className="r">本期发生合计 / 期末余额</td>
            <td className="n">{fmt(det['借合计'])}</td>
            <td className="n">{fmt(det['贷合计'])}</td>
            <td className={'n' + ((det['余额末'] || 0) < 0 ? ' neg' : '')}>{fmt(det['余额末'])}</td><td></td></tr>
        </tbody>
      </table>
    )
    if (!chk) return table
    return <div>
      <div className="fsb-vtools">
        <span className="tip">☑ 勾掉能互相对冲的几笔（灰掉划掉），剩下没勾的应正好＝期末——工作台上核对，不用按计算器。</span>
        <span style={{ flex: 1 }} />
        <button className="btn" style={{ padding: '2px 9px', fontSize: 11.5 }} onClick={() => autoCheckCleared(gid, det, opening)}>一键勾掉已核销</button>
        {checkedCnt > 0 && <button className="btn" style={{ padding: '2px 9px', fontSize: 11.5 }} onClick={() => setChecks(p => ({ ...p, [gid]: new Set() }))}>清除勾选</button>}
      </div>
      <div className="fsb-scroll">{table}</div>
      {checkedCnt > 0 && <div className={'fsb-verify' + (balanced ? ' ok' : '')} style={{ marginTop: 8 }}>
        <b>勾选核对</b>：已勾选 {checkedCnt} 笔，净额 <b className="n" style={{ color: balanced ? 'var(--green)' : 'var(--amber)' }}>{fmt(checkedNet)}</b>（成对核销时应为 0）　·　未勾选剩余 <b className="n">{fmt(uncheckedNet)}</b> {balanced
          ? <span style={{ color: 'var(--green)', fontWeight: 600 }}>✓ 正好＝期末 {fmt(ending)}，核对无误</span>
          : <span style={{ color: 'var(--ink-3)' }}>（把成对的勾到净额为 0，剩余就＝期末 {fmt(ending)}）</span>}
      </div>}
    </div>
  }
  const loadFor = async (o) => {
    setModal(null); setMd(null); setTrace(null)
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
    try { const x = await syncFiSubjectBalance(org); _cache = x; setD(x); setModal(null); setMd(null); setTrace(null); setChk(await getFiSubjectCheck(org)) } finally { setBusy(false) }
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
  const ql = q.trim().toLowerCase()
  const fRows = ql ? rows.filter(r => [r['科目编码'], r['科目名称'], r['账户'], r['科目大类']].join(' ').toLowerCase().includes(ql)) : rows
  const zeroCnt = fRows.filter(r => Math.abs(r['期末'] || 0) < 0.005).length   // 期末为 0（已结清）的维度数
  const vRows = hideZero ? fRows.filter(r => Math.abs(r['期末'] || 0) >= 0.005) : fRows   // 主表实际显示的维度行
  const codes = [...new Set(vRows.map(r => r['科目编码']))]
  const groups = codes.map(c => {
    const rs = vRows.filter(r => r['科目编码'] === c)
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
        <div className="h-sub">物流相关科目（按物流计提入账口径精确圈定）：暂估进项税 2221.01.07 · 其他应付款—供应商往来 2241.02（均挂供应商）· 物流费用 6601/6604/6401/5101（按费用项目：出库/入库运费·仓储费·搬运费）—— 系统取数 + 手工上传两条路都可核对</div></div>
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
      <div className="foot">数据来源：{d.source === 'kingdee' ? '金蝶《科目余额表》报表接口（物流科目段，借−贷有符号口径）' : (d.note || '样例数据')} · 主体 {d.org_name || '—'} · {d.period} · {d.cached_db ? `定格于 ${d['定格于'] || ''}（进页面直接读库，秒开；点右上「从金蝶刷新」才重取）` : `更新于 ${d.updated_at}`}</div>

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
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="🔍 模糊搜索：科目编码 / 名称、供应商、费用项目…"
          style={{ flex: '1 1 300px', maxWidth: 440, height: 34, borderRadius: 8, border: '1px solid var(--line)', background: 'var(--bg-sub)', color: 'var(--ink)', padding: '0 12px', fontSize: 13 }} />
        <label className="ck" title="期末为 0 ＝ 本期已全部结清、没有余额可解析；默认隐藏，让列表只留还挂着余额的维度"><input type="checkbox" checked={hideZero} onChange={e => setHideZero(e.target.checked)} /> 隐藏期末为 0 的{zeroCnt > 0 && <span className="foot" style={{ marginLeft: 4 }}>（{zeroCnt} 个）</span>}</label>
        {ql && <span className="foot">筛出 {fRows.length} 行 / 共 {rows.length} 行<span onClick={() => setQ('')} style={{ color: 'var(--accent)', cursor: 'pointer', marginLeft: 8 }}>清除</span></span>}
      </div>
      <div className="foot" style={{ marginTop: 8 }}>👉 点开任意<b>维度行</b>，弹出它的<b>明细账</b>（逐笔滚动余额、分页）：<span style={{ color: 'var(--green)', fontWeight: 600 }}>标绿「未核销」</span>的那几笔，其和＝期末余额（这笔余额到底挂着哪几笔）；还能一路追溯期初。</div>
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
            {g.rows.map((r, i) => (
              <tr key={g.code + i} className="row" onClick={() => openModal(r['科目编码'], r['维度编码'] || '', r['科目编码'] + ' ' + r['科目名称'], r['账户'])}
                title="点开看这笔余额的明细账（滚动余额 + 未核销开项 + 追溯期初）">
                <td style={{ paddingLeft: 26 }} className="acct"><span style={{ color: 'var(--accent)', marginRight: 4 }}>▸</span>{r['账户']}</td>
                <td></td>
                <td className="num">{fmt(r['期初'])}</td>
                <td className="num">{fmt(r['本期借方'])}</td>
                <td className="num">{fmt(r['本期贷方'])}</td>
                <td className="num" style={{ color: r['期末'] < 0 ? 'var(--red)' : undefined }}>{fmt(r['期末'])}</td>
              </tr>
            ))}
          </React.Fragment>))}
          {groups.length > 0 && <tr style={{ background: 'var(--bg)' }}>
            <td style={{ fontWeight: 700 }}>合计</td><td></td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('期初'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('借'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('贷'))}</td>
            <td className="num" style={{ fontWeight: 700 }}>{fmt(total('期末'))}</td>
          </tr>}
          {groups.length === 0 && <tr><td colSpan="6" className="muted">{ql ? `没有匹配「${q}」的科目/维度。` : (hideZero && zeroCnt > 0 && fRows.length > 0) ? `本期这些维度期末都为 0（已全部结清）。取消勾选「隐藏期末为 0 的」可查看全部 ${zeroCnt} 个。` : '系统侧暂无物流科目数据。样例模式已内置演示数据；金蝶模式请用上方「上传」解析核对。'}</td></tr>}
        </tbody>
      </table></div>
      <div className="foot">口径说明：余额取【借 − 贷】有符号口径，费用类为正、其他应付款（负债）为负，勾稽恒等式 期末 = 期初 + 本期借方 − 本期贷方 恒成立。物流科目段 = 物流计提工具入账落到的科目（销售费用出库运费/仓储费、主营业务成本/制造费用入库运费、研发费用搬运费、其他应付款—供应商往来、暂估进项税）。</div>
    </div>

    {/* 明细账弹窗：滚动余额 + 未核销开项 + 分页 + 追溯期初 */}
    {modal && <div className="fsb-ov" onClick={closeModal}>
      <div className="fsb-modal" onClick={e => e.stopPropagation()}>
        <div className="fsb-mhead">
          <div><div className="fsb-mtitle"><span className="code">{modal.code}</span>{modal.科目名.replace(modal.code, '').trim()} · {modal.维度名}</div>
            <div className="fsb-msub">明细账 · {d.org_name || ''} · {d.period}</div></div>
          <span className="fsb-x" onClick={closeModal} title="关闭">×</span>
        </div>
        <div className="fsb-mbody">
        {mdBusy && <div className="loading">加载明细账…</div>}
        {!mdBusy && md && <>
          <div className={'fsb-hero' + ((md['期末'] || 0) < 0 ? ' neg' : '')}>
            <span className="lbl">本期({d.period}) 期末余额</span>
            <span className={'big' + ((md['期末'] || 0) < 0 ? ' neg' : '')}>{fmt(md['期末'])}</span>
            <span className="eq">＝ 期初 <b>{fmt(md['期初'])}</b> ＋ 本期借 <b>{fmt(md['本期借方'])}</b> － 本期贷 <b>{fmt(md['本期贷方'])}</b></span>
            {md.detail && (md.detail.lines || []).some(l => l['开项']) && <span className="hint"><span className="g">标绿「未核销」</span>那几笔{Math.abs(md.detail['期初开项'] || 0) > 0.005 ? '＝期末里的本期新计提部分' : '的和＝期末（这笔余额的构成）'}。</span>}
          </div>
          {md.note && <div className="foot" style={{ margin: '8px 0 0' }}>{md.note}</div>}
          {(() => {
            const ls = (md.detail && md.detail.lines) || []
            const pages = Math.max(1, Math.ceil(ls.length / PAGE))
            const pg = Math.min(mpage, pages - 1)
            return <div style={{ marginTop: 10 }}>{ledgerTable(md.detail, ls.slice(pg * PAGE, (pg + 1) * PAGE), { gid: 'cur', opening: md['期初'], showOpening: pg === 0, offset: pg * PAGE, code: modal.code, subjName: (modal.科目名 || '').replace(modal.code, '').trim(), dimName: modal.维度名 })}
              {pages > 1 && <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center', marginTop: 8, fontSize: 12.5 }}>
                <button className="btn" disabled={pg <= 0} onClick={() => setMpage(pg - 1)}>上一页</button>
                <span className="foot">第 {pg + 1} / {pages} 页 · 共 {ls.length} 笔</span>
                <button className="btn" disabled={pg >= pages - 1} onClick={() => setMpage(pg + 1)}>下一页</button>
              </div>}
            </div>
          })()}
          {md._debug && <div style={{ marginTop: 8, padding: 8, background: 'var(--bg-sub)', border: '1px dashed var(--amber-line)', borderRadius: 6, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: 'monospace', color: 'var(--ink-2)' }}>🔧 诊断（本期有发生却没匹配到凭证，截图发开发）：{'\n'}{JSON.stringify(md._debug, null, 2)}</div>}
          {d.source === 'kingdee' && md.detail && (Math.abs((md['本期借方'] || 0) - (md.detail['借合计'] || 0)) > 0.005 || Math.abs((md['本期贷方'] || 0) - (md.detail['贷合计'] || 0)) > 0.005) &&
            <div className="banner err" style={{ marginTop: 8 }}>逐笔合计与余额表本期发生对不上：借 差 {fmt((md['本期借方'] || 0) - (md.detail['借合计'] || 0))}、贷 差 {fmt((md['本期贷方'] || 0) - (md.detail['贷合计'] || 0))} —— 多半是某笔凭证摘要没写供应商名、没归进来。</div>}
          {md.detail && Math.abs(md['期初'] || 0) > 0.005 && <div className="foot" style={{ marginTop: 10 }}>
            {Math.abs(md.detail['期初开项'] || 0) > 0.005
              ? <span>⚠ 期末里有 <b style={{ color: 'var(--amber)' }}>{fmt(md.detail['期初开项'])}</b> 来自<b>往期未核销的计提</b>（期初结转还挂着）—— 这部分才需要往前追。</span>
              : <span>✓ 期初 {fmt(md['期初'])} 已在本期<b style={{ color: 'var(--green)' }}>全部核销</b> —— 期末完全由本期新计提构成，不用追溯往期。</span>}
          </div>}
          {md.detail && Math.abs(md.detail['期初开项'] || 0) > 0.005 && <div style={{ marginTop: 10 }}>
            {!trace && <button className="btn primary" onClick={() => doTrace(modal.code, modal.dim)} disabled={traceBusy}>{traceBusy ? '追溯中…' : '↑ 追溯这 ' + fmt(md.detail['期初开项']) + ' 往期来源（逐期往前翻）'}</button>}
            {trace && <div className="fsb-trace">
              {(() => {
                const periodsChrono = [...(trace.chain || [])].reverse().map(c => ({ ym: c.ym, lines: (c.detail && c.detail.lines) || [] }))
                  .concat([{ ym: d.period, lines: (md.detail && md.detail.lines) || [] }])
                const startOpening = (trace.chain && trace.chain.length) ? trace.chain[trace.chain.length - 1]['期初'] : md['期初']
                const supplierCore = String(modal.维度名 || '').split('/')[0]
                const comp = endComposition(periodsChrono, startOpening, md['期末'], supplierCore)
                if (!comp.items.length) return null
                return <div className="fsb-comp">
                  <div className="ttl"><span className="pin">📌</span>期末 {fmt(md['期末'])} 的构成 —— 就是下面这 {comp.items.length} 笔还没核销的账</div>
                  <div className="desc">按【这笔账】归集：同一笔账的 计提 / 红冲 / 核销 / 付款 相互抵消，抵平的就消失，剩下没抵平的才是构成期末的（“仍挂”＝这笔到今天还欠着没结的，加起来正好＝期末）。像"冲暂估+按票重估+付款"三合一凭证，那一单四条腿自相抵平、不再出现。{!trace.reached_zero && <span className="warn"> ⚠ 更早的没追到底，最上面一行是更早结转的汇总。</span>}</div>
                  <div className="fsb-scroll">
                    <table className="fsb-tbl">
                      <thead><tr>{['月份', '凭证', '摘要', '原计提额', '仍挂（构成期末）'].map((h, hi) =>
                        <th key={h} className={hi >= 3 ? 'r' : ''}>{h}</th>)}</tr></thead>
                      <tbody>
                        {comp.items.map((it, ii) => <tr key={ii}>
                          <td style={{ whiteSpace: 'nowrap' }}>{it.kind === '期初' ? (it.ym + ' 前') : it.ym}</td>
                          <td style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>{it.kind === '期初' ? '更早结转' : it['凭证']}</td>
                          <td>{it.kind === '期初' ? `${it.ym} 之前挂着、至今未核销的更早计提（未再逐笔展开）` : it['摘要']}</td>
                          <td className="n">{it.kind === '期初' ? '—' : fmt(it['原额'])}</td>
                          <td className={'n' + (it['仍挂'] < 0 ? ' neg' : '')} style={{ fontWeight: 700 }}>{fmt(it['仍挂'])}</td>
                        </tr>)}
                        <tr className="sum"><td colSpan={4} className="r">合计（＝期末余额）</td>
                          <td className={'n' + ((comp['合计'] || 0) < 0 ? ' neg' : '')}>{fmt(comp['合计'])}</td></tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              })()}
              <div className="thd">逐期明细（核对上面每一笔的来龙去脉）· 期初 {fmt(md['期初'])} 逐期往前翻</div>
              {(trace.note) && <div className="foot" style={{ marginBottom: 6 }}>{trace.note}</div>}
              {(trace.chain || []).map((c, ci) => <div key={ci} className="per">
                <div className="plabel"><b>{c.ym}</b> · 期末 <b>{fmt(c['期末'])}</b> ＝ 期初 <b>{fmt(c['期初'])}</b> ＋ 本期借 <b>{fmt(c['本期借方'])}</b> － 本期贷 <b>{fmt(c['本期贷方'])}</b></div>
                {ledgerTable(c.detail, undefined, { gid: 'tr:' + c.ym, opening: c['期初'], showOpening: true, offset: 0, code: modal.code, subjName: (modal.科目名 || '').replace(modal.code, '').trim(), dimName: modal.维度名 })}
              </div>)}
              {trace.reached_zero && <div className="foot" style={{ color: 'var(--green)' }}>✓ 已追溯到期初为 0 —— 建账起点，到此为止。</div>}
            </div>}
          </div>}
        </>}
        </div>
      </div>
    </div>}
  </div>)
}
