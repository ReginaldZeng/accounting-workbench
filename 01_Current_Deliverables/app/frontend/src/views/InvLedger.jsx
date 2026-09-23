// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）| 发票台账五页签：台账查询（筛选/分页/合计/导出/右侧详情/作废）、税局对账（清单四色+文件包补全）、抵扣勾选、新销方核查、期初导入
// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 「含作废」只多列作废票（review=withvoid＝已通过＋已作废，不再把草稿/待审票拉进台账和合计）；
//   作废后把后端的税务提醒（进项税额转出/撤销勾选）原话留在页面上；详情「留痕」按 log.itemId 列这张票自己的记录（新的在前）。
// 接口契约见 docs/20260923_发票管家/发票管家_技术方案 §5.2「发票台账」。后端与本页同时开发：凡契约没写死的字段一律可选链+默认值兜底。
// 权限：按钮显隐看 invConfig().can.*（deduct/unbind/opening/audit），真正的闸在后端；被拒时把后端原因贴在按钮旁边。
import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  invConfig, invLedger, invLedgerExportUrl, invItemVoid, invFolder,
  invTaxlistImport, invTaxlistReport, invTaxpackImport,
  invDeductPrepare, invDeductDownloadUrl,
  invSellers, invSellerCheck,
  invOpeningPreview, invOpeningCommit, invOpeningStats,
} from '../api.js'
import { Modal, InvViewer, FieldPanel, StatusBadges, FileDrop, useToast, money, fmtTime } from './invShared.jsx'
import './inv-ledger.css'

// ───────────────────────── 小工具 ─────────────────────────

const TAB_KEY = 'inv_ledger_tab'
const TABS = [
  ['query', '台账查询'], ['taxlist', '税局对账'], ['deduct', '抵扣勾选'], ['sellers', '新销方核查'], ['opening', '期初导入'],
]
// 上次停在哪个页签：只是个人便利，存不了（隐私模式等）就回第一页
const tabGet = () => { try { const v = localStorage.getItem(TAB_KEY); return TABS.some(t => t[0] === v) ? v : 'query' } catch { return 'query' } }
const tabSet = v => { try { localStorage.setItem(TAB_KEY, v) } catch { /* 存不了就算了 */ } }

const INV_TYPE_LABEL = {
  special: '增值税专用发票', normal: '增值税普通发票', travel: '旅客运输服务', toll: '通行费发票',
  train: '铁路电子客票', flight: '航空电子客票行程单', vehicle: '机动车销售统一发票',
  quota: '定额发票', taxi: '出租车票', general: '通用机打发票', other: '其他',
}
const KIND_LABEL = { receipt: '收据', other: '非发票附件' }
const VERIFY_OPTS = [['', '全部'], ['green', '已验真'], ['red', '作废红冲'], ['yellow', '税局未见'], ['gray', '不适用'], ['unset', '未对账']]
const DEDUCT_OPTS = [['', '全部'], ['yes', '可抵扣'], ['no', '不可抵扣'], ['unset', '未判定'], ['marked', '已标注勾选']]
const VERIFY_TONE = { green: 'ok', red: 'err', yellow: 'warn', gray: 'mute' }
const VERIFY_TEXT = { green: '已验真', red: '作废红冲', yellow: '税局未见', gray: '不适用' }
const PAGE_SIZE = 50
const LIST_CAP = 100   // 税局对账的三张表每张最多先列 100 行，免得几千行把页面撑爆

