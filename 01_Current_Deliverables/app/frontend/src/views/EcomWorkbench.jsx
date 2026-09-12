import React, { useEffect, useMemo, useState } from 'react'
import EcomFlowLedger, { AccountTable } from './EcomFlowLedger.jsx'
import Preparation from './EcomPreparation.jsx'
import OrderDrawer from './EcomOrderChain.jsx'
import { requestJson, wb, query, post, money, count, percent, useResource } from './ecomWorkbenchApi.js'
import './ecomWorkbench.css'
import overviewIcon from '../assets/ecom-nav/chart-bar.svg'
import prepareIcon from '../assets/ecom-nav/notes.svg'
import incomeIcon from '../assets/ecom-nav/clipboard-check.svg'
import cashIcon from '../assets/ecom-nav/coin-yuan.svg'
import flowsIcon from '../assets/ecom-nav/receipt.svg'

// Shops are resolved from source-backed base data, never a hardcoded display name.
const NAV=[
  ['overview','总览','经营与账户概览',overviewIcon],
  ['prepare','数据准备','按店铺准备资料',prepareIcon],
  ['income','收入确认','发货与应收核对',incomeIcon],
  ['cash','收款核销','支付宝与聚合账户',cashIcon],
  ['flows','账户流水','全字段合并查找',flowsIcon],
]
const BUSINESS={ '':'全部订单',normal:'正常销售',ufirst:'U先试用装',mixed:'混合订单',review:'待确认分类',unknown:'待分类' }
export const Notice=({children}) => children ? <div className="ew-notice" role="status">{children}</div> : null
const Panel=({title,children,extra}) => <section className="ew-panel"><header><h2>{title}</h2>{extra}</header>{children}</section>
function Metric({label,value,sub,onClick}) {const Tag=onClick?'button':'div';return <Tag className="ew-metric" onClick={onClick}><span>{label}</span><strong>{value}</strong><small>{sub}</small></Tag>}

