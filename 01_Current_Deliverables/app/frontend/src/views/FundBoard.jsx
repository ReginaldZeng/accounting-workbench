// [Change Log] Date:2026-07-12 Author:Claude/c Version:V2.96（V2.107 资金构成加「按开户行」书签切换）
// 资金看板（独立页）：集团总资金 + 资金构成(科目大类·环形图) + 各主体资金(横条) + 账户明细。
// 配色走 dataviz 校验通过的分类色(--cat-1..4，蓝/青/黄/绿固定顺序)；主体条=单色量级(accent)。
// 数据源=/api/fund-dashboard（科目 1001/1002/1012/1101 期末余额，本位币口径）。
import React, { useEffect, useState } from 'react'
import { getFund, refreshKingdee } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const yuan = n => '¥' + Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })
const yuan2 = n => Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
// 账户性质 → 固定分类色（颜色跟性质走，不随大小；固定顺序）；未列出的落灰
const NAT_VAR = {
  '基本户': '--cat-1', '一般户': '--cat-2', '库存现金': '--cat-3', '理财': '--cat-4',
  '通知存款': '--cat-5', '资本金户': '--cat-6', '第三方支付': '--cat-7', '其它货币': '--cat-8',
  '银行存款': '--cat-1',   // 样例无出纳主数据、无法细分时的兜底色
}
const natColor = c => `var(${NAT_VAR[c] || '--gray'})`
// 按开户行分组时无固定色，按金额排名依次取分类色（前 8 名上色，其余落灰）
const CAT_VARS = ['--cat-1', '--cat-2', '--cat-3', '--cat-4', '--cat-5', '--cat-6', '--cat-7', '--cat-8']
const CUR_CN = { CNY: '人民币', RMB: '人民币', HKD: '港币', USD: '美元', EUR: '欧元' }

// 环形图：科目大类构成。2px 间隙分隔各段（marks 规范）；中心放集团总资金。
function Donut({ data, total }) {
  const cx = 100, cy = 100, rad = 76, w = 26
  const circ = 2 * Math.PI * rad
  const gap = 3            // 段间留白（px 弧长）
  let acc = 0
  return (<svg viewBox="0 0 200 200" width="200" height="200" role="img" aria-label="资金构成环形图" style={{ flex: '0 0 auto' }}>
    <circle cx={cx} cy={cy} r={rad} fill="none" stroke="var(--line)" strokeWidth={w} opacity="0.4" />
    {data.map((d, i) => {
      const frac = total ? d.value / total : 0
      const len = Math.max(frac * circ - gap, 0)
      const seg = <circle key={i} cx={cx} cy={cy} r={rad} fill="none" stroke={d.color || natColor(d.name)} strokeWidth={w}
        strokeDasharray={`${len} ${circ - len}`} strokeDashoffset={-acc} transform={`rotate(-90 ${cx} ${cy})`}>
        <title>{d.name}：{yuan(d.value)}（{(frac * 100).toFixed(1)}%）</title>
      </circle>
      acc += frac * circ
      return seg
    })}
    <text x={cx} y={cy - 9} textAnchor="middle" fontSize="11" fill="var(--ink-3)">集团总资金</text>
    <text x={cx} y={cy + 13} textAnchor="middle" fontSize="19" fontWeight="700" fill="var(--ink)"
      style={{ fontVariantNumeric: 'tabular-nums' }}>{yuan(total)}</text>
  </svg>)
}

// 横条：各主体资金（单色量级，降序，直接标金额）
function Bars({ data }) {
  const max = Math.max(...data.map(d => d.value), 1)
  return (<div>{data.map((d, i) => (
    <div key={i} style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 3 }}>
        <span style={{ color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '58%' }} title={d.name}>{d.name}</span>
        <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{yuan(d.value)}</span>
      </div>
      <div style={{ height: 8, background: 'var(--bg-rail)', borderRadius: 4 }}>
        <div style={{ height: 8, width: Math.max(d.value / max * 100, 1.5) + '%', background: 'var(--accent)', borderRadius: 4 }} />
      </div>
    </div>
  ))}</div>)
}

