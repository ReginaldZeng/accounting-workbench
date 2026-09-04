// [Change Log] Date:2026-08-18 Author:Claude/c Version:V2.318
// 【临时工考勤】样机页。上传「人力上报汇总表」+「打卡时刻表」→ 按公司口径逐日重算 → 四档判定 → 导出带公式的 Excel。
// 全程只读：不写金蝶、不落库（单价表除外，那是按月落库的基础数据）。
// 版式沿用成本台账的分步工作流（.steps）：一屏一件事，步骤条兼作导航，做完的打 ✓。
import React, { useEffect, useState, useRef } from 'react'
import { tempattParams, tempattReview, tempattExportUrl,
  tempattPeriods, tempattPeriod, tempattRerun, tempattPeriodDel,
  tempattAck, tempattAckUndo, tempattAcks,
  tempattContract, tempattContractSave, tempattContractDel,
  tempattDingStatus, tempattDingPull, tempattDingJob, tempattDingFileUrl,
  tempattContractExportUrl, tempattContractImport, tempattContractImportApply,
  tempattSignoff, tempattSignoffSet, tempattSignoffUndo, tempattAckBatch,
  tempattAdjSign, tempattAdjSignSet, tempattAdjSignUndo } from '../api'
import PeriodPicker from '../components/PeriodPicker.jsx'

// 九步（V2.388 起）：先看结果（②），再调口径/登记合同价（③），
// **结算风险单独一页（④）**——它比工时偏离更要紧，6 月有 71 项，
// 原来挂在「逐人核对」页顶会把那一页整个压住，往下翻半天见不到逐人表。
// 认定台账不再单独占一步——「谁放过了什么」就在结算风险页里，长期认定也折在那儿展开。
const STEPS = [
  { k: 'import', n: '数据接入', d: '上传人力上报汇总表 · 打卡时刻表' },
  { k: 'overview', n: '核对总览', d: '工时对不对 · 钱对不对 · 总量与差额' },
  { k: 'rule', n: '口径与单价', d: '取整/扣减/弹性 · 合同价 vs 人力实际' },
  { k: 'risk', n: '结算风险', d: '同名重复计费 · 归属不符 · 在此认定' },
  { k: 'people', n: '逐人核对', d: '一人一行 · 工时与应付' },
  { k: 'daily', n: '逐日明细', d: '每个出勤日的打卡与重算' },
  { k: 'adj', n: '合同外调整', d: '奖 / 罚 / 蒸练补贴 · 逐笔' },
  { k: 'concl', n: '复核结论', d: '请款金额 vs 按合同价应付' },
  { k: 'cost', n: '用工成本汇总', d: '按派遣方 × 车间 · 导出报告' },
]
// 步号从 STEPS 现算，别在文案里写死。原来「去第④步逐人核对」这句里的 ④ 是手敲的，
// 中间插过一步之后逐人核对已经是第⑤步，那行字就一直指错地方。
const stepNo = k => '①②③④⑤⑥⑦⑧⑨⑩'[STEPS.findIndex(x => x.k === k)] || ''

const BAND = {
  ok: { label: '✓ 与口径一致', color: '#6b7280', bg: 'transparent' },
  under: { label: '△ 少记', color: '#92400e', bg: '#fffbe6' },
  over_in: { label: '○ 多记（弹性内）', color: '#1d4ed8', bg: '#eff6ff' },
  over_out: { label: '⚠ 撑不起上报（整期超弹性）', color: '#b91c1c', bg: '#fef2f2' },
  // 逐日冒尖、但这个人整期没超弹性 → 降级为中性（不红、不必查）。与成本会计「按人合起来判」一致。
  over_absorbed: { label: '○ 撑不起上报（整期已消化）', color: '#6b7280', bg: 'transparent' },
  hard: { label: '⚠ 待查', color: '#b91c1c', bg: '#fef2f2' },
  thin: { label: '△ 待查', color: '#92400e', bg: '#fffbe6' },
  // 白夜混合：shift 口径下已按切班窗口逐日切开、正常判档，这一档正常是 0；
  // 只有切回「按打卡重算」口径才会有数（那个口径要精确到小时，混合日的重算值撑不起）
  mixed: { label: '◇ 白夜混合（待人工）', color: '#6b21a8', bg: '#faf5ff' },
  // 有打卡、当天没算临时工工时。打卡表是全厂的，这些天多半是这人在别的名目下上班——
  // 中性档，不标红（6 月全量实测：1,169 条，占人日 41%，标红会把真问题全淹了）
  unbilled: { label: '◇ 有打卡·未计工时', color: 'var(--ink-3)', bg: 'transparent' },
}
// 结算风险卡（同名 / 归属不符 + 认定按钮）**只在第④步结算风险（risk）**。
// 需求方 2026-08-23：「这个不应该在这里……这个四档就是一个总览，
// 将这个异常提示在下面，并且告知在哪里处理就好了」——总览就该只总览，不该摆要动手的东西。
// ②总览里只在「二、钱对不对」留一行：几项待认 + 「去第④步结算风险」。
// ⚠ 认定弹层的可见步骤要单列：第⑥步逐日明细有它自己的逐日「确认无误」按钮，
//    弹层若只跟着卡片走，⑤步一点按钮就没地方填理由了。
// 「本期结果」相关的条幅按用途分两套，别一刀切（2026-08-22 两轮修正）：
//   · 读自留档条 ＝ 提示「你看的是留档、要改就点重跑」，③必须留：那颗按钮正是改完参数要点的。
const ARCHIVE_STEPS = ['rule', 'overview', 'people', 'daily', 'adj', 'concl', 'cost']
const RISK_CARD_STEPS = ['risk']                // 风险卡（含长期认定清单）挂在哪一步
const ACK_STEPS = ['risk', 'daily']             // 认定弹窗可以从哪几步唤起
const h1 = n => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 1 })
const y0 = n => '¥' + Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
const hm = m => `${String(Math.floor((m || 0) / 60)).padStart(2, '0')}:${String((m || 0) % 60).padStart(2, '0')}`

