// [Change Log] Date:2026-08-06 Author:Claude/c Version:V2.227
// 「账单上传」——物流部专属工作台，V2.227 改三页（业务方定）：
//   ① 解析列表·上传：哪些物流商能自动解析、模板下载、两条上传通道（复核无误的账单包 / 按模板填的计提账单）、
//      质检（新供应商自动通知核算组建档 / 归集判定）、本月已解析批次列表（载入续办）
//   ② 计提表（长表）：待处理行补业务三件套（主体/费用归属/业务线，科目部门由映射自动、核算组把关）+ 全量只读预览
//   ③ 汇总表：按付款主体×物流商汇总，勾选发起钉钉付款提醒（付款审批自动起单待对账线三期接入）
// 看不到：做账去向/费率/录金蝶/打印——那些在核算组的「物流计提」页。
// 数据与核算组同源（logistics_bill_uploads 批次）：物流部提交，核算组「载入这批」接力。
import React, { useEffect, useState } from 'react'
import PeriodPicker from '../components/PeriodPicker.jsx'
import {
  parseBills, parseLongForm, refreshLogisticsRow, getBillUploads, loadBillUpload,
  saveBillUploadRows, submitBillUpload, logisticsPayRemind, getSupplierMatrix, setSupplierDoc,
  getNotifyRecipients, saveNotifyRecipients, testNotify, uploadInvoiceFile, deleteInvoiceFile, diffParseBill,
} from '../api.js'

const money = n => (n == null || n === '') ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const FEE13 = ['销售出库费用', '成品入库费用', '原料入库费用', '成品仓储费用', '原料仓储费用',
  '成品调拨费用', '原料调拨费用', '出库装卸费用', '成品入库装卸费用', '原料入库装卸费用',
  '研发设备采购', '设备调拨费用', '其它']
const BIZ10 = ['植物肉', '鲜食', '零售', '小料', '豆蛋制品', '电商', '山姆零售', 'kikiherb', '海外', '—']
const SUBJ3 = ['深圳星期零', '孝感星期九', '深圳星期九']
const btn = { padding: '6px 14px', borderRadius: 7, border: '0.5px solid #cfcdc4', background: '#fff', cursor: 'pointer', fontSize: 12.5 }
const ctd = { border: '0.5px solid #d8d8d8', padding: '5px 10px', whiteSpace: 'nowrap' }
const cth = { border: '0.5px solid #b9c2d6', padding: '6px 10px', background: '#e8ecf5', color: '#20304d', fontWeight: 600, whiteSpace: 'nowrap' }

const CACHE = {}   // 工作现场缓存（切菜单不丢；与物流计提页各自独立）