let _cache = null
export default function FundBoard({ cfg, onPeriod, onNav }) {
  const [d, setD] = useState(_cache), [busy, setBusy] = useState(false), [stamp, setStamp] = useState('')
  const [fSub, setFSub] = useState('all'), [fBank, setFBank] = useState('all'), [fNat, setFNat] = useState('all'), [q, setQ] = useState('')
  const [qNew, setQNew] = useState(false), [qHideZero, setQHideZero] = useState(false)   // 快速筛选：当月新增 / 隐藏0余额户
  const [catBy, setCatBy] = useState(() => { try { return localStorage.getItem('fw_fund_catby') || 'nature' } catch (e) { return 'nature' } })  // 资金构成书签：按账户性质 / 按开户行
  const [catSel, setCatSel] = useState(null)   // 点图例下钻：账户明细只看某一组（如"其他"里到底是哪些户）
  const pickCatBy = k => { setCatBy(k); setCatSel(null); try { localStorage.setItem('fw_fund_catby', k) } catch (e) {} }
  useEffect(() => {
    getFund().then(x => { _cache = x; setD(x); setStamp((x.source === 'kingdee' ? '金蝶' : '样例') + (x.cached ? ' · 缓存' : ' · 已刷新')) }).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  // V2.177：同逐笔稽核——原 /api/fund-dashboard/sync 只重算不取数，改接取数总闸后再重拉看板
  const sync = async () => {
    setBusy(true)
    try {
      const r = await refreshKingdee()
      if (r && r.ok === false) { window.alert(r.msg || '刷新失败'); return }
      const x = await getFund(); _cache = x; setD(x); setStamp((x.source === 'kingdee' ? '金蝶' : '样例') + ' · 已刷新')
    } catch (e) {
      window.alert(String(e).includes('403') ? '无「从金蝶更新」权限，请联系管理员' : '刷新失败：' + (e && e.message ? e.message : e))
    } finally { setBusy(false) }
  }
  if (!d) return <div className="loading">加载中…</div>

  // 筛选：主体 / 开户行 / 科目 下拉 + 账号模糊搜索。图表与明细都从【筛选后】的账户重新聚合，一起联动。
  const allAccts = d.accounts || []
  const subOpts = [...new Set(allAccts.map(a => a['主体']).filter(Boolean))].sort()
  const bankOpts = [...new Set(allAccts.map(a => a['开户行']).filter(Boolean))].sort()
  const natOpts = [...new Set(allAccts.map(a => a['账户性质']).filter(Boolean))]
  const qq = q.trim()
  const isZero = a => Math.abs(Number(a['期末余额(本位币)'] || 0)) < 0.01
  const newCnt = allAccts.filter(a => a['新增']).length
  const zeroCnt = allAccts.filter(isZero).length
  const accts = allAccts.filter(a =>
    (fSub === 'all' || a['主体'] === fSub) &&
    (fBank === 'all' || a['开户行'] === fBank) &&
    (fNat === 'all' || a['账户性质'] === fNat) &&
    (!qq || String(a['账号'] || '').includes(qq)) &&
    (!qNew || a['新增']) &&
    (!qHideZero || !isZero(a)))
  const hasFilter = fSub !== 'all' || fBank !== 'all' || fNat !== 'all' || !!qq || qNew || qHideZero
  const clearFilter = () => { setFSub('all'); setFBank('all'); setFNat('all'); setQ(''); setQNew(false); setQHideZero(false) }
  const chip = active => ({
    fontSize: 12, padding: '5px 11px', borderRadius: 20, cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none',
    border: '1px solid ' + (active ? 'var(--accent)' : 'var(--line-strong)'),
    color: active ? 'var(--accent)' : 'var(--ink-2)', background: active ? 'var(--accent-soft)' : 'var(--bg)',
  })
  // 从筛选后账户聚合（本位币口径，与集团合计一致）
  const bal = a => Number(a['期末余额(本位币)'] || 0)
  // V2.238 本期变动：带正负号与颜色（增绿减红），0 显示"—"不干扰阅读
  const mvTxt = v => (Math.abs(Number(v || 0)) < 0.005 ? '—' : (Number(v) > 0 ? '+' : '') + yuan2(v))
  const mvColor = v => (Math.abs(Number(v || 0)) < 0.005 ? 'var(--ink-3)' : (Number(v) > 0 ? 'var(--green)' : 'var(--red)'))
  const total = accts.reduce((s, a) => s + bal(a), 0)
  const agg = keyFn => {
    const m = {}
    accts.forEach(a => { const k = keyFn(a) || '其他'; m[k] = (m[k] || 0) + bal(a) })
    return Object.entries(m).map(([name, value]) => ({ name, value })).sort((x, y) => y.value - x.value)
  }
  // 资金构成：按账户性质 或 按开户行（库存现金归到「库存现金」这个开户行，因其无银行）
  const catByNature = catBy === 'nature'
  // 按开户行归组：有开户行就用开户行；本就无银行的（库存现金/第三方支付/理财）各自成组；
  // 只把"是银行户、但开户行没识别出来"的留在"其他"（＝需去出纳主数据补开户行的）
  const bankKey = a => {
    if (a['开户行']) return a['开户行']
    const nat = a['账户性质'] || ''
    if (nat === '库存现金' || nat === '第三方支付' || nat === '理财') return nat
    return '其他'
  }
  const groupKeyOf = a => (catByNature ? (a['账户性质'] || '其他') : bankKey(a))
  const cats = agg(catByNature ? (a => a['账户性质']) : bankKey)
    .map((c, i) => ({ ...c, color: catByNature ? natColor(c.name) : `var(${CAT_VARS[i] || '--gray'})` }))
  const subs = agg(a => a['主体'])
  // 账户明细：点图例下钻后只看选中的那一组（否则看全部筛选后账户）
  const tableAccts = catSel ? accts.filter(a => groupKeyOf(a) === catSel) : accts

  return (<div>
    <div className="head">
      <div><div className="h-title">资金看板</div>
        <div className="h-sub">集团资金全景 · 科目 1001/1002/1012/1101 · 期初 / 本期变动 / 期末（原币与本位币）· 一眼看清钱在哪、这月多了还是少了</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
        <button className="btn" onClick={sync} disabled={busy}>{busy ? '接入中…' : '刷新金蝶数据'}</button>
      </div>
    </div>
    <div className="body">
      {d.error && <div className="banner err">金蝶取数失败：{d.error}</div>}
      {d['未取数'] && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>
        本期未取数：请先到<b>「数据接入」</b>点<b>「从金蝶更新」</b>取回本月金蝶数据。
        {onNav && <a onClick={() => onNav('import')} style={{ marginLeft: 8, color: 'var(--accent)', cursor: 'pointer', fontWeight: 600 }}>去数据接入 ›</a>}
      </div>}
      {/* V2.238 三段总览：期初 → 本期变动 → 期末（本位币口径），一眼看清这个月钱是多了还是少了 */}
      {d['集团期初'] != null && <div style={{ display: 'flex', alignItems: 'stretch', gap: 10, margin: '4px 0 12px', flexWrap: 'wrap' }}>
        {[['期初余额', d['集团期初'], 'var(--ink-2)'],
          ['本期变动', d['集团本期变动'], (Number(d['集团本期变动'] || 0) >= 0 ? 'var(--green)' : 'var(--red)')],
          ['期末余额', d['集团合计'], 'var(--ink)']].map(([lb, v, c], i) =>
          <React.Fragment key={lb}>
            {i > 0 && <div style={{ alignSelf: 'center', color: 'var(--ink-3)', fontSize: 15 }}>{i === 1 ? '+' : '='}</div>}
            <div style={{ flex: '1 1 200px', padding: '9px 14px', background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 10 }}>
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 3 }}>{lb}<span style={{ marginLeft: 5 }}>· 本位币</span></div>
              <div style={{ fontSize: 18, fontWeight: 700, color: c, fontVariantNumeric: 'tabular-nums' }}>
                {lb === '本期变动' && Number(v || 0) > 0 ? '+' : ''}{yuan2(v)}</div>
            </div>
          </React.Fragment>)}
      </div>}
      <div className="foot" style={{ marginBottom: 10 }}>数据源：{stamp}{d['金蝶取数']?.at ? <>　·　{d['金蝶取数'].by || '—'} 于 {d['金蝶取数'].at} 刷新</> : null} · {d.period}</div>

      {/* 筛选条（同项目 .fbar 样式）：主体 / 开户行 / 科目 + 账号模糊搜索；图表与明细一起联动 */}
      <div className="fbar" style={{ marginBottom: 14 }}>
        <span className="fl">筛选</span>
        <label>主体
          <select value={fSub} onChange={e => setFSub(e.target.value)}>
            <option value="all">全部主体</option>
            {subOpts.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>开户行
          <select value={fBank} onChange={e => setFBank(e.target.value)}>
            <option value="all">全部开户行</option>
            {bankOpts.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>性质
          <select value={fNat} onChange={e => setFNat(e.target.value)}>
            <option value="all">全部性质</option>
            {natOpts.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>搜索
          <input type="text" value={q} onChange={e => setQ(e.target.value)} placeholder="账号关键字（模糊匹配）" />
        </label>
        {/* 快速筛选：一键切换 */}
        <span style={chip(qNew)} onClick={() => setQNew(v => !v)} title="本期在金蝶科目余额里首次出现的账户">✦ 当月新增{newCnt ? ` ${newCnt}` : ''}</span>
        <span style={chip(qHideZero)} onClick={() => setQHideZero(v => !v)} title="把期末余额（本位币）为 0 的账户从看板隐藏，只看有钱的户">隐藏0余额户{zeroCnt ? ` ${zeroCnt}` : ''}</span>
        {hasFilter && <span className="lk" onClick={clearFilter}>清除筛选（当前 {accts.length}/{allAccts.length} 户）</span>}
      </div>
      {qNew && !d['新增可判断'] && <div className="foot" style={{ margin: '-6px 0 12px', color: 'var(--amber)' }}>
        上一期没有金蝶数据，暂时无法判断"当月新增"——先到上一期「数据接入」刷新一次金蝶即可对比。
      </div>}

      {/* 资金构成（环形图 + 图例）+ 各主体资金（横条），并排两卡；按各自内容高度排，不强行拉平留空 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(300px,1fr) minmax(300px,1fr)', gap: 14, marginBottom: 14, alignItems: 'start' }}>
        <div className="cat">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>资金构成</div>
            {/* 书签式切换：按账户性质 / 按开户行（不换页，同一张环形图切口径） */}
            <div style={{ display: 'inline-flex', background: 'var(--bg-rail)', borderRadius: 8, padding: 2, flex: '0 0 auto' }}>
              {[['nature', '按账户性质'], ['bank', '按开户行']].map(([k, label]) => (
                <span key={k} onClick={() => pickCatBy(k)} style={{
                  fontSize: 12, padding: '4px 11px', borderRadius: 6, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
                  fontWeight: catBy === k ? 600 : 400, color: catBy === k ? 'var(--accent)' : 'var(--ink-3)',
                  background: catBy === k ? 'var(--bg)' : 'transparent', boxShadow: catBy === k ? '0 1px 3px rgba(28,32,58,.12)' : 'none',
                }}>{label}</span>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
            <Donut data={cats} total={total} />
            <div style={{ flex: 1, minWidth: 190 }}>
              {cats.map((c, i) => {
                const sel = catSel === c.name
                return (<div key={i} onClick={() => setCatSel(sel ? null : c.name)}
                  title={sel ? '再点取消·看全部' : `只看「${c.name}」的账户`}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', margin: i ? '0' : '0', borderTop: i ? '1px solid var(--line)' : 'none', cursor: 'pointer', borderRadius: 6, background: sel ? 'var(--accent-soft)' : undefined }}>
                  <span style={{ width: 10, height: 10, borderRadius: 3, background: c.color, flex: 'none' }} />
                  <span style={{ fontSize: 12.5, color: sel ? 'var(--accent)' : 'var(--ink-2)', fontWeight: sel ? 600 : 400 }}>{c.name}</span>
                  <span style={{ marginLeft: 'auto', fontSize: 12.5, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{yuan(c.value)}</span>
                  <span style={{ fontSize: 11, color: 'var(--ink-3)', width: 42, textAlign: 'right' }}>{total ? (c.value / total * 100).toFixed(1) : 0}%</span>
                </div>)
              })}
              <div style={{ fontSize: 11, color: 'var(--ink-3)', padding: '6px 8px 0' }}>点上面任意一组，下方明细只看该组</div>
            </div>
          </div>
        </div>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>各主体资金 <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}>· {subs.length} 个主体，降序</span></div>
          {subs.length ? <Bars data={subs} /> : <div className="loading" style={{ padding: 16, fontSize: 12.5 }}>暂无数据</div>}
        </div>
      </div>

      {/* 账户明细 */}
      <div className="cat">
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          账户明细 <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}>· {tableAccts.length} 个账户</span>
          {catSel && <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--accent)', background: 'var(--accent-soft)', borderRadius: 20, padding: '3px 10px', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            只看：{catSel}<span onClick={() => setCatSel(null)} style={{ cursor: 'pointer', color: 'var(--ink-3)', fontWeight: 700 }}>✕</span>
          </span>}
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: 1180, fontSize: 12.5, borderCollapse: 'collapse' }}>
            {/* V2.238 三段六列：期初 / 本期变动 / 期末，各分原币与本位币（需求方定）。两层表头，同段两列并在一组下 */}
            <thead>
              <tr>
                {[['主体', 'left'], ['开户行', 'left'], ['账号', 'left'], ['账户性质', 'center'], ['币别', 'center']].map(([h, al]) =>
                  <th key={h} rowSpan={2} style={{ textAlign: al, padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{h}</th>)}
                {['期初余额', '本期变动', '期末余额'].map(h =>
                  <th key={h} colSpan={2} style={{ textAlign: 'center', padding: '5px 8px', color: 'var(--ink-2)', borderBottom: '1px solid var(--line)', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>)}
                <th rowSpan={2} style={{ textAlign: 'center', padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, verticalAlign: 'bottom' }}>状态</th>
              </tr>
              <tr>
                {['原币', '本位币', '原币', '本位币', '原币', '本位币'].map((h, i) =>
                  <th key={i} style={{ textAlign: 'right', padding: '4px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 400, fontSize: 11.5, whiteSpace: 'nowrap' }}>{h}</th>)}
              </tr>
            </thead>
            <tbody>{tableAccts.map((a, i) => {
              const cur = CUR_CN[a['币种']] || a['币种'] || '人民币'
              const foreign = cur !== '人民币'
              const active = a['_active'] !== false
              return <tr key={i} style={{ opacity: active ? 1 : 0.5 }}>
                <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>
                  {a['主体']}
                  {a['新增'] && <span title="当月新增账户" style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, color: 'var(--accent)', background: 'var(--accent-soft)', border: '1px solid var(--accent)', borderRadius: 4, padding: '0 4px', verticalAlign: 'middle' }}>*New</span>}</td>
                <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', color: a['开户行'] ? 'var(--ink-1)' : 'var(--ink-3)' }}>{a['开户行'] || '—'}</td>
                <td className="acct" style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>{a['账号']}
                  {a['本期新开'] && <span title="本期新开的核算维度（如当月新买的理财产品）——余额表尚无此行，数据来自本期序时账" style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, color: 'var(--violet)', background: 'var(--violet-bg, #f5e9ff)', border: '1px solid var(--violet)', borderRadius: 4, padding: '0 4px', verticalAlign: 'middle' }}>本期新开</span>}</td>
                <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: natColor(a['账户性质']) }} />{a['账户性质']}</span>
                </td>
                <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)', color: foreign ? 'var(--blue)' : 'var(--ink-3)', fontWeight: foreign ? 600 : 400 }}>{cur}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontVariantNumeric: 'tabular-nums', color: 'var(--ink-2)' }}>{yuan2(a['期初余额(原币)'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontVariantNumeric: 'tabular-nums', color: 'var(--ink-2)' }}>{yuan2(a['期初余额(本位币)'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontVariantNumeric: 'tabular-nums', color: mvColor(a['本期变动(原币)']) }}>{mvTxt(a['本期变动(原币)'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontVariantNumeric: 'tabular-nums', color: mvColor(a['本期变动(本位币)']) }}>{mvTxt(a['本期变动(本位币)'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontVariantNumeric: 'tabular-nums' }}>{yuan2(a['期末余额(原币)'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{yuan2(a['期末余额(本位币)'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)', color: active ? 'var(--green)' : 'var(--ink-3)' }}>{active ? '生效' : '已停用'}</td>
              </tr>
            })}</tbody>
          </table>
        </div>
      </div>
    </div>
  </div>)
}