export default function TempAttendance() {
  const [step, setStep] = useState('import')
  const [meta, setMeta] = useState(null)
  const [params, setParams] = useState(null)
  const [summary, setSummary] = useState(null)
  const [punch, setPunch] = useState(null)
  const [ding, setDing] = useState(null)        // 钉钉体检结果
  const [dJob, setDJob] = useState(null)        // 取数任务 {任务, 状态, 进度, 说明, 结果}
  const [dFull, setDFull] = useState(false)     // 取整月（慢一倍多），默认只取上报日附近
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('all')
  // ⚠ 一进页面就把期间落到当年当月，**不能留空**。
  // 原来 month 是 ''，只有右上角的选择器把它「显示」成当年当月（pk 的兜底），
  // 于是所有 `month && …` 的判断全部跳过——文件期间不符的拦截形同虚设
  // （使用者实测：右上角 8 月，传 6 月的表照收不误）。
  const [month, setMonth] = useState(() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  })
  const [saveMsg, setSaveMsg] = useState('')
  const [pgRow, setPgRow] = useState({ page: 1, size: 100 })
  const [pgPpl, setPgPpl] = useState({ page: 1, size: 100 })
  const [pf, setPf] = useState({ dept: '', agency: '', name: '', bad: false })
  const [df, setDf] = useState({ dept: '', agency: '', name: '' })   // 逐日明细的筛选
  const [rowSel, setRowSel] = useState({})    // 逐日明细：勾了哪几行（键＝姓名|日）
  const [rowWhy, setRowWhy] = useState('')     // 逐日批量认定的理由
  const [periods, setPeriods] = useState([])       // 已核期次（留档）
  const [periodNote, setPeriodNote] = useState('')
  const [keepMonths, setKeepMonths] = useState(6)
  const [canDel, setCanDel] = useState(false)      // 有无删留档的权限（＝维护单价表那一档）
  const [imTab, setImTab] = useState('upload')     // 第①步页签：上传核对 / 历史复核结果
  const [delAsk, setDelAsk] = useState('')         // 正在二次确认删除的月份
  const [pgPer, setPgPer] = useState({ page: 1, size: 50 })   // 与 Pager 的每页选项对齐
  const [ledger, setLedger] = useState(null)   // 认定清单（长期认定折在结算风险卡里展开）
  const [ruTab, setRuTab] = useState('check')  // 第③步页签：check=对比 / contract=合同价
  const [contract, setContract] = useState(null)
  const [cForm, setCForm] = useState({ 派遣方: '', 岗位: '', 生效日: '', 失效日: '', 备注: '',
    dw: '', dm: '', nw: '', nm: '' })          // 合同价新增/编辑一行
  const [delRow, setDelRow] = useState('')     // 正在二次确认删除的行 id
  const [ledgerBusy, setLedgerBusy] = useState(false)
  const [ackAsk, setAckAsk] = useState(null)   // 正在填理由的那一条
  const [ackWhy, setAckWhy] = useState('')
  const [ackLong, setAckLong] = useState(false)
  const [ackOpen, setAckOpen] = useState(false)
  const [standOpen, setStandOpen] = useState(false)   // 长期认定清单展开/收起
  const [archive, setArchive] = useState(null)     // 非空＝当前结果读自留档，不是刚跑的
  const [signoff, setSignoff] = useState(null)     // 本期「复核结论已确认」的记录，没确认过是 null
  const [adjSign, setAdjSign] = useState(null)     // 本期「奖惩已核对」的记录（第⑦步），⑧要它才开

  const loadPeriods = () => tempattPeriods()
    .then(r => {
      if (!r.ok) return
      setPeriods(r.periods || []); setPeriodNote(r.说明 || '')
      setKeepMonths(r.保留期数 || 6); setCanDel(!!r.可删)
    })
    .catch(() => {})

  // 删一期留档。二次确认在页面上做完了，这里把月份原文当口令再发一遍，后端还会再挡一道
  const delPeriod = async (m) => {
    setBusy(true); setErr('')
    try {
      const r = await tempattPeriodDel({ month: m, confirm: m })
      if (!r.ok) { setErr(r.msg || '删除失败'); return }
      setDelAsk('')
      if (month === m) { setRes(null); setArchive(null) }   // 删的正是当前看着的那期
      await loadPeriods()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  useEffect(() => {
    tempattParams().then(r => { if (r.ok) { setMeta(r); setParams(r.params) } })
      .catch(() => setErr('取口径参数失败'))
    loadPeriods()
  }, [])

  // 打开一期留档：直接把结论装进页面，不碰文件输入框
  const openPeriod = async (m) => {
    setBusy(true); setErr('')
    try {
      const r = await tempattPeriod(m)
      if (!r.ok) { setErr(r.msg || '读取留档失败'); return }
      setRes(r); setMonth(m)
      setArchive({ ...(r.留档信息 || {}), 可重跑: r.可重跑 })
      if (r.params) setParams(r.params)
      setPgRow(v => ({ ...v, page: 1 })); setPgPpl(v => ({ ...v, page: 1 }))
      setPf({ dept: '', agency: '', name: '', bad: false })
      setStep('overview')
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  // 右上角选期间：选到已核过的月份就直接把那期打开；没核过的就清掉当前结果，免得页面上还挂着上一期的数、
  // 认定/导出却落到新选的月份去（review 实测的坑）。月份与结果从此同进同出。
  const pickMonth = (mm) => {
    if (mm === month) return
    if (periods.some(p => p.月份 === mm)) { openPeriod(mm); return }
    setMonth(mm); setRes(null); setArchive(null)
  }

  // 拿留档的原表按当前参数重跑（原表过了留存期就跑不了，后端会明确报出来）
  const rerun = async () => {
    setBusy(true); setErr('')
    try {
      const r = await tempattRerun({ month, params: params || {}, rates: {} })
      if (!r.ok) { setErr(r.msg || '重跑失败'); return }
      setRes(r); setArchive(null)
      setPgRow(v => ({ ...v, page: 1 })); setPgPpl(v => ({ ...v, page: 1 }))
      loadPeriods(); setStep('overview')
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  // ── 复核结论「确认无误」→ 第⑨步用工成本汇总才开 ────────────────
  // 成本汇总是要发群里、往上报的数。确认之前不给看，免得复核还没做完就被复制走了。
  // 确认时把当时那一版的关键数字存成指纹：之后重跑出不一样的数，这枚确认自动失效，
  // 必须重新看过再签。不存指纹的话，改了金额它照样显示「已确认」，那这个确认就没有意义。
  const se0 = res?.settle?.合计 || {}
  const signFp = res ? [se0.人数, se0.表上合计, se0.应付合计, se0.结论,
    (se0.异常派遣方 || []).join(','), res?.stats?.异常多记日次, res?.stats?.待查日次].join('|') : ''
  const signOk = !!(signoff && signoff.指纹 === signFp)
  const loadSignoff = async (m) => {
    if (!m) return setSignoff(null)
    try { const r = await tempattSignoff(m); setSignoff(r.ok ? (r.记录 || null) : null) }
    catch (e) { setSignoff(null) }
  }
  useEffect(() => { loadSignoff(month) }, [month])
  const doSign = async () => {
    setBusy(true)
    try {
      const r = await tempattSignoffSet({ month, 指纹: signFp,
        摘要: `请款合计 ${y0(se0.表上合计)}，结论 ${se0.结论 || '—'}` })
      if (!r.ok) return setErr(r.msg || '确认失败')
      setSignoff(r.记录 || null); setErr(''); setStep('cost')
    } finally { setBusy(false) }
  }
  const undoSign = async () => {
    setBusy(true)
    try {
      const r = await tempattSignoffUndo({ month })
      if (!r.ok) return setErr(r.msg || '撤销失败')
      setSignoff(null); setErr('')
    } finally { setBusy(false) }
  }

  // ── ⑦合同外调整「本期奖惩已核对」→ 第⑧步复核结论才开 ──────────
  // 使用者 2026-08-29：「合同外调整没有确认也开不了8」。奖/罚/补贴是全表唯一没有对照源的钱，
  // 得先由人签一句「核过」，才谈得上看复核结论、出成本汇总。空期也要签（正向留「确实没有」）。
  const _adjList = res?.stats?.合同外调整 || []
  const _adjT = res?.stats?.合同外调整合计 || {}
  const adjFp = res ? [_adjList.length, _adjT.净额 || 0, _adjT.异常 || 0, _adjT.存疑 || 0].join('|') : ''
  const adjSignOk = !!(adjSign && adjSign.指纹 === adjFp)
  const loadAdjSign = async (m) => {
    if (!m) return setAdjSign(null)
    try { const r = await tempattAdjSign(m); setAdjSign(r.ok ? (r.记录 || null) : null) }
    catch (e) { setAdjSign(null) }
  }
  useEffect(() => { loadAdjSign(month) }, [month])
  const doAdjSignMain = async () => {
    setBusy(true)
    try {
      const r = await tempattAdjSignSet({ month, 指纹: adjFp,
        摘要: _adjList.length ? `${_adjList.length} 笔，净额 ${y0(_adjT.净额)}` : '本期无奖惩' })
      if (!r.ok) return setErr(r.msg || '确认失败')
      setAdjSign(r.记录 || null); setErr(''); setStep('concl')
    } finally { setBusy(false) }
  }
  const undoAdjSignMain = async () => {
    setBusy(true)
    try {
      const r = await tempattAdjSignUndo({ month })
      if (!r.ok) return setErr(r.msg || '撤销失败')
      setAdjSign(null); setErr('')
    } finally { setBusy(false) }
  }

  // 第⑦步合同外调整的批量认定 / 撤销。理由一句管一批，写进每一条记录里。
  const doAdjBatch = async (act, keys, why) => {
    setBusy(true)
    try {
      const r = await tempattAckBatch({ month, 类型: '奖罚', 动作: act, 键: keys, 理由: why })
      if (!r.ok) { setErr(r.msg || '操作失败'); return false }
      setErr(r.msg || ''); await reloadCur(); loadLedger(); return true
    } catch (e) { setErr(String(e)); return false } finally { setBusy(false) }
  }

  // 逐日明细的批量认定 / 撤销。逐日异常分两类——「多记」（撑不起上报）和「待查」（仅1次卡等），
  // 后端批量接口一次只收一个类型，所以按类型分组、各调一次；理由一句管这一批。
  const doDailyBatch = async (act, sel, why) => {
    // 逐日异常分两类（多记/待查），后端一次一个类型，这里按类型分组各调一次。
    // ⚠ 若第二组失败，第一组已在服务端落库——出错分支也要 reloadCur()，否则前端还显示整批未认定，
    //   用户以为全失败又去重点（复查揪出）。
    const byType = {}
    sel.forEach(x => (byType[x.类型] || (byType[x.类型] = [])).push(x.键))
    setBusy(true)
    let done = 0
    try {
      for (const [t, keys] of Object.entries(byType)) {
        const r = await tempattAckBatch({ month, 类型: t, 动作: act, 键: keys, 理由: why })
        if (!r.ok) {
          setErr((done ? `已成功 ${done} 组，但后一组失败：` : '') + (r.msg || '操作失败'))
          if (done) { await reloadCur(); loadLedger() }   // 把已落库那组反映到界面
          return false
        }
        done += 1
      }
      setErr(''); await reloadCur(); loadLedger(); return true
    } catch (e) { setErr(String(e)); if (done) { await reloadCur(); loadLedger() } return false }
    finally { setBusy(false) }
  }

  // 认定一条可疑项。理由必填——「为什么不是问题」才是这条记录的价值。
  // 提交后重新载入本期结果，让「待认 / 已认定」两区立刻反映出来
  const askAck = (x) => { setAckAsk(x); setAckWhy(''); setAckLong(false) }
  const loadLedger = async () => {
    setLedgerBusy(true)
    try { const r = await tempattAcks(); if (r.ok) setLedger(r) } catch (e) {} finally { setLedgerBusy(false) }
  }
  // 长期认定跟着结算风险卡走：进了带风险卡的步骤就把清单读进来（它是跨期的，不随本期结果变）
  useEffect(() => { if (ACK_STEPS.includes(step) && res && !ledger) loadLedger() }, [step, res])
  // 认定/撤销之后重读本期结果——认定信息不在留档快照里，得重新拿一次才能看到「已认定」
  // ⚠ 这三个函数 V2.344 改合同价时被误删过，V2.344/V2.345 两版「确认无误」一点就是 JS 报错（V2.346 恢复）
  const reloadCur = async () => {
    if (!month) return
    try { const r = await tempattPeriod(month); if (r.ok) setRes(r) } catch (e) {}
  }
  const doAck = async () => {
    if (!ackAsk || !ackWhy.trim()) return
    setBusy(true); setErr('')
    try {
      const r = await tempattAck({ month, 类型: ackAsk.类型, 键: ackAsk.键,
        理由: ackWhy.trim(), 范围: (ackLong && ackAsk.可长期) ? '长期' : '本期' })
      if (!r.ok) { setErr(r.msg || '认定失败'); return }
      setAckAsk(null); setAckWhy(''); setAckLong(false)
      await reloadCur(); loadLedger()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const doUndo = async (x) => {
    setBusy(true); setErr('')
    try {
      const r = await tempattAckUndo({ month, 类型: x.类型, 键: x.键 })
      if (!r.ok) { setErr(r.msg || '撤销失败'); return }
      await reloadCur(); loadLedger()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const loadContract = () => tempattContract(month)
    .then(r => { if (r.ok) setContract(r) }).catch(() => {})
  useEffect(() => { if (step === 'rule') loadContract() }, [step, month])

  // 存一行合同价。键＝派遣方+岗位+生效日，同键再存＝覆盖那一行。
  // 失效日留空＝到同一行下一条的生效日前一天；显式填是为了「合同到期不再续」那种推不出来的情况
  const saveContractRow = async () => {
    const f = cForm
    if (!f.派遣方.trim()) { setSaveMsg('请填派遣方'); return }
    if (!/^20\d{2}-\d{2}-\d{2}$/.test(f.生效日)) { setSaveMsg('生效日请填 YYYY-MM-DD'); return }
    const num = v => (v === '' || v == null) ? null : Number(v)
    const day = (f.dw !== '' || f.dm !== '') ? [num(f.dw) || 0, num(f.dm) || 0] : null
    const night = (f.nw !== '' || f.nm !== '') ? [num(f.nw) || 0, num(f.nm) || 0] : null
    setBusy(true); setSaveMsg('')
    try {
      const r = await tempattContractSave({ 派遣方: f.派遣方.trim(), 岗位: f.岗位.trim(),
        生效日: f.生效日, 失效日: f.失效日 || '', 备注: f.备注.trim(), day, night })
      if (!r.ok) { setSaveMsg(r.msg || '保存失败'); return }
      setSaveMsg((r.提醒 && r.提醒.length) ? '已保存，但要注意：' + r.提醒.join('；') : '已保存')
      setCForm({ 派遣方: '', 岗位: '', 生效日: '', 失效日: '', 备注: '', dw: '', dm: '', nw: '', nm: '' })
      await loadContract()
    } catch (e) { setSaveMsg(String(e)) } finally { setBusy(false) }
  }
  const delContractRow = async (id) => {
    setBusy(true); setSaveMsg('')
    try {
      const r = await tempattContractDel({ id, confirm: id })
      if (!r.ok) { setSaveMsg(r.msg || '删除失败'); return }
      setDelRow(''); await loadContract()
    } catch (e) { setSaveMsg(String(e)) } finally { setBusy(false) }
  }

  const form = () => {
    const fd = new FormData()
    fd.append('summary', summary); fd.append('punch', punch)
    fd.append('params', JSON.stringify(params || {}))
    // ⚠ 不再发页面单价表。合同价改成「按行带生效期」的登记表之后，
    // 它才是唯一来源；再发页面这张会盖过合同价（merge 里 override 优先级最高），
    // 等于绕开了刚建的生效期机制。
    fd.append('rates', JSON.stringify({}))
    fd.append('month', month || '')
    return fd
  }

  // 导出：现场上传过就带文件走；看的是留档期次（没有文件）只发月份，后端拿留档结论出表
  const exportForm = () => {
    if (summary && punch) return form()
    const fd = new FormData()
    fd.append('params', JSON.stringify(params || {}))
    fd.append('rates', JSON.stringify({}))     // 同上：合同价以登记表为准，不发页面覆盖
    fd.append('month', month || '')
    return fd
  }

  // ── 从钉钉取打卡 ───────────────────────────────────────────────
  // 产出一张与人力导出格式一致的打卡表，落到 punch 上走原来的流程。
  // 不直接把数据塞进内核：取数结果要能人眼核对（可下载），这是这条线定死的规矩。
  useEffect(() => { tempattDingStatus(month).then(setDing).catch(() => setDing(null)) }, [month])

  const pullDing = async () => {
    if (!summary) { setErr('先选「人力上报汇总表」——要照着它上面的人去钉钉取'); return }
    setErr(''); setDJob({ 状态: '进行中', 进度: 0, 说明: '正在发起…' })
    const fd = new FormData()
    fd.append('summary', summary); fd.append('month', month || '')
    fd.append('scope', dFull ? 'full' : 'worked')
    const r = await tempattDingPull(fd)
    if (!r.ok) { setErr(r.msg || '取数失败'); setDJob(null); return }
    setDJob({ ...r, 状态: '进行中', 进度: 0, 说明: '排队中…' })
    const tick = async () => {
      const j = await tempattDingJob(r.任务).catch(() => null)
      if (!j || !j.ok) { setErr('取数任务丢了，请重试'); setDJob(null); return }
      setDJob({ ...j, 任务: r.任务 })
      if (j.状态 === '失败') { setErr('钉钉取数失败：' + (j.错 || '')); return }
      if (j.状态 === '完成') {
        const blob = await fetch(tempattDingFileUrl(r.任务)).then(x => x.blob())
        const f = new File([blob], `${j.月份}钉钉打卡记录.xlsx`,
          { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
        setPunch(f); setRes(null)
        return
      }
      setTimeout(tick, 2500)
    }
    setTimeout(tick, 1500)
  }

  // 选文件时就按文件名里的月份拦一道：期间以右上角为准，对不上的文件**直接不收**。
  // 只提示不拦的话，人照样会点下去，跑完五六分钟才发现两边对不上（2026-08-29 定）。
  // 文件名认不出月份就不拦——认不出不等于不对，不能凭猜拒收。
  const takeFile = (kind, f, set) => {
    if (!f) { set(null); setRes(null); return }
    const m = (f.name || '').match(/(20\d{2})\s*[-年.\/]?\s*(\d{1,2})(?!\d)/)
    const fm = m ? `${m[1]}-${String(+m[2]).padStart(2, '0')}` : ''
    if (month && fm && fm !== month) {
      setErr(`没收这个文件：右上角选的是 ${month}，而「${f.name}」看着是 ${fm} 的。` +
             `期间以右上角为准——要核 ${fm} 就先把右上角切到 ${fm}，再选这个文件。`)
      return
    }
    setErr(''); set(f); setRes(null)
  }

  const run = async (goto = 'overview') => {
    if (!summary || !punch) { setErr('两个文件都要选：人力上报汇总表 + 打卡时刻表'); return }
    setBusy(true); setErr('')
    try {
      const r = await tempattReview(form())
      if (!r.ok) { setErr(r.msg || '核对失败'); setRes(null); return }
      setRes(r)
      if (r.month && !month) setMonth(r.month)
      setArchive(null)
      setPgRow(v => ({ ...v, page: 1 })); setPgPpl(v => ({ ...v, page: 1 }))
      loadPeriods(); setPgPer(v => ({ ...v, page: 1 }))   // 本期已留档，刷新期次列表
      setStep(goto)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const doExport = async () => {
    if (!has && !(summary && punch)) { setErr('先在第①步选文件，或从「已核期次」里点开一期'); return }
    setBusy(true); setErr('')
    try {
      const r = await fetch(tempattExportUrl, { method: 'POST', body: exportForm() })
      if (!r.ok) { const j = await r.json().catch(() => ({})); setErr(j.msg || '导出失败'); return }
      const blob = await r.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `临时工考勤核对报告${month ? '_' + month : ''}.xlsx`
      a.click(); URL.revokeObjectURL(a.href)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const st = res?.stats
  const has = !!st
  // 「仅不一致」＝**真要查的那两档**：打卡撑不起上报、报了工时却没打卡。
  // ◇未计工时 / ◇白夜混合 是中性档，不该混进来（否则 6 月一点就是 1,271 行）
  const rows = (res?.rows || []).filter(r => (filter === 'all' ? true
    : filter === 'issue' ? ['over_out', 'hard'].includes(r.档) : r.档 === filter)
    && (!df.dept || r.部门 === df.dept) && (!df.agency || r.归属 === df.agency)
    && (!df.name || String(r.姓名 || '').includes(df.name.trim())))
  const pickFilter = k => { setFilter(k); setPgRow(v => ({ ...v, page: 1 })) }
  // 逐日明细里可认定的行（撑不起/待查/仅1次卡），键＝姓名|日；批量选中集按此算
  const dConf = r => ['over_out', 'hard', 'thin'].includes(r.档)
  const dKey = r => `${r.姓名}|${r.归属}|${r.日}`
  const dType = r => (r.档 === 'over_out' ? '多记' : '待查')
  const dConfRows = rows.filter(dConf)
  const dailySel = dConfRows.filter(r => rowSel[dKey(r)])
  const dailyNew = dailySel.filter(r => !r.已认定)
  const dailyOld = dailySel.filter(r => r.已认定)
  const runDaily = async (act) => {
    const src = act === '认定' ? dailyNew : dailyOld
    const ok = await doDailyBatch(act, src.map(r => ({ 类型: dType(r), 键: dKey(r) })), rowWhy.trim())
    if (ok) { setRowSel({}); setRowWhy('') }
  }
  // 每档各有多少条，直接印在筛选钮上——不然得逐个点开才知道哪档有货、哪档是空的
  const bandCount = React.useMemo(() => {
    const c = {}
    for (const r of (res?.rows || [])) c[r.档] = (c[r.档] || 0) + 1
    c.all = (res?.rows || []).length
    c.issue = (c.over_out || 0) + (c.hard || 0)
    return c
  }, [res])
  // 第⑥步逐日那张表的「金额」＝ 差异 × 单价。单价是**这个人结算表上实际套用的价**（公司就是按它付的），
  // 所以这个人的单价要是跟合同价对不上，这一列的钱也跟着错。把「谁的单价不符」传下去，逐日行才标得出来。
  const 单价不符者 = new Set((res?.people || [])
    .filter(p => (p.单价不符 || []).length)
    .map(p => `${p.姓名}|${p.归属}`))
  // 一个人「有异常」＝任何一项对不上：打卡撑不起上报、报了工时没打卡、
  // 单价与合同价不符、合同价缺档、应付与结算表对不上。缺档也算——那是「没法核」，不是「没问题」。
  // 「有异常」＝整期超弹性(公司整期多付) 或 有待查。整期口径：逐日冒尖但整期没超的人不算异常。
  const badPerson = p => (p.超弹性 || p.异常多记日次 > 0) || (p.待查日次 > 0)
    || ((p.单价不符 || []).length > 0) || !!p.合同缺档
    || Math.abs(Number(p.应付偏差) || 0) > 0.01
  // 逐人核对的筛选。姓名做模糊，部门/归属做精确——这两个是下拉，值就是表里的原文，不必模糊
  const ppl = (res?.people || []).filter(p =>
    (!pf.dept || p.部门 === pf.dept) && (!pf.agency || p.归属 === pf.agency) &&
    (!pf.name || String(p.姓名 || '').includes(pf.name.trim())) &&
    (!pf.bad || badPerson(p)))
  // 合计跟着筛选走：筛了「锦绣」就该看到锦绣该付多少（＝那家的请款额），而不是永远显示全表合计。
  // 金额与表格同源——优先结算表自己的数，没有金额列才回落到按合同价重算；缺合同价的人另计个数，别悄悄按 0 加进去
  // 两类结算风险摊平成同一种行，认定按钮才好统一挂；已认定的分到另一组
  // 本期在用的派遣方名单（结算表上出现过的），用来判断「打卡那侧」是不是也是一家派遣方
  const 派遣方名单 = new Set((res?.people || []).map(p => p.归属).filter(Boolean))
  const riskAll = [
    ...((st?.同名多行) || []).map(x => ({
      类型: '同名', 键: x.归一姓名, 可长期: true, 已认定: x.已认定,
      标题: x.原名.join(' / '),
      主: `${x.高风险 ? '⚠ ' : '△ '}${x.原名.join('　/　')}　派遣方 ${x.派遣方.join('、')}　工时 ${x.各行工时.join(' + ')} ＝ ${x.合计工时}`,
      说明: `${x.风险}${x.打卡判据 ? '　·　判据：' + x.打卡判据 : ''}`,
    })),
    // 归属不符要**分两种讲**，不能一句话糊过去（使用者反馈：一整页说明都一样，看不出哪条要紧）：
    //   · 打卡那侧也是一家**派遣方** → 两家都在名单里，钱可能付给了错的一家，要紧
    //   · 打卡那侧是车间/调配名目（浮动组、中段车间、外围…）→ 多半是内部调配，看一眼就能放过
    ...((st?.归属与打卡不符) || []).map(x => {
      const 双方都是派遣方 = 派遣方名单.has(x.打卡部门派遣方)
      // 名字后缀手机尾号：同名的两个人（陈晶 / 代进莉）光看名字分不清是不是一个人，
      // 加「（手机尾号XXXX）」才认得出（钉钉取数才有；人力导出没这列，就只显示名字）
      const 名 = x.姓名 + (x.手机尾号 ? `（手机尾号${x.手机尾号}）` : '')
      return {
        类型: '归属不符', 键: x.姓名, 可长期: true, 已认定: x.已认定, 要紧: 双方都是派遣方,
        标题: `${名}　结算归属与打卡部门不符`,
        主: `${双方都是派遣方 ? '⚠' : '△'} ${名}　结算算 ${x.结算归属} 的钱 / 考勤挂在 ${x.打卡部门派遣方}　总工时 ${h1(x.总工时)}`,
        说明: 双方都是派遣方
          ? `⚠ 两边指向的是**两家不同的派遣方**——${x.结算归属} 和 ${x.打卡部门派遣方} 都是在用的派遣方。` +
            `这个人的钱算在 ${x.结算归属} 头上，考勤却挂在 ${x.打卡部门派遣方}，**可能付错了家**。建议找人力问清楚再放过。`
          : `「${x.打卡部门派遣方}」不是派遣方，是车间或内部调配的名目。` +
            `多半正常——确认确实是内部调配就点「确认无误」，以后不再报这一条。`,
      }
    }),
  ]
  // 月份是 'YYYY-MM' 字符串；选择器要 (年, 期)。month 现在一进页面就有值，
  // 这里的兜底只是防御——真为空时显示当年当月，别让选择器空着
  const pk = (() => {
    const m = /^(20\d{2})-(\d{2})$/.exec(month || '')
    if (m) return { y: +m[1], p: +m[2] }
    const d = new Date()
    return { y: d.getFullYear(), p: d.getMonth() + 1 }
  })()
  const monthStatus = {}, monthCount = {}
  for (let i = 1; i <= 12; i++) monthStatus[String(i)] = '未核'
  periods.forEach(x => {
    const mm = /^(20\d{2})-(\d{2})$/.exec(x.月份 || '')
    if (mm && +mm[1] === pk.y) {
      monthStatus[String(+mm[2])] = '已核'
      if (x.人数) monthCount[String(+mm[2])] = x.人数
    }
  })

  const riskOpen = riskAll.filter(x => !x.已认定)
    .sort((a, b) => (b.要紧 ? 1 : 0) - (a.要紧 ? 1 : 0))   // 要紧的排前面
  const riskAcked = riskAll.filter(x => x.已认定)
  const standing = ledger?.长期 || []
  // 本期原表还在库里就能重跑——不依赖「读自留档」那张卡：现场刚跑完一次，想改合同价再看也得有入口
  const canRerun = !!month && periods.some(p => p.月份 === month && p.可重跑)

  const pplSum = ppl.reduce((a, p) => {
    const 有表 = p.表上合计 != null
    return {
      上报: a.上报 + (p.上报总工时 || 0),
      工资: a.工资 + (有表 ? p.表上工资 : p.应付工资 || 0),
      管理费: a.管理费 + (有表 ? p.表上管理费 : p.应付管理费 || 0),
      合计: a.合计 + (有表 ? p.表上合计 : p.应付合计 || 0),
      重算: a.重算 + (p.应付合计 || 0),
      有表: a.有表 + (有表 ? 1 : 0),
      缺档: a.缺档 + (p.应付合计 == null ? 1 : 0),
      不符: a.不符 + ((p.单价不符 || []).length ? 1 : 0),
      // 金额只算「单价差价 × 工时」，**不用应付偏差**：那一项还含别的原因，且多付少付会相抵
      多付: a.多付 + Math.max(0, (p.单价不符 || []).reduce((x, g) => x + (g.金额 || 0), 0)),
      少付: a.少付 + Math.min(0, (p.单价不符 || []).reduce((x, g) => x + (g.金额 || 0), 0)),
    }
  }, { 上报: 0, 工资: 0, 管理费: 0, 合计: 0, 重算: 0, 有表: 0, 缺档: 0, 不符: 0, 多付: 0, 少付: 0 })

  return (
    <div className="tempatt">
      <div className="head">
        <div>
          <div className="h-title">临时工考勤 · 工时核对</div>
          <div className="h-sub">人力上报工时 vs 打卡记录，按公司口径逐日重算。只读工具，不写金蝶。</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* 期间选择器复用全项目那一个组件（components/PeriodPicker）：年度切换 + 12 期状态胶囊。
              状态由本页喂：核过的月份＝已核（数字是那期人数），没核过＝未核 */}
          <PeriodPicker year={pk.y} period={pk.p} statusMap={monthStatus} countMap={monthCount}
            countLabel="人" countNote="数字＝该期已核的人数" disabled={busy}
            onChange={(y, m) => pickMonth(`${y}-${String(m).padStart(2, '0')}`)} />
          {/* 顶栏不再放常驻「导出 Excel」——它绕过 ⑦→⑧→⑨ 签字闸门，没签也能导出含用工成本的整份报告。
              导出统一收敛到第⑨步（要先签⑦奖惩、再签⑧结论才开），确保「复核没做完别被复制走」（复查 V2.403，Reginald 定删）。 */}
        </div>
      </div>

      {/* 步骤条兼作导航：没核对过之前只有第①步可点 */}
      <div className="steps">
        {STEPS.map((s, i) => {
          const done = has && STEPS.findIndex(x => x.k === step) > i
          const cur = step === s.k
          // ③口径与单价不依赖本期结果：成本会计登记合同价不需要先跑一期
          // 签字闸门链：⑦奖惩确认 → ⑧复核结论才开；⑧确认无误 → ⑨用工成本才开
          // ⑨用工成本要**两道都签**：撤销⑦会让⑧重新上锁，⑨也得跟着锁上，否则⑦撤了⑨还开着（复查揪出）
          const lockMsg = (s.k === 'concl' && has && !adjSignOk) ? `需先在第${stepNo('adj')}步确认奖惩已核对`
            : (s.k === 'cost' && has && !adjSignOk) ? `需先在第${stepNo('adj')}步确认奖惩已核对`
            : (s.k === 'cost' && has && !signOk) ? `需先在第${stepNo('concl')}步确认无误` : ''
          const locked = !!lockMsg
          const clickable = (['import', 'rule'].includes(s.k) || has) && !locked
          return (
            <div key={s.k} className={'step' + (cur ? ' cur' : '') + (done ? ' done' : '') + (locked ? ' locked' : '')}
              style={{ cursor: clickable ? 'pointer' : 'default' }}
              title={lockMsg}
              onClick={() => clickable && setStep(s.k)}>
              <div className="num">{locked ? '🔒' : done ? '✓' : i + 1}</div>
              <div><div className="sn">{s.n}</div>
                <div className="sd">{lockMsg || s.d}</div></div>
            </div>
          )
        })}
      </div>

      <div className="body">
        {/* 结算风险：同名跨派遣方 / 同名重复行 / 归属与打卡不符。
            这几条不是工时问题，是【钱被重复付】的问题，比少记严重得多，故置顶。 */}
        {/* 结算风险单独一页后，没有风险时也得给个明确交代——空白页看着像没加载出来 */}
        {has && RISK_CARD_STEPS.includes(step)
          && !(riskOpen.length > 0 || riskAcked.length > 0 || standing.length > 0) &&
          <div className="card" style={{ padding: 22, lineHeight: 2 }}>
            <b style={{ color: '#15803d' }}>✓ 本期没有结算风险</b>
            <div className="note">
              这一页查两件事：<b>同名多行</b>（同一个人可能被重复计费）和<b>归属与打卡不符</b>
              （结算挂在甲派遣方、打卡部门却写着乙）。两样都没查出来。
              <br />它们比工时偏离更要紧——工时差一点是钱多付一点，这两样是<b>整个人的钱可能付错对象或付两遍</b>。
            </div>
          </div>}
        {has && RISK_CARD_STEPS.includes(step) && (riskOpen.length > 0 || riskAcked.length > 0 || standing.length > 0) &&
          <div className={'card ta-risk' + (riskOpen.length ? '' : ' clear')}>
            <b className="hd">
              {riskOpen.length
                ? `结算风险（比工时偏离更要紧，先看这里）· ${riskOpen.length} 项待认`
                : riskAcked.length ? '结算风险：本期全部已认定无误' : '结算风险：本期没有新发现'}
            </b>
            {/* 没查成的人必须报出来，不写这一句，条数变少会被当成情况变好了。
                ⚠ 只说事实（打卡表这几行没写部门），**别替它编原因**——
                原来这里断言「用钉钉取数时离职的人查不到部门」，后来证明钉钉侧查得到，
                而没部门的恰恰是人力导出那张表，等于把因果说反了（V2.378 修）。 */}
            {st?.归属无法核对人数 > 0 && (() => {
              const 名单 = [...new Set(st.归属无法核对名单 || [])]
              return <div className="warn" style={{ fontSize: 12, lineHeight: 1.9, margin: '2px 0 8px' }}>
                ⚠ 另有 <b>{名单.length}</b> 人（{st.归属无法核对人数} 行）的「归属与打卡不符」<b>这一项没查</b>
                ——<b>打卡表里这几行没写部门</b>，没东西跟结算归属比
                {名单.length > 0 && <>：{名单.slice(0, 12).join('、')}{名单.length > 12 && ' 等'}</>}。
                <b>不是没问题，是没查。</b>
                　用上面的「⇩ 从钉钉取打卡」通常能补上部门——2026-06 实测，同样这几个人钉钉侧都有部门，一个都不缺。
              </div>
            })()}

            {riskOpen.map((x, i) => <RiskRow key={'o' + i} x={x} onAck={askAck} busy={busy} />)}

            {riskAcked.length > 0 && <div className="acked">
              <button className="btn link" onClick={() => setAckOpen(v => !v)}>
                {ackOpen ? '收起' : '展开'}已认定无误 {riskAcked.length} 项
              </button>
              <span className="note">　认定不是删除——这些条目仍在，记着谁、什么时候、因为什么认的。</span>
              {ackOpen && riskAcked.map((x, i) => <RiskRow key={'a' + i} x={x} acked onUndo={doUndo} busy={busy} />)}
            </div>}

            {/* 长期认定：每期都压住提示，却只在那期恰好又冒出同一条时才露面。
                万一那个人离职、那条风险不再出现，它就永远躺在库里一直生效——必须有个地方能看全。
                早先单独做成一步「认定台账」，2026-08-22 需求方拍板删掉那一步，清单折到这里。 */}
            {standing.length > 0 && <div className="acked">
              <button className="btn link" onClick={() => setStandOpen(v => !v)}>
                {standOpen ? '收起' : '展开'}长期认定 {standing.length} 条（跨期一直生效）
              </button>
              <span className="note">　它们每一期都会压住对应提示；情况变了记得回来撤销。</span>
              {standOpen && <div className="wrap" style={{ marginTop: 8 }}><table className="tbl">
                <thead><tr><th>类型</th><th>对象</th><th>理由</th><th>认定人</th><th>认定时间</th><th>已生效</th><th></th></tr></thead>
                <tbody>{standing.map((x, i) => {
                  const stale = x.距今天数 != null && x.距今天数 > (ledger?.复核提示天数 || 180)
                  return <tr key={i} style={{ background: stale ? '#fffbe6' : '' }}>
                    <td>{x.类型}</td><td><b>{x.键}</b></td>
                    <td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{x.理由}</td>
                    <td>{x.认定人}</td><td>{x.时间}</td>
                    <td>{x.距今天数 == null ? '—' : `${x.距今天数} 天`}
                      {stale && <div className="warn" style={{ fontSize: 11 }}>建议回头复核一次</div>}</td>
                    <td><button className="btn link-del" disabled={busy}
                      onClick={() => doUndo({ 类型: x.类型, 键: x.键 })}>撤销</button></td>
                  </tr>
                })}</tbody>
              </table></div>}
            </div>}

            <div className="foot">
              同名是<b>姓名归一之后</b>的同名：公司本来就用后缀区分同名者（张博G / 张博J），是归一把后缀抹掉才撞到一起的。
              工具只指认并给判据，是不是同一个人由人来认。认定不是删除——条目仍在，记着谁、什么时候、因为什么认的。
            </div>
          </div>}

        {/* 认定弹层：理由必填——将来翻这份底稿的人要知道当时为什么认为它不是问题 */}
        {ackAsk && ACK_STEPS.includes(step) && <div className="ta-ackmask"
          onClick={e => { if (e.target === e.currentTarget && !busy) { setAckAsk(null); setAckWhy('') } }}>
        <div className="card ta-ackbox" role="dialog" aria-modal="true"
          onKeyDown={e => { if (e.key === 'Escape' && !busy) { setAckAsk(null); setAckWhy('') } }}>
          <div className="hd">确认无误</div>
          <div className="who">{ackAsk.标题}</div>
          <div className="note">{ackAsk.说明}</div>
          <label>理由<span className="req">必填</span>
            <input autoFocus value={ackWhy} placeholder="例：浮动组是车间内部调配名目，结算仍按锦绣，人力已确认"
              onChange={e => setAckWhy(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && ackWhy.trim()) doAck() }} />
            <span className="hint">会连同你的名字和时间一起记进认定台账，随时可撤销</span>
          </label>
          {ackAsk.可长期 ? <label className={'scope' + (ackLong ? ' on' : '')}>
            <input type="checkbox" checked={ackLong} onChange={e => setAckLong(e.target.checked)} />
            <span><b>长期认定</b>——以后每期都不再报这一条
              <span className="warn">会持续压住提示，情况变了也不会再提醒，慎用。不勾就只认定本期。</span></span>
          </label> : <div className="scope tip">这类发现带日期，只能按本期认定。</div>}
          <div className="ops">
            <button className="btn" onClick={() => { setAckAsk(null); setAckWhy('') }} disabled={busy}>取消</button>
            <button className="btn primary" onClick={doAck} disabled={busy || !ackWhy.trim()}>
              {busy ? '提交中…' : (ackLong && ackAsk.可长期 ? '长期认定' : '确认无误')}</button>
          </div>
        </div></div>}

        {/* 「本期状态」那条横幅已删（V2.348，需求方第二次提）。它整条都是重复：
            单价与合同价 → 「二、钱对不对」有一行；异常多记 → 「一、工时对不对」有一行；
            已认定 → 第④步风险卡；待人工确认 → 下面的黄条；只读不写金蝶 → 页面副标题与①使用说明。
            两件下面没有的：人数/人日并进「一、工时对不对」的副标题；两表月份对不上本来就有独立红条。 */}
        {err && <div className="card" style={{ padding: 12, marginBottom: 12, color: '#b91c1c', background: '#fef2f2' }}>{err}</div>}

        {/* 两表月份对不上：这是「是不是拿错文件」的问题，一旦成立后面所有数都不必看，故哪一步都提示 */}
        {has && st.月份不一致 && <Note tone="bad" title="两张表的月份对不上，请先确认是不是拿错了文件："
          items={[`汇总表标题「${st.汇总表标题}」→ 识别为 ${st.汇总表月份}`,
                  `打卡表「${st.打卡表标题}」→ 识别为 ${st.打卡表月份}`]}
          foot="也可能只是标题没改——成本会计的 7 月复核版就把标题写成了「2026年5月」。" />}

        {/* 看的是留档还是刚跑的，必须一眼分得清——否则改了参数看不到变化会以为工具坏了 */}
        {res?.旧口径留档 && ARCHIVE_STEPS.includes(step) && <div className="card" style={{ padding: 12, marginBottom: 12, background: '#fffbeb', border: '1px solid #fcd34d', color: '#92400e', fontSize: 12.5, lineHeight: 1.8 }}>
          <b>⚠ 这一期留档是按「打卡重算」的旧口径跑的</b>——下面四档仍按旧口径显示（少记/多记分档）。
          2026-06 全量实证已表明<b>上报工时是排班班次时长</b>，按旧口径会把大量正常日子报成「少记」。
          点<b>「按当前参数重跑」</b>换成新口径看。
        </div>}
        {res?.旧版留档 && ARCHIVE_STEPS.includes(step) && <div className="card" style={{ padding: 12, marginBottom: 12, background: '#fef2f2', color: '#b91c1c', fontSize: 12.5, lineHeight: 1.8 }}>
          <b>⚠ 这一期留档是旧版规则算的（没有合同价核对）</b>，结论一律按「待核」显示，不能当真。
          {archive?.可重跑 ? '点下方「按当前参数重跑」就会按现行规则重算。' : '原表已过留存期，请回第①步重新上传。'}
        </div>}
        {!archive && has && canRerun && ARCHIVE_STEPS.includes(step) && <div className="card ta-archive">
          改了口径或合同价要看新结果，点
          <button className="btn" style={{ margin: '0 6px' }} onClick={rerun} disabled={busy}>
            {busy ? '重跑中…' : '按当前参数重跑'}</button>（用的是本期留档的原表，不必重新上传）。
        </div>}
        {archive && ARCHIVE_STEPS.includes(step) && <div className="card ta-archive">
          <b>本期结果读自留档</b>　{archive.月份}　·　{archive.跑批时间} 由 {archive.跑批人} 跑批
          {(archive.跑批次数 || 1) > 1 && <>（第 {archive.跑批次数} 次）</>}　·　没有重新上传。
          {archive.可重跑
            ? <> 改了口径或单价要看新结果，点
                <button className="btn" style={{ margin: '0 6px' }} onClick={rerun} disabled={busy}>
                  {busy ? '重跑中…' : '按当前参数重跑'}</button>
                （用的是留档的原表）。</>
            : <span className="note"> 原始两张表已过留存期，只能看这份结论；要改参数请回第①步重新上传。</span>}
        </div>}

        {/* ① 数据接入：使用说明 + 两个页签（上传通道 / 历史复核结果）
            早先把上传区和期次表堆在同一屏，越往后期次越多、上传按钮越被挤到看不见 */}
        {step === 'import' && <>
          <div className="card ta-guide">
            <div className="hd">① 数据接入 · 使用说明</div>
            <p><b>这个工具干什么</b>：把人力上报的《临时工劳务明细汇总表》和考勤系统的《打卡时间》摆在一起核——
              量上报工时与打卡口径的偏离、复核各家派遣方的请款金额、扫结算风险。
              <b>全程只读，不往金蝶写任何东西，也不产生凭证。</b></p>
            <p><b>怎么走</b>：本页上传两张表（打卡表也可点下方「⇩ 从钉钉取打卡」直接取）→ 点「开始核对」→
              顺着上方<b>九步</b>看过去，<b>报告在第⑨步导出</b>。其中⑦合同外调整、⑧复核结论各要点一下「确认」，
              下一步才打开。以前核过的月份，直接切到右边「历史复核结果」点开，<b>不用重新上传</b>。</p>
            <div className="hd2">五件要先知道的</div>
            <ul>
              <li><b>这不是重算工资。</b> 上报工时并不是按打卡算出来的——同一打卡跨度档内上报值能出现
                3–4 种取值，更像排班班次时长。所以本页的「重算」<b>是一把尺子，用来量偏离</b>，
                不是要替代人力的数。<b className="warn">付款仍以人力汇总表与合同为准。</b></li>
              <li><b>少记不追，多记才查；多记按「人整期合起来」判。</b> 人力少记（工人少拿）财务可接受，只作提示；
                多记的异常判定<b>跟成本会计一致——看「这个人整期净多记」是否超弹性</b>，不逐日揪：
                某天冒尖、但本人整期没超，算<b>已消化</b>、不单独查；
                <b className="warn">只有整期净多记超弹性＝公司整期多付钱，才是必查的一档。</b></li>
              <li><b>结算风险比工时偏离要紧。</b> 同名挂在两家派遣方、同名重复行、结算归属与打卡部门不符——
                这三项排在工时偏离<b>前面</b>看。工具只指认并给判据，<b>是不是同一个人由人来认</b>。</li>
              <li><b>合同价由成本会计登记，工具不猜。</b> 第③步「合同价（成本会计维护）」是单价的唯一来源，
                按行带生效期。<b className="warn">没登记的派遣方／岗位，应付算不出来，结论是「待核」——不是正常。</b>
                汇总表表头写的计价规则是人力自己写的，只作参考，不当基准。</li>
              <li><b>结果按期留档。</b> 复核结论长期保存，选月份就能回看；
                <span className="warn">原始两张表只留最近 {keepMonths} 期</span>，过期后仍可看结论、导报告，
                但改口径重跑要重新上传。</li>
            </ul>
          </div>

          <div className="ta-tabs">
            {[['upload', '上传核对'], ['hist', `历史复核结果${periods.length ? `（${periods.length}）` : ''}`]]
              .map(([k, n]) => (
                <button key={k} className={'tab' + (imTab === k ? ' on' : '')} onClick={() => setImTab(k)}>{n}</button>
              ))}
          </div>

          {imTab === 'upload' && <>
            <div className="card" style={{ padding: 18, marginBottom: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
                <Pick label="① 人力上报汇总表" hint="《YYYY年M月（临时工）考勤汇总表》。全量表或按派遣方拆分页都认；右侧金额列是复核结论的主列，表头的计价规则栏只作参考。"
                  file={summary} onPick={f => takeFile('汇总表', f, setSummary)} />
                <div>
                  <Pick label="② 打卡时刻表" hint="考勤系统导出的《打卡时间》。一格多次打卡、含「次日07:52」的跨零点记录都认。"
                    file={punch} onPick={f => takeFile('打卡表', f, setPunch)} />
                  <DingPull ding={ding} job={dJob} onPull={pullDing} hasSummary={!!summary}
                            full={dFull} onFull={setDFull} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: 10, marginTop: 18, alignItems: 'center', flexWrap: 'wrap' }}>
                <button className="btn primary" onClick={() => run('overview')} disabled={busy || !summary || !punch}>
                  {busy ? '核对中…' : '开始核对 →'}
                </button>
                <button className="btn" onClick={() => setStep('rule')}>先看口径与单价</button>
                <FileMonthHint summary={summary} punch={punch} month={month} />
                <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>
                  期间以右上角为准；同时会从两张表的标题里识别月份，识别出来和右上角对不上就拦住不跑（免得按 8 月的合同价算 7 月的数、还把 8 月的留档覆盖掉）。
                  {month && periods.some(p => p.月份 === month) &&
                    <b className="warn">　⚠ {month} 已有留档，跑完会覆盖它。</b>}
                </span>
              </div>
            </div>
          </>}

          {imTab === 'hist' && (periods.length === 0
            ? <div className="card" style={{ padding: 24, color: 'var(--ink-3)' }}>
                还没有留档。到「上传核对」传两张表跑一次，这里就会出现。
              </div>
            : <div className="card ta-periods">
                <div className="hd">已核期次　<span className="note">点「查看」直接看结论，不用重新上传</span></div>
                <div className="wrap">
                  <table className="tbl">
                    <thead><tr>
                      <th>月份</th><th>跑批时间</th><th>跑批人</th><th>人数</th>
                      <th>请款合计</th><th>结论</th><th>⚠异常多记</th><th>金额核对</th><th>奖罚异常</th>
                      <th>原表</th><th>操作</th>
                    </tr></thead>
                    <tbody>{page(periods, pgPer).map(p => (
                      <tr key={p.月份} className={p.月份 === month ? 'on' : ''}>
                        <td><b>{p.月份}</b>{(p.跑批次数 || 1) > 1 && <span className="note"> · 第{p.跑批次数}次</span>}</td>
                        <td>{p.跑批时间}</td><td>{p.跑批人}</td><td>{p.人数}</td>
                        <td>{p.应付合计 == null ? '—' : y0(p.应付合计)}</td>
                        {/* 整期「待核」（合同价没登记）的月份不能长得跟正常月一样；旧留档没存结论就显示 — */}
                        <td style={{ fontWeight: 600, color: p.结论 === '异常' ? '#b91c1c' : p.结论 === '待核' ? '#92400e' : p.结论 === '正常' ? 'var(--ok, #15803d)' : 'var(--ink-3)' }}>
                          {p.结论 === '异常' ? '⚠ 异常' : p.结论 === '待核' ? `△ 待核${p.缺合同价人数 ? `（${p.缺合同价人数} 人缺合同价）` : ''}` : p.结论 === '正常' ? '✓ 正常' : '—'}</td>
                        <td style={{ color: p.异常多记日次 > 0 ? '#b91c1c' : '' }}>{p.异常多记日次 || 0}</td>
                        <td style={{ color: p.金额核对条数 > 0 ? '#b91c1c' : '' }}>{p.金额核对条数 || 0}</td>
                        <td style={{ color: p.合同外调整异常 > 0 ? '#b91c1c' : '' }}>{p.合同外调整异常 || 0}</td>
                        <td>{p.可重跑
                          ? <span className="ok">在库</span>
                          : <span className="note" title="原始两张表只保留最近若干期，过期后仍可看结论，但改参数重跑需重新上传">已过期</span>}</td>
                        <td className="act">
                          {delAsk === p.月份
                            ? <>
                                <b className="warn">删掉 {p.月份}？不可撤销</b>
                                <button className="btn danger" onClick={() => delPeriod(p.月份)} disabled={busy}>
                                  {busy ? '删除中…' : '确认删除'}</button>
                                <button className="btn" onClick={() => setDelAsk('')} disabled={busy}>取消</button>
                              </>
                            : <>
                                <button className="btn" onClick={() => openPeriod(p.月份)} disabled={busy}>查看 →</button>
                                {canDel && <button className="btn link-del" onClick={() => setDelAsk(p.月份)}
                                  title="删掉这一期的结论与原表（调试期的试跑数据可以从这里清）">删除</button>}
                              </>}
                        </td>
                      </tr>))}</tbody>
                  </table>
                </div>
                <Pager total={periods.length} pg={pgPer} on={setPgPer} />
                <div className="foot">
                  {periodNote}
                  {canDel
                    ? <><br />调试期试跑留下的期次可以直接删掉；<b>删除不可撤销</b>，会同时清掉该期的结论与原表，并留审计记录。</>
                    : <><br />你没有「维护单价表」权限，因此不能删留档——删除与改单价同属一档，找管理员开。</>}
                </div>
              </div>)}
        </>}

        {/* ③ 口径与单价 */}
        {step === 'rule' && params && <>
          <div className="ta-tabs">
            {[['check', '对比：合同价 vs 人力实际'], ['contract', '合同价（成本会计维护）']].map(([k, n]) => (
              <button key={k} className={'tab' + (ruTab === k ? ' on' : '')} onClick={() => setRuTab(k)}>{n}</button>
            ))}
          </div>

          {ruTab === 'check' && <div className="card" style={{ padding: 18, marginBottom: 14 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>工时口径参数</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 14 }}>{meta?.口径来源}</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12 }}>
              <Num label="取整粒度（小时）" v={params.round_step} on={v => setParams({ ...params, round_step: v })} />
              <Sel label="取整方向" v={params.round_mode} on={v => setParams({ ...params, round_mode: v })}
                opts={[['floor', '向下取整（实证口径）'], ['round', '四舍五入']]} />
              <Num label="白班扣减（小时）" v={params.day_break} on={v => setParams({ ...params, day_break: v })} />
              <Num label="夜班扣减（小时）" v={params.night_break} on={v => setParams({ ...params, night_break: v })} />
              <Num label="多记弹性（小时/天）" v={params.tolerance} on={v => setParams({ ...params, tolerance: v })} />
              <Sel label="上报工时的口径" v={params.report_basis || 'shift'} on={v => setParams({ ...params, report_basis: v })}
                opts={[['shift', '排班班次时长（实证口径）'], ['punch', '按打卡重算（人力口头口径）']]} />
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 14, lineHeight: 1.9,
                          background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 6, padding: '8px 10px' }}>
              <b>「上报工时的口径」这一项决定整张判定表问的是什么问题。</b><br />
              <b>排班班次时长</b>（默认）——2026-06 全量 448 人实证：打卡跨度 12.5h 和 13.0h 的日子，
              上报<b>同为 11.0h</b>（夜班 452/542 天、白班 590/900 天都报 11.0，即标准班 08:00–20:00 或
              20:00–08:00 扣 1 小时休息）。既然上报的是班次时长，「差了多少」就没有意义，
              只问<b>打卡撑不撑得起这个班</b>。<br />
              <b>按打卡重算</b>——人力 2026-08-10 的口头答复，实证不成立，保留可切回对比：
              切过去 6 月会有 <b>1,410 条报「少记」</b>，那不是人力少记，是两边算法不同。
            </div>
            <div style={{ fontSize: 12, color: '#92400e', marginTop: 14, lineHeight: 1.8 }}>
              夜班切班窗口（当日 {hm(params.night_start_from)} 后首卡＝上班，次日 {hm(params.night_end_by)} 前末卡＝下班）
              是<b>按现有数据反推的，尚未经人力确认</b>。窗口差一点异常清单就假报一大片——
              6 月实测：窗口取「次日 06:00–11:00 首卡」会把异常多记假报成 117 人日，改成现值后收敛到 3 人日。
            </div>
          </div>}

          {ruTab === 'check' && <div className="card" style={{ padding: 18, marginBottom: 14 }}>
            <RateCheck c={st?.单价核对} has={has} rates={res?.rates} />
          </div>}

          {ruTab === 'contract' && <div className="card" style={{ padding: 18, marginBottom: 14 }}>
            <ContractRows c={contract} month={month} busy={busy}
              form={cForm} setForm={setCForm} onSave={saveContractRow}
              onDel={delContractRow} delRow={delRow} setDelRow={setDelRow} msg={saveMsg}
              onImported={r => setContract(x => ({ ...(x || {}), 行: r.行 }))} />
          </div>}
        </>}

        {/* ② 核对总览 */}
        {step === 'overview' && has && <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12, marginBottom: 14 }}>
            <Kpi title="上报总工时" v={h1(st.上报总工时) + ' h'} sub="人力汇总表原数" />
            <Kpi title="按口径重算" v={h1(st.重算总工时) + ' h'} sub="打卡逐日重算" />
            <Kpi title="差异" v={(st.差异小时 > 0 ? '+' : '') + h1(st.差异小时) + ' h'}
              sub={st.差异小时 > 0 ? '正数＝人力少记' : st.差异小时 < 0 ? '负数＝人力多记' : '完全一致'}
              tone={st.差异小时 > 0 ? 'warn' : st.差异小时 < 0 ? 'bad' : ''} />
            <Kpi title="差额金额" v={y0(st.差额金额)} sub="差异 × 各人结算表上实际的单价" />
          </div>

          {/* 总览分两块：上面「工时对不对」（四档判定），下面「钱对不对」（单价/自查/奖罚）。
              早先这一页只有四档，单价不符只在状态条露一枚胶囊、要点进第③步才看得到——
              使用者 2026-08-22：「这一块是不是要把单价不符的也放进来」。是。总览不总览钱，就不叫总览。 */}
          <div className="ov-hd">一、工时对不对　<span className="note">人力上报 vs 打卡逐日重算　·　{st.人数} 人 / {st.比对人日} 人日</span></div>
          <div className="card" style={{ padding: 0, marginBottom: 14 }}>
            <table className="tbl">
              <thead><tr><th style={{ width: 230 }}>判定档</th><th>日次</th><th>工时</th><th>金额</th><th>含义</th></tr></thead>
              <tbody>
                {st.上报口径 !== 'punch'
                  ? <>
                      <Band b="ok" n={st.一致日次}
                        note={`在厂时长撑得起上报的班次——上报是排班班次时长（标准班 11 小时），本就小于在厂时长，不是问题。其中 ${st.打卡多于上报日次 || 0} 天在厂比上报多，合计 ${h1(st.打卡多于上报小时 || 0)} 小时，仅供参考`} />
                      <Band b="over_out" n={st.异常多记日次} h={st.异常多记小时} m={st.异常多记金额}
                        note={`⚠ 唯一必须查的一档，按人整期判（与成本会计一致）：${st.超弹性人数 || 0} 人整期净多记超弹性，公司可能多付；工时/金额为这些人整期净多记。另有 ${st.整期已消化多记日次 || 0} 天逐日冒尖、但本人整期没超，已消化不计。须业务逐笔说明（多为漏打卡）`} />
                    </>
                  : <>
                      <Band b="ok" n={st.一致日次} note="重算与上报分毫不差" />
                      <Band b="over_in" n={st.弹性内多记日次} h={st.弹性内多记小时} m={st.弹性内多记金额}
                        note={`多记 ≤ ${params?.tolerance} 小时/天，属打卡分钟级抖动，视为正常，不追`} />
                      <Band b="over_out" n={st.异常多记日次} h={st.异常多记小时} m={st.异常多记金额}
                        note="⚠ 唯一必须查的一档：公司多付了钱。须业务逐笔说明（实务上多为漏打卡）" />
                      <Band b="under" n={st.少记日次} h={st.少记小时} m={st.少记金额}
                        note="上报少于打卡口径。财务可接受，只作提示，不要求补付" />
                    </>}
                <Band b="hard" n={st.待查日次} note="⚠ 报了工时、却一次卡都没有——先查打卡是否完整再谈工时" />
                {(st.未计工时日次 || 0) > 0 &&
                  <Band b="unbilled" n={st.未计工时日次}
                    note={`有打卡、当天没算临时工工时（其中 ${st['未计工时·仅1次卡'] || 0} 天只有一次卡），涉及 ${st.未计工时人数 || 0} 人。打卡表是全厂门禁数据，这些天多半是这人在别的名目下上班——不是漏记，故不标红`} />}
                {(st.白夜混合日次 || 0) > 0 &&
                  <Band b="mixed" n={st.白夜混合日次} note="同月既有白班又有夜班的人。切班规则已按 2026-06 全量实证定案（首卡落在 16:00 之后按夜班切，下班取次日 11:30 前的末卡），所以这些日子已经逐日判过档了；只有切回「按打卡重算」口径时这一档才会有数。" />}
              </tbody>
            </table>
          </div>

          <MoneyOverview st={st} res={res} go={setStep} riskOpen={riskOpen} riskAcked={riskAcked} />

          {/* 上表「合同价缺档」那行已给判定、格数和去处，这里只补名单——别把同一句结论说两遍 */}
          {st.缺合同价人数 > 0 && <Note tone="warn"
            title={`缺合同价的这 ${st.缺合同价人数} 人分布在这几档：`}
            items={Object.entries((st.缺合同价 || []).reduce((a, x) => {
              const k = `${x.归属 || '（空）'}·${x.岗位}`; (a[k] = a[k] || []).push(x.姓名); return a }, {}))
              .map(([k, names]) => `${k}（${names.length} 人：${names.slice(0, 6).join('、')}${names.length > 6 ? '…' : ''}）`)} />}
          {st.偏离未计价人数 > 0 && <Note tone="warn"
            title={`${st.偏离未计价人数} 人结算表上没有单价、合同价也没登记，偏离金额按 0 计（工时差异仍照算）：`}
            items={(st.偏离未计价 || []).map(x => `${x.姓名}（${x.归属 || '无归属'}·${x.岗位}）——${x.原因}`)} />}

          {(st.白夜混合人数 > 0 || st.未匹配人数 > 0 || st.待指认人数 > 0 || (st.打卡表重名 || []).length > 0) &&
            <Note tone="warn" title="下面这些工具不猜，请人工确认：" items={[
              st.白夜混合人数 > 0 && `${st.白夜混合人数} 人同月既有白班又有夜班，已按切班窗口逐日切开、正常判档（不再整档交人工）：${(st.白夜混合名单 || []).join('、')}`,
              st.待指认人数 > 0 && `${st.待指认人数} 人姓名归一后撞上多个打卡记录，未参与比对：${(st.待人工指认 || []).map(x => `${x.姓名}→${x.候选.join('/')}`).join('；')}`,
              st.未匹配人数 > 0 && `${st.未匹配人数} 人在打卡表里找不到：${(st.未匹配打卡 || []).join('、')}`,
              // 打卡表重名经 _apply_acks 统一成 {姓名, 已认定?} 对象——直接 join 会印出 [object Object]（V2.346 实测）
              (st.打卡表重名 || []).length > 0 && `打卡表里有同名多行：${st.打卡表重名.map(x => (x && x.姓名) || x).join('、')}`,
            ].filter(Boolean)} />}

          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn" onClick={() => setStep('people')}>看逐人核对 →</button>
            <button className="btn" onClick={() => { pickFilter('issue'); setStep('daily') }}>只看不一致的日次 →</button>
          </div>
        </>}

        {/* ④ 逐人核对 */}
        {step === 'people' && has && <>
          {res.缺逐人单价核对 && <div className="card" style={{ padding: 12, marginBottom: 12, background: '#fffbeb', border: '1px solid #fcd34d', color: '#92400e', fontSize: 12.5, lineHeight: 1.8 }}>
            <b>⚠ 这一期留档跑在「逐人单价核对」之前</b>，下表标不出谁的单价与合同价不符
            （②总览按格报的仍然有效）。要逐人看，点上方<b>「按当前参数重跑」</b>。
          </div>}
          <PeopleFilter people={res.people} pf={pf} on={v => { setPf(v); setPgPpl({ ...pgPpl, page: 1 }) }}
                        shown={ppl.length} total={res.people.length} sum={pplSum} showBad />
          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th colSpan={5} className="grp">人员</th>
                  <th colSpan={4} className="grp sep pay">本期应付（结算表金额＝请款依据）· 与合同价对不上的标红，悬停看差在哪</th>
                  <th colSpan={3} className="grp sep">与打卡口径的偏离</th>
                  <th colSpan={3} className="grp sep">偏离分档</th>
                </tr>
                <tr>
                  <th>姓名</th><th>部门</th><th>归属</th><th>岗位</th><th>班型</th>
                  <th className="sep pay">上报工时</th><th className="pay">工资</th>
                  <th className="pay">管理费</th><th className="pay">应付合计</th>
                  <th className="sep">重算工时</th><th>偏离</th><th>偏离金额</th>
                  <th className="sep">△少记 日/时</th><th>○弹性内</th><th>⚠异常多记 日/时</th>
                </tr>
              </thead>
              {/* 金额优先取结算表自己填的（那才是付出去的钱）；表里没有金额列时才回落到按合同价重算，
                  并在 title 里标明来源，免得两种口径混在一列里看不出区别。
                  合同价缺档的人：按合同价应付是空，有结算表金额就照显示结算表的，没有就显示 —
                  ⚠ 行底色只讲**工时**（红＝异常多记、黄＝少记）；**钱**的问题标在「应付合计」这一格上，
                     两类问题各归各的列，别混成一种颜色（需求方 2026-08-23：逐人这里也要看得出单价不符） */}
              <tbody>{page(ppl, pgPpl).map((p, i) => {
                const 有表 = p.表上合计 != null
                const 缺档 = p.应付合计 == null
                const 不符 = (p.单价不符 || []).length > 0
                const 工资 = 有表 ? p.表上工资 : p.应付工资
                const 管理费 = 有表 ? p.表上管理费 : p.应付管理费
                const 合计 = 有表 ? p.表上合计 : p.应付合计
                // 不符与缺档可以同时成立（白班对不上、夜班压根没登记）：两句都要讲，别二选一
                const 差价额 = 不符 ? p.单价不符.reduce((a, g) => a + (g.金额 || 0), 0) : 0
                const 不符详 = 不符 ? `⚠ 与合同价不符——${p.单价不符.map(g => `${g.项目}：表上 ${g.表上}、合同 ${g.合同}（${g.差 > 0 ? '多' : '少'} ${Math.abs(g.差)}）`).join('；')}`
                  + (Math.abs(差价额) > 0.005 ? `。单价算错${差价额 > 0 ? '多付' : '少付'} ${y0(Math.abs(差价额))}` : '') : ''
                const 缺档详 = 缺档 ? `△ 合同价缺档：${p.应付单价来源}（这个人的应付算不出来）` : ''
                const 提示 = [不符详, 缺档详].filter(Boolean).join('　') ||
                  (有表 ? `结算表金额；按合同价应付 ${y0(p.应付合计)}（${p.应付单价来源}）`
                        : `结算表无金额列，此处为按合同价重算：${p.应付单价来源}`)
                const fmt = v => v == null ? '—' : y0(v)
                return (
                <tr key={i} style={{ background: p.异常多记日次 > 0 ? '#fef2f2' : p.少记日次 > 0 ? '#fffbe6' : '' }}>
                  <td>{p.姓名}</td><td>{p.部门}</td><td>{p.归属}</td><td>{p.岗位}</td><td>{p.班型}</td>
                  <td className="sep pay">{h1(p.上报总工时)}</td>
                  <td className="pay">{fmt(工资)}</td><td className="pay">{fmt(管理费)}</td>
                  <td className="pay" title={提示}
                      style={{ fontWeight: 700, background: 不符 ? '#fef2f2' : 缺档 ? '#fffbeb' : '',
                               color: 不符 ? '#b91c1c' : (缺档 && !有表) ? '#92400e' : '' }}>
                    {fmt(合计)}{!有表 && 合计 != null && <span style={{ color: 'var(--ink-3)' }}>*</span>}
                    {不符 && <span style={{ color: '#b91c1c', fontWeight: 700, fontSize: 11 }}> ⚠ 单价不符
                      {Math.abs(差价额) > 0.005 && <> {差价额 > 0 ? '多付' : '少付'} {y0(Math.abs(差价额))}</>}</span>}
                    {缺档 && <span style={{ color: '#92400e', fontWeight: 400, fontSize: 11 }}> 缺合同价</span>}</td>
                  <td className="sep">{h1(p.重算总工时)}</td>
                  <td style={{ color: p.差异 > 0 ? '#92400e' : p.差异 < 0 ? '#b91c1c' : '' }}>{(p.差异 > 0 ? '+' : '') + h1(p.差异)}</td>
                  <td>{y0(p.差额金额)}</td>
                  <td className="sep">{p.少记日次} / {h1(p.少记小时)}</td>
                  <td>{p.弹性内多记日次}</td>
                  <td style={{ fontWeight: p.异常多记日次 > 0 ? 700 : 400 }}>{p.异常多记日次} / {h1(p.异常多记小时)}</td>
                </tr>)})}</tbody>
            </table>
            {!ppl.length && <div style={{ padding: 20, color: 'var(--ink-3)' }}>没有符合筛选条件的人。</div>}
            <Pager total={ppl.length} pg={pgPpl} on={setPgPpl} />
          </div>
        </>}

        {/* ⑤ 逐日明细 */}
        {step === 'daily' && has && <>
          <PeopleFilter people={res.rows || []} pf={df}
                        on={v => { setDf(v); setPgRow(x => ({ ...x, page: 1 })) }}
                        shown={rows.length} total={(res.rows || []).length} unit="人日"
                        extra={[['all', '全部'], ['issue', '仅不一致'], ['over_out', '⚠撑不起上报'],
                                ['hard', '⚠报了工时无打卡'], ['unbilled', '◇未计工时'],
                                ['thin', '△仅1次卡'], ['mixed', '◇白夜混合'], ['ok', '✓撑得住']]
                          .filter(([k]) => k === 'all' || k === 'issue' || (bandCount[k] || 0) > 0)
                          .map(([k, l]) =>
                            <button key={k} className={'btn' + (filter === k ? ' primary' : '')}
                              onClick={() => pickFilter(k)}>
                              {l} <b style={{ opacity: .75 }}>{bandCount[k] || 0}</b>
                            </button>)} />
          <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.9, marginBottom: 10 }}>
            「金额」＝ <b>差异 × 单价</b>。这里的单价是<b>该人结算表上实际套用的含管理费单价</b>（公司就是按它付的，
            所以多记 1 小时就多付这么多）；结算表没有单价列时才退到合同价，来源悬停可见。
            {单价不符者.size > 0 && <span style={{ color: '#b91c1c', fontWeight: 600 }}>
              　⚠ 其中 {单价不符者.size} 人的单价与合同价对不上（单价列标红）——他们这些天的金额是<b>按错的价</b>算出来的，
              先到<b>第③步</b>把单价定了再看这一列。</span>}
          </div>
          {/* 批量确认：撑不起上报/待查这些异常常常同一个原因（例：一批夜班跨零点、打卡属实），
              逐行点、逐行抄同一句理由没意义。勾一批、一句理由，跟⑦合同外调整一个做法。 */}
          {dailySel.length > 0 && <div className="ta-batch">
            <div className="ln">
              <b>已选 {dailySel.length} 项</b>
              {dailyNew.length > 0 && dailyOld.length > 0 && <span className="note">
                （{dailyNew.length} 待确认 · {dailyOld.length} 已确认）</span>}
              <button className="btn link" onClick={() => setRowSel({})}>取消选择</button>
            </div>
            <div className="ln">
              {dailyNew.length > 0 && <input value={rowWhy} onChange={e => setRowWhy(e.target.value)}
                placeholder="这一批为什么不是问题？例：这些夜班跨零点，打卡属实、工时无误"
                onKeyDown={e => { if (e.key === 'Enter' && rowWhy.trim()) runDaily('认定') }} />}
              {dailyNew.length > 0 && <button className="btn primary" disabled={busy || !rowWhy.trim()}
                onClick={() => runDaily('认定')}>确认无误（{dailyNew.length} 项）</button>}
              {dailyOld.length > 0 && <button className="btn" disabled={busy}
                onClick={() => runDaily('撤销')}>撤销确认（{dailyOld.length} 项）</button>}
            </div>
          </div>}
          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table className="tbl">
              <thead><tr>
                <th style={{ width: 34 }}>{dConfRows.length > 0 &&
                  <input type="checkbox" title="全选当前筛出的可认定行"
                    checked={dConfRows.every(r => rowSel[dKey(r)])}
                    onChange={e => setRowSel(e.target.checked
                      ? Object.fromEntries(dConfRows.map(r => [dKey(r), true])) : {})} />}</th>
                <th>姓名</th><th>部门</th><th>归属</th><th>班型</th><th>日</th><th>上班</th>
                <th title="上下班之外的打卡（午休、宵夜等）。不参与在厂时长计算，摆出来是为了让这一行读得通——否则「上班19:51→下班次日08:32、次数4」中间那两张去哪了只能猜">无效卡</th>
                <th>下班</th>
                <th>次数</th><th>跨度</th><th>上报</th><th>重算</th><th>差异</th>
                <th>单价</th><th>金额</th><th>判定</th><th></th>
              </tr></thead>
              <tbody>{page(rows, pgRow).map((r, i) => (
                <tr key={i} style={{ background: BAND[r.档]?.bg }}>
                  <td>{dConf(r) && <input type="checkbox" checked={!!rowSel[dKey(r)]}
                    onChange={e => setRowSel(o => ({ ...o, [dKey(r)]: e.target.checked }))} />}</td>
                  <td>{r.姓名}</td><td>{r.部门 || '—'}</td><td>{r.归属}</td><td>{r.班型}</td><td>{r.日}</td>
                  <td>{r.上班打卡 || '—'}</td>
                  <td style={{ color: 'var(--ink-3)', fontSize: 12, whiteSpace: 'nowrap' }}
                      title={(r.无效卡 || []).length ? '上下班之外的打卡，不参与在厂时长' : ''}>
                    {(r.无效卡 || []).join(' ') || '—'}</td>
                  <td>{r.下班打卡 || '—'}</td>
                  <td>{r.打卡次数}</td><td>{r.跨度 == null ? '—' : h1(r.跨度)}</td>
                  <td>{h1(r.上报工时)}</td><td>{h1(r.重算工时)}</td>
                  <td>{(r.差异 > 0 ? '+' : '') + h1(r.差异)}</td>
                  {(() => {
                    const 不符 = 单价不符者.has(`${r.姓名}|${r.归属}`)
                    return <td title={(r.单价来源 || '') + (不符 ? `　⚠ 这个人的单价与合同价对不上，见第${stepNo('people')}步逐人核对` : '')}
                               style={{ color: !r.单价 ? '#92400e' : 不符 ? '#b91c1c' : '', fontWeight: 不符 ? 700 : 400, whiteSpace: 'nowrap' }}>
                      {r.单价 ? h1(r.单价) : '—'}{不符 && ' ⚠'}</td>
                  })()}
                  <td>{y0(r.金额影响)}</td>
                  <td style={{ color: r.已认定 ? 'var(--ink-3)' : BAND[r.档]?.color, whiteSpace: 'nowrap' }}>
                    {r.判定}
                    {r.已认定 && <span title={`${r.已认定.认定人} · ${r.已认定.时间}　理由：${r.已认定.理由}`}
                      style={{ color: 'var(--ok, #15803d)' }}>　✓已认定</span>}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {['over_out', 'hard', 'thin'].includes(r.档) && (r.已认定
                      ? <button className="btn link" disabled={busy}
                          onClick={() => doUndo({ 类型: r.档 === 'over_out' ? '多记' : '待查', 键: `${r.姓名}|${r.归属}|${r.日}` })}>撤销</button>
                      : <button className="btn link" disabled={busy}
                          onClick={() => askAck({ 类型: r.档 === 'over_out' ? '多记' : '待查', 键: `${r.姓名}|${r.归属}|${r.日}`,
                            可长期: false, 标题: `${r.姓名} ${r.日} 日 ${r.判定}`,
                            说明: `打卡 ${r.上班打卡 || '—'} → ${r.下班打卡 || '—'}，跨度 ${h1(r.跨度)}，上报 ${h1(r.上报工时)}、重算 ${h1(r.重算工时)}。` })}>确认无误</button>)}
                  </td>
                </tr>))}</tbody>
            </table>
            <Pager total={rows.length} pg={pgRow} on={setPgRow} />
          </div>
        </>}

        {/* ⑦ 合同外调整：奖 / 罚 / 蒸练补贴，原先挤在复核结论页下半截 */}
        {step === 'adj' && has && <AdjTable st={st} month={month} busy={busy} onBatch={doAdjBatch}
          sign={adjSign} signOk={adjSignOk} onSign={doAdjSignMain} onUndo={undoAdjSignMain}
          nextName={`第${stepNo('concl')}步 复核结论`} />}

        {/* ⑧ 复核结论：要等⑦合同外调整确认后才开 */}
        {step === 'concl' && has && !adjSignOk && <div className="card" style={{ padding: 24, lineHeight: 2 }}>
          <b>🔒 复核结论要等第{stepNo('adj')}步「合同外调整」确认后才开</b>
          <div className="note" style={{ marginTop: 6 }}>
            {adjSign
              ? <>第{stepNo('adj')}步确认过一次，<b>但之后奖惩数据变了</b>，那枚确认已作废。请回去重新确认。</>
              : <>奖 / 罚 / 补贴是全表唯一没有对照源的钱，得先由人签一句「核过」，
                  才谈得上看复核结论、出成本汇总。请先回第{stepNo('adj')}步「合同外调整」确认。</>}
          </div>
          <button className="btn primary" style={{ marginTop: 12 }} onClick={() => setStep('adj')}>
            去第{stepNo('adj')}步合同外调整 →
          </button>
        </div>}
        {step === 'concl' && has && adjSignOk && <>
          <Conclusion res={res} st={st} month={month} />
          <SignOff rec={signoff} ok={signOk} month={month} busy={busy}
            总={res?.settle?.合计 || {}} onSign={doSign} onUndo={undoSign}
            stepName={`第${stepNo('cost')}步 用工成本汇总`} />
        </>}

        {/* ⑨ 用工成本汇总：没确认之前不给看。
            这不是防谁，是防「复核还没做完，成本表已经被复制到群里」——
            这一页的数是要往上报的，签了字再出来。 */}
        {/* ⑨ 要⑦⑧两道都签。撤销⑦会让⑧失效但 signoff 仍在库里(signOk 可能仍 true)，
            所以这里显式再判 adjSignOk——上游任一未签都锁，先把用户送回缺的那一步。 */}
        {step === 'cost' && has && !adjSignOk && <div className="card" style={{ padding: 24, lineHeight: 2 }}>
          <b>🔒 用工成本汇总要先在第{stepNo('adj')}步确认奖惩</b>
          <div className="note" style={{ marginTop: 6 }}>
            上游的第{stepNo('adj')}步「合同外调整」还没确认（或撤销后奖惩数据变了）。
            这一页的数是要往上报的，得先把奖惩认过、再把复核结论确认，才出。
          </div>
          <button className="btn primary" style={{ marginTop: 12 }} onClick={() => setStep('adj')}>
            去第{stepNo('adj')}步合同外调整 →
          </button>
        </div>}
        {step === 'cost' && has && adjSignOk && !signOk && <div className="card" style={{ padding: 24, lineHeight: 2 }}>
          <b>🔒 用工成本汇总要等复核结论确认后才出</b>
          <div className="note" style={{ marginTop: 6 }}>
            {signoff
              ? <>本期确认过一次（{signoff.确认人}　{signoff.时间}），<b>但之后结果变了</b>
                  ——重跑过或改了口径/合同价，那枚确认已经作废。请回第{stepNo('concl')}步重新看过再确认。</>
              : <>这一页的数（各家用工成本、平均元/h）是要发群里、往上报的。
                  请先回第{stepNo('concl')}步把复核结论看完，点「确认无误」。</>}
          </div>
          <button className="btn primary" style={{ marginTop: 12 }} onClick={() => setStep('concl')}>
            去第{stepNo('concl')}步复核结论 →
          </button>
        </div>}

        {step === 'cost' && has && signOk && adjSignOk && <>
          <CostNote c={res?.用工成本} month={month} />
          <div className="card" style={{ padding: 20 }}>
            <div style={{ fontWeight: 600, marginBottom: 10 }}>导出《临时工考勤核对报告》</div>
            <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 2, marginBottom: 16 }}>
              八个页签：①核对概览　②复核结论（请款金额 vs 按合同价应付，页尾带用工成本汇总）　③逐人核对　
              ④逐日核对　⑤口径参数（含合同价表）　⑥表外临时工　⑦合同外调整　
              <b>⑧打卡原始表</b>（结算名单内每人的逐日打卡时刻，未加工，供逐人对照）。<br />
              <b>异常行标底色</b>：红＝⚠异常（含超弹性多记）、黄＝△少记／待核、浅蓝＝○弹性内多记。<br />
              <b>工时计算列全部保留公式</b>，并引用⑤页参数——改参数或改打卡时刻，数与底色一起重算，
              需求方拿去能继续往下核，而不是拿到一堆死值。
            </div>
            <button className="btn primary" onClick={doExport} disabled={busy}>
              {busy ? '生成中…' : `导出 ${month || ''} 核对报告`}
            </button>
          </div>
        </>}

        {/* 表外临时工不再单独占一步（使用者 2026-08-29：「合同外调整替换掉现在的表外临时工」）。
            但它不是空表——2026-05 实测 28 人，只是 6/7 两月恰好为 0，删掉等于把一道真会响的检查丢了。
            它查的是「有打卡、却没在结算名单上」，本来就跟同名重复计费、归属不符同类，故并进④结算风险。 */}
        {step === 'risk' && has && (res.outsiders || []).length > 0 && <div className="card" style={{ padding: 0, marginTop: 14 }}>
          <div style={{ padding: 14, color: 'var(--ink-2)', fontSize: 12.5, lineHeight: 1.9 }}>
            <b>表外临时工 · {(res.outsiders || []).length} 人</b>　
            打卡表里标着「临时普工」、本月有打卡、却不在结算名单上的人。
            打卡表对临时工的部门只写到派遣方，看不出车间，所以这里<b>只提示、不下结论</b>——
            可能是别的车间的结算表，也可能是本表漏人，需人力确认。
          </div>
          <table className="tbl">
            <thead><tr><th>姓名</th><th>派遣方</th><th>考勤组</th><th>本月出勤天数</th><th>其中仅1-2次打卡</th></tr></thead>
            <tbody>{(res.outsiders || []).map((o, i) => (
              <tr key={i}><td>{o.姓名}</td><td>{o.派遣方}</td><td>{o.考勤组}</td>
                <td>{o.出勤天数}</td><td>{o['仅1-2次打卡天数']}</td></tr>))}</tbody>
          </table>
        </div>}

        {/* 还没核对就点了后面的步骤 */}
        {!has && !['import', 'rule'].includes(step) && <div className="card" style={{ padding: 24, color: 'var(--ink-3)' }}>
          还没有核对结果。请先回第①步上传两张表并点「开始核对」。
        </div>}
      </div>
    </div>
  )
}

// ── 小组件 ────────────────────────────────────────────────
// 表格分页：size=0 表示「全部」。全厂一个月一千多个人日，一屏铺完既难看也卡。
const page = (list, pg) => pg.size ? list.slice((pg.page - 1) * pg.size, pg.page * pg.size) : list

/** 结论表的一行。明细 / 派遣方小计 / 全表合计 三种行长得一样，只差前四格，故合成一个组件——
 *  早先三处各写一遍，改列时漏掉一处就会串列（合计行的补贴格曾经写成了 `{r => 0}`）。 */
function Crow({ r, has, cls, label, open, onToggle, sub, nested }) {
  const bad = Math.abs(r.应付偏差 || 0) > 0.01
  return (
    <tr className={cls} style={{ background: !cls && bad ? '#fef2f2' : '' }}
      onClick={onToggle} title={onToggle ? (open ? '收起明细' : '点开看业务线 × 岗位明细') : undefined}>
      {label === '全表合计'
        ? <><td colSpan={3}>{label}</td><td>{r.人数}</td></>
        : onToggle
          ? <><td><span className="tw">{open ? '▾' : '▸'}</span>{r.归属}</td>
              <td colSpan={2}>{label}<span className="note">（{sub} 行明细）</span></td><td>{r.人数}</td></>
          : label
            ? <><td>{r.归属}</td><td colSpan={2}>{label}</td><td>{r.人数}</td></>
            : <><td className={nested ? 'ind' : ''}>{nested ? '' : r.归属}</td>
                <td>{r.部门}</td><td>{r.岗位}</td><td>{r.人数}</td></>}
      <td className="sep">{h1(r.上报白班工时)}</td><td>{h1(r.上报夜班工时)}</td><td>{h1(r.上报总工时)}</td>
      {has && <>
        <td className="sep pay">{r.表上工资 == null ? '—' : y0(r.表上工资)}</td>
        <td className="pay">{r.表上管理费 == null ? '—' : y0(r.表上管理费)}</td>
        <td className="pay">{r.补贴奖罚 ? y0(r.补贴奖罚) : '—'}</td>
        <td className="pay" style={{ fontWeight: 700 }}>{r.表上合计 == null ? '—' : y0(r.表上合计)}</td>
      </>}
      {/* 按合同价应付：缺档就是 —，不拿别的价算一个出来 */}
      <td className="sep">{r.应付工资 == null ? '—' : y0(r.应付工资)}</td>
      <td>{r.应付管理费 == null ? '—' : y0(r.应付管理费)}</td>
      <td>{r.应付合计 == null ? '—' : y0(r.应付合计)}</td>
      {has && <td style={{ fontWeight: bad ? 700 : 400, color: bad ? '#b91c1c' : '' }}>
        {r.应付偏差 == null ? '—' : y0(r.应付偏差)}</td>}
      {/* 结论由工具给，不让人看着「偏差 ¥0」自己去推断——而且结论不只看偏差，
          单价不符、奖罚异常、超弹性多记、同名重复计费都会把这一格判成异常。
          第三态「待核」＝没发现问题但合同价缺档、应付没法核——不能并进「正常」 */}
      <td className={'sep vd ' + (r.结论 === '异常' ? 'bad' : r.结论 === '待核' ? 'wait' : 'ok')}>
        {r.结论 === '异常' ? '⚠ 异常' : r.结论 === '待核' ? '△ 待核' : '✓ 正常'}
        {(r.异常原因 || []).length > 0 &&
          <div className="why">{r.异常原因.join('；')}</div>}
        {!!r.已认定 && <div className="ak">另有 {r.已认定} 项已认定</div>}
      </td>
    </tr>
  )
}

function Conclusion({ res, st, month }) {
  const se = res.settle || {}
  const rows = se.明细 || []
  const t = se.合计 || {}
  const chk = st.金额核对 || []
  const adjT = st.合同外调整合计 || {}
  const hasTable = (st.有表上金额 || 0) > 0
  const dev = rows.filter(r => Math.abs(r.应付偏差 || 0) > 0.01)
  const rc = st.单价核对 || {}
  // 横幅跟下表同一个口径：**以后端 settle.合计.结论 为准**，页面不再自己另算一套（早先横幅说「✓ 自查通过」、
  // 下表却有「⚠ 异常」，两处打架）。chk/rc/dev 只用来把原因讲具体；已认定的自查项不再算问题
  const chkOpen = chk.filter(c => !c.已认定)
  const hasProblem = t.结论 === '异常'
  const subs = se.派遣方小计 || []
  const byAg = {}
  rows.forEach(r => (byAg[r.归属] || (byAg[r.归属] = [])).push(r))
  const orphan = rows.filter(r => !subs.some(x => x.归属 === r.归属))
  const [open, setOpen] = useState({})
  const tog = a => setOpen(o => ({ ...o, [a]: !o[a] }))
  const allOpen = subs.length > 0 && subs.every(x => open[x.归属])
  return (
    <div className="card ta-concl">
      <div className="hd">复核结论 · {month || '本期'}</div>
      <div className="lead">
        每家派遣方、每条业务线本期该付多少。<b>主列是结算表的金额，也就是各家请款单上的数</b>；
        右侧「按合同价应付」是把尺子——上报工时 × 成本会计登记的合同价——用来量人力有没有按合同的价算，
        <b>本身不是要付的钱</b>。（2026-08-19 实测：五家的请款金额与本表结算列逐家分毫不差。）
        工资与管理费分开列，合同里本就是两条。工时偏离另在第④⑤步看，两者不混在一起。
        结论三态：<b>正常</b>＝各项都对得上；<b className="warn">异常</b>＝有任何一项对不上；
        <b style={{ color: '#92400e' }}>待核</b>＝没发现问题，但合同价没登记、应付没法核。
      </div>

      <div className="verdict">
        {!hasTable
          ? <span className="note">本期结算表里没有金额列（可能是按派遣方拆出来的简表），
              下表只有<b>按合同价重算</b>的金额，没有请款金额可比对——重算值仅供参照，别直接拿去付款。</span>
          : hasProblem
            ? <span className="bad">⚠ {[
                chkOpen.length > 0 && `结算表自查有 ${chkOpen.length} 处对不上`,
                (rc.不符 || 0) > 0 && `单价与合同价 ${rc.不符} 格不符`,
                dev.length > 0 && `${dev.length} 个「派遣方×业务线」格子结算与按合同价应付有偏差`,
                ...((t.异常原因 || []).filter(w => !/合同价缺档|结算与按合同价|明细格/.test(w))),
              ].filter(Boolean).join('；')}，见下表「结论」列。
              {(t.待核派遣方 || []).length > 0 &&
                <span style={{ color: '#92400e', fontWeight: 400 }}>　另有 {t.待核派遣方.join('、')} 合同价缺档，待核。</span>}</span>
            : t.结论 === '待核'
              ? <span className="wait">△ 本期请款合计 <b className="big">{y0(t.表上合计)}</b>
                  （工资 {y0(t.表上工资)} ＋ 管理费 {y0(t.表上管理费)}），按派遣方分 {(se.派遣方小计 || []).length} 笔。
                  结算表内部自洽（金额＝工时×表上单价、勾稽平），
                  <b>但 {(t.待核派遣方 || []).join('、') || '部分派遣方'} 的合同价没登记，应付无法按合同价核对</b>——
                  这部分是「待核」，不是「正常」。请成本会计到第③步「合同价（成本会计维护）」登记后点「按当前参数重跑」。
                  {(t.异常派遣方 || []).length > 0 &&
                    <b className="warn">　⚠ {t.异常派遣方.join('、')} 另有需要说明的事项，见下表「结论」列。</b>}</span>
              : <span className="ok">✓ 本期应付合计 <b className="big">{y0(t.表上合计)}</b>
                  （工资 {y0(t.表上工资)} ＋ 管理费 {y0(t.表上管理费)}），按派遣方分 {(se.派遣方小计 || []).length} 笔请款。
                  自查通过：单价与合同价一致、金额＝工时×单价、内部勾稽平。
                  {(t.异常派遣方 || []).length > 0 &&
                    <b className="warn">　⚠ 但 {t.异常派遣方.join('、')} 本期有需要说明的事项，见下表「结论」列。</b>}
                  {(() => {
                    if (t.应付合计 == null) return null
                    const d = (t.表上合计 || 0) - (t.应付合计 || 0)
                    if (Math.abs(d) < 0.01) return <>与按合同价应付的 {y0(t.应付合计)} 分毫不差。</>
                    return Math.abs(t.补贴奖罚 || 0) > 0.01
                      ? <>按「上报工时 × 合同价」是 {y0(t.应付合计)}，相差 <b>{y0(d)}</b>——正是补贴/奖/罚，
                          不是单价或工时问题。</>
                      : <>按合同价应付是 {y0(t.应付合计)}，相差 <b>{y0(d)}</b>，原因待查。</>
                  })()}</span>}
        {hasTable && (adjT.异常 > 0 || adjT.存疑 > 0) &&
          <div style={{ marginTop: 8 }}>
            <span className="bad">⚠ 奖/罚/补贴另有 {adjT.异常} 笔异常
              {adjT.存疑 > 0 && <>、{adjT.存疑} 笔存疑</>}</span>
            <span className="note">——这三项没有对照源，金额本身工具验不了，逐笔明细在第{stepNo('adj')}步「合同外调整」。</span>
          </div>}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <span className="note">一家派遣方一行，点行展开它的业务线 × 岗位明细</span>
        <button className="btn link" onClick={() => setOpen(allOpen ? {} :
          Object.fromEntries(subs.map(x => [x.归属, true])))}>
          {allOpen ? '收起全部' : `展开全部（${rows.length} 行明细）`}
        </button>
      </div>
      <div className="wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th colSpan={4} className="grp">派遣方 × 业务线 × 岗位</th>
              <th colSpan={3} className="grp sep">上报工时</th>
              {hasTable && <th colSpan={4} className="grp sep pay">结算表应付 ＝ 请款金额</th>}
              <th colSpan={3} className="grp sep">按合同价应付（校验尺）</th>
              {hasTable && <th className="grp">偏差</th>}
              <th className="grp sep">结论</th>
            </tr>
            <tr>
              <th>归属（派遣方）</th><th>部门（业务线）</th><th>岗位</th><th>人数</th>
              <th className="sep">白班</th><th>夜班</th><th>合计</th>
              {hasTable && <><th className="sep pay">工资</th><th className="pay">管理费</th>
                <th className="pay">补贴/奖/罚</th><th className="pay">应付合计</th></>}
              <th className="sep">工资</th><th>管理费</th><th>合计</th>
              {hasTable && <th>结算−合同价</th>}
              <th className="sep">正常 / 异常</th>
            </tr>
          </thead>
          <tbody>
            {/* 一家派遣方一行，点开才看它下面的业务线 × 岗位明细。
                原来是「全部明细在上、小计在下」，六月 21 行明细压着 8 行小计，
                要对某一家的请款单得先在明细里翻半天才找到它的小计（使用者 2026-08-29 提的）。
                ⚠ 兜底：万一某条明细的归属不在小计名单里（理论上不会，roll 是按归属分的），
                   也要摆出来，不能因为分不到组就从表里消失。 */}
            {subs.map((r, i) => <React.Fragment key={'s' + i}>
              <Crow r={r} has={hasTable} cls="sub" label="派遣方小计"
                sub={(byAg[r.归属] || []).length}
                open={!!open[r.归属]} onToggle={() => tog(r.归属)} />
              {open[r.归属] && (byAg[r.归属] || []).map((d, j) =>
                <Crow key={'d' + j} r={d} has={hasTable} nested />)}
            </React.Fragment>)}
            {orphan.map((r, i) => <Crow key={'o' + i} r={r} has={hasTable} />)}
            <Crow r={t} has={hasTable} cls="tot" label="全表合计" />
          </tbody>
        </table>
      </div>

      {chk.length > 0 && <>
        <div className="hd2">结算表自查明细（{chk.length} 处）</div>
        <div className="wrap">
          <table className="tbl">
            <thead><tr><th>姓名</th><th>归属</th><th>岗位</th><th>类型</th><th>项目</th>
              <th>表上</th><th>应为</th><th>差</th><th>说明</th></tr></thead>
            <tbody>{chk.slice(0, 200).map((c, i) => (
              <tr key={i} style={{ background: '#fef2f2' }}>
                <td>{c.姓名}</td><td>{c.归属}</td><td>{c.岗位}</td><td>{c.类型}</td><td>{c.项目}</td>
                <td>{c.表上}</td><td>{c.应为}</td><td style={{ fontWeight: 700 }}>{c.差}</td>
                <td style={{ textAlign: 'left' }}>{c.说明}</td>
              </tr>))}</tbody>
          </table>
        </div>
      </>}

      <div className="foot">
        「结算表应付」＝ 人力汇总表右侧金额列原样带出，一个字没改，就是各家请款单的金额。<br />
        「按合同价应付」＝ 上报白班工时 × 白班合同价 ＋ 上报夜班工时 × 夜班合同价，工资与管理费分开算；
        合同价取「派遣方 × 岗位 × 班次」，来自第③步成本会计登记的合同价登记表，<b>没登记的格子留空、结论「待核」</b>。
        它只用来验人力有没有按合同的价算，<b>不含补贴/奖/罚</b>，所以两列有这类调整时本就该差一截，
        差额在「补贴/奖/罚」列里看得见，逐笔明细在第{stepNo('adj')}步。<br />
        <b>本工具只读不写账，不产生凭证</b>，实际付款以人力上报的汇总表与合同为准。
      </div>
    </div>
  )
}

/* 复核结论「确认无误」：第⑨步用工成本汇总的闸门。
   为什么要有这道闸——成本汇总那一页是**要发群里、往上报的数**（各家用工成本、平均元/h），
   复核还没做完就被复制走，事后再纠正比一开始晚出半天贵得多（使用者 2026-08-29 提的）。
   确认会连当时那一版的关键数字一起存（指纹）：之后重跑出不一样的数，这枚确认自动失效。
   不然改了金额它照样显示「已确认」，这个确认就等于没签。 */
function SignOff({ rec, ok, month, busy, 总, onSign, onUndo, stepName }) {
  const [seen, setSeen] = useState(false)
  const bad = 总.结论 === '异常'
  const 摘要 = <>本期共 <b>{总.人数 || 0}</b> 人，请款合计 <b>{y0(总.表上合计)}</b>，
    结论 <b style={{ color: bad ? '#b91c1c' : 'var(--ok, #15803d)' }}>{总.结论 || '—'}</b></>
  if (ok) return <div className="card ta-sign on">
    <b>✓ 本期复核结论已确认无误</b>
    <div className="who">{rec.确认人}　{rec.时间}{rec.摘要 ? `　${rec.摘要}` : ''}</div>
    <div className="note">{stepName}已开。若之后重跑或改了口径/合同价，数一变这枚确认自动失效，要重新看过再确认。</div>
    <button className="btn link-del" disabled={busy} onClick={onUndo}>撤销确认</button>
  </div>
  return <div className="card ta-sign">
    <b>确认无误 → 出{stepName}</b>
    <div className="note">
      {摘要}。<br />
      确认的是<b>上面这张复核结论表</b>：各家请款金额、按合同价应付、偏差与结论都看过了。
      确认之后才出{stepName}——那一页的数是要发群里、往上报的。
      {rec && <><br /><span className="warn">本期在 {rec.时间} 由 {rec.确认人} 确认过一次，
        但之后结果变了（重跑或改了口径/合同价），那枚确认已作废，需要重新确认。</span></>}
    </div>
    {bad && <div className="warn">
      ⚠ 本期结论是<b>异常</b>{(总.异常派遣方 || []).length > 0 && <>：{总.异常派遣方.join('、')}</>}。
      异常不一定是错——可能已经有说法了，但<b>确认就是替它签字</b>，请先逐条看过。
    </div>}
    {bad && <label className={'scope' + (seen ? ' on' : '')}>
      <input type="checkbox" checked={seen} onChange={e => setSeen(e.target.checked)} />
      <span>上面的异常我已逐条看过</span>
    </label>}
    <button className="btn primary" disabled={busy || (bad && !seen)} onClick={onSign}>
      {busy ? '提交中…' : `确认无误，出${stepName}`}
    </button>
  </div>
}

/* 合同外调整：奖 / 罚 / 蒸练补贴。原先挤在复核结论页下半截，2026-08-29 拆成第⑦步。
   拆出来不是为了好看：这一页跟上下两页的性质完全不同——
   复核结论比的是「表上的钱 vs 按合同价该给的钱」，两边都有据可依；
   这一页的三项**没有任何对照源**，工具只能列出来给人看，判不了对错。混在一页里容易被当成同一种「已核过」。 */
function AdjTable({ st, month, busy, onBatch, sign, signOk, onSign, onUndo, nextName }) {
  const adj = st.合同外调整 || []
  const adjT = st.合同外调整合计 || {}
  const [pf, setPf] = useState({ dept: '', agency: '', name: '' })
  const [lv, setLv] = useState('all')          // 级别：全部 / 异常 / 存疑 / 提示
  const [proj, setProj] = useState('')         // 项目：蒸练补贴 / 奖金 / 罚款…
  const [openOnly, setOpenOnly] = useState(false)
  const [sel, setSel] = useState({})           // 勾了哪几条
  const [why, setWhy] = useState('')
  // 认定的键就是后端的 `姓名|项目`（_apply_acks 里那个）。同一个人同一个项目有两笔时，
  // 这两笔共用一把钥匙、只能一起认定——这是认定表本来的设计，不是这一页新引入的。
  const K = a => `${a.姓名}|${a.项目}`

  // 本期奖惩「已核对」确认（状态与提交都在主组件，这里只渲染）——跟逐笔认定是两回事：
  // 逐笔管「某一笔像有问题但没事」，这个管「整期奖/罚/补贴我看过了」，
  // 一笔都没有时也能正向签「确认无奖惩」，且**没签就开不了第⑧步复核结论**（使用者 2026-08-29）。
  const signBar = (
    <div className={'card ta-sign' + (signOk ? ' on' : '')} style={{ marginTop: 12 }}>
      {signOk
        ? <>
          <b>✓ 本期奖惩已核对{adj.length ? '无误' : '（本期无奖惩）'}</b>
          <div className="who">{sign.确认人}　{sign.时间}{sign.摘要 ? `　${sign.摘要}` : ''}</div>
          <div className="note">{nextName || '复核结论'}已开。这是「核过」的正向记录，会跟着报告走；奖惩数据若重跑变了，这枚确认自动失效。</div>
          <button className="btn link-del" disabled={busy} onClick={onUndo}>撤销确认</button>
        </>
        : <>
          <b>确认{adj.length ? '本期奖惩已核对' : '本期无奖惩情况'} → 开{nextName || '复核结论'}</b>
          <div className="note">
            {adj.length
              ? <>本期 {adj.length} 笔（净额 {y0(adjT.净额)}）已逐笔看过、该附的单据都附了。奖 / 罚 / 补贴是全表唯一没有对照源的钱，先签这一句，才开{nextName || '复核结论'}。</>
              : <>本期结算表里没有奖 / 罚 / 补贴。点一下正向签一句——记的是「核过、确实没有」，不是「没看」；签了才开{nextName || '复核结论'}。</>}
            {sign && !signOk && <><br /><span style={{ color: '#b91c1c' }}>
              之前确认过（{sign.确认人}　{sign.时间}），但之后奖惩数据变了，那枚确认已失效，请重新确认。</span></>}
          </div>
          <button className="btn primary" disabled={busy} onClick={onSign}>
            {busy ? '提交中…' : (adj.length ? '确认已核对' : '确认无奖惩')}
          </button>
        </>}
    </div>
  )

  if (!adj.length) return <>
    <div className="card" style={{ padding: 22, lineHeight: 2 }}>
      <b style={{ color: '#15803d' }}>✓ 本期没有合同外调整</b>
      <div className="note">
        结算表里没有奖金、罚款、蒸练补贴这类合同价之外的钱。
        <br />本期应付＝工时 × 合同价，没有第二个来源需要另外找单据。
      </div>
    </div>
    {signBar}
  </>

  const list = adj.filter(a =>
    (!pf.dept || a.部门 === pf.dept) &&
    (!pf.agency || a.归属 === pf.agency) &&
    (!pf.name || String(a.姓名 || '').includes(pf.name)) &&
    (lv === 'all' || a.级别 === lv) &&
    (!proj || a.项目 === proj) &&
    (!openOnly || !a.已认定))
  const lvN = {}, projN = {}
  adj.forEach(a => {
    lvN[a.级别] = (lvN[a.级别] || 0) + 1
    projN[a.项目 || '（空）'] = (projN[a.项目 || '（空）'] || 0) + 1
  })
  const projs = Object.keys(projN).sort()
  // 小计。罚款是负数，同组里奖罚相抵会互相吃掉——所以只要这一组里有负数，
  // 就把「奖/补贴」和「罚」分开再写一遍，否则「合计 ¥0」看着像本期什么都没有。
  const sum = arr => arr.reduce((s, a) => s + (Number(a.金额) || 0), 0)
  const pos = arr => arr.filter(a => (Number(a.金额) || 0) > 0)
  const neg = arr => arr.filter(a => (Number(a.金额) || 0) < 0)
  const bySum = (arr, k) => Object.entries(arr.reduce((m, a) => {
    const g = a[k] || '（空）'; m[g] = (m[g] || 0) + (Number(a.金额) || 0); return m
  }, {})).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]))
  const 拆奖罚 = arr => neg(arr).length > 0 && pos(arr).length > 0
    ? <span className="note">（奖 / 补贴 {y0(sum(pos(arr)))}　罚 {y0(sum(neg(arr)))}）</span> : null
  const acked = adj.filter(a => a.已认定).length
  const extraOn = lv !== 'all' || !!proj || openOnly

  const picked = list.filter(a => sel[K(a)])
  const pickNew = [...new Set(picked.filter(a => !a.已认定).map(K))]
  const pickOld = [...new Set(picked.filter(a => a.已认定).map(K))]
  const picksum = picked.reduce((s, a) => s + (Number(a.金额) || 0), 0)
  const allOn = list.length > 0 && list.every(a => sel[K(a)])
  const run = async (act) => {
    const ok = await onBatch(act, act === '认定' ? pickNew : pickOld, why.trim())
    if (ok) { setSel({}); setWhy('') }
  }

  return <>
    <PeopleFilter people={adj} pf={pf} on={setPf} shown={list.length} total={adj.length} unit="笔"
      extraActive={extraOn}
      onClear={() => { setPf({ dept: '', agency: '', name: '' }); setLv('all'); setProj(''); setOpenOnly(false) }}
      extra={<>
        {[['all', '全部'], ['异常', '⚠ 异常'], ['存疑', '△ 存疑'], ['提示', '提示']]
          .filter(([k]) => k === 'all' || (lvN[k] || 0) > 0)
          .map(([k, l]) => <button key={k} className={'btn' + (lv === k ? ' primary' : '')}
            onClick={() => setLv(k)}>{l} <b style={{ opacity: .75 }}>{k === 'all' ? adj.length : lvN[k]}</b></button>)}
        {/* 项目按钮跟级别按钮同一排。**一种项目时也摆**——原来写成「多于一种才出现」，
            六月只有蒸练补贴，这个筛选就整个不见了（使用者：「还要加一个按项目筛选」）。 */}
        <span className="bands">
          <button className={'btn' + (proj === '' ? ' primary' : '')} onClick={() => setProj('')}>
            项目：全部 <b style={{ opacity: .75 }}>{adj.length}</b></button>
          {projs.map(v => <button key={v} className={'btn' + (proj === v ? ' primary' : '')}
            onClick={() => setProj(v)}>{v} <b style={{ opacity: .75 }}>{projN[v]}</b></button>)}
        </span>
        {acked > 0 && <label className={'scope' + (openOnly ? ' on' : '')}>
          <input type="checkbox" checked={openOnly} onChange={e => setOpenOnly(e.target.checked)} />
          <span>只看未确认</span>
        </label>}
      </>} />

    <div className="card ta-concl">
      <div className="hd">
        合同外调整 · {month || '本期'}：奖 / 罚 / 蒸练补贴（{adj.length} 笔，净额 {y0(adjT.净额)}）
        {(adjT.异常 + adjT.存疑) > 0 &&
          <span style={{ color: '#b91c1c', marginLeft: 8 }}>· {adjT.异常} 异常 / {adjT.存疑} 存疑</span>}
        {acked > 0 && <span style={{ color: 'var(--ok, #15803d)', marginLeft: 8 }}>· 已确认 {acked} 笔</span>}
      </div>
      <div className="lead">
        这三项是全表<b>唯一没有对照源的钱</b>——工时有打卡可比、单价有合同可比，奖罚补贴只有结算表这一处孤证。
        <b>工具验不了金额对不对</b>，只能逐笔列出来、验符号（罚 ≤ 0、奖 ≥ 0）、验占工资比例，
        金额本身要靠审批单 / 处罚通知去核。<b>过了检查 ≠ 这笔奖罚是对的。</b>
      </div>

      {/* 筛选范围小计：不勾也看得见「这一筛是多少钱」。
          复核的顺序通常是先按项目筛出一类、看这一类总共多少钱，再决定要不要整批认。 */}
      <div className="ta-adjsum">
        当前筛出 <b>{list.length}</b> 笔，金额合计 <b>{y0(sum(list))}</b> {拆奖罚(list)}
        {acked > 0 && <span className="note">
          　·　其中已确认 {list.filter(a => a.已认定).length} 笔 {y0(sum(list.filter(a => a.已认定)))}、
          未确认 {list.filter(a => !a.已认定).length} 笔 <b>{y0(sum(list.filter(a => !a.已认定)))}</b></span>}
        {projs.length > 1 && !proj && <div className="note">
          按项目：{bySum(list, '项目').map(([k, v]) => `${k} ${y0(v)}`).join('　·　')}</div>}
      </div>

      {/* 批量确认。这类东西是成批出现的——六月十四笔里十三笔是蒸练补贴、同一张审批单管全部，
          逐条点十四次、逐条抄同一句理由，人只会越抄越敷衍。理由一句管一批，写进每一条记录。 */}
      {picked.length > 0 && <div className="ta-batch">
        <div className="ln">
          <b>已选 {picked.length} 笔　合计 {y0(picksum)}</b> {拆奖罚(picked)}
          {pickNew.length > 0 && pickOld.length > 0 && <span className="note">
            （{pickNew.length} 笔未确认 {y0(sum(picked.filter(a => !a.已认定)))} ·
            {pickOld.length} 笔已确认 {y0(sum(picked.filter(a => a.已认定)))}）</span>}
          <button className="btn link" onClick={() => setSel({})}>取消选择</button>
        </div>
        {/* 签字之前要看得见「这一批到底多少钱、分在哪几家」——
            只报一个总数，付错家、某一家金额突然翻倍这类事看不出来 */}
        <div className="sub">
          按项目：{bySum(picked, '项目').map(([k, v]) => `${k} ${y0(v)}`).join('　·　')}
          <br />按派遣方：{bySum(picked, '归属').map(([k, v]) => `${k} ${y0(v)}`).join('　·　')}
        </div>
        <div className="ln">
          {pickNew.length > 0 && <input value={why} onChange={e => setWhy(e.target.value)}
            placeholder="这一批为什么不是问题？例：6月蒸练补贴已附审批单，逐笔核对无误"
            onKeyDown={e => { if (e.key === 'Enter' && why.trim()) run('认定') }} />}
          {pickNew.length > 0 && <button className="btn primary" disabled={busy || !why.trim()}
            onClick={() => run('认定')}>确认无误（{pickNew.length} 笔 {y0(sum(picked.filter(a => !a.已认定)))}）</button>}
          {pickOld.length > 0 && <button className="btn" disabled={busy}
            onClick={() => run('撤销')}>撤销确认（{pickOld.length} 笔）</button>}
        </div>
      </div>}

      <div className="wrap">
        <table className="tbl">
          <thead><tr>
            <th style={{ width: 34 }}><input type="checkbox" checked={allOn} title="全选当前筛出的"
              onChange={() => setSel(allOn ? {} : Object.fromEntries(list.map(a => [K(a), true])))} /></th>
            <th>级别</th><th>状态</th><th>姓名</th><th>归属</th><th>部门</th><th>岗位</th>
            <th>项目</th><th>金额</th><th>当月工资</th><th>占工资</th><th>说明</th>
          </tr></thead>
          <tbody>{list.slice(0, 300).map((a, i) => {
            const ak = a.已认定
            return <tr key={i} className={ak ? 'done' : ''}
              style={{ background: ak ? '' : a.级别 === '异常' ? '#fef2f2' : a.级别 === '存疑' ? '#fffbe6' : '' }}>
              <td><input type="checkbox" checked={!!sel[K(a)]}
                onChange={e => setSel(o => ({ ...o, [K(a)]: e.target.checked }))} /></td>
              <td style={{ fontWeight: (ak || a.级别 === '提示') ? 400 : 700, color: ak ? 'var(--ink-3)' : a.级别 === '异常' ? '#b91c1c' : a.级别 === '存疑' ? '#92400e' : '' }}>
                {!ak && (a.级别 === '异常' ? '⚠ ' : a.级别 === '存疑' ? '△ ' : '')}{a.级别}</td>
              <td style={{ color: ak ? 'var(--ok, #15803d)' : 'var(--ink-3)' }}>{ak ? '✓ 已确认' : '—'}</td>
              <td>{a.姓名}</td><td>{a.归属}</td><td>{a.部门}</td><td>{a.岗位}</td>
              <td>{a.项目}</td><td style={{ fontWeight: 600 }}>{y0(a.金额)}</td>
              <td>{y0(a.当月工资)}</td><td>{a.占工资 == null ? '—' : a.占工资 + '%'}</td>
              <td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{a.说明}
                {ak && <div style={{ color: 'var(--ok, #15803d)', fontSize: 11.5, marginTop: 2 }}>
                  ✓ {ak.认定人}　{ak.时间}　{ak.理由}</div>}</td>
            </tr>
          })}</tbody>
        </table>
      </div>
      {!list.length && <div style={{ padding: 16, color: 'var(--ink-3)' }}>当前筛选没有匹配的记录。</div>}
      {list.length > 300 && <div className="foot">页面只摆前 300 笔，全部 {list.length} 笔在导出报告的⑦合同外调整页。</div>}
      <div className="foot">
        「确认无误」记的是<b>谁、什么时候、因为什么</b>认为这一批不是问题，不是删除——条目仍在，
        下一期跑出来还会显示这枚确认。撤销随时可以。
      </div>
    </div>
    {signBar}
  </>
}

