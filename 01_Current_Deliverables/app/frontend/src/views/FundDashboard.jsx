// [Change Log] Date:2026-07-06 Author:Claude/c Version:V2.34
// 余额调节表：①账户名完整不截断(+账户名称) ②加「币别」列(外币蓝字) ③加「未达原因」列——
// 有「认领/处理差异」权限的会计可填/改，显示填写人+时间，供领导核查(领导只读可见)。
// [Change Log] Date:2026-07-03 Author:Claude/c Version:V1.1 资金看板冷灰重构+缓存不清屏
import React, { useEffect, useState } from 'react'
import { getFund, syncFund, getBalanceAdjust, syncBalanceAdjust, getChannelAdjust, syncChannelAdjust, saveBalanceNote, yuan } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'
import Steps from '../components/Steps.jsx'

let _cache = null   // 跨视图切换保留
// 余额调节表 表头 hover 小字（列标题保持短，碰一下才浮出说明）
const TIP = {
  '银行对账单余额': '＝该账户银行流水的末笔余额（即银行侧期末余额）',
  '金蝶账面余额': '＝金蝶科目余额表·期末余额。因本月未过账，余额表接口“期末”字段停在期初，故用「期初＋本期序时账」实时算，结果与金蝶科目余额表期末完全一致',
  '本位币账面': '金蝶账面的账簿本位币金额（境外账套 Sinkio/Starfield 本位币=美元、境内=人民币），与逐笔稽核"本位币"口径一致；人民币户与原币相同故显"—"。金蝶记账口径，不做集团人民币折算',
  '银行对账单余额': '＝该账户银行流水末笔余额，是本表的【锚点·真实】。金蝶账面要向它看齐、最终等于它',
  '金蝶待更正': '＝金蝶单边净额（金蝶已记、银行没有）。以银行为准，这些很大概率是金蝶做错（重复/错账户/内部划转记本户）→ 金蝶应冲减或改到正确账户',
  '金蝶应补记': '＝银行已记、金蝶未记（疑似漏账＋内部往来未做账）→ 金蝶应补做账',
  '更正后账面': '＝金蝶账面 − 金蝶待更正（冲错）＋ 金蝶应补记（补漏）。金蝶照此更正后，账面就等于银行余额',
  '对银行差额': '＝更正后账面 − 银行对账单余额，应为 0。不为 0＝还有说不清的差异，需人工查',
  '未达原因 / 说明': '会计对该户未达账项的解释（如"6月末利息银行已入、金蝶7月补记"），供领导核查；有未达或不平的账户建议填',
}
// 币别代码 → 中文（台账存 CNY/HKD/USD，与逐笔稽核的中文币别口径统一）
const CUR_CN = { CNY: '人民币', RMB: '人民币', HKD: '港币', USD: '美元', EUR: '欧元', GBP: '英镑', JPY: '日元' }
const curCn = c => CUR_CN[c] || c || ''

// 钩稽面板的一环（做法2）：左侧 ✓/! 徽标 + 环名 + 明细
function TieRow({ n, title, sub, ok, star, children }) {
  return (<div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 0', borderTop: '1px solid var(--line)' }}>
    <span style={{ width: 20, height: 20, borderRadius: '50%', flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, marginTop: 1, background: ok ? 'var(--green-bg)' : 'var(--amber-bg)', color: ok ? 'var(--green)' : 'var(--amber)' }}>{ok ? '✓' : '!'}</span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 12.5, marginBottom: 2 }}><b>{n} {title}</b> <span style={{ color: 'var(--ink-3)', fontSize: 11.5 }}>{sub}</span>{star ? <span style={{ color: 'var(--accent)', marginLeft: 4 }} title="对金蝶科目余额表核对">★</span> : null}</div>
      <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{children}</div>
    </div>
  </div>)
}

