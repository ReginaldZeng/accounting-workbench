// [Change Log] Date:2026-08-06 Author:Claude/c Version:V2.195
// 账单直采落地（重构方案 v2.1）：①步加「核对后账单包」多文件主入口（手填模板降兜底）；
// ②步扩为 映射与税率 四 tab（费用归属映射/业务线/标注翻译/税率）；③步活表化——点行展开=分录+维度编辑器
// （费用归属/业务线/主体/科目/部门 下拉可改，服务端 row-refresh 重算，改动可「采纳入维表」）+底部做账去向汇总。
// 账单行=A期 voucher 同构（res.vouchers 全链复用），④勾选录入 ⑤打印 原样吃。
// [Change Log] Date:2026-07-06 Author:Claude/c Version:V2.30
// 页面重构对齐「对账程序」骨架：.head/.body 全宽自适应（去 1180 限宽）、复用全局 .steps 步骤条(可点跳转)、
// 五步各自独立成页（①导入排查 ②税率维护 ③解析复核 ④勾选录入 ⑤打印附件），状态在父组件共享。
// [Change Log] Date:2026-07-06 Author:Claude/c Version:V2.29 5步工作流+权限分离+税率维表+一键录入(见台账)
// [Change Log] Date:2026-07-05 Author:Claude/c Version:V2.20~V2.28 解析/复核清单/打印(见台账)
import React, { useEffect, useState } from 'react'
import PeriodPicker from '../components/PeriodPicker.jsx'
import {
  parseLogistics, checkLogisticsSuppliers, getLogisticsRates, saveLogisticsRate,
  deleteLogisticsRate, postLogistics, getLogisticsPosted, unpostLogistics,
  parseBills, refreshLogisticsRow, adoptFeeMap, getExpenseRatio,
  getBillUploads, loadBillUpload, saveBillUploadRows, parseLongForm, submitBillUpload,
} from '../api.js'

const MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
// 13 类费用归属闭环（重构方案 v2.1 §2.1，D-1）
const FEE13 = ['销售出库费用', '成品入库费用', '原料入库费用', '成品仓储费用', '原料仓储费用',
  '成品调拨费用', '原料调拨费用', '出库装卸费用', '成品入库装卸费用', '原料入库装卸费用',
  '研发设备采购', '设备调拨费用', '其它']
const BIZ10 = ['植物肉', '鲜食', '零售', '小料', '豆蛋制品', '电商', '山姆零售', 'kikiherb', '海外', '—']
const SUBJ3 = ['深圳星期零', '孝感星期九', '深圳星期九']
const ACC5 = ['6601 销售费用', '6401 主营业务成本', '5101 制造费用', '6604 研发费用', '6402 其他业务支出']
const DEPT5 = ['永续物流中心', '仓储物流部', '茶饮小料部', '永续供应中心', '永续研发中心']
const ITEM6 = ['出库运费', '入库运费', '货物仓储费', '研发外购', '搬运费', '原辅料及包装物']
const money = n => (n == null || n === '') ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const pct = r => (r == null ? '' : (Math.round(r * 10000) / 100) + '%')

function Metric({ label, value, tone }) {
  const col = tone === 'ok' ? '#1a7f4b' : tone === 'warn' ? '#b4690e' : tone === 'bad' ? '#c0392b' : 'var(--ink,#2c2c2a)'
  return (<div style={{ background: 'var(--bg-sub,#f6f6f3)', borderRadius: 8, padding: '10px 14px', minWidth: 110 }}>
    <div style={{ fontSize: 12, color: 'var(--ink-3,#77756e)' }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 600, color: col, marginTop: 2 }}>{value}</div>
  </div>)
}

// 物流计提五步条：复用对账程序 .steps/.step 全局样式，全部可点跳转
const L_ORDER = [1, 2, 3, 4, 5]
const L_LABEL = { 1: '上传账单', 2: '解析复核·活表', 3: '做账去向与费率', 4: '勾选录入', 5: '打印附件' }
function LSteps({ current, done, sub, onNav }) {
  return (<div className="steps la-noprint">
    {L_ORDER.map(id => {
      const state = id === current ? 'cur' : (done[id] ? 'done' : '')
      const clickable = id !== current
      return <div className={'step ' + state} key={id}
        onClick={() => clickable && onNav(id)}
        title={clickable ? '点击进入「' + L_LABEL[id] + '」' : undefined}
        style={clickable ? { cursor: 'pointer' } : undefined}>
        <div className="num">{state === 'done' ? '✓' : id}</div>
        <div><div className="sn">{L_LABEL[id]}{clickable ? ' ›' : ''}</div><div className="sd">{sub[id] || ''}</div></div>
      </div>
    })}
  </div>)
}

// 模块级工作现场缓存（V2.219）：切到别的菜单组件即卸载、useState 全丢——用户实测"上传完切个页面
// 回来又要重传"。解析结果/勾选/费率等挂这里，切菜单往返不丢；刷新页面才清（刷新=用户主动要新现场）。
const CACHE = {}