/* 一条结算风险。未认定的挂「确认无误」；已认定的显示认定信息并可撤销。
   两种状态用同一个组件渲染，免得改了一边忘了另一边。 */
function RiskRow({ x, acked, onAck, onUndo, busy }) {
  return (
    <div className={'risk-row' + (acked ? ' on' : '')}>
      <div className="main">{x.主}</div>
      <div className="sub">{x.说明}</div>
      {acked
        ? <div className="ackinfo">
            ✓ 已认定无误（{x.已认定.范围}）　{x.已认定.认定人} · {x.已认定.时间}
            <div className="why">理由：{x.已认定.理由}</div>
            <button className="btn link" onClick={() => onUndo(x)} disabled={busy}>撤销认定</button>
          </div>
        : <button className="btn" onClick={() => onAck(x)} disabled={busy}>确认无误 →</button>}
    </div>
  )
}

/* 合同价 vs 人力实际计价。第③步最该回答的问题：**人力到底有没有按合同的价算。**
   ⚠ 合同价取的是**本工具里成本会计维护的那张表**，不是汇总表表头解析出来的——
      表头那段是人力自己写的，拿它当基准就成了人力跟自己比，永远一致，核对白做。 */
function RateCheck({ c, has, rates }) {
  if (!has) return (
    <div className="ta-rchk none"><b>合同价 vs 人力实际计价</b>　
      还没有核对结果。先在第①步上传两张表（或从历史期次点开一期），这里才知道人力实际按什么价算的。</div>
  )
  // 老留档是加这项检查之前跑的，里面没有这段数据。不要静默留白——
  // 「按当前参数重跑」那颗按钮就在本页上方，直接告诉他点它
  if (!c) return (
    <div className="ta-rchk none"><b>合同价 vs 人力实际计价</b>　
      这一期的留档是加这项检查之前跑的，没有这段数据。点上方<b>「按当前参数重跑」</b>即可得到
      （用的是留档的原表，不必重新上传）。</div>
  )
  if (!c.有人力数据) return (
    <div className="ta-rchk none"><b>合同价 vs 人力实际计价</b>　
      本期结算表里没有单价/金额列（可能是按派遣方拆出来的简表），无从比对。</div>
  )
  const px = v => v ? `${v.员工工资} + ${v.管理费} = ${v.合计}` : '—'
  const used = (c.明细 || []).filter(x => x.人数 > 0)      // 本期没人用到的档不占版面，折进下面一句
  const bad = c.不符 || 0, miss = c.合同缺档 || 0
  const mid = rates?.期中调价 || []
  const hp = rates?.表头解析 || {}
  const hl = rates?.对外总价 || {}
  const pb = b => b ? `${b[0]} + ${b[1]} = ${Math.round((b[0] + b[1]) * 100) / 100}` : '—'
  return (
    <div className={'ta-rchk' + (bad ? ' bad' : miss ? ' wait' : ' ok')}>
      <b>合同价 vs 人力实际计价</b>
      {/* 三态与状态条同口径：不符＝红；缺档＝琥珀「待核」（不是"对不上"，是"没法核"）；全一致才绿 */}
      {bad
        ? <span className="tag bad">⚠ {bad} 格与合同价不符{miss ? `，另 ${miss} 格缺档待核` : ''}（一致 {c.一致} 格）</span>
        : miss
          ? <span className="tag wait">△ {miss} 格合同价缺档、待核（一致 {c.一致} 格）</span>
          : <span className="tag ok">✓ 本期用到的 {c.一致} 格全部一致</span>}
      {mid.length > 0 && <div className="miss">
        ⚠ <b>本期期中调价</b>：{mid.map(x => `${x.派遣方}·${x.岗位}（${(x.生效日 || []).join(' → ')}）`).join('；')}。
        本工具按整月一个价核，取覆盖期末那一行；调价当月的差异请人工复核。
      </div>}
      <div className="src">合同价取自：<b>{c.合同来源}</b>　·　
        人力实际取自结算表里每个人身上套用的单价列。<b>工资与管理费分开比</b>——
        16.5+2.5 与 17+2 合计都是 19，但在合同、入账、发票上是两条不同的线。</div>
      {c.合同缺档 > 0 && <div className="miss">
        <b>{c.合同缺档} 格「合同缺档」</b>——本期这几档人力照价算了钱，但
        <b>「合同价（成本会计维护）」页里没有覆盖本期的行</b>，所以<b>无从判断对错</b>。
        缺档不等于没问题，只等于<b>还没核</b>；请成本会计把这几行登记上，再回来看这一页。
      </div>}
      <div className="wrap"><table className="tbl">
        <thead><tr><th>派遣方</th><th>岗位</th><th>班次</th>
          <th>合同价（员工+管理费=合计）</th><th>人力实际</th><th>人数</th><th>工时</th><th>判定</th></tr></thead>
        <tbody>{used.map((x, i) => {
          const isMiss = x.状态 === '合同缺档'
          const isBad = x.状态 !== '一致' && !isMiss
          return <tr key={i} style={{ background: isBad ? '#fef2f2' : isMiss ? '#fffbeb' : '#f0fdf4' }}>
            <td><b>{x.派遣方}</b></td><td>{x.岗位}</td><td>{x.班次}</td>
            <td>{px(x.合同)}</td>
            <td style={{ fontWeight: isBad ? 700 : 400 }}>{px(x.人力)}</td>
            <td>{x.人数}</td><td>{h1(x.工时)}</td>
            <td className={isBad ? 'vbad' : isMiss ? 'vwait' : 'vok'}>
              {isBad ? '⚠ ' + x.状态.replace('⚠', '') : isMiss ? '△ 合同缺档（待核）' : '✓ 一致'}
              {x.差额 && <div className="why">
                差：员工 {x.差额.员工工资 > 0 ? '+' : ''}{x.差额.员工工资}、
                管理费 {x.差额.管理费 > 0 ? '+' : ''}{x.差额.管理费}、
                合计 {x.差额.合计 > 0 ? '+' : ''}{x.差额.合计}</div>}
              {x.说明 && <div className="why">{x.说明}</div>}
            </td>
          </tr>
        })}</tbody>
      </table></div>
      {c.本期无人 > 0 && <div className="src">另有 {c.本期无人} 格合同里有价、本期没人套用（不是问题，只是没用到），未列出。</div>}
      {/* 人力表头写的规则：只作参考，摆在最下面；它是被核对的一方，不是基准 */}
      {(Object.keys(hp).length > 0 || hl.day) && <details className="ref">
        <summary>参考：汇总表表头写的计价规则（人力自己写的，不作基准）</summary>
        <div className="src">
          {hl.day && <>表头写的合计价：白班 {hl.day} 元/小时·人、夜班 {hl.night} 元/小时·人。</>}
          {Object.entries(hp).map(([a, posts]) => Object.entries(posts || {}).map(([post, b]) =>
            <div key={a + post}>{a}·{post}：白班 {pb(b?.day)}｜夜班 {pb(b?.night)}</div>))}
          {(rates?.表头未解析行 || []).map((w, i) => <div key={'n' + i}>表头有一行没看懂：{w}</div>)}
        </div>
      </details>}
    </div>
  )
}

