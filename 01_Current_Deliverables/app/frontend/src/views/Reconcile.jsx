// [Change Log] Date:2026-07-06 Author:Claude/c Version:V2.32
// 币别区分：加「币别」列(外币蓝字)；外币户金额=原币不带¥号、行下挂本位币小字；详情面板列 原币/本位币/汇率；
// 新状态「汇兑损益·账面调整」(fx_adjust,期末重估无银行流水)入切片器与状态标签；页脚补外币口径说明。
// [Change Log] Date:2026-07-06 Author:Claude/c Version:V2.31
// 切片器计数随筛选条联动：底数集=除状态外全部筛选(主体/开户行/账号/搜索/认领)命中，切片器按底数集计数，
// 有筛选且数字被收窄时显示 当前/全量 小分母；表格=底数集∩状态切片（口径与切片器一致）。
// [Change Log] Date:2026-07-03 Author:Claude/c Version:V1.1
// 逐笔稽核屏 v2：四步链路条 + 可信度带 + 七态 KPI/筛选 + 置信度 + 行内配对下钻 + 组合候选。
import React, { useEffect, useState } from 'react'
import { getReconcile, refreshKingdee, claimReconcile, yuan, yuan4 } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'
import Steps from '../components/Steps.jsx'

let _cache = null   // 跨视图切换保留，切回不清屏

// status -> [中文, 标签类, 是否优先(红底), 是否可下钻]
const ST = {
  bank_leak:    ['疑似漏账', 'leak', true, false],
  xfer_unbooked:['内部往来·未做账', 'late', false, true],   // V2.196：可点开——收支两腿对照，未做账腿高亮
  amount_wrong: ['做错·金额', 'werr', false, true],
  late_month:   ['晚记·本月', 'late', false, true],
  late_cross:   ['跨期晚记', 'late', false, true],
  misbook:      ['疑记错户·账在他户', 'werr', false, true],   // V2.194：可点开详情——钱在哪里/账在哪里两栏对照
  combo_pending:['组合待确认', 'late', false, true],    // V2.195：可点开详情——逐张列 日期/金额/凭证/制单人/摘要
  kd_only:      ['金蝶单边·疑似做错', 'kd', false, false],
  kd_xfer:      ['内部划转·对应他账户', 'late', false, true],  // V2.196：可点开——收支两腿对照
  fx_adjust:    ['汇兑损益·账面调整', 'unmap', false, false],
  no_bank_acct: ['账户缺银行流水', 'unmap', false, false],
  no_kd_acct:   ['账户缺金蝶数据', 'unmap', false, false],
  unmapped:     ['账号对不上台账', 'unmap', false, false],
  matched:      ['已匹配', 'ok', false, true],
}
const CONF = { '高': 'hi', '中': 'mid', '低': 'lo' }

// V2.172 内部往来两腿列示（需求方定：要看清"做了哪边、哪边没做、还是两边都没做"）：
// ①真划转=收方户/支方户各标 已做账/未做账；②同向=钱走哪个户/账记哪个户（疑账号维度记错）；
// ③两腿均未做（两户银行都动、金蝶两边查无）；④金蝶对开（两边账都做了、都缺银行流水）。
// 旧定格结果无「内部往来明细」时退回原一行文字。
function XferLegs({ r }) {
  const xi = r['内部往来明细']
  if (!xi) {
    return r['内部往来对应']
      ? <div className="sub" style={{ color: 'var(--violet)' }}>内部往来 → {r['内部往来对应']}</div> : null
  }
  const st = { color: 'var(--violet)' }
  const tail = a => (a ? '…' + String(a).slice(-6) : '')
  const oppName = (xi['对方主体'] || xi['对方开户行'] || '对方户') + tail(xi['对方账号'])
  const selfName = '本户' + tail(r['账号'])
  const isKd = r.status === 'kd_xfer'
  const byDir = legs => legs.sort((a, b) => (a.dir === '收' ? -1 : 1) - (b.dir === '收' ? -1 : 1))
  if (xi['两腿均未做']) {
    const legs = byDir([{ dir: r['方向'], name: selfName }, { dir: xi['对方方向'], name: oppName }])
    return <>
      <div className="sub" style={st}>收：{legs[0].name}（未做账）　支：{legs[1].name}（未做账）</div>
      <div className="sub" style={{ ...st, fontWeight: 600 }}>⚠ 两边账都没做——两户银行均已动，金蝶两边查无</div>
    </>
  }
  if (isKd && !xi['对方是银行流水']) {
    return <div className="sub" style={st}>两边账都做了（本户 {r['金蝶凭证'] || '—'} ↔ {oppName} {xi['对方凭证'] || '—'} 对开）· 两边都缺银行流水</div>
  }
  if (xi['同向']) {
    const money = isKd ? `${oppName}（银行已动·该户未做账）` : `${selfName}（银行已动·本户未做账）`
    const book = isKd ? `${selfName}（已做账 ${r['金蝶凭证'] || '—'}）` : `${oppName}（已做账 ${xi['对方凭证'] || '—'}）`
    return <>
      <div className="sub" style={st}>钱走：{money}</div>
      <div className="sub" style={st}>账记：{book} · 疑账号维度记错</div>
    </>
  }
  const legs = byDir([
    { dir: r['方向'], name: selfName, note: isKd ? `已做账 ${r['金蝶凭证'] || '—'}` : '未做账' },
    { dir: xi['对方方向'], name: oppName,
      note: xi['对方是银行流水'] ? '未做账（银行已动）' : `已做账 ${xi['对方凭证'] || '—'}` }])
  return <>
    <div className="sub" style={st}>收：{legs[0].name}（{legs[0].note}）</div>
    <div className="sub" style={st}>支：{legs[1].name}（{legs[1].note}）</div>
  </>
}
const CNT = { bank_leak: '疑似漏账', xfer_unbooked: '内部往来·未做账', amount_wrong: '做错·金额',
  misbook: '疑记错户·账在他户',
  late_month: '晚记·本月', late_cross: '跨期晚记', combo_pending: '组合待确认',
  kd_only: '金蝶单边·疑似做错', kd_xfer: '内部划转·对应他账户',
  fx_adjust: '汇兑损益·账面调整', no_bank_acct: '账户缺银行流水',
  no_kd_acct: '账户缺金蝶数据', unmapped: '账号对不上台账', matched: '已匹配' }