export default function LogisticsAccrual({ user }) {
  const can = k => !!(user && (user.role === 'admin' || (user.perms || {})[k]))
  const canUp = can('logistics_upload'), canPost = can('logistics_post')

  const [step, setStep] = useState(CACHE.step || 1)
  const [month, setMonth] = useState(CACHE.month || 7)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [res, setRes] = useState(CACHE.res || null)
  const [sup, setSup] = useState(null)
  const [openV, setOpenV] = useState(null)
  // 账单直采（V2.195 主路径）
  const [billFiles, setBillFiles] = useState([])
  const [billInfo, setBillInfo] = useState(CACHE.billInfo || null)     // {per_file, stats, unknown_files}
  const [edited, setEdited] = useState(CACHE.edited || new Set())    // 人工改过维度的行（_oi），琥珀标+可采纳入维表
  // 税率维表
  const [rates, setRates] = useState([])
  const [rmsg, setRmsg] = useState('')
  const [nr, setNr] = useState({ supplier: '', fee_type: '', rate: '' })
  // B 期·费用率（V2.197 第一刀：分母=BP 各业务线不含税收入）
  const [ratio, setRatio] = useState(CACHE.ratio || null)
  const [ratioBusy, setRatioBusy] = useState(false)
  const [withPrev, setWithPrev] = useState(CACHE.withPrev || false)
  // 步③内两页切换（V2.219 业务方定：做账去向 / 费率看板 点击切换，不上下堆叠）
  const [destTab, setDestTab] = useState(CACHE.destTab || 'dest')
  // 该月上传批次列表（V2.221 月份胶囊配套：谁/何时/传了哪几份，可载入恢复现场）
  const [uploads, setUploads] = useState(CACHE.uploads || null)
  // 当前现场对应的上传批次（V2.223 自动保存的落点）；savedAt=最近一次改动写回时间（回执）
  const [uploadId, setUploadId] = useState(CACHE.uploadId || null)
  const [savedAt, setSavedAt] = useState(CACHE.savedAt || '')
  // 长表质检结果（V2.224 核算组定稿：新增供应商通知回执 + 干净度问题清单）
  const [lfInfo, setLfInfo] = useState(CACHE.lfInfo || null)
  // 账单归集状态（V2.225 定稿流程：归集完成判定 + 新供应商账单通知回执 + 提交给核算组）
  const [collect, setCollect] = useState(CACHE.collect || null)
  // 录入
  const [sel, setSel] = useState(CACHE.sel || new Set())
  const [postPeriod, setPostPeriod] = useState(CACHE.postPeriod ?? null)   // null=跟随会计月份
  const [postYear, setPostYear] = useState(CACHE.postYear || 2026)
  const [postRes, setPostRes] = useState(CACHE.postRes || null)
  // 撤销/删除已录草稿
  const [posted, setPosted] = useState(null)      // {items:[...]} 或 null(未加载)
  const [pSel, setPSel] = useState(new Set())
  const [pBusy, setPBusy] = useState(false)

  const loadRates = () => getLogisticsRates().then(r => setRates(r.rates || [])).catch(() => {})
  useEffect(() => { loadRates() }, [])
  // 工作现场写回缓存（切菜单组件卸载不丢；file/billFiles 是临时选择不缓存）。
  // ⚠必须放在全部相关 useState 之后——deps 数组渲染时即求值，放前面=TDZ 白屏（V2.219 实翻车）
  useEffect(() => {
    Object.assign(CACHE, { step, month, res, billInfo, edited, sel, postPeriod, postYear, postRes, ratio, withPrev, destTab, uploads, uploadId, savedAt, lfInfo, collect })
  }, [step, month, res, billInfo, edited, sel, postPeriod, postYear, postRes, ratio, withPrev, destTab, uploads, uploadId, savedAt, lfInfo, collect])

  const refreshUploads = (y, m) => getBillUploads(y, m).then(r => setUploads(r.uploads || [])).catch(() => setUploads([]))
  useEffect(() => { refreshUploads(postYear, month) }, [])   // 挂载拉一次；切月在 pickMonth 里拉

  // 切换计提月份（V2.221 业务方定：胶囊要"真有用"）——换月=换现场：清空上月解析/勾选/费率，再拉新月的上传批次
  const pickMonth = (y, m) => {
    if (y === postYear && m === month) return
    setPostYear(y); setMonth(m); setPostPeriod(null)
    setRes(null); setBillInfo(null); setSel(new Set()); setEdited(new Set())
    setRatio(null); setPostRes(null); setOpenV(null); setErr(''); setUploads(null)
    setUploadId(null); setSavedAt(''); setLfInfo(null); setCollect(null)
    refreshUploads(y, m)
  }
  // 提交给核算组（V2.225 定稿收口）：批次标已提交 + 邮件/钉钉通知核算组检查录入
  const doSubmit = async () => {
    if (!uploadId) { setErr('当前没有上传批次（先解析账单或载入一批）'); return }
    if (!window.confirm(`确认把 ${postYear} 年 ${month} 月计提长表提交给核算组？\n\n将发送 邮件+钉钉 通知核算组检查并录入金蝶。`)) return
    setErr(''); setBusy('submit')
    try {
      const r = await submitBillUpload(uploadId)
      if (!r.ok) throw new Error(r.msg || '提交失败')
      const n = r.notify || {}
      const dd = n.dingtalk ? (n.dingtalk.sent ? '✓' : `✗(${n.dingtalk.msg || '未配置'})`) : '—'
      const em = n.email ? (n.email.sent ? '✓' : `✗(${n.email.msg || '未配置'})`) : '—'
      alert(`已提交给核算组。通知：钉钉 ${dd}；邮件 ${em}`)
      refreshUploads(postYear, month)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  // 物流部长表上传（V2.224 核算组定稿）：解析+质检；新供应商已由服务端自动通知核算组
  const doLongForm = async () => {
    if (!file) { setErr('请先选择长表文件（计提表模板 .xlsx）'); return }
    setErr(''); setBusy('lf'); setLfInfo(null); setRes(null); setBillInfo(null); setPostRes(null); setEdited(new Set())
    try {
      const r = await parseLongForm(file, month, postYear)
      if (!r.ok) throw new Error(r.msg || '长表解析失败')
      const rows = r.rows || []
      setLfInfo({ new_suppliers: r.new_suppliers || [], dirty: r.dirty || [], notify: r.notify, clean: r.clean, stats: r.stats })
      setBillInfo({ per_file: r.per_file || [], stats: r.stats || {}, unknown_files: [] })
      setRes({ ok: true, vouchers: rows, summary: {
        生成凭证: rows.length, 借贷平衡通过: rows.filter(v => v.可录入).length,
        含税合计: r.stats && r.stats.含税合计, 缺税率: rows.filter(v => v.税率来源 === '缺税率').length,
        待人工: r.stats && r.stats.待人工行,
      } })
      setSel(new Set(rows.map((v, i) => v.可录入 === false ? null : i).filter(i => i !== null)))
      setUploadId(r.upload_id || null); setSavedAt('')
      refreshUploads(postYear, month)
      if (r.clean) setStep(2)   // 干净=直进活表；有新供应商/不干净=留在①看质检卡
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  // 载入某上传批次——恢复现场，不用重传文件
  const doLoadUpload = async (u2) => {
    setErr(''); setBusy('load')
    try {
      const r = await loadBillUpload(u2.id)
      if (!r.ok) throw new Error(r.msg || '载入失败')
      const rows = r.rows || []
      setBillInfo({ per_file: r.per_file || [], stats: r.stats || {}, unknown_files: [] })
      setRes({ ok: true, vouchers: rows, summary: {
        生成凭证: rows.length, 借贷平衡通过: rows.filter(v => v.可录入).length,
        含税合计: r.stats && r.stats.含税合计, 缺税率: rows.filter(v => v.税率来源 === '缺税率').length,
        待人工: r.stats && r.stats.待人工行,
      } })
      setSel(new Set(rows.map((v, i) => v.可录入 === false ? null : i).filter(i => i !== null)))
      setEdited(new Set()); setRatio(null); setPostRes(null)
      setUploadId(u2.id); setSavedAt('')
      setStep(2)
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }

  // 账单直采：多文件 → 计提明细活表（rows=voucher 同构，直接装进 res.vouchers 复用全链）
  const doBillsParse = async () => {
    if (!billFiles.length) { setErr('请先选择核对后的账单文件（可多选）'); return }
    setErr(''); setBusy('bills'); setRes(null); setPostRes(null); setBillInfo(null); setEdited(new Set())
    try {
      const r = await parseBills(billFiles, month, postYear)
      if (!r.ok) throw new Error(r.msg || '账单解析失败')
      setUploadId(r.upload_id || null); setSavedAt('')
      setCollect({ complete: !!r.complete, issues: r.issues || [], notify: r.notify, unknown_files: r.unknown_files || [] })
      refreshUploads(postYear, month)   // 新批次已留痕，刷新该月上传列表
      const rows = r.rows || []
      setBillInfo({ per_file: r.per_file || [], stats: r.stats || {}, unknown_files: r.unknown_files || [] })
      setRes({ ok: true, vouchers: rows, summary: {
        生成凭证: rows.length, 借贷平衡通过: rows.filter(v => v.可录入).length,
        含税合计: r.stats && r.stats.含税合计, 缺税率: rows.filter(v => v.税率来源 === '缺税率').length,
        待人工: r.stats && r.stats.待人工行,
      } })
      setSel(new Set(rows.map((v, i) => v.可录入 === false ? null : i).filter(i => i !== null)))
      setStep(2)
    } catch (e) {
      const m = String(e.message || e)
      setErr(m.includes('401') ? '登录已失效——多半是同时开着其它端口的工作台把会话顶掉了。请刷新本页重新登录后再传（V2.196 起各端口会话已隔离，重新登录一次即永久解决）。' : m)
    } finally { setBusy('') }
  }

  // 活表：改一行的维度 → 服务端重算（摘要/税额/分录/可录入）→ 回填
  const patchRow = async (oi, patch, requery) => {
    const row = { ...res.vouchers[oi], ...patch }
    try {
      const r = await refreshLogisticsRow({ month, row, requery: !!requery })
      if (!r.ok) throw new Error(r.msg || '重算失败')
      setRes(prev => {
        const nr = prev.vouchers.map((v, i) => i === oi ? r.row : v)
        // 自动保存回上传批次（V2.223 业务方："是不是要加一个保存"）——改一行即全量回写，
        // 下次「载入这批」拿到的就是改过的现场，不会回退到解析原始值
        if (uploadId) saveBillUploadRows(uploadId, nr)
          .then(x => { if (x.ok) setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false })) })
          .catch(() => {})
        return { ...prev, vouchers: nr }
      })
      setEdited(prev => new Set(prev).add(oi))
      setSel(prev => { const n = new Set(prev); if (r.row.可录入 === false) n.delete(oi); return n })
    } catch (e) { setErr(String(e.message || e)) }
  }
  const doRatio = async () => {
    setRatioBusy(true); setRatio(null)
    try {
      const r = await getExpenseRatio({ year: postYear, month, rows: V, with_prev: withPrev })
      if (!r.ok) throw new Error(r.msg || '费率计算失败')
      setRatio(r)
    } catch (e) { setErr(String(e.message || e)) } finally { setRatioBusy(false) }
  }
  const doAdopt = async (v) => {
    const scope = window.prompt(
      `把这行的做账维度存入映射维表，下月自动预填。\n维度：${v.科目} / ${v.部门} / ${v.费用项目}\n\n适用范围填：\n1 = 仅 ${v.主体} × ${v.费用归属}\n2 = 仅 ${v.业务线} × ${v.费用归属}\n3 = ${v.费用归属} 默认（所有主体业务线）`, '1')
    if (!scope) return
    const b = { fee: v.费用归属, subject: scope === '1' ? v.主体 : '', bizline: scope === '2' ? v.业务线 : '',
      account: v.科目, dept: v.部门, item: v.费用项目, sword: v.摘要用语 }
    const r = await adoptFeeMap(b).catch(e => ({ ok: false, msg: String(e.message || e) }))
    if (r.ok) { alert('已采纳入维表，下月同场景自动预填（在「基础数据」页可查）') } else setErr(r.msg || '采纳失败')
  }

  const doParse = async (goNext) => {
    if (!file) { setErr('请先在「① 导入与排查」选择物流计提表（.xlsx）'); return }
    setErr(''); setBusy('parse'); setRes(null); setPostRes(null)
    try {
      const r = await parseLogistics(file, month)
      if (!r.ok) throw new Error(r.msg || '解析失败')
      setRes(r)
      setSel(new Set((r.vouchers || []).map((v, i) => v.可录入 === false ? null : i).filter(i => i !== null)))
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const doSuppliers = async () => {
    if (!file) { setErr('请先选择物流计提表（.xlsx）'); return }
    setErr(''); setBusy('sup'); setSup(null)
    try { const r = await checkLogisticsSuppliers(file, month); if (!r.ok) throw new Error(r.msg || '核对失败'); setSup(r) }
    catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const addRate = async (preset) => {
    const b = preset || nr
    if (!b.supplier || b.rate === '') { setRmsg('供应商全名和税率都要填'); return }
    const r = await saveLogisticsRate(b).catch(e => ({ ok: false, msg: String(e.message || e) }))
    if (r.ok) { setRmsg(''); if (!preset) setNr({ supplier: '', fee_type: '', rate: '' }); loadRates() }
    else setRmsg(r.msg || '保存失败')
  }
  const delRate = async (row) => {
    if (!window.confirm(`删除税率：${row.supplier} × ${row.fee_type || '(默认)'} = ${pct(row.rate)}？`)) return
    const r = await deleteLogisticsRate({ id: row.id }); if (r.ok) loadRates()
  }

  const V = (res && res.vouchers) || []
  const S = res && res.summary
  const effPeriod = postPeriod || month
  const selArr = V.filter((v, i) => sel.has(i))
  const selGross = selArr.reduce((a, v) => a + (v.含税 || 0), 0)

  const doPost = async () => {
    if (!selArr.length) { setErr('请先勾选要录入的凭证'); return }
    if (!window.confirm(`确认把勾选的 ${selArr.length} 张计提凭证（含税合计 ${money(selGross)} 元）录入金蝶 ${postYear} 年第 ${effPeriod} 期？\n\n· 只存为凭证草稿，提交和审核仍由人在金蝶完成\n· 金蝶该月已有同摘要凭证的会自动跳过，防止录重`)) return
    setErr(''); setBusy('post'); setPostRes(null)
    try {
      const r = await postLogistics({ year: postYear, month, period: effPeriod, vouchers: selArr })
      if (!r.ok) throw new Error(r.msg || '录入失败')
      setPostRes(r)
      // 凭证号回填到复核清单/打印卡片
      const byZy = {}; r.results.forEach(it => { byZy[it.摘要] = it })
      setRes(prev => ({ ...prev, vouchers: prev.vouchers.map(v => {
        const it = byZy[v.摘要]
        return (it && it.凭证号) ? { ...v, 凭证号: it.凭证号 } : v
      }) }))
    } catch (e) { setErr(String(e.message || e)) } finally { setBusy('') }
  }

  // 加载"本工具已录入金蝶的凭证"(按当前落账年/期),供撤销删除草稿
  const loadPosted = async () => {
    setErr(''); setPBusy(true); setPosted(null); setPSel(new Set())
    try {
      const r = await getLogisticsPosted(postYear, effPeriod)
      if (!r.ok) throw new Error(r.msg || '加载失败')
      setPosted(r)
      setPSel(new Set(r.items.filter(it => it.可撤销).map(it => it.id)))   // 默认勾上可撤销的
    } catch (e) { setErr(String(e.message || e)) } finally { setPBusy(false) }
  }
  const doUnpost = async () => {
    const ids = [...pSel]
    if (!ids.length) { setErr('请先勾选要撤销的凭证'); return }
    if (!window.confirm(`确认撤销（删除）勾选的 ${ids.length} 张凭证草稿？\n\n· 只删除本工具录入、且仍是草稿态的凭证\n· 已提交/已审核的删不了，需先去金蝶反审核\n· 删除不可恢复，删后可重新录入`)) return
    setErr(''); setPBusy(true)
    try {
      const r = await unpostLogistics(ids)
      if (!r.ok) throw new Error(r.msg || '撤销失败')
      await loadPosted()   // 刷新列表
      const blocked = r.results.filter(x => x.status === 'blocked')
      alert(`已删除 ${r['删除']} 张${r['拦下'] ? `，${r['拦下']} 张已提交/已审核未删（需去金蝶反审核）` : ''}${r['失败'] ? `，${r['失败']} 张失败` : ''}` +
        (blocked.length ? '\n\n未删：' + blocked.map(x => `${x.凭证号}(${x.msg})`).join('；') : ''))
    } catch (e) { setErr(String(e.message || e)) } finally { setPBusy(false) }
  }

  const COLS = ['序', '物流商', '物流商全称', '含税', '税率', '未税', '税额', '借方科目', '部门', '费用项目', '业务线', '主体', '凭证号']
  const NC = COLS.length
  const th = { border: '0.5px solid #d9d7cf', padding: '5px 8px', background: '#305496', color: '#fff', fontWeight: 500, whiteSpace: 'nowrap', position: 'sticky', top: 0 }
  const tdP = { padding: '4px 8px', whiteSpace: 'nowrap' }
  const OUT = '2px solid #444', INN = '0.5px solid #e2e0d8'
  const bd = (c, top, bot) => ({ borderTop: top ? OUT : INN, borderBottom: bot ? OUT : INN, borderLeft: c === 0 ? OUT : INN, borderRight: c === NC - 1 ? OUT : INN })
  const cth = { border: '0.5px solid #b9c2d6', padding: '3px 5px', background: '#e8ecf5', color: '#20304d', fontWeight: 500, whiteSpace: 'nowrap' }
  const ctd = { border: '0.5px solid #d8d8d8', padding: '3px 5px', whiteSpace: 'nowrap' }
  const ctr = { ...ctd, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
  // 按 (主体, 物流商) 分组，供 小计 + 加粗外框
  const subjOrder = { '深圳星期零': 0, '深圳星期九': 1, '孝感星期九': 2 }
  const seenLg = []
  V.forEach(v => { if (!seenLg.includes(v.物流商)) seenLg.push(v.物流商) })
  const sortedV = V.map((v, oi) => ({ ...v, _oi: oi })).sort((a, b) =>
    (subjOrder[a.主体] ?? 9) - (subjOrder[b.主体] ?? 9) || seenLg.indexOf(a.物流商) - seenLg.indexOf(b.物流商) || 0)
  sortedV.forEach((v, i) => { v._seq = i + 1 })
  const groups = []
  sortedV.forEach(v => {
    const k = v.主体 + '|' + v.物流商
    let g = groups[groups.length - 1]
    if (!g || g.k !== k) { g = { k, 物流商: v.物流商, items: [], 含税: 0, 未税: 0, 税额: 0 }; groups.push(g) }
    g.items.push(v); g.含税 += v.含税; g.未税 += v.未税; g.税额 += v.税额
  })
  const selectable = sortedV.filter(v => v.可录入 !== false)
  const allSel = selectable.length > 0 && selectable.every(v => sel.has(v._oi))
  const toggleAll = () => setSel(allSel ? new Set() : new Set(selectable.map(v => v._oi)))
  const toggleOne = (oi) => setSel(p => { const n = new Set(p); n.has(oi) ? n.delete(oi) : n.add(oi); return n })

  const postStat = postRes && { saved: postRes.成功, skip: postRes.跳过, fail: postRes['失败或拦下'] }
  const rateTag = (v) => v.税率来源 === '缺税率'
    ? <span style={{ marginLeft: 4, fontSize: 10, color: 'var(--red)', border: '0.5px solid #e7b9b9', borderRadius: 4, padding: '0 3px' }}>缺</span>
    : v.税率来源 === '计提表'
      ? <span title="税率来自计提表格内，建议维护进税率表" style={{ marginLeft: 4, fontSize: 10, color: 'var(--amber)', border: '0.5px solid #ecd3ab', borderRadius: 4, padding: '0 3px' }}>表内</span>
      : null

  // 复核清单表（③复核只读 / ④带勾选列），步骤间复用。
  // 注意：这是普通渲染函数（调用式），不能写成 <ReviewTable/> 内嵌组件——组件身份每次渲染都变，
  // React 会整表卸载重建：丢滚动位置、旧 DOM 节点失效（V2.30 实测勾选连点失灵即此因）。
  // 活表维度编辑器（③步展开区，V2.195）：改 主体/费用归属/业务线 → 服务端 requery 重查映射默认；
  // 改 科目/部门/费用项目 → 直接覆盖（人工优先于映射），服务端只重算摘要/分录。
  const dimSel = (v, field, opts, requery, extra) => (
    <label style={{ fontSize: 12, color: '#555' }}>{field}
      <select value={v[field] || ''} onChange={e => patchRow(v._oi, { [field]: e.target.value }, requery)}
        style={{ marginLeft: 4, padding: '3px 6px', borderRadius: 6, border: '0.5px solid #cfcdc4', maxWidth: 150 }}>
        {(extra || []).concat(opts).map(o => <option key={o || '(空)'} value={o}>{o || '（待定）'}</option>)}
      </select>
    </label>)
  const editZone = (v) => (
    <div onClick={e => e.stopPropagation()} style={{ marginTop: 8, padding: '8px 10px', background: edited.has(v._oi) ? '#fdf6e3' : '#f2f4fa', borderRadius: 8, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: '#305496' }}>✎ 活表·改维度</span>
      {dimSel(v, '主体', SUBJ3, true)}
      {dimSel(v, '费用归属', FEE13, true, v.费用归属 ? [] : [''])}
      {dimSel(v, '业务线', BIZ10, true)}
      {dimSel(v, '科目', ACC5, false, v.科目 ? [] : [''])}
      {dimSel(v, '部门', DEPT5, false, v.部门 ? [] : [''])}
      {dimSel(v, '费用项目', ITEM6, false, v.费用项目 ? [] : [''])}
      {v.映射层级 && <span style={{ fontSize: 11, color: '#8a8880' }}>映射:{v.映射层级}{v.manual ? '·🖐人工核对类' : ''}</span>}
      {edited.has(v._oi) && canUp && <button style={{ ...btn, padding: '3px 10px', fontSize: 12 }} onClick={() => doAdopt(v)}>采纳入维表</button>}
      {v.备注 && <span style={{ fontSize: 11, color: '#8a8880', width: '100%' }}>{v.备注}</span>}
    </div>)

  const reviewTable = (withCheck, withEdit) => (
    <div className="la-tblwrap" style={{ overflowX: 'auto', border: '0.5px solid #e2e0d8', borderRadius: 8 }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
        <thead><tr>
          {withCheck && <th className="la-noprint" style={{ ...th, width: 30 }}><input type="checkbox" checked={allSel} onChange={toggleAll} title="全选/全不选" /></th>}
          {COLS.map(h => <th key={h} style={th}>{h}</th>)}
        </tr></thead>
        <tbody>
          {groups.map((g, gi) => <React.Fragment key={gi}>
            {g.items.map((v, ri) => <React.Fragment key={v._oi}>
              <tr onClick={() => setOpenV(openV === v._oi ? null : v._oi)} style={{ cursor: 'pointer', background: openV === v._oi ? 'var(--accent-soft)' : v.可录入 === false ? '#fdf6f6' : edited.has(v._oi) ? '#fdf6e3' : '#fff' }}>
                {withCheck && <td className="la-noprint" style={{ ...tdP, border: INN, textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                  <input type="checkbox" disabled={v.可录入 === false} checked={sel.has(v._oi)} onChange={() => toggleOne(v._oi)} />
                </td>}
                <td style={{ ...tdP, ...bd(0, ri === 0), textAlign: 'center' }}>{v._seq}</td>
                <td style={{ ...tdP, ...bd(1, ri === 0) }}>{v.物流商}</td>
                <td style={{ ...tdP, ...bd(2, ri === 0), maxWidth: 210, overflow: 'hidden', textOverflow: 'ellipsis' }}>{v.公司全名}</td>
                <td style={{ ...tdP, ...bd(3, ri === 0), textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(v.含税)}</td>
                <td style={{ ...tdP, ...bd(4, ri === 0), textAlign: 'right' }}>{Math.round((v.税率 || 0) * 100)}%{rateTag(v)}</td>
                <td style={{ ...tdP, ...bd(5, ri === 0), textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(v.未税)}</td>
                <td style={{ ...tdP, ...bd(6, ri === 0), textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(v.税额)}</td>
                <td style={{ ...tdP, ...bd(7, ri === 0) }}>{v.科目}</td>
                <td style={{ ...tdP, ...bd(8, ri === 0) }}>{v.部门}</td>
                <td style={{ ...tdP, ...bd(9, ri === 0) }}>{v.费用项目}</td>
                <td style={{ ...tdP, ...bd(10, ri === 0) }}>{v.业务线 || '—'}{v.产品项目 ? <span title="挂产品项目 TO C" style={{ marginLeft: 4, fontSize: 10, color: '#20607a', border: '0.5px solid #bcd6e2', borderRadius: 4, padding: '0 3px' }}>TO C</span> : null}</td>
                <td style={{ ...tdP, ...bd(11, ri === 0) }}>{v.主体}</td>
                <td style={{ ...tdP, ...bd(12, ri === 0), textAlign: 'center' }}>{v.凭证号 || '(待录)'}</td>
              </tr>
              {openV === v._oi && <tr className="la-detail"><td colSpan={NC + (withCheck ? 1 : 0)} style={{ ...tdP, border: INN, background: '#f7f8fc', padding: '8px 14px' }}>
                <div style={{ fontSize: 12, color: '#555', marginBottom: 4 }}>摘要：{v.摘要 || <span style={{ color: 'var(--red)' }}>（维度未定，选好费用归属后自动生成）</span>}　<span style={{ color: '#8a8880' }}>税率来源：{v.税率来源 || '计提表'}</span></div>
                {withEdit && canUp && editZone(v)}
                <table style={{ borderCollapse: 'collapse', fontSize: 12 }}><tbody>
                  {(v.分录 || []).map((l, k) => <tr key={k}>
                    <td style={{ padding: '2px 10px 2px 0' }}>{l.方向}</td>
                    <td style={{ padding: '2px 14px 2px 0' }}>{l.科目}</td>
                    <td style={{ padding: '2px 14px 2px 0', color: '#77756e' }}>{l.维度}</td>
                    <td style={{ padding: '2px 14px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{l.借方 ? '借 ' + money(l.借方) : ''}</td>
                    <td style={{ padding: '2px 0', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{l.贷方 ? '贷 ' + money(l.贷方) : ''}</td>
                  </tr>)}
                </tbody></table>
              </td></tr>}
            </React.Fragment>)}
            <tr className="la-sub" style={{ background: '#ededed', fontWeight: 600 }}>
              {withCheck && <td className="la-noprint" style={{ ...tdP, border: INN }}></td>}
              <td colSpan={3} style={{ ...tdP, ...bd(0, false, true), borderRight: INN, textAlign: 'right' }}>{g.物流商} 小计</td>
              <td style={{ ...tdP, ...bd(3, false, true), textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(g.含税)}</td>
              <td style={{ ...tdP, ...bd(4, false, true) }}></td>
              <td style={{ ...tdP, ...bd(5, false, true), textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(g.未税)}</td>
              <td style={{ ...tdP, ...bd(6, false, true), textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(g.税额)}</td>
              <td colSpan={6} style={{ ...tdP, ...bd(12, false, true), borderLeft: INN }}></td>
            </tr>
          </React.Fragment>)}
        </tbody>
      </table>
    </div>
  )

  const NoData = ({ need }) => (
    <div style={{ border: '1px dashed var(--line-strong,#cfcdc4)', borderRadius: 10, padding: '40px 20px', textAlign: 'center', color: 'var(--ink-3,#8a8880)', fontSize: 13 }}>
      {need}　<span className="lk" style={{ color: '#305496', cursor: 'pointer', fontWeight: 600 }} onClick={() => setStep(need.includes('解析') ? 2 : 1)}>去处理 ›</span>
    </div>
  )
  const NextBtn = ({ to, label }) => (
    <div style={{ marginTop: 4 }}>
      <button onClick={() => setStep(to)} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496' }}>{label} ›</button>
    </div>
  )

  const stepSub = {
    1: billInfo ? `账单 ${billInfo.stats.文件数} 个 · ${billInfo.stats.票数} 票 · ${month}月`
      : file ? `${file.name.slice(0, 18)} · ${month}月` : '账单包(主)·手填表(兜底)',
    2: S ? `${S.生成凭证} 行 · 可录 ${S.借贷平衡通过}${S.待人工 ? ` · 待人工${S.待人工}` : ''}` : '活表改维度·采纳入维表',
    3: ratio && ratio.available ? `总费率 ${(ratio.合计.总费率 * 100).toFixed(1)}%` : '科目去向+费率看板',
    4: postStat ? `成功 ${postStat.saved} 张${postStat.skip ? ` 跳过${postStat.skip}` : ''}` : '存草稿·回凭证号',
    5: '复核清单+凭证卡片',
  }
  const stepDone = { 1: !!res || !!sup, 2: rates.length > 0, 3: !!res, 4: !!(postStat && postStat.saved), 5: false }

  return (<div>
    <style>{`
      .la-print-head{display:none}
      .la-cards{display:none}
      @media print{
        @page{size:A4 landscape;margin:8mm}
        body *{visibility:hidden}
        #la-print,#la-print *{visibility:visible}
        #la-print{position:absolute;left:0;top:0;width:100%;padding:0;margin:0}
        #la-print .la-noprint{display:none !important}
        #la-print .la-detail{display:none !important}
        .la-print-head{display:block;font-size:15px;font-weight:600;text-align:center;margin:0 0 8px}
        #la-print table{width:100%;font-size:10px;border-collapse:collapse}
        #la-print thead{display:table-header-group}
        #la-print thead th{background:#305496 !important;color:#fff !important;-webkit-print-color-adjust:exact;print-color-adjust:exact;position:static}
        #la-print .la-sub td{background:#ededed !important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
        #la-print .la-tblwrap{overflow:visible !important;border:none !important;border-radius:0 !important}
        #la-print .la-cards{display:block}
        #la-print .la-card{page-break-before:always;page-break-inside:avoid;border:1.5px solid #333;padding:10px 12px}
        #la-print .la-card table{font-size:9px}
        #la-print .la-card thead th{background:#e8ecf5 !important;color:#20304d !important}
        #la-print .la-card .hl td{background:#FFF2CC !important;-webkit-print-color-adjust:exact;print-color-adjust:exact;font-weight:600}
      }
    `}</style>
    <div className="head la-noprint" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap' }}>
      <div><div className="h-title">物流计提</div>
        <div className="h-sub">月结与结账 · 通用技能 · 账单直采：上传核对后账单 → 活表复核（改维度·采纳入维表）→ 做账去向与费率 → 勾选录入金蝶（存草稿，人审核）→ 打印附件；映射与税率维护在「基础数据」页；手填模板=兜底</div></div>
      {/* 期间选择器在右上角（V2.222 业务方定：对齐银行对账位置）；步①原位只读提醒锁定账期 */}
      <PeriodPicker year={postYear} period={month} source="logi" status={postRes ? '已计提' : undefined}
        onChange={pickMonth} />
    </div>
    <div className="body">
      <LSteps current={step} done={stepDone} sub={stepSub} onNav={setStep} />

      {err && <div className="la-noprint" style={{ background: '#fcebeb', color: '#a32d2d', border: '0.5px solid #f0c4c4', borderRadius: 8, padding: '8px 12px', fontSize: 13 }}>{err}</div>}

      {/* ══ ① 上传账单（账单直采=主路径 V2.195）══ */}
      {step === 1 && <>
        <div style={{ background: 'var(--bg,#fff)', border: '1.5px solid #305496', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13.5, fontWeight: 600 }}>核对后账单包（主路径）</span>
            <span title="要换月份，用右上角的期间选择器（换月会清空当前现场）" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, padding: '5px 12px', borderRadius: 8, background: 'var(--accent-soft,#edeefb)', border: '1px solid var(--accent,#4b53c4)', color: 'var(--accent,#4b53c4)', fontWeight: 600 }}>
              🔒 本次计提锁定：{postYear} 年 {month} 月
              <span style={{ fontWeight: 400, fontSize: 11, opacity: .75 }}>（换月在右上角）</span>
            </span>
            <input type="file" accept=".xlsx,.xls" multiple disabled={!canUp}
              onChange={e => { setBillFiles([...e.target.files]); setErr('') }} style={{ fontSize: 13 }} />
            <button onClick={doBillsParse} disabled={!!busy || !canUp} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496' }}>
              {busy === 'bills' ? '解析中…' : `解析账单${billFiles.length ? `（${billFiles.length} 个文件）` : ''} → 活表复核`}</button>
            {!canUp && <span style={{ fontSize: 12, color: 'var(--amber)' }}>你的账号没有「上传物流计提表」权限，请联系管理员</span>}
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-3,#8a8880)', marginTop: 8, lineHeight: 1.7 }}>
            把各物流商的「核对后账单」一次多选上传（对账时标好的 类型/主体/金蝶单号 会被自动识别，直接聚合成计提明细）。
            缺标注的票进"待人工"不拦上传；票级明细自动存档给对账用。目前支持：丰源 / 易风达 / 极鲜达 / 跨越 / 链盟 / 顺丰冷运 / 顺丰速运 / 比翼 / 顺鸽。
          </div>
          {billInfo && <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {billInfo.per_file.map((pf, i) => <span key={i} style={{ fontSize: 12, border: '0.5px solid ' + (pf.状态 === '已解析' ? '#cbe4d5' : '#e7b9b9'), background: pf.状态 === '已解析' ? '#eef7f0' : '#fcebeb', borderRadius: 6, padding: '4px 9px' }}>
              {pf.文件}　{pf.状态}·{pf.票数}票·{money(pf.金额)}</span>)}
            {billInfo.unknown_files.map((fn, i) => <span key={'u' + i} style={{ fontSize: 12, border: '0.5px solid #ecd3ab', background: '#fff8ec', borderRadius: 6, padding: '4px 9px' }}>
              {fn}　⚠没认出是哪家物流商（文件名带上商名）</span>)}
          </div>}
          {/* 归集状态卡（V2.225 定稿流程）：完成→绿卡+提交按钮；未完成→琥珀清单；新供应商账单→通知回执 */}
          {collect && <div style={{ marginTop: 10 }}>
            {collect.unknown_files.length > 0 && <div style={{ fontSize: 12, color: '#7a5a12', border: '1px solid var(--red-line,#e6b7b0)', background: 'var(--red-bg,#fbecea)', borderRadius: 8, padding: '8px 12px', marginBottom: 8 }}>
              🆕 {collect.unknown_files.length} 个账单疑似<b>新供应商</b>，已自动通知核算组建档：
              钉钉 {collect.notify && collect.notify.dingtalk ? (collect.notify.dingtalk.sent ? '✓已发' : `未发（${collect.notify.dingtalk.msg || '未配置'}）`) : '—'}；
              邮件 {collect.notify && collect.notify.email ? (collect.notify.email.sent ? '✓已发' : `未发（${collect.notify.email.msg || '未配置'}）`) : '—'}。建档后重传该账单。</div>}
            {collect.complete
              ? <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', border: '1px solid var(--green-line,#cbe4d5)', background: 'var(--green-bg,#e8f4ee)', borderRadius: 8, padding: '9px 12px' }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--green,#1f7a55)' }}>✓ 账单归集完成</span>
                <span style={{ fontSize: 12, color: '#555' }}>全部账单认出、维度齐全、税率齐——复核无误后提交</span>
                <button onClick={doSubmit} disabled={!!busy} style={{ ...btn, background: 'var(--green,#1f7a55)', color: '#fff', borderColor: 'var(--green,#1f7a55)', fontWeight: 600 }}>
                  {busy === 'submit' ? '提交中…' : '提交给核算组（通知检查录入）'}</button>
              </div>
              : <div style={{ border: '1px solid var(--amber-line,#e6cfa6)', background: 'var(--amber-bg,#f8f0e0)', borderRadius: 8, padding: '9px 12px' }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--amber,#a35a00)', marginBottom: 4 }}>⚠ 归集还没完成：</div>
                {collect.issues.map((x, i) => <div key={i} style={{ fontSize: 12.5, color: '#7a5a12' }}>· {x}</div>)}
                <div style={{ fontSize: 12, color: '#8a8880', marginTop: 4 }}>待人工的去「② 解析复核」待处理区补；处理完回这里（或步②底部）提交给核算组。</div>
              </div>}
          </div>}
          {/* 该月上传批次历史（V2.221 月份胶囊配套）：谁/何时/哪几份，按列表列示；点载入恢复现场不用重传 */}
          {uploads && uploads.length > 0 && <div style={{ marginTop: 12, border: '1px solid var(--blue-line,#bcd4f4)', background: 'var(--blue-bg,#f0f6fe)', borderRadius: 8, padding: '10px 14px' }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--blue,#2c6bcf)', marginBottom: 6 }}>
              {postYear} 年 {month} 月已有 {uploads.length} 次账单上传（最新在前）——不用重传，点「载入」直接恢复：</div>
            {uploads.map((u2, i) => <div key={u2.id} style={{ padding: '7px 0', borderTop: i ? '1px dashed var(--blue-line,#cfe0f7)' : 'none' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 12.5 }}>
                <b>{u2.operator}</b><span style={{ color: '#77756e' }}>于 {u2.ts} 上传</span>
                <span>{(u2.stats || {}).文件数} 个文件 · {(u2.stats || {}).票数} 票 · {money((u2.stats || {}).含税合计)}</span>
                {i === 0 && <span style={{ fontSize: 11, color: 'var(--green)', border: '1px solid var(--green-line)', background: 'var(--green-bg)', borderRadius: 10, padding: '1px 8px' }}>最新</span>}
                {u2.status === '已提交' && <span title={`${u2.submitted_by} 于 ${u2.submitted_at} 提交`} style={{ fontSize: 11, color: 'var(--blue,#2c6bcf)', border: '1px solid var(--blue-line,#bcd4f4)', background: 'var(--blue-bg,#e7f0fc)', borderRadius: 10, padding: '1px 8px', fontWeight: 600 }}>已提交给核算组</span>}
                <button style={{ ...btn, padding: '3px 12px', fontSize: 12 }} disabled={!!busy} onClick={() => doLoadUpload(u2)}>{busy === 'load' ? '载入中…' : '载入这批 → 活表复核'}</button>
              </div>
              <div style={{ marginTop: 4, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {(u2.per_file || []).map((pf, k) => <span key={k} style={{ fontSize: 11, color: '#555', border: '0.5px solid var(--line,#dfe5ee)', background: '#fff', borderRadius: 5, padding: '2px 7px' }}>
                  {pf.文件}·{pf.票数}票·{money(pf.金额)}</span>)}
              </div>
            </div>)}
          </div>}
          {uploads && uploads.length === 0 && !billInfo && <div style={{ marginTop: 10, fontSize: 12, color: '#8a8880' }}>
            {postYear} 年 {month} 月还没有上传过账单——选好文件点上方按钮解析。</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', background: 'var(--bg-sub,#fafaf7)', border: '1px dashed var(--line-strong,#cfcdc4)', borderRadius: 10, padding: '12px 16px' }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>物流部长表上传（手填计提表模板）</span>
          <input type="file" accept=".xlsx" disabled={!canUp} onChange={e => { setFile(e.target.files[0]); setSup(null); setErr('') }} style={{ fontSize: 13 }} />
          <button onClick={doLongForm} disabled={!!busy || !canUp} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496' }}>{busy === 'lf' ? '解析质检中…' : '解析长表 → 质检'}</button>
          <button onClick={doSuppliers} disabled={!!busy || !canUp} style={btn} title="与金蝶供应商档案比对">{busy === 'sup' ? '核对中…' : '金蝶档案核对'}</button>
          <span style={{ fontSize: 11.5, color: '#a5a399', marginLeft: 'auto' }}>自动检测：新增供应商→邮件+钉钉通知核算组；计提表干净度逐行体检</span>
        </div>
        {/* 长表质检结果（V2.224）：新增供应商红卡（含通知回执）+ 不干净行清单 + 干净绿卡 */}
        {lfInfo && <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {lfInfo.new_suppliers.length > 0 && <div style={{ border: '2px solid var(--red,#c0392b)', borderRadius: 10, padding: '10px 14px', background: 'var(--red-bg,#fbecea)' }}>
            <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--red,#c0392b)', marginBottom: 6 }}>
              🆕 发现 {lfInfo.new_suppliers.length} 家新供应商（不在「基础数据·供应商列表」）</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
              {lfInfo.new_suppliers.map((x, i) => <span key={i} style={{ fontSize: 12.5, background: '#fff', border: '1px solid var(--red-line,#e6b7b0)', borderRadius: 6, padding: '4px 10px' }}>
                <b>{x.简称}</b>　{x.行数} 行 · {money(x.金额)}</span>)}
            </div>
            <div style={{ fontSize: 12, color: '#7a5a12' }}>
              已自动通知核算组建档并维护税率：
              钉钉 {lfInfo.notify && lfInfo.notify.dingtalk ? (lfInfo.notify.dingtalk.sent ? '✓已发' : `未发（${lfInfo.notify.dingtalk.msg || '未配置'}）`) : '—'}；
              邮件 {lfInfo.notify && lfInfo.notify.email ? (lfInfo.notify.email.sent ? '✓已发' : `未发（${lfInfo.notify.email.msg || '未配置'}）`) : '—'}。
              核算组建档后回到本页「载入这批」继续。</div>
          </div>}
          {lfInfo.dirty.length > 0 && <div style={{ border: '1px solid var(--amber-line,#e6cfa6)', borderRadius: 10, padding: '10px 14px', background: 'var(--amber-bg,#f8f0e0)' }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--amber,#a35a00)', marginBottom: 6 }}>⚠ 计提表不干净：{lfInfo.dirty.length} 行有问题（可去「② 解析复核」的待处理区直接改，或回 Excel 改了重传）</div>
            {lfInfo.dirty.slice(0, 10).map((d, i) => <div key={i} style={{ fontSize: 12.5, padding: '3px 0' }}>
              第 <b>{d.行}</b> 行　{d.物流商}　{money(d.金额)}　<span style={{ color: 'var(--amber,#a35a00)' }}>{d.问题.join('；')}</span></div>)}
            {lfInfo.dirty.length > 10 && <div style={{ fontSize: 12, color: '#8a8880' }}>…还有 {lfInfo.dirty.length - 10} 行，见「② 解析复核」待处理区</div>}
          </div>}
          {lfInfo.clean && <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--green,#1f7a55)', border: '1px solid var(--green-line,#cbe4d5)', background: 'var(--green-bg,#e8f4ee)', borderRadius: 10, padding: '10px 14px' }}>
            ✓ 计提表干净：{lfInfo.stats.票数} 行全部合规、无新增供应商——已自动进入「② 解析复核」。</div>}
          {!lfInfo.clean && res && <button onClick={() => setStep(2)} style={{ ...btn, alignSelf: 'flex-start', background: '#305496', color: '#fff', borderColor: '#305496' }}>带着问题先进「② 解析复核」（待处理区可直接改）›</button>}
        </div>}
        {sup && <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '12px 16px', background: sup.缺建档.length ? '#fff8ec' : '#eef7f0' }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 6 }}>供应商核对闸门</div>
          {sup.缺建档.length === 0
            ? <div style={{ color: '#1a7f4b', fontSize: 13 }}>✓ 计提表 {sup.计提表供应商数} 家供应商全部已在金蝶建档（金蝶共 {sup.金蝶供应商数} 家），可放心生成/录入。</div>
            : <div style={{ fontSize: 13 }}>
              <div style={{ color: 'var(--amber)', marginBottom: 6 }}>⚠ 有 {sup.缺建档.length} 家供应商金蝶还没建档，需先去金蝶手工建档、再回来重新核对：</div>
              <ul style={{ margin: 0, paddingLeft: 20, color: '#7a5a12' }}>{sup.缺建档.map(m => <li key={m}>{m}</li>)}</ul>
            </div>}
        </div>}
        {file && <NextBtn to={2} label="下一步 · 解析复核" />}
      </>}

      {/* ══ ② 解析复核·活表 ══ */}
      {step === 2 && <>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {billInfo
            ? <span style={{ fontSize: 12.5, color: '#305496', fontWeight: 600 }}>账单直采 · {billInfo.stats.文件数} 个账单 · {billInfo.stats.票数} 票 · {month} 月（重传回「① 上传账单」）</span>
            : <><button onClick={() => doParse()} disabled={!!busy || !canUp} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496' }}>{busy === 'parse' ? '解析中…' : (res ? '重新解析' : '解析生成凭证')}</button>
              <span style={{ fontSize: 12, color: '#8a8880' }}>{file ? `当前文件：${file.name} · ${month} 月` : '还没选文件，请回「① 上传账单」'}　·　费用率异常校验＝B 期</span></>}
        </div>
        {!res && !busy && !file && <NoData need="先在「① 上传账单」上传账单包或手填计提表" />}
        {S && <>
          {/* 统计卡 + 活表说明同一行（V2.223 业务方定：说明填进右侧空白，不单独占行） */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'stretch' }}>
            <Metric label={billInfo ? '计提明细行' : '生成凭证'} value={S.生成凭证} />
            <Metric label="可录入" value={`${S.借贷平衡通过}/${S.生成凭证}`} tone={S.借贷平衡通过 === S.生成凭证 ? 'ok' : 'warn'} />
            {S.未税核对一致 != null && <Metric label="未税核对一致" value={`${S.未税核对一致}/${S.生成凭证}`} tone={S.未税核对一致 === S.生成凭证 ? 'ok' : 'warn'} />}
            {S.待人工 != null && <Metric label="待人工(补维度)" value={S.待人工} tone={S.待人工 ? 'bad' : 'ok'} />}
            {S.未覆盖映射 != null && <Metric label="未覆盖映射" value={S.未覆盖映射} tone={S.未覆盖映射 ? 'bad' : 'ok'} />}
            <Metric label="缺税率" value={S.缺税率 || 0} tone={S.缺税率 ? 'bad' : 'ok'} />
            <Metric label="含税合计" value={money(S.含税合计)} />
            {S.非月结记录 != null && <Metric label="非月结(不做账)" value={S.非月结记录} tone="warn" />}
            {billInfo && <div style={{ flex: 1, minWidth: 320, fontSize: 12, lineHeight: 1.7, color: '#77756e', border: '1px dashed var(--line-strong,#cfcdc4)', borderRadius: 8, padding: '8px 12px', background: 'var(--bg-sub,#fafaf7)' }}>
              <b>活表</b>：点行展开=分录+维度编辑（下拉可改，摘要分录自动重算）。映射只给默认值，改过的行变<span style={{ background: '#fdf6e3', padding: '0 3px' }}>琥珀</span>可「采纳入维表」；<span style={{ background: '#fdf6f6', padding: '0 3px' }}>红</span>=待人工，上方待处理区直改。金额不可改——改数回账单重传。口径 → 左侧「基础数据」。
            </div>}
          </div>
          {res.unmapped && res.unmapped.length > 0 && <div style={{ background: '#fcebeb', color: '#a32d2d', border: '0.5px solid #f0c4c4', borderRadius: 8, padding: '8px 12px', fontSize: 13 }}>
            以下费用归属映射表未覆盖，已拦下未生成凭证（需补映射）：{res.unmapped.map(u => `${u.主体}·${u.费用归属}`).join('、')}
          </div>}
          {/* 待处理区（V2.223 业务方定：问题行单独拎最前、醒目直改；下方完整清单保持原顺序，同一数据源改一处两处同步） */}
          {billInfo && canUp && (() => {
            const bad = V.map((v, oi) => ({ v, oi })).filter(x => x.v.可录入 === false)
            if (!bad.length) return <div style={{ fontSize: 12.5, color: 'var(--green,#1f7a55)', border: '1px solid var(--green-line,#cbe4d5)', background: 'var(--green-bg,#e8f4ee)', borderRadius: 8, padding: '8px 12px', fontWeight: 600 }}>✓ 全部 {V.length} 行维度齐全、可录入——没有待处理项</div>
            const why = (v) => !v.费用归属 ? '账单无标注/到付——先选费用归属和业务线'
              : v.税率来源 === '缺税率' ? '缺税率——去「基础数据」给该商补税率，或检查费用归属选对没有'
              : v.manual && (!v.科目 || !v.部门) ? '设备调拨/其它——科目和部门要逐笔人工定'
              : '维度不全——补齐下拉里标（待定）的项'
            const badSum = bad.reduce((a, x) => a + (x.v.含税 || 0), 0)
            return <div style={{ border: '2px solid var(--red,#c0392b)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ padding: '9px 14px', background: 'var(--red-bg,#fbecea)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--red,#c0392b)' }}>⚠ 待处理 {bad.length} 行 · {money(badSum)}</span>
                <span style={{ fontSize: 12, color: '#7a5a12' }}>在这里直接改，改好一行下面清单同步变、这里自动消掉；不改完不能录入</span>
              </div>
              {bad.map(({ v, oi }, i) => {
                const vv = { ...v, _oi: oi }   // editZone/patchRow 按 _oi 定位原始行（下方清单同一数据源，改即同步）
                return <div key={oi} style={{ padding: '10px 14px', borderTop: i ? '1px dashed var(--red-line,#e6b7b0)' : 'none', background: '#fff' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 12.5, marginBottom: 2 }}>
                    <b>{v.物流商}</b><span>{v.主体 || <span style={{ color: 'var(--red)' }}>（主体待选）</span>}</span>
                    <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{money(v.含税)}</span>
                    <span style={{ color: 'var(--red,#c0392b)', fontSize: 12 }}>{why(v)}</span>
                    {v.备注 && <span style={{ color: '#8a8880', fontSize: 11.5 }}>{v.备注}</span>}
                  </div>
                  {editZone(vv)}
                </div>
              })}
            </div>
          })()}
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>复核清单（{V.length} 张凭证 · 点行{billInfo ? '改维度/看分录' : '看分录'} · 原顺序不动）</div>
          {reviewTable(false, !!billInfo)}
          <div style={{ fontSize: 12, color: '#1a7f4b', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span>✓ 每张凭证 借=贷、未税+税额=含税，工具已校验。
              {uploadId && <span style={{ color: '#77756e' }}>　维度改动<b style={{ color: 'var(--green,#1f7a55)' }}>自动保存</b>{savedAt ? `（最近 ${savedAt}）` : ''}。</span>}</span>
            {uploadId && V.length > 0 && V.every(v => v.可录入 !== false) &&
              <button onClick={doSubmit} disabled={!!busy} style={{ ...btn, background: 'var(--green,#1f7a55)', color: '#fff', borderColor: 'var(--green,#1f7a55)', fontWeight: 600 }}>
                {busy === 'submit' ? '提交中…' : '提交给核算组（通知检查录入）'}</button>}
          </div>
          <NextBtn to={3} label="下一步 · 做账去向与费率" />
        </>}
      </>}

      {/* ══ ③ 做账去向与费率（V2.198 独立成步，业务方定）══ */}
      {step === 3 && <>
        {!res ? <NoData need="先在「② 解析复核」解析生成计提明细" /> : <>
          <div style={{ display: 'inline-flex', border: '0.5px solid #cfcdc4', borderRadius: 8, overflow: 'hidden', fontSize: 13 }}>
            {[['dest', '做账去向'], ['ratio', '费率看板']].map(([k, lb2]) =>
              <span key={k} onClick={() => setDestTab(k)} style={{ padding: '8px 20px', cursor: 'pointer', background: destTab === k ? '#edeefb' : '#fff', color: destTab === k ? '#305496' : '#77756e', fontWeight: destTab === k ? 600 : 400, borderRight: '0.5px solid #e6e4dc' }}>{lb2}</span>)}
          </div>
          {/* 做账去向汇总（V2.195）：本月费用做到哪个科目/业务线/部门 一页看全 */}
          {destTab === 'dest' && billInfo && (() => {
            const dest = {}
            let pend = 0, pendN = 0
            V.forEach(v => {
              if (!v.费用归属 || !v.科目) { if (v.含税) { pend += v.含税; pendN++ } return }
              const k = [v.主体, v.科目, v.部门, v.业务线 || '—', v.产品项目 || ''].join('|')
              const d = dest[k] || (dest[k] = { 主体: v.主体, 科目: v.科目, 部门: v.部门, 业务线: v.业务线 || '—', 产品项目: v.产品项目 || '', 未税: 0, 税额: 0, 含税: 0, n: 0 })
              d.未税 += v.未税 || 0; d.税额 += v.税额 || 0; d.含税 += v.含税 || 0; d.n++
            })
            const rows2 = Object.values(dest).sort((a, b) => (subjOrder[a.主体] ?? 9) - (subjOrder[b.主体] ?? 9) || String(a.科目).localeCompare(b.科目) || b.含税 - a.含税)
            return <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ padding: '10px 14px', background: 'var(--bg-sub,#f6f6f3)', borderBottom: '0.5px solid #e6e4dc', fontSize: 13.5, fontWeight: 600 }}>做账去向（本月费用做到哪个科目 / 哪个业务线 / 哪个部门 · 随上表改动实时变）</div>
              <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
                <thead><tr>{['付款主体', '借方科目', '部门', '业务线', '产品项目', '未税', '税额', '含税', '笔数'].map(h => <th key={h} style={{ ...cth, padding: '6px 12px' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {rows2.map((d, i) => <tr key={i}>
                    <td style={{ ...ctd, padding: '5px 12px' }}>{d.主体}</td>
                    <td style={{ ...ctd, padding: '5px 12px' }}>{d.科目}</td>
                    <td style={{ ...ctd, padding: '5px 12px' }}>{d.部门}</td>
                    <td style={{ ...ctd, padding: '5px 12px' }}>{d.业务线}{d.产品项目 === 'CPXM017' ? ' ·山姆TO C' : ''}</td>
                    <td style={{ ...ctd, padding: '5px 12px', fontFamily: 'Consolas,monospace' }}>{d.产品项目 || '—'}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(d.未税)}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(d.税额)}</td>
                    <td style={{ ...ctr, padding: '5px 12px', fontWeight: 600 }}>{money(d.含税)}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{d.n}</td>
                  </tr>)}
                  {pendN > 0 && <tr style={{ background: '#fff8ec' }}>
                    <td colSpan={5} style={{ ...ctd, padding: '5px 12px', color: '#7a5a12', fontWeight: 600 }}>🖐 维度待定（上表红底行，补齐后自动归位）</td>
                    <td style={ctr}></td><td style={ctr}></td>
                    <td style={{ ...ctr, padding: '5px 12px', fontWeight: 600 }}>{money(pend)}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{pendN}</td>
                  </tr>}
                  <tr style={{ background: '#ededed', fontWeight: 600 }}>
                    <td colSpan={5} style={{ ...ctd, padding: '5px 12px', textAlign: 'right' }}>合计</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(rows2.reduce((a, d) => a + d.未税, 0))}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(rows2.reduce((a, d) => a + d.税额, 0))}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(rows2.reduce((a, d) => a + d.含税, 0) + pend)}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{rows2.reduce((a, d) => a + d.n, 0) + pendN}</td>
                  </tr>
                </tbody>
              </table></div>
            </div>
          })()}
          {/* B 期·费用率看板（V2.197 第一刀）：分母=BP 工作台各业务线不含税收入 */}
          {destTab === 'ratio' && <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, overflow: 'hidden' }}>
            <div style={{ padding: '10px 14px', background: 'var(--bg-sub,#f6f6f3)', borderBottom: '0.5px solid #e6e4dc', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13.5, fontWeight: 600 }}>费率看板（物流费 ÷ 收入 · 收入取自 BP 工作台）</span>
              <label style={{ fontSize: 12, color: '#555' }}><input type="checkbox" checked={withPrev} onChange={e => setWithPrev(e.target.checked)} /> 含上月环比（查金蝶已录计提，稍慢）</label>
              <button onClick={doRatio} disabled={ratioBusy} style={btn}>{ratioBusy ? '取数中…' : '算费率'}</button>
              <span style={{ fontSize: 11.5, color: '#8a8880' }}>口径：未税物流费 ÷ 不含税收入（BP 应收单口径），两边天然同口径</span>
            </div>
            {ratio && ratio.available === false && <div style={{ padding: '10px 14px', fontSize: 12.5, color: 'var(--amber)' }}>{ratio.msg}</div>}
            {ratio && ratio.available && <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', fontSize: 12.5, width: '100%' }}>
                <thead><tr>{['业务线', '本期收入(不含税)', '本期费用(未税)', '本期费率', '上期收入', '上期费用', '上期费率', '提示'].map(h => <th key={h} style={{ ...cth, padding: '6px 12px' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {/* 数字列 width:1% 收缩到内容宽，剩余全给提示列——V2.219 业务方：提示别转行难看 */}
                  {ratio.lines.map((l, i) => <tr key={i} style={{ background: l.提示 && l.提示.startsWith('⚠') ? '#fff8ec' : l.业务线.includes('非古茗') ? '#f4f8f5' : '#fff' }}>
                    <td style={{ ...ctd, padding: '5px 12px', fontWeight: 600, width: '1%', paddingLeft: l.业务线.includes('非古茗') ? 26 : 12 }}>{l.业务线}</td>
                    <td style={{ ...ctr, padding: '5px 12px', width: '1%' }}>{l.本期收入 == null ? '—' : money(l.本期收入)}</td>
                    <td style={{ ...ctr, padding: '5px 12px', width: '1%' }}>{money(l.本期费用)}</td>
                    <td style={{ ...ctr, padding: '5px 12px', width: '1%', fontWeight: 600 }}>{l.本期费率 == null ? '—' : (l.本期费率 * 100).toFixed(2) + '%'}</td>
                    <td style={{ ...ctr, padding: '5px 12px', width: '1%', color: '#77756e' }}>{l.上期收入 == null ? '—' : money(l.上期收入)}</td>
                    <td style={{ ...ctr, padding: '5px 12px', width: '1%', color: '#77756e' }}>{l.上期费用 == null ? (ratio.prev_loaded ? '—' : '') : money(l.上期费用)}</td>
                    <td style={{ ...ctr, padding: '5px 12px', width: '1%', color: '#77756e' }}>{l.上期费率 == null ? (ratio.prev_loaded ? '—' : '') : (l.上期费率 * 100).toFixed(2) + '%'}</td>
                    <td style={{ ...ctd, padding: '5px 12px', fontSize: 12, whiteSpace: 'normal', color: l.提示 && l.提示.startsWith('⚠') ? '#b4690e' : '#8a8880' }}>{l.提示}</td>
                  </tr>)}
                  <tr style={{ background: '#ededed', fontWeight: 600 }}>
                    <td style={{ ...ctd, padding: '5px 12px' }}>合计</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(ratio.合计.收入)}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{money(ratio.合计.费用)}</td>
                    <td style={{ ...ctr, padding: '5px 12px' }}>{ratio.合计.总费率 == null ? '—' : (ratio.合计.总费率 * 100).toFixed(2) + '%'}</td>
                    <td colSpan={4} style={{ ...ctd, padding: '5px 12px', fontSize: 11.5, color: '#555', fontWeight: 400 }}>
                      总费率(非古茗口径)=<b>{ratio.合计.总费率非古茗 == null ? '—' : (ratio.合计.总费率非古茗 * 100).toFixed(2) + '%'}</b>（收入 {money(ratio.合计.收入非古茗)}）
                      · 分子=有业务线费用 {money(ratio.合计.业务线费用)}（研发/设备不摊）{ratio.bp_unmapped ? ` · BP 有 ${money(ratio.bp_unmapped)} 收入未对照` : ''}</td>
                  </tr>
                </tbody>
              </table>
              <div style={{ padding: '8px 14px', fontSize: 11.5, color: '#8a8880', lineHeight: 1.7 }}>
                期间 {ratio.period}，环比基期 {ratio.prev_period}{ratio.prev_loaded ? '（上期费用=金蝶已录计提）' : '（未取上期，勾选环比后重算）'}。
                {ratio.bp_meta && <>收入批次：BP 于 <b style={{ color: '#555' }}>{(ratio.bp_meta.fetched_at || '').slice(0, 16) || '未知时间'}</b> 取数
                  {ratio.bp_meta.pending > 0 && <span style={{ color: 'var(--amber)' }}>；另有 <b>{money(ratio.bp_meta.pending)}</b>（{ratio.bp_meta.pending_rows} 行）收入在 BP 待确认归线——各线分母偏小、费率偏高，去 BP 驾驶舱确认映射后回来重算</span>}
                  {ratio.bp_meta.unassigned > 0 && <span style={{ color: 'var(--amber)' }}>；{money(ratio.bp_meta.unassigned)} 无映射未归属</span>}
                  。收入没对上销售额时先看取数时间——批次停在月中就去 BP「应收单」重新取数。</>}
              </div>
            </div>}
          </div>}
          <NextBtn to={4} label="下一步 · 勾选录入金蝶" />
        </>}
      </>}

      {/* ══ ④ 勾选录入 ══ */}
      {step === 4 && <>
        {!res ? <NoData need="先在「② 解析复核」解析生成凭证" /> : <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', background: 'var(--bg,#fafaf7)', border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '14px 16px' }}>
            <span style={{ fontSize: 13 }}>已勾选 <b>{selArr.length}</b> / {V.length} 张 · 含税合计 <b style={{ fontVariantNumeric: 'tabular-nums' }}>{money(selGross)}</b></span>
            <label style={{ fontSize: 13, color: '#555' }}>凭证落账月份
              <select value={postYear} onChange={e => setPostYear(Number(e.target.value))} style={{ marginLeft: 6, padding: '5px 8px', borderRadius: 6, border: '0.5px solid #cfcdc4' }}>
                {[2025, 2026, 2027].map(y => <option key={y} value={y}>{y} 年</option>)}
              </select>
              <select value={effPeriod} onChange={e => setPostPeriod(Number(e.target.value))} style={{ marginLeft: 4, padding: '5px 8px', borderRadius: 6, border: '0.5px solid #cfcdc4' }}>
                {MONTHS.map(m => <option key={m} value={m}>第 {m} 期（{m}月）</option>)}
              </select>
            </label>
            <button onClick={doPost} disabled={!!busy || !canPost || !selArr.length} title={canPost ? '' : '你的账号没有「一键录入金蝶」权限'}
              style={{ ...btn, background: canPost ? '#1a7f4b' : '#f2f1ec', color: canPost ? '#fff' : '#a5a399', borderColor: canPost ? '#1a7f4b' : '#d9d7cf' }}>
              {busy === 'post' ? '录入中…' : '一键录入金蝶（存草稿）'}</button>
            {!canPost && <span style={{ fontSize: 12, color: 'var(--amber)' }}>写正式账套的敏感操作，需管理员在「账号管理」开通「物流计提·一键录入金蝶」</span>}
          </div>
          {postStat && <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '12px 16px', background: postStat.fail ? '#fff8ec' : '#eef7f0', fontSize: 13 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>录入结果　{postRes.year} 年第 {postRes.period} 期：成功 {postStat.saved} 张{postStat.skip ? ` · 疑已录过跳过 ${postStat.skip} 张` : ''}{postStat.fail ? ` · 失败/拦下 ${postStat.fail} 张` : ''}</div>
            {postStat.saved > 0 && <div style={{ color: '#1a7f4b', marginBottom: 4 }}>✓ 成功的凭证号已回填到清单和打印卡片，去「⑤ 打印附件」出纸交审核。</div>}
            {postRes.results.filter(it => it.status !== 'saved').map((it, i) => <div key={i} style={{ color: it.status === 'skipped' ? '#7a5a12' : '#a32d2d', marginTop: 2 }}>
              {it.status === 'skipped' ? '跳过' : '拦下'} · {it.主体} {it.公司全名} {it.费用归属}：{it.msg}</div>)}
          </div>}

          {/* 撤销 / 删除已录草稿 —— 录错了、测试的，从这里删掉金蝶草稿 */}
          {canPost && <div style={{ border: '1px solid var(--line,#e6e4dc)', borderRadius: 10, padding: '12px 16px', background: '#fbfbf8' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>撤销 / 删除已录草稿</span>
              <span style={{ fontSize: 12, color: '#8a8880' }}>查看本工具录入 {postYear} 年第 {effPeriod} 期的凭证，删掉录错/测试的草稿</span>
              <button onClick={loadPosted} disabled={pBusy} style={{ ...btn, marginLeft: 'auto' }}>{pBusy ? '加载中…' : (posted ? '刷新' : '查看已录入凭证')}</button>
            </div>
            {posted && (posted.items.length === 0
              ? <div style={{ fontSize: 12.5, color: '#8a8880', marginTop: 8 }}>本工具在 {postYear} 年第 {effPeriod} 期没有录入记录。</div>
              : <div style={{ marginTop: 10 }}>
                <div style={{ overflowX: 'auto', border: '0.5px solid #e2e0d8', borderRadius: 8 }}>
                  <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%', minWidth: 640 }}>
                    <thead><tr>{['', '凭证号', '摘要', '录入人', '录入时间', '金蝶状态'].map((h, i) => <th key={i} style={{ ...th, background: '#6b6f76' }}>{h}</th>)}</tr></thead>
                    <tbody>{posted.items.map(it => {
                      const can = it.可撤销
                      return <tr key={it.id} style={{ background: '#fff', opacity: can ? 1 : 0.6 }}>
                        <td style={{ ...tdP, border: INN, textAlign: 'center' }}><input type="checkbox" disabled={!can} checked={pSel.has(it.id)} onChange={() => setPSel(p => { const n = new Set(p); n.has(it.id) ? n.delete(it.id) : n.add(it.id); return n })} /></td>
                        <td style={{ ...tdP, border: INN, fontFamily: 'monospace' }}>{it.凭证号}</td>
                        <td style={{ ...tdP, border: INN, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.摘要}</td>
                        <td style={{ ...tdP, border: INN }}>{it.录入人}</td>
                        <td style={{ ...tdP, border: INN, color: '#77756e' }}>{it.录入时间}</td>
                        <td style={{ ...tdP, border: INN, textAlign: 'center', color: (it.金蝶状态 === '已审核' || it.金蝶状态 === '已提交') ? '#c0392b' : (it.金蝶状态 === '已删除' ? '#8a8880' : '#1a7f4b') }}>{it.金蝶状态}{!can && it.金蝶状态 !== '已删除' ? '（需去金蝶反审核）' : ''}</td>
                      </tr>
                    })}</tbody>
                  </table>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                  <button onClick={doUnpost} disabled={pBusy || pSel.size === 0} style={{ ...btn, background: '#c0392b', color: '#fff', borderColor: '#c0392b' }}>{pBusy ? '处理中…' : `撤销选中 · 删除草稿（${pSel.size}）`}</button>
                  <span style={{ fontSize: 11.5, color: '#8a8880' }}>只删本工具录入且仍是草稿态的；已提交/已审核的删不了（灰行）。删后可重新录入。</span>
                </div>
              </div>)}
          </div>}

          <div style={{ fontSize: 13.5, fontWeight: 600 }}>勾选要录入的凭证（{V.length} 张 · 缺税率的行不能勾 · 点行看分录）</div>
          {reviewTable(true)}
          {postStat && postStat.saved > 0 && <NextBtn to={5} label="下一步 · 打印附件" />}
        </>}
      </>}

      {/* ══ ⑤ 打印附件 ══ */}
      {step === 5 && <>
        {!res ? <NoData need="先在「② 解析复核」解析生成凭证" /> : <>
          <div className="la-noprint" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <button onClick={() => window.print()} style={{ ...btn, background: '#305496', color: '#fff', borderColor: '#305496' }}>打印复核清单 + 凭证卡片</button>
            <span style={{ fontSize: 12, color: '#1a7f4b' }}>✓ 复核清单在前、每张凭证一页卡片在后，可直接装订作凭证附件；录入成功的凭证号已自动标注。</span>
          </div>
          <div id="la-print">
            <div className="la-print-head">物流费计提复核清单　·　2026 年 {month} 月</div>
            {reviewTable(false)}
            <div className="la-cards">
              {groups.flatMap((g, gi) => g.items.map((v) => (
                <div className="la-card" key={`c${v._oi}`}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', borderBottom: '1px solid #333', paddingBottom: 6, marginBottom: 6 }}>
                    <div style={{ border: '0.5px solid #888', borderRadius: 3, padding: '4px 8px', lineHeight: 1.5, fontSize: 10 }}>
                      <span style={{ color: '#888' }}>主体　</span><span style={{ fontWeight: 600 }}>{v.主体}</span><br />
                      <span style={{ color: '#888' }}>凭证号　</span><span style={{ fontWeight: 600, fontFamily: 'monospace' }}>{v.凭证号 || '(待录)'}</span>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>物流费计提 · 计提附件</div>
                      <div style={{ fontSize: 10, color: '#555', marginTop: 2 }}>会计期间　2026 年 {month} 月</div>
                    </div>
                  </div>
                  <div style={{ fontSize: 10, marginBottom: 6 }}>供应商：<b>{v.公司全名}</b></div>
                  <div style={{ fontSize: 9.5, color: '#555', margin: '0 0 3px' }}>① 该供应商本月计提明细（高亮行 = 本张凭证）</div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9 }}>
                    <thead><tr>{COLS.map(h => <th key={h} style={cth}>{h}</th>)}</tr></thead>
                    <tbody>
                      {g.items.map((it) => (
                        <tr key={it._oi} className={it._oi === v._oi ? 'hl' : ''} style={it._oi === v._oi ? { background: '#FFF2CC', fontWeight: 600 } : {}}>
                          <td style={{ ...ctd, textAlign: 'center' }}>{it._seq}</td>
                          <td style={ctd}>{it.物流商}</td>
                          <td style={ctd}>{it.公司全名}</td>
                          <td style={ctr}>{money(it.含税)}</td>
                          <td style={{ ...ctd, textAlign: 'right' }}>{Math.round((it.税率 || 0) * 100)}%</td>
                          <td style={ctr}>{money(it.未税)}</td>
                          <td style={ctr}>{money(it.税额)}</td>
                          <td style={ctd}>{it.科目}</td>
                          <td style={ctd}>{it.部门}</td>
                          <td style={ctd}>{it.费用项目}</td>
                          <td style={ctd}>{it.业务线 || '—'}</td>
                          <td style={ctd}>{it.主体}</td>
                          <td style={{ ...ctd, textAlign: 'center' }}>{it.凭证号 || '(待录)'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ fontSize: 9.5, color: '#555', margin: '8px 0 3px' }}>② 本张凭证（{v.凭证号 || '待录'}）生成分录</div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9.5 }}>
                    <tbody>
                      {v.分录.map((l, k) => (
                        <tr key={k}>
                          <td style={{ ...ctd, width: 26, textAlign: 'center' }}>{l.方向}</td>
                          <td style={ctd}>{l.科目}</td>
                          <td style={{ ...ctd, color: '#555' }}>{l.维度}</td>
                          <td style={ctr}>{l.借方 ? money(l.借方) : ''}</td>
                          <td style={ctr}>{l.贷方 ? money(l.贷方) : ''}</td>
                        </tr>
                      ))}
                      <tr style={{ fontWeight: 600 }}>
                        <td style={ctd} colSpan={3}>合计（借 = 贷 ✓）</td>
                        <td style={ctr}>{money(v.未税 + v.税额)}</td>
                        <td style={ctr}>{money(v.含税)}</td>
                      </tr>
                    </tbody>
                  </table>
                  <div style={{ fontSize: 9.5, marginTop: 6 }}>摘要　{v.摘要}</div>
                  <div style={{ display: 'flex', gap: 28, marginTop: 10, fontSize: 9.5, color: '#666' }}>
                    <span>制单：＿＿＿＿</span><span>审核：＿＿＿＿</span><span>日期：＿＿＿＿</span>
                  </div>
                </div>
              )))}
            </div>
          </div>
        </>}
      </>}
    </div>
  </div>)
}

const btn = { padding: '6px 14px', borderRadius: 7, border: '0.5px solid #b9b7ae', background: '#fff', cursor: 'pointer', fontSize: 13 }
const inp = { padding: '6px 10px', borderRadius: 7, border: '0.5px solid #cfcdc4', fontSize: 13 }