const errText = e => (e && (e.message || String(e))) || '操作失败，原因未知'
const pad2 = n => String(n).padStart(2, '0')
// 默认日期取本机当天：只是表单默认值，人可以改，不参与任何计算
const todayStr = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` }
const monthStr = () => todayStr().slice(0, 7)
const first = (...vs) => { for (const v of vs) if (v !== undefined && v !== null && v !== '') return v; return '' }
const typeText = it => first(it?.typeLabel, INV_TYPE_LABEL[it?.invType], KIND_LABEL[it?.kind], it?.invType) || '—'
// 表格里用简称（全称太长会把右边几列挤出去），全称放悬停提示
const TYPE_SHORT = {
  special: '专票', normal: '普票', travel: '旅客运输', toll: '通行费', train: '火车票', flight: '机票行程单',
  vehicle: '机动车', quota: '定额', taxi: '出租车', general: '通用机打', other: '其他',
}
const typeShort = it => (it?.kind && it.kind !== 'invoice' ? KIND_LABEL[it.kind] : TYPE_SHORT[it?.invType]) || typeText(it)
const fBiz = f => first(f?.businessId, f?.business_id)
function deductText(it) {
  if (it?.deductStatus === 'checked') return '已勾选'
  if (it?.deductStatus === 'marked') return '已标注勾选'
  if (it?.deductible === true || it?.deductible === 1) return '可抵扣'
  if (it?.deductible === false || it?.deductible === 0) return '不可抵扣'
  return '未判定'
}
// 后端的「识别到的列」可能给字符串数组（'发票号码 ← 数电票号码'），也可能给 {字段: 表头}
function mappingLines(m) {
  if (!m) return []
  if (Array.isArray(m)) return m.map(x => (typeof x === 'string' ? x : `${x?.field || x?.label || ''} ← ${x?.header || ''}`))
  if (typeof m === 'object') return Object.entries(m).filter(([, v]) => v).map(([k, v]) => `${k} ← ${v}`)
  return []
}
const asList = v => (Array.isArray(v) ? v : [])
const n0 = v => (Number.isFinite(Number(v)) ? Number(v) : 0)

function VerifyBadge({ v, note }) {
  if (!v || !VERIFY_TONE[v]) return <span className="inv-badge mute" title="还没拿税局清单对过">未对账</span>
  return <span className={'inv-badge ' + VERIFY_TONE[v]} title={note || undefined}>{VERIFY_TEXT[v]}</span>
}

function ErrLine({ msg }) {
  return msg ? <div className="inv-lg-err" role="alert">{msg}</div> : null
}

function NoPerm({ what }) {
  return <div className="inv-lg-note">{what}需要单独的操作权限，您目前只能查看。如需开通，请找系统管理员。</div>
}

// ───────────────────────── 页签 1：台账查询 ─────────────────────────

const EMPTY_FILTER = { from: '', to: '', q: '', invType: '', verify: '', deduct: '', seller: '', withVoid: false }
// 台账只放审核通过的票；勾「含作废」＝已通过＋已作废（withvoid）。后端的 'all'（连草稿/待审都算）只给内部用，这页不发。
export const toParams = f => ({
  from: f.from, to: f.to, q: f.q.trim(), invType: f.invType, verify: f.verify, deduct: f.deduct,
  seller: f.seller.trim(), review: f.withVoid ? 'withvoid' : '',
})
// 作废提示里带着税务动作（进项税额转出 / 撤销勾选）时要留在页面上，不能 2 秒就消失
export const voidNeedsTaxAction = msg => /转出|撤销/.test(String(msg || ''))
/**
 * 这张票自己的留痕：后端 log 视图把票 id 放在顶层 itemId（C7），新的在前，取前 n 条。
 * @param {object[]} logs 票夹留痕（invFolder().logs）
 * @param {number} itemId
 * @param {number} [n=8]
 */
export function itemLogs(logs, itemId, n = 8) {
  return asList(logs).filter(l => l && l.itemId !== undefined && l.itemId !== null && l.itemId === itemId).slice(0, n)
}
// 留痕一行里只带人话的那一句（原因/说明），不把内部字段名摊给会计看
function logBrief(d) {
  if (d === null || d === undefined || d === '') return ''
  if (typeof d === 'string') return d.slice(0, 80)
  if (typeof d !== 'object') return String(d)
  const t = first(d.note, d.msg, d.reason)
  return t ? String(t).slice(0, 80) : ''
}

function FilterBar({ f, setF, exportUrl, total }) {
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))
  const dirty = Object.keys(EMPTY_FILTER).some(k => f[k] !== EMPTY_FILTER[k])
  return (
    <div className="fbar inv-lg-fbar">
      <label>开票日期
        <input type="date" value={f.from} onChange={e => set('from', e.target.value)} aria-label="开票日期从" />
        <span className="muted">至</span>
        <input type="date" value={f.to} onChange={e => set('to', e.target.value)} aria-label="开票日期到" />
      </label>
      <label>搜索<input type="text" value={f.q} onChange={e => set('q', e.target.value)} placeholder="发票号码 / 审批单号 / 申请人" /></label>
      <label>销方<input type="text" value={f.seller} onChange={e => set('seller', e.target.value)} placeholder="名称或税号" className="inv-lg-short" /></label>
      <label>票种
        <select value={f.invType} onChange={e => set('invType', e.target.value)}>
          <option value="">全部</option>
          {Object.entries(INV_TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label>验真
        <select value={f.verify} onChange={e => set('verify', e.target.value)}>
          {VERIFY_OPTS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label>抵扣
        <select value={f.deduct} onChange={e => set('deduct', e.target.value)}>
          {DEDUCT_OPTS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label className="inv-lg-ck"><input type="checkbox" checked={f.withVoid} onChange={e => set('withVoid', e.target.checked)} />含作废</label>
      {dirty && <button type="button" className="btn" onClick={() => setF(EMPTY_FILTER)}>清空条件</button>}
      <span className="inv-lg-grow" />
      <a className="btn" href={exportUrl} title={total ? `按当前条件导出 ${total} 张` : '按当前条件导出'}>导出 Excel</a>
    </div>
  )
}

function LedgerTable({ rows, sum, total, selId, onPick, withVoid }) {
  return (
    <div className="tbl-wrap">
      <table className="inv-lg-tbl">
        <thead><tr>
          <th>开票日期</th><th>票种</th><th>发票号码</th><th>销方</th>
          <th className="num">金额</th><th className="num">税额</th><th className="num">价税合计</th>
          <th>验真</th><th>抵扣</th><th>审批单</th><th>登记 / 审核</th>
        </tr></thead>
        <tbody>
          {rows.map(r => {
            const f = r.folder || {}
            const isVoid = r.review === 'void'
            return (
              <tr key={r.id} className={'row' + (r.id === selId ? ' on' : '') + (isVoid ? ' void' : '')} tabIndex={0}
                onClick={() => onPick(r)} onKeyDown={e => { if (e.key === 'Enter') onPick(r) }}>
                <td className="mono">{r.date || '—'}</td>
                <td className="inv-lg-type" title={typeText(r)}>{typeShort(r)}{isVoid && <span className="inv-badge err inv-lg-ml">已作废</span>}</td>
                <td className="mono">{r.number || '—'}{r.split ? <span className="inv-lg-sub">分摊 {money(r.alloc)}</span> : null}</td>
                <td className="inv-lg-seller" title={r.sellerTaxId || undefined}>{r.sellerName || '—'}</td>
                <td className="num">{money(r.amount)}</td>
                <td className="num">{money(r.tax)}</td>
                <td className="num">{money(r.total)}</td>
                <td><VerifyBadge v={r.verify} note={r.verifyNote} /></td>
                <td className="inv-lg-nowrap">{deductText(r)}</td>
                <td className="inv-lg-nowrap">{fBiz(f) ? <span className="mono">{fBiz(f)}</span> : <span>{f.title || '—'}</span>}
                  {f.applicant ? <span className="inv-lg-sub">{f.applicant}</span> : null}</td>
                <td className="inv-lg-nowrap">{r.createdBy || '—'}<span className="inv-lg-sub">{r.reviewBy ? '审 ' + r.reviewBy : '未审'}</span></td>
              </tr>
            )
          })}
        </tbody>
        {rows.length > 0 && (
          <tfoot><tr className="inv-lg-sum">
            <td colSpan={4}>合计（当前条件共 {total} 张{withVoid ? '；作废的票只列出来，不计入合计' : '，不含作废'}）</td>
            <td className="num">{money(sum?.amount)}</td>
            <td className="num">{money(sum?.tax)}</td>
            <td className="num">{money(sum?.total)}</td>
            <td colSpan={4} />
          </tr></tfoot>
        )}
      </table>
    </div>
  )
}

function Pager({ page, total, onPage, busy }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  if (total <= PAGE_SIZE) return null
  return (
    <div className="inv-lg-pager">
      <button type="button" className="btn" disabled={busy || page <= 1} onClick={() => onPage(page - 1)}>上一页</button>
      <span>第 {page} / {pages} 页，共 {total} 张</span>
      <button type="button" className="btn" disabled={busy || page >= pages} onClick={() => onPage(page + 1)}>下一页</button>
    </div>
  )
}

function VoidModal({ item, onClose, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const go = async () => {
    if (!note.trim()) { setErr('请写明作废原因，会留在这张票的记录里'); return }
    setBusy(true); setErr('')
    // 整包交回去：msg 里可能带「记得在税局做进项税额转出 / 撤销勾选」，页面要原话留给会计
    try { const r = await invItemVoid(item.id, note.trim()); onDone(r || {}) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Modal title="作废这张票" onClose={onClose} width={520}
      footer={<>
        <button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button>
        <button type="button" className="btn primary inv-lg-danger" onClick={go} disabled={busy}>{busy ? '正在作废…' : '确认作废'}</button>
      </>}>
      <div className="inv-lg-form">
        <div className="inv-lg-note">
          发票号码 <b className="mono">{item.number || '—'}</b>，价税合计 <b>{money(item.total)}</b>。
          作废后这张票不再计入台账合计，查重时也不再拦别的单；记录保留，勾上「含作废」还能查到。
        </div>
        <label className="inv-lg-fld">作废原因（必填）
          <textarea className="inv-lg-ta" rows={3} value={note} autoFocus maxLength={500}
            onChange={e => { setNote(e.target.value); setErr('') }} placeholder="例：重复登记 / 供应商已红冲重开 / 挂错了审批单" />
        </label>
        <ErrLine msg={err} />
      </div>
    </Modal>
  )
}

function DetailPanel({ row, can, onClose, onVoided }) {
  const [view, setView] = useState(false)
  const [field, setField] = useState('')
  const [voiding, setVoiding] = useState(false)
  const [fd, setFd] = useState(null)       // 挂靠审批单的完整信息（invFolder），拿不到就只用行里带的简版
  const [fdErr, setFdErr] = useState('')
  const fid = row?.folder?.id || row?.folderId
  const boxRef = useRef(null)
  useEffect(() => {
    setField(''); setView(false)
    // 窄屏时详情排在表格下面，点了行却看不到变化——滚过去让人看见
    const el = boxRef.current
    if (el && el.getBoundingClientRect().top > window.innerHeight - 80) el.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [row?.id])
  useEffect(() => {
    let live = true
    setFd(null); setFdErr('')
    if (fid) invFolder(fid).then(r => { if (live) setFd(r) }).catch(e => { if (live) setFdErr(errText(e)) })
    return () => { live = false }
  }, [fid])
  if (!row) return null
  const f = { ...(row.folder || {}), ...(fd?.folder || {}) }
  const file = row.file || null
  const isVoid = row.review === 'void'
  // 这张票自己的操作记录：按 log.itemId 过滤（票夹级日志不往这里塞），新的在前
  const logs = itemLogs(fd?.logs, row.id, 8)
  return (
    <aside className="inv-lg-side" aria-label="票据详情" ref={boxRef}>
      <div className="inv-lg-side-h">
        <div className="inv-lg-side-t">{typeText(row)} <span className="mono">{row.number || ''}</span></div>
        <button type="button" className="inv-x" onClick={onClose} aria-label="关闭详情">×</button>
      </div>
      <StatusBadges item={row} />
      <button type="button" className="inv-lg-thumb" onClick={() => setView(true)} title="点开放大看">
        {file?.thumb ? <img src={file.thumb} alt="票面缩略图" /> : <span className="inv-muted">只有二维码信息，暂无图片</span>}
      </button>
      <FieldPanel item={row} activeField={field} onFieldFocus={k => { setField(k); if (file) setView(true) }} />

      <div className="inv-lg-box">
        <div className="inv-lg-box-h">挂靠审批单</div>
        {fid ? <>
          <div className="inv-lg-kv"><span>审批单号</span><b className="mono">{fBiz(f) || '（手工票夹）'}</b></div>
          {f.title && <div className="inv-lg-kv"><span>标题</span><b>{f.title}</b></div>}
          {f.template && <div className="inv-lg-kv"><span>类型</span><b>{f.template}</b></div>}
          <div className="inv-lg-kv"><span>申请人</span><b>{first(f.applicant, '—')}{f.dept ? ' · ' + f.dept : ''}</b></div>
          {first(f.payee?.name, f.payee_name) && <div className="inv-lg-kv"><span>收款方</span><b>{first(f.payee?.name, f.payee_name)}</b></div>}
          {f.amount !== undefined && f.amount !== null && <div className="inv-lg-kv"><span>单据金额</span><b className="inv-num">{money(f.amount)}</b></div>}
          {can.audit && <a className="inv-lk" href={`#/invaudit?folder=${encodeURIComponent(fid)}`} target="_blank" rel="noopener">在发票审核里打开这张单 ↗</a>}
          {fdErr && <div className="inv-muted">审批单详情没读到：{fdErr}</div>}
        </> : <div className="inv-muted">这张票没有挂在审批单上</div>}
      </div>

      <div className="inv-lg-box">
        <div className="inv-lg-box-h">留痕</div>
        <div className="inv-lg-kv"><span>登记</span><b>{first(row.createdBy, '—')} · {fmtTime(row.createdAt)}</b></div>
        <div className="inv-lg-kv"><span>审核</span><b>{row.reviewBy ? `${row.reviewBy} · ${fmtTime(row.reviewAt)}` : '未审核'}{row.selfReview ? '（自审）' : ''}</b></div>
        {row.reviewNote && <div className="inv-lg-kv"><span>{isVoid ? '作废原因' : '审核备注'}</span><b>{row.reviewNote}</b></div>}
        <div className="inv-lg-kv"><span>抵扣</span><b>{deductText(row)}{row.deductReason ? '（' + row.deductReason + '）' : ''}</b></div>
        {row.verifyNote && <div className="inv-lg-kv"><span>验真说明</span><b>{row.verifyNote}</b></div>}
        {logs.map((l, i) => <div key={l.id ?? i} className="inv-lg-log">{fmtTime(l.ts)} {l.user || '系统'} {l.action || ''}
          {logBrief(l.detail) ? <span className="inv-muted">：{logBrief(l.detail)}</span> : null}</div>)}
        {fid && fd && !logs.length && <div className="inv-muted">这张票还没有单独的操作记录</div>}
      </div>

      {can.unbind && !isVoid && (
        <div className="inv-lg-side-act">
          <button type="button" className="btn inv-lg-danger-ghost" onClick={() => setVoiding(true)}>作废这张票</button>
        </div>
      )}

      {view && (
        <Modal title={`${typeText(row)} ${row.number || ''}`} onClose={() => setView(false)} width={1040}>
          <InvViewer item={row} activeField={field} height={620} />
        </Modal>
      )}
      {voiding && <VoidModal item={row} onClose={() => setVoiding(false)}
        onDone={r => { setVoiding(false); onVoided(r) }} />}
    </aside>
  )
}