const CHIPS = [['all', '全部'], ['bank_leak', '疑似漏账'], ['amount_wrong', '做错·金额'],
  ['late_month', '晚记·本月'], ['late_cross', '跨期晚记'], ['kd_only', '金蝶单边·疑似做错'],
  ['unmapped', '账号对不上台账'], ['matched', '已匹配']]
// 切片器（点击即筛选、与表格联动、选中高亮）。V2.33 合并同类态：三对相近状态各并成一格，
// 切片器变少但表格行仍显示细分状态标签（点进去看得到）。第5位=该格涵盖的后端状态集（all=null）。
//   晚记 = 本月 + 跨期；内部往来 = 未做账 + 对应他账户；账户缺数据 = 缺银行流水 + 缺金蝶数据。
//   汇兑·账面调整 单列（无可配对同类，外币期末重估特有）。
const KPI = [
  ['all', '全部', 'var(--ink-3)', false, null],
  ['bank_leak', '疑似漏账', 'var(--red)', true, ['bank_leak']],
  ['misbook', '疑记错户', 'var(--red)', true, ['misbook']],
  ['amount_wrong', '做错·金额', 'var(--red)', true, ['amount_wrong']],
  ['kd_only', '金蝶单边·疑似做错', 'var(--red)', true, ['kd_only']],
  ['acct_missing', '账户缺数据', 'var(--red)', true, ['no_bank_acct', 'no_kd_acct']],
  ['internal', '内部往来', 'var(--red)', true, ['xfer_unbooked', 'kd_xfer']],
  ['combo', '组合待确认', 'var(--violet)', false, ['combo_pending']],
  ['late', '晚记', 'var(--violet)', false, ['late_month', 'late_cross']],
  ['fx_adjust', '汇兑·账面调整', 'var(--gray)', false, ['fx_adjust']],
  ['unmapped', '账号对不上', 'var(--gray)', false, ['unmapped']],
  ['matched', '已匹配', 'var(--green)', false, ['matched']],
]
// V2.202 切片白话说明：选中哪个切片，就在切片器下方用该切片颜色显示"这是什么意思"（需求方定）
const SLICE_DESC = {
  bank_leak: '疑似漏账＝银行有这笔钱进出，金蝶整个取数窗口查无对应凭证——真·没人做账，需核实后补记。',
  misbook: '疑记错户＝钱和账都在，但凭证挂在了另一个户上（同额·同向·同期，且对方户名命中凭证摘要）——不用重做账，把凭证的银行账号维度改到实走账户即可。',
  amount_wrong: '做错·金额＝银行和金蝶配对上了，但金额不相等（比对到4位小数）——按差额更正凭证。',
  kd_only: '金蝶单边＝金蝶记了账，银行整月查无这笔钱——查是否重复做账/凭证做错/提前做账，或银行流水没导全。',
  acct_missing: '账户缺数据＝整个账户只有一侧有数据——缺金蝶：该户整月没做账（批量漏账或口径待定）；缺流水：出纳漏导该户对账单。补齐后点「刷新金蝶数据」重核。',
  internal: '内部往来＝集团内部划转的腿——点开看哪边收、哪边支、哪条腿没做账（高亮），各户补各自的腿。',
  combo: '组合待确认＝一笔=多笔且合计分毫不差（合并缴税/理财本息拆张等）——两边都已做账，点开核对每张凭证，确认后认领留痕，不算错漏账。',
  late: '晚记＝配对上了，但金蝶比银行晚≥1天入账——本月内为轻，跨会计月为重（会扭曲期末数）。',
  fx_adjust: '汇兑·账面调整＝外币户期末汇率重估（原币0、只动本位币）——纯账面调整，本来就没有银行流水，不参与配对。',
  unmapped: '账号对不上＝流水/凭证上的账号在账户台账里找不到是谁（掩码/短号等）——先到账户台账补登记。',
  matched: '已匹配＝同账户、同方向、金额(4位)相等、金蝶当天入账——无异常。',
}
const SLICE_ST = {}   // 切片器 key -> 涵盖的后端状态集，供筛选/计数展开
KPI.forEach(k => { if (k[4]) SLICE_ST[k[0]] = k[4] })
const sumSt = (cnt, sts) => (sts || []).reduce((a, s) => a + (cnt[s] || 0), 0)
// 认领情况筛选：与银行筛选一样是「附加条件」，和上方状态切片器 AND 叠加。matched 无认领流程，认领筛选一律不含 matched。
const CLAIM_F = [['all', '全部认领情况'], ['unclaimed', '未认领'], ['claimed', '已认领'], ['claimed_undone', '已认未调整']]
function matchClaim(r, cf) {
  if (cf === 'all') return true
  if (r.status === 'matched') return false        // 已匹配不进认领口径
  const st = r['认领状态'] || '待认领'
  if (cf === 'unclaimed') return st === '待认领'
  if (cf === 'claimed') return st !== '待认领'      // 已认领/已调整/识别有误 都算"已认领"
  if (cf === 'claimed_undone') return st === '已认领' // 认了但还没标"已调整"——待办积压
  return true
}