/* 合同价登记表：**每一行（派遣方 × 岗位）各自带生效日与失效日**。
   各家续签时间本来就不同——锦绣 8 月调价，不该逼着把华顺的价也重述一遍。
   失效日留空＝同一行下一条的生效日前一天；显式填是为了「合同到期不再续」那种推不出来的情况。 */
/* 合同价的导出／导入。
   导入分两步：先把「新增哪几行、覆盖哪几行、旧值→新值」摆出来，确认了才写。
   这张表是所有「钱对不对」的唯一基准，一次盲写可能把整月应付算错，
   而且错了页面上仍是一片绿——只是绿得不对。所以宁可多一步。 */
function ContractIO({ onDone }) {
  const [file, setFile] = useState(null)
  const [pre, setPre] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const preview = async (f) => {
    setFile(f); setPre(null); setMsg('')
    if (!f) return
    setBusy(true)
    const fd = new FormData(); fd.append('file', f)
    const r = await tempattContractImport(fd)
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '读不了这个文件'); return }
    setPre(r)
  }
  const apply = async () => {
    setBusy(true); setMsg('')
    const fd = new FormData(); fd.append('file', file)
    const r = await tempattContractImportApply(fd)
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '导入失败'); return }
    setMsg(`已写入 ${r.写入} 行。`)
    setPre(null); setFile(null)
    onDone && onDone(r)
  }
  const C = pre?.计数 || {}
  return <>
    <a className="btn" href={tempattContractExportUrl} style={{ padding: '4px 12px', fontSize: 12 }}>⇧ 导出 Excel</a>
    <label className="btn" style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}>
      ⇩ 导入
      <input type="file" accept=".xlsx" style={{ display: 'none' }}
        onChange={e => preview(e.target.files?.[0] || null)} />
    </label>
    {busy && <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>处理中…</span>}
    {msg && <span className="warn" style={{ fontSize: 12 }}>{msg}</span>}
    {pre && <div className="card" style={{ position: 'fixed', inset: '8% 12%', zIndex: 60, padding: 20,
      overflow: 'auto', boxShadow: '0 12px 48px rgba(0,0,0,.25)', background: 'var(--bg-1)' }}>
      <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>导入预览 —— 还没有写进去</div>
      <div style={{ fontSize: 12.5, lineHeight: 1.9, marginBottom: 10 }}>
        文件里 <b>{(pre.行 || []).length}</b> 行：
        新增 <b>{C['新增'] || 0}</b>　覆盖 <b className="warn">{C['覆盖'] || 0}</b>
        不变 {C['不变'] || 0}
        {(C['✗ 有问题'] || 0) > 0 && <b className="warn">有问题 {C['✗ 有问题']}</b>}
        <div style={{ color: 'var(--ink-3)' }}>
          键＝派遣方＋岗位＋生效日。<b>导入不删行</b>——系统里另有 {pre.保留未提及} 行文件没提到，会原样保留；
          要删请在下面表格里逐行删。
        </div>
        {(C['✗ 有问题'] || 0) > 0 &&
          <div className="warn">⚠ 有问题的行没法写。<b>整批都不会写</b>——半批写进去比不写更难收拾。先改好文件再导。</div>}
      </div>
      <div style={{ maxHeight: '52vh', overflow: 'auto' }}>
        <table className="tbl" style={{ fontSize: 12 }}>
          <thead><tr>
            <th>行</th><th>动作</th><th>派遣方</th><th>岗位</th><th>生效日</th>
            <th>白班</th><th>夜班</th><th>失效日</th><th>说明</th>
          </tr></thead>
          <tbody>
            {(pre.行 || []).map((x, i) => {
              const bad = x.动作.startsWith('✗')
              return <tr key={i} style={bad ? { background: '#fef2f2' }
                : x.动作 === '覆盖' ? { background: '#fffbeb' }
                : x.动作 === '新增' ? { background: '#f0fdf4' } : undefined}>
                <td>{x.行号}</td>
                <td><b>{x.动作}</b></td>
                <td>{x.派遣方}</td><td>{x.岗位}</td><td>{x.生效日}</td>
                <td>{x.旧 ? <>{x.旧.白班} <b>→ {x.新.白班}</b></> : x.新.白班}</td>
                <td>{x.旧 ? <>{x.旧.夜班} <b>→ {x.新.夜班}</b></> : x.新.夜班}</td>
                <td>{x.旧 ? <>{x.旧.失效日} <b>→ {x.新.失效日}</b></> : x.新.失效日}</td>
                <td className={bad ? 'warn' : ''}>{bad ? (x.问题 || []).join('；') : (x.新.备注 || '')}</td>
              </tr>
            })}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
        <button className="btn primary" disabled={busy || !pre.可写} onClick={apply}>
          {busy ? '写入中…' : `确认写入（新增 ${C['新增'] || 0}、覆盖 ${C['覆盖'] || 0}）`}
        </button>
        <button className="btn" onClick={() => { setPre(null); setFile(null) }}>取消</button>
        {!pre.可写 && <span className="warn" style={{ fontSize: 12, alignSelf: 'center' }}>
          {(C['✗ 有问题'] || 0) > 0 ? '先把有问题的行改好' : '没有要新增或覆盖的行'}
        </span>}
      </div>
    </div>}
  </>
}

