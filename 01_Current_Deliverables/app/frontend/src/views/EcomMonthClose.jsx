// [Change Log]
// Date: 2026-09-10
// Author: Codex
// Version: V2.553
// Description: 天猫平台优先的真实数据工作台：逐单订单、宝贝销售、资金去向及旺店通/金蝶核销承接。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ecTmallAlipayUpload, ecTmallMonthCloseLatest, ecTmallMonthCloseUpload, ecTmallOrderDetails,
  ecWdtMonthCloseLatest, ecWdtMonthCloseUpload,
} from '../api.js'
import './ecomMonthClose.css'

const WORKFLOW = ['平台订单', '商品明细', '资金入账', '发货核对', '应收核对', '退款费用', '成本结转', '月结底稿']
const EMPTY_TMALL = { sources: {}, reconciliation: {} }

const money = value => value == null
  ? '待补数据'
  : Number(value).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY', minimumFractionDigits: 2 })
const count = value => Number(value || 0).toLocaleString('zh-CN')
const sourceOf = (tmall, kind) => tmall.sources?.[kind] || { available: false }

const Icon = ({ name }) => {
  const paths = {
    refresh: <><path d="M20 11a8 8 0 1 0-2.35 5.65"/><path d="M20 4v7h-7"/></>,
    arrow: <><path d="M5 12h14"/><path d="m14 7 5 5-5 5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    warning: <><path d="M12 4 3 20h18z"/><path d="M12 9v4M12 17h.01"/></>,
    upload: <><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/></>,
    search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></>,
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}

const Status = ({ value }) => {
  const map = { ready: ['已就绪', 'done'], warning: ['有提醒', 'doing'], blocked: ['被阻塞', 'blocked'], missing: ['待导入', 'todo'] }
  const [label, tone] = map[value] || map.missing
  return <span className={`emc-status ${tone}`}><i />{label}</span>
}

function UploadButton({ inputRef, label, kind, uploading, canUpload, onFile, accept = '.xlsx', multiple = false }) {
  return <>
    <input ref={inputRef} type="file" accept={accept} multiple={multiple} onChange={event => onFile(kind, event)} hidden />
    <button className="emc-button tiny secondary" disabled={!canUpload || uploading === kind}
      title={canUpload ? '' : '需要上传结算流水/跑批权限'} onClick={() => inputRef.current?.click()}>
      <Icon name="upload" />{uploading === kind ? '解析中' : label}
    </button>
  </>
}

export default function EcomMonthClose({ user, onNav }) {
  const [screen, setScreen] = useState('overview')
  const [period, setPeriod] = useState('2026-08')
  const [tmall, setTmall] = useState({ ...EMPTY_TMALL, loading: true, error: '' })
  const [wdt, setWdt] = useState({ available: false, loading: true, error: '' })
  const [uploading, setUploading] = useState('')
  const [toast, setToast] = useState('')
  const orderInput = useRef(null)
  const itemInput = useRef(null)
  const fundInput = useRef(null)
  const alipayInput = useRef(null)
  const wdtInput = useRef(null)

  useEffect(() => {
    const previousTitle = document.title
    document.title = '天猫平台数据工作台 · 财务核算'
    return () => { document.title = previousTitle }
  }, [])

  const loadAll = useCallback(async () => {
    setTmall(old => ({ ...old, loading: true, error: '' }))
    setWdt(old => ({ ...old, loading: true, error: '' }))
    const [tmallResult, wdtResult] = await Promise.allSettled([
      ecTmallMonthCloseLatest(period), ecWdtMonthCloseLatest(period),
    ])
    setTmall(tmallResult.status === 'fulfilled'
      ? { ...tmallResult.value, loading: false, error: '' }
      : { ...EMPTY_TMALL, loading: false, error: tmallResult.reason?.message || '读取失败' })
    setWdt(wdtResult.status === 'fulfilled'
      ? { ...wdtResult.value, loading: false, error: '' }
      : { available: false, loading: false, error: wdtResult.reason?.message || '读取失败' })
  }, [period])

  useEffect(() => { loadAll() }, [loadAll])

  const canUpload = !!(user && (user.role === 'admin' || (user.perms || {}).ec_settle_upload))
  const upload = async (kind, event) => {
    const files = Array.from(event.target.files || [])
    const file = files[0]
    event.target.value = ''
    if (!file) return
    setUploading(kind)
    try {
      if (kind === 'wdt') {
        const result = await ecWdtMonthCloseUpload(period, file)
        setWdt({ ...result, loading: false, error: '' })
        setToast(result.duplicate ? '旺店通文件已导入，本次复用原汇总' : '旺店通销售出库已导入')
      } else if (kind === 'alipay') {
        const result = await ecTmallAlipayUpload(period, files)
        setTmall({ ...result, loading: false, error: '' })
        const summary = result.sources?.alipay?.summary || {}
        setToast(result.duplicate
          ? '支付宝 2088 流水已导入，本次复用原汇总'
          : `支付宝流水已合并：${count(summary.unique_rows)} 条唯一流水`)
      } else {
        const result = await ecTmallMonthCloseUpload(period, kind, file)
        setTmall({ ...result, loading: false, error: '' })
        const labels = { order: '订单报表', item: '宝贝销售明细', fund: '聚合结算账户' }
        setToast(result.duplicate ? `${labels[kind]}已导入，本次复用原汇总` : `${labels[kind]}已导入并完成校验`)
      }
      window.setTimeout(() => setToast(''), 2600)
    } catch (error) {
      const message = error.message || '导入失败'
      if (kind === 'wdt') setWdt(old => ({ ...old, loading: false, error: message }))
      else setTmall(old => ({ ...old, loading: false, error: message }))
    } finally {
      setUploading('')
    }
  }

  const sources = useMemo(() => buildSources(tmall, wdt), [tmall, wdt])
  const readyCount = sources.filter(source => source.state === 'ready' || source.state === 'warning').length
  const refs = { order: orderInput, item: itemInput, fund: fundInput, alipay: alipayInput, wdt: wdtInput }

  return <div className="emc-page">
    {toast ? <div className="emc-toast" role="status"><Icon name="check" />{toast}</div> : null}
    <header className="emc-header">
      <div>
        <nav className="emc-breadcrumb" aria-label="当前位置"><span>应收模块</span><i>/</i><span>电商对账</span><i>/</i><strong>天猫月结</strong></nav>
        <div className="emc-title">天猫平台数据工作台</div>
        <div className="emc-subtitle">先建立平台订单、商品和资金事实，再接旺店通、金蝶与月结底稿</div>
      </div>
      <div className="emc-actions">
        <label className="emc-field"><span>结算期间</span><select value={period} onChange={event => setPeriod(event.target.value)}><option value="2026-08">2026年8月</option><option value="2026-07">2026年7月</option></select></label>
        <button className="emc-button secondary" onClick={loadAll} disabled={tmall.loading || wdt.loading}><Icon name="refresh" />{tmall.loading || wdt.loading ? '刷新中' : '刷新数据状态'}</button>
      </div>
    </header>

    <nav className="emc-module-tabs" aria-label="电商对账功能">
      <button className="active">月结工作台</button>
      <button onClick={() => onNav?.('ecomsettle')}>收款核销</button>
      <button onClick={() => onNav?.('ecombase')}>基础资料</button>
    </nav>
    <div className="emc-demo"><strong>数据边界</strong> 逐单页仅保存订单号和核算必要字段。收货人、电话、地址、留言、备注和支付单号不进入页面、日志或核算库。</div>
    <div className="emc-view-switch" aria-label="页面切换">
      <button className={screen === 'overview' ? 'active' : ''} onClick={() => setScreen('overview')}>天猫数据总览</button>
      <button className={screen === 'workbench' ? 'active' : ''} onClick={() => setScreen('workbench')}>来源与勾稽</button>
      <button className={screen === 'orders' ? 'active' : ''} onClick={() => setScreen('orders')}>订单明细</button>
    </div>
    {tmall.error ? <div className="emc-page-error" role="alert">天猫数据读取失败：{tmall.error}</div> : null}
    {wdt.error ? <div className="emc-page-error" role="alert">旺店通读取失败：{wdt.error}</div> : null}
    {screen === 'overview'
      ? <Overview tmall={tmall} sources={sources} readyCount={readyCount}
          onOpen={() => setScreen('workbench')} onOpenOrders={() => setScreen('orders')} />
      : screen === 'orders'
        ? <OrderDetailView period={period} />
        : <Workbench tmall={tmall} wdt={wdt} sources={sources} readyCount={readyCount} refs={refs}
            canUpload={canUpload} uploading={uploading} upload={upload} />}
  </div>
}

function buildSources(tmall, wdt) {
  const order = sourceOf(tmall, 'order')
  const item = sourceOf(tmall, 'item')
  const fund = sourceOf(tmall, 'fund')
  const alipay = sourceOf(tmall, 'alipay')
  const wdtSummary = wdt.summary || {}
  return [
    { key: 'order', name: '天猫订单报表', state: order.available ? order.status : 'missing', count: order.available ? `${count(order.summary.order_count)} 单` : '尚未导入', note: '订单创建时间归属' },
    { key: 'item', name: '宝贝销售明细', state: item.available ? item.status : 'missing', count: item.available ? `${count(item.summary.suborder_count)} 个子订单` : '尚未导入', note: item.available && item.summary.missing_merchant_sku_rows ? `${count(item.summary.missing_merchant_sku_rows)} 行缺商家编码` : '商品与数量口径' },
    { key: 'fund', name: '聚合结算账户', state: fund.available ? fund.status : 'missing', count: fund.available ? `${count(fund.summary.payment_serial_count)} 笔` : '尚未导入', note: '入账时间归属' },
    { key: 'alipay', name: '支付宝 2088 流水', state: alipay.available ? alipay.status : 'missing', count: alipay.available ? `${count(alipay.summary.unique_rows)} 条` : '尚未导入', note: alipay.available && alipay.summary.duplicate_row_occurrences ? `已去重 ${count(alipay.summary.duplicate_row_occurrences)} 条` : '确认支付宝账户入账' },
    { key: 'refund', name: '平台退款售后', state: 'missing', count: '待提供', note: '解释退款原因与阶段' },
    { key: 'shipment', name: '旺店通销售出库', state: wdt.available ? (wdt.status || 'warning') : 'missing', count: wdt.available ? `${count(wdtSummary.order_count)} 单` : '尚未导入', note: '发货与应收核对' },
    { key: 'cost', name: '商品成本', state: wdt.available && wdtSummary.cost_status === 'ready' ? 'ready' : 'blocked', count: wdt.available ? money(wdtSummary.cost_total) : '缺少有效成本', note: '成本通过前禁止月结' },
  ]
}

function Overview({ tmall, onOpen, onOpenOrders }) {
  const order = sourceOf(tmall, 'order').summary || {}
  const item = sourceOf(tmall, 'item').summary || {}
  const fund = sourceOf(tmall, 'fund').summary || {}
  const alipay = sourceOf(tmall, 'alipay').summary || {}
  const orderItem = tmall.reconciliation?.order_item || {}
  const destination = tmall.reconciliation?.payment_destination || {}
  const byMethod = destination.by_payment_method || {}
  const currentDifference = (item.gross_paid_total == null || order.current_paid_total == null)
    ? null : item.gross_paid_total - order.current_paid_total
  const refundSuccess = item.refund_status_counts?.['退款成功'] || 0
  return <main className="emc-content">
    <section className="emc-flow" aria-label="天猫月结流程">
      {WORKFLOW.map((step, index) => <div className={`emc-flow-step ${index < 3 ? 'done' : index === 3 ? 'current' : ''}`} key={step}><span>{index + 1}</span><b>{step}</b></div>)}
    </section>
    <section className="emc-kpis" aria-label="天猫平台关键指标">
      <article><span>主订单</span><strong>{order.order_count == null ? '—' : count(order.order_count)}</strong><small>订单创建时间：2026年8月</small></article>
      <article><span>商品成交实付</span><strong>{money(item.gross_paid_total)}</strong><small>宝贝销售明细成交态</small></article>
      <article className="warn"><span>退款金额</span><strong>{money(item.refund_total)}</strong><small>{count(refundSuccess)} 个子订单退款成功</small></article>
      <article><span>成交实付减退款</span><strong>{money(item.net_paid_after_refund)}</strong><small>过程口径，不等同确认收入</small></article>
    </section>

    <div className="emc-report-grid">
      <section className="emc-panel emc-report-card wide">
        <div className="emc-panel-head"><div><h2>订单与商品明细勾稽</h2><p>先证明平台两张报表覆盖同一批订单</p></div><Status value={orderItem.available ? orderItem.status : 'missing'} /></div>
        <div className="emc-tieout">
          <div><span>订单报表主订单</span><strong>{order.order_count == null ? '—' : count(order.order_count)}</strong></div>
          <div><span>宝贝明细主订单</span><strong>{item.main_order_count == null ? '—' : count(item.main_order_count)}</strong></div>
          <div className={orderItem.order_only || orderItem.item_only ? 'bad' : 'good'}><span>精确匹配</span><strong>{orderItem.available ? count(orderItem.matched_main_orders) : '待导入'}</strong><small>{orderItem.available ? `单表独有 ${count((orderItem.order_only || 0) + (orderItem.item_only || 0))} 单` : '两表到齐后自动校验'}</small></div>
          <div><span>子订单 / 商品件数</span><strong>{item.suborder_count == null ? '—' : `${count(item.suborder_count)} / ${count(item.goods_quantity)}`}</strong><small>{count(item.merchant_sku_count)} 个商家编码</small></div>
        </div>
      </section>

      <section className="emc-panel emc-report-card">
        <div className="emc-panel-head"><div><h2>订单状态</h2><p>订单报表当前态</p></div></div>
        <div className="emc-stat-list">
          <div><span>交易成功</span><strong>{count(order.status_counts?.['交易成功'])}</strong></div>
          <div><span>交易关闭</span><strong>{count(order.status_counts?.['交易关闭'])}</strong></div>
          <div><span>待确认收货</span><strong>{count(order.status_counts?.['卖家已发货，等待买家确认'])}</strong></div>
          <div><span>付款时间跨到下月</span><strong>{count(order.payment_after_period_orders)}</strong></div>
        </div>
      </section>

      <section className="emc-panel emc-report-card">
        <div className="emc-panel-head"><div><h2>金额口径差异</h2><p>成交态与订单当前态不强行合并</p></div></div>
        <div className="emc-stat-list">
          <div><span>商品成交实付</span><strong>{money(item.gross_paid_total)}</strong></div>
          <div><span>订单当前实付</span><strong>{money(order.current_paid_total)}</strong></div>
          <div className="warn"><span>两口径差额</span><strong>{money(currentDifference)}</strong></div>
          <div><span>两表退款合计</span><strong>{money(item.refund_total)}</strong><small>与订单报表一致</small></div>
        </div>
      </section>

      <section className="emc-panel emc-report-card wide">
        <div className="emc-panel-head"><div><h2>订单实收账户去向</h2><p>订单支付方式只是线索；2088 与聚合资金表的「交易收款」是入账证据</p></div><Status value={destination.available ? destination.status : 'missing'} /></div>
        <div className="emc-table-wrap"><table className="emc-table route"><thead><tr><th>订单支付方式</th><th className="num">平台订单</th><th className="num">支付宝 2088 实收</th><th className="num">聚合账户实收</th><th className="num">未命中</th><th>当前判断</th></tr></thead><tbody>
          <RouteRow method="微信支付" value={byMethod['微信支付']} />
          <RouteRow method="支付宝" value={byMethod['支付宝']} />
        </tbody></table></div>
        <div className="emc-route-note"><strong>实收去向校验</strong><span>2088 命中 {count(destination.alipay_receipt_orders)} 个本期订单</span><span>聚合命中 {count(destination.aggregate_receipt_orders)} 个</span><span>未命中 {count(destination.unresolved_orders)} 个</span><span>双端实收冲突 {count(destination.both_receipt_sources)} 个</span><button className="emc-link" onClick={onOpenOrders}>逐单查看 <Icon name="arrow" /></button></div>
      </section>

      <section className="emc-panel emc-report-card">
        <div className="emc-panel-head"><div><h2>聚合账户收支</h2><p>提现单列，不当平台费用</p></div></div>
        <div className="emc-stat-list">
          <div><span>交易收款</span><strong>{money(fund.entry_types?.['交易收款']?.income)}</strong></div>
          <div><span>平台扣款</span><strong>{money(fund.entry_types?.['扣款']?.expense)}</strong></div>
          <div><span>售后退款</span><strong>{money(fund.entry_types?.['交易退款(售后)']?.expense)}</strong></div>
          <div><span>提现</span><strong>{money(fund.entry_types?.['提现']?.expense)}</strong></div>
        </div>
      </section>

      <section className="emc-panel emc-report-card">
        <div className="emc-panel-head"><div><h2>支付宝 2088 收支</h2><p>7 个文件按流水号去重后汇总</p></div></div>
        <div className="emc-stat-list">
          <div><span>唯一流水</span><strong>{alipay.unique_rows == null ? '待导入' : count(alipay.unique_rows)}</strong><small>{alipay.effective_segment_count == null ? '' : `${count(alipay.effective_segment_count)} 个有效分片`}</small></div>
          <div><span>账户净变动</span><strong>{money(alipay.net_change)}</strong></div>
          <div><span>交易收款</span><strong>{money(alipay.platform_receipt_income_total)}</strong></div>
          <div className="warn"><span>已剔除重复流水</span><strong>{alipay.duplicate_row_occurrences == null ? '待导入' : count(alipay.duplicate_row_occurrences)}</strong></div>
        </div>
      </section>

      <section className="emc-panel emc-next-card">
        <div><span>下一份关键数据</span><h2>平台退款售后明细</h2><p>用于解释退款原因、货物状态和跨期退款。</p></div>
        <button className="emc-button primary" onClick={onOpen}>查看来源与导入 <Icon name="arrow" /></button>
      </section>
    </div>
  </main>
}

function RouteRow({ method, value = {} }) {
  const conflicts = value.both_receipt_sources || 0
  const unresolved = value.unresolved_orders || 0
  const judgement = conflicts
    ? `${count(conflicts)} 单同时命中两个实收账户，需复核`
    : unresolved ? `${count(unresolved)} 单保留为跨期/未结算` : '本期实收去向已定位'
  return <tr><td><strong>{method}</strong></td><td className="num">{count(value.orders)}</td><td className="num">{count(value.alipay_receipt_orders)}</td><td className="num">{count(value.aggregate_receipt_orders)}</td><td className="num">{count(unresolved)}</td><td>{judgement}</td></tr>
}

const detailMoney = value => Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dateTime = value => value ? value.slice(0, 16) : '—'

function DestinationChip({ value }) {
  const tone = { 支付宝2088: 'alipay', 聚合账户: 'aggregate', 待查: 'pending', 冲突: 'conflict' }[value] || 'pending'
  return <span className={`emc-destination ${tone}`}>{value}</span>
}

function OrderDetailView({ period }) {
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState({ q: '', paymentMethod: '', destination: '', status: '' })
  const [page, setPage] = useState(1)
  const [data, setData] = useState({ rows: [], destination_counts: {}, payment_methods: [], statuses: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    ecTmallOrderDetails(period, { ...filters, page, pageSize: 50 }).then(result => {
      if (active) setData(result)
    }).catch(reason => {
      if (active) setError(reason.message || '订单明细读取失败')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [period, page, filters])

  const changeFilter = (key, value) => {
    setPage(1)
    setFilters(old => ({ ...old, [key]: value }))
  }
  const submitSearch = event => {
    event.preventDefault()
    changeFilter('q', query.trim())
  }
  const destinationCounts = data.destination_counts || {}
  return <main className="emc-content emc-order-detail">
    <section className="emc-detail-summary" aria-label="逐单资金去向汇总">
      <article><span>全部订单</span><strong>{count(data.all_order_count)}</strong><small>订单报表逐单口径</small></article>
      <article className="alipay"><span>支付宝 2088</span><strong>{count(destinationCounts['支付宝2088'])}</strong><small>0010001 交易收款命中</small></article>
      <article className="aggregate"><span>聚合账户</span><strong>{count(destinationCounts['聚合账户'])}</strong><small>聚合表交易收款命中</small></article>
      <article className="pending"><span>待查订单</span><strong>{count(destinationCounts['待查'])}</strong><small>可能跨期或尚未结算</small></article>
      <article className="conflict"><span>双端冲突</span><strong>{count(destinationCounts['冲突'])}</strong><small>两个资金账户均命中</small></article>
    </section>

    <section className="emc-panel emc-detail-panel">
      <div className="emc-detail-head">
        <div><h2>订单收入确认明细</h2><p>平台数据、旺店通/金蝶内部数据与核对结论放在同一行，共筛出 {count(data.total)} 单</p></div>
        <form className="emc-order-search" onSubmit={submitSearch}>
          <Icon name="search" />
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="输入完整或部分订单号" aria-label="搜索订单号" />
          <button type="submit">查询</button>
        </form>
      </div>
      <div className="emc-detail-filters">
        <label><span>支付方式</span><select value={filters.paymentMethod} onChange={event => changeFilter('paymentMethod', event.target.value)}><option value="">全部</option>{(data.payment_methods || []).map(value => <option key={value}>{value}</option>)}</select></label>
        <label><span>实际去向</span><select value={filters.destination} onChange={event => changeFilter('destination', event.target.value)}><option value="">全部</option><option>支付宝2088</option><option>聚合账户</option><option>待查</option><option>冲突</option></select></label>
        <label><span>订单状态</span><select value={filters.status} onChange={event => changeFilter('status', event.target.value)}><option value="">全部</option>{(data.statuses || []).map(value => <option key={value}>{value}</option>)}</select></label>
        {(filters.q || filters.paymentMethod || filters.destination || filters.status) ? <button className="emc-clear" onClick={() => { setQuery(''); setPage(1); setFilters({ q: '', paymentMethod: '', destination: '', status: '' }) }}>清除筛选</button> : null}
      </div>
      {error ? <div className="emc-page-error" role="alert">{error}</div> : null}
      {!error && loading ? <div className="emc-empty">正在读取订单明细…</div> : null}
      {!error && !loading && !data.available ? <div className="emc-empty">当前订单批次尚未生成逐单数据，请重新导入订单报表。</div> : null}
      {!error && !loading && data.available ? <>
        <div className="emc-table-wrap emc-order-scroll"><table className="emc-table emc-order-table"><thead><tr className="emc-column-groups"><th colSpan="6">平台侧数据</th><th colSpan="4">旺店通 / 金蝶内部数据</th><th colSpan="6">核对结果</th></tr><tr>
          <th>订单编号</th><th>创建时间</th><th>订单状态</th><th>支付方式</th><th className="num">当前实付</th><th className="num">平台退款</th><th>内部同步</th><th>金蝶应收单号</th><th className="num">金蝶应收</th><th className="num">退款调节</th><th className="num">2088 实收</th><th className="num">聚合实收</th><th>实际去向</th><th>入账时间</th><th className="num">核对差额</th><th>核对状态 / 差异原因</th>
        </tr></thead><tbody>
          {(data.rows || []).map(row => <tr key={row.order_no}>
            <td className="mono order-no">{row.order_no}</td><td>{dateTime(row.created_at)}</td><td><span className="emc-order-status">{row.status}</span></td><td>{row.payment_method}</td><td className="num">{detailMoney(row.current_paid)}</td><td className={`num ${row.refund ? 'negative' : ''}`}>{detailMoney(row.refund)}</td><td><span className={`emc-internal ${row.internal_sync_status === '已接入' ? 'ready' : ''}`}>{row.internal_sync_status}</span></td><td className="mono">{row.kingdee_ar_no || '—'}</td><td className="num">{row.kingdee_ar_amount == null ? '—' : detailMoney(row.kingdee_ar_amount)}</td><td className="num">{row.wdt_refund_adjustment == null ? '—' : detailMoney(row.wdt_refund_adjustment)}</td><td className="num">{detailMoney(row.alipay_receipt)}</td><td className="num">{detailMoney(row.aggregate_receipt)}</td><td><DestinationChip value={row.destination} /></td><td>{dateTime(row.receipt_at)}</td><td className={`num ${row.reconcile_diff ? 'negative' : ''}`}>{row.reconcile_diff == null ? '—' : detailMoney(row.reconcile_diff)}</td><td className="emc-reconcile-cell"><strong>{row.reconcile_status}</strong><small>{row.difference_reason}</small></td>
          </tr>)}
        </tbody></table>{!data.rows?.length ? <div className="emc-empty">没有符合筛选条件的订单</div> : null}</div>
        <div className="emc-pagination"><span>第 {data.page || 1} / {data.page_count || 1} 页，每页 50 单</span><div><button disabled={page <= 1} onClick={() => setPage(value => Math.max(1, value - 1))}>上一页</button><button disabled={page >= (data.page_count || 1)} onClick={() => setPage(value => value + 1)}>下一页</button></div></div>
      </> : null}
    </section>
    <div className="emc-privacy-note">平台与资金列已按本次真实报表填充。旺店通/金蝶列只在逐单核销跑批存在时回填，否则明确显示“待同步”。逐单视图不含收货人、电话、地址、留言、备注和支付流水号。</div>
  </main>
}

function Workbench({ tmall, wdt, sources, readyCount, refs, canUpload, uploading, upload }) {
  const order = sourceOf(tmall, 'order')
  const item = sourceOf(tmall, 'item')
  const fund = sourceOf(tmall, 'fund')
  const alipay = sourceOf(tmall, 'alipay')
  const orderItem = tmall.reconciliation?.order_item || {}
  const orderFund = tmall.reconciliation?.order_fund || {}
  const orderAlipay = tmall.reconciliation?.order_alipay || {}
  return <main className="emc-content workbench">
    <section className="emc-panel emc-sources">
      <div className="emc-panel-head compact"><div><h2>天猫月结数据源 <span>{readyCount}/7</span></h2><p>先完成平台事实，再接业务系统和账务系统</p></div><small>绿色为结构校验通过，黄色为数据质量提醒</small></div>
      <div className="emc-source-grid">{sources.map(source => <article className={source.state === 'blocked' ? 'missing' : source.state} key={source.key}><span className="emc-source-icon">{source.state === 'ready' ? <Icon name="check" /> : <Icon name="warning" />}</span><div><strong>{source.name}</strong><b>{source.state === 'ready' ? '已就绪' : source.state === 'warning' ? '有提醒' : source.state === 'blocked' ? '被阻塞' : '待导入'}</b><small>{source.count} · {source.note}</small></div></article>)}</div>
    </section>

    <section className="emc-panel emc-upload-panel">
      <div className="emc-panel-head"><div><h2>平台文件导入</h2><p>系统按表头识别业务字段，个人信息列在导入边界丢弃</p></div></div>
      <div className="emc-upload-grid">
        <UploadRow title="订单报表" source={order} detail={order.available ? `${count(order.summary.order_count)} 个主订单，${money(order.summary.current_paid_total)} 当前实付` : '天猫卖家中心订单导出'}>
          <UploadButton inputRef={refs.order} kind="order" label="导入订单报表" uploading={uploading} canUpload={canUpload} onFile={upload} />
        </UploadRow>
        <UploadRow title="宝贝销售明细" source={item} detail={item.available ? `${count(item.summary.suborder_count)} 个子订单，${count(item.summary.goods_quantity)} 件商品` : '天猫卖家中心宝贝销售明细'}>
          <UploadButton inputRef={refs.item} kind="item" label="导入宝贝明细" uploading={uploading} canUpload={canUpload} onFile={upload} />
        </UploadRow>
        <UploadRow title="聚合结算账户" source={fund} detail={fund.available ? `${count(fund.summary.payment_serial_count)} 笔，净变动 ${money(fund.summary.net_change)}` : '资金管理聚合结算账户余额明细'}>
          <UploadButton inputRef={refs.fund} kind="fund" label="导入聚合资金" uploading={uploading} canUpload={canUpload} onFile={upload} />
        </UploadRow>
        <UploadRow title="支付宝 2088 流水" source={alipay} detail={alipay.available ? `${count(alipay.summary.unique_rows)} 条唯一流水，${count(alipay.summary.effective_segment_count)} 个有效分片` : '可一次选择同月多个账务组合查询分片'}>
          <UploadButton inputRef={refs.alipay} kind="alipay" label="导入 2088 分片" uploading={uploading} canUpload={canUpload} onFile={upload} accept=".xls,.xlsx,.zip" multiple />
        </UploadRow>
        <UploadRow title="旺店通销售出库" source={wdt} detail={wdt.available ? `${count(wdt.summary.order_count)} 单，按发货时间归属` : '平台数据完成后用于发货与应收核对'}>
          <UploadButton inputRef={refs.wdt} kind="wdt" label="导入旺店通出库" uploading={uploading} canUpload={canUpload} onFile={upload} />
        </UploadRow>
      </div>
    </section>

    <div className="emc-work-grid">
      <section className="emc-panel emc-checks">
        <div className="emc-panel-head"><div><h2>自动勾稽结果</h2><p>这里只展示计数和金额，不展示订单号</p></div></div>
        <div className="emc-check-list">
          <CheckRow title="订单报表 ↔ 宝贝销售明细" state={orderItem.available ? orderItem.status : 'missing'} result={orderItem.available ? `${count(orderItem.matched_main_orders)} 个主订单精确匹配` : '等待两张平台表'} note={orderItem.available ? `单表独有 ${count((orderItem.order_only || 0) + (orderItem.item_only || 0))} 单` : '按不可逆订单技术键勾稽'} />
          <CheckRow title="订单支付方式 ↔ 聚合账户" state={orderFund.available ? 'ready' : 'missing'} result={orderFund.available ? `${count(orderFund.matched_current_period_orders)} 个本期订单确认进入聚合账户` : '等待订单与聚合资金表'} note={orderFund.available ? `${count(orderFund.fund_orders_outside_current_order_export)} 个聚合入账来自其他下单期间` : '按淘宝订单编号勾稽'} />
          <CheckRow title="平台订单 ↔ 支付宝 2088 交易收款" state={orderAlipay.available ? orderAlipay.status : 'missing'} result={orderAlipay.available ? `${count(orderAlipay.matched_current_period_orders)} 个本期订单确认进入支付宝 2088` : '等待订单与 2088 流水'} note={orderAlipay.available ? `本期命中交易收款 ${money(orderAlipay.matched_receipt_income)}` : '按不可逆订单键与费目码 0010001 勾稽'} />
          <CheckRow title="平台订单 ↔ 旺店通发货" state={wdt.available ? 'warning' : 'missing'} result={wdt.available ? '旺店通已导入，等待接入订单级勾稽' : '尚未导入旺店通'} note="下一阶段按订单技术键比对" />
        </div>
      </section>

      <aside className="emc-panel emc-blockers">
        <div className="emc-panel-head"><div><h2>接下来要补</h2><p>按核算顺序排列</p></div></div>
        <div className="emc-blocker-list">
          <button><i className="red"/><span><strong>退款售后明细</strong><small>解释 {count(item.summary?.refund_status_counts?.['退款成功'])} 个退款成功子订单</small></span><Icon name="arrow"/></button>
          <button><i className="amber"/><span><strong>补 4 行商家编码</strong><small>宝贝明细存在 SKU 质量提醒，影响成本匹配</small></span><Icon name="arrow"/></button>
          <button><i className="blue"/><span><strong>旺店通与金蝶核对</strong><small>平台事实稳定后再进入发货、应收和成本</small></span><Icon name="arrow"/></button>
        </div>
      </aside>
    </div>
  </main>
}

function UploadRow({ title, source, detail, children }) {
  const state = source.available ? (source.status || 'warning') : 'missing'
  return <article><div><h3>{title}</h3><Status value={state} /><p>{detail}</p><small>{source.imported_at ? `最近导入 ${source.imported_at}` : '未留存原文件和文件名'}</small></div>{children}</article>
}

function CheckRow({ title, state, result, note }) {
  return <article><span className={`emc-check-dot ${state}`} /><div><strong>{title}</strong><p>{result}</p><small>{note}</small></div><Status value={state} /></article>
}