export default function FundDashboard({ cfg, onPeriod, onNav, user }) {
  const canNote = !!(user && (user.role === 'admin' || (user.perms || {}).claim))   // 会计填未达原因=认领/处理差异权限
  const [d, setD] = useState(_cache)
  const [busy, setBusy] = useState(false), [stamp, setStamp] = useState('')
  const [ba, setBa] = useState(null), [baBusy, setBaBusy] = useState(false)
  const [ca, setCa] = useState(null), [caBusy, setCaBusy] = useState(false)
  const [editAcct, setEditAcct] = useState(null), [editText, setEditText] = useState(''), [noteBusy, setNoteBusy] = useState(false)
  const [baMain, setBaMain] = useState('all')   // 余额调节表·主体筛选
  useEffect(() => {
    getFund().then(x => { _cache = x; setD(x); setStamp((x.source === 'kingdee' ? '金蝶' : '样例') + (x.cached ? ' · 缓存(秒开)' : ' · 已刷新')) }).catch(() => {})
    getBalanceAdjust().then(setBa).catch(() => {})
    getChannelAdjust().then(setCa).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  const refreshBa = async () => { setBaBusy(true); try { setBa(await syncBalanceAdjust()) } finally { setBaBusy(false) } }
  const startEdit = (a) => { setEditAcct(a['账号']); setEditText(a['未达原因'] || '') }
  const saveNote = async (acct) => {
    setNoteBusy(true)
    try {
      const r = await saveBalanceNote({ acct, note: editText })
      if (!r.ok) { alert(r.msg || '保存失败'); return }
      setBa(prev => ({ ...prev, accounts: prev.accounts.map(x => x['账号'] === acct
        ? { ...x, 未达原因: editText.trim(), 原因填写人: r.operator, 原因时间: r.ts } : x) }))
      setEditAcct(null)
    } catch (e) { alert(String(e.message || e)) } finally { setNoteBusy(false) }
  }
  const refreshCa = async () => { setCaBusy(true); try { setCa(await syncChannelAdjust()) } finally { setCaBusy(false) } }
  const sync = async () => {
    setBusy(true); setStamp('接入中…')
    try { const x = await syncFund(); _cache = x; setD(x); setStamp((x.source === 'kingdee' ? '金蝶' : '样例') + ' · 已刷新') } finally { setBusy(false) }
  }
  if (!d) return <div className="loading">加载中…</div>

  // 余额调节表·主体筛选：下拉选主体，表格与对平/不平计数都跟着收窄
  const baAll = (ba && ba.accounts) || []
  const baMains = [...new Set(baAll.map(a => a['主体']).filter(Boolean))].sort()
  const baRows = baAll.filter(a => baMain === 'all' || a['主体'] === baMain)
  const baStat = {
    ok: baRows.filter(a => a['状态'] === '账实相符').length,
    fix: baRows.filter(a => a['状态'] === '待金蝶更正').length,
    diff: baRows.filter(a => a['状态'] === '不明差异').length,
    na: baRows.filter(a => a['状态'] === '缺账面').length,
  }

  return (<div>
    <div className="head">
      <div><div className="h-title">银行存款余额调节</div><div className="h-sub">四步工作流第 3 步 · 银行存款余额调节表（银行对账单 vs 金蝶账面，未达账项两边调平）+ 第三方渠道余额勾稽（资金全景请看侧栏「资金看板」）</div></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={cfg['数据状态']} />
      </div>
    </div>
    <div className="body">
      <Steps current="balance" onNav={onNav} sub={{ balance: '银行存款余额调节' }} />
      {d.error && <div className="banner err">金蝶取数失败：{d.error}</div>}
      {d['未取数'] && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>
        本期未取数：请先到<b>「数据接入」</b>点<b>「从金蝶更新」</b>取回本月金蝶数据。
        {onNav && <a onClick={() => onNav('import')} style={{ marginLeft: 8, color: 'var(--accent)', cursor: 'pointer', fontWeight: 600 }}>去数据接入 ›</a>}
      </div>}
      <div className="foot">数据源：{stamp} · 科目 1001/1002/1012/1101 · {d.period}</div>

      {/* 看表说明：未达账项两侧调节的白话解释，默认折叠，点开才展开，不打扰熟手 */}
      <details className="explain">
        <summary>怎么看这张表？「银行侧未达 / 账面侧未达」是什么意思（点开）</summary>
        <div className="explain-in">
          <p>银行余额和金蝶账面对不上，大多不是记错，而是<b>同一笔钱一边记了、另一边还没记</b>的时间差——这些就叫<b>未达账项</b>。调节表把两边各自缺的那笔补上，补完就应该一样。</p>
          <div className="explain-grid">
            <div className="ex-card">
              <div className="ex-h">银行侧未达</div>
              <div className="ex-sub">企业已记、银行未记</div>
              <p>金蝶账上已经入了，银行流水里还没这笔（来自逐笔稽核的<b>「金蝶单边·待查」</b>）。银行对账单余额还没含它，所以补在<b>银行侧</b>。</p>
            </div>
            <div className="ex-card">
              <div className="ex-h">账面侧未达</div>
              <div className="ex-sub">银行已记、企业未记</div>
              <p>银行流水里已经有了，金蝶还没入账（来自<b>「疑似漏账」＋「内部往来·未做账」</b>）。金蝶账面余额还没含它，所以补在<b>账面侧</b>。</p>
            </div>
          </div>
          <p className="ex-foot">两边各自补完未达 → <b>调节后银行余额 ＝ 调节后账面余额</b> → 差额 0 就是<b style={{ color: 'var(--green)' }}>对平 ✓</b>，账实相符。某户<b style={{ color: 'var(--red)' }}>不平</b> ＝ 还有一笔差额没有未达账项能解释，多为上月滚过来的<b>期初跨期未达</b>，需人工看一眼。</p>
        </div>
      </details>

      {/* 钩稽关系面板（做法2）：把核对链摊开，每环带勾稽结果，重点是"对金蝶科目余额表"这一环 */}
      {ba && ba.钩稽 && (() => {
        const t = ba.钩稽, cnt = t.笔数 || {}, b2 = t.余额调节 || {}, sb = t.科目余额表 || {}
        const chip = ok => <span style={{ color: ok ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>{ok ? '✓' : '✗'}</span>
        return <div className="cat" style={{ marginTop: 4, marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>钩稽关系 · 核对链
            <span style={{ marginLeft: 8, fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}>每个数都追得到源头、当面咬合，非黑箱；供领导 / 审计核查</span>
          </div>
          <TieRow n="①" title="笔数勾稽" sub="账证相符 · 每一笔都归了类" ok={cnt.银行对平 && cnt.金蝶对平}>
            银行 <b>{cnt.银行笔数}</b> 笔 {chip(cnt.银行对平)} 全归类　·　金蝶 <b>{cnt.金蝶笔数}</b> 笔 {chip(cnt.金蝶对平)} 全归类
          </TieRow>
          <TieRow n="②" title="余额调节勾稽" sub="账实相符 · 以银行为锚点，金蝶更正后 = 银行余额" ok={(b2.待金蝶更正 || 0) === 0 && (b2.不明差异 || 0) === 0}>
            覆盖 <b>{b2.覆盖户数}</b> 户 · 账实相符 <b style={{ color: 'var(--green)' }}>{b2.账实相符}</b>
            {b2.待金蝶更正 ? <span> · 待金蝶更正 <b style={{ color: 'var(--amber)' }}>{b2.待金蝶更正}</b></span> : null}
            {b2.不明差异 ? <span> · 不明差异 <b style={{ color: 'var(--red)' }}>{b2.不明差异}</b></span> : null}
            {(b2.待金蝶更正 || b2.不明差异) ? <span style={{ color: 'var(--ink-3)' }}>（金蝶单边＝疑似金蝶做错，见下表"金蝶待更正"列）</span> : null}
          </TieRow>
          <TieRow n="③" title="对金蝶科目余额表" sub="账账相符 · 金蝶 1002 每个有余额的户都核对到" ok={sb.未纳入户数 === 0} star>
            金蝶科目余额表 1002 共 <b>{sb.有余额户数}</b> 户有余额　=　已纳入调节 <b style={{ color: 'var(--green)' }}>{sb.已纳入调节}</b> 户　+　未纳入 <b style={{ color: sb.未纳入户数 ? 'var(--amber)' : 'var(--ink-3)' }}>{sb.未纳入户数}</b> 户
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>口径：{sb.口径}；调节表的"金蝶账面余额"即科目余额表该户期末，逐户一致</div>
            {sb.未纳入户数 > 0 && <div style={{ marginTop: 6, background: 'var(--bg-rail)', borderRadius: 8, padding: '8px 10px' }}>
              <div style={{ fontSize: 11.5, color: 'var(--amber)', marginBottom: 4 }}>⚠ 下列户金蝶有余额、但没导银行流水，<b>尚未做银行余额调节</b>（核对缺口，看是否要补银行对账单）：</div>
              {sb.未纳入户.map((u, i) => <div key={i} style={{ fontSize: 12, display: 'flex', gap: 8, flexWrap: 'wrap', padding: '2px 0' }}>
                <span style={{ color: 'var(--ink-2)' }}>{u.主体 ? String(u.主体).slice(0, 12) : '—'}</span>
                <span style={{ color: 'var(--ink-3)' }}>{u.开户行}</span>
                <span className="acct">{u.账号}</span>
                <span style={{ marginLeft: 'auto', fontWeight: 600 }}>{yuan(u.金蝶账面)}{u.币别 && u.币别 !== 'CNY' && u.币别 !== '人民币' ? <span style={{ color: 'var(--blue)', fontSize: 11 }}> {curCn(u.币别)}</span> : null}</span>
              </div>)}
            </div>}
          </TieRow>
        </div>
      })()}

      {/* 银行存款余额调节表：银行对账单 vs 金蝶账面，用逐笔稽核的未达账项两边调节 → 对平 */}
      {ba && ba.accounts && ba.accounts.length > 0 && <div className="cat" style={{ marginTop: 4 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>银行存款余额调节表 <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}>以银行流水为锚点</span>
            <span style={{ marginLeft: 10, fontSize: 12 }}>
              <span style={{ color: 'var(--green)' }}>账实相符 {baStat.ok}</span>
              {baStat.fix ? <span> · <span style={{ color: 'var(--amber)' }}>待金蝶更正 {baStat.fix}</span></span> : null}
              {baStat.diff ? <span> · <span style={{ color: 'var(--red)' }}>不明差异 {baStat.diff}</span></span> : null}
              {baStat.na ? <span> · <span style={{ color: 'var(--ink-3)' }}>缺账面 {baStat.na}</span></span> : null}
              {baMain !== 'all' ? `　（${baMain}）` : ''}</span>
          </div>
          <button className="btn" onClick={refreshBa} disabled={baBusy}>{baBusy ? '刷新中…' : '刷新'}</button>
        </div>
        <div className="foot" style={{ marginBottom: 8 }}><b>银行流水是准的</b>，金蝶账面要向银行看齐。金蝶单边（金蝶有、银行无）很大概率是金蝶做错 → 记「金蝶待更正」；银行有金蝶没记的 → 记「金蝶应补记」。金蝶按此冲错、补漏后应＝银行余额（对银行差额=0）。<b style={{ color: 'var(--amber)' }}>有金蝶待更正的户不算对平，需去金蝶更正</b>。</div>
        {/* 主体筛选：与逐笔稽核筛选条同款 .fbar */}
        <div className="fbar" style={{ marginBottom: 10 }}>
          <span className="fl">筛选</span>
          <label>主体
            <select value={baMain} onChange={e => setBaMain(e.target.value)}>
              <option value="all">全部主体</option>
              {baMains.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          {baMain !== 'all' && <span className="lk" onClick={() => setBaMain('all')}>清除筛选</span>}
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: 1160, fontSize: 12.5, borderCollapse: 'collapse' }}>
            <thead><tr>{['主体 / 账户名称', '账号', '币别', '银行对账单余额', '金蝶账面余额', '本位币账面', '金蝶待更正', '金蝶应补记', '更正后账面', '对银行差额', '状态', '未达原因 / 说明'].map((h, i) =>
              <th key={h} title={TIP[h] || undefined} style={{ textAlign: i === 0 || i === 11 ? 'left' : (i === 2 || i === 10 ? 'center' : 'right'), padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap', cursor: TIP[h] ? 'help' : 'default', background: i === 3 ? 'var(--accent-soft,var(--accent-soft))' : undefined }}>{h}{TIP[h] ? <span style={{ color: 'var(--ink-4,var(--ink-3))', marginLeft: 3, fontSize: 11 }}>ⓘ</span> : null}</th>)}</tr></thead>
            <tbody>{baRows.map((a, i) => {
              const cur = curCn(a['币别'])
              const foreign = cur && cur !== '人民币'
              const nm2 = (a['账户名称'] && a['账户名称'] !== a['主体']) ? a['账户名称'] : ''
              const editing = editAcct === a['账号']
              const st = a['状态'] || ''
              const stColor = st === '账实相符' ? 'var(--green)' : st === '待金蝶更正' ? 'var(--amber)' : st === '不明差异' ? 'var(--red)' : 'var(--ink-3)'
              const stMark = st === '账实相符' ? '✓ 账实相符' : st === '待金蝶更正' ? '⚠ 待金蝶更正' : st === '不明差异' ? '✗ 不明差异' : '缺账面'
              const fix = a['金蝶待更正'], add = a['金蝶应补记'], gap = a['对银行差额']
              return <tr key={i}>
                <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', minWidth: 150 }}>{a['主体'] || a['账户名称'] || '—'}<div className="sub">{[nm2, a['开户行']].filter(Boolean).join(' · ') || ''}</div></td>
                <td className="acct" style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>{a['账号']}</td>
                <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)', color: foreign ? 'var(--blue)' : 'var(--ink-3)', fontWeight: foreign ? 600 : 400, whiteSpace: 'nowrap' }}>{cur || '—'}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontWeight: 600, background: 'var(--accent-soft,var(--accent-soft))' }}>{yuan(a['银行对账单余额'])}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{a['金蝶账面余额'] != null ? yuan(a['金蝶账面余额']) : '—'}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' }}>{a['金蝶账面本位币'] != null ? <span style={{ color: 'var(--blue)' }}>{Number(a['金蝶账面本位币']).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}<span style={{ color: 'var(--ink-3)', fontSize: 11, marginLeft: 3 }}>美元</span></span> : <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: (fix && Math.abs(fix) > 0.01) ? 'var(--amber)' : 'var(--ink-3)', fontWeight: (fix && Math.abs(fix) > 0.01) ? 600 : 400 }}>{(fix && Math.abs(fix) > 0.01) ? yuan(fix) : '—'}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: (add && Math.abs(add) > 0.01) ? 'var(--violet)' : 'var(--ink-3)' }}>{(add && Math.abs(add) > 0.01) ? yuan(add) : '—'}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontWeight: 600 }}>{a['更正后账面'] != null ? yuan(a['更正后账面']) : '—'}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: (gap != null && Math.abs(gap) > 0.01) ? 'var(--red)' : 'var(--green)', whiteSpace: 'nowrap' }}>{gap == null ? '—' : Math.abs(gap) > 0.01 ? yuan(gap) : '0 ✓'}</td>
                <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)', color: stColor, fontWeight: 600, whiteSpace: 'nowrap' }}>{stMark}</td>
                <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', minWidth: 220 }}>
                  {editing
                    ? <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                        <textarea autoFocus value={editText} onChange={e => setEditText(e.target.value)} rows={2}
                          placeholder="填未达原因，如：6月末利息银行已入、金蝶7月补记"
                          style={{ flex: 1, minWidth: 150, fontSize: 12, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--line-strong,#cfcdc4)', fontFamily: 'inherit', resize: 'vertical' }} />
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          <button className="btn" style={{ height: 24, padding: '0 8px', fontSize: 12 }} onClick={() => saveNote(a['账号'])} disabled={noteBusy}>{noteBusy ? '…' : '保存'}</button>
                          <span className="lk" style={{ fontSize: 11 }} onClick={() => setEditAcct(null)}>取消</span>
                        </div>
                      </div>
                    : (a['未达原因']
                        ? <div>
                            <div style={{ fontSize: 12, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>{a['未达原因']}</div>
                            <div className="sub" style={{ marginTop: 2 }}>{a['原因填写人']}{a['原因时间'] ? ' · ' + a['原因时间'] : ''}{canNote ? <span className="lk" style={{ marginLeft: 8, fontSize: 11 }} onClick={() => startEdit(a)}>改</span> : null}</div>
                          </div>
                        : (a['有未达']
                            ? (canNote ? <span className="lk" style={{ fontSize: 12 }} onClick={() => startEdit(a)}>+ 填原因</span> : <span style={{ color: 'var(--amber)', fontSize: 12 }}>待会计说明</span>)
                            : <span style={{ color: 'var(--ink-3)' }}>—</span>))}
                </td>
              </tr>
            })}</tbody>
          </table>
        </div>
      </div>}

      {/* 第三方渠道余额勾稽：支付宝等渠道对账单期末余额 vs 金蝶1012账面，逐笔对不了→核对总额(本期净+期末余额) */}
      {ca && ca.channels && ca.channels.length > 0 && <div className="cat">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>第三方渠道余额勾稽（支付宝等 · 1012）
            <span style={{ marginLeft: 10, fontSize: 12 }}><span style={{ color: 'var(--green)' }}>净一致 {ca['净一致户数']}</span> / 共 {ca['总户数']} 户</span>
          </div>
          <button className="btn" onClick={refreshCa} disabled={caBusy}>{caBusy ? '刷新中…' : '刷新'}</button>
        </div>
        <div className="foot" style={{ marginBottom: 8 }}>渠道海量微交易 vs 金蝶汇总，逐笔对不了→核对总额：<b>本期净</b>一致即渠道进出与金蝶相符；期末余额差=期初跨期差（同银行调节）。</div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: 720, fontSize: 12.5, borderCollapse: 'collapse' }}>
            <thead><tr>{['渠道账户', '笔数', '渠道期末余额', '本期净', '金蝶维度', '金蝶账面', '本期净一致', '余额差'].map((h, i) =>
              <th key={h} style={{ textAlign: i === 0 || i === 4 ? 'left' : (i === 6 ? 'center' : 'right'), padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>)}</tr></thead>
            <tbody>{ca.channels.map((c, i) => <tr key={i}>
              <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', wordBreak: 'break-all' }}>{c['支付宝账户']}<div className="sub">{c['渠道']}</div></td>
              <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{c['笔数']}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{c['渠道期末余额'] != null ? yuan(c['渠道期末余额']) : '—'}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{yuan(c['本期净'])}</td>
              <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', color: c['金蝶维度'].startsWith('(') ? 'var(--amber)' : 'var(--ink)', wordBreak: 'break-all' }}>{c['金蝶维度']}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{c['金蝶账面'] != null ? yuan(c['金蝶账面']) : '—'}</td>
              <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)', color: c['净一致'] ? 'var(--green)' : 'var(--amber)' }}>{c['净一致'] ? '✓' : '待映射'}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: (c['余额差'] && Math.abs(c['余额差']) > 0.01) ? 'var(--amber)' : 'var(--ink-3)' }}>{c['余额差'] != null ? yuan(c['余额差']) : '—'}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </div>}
    </div>
  </div>)
}