export default function EcomWorkbench({user,onNav,initialScreen='overview'}) {
  const [screen,setScreen]=useState(initialScreen),[period,setPeriod]=useState('2026-08'),[shop,setShop]=useState(''),[business,setBusiness]=useState(''),[flag,setFlag]=useState(''),[revision,setRevision]=useState(0),[drawer,setDrawer]=useState(null),[message,setMessage]=useState(''),[flowFilter,setFlowFilter]=useState({})
  const result=useResource(screen==='overview'?`/api/ec/workbench/overview?${query({period,business})}`:null,revision)
  const shopResult=useResource(`/api/ec/workbench/shops?${query({period:screen==='prepare'?period:''})}`,revision)
  const overview=result.data
  const overviewPending=overview?.shops?.some(s=>['pending','building'].includes(s.result?.status))
  useEffect(()=>{if(screen!=='overview'||!overviewPending)return;const timer=setInterval(()=>setRevision(v=>v+1),5000);return()=>clearInterval(timer)},[screen,overviewPending])
  const shops=useMemo(()=>{
    const base=shopResult.data?.shops?.length?shopResult.data.shops:(overview?.shops||[])
    const summaries=new Map((overview?.shops||[]).map(s=>[s.id,s]))
    return base.map(s=>({...s,...(summaries.get(s.id)||{})}))
  },[shopResult.data,overview?.shops])
  const canEdit=user?.role==='admin'||user?.perms?.ec_settle_upload
  useEffect(() => {if (shops.length&&(!shop||!shops.some(s=>s.id===shop))) setShop(shops.find(s=>s.platform==='天猫'&&s.available)?.id||shops.find(s=>s.platform==='天猫')?.id||shops[0].id)},[shops,shop])
  useEffect(() => {setScreen(initialScreen)},[initialScreen])
  const changePeriod=e=>{setPeriod(e.target.value);setDrawer(null);setFlag('')}
  const drill=(id,filter='',next='income')=>{setShop(id);setFlag(filter);setScreen(next)}
  const openLedger=(filter={})=>{setFlowFilter(filter);setDrawer(null);setScreen('flows')}
  return <div className="ew-workbench">
    <header className="ew-header"><div><h1>电商对账工作台</h1><p>平台事实 → 发货确认 → 应收核对 → 账户收款</p></div><div className="ew-header-tools"><span className="ew-readonly">金蝶只读</span>{screen!=='flows' && <label>结算期间<input aria-label="结算期间" type="month" value={period} onChange={changePeriod}/></label>}<button onClick={()=>setRevision(v=>v+1)}>刷新数据</button><button className="ew-link ew-basic" onClick={()=>onNav?.('ecombase')}>基础资料</button></div></header>
    <nav className="ew-nav ew-stage-nav" aria-label="电商工作流">{NAV.map(([key,label,description,icon])=><button key={key} aria-label={label} aria-current={screen===key?'page':undefined} className={screen===key?'active':''} onClick={()=>{setScreen(key);setFlag('')}}><span className="ew-stage-symbol" aria-hidden="true"><span className="ew-stage-icon" style={{maskImage:`url(${JSON.stringify(icon)})`,WebkitMaskImage:`url(${JSON.stringify(icon)})`}}/></span><span className="ew-stage-copy"><strong>{label}</strong><small>{description}</small></span></button>)}</nav>
    {screen==='overview' && <div className="ew-filter"><span>业务范围</span>{Object.entries(BUSINESS).slice(0,4).map(([key,label])=><button key={key} className={business===key?'active':''} onClick={()=>setBusiness(key)}>{label}</button>)}<span className="ew-muted">覆盖 {overview?.coverage?.available ?? '—'} / {overview?.coverage?.total ?? '—'} 家店铺 · 缺数据不计作零</span></div>}
    <Notice>{message||(screen==='overview'?result.error:shopResult.error)}</Notice>
    {screen==='flows' ? <EcomFlowLedger refreshToken={revision} onChanged={()=>setRevision(v=>v+1)} user={user} period={flowFilter.q?'':period} initialQuery={flowFilter.q||''} initialAccountId={flowFilter.account_id||''}/> : screen==='overview' ? <Overview data={overview} loading={result.loading} revision={revision} drill={drill} openCash={id=>openLedger({account_id:typeof id==='string'?id:''})}/> : screen==='prepare' ? <div className="ew-layout"><aside className="ew-shops"><h2>店铺 <small>{shops.length} 家</small></h2>{shops.map(s=>{const readiness=shopResult.loading?'linked':s.readiness||'linked',meta=shopResult.loading?'正在读取准备进度…':s.required?`齐套 ${s.ready}/${s.required} · 文件 ${s.files}`:'待配置所需资料';return <button key={s.id} className={shop===s.id?'active':''} onClick={()=>{setShop(s.id);setFlag('');setDrawer(null)}}><span><i className={readiness}/>{s.name}</span><small>{meta}</small></button>})}</aside><main className="ew-main">{shop&&<Preparation key={shop+period} period={period} shop={shop} shopInfo={shops.find(s=>s.id===shop)} revision={revision} canEdit={canEdit} refresh={()=>setRevision(v=>v+1)} notify={setMessage} onBasic={()=>onNav?.('ecombase')}/>}</main></div> : shop&&<OrderWorkspace key={period+screen} user={user} period={period} shop={shop} shops={shops} setShop={setShop} mode={screen} business={business} setBusiness={setBusiness} flag={flag} setFlag={setFlag} revision={revision} openOrder={(no,tab='flow',version='')=>setDrawer({no,tab,version})}/>}
    <p className="ew-footnote">GSV = 支付成功金额 − 已成功退款，未扣平台费用。订单按创建月归属；收款以支付宝 / 聚合账户入账为止，不代表已提现到公司银行。今晚金蝶仅查询，禁止新增、下推、提交或审核。</p>
    {drawer && <OrderDrawer period={period} shop={shop} orderNo={drawer.no} version={drawer.version} initialTab={drawer.tab} onClose={()=>setDrawer(null)} onOpenLedger={()=>openLedger({q:drawer.no})}/>}
  </div>
}

