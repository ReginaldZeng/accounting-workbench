import React, { useEffect, useState } from 'react'
import { requestJson, query, money, count, useResource } from './ecomWorkbenchApi.js'
import { ChainRow } from './EcomOrderChain.jsx'

// V2.811 抖音月结：动账明细 × 金蝶应收逐单对 → 哪些能核、哪些要看 → 收款单草稿。只读金蝶。
const BASE='/api/ec/douyin'
const WS={A:'未核销',B:'部分核销',C:'已核销'}

export default function EcomDouyinSettle({period,shop,shops,setShop,canEdit,revision,notify,onPrepare}) {
  const [tick,setTick]=useState(0),[cat,setCat]=useState(''),[q,setQ]=useState(''),[search,setSearch]=useState(''),[page,setPage]=useState(1),[error,setError]=useState(''),[showMissing,setShowMissing]=useState(false),[proof,setProof]=useState('')
  const top=useResource(`${BASE}/settle?${query({period,shop})}`,`${revision}:${tick}`,`dy:${period}:${shop}`),data=top.data,r=data?.result,ar=data?.ar
  const refreshing=!!data?.refreshing
  useEffect(()=>{if(!refreshing)return;const timer=setInterval(()=>setTick(v=>v+1),3000);return()=>clearInterval(timer)},[refreshing])
  useEffect(()=>{setCat('');setQ('');setSearch('');setPage(1);setShowMissing(false);setProof('')},[period,shop])
  const key=JSON.stringify([period,shop,cat,search,ar?.ts])
  const list=useResource(r?`${BASE}/bills?${query({period,shop,cat,q:search,page})}`:null,ar?.ts||'',key),bills=list.data
  const lost=useResource(r&&showMissing?`${BASE}/missing?${query({period,shop})}`:null,ar?.ts||'')
  const sync=async()=>{try{setError('');const body=new FormData();body.append('period',period);body.append('shop',shop);await requestJson(`${BASE}/ar-refresh`,{method:'POST',body});notify?.('已发起金蝶只读查询，完成后自动更新。');setTick(v=>v+1)}catch(e){setError(e.message)}}
  const src=data?.sources||{},hasSettle=src.dy_settle>0,hasLedger=src.dy_ledger>0,hasOrders=src.dy_orders>0,hasPlatform=src.dy_platform>0
  const bar=<div className="ew-result-status" role="status">
    <label style={{display:'flex',alignItems:'center',gap:8}}>店铺<select aria-label="店铺" value={shop} onChange={e=>setShop(e.target.value)}>{shops.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <span className="ew-readonly">金蝶只读</span>
    <span>{refreshing?'正在从金蝶读取这家店的应收单，通常十几秒…':ar?`应收上次同步 ${ar.ts} · ${ar.operator} · ${count(ar.bills)} 张（${ar.since} 起）`:'还没有同步这家店的金蝶应收'}</span>
    <button disabled={!canEdit||refreshing} title={canEdit?'只查询金蝶，不改任何单据':'需要电商对账的上传/跑批权限'} onClick={sync}>{refreshing?'读取中…':'只读同步金蝶应收'}</button>
    {r&&<button onClick={()=>window.open(`${BASE}/export?${query({period,shop})}`,'_blank')} title="收款单草稿 + 各类应收清单；“可核销”那页就是下推用的源单">导出 Excel</button>}
  </div>
  const notes=<>{(error||data?.error||top.error)&&<div className="ew-notice" role="status">{error||data?.error||top.error}</div>}
    {data&&!hasSettle&&<div className="ew-notice" role="status">{period} 还没有抖音动账明细（订单维度），对不了账。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}
    {data&&hasSettle&&!hasLedger&&<div className="ew-notice" role="status">{period} 还没有带余额的账户流水：到账金额先按订单维度明细算，余额核对不了。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}
    {data&&hasSettle&&!hasOrders&&<div className="ew-notice" role="status">{period} 还没有旺店通订单明细：合单发货的订单认不出来，会被报成“金额对不上”或“金蝶里没有应收”。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}
    {data&&hasSettle&&!hasPlatform&&<div className="ew-notice" role="status">{period} 还没有抖店订单导出：没结算的应收看不出在平台上是等结算、售后中还是已关闭（不影响对账和下推）。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}</>
  if(!r) return <>{bar}{notes}<section className="ew-panel"><p className="ew-empty">{top.loading&&!data?'正在读取…':refreshing?'正在从金蝶读取，读完自动显示。':'导入抖音动账明细后，点上方“只读同步金蝶应收”，系统逐单对出哪些能核销。'}</p></section></>
  const d=r.draft,bal=r.balance,book=r.book||{},catOf=k=>r.categories.find(c=>c.key===k)||{count:0,amount:0}
  const booked=book.debit||book.credit,miss=r.buckets.find(x=>x.key==='missing'),others=d.deductions.filter(x=>x.kind==='other'),fees=d.deductions.filter(x=>x.kind==='fee')
  const held={count:r.held.reduce((n,x)=>n+x.count,0),amount:r.held.reduce((n,x)=>n+x.amount,0)}
  const jump=id=>document.getElementById(id)?.scrollIntoView({behavior:'smooth',block:'start'})
  const pick=k=>{setCat(cat===k?'':k);setPage(1);jump('dy-bills')}
  const todo=[['mismatch','金额对不上',catOf('mismatch'),'两边说法不一样，每张写明了是哪种情况'],
    ['hold','金额对得上，但系统不推',held,r.held.map(x=>x.label).join('；')],
    ['overdue',`发货超过 ${r.overdue_days} 天还没结算`,catOf('overdue'),'查买家是不是一直没确认收货，或者已经退款'],
    ['no_order','应收单上没有平台订单号',catOf('no_order'),'系统对不了，要人看']].filter(x=>x[2].count)
  return <>{bar}{notes}
    <div className="ew-closeout ew-open-cards ew-dy-cards">{r.categories.filter(c=>c.count||c.key!=='no_order').map(c=><button type="button" key={c.key} className={`ew-co${c.key==='ok'?' ew-co-primary':''}${cat===c.key?' on':''}`} onClick={()=>{setCat(cat===c.key?'':c.key);setPage(1)}}><span className="ew-co-lab">{c.label}</span><strong>¥ {money(c.amount)}</strong><small>{count(c.count)} 张应收 · {count(c.orders)} 单</small></button>)}</div>
    <h3 className="ew-dy-step"><b>①</b>系统替你做的<small>干净的应收，分批备成金蝶暂存收款单</small></h3>
    <PushPanel period={period} shop={shop} canEdit={canEdit} stamp={ar?.ts||''} notify={notify}/>
    <h3 className="ew-dy-step"><b>②</b>要你处理的<small>系统判断不了，或者不该由系统做的</small></h3>
    <section className="ew-panel"><div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>事项</th><th className="ew-num">数量</th><th className="ew-num">金额</th><th>说明</th><th></th></tr></thead><tbody>
      {todo.map(([k,label,c,tip])=><tr key={k}><td><strong>{label}</strong></td><td className="ew-num">{count(c.count)} 张应收</td><td className="ew-num">{money(c.amount)}</td><td className="ew-issue">{tip}</td><td><button className="ew-link" onClick={()=>pick(k)}>{cat===k?'取消筛选':'看清单'}</button></td></tr>)}
      {miss.orders>0&&<tr><td><strong>钱结进来了，金蝶里没有应收</strong></td><td className="ew-num">{count(miss.orders)} 个订单</td><td className="ew-num">{money(miss.flow)}</td><td className="ew-issue">查旺店通有没有这单、应收为什么没开</td><td><button className="ew-link" onClick={()=>setShowMissing(v=>!v)}>{showMissing?'收起':'看订单'}</button></td></tr>}
      {others.length>0&&<tr><td><strong>不挂在订单上的账户进出</strong></td><td className="ew-num">{others.length} 种</td><td className="ew-num">{money(d.other_total)}</td><td className="ew-issue">{others.slice(0,3).map(x=>x.name).join('、')}{others.length>3?' 等':''}；不在任何一张下推的收款单里，要另外入账</td><td><button className="ew-link" onClick={()=>jump('dy-flow')}>看明细</button></td></tr>}
      {!todo.length&&!miss.orders&&!others.length&&<tr><td colSpan="5" className="ew-empty">没有要人处理的</td></tr>}
    </tbody></table></div>
      {showMissing&&<div className="ew-scroll" style={{maxHeight:260}}><table className="ew-open-table"><thead><tr><th>平台订单号</th><th className="ew-num">流水应冲额</th><th>情况</th></tr></thead><tbody>{lost.data?.rows?.map(m=><tr key={m.order}><td><button className="ew-link" onClick={()=>setProof(m.order)}>{m.order}</button></td><td className="ew-num">{money(m.flow)}</td><td>{m.label}</td></tr>)}{!lost.data&&<tr><td colSpan="3" className="ew-empty">正在读取…</td></tr>}</tbody></table></div>}
    </section>
    <div id="dy-bills"/>
    <form className="ew-order-filters ew-order-filters-expanded" onSubmit={e=>{e.preventDefault();setSearch(q);setPage(1)}}>
      <label><span>分类</span><select aria-label="分类" value={cat} onChange={e=>{setCat(e.target.value);setPage(1)}}><option value="">全部未核销应收</option>{r.categories.map(c=><option key={c.key} value={c.key}>{c.label}</option>)}<option value="hold">金额对得上、但订单带红字等（系统不推）</option></select></label>
      <label className="ew-order-search"><span>模糊搜索</span><input aria-label="模糊搜索" value={q} onChange={e=>setQ(e.target.value)} placeholder="应收单号或平台订单号"/></label>
      <button className="ew-primary">查询</button><button type="button" onClick={()=>{setCat('');setQ('');setSearch('');setPage(1)}}>清空筛选</button>
    </form>
    {list.error&&<div className="ew-notice" role="status">{list.error}</div>}
    <div className="ew-scroll ew-panel" aria-busy={list.loading}><table className="ew-order-table ew-open-table"><thead><tr><th>应收单号</th><th>业务日期</th><th>平台订单号</th><th className="ew-num">应收金额</th><th className="ew-num">已核销</th><th className="ew-num">未核销</th><th>抖音结算时间</th><th className="ew-num">到账</th><th className="ew-num">结算时扣费</th><th className="ew-num" title="到账 + 结算时扣费">流水应冲额</th><th className="ew-num">差额</th><th>分类</th></tr></thead>
      <tbody>{bills?.rows?.map(b=><tr key={b.no}><td>{b.order?<button className="ew-link" title="看这张单的依据：抖音每笔动账 + 金蝶每张应收" onClick={()=>setProof(b.order)}>{b.no}</button>:b.no}<small>{WS[b.ws]||b.ws}{b.ds!=='C'&&' · 未审核'}</small></td><td>{b.date}</td><td>{b.order||<span className="ew-muted-num">—</span>}</td><td className="ew-num">{money(b.amount)}</td><td className={`ew-num ${b.written===0?'ew-zero':''}`}>{money(b.written)}</td><td className={`ew-num ${b.open<0?'ew-open-red':''}`}><strong>{money(b.open)}</strong></td><td>{b.settled_at?b.settled_at.slice(0,16):<span className="ew-muted-num">还没结算</span>}</td><td className="ew-num">{b.flow==null?'—':money(b.cash)}</td><td className="ew-num">{b.flow==null?'—':money(b.fee)}</td><td className="ew-num">{b.flow==null?'—':money(b.flow)}</td><td className={`ew-num ${b.diff?'ew-open-red':''}`}>{b.diff==null?'—':money(b.diff)}</td><td className="ew-issue">{r.categories.find(c=>c.key===b.cat)?.label}{b.merged>0&&<small>合单发货 · {b.merged} 个平台订单一起对</small>}{b.reason&&<small>{b.reason}</small>}{b.pstate&&<small>{b.pstate}</small>}</td></tr>)}
        {!bills?.rows?.length&&<tr><td colSpan="12" className="ew-empty">{list.loading?'正在读取…':'当前筛选下没有应收单'}</td></tr>}</tbody></table></div>
    <div className="ew-pagination"><span>{list.loading&&bills?`正在读取第 ${page} 页…`:`共 ${count(bills?.total)} 张 · 未核销合计 ¥ ${money(bills?.amount)} · 截至 ${r.end} · 每页 50 张 · 点应收单号看依据${hasPlatform?' · 分类下面写的平台情况是订单导出那天的，不是月底的':''}`}</span><button disabled={page<=1||list.loading} onClick={()=>setPage(p=>p-1)}>上一页</button>{bills?.page??page} / {bills?.pages??'—'}<button disabled={!bills?.pages||(bills?.page??page)>=bills.pages||list.loading} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
    <h3 className="ew-dy-step"><b>③</b>用来核对的<small>账户余额、这个月钱的进出、两边各多少</small></h3>
    <section className="ew-panel"><header><h2>账户余额核对</h2><span className="ew-muted">{bal?`流水 ${count(bal.rows)} 笔 · ${bal.first.slice(0,10)} 至 ${bal.last.slice(0,10)}`:'需要带余额的账户流水'}</span></header><div className="ew-scroll"><table className="ew-open-table"><thead><tr><th></th><th className="ew-num">期初</th><th className="ew-num">本月净变动</th><th className="ew-num">期末</th><th>说明</th></tr></thead><tbody>
      <tr><td>抖音账户流水</td><td className="ew-num">{money(bal?.open)}</td><td className="ew-num">{money(bal?.net)}</td><td className="ew-num">{money(bal?.close)}</td><td>{bal?(bal.breaks?<span className="ew-open-red">余额链有 {bal.breaks} 处接不上，流水可能不全</span>:'余额逐笔接得上'):'—'}</td></tr>
      <tr><td>金蝶账面{book.account&&<small>{book.account}</small>}</td><td className="ew-num">{money(book.open)}</td><td className="ew-num">{book.error?'—':money((book.debit||0)-(book.credit||0))}</td><td className="ew-num">{money(book.close)}</td><td>{book.error||(!bal?'—':Math.abs(book.open-bal.open)>=0.005?<span className="ew-open-red">期初和流水差 {money(book.open-bal.open)}</span>:booked?(Math.abs(book.close-bal.close)<0.005?'期初、期末都和流水一致':<span className="ew-open-red">期末和流水差 {money(book.close-bal.close)}</span>):'期初和流水一致 · 本月还没入账')}</td></tr>
    </tbody></table></div></section>
    <section className="ew-panel" id="dy-flow"><header><h2>{period} 账户进出汇总</h2><span className="ew-muted">用来和账户余额核对，不是收款单</span></header><div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>项目</th><th className="ew-num">金额</th><th>说明</th></tr></thead>
      <tbody><tr><td>货款结算到账</td><td className="ew-num">{money(d.settle_cash)}</td><td>平台已扣完费用后进账户的钱 · {count(r.coverage.settled_orders)} 个订单</td></tr>
        {others.map(x=><tr key={x.name}><td>　{x.name}</td><td className={`ew-num ${x.amount>0?'ew-open-red':''}`}>{money(-x.amount)}</td><td className="ew-muted-num">不挂在订单上，不在任何一张下推的收款单里</td></tr>)}</tbody>
      <tfoot><tr><td>本月账户净变动</td><td className="ew-num">{money(d.net)}</td><td>＝ 期末余额 − 期初余额{!d.from_ledger&&'（没有账户流水，按订单维度明细算）'}</td></tr></tfoot></table></div>
      <p className="ew-muted ew-padding" style={{paddingTop:12}}>结算时平台直接扣掉、不经过账户的费用共 ¥ {money(d.fee_total)}：{fees.map(x=>`${x.name} ${money(x.amount)}`).join('、')||'无'}。货款结算到账 + 这些费用 = 本月结算订单应冲的应收 ¥ {money(d.total-d.other_total)}，下面按两边对不对得上分开列。</p>
      <div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>本月结算的订单</th><th className="ew-num">订单数</th><th className="ew-num">流水应冲应收</th><th className="ew-num">金蝶挂着的应收</th><th className="ew-num">两边差</th></tr></thead>
        <tbody>{r.buckets.map(b=><tr key={b.key}><td>{b.label}{['written','missing'].includes(b.key)&&b.orders>0&&<> <button className="ew-link" onClick={()=>setShowMissing(v=>!v)}>{showMissing?'收起':'看订单'}</button></>}</td><td className="ew-num">{count(b.orders)}</td><td className="ew-num">{money(b.flow)}</td><td className="ew-num">{money(b.ar)}</td><td className={`ew-num ${b.key!=='ok'&&Math.abs(b.flow-b.ar)>=0.005?'ew-open-red':''}`}>{money(b.flow-b.ar)}</td></tr>)}</tbody></table></div>
    </section>
    {proof&&<OrderProof period={period} shop={shop} shopName={shops.find(s=>s.id===shop)?.name||shop} order={proof} onClose={()=>setProof('')}/>}
  </>
}

// 订单全链路（抖音）：版式照天猫那张——先给三个结论，再三方金额对账，下面按业务环节逐行列证据
const yuan=v=>v==null?'—':`¥ ${money(v)}`
function OrderProof({period,shop,shopName,order,onClose}) {
  const res=useResource(`${BASE}/order?${query({period,shop,order})}`),d=res.data
  // 应收单的物料、数量、单价、来源单据：抽屉打开后现查金蝶（只读），查不到不影响上面的对账
  const nos=d?.bills?.map(b=>b.no).join(',')||'',extra=useResource(nos?`${BASE}/order-bills?${query({period,shop,nos})}`:''),xb=extra.data?.bills||{}
  useEffect(()=>{const overflow=document.body.style.overflow;document.body.style.overflow='hidden';const key=e=>{if(e.key==='Escape')onClose()};document.addEventListener('keydown',key);return()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key)}},[onClose])
  const body=()=>{
    const diff=Math.round((d.open_total-d.flow_total)*100)/100,blue=d.bills.filter(b=>b.amount>0),red=d.bills.filter(b=>b.amount<0),sb=d.subsidy_back,opened=d.bills.filter(b=>b.ws!=='C')
    const pl=d.platform||[],plStatus=[...new Set(pl.map(x=>x.status+(x.after?' · '+x.after:'')))].join('、'),near=(a,b)=>Math.abs(a-b)<=0.02,closed=!!d.platform_closed
    const pNet=d.platform_gross==null?null:Math.round((d.platform_gross-(d.platform_unshipped||0))*100)/100      // 发了货的子订单合计：金蝶蓝字照发货开
    const full=d.paid_total!=null&&Math.abs(d.paid_total+d.subsidy_total-d.refund_total-d.fee_total-d.cash_total)<0.011
    // 平台下单金额和金蝶蓝字比：等于全部子订单、等于发了货的那部分、或被红字冲回后净额相等，都是正常的；都对不上才标红
    const blueLine=()=>{
      if(d.platform_gross==null||!blue.length) return null
      const g=d.platform_gross,u=d.platform_unshipped||0,bt=d.blue_total
      const base=`平台订单下单时值 ${money(g)}（买家应付 ${money(d.platform_pay)} ＋ 平台／达人承担 ${money(g-d.platform_pay)}）`
      if(u>0&&near(u,g)&&near(bt,g)) return red.length>0&&near(d.ar_total,0)?<span>{base}；平台上这单没发货就退款关闭了，金蝶照发货开的蓝字 {money(bt)} 已被红字全部冲回</span>:<span className="ew-open-red">{base}；平台上这单没发货就退款关闭了，金蝶却照发货开了蓝字 {money(bt)}，请核对货是否拦回、要不要补红字</span>
      if(near(bt,g)) return <span>{base}，金蝶蓝字开的也是这个数</span>
      if(u>0&&near(bt,pNet)) return <span>{base}，其中 {money(u)} 的子订单没发货就关闭、不开应收；发了货的 {money(pNet)}，金蝶蓝字开的也是这个数</span>
      if(red.length>0&&(near(d.ar_total,g)||near(d.ar_total,pNet))) return <span>{base}；金蝶蓝字合计 {money(bt)}，其中 {money(bt-d.ar_total)} 已被红字冲回，净额和平台一致</span>
      if(red.length>0&&bt>g+0.02&&near(d.ar_total,0)) return <span>{base}；金蝶蓝字合计 {money(bt)}（{blue.length} 张），已被红字全部冲回</span>
      const gap=Math.round((bt-(u>0?pNet:g))*100)/100
      return <span className="ew-open-red">{base}{u>0&&`，其中 ${money(u)} 没发货就关闭`}；金蝶蓝字开了 {money(bt)}，{gap>0?'多':'少'} {money(Math.abs(gap))}{!merged&&blue.length>1&&gap>0&&`——同一订单开了 ${blue.length} 张蓝字（换货／补发），请核对是否重复`}</span>
    }
    const settleRows=d.flows.filter(f=>f.gross!=null),after=d.flows.filter(f=>f.gross==null),ships=[...new Set(d.bills.map(b=>b.ship).filter(Boolean))],merged=d.members.length>1,w=d.wdt||[]
    const v1=!d.bills.length?{c:'crit',v:'金蝶没有应收',s:'近 4 个月没找到这个订单的应收单'}:{c:'ok',v:`已开应收 ${yuan(d.ar_total)}`,s:`蓝字 ${blue.length} 张${red.length?` · 红字 ${red.length} 张`:''}`}
    const v2=!d.bills.length?{c:'na',v:'不适用',s:'没有应收单可对'}:!opened.length?{c:'ok',v:'已核销',s:`已核销 ${yuan(d.written_total)}`}:d.settled&&Math.abs(diff)<0.005?{c:'ok',v:'对平 · 可核销',s:`未核销 ${yuan(d.open_total)} ＝ 流水应冲`}:d.settled&&d.unsettled.length?{c:'warn',v:'合单没结完',s:`合单的 ${d.members.length} 个订单里还有 ${d.unsettled.length} 个没结算`}:!d.settled&&closed&&red.length>0&&Math.abs(d.open_total)<0.005?{c:'ok',v:'红蓝已冲平',s:'平台订单已关闭、不会结算；金蝶蓝字和红字金额相等，做红蓝对冲即可'}:!d.settled&&closed&&d.later_red<0&&Math.abs(d.open_total+d.later_red)<0.005?{c:'warn',v:'平台已关闭',s:`未核销 ${yuan(d.open_total)}；金蝶 ${d.later_red_at.slice(5)} 已开红字 ${money(d.later_red)}（记在下个月），下月做红蓝对冲即可`}:!d.settled&&closed?{c:'crit',v:'平台已关闭',s:`未核销 ${yuan(d.open_total)}，平台订单已关闭，货款不会结算`}:!d.settled?{c:'warn',v:'待结算',s:`未核销 ${yuan(d.open_total)}，抖音还没结这单`}:Math.abs(diff)<0.005?{c:'ok',v:'对平 · 可核销',s:`未核销 ${yuan(d.open_total)} ＝ 流水应冲`}:{c:'crit',v:`差额 ${yuan(diff)}`,s:'金蝶未核销 ≠ 流水应冲'}
    const v3=!d.settled&&closed?{c:'na',v:'货款不会到账',s:'平台订单已关闭'}:!d.settled?{c:'warn',v:'还没到账',s:`到 ${period} 月底流水里没有这单`}:{c:after.length?'warn':'ok',v:`已到账 ${yuan(d.cash_total)}`,s:after.length?`结算后另有 ${after.length} 笔退款／调整 ${yuan(d.after_total)}`:`${d.settled_at.slice(0,16)} 结算进聚合账户`}
    const arrow1=!d.bills.length?['ec-diff','没有应收']:!d.settled||!opened.length?['ec-warn',!d.settled?(closed?'货款不结':'待结算'):'已核销']:Math.abs(diff)<0.005?['ec-ok','差 0 ✓']:['ec-diff',`差 ${money(diff)}`]
    return <>
      <div className="ew-drawer-metrics">{[[d.paid_total!=null&&!sb&&pNet!=null&&!near(d.paid_total+d.subsidy_total,pNet)?'已结算部分的订单金额':d.paid_total==null&&d.platform_unshipped>0&&pNet>0?'发了货部分的订单金额':'订单金额',sb?sb.blue:d.paid_total!=null?d.paid_total+d.subsidy_total:pNet>0?pNet:d.platform_gross],[d.paid_total!=null?'消费者实付':'买家应付',d.paid_total!=null?d.paid_total:d.platform_unshipped===0?d.platform_pay:null],[sb?'退款＋收回补贴':'退款金额',d.settled?d.refund_total+(sb?sb.back:0):null],['平台费用',d.settled?d.fee_total:null],['账户净收',d.settled?d.cash_total+d.after_total:null]].map(([label,value])=><div key={label}><small>{label}</small><strong>{yuan(value)}</strong></div>)}</div>
      <div className="ec-verdicts">{[['收入确认',v1],['应收核对',v2],['收款核对',v3]].map(([lab,v])=><div key={lab} className={`ec-vtile ec-v-${v.c}`}><small>{lab}</small><strong>{v.v}</strong><span>{v.s}</span></div>)}</div>
      <div className="ec-threeway"><div className="ec-tw-title">三方金额对账 · 金蝶未核销应收 → 流水应冲应收 → 账户到账</div><div className="ec-tw-flow">
        <div className="ec-tw-node"><small>金蝶未核销应收</small><b className={opened.length?'':'ec-tw-mute'}>{opened.length?yuan(d.open_total):d.bills.length?'已核销':'没有应收'}</b></div>
        <div className="ec-tw-arrow">▶<em className={arrow1[0]}>{arrow1[1]}</em></div>
        <div className="ec-tw-node"><small>流水应冲应收（到账 + 扣费）</small><b className={d.settled?'':'ec-tw-mute'}>{d.settled?yuan(d.flow_total):closed?'货款不结':'待结算'}</b></div>
        <div className="ec-tw-arrow">▶<em className="ec-warn">{d.settled?`平台费 ¥ ${money(d.fee_total)}`:'—'}</em></div>
        <div className="ec-tw-node"><small>账户到账</small><b className={d.settled?'':'ec-tw-mute'}>{d.settled?yuan(d.cash_total):closed?'货款不到账':'待到账'}</b></div>
      </div></div>
      <div className="ec-formula">
        <div><small>抖音这边的钱是这么算的</small>{d.settled&&sb?<>
            <span>下单时：买家实付 {money(d.paid_total)} ＋ 平台补贴 {money(sb.before)} ＝ {money(sb.blue)}（和金蝶蓝字一样）</span>
            <span>结算前退了一部分：退给买家 {money(d.refund_total)}，平台补贴按同样比例收回 {money(sb.back)}（只剩 {money(d.subsidy_total)}）</span>
            <span>最后留下 {money(sb.blue)} － {money(d.refund_total)} － {money(sb.back)} ＝ <b>{money(sb.keep)}</b>，再扣平台费用 {money(d.fee_total)} ＝ <b>到账 {money(d.cash_total)}</b></span></>
          :d.settled?(full
          ?<span>买家实付 {money(d.paid_total)} ＋ 平台等补贴 {money(d.subsidy_total)} － 结算时退款 {money(d.refund_total)} － 平台扣费 {money(d.fee_total)} ＝ <b>到账 {money(d.cash_total)}</b></span>
          :<span>到账 {money(d.cash_total)} ＋ 平台扣费 {money(d.fee_total)} ＝ 应冲应收 {money(d.flow_total)}{d.refund_total>0&&`（结算时已退款 ${money(d.refund_total)}）`}</span>):<span className="ew-muted-num">{closed?'平台订单已关闭，这单不会结算':'这单还没结算'}</span>}
          {d.settled&&!sb&&full&&<span>所以应该冲掉的应收 ＝ 到账 {money(d.cash_total)} ＋ 扣费 {money(d.fee_total)} ＝ <b>{money(d.flow_total)}</b></span>}</div>
        <div><small>金蝶这边挂着的应收</small>{d.bills.length?<span>{[blue.length>0&&`蓝字 ${money(blue.reduce((n,b)=>n+b.open,0))}`,red.length>0&&`红字 ${money(red.reduce((n,b)=>n+b.open,0))}`].filter(Boolean).join(' ＋ ')} ＝ <b>未核销 {money(d.open_total)}</b>{d.written_total!==0&&`（已核销 ${money(d.written_total)}）`}</span>:<span className="ew-muted-num">没有应收单</span>}
          {sb&&<span className={Math.abs(sb.red-sb.red_should)>=0.015?'ew-open-red':''}>蓝字没错。红字该冲 {money(sb.red_should)}（退给买家的＋收回的补贴），{red.length===0?'实际还没开红字':Math.abs(sb.red-sb.red_should)<0.015?'实际也是这么冲的':`实际冲了 ${money(sb.red)}，${sb.red<sb.red_should?'多':'少'}冲 ${money(Math.abs(sb.red-sb.red_should))}`}</span>}
          {blueLine()}
          {d.settled&&d.bills.length>0&&(opened.length?<span className={Math.abs(diff)>=0.005?'ew-open-red':''}>{Math.abs(diff)<0.005?'两边相等':`两边差 ${money(diff)}（金蝶 − 抖音）`}</span>:<span>应收已经全部核销</span>)}</div>
      </div>
      <div className="ec-chain-badges">{d.categories.map(c=><span key={c}>{c}</span>)}{d.btype&&<span>{d.btype}</span>}{merged&&<span>合单发货 · {d.members.length} 个平台订单</span>}</div>
      {merged&&<p className="ec-chain-note">这 {d.members.length} 个平台订单在旺店通合成一张单发货，金蝶应收照合并后的单开、只记了其中一个订单号，所以放在一起对：{d.members.join('、')}。</p>}
      {d.reason&&<div className="ew-notice" role="status">{d.reason}</div>}
      {d.platform_state&&<div className="ew-notice" role="status">{d.platform_state}{!d.platform_state.includes('订单导出')&&'（按订单导出那一刻的状态，不是月底的）'}</div>}
      <p className="ec-chain-note">按业务环节排列，不代表实际时间先后。金额只覆盖已上传的动账明细；到账指结算进抖音聚合账户，不等于已提现到银行。{d.settled&&d.paid_total==null&&' 订单金额、实付、下单时间要重新上传一次动账明细（订单维度）才显示。'}</p>
      <div className="ew-scroll ec-chain-scroll"><table className="ec-chain-table"><thead><tr><th>业务环节</th><th>日期／时间</th><th>关联单据／账户</th><th>金额／数量</th><th>核对状态</th></tr></thead><tbody>
        <ChainRow name="平台订单与支付" detailLabel="平台订单明细" initialOpen={pl.length>0&&pl.length<=6} date={<><small>下单</small><span>{d.platform_at||d.ordered_at||'未取得'}</span></>} summary={<>{merged?d.members.map(o=><span key={o} style={{display:'block'}}>{o}</span>):order}{d.goods.map(g=><small key={g}>{g}</small>)}</>} amount={d.platform_gross!=null?<>下单时订单金额 ¥ {money(d.platform_gross)}<small>买家应付 ¥ {money(d.platform_pay)} · 平台／达人承担 ¥ {money(d.platform_gross-d.platform_pay)}</small></>:d.paid_total==null?null:<>实付 ¥ {money(d.paid_total)}<small>平台／达人等补贴 ¥ {money(d.subsidy_total)}</small></>} status={pl.length?plStatus:d.settled?'来自抖音动账明细（平台订单明细未导入）':'流水里还没有这单'}>
          {pl.length>0&&<div className="ew-scroll"><table className="ec-bill-lines"><thead><tr><th>子订单号</th><th>商品</th><th>数量</th><th>买家应付</th><th>平台承担</th><th>达人承担</th><th>商家优惠</th><th>订单状态</th><th>发货／确认收货</th><th>商家备注</th></tr></thead><tbody>{pl.map(x=><tr key={x.id}>
            <td>{x.id}{merged&&<small>主订单 {x.order}</small>}</td><td className="ec-wrap">{x.goods}<small>{[x.spec,x.code].filter(Boolean).join(' · ')}</small></td><td>{x.qty}</td><td>{money(x.pay)}</td><td>{money(x.plat)}</td><td>{money(x.kol)}</td><td>{money(x.shop)}</td>
            <td>{x.status}{x.status==='已关闭'&&!x.ship&&<small>平台上没发货就关闭</small>}{x.after&&<small>售后：{x.after}</small>}{x.cancel&&<small>{x.cancel}</small>}</td><td>{x.ship?x.ship.slice(5,16):'未发货'}<small>{x.done?`${x.done.slice(5,16)} 确认收货`:'未确认收货'}</small></td><td className="ec-wrap">{x.memo||'—'}</td></tr>)}</tbody></table></div>}
        </ChainRow>
        {w.length?<ChainRow name="旺店通订单／发货" detailLabel="旺店通订单" initialOpen={merged} date={<><small>发货时间</small><span>{w[0].ship||'未发货'}</span></>} summary={<>{w.map(x=>x.jy).join('、')}{ships.length>0&&<small>出库单 {ships.join('、')}</small>}</>} amount={<>应收 ¥ {money(w.reduce((s,x)=>s+x.recv,0))}<small>买家实付 ¥ {money(w.reduce((s,x)=>s+x.paid,0))}</small></>} status={[...new Set(w.map(x=>x.status+(x.refund?' · '+x.refund:'')))].join('、')}>
          <div className="ew-scroll"><table><thead><tr><th>旺店通订单号</th><th>包含的平台订单</th><th>货品</th><th>应收金额</th><th>买家实付</th><th>发货时间</th><th>状态</th></tr></thead><tbody>{w.map(x=><tr key={x.jy}><td>{x.jy}</td><td>{x.orders.map(o=><small key={o} style={{marginTop:0}}>{o}</small>)}</td><td>{x.goods.map(g=><small key={g} style={{marginTop:0}}>{g}</small>)}</td><td>{money(x.recv)}</td><td>{money(x.paid)}</td><td>{x.ship||'未发货'}</td><td>{x.status}{x.refund&&<small>{x.refund}</small>}</td></tr>)}</tbody></table></div>
        </ChainRow>:<ChainRow name="旺店通订单／发货" date={<><small>应收业务日期</small><span>{blue[0]?.date||'未取得'}</span></>} summary={ships.length?`出库单 ${ships.join('、')}`:'未取得'} status={ships.length?'旺店通订单明细未导入，只有金蝶应收单上带的出库单号':'旺店通订单明细未导入'}/>}
        <ChainRow name="金蝶应收" detailLabel="应收明细" initialOpen={d.bills.length>0} date={d.bills.length?<><small>业务日期</small><span>{d.bills[0].date}</span>{d.bills.length>1&&<><small>至</small><span>{d.bills[d.bills.length-1].date}</span></>}</>:'未取得'} summary={d.bills.length?d.bills.map(b=>b.no).join('、'):'未取得关联应收单'} amount={d.bills.length?<>应收 ¥ {money(d.ar_total)}<small>未核销 ¥ {money(d.open_total)}</small></>:null} status={!d.bills.length?'未取得':red.length?'含红字应收':'已取得应收'}>
          {d.bills.length>0&&<div className="ew-scroll"><table className="ec-bill-lines"><thead><tr><th>应收单号</th><th>业务日期</th><th>物料</th><th>数量</th><th>含税单价</th><th>不含税／税额</th><th>应收金额</th><th>已核销</th><th>未核销</th><th>来源单据</th><th>状态</th></tr></thead><tbody>{d.bills.flatMap(b=>{
            const x=xb[b.no],lines=x?.lines?.length?x.lines:[null],n=lines.length
            return lines.map((l,i)=><tr key={b.no+':'+i}>
              {i===0&&<td rowSpan={n}>{b.no}<small>{[b.amount<0&&'红字',x?.type,n>1&&`整单 ${money(b.amount)}`].filter(Boolean).join(' · ')}</small></td>}
              {i===0&&<td rowSpan={n}>{b.date}</td>}
              <td className="ec-wrap">{l?<>{l.name}<small>{l.code}</small></>:'—'}</td><td>{l?`${l.qty} ${l.unit}`:'—'}</td><td>{l?money(l.price):'—'}</td><td>{l?<>{money(l.net)}<small>税 {money(l.tax)} · {l.rate}%</small></>:'—'}</td>
              <td>{money(l?l.amount:b.amount)}</td>
              {i===0&&<td rowSpan={n}>{money(b.written)}</td>}{i===0&&<td rowSpan={n}><strong>{money(b.open)}</strong></td>}
              <td>{l?.src?<>{l.src}<small>{l.src_type}</small></>:'—'}{i===0&&x?.jy&&<small>旺店通 {x.jy}</small>}</td>
              {i===0&&<td rowSpan={n}>{WS[b.ws]||b.ws}{b.ds!=='C'&&' · 未审核'}{x?.approver&&<small>{x.approver} {x.approved.slice(5)} 审核</small>}{x?.creator&&<small>{x.creator} {x.created.slice(5)} 生成</small>}</td>}
            </tr>)})}</tbody></table>
            {(extra.loading||extra.error||extra.data?.error)&&<p className="ec-chain-note">{extra.loading?'正在向金蝶取物料、数量、来源单据…':extra.data?.error||'物料、数量这些明细没取到，稍后重开这张单再试'}</p>}</div>}
        </ChainRow>
        <ChainRow name="抖音结算（聚合账户）" detailLabel="动账明细" initialOpen={settleRows.length>0} date={settleRows.length?<><small>结算时间</small><span>{settleRows[0].t}</span>{settleRows.length>1&&<><small>至</small><span>{settleRows[settleRows.length-1].t}</span></>}</>:'未取得'} summary={settleRows.length?<>{settleRows[0].id}<small>{settleRows.length} 笔结算 · {d.btype}</small></>:closed?'平台订单已关闭，不会有结算流水':'流水里还没有这单'} amount={settleRows.length?<>到账 ¥ {money(d.cash_total)}<small>扣费 ¥ {money(d.fee_total)}{d.refund_total?` · 退款 ¥ ${money(d.refund_total)}`:''}</small></>:null} status={settleRows.length?'已结算进账户':closed?'平台已关闭，不会结算':'待结算'}>
          {settleRows.length>0&&<div className="ew-scroll"><table><thead><tr><th>动账时间</th><th>动账流水号</th><th>到账金额</th><th>结算时扣费</th><th>结算时退款</th><th>应冲应收</th><th>当时账户余额</th></tr></thead><tbody>{settleRows.map(f=><tr key={f.id}><td>{f.t}{!f.in_period&&<small>不在 {period}</small>}</td><td>{f.id}{merged&&<small>订单 {f.order}</small>}</td><td>{money(f.amt)}</td><td>{Object.keys(f.fees).length?Object.entries(f.fees).map(([k,v])=><small key={k}>{k} {money(v)}</small>):'—'}</td><td>{f.refund?money(f.refund):'—'}</td><td><strong>{money(f.gross)}</strong></td><td>{f.bal==null?'—':money(f.bal)}</td></tr>)}</tbody></table></div>}
        </ChainRow>
        {after.length>0&&<ChainRow name="结算后退款／调整" detailLabel="动账明细" initialOpen date={<><small>动账时间</small><span>{after[0].t}</span></>} summary={`${after.length} 笔 · ${[...new Set(after.map(f=>f.scene))].join('、')}`} amount={`¥ ${money(d.after_total)}`} status="钱已从账户进出，对应的红字应收要人看">
          <div className="ew-scroll"><table><thead><tr><th>动账时间</th><th>动账流水号</th><th>场景</th><th>金额</th><th>当时账户余额</th></tr></thead><tbody>{after.map(f=><tr key={f.id}><td>{f.t}</td><td>{f.id}</td><td>{f.scene}<small>{f.memo}</small></td><td>{money(f.amt)}</td><td>{f.bal==null?'—':money(f.bal)}</td></tr>)}</tbody></table></div>
        </ChainRow>}
        <ChainRow name="金蝶收款单／应收核销" date="未取得" summary={!d.bills.length?'没有应收单':!opened.length?'这个订单的应收已全部核销':d.written_total?`已核销 ¥ ${money(d.written_total)}，还剩 ¥ ${money(d.open_total)}`:'还没有核销'} status={!opened.length&&d.bills.length?'已核销':d.settled&&Math.abs(diff)<0.005?'可以下推收款单核销':'待处理'}/>
      </tbody></table></div>
    </>
  }
  return <div className="ew-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="ew-drawer ec-chain-drawer" role="dialog" aria-modal="true" aria-label="订单全链路">
    <header><div><span className="ew-muted">订单全链路 · 金蝶只读</span><h2>{order}</h2><small>{shopName} · {period}</small></div><button onClick={onClose}>关闭</button></header>
    {res.error?<p role="alert">读取失败：{res.error}。请关闭后重试。</p>:!d?<p role="status">正在读取这个订单的证据…</p>:body()}
  </section></div>
}

