import React, { useEffect, useState } from 'react'
import { requestJson, query, money, count, useResource } from './ecomWorkbenchApi.js'

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
  const src=data?.sources||{},hasSettle=src.dy_settle>0,hasLedger=src.dy_ledger>0
  const bar=<div className="ew-result-status" role="status">
    <label style={{display:'flex',alignItems:'center',gap:8}}>店铺<select aria-label="店铺" value={shop} onChange={e=>setShop(e.target.value)}>{shops.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <span className="ew-readonly">金蝶只读</span>
    <span>{refreshing?'正在从金蝶读取这家店的应收单，通常十几秒…':ar?`应收上次同步 ${ar.ts} · ${ar.operator} · ${count(ar.bills)} 张（${ar.since} 起）`:'还没有同步这家店的金蝶应收'}</span>
    <button disabled={!canEdit||refreshing} title={canEdit?'只查询金蝶，不改任何单据':'需要电商对账的上传/跑批权限'} onClick={sync}>{refreshing?'读取中…':'只读同步金蝶应收'}</button>
    {r&&<button onClick={()=>window.open(`${BASE}/export?${query({period,shop})}`,'_blank')} title="收款单草稿 + 各类应收清单；“可核销”那页就是下推用的源单">导出 Excel</button>}
  </div>
  const notes=<>{(error||data?.error||top.error)&&<div className="ew-notice" role="status">{error||data?.error||top.error}</div>}
    {data&&!hasSettle&&<div className="ew-notice" role="status">{period} 还没有抖音动账明细（订单维度），对不了账。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}
    {data&&hasSettle&&!hasLedger&&<div className="ew-notice" role="status">{period} 还没有带余额的账户流水：到账金额先按订单维度明细算，余额核对不了。<button className="ew-link" onClick={onPrepare}>去数据准备导入</button></div>}</>
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
      <tbody>{bills?.rows?.map(b=><tr key={b.no}><td>{b.order?<button className="ew-link" title="看这张单的依据：抖音每笔动账 + 金蝶每张应收" onClick={()=>setProof(b.order)}>{b.no}</button>:b.no}<small>{WS[b.ws]||b.ws}{b.ds!=='C'&&' · 未审核'}</small></td><td>{b.date}</td><td>{b.order||<span className="ew-muted-num">—</span>}</td><td className="ew-num">{money(b.amount)}</td><td className={`ew-num ${b.written===0?'ew-zero':''}`}>{money(b.written)}</td><td className={`ew-num ${b.open<0?'ew-open-red':''}`}><strong>{money(b.open)}</strong></td><td>{b.settled_at?b.settled_at.slice(0,16):<span className="ew-muted-num">还没结算</span>}</td><td className="ew-num">{b.flow==null?'—':money(b.cash)}</td><td className="ew-num">{b.flow==null?'—':money(b.fee)}</td><td className="ew-num">{b.flow==null?'—':money(b.flow)}</td><td className={`ew-num ${b.diff?'ew-open-red':''}`}>{b.diff==null?'—':money(b.diff)}</td><td className="ew-issue">{r.categories.find(c=>c.key===b.cat)?.label}</td></tr>)}
        {!bills?.rows?.length&&<tr><td colSpan="12" className="ew-empty">{list.loading?'正在读取…':'当前筛选下没有应收单'}</td></tr>}</tbody></table></div>
    <div className="ew-pagination"><span>{list.loading&&bills?`正在读取第 ${page} 页…`:`共 ${count(bills?.total)} 张 · 未核销合计 ¥ ${money(bills?.amount)} · 截至 ${r.end} · 每页 50 张 · 点应收单号看依据`}</span><button disabled={page<=1||list.loading} onClick={()=>setPage(p=>p-1)}>上一页</button>{bills?.page??page} / {bills?.pages??'—'}<button disabled={!bills?.pages||(bills?.page??page)>=bills.pages||list.loading} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
    {proof&&<OrderProof period={period} shop={shop} order={proof} onClose={()=>setProof('')}/>}
  </>
}