function QueryTab({ can, flash }) {
  const [f, setF] = useState(EMPTY_FILTER)
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)      // {total, sum, rows}
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState(null)
  const [voidNote, setVoidNote] = useState('')  // 作废后要去税局做的动作（转出/撤销勾选）：常驻，人点了才收起
  const seq = useRef(0)                         // 只认最后一次请求的结果（连打筛选时旧请求晚到会覆盖新结果）
  const params = toParams(f)
  const key = JSON.stringify(params)

  const load = useCallback(async (p) => {
    const my = ++seq.current
    setBusy(true); setErr('')
    try {
      const r = await invLedger({ ...JSON.parse(key), page: p, size: PAGE_SIZE })
      if (my !== seq.current) return
      setData({ total: n0(r?.total), sum: r?.sum || {}, rows: asList(r?.rows) })
    } catch (e) {
      if (my === seq.current) setErr(errText(e))
    } finally {
      if (my === seq.current) setBusy(false)
    }
  }, [key])

  // 条件一变回到第 1 页；文字框打字时稍等一下再查，免得每敲一个字查一次
  useEffect(() => {
    const t = setTimeout(() => { setPage(1); load(1) }, 350)
    return () => clearTimeout(t)
  }, [load])

  const goPage = p => { setPage(p); load(p) }
  const onVoided = r => {
    const msg = (r && r.msg) || '已作废'
    const tax = voidNeedsTaxAction(msg)
    flash(msg, tax ? 'err' : 'ok')
    setVoidNote(tax ? msg : '')
    const it = r && r.item
    if (it) setSel(s => (s && s.id === it.id ? { ...s, ...it, folder: s.folder } : s))
    load(page)
  }
  const rows = data?.rows || []
  const exportUrl = invLedgerExportUrl(params)

  return (
    <>
      <FilterBar f={f} setF={setF} exportUrl={exportUrl} total={data?.total} />
      {voidNote && <div className="inv-lg-warn inv-lg-voidnote" role="alert">
        <span>{voidNote}</span>
        <button type="button" className="inv-x" onClick={() => setVoidNote('')} aria-label="知道了" title="知道了">×</button>
      </div>}
      <ErrLine msg={err && '台账没读出来：' + err} />
      <div className={'inv-lg-main' + (sel ? ' has-side' : '')}>
        <div className="inv-lg-list">
          {!data && busy && <div className="loading">正在读取台账…</div>}
          {data && rows.length === 0 && !busy && (
            <div className="inv-lg-empty">没有符合条件的票。只有审核通过的票才会进台账{f.withVoid ? '，换个条件再试试。' : '；要看作废的票，勾上「含作废」。'}</div>
          )}
          {rows.length > 0 && <LedgerTable rows={rows} sum={data.sum} total={data.total} selId={sel?.id} onPick={setSel} withVoid={f.withVoid} />}
          {data && <Pager page={page} total={data.total} onPage={goPage} busy={busy} />}
          {busy && data && <div className="inv-muted inv-lg-busy"><span className="inv-spin" /> 正在刷新…</div>}
        </div>
        {sel && <DetailPanel row={sel} can={can} onClose={() => setSel(null)} onVoided={onVoided} />}
      </div>
    </>
  )
}

