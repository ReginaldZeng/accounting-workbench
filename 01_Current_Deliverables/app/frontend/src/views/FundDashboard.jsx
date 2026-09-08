// [Change Log] Date:2026-09-08 Author:Claude/c Version:V2.517
// 余额调节表·全科目 差额改「调节后」：银行存款引智能表「更正后账面」→列出「未达调节」额+「调节后差额」，
// 能被未达账项(如内部往来未做账)解释的调平到0、真差异才亮红；渠道/理财/现金无逐笔则调节后=毛差。
// [Change Log] Date:2026-09-07 Author:Claude/c Version:V2.511
// 余额调节表·全科目：开户日期改由「账户台账」维护、本表只读引用（不再在本表手填）；账户名优先用金蝶核算维度友好户名
// （电商渠道在出纳台账账号为空时，不再显裸编码）。
// [Change Log] Date:2026-09-07 Author:Claude/c Version:V2.509
// 余额调节表·全科目：①待人工的户支持手填银行侧余额(存住·差额自动算·数据来源=人工录入)；②开户日期手填一次跨期记住；
// ③账户名显已销户标；④导出 Excel 单月扁表(科目/账户/币别/账户状态/开户日期/数据来源/余额/差额/备注)。
// [Change Log] Date:2026-09-07 Author:Claude/c Version:V2.506
// 余额调节表·全科目 接电商渠道银行侧：其他货币资金(支付宝等)银行侧余额接第三方渠道对账「渠道期末余额」，
// 银行流水余额格显小「渠道」来源标；差额自动算出。理财/结构性存款/现金仍待人工（后续接）。
// [Change Log] Date:2026-09-07 Author:Claude/c Version:V2.503
// 第三步加「余额调节表·全科目」tab（对标业务方《各银行余额》表）：四类科目(库存现金/银行存款/交易性金融资产/
// 其他货币资金)逐户 银行流水余额+汇率+综合本位币 | 金蝶系统余额(原币) | 差额 | 备注；默认折叠全零户；备注复用未达存储。
// 原「银行存款余额调节(智能未达)」移入「未达调节」tab，内容不变。
// [Change Log] Date:2026-07-06 Author:Claude/c Version:V2.34
// 余额调节表：①账户名完整不截断(+账户名称) ②加「币别」列(外币蓝字) ③加「未达原因」列——
// 有「认领/处理差异」权限的会计可填/改，显示填写人+时间，供领导核查(领导只读可见)。
// [Change Log] Date:2026-07-03 Author:Claude/c Version:V1.1 资金看板冷灰重构+缓存不清屏
import React, { useEffect, useState } from 'react'
import { getFund, syncFund, getBalanceAdjust, syncBalanceAdjust, getChannelAdjust, syncChannelAdjust, saveBalanceNote, getBalanceStatement, syncBalanceStatement, setStmtManualBalance, balanceStatementExportUrl, yuan } from '../api.js'
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