function Conf({ v }) {
  if (!v) return <span className="muted">—</span>
  return <span className={'conf ' + CONF[v]}><span className="bars"><i /><i /><i /></span><span className="cl">{v}</span></span>
}

// 认领工作流：待认领→XXX已认领→XXX已调整 / 识别有误
function Claim({ r, doClaim }) {
  const st = r['认领状态'] || '待认领'
  const who = r['认领人'] || ''
  if (st === '待认领') return <span className="lk" onClick={e => doClaim(e, r, '认领')}>认领</span>
  const txt = st === '已认领' ? `${who}·已认领` : (st === '已调整' ? `${who}·已调整` : `识别有误${who ? '·' + who : ''}`)
  const col = st === '已调整' ? 'var(--green)' : (st === '识别有误' ? 'var(--red)' : 'var(--blue)')
  return <div>
    <div style={{ fontSize: 11.5, color: col, marginBottom: 2 }} title={r['认领时间'] + (r['认领备注'] ? ' · ' + r['认领备注'] : '')}>{txt}</div>
    <div className="acts">
      {st === '已认领' && <><span className="lk" onClick={e => doClaim(e, r, '已调整')}>标已调整</span><span className="lk q" onClick={e => doClaim(e, r, '识别有误')}>识别有误</span></>}
      <span className="lk" onClick={e => doClaim(e, r, '撤销')}>撤销</span>
    </div>
  </div>
}

const PAGE_SIZE = 15   // 一页 15 笔

