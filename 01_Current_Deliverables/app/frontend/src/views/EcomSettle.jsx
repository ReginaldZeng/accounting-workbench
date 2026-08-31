// [Change Log] Date:2026-08-13 Author:Claude/c Version:V2.279(凭证核对逐笔跟行+总览上移+加载明示) / V2.252-278(前版)
// 「电商对账 › 收款核销」页（条目⑤一期）。V2.256 需求方定信息架构：
//   · 左上角=期间-状态（期间选择器+状态徽章，参考银行对账模式）
//   · 页签①文件导入：刷新金蝶(自动) → 支付宝流水(自动·来自银行对账流水包) → 手工文件灯表(绿灯/灰灯)
//   · 页签②对账概览 ③逐单核销 ④凭证预览(含一键录入凭证——ec_post 敏感权限、草稿 only、配置齐才亮)
// 口径：确认书⑤；引擎 kernels/ec_settle.py。
import React, { useEffect, useRef, useState } from 'react'
import {
  runEcSettle, ecSettleProgress, ecSettleRuns, ecSettleResult,
  ecSources, ecKdRefresh, ecUploadFiles, ecRunAuto, ecPostVoucher, ecPostStatus,
  ecNotifyGet, ecNotifySave, ecNotifyTest, ecOrderDetail, ecExclNotes, ecExclNoteSave, ecExclNotesBatch,
  getEcBasicdata, ecVoucherCheck,
} from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

const fmt = n => Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const cnt = n => Number(n || 0).toLocaleString()
const nowPeriod = () => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') }

const EcStyle = () => <style>{`
.ec-wrap{padding:18px 24px 40px}
.ec-wrap .head{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.ec-wrap .h-title{font-size:17px;font-weight:600}
.ec-wrap .h-sub{font-size:12px;color:var(--ink-2);margin-top:3px}
.ec-badge{font-size:12px;padding:4px 12px;border-radius:999px;font-weight:600}
.ec-tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-top:10px}
.ec-tab{padding:11px 15px;font-size:13px;font-weight:600;color:var(--ink-3);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.ec-tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.ec-tab:hover{color:var(--ink-2)}
.ec-body{padding-top:14px;display:flex;flex-direction:column;gap:14px}
.ec-card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:16px 20px}
.ec-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.ec-kpi{border:1px solid var(--line);border-left-width:3px;border-radius:9px;padding:11px 13px;background:var(--bg-sub)}
.ec-kpi .kl{font-size:11.5px;color:var(--ink-2)}
.ec-kpi .kv{font-size:21px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.ec-kpi .ks{font-size:11px;color:var(--ink-3);margin-top:2px}
.ec-bks{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:10px}
.ec-bk{border:1px solid var(--line);border-left-width:3px;border-radius:9px;padding:12px 14px;background:var(--bg);cursor:pointer}
.ec-bk.on{background:var(--accent-soft);border-color:var(--accent)}
.ec-bk .bt{font-weight:600;font-size:12.5px}
.ec-bk .bn{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;margin:4px 0 2px}
.ec-bk .bd{font-size:11px;color:var(--ink-3);line-height:1.6}
.ec-tblwrap{border:1px solid var(--line);border-radius:9px;overflow:auto}
.ec-wrap table{border-collapse:collapse;font-size:12.5px;width:100%}
.ec-wrap thead th{padding:9px 10px;font-weight:600;white-space:nowrap;text-align:left;color:var(--ink-2);background:var(--bg-sub);border-bottom:1px solid var(--line)}
.ec-wrap tbody td{padding:8px 10px;white-space:nowrap;border-top:1px solid var(--line)}
.ec-wrap tr.r-crossed td{background:var(--red-bg)}
.ec-wrap tr.r-real td{background:var(--amber-bg)}
.ec-num{text-align:right!important;font-family:var(--font-mono,ui-monospace,monospace);font-size:11.5px;font-variant-numeric:tabular-nums}
.ec-mono{font-family:var(--font-mono,ui-monospace,monospace);font-size:11.5px}
.ec-tag{font-size:11px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.ec-chip{font-size:12px;padding:5px 12px;border-radius:999px;cursor:pointer;border:1px solid var(--line-strong);background:transparent;color:var(--ink-2)}
.ec-chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.ec-inp{padding:6px 10px;font-size:12.5px;border:1px solid var(--line-strong);border-radius:8px;background:var(--bg);color:var(--ink)}
.ec-light{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:1px}
.ec-step{display:flex;gap:11px;align-items:flex-start;padding:11px 14px;flex:1;min-width:200px}
.ec-step .num{width:22px;height:22px;border-radius:50%;flex:0 0 auto;display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700;background:var(--accent-soft);color:var(--accent)}
.ec-steps{display:flex;border:1px solid var(--line);border-radius:9px;background:var(--bg-rail);overflow-x:auto}
.ec-step + .ec-step{border-left:1px solid var(--line)}
@keyframes ec-breathe{0%,100%{box-shadow:0 0 0 0 rgba(31,166,105,.55),0 0 6px 1px rgba(31,166,105,.5)}50%{box-shadow:0 0 0 8px rgba(31,166,105,0),0 0 12px 3px rgba(31,166,105,.25)}}
.ec-live{display:inline-block;width:13px;height:13px;border-radius:50%;flex:0 0 auto;
  background:radial-gradient(circle at 35% 35%, #4fd69a, var(--green,#1f7a55));
  border:2px solid var(--green-bg,#e8f4ee);animation:ec-breathe 1.6s ease-in-out infinite}
.ec-frac{font-size:11px;padding:2px 9px;border-radius:999px;background:var(--amber-bg,#f8f0e0);color:var(--amber,#a35a00);font-weight:700;font-variant-numeric:tabular-nums}
.ec-master{display:flex;gap:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--bg)}
.ec-shops{width:280px;flex:0 0 auto;border-right:1px solid var(--line);background:var(--bg-sub)}
.ec-shoprow{display:flex;align-items:center;gap:10px;padding:12px 14px;cursor:pointer;border-bottom:1px solid var(--line);font-size:12.5px}
.ec-shoprow:hover{background:var(--accent-soft)}
.ec-shoprow.on{background:var(--bg);font-weight:600;box-shadow:inset 3px 0 0 var(--accent)}
.ec-detail{flex:1;padding:14px 18px;min-width:0}
.ec-filerow{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid var(--line);font-size:12.5px;flex-wrap:wrap}
.ec-filerow:last-child{border-bottom:none}
.ec-filerow .fname{font-weight:600;min-width:150px}
.ec-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:40}
.ec-drawer{position:fixed;top:0;right:0;width:min(600px,94vw);height:100vh;background:var(--bg);border-left:1px solid var(--line-strong);z-index:50;overflow-y:auto;padding:18px 20px 40px;box-shadow:-4px 0 24px rgba(0,0,0,.12)}
.ec-dsec{border:1px solid var(--line);border-radius:9px;margin-top:12px;overflow:hidden}
.ec-dsec .st{padding:8px 13px;background:var(--bg-sub);font-weight:600;font-size:12.5px;border-bottom:1px solid var(--line)}
.ec-dsec .sb{padding:11px 13px;font-size:12.5px;line-height:1.9}
.ec-drawer tr.neg td{background:var(--red-bg)}
.ec-drawer tr.partner td{background:var(--blue-bg,#e7f0fc)}
.ec-tl{list-style:none;margin:0;padding:0}
.ec-tl li{position:relative;padding:0 0 11px 20px;font-size:12.5px;line-height:1.6}
.ec-tl li::before{content:'';position:absolute;left:4px;top:5px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.ec-tl li.red::before{background:var(--red,#c0392b)}
.ec-tl li.green::before{background:var(--green,#1f7a55)}
.ec-tl li.blue::before{background:var(--blue,#2c6bcf)}
.ec-tl li::after{content:'';position:absolute;left:7.5px;top:15px;bottom:-2px;width:1px;background:var(--line-strong)}
.ec-tl li:last-child::after{display:none}
.ec-tl .tt{font-family:var(--font-mono,ui-monospace,monospace);font-size:11px;color:var(--ink-3);margin-right:8px}
`}</style>

const Tag = ({ c, children }) => {
  const m = { ok: ['var(--green-bg,#e8f4ee)', 'var(--green,#1f7a55)'], gray: ['var(--gray-bg,#eef0f3)', 'var(--gray,#6b7280)'],
    red: ['var(--red-bg,#fbecea)', 'var(--red,#c0392b)'], amber: ['var(--amber-bg,#f8f0e0)', 'var(--amber,#a35a00)'],
    blue: ['var(--blue-bg,#e7f0fc)', 'var(--blue,#2c6bcf)'], accent: ['var(--accent-soft)', 'var(--accent)'] }[c] || []
  return <span className="ec-tag" style={{ background: m[0], color: m[1] }}>{children}</span>
}
// 绿=实心（到位）/ 黄=实心（部分/可选缺）/ 灰=**空心粗圈**（缺项，一眼看出这里空着）
const Light = ({ on, half }) => <span className="ec-light" style={on
  ? { background: 'var(--green,#1f7a55)', width: 11, height: 11 }
  : half ? { background: 'var(--amber,#a35a00)', width: 11, height: 11 }
  : { background: 'transparent', border: '2.5px solid var(--ink-3,#9298a4)', width: 11, height: 11 }} />
const BUCKET = {
  ok: { label: '可核销', tag: <Tag c="ok">可核销</Tag> },
  ufirst: { label: 'U先·月末总账', tag: <Tag c="gray">U先·月末总账</Tag> },
  crossed: { label: '串单嫌疑', tag: <Tag c="red">串单嫌疑</Tag> },
  real: { label: '真差异', tag: <Tag c="amber">真差异</Tag> },
  carry: { label: '跨期调节', tag: <Tag c="blue">跨期调节</Tag> },
}
const BUCKET_CARDS = [
  ['ufirst', 'var(--gray,#6b7280)', 'U先引流 · 月末总账汇总', '逐单跳过；每月对总数 Σ收款 ↔ 总账汇总凭证'],
  ['real', 'var(--red,#c0392b)', '真差异 · 要人看', '逐笔点开处理，桶内即工作清单'],
  ['crossed', 'var(--amber,#a35a00)', '串单嫌疑 · ±配对', '合并发货盖错单号；改单号前禁止核销'],
  ['ok', 'var(--green,#1f7a55)', '可核销', '平台＝应收分毫相等，导出清单去金蝶操作'],
]