export default function LogisticsBillUpload({ user }) {
  const can = k => !!(user && (user.role === 'admin' || (user.perms || {})[k]))
  const canUp = can('logistics_upload')

  const [page, setPage] = useState(CACHE.page || 1)             // ①解析列表·上传 ②计提表 ③汇总表
  const [year, setYear] = useState(CACHE.year || 2026)
  const [month, setMonth] = useState(CACHE.month || 7)
  const [billFiles, setBillFiles] = useState([])
  const [lfFile, setLfFile] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [rows, setRows] = useState(CACHE.rows || null)          // 明细行（voucher 同构）
  const [perFile, setPerFile] = useState(CACHE.perFile || null)
  const [collect, setCollect] = useState(CACHE.collect || null) // {complete, issues, notify, unknown_files, new_suppliers?}
  const [uploads, setUploads] = useState(CACHE.uploads || null)
  const [uploadId, setUploadId] = useState(CACHE.uploadId || null)
  const [savedAt, setSavedAt] = useState(CACHE.savedAt || '')
  const [submitted, setSubmitted] = useState(CACHE.submitted || false)
  const [mx, setMx] = useState(CACHE.mx || null)                // 生命周期矩阵（主体×商：初始账单→计提→定稿→发票→付款）
  const [invEdit, setInvEdit] = useState(null)                  // 正在登记发票的行 {short, subject, key}
  const [invForm, setInvForm] = useState({ no: '', amt: '', date: '' })
  const [vfEdit, setVfEdit] = useState(null)                    // 正在按异议定稿的行 {short, subject, key}
  const [vfForm, setVfForm] = useState({ amt: '', note: '' })
  const [vfDiff, setVfDiff] = useState(null)                    // 修正版比对结果 {diff, subject_totals, 总差额}
  const [paySel, setPaySel] = useState(CACHE.paySel || {})      // 汇总表勾选：`主体|全名` -> true
  const [loadedInfo, setLoadedInfo] = useState(CACHE.loadedInfo || null)  // 自动恢复的批次回执（谁/何时/几份）
  const [nf, setNf] = useState(CACHE.nf || null)                // ④通知设置：{scenes, fallback, passcode_set}
  const [nfEdit, setNfEdit] = useState({})                      // 场景 -> {mobiles, emails} 本地编辑值
  const [nfPass, setNfPass] = useState('')                      // 修改口令（conf.ini [notify] passcode；机密不进 CACHE）
  // 现场写回缓存——必须放在所有 useState 之后（前置会 TDZ 白屏，V2.222 实测）
  useEffect(() => {
    Object.assign(CACHE, { page, year, month, rows, perFile, collect, uploads, uploadId, savedAt, submitted, mx, paySel, loadedInfo, nf })
  }, [page, year, month, rows, perFile, collect, uploads, uploadId, savedAt, submitted, mx, paySel, loadedInfo, nf])

  const refreshUploads = (y, m) => getBillUploads(y, m).then(r => setUploads(r.uploads || [])).catch(() => setUploads([]))
  const refreshMatrix = (y, m) => getSupplierMatrix(y, m).then(r => { if (r.ok) setMx(r) }).catch(() => {})
  const refreshNotify = () => getNotifyRecipients().then(r => { if (r.ok) { setNf(r); setNfEdit({}) } }).catch(() => {})
  useEffect(() => { refreshUploads(year, month); refreshMatrix(year, month); refreshNotify() }, [])
  // 批次列表撤了（V2.228 业务方定）：进页/切月改成静默恢复本月最新批次——现场永远在，不用手动「载入」。
  // 优先挑有明细的批次（空批次=解析失败留痕，别把好现场顶掉）。
  useEffect(() => {
    if (!uploads || !uploads.length || rows || uploadId || busy) return
    const best = uploads.find(u2 => ((u2.stats || {}).明细行数 || (u2.stats || {}).票数)) || uploads[0]
    doLoad(best)
  }, [uploads])
  const pickMonth = (y, m) => {
    if (y === year && m === month) return
    setYear(y); setMonth(m)
    setRows(null); setPerFile(null); setCollect(null); setUploadId(null); setSavedAt(''); setSubmitted(false); setErr(''); setPaySel({}); setLoadedInfo(null)
    setMx(null); setInvEdit(null); setVfEdit(null)
    setUploads(null); refreshUploads(y, m); refreshMatrix(y, m)   // 拉到该月批次后由自动恢复接手
  }

  const applyResult = (r) => {
    setRows(r.rows || [])
    setPerFile(r.per_file || [])
    // 服务端返回的是按商合并后的全量现场（V2.229）——归集完成要看合并后有没有待补行，不能只看本次文件
    const pendN = (r.rows || []).filter(v => v.可录入 === false).length
    setCollect({ complete: (!!r.complete || !!r.clean) && pendN === 0,
      issues: (r.issues && r.issues.length) ? r.issues : (pendN ? [`${pendN} 行待补维度`] : []),
      notify: r.notify, unknown_files: r.unknown_files || [], new_suppliers: r.new_suppliers || [], dirty: r.dirty || [] })
    setUploadId(r.upload_id || null); setSavedAt(''); setSubmitted(false); setPaySel({}); setLoadedInfo(null)
    refreshUploads(year, month); refreshMatrix(year, month)
  }
  const doBills = async () => {
    if (!billFiles.length) { setErr('请先选择账单文件（可多选）'); return }
    setErr(''); setBusy('bills')
    try { const r = await parseBills(billFiles, month, year); if (!r.ok) throw new Error(r.msg || '解析失败'); applyResult(r) }
    catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const doLf = async () => {
    if (!lfFile) { setErr('请先选择计提账单（长表）文件'); return }
    setErr(''); setBusy('lf')
    try { const r = await parseLongForm(lfFile, month, year); if (!r.ok) throw new Error(r.msg || '解析失败'); applyResult(r) }
    catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  // 静默恢复批次现场（自动触发，不切页不打扰——徽标/质检卡/②③页数据就位即可）
  const doLoad = async (u2) => {
    setErr(''); setBusy('load')
    try {
      const r = await loadBillUpload(u2.id)
      if (!r.ok) throw new Error(r.msg || '载入失败')
      setRows(r.rows || []); setPerFile(r.per_file || [])
      const pend2 = (r.rows || []).filter(v => v.可录入 === false)
      setCollect({ complete: pend2.length === 0, issues: pend2.length ? [`${pend2.length} 行待补维度`] : [],
        notify: null, unknown_files: [], new_suppliers: [], dirty: [] })
      setUploadId(u2.id); setSavedAt(''); setSubmitted(u2.status === '已提交'); setPaySel({})
      setLoadedInfo({ operator: u2.operator, ts: u2.ts, stats: u2.stats || {} })
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }

  // 补维度（物流部只管业务三件套：主体/费用归属/业务线——科目部门映射自动，核算组复核把关）
  const patchRow = async (oi, patch) => {
    const row = { ...rows[oi], ...patch }
    try {
      const r = await refreshLogisticsRow({ month, row, requery: true })
      if (!r.ok) throw new Error(r.msg || '重算失败')
      const nr = rows.map((v, i) => i === oi ? r.row : v)
      setRows(nr)
      const pend2 = nr.filter(v => v.可录入 === false)
      setCollect(c => c ? { ...c, complete: pend2.length === 0 && !(c.unknown_files || []).length,
        issues: pend2.length ? [`${pend2.length} 行待补维度`] : [] } : c)
      if (uploadId) saveBillUploadRows(uploadId, nr)
        .then(x => { if (x.ok) setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false })) }).catch(() => {})
    } catch (e) { setErr(String(e.message || e)) }
  }
  const doSubmit = async () => {
    if (!uploadId) { setErr('先解析账单或载入一批'); return }
    if (!window.confirm(`确认把 ${year} 年 ${month} 月计提表提交给核算组？\n将发送 邮件+钉钉 通知核算组检查并录入金蝶。`)) return
    setErr(''); setBusy('submit')
    try {
      const r = await submitBillUpload(uploadId)
      if (!r.ok) throw new Error(r.msg || '提交失败')
      const n = r.notify || {}
      alert(`已提交给核算组。通知：钉钉 ${n.dingtalk ? (n.dingtalk.sent ? '✓' : '未配置') : '—'}；邮件 ${n.email ? (n.email.sent ? '✓' : '未配置') : '—'}`)
      setSubmitted(true); refreshUploads(year, month)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }

  // 供应商档案动作（V2.232 分主体）：定稿/撤定稿/发票登记与清除/付款标记撤销
  const rowKey = (s) => `${s.主体}|${s.简称}`
  const doDocAction = async (short, subject, action, extra) => {
    setErr('')
    try {
      const r = await setSupplierDoc({ year, month, short, subject, action, ...(extra || {}) })
      if (!r.ok) throw new Error(r.msg || '操作失败')
      refreshMatrix(year, month)
      return true
    } catch (e) { setErr(String(e.message || e)); return false }
  }
  const doVerify = (s) => {
    if (!window.confirm(`确认「${s.主体} × ${s.简称}」${month} 月账单核对无误、金额定稿？\n定稿金额＝该主体当前账单 ${money(s.初始账单.金额)} 元。\n有异议的话，先重传修订后的账单，再来确认。`)) return
    doDocAction(s.简称, s.主体, 'verify')
  }
  const doUnverify = (s) => { if (window.confirm(`撤销「${s.主体} × ${s.简称}」的定稿？（发票登记会保留）`)) doDocAction(s.简称, s.主体, 'unverify') }
  // 按异议定稿（V2.235）：对账吵出新金额时——计提行不动（已入账），手登正确金额+传正确版/盖章版账单存档，差异次月冲
  const openVf = (s) => {
    setVfEdit({ short: s.简称, subject: s.主体, key: rowKey(s) })
    setVfForm({ amt: String(s.计提.含税 || s.初始账单.金额 || ''), note: s.定稿.说明 || '' })
    setVfDiff(null)
  }
  const saveVf = async (s) => {
    const amt = parseFloat(vfForm.amt)
    if (isNaN(amt)) { setErr('定稿金额要填数字'); return }
    if (!vfForm.note.trim()) { setErr('请写一句差异原因（如：破损扣款/运费复核少算——核算组冲差要看）'); return }
    if (await doDocAction(vfEdit.short, vfEdit.subject, 'verify',
      { final_amount: amt, verify_note: vfForm.note.trim() })) { setVfEdit(null); setVfDiff(null) }
  }
  // 传修正版账单 → 沙箱解析比对（不进批次、计提零改动）→ 差异行指认 + 自动带定稿金额/预填原因 + 自动存档
  const doDiffParse = async (s, file) => {
    if (!file) return
    setErr(''); setBusy('diff')
    try {
      const r = await diffParseBill(year, month, s.简称, s.主体, file)
      if (!r.ok) throw new Error(r.msg || '比对失败')
      setVfDiff(r)
      const mine = (r.subject_totals || {})[s.主体]
      if (mine) setVfForm(f => ({
        amt: String(mine.修正),
        note: f.note || (r.diff || []).filter(d => d.主体 === s.主体 && Math.abs(d.差额) >= 0.01).slice(0, 3)
          .map(d => `${d.费用归属}${d.业务线 !== '—' ? '·' + d.业务线 : ''}${d.描述 ? '·' + d.描述 : ''} ${d.差额 > 0 ? '+' : ''}${money(d.差额)}`).join('；'),
      }))
      refreshMatrix(year, month)   // 修正版已自动存档为结算账单附件
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const doUnpay = (s) => {
    if (!window.confirm(`撤销「${s.主体} × ${s.简称}」的付款提醒标记？\n只是把状态复位（误点时用）——已发出去的钉钉/邮件收不回。`)) return
    doDocAction(s.简称, s.主体, 'unpay')
  }
  const doClearInv = (s) => { if (window.confirm(`清除「${s.主体} × ${s.简称}」的发票登记？（附件不受影响）`)) doDocAction(s.简称, s.主体, 'clear_invoice') }
  const openInv = (s) => {
    setInvEdit({ short: s.简称, subject: s.主体, key: rowKey(s) })
    setInvForm({ no: s.发票.票号 || '', amt: s.发票.金额 != null ? String(s.发票.金额) : (s.定稿.金额 != null ? String(s.定稿.金额) : ''), date: s.发票.日期 || '' })
  }
  const saveInv = async () => {
    const amt = parseFloat(invForm.amt)
    if (!invForm.no.trim()) { setErr('票号不能为空'); return }
    if (isNaN(amt)) { setErr('发票金额要填数字'); return }
    if (await doDocAction(invEdit.short, invEdit.subject, 'invoice',
      { invoice_no: invForm.no.trim(), invoice_amount: amt, invoice_date: invForm.date.trim() })) setInvEdit(null)
  }
  const doInvFile = async (s, file, kind) => {
    if (!file) return
    setErr(''); setBusy('invfile')
    try {
      const r = await uploadInvoiceFile(year, month, s.简称, s.主体, file, kind)
      if (!r.ok) throw new Error(r.msg || '上传失败')
      refreshMatrix(year, month)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const doInvFileDel = async (s, f) => {
    if (!window.confirm(`删除发票附件「${f.filename}」？`)) return
    setErr('')
    try {
      const r = await deleteInvoiceFile(f.id)
      if (!r.ok) throw new Error(r.msg || '删除失败')
      refreshMatrix(year, month)
    } catch (e) { setErr(String(e.message || e)) }
  }

  // ④通知设置：分场景收件人（V2.230）——空=回落服务器公共名单；凭证只在服务器配置文件
  const nfVal = (sc, field) => {
    const e = nfEdit[sc.scene]
    return e && e[field] != null ? e[field] : (sc[field] || '')
  }
  const nfChanged = (sc) => nfVal(sc, 'mobiles') !== (sc.mobiles || '') || nfVal(sc, 'emails') !== (sc.emails || '')
  const saveNf = async (sc) => {
    if (!nfPass.trim()) { setErr('请先在上方填「修改口令」——口令由管理员写在服务器 conf.ini [notify] passcode 里'); return }
    setErr(''); setBusy('nf')
    try {
      const r = await saveNotifyRecipients(sc.scene, nfVal(sc, 'mobiles'), nfVal(sc, 'emails'), nfPass.trim())
      if (!r.ok) throw new Error(r.msg || '保存失败')
      refreshNotify()
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const fmtReceipt = (n) => {
    const one = (k, label) => {
      const x = (n || {})[k]
      if (!x) return `${label}：—`
      return x.sent ? `${label}：✓ 已发（${(x.to || []).length || ''}人）` : `${label}：${x.msg || '未发'}`
    }
    if (n && n.none) return n.none.msg
    return `${one('dingtalk', '钉钉')}\n${one('email', '邮件')}`
  }
  const doTestNotify = async (sc) => {
    if (nfChanged(sc)) { setErr('先点「保存」再发测试——测试按已保存的名单发'); return }
    if (!window.confirm(`给「${sc.scene}」当前收件人真发一条测试消息？\n（服务器上会真发钉钉/邮件；收到即配置正确）`)) return
    setErr(''); setBusy('nftest')
    try {
      const r = await testNotify(sc.scene)
      if (!r.ok) throw new Error(r.msg || '测试失败')
      alert(`测试已发出。回执：\n${fmtReceipt(r.notify)}`)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }

  // 汇总表：付款主体 × 物流商全名（付款就是按这两个维度打款）
  const paySum = []
  if (rows && rows.length) {
    const idx = {}
    rows.forEach(v => {
      const k = `${v.主体 || '待补'}|${v.公司全名 || v.物流商 || ''}`
      if (!(k in idx)) { idx[k] = paySum.length; paySum.push({ key: k, 主体: v.主体 || '待补', 全名: v.公司全名 || v.物流商 || '', 物流商: v.物流商 || '', 行数: 0, 票数: 0, 含税: 0, 待补: 0 }) }
      const s = paySum[idx[k]]
      s.行数 += 1; s.票数 += (v.票数 || 0); s.含税 += (v.含税 || 0); if (v.可录入 === false) s.待补 += 1
    })
    paySum.sort((a, b) => b.含税 - a.含税)
  }
  // 付款闸门（V2.229，V2.232 分主体）：该主体×商 核对定稿 + 发票已登记 才能勾选发起（服务端同样拦，双保险）
  const docBy = {}
  ;((mx && mx.suppliers) || []).forEach(d => { docBy[`${d.主体}|${d.简称}`] = d })
  const payGate = (s2) => { const d = docBy[`${s2.主体}|${s2.物流商}`]; return !s2.待补 && !!(d && d.定稿.已确认 && d.发票.票号) }
  const paySelRows = paySum.filter(s => paySel[s.key])
  const paySelTotal = paySelRows.reduce((a, s) => a + s.含税, 0)
  const doPayRemind = async () => {
    if (!paySelRows.length) { setErr('请先在汇总表勾选要发起付款的物流商'); return }
    if (!window.confirm(`确认对勾选的 ${paySelRows.length} 笔（合计 ${money(paySelTotal)} 元）发起钉钉付款提醒？`)) return
    setErr(''); setBusy('pay')
    try {
      const items = paySelRows.map(s => ({ 主体: s.主体, 全名: s.全名, 物流商: s.物流商, 含税: Math.round(s.含税 * 100) / 100, 行数: s.行数 }))
      const r = await logisticsPayRemind(year, month, items)
      if (!r.ok) throw new Error(r.msg || '发起失败')
      const n = r.notify || {}
      alert(`付款提醒已发起。通知：钉钉 ${n.dingtalk ? (n.dingtalk.sent ? '✓' : '未配置') : '—'}；邮件 ${n.email ? (n.email.sent ? '✓' : '未配置') : '—'}`)
      setPaySel({}); refreshMatrix(year, month)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }

  const pend = (rows || []).map((v, i) => ({ v, i })).filter(x => x.v.可录入 === false)
  const total = (rows || []).reduce((a, v) => a + (v.含税 || 0), 0)
  const sel = (v, field, opts) => <select value={v[field] || ''} onChange={e => patchRow(v._oi, { [field]: e.target.value })}
    style={{ padding: '3px 6px', borderRadius: 6, border: '0.5px solid #cfcdc4', maxWidth: 130 }}>
    {(v[field] ? [] : ['']).concat(opts).map(o => <option key={o || '空'} value={o}>{o || '（请选）'}</option>)}
  </select>

  // 归集/提交状态卡（①②两页都放：①是解析后的第一眼，②是补完维度后的收口）
  const qcCard = collect && <>
    {(collect.unknown_files.length > 0 || (collect.new_suppliers || []).length > 0) && <div style={{ border: '2px solid var(--red,#c0392b)', background: 'var(--red-bg,#fbecea)', borderRadius: 10, padding: '10px 14px' }}>
      <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--red,#c0392b)', marginBottom: 4 }}>🆕 发现新供应商，已自动通知核算组建档</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        {collect.unknown_files.map((fn, i) => <span key={'f' + i} style={{ fontSize: 12.5, background: '#fff', border: '1px solid var(--red-line,#e6b7b0)', borderRadius: 6, padding: '3px 9px' }}>{fn}</span>)}
        {(collect.new_suppliers || []).map((x, i) => <span key={'s' + i} style={{ fontSize: 12.5, background: '#fff', border: '1px solid var(--red-line,#e6b7b0)', borderRadius: 6, padding: '3px 9px' }}><b>{x.简称}</b>·{x.行数}行·{money(x.金额)}</span>)}
      </div>
      <div style={{ fontSize: 12, color: '#7a5a12' }}>核算组建档后：账单类的重传该文件；长表类的「载入」继续。
        通知回执：钉钉 {collect.notify && collect.notify.dingtalk ? (collect.notify.dingtalk.sent ? '✓已发' : '未配置') : '—'}；邮件 {collect.notify && collect.notify.email ? (collect.notify.email.sent ? '✓已发' : '未配置') : '—'}</div>
    </div>}
    {collect.complete
      ? <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', border: '1px solid var(--green-line,#cbe4d5)', background: 'var(--green-bg,#e8f4ee)', borderRadius: 10, padding: '10px 14px' }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--green,#1f7a55)' }}>✓ 归集完成</span>
        <span style={{ fontSize: 12.5 }}>{(rows || []).length} 行 · 含税合计 <b>{money(total)}</b></span>
        {submitted
          ? <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--blue,#2c6bcf)' }}>已提交给核算组 ✓（等核算组检查录入）</span>
          : <button onClick={doSubmit} disabled={!!busy || !canUp} style={{ ...btn, background: 'var(--green,#1f7a55)', color: '#fff', borderColor: 'var(--green,#1f7a55)', fontWeight: 600 }}>
            {busy === 'submit' ? '提交中…' : '✔ 保存并提交给核算组'}</button>}
        {savedAt && <span style={{ fontSize: 11.5, color: '#8a8880' }}>改动已自动保存（{savedAt}）</span>}
      </div>
      : <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', border: '1px solid var(--amber-line,#e6cfa6)', background: 'var(--amber-bg,#f8f0e0)', borderRadius: 10, padding: '10px 14px' }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--amber,#a35a00)' }}>⚠ 还没归集完成：{collect.issues.join('；')}</span>
        {page === 1 && pend.length > 0 && <button onClick={() => setPage(2)} style={{ ...btn, borderColor: 'var(--amber,#a35a00)', color: 'var(--amber,#a35a00)', fontWeight: 600 }}>去「② 计提表」补齐 →</button>}
      </div>}
  </>

  const tabBtn = (k, label, badge) => <button key={k} onClick={() => setPage(k)}
    style={{ ...btn, padding: '7px 18px', fontWeight: page === k ? 700 : 400, position: 'relative',
      background: page === k ? 'var(--accent,#4b53c4)' : '#fff', color: page === k ? '#fff' : '#3a3934',
      borderColor: page === k ? 'var(--accent,#4b53c4)' : '#cfcdc4' }}>
    {label}{badge ? <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 700, background: 'var(--red,#c0392b)', color: '#fff', borderRadius: 9, padding: '1px 7px' }}>{badge}</span> : null}
  </button>

  return <div>
    <div className="head" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap' }}>
      <div><div className="h-title">账单上传</div>
        <div className="h-sub">物流部工作台：① 传复核无误的账单（或按模板填的计提账单）→ 系统转计提长表+质检 → ② 计提表补维度 → 提交给核算组 → ③ 汇总表发起付款提醒；④ 每类通知发给谁。做账科目/录金蝶由核算组在「物流计提」完成。</div></div>
      <PeriodPicker year={year} period={month} source="logi" onChange={pickMonth} />
    </div>
    <div className="body">
      {err && <div style={{ background: '#fcebeb', color: '#a32d2d', border: '0.5px solid #f0c4c4', borderRadius: 8, padding: '8px 12px', fontSize: 13 }}>{err}</div>}
      {!canUp && <div style={{ fontSize: 12.5, color: 'var(--amber,#b4690e)' }}>你的账号没有「上传物流计提表」权限，请联系管理员。</div>}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {tabBtn(1, '① 解析列表 · 上传')}
        {tabBtn(2, '② 计提表（长表）', pend.length || null)}
        {tabBtn(3, '③ 汇总表 · 付款')}
        {tabBtn(4, '④ 通知设置')}
        <span style={{ fontSize: 12, padding: '2px 10px', borderRadius: 12, background: 'var(--accent-soft,#edeefb)', color: 'var(--accent,#4b53c4)', fontWeight: 600 }}>🔒 {year} 年 {month} 月</span>
        {uploadId && <span style={{ fontSize: 11.5, color: '#8a8880' }}>当前批次 #{uploadId}{submitted ? '（已提交）' : ''}</span>}
      </div>

      {/* ============ ① 解析列表 · 上传 ============ */}
      {page === 1 && <>
        {/* 供应商生命周期矩阵（V2.229）：一行一商，从左到右点亮 初始账单→计提→核对定稿→发票→付款 */}
        {mx && (mx.suppliers || []).length > 0 && <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{ padding: '9px 14px', background: 'var(--bg-sub,#f6f6f3)', fontSize: 13, fontWeight: 600 }}>
            {year} 年 {month} 月供应商进度——每家走到哪、卡在哪，一眼见底</div>
          <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
            <thead><tr>{['付款主体', '供应商', '初始账单', '计提', '核对定稿', '发票（登记＋PDF/扫描件）', '付款'].map(h => <th key={h} style={cth}>{h}</th>)}</tr></thead>
            <tbody>{(mx.suppliers || []).map(s => {
              const b = s.初始账单, a = s.计提, v = s.定稿, iv = s.发票, p = s.付款
              return <React.Fragment key={rowKey(s)}>
                <tr style={{ background: !b.已到 && s.应到 ? 'var(--red-bg,#fbecea)' : '#fff' }}>
                  <td style={ctd}>{s.主体 === '待补' ? <span style={{ color: 'var(--red,#c0392b)', fontWeight: 600 }}>待补</span> : s.主体}</td>
                  <td style={ctd} title={s.全名}><b>{s.简称}</b> <span style={{ color: '#a3a199', fontSize: 11 }}>{s.可解析 ? '自动解析' : '模板通道'}</span></td>
                  <td style={ctd}>{b.已到
                    ? <span title={b.文件}>✓ <span style={{ color: '#77756e' }}>{b.时间}</span> · {b.票数} 票 · <b>{money(b.金额)}</b></span>
                    : s.应到 ? <span style={{ color: 'var(--red,#c0392b)', fontWeight: 600 }}>未到——上月有费用，记得催</span>
                      : <span style={{ color: '#a3a199' }}>未到</span>}</td>
                  <td style={ctd}>{!a.行数 ? '—'
                    : a.待补 ? <span style={{ color: 'var(--amber,#a35a00)', fontWeight: 600 }}>待补 {a.待补} 行</span>
                      : a.已提交 ? <span style={{ color: 'var(--blue,#2c6bcf)', fontWeight: 600 }}>已提交 ✓</span>
                        : <span style={{ color: 'var(--green,#1f7a55)' }}>✓ 就绪 {money(a.含税)}</span>}</td>
                  <td style={ctd}>
                    {!b.已到 ? '—'
                      : v.已确认 ? <span style={{ color: 'var(--green,#1f7a55)', fontWeight: 600 }}>✓ 定稿 {money(v.金额)}
                        {v.与计提差 != null && Math.abs(v.与计提差) >= 0.01 &&
                          <span style={{ color: 'var(--amber,#a35a00)' }} title={`${v.说明 || '未填原因'}——计提已入账不动，差异由核算组次月冲`}>　与计提差 {money(v.与计提差)}</span>}
                        <a onClick={() => doUnverify(s)} style={{ marginLeft: 6, fontSize: 11, color: '#a3a199', cursor: 'pointer' }}>撤</a></span>
                        : <>
                          <button onClick={() => doVerify(s)} disabled={!canUp} style={{ ...btn, padding: '3px 10px', fontSize: 12 }}
                            title="该主体核对无异议、金额=计提金额，点这里一键定稿">✓ 确认无误</button>
                          <button onClick={() => openVf(s)} disabled={!canUp} style={{ ...btn, padding: '3px 10px', fontSize: 12, marginLeft: 4, color: 'var(--amber,#a35a00)', borderColor: 'var(--amber-line,#e6cfa6)' }}
                            title="对账后金额有出入：手登正确金额+传正确版/盖章版账单存档——计提行不动，差异次月冲">金额有出入…</button>
                        </>}
                    {(v.附件 || []).length > 0 && <span style={{ marginLeft: 6 }}>
                      {v.附件.map(f => <span key={f.id} style={{ fontSize: 11, marginRight: 4, whiteSpace: 'nowrap' }}>
                        <a href={`/api/logistics-accrual/invoice-file?fid=${f.id}`} style={{ color: 'var(--accent,#4b53c4)' }} title={`结算账单 · ${f.uploaded_by} 传于 ${f.ts}`}>📎{f.filename}</a>
                        <a onClick={() => doInvFileDel(s, f)} style={{ color: '#a3a199', cursor: 'pointer', marginLeft: 1 }}>✕</a></span>)}
                    </span>}
                  </td>
                  <td style={ctd}>
                    {!v.已确认 ? <span style={{ color: '#a3a199' }}>—（先定稿）</span>
                      : iv.票号 ? <span>{iv.票号} · <b>{money(iv.金额)}</b>
                        {iv.差异 != null && Math.abs(iv.差异) >= 0.01 && <span style={{ color: 'var(--amber,#a35a00)', fontWeight: 600 }}>　差 {money(iv.差异)}</span>}
                        <a onClick={() => openInv(s)} style={{ marginLeft: 6, fontSize: 11, color: 'var(--accent,#4b53c4)', cursor: 'pointer' }}>改</a>
                        <a onClick={() => doClearInv(s)} style={{ marginLeft: 4, fontSize: 11, color: '#a3a199', cursor: 'pointer' }}>清</a></span>
                        : <button onClick={() => openInv(s)} disabled={!canUp} style={{ ...btn, padding: '3px 10px', fontSize: 12 }}>＋登记发票</button>}
                    {(iv.附件 || []).length > 0 && <span style={{ marginLeft: 6 }}>
                      {iv.附件.map(f => <span key={f.id} style={{ fontSize: 11, marginRight: 4, whiteSpace: 'nowrap' }}>
                        <a href={`/api/logistics-accrual/invoice-file?fid=${f.id}`} style={{ color: 'var(--accent,#4b53c4)' }} title={`${f.uploaded_by} 传于 ${f.ts}`}>📎{f.filename}</a>
                        <a onClick={() => doInvFileDel(s, f)} style={{ color: '#a3a199', cursor: 'pointer', marginLeft: 1 }}>✕</a></span>)}
                    </span>}
                  </td>
                  <td style={ctd}>{p.已提醒 ? <span style={{ color: 'var(--blue,#2c6bcf)', fontWeight: 600 }}>已提醒 <span style={{ fontWeight: 400, color: '#77756e' }}>{p.时间}</span>
                    <a onClick={() => doUnpay(s)} style={{ marginLeft: 5, fontSize: 11, color: '#a3a199', cursor: 'pointer' }}>撤</a></span>
                    : p.可申请 ? <span style={{ color: 'var(--green,#1f7a55)', fontWeight: 600 }}>可申请 → ③汇总表</span>
                      : <span style={{ color: '#a3a199' }}>🔒 {!a.行数 ? '待计提' : a.待补 ? '待补维度' : !v.已确认 ? '待定稿' : '待开票'}</span>}</td>
                </tr>
                {vfEdit && vfEdit.key === rowKey(s) && <tr><td colSpan={7} style={{ ...ctd, background: 'var(--amber-bg,#f8f0e0)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--amber,#a35a00)' }}>「{s.主体} × {s.简称}」按异议定稿</span>
                    <span style={{ fontSize: 12 }}>——计提已入账 {money(s.计提.含税)}，<b>不动</b>；差异次月由核算组冲。</span>
                    <label style={{ fontSize: 12, fontWeight: 600 }}>第一步·传修正版账单(Excel)自动找差：<input type="file" accept=".xlsx,.xls" disabled={!!busy}
                      onChange={e => { doDiffParse(s, e.target.files[0]); e.target.value = '' }} style={{ fontSize: 12 }} /></label>
                    {busy === 'diff' && <span style={{ fontSize: 12 }}>比对中…</span>}
                  </div>
                  {vfDiff && (() => {
                    const lines = (vfDiff.diff || []).filter(d => Math.abs(d.差额) >= 0.01)
                    const others = Object.entries(vfDiff.subject_totals || {}).filter(([k, t]) => k !== s.主体 && Math.abs(t.差额) >= 0.01)
                    return <div style={{ background: '#fff', border: '1px solid var(--amber-line,#e6cfa6)', borderRadius: 8, padding: '6px 10px', marginBottom: 6 }}>
                      {lines.length === 0
                        ? <span style={{ fontSize: 12.5, color: 'var(--green,#1f7a55)', fontWeight: 600 }}>✓ 修正版与计提逐类核对：没有差异（合计一致）</span>
                        : <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
                          <thead><tr>{['主体', '费用归属', '业务线', '描述', '计提', '修正版', '差额'].map(h => <th key={h} style={{ ...cth, padding: '3px 8px' }}>{h}</th>)}</tr></thead>
                          <tbody>{lines.map((d, i) => <tr key={i} style={{ background: d.主体 === s.主体 ? 'var(--amber-bg,#f8f0e0)' : '#fff' }}>
                            <td style={{ ...ctd, padding: '3px 8px' }}>{d.主体}</td><td style={{ ...ctd, padding: '3px 8px' }}>{d.费用归属}</td>
                            <td style={{ ...ctd, padding: '3px 8px' }}>{d.业务线}</td><td style={{ ...ctd, padding: '3px 8px' }}>{d.描述}</td>
                            <td style={{ ...ctd, padding: '3px 8px', textAlign: 'right' }}>{money(d.计提)}</td>
                            <td style={{ ...ctd, padding: '3px 8px', textAlign: 'right' }}>{money(d.修正)}</td>
                            <td style={{ ...ctd, padding: '3px 8px', textAlign: 'right', fontWeight: 700, color: 'var(--amber,#a35a00)' }}>{d.差额 > 0 ? '+' : ''}{money(d.差额)}</td>
                          </tr>)}</tbody>
                        </table>}
                      <div style={{ fontSize: 12, marginTop: 4 }}>
                        本主体（{s.主体}）修正版合计 <b>{money(((vfDiff.subject_totals || {})[s.主体] || {}).修正)}</b>（已自动填入定稿金额）；修正版文件已存档为结算账单📎。
                        {others.length > 0 && <span style={{ color: 'var(--red,#c0392b)', fontWeight: 600 }}>　⚠ {others.map(([k, t]) => `${k} 也差 ${money(t.差额)}`).join('；')}——到那行也要按异议定稿。</span>}
                      </div>
                    </div>
                  })()}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12 }}>第二步·实际应付</span>
                    <input value={vfForm.amt} onChange={e => setVfForm(f => ({ ...f, amt: e.target.value }))}
                      style={{ padding: '3px 8px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 110, textAlign: 'right' }} />
                    　差异原因 <input value={vfForm.note} onChange={e => setVfForm(f => ({ ...f, note: e.target.value }))} placeholder="传修正版会自动预填，可改（核算组冲差要看）"
                      style={{ padding: '3px 8px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 320 }} />
                    　<button onClick={() => saveVf(s)} style={{ ...btn, background: 'var(--amber,#a35a00)', color: '#fff', borderColor: 'var(--amber,#a35a00)', padding: '3px 12px', fontSize: 12 }}>定稿</button>
                    <button onClick={() => { setVfEdit(null); setVfDiff(null) }} style={{ ...btn, padding: '3px 12px', fontSize: 12, marginLeft: 4 }}>取消</button>
                    　<label style={{ fontSize: 12 }}>盖章版/其它凭证：<input type="file" accept=".pdf,.jpg,.jpeg,.png,.ofd,.xlsx,.xls" disabled={!!busy}
                      onChange={e => { doInvFile(s, e.target.files[0], '结算账单'); e.target.value = '' }} style={{ fontSize: 12 }} /></label>
                    <span style={{ fontSize: 11, color: '#8a8880' }}>（附件只存档不解析——计提数据不会被顶掉）</span>
                  </div>
                </td></tr>}
                {invEdit && invEdit.key === rowKey(s) && <tr><td colSpan={7} style={{ ...ctd, background: 'var(--bg-sub,#fafaf7)' }}>
                  <span style={{ fontSize: 12.5, marginRight: 8, fontWeight: 600 }}>登记「{s.主体} × {s.简称}」发票：</span>
                  票号 <input value={invForm.no} onChange={e => setInvForm(f => ({ ...f, no: e.target.value }))} placeholder="多张用；隔开" style={{ padding: '3px 8px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 200 }} />
                  　金额(含税) <input value={invForm.amt} onChange={e => setInvForm(f => ({ ...f, amt: e.target.value }))} style={{ padding: '3px 8px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 110, textAlign: 'right' }} />
                  　开票日期 <input value={invForm.date} onChange={e => setInvForm(f => ({ ...f, date: e.target.value }))} placeholder="2026-08-15" style={{ padding: '3px 8px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 110 }} />
                  　<button onClick={saveInv} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496', padding: '3px 12px', fontSize: 12 }}>保存</button>
                  <button onClick={() => setInvEdit(null)} style={{ ...btn, padding: '3px 12px', fontSize: 12, marginLeft: 6 }}>取消</button>
                  　<label style={{ fontSize: 12 }}>附上 PDF/扫描件：<input type="file" accept=".pdf,.jpg,.jpeg,.png,.ofd" disabled={!!busy}
                    onChange={e => { doInvFile(s, e.target.files[0]); e.target.value = '' }} style={{ fontSize: 12 }} /></label>
                  <span style={{ fontSize: 11, color: '#8a8880' }}>（附件即传即存，可多份；票号金额仍要登记——用来跟定稿金额自动比差）</span>
                </td></tr>}
              </React.Fragment>
            })}</tbody>
          </table></div>
        </div>}

        {/* 通道一：复核无误的账单（各物流商账单包，系统自动解析） */}
        <div style={{ background: '#fff', border: '1.5px solid #305496', borderRadius: 10, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, fontWeight: 700, color: '#305496' }}>通道一 · 复核无误的账单</span>
          <span style={{ fontSize: 12, color: '#77756e' }}>各物流商核对后的原始账单，可多选，系统自动转计提长表</span>
          <a href="/api/logistics-accrual/annotation-spec" style={{ ...btn, textDecoration: 'none', color: '#3a3934', display: 'inline-block', padding: '4px 12px', fontSize: 12 }}
            title="账单「类型」列照这套标准写，系统 100% 认得维度——实时从基础数据·标注翻译表生成，每月下最新的">⬇ 标注规范</a>
          <input type="file" accept=".xlsx,.xls" multiple disabled={!canUp} onChange={e => setBillFiles([...e.target.files])} style={{ fontSize: 13 }} />
          <button onClick={doBills} disabled={!!busy || !canUp} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496' }}>
            {busy === 'bills' ? '解析中…' : `解析账单${billFiles.length ? `（${billFiles.length} 个）` : ''}`}</button>
        </div>
        {/* 通道二：计提账单（按模板填的计提长表）＋ 模板下载 */}
        <div style={{ background: 'var(--bg-sub,#fafaf7)', border: '1px dashed var(--line-strong,#cfcdc4)', borderRadius: 10, padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, fontWeight: 700 }}>通道二 · 计提账单（模板长表）</span>
          <span style={{ fontSize: 12, color: '#77756e' }}>货拉拉/个人报销等无账单费用、暂不能自动解析的物流商，按模板整理后上传</span>
          <a href="/api/logistics-accrual/template" style={{ ...btn, textDecoration: 'none', color: '#3a3934', display: 'inline-block' }}>⬇ 下载模板</a>
          <input type="file" accept=".xlsx" disabled={!canUp} onChange={e => setLfFile(e.target.files[0])} style={{ fontSize: 13 }} />
          <button onClick={doLf} disabled={!!busy || !canUp} style={btn}>{busy === 'lf' ? '解析中…' : '解析计提账单'}</button>
        </div>

        {/* 本次解析的文件状态 + 质检 */}
        {perFile && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {perFile.map((pf, i) => <span key={i} style={{ fontSize: 12, border: '0.5px solid ' + (pf.状态 === '已解析' ? '#cbe4d5' : '#e7b9b9'), background: pf.状态 === '已解析' ? '#eef7f0' : '#fcebeb', borderRadius: 6, padding: '4px 9px' }}>
            {pf.文件}　{pf.状态}·{pf.票数}票·{money(pf.金额)}</span>)}
        </div>}
        {qcCard}

        {/* 批次列表已撤（V2.228 业务方定）——只留一行回执说明现场是自动恢复的 */}
        {loadedInfo && <div style={{ fontSize: 12, color: '#8a8880' }}>
          已自动恢复本月最新批次：{loadedInfo.operator} 于 {loadedInfo.ts} 上传（{loadedInfo.stats.文件数} 个文件 · {loadedInfo.stats.票数} 票 · {money(loadedInfo.stats.含税合计)}）{submitted ? '，已提交给核算组' : ''}——重新上传会另起新批次并覆盖当前现场。
        </div>}
      </>}

      {/* ============ ② 计提表（长表） ============ */}
      {page === 2 && <>
        {!rows && <div style={{ fontSize: 13, color: '#77756e', padding: '18px 0' }}>本月还没有计提表——到「① 解析列表 · 上传」上传账单即可（传过的会自动恢复）。</div>}
        {rows && qcCard}
        {/* 待处理区：物流部补业务三件套（主体/费用归属/业务线），科目部门自动 */}
        {pend.length > 0 && <div style={{ border: '2px solid var(--red,#c0392b)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{ padding: '9px 14px', background: 'var(--red-bg,#fbecea)', fontSize: 13.5, fontWeight: 700, color: 'var(--red,#c0392b)' }}>
            待处理 {pend.length} 行 · {money(pend.reduce((a, x) => a + (x.v.含税 || 0), 0))}——选好三个下拉即归位</div>
          {pend.map(({ v, i }, k) => {
            const vv = { ...v, _oi: i }
            return <div key={i} style={{ padding: '9px 14px', borderTop: k ? '1px dashed var(--red-line,#e6b7b0)' : 'none', background: '#fff', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 12.5 }}>
              <b>{v.物流商}</b><span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{money(v.含税)}</span>
              <span>主体{sel(vv, '主体', SUBJ3)}</span>
              <span>费用归属{sel(vv, '费用归属', FEE13)}</span>
              <span>业务线{sel(vv, '业务线', BIZ10)}</span>
              {v.税率来源 === '缺税率' && <span style={{ color: 'var(--red,#c0392b)', fontSize: 12 }}>缺税率——请核算组在基础数据补</span>}
              {v.备注 && <span style={{ color: '#8a8880', fontSize: 11.5 }}>{v.备注}</span>}
            </div>
          })}
        </div>}
        {/* 明细预览（只读；做账科目等由核算组把关） */}
        {rows && rows.length > 0 && <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{ padding: '9px 14px', background: 'var(--bg-sub,#f6f6f3)', fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span>计提长表预览（{rows.length} 行 · 合计 {money(total)} · 做账科目由核算组复核）</span>
            <a href={`/api/logistics-accrual/export-long-form?year=${year}&month=${month}`}
              style={{ ...btn, textDecoration: 'none', color: '#3a3934', padding: '3px 12px', fontSize: 12, fontWeight: 400 }}>⬇ 导出长表(Excel)</a>
            <span style={{ fontSize: 11.5, color: '#8a8880', fontWeight: 400 }}>解析不了的商（新商没解析器时）：导出 → Excel 里手工补行 → 回「①通道二」重新上传，全量兜底。</span>
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 420, overflowY: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
            <thead><tr>{['主体', '物流商', '费用归属', '业务线', '业务描述', '含税', '税率', '备注'].map(h => <th key={h} style={cth}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((v, i) => <tr key={i} style={{ background: v.可录入 === false ? '#fdf6f6' : '#fff' }}>
              <td style={ctd}>{v.主体 || '—'}</td><td style={{ ...ctd, fontWeight: 600 }}>{v.物流商}</td>
              <td style={ctd}>{v.费用归属 || <span style={{ color: 'var(--red,#c0392b)' }}>待补</span>}</td>
              <td style={ctd}>{v.业务线 || '—'}</td><td style={ctd}>{v.业务描述 || ''}</td>
              <td style={{ ...ctd, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(v.含税)}</td>
              <td style={{ ...ctd, textAlign: 'right' }}>{v.税率 == null ? '—' : Math.round(v.税率 * 100) + '%'}</td>
              <td style={{ ...ctd, fontSize: 11.5, color: '#8a8880', whiteSpace: 'normal', maxWidth: 260 }}>{v.备注}</td>
            </tr>)}</tbody>
          </table></div>
        </div>}
      </>}

      {/* ============ ③ 汇总表 · 付款 ============ */}
      {page === 3 && <>
        {!rows && <div style={{ fontSize: 13, color: '#77756e', padding: '18px 0' }}>本月还没有数据——到「① 解析列表 · 上传」上传账单即可（传过的会自动恢复）。</div>}
        {rows && paySum.length > 0 && <>
          <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, overflow: 'hidden' }}>
            <div style={{ padding: '9px 14px', background: 'var(--bg-sub,#f6f6f3)', fontSize: 13, fontWeight: 600 }}>
              {year} 年 {month} 月物流费汇总（按付款主体 × 物流商）——勾选后可发起钉钉付款提醒</div>
            <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
              <thead><tr>
                <th style={{ ...cth, width: 34 }}><input type="checkbox" disabled={!canUp}
                  checked={paySum.some(s => payGate(s)) && paySum.filter(s => payGate(s)).every(s => paySel[s.key])}
                  onChange={e => { const on = e.target.checked; const ns = {}; if (on) paySum.filter(s => payGate(s)).forEach(s => { ns[s.key] = true }); setPaySel(ns) }} /></th>
                {['付款主体', '物流商', '明细行数', '票数', '含税金额', '状态'].map(h => <th key={h} style={cth}>{h}</th>)}
              </tr></thead>
              <tbody>{paySum.map(s => <tr key={s.key} style={{ background: paySel[s.key] ? 'var(--accent-soft,#edeefb)' : '#fff' }}>
                <td style={{ ...ctd, textAlign: 'center' }}><input type="checkbox" disabled={!canUp || !payGate(s)} checked={!!paySel[s.key]}
                  title={payGate(s) ? '' : '核对定稿＋登记发票后才能发起付款（去①页点亮）'}
                  onChange={e => setPaySel(p => ({ ...p, [s.key]: e.target.checked }))} /></td>
                <td style={ctd}>{s.主体}</td>
                <td style={ctd}><b>{s.全名}</b>{s.物流商 && s.物流商 !== s.全名 ? <span style={{ color: '#8a8880', fontSize: 11.5 }}>（{s.物流商}）</span> : null}</td>
                <td style={{ ...ctd, textAlign: 'right' }}>{s.行数}</td>
                <td style={{ ...ctd, textAlign: 'right' }}>{s.票数 || '—'}</td>
                <td style={{ ...ctd, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{money(s.含税)}</td>
                <td style={ctd}>{(() => {
                  const d = docBy[`${s.主体}|${s.物流商}`]
                  if (s.待补) return <span style={{ color: 'var(--amber,#a35a00)', fontWeight: 600 }}>待补 {s.待补} 行</span>
                  if (!d || !d.定稿.已确认) return <span style={{ color: 'var(--amber,#a35a00)' }}>待定稿（①页确认无误）</span>
                  if (!d.发票.票号) return <span style={{ color: 'var(--amber,#a35a00)' }}>待开票（①页登记发票）</span>
                  if (d.付款.已提醒) return <span style={{ color: 'var(--blue,#2c6bcf)', fontWeight: 600 }}>已提醒 <span style={{ fontWeight: 400, color: '#77756e' }}>{d.付款.时间}</span></span>
                  return <span style={{ color: 'var(--green,#1f7a55)', fontWeight: 600 }}>可申请 ✓</span>
                })()}</td>
              </tr>)}</tbody>
              <tfoot><tr style={{ background: 'var(--bg-sub,#f6f6f3)', fontWeight: 700 }}>
                <td style={ctd}></td><td style={ctd} colSpan={2}>合计（{paySum.length} 笔）</td>
                <td style={{ ...ctd, textAlign: 'right' }}>{rows.length}</td>
                <td style={{ ...ctd, textAlign: 'right' }}>{paySum.reduce((a, s) => a + (s.票数 || 0), 0) || '—'}</td>
                <td style={{ ...ctd, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(total)}</td><td style={ctd}></td>
              </tr></tfoot>
            </table></div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', border: '1px solid var(--line,#e6e4dc)', background: '#fff', borderRadius: 10, padding: '10px 14px' }}>
            <span style={{ fontSize: 12.5 }}>已勾选 <b>{paySelRows.length}</b> 笔 · 合计 <b style={{ fontVariantNumeric: 'tabular-nums' }}>{money(paySelTotal)}</b> 元</span>
            <button onClick={doPayRemind} disabled={!!busy || !canUp || !paySelRows.length}
              style={{ ...btn, background: paySelRows.length ? '#305496' : '#f0efe9', color: paySelRows.length ? '#fff' : '#a3a199', borderColor: paySelRows.length ? '#305496' : '#cfcdc4', fontWeight: 600 }}>
              {busy === 'pay' ? '发起中…' : '📣 发起钉钉付款提醒'}</button>
            <span style={{ fontSize: 11.5, color: '#8a8880' }}>只有「核对定稿＋发票已登记」的行才能勾选（闸门）；当前为钉钉/邮件提醒核算组安排付款，对接钉钉付款审批自动起单待「账单核对」三期一起接。</span>
          </div>
        </>}
      </>}

      {/* ============ ④ 通知设置 ============ */}
      {page === 4 && <>
        <div style={{ border: '1px solid var(--blue-line,#bcd4f4)', background: 'var(--blue-bg,#f0f6fe)', borderRadius: 8, padding: '10px 14px', fontSize: 12.5, lineHeight: 1.8 }}>
          <b>每类通知发给谁，在这里配</b>——改完点「保存」立即生效，不用动服务器。留空的场景发给<b>公共名单</b>
          （当前：钉钉 {nf && nf.fallback && (nf.fallback.mobiles.length || nf.fallback.userids.length) ? [...nf.fallback.mobiles, ...nf.fallback.userids].join('、') : '（没配）'}；
          邮件 {nf && nf.fallback && nf.fallback.emails.length ? nf.fallback.emails.join('、') : '（没配）'}）。
          钉钉应用凭证仍在服务器配置文件里，这里只管收件人；手机号须是钉钉注册号，多人用逗号隔开。
          <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {nf && nf.passcode_set === false
              ? <span style={{ color: 'var(--amber,#a35a00)', fontWeight: 600 }}>⚠ 服务器还没设置修改口令（conf.ini [notify] passcode 为空）——页面暂时只能看不能改，请联系管理员配置。</span>
              : <label style={{ fontSize: 12.5, fontWeight: 600 }}>🔑 修改口令
                <input type="password" value={nfPass} onChange={e => setNfPass(e.target.value)} placeholder="管理员在 conf.ini 定义"
                  style={{ marginLeft: 6, padding: '4px 9px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 180 }} />
                <span style={{ fontWeight: 400, color: '#77756e', marginLeft: 6 }}>保存收件人须口令（防随手改动把通知发丢）；发送测试不用。</span></label>}
          </div>
        </div>
        {!nf && <div style={{ fontSize: 13, color: '#77756e', padding: '12px 0' }}>加载中…</div>}
        {nf && (nf.scenes || []).map(sc => <div key={sc.scene} style={{ border: '1px solid var(--line,#e6e4dc)', background: '#fff', borderRadius: 10, padding: '12px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
            <span style={{ fontSize: 13.5, fontWeight: 700 }}>{sc.scene}</span>
            <span style={{ fontSize: 12, color: '#77756e' }}>{sc.desc}</span>
            {sc.updated_at && <span style={{ fontSize: 11, color: '#a3a199' }}>上次改动：{sc.updated_by} 于 {sc.updated_at}</span>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <label style={{ fontSize: 12.5 }}>钉钉手机号
              <input value={nfVal(sc, 'mobiles')} disabled={!canUp} placeholder="留空＝公共名单"
                onChange={e => setNfEdit(p => ({ ...p, [sc.scene]: { mobiles: e.target.value, emails: nfVal(sc, 'emails') } }))}
                style={{ marginLeft: 6, padding: '4px 9px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 250 }} /></label>
            <label style={{ fontSize: 12.5 }}>邮箱
              <input value={nfVal(sc, 'emails')} disabled={!canUp} placeholder="留空＝公共名单"
                onChange={e => setNfEdit(p => ({ ...p, [sc.scene]: { mobiles: nfVal(sc, 'mobiles'), emails: e.target.value } }))}
                style={{ marginLeft: 6, padding: '4px 9px', border: '0.5px solid #cfcdc4', borderRadius: 6, width: 280 }} /></label>
            <button onClick={() => saveNf(sc)} disabled={!!busy || !canUp || !nfChanged(sc) || (nf && nf.passcode_set === false)}
              style={{ ...btn, background: nfChanged(sc) ? '#305496' : '#fff', color: nfChanged(sc) ? '#fff' : '#a3a199', borderColor: nfChanged(sc) ? '#305496' : '#cfcdc4' }}>
              {busy === 'nf' ? '保存中…' : '保存'}</button>
            <button onClick={() => doTestNotify(sc)} disabled={!!busy || !canUp} style={btn}
              title="按已保存的名单真发一条测试消息，收到即配置正确">{busy === 'nftest' ? '发送中…' : '发送测试'}</button>
            {nfChanged(sc) && <span style={{ fontSize: 11.5, color: 'var(--amber,#a35a00)' }}>改了还没保存</span>}
          </div>
        </div>)}
      </>}
    </div>
  </div>
}
