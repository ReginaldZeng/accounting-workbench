import React, { useEffect, useRef, useState } from 'react'
import { useResource, query, money } from './ecomWorkbenchApi.js'
import './ecomOrderChain.css'

function ChainRow({name,summary,amount,status,date='未取得',detailLabel='查看明细',children,initialOpen=false}) {
  const [open,setOpen]=useState(initialOpen)
  return <><tr><th scope="row">{name}</th><td className="ec-chain-date">{date}</td><td>{summary}{children&&<button className="ew-link ec-chain-toggle" aria-label={`${open?'收起':'展开'}${name}`} aria-expanded={open} onClick={()=>setOpen(!open)}>{open?'收起':detailLabel}</button>}</td><td>{amount||'—'}</td><td><span className="ec-chain-status">{status}</span></td></tr>{open&&<tr className="ec-chain-detail"><td colSpan="5">{children}</td></tr>}</>
}

function DateRange({values,label}) {
  const dates=[...new Set(values.filter(v=>typeof v==='string'&&v.trim()).map(v=>v.trim().replace('T',' ')))].sort()
  return <>{label&&<small>{label}</small>}{dates.length?<><span>{dates[0]}</span>{dates.length>1&&<><small>至</small><span>{dates[dates.length-1]}</span></>}{values.some(v=>!v)&&<small>部分日期未提供</small>}</>:'未取得'}</>
}

const eventName=e=>e.aggregate_copy?'聚合渠道映射':({receipt:'账户收款',refund:'退款',fee:'费用扣款／返还',adjustment:'补贴／调整',transfer:'划转'})[e.kind]||'待识别收支'
const cashTotal=(events,key)=>events.length&&events.every(e=>e[key]!=null&&Number.isFinite(Number(e[key])))?events.reduce((sum,e)=>sum+Math.round(Number(e[key])*1e6),0)/1e6:null
function CashEvidence({events,onOpenLedger}) {
  return <><div className="ew-scroll"><table><thead><tr><th>业务／说明</th><th>账户／流水标识</th><th>时间</th><th>收入</th><th>支出</th></tr></thead><tbody>{events.map((e,i)=><tr key={i}><td>{eventName(e)}{e.kind==='fee'&&<small>{({routine:'常规费用',commission:'佣金',unclassified:'费用待分类'})[e.fee_category]||'费用待分类'}</small>}<small>{e.label} {e.code}</small>{e.aggregate_copy&&<small>映射记录，不重复计入订单净收</small>}</td><td>{e.account_name||'未提供账户名称'}<small>{e.serial||e.ledger_id||'流水标识待查看原始字段'}</small></td><td>{e.date||'未提供'}</td><td>{money(e.income)}</td><td>{money(e.expense)}</td></tr>)}</tbody></table></div><button onClick={onOpenLedger}>在账户流水页查这笔订单</button></>
}