function ContractRows({ c, month, busy, form, setForm, onSave, onDel, delRow, setDelRow, msg, onImported }) {
  const rows = c?.行 || []
  const mid = c?.期中调价 || []
  const bad = c?.问题行 || []
  const can = c?.可维护
  const px = b => b ? `${b[0]} + ${b[1]} = ${(b[0] + b[1]).toFixed(2).replace(/\.00$/, '')}` : '—'
  const f = (k, v) => setForm({ ...form, [k]: v })
  return (
    <div className="ta-ctr">
      <div className="hd" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span>合同价登记表</span>
        <span className="note">成本会计维护 · 每行自带生效日与失效日 · 记录录入人与时间</span>
        <span style={{ flex: 1 }} />
        <ContractIO onDone={onImported} />
      </div>

      {month && <div className={'apply' + (rows.some(r => r.本期生效) ? '' : ' warn')}>
        {rows.some(r => r.本期生效)
          ? <><b>{month}（{c.期间?.起} ~ {c.期间?.止}）适用 {rows.filter(r => r.本期生效).length} 行</b>
              ，下表中带底色的就是。没有任何行覆盖到的派遣方，会在「对比」页报「合同缺档」。</>
          : <>⚠ {month} 没有任何合同价行覆盖——「对比」页会把人力用到的每一档都报成「合同缺档（待核）」。</>}
      </div>}

      {mid.length > 0 && <div className="mid">
        ⚠ <b>{month} 期内换过价</b>：{mid.map(x => `${x.派遣方}·${x.岗位}（${x.生效日.join(' → ')}）`).join('；')}。
        本工具按<b>整月一个价</b>核对（人力结算表本身就是一人一价按月结的），取的是覆盖期末那一行。
        <b>跨调价日的月份请人工确认</b>。
      </div>}

      {bad.length > 0 && <div className="mid">
        ⚠ <b>区间有问题的行 {bad.length} 条</b>：
        {bad.map(x => `${x.派遣方}·${x.岗位} ${x.生效日} 起——${x.问题.join('；')}`).join('　|　')}。
        重叠会取错价，空档会取不到价，两种都得改。
      </div>}

      <div className="wrap"><table className="tbl">
        <thead>
          <tr>
            <th rowSpan={2}>派遣方</th><th rowSpan={2}>岗位</th>
            <th colSpan={2} className="sep">白班 / 夜班（员工+管理费=合计）</th>
            <th colSpan={2} className="sep">生效期间</th>
            <th rowSpan={2} className="sep">当前状态</th>
            <th rowSpan={2}>备注</th>
            <th rowSpan={2} className="sep">录入人 · 时间</th>
            {can && <th rowSpan={2}>操作</th>}
          </tr>
          <tr><th className="sep">白班</th><th>夜班</th><th className="sep">生效日</th><th>失效日</th></tr>
        </thead>
        <tbody>{rows.map(r => (
          <tr key={r.id} className={r.本期生效 ? 'on' : ''}
            /* 已失效的整行压灰：留着是因为历史期次要按当时的价核，但别让人误当成还在用的价 */
            style={r.当前状态 === '已失效' ? { background: '#f1f2f4', color: '#8a8f98' } : undefined}>
            <td><b>{r.派遣方}</b></td><td>{r.岗位}</td>
            <td className="sep">{px(r.day)}</td><td>{px(r.night)}</td>
            <td className="sep">{r.生效日}</td>
            <td>{r.实际失效日 || '至今'}
              <div className="note">{r.失效日来源}</div>
              {r.问题?.length > 0 && <div className="warn">⚠ {r.问题.join('；')}</div>}</td>
            <td className="sep">{
              r.当前状态 === '已失效' ? <b style={{ color: '#8a8f98' }}>✖ 已失效</b>
                : r.当前状态 === '未生效' ? <b style={{ color: '#b45309' }}>○ 未生效</b>
                  : <b style={{ color: '#15803d' }}>✔ 生效中</b>
            }<div className="note">{r.当前状态 === '未生效' ? `${r.生效日} 才起算` : (r.实际失效日 || '至今')}</div></td>
            <td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{r.备注 || '—'}</td>
            <td className="sep note">{r.录入人 || '—'}<br />{r.录入时间 || ''}</td>
            {can && <td className="act">
              {delRow === r.id
                ? <><b className="warn">删掉这一行？</b>
                    <button className="btn danger" disabled={busy} onClick={() => onDel(r.id)}>确认删除</button>
                    <button className="btn" disabled={busy} onClick={() => setDelRow('')}>取消</button></>
                : <><button className="btn" disabled={busy} title="载入下方表单，改完存成新的一行"
                      onClick={() => setForm({ 派遣方: r.派遣方, 岗位: r.岗位, 生效日: '', 失效日: '', 备注: r.备注 || '',
                        dw: r.day ? r.day[0] : '', dm: r.day ? r.day[1] : '',
                        nw: r.night ? r.night[0] : '', nm: r.night ? r.night[1] : '' })}>照此新增</button>
                    <button className="btn link-del" disabled={busy} onClick={() => setDelRow(r.id)}>删除</button></>}
            </td>}
          </tr>))}
          {rows.length === 0 && <tr><td colSpan={can ? 10 : 9} style={{ color: 'var(--ink-3)', padding: 16 }}>
            还没有登记任何合同价。用下面一行录入。</td></tr>}
        </tbody>
      </table></div>

      {can && <div className="addrow">
        <div className="t">登记一行合同价</div>
        <div className="grid">
          <label>派遣方 <input value={form.派遣方} placeholder="如 锦绣" onChange={e => f('派遣方', e.target.value)} /></label>
          <label>岗位 <input value={form.岗位} placeholder="留空＝普工" onChange={e => f('岗位', e.target.value)} /></label>
          <label>白班员工工资 <input type="number" step="0.5" value={form.dw} onChange={e => f('dw', e.target.value)} /></label>
          <label>白班管理费 <input type="number" step="0.5" value={form.dm} onChange={e => f('dm', e.target.value)} /></label>
          <label>夜班员工工资 <input type="number" step="0.5" value={form.nw} onChange={e => f('nw', e.target.value)} />
            <span className="note">留空＝该档无夜班</span></label>
          <label>夜班管理费 <input type="number" step="0.5" value={form.nm} onChange={e => f('nm', e.target.value)} /></label>
          <label>生效日 <input type="date" value={form.生效日} onChange={e => f('生效日', e.target.value)} /></label>
          <label>失效日 <input type="date" value={form.失效日} onChange={e => f('失效日', e.target.value)} />
            <span className="note">留空＝到下一条前一天</span></label>
          <label className="wide">备注 <input value={form.备注} placeholder="如：续签调价 / 到期不再续" onChange={e => f('备注', e.target.value)} /></label>
        </div>
        <div className="ops">
          <button className="btn primary" disabled={busy} onClick={onSave}>{busy ? '保存中…' : '保存这一行'}</button>
          {msg && <span className={/失败|请填|不能|注意/.test(msg) ? 'warn' : 'ok'}>{msg}</span>}
        </div>
      </div>}

      <div className="foot">
        键＝<b>派遣方 + 岗位 + 生效日</b>，同键再存＝覆盖那一行。<br />
        失效日<b>留空＝到同一行下一条的生效日前一天</b>；显式填是给「合同到期不再续」用的——那种日期推不出来。
        显式填就可能出现<b>重叠</b>或<b>空档</b>，两种都会让某期取错价或取不到价，工具逐条检出并标红。<br />
        单价唯一来源＝本表。没登记的档一律「缺档／待核」，不拿汇总表表头解析值或任何默认值顶上——
        表头是人力自己写的，拿它当基准就成了人力跟自己比；它只在「对比」页最下面和导出报告⑤页底部作参考展示。
      </div>
    </div>
  )
}