function Overview({data,loading,revision,drill,openCash}) {
  const accounts=useResource('/api/ec/flows/accounts',revision),m=data?.totals
  return <div className="ew-overview">{data?.shops?.some(s=>s.result?.stale||['pending','building','error'].includes(s.result?.status))&&<Notice>部分店铺核算结果正在更新或需要重试；总览仅显示各店已保存的完整版本。</Notice>}<div className="ew-metrics"><Metric label="订单数" value={count(m?.orders)} sub="按创建月份归属"/><Metric label="GMV · 支付成功金额" value={`¥ ${money(m?.gmv)}`} sub="仅支付成功订单"/><Metric label="GSV · 净成交金额" value={`¥ ${money(m?.gsv)}`} sub="扣退款 · 未扣平台费用"/><Metric label="平台费用" value={`¥ ${money(m?.fees)}`} sub="按已匹配订单流水归集"/></div>
    <div className="ew-attention"><div>仅退款<strong>¥ {money(m?.refund_only)}</strong><small>金额占 GMV {percent(m?.refund_only_rate)}</small></div><div>退货退款<strong>¥ {money(m?.return_refund)}</strong><small>金额占 GMV {percent(m?.return_refund_rate)}</small></div><div>未发货<strong>{count(m?.unshipped_count)} 笔 / ¥ {money(m?.unshipped_amount)}</strong><small>已支付、尚未发货的有效订单</small></div><div>待人工介入<strong>{count(m?.manual)} 笔</strong><small>按订单去重，不重复累加问题数</small></div></div>
    {m?.unknown_refund>0 && <Notice>尚有 ¥ {money(m.unknown_refund)} 退款未取得明确售后类型，暂不归入“仅退款”或“退货退款”。请在数据准备补充售后明细。</Notice>}
    <Panel title="店铺经营与核对概览" extra={<span className="ew-muted">总览 → 店铺 → 订单证据</span>}><div className="ew-scroll"><table><thead><tr><th>店铺 / 齐套状态</th><th>订单数</th><th>GMV</th><th>GSV</th><th>仅退款 / 比例</th><th>退货退款 / 比例</th><th>费用</th><th>未发货 / 金额</th><th>待人工介入</th></tr></thead><tbody>{data?.shops?.map(s=><tr key={s.id}><td><button className="ew-link" onClick={()=>drill(s.id)}><i className={'ew-dot '+s.readiness}/>{s.name}</button><small>{s.ready}/{s.required} 类 · {s.files} 个文件</small></td><td>{count(s.metrics?.orders)}</td><td>{money(s.metrics?.gmv)}</td><td>{money(s.metrics?.gsv)}</td><td><button className="ew-link" onClick={()=>drill(s.id,'refund_only')}>{money(s.metrics?.refund_only)}</button><small>{percent(s.metrics?.refund_only_rate)}</small></td><td><button className="ew-link" onClick={()=>drill(s.id,'return_refund')}>{money(s.metrics?.return_refund)}</button><small>{percent(s.metrics?.return_refund_rate)}</small></td><td><button className="ew-link" onClick={()=>drill(s.id,'fees','cash')}>{money(s.metrics?.fees)}</button></td><td><button className="ew-link" onClick={()=>drill(s.id,'unshipped')}>{count(s.metrics?.unshipped_count)} 笔</button><small>{money(s.metrics?.unshipped_amount)}</small></td><td><button className="ew-link" onClick={()=>drill(s.id,'manual')}>{count(s.metrics?.manual)}</button></td></tr>)}{(!data || loading)&&<tr><td colSpan="9" className="ew-empty">{loading?'正在汇总各店铺数据…':'暂无数据'}</td></tr>}</tbody><tfoot><tr><td>已接入店铺合计</td><td>{count(m?.orders)}</td><td>{money(m?.gmv)}</td><td>{money(m?.gsv)}</td><td>{money(m?.refund_only)}</td><td>{money(m?.return_refund)}</td><td>{money(m?.fees)}</td><td>{count(m?.unshipped_count)} 笔</td><td>{count(m?.manual)}</td></tr></tfoot></table></div></Panel>
    <Panel title="资金账户余额" extra={<button className="ew-link" onClick={()=>openCash('')}>查找账户流水</button>}><Notice>{accounts.error}</Notice><AccountTable data={accounts.data} onSelect={openCash}/><p className="ew-muted ew-padding">一个账户一行；共享账户不按店铺重复汇总。余额以原文件实际字段为准，不使用净收支推算。</p></Panel>
  </div>
}