export default function Reconcile({ cfg, onPeriod, onNav, user }) {
  const [d, setD] = useState(_cache), [f, setF] = useState('all'), [exp, setExp] = useState(null), [busy, setBusy] = useState(false)
  const [page, setPage] = useState(1)
  const [mainF, setMainF] = useState('all'), [bankF, setBankF] = useState('all'), [acctF, setAcctF] = useState('all'), [claimF, setClaimF] = useState('all')
  const [kw, setKw] = useState('')   // 模糊搜索：户名/账号片段/对方/摘要 任一包含即命中
  const [amtF, setAmtF] = useState('')   // V2.198 金额精确框：3000 只命中 3,000.00（模糊会把33,000/13,000.50全炸出来）
  useEffect(() => { setPage(1); setExp(null) }, [f, mainF, bankF, acctF, claimF, kw, amtF])   // 任一筛选变化都回到第 1 页
  useEffect(() => {
    const load = () => getReconcile().then(x => { _cache = x; setD(x) }).catch(() => {})
    load()
    const timer = setInterval(load, 5000)   // 认领是多人协作，5秒自动刷新，别人认领/调整即时可见
    return () => clearInterval(timer)
  }, [cfg.source, cfg.year, cfg.period, cfg.bank_import_dir])
  // V2.177：此前这里调 /api/reconcile/sync——只把已定格的旧数据重算一遍，从没真去金蝶取数（需求方发现"刷新没反映"）。
  // 改接唯一取数总闸 /api/kingdee/refresh（真取+重新定格），再重拉稽核结果；失败/无权限弹明白话，不再静默。
  const refresh = async () => {
    setBusy(true)
    try {
      const r = await refreshKingdee()
      if (r && r.ok === false) { window.alert(r.msg || '刷新失败'); return }
      const x = await getReconcile(); _cache = x; setD(x)
    } catch (e) {
      window.alert(String(e).includes('403') ? '无「从金蝶更新」权限，请联系管理员' : '刷新失败：' + (e && e.message ? e.message : e))
    } finally { setBusy(false) }
  }
  const doClaim = async (e, r, action) => {
    e.stopPropagation()
    let note = ''
    if (action === '识别有误') { note = window.prompt('识别有误 · 说明哪里判错（给 IT 修 BUG）：', ''); if (note === null) return }
    await claimReconcile({ key: r.key, action, 备注: note })    // 操作人=登录用户(服务端认)
    const x = await getReconcile(); _cache = x; setD(x)
  }
  if (!d) return <div className="loading">加载中…</div>
  const s = d.summary, g = d.guardrail
  // 全局稳定序号：后端已按「账号分组·组内银行日期升序」定序，此处顺序编号；不随筛选/翻页改变，便于"看第几号那条"沟通
  d.results.forEach((r, i) => { r._seq = i + 1 })
  // 筛选下拉：主体 → 开户行 → 账号 逐层联动收窄（选了上层，下层只列其名下的项），账号带户名便于辨认
  const mains = [...new Set(d.results.map(r => r['主体']).filter(Boolean))].sort()
  const banks = [...new Set(d.results.filter(r => mainF === 'all' || r['主体'] === mainF).map(r => r['开户行']).filter(Boolean))].sort()
  const acctMap = {}
  d.results.forEach(r => { const a = r['账号']; if (a && (mainF === 'all' || r['主体'] === mainF) && (bankF === 'all' || r['开户行'] === bankF) && !(a in acctMap)) acctMap[a] = r['主体'] || r['户名'] || '' })
  const accts = Object.keys(acctMap).sort()
  const q = kw.trim().toLowerCase()
  // V2.198 需求方定：搜索分两档——关键字框=模糊（户名/账号片段如0388/对方/摘要），金额框=精确。
  const hitKw = r => (String(r['收(付)方名称'] || '') + ' ' + String(r['摘要'] || '') + ' '
    + String(r['账号'] || '') + ' ' + String(r['主体'] || '') + ' ' + String(r['户名'] || '')
    + ' ' + String(r['开户行'] || '') + ' ' + String(r['制单人'] || '')).toLowerCase().includes(q)
  // 金额精确：去千分位/¥/空格后按数值等值比（±半分容差吸收显示位数），比 借方/贷方/金蝶金额 三处
  const qa = amtF.trim().replace(/[,，¥￥\s]/g, '')
  const qaNum = /^\d+(\.\d+)?$/.test(qa) ? parseFloat(qa) : null
  const hitAmt = r => qaNum == null ? true
    : [r['借方金额'], r['贷方金额'], r['金蝶金额']].some(v =>
        v != null && Math.abs(Math.abs(v) - qaNum) < 0.005)
  // 切片器随筛选条联动：底数集 = 除状态切片外的全部筛选(主体/开户行/账号/搜索/认领)命中——
  // 切片器计数都按底数集算（筛选一动数字跟着变），表格再在底数集上叠加状态切片。
  const base = d.results.filter(r =>
    (mainF === 'all' || r['主体'] === mainF) &&
    (bankF === 'all' || r['开户行'] === bankF) &&
    (acctF === 'all' || r['账号'] === acctF) &&
    (!q || hitKw(r)) &&
    (qaNum == null || hitAmt(r)) &&
    matchClaim(r, claimF))
  const fullCnt = {}, sliceCnt = {}      // 按后端细分状态计数：全量 / 底数集
  d.results.forEach(r => { fullCnt[r.status] = (fullCnt[r.status] || 0) + 1 })
  base.forEach(r => { sliceCnt[r.status] = (sliceCnt[r.status] || 0) + 1 })
  const rows = base.filter(r => f === 'all' || (SLICE_ST[f] || []).includes(r.status))
  const filtered = mainF !== 'all' || bankF !== 'all' || acctF !== 'all' || claimF !== 'all' || q !== '' || amtF.trim() !== ''
  const resetFilters = () => { setMainF('all'); setBankF('all'); setAcctF('all'); setClaimF('all'); setKw(''); setAmtF('') }
  const selMain = e => { setMainF(e.target.value); setBankF('all'); setAcctF('all') }   // 换主体时清开户行+账号
  const selBank = e => { setBankF(e.target.value); setAcctF('all') }   // 换开户行时清账号，避免账号不属于该行
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const cur = Math.min(page, pages)
  const pageRows = rows.slice((cur - 1) * PAGE_SIZE, cur * PAGE_SIZE)
  const goPage = p => { setPage(Math.min(Math.max(1, p), pages)); setExp(null) }
  const countOk = g['银行笔数核对一致'] && g['金蝶笔数核对一致']
  const kdOnly = s['金蝶单边·疑似做错'] || 0
  const allPass = countOk && kdOnly === 0

  return (<div>
    <div className="head">
      <div><div className="h-title">银行–金蝶稽核</div>
        <div className="h-sub">银行流水 × 金蝶银行存款(1002)序时账 · 找 漏账 / 做错 / 晚记，账号对不上台账不猜</div></div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <PeriodPicker year={cfg && cfg.year} period={cfg && cfg.period} onChange={onPeriod} status={cfg && cfg['数据状态']} />
          <button className="btn" onClick={refresh} disabled={busy} title="重新去金蝶取最新序时账数据（金蝶那边补/改/删了凭证，点这里就更新过来）">{busy ? '刷新中…' : '刷新金蝶数据'}</button>
          <button className="btn btn-pri" onClick={() => window.open('/api/export/report', '_blank')}>导出对账底稿</button>
        </div>
        {/* V2.176 需求方定：刷新要有回执——谁于何时刷的，点完这行小字立即变化即为生效 */}
        {d['金蝶取数']?.at ? <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          金蝶数据：{d['金蝶取数'].by || '—'} 于 {d['金蝶取数'].at} 刷新</div> : null}
      </div>
    </div>
    <div className="body">
      <Steps current="reconcile" onNav={onNav} sub={{
        import: `银行 ${g['银行笔数']} 笔 · 金蝶 ${g['金蝶笔数']} 笔`,
        reconcile: '进行中 · 本屏' }} />

      <div className="trust">
        <div className="lead">可信度勾稽</div>
        <div className="checks">
          <span className={'chk ' + (countOk ? 'pass' : 'warn')}>{countOk ? '✓' : '⚠'} 笔数勾稽 · 银行 <b>{g['银行笔数']}</b> / 金蝶 <b>{g['金蝶笔数']}</b> 各归一类</span>
          <span className={'chk ' + (kdOnly === 0 ? 'pass' : 'warn')}>{kdOnly === 0 ? '✓' : '⚠'} 金蝶单边 <b>{kdOnly}</b> 笔{kdOnly > 0 ? '（疑似金蝶做错·待核，或银行流水未导全）' : ''}</span>
        </div>
        <span className={'verdict ' + (allPass ? 'ok' : 'warn')}>{allPass ? '可信度 · 通过' : '可信度 · 待复核'}</span>
      </div>

      {d.error && <div className="banner err">金蝶取数失败：{d.error}</div>}
      {d['未取数'] && <div className="banner" style={{ background: 'var(--amber-bg)', color: 'var(--amber)', borderColor: 'var(--amber-line)' }}>
        本期未取数：请先到<b>「数据接入」</b>上传银行流水、点<b>「从金蝶更新」</b>，把本月数据备齐。
        {onNav && <a onClick={() => onNav('import')} style={{ marginLeft: 8, color: 'var(--accent)', cursor: 'pointer', fontWeight: 600 }}>去数据接入 ›</a>}
      </div>}

      {/* 切片器（V2.33 合并同类态）：点击即筛选，计数随筛选条联动；合并格计数=所辖状态之和，表格行仍显示细分状态 */}
      <div className="kpis">
        {KPI.map(([key, label, color, prio, sts]) => {
          const n = key === 'all' ? base.length : sumSt(sliceCnt, sts)
          const total = key === 'all' ? d.results.length : sumSt(fullCnt, sts)
          if (key === 'unmapped' && total === 0) return null   // V2.196：账号对不上=安全网，平时藏起、非0才显
          const merged = sts && sts.length > 1
          return <div key={key} className={'kpi slice' + (prio && total > 0 ? ' prio' : '') + (f === key ? ' on' : '')} onClick={() => setF(key)} role="button" tabIndex={0}
            title={(filtered ? `当前筛选下 ${n} 笔 · 全量 ${total} 笔` : '') + (merged ? (filtered ? ' · ' : '') + '合并态，明细状态见表格' : '') || undefined}>
            <div className="kl"><span className="dot" style={{ background: color }} />{label}{merged ? <span style={{ color: 'var(--ink-3)', fontWeight: 400 }}> ⧉</span> : null}</div>
            <div className="kv">{n}{filtered && n !== total ? <span className="kt">/{total}</span> : null}</div></div>
        })}
      </div>
      {/* 银行 + 认领情况筛选：与上方状态切片器 AND 叠加，缩小到具体账户 / 认领进度 */}
      <div className="fbar">
        <span className="fl">筛选</span>
        <label>主体
          <select value={mainF} onChange={selMain}>
            <option value="all">全部主体</option>
            {mains.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label>开户行
          <select value={bankF} onChange={selBank}>
            <option value="all">全部开户行</option>
            {banks.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label>账号
          <select value={acctF} onChange={e => setAcctF(e.target.value)}>
            <option value="all">全部账号</option>
            {accts.map(a => <option key={a} value={a}>{a}{acctMap[a] ? ' · ' + acctMap[a] : ''}</option>)}
          </select>
        </label>
        <label>搜索
          <input type="text" value={kw} onChange={e => setKw(e.target.value)} placeholder="户名 / 账号片段 / 制单人 / 对方 / 摘要（模糊）" />
        </label>
        <label>金额
          <input type="text" value={amtF} onChange={e => setAmtF(e.target.value)} placeholder="精确 如 3000" style={{ width: 110 }} />
        </label>
        <label>认领情况
          <select value={claimF} onChange={e => setClaimF(e.target.value)}>
            {CLAIM_F.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {filtered && <span className="lk" onClick={resetFilters}>清除筛选</span>}
      </div>

      {/* 表格上沿：左=筛选提示，右=分页导航（悬在「操作」列上方） */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginTop: -6 }}>
        <div className="foot">点上方切片器筛选 · 当前：<b style={{ color: 'var(--ink)' }}>{KPI.find(k => k[0] === f)[1]}</b>（{rows.length} 条）
          {f !== 'all' && SLICE_DESC[f] ? <span style={{ color: (KPI.find(k => k[0] === f) || [])[2] || 'var(--ink-2)', marginLeft: 8, fontWeight: 600 }}>{SLICE_DESC[f]}</span> : null}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="foot">共 {rows.length} 笔 · 每页 {PAGE_SIZE} 笔 · 第 <b style={{ color: 'var(--ink)' }}>{cur}</b> / {pages} 页</span>
          <button className="btn" style={{ height: 26, padding: '0 9px', fontSize: 12 }} onClick={() => goPage(cur - 1)} disabled={cur <= 1}>‹ 上一页</button>
          <button className="btn" style={{ height: 26, padding: '0 9px', fontSize: 12 }} onClick={() => goPage(cur + 1)} disabled={cur >= pages}>下一页 ›</button>
        </div>
      </div>


      <div className="tbl-wrap"><table style={{ minWidth: 1080 }}>
        <thead><tr>{['序号', '状态', '置信度', '日期', '开户行', '账号 / 户名', '币别', '收(付)方', '借方(收)', '贷方(支)', '摘要', '金蝶凭证', '操作'].map((h, i) =>
          <th className="th" key={h} style={(i === 8 || i === 9) ? { textAlign: 'right' } : null}>{h}</th>)}</tr></thead>
        <tbody>{pageRows.map((r, i) => {
          const [cn, cls, prio, canExp] = ST[r.status] || ['—', '', false, false]
          const open = exp === i
          const cur = r['币别'] || ''
          const foreign = cur && cur !== '人民币'
          // 外币户金额=原币，不带 ¥ 号（¥ 只给人民币/未知币别）；外币行下挂本位币小字（金蝶界面显示的数）；
          // 汇兑重估行原币=0，表内金额是本位币口径，挂灰字说明
          const amt = v => foreign ? Number(v).toLocaleString('en-US', { maximumFractionDigits: 4 }) : yuan(v)
          const baseSub = r['本位币金额'] != null ? <div className="sub">本位币 {yuan4(r['本位币金额'])}</div>
            : (r.status === 'fx_adjust' ? <div className="sub">本位币口径（原币 0）</div> : null)
          return <React.Fragment key={i}>
            <tr className={'row' + (prio ? ' prio' : '')} onClick={() => canExp && setExp(open ? null : i)}>
              <td className="mono" style={{ color: 'var(--ink-3)' }}>{r._seq}</td>
              <td><span className={'tag ' + cls}>{cn}</span>{r['组合候选'] && <span className="combo-tag">组合候选</span>}</td>
              <td><Conf v={r['置信度']} /></td>
              <td>{r['日期'] || '—'}{r['晚记'] ? <div className="sub" style={{ color: 'var(--violet)' }}>{r['晚记']}</div> : null}</td>
              <td>{r['开户行'] || '—'}</td>
              <td><div className="acct">{r['账号'] || '—'}</div><div className="sub">{r['主体'] || r['户名'] || ''}</div></td>
              <td>{cur ? <span style={foreign ? { color: 'var(--blue)', fontWeight: 600 } : null}>{cur}</span> : '—'}</td>
              {/* V2.170：此格自 V2.7 起一直缺失，表头 13 列行只有 12 格，「收(付)方」起整体左移一列 */}
              <td className="muted">{r['收(付)方名称'] || '—'}</td>
              <td className="num" style={prio ? { color: 'var(--red)' } : null}>{r['借方金额'] != null ? amt(r['借方金额']) : '—'}{r['借方金额'] != null ? baseSub : null}</td>
              <td className="num" style={prio ? { color: 'var(--red)' } : null}>{r['贷方金额'] != null ? amt(r['贷方金额']) : '—'}{r['贷方金额'] != null ? baseSub : null}</td>
              <td className="muted">{r['摘要'] || '—'}{(() => {
                // V2.179：组合行显示整组等式（目标=成员1＋成员2…，凭证号都在），人在本屏直接判；旧数据退回一行标签
                const ci = r['组合明细']
                if (ci) {
                  const leg = m => `${m['凭证'] || (m['日期'] || '').slice(5)} ${yuan4(m['金额'])}`
                  return <div className="sub" style={{ color: 'var(--violet)' }}>
                    组合：{ci['目标侧']} {(ci['目标']['日期'] || '').slice(5)} {yuan4(ci['目标']['金额'])}（{ci['目标']['凭证'] || ci['目标']['摘要'] || '—'}）＝ {ci['成员'].map(leg).join(' ＋ ')} · 合计分毫不差
                  </div>
                }
                return r['组合候选说明'] ? <div className="sub" style={{ color: 'var(--violet)' }}>{r['组合候选说明']}</div> : null
              })()}{r['记错户对应'] ? <div className="sub" style={{ color: 'var(--amber)' }}>{r['记错户对应']} · 核实后请更正凭证的账号维度</div> : null}<XferLegs r={r} /></td>
              <td className="muted">{r['金蝶凭证'] || '—'}{r['差额'] != null ? <span style={{ color: 'var(--amber)', fontWeight: 600 }}> 差 {yuan4(r['差额'])}</span> : null}{r['制单人'] ? <div className="sub">制单 {r['制单人']}</div> : null}</td>
              <td>{r.status === 'matched'
                ? (canExp ? <span className="lk">{open ? '收起 ▴' : '看详情 ▾'}</span> : <span className="muted">—</span>)
                : <span onClick={e => e.stopPropagation()}><Claim r={r} doClaim={doClaim} /></span>}</td>
            </tr>
            {/* V2.194 疑记错户详情（需求方定：点开要看清"钱在哪里、账在哪里"）——两栏对照+更正指引 */}
            {open && canExp && r.status === 'misbook' && r['记错户明细'] && (() => {
              const mi = r['记错户明细'], mo = mi['钱在'], bk = mi['账在']
              const nm = x => ((x['开户行'] || '') + ' ' + (x['账号'] || '')).trim()
              return <tr className="exp"><td colSpan="13">
                <div className="detail">
                  <div className="pane"><div className="ph">💰 钱在哪里 · 银行流水实走</div>
                    <div className="kv-row"><span className="kk">账户</span><span className="vv">{nm(mo)}{mo['主体'] ? `（${mo['主体']}）` : ''}</span></div>
                    <div className="kv-row"><span className="kk">流水</span><span className="vv">{mo['日期']} {mo['方向']} {yuan4(mo['金额'])}</span></div>
                    <div className="kv-row"><span className="kk">对方户名</span><span className="vv">{mo['对方'] || '—'}</span></div>
                    <div className="kv-row"><span className="kk">摘要</span><span className="vv">{mo['摘要'] || '—'}</span></div>
                  </div>
                  <div className="pane"><div className="ph">📒 账在哪里 · 金蝶凭证挂的户</div>
                    <div className="kv-row"><span className="kk">账户</span><span className="vv">{nm(bk)}{bk['主体'] ? `（${bk['主体']}）` : ''}</span></div>
                    <div className="kv-row"><span className="kk">凭证</span><span className="vv">{bk['凭证'] || '—'}{bk['制单人'] ? `（制单 ${bk['制单人']}）` : ''}</span></div>
                    <div className="kv-row"><span className="kk">记账</span><span className="vv">{bk['日期']} {bk['方向']} {yuan4(bk['金额'])}</span></div>
                    <div className="kv-row"><span className="kk">摘要</span><span className="vv">{bk['摘要'] || '—'}</span></div>
                  </div>
                  <div className="pane side">
                    <div className="diffbox">
                      <div>💰 钱从 <b>…{String(mo['账号'] || '').slice(-6)}</b> 走</div>
                      <div>📒 账记在 <b>…{String(bk['账号'] || '').slice(-6)}</b>（{bk['凭证'] || '—'}）</div>
                      <div style={{ marginTop: 6, fontWeight: 400 }}>判定依据：同额 · 同向 · 同期，且对方户名命中凭证摘要</div>
                      <div style={{ marginTop: 6 }}>处理：把凭证的<b>银行账号维度</b>更正到实走账户，改完点「刷新金蝶数据」复核</div>
                    </div>
                  </div>
                </div>
              </td></tr>
            })()}
            {/* V2.195 组合待确认详情（需求方定：933,550.93 分别坐在哪天/金额/凭证/制单人/摘要，逐张列清） */}
            {open && canExp && r.status === 'combo_pending' && r['组合明细'] && (() => {
              const ci = r['组合明细'], tgt = ci['目标']
              return <tr className="exp"><td colSpan="13">
                <div className="detail">
                  <div className="pane"><div className="ph">{ci['目标侧'] === '银行' ? '💰 银行 · 合并的一笔' : '📒 金蝶 · 合并的一张'}</div>
                    <div className="kv-row"><span className="kk">日期</span><span className="vv">{tgt['日期'] || '—'}</span></div>
                    <div className="kv-row"><span className="kk">金额</span><span className="vv">{yuan4(tgt['金额'])}</span></div>
                    {tgt['凭证'] ? <div className="kv-row"><span className="kk">凭证</span><span className="vv">{tgt['凭证']}{tgt['制单人'] ? `（制单 ${tgt['制单人']}）` : ''}</span></div> : null}
                    <div className="kv-row"><span className="kk">摘要</span><span className="vv">{tgt['摘要'] || '—'}</span></div>
                  </div>
                  <div className="pane"><div className="ph">{ci['成员侧'] === '金蝶' ? `📒 金蝶 · 拆成的 ${ci['成员'].length} 张` : `💰 银行 · 拆成的 ${ci['成员'].length} 笔`}</div>
                    {ci['成员'].map((m, j) => <div className="kv-row" key={j}>
                      <span className="kk">{m['凭证'] || (m['日期'] || '').slice(5) || `第${j + 1}笔`}</span>
                      <span className="vv">{m['日期']} · {yuan4(m['金额'])}{m['制单人'] ? ` · 制单 ${m['制单人']}` : ''}{m['摘要'] ? ` · ${m['摘要']}` : ''}</span>
                    </div>)}
                  </div>
                  <div className="pane side">
                    <div className="diffbox" style={{ color: 'var(--green)', background: 'var(--green-bg)', borderColor: 'var(--green-line)' }}>
                      {ci['成员'].map(m => yuan4(m['金额'])).join(' ＋ ')} ＝ {yuan4(ci['合计'])} · 与{ci['目标侧']}侧 {yuan4(tgt['金额'])} 分毫不差</div>
                    <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 8 }}>确认无误后点「认领」留痕（组合不自动核销）。</div>
                  </div>
                </div>
              </td></tr>
            })()}
            {/* V2.196 内部往来详情（需求方定：展开一边收一边支，没做账的高亮） */}
            {open && canExp && (r.status === 'xfer_unbooked' || r.status === 'kd_xfer') && r['内部往来明细'] && (() => {
              const xi = r['内部往来明细']
              const isKd = r.status === 'kd_xfer'
              const amt = r['借方金额'] != null ? r['借方金额'] : r['贷方金额']
              const self = { dir: r['方向'], 行: r['开户行'] || '', 账号: r['账号'] || '', 名: r['主体'] || r['户名'] || '',
                日期: r['日期'] || r['金蝶日期'] || '', 金额: amt, booked: isKd,
                凭证: r['金蝶凭证'] || '', 制单: r['制单人'] || '', 摘要: r['摘要'] || '' }
              const opp = { dir: xi['对方方向'], 行: xi['对方开户行'] || '', 账号: xi['对方账号'] || '', 名: xi['对方主体'] || '',
                日期: xi['对方日期'] || '', 金额: amt, booked: !xi['对方是银行流水'],
                凭证: xi['对方凭证'] || '', 制单: xi['对方制单人'] || '', 摘要: xi['对方摘要'] || '' }
              const same = !!xi['同向']
              const legs = same ? (isKd ? [opp, self] : [self, opp])                       // 同向：钱走腿在前、账记腿在后
                : [self, opp].sort((a, b) => (a.dir === '收' ? -1 : 1) - (b.dir === '收' ? -1 : 1))  // 反向：收在前支在后
              const title = (lg, j) => same ? (j === 0 ? `💰 钱走 · ${lg.dir}` : `📒 账记 · ${lg.dir}`)
                : (lg.dir === '收' ? '⬇ 收方' : '⬆ 支方')
              const hi = { background: 'var(--amber-bg)', borderColor: 'var(--amber)', borderStyle: 'solid', borderWidth: 1, borderRadius: 8 }
              const Pane = (lg, j) => <div className="pane" key={j} style={lg.booked ? undefined : hi}>
                <div className="ph">{title(lg, j)} · {lg.booked ? '已做账' : '⚠ 未做账'}</div>
                <div className="kv-row"><span className="kk">账户</span><span className="vv">{(lg.行 + ' ' + lg.账号).trim()}{lg.名 ? `（${lg.名}）` : ''}</span></div>
                <div className="kv-row"><span className="kk">日期 / 金额</span><span className="vv">{lg.日期 || '—'} · {lg.dir} {yuan4(lg.金额)}</span></div>
                {lg.booked ? <div className="kv-row"><span className="kk">凭证</span><span className="vv">{lg.凭证 || '—'}{lg.制单 ? `（制单 ${lg.制单}）` : ''}</span></div>
                  : <div className="kv-row"><span className="kk">凭证</span><span className="vv">—（金蝶查无，待补记）</span></div>}
                <div className="kv-row"><span className="kk">摘要</span><span className="vv">{lg.摘要 || '—'}</span></div>
              </div>
              const hint = xi['两腿均未做'] ? '⚠ 两户银行均已动、金蝶两边查无——两个户各需补记一条腿。'
                : (isKd && !xi['对方是银行流水']) ? '两边账都做了（对开）、两边都缺银行流水——核对流水导出范围/归属期。'
                : same ? '钱走一个户、账记在另一个户——疑账号维度记错，核实后更正凭证的银行账号维度。'
                : '划转两腿一收一支：高亮那条腿金蝶查无，请核算组在该户补记；补完点「刷新金蝶数据」复核。'
              return <tr className="exp"><td colSpan="13">
                <div className="detail">
                  {legs.map(Pane)}
                  <div className="pane side"><div className="diffbox">{hint}</div></div>
                </div>
              </td></tr>
            })()}
            {open && canExp && r.status !== 'misbook' && r.status !== 'combo_pending' && r.status !== 'xfer_unbooked' && r.status !== 'kd_xfer' && <tr className="exp"><td colSpan="13">
              <div className="detail">
                <div className="pane"><div className="ph">银行侧 · 流水</div>
                  <div className="kv-row"><span className="kk">日期</span><span className="vv">{r['日期'] || '—'}</span></div>
                  <div className="kv-row"><span className="kk">方向 / 金额</span><span className="vv">{r['方向']} {yuan4(r['借方金额'] != null ? r['借方金额'] : r['贷方金额'])}{foreign ? '（' + cur + '·原币）' : ''}</span></div>
                  <div className="kv-row"><span className="kk">对方户名</span><span className="vv">{r['收(付)方名称'] || '—'}</span></div>
                  <div className="kv-row"><span className="kk">摘要</span><span className="vv">{r['摘要'] || '—'}</span></div>
                </div>
                <div className="pane"><div className="ph">金蝶侧 · 序时账(1002)</div>
                  <div className="kv-row"><span className="kk">日期</span><span className="vv">{r['金蝶日期'] || '—'}</span></div>
                  <div className="kv-row"><span className="kk">金额(4位)</span><span className="vv">{r['金蝶金额'] != null ? yuan4(r['金蝶金额']) + (foreign ? '（' + cur + '·原币）' : '') : '—'}</span></div>
                  {r['本位币金额'] != null && <div className="kv-row"><span className="kk">本位币金额</span><span className="vv">{yuan4(r['本位币金额'])}{r['汇率'] ? '（汇率 ' + r['汇率'] + '）' : ''}</span></div>}
                  <div className="kv-row"><span className="kk">凭证</span><span className="vv">{r['金蝶凭证'] || '—'}</span></div>
                  <div className="kv-row"><span className="kk">制单人</span><span className="vv">{r['制单人'] || '—'}</span></div>
                  <div className="kv-row"><span className="kk">日期差</span><span className="vv">{r['日期差天'] != null ? r['日期差天'] + ' 天' + (r['日期差天'] === 0 ? '（准时）' : '') : '—'}</span></div>
                </div>
                <div className="pane side">
                  {r['差额'] != null && Math.abs(r['差额']) > 1e-9
                    ? <div className="diffbox">金额差 <b>{yuan4(r['差额'])}</b> · 比对到 4 位小数，判「做错·金额」</div>
                    : <div className="diffbox" style={{ color: 'var(--green)', background: 'var(--green-bg)', borderColor: 'var(--green-line)' }}>金额(4位)一致{r['晚记'] ? ' · ' + r['晚记'] : ' · 当天准时'}</div>}
                  <div style={{ display: 'flex', gap: 8 }}><button className="act-btn pri">生成补账建议</button><button className="act-btn">改判</button></div>
                </div>
              </div>
            </td></tr>}
          </React.Fragment>
        })}
          {rows.length === 0 && <tr><td colSpan="13" className="muted" style={{ padding: '20px' }}>该筛选下无记录。</td></tr>}
        </tbody>
      </table></div>

      <div className="foot">口径：金额比对到 4 位小数（金蝶 4 位属正常）· <b style={{ color: 'var(--ink-2)' }}>外币户按原币比对</b>（银行流水↔金蝶原币金额，本位币折算金额在明细里另列）· 汇兑损益期末重估（原币0、只动本位币）单列「汇兑损益·账面调整」不参与配对 · 晚记以当天为准分本月/跨期 · 账号对不上台账单列交人工 · 一笔=多笔且合计分毫不差（合并缴税/理财本息拆张等）单列「组合待确认」不算错漏账、仍须人工确认不自动核销 · 整户只一侧有数据归「账户缺银行流水/缺金蝶数据」不算漏账/单边{(d['余额稽核账户'] && d['余额稽核账户'].length) ? ` · 余额稽核 ${d['余额稽核账户'].length} 户不逐笔` : ''}。银行流水与金蝶来源见「数据接入」页。</div>
    </div>
  </div>)
}
