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
  const src=data?.sources||{},hasSettle=src.dy_settle>0,hasLedger=src.dy_ledger>0,hasOrders=src.dy_orders>0
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
    {data&&hasSettle&&!hasOrders&&<div className="ew-notice" role="status">{period} 还没有旺店通订单明细：合单发货的订单认不出来，会被报成“金额对不上”或“金蝶里没有应收”。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}</>
  if(!r) return <>{bar}{notes}<section className="ew-panel"><p className="ew-empty">{top.loading&&!data?'正在读取…':refreshing?'正在从金蝶读取，读完自动显示。':'导入抖音动账明细后，点上方“只读同步金蝶应收”，系统逐单对出哪些能核销。'}</p></section></>
  const d=r.draft,ok=r.buckets.find(b=>b.key==='ok'),pending=Math.round((d.total-ok.flow)*100)/100,bal=r.balance,book=r.book||{}
  const booked=book.debit||book.credit
  return <>{bar}{notes}
    <div className="ew-closeout ew-open-cards ew-dy-cards">{r.categories.filter(c=>c.count||c.key!=='no_order').map(c=><button type="button" key={c.key} className={`ew-co${c.key==='ok'?' ew-co-primary':''}${cat===c.key?' on':''}`} onClick={()=>{setCat(cat===c.key?'':c.key);setPage(1)}}><span className="ew-co-lab">{c.label}</span><strong>¥ {money(c.amount)}</strong><small>{count(c.count)} 张应收 · {count(c.orders)} 单</small></button>)}</div>
    <section className="ew-panel"><header><h2>{period} 收款单草稿</h2><span className="ew-muted">照现行做法：到账一行 + 一种扣款一行（内部转销）· 系统不写金蝶</span></header><div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>行</th><th>结算方式</th><th>收款账户</th><th className="ew-num">金额</th><th>摘要</th></tr></thead>
      <tbody><tr><td>1</td><td>支付宝</td><td>{book.account||<span className="ew-muted-num">同步金蝶后带出</span>}</td><td className="ew-num"><strong>{money(d.net)}</strong></td><td>本月账户净变动（到账）{!d.from_ledger&&<small>没有账户流水，按订单维度明细算</small>}</td></tr>
        {d.deductions.map((x,i)=><tr key={x.name}><td>{i+2}</td><td>内部转销</td><td className="ew-muted-num">—</td><td className={`ew-num ${x.amount<0?'ew-open-red':''}`}>{money(x.amount)}</td><td>{x.memo}</td></tr>)}</tbody>
      <tfoot><tr><td colSpan="3">收款明细合计（＝本月结算订单应冲的应收）</td><td className="ew-num">{money(d.total)}</td><td>其中到账 {money(d.net)} · 扣款 {money(d.deduction_total)}</td></tr></tfoot></table></div>
      <p className="ew-muted ew-padding" style={{paddingTop:12}}>金额一致、可以直接下推的应收是 <b>¥ {money(ok.ar)}</b>（{count(ok.orders)} 单）。{pending!==0&&<>另有 <b>¥ {money(pending)}</b> 的结算对应的应收还对不上号，见下表，处理完两边才平。</>}</p>
      <div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>本月结算的订单</th><th className="ew-num">订单数</th><th className="ew-num">流水应冲应收</th><th className="ew-num">金蝶挂着的应收</th><th className="ew-num">两边差</th></tr></thead>
        <tbody>{r.buckets.map(b=><tr key={b.key}><td>{b.label}{['written','missing'].includes(b.key)&&b.orders>0&&<> <button className="ew-link" onClick={()=>setShowMissing(v=>!v)}>{showMissing?'收起':'看订单'}</button></>}</td><td className="ew-num">{count(b.orders)}</td><td className="ew-num">{money(b.flow)}</td><td className="ew-num">{money(b.ar)}</td><td className={`ew-num ${b.key!=='ok'&&Math.abs(b.flow-b.ar)>=0.005?'ew-open-red':''}`}>{money(b.flow-b.ar)}</td></tr>)}</tbody></table></div>
      {showMissing&&<div className="ew-scroll" style={{maxHeight:260}}><table className="ew-open-table"><thead><tr><th>平台订单号</th><th className="ew-num">流水应冲额</th><th>情况</th></tr></thead><tbody>{lost.data?.rows?.map(m=><tr key={m.order}><td><button className="ew-link" onClick={()=>setProof(m.order)}>{m.order}</button></td><td className="ew-num">{money(m.flow)}</td><td>{m.label}</td></tr>)}{!lost.data&&<tr><td colSpan="3" className="ew-empty">正在读取…</td></tr>}</tbody></table></div>}
    </section>
    <section className="ew-panel"><header><h2>账户余额核对</h2><span className="ew-muted">{bal?`流水 ${count(bal.rows)} 笔 · ${bal.first.slice(0,10)} 至 ${bal.last.slice(0,10)}`:'需要带余额的账户流水'}</span></header><div className="ew-scroll"><table className="ew-open-table"><thead><tr><th></th><th className="ew-num">期初</th><th className="ew-num">本月净变动</th><th className="ew-num">期末</th><th>说明</th></tr></thead><tbody>
      <tr><td>抖音账户流水</td><td className="ew-num">{money(bal?.open)}</td><td className="ew-num">{money(bal?.net)}</td><td className="ew-num">{money(bal?.close)}</td><td>{bal?(bal.breaks?<span className="ew-open-red">余额链有 {bal.breaks} 处接不上，流水可能不全</span>:'余额逐笔接得上'):'—'}</td></tr>
      <tr><td>金蝶账面{book.account&&<small>{book.account}</small>}</td><td className="ew-num">{money(book.open)}</td><td className="ew-num">{book.error?'—':money((book.debit||0)-(book.credit||0))}</td><td className="ew-num">{money(book.close)}</td><td>{book.error||(!bal?'—':Math.abs(book.open-bal.open)>=0.005?<span className="ew-open-red">期初和流水差 {money(book.open-bal.open)}</span>:booked?(Math.abs(book.close-bal.close)<0.005?'期初、期末都和流水一致':<span className="ew-open-red">期末和流水差 {money(book.close-bal.close)}</span>):'期初和流水一致 · 本月还没入账')}</td></tr>
    </tbody></table></div></section>
    <form className="ew-order-filters ew-order-filters-expanded" onSubmit={e=>{e.preventDefault();setSearch(q);setPage(1)}}>
      <label><span>分类</span><select aria-label="分类" value={cat} onChange={e=>{setCat(e.target.value);setPage(1)}}><option value="">全部未核销应收</option>{r.categories.map(c=><option key={c.key} value={c.key}>{c.label}</option>)}</select></label>
      <label className="ew-order-search"><span>模糊搜索</span><input aria-label="模糊搜索" value={q} onChange={e=>setQ(e.target.value)} placeholder="应收单号或平台订单号"/></label>
      <button className="ew-primary">查询</button><button type="button" onClick={()=>{setCat('');setQ('');setSearch('');setPage(1)}}>清空筛选</button>
    </form>
    {list.error&&<div className="ew-notice" role="status">{list.error}</div>}
    <div className="ew-scroll ew-panel" aria-busy={list.loading}><table className="ew-order-table ew-open-table"><thead><tr><th>应收单号</th><th>业务日期</th><th>平台订单号</th><th className="ew-num">应收金额</th><th className="ew-num">已核销</th><th className="ew-num">未核销</th><th>抖音结算时间</th><th className="ew-num">到账</th><th className="ew-num">结算时扣费</th><th className="ew-num" title="到账 + 结算时扣费">流水应冲额</th><th className="ew-num">差额</th><th>分类</th></tr></thead>
      <tbody>{bills?.rows?.map(b=><tr key={b.no}><td>{b.order?<button className="ew-link" title="看这张单的依据：抖音每笔动账 + 金蝶每张应收" onClick={()=>setProof(b.order)}>{b.no}</button>:b.no}<small>{WS[b.ws]||b.ws}{b.ds!=='C'&&' · 未审核'}</small></td><td>{b.date}</td><td>{b.order||<span className="ew-muted-num">—</span>}</td><td className="ew-num">{money(b.amount)}</td><td className={`ew-num ${b.written===0?'ew-zero':''}`}>{money(b.written)}</td><td className={`ew-num ${b.open<0?'ew-open-red':''}`}><strong>{money(b.open)}</strong></td><td>{b.settled_at?b.settled_at.slice(0,16):<span className="ew-muted-num">还没结算</span>}</td><td className="ew-num">{b.flow==null?'—':money(b.cash)}</td><td className="ew-num">{b.flow==null?'—':money(b.fee)}</td><td className="ew-num">{b.flow==null?'—':money(b.flow)}</td><td className={`ew-num ${b.diff?'ew-open-red':''}`}>{b.diff==null?'—':money(b.diff)}</td><td className="ew-issue">{r.categories.find(c=>c.key===b.cat)?.label}{b.merged>0&&<small>合单发货 · {b.merged} 个平台订单一起对</small>}{b.reason&&<small>{b.reason}</small>}</td></tr>)}
        {!bills?.rows?.length&&<tr><td colSpan="12" className="ew-empty">{list.loading?'正在读取…':'当前筛选下没有应收单'}</td></tr>}</tbody></table></div>
    <div className="ew-pagination"><span>{list.loading&&bills?`正在读取第 ${page} 页…`:`共 ${count(bills?.total)} 张 · 未核销合计 ¥ ${money(bills?.amount)} · 截至 ${r.end} · 每页 50 张 · 点应收单号看依据`}</span><button disabled={page<=1||list.loading} onClick={()=>setPage(p=>p-1)}>上一页</button>{bills?.page??page} / {bills?.pages??'—'}<button disabled={!bills?.pages||(bills?.page??page)>=bills.pages||list.loading} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
    {proof&&<OrderProof period={period} shop={shop} shopName={shops.find(s=>s.id===shop)?.name||shop} order={proof} onClose={()=>setProof('')}/>}
  </>
}

