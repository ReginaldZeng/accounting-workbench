// [Change Log] Date:2026-07-03 Author:Claude/c Version:V1.1  前端 API 封装（加 reconcile/sync + 4位金额格式）
// cache:'no-store' —— 接口永不吃浏览器缓存，避免后端更新后前端拿到旧数据（字段对不上）
const j = async (url, opt) => { const r = await fetch(url, { cache: 'no-store', ...opt }); if(!r.ok) throw new Error(url+' '+r.status); return r.json(); }
export const getFund = () => j('/api/fund-dashboard')
export const syncFund = () => j('/api/fund-dashboard/sync', {method:'POST'})
export const getLedger = () => j('/api/account-ledger')
export const syncLedger = () => j('/api/account-ledger/sync', {method:'POST'})
export const setLedgerOverride = (payload) => j('/api/account-ledger/override', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
export const getReconcile = () => j('/api/reconcile')
export const syncReconcile = () => j('/api/reconcile/sync', {method:'POST'})
export const getOperators = () => j('/api/operators')
export const claimReconcile = (payload) => j('/api/reconcile/claim', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
// 登录 / 账号（阶段2）
const jp = (url, body) => j(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})})
export const login = (b) => jp('/api/login', b)
export const apiLogout = () => jp('/api/logout')
export const getMe = () => j('/api/me')
export const listUsers = () => j('/api/users')
export const createUser = (b) => jp('/api/users/create', b)
export const setUserActive = (b) => jp('/api/users/active', b)
export const resetPwd = (b) => jp('/api/users/reset-pwd', b)
export const deleteUser = (b) => jp('/api/users/delete', b)
export const setUserPerms = (b) => jp('/api/users/perms', b)
export const getPermCaps = () => j('/api/perms/caps')
// BP 权限码表对账（V2.106）：BP 是码表真相源，此处比对本地 CAP_META 是否漏登记。BP 不可达→available:false
export const getBpPermDrift = () => j('/api/bp-perm-drift')
export const setCapSensitivity = (b) => jp('/api/perms/sensitivity/toggle', b)
export const getPortalTools = () => j('/api/portal/tools')
export const getLlmHubStatus = (fresh) => j('/api/llm-hub/status' + (fresh ? '?fresh=1' : ''))
export const getLlmHubUsage = (days = 7) => j(`/api/llm-hub/usage?days=${days}`)
export const getGwCredentials = () => j('/api/llm-hub/gateway/credentials')
export const createGwCredential = (b) => jp('/api/llm-hub/gateway/credentials', b)
export const revokeGwCredential = (id) => jp(`/api/llm-hub/gateway/credentials/${id}/revoke`)
export const rotateGwCredential = (id) => jp(`/api/llm-hub/gateway/credentials/${id}/rotate`)
export const getGwUsage = (days = 7) => j(`/api/llm-hub/gateway/usage?days=${days}`)
export const setWorkbenchKey = (b) => jp('/api/llm-hub/key', b)
export const setWorkbenchPolicy = (b) => jp('/api/llm-hub/policy', b)
export const setWorkbenchModel = (b) => jp('/api/llm-hub/model', b)
export const addWorkbenchProvider = (b) => jp('/api/llm-hub/provider', b)
export const getLlmHubAudit = (days = 7, onlyErrors = false) => j(`/api/llm-hub/audit?days=${days}${onlyErrors ? '&onlyErrors=1' : ''}`)
export const savePortalTool = (b) => jp('/api/portal/tools/save', b)
export const deletePortalTool = (b) => jp('/api/portal/tools/delete', b)
export const resetPortalTools = () => jp('/api/portal/tools/reset-defaults', {})
export const apiChangePwd = (b) => jp('/api/change-pwd', b)
export const getDataSources = () => j('/api/data-sources')
export const syncDataSources = () => j('/api/data-sources/sync', {method:'POST'})
export const getBalanceAdjust = () => j('/api/balance-adjust')
export const syncBalanceAdjust = () => j('/api/balance-adjust/sync', {method:'POST'})
export const saveBalanceNote = (b) => jp('/api/balance-adjust/note', b)
export const getChannelAdjust = () => j('/api/channel-adjust')
export const syncChannelAdjust = () => j('/api/channel-adjust/sync', {method:'POST'})
// 密码走请求头(不进 URL/日志)；用 encodeURIComponent 兼容非 ASCII 密码
export const uploadBankZip = (file, password) => j('/api/bank-import/upload', {method:'POST', body:file, headers: password ? {'X-Zip-Password': encodeURIComponent(password)} : {}})
export const confirmBankDup = () => jp('/api/bank-import/confirm-dup', {})
export const getSubjectBalance = () => j('/api/subject-balance')
// 报表仪表盘（子公司报表）：GET 读缓存，POST 强刷金蝶（V2.248）
export const getReportDashboard = () => j('/api/report/dashboard')
export const refreshReportDashboard = () => j('/api/report/dashboard/refresh', { method: 'POST' })
export const syncSubjectBalance = () => j('/api/subject-balance/sync', {method:'POST'})
export const getSubjectCheck = () => j('/api/subject-balance/check')
export const uploadSubjectReport = (file) => j('/api/subject-balance/upload', {method:'POST', body:file})
export const getWealthRecon = () => j('/api/wealth-recon')
export const syncWealthRecon = () => j('/api/wealth-recon/sync', {method:'POST'})
export const refreshKingdee = () => j('/api/kingdee/refresh', {method:'POST'})
export const getConfig = () => j('/api/config')
export const setConfig = (b) => j('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
export const testKingdee = () => j('/api/kingdee/test')
export const parseLogistics = (file, month) => j(`/api/logistics-accrual/parse?month=${month}`, {method:'POST', body:file})
export const checkLogisticsSuppliers = (file, month) => j(`/api/logistics-accrual/suppliers-check?month=${month}`, {method:'POST', body:file})
export const getLogisticsRates = () => j('/api/logistics-accrual/tax-rates')
export const saveLogisticsRate = (b) => jp('/api/logistics-accrual/tax-rates/save', b)
export const deleteLogisticsRate = (b) => jp('/api/logistics-accrual/tax-rates/delete', b)
export const postLogistics = (b) => jp('/api/logistics-accrual/post', b)
// 账单直采(V2.195)：核对后账单包(多文件 FormData·字段 files)→计提明细活表
export const parseBills = (files, month, year) => { const fd = new FormData(); [...files].forEach(f => fd.append('files', f)); return j(`/api/logistics-accrual/bills-parse?month=${month}&year=${year || 2026}`, { method: 'POST', body: fd }) }
export const getBillUploads = (year, month) => j(`/api/logistics-accrual/bill-uploads?year=${year}&month=${month}`)
export const loadBillUpload = (id) => jp('/api/logistics-accrual/bill-uploads/load', { id })
export const saveBillUploadRows = (id, rows) => jp('/api/logistics-accrual/bill-uploads/save-rows', { id, rows })
export const parseLongForm = (file, month, year) => { const fd = new FormData(); fd.append('file', file); return j(`/api/logistics-accrual/long-form-parse?month=${month}&year=${year || 2026}`, { method: 'POST', body: fd }) }
export const submitBillUpload = (id) => jp('/api/logistics-accrual/bill-uploads/submit', { id })
export const getParseSupport = () => j('/api/logistics-accrual/parse-support')
export const logisticsPayRemind = (year, month, items) => jp('/api/logistics-accrual/pay-remind', { year, month, items })
export const getSupplierMatrix = (year, month) => j(`/api/logistics-accrual/supplier-matrix?year=${year}&month=${month}`)
export const setSupplierDoc = (body) => jp('/api/logistics-accrual/supplier-doc', body)
export const uploadInvoiceFile = (year, month, short, subject, file, kind) => { const fd = new FormData(); fd.append('file', file); return j(`/api/logistics-accrual/invoice-file?year=${year}&month=${month}&short=${encodeURIComponent(short)}&subject=${encodeURIComponent(subject)}&kind=${encodeURIComponent(kind || '发票')}`, { method: 'POST', body: fd }) }
export const deleteInvoiceFile = (id) => jp('/api/logistics-accrual/invoice-file-delete', { id })
export const diffParseBill = (year, month, short, subject, file) => { const fd = new FormData(); fd.append('file', file); return j(`/api/logistics-accrual/diff-parse?year=${year}&month=${month}&short=${encodeURIComponent(short)}&subject=${encodeURIComponent(subject)}`, { method: 'POST', body: fd }) }
export const getNotifyRecipients = () => j('/api/logistics-accrual/notify-recipients')
export const saveNotifyRecipients = (scene, mobiles, emails, passcode) => jp('/api/logistics-accrual/notify-recipients', { scene, mobiles, emails, passcode })
export const testNotify = (scene) => jp('/api/logistics-accrual/notify-test', { scene })
export const refreshLogisticsRow = (b) => jp('/api/logistics-accrual/row-refresh', b)
export const adoptFeeMap = (b) => jp('/api/logistics-accrual/adopt', b)
export const getFeeMap = () => j('/api/logistics-accrual/fee-map')
export const saveFeeMap = (b) => jp('/api/logistics-accrual/fee-map/save', b)
export const deleteFeeMap = (b) => jp('/api/logistics-accrual/fee-map/delete', b)
export const getBizlines = () => j('/api/logistics-accrual/bizlines')
export const saveBizline = (b) => jp('/api/logistics-accrual/bizlines/save', b)
export const deleteBizline = (b) => jp('/api/logistics-accrual/bizlines/delete', b)
export const getTypeMap = () => j('/api/logistics-accrual/type-map')
export const getExpenseRatio = (b) => jp('/api/logistics-accrual/expense-ratio', b)
export const getLogiSuppliers = () => j('/api/logistics-accrual/suppliers')
export const saveLogiSupplier = (b) => jp('/api/logistics-accrual/suppliers/save', b)
export const deleteLogiSupplier = (b) => jp('/api/logistics-accrual/suppliers/delete', b)
export const saveTypeMap = (b) => jp('/api/logistics-accrual/type-map/save', b)
export const deleteTypeMap = (b) => jp('/api/logistics-accrual/type-map/delete', b)
export const getLogisticsPosted = (year, period) => j(`/api/logistics-accrual/posted?year=${year}&period=${period}`)
export const unpostLogistics = (ids) => jp('/api/logistics-accrual/unpost', { ids })
export const listReconParsers = () => j('/api/logistics-recon/parsers')
export const listReconCarriers = (year, period) => j(`/api/logistics-recon/carriers?year=${year || 0}&period=${period || 0}`)
// 账单上传：一律用 FormData·字段 files（可多文件·天鹰按月拆多份，文件数不定）。后端两种都收，向后兼容。
const reconBody = (files) => { const fd = new FormData(); (Array.isArray(files) ? files : [files]).forEach(f => f && fd.append('files', f)); return fd }
export const parseRecon = (carrier, files) => j('/api/logistics-recon/parse?carrier=' + encodeURIComponent(carrier), { method: 'POST', body: reconBody(files) })
export const runRecon = (carrier, files) => j('/api/logistics-recon/reconcile?carrier=' + encodeURIComponent(carrier), { method: 'POST', body: reconBody(files) })
// 月结看板 / 期间封存（V2.41）——封存/解封失败时后端用 403/409 + {ok:false,msg} 表达，不能让 j() 抛掉 msg
const jpSoft = async (url, body) => {
  const r = await fetch(url, { cache: 'no-store', method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
  try { return await r.json() } catch (e) { return { ok: false, msg: '服务端无响应(' + r.status + ')' } }
}
// 导航模块上线管理（V2.63）——全员生效的上线开关，只管导航不挡接口
export const getNavModules = () => j('/api/nav-modules')
export const saveNavModules = (state, posts, templates) => jpSoft('/api/nav-modules/save', { state, posts, templates })
// 只存岗位模板（权限中枢「岗位模板设置」抽屉用）。后端 V2.144 起 state 缺席不再被重置
export const saveNavTemplates = (templates) => jpSoft('/api/nav-modules/save', { templates })
// 只存岗位名单（权限中枢就近新增岗位用；传全量名单）。posts 段后端本就有 isinstance guard
export const saveNavPosts = (posts) => jpSoft('/api/nav-modules/save', { posts })
export const addNavModule = (b) => jpSoft('/api/nav-modules/add-module', b)
export const delNavModule = (key) => jpSoft('/api/nav-modules/del-module', { key })
export const moveNavModule = (b) => jpSoft('/api/nav-modules/move', b)          // 改位置：sec/parent/order/label
export const saveNavSections = (sections) => jpSoft('/api/nav-sections/save', { sections })
export const setUserPost = (b) => jpSoft('/api/users/post', b)                   // 设岗位；apply=true 顺带套用模板
// 某年12个月各自数据状态。source 缺省=全局账本；传 'cl:<账簿代码>'=成本台账某主体的账本
export const getPeriodStatuses = (year, source) =>
  j('/api/period-statuses?year=' + year + (source ? '&source=' + encodeURIComponent(source) : ''))
export const getPeriod = () => j('/api/period')
export const closePeriod = (b) => jpSoft('/api/period/close', b)
export const reopenPeriod = (b) => jpSoft('/api/period/reopen', b)
// 成本台账（存货月结核对，V2.58）
// 成本计算单【原表】单传（V2.288）：接口取不到「来源单据/费用分配标准/分配标准值/作业活动」那 6 列，
// 只能从金蝶界面导出后传进来。只改本期输入里的这一块，不动已取的数。
// 一键金蝶取数（跨维度汇总表 API，V2.61/V2.116）
export const analyzeCostLedgerKingdee = (year, period, org) => j(`/api/cost-ledger/analyze-kingdee?year=${year}&period=${period}&org=${org}`, {method:'POST'})
// 成本台账 › 多主体（V2.126）——主体清单取自平台主体档案；每个主体一条独立封存线
export const getCostLedgerOrgs = () => j('/api/cost-ledger/orgs')
// 成本台账 › 期间化 + 封存（V2.122）——数据按期落库、全员共享；封存后只读、读快照
export const getCostLedgerState = (year, period, org) => j(`/api/cost-ledger/state?year=${year}&period=${period}&org=${org}`)
// 收发存明细查询（第④步）：服务端按 仓库/类别/关键字/负结存 筛+分页，不把 2,770 行推给页面
export const getCostLedgerDetail = ({ year, period, org, wh = '', cat = '', q = '', neg = 0,
  limit = 100, offset = 0 }) =>
  j(`/api/cost-ledger/detail?year=${year}&period=${period}&org=${org}`
    + `&wh=${encodeURIComponent(wh)}&cat=${encodeURIComponent(cat)}&q=${encodeURIComponent(q)}`
    + `&neg=${neg ? 1 : 0}&limit=${limit}&offset=${offset}`)
export const closeCostLedgerPeriod = (b) => jpSoft('/api/cost-ledger/close', b)
export const reopenCostLedgerPeriod = (b) => jpSoft('/api/cost-ledger/reopen', b)
// 成本台账 › 仓库类型对照维护（V2.119）——金蝶仓库档案(107) ⋃ 现有对照 ⋃ 本期出现过的仓库
export const getWhTypes = () => j('/api/cost-ledger/warehouse-types')
export const saveWhTypes = (map, types, notes) => jpSoft('/api/cost-ledger/warehouse-types', { map, types, notes })
// 存货台账 › 基础资料 › 类别↔科目对照（V2.254）——原先只在 sample_data 的 json 里、没有数据库兜底，
// 部署包又历来不带那个目录 → 服务器上一点取数就 500（V2.132）。现改为页面维护、存数据库。
// 存货台账 › 存货看板（V2.254）——本年 1..period 的结存趋势/构成/收发流量/周转
export const getCostLedgerDash = (year, period, org, months = 5, basis = 'current') =>
  j(`/api/cost-ledger/dashboard?year=${year}&period=${period}&org=${org}&months=${months}&basis=${basis}`)
export const getCatSubjects = () => j('/api/cost-ledger/cat-subjects')
export const saveCatSubjects = (subjects, extra) => jpSoft('/api/cost-ledger/cat-subjects', { subjects, extra })
// 基础数据 › 主体档案（平台级，V2.74）——凭证归档取简码作册号首段、物流计提取账簿代码写金蝶
export const getOrgs = () => j('/api/orgs')
export const saveOrg = (b) => jpSoft('/api/orgs/save', b)
export const deleteOrg = (id) => jpSoft('/api/orgs/delete', { id })
// 凭证归档（其它小工具，V2.77）
export const archiveOrgs = () => j('/api/archive/orgs')
export const archiveFind = (org, year, month, no) => j(`/api/archive/find?org=${encodeURIComponent(org)}&year=${year}&month=${month}&no=${no}`)
export const archiveVolumes = (org = '', year = '', status = '') => {
  const q = new URLSearchParams()
  if (org) q.set('org', org)
  if (year) q.set('year', year)
  if (status) q.set('status', status)
  return j('/api/archive/volumes' + (q.toString() ? '?' + q : ''))
}
export const archivePeriodInfo = (org, year, month) => j(`/api/archive/period-info?org=${encodeURIComponent(org)}&year=${year}&month=${month}`)
export const archiveRegister = (b) => jpSoft('/api/archive/register', b)
export const archiveLocations = () => j('/api/archive/locations')
export const archiveSaveLocation = (b) => jpSoft('/api/archive/locations/save', b)
export const archiveTransfer = (b) => jpSoft('/api/archive/transfer', b)
export const archiveBorrow = (b) => jpSoft('/api/archive/borrow', b)
export const archiveReturn = (b) => jpSoft('/api/archive/return', b)
export const archiveDestroyApply = (b) => jpSoft('/api/archive/destroy/apply', b)
export const archiveDestroyCancel = (b) => jpSoft('/api/archive/destroy/cancel', b)
export const archiveDestroyExecute = (b) => jpSoft('/api/archive/destroy/execute', b)
export const archiveImportTemplateUrl = '/api/archive/import-template'
export const archiveImport = (file) => fetch('/api/archive/import', { method: 'POST', body: file }).then(r => r.json())
export const archiveCheckup = (org, year, month, total) => j(`/api/archive/checkup?org=${encodeURIComponent(org)}&year=${year}&month=${month}&total=${total}`)
// 汇率录入（V2.159）——preview/post/history/unpost 用 jpSoft，好让 {ok:false} 的闸门/错误提示透传不被抛掉
export const getFxOrgs = () => j('/api/fxrate/orgs')
export const getFxStatus = (year, org) => j(`/api/fxrate/status?year=${year}&org=${encodeURIComponent(org || '')}`)
export const autorunFxRate = (b) => jpSoft('/api/fxrate/autorun-now', b)   // 预演(dry:1)/立即跑批 单月
export const getFxAutorunConfig = () => j('/api/fxrate/autorun-config')
export const toggleFxAutorun = (on) => jpSoft('/api/fxrate/autorun-toggle', { on })
export const previewFxRate = (b) => jpSoft('/api/fxrate/preview', b)
export const postFxRate = (b) => jpSoft('/api/fxrate/post', b)
export const getFxPosted = (year, month, org) => j(`/api/fxrate/posted?year=${year}&month=${month}&org=${encodeURIComponent(org||'')}`)
export const unpostFxRate = (ids) => jpSoft('/api/fxrate/unpost', { ids })
export const fxRateHistory = (b) => jpSoft('/api/fxrate/history', b)
export const getFxNotifyConfig = () => j('/api/fxrate/notify-config')
export const saveFxNotifyConfig = (b) => jpSoft('/api/fxrate/notify-config', b)
export const testFxNotify = () => jpSoft('/api/fxrate/notify-test', {})
// 报表导出（V2.241）——run/config 用 jpSoft，好让 {ok:false} 的路径/口令提示原样透传给用户
export const getRptExportOrgs = (year, period) => j(`/api/rptexport/orgs?year=${year}&period=${period}`)
export const getRptExportConfig = () => j('/api/rptexport/config')
export const saveRptExportConfig = (b) => jpSoft('/api/rptexport/config', b)
export const runRptExport = (b) => jpSoft('/api/rptexport/run', b)
export const getRptExportProgress = () => j('/api/rptexport/progress')
export const testRptExportNotify = () => jpSoft('/api/rptexport/notify-test', {})
export const requestRptExportSync = () => jpSoft('/api/rptexport/request-sync', {})
export const listRptExportFiles = (year, period) => j(`/api/rptexport/files?year=${year}&period=${period}`)
export const getRptExportPeriodStatus = (year) => j(`/api/rptexport/period-status?year=${year}`)
export const deleteRptExportFiles = (b) => jpSoft('/api/rptexport/delete', b)
// ── 电商对账（V2.250 条目⑤一期）──
export const getEcBasicdata = () => j('/api/ec/basicdata')
export const saveEcBasicdata = (b) => j('/api/ec/basicdata', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
export const runEcSettle = (form) => j('/api/ec/settle/run', {method:'POST', body: form})
export const ecSettleProgress = (id) => j(`/api/ec/settle/progress?run_id=${id}`)
export const ecSettleRuns = () => j('/api/ec/settle/runs')
export const ecSettleResult = (id, bucket, page, f) => j(`/api/ec/settle/result?run_id=${id}&bucket=${encodeURIComponent(bucket||'')}&page=${page||1}`
  + (f&&f.shop ? `&shop=${encodeURIComponent(f.shop)}` : '') + (f&&f.order ? `&order_no=${encodeURIComponent(f.order)}` : '')
  + (f&&f.ar ? `&ar_no=${encodeURIComponent(f.ar)}` : '') + (f&&f.serial ? `&serial_no=${encodeURIComponent(f.serial)}` : ''))
export const ecSources = (period) => j(`/api/ec/settle/sources?period=${period}`)
export const ecKdRefresh = (form) => j('/api/ec/settle/kd-refresh', {method:'POST', body: form})
export const ecManualUpload = (form) => j('/api/ec/settle/manual-upload', {method:'POST', body: form})
export const ecUploadFiles = (form) => j('/api/ec/settle/upload-files', {method:'POST', body: form})
export const ecRunAuto = (form) => j('/api/ec/settle/run-auto', {method:'POST', body: form})
export const ecPostVoucher = (b) => j('/api/ec/settle/post-voucher', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
export const ecPostStatus = (id) => j(`/api/ec/settle/post-status?run_id=${id}`)
export const ecVoucherCheck = (id) => j(`/api/ec/settle/voucher-check?run_id=${id}`)
export const ecOrderDetail = (rid, no) => j(`/api/ec/settle/order-detail?run_id=${rid}&order_no=${encodeURIComponent(no)}`)
export const ecExclNotes = (period, shop) => j(`/api/ec/settle/excl-notes?period=${period}&shop=${encodeURIComponent(shop)}`)
export const ecExclNoteSave = (b) => j('/api/ec/settle/excl-note', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
export const ecExclNotesBatch = (b) => j('/api/ec/settle/excl-notes-batch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
export const ecNotifyGet = () => j('/api/ec/settle/notify-recipients')
export const ecNotifySave = (b) => j('/api/ec/settle/notify-recipients', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
export const ecNotifyTest = (b) => j('/api/ec/settle/notify-test', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})
// 临时工考勤（V2.318）：两张表在内存里算完即走，不落库，故没有 get/save，只有一次性的 review/export
export const tempattParams = () => j('/api/tempatt/params')
export const tempattReview = (fd) => fetch('/api/tempatt/review', { method:'POST', body: fd }).then(r => r.json())
export const tempattExportUrl = '/api/tempatt/export'
export const tempattRates = (month) => j(`/api/tempatt/rates?month=${encodeURIComponent(month||'')}`)
export const tempattSaveRates = (b) => jpSoft('/api/tempatt/rates/save', b)
export const tempattStructure = (fd) => fetch('/api/tempatt/structure', { method:'POST', body: fd }).then(r => r.json())
// 按期留档（V2.320）：选月份直接看历史，不必重新上传
export const tempattPeriods = () => j('/api/tempatt/periods')
export const tempattPeriod = (month) => j(`/api/tempatt/period?month=${encodeURIComponent(month||'')}`)
export const tempattRerun = (b) => jpSoft('/api/tempatt/rerun', b)
export const tempattPeriodDel = (b) => jpSoft('/api/tempatt/period/delete', b)
// 看板取历次复核留档，不再上传结构表（V2.334）；fresh=1 强制重算缓存
export const tempattBoard = (fresh) => j('/api/tempatt/board' + (fresh ? '?fresh=1' : ''))
// 认定：确认某条可疑项无误（不是删除，仍保留并记录谁/何时/为什么）
export const tempattAck = (b) => jpSoft('/api/tempatt/ack', b)
export const tempattAckUndo = (b) => jpSoft('/api/tempatt/ack/undo', b)
// 批量认定/撤销（第⑦步合同外调整：十四笔里十三笔同一张审批单，逐条点没意义）
export const tempattAckBatch = (b) => jpSoft('/api/tempatt/ack/batch', b)
export const tempattAcks = () => j('/api/tempatt/acks')   // 认定台账（第④步）
// 复核结论「确认无误」：第⑨步用工成本汇总在确认之前不开
export const tempattSignoff = (month) => j(`/api/tempatt/signoff?month=${encodeURIComponent(month||'')}`)
export const tempattSignoffSet = (b) => jpSoft('/api/tempatt/signoff', b)
export const tempattSignoffUndo = (b) => jpSoft('/api/tempatt/signoff/undo', b)
// 本期奖惩已核对确认（第⑦步合同外调整，含空期正向签「无奖惩」）
export const tempattAdjSign = (month) => j(`/api/tempatt/adjsign?month=${encodeURIComponent(month||'')}`)
export const tempattAdjSignSet = (b) => jpSoft('/api/tempatt/adjsign', b)
export const tempattAdjSignUndo = (b) => jpSoft('/api/tempatt/adjsign/undo', b)
// 合同价（成本会计维护，按年月日生效）
export const tempattContract = (month) => j(`/api/tempatt/contract?month=${encodeURIComponent(month||'')}`)
export const tempattContractSave = (b) => jpSoft('/api/tempatt/contract/row/save', b)
export const tempattContractDel = (b) => jpSoft('/api/tempatt/contract/row/delete', b)
// 从钉钉取打卡（V2.354）：一个月约一万次调用、几分钟，所以是后台任务 + 轮询
export const tempattDingStatus = (month) => j(`/api/tempatt/ding/status?month=${encodeURIComponent(month||'')}`)
export const tempattDingPull = (fd) => fetch('/api/tempatt/ding/pull', { method:'POST', body: fd }).then(r => r.json())
export const tempattDingJob = (id) => j(`/api/tempatt/ding/job?id=${encodeURIComponent(id||'')}`)
export const tempattDingFileUrl = (id) => `/api/tempatt/ding/file?id=${encodeURIComponent(id||'')}`
// 合同价登记表 导出/导入（V2.363）。导入两步：先预览差异，确认了才写
export const tempattContractExportUrl = '/api/tempatt/contract/export'
export const tempattContractImport = (fd) => fetch('/api/tempatt/contract/import', { method:'POST', body: fd }).then(r => r.json())
export const tempattContractImportApply = (fd) => fetch('/api/tempatt/contract/import/apply', { method:'POST', body: fd }).then(r => r.json())
export const yuan = n => '¥'+Number(n||0).toLocaleString('en-US',{maximumFractionDigits:2})
export const yuan4 = n => Number(n||0).toLocaleString('en-US',{minimumFractionDigits:2, maximumFractionDigits:4})
// ── BOM报价审核（V-draft）：钉钉审批附件→解析→复核→定稿→BP消费 ──
export const getBomConfig = () => j('/api/bom/config')
export const getBomLedger = (mode = 'std') => j('/api/bom/ledger?mode=' + mode)
export const getBomEntry = (id) => j(`/api/bom/entry/${id}`)
export const bomFetchApproval = (approvalNo) => jp('/api/bom/fetch-approval', { approvalNo })
export const bomUpload = (files, approvalNo) => { const fd = new FormData(); (Array.isArray(files) ? files : [files]).forEach(f => f && fd.append('file', f)); if (approvalNo) fd.append('approvalNo', approvalNo); return fetch('/api/bom/upload', { method: 'POST', body: fd }).then(r => r.json()) }
export const bomBook = (stagingId, indexes) => jp('/api/bom/book', { stagingId, indexes })
export const bomReview = (entryId, fee, channel, materials) => jp('/api/bom/review', { entryId, fee, channel, materials })
export const bomConfirmStep = (entryId, step, on = true) => jp('/api/bom/confirm-step', { entryId, step, on })
export const bomApplyGoods = (entryId) => jp('/api/bom/apply-goods', { entryId })
export const bomClassify = (entryId, category, quotable, reason) =>
  jp('/api/bom/classify', { entryId, category, quotable, reason })
export const getBomApproval = (no) => j('/api/bom/approval?no=' + encodeURIComponent(no))
export const bomIntake = (approvalNo) => jp('/api/bom/intake', { approvalNo })
export const bomFinalReview = (entryId, approve, note) => jp('/api/bom/final-review', { entryId, approve, note })
export const bomVoidRequest = (payload) => jp('/api/bom/void-request', payload)
export const bomVoidReview = (payload) => jp('/api/bom/void-review', payload)
export const getBomPending = (groupId, productKey) =>
  j(`/api/bom/pending?groupId=${encodeURIComponent(groupId)}&productKey=${encodeURIComponent(productKey)}`)
export const bomRefetchReplace = (groupId, approvalNo) => jp('/api/bom/refetch-replace', { groupId, approvalNo })
export const bomReplaceSheet = (groupId, approvalNo, file) => {
  const fd = new FormData(); fd.append('groupId', groupId); fd.append('approvalNo', approvalNo || ''); fd.append('file', file)
  return fetch('/api/bom/replace-sheet', { method: 'POST', body: fd }).then(r => r.json())
}
export const getBomSettings = () => j('/api/bom/settings')
export const setBomSettings = (cfg) => jp('/api/bom/settings', cfg)
export const bomFinalize = (entryId) => jp('/api/bom/finalize', { entryId })
export const bomUnfinalize = (entryId) => jp('/api/bom/unfinalize', { entryId })
export const bomAttachBomList = (entryId, file) => { const fd = new FormData(); fd.append('entryId', entryId); fd.append('file', file); return fetch('/api/bom/attach-bomlist', { method: 'POST', body: fd }).then(r => r.json()) }
export const getBomKdPurchase = (code, months = 12) => j(`/api/bom/kd-purchase?code=${encodeURIComponent(code)}&months=${months}`)
export const getBomMaterialUsage = (code, exclude) => j(`/api/bom/material-usage?code=${encodeURIComponent(code)}${exclude ? `&exclude=${exclude}` : ''}`)
export const bomSetMatType = (entryId, mat, subType) => jp('/api/bom/set-mat-type', { entryId, subType, matCode: mat.matCode, matName: mat.matName, seg: mat.seg })
export const bomSetErpCode = (entryId, erpCode) => jp('/api/bom/set-erp-code', { entryId, erpCode })
export const getBomUsageSpreads = (entryId) => j(`/api/bom/usage-spreads?entryId=${entryId}`)
export const getBomInvoiceRules = () => j('/api/bom/invoice-rules')
export const setBomInvoiceRules = (rules) => jp('/api/bom/invoice-rules', { rules })
export const bomExportPrettyUrl = (id) => `/api/bom/export/pretty?entry_id=${id}`
export const bomExportOriginalUrl = (id) => `/api/bom/export/original?entry_id=${id}`