// 全科目余额调节表·一个科目分组（对标业务方《各银行余额》表；默认折叠全零户）
function StmtGroup({ g, canNote, editAcct, editText, setEditText, startEditStmt, saveNoteStmt, noteBusy, setEditAcct, saveStmtCell }) {
  const [showZero, setShowZero] = useState(false)
  const [ce, setCe] = useState(null)   // {acct, field:'bal'|'date'} 正在编辑的单元格
  const [cv, setCv] = useState('')
  const nz = g.accounts.filter(a => !a['全零'])
  const rows = showZero ? g.accounts : nz
  const zeroN = g.accounts.length - nz.length
  const rate = r => (r == null ? '—' : Number(r).toLocaleString('en-US', { minimumFractionDigits: r === 1 ? 0 : 4, maximumFractionDigits: 4 }))
  const startCell = (acct, field, cur) => { setCe({ acct, field }); setCv(cur == null ? '' : String(cur)) }
  const doCell = async () => { const ok = await saveStmtCell(ce.acct, ce.field, cv); if (ok) setCe(null) }
  const isCell = (acct, field) => ce && ce.acct === acct && ce.field === field
  const srcTag = { 渠道: '渠道', 手填: '手填' }
  return <div className="cat" style={{ marginTop: 4 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{g.科目}
        <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--ink-3)', fontWeight: 400 }}>{g.非零户数} 户
          {g.有差异户数 ? <span> · <span style={{ color: 'var(--amber)' }}>有差异 {g.有差异户数}</span></span> : <span style={{ color: 'var(--green)' }}> · 全部对平</span>}
        </span>
      </div>
      {zeroN > 0 && <span className="lk" style={{ fontSize: 12 }} onClick={() => setShowZero(v => !v)}>{showZero ? '收起全零户' : `展开 ${zeroN} 个全零户`}</span>}
    </div>
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', minWidth: 1000, fontSize: 12.5, borderCollapse: 'collapse' }}>
        <thead><tr>{['主体 / 账户名称', '币别', '银行流水余额', '汇率', '综合本位币', '金蝶系统余额', '未达调节', '调节后差额', '备注'].map((h, i) =>
          <th key={h} title={h === '未达调节' ? '银行存款：逐笔稽核推出的未达账项净额（金蝶应补记−待更正），如内部往来未做账。渠道/理财/现金无此项' : (h === '调节后差额' ? '＝银行−（金蝶+未达调节）。能被未达解释的调平到0；仍≠0的才是真要查的差异' : undefined)}
            style={{ textAlign: i === 0 || i === 8 ? 'left' : (i === 1 ? 'center' : 'right'), padding: '6px 8px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap', background: i === 2 ? 'var(--accent-soft,var(--accent-soft))' : (i === 7 ? 'var(--accent-soft,var(--accent-soft))' : undefined), cursor: (h === '未达调节' || h === '调节后差额') ? 'help' : 'default' }}>{h}{(h === '未达调节' || h === '调节后差额') ? <span style={{ color: 'var(--ink-3)', marginLeft: 2, fontSize: 11 }}>ⓘ</span> : null}</th>)}</tr></thead>
        <tbody>{rows.map((a, i) => {
          const cur = a['币别'] || ''
          const foreign = cur && cur !== '人民币'
          const editing = editAcct === a['账号']
          const unmatched = a['未达调节']
          const netDiff = a['调节后差额']
          const hasDiff = netDiff != null && Math.abs(netDiff) > 0.01
          return <tr key={i}>
            <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', minWidth: 190 }}>
              {a['账户名称'] || a['主体'] || '—'}
              {a['账户状态'] === '已销户' ? <span style={{ marginLeft: 5, fontSize: 10, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 3px' }}>已销户</span> : null}
              <div className="sub">{[a['主体'], a['开户行']].filter(Boolean).join(' · ')}</div>
              {a['开户日期'] ? <div className="sub" style={{ marginTop: 1 }}>开户日：{a['开户日期']}</div> : null}
            </td>
            <td style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--line)', color: foreign ? 'var(--blue)' : 'var(--ink-3)', fontWeight: foreign ? 600 : 400, whiteSpace: 'nowrap' }}>{cur || '—'}</td>
            <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', fontWeight: 600, background: 'var(--accent-soft,var(--accent-soft))' }}>
              {isCell(a['账号'], 'bal')
                ? <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                    <input autoFocus type="text" inputMode="decimal" value={cv} onChange={e => setCv(e.target.value)} placeholder="银行侧余额"
                      style={{ width: 90, fontSize: 12, padding: '2px 4px', borderRadius: 4, border: '1px solid var(--line-strong,#cfcdc4)', textAlign: 'right' }} />
                    <span className="lk" style={{ fontSize: 11 }} onClick={doCell}>存</span><span className="lk" style={{ fontSize: 11 }} onClick={() => setCe(null)}>×</span>
                  </span>
                : (a['银行侧缺']
                    ? (canNote ? <span className="lk" style={{ fontSize: 12 }} onClick={() => startCell(a['账号'], 'bal', '')}>+ 填余额</span> : <span style={{ color: 'var(--amber)', fontWeight: 500, fontSize: 11.5 }}>待人工</span>)
                    : <span>{yuan(a['银行流水余额'])}
                        {a['银行侧来源'] === '渠道' ? <span style={{ color: 'var(--ink-3)', fontSize: 10, marginLeft: 4, fontWeight: 400 }} title="来自第三方渠道对账（支付宝等）">渠道</span> : null}
                        {a['银行侧来源'] === '手填' ? <span style={{ color: 'var(--violet)', fontSize: 10, marginLeft: 4, fontWeight: 400 }}>手填{canNote ? <span className="lk" style={{ marginLeft: 2 }} onClick={() => startCell(a['账号'], 'bal', a['银行流水余额'])}>改</span> : null}</span> : null}
                      </span>)}
            </td>
            <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: 'var(--ink-3)' }}>{rate(a['汇率'])}</td>
            <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: foreign ? 'var(--blue)' : 'var(--ink-3)' }}>{a['综合本位币'] != null ? yuan(a['综合本位币']) : '—'}</td>
            <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{yuan(a['金蝶系统余额'])}</td>
            <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', color: (unmatched != null && Math.abs(unmatched) > 0.01) ? 'var(--violet)' : 'var(--ink-3)', whiteSpace: 'nowrap' }} title={a['差额'] != null ? '毛差(银行−金蝶未调节)=' + yuan(a['差额']) : undefined}>{unmatched == null ? '—' : (Math.abs(unmatched) > 0.01 ? yuan(unmatched) : '0')}</td>
            <td style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--line)', background: 'var(--accent-soft,var(--accent-soft))', color: a['银行侧缺'] ? 'var(--ink-3)' : (hasDiff ? 'var(--red)' : 'var(--green)'), fontWeight: hasDiff ? 700 : 500, whiteSpace: 'nowrap' }}>{a['银行侧缺'] ? '—' : (hasDiff ? yuan(netDiff) : '0 ✓')}</td>
            <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)', minWidth: 200 }}>
              {editing
                ? <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                    <textarea autoFocus value={editText} onChange={e => setEditText(e.target.value)} rows={2} placeholder="填差额说明，如：差额是计提了2025年12月利息"
                      style={{ flex: 1, minWidth: 130, fontSize: 12, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--line-strong,#cfcdc4)', fontFamily: 'inherit', resize: 'vertical' }} />
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <button className="btn" style={{ height: 24, padding: '0 8px', fontSize: 12 }} onClick={() => saveNoteStmt(a['账号'])} disabled={noteBusy}>{noteBusy ? '…' : '保存'}</button>
                      <span className="lk" style={{ fontSize: 11 }} onClick={() => setEditAcct(null)}>取消</span>
                    </div>
                  </div>
                : (a['备注']
                    ? <div><div style={{ fontSize: 12, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>{a['备注']}</div>
                        <div className="sub" style={{ marginTop: 2 }}>{a['备注人']}{a['备注时间'] ? ' · ' + a['备注时间'] : ''}{canNote ? <span className="lk" style={{ marginLeft: 8, fontSize: 11 }} onClick={() => startEditStmt(a)}>改</span> : null}</div></div>
                    : (hasDiff
                        ? (canNote ? <span className="lk" style={{ fontSize: 12 }} onClick={() => startEditStmt(a)}>+ 填说明</span> : <span style={{ color: 'var(--amber)', fontSize: 12 }}>待说明</span>)
                        : (canNote ? <span className="lk" style={{ fontSize: 11, color: 'var(--ink-3)' }} onClick={() => startEditStmt(a)}>+ 备注</span> : <span style={{ color: 'var(--ink-3)' }}>—</span>)))}
            </td>
          </tr>
        })}</tbody>
      </table>
    </div>
  </div>
}

export default function FundDashboard({ cfg, onPeriod, onNav, user }) {
  const canNote = !!(user && (user.role === 'admin' || (user.perms || {}).claim))   // 会计填未达原因=认领/处理差异权限
  const [d, setD] = useState(_cache)
  const [busy, setBusy] = useState(false), [stamp, setStamp] = useState('')
  const [ba, setBa] = useState(null), [baBusy, setBaBusy] = useState(false)
  const [bs, setBs] = useState(null), [bsBusy, setBsBusy] = useState(false)   // 全科目余额调节表
  const [tab, setTab] = useState('statement')                                 // statement=全科目调节表 / smart=未达调节
  const [ca, setCa] = useState(null), [caBusy, setCaBusy] = useState(false)
  const [editAcct, setEditAcct] = useState(null), [editText, setEditText] = useState(''), [noteBusy, setNoteBusy] = useState(false)
  const [baMain, setBaMain] = useState('all')   // 余额调节表·主体筛选
  useEffect(() => {
    getFund().then(x => { _cache = x; setD(x); setStamp((x.source === 'kingdee' ? '金蝶' : '样例') + (x.cached ? ' · 缓存(秒开)' : ' · 已刷新')) }).catch(() => {})
    getBalanceStatement().then(setBs).catch(() => {})
    getBalanceAdjust().then(setBa).catch(() => {})
    getChannelAdjust().then(setCa).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  const refreshBa = async () => { setBaBusy(true); try { setBa(await syncBalanceAdjust()) } finally { setBaBusy(false) } }
  const refreshBs = async () => { setBsBusy(true); try { setBs(await syncBalanceStatement()) } finally { setBsBusy(false) } }
  const startEdit = (a) => { setEditAcct(a['账号']); setEditText(a['未达原因'] || '') }
  const startEditStmt = (a) => { setEditAcct(a['账号']); setEditText(a['备注'] || '') }
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
  const saveNoteStmt = async (acct) => {   // 全科目调节表的备注，复用同一存储（后端两处缓存一起清）
    setNoteBusy(true)
    try {
      const r = await saveBalanceNote({ acct, note: editText })
      if (!r.ok) { alert(r.msg || '保存失败'); return }
      setBs(prev => prev && ({ ...prev, groups: prev.groups.map(g => ({ ...g, accounts: g.accounts.map(x => x['账号'] === acct
        ? { ...x, 备注: editText.trim(), 备注人: r.operator, 备注时间: r.ts } : x) })) }))
      setEditAcct(null)
    } catch (e) { alert(String(e.message || e)) } finally { setNoteBusy(false) }
  }
  // 手填银行侧余额（待人工的户用；后端存住、差额自动重算）→ 存完重取一次调节表最省心。
  // 开户日期不在此填——它是账户主数据，在「账户台账」维护，调节表只读引用。
  const saveStmtCell = async (acct, kind, value) => {
    setNoteBusy(true)
    try {
      const r = await setStmtManualBalance(acct, value)
      if (!r.ok) { alert(r.msg || '保存失败'); return false }
      setBs(await getBalanceStatement())   // 差额/数据来源随手填变，重取保证一致
      return true
    } catch (e) { alert(String(e.message || e)); return false } finally { setNoteBusy(false) }
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
      <div><div className="h-title">余额调节</div><div className="h-sub">四步工作流第 3 步 · 余额调节表（全四类科目：银行流水 vs 金蝶系统，逐户对差额）+ 未达调节（银行存款·逐笔稽核推未达）+ 第三方渠道勾稽（资金全景请看侧栏「资金看板」）</div></div>
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

      {/* 两个 tab：①余额调节表=全四类科目对照总表（对标业务方《各银行余额》表）②未达调节=智能·逐笔稽核推未达 */}
      <div className="fbar" style={{ marginTop: 6, marginBottom: 12, gap: 4 }}>
        <span className={'tabx' + (tab === 'statement' ? ' on' : '')} onClick={() => setTab('statement')}
          style={{ cursor: 'pointer', padding: '5px 12px', borderRadius: 7, fontSize: 12.5, fontWeight: tab === 'statement' ? 600 : 400, background: tab === 'statement' ? 'var(--accent-soft,var(--accent-soft))' : 'transparent', color: tab === 'statement' ? 'var(--accent)' : 'var(--ink-2)' }}>余额调节表 · 全科目</span>
        <span className={'tabx' + (tab === 'smart' ? ' on' : '')} onClick={() => setTab('smart')}
          style={{ cursor: 'pointer', padding: '5px 12px', borderRadius: 7, fontSize: 12.5, fontWeight: tab === 'smart' ? 600 : 400, background: tab === 'smart' ? 'var(--accent-soft,var(--accent-soft))' : 'transparent', color: tab === 'smart' ? 'var(--accent)' : 'var(--ink-2)' }}>未达调节 · 智能（银行存款）</span>
      </div>

      {/* Tab①：全四类科目余额调节表（对标《各银行余额》：银行流水余额+汇率+综合本位币 | 金蝶系统余额 | 差额 | 备注）*/}
      {tab === 'statement' && <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
          <div className="foot" style={{ flex: 1, minWidth: 260 }}>对标《各银行余额》调节表 · <b>全四类科目</b> · 单月（{d.period}）。均按<b>原币</b>；综合本位币＝原币×记账汇率。<b>差额已按未达账项调节</b>：能被未达解释的（如内部往来未做账）<b style={{ color: 'var(--green)' }}>调节后差额=0</b>、旁列未达调节额；仍<b style={{ color: 'var(--red)' }}>≠0 的才是真要查的差异</b>。未达调节仅银行存款有（走逐笔稽核）；渠道/理财/现金的调节后差额=毛差。银行存款取流水、电商渠道取渠道对账余额；理财/结构性存款/现金银行侧暂「待人工」。
            {bs && bs.groups && bs.groups.length > 0 && (bs.差异户数 ? <span> · <b style={{ color: 'var(--amber)' }}>有差异 {bs.差异户数} 户</b></span> : <span> · <b style={{ color: 'var(--green)' }}>全部对平</b></span>)}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {bs && bs.groups && bs.groups.length > 0 && <a className="btn" href={balanceStatementExportUrl()} style={{ textDecoration: 'none' }}>导出 Excel</a>}
            <button className="btn" onClick={refreshBs} disabled={bsBusy}>{bsBusy ? '刷新中…' : '刷新'}</button>
          </div>
        </div>
        {bs && bs['未取数'] && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>本期未取数：请先到「数据接入」点「从金蝶更新」。</div>}
        {bs && bs.error && <div className="banner err">金蝶取数失败：{bs.error}</div>}
        {bs && bs.groups && bs.groups.length > 0
          ? bs.groups.map(g => <StmtGroup key={g.科目} g={g} canNote={canNote}
              editAcct={editAcct} editText={editText} setEditText={setEditText}
              startEditStmt={startEditStmt} saveNoteStmt={saveNoteStmt} noteBusy={noteBusy} setEditAcct={setEditAcct}
              saveStmtCell={saveStmtCell} />)
          : (bs && !bs['未取数'] && !bs.error ? <div className="foot">（本期无科目余额数据）</div> : null)}
      </div>}

      {tab === 'smart' && <div>
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
      </div>}
    </div>
  </div>)
}