// 下推收款单到金蝶「暂存」：只挑最干净的应收，分批备好；保存、提交、审核都留给会计在金蝶里做
function PushPanel({period,shop,canEdit,stamp,notify}) {
  const [tick,setTick]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[mode,setMode]=useState(''),[size,setSize]=useState('')
  const res=useResource(`${BASE}/push/plan?${query({period,shop})}`,`${stamp}:${tick}`,`push:${period}:${shop}`),p=res.data
  useEffect(()=>{setError('');setMode('');setSize('')},[period,shop])
  const running=!!p?.job?.running
  useEffect(()=>{if(!running)return;const timer=setInterval(()=>setTick(v=>v+1),3000);return()=>clearInterval(timer)},[running])
  const form=values=>{const body=new FormData();Object.entries(values).forEach(([k,v])=>body.append(k,v));return {method:'POST',body}}
  const act=async(fn,done)=>{setBusy(true);setError('');try{const r=await fn();notify?.(done(r))}catch(e){setError(e.message)}finally{setBusy(false);setTick(v=>v+1)}}
  if(!p) return <section className="ew-panel"><header><h2>下推收款单到金蝶（暂存）</h2></header><p className="ew-empty">{res.error||'正在读取…'}</p></section>
  const b=p.batch,on=p.conf.mode==='on',lines=b.lines||[]
  const run=()=>{if(!window.confirm(`把这一批 ${count(b.count)} 张应收（合计 ¥${money(b.total)}）下推成一张金蝶暂存收款单？\n\n只建暂存草稿，不保存、不提交、不审核。`))return
    act(()=>requestJson(`${BASE}/push/run`,form({period,shop,times:1})),()=>'已开始下推，在后台进行；这一块会自动刷新进度，可以离开页面。')}
  const rounds=p?Math.ceil(p.left.count/p.conf.size):0
  const runAll=()=>{if(!window.confirm(`把还剩的 ${count(p.left.count)} 张应收（合计 ¥${money(p.left.amount)}）全部下推？\n\n每批 ${count(p.conf.size)} 张，系统自动连着推 ${rounds} 批，得到 ${rounds} 张金蝶暂存收款单。\n只建暂存草稿，不保存、不提交、不审核。中途哪一批出错就停在那里。`))return
    act(()=>requestJson(`${BASE}/push/run`,form({period,shop,times:0})),()=>`已开始全部下推，共 ${rounds} 批，在后台进行；这一块会自动刷新进度，可以离开页面。`)}
  const undo=x=>{if(!window.confirm(x.count?`撤回这一批（${count(x.count)} 张应收）？会把金蝶里那张暂存收款单删掉。`:`删掉金蝶里这张没登记的暂存收款单（内码 ${x.fid}）？`))return
    act(()=>requestJson(`${BASE}/push/undo`,form({period,shop,fid:x.fid})),()=>'已撤回，金蝶里的暂存收款单已删除。')}
  const save=()=>act(()=>requestJson(`${BASE}/push/mode`,form({mode:mode||p.conf.mode,size:size||p.conf.size})),()=>'下推设置已保存。')
  return <section className="ew-panel"><header><h2>下推收款单到金蝶（暂存）</h2><span className="ew-muted">系统只把单子备到“暂存”；保存、提交、审核由会计在金蝶里做，审核时金蝶才核销</span></header>
    <div className="ew-result-status" style={{padding:'12px 18px 0'}}><span className={`ew-order-pill ${on?'ew-pill-ok':'ew-pill-wait'}`}>当前档位：{p.conf.mode_label}</span>
      <span>{p.conf.mode==='off'?'已关闭，不能下推。':p.conf.mode==='dry'?'演练：下面只是“如果真做会建什么单”，不会写金蝶。':'真做：点下推会在金蝶里建一张暂存收款单。'}</span>
      {p.can_admin&&<><label style={{display:'flex',alignItems:'center',gap:6}}>档位<select aria-label="下推档位" value={mode||p.conf.mode} onChange={e=>setMode(e.target.value)}><option value="off">关</option><option value="dry">演练</option><option value="on">真做</option></select></label>
        <label style={{display:'flex',alignItems:'center',gap:6}}>每批<input aria-label="每批张数" type="number" min="1" max={p.conf.max} style={{width:90}} value={size||p.conf.size} onChange={e=>setSize(e.target.value)}/>张</label>
        {p.left.count>0&&<button type="button" disabled={busy} title="金蝶一张收款单最多挂 10,000 行源单，所以每批最多 10,000 张" onClick={()=>setSize(String(Math.min(p.left.count,p.conf.max)))}>{p.left.count>p.conf.max?`最多（${count(p.conf.max)} 张）`:`全部（${count(p.left.count)} 张）`}</button>}
        <button disabled={busy||(!mode&&!size)} onClick={save}>保存设置</button></>}</div>
    {(error||p.problem||p.job.error)&&<div className="ew-notice" role="status" style={{margin:'10px 18px'}}>{error||p.job.error||p.problem}</div>}
    {running&&<div className="ew-notice" role="status" style={{margin:'10px 18px'}}>正在下推第 {p.job.batch} 批（已 {p.job.seconds} 秒）：{p.job.stage}。{p.job.done&&`前面已推成 ${p.job.done.batches} 批、${count(p.job.done.count)} 张。`}在后台进行，可以离开页面。</div>}
    {!running&&p.job.done&&<div className="ew-notice" role="status" style={{margin:'10px 18px'}}>上一次下推完成：{p.job.done.batches>1&&`${p.job.done.batches} 批 · `}{count(p.job.done.count)} 张应收 · ¥ {money(p.job.done.total)} · 用时 {p.job.done.seconds} 秒（其中金蝶下推 {p.job.done.push_seconds} 秒）{p.job.done.receipts.length>1&&`，共 ${p.job.done.receipts.length} 张暂存收款单（${p.job.done.receipts.map(x=>count(x.count)+' 张').join(' + ')}），每张各自两边平`}。请到金蝶收款单列表按“暂存”查看。</div>}
    {p.orphans.length>0&&<div className="ew-notice" role="status" style={{margin:'10px 18px'}}>金蝶里有 {p.orphans.length} 张接口账号建的暂存收款单没有登记在下面（多半是下推中途断掉留下的）：{p.orphans.map(x=><span key={x.fid} style={{marginRight:12}}>内码 {x.fid} · {x.at} <button disabled={!canEdit||busy} onClick={()=>undo(x)}>删掉</button></span>)}</div>}
    <p className="ew-muted ew-padding" style={{paddingTop:12}}>可以直接下推的应收 <b>{count(p.eligible.count)} 张 · ¥ {money(p.eligible.amount)}</b>（可核销、蓝字、已审核、没核销过、订单里没有红字、两边分毫不差）。已下推 {count(p.pushed.count)} 张 · ¥ {money(p.pushed.amount)}，还剩 <b>{count(p.left.count)} 张 · ¥ {money(p.left.amount)}</b>。
      {p.skipped.length>0&&<> 可核销但不下推的：{p.skipped.map(s=>`${s.label} ${count(s.count)} 张 ¥${money(s.amount)}`).join('；')}。</>}</p>
    {b.count>0?<><p className="ew-padding" style={{margin:0}}><b>下一批：{count(b.count)} 张应收</b>　{b.first} ～ {b.last}　业务日期 {b.from} 至 {b.to}　源单应收合计 ¥ {money(b.total)}</p>
      <div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>行</th><th>结算方式</th><th>收款账户</th><th className="ew-num">金额</th><th>摘要</th></tr></thead>
        <tbody>{lines.map((x,i)=><tr key={x.name}><td>{i+1}</td><td>{x.kind==='cash'?'支付宝':'内部转销'}</td><td>{x.kind==='cash'?(p.account||<span className="ew-open-red">认不出收款账户，请重新同步金蝶应收</span>):<span className="ew-muted-num">—</span>}</td><td className="ew-num">{money(x.amount)}</td><td>{x.kind==='cash'?'这批订单结算到账的钱':x.memo}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan="3">收款明细合计</td><td className="ew-num">{money(b.line_total)}</td><td>{b.line_total===b.total?'＝ 源单应收合计，两边平':<span className="ew-open-red">≠ 源单应收合计</span>}</td></tr></tfoot></table></div>
      <div className="ew-tools"><button className="ew-primary" disabled={!on||!canEdit||busy||running||!p.account||b.line_total!==b.total} title={!on?'当前不是“真做”档，不会写金蝶':canEdit?'只建暂存草稿':'需要电商对账的上传/跑批权限'} onClick={run}>{running?'正在下推…':busy?'处理中…':`只推这一批（${count(b.count)} 张）`}</button>
        {rounds>1&&<button disabled={!on||!canEdit||busy||running||!p.account||b.line_total!==b.total} title="按每批张数一批接一批推到没有为止，每批一张暂存收款单" onClick={runAll}>全部推完（{count(p.left.count)} 张 · {rounds} 张收款单）</button>}
        <span className="ew-muted">业务日期填 {period} 月末；扣款行的费用项目照 8 月抖音收款单填“电商”。推之前系统会到金蝶逐张复核状态，有一张变了就整批不推。</span></div></>
      :<p className="ew-empty">{p.eligible.count?'可以下推的应收都推完了。':'现在没有可以直接下推的应收。'}</p>}
    {p.batches.length>0&&<div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>下推时间</th><th>操作人</th><th className="ew-num">应收张数</th><th className="ew-num">金额</th><th>应收单号</th><th>用时</th><th>金蝶里的现状</th><th>操作</th></tr></thead>
      <tbody>{p.batches.map(x=><tr key={x.fid}><td>{x.at}</td><td>{x.by}</td><td className="ew-num">{count(x.count)}</td><td className="ew-num">{money(x.total)}</td><td>{x.first} ～ {x.last}</td><td>{x.seconds!=null?`${x.seconds} 秒`:'—'}</td><td>{x.state}{x.number&&<small>{x.number}</small>}</td><td>{x.can_undo?<button disabled={!canEdit||busy} onClick={()=>undo(x)}>撤回</button>:'—'}</td></tr>)}</tbody></table></div>}
    {p.other.lines.length>0&&<p className="ew-muted ew-padding" style={{paddingTop:12}}>注意：不挂在订单上的账户进出共 ¥ {money(p.other.total)}（{p.other.lines.slice(0,4).map(x=>`${x.name} ${money(x.amount)}`).join('、')}{p.other.lines.length>4?' 等':''}）<b>不在任何一批里</b>，要另外入账；在这之前金蝶账面余额会比流水余额多出这一块。</p>}
  </section>
}