// 订单全链路（抖音）：版式照天猫那张——先给三个结论，再三方金额对账，下面按业务环节逐行列证据
const yuan=v=>v==null?'—':`¥ ${money(v)}`
function OrderProof({period,shop,shopName,order,onClose}) {
  const res=useResource(`${BASE}/order?${query({period,shop,order})}`),d=res.data
  useEffect(()=>{const overflow=document.body.style.overflow;document.body.style.overflow='hidden';const key=e=>{if(e.key==='Escape')onClose()};document.addEventListener('keydown',key);return()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key)}},[onClose])
  const body=()=>{
    const diff=Math.round((d.open_total-d.flow_total)*100)/100,blue=d.bills.filter(b=>b.amount>0),red=d.bills.filter(b=>b.amount<0),opened=d.bills.filter(b=>b.ws!=='C')
    const settleRows=d.flows.filter(f=>f.gross!=null),after=d.flows.filter(f=>f.gross==null),ships=[...new Set(d.bills.map(b=>b.ship).filter(Boolean))],merged=d.members.length>1,w=d.wdt||[]
    const v1=!d.bills.length?{c:'crit',v:'金蝶没有应收',s:'近 4 个月没找到这个订单的应收单'}:{c:'ok',v:`已开应收 ${yuan(d.ar_total)}`,s:`蓝字 ${blue.length} 张${red.length?` · 红字 ${red.length} 张`:''}`}
    const v2=!d.bills.length?{c:'na',v:'不适用',s:'没有应收单可对'}:!opened.length?{c:'ok',v:'已核销',s:`已核销 ${yuan(d.written_total)}`}:d.settled&&d.unsettled.length?{c:'warn',v:'合单没结完',s:`合单的 ${d.members.length} 个订单里还有 ${d.unsettled.length} 个没结算`}:!d.settled?{c:'warn',v:'待结算',s:`未核销 ${yuan(d.open_total)}，抖音还没结这单`}:Math.abs(diff)<0.005?{c:'ok',v:'对平 · 可核销',s:`未核销 ${yuan(d.open_total)} ＝ 流水应冲`}:{c:'crit',v:`差额 ${yuan(diff)}`,s:'金蝶未核销 ≠ 流水应冲'}
    const v3=!d.settled?{c:'warn',v:'还没到账',s:`到 ${period} 月底流水里没有这单`}:{c:after.length?'warn':'ok',v:`已到账 ${yuan(d.cash_total)}`,s:after.length?`结算后另有 ${after.length} 笔退款／调整 ${yuan(d.after_total)}`:`${d.settled_at.slice(0,16)} 结算进聚合账户`}
    const arrow1=!d.settled||!opened.length?['ec-warn',!d.settled?'待结算':'已核销']:Math.abs(diff)<0.005?['ec-ok','差 0 ✓']:['ec-diff',`差 ${money(diff)}`]
    return <>
      <div className="ew-drawer-metrics">{[['订单金额',d.paid_total==null?null:d.paid_total+d.subsidy_total],['消费者实付',d.paid_total],['退款金额',d.settled?d.refund_total:null],['平台费用',d.settled?d.fee_total:null],['账户净收',d.settled?d.cash_total+d.after_total:null]].map(([label,value])=><div key={label}><small>{label}</small><strong>{yuan(value)}</strong></div>)}</div>
      <div className="ec-verdicts">{[['收入确认',v1],['应收核对',v2],['收款核对',v3]].map(([lab,v])=><div key={lab} className={`ec-vtile ec-v-${v.c}`}><small>{lab}</small><strong>{v.v}</strong><span>{v.s}</span></div>)}</div>
      <div className="ec-threeway"><div className="ec-tw-title">三方金额对账 · 金蝶未核销应收 → 流水应冲应收 → 账户到账</div><div className="ec-tw-flow">
        <div className="ec-tw-node"><small>金蝶未核销应收</small><b className={opened.length?'':'ec-tw-mute'}>{opened.length?yuan(d.open_total):d.bills.length?'已核销':'没有应收'}</b></div>
        <div className="ec-tw-arrow">▶<em className={arrow1[0]}>{arrow1[1]}</em></div>
        <div className="ec-tw-node"><small>流水应冲应收（到账 + 扣费）</small><b className={d.settled?'':'ec-tw-mute'}>{d.settled?yuan(d.flow_total):'待结算'}</b></div>
        <div className="ec-tw-arrow">▶<em className="ec-warn">{d.settled?`平台费 ¥ ${money(d.fee_total)}`:'—'}</em></div>
        <div className="ec-tw-node"><small>账户到账</small><b className={d.settled?'':'ec-tw-mute'}>{d.settled?yuan(d.cash_total):'待到账'}</b></div>
      </div></div>
      <div className="ec-chain-badges">{d.categories.map(c=><span key={c}>{c}</span>)}{d.btype&&<span>{d.btype}</span>}{merged&&<span>合单发货 · {d.members.length} 个平台订单</span>}</div>
      {merged&&<p className="ec-chain-note">这 {d.members.length} 个平台订单在旺店通合成一张单发货，金蝶应收照合并后的单开、只记了其中一个订单号，所以放在一起对：{d.members.join('、')}。</p>}
      {d.reason&&<div className="ew-notice" role="status">{d.reason}</div>}
      <p className="ec-chain-note">按业务环节排列，不代表实际时间先后。金额只覆盖已上传的动账明细；到账指结算进抖音聚合账户，不等于已提现到银行。{d.settled&&d.paid_total==null&&' 订单金额、实付、下单时间要重新上传一次动账明细（订单维度）才显示。'}</p>
      <div className="ew-scroll ec-chain-scroll"><table className="ec-chain-table"><thead><tr><th>业务环节</th><th>日期／时间</th><th>关联单据／账户</th><th>金额／数量</th><th>核对状态</th></tr></thead><tbody>
        <ChainRow name="平台订单与支付" date={<><small>下单</small><span>{d.ordered_at||'未取得'}</span></>} summary={<>{merged?d.members.map(o=><span key={o} style={{display:'block'}}>{o}</span>):order}{d.goods.map(g=><small key={g}>{g}</small>)}</>} amount={d.paid_total==null?null:<>实付 ¥ {money(d.paid_total)}<small>平台／达人等补贴 ¥ {money(d.subsidy_total)}</small></>} status={d.settled?'来自抖音动账明细':'流水里还没有这单'}/>
        {w.length?<ChainRow name="旺店通订单／发货" detailLabel="旺店通订单" initialOpen={merged} date={<><small>发货时间</small><span>{w[0].ship||'未发货'}</span></>} summary={<>{w.map(x=>x.jy).join('、')}{ships.length>0&&<small>出库单 {ships.join('、')}</small>}</>} amount={<>应收 ¥ {money(w.reduce((s,x)=>s+x.recv,0))}<small>买家实付 ¥ {money(w.reduce((s,x)=>s+x.paid,0))}</small></>} status={[...new Set(w.map(x=>x.status+(x.refund?' · '+x.refund:'')))].join('、')}>
          <div className="ew-scroll"><table><thead><tr><th>旺店通订单号</th><th>包含的平台订单</th><th>货品</th><th>应收金额</th><th>买家实付</th><th>发货时间</th><th>状态</th></tr></thead><tbody>{w.map(x=><tr key={x.jy}><td>{x.jy}</td><td>{x.orders.map(o=><small key={o} style={{marginTop:0}}>{o}</small>)}</td><td>{x.goods.map(g=><small key={g} style={{marginTop:0}}>{g}</small>)}</td><td>{money(x.recv)}</td><td>{money(x.paid)}</td><td>{x.ship||'未发货'}</td><td>{x.status}{x.refund&&<small>{x.refund}</small>}</td></tr>)}</tbody></table></div>
        </ChainRow>:<ChainRow name="旺店通订单／发货" date={<><small>应收业务日期</small><span>{blue[0]?.date||'未取得'}</span></>} summary={ships.length?`出库单 ${ships.join('、')}`:'未取得'} status={ships.length?'旺店通订单明细未导入，只有金蝶应收单上带的出库单号':'旺店通订单明细未导入'}/>}
        <ChainRow name="金蝶应收" detailLabel="应收明细" initialOpen={d.bills.length>0} date={d.bills.length?<><small>业务日期</small><span>{d.bills[0].date}</span>{d.bills.length>1&&<><small>至</small><span>{d.bills[d.bills.length-1].date}</span></>}</>:'未取得'} summary={d.bills.length?d.bills.map(b=>b.no).join('、'):'未取得关联应收单'} amount={d.bills.length?<>应收 ¥ {money(d.ar_total)}<small>未核销 ¥ {money(d.open_total)}</small></>:null} status={!d.bills.length?'未取得':red.length?'含红字应收':'已取得应收'}>
          {d.bills.length>0&&<div className="ew-scroll"><table><thead><tr><th>应收单号</th><th>业务日期</th><th>应收金额</th><th>已核销</th><th>未核销</th><th>状态</th></tr></thead><tbody>{d.bills.map(b=><tr key={b.no}><td>{b.no}{b.amount<0&&<small>红字</small>}</td><td>{b.date}</td><td>{money(b.amount)}</td><td>{money(b.written)}</td><td><strong>{money(b.open)}</strong></td><td>{WS[b.ws]||b.ws}{b.ds!=='C'&&' · 未审核'}</td></tr>)}</tbody></table></div>}
        </ChainRow>
        <ChainRow name="抖音结算（聚合账户）" detailLabel="动账明细" initialOpen={settleRows.length>0} date={settleRows.length?<><small>结算时间</small><span>{settleRows[0].t}</span>{settleRows.length>1&&<><small>至</small><span>{settleRows[settleRows.length-1].t}</span></>}</>:'未取得'} summary={settleRows.length?<>{settleRows[0].id}<small>{settleRows.length} 笔结算 · {d.btype}</small></>:'流水里还没有这单'} amount={settleRows.length?<>到账 ¥ {money(d.cash_total)}<small>扣费 ¥ {money(d.fee_total)}{d.refund_total?` · 退款 ¥ ${money(d.refund_total)}`:''}</small></>:null} status={settleRows.length?'已结算进账户':'待结算'}>
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