// 一个订单的依据：左手抖音每一笔动账（到账 + 各项扣费 = 应冲应收），右手金蝶每一张应收单
function OrderProof({period,shop,order,onClose}) {
  const res=useResource(`${BASE}/order?${query({period,shop,order})}`),d=res.data
  useEffect(()=>{const key=e=>{if(e.key==='Escape')onClose()};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key)},[onClose])
  const diff=d?Math.round((d.open_total-d.flow_total)*100)/100:0
  return <div className="ew-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="ew-drawer" role="dialog" aria-modal="true" aria-label="订单依据">
    <header><div><small className="ew-muted">平台订单号</small><h2>{order}</h2></div><button onClick={onClose}>关闭</button></header>
    {res.error&&<div className="ew-notice" role="status">{res.error}</div>}
    {!d&&!res.error&&<p className="ew-empty">正在读取…</p>}
    {d&&<>
      <div className="ew-drawer-metrics"><div><small>{period} 到账</small><strong>{money(d.cash_total)}</strong></div><div><small>结算时扣费</small><strong>{money(d.fee_total)}</strong></div><div><small>流水应冲应收（到账 + 扣费）</small><strong>{money(d.flow_total)}</strong></div><div><small>金蝶未核销应收</small><strong className={Math.abs(diff)>=0.005?'ew-open-red':''}>{money(d.open_total)}</strong></div></div>
      <p className="ew-muted" style={{marginTop:10}}>{!d.flows.length?`到 ${period} 月底，抖音流水里还没有这个订单。`:Math.abs(diff)<0.005?'两边一致。':`两边差 ${money(diff)}（金蝶 − 流水）。`}</p>
      <h3>抖音动账明细</h3>
      <div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>动账时间</th><th>动账流水号</th><th>场景 · 计费类型</th><th className="ew-num">到账金额</th><th>结算时扣费</th><th className="ew-num">结算时退款</th><th className="ew-num">应冲应收</th><th className="ew-num">当时账户余额</th></tr></thead>
        <tbody>{d.flows.map(f=><tr key={f.id}><td>{f.t}{!f.in_period&&<small>不在 {period}</small>}</td><td>{f.id}</td><td>{f.scene}<small>{f.btype}</small></td><td className={`ew-num ${f.amt<0?'ew-open-red':''}`}>{money(f.amt)}</td><td>{Object.keys(f.fees).length?Object.entries(f.fees).map(([k,v])=><small key={k} style={{marginTop:0}}>{k} {money(v)}</small>):'—'}</td><td className="ew-num">{f.refund?money(f.refund):'—'}</td><td className="ew-num">{f.gross==null?'—':<strong>{money(f.gross)}</strong>}</td><td className="ew-num">{f.bal==null?'—':money(f.bal)}</td></tr>)}
          {!d.flows.length&&<tr><td colSpan="8" className="ew-empty">没有动账记录</td></tr>}</tbody></table></div>
      <h3>金蝶应收单</h3>
      <div className="ew-scroll"><table className="ew-open-table"><thead><tr><th>应收单号</th><th>业务日期</th><th className="ew-num">应收金额</th><th className="ew-num">已核销</th><th className="ew-num">未核销</th><th>状态</th></tr></thead>
        <tbody>{d.bills.map(b=><tr key={b.no}><td>{b.no}</td><td>{b.date}</td><td className={`ew-num ${b.amount<0?'ew-open-red':''}`}>{money(b.amount)}</td><td className="ew-num">{money(b.written)}</td><td className="ew-num"><strong>{money(b.open)}</strong></td><td>{WS[b.ws]||b.ws}{b.ds!=='C'&&' · 未审核'}</td></tr>)}
          {!d.bills.length&&<tr><td colSpan="6" className="ew-empty">金蝶里没有这个订单的应收单（查的是近 4 个月）</td></tr>}</tbody></table></div>
      <p className="ew-muted" style={{marginTop:14}}>动账明细来自上传的抖音文件，流水号可回抖音后台查；应收单来自金蝶只读查询，单号可回金蝶查。</p>
    </>}
  </section></div>
}