const SHIPMENT={shipped:'已发货',unshipped:'已下单未发货',unknown:'发货待确认'}
const REFUND_STATE={none:'无退款',full_only:'全额仅退款',partial_only:'部分仅退款',full_return:'全额退货退款',partial_return:'部分退货退款',full_mixed:'全额混合退款',partial_mixed:'部分混合退款',unknown:'退款类型待确认'}
const SETTLEMENT={settled:'已结算',partial:'部分结算',review:'结算待核对',unknown:'待结算 / 待补流水'}
function FeeCell({row,onOpen}) {
  return <>{[['常规',row.routine_fee],['佣金',row.commission_fee]].map(([label,value])=><button type="button" className="ew-fee-line" key={label} onClick={onOpen} aria-label={`查看${label}费用 ${row.order_no}`}><span>{label}</span><strong>{value==null?'—':`¥${money(value)}`}</strong><small>{value==null||!row.paid?'—':`${(value/row.paid*100).toFixed(2)}%`}</small></button>)}{row.unclassified_fee!==0&&row.unclassified_fee!=null&&<small className="ew-issue">待分类 ¥{money(row.unclassified_fee)}</small>}</>
}
function OrderWorkspace({user,period,shop,shops,setShop,mode,business,setBusiness,flag,setFlag,revision,openOrder:openOrderAtVersion}) {
  const [q,setQ]=useState(''),[search,setSearch]=useState(''),[channel,setChannel]=useState(''),[cashTab,setCashTab]=useState('orders')
  const [dates,setDates]=useState({start_date:'',end_date:''}),[dateQuery,setDateQuery]=useState({start_date:'',end_date:''}),[shipment,setShipment]=useState(''),[refundState,setRefundState]=useState(''),[settlement,setSettlement]=useState(''),[jump,setJump]=useState('')
  const [navigation,setNavigation]=useState({key:'',page:1,version:''}),[reload,setReload]=useState(0),[tick,setTick]=useState(0),[actionError,setActionError]=useState('')
  const key=JSON.stringify([period,shop,business,channel,flag,search,revision,reload,dateQuery,shipment,refundState,settlement])
  const page=navigation.key===key?navigation.page:1,version=navigation.key===key?navigation.version:''
  const result=useResource(cashTab==='orders'?`/api/ec/workbench/orders?${query({period,shop,business,channel,flag,q:search,page,version,...dateQuery,shipment,refund_state:refundState,settlement})}`:null,`${revision}:${reload}:${tick}`,key),data=result.data
  const setPage=value=>setNavigation({key,page:typeof value==='function'?value(data?.page||page):value,version:data?.result?.build_id||''})
  const openOrder=(no,tab='flow')=>openOrderAtVersion(no,tab,data?.result?.build_id||'')
  const state=data?.result
  const pending=state?.status==='pending'||state?.status==='building'
  useEffect(()=>{if(!pending)return;const timer=setInterval(()=>setTick(v=>v+1),3000);return()=>clearInterval(timer)},[pending])
  const retry=async()=>{try{setActionError('');await wb('results/refresh',post({period,shop}));setReload(v=>v+1)}catch(e){setActionError(e.message)}}
  const canEdit=user?.role==='admin'||user?.perms?.ec_settle_upload
  const filter=(label,value,setter,options)=><label><span>{label}</span><select aria-label={label} value={value} onChange={e=>setter(e.target.value)}><option value="">全部</option>{Object.entries(options).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
  return <><form className="ew-order-filters ew-order-filters-expanded" onSubmit={e=>{e.preventDefault();setSearch(q);setDateQuery({...dates});setPage(1);setJump('')}}>
    <label><span>店铺</span><select aria-label="店铺" value={shop} onChange={e=>setShop(e.target.value)}>{shops.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    {filter('业务类型',business,setBusiness,Object.fromEntries(Object.entries(BUSINESS).filter(([k])=>k)))}
    {filter('发货状态',shipment,setShipment,SHIPMENT)}{filter('退款状态',refundState,setRefundState,REFUND_STATE)}{filter('结算状态',settlement,setSettlement,SETTLEMENT)}
    {filter('支付渠道',channel,setChannel,{alipay:'支付宝',fund:'聚合账户',multiple:'多渠道',pending:'待确认渠道'})}
    {filter('核对提示',flag,setFlag,{manual:'待人工介入',unshipped:'未发货',ar_missing:'已发货未匹配应收',refund_only:'仅退款',return_refund:'退货退款',unknown_refund:'退款类型待分类',fees:'有平台费用'})}
    <label><span>订单创建 · 开始日期</span><input aria-label="订单创建开始日期" type="date" value={dates.start_date} max={dates.end_date||undefined} onChange={e=>setDates({...dates,start_date:e.target.value})}/></label>
    <label><span>订单创建 · 截止日期</span><input aria-label="订单创建截止日期" type="date" value={dates.end_date} min={dates.start_date||undefined} onChange={e=>setDates({...dates,end_date:e.target.value})}/></label>
    <label className="ew-order-search"><span>模糊搜索</span><input aria-label="模糊搜索" value={q} onChange={e=>setQ(e.target.value)} placeholder="订单号、商品或 SKU"/></label><button className="ew-primary">查询</button>
    <button type="button" onClick={()=>{setQ('');setSearch('');setBusiness('');setChannel('');setFlag('');setShipment('');setRefundState('');setSettlement('');setDates({start_date:'',end_date:''});setDateQuery({start_date:'',end_date:''});setPage(1);setJump('')}}>清空筛选</button>
    <span className="ew-muted">{count(data?.total)} 笔 · 日期筛选限本结算期 · 费用率＝对应费用÷实付金额</span>
    </form>{mode==='cash'&&<div className="ew-subnav">{[['orders','订单收款'],['preview','凭证预览'],['history','历史核销记录']].map(([key,label])=><button key={key} className={cashTab===key?'active':''} onClick={()=>setCashTab(key)}>{label}</button>)}</div>}
    {cashTab==='flows'?<EcomFlowLedger user={user} period={period}/>:cashTab==='preview'?<VoucherPreview user={user} period={period} shop={shop} revision={revision}/>:cashTab==='history'?<History period={period} shop={shop}/>:<><Notice>{result.error||actionError}</Notice>
    {state&&<div className="ew-result-status" role="status">{pending?(state.build_id?'后台更新中，当前仍显示上一完整版本。':'正在后台首次生成核算结果，可离开页面，完成后自动展示。'):state.status==='error'?state.error:state.status==='missing'?'尚无已保存核算结果，请先准备资料。':state.stale?'已有新版核算结果；本次翻页继续使用原版本，避免混页。':`已保存 ${count(state.stored_rows)} 笔 · 版本 ${state.build_id?.slice(0,8)} · ${state.built_at||''}`}{state.stale&&state.status==='ready'&&<button onClick={()=>setReload(v=>v+1)}>查看新版</button>}{['error','missing'].includes(state.status)&&<button disabled={!canEdit} onClick={retry}>{state.status==='error'?'重试生成':'生成核算结果'}</button>}</div>}
    <div className="ew-scroll ew-panel" aria-busy={result.loading}><table className="ew-order-table ew-order-table-expanded"><thead><tr><th>店铺简称</th><th>业务类型 / 订单状态</th><th>订单号</th><th className="ew-num">订单金额</th><th className="ew-num">实付金额</th><th className="ew-num">退款金额</th><th>费用金额 / 费率</th><th className="ew-num">金蝶应收金额</th><th title="按已关联账户流水核对货款与退款，不代表平台费用已完整或银行到账">结算状态</th><th>核对提示</th></tr></thead><tbody>{data?.rows?.map(r=><tr key={r.order_no}>
      <td title={shops.find(s=>s.id===shop)?.name||shop}>{shops.find(s=>s.id===shop)?.name||shop}</td>
      <td><span className="ew-order-pill">{r.business_label}丨{SHIPMENT[r.shipment_state]||'发货待确认'}</span><small>{r.refund>0?(REFUND_STATE[r.refund_state]||'退款类型待确认'):r.status!==SHIPMENT[r.shipment_state]?r.status:null}</small></td>
      <td><button className="ew-link ew-order-id" onClick={()=>openOrder(r.order_no)}>{r.order_no}</button></td><td className="ew-num">{money(r.order_amount)}</td><td className="ew-num ew-paid">{money(r.paid)}</td><td className={`ew-num ${r.refund===0?'ew-zero':''}`}>{money(r.refund)}</td>
      <td><FeeCell row={r} onOpen={()=>openOrder(r.order_no,'cash')}/></td>
      <td className="ew-num">{r.business_type==='ufirst'?'不适用':r.ar_amount==null?'未匹配':money(r.ar_amount)}{r.business_type!=='ufirst'&&r.ar_diff!=null&&r.ar_diff!==0?<small className="ew-difference">差额 {r.ar_diff>0?'+':''}{money(r.ar_diff)}</small>:r.business_type!=='ufirst'&&r.ar_amount!=null&&r.ar_diff==null?<small>比较依据待补</small>:null}</td>
      <td><span className="ew-order-pill">{SETTLEMENT[r.settlement_state]||SETTLEMENT.unknown}</span><small>{r.destination==='待结算 / 待补流水'?'渠道待确认':r.destination}</small></td>
      <td>{r.issues?.length?<><span className="ew-issue">{r.issues[0]}</span>{r.issues.length>1&&<details><summary>另 {r.issues.length-1} 项</summary>{r.issues.slice(1).map((issue,i)=><small key={i}>{issue}</small>)}</details>}</>:'—'}</td>
    </tr>)}{!data?.rows?.length&&<tr><td colSpan="10" className="ew-empty">{result.loading?'正在读取订单…':pending?'核算结果正在后台生成…':result.error?'读取失败，请重试':state?.status==='error'?'核算结果生成失败，原始资料保留':state?.status==='missing'?'尚无已保存结果':'当前筛选下没有订单'}</td></tr>}</tbody></table></div>
    <div className="ew-pagination"><span>{result.loading&&data?.rows?.length?`正在读取第 ${page} 页…`:'每页 30 笔 · 数据库分页'}</span><button disabled={page<=1||result.loading} onClick={()=>setPage(p=>p-1)}>上一页</button>{data?.page??page} / {data?.pages??'—'}<button disabled={!data?.pages||(data?.page??page)>=data.pages||result.loading} onClick={()=>setPage(p=>p+1)}>下一页</button>
      <form className="ew-page-jump" onSubmit={e=>{e.preventDefault();const n=Number(jump);if(Number.isInteger(n)&&n>=1&&n<=data?.pages){setPage(n);setJump('')}}}><label>跳至 <input aria-label="跳转页码" type="number" min="1" max={data?.pages||1} step="1" required value={jump} onChange={e=>setJump(e.target.value)} disabled={result.loading||!data?.pages}/> 页</label><button disabled={result.loading||!data?.pages}>跳转</button></form>
    </div></>}
  </>
}

function VoucherPreview({user,period,shop,revision}) {
  const [syncRevision,setSyncRevision]=useState(0),[syncMessage,setSyncMessage]=useState('')
  const canEdit=user?.role==='admin'||user?.perms?.ec_settle_upload
  const sync=async()=>{try{await wb('kingdee-docs/refresh',post({period,shop}));setSyncMessage('已发起只读查询，完成后刷新预览。')}catch(e){setSyncMessage(e.message)}}
  const result=useResource(`/api/ec/workbench/preview?${query({period,shop})}`,`${revision}:${syncRevision}`),data=result.data
  const [onlyU,setOnlyU]=useState(true)
  return <Panel title="金蝶凭证只读预览" extra={<div><button disabled={!canEdit} onClick={sync}>读取历史凭证</button><button onClick={()=>setSyncRevision(v=>v+1)}>刷新预览</button></div>}><Notice>{syncMessage||result.error||data?.notice}</Notice><div className="ew-tools"><label><input type="checkbox" checked={onlyU} onChange={e=>setOnlyU(e.target.checked)}/> 只看 U先相关凭证</label><button disabled title="今晚金蝶只读">下推收款单 / 入账（未开放）</button></div>{data?.existing_vouchers?.filter(v=>!onlyU||v.ufirst).map(v=><details className="ew-voucher" key={v.bill} open><summary>{v.number} · {v.date?.slice(0,10)} · 状态 {v.status} · 单据编号 {v.bill}</summary><p className="ew-muted">摘要提及 {v.receipt_references?.join('、')||'无收款单号'}；仅为参考，尚未证明订单与收款单来源关系。</p><div className="ew-scroll"><table><thead><tr><th>摘要</th><th>科目</th><th>借方</th><th>贷方</th></tr></thead><tbody>{v.lines?.map((l,i)=><tr key={i}><td>{l.memo}</td><td>{l.account} {l.account_name}</td><td>{money(l.debit)}</td><td>{money(l.credit)}</td></tr>)}</tbody></table></div></details>)}{!data?.existing_vouchers?.length&&<p className="ew-empty">尚未读取参考凭证。可点击上方“读取历史凭证”，仅查询、不写入。</p>}<div className="ew-padding"><strong>新凭证预览前待确认</strong><ul>{data?.draft?.checks?.map(c=><li key={c}>{c}</li>)}</ul></div></Panel>
}
function History({period,shop}) {
  const result=useResource(`/api/ec/workbench/history?${query({period,shop})}`)
  const buckets={ok:'已匹配',ufirst:'U先汇总',crossed:'串单复核',carry:'跨期调节',real:'真实差异'}
  return <Panel title="历史订单核销记录"><Notice>{result.error}</Notice><p className="ew-muted ew-padding">这里保留原订单核销分桶，与账户流水业务分桶是两套不同的检查。</p><div className="ew-scroll"><table><thead><tr><th>批次</th><th>状态 / 时间</th><th>原核销结果</th></tr></thead><tbody>{result.data?.rows?.map(r=><tr key={r.id}><td>#{r.id}</td><td>{r.status}<small>{r.ts}</small></td><td>{Object.entries(r.stats?.buckets||{}).map(([key,v])=><span className="ew-history-bucket" key={key}>{buckets[key]||key} {count(v.cnt)} 笔</span>)}</td></tr>)}{!result.data?.rows?.length&&<tr><td colSpan="3" className="ew-empty">本店本期暂无历史核销批次</td></tr>}</tbody></table></div></Panel>
}