// ───────────────────────── 页签 2：税局对账 ─────────────────────────

const STAT_DEFS = [
  ['green', '🟢 已验真', '税局清单里有，状态正常'],
  ['red', '🔴 作废红冲', '清单里有，但已作废或红冲'],
  ['yellow', '🟡 税局未见', '清单里没有：可能还没上传、抬头税号填错，或假票，要逐张核'],
  ['gray', '⚪ 不适用', '清单本来覆盖不到（定额、出租车、个人抬头等）'],
]

function StatCards({ counts }) {
  return (
    <div className="inv-lg-stats">
      {STAT_DEFS.map(([k, label, tip]) => (
        <div key={k} className={'inv-lg-stat ' + k}>
          <div className="inv-lg-stat-l">{label}</div>
          <div className="inv-lg-stat-v">{n0(counts?.[k])}<span> 张</span></div>
          <div className="inv-lg-stat-d">{tip}</div>
        </div>
      ))}
    </div>
  )
}

// 三张表的行可能是税局清单行，也可能是台账 Item：字段名都兜一下
const rDate = r => first(r?.date, r?.issueDate, r?.issue_date) || '—'
const rSeller = r => first(r?.sellerName, r?.seller_name) || '—'
const rNum = r => first(r?.number, r?.no) || '—'

function MiniTable({ title, hint, rows, extra, empty }) {
  const shown = rows.slice(0, LIST_CAP)
  return (
    <details className="inv-lg-det" open={rows.length > 0}>
      <summary><b>{title}</b> <span className="inv-lg-cnt">{rows.length} 张</span>{hint && <span className="inv-muted"> · {hint}</span>}</summary>
      {rows.length === 0 ? <div className="inv-lg-empty sm">{empty}</div> : (
        <div className="tbl-wrap inv-lg-mini">
          <table>
            <thead><tr><th>发票号码</th><th>开票日期</th><th>销方</th><th className="num">价税合计</th><th>{extra.head}</th></tr></thead>
            <tbody>{shown.map((r, i) => (
              <tr key={(r?.id || rNum(r)) + '-' + i}>
                <td className="mono">{rNum(r)}</td><td className="mono">{rDate(r)}</td><td>{rSeller(r)}</td>
                <td className="num">{money(r?.total)}</td><td>{extra.cell(r)}</td>
              </tr>
            ))}</tbody>
          </table>
          {rows.length > LIST_CAP && <div className="inv-muted inv-lg-pad">只列出前 {LIST_CAP} 张，其余 {rows.length - LIST_CAP} 张请到电子税务局或导出台账里看。</div>}
        </div>
      )}
    </details>
  )
}

function hintText(h) {
  if (!h) return ''
  if (typeof h === 'string') return h
  const who = first(h.payee, h.payeeName, h.payee_name, h.applicant, h.businessId)
  return who ? `可能是后补池里「${who}」这笔` : '可能是后补池里的一笔'
}

