import React, { useEffect, useState } from 'react'
import { requestJson, query, money, count, useResource } from './ecomWorkbenchApi.js'

// V2.810 金蝶未核销清单：从金蝶里还挂着没核的应收单 / 收款单出发。只读金蝶。
const BASE='/api/ec/open-items'
const EMPTY={pile:'',shop:'',month:'',hint:''}

export default function EcomOpenItems({canEdit,revision,notify}) {
  const [tick,setTick]=useState(0),[side,setSide]=useState('ar'),[filter,setFilter]=useState(EMPTY),[q,setQ]=useState(''),[search,setSearch]=useState(''),[page,setPage]=useState(1),[error,setError]=useState('')
  const top=useResource(`${BASE}/summary`,`${revision}:${tick}`,'open-summary'),data=top.data,s=data?.summary,meta=data?.meta,labels=data?.labels||{}
  const refreshing=!!data?.refreshing
  useEffect(()=>{if(!refreshing)return;const timer=setInterval(()=>setTick(v=>v+1),3000);return()=>clearInterval(timer)},[refreshing])
  const params={side,...filter,q:search}
  const key=JSON.stringify([params,meta?.ts])
  const list=useResource(s?`${BASE}/bills?${query({...params,page})}`:null,meta?.ts||'',key),bills=list.data
  const pick=next=>{setFilter(f=>({...f,...next}));setPage(1)}
  const reset=()=>{setFilter(EMPTY);setQ('');setSearch('');setPage(1)}
  const sync=async()=>{try{setError('');await requestJson(`${BASE}/refresh`,{method:'POST'});notify?.('已发起金蝶只读查询，完成后自动更新。');setTick(v=>v+1)}catch(e){setError(e.message)}}
  const exportUrl=`${BASE}/export?${query(params)}`
  const select=(label,name,options)=><label><span>{label}</span><select aria-label={label} value={filter[name]} onChange={e=>pick({[name]:e.target.value})}><option value="">全部</option>{options.map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
  const status=<div className="ew-result-status" role="status">
    <span className="ew-readonly">金蝶只读</span>
    <span>{refreshing?'正在从金蝶读取未核销单据，通常十几秒…':meta?`上次同步 ${meta.ts} · ${meta.operator} · 应收单 ${count(meta.ar)} 张、收款单 ${count(meta.rec)} 张`:'还没有同步过'}</span>
    <button disabled={!canEdit||refreshing} title={canEdit?'只查询金蝶，不改任何单据':'需要电商对账的上传/跑批权限'} onClick={sync}>{refreshing?'读取中…':'只读同步'}</button>
  </div>
  if(!s) return <>{status}{(error||data?.error||top.error)&&<div className="ew-notice" role="status">{error||data?.error||top.error}</div>}
    <section className="ew-panel"><p className="ew-empty">{top.loading&&!data?'正在读取…':refreshing?'正在从金蝶读取，读完自动显示。':'这里列出金蝶里电商还没核销完的应收单和收款单。点上方“只读同步”取一次数。'}</p></section></>
  const shopOptions=(side==='rec'?s.receipts.shops:s.shops).map(r=>[r.shop,r.shop])
  return <>{status}{(error||data.error)&&<div className="ew-notice" role="status">{error||data.error}</div>}
    <div className="ew-closeout ew-open-cards">
      <button type="button" className={`ew-co ew-co-primary${!filter.pile&&side==='ar'?' on':''}`} onClick={()=>{setSide('ar');pick({pile:''})}}><span className="ew-co-lab">未核销应收合计</span><strong>¥ {money(s.total.amount)}</strong><small>{count(s.total.count)} 张应收单</small></button>
      {Object.entries(labels.piles||{}).map(([k,label])=><button type="button" key={k} className={`ew-co${filter.pile===k&&side==='ar'?' on':''}`} onClick={()=>{setSide('ar');pick({pile:k})}}><span className="ew-co-lab">{label}</span><strong>¥ {money(s.piles[k].amount)}</strong><small>{count(s.piles[k].count)} 张 · {labels.pile_help?.[k]}</small></button>)}
      <button type="button" className={`ew-co${side==='rec'?' on':''}`} onClick={()=>{setSide('rec');reset()}}><span className="ew-co-lab">收款单未核销</span><strong>¥ {money(s.receipts.total.amount)}</strong><small>{count(s.receipts.total.count)} 张 · 钱收了还没核到应收</small></button>
    </div>
    <section className="ew-panel"><header><h2>各店铺挂了多少</h2><span className="ew-muted">点数字看明细 · 未核销金额 = 应收金额 − 已核销金额，红字应收为负数</span></header><div className="ew-scroll"><table className="ew-open-matrix"><thead><tr><th>店铺（金蝶客户）</th>{Object.values(labels.piles||{}).map(l=><th key={l} className="ew-num">{l}</th>)}<th className="ew-num">合计</th></tr></thead>
      <tbody>{s.shops.map(r=><tr key={r.shop}><td>{r.shop}</td>{[...Object.keys(labels.piles||{}),'total'].map(k=><td key={k} className="ew-num">{r[k].count?<button className="ew-link" onClick={()=>{setSide('ar');pick({shop:r.shop,pile:k==='total'?'':k,hint:'',month:''})}}>{money(r[k].amount)}<small>{count(r[k].count)} 张</small></button>:<span className="ew-muted-num">—</span>}</td>)}</tr>)}</tbody>
      <tfoot><tr><td>合计</td>{Object.keys(labels.piles||{}).map(k=><td key={k} className="ew-num">{money(s.piles[k].amount)}<small>{count(s.piles[k].count)} 张</small></td>)}<td className="ew-num">{money(s.total.amount)}<small>{count(s.total.count)} 张</small></td></tr></tfoot></table></div></section>
    {Object.keys(s.hints).length>0&&<div className="ew-open-hints"><span>只靠金蝶就能看出来的：</span>{Object.entries(s.hints).map(([k,v])=><button key={k} className={filter.hint===k&&side==='ar'?'active':''} onClick={()=>{setSide('ar');pick({hint:filter.hint===k?'':k})}}>{labels.hints?.[k]} <b>{count(v.count)}</b> 张</button>)}</div>}
    <div className="ew-subnav">{[['ar','应收单'],['rec','收款单']].map(([k,l])=><button key={k} className={side===k?'active':''} onClick={()=>{setSide(k);reset()}}>{l}</button>)}</div>
    <form className="ew-order-filters ew-order-filters-expanded" onSubmit={e=>{e.preventDefault();setSearch(q);setPage(1)}}>
      {select(side==='rec'?'往来单位':'店铺','shop',shopOptions)}{select('哪一堆','pile',Object.entries(labels.piles||{}))}
      <label><span>月份</span><input aria-label="月份" type="month" value={filter.month} onChange={e=>pick({month:e.target.value})}/></label>
      {select('提示','hint',[...Object.entries(labels.hints||{}).filter(([k])=>side==='ar'||['unaudited','partial'].includes(k)),['none','没有提示的']])}
      <label className="ew-order-search"><span>模糊搜索</span><input aria-label="模糊搜索" value={q} onChange={e=>setQ(e.target.value)} placeholder={side==='rec'?'收款单号':'应收单号或平台订单号'}/></label>
      <button className="ew-primary">查询</button><button type="button" onClick={reset}>清空筛选</button>
      <button type="button" disabled={!bills?.total} onClick={()=>window.open(exportUrl,'_blank')} title="导出当前筛选下的全部单据（含汇总页）">导出 Excel</button>
    </form>
    {list.error&&<div className="ew-notice" role="status">{list.error}</div>}
    <div className="ew-scroll ew-panel" aria-busy={list.loading}><table className="ew-order-table ew-open-table"><thead>{side==='rec'
      ?<tr><th>收款单号</th><th>日期</th><th>往来单位</th><th className="ew-num">收款金额</th><th className="ew-num">已核销</th><th className="ew-num">未核销</th><th>状态</th><th>结算方式 · 收款账户</th></tr>
      :<tr><th>应收单号</th><th>业务日期</th><th>店铺</th><th className="ew-num">应收金额</th><th className="ew-num">已核销</th><th className="ew-num">未核销</th><th>状态</th><th>平台订单号</th><th>提示</th></tr>}</thead>
      <tbody>{bills?.rows?.map(b=><tr key={b.no}><td>{b.no}</td><td>{b.date}</td><td>{b.shop}</td><td className="ew-num">{money(b.amount)}</td><td className={`ew-num ${b.written===0?'ew-zero':''}`}>{money(b.written)}</td><td className={`ew-num ${b.open<0?'ew-open-red':''}`}><strong>{money(b.open)}</strong></td>
        <td><span className={`ew-order-pill ${b.ds==='C'?'':'ew-pill-wait'}`}>{labels.ws?.[b.ws]||b.ws}{b.ds!=='C'&&` · ${labels.ds?.[b.ds]||b.ds}`}</span><small>{labels.piles?.[b.pile]}</small></td>
        {side==='rec'?<td>{b.settle||'—'}<small>{b.account}</small></td>:<><td>{b.order||<span className="ew-muted-num">—</span>}</td><td className="ew-issue">{b.hint_text||'—'}</td></>}</tr>)}
        {!bills?.rows?.length&&<tr><td colSpan={side==='rec'?8:9} className="ew-empty">{list.loading?'正在读取…':'当前筛选下没有单据'}</td></tr>}</tbody></table></div>
    <div className="ew-pagination"><span>{list.loading&&bills?`正在读取第 ${page} 页…`:`共 ${count(bills?.total)} 张 · 未核销合计 ¥ ${money(bills?.amount)} · 每页 50 张`}</span><button disabled={page<=1||list.loading} onClick={()=>setPage(p=>p-1)}>上一页</button>{bills?.page??page} / {bills?.pages??'—'}<button disabled={!bills?.pages||(bills?.page??page)>=bills.pages||list.loading} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
  </>
}