export default function EcomSettle({ user }) {
  const canRun = !!(user && (user.role === 'admin' || (user.perms || {}).ec_settle_upload))
  const canPost = !!(user && (user.role === 'admin' || (user.perms || {}).ec_post))
  const [tab, setTab] = useState('import')       // import / overview / orders / voucher
  const [period, setPeriod] = useState(nowPeriod())
  const [sources, setSources] = useState(null)
  const [selShop, setSelShop] = useState('')     // 主从式：左店铺列表当前选中
  const [runs, setRuns] = useState([])
  const [runId, setRunId] = useState(null)
  const [stats, setStats] = useState(null)
  const [progress, setProgress] = useState('')
  const [err, setErr] = useState('')
  const [bucket, setBucket] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [postSt, setPostSt] = useState(null)
  const [nData, setNData] = useState(null)     // 通知设置：场景收件人+公共名单+口令是否已配
  const [nPass, setNPass] = useState('')
  const [nMsg, setNMsg] = useState('')
  const [detail, setDetail] = useState(null)   // 操作详情抽屉：null=关 / {loading} / 档案数据
  const [exNotes, setExNotes] = useState({})   // 剔除留痕·定性登记：{流水号: {verdict,note,operator,ts}}（V2.274）
  const [exEdit, setExEdit] = useState(null)   // 正在登记的行：{serial, verdict, note}
  const [exSel, setExSel] = useState({})       // 批量勾选（V2.275 活动一场=一串流水）：{流水号: {serial,kind,flow_ts,amount}}
  const [exBatch, setExBatch] = useState({ verdict: '正常', note: '' })   // 批量条：判定+说明
  const [exFlt, setExFlt] = useState({ kind: '', status: '', q: '' })     // 剔除明细筛选（V2.276）：类别chip/登记状态/模糊搜索
  const [exPage, setExPage] = useState(1)      // 剔除明细分页（V2.276）：每页 50 笔
  const [vchk, setVchk] = useState(null)       // 与已入账凭证核对（V2.278）：null=未查 / {loading} / 结果
  const [shopNames, setShopNames] = useState({})   // 管理名称（V2.277）：{旺店通店名: 简称}——只改显示，数据键不动
  useEffect(() => {
    getEcBasicdata().then(b => {
      const m = {}
      ;(b.shop_map || []).forEach(r => { const v = (r.mgmt_name || '').trim(); if (v) m[r.wdt_name] = v })
      setShopNames(m)
    }).catch(() => {})
  }, [])
  const shopDisp = s => shopNames[s] || s
  const [flt, setFlt] = useState({ shop: '', order: '', ar: '', serial: '' })   // 生效的筛选
  const [fin, setFin] = useState({ shop: '', order: '', ar: '', serial: '' })   // 输入中的筛选
  const openDetail = (o) => {
    setDetail({ loading: true, order: o })
    ecOrderDetail(runId, o.order_no).then(d => setDetail({ ...d, loading: false }))
      .catch(e => setDetail({ loading: false, order: o, error: String(e.message || e) }))
  }
  // 抽屉翻单：当前筛选+桶的结果序列内 上一笔/下一笔，页边界自动翻页续走
  const navDetail = async (dir) => {
    if (!detail?.order || !data) return
    const idx = data.orders.findIndex(x => x.order_no === detail.order.order_no)
    let target = null
    if (idx >= 0 && idx + dir >= 0 && idx + dir < data.orders.length) {
      target = data.orders[idx + dir]
    } else {
      const np = page + dir
      if (np < 1 || np > Math.ceil(data.total / data.size)) return
      const d2 = await ecSettleResult(runId, bucket, np, flt)
      setPage(np); setData(d2)
      target = dir > 0 ? d2.orders[0] : d2.orders[d2.orders.length - 1]
    }
    if (target) openDetail(target)
  }
  const flowRef = useRef(); const rkRefs = useRef({})
  const timer = useRef(null)
  useEffect(() => { if (tab === 'notify' && canRun && !nData) ecNotifyGet().then(setNData).catch(e => setNMsg(String(e.message || e))) }, [tab])

  const loadRuns = (pickPeriod) => ecSettleRuns().then(r => {
    setRuns(r.runs || [])
    const done = (r.runs || []).find(x => x.status === 'done' && (!pickPeriod || x.period === pickPeriod))
    if (done) { setRunId(done.id); setStats(done.stats); if (!pickPeriod) setPeriod(done.period) }
  }).catch(e => setErr(String(e.message || e)))
  useEffect(() => { loadRuns() }, [])
  useEffect(() => { if (/^\d{4}-\d{2}$/.test(period)) ecSources(period).then(setSources).catch(() => setSources(null)) }, [period])
  useEffect(() => {
    if (!runId) return
    ecSettleResult(runId, bucket, page, flt).then(setData).catch(e => setErr(String(e.message || e)))
    ecPostStatus(runId).then(setPostSt).catch(() => setPostSt(null))
    setVchk(null)                                // 换跑批即作废上一次凭证核对结果
  }, [runId, bucket, page, flt])
  const doVchk = () => {
    setVchk({ loading: true })
    ecVoucherCheck(runId).then(setVchk).catch(e => setVchk({ error: String(e.message || e) }))
  }
  // 剔除留痕定性登记按 期间×店铺 存（跨跑批留存），跟着当前跑批加载
  const curRun = runs.find(r => r.id === runId)
  useEffect(() => {
    if (curRun) ecExclNotes(curRun.period, curRun.shop).then(r => setExNotes(r.notes || {})).catch(() => setExNotes({}))
  }, [runId, runs.length])
  const saveExNote = async (d, kind, verdict, note) => {
    try {
      await ecExclNoteSave({ period: curRun.period, shop: curRun.shop, serial: d['流水号'],
                             kind, flow_ts: d['时间'], amount: d['金额'], verdict, note })
      const r = await ecExclNotes(curRun.period, curRun.shop)
      setExNotes(r.notes || {}); setExEdit(null)
    } catch (e) { setErr(String(e.message || e)) }
  }
  // 批量勾选（V2.275）：行勾 + 表头全选筛选结果（V2.276 起在渲染处按筛选集切换）；一次登记同一判定与说明（撤销登记=清记录）
  const exToggle = (d, kind) => {
    const sn = d['流水号']; if (!sn) return
    setExSel(s => { const n = { ...s }; if (n[sn]) delete n[sn]
      else n[sn] = { serial: sn, kind, flow_ts: d['时间'], amount: d['金额'] }; return n })
  }
  const batchExNote = async () => {
    try {
      const verdict = exBatch.verdict === '撤销' ? '' : exBatch.verdict
      await ecExclNotesBatch({ period: curRun.period, shop: curRun.shop, verdict,
                               note: verdict ? exBatch.note : '', items: Object.values(exSel) })
      const r = await ecExclNotes(curRun.period, curRun.shop)
      setExNotes(r.notes || {}); setExSel({}); setExEdit(null)
    } catch (e) { setErr(String(e.message || e)) }
  }

  // 期间-状态徽章：本期有完成跑批→已核销；数据源齐→就绪；否则待导入
  const periodRun = runs.find(r => r.period === period && r.status === 'done')
  const srcReady = sources && (sources.rows || []).some(r => r.alipay.ok)
  const badge = progress ? ['blue', '跑批中…'] : periodRun ? ['ok', '本期已核销'] : srcReady ? ['accent', '数据就绪 · 待核销'] : ['gray', '待导入数据']

  const poll = (id) => {
    clearInterval(timer.current)
    timer.current = setInterval(async () => {
      try {
        const p = await ecSettleProgress(id)
        setProgress(p.step || p.status)
        if (p.status === 'done') { clearInterval(timer.current); setProgress(''); setStats(p.stats); setRunId(id); setPage(1); setTab('overview'); loadRuns(period) }
        if (p.status === 'error') { clearInterval(timer.current); setProgress(''); setErr('跑批失败：' + (p.error || p.stats?.error || '未知')) }
      } catch (e) { /* 网络抖动继续轮询 */ }
    }, 2500)
  }

  const kdRefresh = async () => {
    try {
      setErr('')
      const form = new FormData(); form.append('period', period)
      await ecKdRefresh(form)
      const t = setInterval(() => ecSources(period).then(s => {
        setSources(s); if (!s?.kd?.refreshing) clearInterval(t)
      }).catch(() => clearInterval(t)), 3000)
    } catch (e) { setErr(String(e.message || e)) }
  }
  const [upMsg, setUpMsg] = useState(null)
  const uploadMulti = async (shop, fileList) => {
    if (!fileList || !fileList.length) return
    try {
      setErr(''); setUpMsg({ shop, text: '识别中…' })
      const form = new FormData()
      form.append('period', period); form.append('shop', shop)
      ;[...fileList].forEach(f => form.append('files', f))
      const r = await ecUploadFiles(form)
      setUpMsg({ shop,
        text: (r.recognized || []).map(x => `${x.file} → ${x.kind} ✓`).join('；'),
        unknown: r.unknown || [] })
      ecSources(period).then(setSources)
    } catch (e) { setUpMsg(null); setErr(String(e.message || e)) }
  }
  const runAuto = async (shop) => {
    try {
      setErr('')
      const form = new FormData(); form.append('shop', shop); form.append('period', period)
      setProgress('发起自动跑批…')
      const r = await ecRunAuto(form)
      poll(r.run_id)
    } catch (e) { setProgress(''); setErr(String(e.message || e).replace(/^.*\d{3} ?/, '')) }
  }
  const runManual = async () => {
    try {
      setErr('')
      const f = flowRef.current?.files?.[0]
      if (!f) { setErr('请先选支付宝流水文件'); return }
      const form = new FormData()
      form.append('shop', sources?.rows?.[0]?.shop || '星期零STARFIELD 天猫官旗店')
      form.append('period', period); form.append('flow', f)
      setProgress('上传中…')
      const r = await runEcSettle(form)
      poll(r.run_id)
    } catch (e) { setProgress(''); setErr(String(e.message || e)) }
  }
  const doPost = async () => {
    if (!window.confirm('确认把两张结算凭证（扣款项 / 收款核销）录入金蝶？\n\n· 只建【草稿】，提交/审核仍由人在金蝶完成\n· 同期同店只允许录一次（防重复记账）')) return
    try {
      setErr('')
      const r = await ecPostVoucher({ run_id: runId })
      alert('已建草稿：' + r.vouchers.map(v => `${v.kind} 内码${v.kd_id} ¥${fmt(v.amount)}`).join('；') + '\n请到金蝶复核后提交/审核。')
      ecPostStatus(runId).then(setPostSt)
    } catch (e) { setErr(String(e.message || e)) }
  }
  const exportOk = () => {
    ecSettleResult(runId, 'ok', 1).then(async first => {
      let rows = first.orders; const pages = Math.ceil(first.total / first.size)
      for (let p = 2; p <= pages; p++) rows = rows.concat((await ecSettleResult(runId, 'ok', p)).orders)
      const head = '店铺,平台订单号,支付渠道,到账时间,流水号,平台到账,应收单号,应收金额\n'
      const body = rows.map(o => [o.shop, o.order_no, o.channel, o.arrive_time, o.serial_no, o.plat_amt, o.ar_no, o.ar_amt].join(',')).join('\n')
      const blob = new Blob(['﻿' + head + body], { type: 'text/csv' })
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
      a.download = `可核销清单_${period}.csv`; a.click()
    })
  }

  const b = stats?.buckets || {}
  const run = runs.find(r => r.id === runId)
  const postReady = postSt && !postSt.cfg_missing?.length && !postSt.codes_missing?.length && !(postSt.posted || []).length

  return <div className="ec-wrap">
    <EcStyle />
    {/* 页头照银行对账成品：左=标题+说明；右上角=状态徽章 + 期间控件 + 刷新金蝶数据 */}
    <div className="head">
      <div>
        <div className="h-title">收款核销</div>
        <div className="h-sub">支付宝结算 ↔ 金蝶应收 · 逐单核销 · 差异分桶（对金蝶只读；凭证仅建草稿）</div>
      </div>
      <div className="h-right">
        <span className="ec-badge" style={{
          background: { ok: 'var(--green-bg,#e8f4ee)', accent: 'var(--accent-soft)', blue: 'var(--blue-bg,#e7f0fc)', gray: 'var(--gray-bg,#eef0f3)' }[badge[0]],
          color: { ok: 'var(--green,#1f7a55)', accent: 'var(--accent)', blue: 'var(--blue,#2c6bcf)', gray: 'var(--gray,#6b7280)' }[badge[0]] }}>{badge[1]}</span>
        <PeriodPicker year={Number(period.slice(0, 4)) || 2026} period={Number(period.slice(5, 7)) || 1}
          source="ec" onChange={(y, m) => setPeriod(y + '-' + String(m).padStart(2, '0'))} />
        {canRun && <button className="btn-sec" style={{ height: 34, padding: '0 14px', borderRadius: 8, fontSize: 12.5, cursor: 'pointer' }}
          disabled={sources?.kd?.refreshing} onClick={kdRefresh}>{sources?.kd?.refreshing ? '刷新中…' : '刷新金蝶数据'}</button>}
      </div>
    </div>

    <div className="ec-tabs">
      {[['import', '文件导入'], ['overview', '对账概览'], ['orders', '逐单核销'], ['voucher', '凭证预览'],
        ...(canRun ? [['notify', '通知设置']] : [])]
        .map(([k, l]) => <div key={k} className={'ec-tab' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</div>)}
    </div>

    <div className="ec-body">
      {err && <div style={{ fontSize: 12.5, color: 'var(--red,#c0392b)' }}>{err}</div>}
      {progress && <div style={{ fontSize: 12.5, color: 'var(--blue,#2c6bcf)' }}>⏳ {progress}（十万行流水约 2–4 分钟，可切页签）</div>}

      {/* ================= ① 文件导入 ================= */}
      {tab === 'import' && (() => {
        const rows = sources?.rows || []
        const some = f => rows.some(f), every = f => rows.length > 0 && rows.every(f)
        const s1 = every(r => r.alipay.ok); const s1h = some(r => r.alipay.ok)
        const s2 = !!sources?.kd?.meta
        const s3 = every(r => ['销售出库', '销售退货', '退款不退货'].every(k => r.wdt[k].ok))
        const s3h = some(r => ['销售出库', '销售退货', '退款不退货'].some(k => r.wdt[k].ok))
        const s4 = every(r => r.platform['平台订单']?.ok)
        const s4h = some(r => ['平台订单', '平台退款', '平台价保'].some(k => r.platform[k]?.ok))
        return <>
        <div className="ec-steps">
          <div className="ec-step"><span className="num">1</span><div>
            {/* 灯=流水包里找没找到电商账户的流水文件（绿=各店都找到 / 黄=部分 / 灰=没有）*/}
            <b style={{ fontSize: 12.5 }}><Light on={s1} half={!s1 && s1h} />账户流水 · 自动</b>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
              {(() => {
                const n = rows.reduce((a, r) => a + (r.alipay.files || []).length, 0)
                const who = sources?.flow_pkg ? `（${sources.flow_pkg.operator} 于 ${sources.flow_pkg.ts} 上传）` : ''
                return n > 0 ? `外部资金账户（支付宝/微信/聚合…）· 已从银行对账流水包识别 ${n} 个文件${who}`
                  : `外部资金账户流水——本期流水包里没找到，出纳在「银行对账 › 数据导入」传${who}`
              })()}</div></div></div>
          <div className="ec-step"><span className="num">2</span><div>
            <b style={{ fontSize: 12.5 }}><Light on={s2} half={false} />金蝶数据 · 半自动</b>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
              {sources?.kd?.error ? <span style={{ color: 'var(--red,#c0392b)' }}>刷新失败：{sources.kd.error}</span>
                : sources?.kd?.meta ? `${sources.kd.meta.operator} 于 ${sources.kd.meta.ts} 刷新 · ${cnt(sources.kd.meta.rows)} 行（跑批用此缓存）`
                : '还没刷过（右上角「刷新金蝶数据」）——不刷也行，跑批时实时拉取'}</div></div></div>
          <div className="ec-step"><span className="num">3</span><div>
            <b style={{ fontSize: 12.5 }}><Light on={s3} half={!s3 && s3h} />旺店通数据上传</b>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>销售出库·销售退货·退款不退货——拖入自动识别；①②③齐＝可核销</div></div></div>
          <div className="ec-step"><span className="num">4</span><div>
            <b style={{ fontSize: 12.5 }}><Light on={s4} half={!s4 && s4h} />平台数据</b>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>卖家中心订单导出（多导一个月）——四类全齐＝呼吸灯</div></div></div>
        </div>

        {sources?.hint_no_shop
          ? <div className="ec-card" style={{ fontSize: 12.5, color: 'var(--amber,#a35a00)' }}>{sources.hint_no_shop}</div>
          : <div className="ec-master">
            {/* 左：店铺列表——四类全齐=呼吸灯；①②③齐=常亮绿·可核销；否则缺x/4（分子=缺数,分母=总数） */}
            <div className="ec-shops">
              <div style={{ padding: '11px 14px', fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', borderBottom: '1px solid var(--line)' }}>
                店铺 · {period}</div>
              {(sources?.rows || []).map(r => {
                const sel = (selShop || sources.rows[0]?.shop) === r.shop
                return <div key={r.shop} className={'ec-shoprow' + (sel ? ' on' : '')} onClick={() => setSelShop(r.shop)}>
                  {r.ready_all ? <span className="ec-live" title="四类数据全齐" />
                    : r.ready_settle ? <span className="ec-light" style={{ background: 'var(--green,#1f7a55)' }} title="①②③齐，可核销（平台数据未传）" />
                    : <span className="ec-frac" title="缺的文件数 / 需要的总数">缺{r.missing}/4</span>}
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{shopDisp(r.shop)}</span>
                  {r.ready_settle && !r.ready_all && <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--green,#1f7a55)' }}>可核销</span>}
                </div>
              })}
            </div>
            {/* 右：选中店铺的四类文件明细 + 拖入识别 */}
            {(() => {
              const r = (sources?.rows || []).find(x => x.shop === (selShop || sources?.rows?.[0]?.shop))
              if (!r) return <div className="ec-detail" style={{ color: 'var(--ink-3)', fontSize: 12.5 }}>左侧还没有店铺——基础资料里给店铺配支付宝账号。</div>
              const kdOk = !!sources?.kd?.meta
              return <div className="ec-detail">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
                  <b style={{ fontSize: 13 }}>{shopDisp(r.shop)}</b>
                  {shopNames[r.shop] && <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 6 }}>{r.shop}</span>}
                  <span className="ec-mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{r.acct}</span>
                  {canRun
                    ? <button className="btn-primary" style={{ marginLeft: 'auto', padding: '6px 16px', borderRadius: 8, fontSize: 12.5, cursor: 'pointer' }}
                        disabled={!r.alipay.ok || !!progress} onClick={() => runAuto(r.shop)}>开始核销</button>
                    : <span style={{ marginLeft: 'auto' }}><Tag c="amber">需跑批权限</Tag></span>}
                </div>
                <div className="ec-filerow">
                  <Light on={r.alipay.ok} /><span className="fname">① 账户流水（自动）</span>
                  {r.alipay.ok
                    ? <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{r.alipay.files.join('；')}</span>
                    : <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>本期流水包里没找到——确认出纳已在「银行对账 › 数据导入」上传</span>}
                </div>
                <div className="ec-filerow">
                  <Light on={kdOk} half={!kdOk} /><span className="fname">② 金蝶数据（半自动）</span>
                  <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>
                    {kdOk ? `缓存 ${cnt(sources.kd.meta.rows)} 行 · ${sources.kd.meta.operator} ${sources.kd.meta.ts}`
                      : '未刷新——跑批时实时拉取（也可点上方「刷新」先备好）'}</span>
                </div>
                {['销售出库', '销售退货', '退款不退货'].map((k, i) => <div className="ec-filerow" key={k}>
                  <Light on={r.wdt[k].ok} half={!r.wdt[k].ok} />
                  <span className="fname">{i === 0 ? '③ ' : '　 '}旺店通 · {k}{k === '退款不退货' ? '' : ''}</span>
                  {r.wdt[k].ok
                    ? <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{r.wdt[k].name}</span>
                    : <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                        {k === '退款不退货' ? '未识别到（缺了核销也能跑，退款调节记 0）' : '未识别到（发货核对要用；核销可先跑）'}</span>}
                </div>)}
                {[['平台订单', '④ 平台 · 订单导出', '未识别到（卖家中心按月导出，多导一个月）', false],
                  ['平台退款', '　 平台 · 退款明细', '未识别到（推荐——「货物状态」列解释未发货退款单）', true],
                  ['平台保证金', '　 平台 · 保证金明细', '未识别到（推荐——结算后客服退款走保证金池，缺它此类单成假差异）', true],
                  ['平台价保', '　 平台 · 价保赔付', '未识别到（可选，本期无价保则为空表）', true]].map(([k, label, miss, opt]) =>
                  <div className="ec-filerow" key={k}>
                    <Light on={r.platform[k]?.ok} half={opt && !r.platform[k]?.ok} />
                    <span className="fname">{label}</span>
                    {r.platform[k]?.ok
                      ? <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{r.platform[k].name}</span>
                      : <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{miss}</span>}
                  </div>)}
                {canRun && <div style={{ marginTop: 12, padding: '12px 14px', border: '1px dashed var(--line-strong)', borderRadius: 9, background: 'var(--bg-sub)' }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>拖入文件 · 自动识别归类</div>
                  <input type="file" multiple accept=".xlsx" style={{ fontSize: 12 }}
                    onChange={e => { uploadMulti(r.shop, e.target.files); e.target.value = '' }} />
                  <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>
                    把旺店通（销售出库/销售退货/退款不退货）和卖家中心（订单导出）的文件<b>全选一次拖入</b>，按表头自动认类型；认不出的会单独列出来，不猜不静默。</div>
                  {upMsg && upMsg.shop === r.shop && <div style={{ fontSize: 11.5, marginTop: 6, color: 'var(--green,#1f7a55)' }}>
                    {upMsg.text}
                    {(upMsg.unknown || []).length > 0 && <div style={{ color: 'var(--red,#c0392b)' }}>未识别：{upMsg.unknown.join('；')}——表头对不上任何已知类型，请人工确认</div>}
                  </div>}
                </div>}
              </div>
            })()}
          </div>}
        {(sources?.orphan_files || []).length > 0 && <div style={{ fontSize: 11.5, color: 'var(--amber,#a35a00)' }}>
          ⚠ 流水包里有 {sources.orphan_files.length} 个支付宝文件的账号不在店铺对照表：{sources.orphan_files.join('；')}——若是新店铺，去「基础资料」补配（不静默忽略）</div>}

        <div className="ec-card">
          <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>兜底 · 手工上传流水（银行对账没这期流水包时用）</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 12.5, color: 'var(--ink-2)' }}>
            支付宝流水 <input type="file" ref={flowRef} accept=".xlsx,.xls,.zip" style={{ fontSize: 12 }} />
            {canRun && <button className="btn-sec" style={{ padding: '5px 14px', borderRadius: 8, fontSize: 12, cursor: 'pointer' }}
              onClick={runManual} disabled={!!progress}>手工跑批</button>}
            <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>支持 .xlsx / 支付宝原始 .xls / .xls.zip；多页工作簿按表头自动认页</span>
          </div>
        </div>

        {runs.length > 0 && <div className="ec-card">
          <div style={{ fontWeight: 600, fontSize: 12.5, marginBottom: 6 }}>跑批历史</div>
          <div className="ec-tblwrap" style={{ maxWidth: 860 }}>
            <table>
              <thead><tr><th>#</th><th>结算期</th><th>店铺</th><th>状态</th><th>数据来源</th><th>操作人 / 时间</th></tr></thead>
              <tbody>{runs.map(r => <tr key={r.id} style={{ cursor: 'pointer' }}
                onClick={() => { setRunId(r.id); setStats(r.stats); setPeriod(r.period); setPage(1); setTab('overview') }}>
                <td className="ec-mono">{r.id}</td><td className="ec-mono">{r.period}</td><td>{shopDisp(r.shop)}</td>
                <td>{r.status === 'done' ? <Tag c="ok">完成</Tag> : r.status === 'error' ? <Tag c="red">失败</Tag> : <Tag c="blue">进行中</Tag>}</td>
                <td style={{ whiteSpace: 'normal', fontSize: 11.5, color: 'var(--ink-2)' }}>{r.filenames}</td>
                <td style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{r.operator} {r.ts}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </div>}
      </>})()}

      {/* ================= ② 对账概览 ================= */}
      {tab === 'overview' && (!stats
        ? <div className="ec-card" style={{ color: 'var(--ink-2)' }}>还没有本期跑批结果——去「文件导入」页签开始核销。</div>
        : <>
          {run && <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
            {run.period} · {shopDisp(run.shop)} · 数据来源：{run.filenames} · {run.operator} {run.ts}
            {stats?.unmapped_codes > 0 && <b style={{ color: 'var(--red,#c0392b)' }}> · ⚠ {stats.unmapped_codes} 个费目码未映射科目——去「基础资料」补齐后重跑</b>}</div>}
          <div className="ec-kpis">
            {[['支付宝流水', cnt(stats.flow_rows), '直连+聚合两账户', 'var(--blue,#2c6bcf)'],
              ['结算订单', cnt(stats.orders), '按业务基础订单号', 'var(--blue,#2c6bcf)'],
              ['平台收入', fmt(stats.plat_amt), '交易收款−余额退款', 'var(--accent)'],
              ['金蝶应收命中', fmt(stats.ar_amt), cnt(stats.ar_rows) + ' 行整段拉取', 'var(--accent)'],
              ['平台费用', fmt(stats.fee_out), '扣款项合计', 'var(--accent)'],
              ['退款不退货调节', fmt(stats.rk_amt), cnt(stats.refund_rows) + ' 行', 'var(--green,#1f7a55)']].map(([l, v, s, c]) =>
              <div key={l} className="ec-kpi" style={{ borderLeftColor: c }}>
                <div className="kl">{l}</div><div className="kv">{v}</div><div className="ks">{s}</div>
              </div>)}
          </div>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>差异分桶 —— {cnt(stats.orders)} 单进来，人只需要看 {cnt(b.real?.cnt || 0)} 笔</div>
          <div className="ec-bks">
            {BUCKET_CARDS.map(([k, color, title, note]) => <div key={k}
              className={'ec-bk' + (bucket === k ? ' on' : '')} style={{ borderLeftColor: color }}
              onClick={() => { setBucket(k); setPage(1); setTab('orders') }}>
              <div className="bt">{title}</div>
              <div className="bn">{cnt(b[k]?.cnt)} 笔 · {fmt(b[k]?.amount)}</div>
              <div className="bd">{note}</div>
            </div>)}
          </div>
          {/* V2.273-276 需求方定：剔除留痕=逐笔明细+定性登记+批量勾选；V2.276 类别chips筛选+状态/模糊搜索+分页 */}
          {(data?.excluded || []).length > 0 && (() => {
            const allRows = []
            data.excluded.forEach(e => (e.detail || []).forEach(d => allRows.push({ e, d })))
            const q = exFlt.q.trim().toLowerCase()
            const rows = allRows.filter(({ e, d }) => {
              if (exFlt.kind && e.kind !== exFlt.kind) return false
              const n = d['流水号'] ? exNotes[d['流水号']] : null
              if (exFlt.status === '未登记' && n) return false
              if (exFlt.status === '正常' && (!n || n.verdict !== '正常')) return false
              if (exFlt.status === '违规' && (!n || n.verdict !== '违规')) return false
              if (q) {
                const hay = [d['时间'], d['流水号'], d['对方'], d['描述'], String(d['金额']),
                  n && n.note, n && n.operator].filter(Boolean).join(' ').toLowerCase()
                if (!hay.includes(q)) return false
              }
              return true
            })
            const PS = 50
            const pages = Math.max(1, Math.ceil(rows.length / PS))
            const pg = Math.min(exPage, pages)
            const pageRows = rows.slice((pg - 1) * PS, pg * PS)
            const selectable = rows.filter(({ d }) => d['流水号'])
            const allSel = selectable.length > 0 && selectable.every(({ d }) => exSel[d['流水号']])
            const toggleFiltered = () => setExSel(s => {
              const nx = { ...s }
              selectable.forEach(({ e, d }) => { const sn = d['流水号']
                if (allSel) delete nx[sn]
                else nx[sn] = { serial: sn, kind: e.kind, flow_ts: d['时间'], amount: d['金额'] } })
              return nx
            })
            const truncated = data.excluded.filter(e => (e.detail || []).length < e.cnt)
            return <div className="ec-card">
              <div style={{ marginBottom: 10 }}><b style={{ fontSize: 13 }}>剔除留痕</b>
                <span style={{ fontSize: 12, color: 'var(--ink-2)', marginLeft: 10 }}>非天猫结算业务，不进凭证与核销——逐笔过目定性：对得上活动/推广的属正常，对不上的按违规资金流出去追。</span></div>
              {/* 类别 chips：汇总即筛选（V2.276） */}
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                <button className={'ec-chip' + (!exFlt.kind ? ' on' : '')}
                  onClick={() => { setExFlt({ ...exFlt, kind: '' }); setExPage(1) }}>
                  全部 {cnt(data.excluded.reduce((s, e) => s + e.cnt, 0))} 笔</button>
                {data.excluded.map(e => <button key={e.id} className={'ec-chip' + (exFlt.kind === e.kind ? ' on' : '')} title={e.kind}
                  onClick={() => { setExFlt({ ...exFlt, kind: e.kind }); setExPage(1) }}>
                  {e.kind.split('（')[0]} {cnt(e.cnt)} 笔 · {fmt(e.amount)}</button>)}
              </div>
              {/* 状态筛选 + 模糊搜索（V2.276） */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
                <select className="ec-inp" style={{ padding: '4px 8px', fontSize: 12 }} value={exFlt.status}
                  onChange={ev => { setExFlt({ ...exFlt, status: ev.target.value }); setExPage(1) }}>
                  <option value="">全部状态</option><option value="未登记">未登记</option>
                  <option value="正常">已登记·对上活动/推广</option><option value="违规">已登记·违规要追</option>
                </select>
                <input className="ec-inp" style={{ padding: '4px 8px', fontSize: 12, width: 280 }} value={exFlt.q}
                  placeholder="模糊搜索：流水号 / 对方 / 描述 / 金额 / 登记说明"
                  onChange={ev => { setExFlt({ ...exFlt, q: ev.target.value }); setExPage(1) }} />
                <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>筛出 {cnt(rows.length)} 笔
                  {rows.length !== allRows.length && <> / 共 {cnt(allRows.length)} 笔</>}</span>
              </div>
              {/* 批量操作条（V2.275）：活动一场=一串流水，勾完一次登记 */}
              {canRun && Object.keys(exSel).length > 0 && <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
                margin: '0 0 10px', padding: '8px 12px', border: '1px solid var(--line-strong)', borderRadius: 8, background: 'var(--bg-sub)', fontSize: 12 }}>
                <b>已选 {Object.keys(exSel).length} 笔 · 合计 {fmt(Object.values(exSel).reduce((s, x) => s + Number(x.amount || 0), 0))}</b>
                <select className="ec-inp" style={{ padding: '3px 6px', fontSize: 11.5 }} value={exBatch.verdict}
                  onChange={ev => setExBatch({ ...exBatch, verdict: ev.target.value })}>
                  <option value="正常">对上活动/推广</option><option value="违规">违规·要追</option><option value="撤销">撤销登记</option>
                </select>
                {exBatch.verdict !== '撤销' && <input className="ec-inp" style={{ padding: '3px 6px', fontSize: 11.5, width: 220 }} value={exBatch.note}
                  placeholder={exBatch.verdict === '违规' ? '去向/追款说明（整批同一句）' : '对上的活动或推广计划（整批同一句）'}
                  onChange={ev => setExBatch({ ...exBatch, note: ev.target.value })} />}
                <button className="btn-sec" style={{ padding: '3px 12px', fontSize: 11.5, cursor: 'pointer' }} onClick={batchExNote}>
                  批量{exBatch.verdict === '撤销' ? '撤销' : '登记'}</button>
                <button style={{ padding: '3px 8px', fontSize: 11.5, cursor: 'pointer', background: 'none', border: 'none', color: 'var(--ink-3)' }}
                  onClick={() => setExSel({})}>清除选择</button>
              </div>}
              <div className="ec-tblwrap">
                <table>
                  <thead><tr>
                    {canRun && <th style={{ width: 26 }}><input type="checkbox" style={{ cursor: 'pointer' }} checked={allSel}
                      disabled={!selectable.length} title="全选/清空筛选结果（跨页）" onChange={toggleFiltered} /></th>}
                    <th>时间</th><th className="ec-num">金额</th><th>流水号</th><th>类别</th><th>对方 / 描述（原文）</th><th>处理记录</th>
                  </tr></thead>
                  <tbody>
                    {pageRows.map(({ e, d }, i) => {
                      const sn = d['流水号'] || ''
                      const n = sn ? exNotes[sn] : null
                      const editing = exEdit && exEdit.serial === sn
                      return <tr key={sn || 'r' + i}>
                        {canRun && <td><input type="checkbox" style={{ cursor: sn ? 'pointer' : 'not-allowed' }}
                          checked={!!exSel[sn]} disabled={!sn} onChange={() => exToggle(d, e.kind)} /></td>}
                        <td className="ec-mono">{d['时间']}</td>
                        <td className="ec-num" style={{ color: Number(d['金额']) < 0 ? 'var(--red,#c0392b)' : undefined }}>{fmt(d['金额'])}</td>
                        <td className="ec-mono">{sn || '—'}</td>
                        <td style={{ fontSize: 11.5 }} title={e.kind}>{e.kind.split('（')[0]}</td>
                        <td style={{ whiteSpace: 'normal', fontSize: 11.5, color: 'var(--ink-2)' }}>{[d['对方'], d['描述']].filter(Boolean).join(' · ') || '—'}</td>
                        <td style={{ whiteSpace: 'normal', minWidth: 220 }}>
                          {/* V2.274 定性登记：对上活动/推广=正常，对不上=违规——记录跨跑批留存 */}
                          {editing ? <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                            <select className="ec-inp" style={{ padding: '3px 6px', fontSize: 11.5 }} value={exEdit.verdict}
                              onChange={ev => setExEdit({ ...exEdit, verdict: ev.target.value })}>
                              <option value="正常">对上活动/推广</option><option value="违规">违规·要追</option>
                            </select>
                            <input className="ec-inp" style={{ padding: '3px 6px', fontSize: 11.5, width: 150 }} value={exEdit.note}
                              placeholder={exEdit.verdict === '违规' ? '去向/追款说明' : '对上的活动或推广计划'}
                              onChange={ev => setExEdit({ ...exEdit, note: ev.target.value })} />
                            <button className="btn-sec" style={{ padding: '3px 10px', fontSize: 11.5, cursor: 'pointer' }}
                              onClick={() => saveExNote(d, e.kind, exEdit.verdict, exEdit.note)}>保存</button>
                            <button style={{ padding: '3px 8px', fontSize: 11.5, cursor: 'pointer', background: 'none', border: 'none', color: 'var(--ink-3)' }}
                              onClick={() => setExEdit(null)}>取消</button>
                          </span>
                          : n ? <span style={{ fontSize: 11.5 }}>
                            <b style={{ color: n.verdict === '违规' ? 'var(--red,#c0392b)' : 'var(--green,#1f7a55)' }}>
                              {n.verdict === '违规' ? '✗ 违规·要追' : '✓ 对上活动/推广'}</b>
                            {n.note && <span style={{ color: 'var(--ink-2)' }}>　{n.note}</span>}
                            <span style={{ color: 'var(--ink-3)' }}>　{n.operator} · {String(n.ts).slice(0, 10)}</span>
                            {canRun && <>
                              <button style={{ marginLeft: 6, padding: 0, fontSize: 11.5, cursor: 'pointer', background: 'none', border: 'none', color: 'var(--accent)' }}
                                onClick={() => setExEdit({ serial: sn, verdict: n.verdict, note: n.note })}>改</button>
                              <button style={{ marginLeft: 4, padding: 0, fontSize: 11.5, cursor: 'pointer', background: 'none', border: 'none', color: 'var(--ink-3)' }}
                                onClick={() => saveExNote(d, e.kind, '', '')}>撤</button>
                            </>}
                          </span>
                          : !sn ? <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>旧跑批无流水号——重跑本期后可登记</span>
                          : canRun ? <button className="btn-sec" style={{ padding: '3px 12px', fontSize: 11.5, cursor: 'pointer' }}
                              onClick={() => setExEdit({ serial: sn, verdict: '正常', note: '' })}>登记</button>
                          : <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>未登记</span>}
                        </td>
                      </tr>
                    })}
                    {pageRows.length === 0 && <tr><td colSpan={canRun ? 7 : 6} style={{ color: 'var(--ink-3)', fontSize: 12 }}>没有符合筛选条件的明细。</td></tr>}
                  </tbody>
                </table>
              </div>
              {/* 分页（V2.276）：每页 50 笔 */}
              {pages > 1 && <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, fontSize: 12.5 }}>
                <span style={{ color: 'var(--ink-2)' }}>共 {cnt(rows.length)} 笔 · 第 {pg} / {pages} 页</span>
                <button className="btn-sec" style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}
                  disabled={pg <= 1} onClick={() => setExPage(pg - 1)}>‹ 上一页</button>
                <button className="btn-sec" style={{ padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}
                  disabled={pg >= pages} onClick={() => setExPage(pg + 1)}>下一页 ›</button>
              </div>}
              {truncated.length > 0 && <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--amber,#a35a00)' }}>
                {truncated.map(e => `「${e.kind.split('（')[0]}」仅存 ${(e.detail || []).length}/${e.cnt} 笔明细`).join('；')}——该次为旧版跑批的样例留痕，重跑本期即全量。</div>}
            </div>
          })()}
        </>)}

      {/* ================= ③ 逐单核销 ================= */}
      {tab === 'orders' && (!stats
        ? <div className="ec-card" style={{ color: 'var(--ink-2)' }}>还没有跑批结果。</div>
        : <div className="ec-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            {['', 'ok', 'real', 'crossed', 'ufirst', 'carry'].map(k => <button key={k} className={'ec-chip' + (bucket === k ? ' on' : '')}
              onClick={() => { setBucket(k); setPage(1) }}>{k === '' ? '全部' : BUCKET[k].label}{k && b[k] ? ' ' + cnt(b[k].cnt) : ''}</button>)}
            <button className="btn-sec" style={{ marginLeft: 'auto', padding: '6px 14px', borderRadius: 8, fontSize: 12.5, cursor: 'pointer' }}
              onClick={exportOk} disabled={!runId}>导出可核销清单</button>
          </div>
          {/* 四路筛选（V2.267 需求方定）：店铺 / 平台订单号 / 应收单号 / 流水号 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--ink-2)' }}>
            <select className="selctl" value={fin.shop}
              onChange={e => { const v = { ...fin, shop: e.target.value }; setFin(v); setFlt(v); setPage(1) }}>
              <option value="">全部店铺</option>
              {[...new Set([...(sources?.rows || []).map(r => r.shop), ...(data?.orders || []).map(o => o.shop)])].filter(Boolean)
                .map(s => <option key={s} value={s}>{shopDisp(s)}</option>)}
            </select>
            {[['order', '平台订单号'], ['ar', '应收单号'], ['serial', '流水号']].map(([k, ph]) =>
              <input key={k} className="ec-inp ec-mono" style={{ width: 150 }} placeholder={ph} value={fin[k]}
                onChange={e => setFin({ ...fin, [k]: e.target.value })}
                onKeyDown={e => { if (e.key === 'Enter') { setFlt({ ...fin }); setPage(1) } }} />)}
            <button className="btn-sec" style={{ padding: '5px 13px', borderRadius: 8, fontSize: 12, cursor: 'pointer' }}
              onClick={() => { setFlt({ ...fin }); setPage(1) }}>筛选</button>
            {(flt.shop || flt.order || flt.ar || flt.serial) && <button className="btn-sec" style={{ padding: '5px 13px', borderRadius: 8, fontSize: 12, cursor: 'pointer' }}
              onClick={() => { const z = { shop: '', order: '', ar: '', serial: '' }; setFin(z); setFlt(z); setPage(1) }}>清空</button>}
          </div>
          <div className="ec-tblwrap">
            <table style={{ minWidth: 1120 }}>
              <thead><tr>{['店铺', '平台订单号', '支付渠道', '平台到账', '应收单号', '应收金额', '退款调节', '到账时间', '差异', '状态', '说明']
                .map(h => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>{(data?.orders || []).map(o => <tr key={o.id} className={o.bucket === 'crossed' ? 'r-crossed' : o.bucket === 'real' ? 'r-real' : ''}
                style={{ cursor: 'pointer' }} onClick={() => openDetail(o)} title="点击看操作详情（金蝶蓝红逐行/退货链/伙伴单）">
                <td style={{ color: 'var(--ink-2)' }}>{o.shop ? (shopNames[o.shop] || o.shop.replace('星期零STARFIELD ', '')) : '—'}</td>
                <td className="ec-mono" style={{ color: 'var(--accent)' }}>{o.order_no}</td>
                <td>{o.channel === '聚合' ? <Tag c="amber">聚合</Tag> : <Tag c="blue">{o.channel || '支付宝'}</Tag>}</td>
                <td className="ec-num">{fmt(o.plat_amt)}</td>
                <td className="ec-mono">{o.ar_no || <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                <td className="ec-num">{fmt(o.ar_amt)}</td>
                <td className="ec-num">{o.rk_amt ? fmt(o.rk_amt) : ''}</td>
                <td className="ec-mono" style={{ color: 'var(--ink-2)' }}>{(o.arrive_time || '').slice(5, 16)}</td>
                <td className="ec-num" style={{ color: Math.abs(o.diff) > 0.005 ? 'var(--red,#c0392b)' : 'var(--ink-3)', fontWeight: Math.abs(o.diff) > 0.005 ? 700 : 400 }}>{fmt(o.diff)}</td>
                <td>{(BUCKET[o.bucket] || BUCKET.real).tag}</td>
                <td style={{ whiteSpace: 'normal', maxWidth: 260, fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>{o.note}</td>
              </tr>)}</tbody>
            </table>
          </div>
          {data && <div style={{ marginTop: 10, fontSize: 12, color: 'var(--ink-2)', display: 'flex', gap: 10, alignItems: 'center' }}>
            共 {cnt(data.total)} 行
            <button className="btn-sec" style={{ padding: '4px 12px', borderRadius: 7, fontSize: 12, cursor: 'pointer' }} disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ 上一页</button>
            第 {page} / {Math.max(1, Math.ceil(data.total / data.size))} 页
            <button className="btn-sec" style={{ padding: '4px 12px', borderRadius: 7, fontSize: 12, cursor: 'pointer' }} disabled={page >= Math.ceil(data.total / data.size)} onClick={() => setPage(page + 1)}>下一页 ›</button>
          </div>}
        </div>)}

      {/* ================= ④ 凭证预览（含一键录入） ================= */}
      {tab === 'voucher' && (!stats
        ? <div className="ec-card" style={{ color: 'var(--ink-2)' }}>还没有跑批结果。</div>
        : <div className="ec-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>费目→科目按「基础资料」映射；<b style={{ color: 'var(--red,#c0392b)' }}>「待定」</b>=新费目码。</span>
            <span style={{ marginLeft: 'auto' }} />
            <button className="btn-sec" style={{ padding: '7px 16px', borderRadius: 8, fontSize: 12.5, cursor: 'pointer' }}
              disabled={vchk?.loading} onClick={doVchk}
              title="本期账已做时：按 期间+账簿+摘要(客户名+SKD收款单号) 查金蝶总账凭证，与本页数字逐层核对（只读）">
              {vchk?.loading ? '⏳ 正在查金蝶…' : '与已入账凭证核对'}</button>
            {(postSt?.posted || []).length > 0 && <Tag c="ok">本期已录：{postSt.posted.map(p => `${p.kind} 内码${p.kd_id}`).join('；')}</Tag>}
            {canPost && !(postSt?.posted || []).length && <button className="btn-primary"
              style={{ padding: '7px 18px', borderRadius: 8, fontSize: 12.5, cursor: postReady ? 'pointer' : 'not-allowed', opacity: postReady ? 1 : .55 }}
              disabled={!postReady} onClick={doPost}
              title={postReady ? '两张凭证建为金蝶草稿' : '需先在基础资料配齐：' +
                [(postSt?.cfg_missing || []).length ? '凭证配置(' + postSt.cfg_missing.join('/') + ')' : '',
                 (postSt?.codes_missing || []).length ? '费目科目编码(' + postSt.codes_missing.slice(0, 4).join('、') + (postSt.codes_missing.length > 4 ? '…' : '') + ')' : ''].filter(Boolean).join('；')}>
              一键录入结算凭证（草稿）</button>}
            {!canPost && <Tag c="amber">录入凭证需「一键录入结算凭证」权限（敏感）</Tag>}
          </div>
          {postSt && !postReady && !(postSt.posted || []).length && (postSt.cfg_missing?.length || postSt.codes_missing?.length) > 0 &&
            <div style={{ fontSize: 12, color: 'var(--amber,#a35a00)', marginBottom: 10 }}>
              ⚠ 录入前需在「基础资料」配齐：{postSt.cfg_missing?.length ? '凭证配置（' + postSt.cfg_missing.join('、') + '）' : ''}
              {postSt.cfg_missing?.length && postSt.codes_missing?.length ? '；' : ''}
              {postSt.codes_missing?.length ? '费目科目编码（' + postSt.codes_missing.join('、') + '）' : ''}——科目编码请以春艳实际记账口径为准
            </div>}
          {/* 与已入账凭证核对（V2.278；V2.279 需求方定：总览挪第一屏+加载明示+"一笔笔跟在后面"——费目行尾核对列 */}
          {vchk?.loading && <div style={{ margin: '0 0 12px', padding: '9px 13px', border: '1px dashed var(--line-strong)', borderRadius: 9, background: 'var(--bg-sub)', fontSize: 12.5, color: 'var(--ink-2)' }}>
            ⏳ 正在查金蝶总账凭证（登录 + 按期间/账簿/摘要检索，一般 10~30 秒）——查到后总览在这里、逐笔结果跟在下方每行费目后面。</div>}
          {vchk && !vchk.loading && <div style={{ margin: '0 0 12px', padding: '10px 14px', border: '1px solid var(--line)', borderRadius: 10, background: 'var(--bg-sub)' }}>
            {vchk.error ? <div style={{ fontSize: 12.5, color: 'var(--red,#c0392b)' }}>核对失败：{vchk.error}</div>
            : !vchk.found ? <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>◌ {vchk.msg}</div>
            : <>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>已入账凭证核对</div>
              {vchk.vouchers.map(v => <div key={v.bill_no} style={{ fontSize: 12.5, marginBottom: 6 }}>
                <b>{v.grp || '记'}-{v.gno}</b>
                <span style={{ color: 'var(--ink-2)' }}>（凭证号 <span className="ec-mono">{v.bill_no}</span> · {v.date} · 收款单 <span className="ec-mono">{v.skd || '—'}</span> · 制单 {v.maker} · {v.lines} 行）</span>
                　<Tag c={v.audited ? 'ok' : 'amber'}>{v.status}{v.checker ? `：${v.checker} ${v.audit_dt}` : ''}</Tag>
                <span style={{ color: 'var(--ink-2)' }}>　借1012 <b className="ec-mono">{fmt(v.dr_1012)}</b> · 贷1122 <b className="ec-mono">{fmt(v.cr_1122)}</b></span>
              </div>)}
              <div className="ec-tblwrap" style={{ marginTop: 8, maxWidth: 680 }}>
                <table>
                  <thead><tr><th></th><th className="ec-num">凭证侧</th><th className="ec-num">本页跑批侧</th><th className="ec-num">差异</th></tr></thead>
                  <tbody>{[['净到账（借1012 其他货币资金）', vchk.totals.recv_theirs, vchk.totals.recv_ours],
                           ['核销应收（贷1122 应收账款）', vchk.totals.ar_theirs, vchk.totals.ar_ours],
                           ['费用合计（客户往来贷方 / 费目净额）', vchk.totals.fee_theirs, vchk.totals.fee_ours]].map(([lb, t, o]) => {
                    const d = Math.round(((t || 0) - (o || 0)) * 100) / 100
                    return <tr key={lb}><td style={{ whiteSpace: 'normal' }}>{lb}</td>
                      <td className="ec-num">{fmt(t)}</td><td className="ec-num">{fmt(o)}</td>
                      <td className="ec-num" style={{ color: Math.abs(d) > 0.005 ? 'var(--red,#c0392b)' : 'var(--green,#1f7a55)', fontWeight: 600 }}>{fmt(d)}</td></tr>
                  })}</tbody>
                </table>
              </div>
              {(vchk.un_theirs || []).length > 0 && <div style={{ fontSize: 12, marginTop: 8 }}>
                <b style={{ color: 'var(--amber,#a35a00)' }}>{vchk.un_theirs.length} 行凭证有、本页没对上</b>（多为她把几个费目并作一行的合并行）：
                {vchk.un_theirs.map(t => `${fmt(t.amount)}（${t.voucher}）`).join('、')}</div>}
              {(vchk.related || []).length > 0 && <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 6 }}>
                另有 {vchk.related.length} 张摘要相关的调整类凭证（不进核对口径）：
                {vchk.related.map(v => `${v.grp || '记'}-${v.gno}（${v.bill_no} · ${v.status}）`).join('、')}</div>}
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 6 }}>
                逐笔核对结果跟在下方费目行尾；贷应收差异多为真差异/跨期/U先未核销单——逐单核销页按桶点开即知。本核对只读金蝶，不动任何账。</div>
            </>}
          </div>}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {['支付宝', '聚合'].map(z => {
              const rows = (data?.fees || []).filter(f => f.zone === z && (f.income || f.outgo))
              const ok = vchk?.found
              const vMap = {}
              if (ok) (vchk.matched || []).forEach(mm => { vMap[mm.code] = mm.voucher })
              const unOurs = ok ? new Set((vchk.un_ours || []).map(o => o.code)) : new Set()
              const gnoOf = no => { const v = (vchk.vouchers || []).find(x => x.bill_no === no); return v ? `${v.grp || '记'}-${v.gno}` : no }
              return <div key={z} style={{ flex: 1, minWidth: 380 }}>
                <div style={{ fontWeight: 600, fontSize: 12.5, marginBottom: 6 }}>{z === '支付宝' ? '支付宝直连账户' : '天猫聚合结算账户'}</div>
                <div className="ec-tblwrap">
                  <table>
                    <thead><tr><th>费目</th><th>科目</th><th className="ec-num">收入</th><th className="ec-num">支出</th>{ok && <th>凭证核对</th>}</tr></thead>
                    <tbody>{rows.map(f => <tr key={f.id}>
                      <td style={{ whiteSpace: 'normal' }}><span className="ec-mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{f.code}</span> {f.label}</td>
                      <td style={{ color: f.account === '待定' ? 'var(--red,#c0392b)' : undefined, fontWeight: f.account === '待定' ? 700 : 400 }}>{f.account}</td>
                      <td className="ec-num">{f.income ? fmt(f.income) : ''}</td>
                      <td className="ec-num">{f.outgo ? fmt(f.outgo) : ''}</td>
                      {ok && <td style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                        {/* V2.279 一笔笔跟在后面：绿勾=净额与凭证行金额精确对上（两账户合并口径） */}
                        {vMap[f.code] ? <span style={{ color: 'var(--green,#1f7a55)', fontWeight: 600 }}>✓ {gnoOf(vMap[f.code])}</span>
                        : unOurs.has(f.code) ? <span style={{ color: 'var(--amber,#a35a00)' }} title="她的收款单常把几个费目并作一行——费用合计行对上即口径差异，非漏账">并入合并行</span>
                        : String(f.account) === '应收账款' ? <span style={{ color: 'var(--ink-3)' }} title="应收侧随总览「贷1122」总额核对">随1122总额</span>
                        : <span style={{ color: 'var(--ink-3)' }}>—</span>}
                      </td>}
                    </tr>)}</tbody>
                  </table>
                </div>
              </div>
            })}
          </div>
          <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--ink-3)' }}>
            一键录入=两张凭证（借费用/贷其他货币资金 + 借其他货币资金/贷应收账款），<b>只建草稿</b>、提交审核人在金蝶做；同期同店防重录（ec_post_log 台账）。
          </div>
        </div>)}

      {/* ================= ⑤ 通知设置（照物流线惯例：分场景收件人+改动须口令） ================= */}
      {tab === 'notify' && canRun && <div className="ec-card" style={{ maxWidth: 860 }}>
        <div style={{ marginBottom: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>
            跑批完成/失败自动发钉钉与邮件。场景收件人留空＝发 conf.ini 公共名单；改动须<b>通知口令</b>（与汇率/物流线同一把）。</span></div>
        {nMsg && <div style={{ fontSize: 12.5, color: nMsg.includes('✓') ? 'var(--green,#1f7a55)' : 'var(--red,#c0392b)', marginBottom: 8 }}>{nMsg}</div>}
        {!nData ? <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>载入中…</div> : <>
          <div className="ec-tblwrap">
            <table>
              <thead><tr><th>场景</th><th>说明</th><th>钉钉手机号（逗号分隔）</th><th>邮件收件人</th><th style={{ width: 150 }}>操作</th></tr></thead>
              <tbody>{nData.scenes.map((s, i) => <tr key={s.scene}>
                <td style={{ fontWeight: 600 }}>{s.scene}</td>
                <td style={{ whiteSpace: 'normal', fontSize: 11.5, color: 'var(--ink-2)' }}>{s.desc}
                  {s.updated_by && <div style={{ color: 'var(--ink-3)' }}>{s.updated_by} {s.updated_at}</div>}</td>
                <td><input className="ec-inp ec-mono" style={{ width: 180 }} value={s.mobiles}
                  onChange={e => { const n = { ...nData }; n.scenes = nData.scenes.slice(); n.scenes[i] = { ...s, mobiles: e.target.value }; setNData(n) }}
                  placeholder={'空=公共名单 ' + (nData.fallback.mobiles || []).join(',')} disabled={!nData.passcode_set} /></td>
                <td><input className="ec-inp" style={{ width: 190 }} value={s.emails}
                  onChange={e => { const n = { ...nData }; n.scenes = nData.scenes.slice(); n.scenes[i] = { ...s, emails: e.target.value }; setNData(n) }}
                  placeholder={'空=公共名单 ' + (nData.fallback.emails || []).join(',')} disabled={!nData.passcode_set} /></td>
                <td>
                  <button className="btn-sec" style={{ padding: '4px 11px', borderRadius: 7, fontSize: 11.5, cursor: 'pointer' }} disabled={!nData.passcode_set}
                    onClick={async () => { try { setNMsg(''); const r = await ecNotifySave({ scene: s.scene, mobiles: s.mobiles, emails: s.emails, passcode: nPass }); setNMsg(r.ok ? '✓ 已保存' : r.msg) } catch (e) { setNMsg(String(e.message || e)) } }}>保存</button>
                  <button className="btn-sec" style={{ padding: '4px 11px', borderRadius: 7, fontSize: 11.5, cursor: 'pointer', marginLeft: 6 }}
                    onClick={async () => { try { setNMsg('测试发送中…'); const r = await ecNotifyTest({ scene: s.scene }); const v = r.notify || {}; setNMsg('测试回执 → ' + JSON.stringify(v).slice(0, 120)) } catch (e) { setNMsg(String(e.message || e)) } }}>测试</button>
                </td>
              </tr>)}</tbody>
            </table>
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, fontSize: 12.5, color: 'var(--ink-2)', flexWrap: 'wrap' }}>
            通知口令 <input type="password" className="ec-inp" style={{ width: 130 }} value={nPass} onChange={e => setNPass(e.target.value)}
              placeholder={nData.passcode_set ? '保存时校验' : '后端未配置'} disabled={!nData.passcode_set} />
            {!nData.passcode_set && <Tag c="amber">后端未设置通知口令（conf.ini [notify] passcode）——页面锁改，先联系管理员配置</Tag>}
            <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
              公共名单：钉钉{nData.fallback.dingtalk_ready ? '已配置' : '未配置'} · 邮件{nData.fallback.email_ready ? '已配置' : '未配置'}</span>
          </div>
        </>}
      </div>}
    </div>

    {/* ================= 操作详情抽屉（点逐单核销行滑出）================= */}
    {detail && <>
      <div className="ec-overlay" onClick={() => setDetail(null)} />
      <div className="ec-drawer">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <div style={{ fontSize: 14.5, fontWeight: 600 }}>订单 <span className="ec-mono">{detail.order?.order_no}</span></div>
            <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 3 }}>
              {shopDisp(detail.order?.shop || '')} · {period} · {(BUCKET[detail.order?.bucket] || {}).tag}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn-sec" style={{ padding: '4px 12px', borderRadius: 7, fontSize: 12, cursor: 'pointer' }}
              onClick={() => navDetail(-1)} title="同筛选序列内上一笔（页边界自动翻页）">‹ 上一笔</button>
            <button className="btn-sec" style={{ padding: '4px 12px', borderRadius: 7, fontSize: 12, cursor: 'pointer' }}
              onClick={() => navDetail(1)} title="同筛选序列内下一笔（页边界自动翻页）">下一笔 ›</button>
            <button className="btn-sec" style={{ padding: '4px 12px', borderRadius: 7, fontSize: 12, cursor: 'pointer' }} onClick={() => setDetail(null)}>关闭 ✕</button>
          </div>
        </div>
        {detail.loading && <div style={{ marginTop: 14, fontSize: 12.5, color: 'var(--ink-3)' }}>⏳ 正在从金蝶反查蓝红明细与退货链…</div>}
        {detail.error && <div style={{ marginTop: 14, fontSize: 12.5, color: 'var(--red,#c0392b)' }}>{detail.error}</div>}
        {!detail.loading && !detail.error && <>
          {/* V2.272 需求方定：核销结论三行式——账户到账 / 应收单 / 差异，一行一件事 */}
          <div className="ec-dsec"><div className="st">核销结论</div><div className="sb">
            <div style={{ display: 'grid', gridTemplateColumns: '76px 1fr', rowGap: 7, alignItems: 'baseline' }}>
              <span style={{ color: 'var(--ink-3)' }}>账户到账</span>
              <span><b className="ec-mono">{fmt(detail.order.plat_amt)}</b>
                {detail.order.arrive_time && <span style={{ color: 'var(--ink-2)' }}>　{detail.order.arrive_time} · {detail.order.channel || '支付宝'} · 流水号 <span className="ec-mono">{detail.order.serial_no || '—'}</span></span>}</span>
              <span style={{ color: 'var(--ink-3)' }}>应收单</span>
              <span><b className="ec-mono">{fmt(detail.order.ar_amt)}</b>
                {detail.order.ar_no && <span style={{ color: 'var(--ink-2)' }}>　<span className="ec-mono">{detail.order.ar_no}</span></span>}
                {detail.order.rk_amt ? <span style={{ color: 'var(--ink-2)' }}>　退款调节 <b className="ec-mono">{fmt(detail.order.rk_amt)}</b></span> : null}</span>
              <span style={{ color: 'var(--ink-3)' }}>差异</span>
              <span><b className="ec-mono" style={{ color: Math.abs(detail.order.diff) > 0.005 ? 'var(--red,#c0392b)' : 'var(--green,#1f7a55)' }}>{fmt(detail.order.diff)}</b></span>
            </div>
            {detail.order.note && <div style={{ marginTop: 8, color: 'var(--amber,#a35a00)' }}>机器初判：{detail.order.note}</div>}
          </div></div>
          {/* 时间链（V2.268/269）：下单→付款→发货→应收→到账→红冲/退货→核销 业务全链一条线 */}
          {(() => {
            const ev = []
            const biz = detail.biz || {}
            if (biz.created) ev.push({ t: biz.created, c: 'blue', txt: '买家下单（平台订单导出）' })
            if (biz.paid) ev.push({ t: biz.paid, c: 'blue', txt: '买家付款（平台订单导出）' })
            if (biz.shipped) ev.push({ t: biz.shipped, c: 'blue',
              txt: `仓库发货：旺店通 ${biz.ck || ''}${biz.jy ? ' / ' + biz.jy : ''}（③销售出库）` })
            const grp = {}
            ;(detail.ar_rows || []).forEach(r => {
              const key = r.bill + '|' + String(r.date).slice(0, 10) + '|' + (Number(r.amt) < 0 ? 'R' : 'B')
              const g = grp[key] = grp[key] || { bill: r.bill, t: String(r.date).slice(0, 10), neg: Number(r.amt) < 0, amt: 0, n: 0, t3: r.t3, src: r.src }
              g.amt += Number(r.amt); g.n += 1
            })
            Object.values(grp).forEach(g => ev.push({ t: g.t, c: g.neg ? 'red' : '',
              txt: g.neg ? `金蝶红字冲销 ${g.bill} ${fmt(g.amt)}（${g.n} 行 · 源单 ${g.src || '—'}）`
                : `金蝶生成应收 ${g.bill} +${fmt(g.amt)}（${g.n} 行 · 出库/单号 ${g.t3 || '—'}）` }))
            ;(detail.returns || []).forEach(r => ev.push({ t: String(r.date).slice(0, 10), c: 'red',
              txt: `退货入仓 ${r.bill}：${String(r.mat_name).slice(0, 10)}×${r.qty}（退换单 ${r.tk} · 退货挂单号 ${r.orig}）` }))
            const pgrp = {}
            ;(detail.partners || []).forEach(r => {
              const key = r.bill + '|' + String(r.date).slice(0, 10)
              const g = pgrp[key] = pgrp[key] || { bill: r.bill, t: String(r.date).slice(0, 10), amt: 0, t6: r.t6 }
              g.amt += Number(r.amt)
            })
            Object.values(pgrp).forEach(g => ev.push({ t: g.t, c: 'blue',
              txt: `同发货蓝字挂他单：${g.bill} ${fmt(g.amt)}（盖 …${String(g.t6).slice(-10)}）` }))
            if (detail.order.arrive_time) ev.push({ t: detail.order.arrive_time.slice(0, 16), c: 'green',
              txt: `支付宝到账 ${fmt(detail.order.plat_amt)}（${detail.order.channel || '支付宝'} · 流水号 ${detail.order.serial_no || '—'}）` })
            ev.sort((a, b) => (a.t < b.t ? -1 : 1))
            ev.push({ t: '期末', c: Math.abs(detail.order.diff) > 0.005 ? 'red' : 'green',
              txt: `本期核销判定：${(BUCKET[detail.order.bucket] || {}).label || detail.order.bucket} · 差异 ${fmt(detail.order.diff)}` })
            // V2.270 节点缺失说实话（兜底不静默）：后端 biz_gap 区分「文件没传」vs「文件里没这张单」
            const gap = detail.biz_gap || {}
            const isUfirst = detail.order.bucket === 'ufirst'
            const notes = []
            if (!biz.created) {
              if (gap.order === 'no_file') notes.push('买家下单/付款节点缺失：④平台订单还没上传，传了才能显示。')
              else if (gap.order === 'not_in_file') {
                let s = isUfirst
                  ? '买家下单/付款节点缺失：该单号不在本期④平台订单导出里——U先单多为上月末下单、本月初结算，平台导出按下单时间框定，框不到属正常。'
                  : '买家下单/付款节点缺失：该单号不在本期④平台订单导出里（平台导出按下单时间框定，上月下单本月结算的单子框不到）。'
                if (gap.order_prev) s += `本单应收在上月——期间切到 ${gap.order_prev} 补传该月④平台订单，节点即自动补上。`
                notes.push(s)
              }
            }
            if (!biz.shipped) {
              if (isUfirst) notes.push('U先引流单不走旺店通仓，本就没有发货节点。')
              else if (gap.ship === 'no_file') notes.push('仓库发货节点缺失：③销售出库还没上传，传了才能显示。')
              else if (gap.ship === 'not_in_file') {
                let s = '仓库发货节点缺失：该单号不在③销售出库导出里。'
                if (gap.ship_prev) s += `本单上月发货——期间切到 ${gap.ship_prev} 补传该月③销售出库，节点即自动补上。`
                notes.push(s)
              }
            }
            return <div className="ec-dsec"><div className="st">时间链</div><div className="sb">
              <ul className="ec-tl">{ev.map((e, i) => <li key={i} className={e.c}>
                <span className="tt">{e.t}</span>{e.txt}</li>)}</ul>
              {notes.map((n, i) => <div key={i} style={{ marginTop: i ? 3 : 8, fontSize: 11.5, color: 'var(--ink-3)' }}>◌ {n}</div>)}
            </div></div>
          })()}
          <div className="ec-dsec"><div className="st">金蝶应收明细（蓝正红负 · 勾稽字段透出）</div>
            {(detail.ar_rows || []).length === 0 ? <div className="sb" style={{ color: 'var(--ink-3)' }}>本单名下查无应收行（疑串至合并发货的另一单——看下方伙伴蓝字）</div>
              : <div style={{ overflowX: 'auto' }}><table style={{ fontSize: 11.5 }}>
                <thead><tr><th>应收单</th><th>日期</th><th>物料</th><th className="ec-num">数量</th><th className="ec-num">金额</th><th>出库/订单号(T3)</th><th>源单</th></tr></thead>
                <tbody>{detail.ar_rows.map((r, i) => <tr key={i} className={Number(r.amt) < 0 ? 'neg' : ''}>
                  <td className="ec-mono">{r.bill}</td><td className="ec-mono">{String(r.date).slice(0, 10)}</td>
                  <td style={{ whiteSpace: 'normal' }}><span className="ec-mono" style={{ color: 'var(--ink-3)' }}>{r.mat}</span> {String(r.mat_name).slice(0, 12)}</td>
                  <td className="ec-num">{r.qty}</td>
                  <td className="ec-num" style={{ color: Number(r.amt) < 0 ? 'var(--red,#c0392b)' : undefined }}>{fmt(r.amt)}</td>
                  <td className="ec-mono">{r.t3}</td><td className="ec-mono">{r.src}</td>
                </tr>)}</tbody>
              </table></div>}
          </div>
          {(detail.returns || []).length > 0 && <div className="ec-dsec"><div className="st">退货链（红字源单反查）</div>
            <div style={{ overflowX: 'auto' }}><table style={{ fontSize: 11.5 }}>
              <thead><tr><th>销售退货单</th><th>日期</th><th>物料</th><th className="ec-num">实退</th><th>退换单号</th><th>退货挂的原始单号</th></tr></thead>
              <tbody>{detail.returns.map((r, i) => <tr key={i}>
                <td className="ec-mono">{r.bill}</td><td className="ec-mono">{String(r.date).slice(0, 10)}</td>
                <td style={{ whiteSpace: 'normal' }}><span className="ec-mono" style={{ color: 'var(--ink-3)' }}>{r.mat}</span> {String(r.mat_name).slice(0, 12)}</td>
                <td className="ec-num">{r.qty}</td><td className="ec-mono">{r.tk}</td><td className="ec-mono">{r.orig}</td>
              </tr>)}</tbody>
            </table></div>
            <div className="sb" style={{ borderTop: '1px solid var(--line)', color: 'var(--ink-2)', fontSize: 11.5 }}>
              核对退货物料与平台退款明细是否同一商品——退错商品行=红冲金额失真（实物请仓库核实）。</div>
          </div>}
          {(detail.partners || []).length > 0 && <div className="ec-dsec"><div className="st">同发货单下的伙伴蓝字（挂在其他订单名下）</div>
            <div style={{ overflowX: 'auto' }}><table style={{ fontSize: 11.5 }}>
              <thead><tr><th>应收单</th><th>日期</th><th>物料</th><th className="ec-num">金额</th><th>盖的原始单号(T6)</th></tr></thead>
              <tbody>{detail.partners.map((r, i) => <tr key={i} className="partner">
                <td className="ec-mono">{r.bill}</td><td className="ec-mono">{String(r.date).slice(0, 10)}</td>
                <td style={{ whiteSpace: 'normal' }}><span className="ec-mono" style={{ color: 'var(--ink-3)' }}>{r.mat}</span> {String(r.mat_name).slice(0, 12)}</td>
                <td className="ec-num">{fmt(r.amt)}</td><td className="ec-mono" style={{ whiteSpace: 'normal', wordBreak: 'break-all' }}>{r.t6}</td>
              </tr>)}</tbody>
            </table></div>
            <div className="sb" style={{ borderTop: '1px solid var(--line)', color: 'var(--ink-2)', fontSize: 11.5 }}>
              合并发货时应收单可能整单盖了另一订单的号（串单根源）——本单的货若出现在这里，改单号后差异自平。</div>
          </div>}
        </>}
      </div>
    </>}
  </div>
}