export default function EcomOrderChain({period,shop,orderNo,version,initialTab,onClose,onOpenLedger}) {
  const result=useResource(`/api/ec/workbench/order?${query({period,shop,order_no:orderNo,version})}`),r=result.data?.order
  const ref=useRef(null)
  useEffect(()=>{
    const previous=document.activeElement,overflow=document.body.style.overflow
    document.body.style.overflow='hidden';ref.current?.focus()
    const key=e=>{if(e.key==='Escape')onClose();if(e.key==='Tab'){
      const nodes=[...ref.current.querySelectorAll('button:not(:disabled),a[href],input,select,[tabindex="0"]')].filter(n=>n.getClientRects().length)
      if(!nodes.length){e.preventDefault();return}
      const first=nodes[0],last=nodes[nodes.length-1]
      if(e.shiftKey&&(document.activeElement===first||document.activeElement===ref.current)){e.preventDefault();last.focus()}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
    }}
    document.addEventListener('keydown',key)
    return()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key);previous?.focus()}
  },[])
  const ufirst=r?.business_type==='ufirst',shipments=r?.shipments||[],ars=r?.ar_documents||[],events=r?.events||[]
  const shippingCount=new Set(shipments.map(s=>s.shipment_no).filter(Boolean)).size
  const waybills=[...new Map(shipments.filter(s=>s.logistics_no).map(s=>[`${s.logistics_company||''}:${s.logistics_no}`,s])).values()]
  const notApplicable=ufirst?'不适用':'未取得'
  return <div className="ew-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="ew-drawer ec-chain-drawer" role="dialog" aria-modal="true" aria-labelledby="order-drawer-title" tabIndex="-1" ref={ref}>
    <header><div><span className="ew-muted">订单全链路 · 金蝶只读</span><h2 id="order-drawer-title">{orderNo}</h2><small>{shop} · {period}</small></div><button onClick={onClose}>关闭</button></header>
    {result.error?<p role="alert">读取失败：{result.error}。请关闭后重试。</p>:!r?<p role="status">正在读取已保存订单证据…</p>:<>
      <div className="ew-drawer-metrics">{[['订单金额',r.order_amount],['消费者实付',r.paid],['退款金额',r.refund],['平台费用',r.fees],['账户净收',r.net_receipt]].map(([label,value])=><div key={label}><small>{label}</small><strong>¥ {money(value)}</strong></div>)}</div>
      <div className="ec-chain-badges"><span>{r.business_label}丨{r.status||'订单状态待补'}</span><span>{r.destination||'资金去向待核对'}</span></div>
      <p className="ec-chain-note">按业务环节排列，不代表实际时间先后。金额仅覆盖已有证据；账户净收不等于银行到账或拟生成收款单金额。</p>
      <div className="ew-scroll ec-chain-scroll"><table className="ec-chain-table"><thead><tr><th>业务环节</th><th>日期／时间</th><th>关联单据／账户</th><th>金额／数量</th><th>核对状态</th></tr></thead><tbody>
        <ChainRow name="平台订单与支付" detailLabel="商品明细" date={<><small>创建</small><span>{r.created_at||'未取得'}</span><small>支付</small><span>{r.paid_at||'未取得'}</span></>} summary={orderNo} amount={`实付 ¥ ${money(r.paid)}`} status="已取得订单">
          <div className="ew-scroll"><table><thead><tr><th>子订单号</th><th>商品／SKU</th><th>数量</th><th>实付</th></tr></thead><tbody>{(r.items||[]).map((item,i)=><tr key={i}><td>{item.suborder_no||'未提供'}</td><td>{item.name}<small>{item.sku||'未提供 SKU'}</small></td><td>{item.quantity??'—'}</td><td>{money(item.paid)}</td></tr>)}</tbody></table></div>{!r.items?.length&&<p>未取得商品明细。</p>}<p>平台退款累计 ¥ {money(r.refund)}；退款类型与售后进度以售后证据为准。</p>
        </ChainRow>
        <ChainRow name="旺店通出库／发货" detailLabel="出库明细" date={ufirst&&!shipments.length?'不适用':<DateRange label="发货时间" values={shipments.map(s=>s.date)}/>} summary={shipments.length?<>{[...new Set(shipments.map(s=>s.shipment_no).filter(Boolean))].slice(0,2).join('、')||'单号未提供'}<small>{shippingCount} 张出库单 · {shipments.length} 行明细</small></>:ufirst?'U先不走旺店通':'未取得关联出库单'} amount={null} status={shipments.length?(ufirst?'与U先路径冲突，待核对':'已关联，待核对数量'):notApplicable}>
          {shipments.length?shipments.map((s,i)=><div className="ew-evidence" key={i}><strong>{s.shipment_no||'未提供单号'}</strong> · {s.status}<p>旺店通订单 {s.internal_order||'未提供'} · 原始订单 {s.original_order||'未提供'}</p><p>{s.name||s.sku} · {s.spec} · 数量 {s.quantity??'—'}</p><small>发货 {s.date||'未提供'}</small></div>):<p>{ufirst?'本业务不要求旺店通出库。':'缺少关联出库证据，不能据此判断未发货。'}</p>}
        </ChainRow>
        <ChainRow name="物流信息" detailLabel="物流单号" date={<><small>揽收／物流节点</small>未取得</>} summary={waybills.length?`${waybills.length} 个物流商／运单组合`:'未取得物流单号'} amount={null} status={waybills.length?'已取得运单':'待补物流证据'}>{waybills.map((s,i)=><div className="ew-evidence" key={i}><strong>{s.logistics_company||'未提供物流商'} · {s.logistics_no}</strong><p>关联出库 {s.shipment_no||'未提供'} · 运费尚未关联</p></div>)}{!waybills.length&&<p>不将订单号或出库单号当作物流单号；U先也可补充实际物流证据。</p>}</ChainRow>
        <ChainRow name="金蝶销售出库" date={notApplicable} summary={ufirst?'U先不经过此环节':'当前订单结果未包含销售出库来源链'} status={notApplicable}/>
        <ChainRow name="金蝶应收" detailLabel="应收明细" date={ufirst&&!ars.length?'不适用':<DateRange label="业务日期" values={ars.map(a=>a.date)}/>} summary={ars.length?ars.map(a=>a.bill_no).join('、'):ufirst?'U先直接汇总至总账':'未取得关联应收单'} amount={ars.length?`¥ ${money(r.ar_amount)}`:null} status={ars.length?(ufirst?'与U先路径冲突，待核对':r.ar_diff!=null&&r.ar_diff!==0?'金额有差异':'已有应收关联，来源链待核实'):notApplicable}>{ars.map((a,i)=><div className="ew-evidence" key={i}><strong>{a.bill_no}</strong> · {a.date||'未提供日期'} · ¥ {money(a.amount)}{a.has_red?' · 含红字':''}</div>)}<p>应收差额 ¥ {money(r.ar_diff)}；不把应收业务日期当作单据实际创建时间。</p></ChainRow>
        {['alipay','fund'].map(channel=>{
          const matched=events.filter(e=>e.channel===channel),actual=matched.filter(e=>!e.aggregate_copy),names=[...new Set(matched.map(e=>e.account_name).filter(Boolean))]
          return <ChainRow key={channel} detailLabel="收支明细" date={<DateRange label={actual.length||!matched.length?'入账时间':'映射记录时间'} values={(actual.length?actual:matched).map(e=>e.date)}/>} name={channel==='alipay'?'支付宝收支':'聚合账户收支'} summary={<>{names.join('、')||'账户名称待补'}<small>{matched.length?`${actual.length} 笔收支 · ${matched.length-actual.length} 笔映射`:'当前订单未关联该渠道流水'}</small></>} amount={actual.length?<>收入 ¥ {money(cashTotal(actual,'income'))}<small>支出 ¥ {money(cashTotal(actual,'expense'))}</small></>:null} status={actual.length?'已有流水，待核对':matched.length?'仅映射，不重复计款':'待核对覆盖范围'} initialOpen={initialTab==='cash'&&matched.length>0}>
            {matched.length?<CashEvidence events={matched} onOpenLedger={onOpenLedger}/>:<><p>未匹配流水不等于账户没有收支，也不表示金额为零。</p><button onClick={onOpenLedger}>查找订单流水</button></>}
          </ChainRow>
        })}
        <ChainRow name="金蝶收款单" date={notApplicable} summary={ufirst?'当前U先按总账汇总处理':'尚未取得收款单与本订单的来源关系'} status={notApplicable}/>
        <ChainRow name="应收核销" date={notApplicable} summary={ufirst?'不要求逐单应收核销':'尚未取得应收与收款的核销关系'} status={notApplicable}/>
        <ChainRow name="总账凭证" summary={ufirst?'待关联所属U先汇总批次及凭证':'尚未取得本订单与凭证的来源关系'} status="待关联凭证"/>
      </tbody></table></div>

    </>}
  </section></div>
}