/* 逐人和逐日共用这一套筛选（V2.370）。
   逐人多一个「只看异常」；逐日不显示应付合计（那是逐人才有的口径），改显示条数。
   unit 是计数单位：逐人是「人」，逐日是「人日」。 */
function PeopleFilter({ people, pf, on, shown, total, sum, showBad, unit = '人', extra,
                        extraActive, onClear }) {
  const uniq = k => [...new Set(people.map(p => p[k]).filter(Boolean))].sort()
  const on1 = (k, v) => on({ ...pf, [k]: v })
  // extraActive/onClear 给「本组件之外还有筛选」的页面用（第⑦步的级别/项目/只看未确认）——
  // 不接进来的话，那几个筛选一开，计数行还写「共 14 笔」，清空筛选也清不掉它们
  const active = pf.dept || pf.agency || pf.name || pf.bad || extraActive
  return (
    <div className="card ta-filter">
      <div className="row">
        <label>部门
          <select value={pf.dept} onChange={e => on1('dept', e.target.value)}>
            <option value="">全部</option>
            {uniq('部门').map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>归属（派遣方）
          <select value={pf.agency} onChange={e => on1('agency', e.target.value)}>
            <option value="">全部</option>
            {uniq('归属').map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>姓名
          <input value={pf.name} placeholder="输入即筛，支持部分匹配"
                 onChange={e => on1('name', e.target.value)} />
        </label>
        {showBad && <label className={'scope' + (pf.bad ? ' on' : '')}
          title="只留任何一项对不上的人：撑不起上报 / 报了工时没打卡 / 单价与合同价不符 / 合同价缺档 / 应付与结算表对不上">
          <input type="checkbox" checked={!!pf.bad} onChange={e => on1('bad', e.target.checked)} />
          <span>只看异常</span>
        </label>}
        {active && <button className="btn"
          onClick={() => onClear ? onClear()
            : on({ dept: '', agency: '', name: '', ...(showBad ? { bad: false } : {}) })}>清空筛选</button>}
        {extra && <span className="bands">{extra}</span>}
        <span className="cnt">{active ? `筛出 ${shown} / ${total} ${unit}` : `共 ${total} ${unit}`}</span>
      </div>
      {/* 分隔符要写在同一行的末尾——写在下一行开头会被 JSX 的行首空白裁掉，实测少了一个「　」 */}
      {sum && <div className="tot">
        本页筛选范围合计：上报 <b>{h1(sum.上报)}</b> 小时　·　工资 <b>{y0(sum.工资)}</b>　·
        管理费 <b>{y0(sum.管理费)}</b>　·　<b className="big">应付合计 {y0(sum.合计)}</b>
        {sum.缺档 > 0
          ? <span className="hint" style={{ color: '#92400e' }}>　（{sum.有表 > 0 ? '结算表金额；' : ''}按合同价应付算不全——{sum.缺档} 人合同价缺档）</span>
          : sum.有表 > 0
            ? <span className="hint">　（结算表金额；按合同价应付 {y0(sum.重算)}）</span>
            : <span className="hint">　（结算表无金额列，此处为按合同价应付）</span>}
        {sum.不符 > 0 && <span className="hint" style={{ color: '#b91c1c', fontWeight: 600 }}>
          　⚠ 其中 {sum.不符} 人单价与合同价不符
          {sum.多付 > 0.005 && <>，单价算错多付 {y0(sum.多付)}</>}
          {sum.少付 < -0.005 && <>{sum.多付 > 0.005 ? '、' : '，'}少付 {y0(Math.abs(sum.少付))}</>}
          　（下表这一列标红的就是）</span>}
      </div>}
    </div>
  )
}

function Pager({ total, pg, on }) {
  const pages = pg.size ? Math.max(1, Math.ceil(total / pg.size)) : 1
  const cur = Math.min(pg.page, pages)
  const go = p => on({ ...pg, page: Math.min(Math.max(1, p), pages) })
  const from = pg.size ? (cur - 1) * pg.size + 1 : 1
  const to = pg.size ? Math.min(cur * pg.size, total) : total
  return <div className="pager">
    <span>共 {total} 行{total > 0 && <>，当前第 {from}–{to} 行</>}</span>
    <span className="sp" />
    <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>每页
      <select value={pg.size} onChange={e => on({ page: 1, size: Number(e.target.value) })}>
        {[50, 100, 200, 500].map(n => <option key={n} value={n}>{n}</option>)}
        <option value={0}>全部</option>
      </select>
    </label>
    <button className="btn" onClick={() => go(1)} disabled={cur <= 1}>首页</button>
    <button className="btn" onClick={() => go(cur - 1)} disabled={cur <= 1}>上一页</button>
    <span>{cur} / {pages}</span>
    <button className="btn" onClick={() => go(cur + 1)} disabled={cur >= pages}>下一页</button>
    <button className="btn" onClick={() => go(pages)} disabled={cur >= pages}>末页</button>
  </div>
}

function Note({ tone, title, items, foot }) {
  const c = tone === 'bad'
    ? { bg: '#fef2f2', bd: '#fca5a5', fg: '#b91c1c' }
    : { bg: '#fffbe6', bd: '#fcd34d', fg: '#92400e' }
  return <div style={{ background: c.bg, border: `1px solid ${c.bd}`, borderRadius: 8, padding: 12, marginBottom: 14 }}>
    <b style={{ color: c.fg }}>{title}</b>
    <ul style={{ margin: '6px 0 0', paddingLeft: 20, lineHeight: 1.9 }}>
      {(items || []).map((w, i) => <li key={i}>{w}</li>)}
    </ul>
    {foot && <div style={{ marginTop: 6, fontSize: 12 }}>{foot}</div>}
  </div>
}

// 同名的人，工具替谁做了选择，必须摆出来让成本会计复核——
// 只显示一个「自动定人 46 个」的数字等于没说，出了错也没人看得见。
/* 用工成本汇总 + 可直接发群的通报文字。
   与导出 Excel 用的是**同一份后端计算**（res.用工成本）——两边各算一次迟早对不上，
   2026-08 那条手敲的通报就是金额对、两个车间的平均单价照搬了上个月。 */
/* 文件名里通常带着月份（「2026年7月（临时工）考勤汇总表」「2026-07打卡记录」）。
   在点「开始核对」之前先跟右上角比一下，对不上就提醒——否则要等提交后被后端拦下才知道，
   白等一次上传（2026-08-29 反馈：右上角选 8 月、导出的却是 7 月）。 */
function FileMonthHint({ summary, punch, month }) {
  const pick = f => {
    const m = (f?.name || '').match(/(20\d{2})\s*[-年./]?\s*(\d{1,2})/)
    return m ? `${m[1]}-${String(+m[2]).padStart(2, '0')}` : ''
  }
  const got = [['汇总表', pick(summary)], ['打卡表', pick(punch)]].filter(x => x[1])
  const bad = month ? got.filter(x => x[1] !== month) : []
  if (!bad.length) return null
  return <span className="warn" style={{ fontSize: 12 }}>
    ⚠ 右上角选的是 <b>{month}</b>，但{bad.map(x => `${x[0]}文件名像 ${x[1]}`).join('、')}。
    期间以右上角为准；真要核那一期，请先把右上角切过去。
  </span>
}

function CostNote({ c, month }) {
  const [ok, setOk] = useState('')
  if (!c || !(c.行 || []).length) return null
  const txt = (c.文字 || []).join('\n')
  const copy = async () => {
    try { await navigator.clipboard.writeText(txt); setOk('已复制') }
    catch { setOk('复制失败，请手动选中') }
    setTimeout(() => setOk(''), 2500)
  }
  const yn = v => v == null ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const hn = v => v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 })
  return <div className="card" style={{ padding: 16, marginBottom: 14 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
      <b>用工成本汇总 · {month || '本期'}</b>
      <span className="note">按派遣方 × 车间。金额取结算表应付（各家请款单上的钱），元/h ＝ 金额 ÷ 上报工时</span>
    </div>
    <div style={{ overflowX: 'auto' }}>
      <table className="tbl" style={{ fontSize: 12.5 }}>
        <thead>
          <tr>
            <th rowSpan={2}>派遣方</th>
            <th colSpan={3} className="sep">合计</th>
            {(c.车间 || []).map(sh => <th key={sh} colSpan={3} className="sep">{sh}车间</th>)}
          </tr>
          <tr>
            <th className="sep">金额</th><th>工时</th><th>元/h</th>
            {(c.车间 || []).map(sh => <React.Fragment key={sh}>
              <th className="sep">金额</th><th>工时</th><th>元/h</th></React.Fragment>)}
          </tr>
        </thead>
        <tbody>
          {(c.行 || []).map((x, i) => <tr key={i}>
            <td><b>{x.派遣方}</b></td>
            <td className="sep">{yn(x.金额)}</td><td>{hn(x.工时)}</td><td>{yn(x.元每小时)}</td>
            {(c.车间 || []).map(sh => {
              const v = x.车间?.[sh]
              return <React.Fragment key={sh}>
                <td className="sep">{v ? yn(v.金额) : '—'}</td>
                <td>{v ? hn(v.工时) : '—'}</td>
                <td>{v ? yn(v.元每小时) : '—'}</td>
              </React.Fragment>
            })}
          </tr>)}
          {c.合计 && <tr style={{ background: 'var(--bg-sub)', fontWeight: 700 }}>
            <td>合计</td>
            <td className="sep">{yn(c.合计.金额)}</td><td>{hn(c.合计.工时)}</td><td>{yn(c.合计.元每小时)}</td>
            {(c.车间 || []).map(sh => {
              const v = c.合计.车间?.[sh]
              return <React.Fragment key={sh}>
                <td className="sep">{v ? yn(v.金额) : '—'}</td>
                <td>{v ? hn(v.工时) : '—'}</td>
                <td>{v ? yn(v.元每小时) : '—'}</td>
              </React.Fragment>
            })}
          </tr>}
        </tbody>
      </table>
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '12px 0 6px' }}>
      <b>下面这段可直接发群</b>
      <button className="btn" style={{ padding: '3px 12px', fontSize: 12 }} onClick={copy}>复制全文</button>
      {ok && <span style={{ fontSize: 12, color: '#15803d' }}>{ok}</span>}
    </div>
    <pre style={{ margin: 0, padding: 12, background: 'var(--bg-sub)', borderRadius: 8,
      whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12.5, lineHeight: 1.9,
      fontFamily: 'inherit' }}>{txt}</pre>
  </div>
}