function ReportView({ report, batch }) {
  const hints = asList(report?.laterHints)
  const hintOf = r => hints.find(h => h && typeof h === 'object' && h.number && h.number === rNum(r))
  const orphanHints = hints.filter(h => !(h && typeof h === 'object' && h.number))
  const lines = mappingLines(report?.mappingLines || report?.mapping)
  const warns = asList(report?.warnings)
  const b = batch || report?.batch
  return (
    <div className="inv-lg-stack">
      {b && <div className="inv-muted">对账依据：{first(b.name, '税局清单')}{b.rows ? `，共 ${b.rows} 行` : ''}{first(b.createdAt, b.created_at) ? `，${fmtTime(first(b.createdAt, b.created_at))} 导入` : ''}{first(b.createdBy, b.created_by) ? `（${first(b.createdBy, b.created_by)}）` : ''}</div>}
      <StatCards counts={report?.counts} />
      {(lines.length > 0 || warns.length > 0) && (
        <div className="inv-lg-box">
          {lines.length > 0 && <><div className="inv-lg-box-h">识别到的列（台账字段 ← 清单表头）</div>
            <div className="inv-lg-map">{lines.map((l, i) => <span key={i}>{l}</span>)}</div></>}
          {warns.map((w, i) => <div key={i} className="inv-lg-warn">{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
        </div>
      )}
      <MiniTable title="税局有、台账没有" hint="票开给我们了，但还没登记进来" rows={asList(report?.notInLedger)}
        empty="清单里的票台账都有了。"
        extra={{ head: '线索', cell: r => { const h = hintOf(r); return h ? <span className="inv-lg-hint">{hintText(h)}</span> : <span className="inv-muted">{first(r?.status, '')}</span> } }} />
      {orphanHints.length > 0 && (
        <div className="inv-lg-box">
          <div className="inv-lg-box-h">后补池里可能对得上的单</div>
          {orphanHints.map((h, i) => <div key={i} className="inv-lg-hint">{hintText(h)}</div>)}
        </div>
      )}
      <MiniTable title="台账有、税局没有" hint="要逐张核：还没上传、抬头税号填错，或假票" rows={asList(report?.notInList)}
        empty="台账里的票在清单里都找到了。"
        extra={{ head: '审批单', cell: r => <span>{first(fBiz(r?.folder), r?.businessId)} {first(r?.folder?.applicant, r?.applicant)}</span> }} />
      <MiniTable title="作废红冲" hint="清单显示已作废或红冲，别再付款或抵扣" rows={asList(report?.red)}
        empty="没有作废或红冲的票。"
        extra={{ head: '清单状态', cell: r => <span className="inv-lg-red">{first(r?.status, r?.verifyNote, '作废/红冲')}</span> }} />
    </div>
  )
}

function TaxpackBox({ can }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState(null)
  const go = async files => {
    setBusy(true); setErr(''); setRes(null)
    try { setRes(await invTaxpackImport(files)) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const filled = asList(res?.filled), un = asList(res?.unmatched), errs = asList(res?.errors)
  return (
    <section className="inv-lg-sec">
      <div className="inv-lg-sec-h">用税局文件包补全明细和原件</div>
      <div className="inv-muted">只有二维码、没有电子原件的票，可以在电子税务局批量下载发票文件后拖进来，系统按发票号码对上台账里的票，补上票面明细和原件。</div>
      {can.deduct ? (
        <FileDrop onFiles={go} accept=".pdf,.ofd,.xml,.zip,.rar,.7z" disabled={busy}>
          <span className="inv-drop-tx">{busy ? <><span className="inv-spin" /> 正在处理文件包…</> : <>拖入税局批量下载的发票文件包（PDF/OFD/XML/压缩包），或<b>点击选择</b></>}</span>
        </FileDrop>
      ) : <NoPerm what="导入税局文件包" />}
      <ErrLine msg={err && '文件包没导进去：' + err} />
      {res && (
        <div className="inv-lg-stack sm">
          <div className="inv-lg-ok">补全了 {filled.length} 张票的明细和原件。</div>
          {un.length > 0 && <div className="inv-lg-box">
            <div className="inv-lg-box-h">台账里没找到对应的票（{un.length} 个文件）</div>
            {un.slice(0, LIST_CAP).map((u, i) => <div key={i} className="inv-lg-li"><span className="mono">{first(u?.number, '号码未读出')}</span> <span className="inv-muted">{u?.name || ''}</span></div>)}
            <div className="inv-muted">这些票可能还没登记，或登记时号码有误；先在收票工作台登记后再导一次即可。</div>
          </div>}
          {errs.length > 0 && <div className="inv-lg-box">
            <div className="inv-lg-box-h">处理失败的文件（{errs.length} 个）</div>
            {errs.map((x, i) => <div key={i} className="inv-lg-warn">{typeof x === 'string' ? x : `${first(x?.name, '')} ${first(x?.msg, x?.error, '')}`}</div>)}
          </div>}
        </div>
      )}
    </section>
  )
}

function TaxlistTab({ can, flash }) {
  const [rep, setRep] = useState(null)      // {report, batch}
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    let live = true
    invTaxlistReport().then(r => { if (live) setRep({ report: r?.report || null, batch: r?.batch || null }) })
      .catch(e => { if (live) setLoadErr(errText(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])
  const go = async files => {
    if (!files[0]) return
    setBusy(true); setErr('')
    try {
      const r = await invTaxlistImport(files[0])
      setRep({ report: r?.report || null, batch: r?.batch || null })
      flash(`税局清单已导入${r?.batch?.rows ? `（${r.batch.rows} 行）` : ''}，四色结果已更新`, 'ok')
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="inv-lg-stack">
      <section className="inv-lg-sec">
        <div className="inv-lg-sec-h">拿税局清单给台账验真</div>
        <ol className="inv-lg-steps">
          <li>登录电子税务局，依次点 <b>税务数字账户 › 发票查询统计 › 全量发票查询</b>；</li>
          <li>选 <b>取得发票</b>，开票日期选要核的期间，点 <b>导出</b>，得到一个 Excel；</li>
          <li>把这个 Excel 原样拖到下面，系统逐张标四色，并列出两边对不上的票。</li>
        </ol>
        {can.deduct ? (
          <FileDrop onFiles={go} accept=".xlsx,.xls,.csv" multiple={false} disabled={busy}>
            <span className="inv-drop-tx">{busy ? <><span className="inv-spin" /> 正在逐张比对…</> : <>把税局导出的「取得发票」清单拖到这里，或<b>点击选择</b></>}</span>
          </FileDrop>
        ) : <NoPerm what="导入税局清单" />}
        <ErrLine msg={err && '清单没导进去：' + err} />
      </section>
      {loading && <div className="loading">正在读取上次的对账结果…</div>}
      {!loading && loadErr && <ErrLine msg={'上次的对账结果没读出来：' + loadErr} />}
      {!loading && !loadErr && !rep?.report && <div className="inv-lg-empty">还没导过税局清单。导一次之后，这里会显示最近一次的对账结果。</div>}
      {rep?.report && <ReportView report={rep.report} batch={rep.batch} />}
      <TaxpackBox can={can} />
    </div>
  )
}

// ───────────────────────── 页签 3：抵扣勾选 ─────────────────────────

function DeductTab({ can }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [res, setRes] = useState(null)
  const [fname, setFname] = useState('')
  const [ok, setOk] = useState(false)
  const go = async files => {
    if (!files[0]) return
    setBusy(true); setErr(''); setRes(null); setOk(false); setFname(files[0].name || '')
    try { setRes(await invDeductPrepare(files[0])) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const s = res?.summary || {}
  const prev = asList(res?.preview).slice(0, 200)
  const warns = asList(res?.warnings)
  const decTone = d => (d === '是' || d === 'yes' || d === true ? 'ok' : d === '否' || d === 'no' || d === false ? 'mute' : 'warn')
  const decText = d => (d === true || d === 'yes' ? '是' : d === false || d === 'no' ? '否' : (d || '未判定'))
  return (
    <div className="inv-lg-stack">
      <section className="inv-lg-sec">
        <div className="inv-lg-sec-h">抵扣勾选：系统标注，人来确认和提交</div>
        <ol className="inv-lg-steps">
          <li>电子税务局 <b>抵扣类勾选</b> 里导出<b>未勾选清单</b>；</li>
          <li>把导出的文件原样拖进来，系统逐张标「是否勾选」：<b>审核通过＋可抵扣＋已验真</b> 的标「是」，其余标「否」或留空；</li>
          <li>对着下面的预览核一遍（会计确认）；</li>
          <li>下载已标注的文件，格式和原文件一样；</li>
          <li>回电子税务局用 <b>清单导入勾选</b> 导入这个文件；</li>
          <li><b>提交勾选</b> 和 <b>统计确认</b> 仍由人在税局里点，系统不替你提交。</li>
        </ol>
        {can.deduct ? (
          <FileDrop onFiles={go} accept=".xlsx,.xls,.csv" multiple={false} disabled={busy}>
            <span className="inv-drop-tx">{busy ? <><span className="inv-spin" /> 正在逐张标注…</> : <>把税局导出的未勾选清单拖到这里，或<b>点击选择</b></>}</span>
          </FileDrop>
        ) : <NoPerm what="抵扣勾选" />}
        <ErrLine msg={err && '清单没标注成功：' + err} />
      </section>
      {res && (
        <section className="inv-lg-sec">
          <div className="inv-lg-sec-h">标注结果{fname ? `：${fname}` : ''}</div>
          <div className="inv-lg-chips">
            <span className="inv-lg-chip">共 <b>{n0(s.rows)}</b> 张</span>
            <span className="inv-lg-chip ok">标「是」<b>{n0(s.yes)}</b></span>
            <span className="inv-lg-chip">标「否」<b>{n0(s.no)}</b></span>
            <span className="inv-lg-chip warn">未判定 <b>{n0(s.unknown)}</b></span>
            <span className="inv-lg-chip err">台账里没有 <b>{n0(s.notInLedger)}</b></span>
          </div>
          {n0(s.notInLedger) > 0 && <div className="inv-lg-warn">有 {n0(s.notInLedger)} 张票台账里没有：说明还没登记或还没审核，这些票系统不会标「是」。先去收票、审核，再重新拖一次。</div>}
          {warns.map((w, i) => <div key={i} className="inv-lg-warn">{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
          {prev.length > 0 && (
            <div className="tbl-wrap inv-lg-mini">
              <table>
                <thead><tr><th>发票号码</th><th>是否勾选</th><th>原因</th></tr></thead>
                <tbody>{prev.map((p, i) => (
                  <tr key={i}><td className="mono">{p?.number || '—'}</td>
                    <td><span className={'inv-badge ' + decTone(p?.decision)}>{decText(p?.decision)}</span></td>
                    <td>{p?.reason || ''}</td></tr>
                ))}</tbody>
              </table>
              {n0(s.rows) > prev.length && <div className="inv-muted inv-lg-pad">预览只列前 {prev.length} 张，下载的文件里是全部。</div>}
            </div>
          )}
          <label className="inv-lg-ck big"><input type="checkbox" checked={ok} onChange={e => setOk(e.target.checked)} />我已核对上面的标注</label>
          <div className="inv-lg-row">
            {res?.token
              ? <a className={'btn primary' + (ok ? '' : ' inv-lg-off')} href={ok ? invDeductDownloadUrl(res.token) : undefined}
                aria-disabled={!ok} onClick={e => { if (!ok) e.preventDefault() }}>下载已标注文件</a>
              : <span className="inv-lg-warn">没拿到下载凭证，请重新拖一次文件。</span>}
            <span className="inv-muted">下载后去税局「清单导入勾选」导入；导入后记得人工 <b>提交勾选</b>、<b>统计确认</b>。</span>
          </div>
        </section>
      )}
    </div>
  )
}

// ───────────────────────── 页签 4：新销方核查 ─────────────────────────

const SELLER_STATUS = [['', '全部'], ['unchecked', '未查'], ['无记录', '无记录'], ['hit', '命中']]
const CHANNELS = ['信用中国', '省税务局公布栏', '其他']

function CheckModal({ seller, onClose, onDone }) {
  const [form, setForm] = useState({
    date: todayStr(), channel: seller.checkChannel || CHANNELS[0],
    result: seller.checkResult === '命中' ? '命中' : '无记录', note: seller.checkNote || '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k, v) => { setErr(''); setForm(p => ({ ...p, [k]: v })) }
  const go = async () => {
    if (!form.date) { setErr('请填核查日期'); return }
    setBusy(true)
    try {
      await invSellerCheck({ taxId: seller.taxId, date: form.date, channel: form.channel, result: form.result, note: form.note.trim() })
      onDone()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Modal title="登记核查结果" onClose={onClose} width={540}
      footer={<>
        <button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button>
        <button type="button" className="btn primary" onClick={go} disabled={busy}>{busy ? '正在保存…' : '保存'}</button>
      </>}>
      <div className="inv-lg-form">
        <div className="inv-lg-note"><b>{seller.name || '—'}</b><br /><span className="mono">{seller.taxId || '—'}</span></div>
        <label className="inv-lg-fld">核查日期<input className="inv-in" type="date" value={form.date} onChange={e => set('date', e.target.value)} /></label>
        <label className="inv-lg-fld">查询渠道
          <select value={form.channel} onChange={e => set('channel', e.target.value)}>{CHANNELS.map(c => <option key={c}>{c}</option>)}</select>
        </label>
        <div className="inv-lg-fld">核查结果
          <div className="inv-lg-radio">
            {['无记录', '命中'].map(v => (
              <label key={v} className={'inv-lg-ck' + (v === '命中' && form.result === v ? ' hit' : '')}>
                <input type="radio" name="inv-lg-res" checked={form.result === v} onChange={() => set('result', v)} />{v}
              </label>
            ))}
          </div>
        </div>
        {form.result === '命中' && <div className="inv-lg-warn">命中只做提示，不会自动拒付。请把情况告诉经办人和财务经理，由人来决定怎么处理。</div>}
        <label className="inv-lg-fld">备注<textarea className="inv-lg-ta" rows={3} maxLength={500} value={form.note} onChange={e => set('note', e.target.value)} placeholder="可写查询截图存放位置、案件名称等" /></label>
        <ErrLine msg={err} />
      </div>
    </Modal>
  )
}

function SellersTab({ can, flash }) {
  const [month, setMonth] = useState(monthStr())
  const [status, setStatus] = useState('')
  const [rows, setRows] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [checking, setChecking] = useState(null)
  const seq = useRef(0)
  const load = useCallback(async () => {
    const my = ++seq.current
    setBusy(true); setErr('')
    try { const r = await invSellers({ month, status }); if (my === seq.current) setRows(asList(r?.rows)) }
    catch (e) { if (my === seq.current) setErr(errText(e)) }
    finally { if (my === seq.current) setBusy(false) }
  }, [month, status])
  useEffect(() => { load() }, [load])
  const resTone = r => (r === '命中' ? 'err' : r === '无记录' ? 'ok' : 'mute')
  const exportUrl = '/api/inv/sellers/export?' + new URLSearchParams({ month: month || '', ...(status ? { status } : {}) }).toString()
  return (
    <div className="inv-lg-stack">
      <div className="inv-lg-note">
        每月列出<b>第一次出现</b>的销方，请专人按统一社会信用代码去官方渠道查一次，把结果填回来：
        ① <a className="inv-lk" href="https://www.creditchina.gov.cn/" target="_blank" rel="noopener noreferrer">信用中国</a>「重大税收违法失信主体」查询；
        ② 销方所在省税务局官网的「重大税收违法失信案件信息公布栏」。
        这两个网站有验证码，系统不会自动去查；命中只提示，不自动拒付。
      </div>
      <div className="fbar inv-lg-fbar">
        <label>首次出现月份<input type="month" value={month} onChange={e => setMonth(e.target.value)} /></label>
        <label>核查结果
          <select value={status} onChange={e => setStatus(e.target.value)}>{SELLER_STATUS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </label>
        {busy && <span className="inv-muted"><span className="inv-spin" /> 读取中…</span>}
        <span className="inv-lg-grow" />
        <a className="btn" href={exportUrl}>导出 Excel</a>
      </div>
      <ErrLine msg={err && '销方名单没读出来：' + err} />
      {rows === null && busy && <div className="loading">正在读取新销方…</div>}
      {rows && rows.length === 0 && <div className="inv-lg-empty">{month ? `${month} ` : ''}没有符合条件的新销方。</div>}
      {rows && rows.length > 0 && (
        <div className="tbl-wrap">
          <table>
            <thead><tr><th>销方</th><th>税号</th><th>首次出现</th><th className="num">张数</th><th className="num">价税合计</th><th>核查结果</th><th /></tr></thead>
            <tbody>{rows.map(r => {
              const res = r.checkResult && r.checkResult !== '未查' ? r.checkResult : ''
              return (
                <tr key={r.taxId || r.name}>
                  <td>{r.name || '—'}</td>
                  <td className="mono">{r.taxId || '—'}</td>
                  <td className="mono">{r.firstSeen || '—'}</td>
                  <td className="num">{n0(r.items)}</td>
                  <td className="num">{money(r.total)}</td>
                  <td>
                    <span className={'inv-badge ' + resTone(res)}>{res || '未查'}</span>
                    {res && <span className="inv-lg-sub">{[r.checkDate, r.checkChannel, r.checkedBy].filter(Boolean).join(' · ')}</span>}
                    {r.checkNote && <span className="inv-lg-sub" title={r.checkNote}>{r.checkNote}</span>}
                  </td>
                  <td className="inv-lg-nowrap">{can.deduct && r.taxId && <button type="button" className="btn" onClick={() => setChecking(r)}>{res ? '改核查结果' : '登记核查'}</button>}</td>
                </tr>
              )
            })}</tbody>
          </table>
        </div>
      )}
      {checking && <CheckModal seller={checking} onClose={() => setChecking(null)}
        onDone={() => { setChecking(null); flash('核查结果已保存', 'ok'); load() }} />}
    </div>
  )
}

// ───────────────────────── 页签 5：期初导入 ─────────────────────────

const OPEN_COLS = [['number', '发票号码'], ['code', '发票代码'], ['date', '开票日期'], ['total', '价税合计'], ['sellerName', '销方'], ['buyerName', '购方'], ['ref', '报销单号 / 报销人']]

function SampleTable({ sample, headers }) {
  const rows = asList(sample).slice(0, 20)
  if (!rows.length) return null
  // 样例行可能是解析后的对象，也可能是原表的数组行（配合 headers）
  if (Array.isArray(rows[0])) {
    const hs = asList(headers)
    return (
      <div className="tbl-wrap inv-lg-mini"><table>
        <thead><tr>{hs.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c === null || c === undefined ? '' : String(c)}</td>)}</tr>)}</tbody>
      </table></div>
    )
  }
  return (
    <div className="tbl-wrap inv-lg-mini"><table>
      <thead><tr>{OPEN_COLS.map(([k, l]) => <th key={k} className={k === 'total' ? 'num' : undefined}>{l}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i} className={r?.numberLost ? 'inv-lg-bad' : undefined}>
          {OPEN_COLS.map(([k]) => {
            const v = k === 'date' ? first(r?.date, r?.issueDate, r?.issue_date) : r?.[k]
            return <td key={k} className={k === 'total' ? 'num' : (k === 'number' || k === 'code' ? 'mono' : undefined)}>
              {k === 'total' ? money(v) : (v === null || v === undefined || v === '' ? '—' : String(v))}
              {k === 'number' && r?.numberLost ? <span className="inv-badge err inv-lg-ml">号码丢位</span> : null}
            </td>
          })}
        </tr>
      ))}</tbody>
    </table></div>
  )
}

function OpeningStats({ stats, err }) {
  if (err) return <ErrLine msg={'期初底子情况没读出来：' + err} />
  if (!stats) return <div className="loading">正在读取已导入的期初…</div>
  const bs = asList(stats.batches)
  return (
    <section className="inv-lg-sec">
      <div className="inv-lg-sec-h">已导入的期初底子：共 {n0(stats.total)} 张</div>
      {bs.length === 0 ? <div className="inv-muted">还没导入过。</div> : (
        <div className="tbl-wrap inv-lg-mini"><table>
          <thead><tr><th>批次</th><th>来源</th><th>文件</th><th className="num">行数</th><th>导入人</th><th>导入时间</th></tr></thead>
          <tbody>{bs.map((b, i) => (
            <tr key={first(b?.id, b?.batchId, b?.batch_id, i)}>
              <td className="mono">{first(b?.id, b?.batchId, b?.batch_id, '—')}</td>
              <td>{first(b?.source, '—')}</td>
              <td>{first(b?.name, '—')}</td>
              <td className="num">{n0(first(b?.inserted, b?.rows))}</td>
              <td>{first(b?.importedBy, b?.imported_by, b?.createdBy, b?.created_by, '—')}</td>
              <td className="mono">{fmtTime(first(b?.importedAt, b?.imported_at, b?.createdAt, b?.created_at))}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </section>
  )
}

function OpeningTab({ can, flash }) {
  const [stats, setStats] = useState(null)
  const [statsErr, setStatsErr] = useState('')
  const [pv, setPv] = useState(null)
  const [fname, setFname] = useState('')
  const [source, setSource] = useState('票总管')
  const [busy, setBusy] = useState('')      // 'preview' | 'commit' | ''
  const [err, setErr] = useState('')
  const [done, setDone] = useState(null)
  const loadStats = useCallback(() => {
    setStatsErr('')
    return invOpeningStats().then(setStats).catch(e => setStatsErr(errText(e)))
  }, [])
  useEffect(() => { if (can.opening) loadStats() }, [can.opening, loadStats])
  if (!can.opening) {
    return <div className="inv-lg-note">期初导入会改动查重底子，需要单独的「期初导入」权限。您目前没有这个权限，如需导入请找系统管理员开通。</div>
  }
  const preview = async files => {
    if (!files[0]) return
    setBusy('preview'); setErr(''); setPv(null); setDone(null); setFname(files[0].name || '')
    try { setPv(await invOpeningPreview(files[0])) } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const commit = async () => {
    if (!source.trim()) { setErr('请填来源名称，例如「票总管」'); return }
    setBusy('commit'); setErr('')
    try {
      const r = await invOpeningCommit({ token: pv.token, source: source.trim() })
      setDone(r); setPv(null)
      flash(`期初已导入 ${n0(r?.inserted)} 张`, 'ok')
      loadStats()
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const lost = pv ? (typeof pv.numberLost === 'boolean' ? (pv.numberLost ? 1 : 0) : n0(pv.numberLost)) : 0
  const lines = mappingLines(pv?.mappingLines || pv?.mapping)
  const est = pv ? Math.max(0, n0(first(pv.willInsert, pv.newRows, n0(pv.rows) - n0(pv.dupWithin) - n0(pv.alreadyIn)))) : 0
  return (
    <div className="inv-lg-stack">
      <section className="inv-lg-sec">
        <div className="inv-lg-sec-h">期初导入</div>
        <div className="inv-muted">把票总管导出的历史发票清单拖进来，作为查重底子：以后再有人登记这些票，系统会提示「期初已有这张票」。只进查重底子，不进台账合计。</div>
        <FileDrop onFiles={preview} accept=".xlsx,.xls,.csv" multiple={false} disabled={!!busy}>
          <span className="inv-drop-tx">{busy === 'preview' ? <><span className="inv-spin" /> 正在读取…</> : <>把历史发票清单拖到这里，或<b>点击选择</b>（先预览，确认后才导入）</>}</span>
        </FileDrop>
        {!pv && <ErrLine msg={err && '清单没读成功：' + err} />}
        {done && <div className="inv-lg-ok">导入完成：新增 {n0(done.inserted)} 张进查重底子。</div>}
      </section>
      {pv && (
        <section className="inv-lg-sec">
          <div className="inv-lg-sec-h">预览{fname ? `：${fname}` : ''}</div>
          <div className="inv-lg-chips">
            <span className="inv-lg-chip">读到 <b>{n0(pv.rows)}</b> 行</span>
            <span className="inv-lg-chip">文件内重复 <b>{n0(pv.dupWithin)}</b></span>
            <span className="inv-lg-chip">底子里已有 <b>{n0(pv.alreadyIn)}</b></span>
            {lost > 0 && <span className="inv-lg-chip err">号码丢位 <b>{typeof pv.numberLost === 'boolean' ? '有' : lost}</b></span>}
            <span className="inv-lg-chip ok">预计新增约 <b>{est}</b></span>
          </div>
          {lost > 0 && <div className="inv-lg-warn">有发票号码在 Excel 里显示成了科学计数法（像 2.6E+19 这样），后几位已经丢了，这些行没法拿来查重。请在票总管导出时把号码列设成「文本」，或用导出的 CSV 原件，再重新拖一次。</div>}
          {lines.length > 0 && <div className="inv-lg-box"><div className="inv-lg-box-h">识别到的列（字段 ← 表头）</div>
            <div className="inv-lg-map">{lines.map((l, i) => <span key={i}>{l}</span>)}</div></div>}
          {asList(pv.warnings).map((w, i) => <div key={i} className="inv-lg-warn">{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
          <SampleTable sample={pv.sample} headers={pv.headers} />
          <div className="inv-lg-row">
            <label className="inv-lg-fld inline">来源名称<input className="inv-in" value={source} maxLength={40} onChange={e => { setSource(e.target.value); setErr('') }} /></label>
            <button type="button" className="btn primary" onClick={commit} disabled={!!busy || !pv.token}>{busy === 'commit' ? '正在导入…' : '确认导入'}</button>
            <button type="button" className="btn" onClick={() => { setPv(null); setErr('') }} disabled={!!busy}>不导了</button>
          </div>
          <ErrLine msg={err && '没导入成功：' + err} />
        </section>
      )}
      <OpeningStats stats={stats} err={statsErr} />
    </div>
  )
}

// ───────────────────────── 页面 ─────────────────────────

export default function InvLedger({ user }) {
  const [tab, setTab] = useState(tabGet)
  const [cfg, setCfg] = useState(null)
  const [cfgErr, setCfgErr] = useState('')
  const [toast, flash] = useToast()
  useEffect(() => {
    let live = true
    invConfig().then(r => { if (live) setCfg(r) }).catch(e => { if (live) setCfgErr(errText(e)) })
    return () => { live = false }
  }, [])
  const can = cfg?.can || {}
  const pick = k => { setTab(k); tabSet(k) }
  const sub = {
    query: '审核通过的票都在这里，一票一行；点一行看详情',
    taxlist: '拿电子税务局导出的清单逐张验真',
    deduct: '按台账给税局的未勾选清单标注，人来确认、提交',
    sellers: '每月第一次出现的销方，人工查一次失信名单',
    opening: '导入历史发票清单，作为查重底子',
  }[tab]
  return (
    <div className="inv-lg">
      <div className="head">
        <div><div className="h-title">发票台账</div><div className="h-sub">{sub}</div></div>
      </div>
      <div className="body">
        <div className="inv-lg-tabs" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} className={'inv-lg-tab' + (tab === k ? ' on' : '')} onClick={() => pick(k)}>{l}</button>
          ))}
        </div>
        {cfgErr && <ErrLine msg={'权限信息没读到，操作按钮先都藏起来了：' + cfgErr} />}
        {!cfg && !cfgErr ? <div className="loading">加载中…</div> : (
          <>
            {tab === 'query' && <QueryTab can={can} flash={flash} />}
            {tab === 'taxlist' && <TaxlistTab can={can} flash={flash} />}
            {tab === 'deduct' && <DeductTab can={can} />}
            {tab === 'sellers' && <SellersTab can={can} flash={flash} />}
            {tab === 'opening' && <OpeningTab can={can} flash={flash} />}
          </>
        )}
      </div>
      {toast}
    </div>
  )
}