function DupReview({ rec, merged }) {
  const [open, setOpen] = useState(false)
  if (!(rec || []).length) return null
  const bad = rec.filter(x => !x.已定)
  const 弱 = rec.filter(x => x.已定 && (x.候选.find(c => c.选中)?.得分 ?? 1) < 0.8)
  return <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #bae6fd' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <b>同名 {rec.length} 组，工具已替你定人</b>
      {!!merged && <span>（其中 {merged} 组手机号相同＝同一个人在钉钉有两个账号，打卡已合并）</span>}
      <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }}
        onClick={() => setOpen(!open)}>{open ? '收起' : '展开复核'}</button>
    </div>
    <div style={{ marginTop: 4 }}>
      {bad.length
        ? <span className="warn">⚠ 其中 {bad.length} 组定不了，这些人会被判成「没打卡」，请人工确认。</span>
        : <span>全部定出来了。</span>}
      {!!弱.length && <span className="warn">　⚠ {弱.length} 组吻合度不高（&lt;0.8），建议重点看。</span>}
      <span style={{ color: 'var(--ink-3)' }}>　同一份底稿也在打卡表的第二页里，可打印存档。</span>
    </div>
    {open && <div style={{ marginTop: 8, maxHeight: 300, overflow: 'auto' }}>
      <table className="tbl" style={{ fontSize: 12 }}>
        <thead><tr>
          <th>姓名</th><th>选中的人</th><th>当月打卡</th><th>命中上工日</th>
          <th>吻合度</th><th>没选的候选</th>
        </tr></thead>
        <tbody>
          {rec.map((x, i) => {
            const hit = x.候选.find(c => c.选中)
            const oth = x.候选.filter(c => !c.选中)
            const weak = x.已定 && (hit?.得分 ?? 1) < 0.8
            return <tr key={i} style={!x.已定 ? { background: '#fef2f2' }
              : weak ? { background: '#fffbeb' } : undefined}>
              <td><b>{x.姓名}</b></td>
              <td>{x.已定
                ? <>手机尾号 <b>{hit?.手机尾号 || '（无）'}</b>
                  {hit && hit.账号.length > 1 && <span>　·　{hit.账号.length} 个账号已合并</span>}</>
                : <span className="warn">⚠ 定不了，需人工</span>}</td>
              <td>{hit ? `${hit.打卡日数} 天` : '—'}</td>
              <td>{hit ? `${hit.命中上工日} / ${x.上工日数}` : `— / ${x.上工日数}`}</td>
              <td>{hit ? hit.得分 : '—'}</td>
              <td style={{ color: 'var(--ink-3)' }}>
                {oth.map(c => `尾号${c.手机尾号 || '?'}（打卡${c.打卡日数}天,命中${c.命中上工日}）`).join('；') || '—'}
              </td>
            </tr>
          })}
        </tbody>
      </table>
    </div>}
  </div>
}

// 从钉钉直接取打卡：直连考勤源头取原始打卡，不经人力二次手工导出，更准更独立
// （工具是财务核人力报的工时，打卡表若也用人力导的那份＝被核方自己交卷，直连钉钉才是可信源头）。
// 取回来先生成一张打卡表让人核对，再走原流程——不做成黑箱。
function DingPull({ ding, job, onPull, hasSummary, full, onFull }) {
  // 取数计时器：钉钉取数慢（整月能三五分钟），跑着的时候把「已用 M:SS」滚出来，完成后定格成「用时」。
  // hooks 必须在下面的早返回之前、无条件调用。
  const running = job?.状态 === '进行中'
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(null)
  useEffect(() => {
    if (running) {
      if (startRef.current == null) startRef.current = Date.now()
      const upd = () => setElapsed(Math.max(0, Math.round((Date.now() - startRef.current) / 1000)))
      upd()
      const id = setInterval(upd, 1000)
      return () => clearInterval(id)
    }
    startRef.current = null   // 跑完/失败清起点：下次取数重新计时；elapsed 值定格给「用时」显示用
  }, [running])
  const fmtT = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  const box = (bg, bd, cl) => ({ marginTop: 10, padding: '10px 12px', borderRadius: 8,
    background: bg, border: '1px solid ' + bd, color: cl, fontSize: 12, lineHeight: 1.75 })
  if (!ding) return null
  if (!ding.可用) {
    const bad = (ding.项 || []).filter(x => !x.通)
    return <div style={box('#fff7ed', '#fed7aa', '#9a3412')}>
      <b>钉钉取数暂不可用</b>
      {ding.期次说明 && ding.期次可取 === false && <div>· {ding.期次说明}</div>}
      {bad.map((x, i) => <div key={i}>
        · <b>{x.项}</b> 没开通{x.为什么要 ? `——${x.为什么要}` : ''}
        {x.申请链接 && <> 　<a href={x.申请链接} target="_blank" rel="noreferrer">点此让管理员申请</a></>}
      </div>)}
      {!bad.length && ding.说明 && <div>· {ding.说明}</div>}
      <div style={{ marginTop: 4, color: 'var(--ink-3)' }}>开通前请继续用人力导出的打卡表。</div>
    </div>
  }
  const r = job?.结果
  return <div style={box('#f0f9ff', '#bae6fd', '#075985')}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <button className="btn" onClick={onPull}
        disabled={!hasSummary || job?.状态 === '进行中'}>
        {job?.状态 === '进行中' ? '取数中…' : '⇩ 从钉钉取打卡'}
      </button>
      <span>{hasSummary ? '照着左边汇总表上的人去钉钉取——直连钉钉，比人力手工导的打卡表更准' : '先选左边的汇总表'}</span>
    </div>
    {/* 只取「报了工时的日子前后各一天」是默认：红灯判的都是有上报的日子，一条不少；
        整月多出来的只有「有打卡·未计工时」这个中性档。2026-06 实测 143 秒 vs 311 秒。 */}
    <label style={{ display: 'flex', gap: 7, alignItems: 'flex-start', marginTop: 8, cursor: 'pointer' }}>
      <input type="checkbox" checked={!!full} disabled={job?.状态 === '进行中'}
        onChange={e => onFull(e.target.checked)} style={{ marginTop: 3 }} />
      <span>取整月每一天 + 上月末 / 次月初<span style={{ color: 'var(--ink-3)' }}>
        （慢一倍多）。<b>要导出完整全月的打卡原始表（报告第⑧页）就勾这个</b>——
        它把整月每一天、外加<b>上月末和次月初两天</b>都取回来（夜班跨零点，边界那班的下班卡落在隔月）。
        <br />不勾＝只取<b>报了工时的日子前后各一天</b>：判定用的红灯（撑不起上报 / 报了工时没打卡）
        <b>一条不少</b>，只是⑧原始表不是整月、「◇有打卡·未计工时」这个中性档也统计不全。</span></span>
    </label>
    {job?.状态 === '进行中' && <div style={{ marginTop: 8 }}>
      <div style={{ height: 6, background: '#e0f2fe', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${job.进度 || 0}%`, background: '#0284c7',
          transition: 'width .4s' }} />
      </div>
      <div style={{ marginTop: 4 }}>{job.说明}　<b>已用 {fmtT(elapsed)}</b>{job.预计 ? `　（预计${job.预计}）` : ''}</div>
    </div>}
    {job?.状态 === '完成' && r && <div style={{ marginTop: 8 }}>
      ✅ <b>{job.月份}</b> 取到 <b>{r.人数}</b> 人 / <b>{r.打卡日次}</b> 人日，已填入上面的「打卡时刻表」。
      <span style={{ color: 'var(--ink-3)' }}>　（期间{job.月份来源}）{elapsed > 0 ? `　用时 ${fmtT(elapsed)}` : ''}</span>
      {r.取数范围 === 'worked'
        ? <div style={{ color: 'var(--ink-3)' }}>
            本次只取了报了工时的日子前后各一天——红灯一条不少，但⑧打卡原始表不是整月、
            「◇有打卡·未计工时」这一档也统计不全（<b>要导出完整全月就勾「取整月每一天」重取</b>）。
          </div>
        : <div style={{ color: 'var(--ink-3)' }}>
            本次取了<b>整月每一天 + 上月末 / 次月初</b>，⑧打卡原始表是完整全月（含跨月边界）。
          </div>}
      <div style={{ marginTop: 4 }}>
        <a href={tempattDingFileUrl(job.任务)}>下载这张打卡表核对</a>
        <span style={{ color: 'var(--ink-3)' }}>　建议先抽几个人跟钉钉 App 上的记录对一眼再往下走。</span>
      </div>
      <DupReview rec={r.重名记录} merged={r.合并账号组数} />
      {!!(r.未取到 || []).length && <div className="warn" style={{ marginTop: 4 }}>
        ⚠ 有 {r.未取到.length} 人没取到（重名分不清或钉钉查无此人）：{r.未取到.slice(0, 8).join('、')}
        {r.未取到.length > 8 && ' 等'}。<b>这些人会被判成「没打卡」，请改用人力导出的打卡表，别照这份下结论。</b>
      </div>}
    </div>}
  </div>
}

function Pick({ label, hint, file, onPick }) {
  return <label style={{ display: 'block' }}>
    <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 8, lineHeight: 1.7, minHeight: 34 }}>{hint}</div>
    <input type="file" accept=".xlsx,.xls" onChange={e => onPick(e.target.files[0] || null)} />
    {file && <div style={{ fontSize: 12, color: '#166534', marginTop: 6 }}>已选：{file.name}</div>}
  </label>
}

function Num({ label, v, on }) {
  return <label style={{ display: 'block' }}>
    <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 4 }}>{label}</div>
    <input type="number" step="0.5" value={v} style={{ width: '100%' }}
      onChange={e => on(parseFloat(e.target.value))} />
  </label>
}

function Sel({ label, v, on, opts }) {
  return <label style={{ display: 'block' }}>
    <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 4 }}>{label}</div>
    <select value={v} style={{ width: '100%' }} onChange={e => on(e.target.value)}>
      {opts.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  </label>
}

// 单价一行：派遣方 · 岗位 · 白班三格 · 夜班三格。同一派遣方的多个岗位用 rowSpan 归成一组。
// 输入框无边框、铺满格子——格线本身就是边框，观感同 Excel；合计与派遣方/岗位是只读格，用底色区分。
function Kpi({ title, v, sub, tone }) {
  const c = tone === 'bad' ? '#b91c1c' : tone === 'warn' ? '#92400e' : 'inherit'
  return <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>{title}</div>
    <div style={{ fontSize: 24, fontWeight: 700, color: c, marginTop: 4 }}>{v}</div>
    {sub && <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>}
  </div>
}

/* 「钱对不对」——总览里与四档判定并列的另一半。
   四项各自回答一句话，点进去能到能处理它的那一步：
     单价与合同价不符  → 人力用的价和合同不一样，是真要查的（第③步逐格看）
     合同价缺档        → 没登记就没法核，是「待核」不是「正常」（第③步去登记）
     结算表自查        → 表自己内部对不对得上：金额＝工时×表上单价、勾稽平（第⑦步看明细）
     奖罚/补贴异常     → 全表唯一没有对照源的钱，只验符号和占比（第⑦步看明细）
   ⚠ 这四项的数取自后端 stats，与第③⑦步、导出报告同源，不在页面上另算一套。 */
function MoneyOverview({ st, res, go, riskOpen, riskAcked }) {
  const rc = st.单价核对 || {}
  const chk = (st.金额核对 || []).filter(x => !x.已认定)
  // 「结算表自查」那一行只数金额与勾稽两道：单价那一道已由上面「单价与合同价不符」报过，
  // 两行都数就是同一批人报两遍，何况本行说的是「表自己内部对不上」，单价不符不是内部的事
  const chkSelf = chk.filter(x => x.类型 !== '单价')
  const adjT = st.合同外调整合计 || {}
  const noPrice = !rc.有人力数据 && (st.有表上金额 || 0) === 0
  const cells = (rc.明细 || []).filter(x => x.人数 > 0)
  const sum = (pred) => cells.filter(pred).reduce((a, x) => ({ 格: a.格 + 1, 人: a.人 + (x.人数 || 0), 工时: a.工时 + (x.工时 || 0) }), { 格: 0, 人: 0, 工时: 0 })
  const bad = sum(x => x.状态 === '⚠不符')
  const miss = sum(x => x.状态 === '合同缺档')
  const okc = sum(x => x.状态 === '一致')
  const rOpen = (riskOpen || []).length, rAck = (riskAcked || []).length
  const 多价 = rc.同格多价 || 0
  const rows = [
    {
      // 排第一：它不是「算错多少钱」，是「同一笔钱付了两次 / 付给了不该付的那家」，比单价错更要紧
      k: 'risk', lv: rOpen ? 'bad' : (rAck ? 'ok' : 'ok'),
      t: '结算风险',
      v: rOpen ? `${rOpen} 项待认` : rAck ? `全部已认定无误（${rAck} 项）` : '本期没有',
      sub: rOpen ? '同名重复计费 / 归属与打卡不符' : '',
      why: (rOpen || rAck)
        ? '同名挂在两家派遣方、同名重复行、结算归属与打卡部门不符——这三项是「钱被重复付、或付给了不该付的那一家」，比工时偏离要紧。工具只指认并给判据，是不是同一个人由人来认。'
        : '本期没有同名重复、也没有结算归属与打卡部门不符。',
      // ⚠ 认定这些风险的按钮/弹层只在④结算风险页（RISK_CARD_STEPS=['risk']），不在⑤逐人核对。
      //   早先写成 'people' 会把点「N 项待认」的人送到⑤——那里根本没有风险卡可认（复查 V2.402 揪出）。
      to: 'risk', toName: `去第${stepNo('risk')}步结算风险 →`,
    },
    {
      k: 'rate', lv: (bad.格 || 多价) ? 'bad' : miss.格 ? 'wait' : okc.格 ? 'ok' : 'na',
      t: '单价与合同价不符',
      v: bad.格 ? `${bad.格} 格` : okc.格 ? `0 格（一致 ${okc.格} 格）` : '0 格',
      // ⚠ 人数取 st.单价不符人数（**按人去重**）。逐格表里的「人数」是按格×班次累加的人次，
      //    白夜混合的人两班都不符会被数两次，跟第④步标红的人头对不上（V2.349 审出）
      sub: bad.格 ? `${st.单价不符人数 ?? bad.人} 人 · ${h1(st.单价不符工时 ?? bad.工时)} 小时`
        + (Math.abs(st.单价不符多付 || 0) > 0.005 ? `　单价算错多付 ${y0(st.单价不符多付)}` : '')
        + (Math.abs(st.单价不符少付 || 0) > 0.005 ? `　少付 ${y0(Math.abs(st.单价不符少付))}` : '')
        : (!okc.格 && miss.格) ? `本期用到的 ${miss.格} 格全部缺合同价，无从比对` : '',
      why: (bad.格
        ? '人力实际套用的单价与成本会计登记的合同价对不上——这是真要查的一档：算错的是单价，钱就一路错到底。'
        : okc.格 ? '本期用到的每一档，人力实际单价都与合同价一致。'
        : '把下一行的缺档补上，这里才比得起来：逐格比「员工工资」和「管理费」两条线——16.5+2.5 与 17+2 合计都是 19，但在合同、入账、发票上是两条不同的线。')
        + (多价 ? `　⚠ 另有 ${多价} 格「同一格里用了不止一种单价」：先查清为什么，再谈跟合同价对不对——这一档在第④步标不出具体是谁。` : ''),
      to: 'rule', toName: `去第${stepNo('rule')}步逐格看 →`,
    },
    {
      k: 'miss', lv: miss.格 ? 'wait' : 'ok',
      t: '合同价缺档',
      v: miss.格 ? `${miss.格} 格` : '0 格',
      sub: miss.格 ? `${miss.人} 人 · ${h1(miss.工时)} 小时` : '',
      why: miss.格
        ? '人力照价算了钱，但合同价登记表里没有覆盖本期的行——无从判断对错。缺档不等于没问题，只等于还没核；复核结论里这些格子记「待核」。'
        : '本期用到的每一档，合同价都登记了。',
      to: 'rule', toName: `去第${stepNo('rule')}步登记 →`,
    },
    {
      // ⚠ 只数「金额」「勾稽」两道。「单价」那一道也在 st.金额核对 里，但它已由上面「单价与合同价不符」
      //    那一行报过了——两行都数就是同一批人报两遍，而且本行的说明写的是「表自己内部对不上」，
      //    单价不符恰恰不是内部的事（V2.349 审出）
      k: 'chk', lv: chkSelf.length ? 'bad' : (st.有表上金额 || 0) ? 'ok' : 'na',
      t: '结算表自查',
      v: (st.有表上金额 || 0) ? (chkSelf.length ? `${chkSelf.length} 处对不上` : '全部通过') : '未做',
      sub: chkSelf.length ? `涉及 ${new Set(chkSelf.map(x => x.姓名)).size} 人` : '',
      why: (st.有表上金额 || 0)
        ? (chkSelf.length
          ? '结算表自己内部就对不上：金额 ≠ 工时 × 表上单价，或员工工资/合计勾稽不平。工具只指出，不改数。'
          : '金额＝工时×表上单价、员工工资与合计勾稽都平，结算表内部自洽。（单价对不对见上一行，那不是表内部的事）')
        : '本期结算表没有金额列（可能是按派遣方拆出来的简表），这三道核对做不了。',
      to: 'concl', toName: `去第${stepNo('concl')}步看明细 →`,
    },
    {
      k: 'adj', lv: adjT.异常 ? 'bad' : adjT.存疑 ? 'wait' : 'ok',
      t: '奖 / 罚 / 补贴',
      v: adjT.笔数 ? `${adjT.笔数} 笔，净额 ${y0(adjT.净额)}` : '本期没有',
      sub: (adjT.异常 || adjT.存疑) ? `${adjT.异常 || 0} 异常 · ${adjT.存疑 || 0} 存疑` : '',
      why: adjT.笔数
        ? '这三项是全表唯一没有对照源的钱——工时有打卡可比、单价有合同可比，它们只有结算表这一处孤证。工具验不了金额本身，只验符号（罚≤0、奖≥0）和占工资比例。过了检查 ≠ 这笔奖罚是对的。'
        : '本期没有合同外调整。',
      to: 'adj', toName: `去第${stepNo('adj')}步看明细 →`,
    },
  ]
  const TAG = { bad: { t: '⚠ 要查', c: '#b91c1c', bg: '#fef2f2' }, wait: { t: '△ 待核', c: '#92400e', bg: '#fffbeb' },
                ok: { t: '✓ 正常', c: 'var(--ok, #15803d)', bg: '#f0fdf4' }, na: { t: '— 未做', c: 'var(--ink-3)', bg: 'transparent' } }
  return <>
    <div className="ov-hd">二、钱对不对　<span className="note">结算风险 · 单价 · 结算表自查 · 合同外调整。工时偏离不影响付款，这几项才直接关系到付出去的钱</span></div>
    {noPrice && <div className="ov-none">本期结算表里没有单价/金额列，下面四项大多做不了——要核钱，得拿带金额列的那版汇总表。</div>}
    <div className="card" style={{ padding: 0, marginBottom: 14 }}>
      <table className="tbl ov-money">
        <thead><tr><th style={{ width: 150 }}>核对项</th><th style={{ width: 100 }}>判定</th><th style={{ width: 170 }}>结果</th><th>说明</th><th style={{ width: 150 }}></th></tr></thead>
        <tbody>{rows.map(r => {
          const g = TAG[r.lv]
          return <tr key={r.k} style={{ background: g.bg }}>
            <td style={{ fontWeight: 600 }}>{r.t}</td>
            <td style={{ color: g.c, fontWeight: 700, whiteSpace: 'nowrap' }}>{g.t}</td>
            <td style={{ fontWeight: 600 }}>{r.v}{r.sub && <div className="note">{r.sub}</div>}</td>
            <td style={{ color: 'var(--ink-3)', textAlign: 'left', whiteSpace: 'normal' }}>{r.why}</td>
            <td><button className="btn" onClick={() => go(r.to)}>{r.toName}</button></td>
          </tr>
        })}</tbody>
      </table>
    </div>
  </>
}

function Band({ b, n, h, m, note }) {
  const s = BAND[b]
  return <tr style={{ background: s.bg }}>
    <td style={{ color: s.color, fontWeight: 600 }}>{s.label}</td>
    <td>{n || 0}</td><td>{h === undefined ? '—' : h1(h)}</td><td>{m === undefined ? '—' : y0(m)}</td>
    <td style={{ color: 'var(--ink-3)' }}>{note}</td>
  </tr>
}
