// [Change Log] Date: 2026-10-19 | Author: Codex | Version: V2.994 | 总账凭证批量审核与选择部分反审
import React, { useEffect, useRef, useState } from 'react'
import './voucherReview.css'

async function api(path, body) {
  const r = await fetch('/api/voucher-review'+path, {credentials:'same-origin', ...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})})
  const d = await r.json(); if (!r.ok) throw new Error(d.detail || '请求失败'); return d
}
const requestId = () => {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes=crypto.getRandomValues(new Uint8Array(16)); bytes[6]=(bytes[6]&15)|64; bytes[8]=(bytes[8]&63)|128
  const h=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')
  return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20)
}
const money = v => Number(v || 0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})
const statusName = {A:'创建',B:'审核中',C:'已审核',D:'重新审核',Z:'暂存'}
const columns = {subject:'主体',checker:'审核人',debit:'借方金额',credit:'贷方金额',accounts:'科目编码',currencies:'币别',rates:'汇率',original:'原币金额（按币别）'}
const defaults = ['subject','checker','debit','credit']
const extras = ['accounts','currencies','rates','original']
const empty = {books:['107'],month:'2026-09',status:'pending',creators:[],summary:''}

export default function VoucherReview({user}) {
  const pref = 'voucher-columns:'+user.name
  const [cols,setCols]=useState(()=>{try {return JSON.parse(localStorage.getItem(pref)) || defaults} catch{return defaults}})
  const [books,setBooks]=useState([]), [draft,setDraft]=useState(empty), [applied,setApplied]=useState(null)
  const [data,setData]=useState({rows:[],total:0,creators:[],counts:{}}), [page,setPage]=useState(1), [size,setSize]=useState(20)
  const [selected,setSelected]=useState([]), [loading,setLoading]=useState(false), [error,setError]=useState('')
  const [columnOpen,setColumnOpen]=useState(false), [detail,setDetail]=useState(null), [detailPage,setDetailPage]=useState(1), [detailLoading,setDetailLoading]=useState(false)
  const [confirm,setConfirm]=useState(null), [job,setJob]=useState(null), [history,setHistory]=useState(null), [aggregate,setAggregate]=useState({})
  const searchSeq=useRef(0), detailSeq=useRef(0), active=useRef(true)
  useEffect(()=>{active.current=true; api('/books').then(x=>setBooks(x.books)).catch(e=>setError(e.message)); const old=localStorage.getItem('voucher-job:'+user.name); if(old) api('/jobs/'+old).then(setJob).catch(()=>{}); return()=>{active.current=false}},[])
  useEffect(()=>{localStorage.setItem(pref,JSON.stringify(cols))},[cols,pref])
  useEffect(()=>{if(!job || !['queued','running'].includes(job.status))return; let polling=false; const t=setInterval(()=>{if(polling)return;polling=true;api('/jobs/'+job.id).then(setJob).catch(e=>setError(e.message)).finally(()=>{polling=false})},1800); return()=>clearInterval(t)},[job])
  useEffect(()=>{if(job?.status==='done' && applied) load(applied,page)},[job?.status])
  useEffect(()=>{
    if(!cols.some(c=>extras.includes(c)) || !data.rows.length) return
    let cancelled=false, timer
    const next={...aggregate}
    async function tick(){
      // 顺序请求，后台缓存全量分录汇总，避免并发打满金蝶。
      for(const row of data.rows){if(cancelled)return; if(['done','failed'].includes(next[row.id]?.status))continue; try{next[row.id]=await api('/'+row.id+'/summary')}catch(e){next[row.id]={status:'failed',error:e.message}}}
      if(!cancelled){setAggregate({...next}); if(Object.values(next).some(x=>x.status==='running')) timer=setTimeout(tick,4000)}
    }
    tick(); return()=>{cancelled=true;clearTimeout(timer)}
  },[data.rows,cols.join(',')])
  function change(k,v){setDraft(d=>({...d,[k]:v,...(['books','month'].includes(k)?{creators:[]}: {})}));setSelected([])}
  async function load(filter=draft,p=1,pageSize=size){
    const seq=++searchSeq.current;setLoading(true);setError('');setSelected([])
    try{const d=await api('/search',{...filter,page:p,size:pageSize});if(seq===searchSeq.current && active.current){setData(d);setApplied({...filter});setPage(p);setSize(pageSize);setAggregate({})}}
    catch(e){if(seq===searchSeq.current)setError(e.message)}finally{if(seq===searchSeq.current)setLoading(false)}
  }
  async function openDetail(row,p=1){
    const seq=++detailSeq.current;setDetail({header:row,rows:[],total:row.lines});setDetailPage(p);setDetailLoading(true)
    try{const d=await api('/'+row.id+'/entries?page='+p);if(seq===detailSeq.current)setDetail(d)}catch(e){setError(e.message)}finally{if(seq===detailSeq.current)setDetailLoading(false)}
  }
  const can = action => user.role==='admin' || user.perms?.['voucher_'+action]
  const running=job && ['queued','running'].includes(job.status)
  const eligible=(r,action)=>action==='audit' ? ['A','B'].includes(r.status) : r.status==='C' && r.tool_owned
  const chosen=data.rows.filter(r=>selected.includes(r.id))
  function ask(action){const rows=chosen.filter(r=>eligible(r,action));if(rows.length)setConfirm({action,rows,request_id:requestId()})}
  async function execute(){
    const body={request_id:confirm.request_id,action:confirm.action,items:confirm.rows.map(r=>({id:Number(r.id),fingerprint:r.fingerprint}))}
    setLoading(true);setError('')
    try{const j=await api('/jobs',body);localStorage.setItem('voucher-job:'+user.name,j.id);setJob({id:j.id,status:'queued',results:[]});setConfirm(null);setSelected([])}catch(e){setError(e.message)}finally{setLoading(false)}
  }
  const multi=(value,fn,options)=><details className="vr-multi" onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))e.currentTarget.open=false}} onKeyDown={e=>{if(e.key==='Escape')e.currentTarget.open=false}}><summary title={options.filter(([v])=>value.includes(v)).map(([,l])=>l).join('、')}>{value.length ? (options.find(([v])=>v===value[0])?.[1] || value[0]) + (value.length>1?' 等 '+value.length+' 项':'') : '全部'}<span>⌄</span></summary><div className="vr-options" role="listbox" aria-multiselectable="true">{options.length ? options.map(([v,l])=><button type="button" role="option" aria-label={l} aria-selected={value.includes(v)} key={v} onClick={e=>{e.preventDefault();fn(value.includes(v)?value.filter(x=>x!==v):[...value,v])}}><span className={'vr-check '+(value.includes(v)?'checked':'')}>{value.includes(v)?'✓':''}</span>{l}</button>):<p>查询后可选择</p>}</div></details>
  const shown = applied?.books?.length>1 && !cols.includes('subject') ? ['subject',...cols] : cols
  const dirty=applied && JSON.stringify(draft)!==JSON.stringify(applied)
  function mark(text){const t=String(text||'');const q=applied?.summary?.trim();if(!q)return t;return t.split(q).map((x,i)=><React.Fragment key={i}>{i>0 && <mark>{q}</mark>}{x}</React.Fragment>)}
  return <div className="vr">
    <div className="head"><div><div className="h-title">凭证审核</div><div className="vr-sub">金蝶实时凭证 · 按凭证批量处理，逐张记录结果</div></div><button className="btn" onClick={()=>api('/jobs').then(x=>setHistory(x.jobs)).catch(e=>setError(e.message))}>操作记录</button></div>
    <div className="vr-body">
      <div className="vr-filter">
        <label>主体 <small>可多选</small>{multi(draft.books,v=>change('books',v),books.map(b=>[b.code,b.name+' · '+b.code]))}</label>
        <label>会计期间<input type="month" value={draft.month} onChange={e=>change('month',e.target.value)}/></label>
        <label>审核状态<select value={draft.status} onChange={e=>change('status',e.target.value)}>{[['pending','未审核（创建 + 审核中）'],['A','创建'],['B','审核中'],['C','已审核'],['all','全部']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
        <label>制单人 <small>可多选</small>{multi(draft.creators,v=>change('creators',v),data.creators.map(x=>[x,x||'未填写']))}</label>
        <label className="vr-keyword">摘要包含<input placeholder="输入摘要关键词" value={draft.summary} onChange={e=>change('summary',e.target.value)} onKeyDown={e=>e.key==='Enter'&&load()}/></label>
        <div className="vr-filter-actions"><button className="btn primary" disabled={loading || !draft.books.length} onClick={()=>load()}> {loading?'查询中…':'查询'}</button><button className="btn" onClick={()=>{setDraft(empty);setSelected([]);load(empty)}}>重置</button></div>
      </div>
      {error && <div role="alert" className="vr-error">{error}</div>}
      <div className="vr-toolbar"><div className="vr-tabs">{[['pending','未审核',(data.counts.A||0)+(data.counts.B||0)],['C','已审核',data.counts.C||0],['all','全部',null]].map(([v,l,n])=><button key={v} className={draft.status===v?'active':''} onClick={()=>{const f={...draft,status:v};setDraft(f);load(f)}}>{l} {n!==null && <b>{n}</b>}</button>)}</div><div className="vr-columns"><button className="btn" onClick={()=>setColumnOpen(!columnOpen)}>显示列 ▾</button>{columnOpen && <div className="vr-colpanel"><strong>固定显示</strong><p>日期、凭证号、明细行数、制单人、状态</p>{Object.entries(columns).map(([k,l])=><label key={k}><input type="checkbox" checked={cols.includes(k)} onChange={e=>setCols(x=>e.target.checked?[...x,k]:x.filter(c=>c!==k))}/>{l}</label>)}<button className="btn" onClick={()=>setCols(defaults)}>恢复默认</button></div>}</div></div>
      {dirty && <div className="vr-note">筛选条件已修改，请点击查询。当前仍显示上一次查询结果。</div>}
      <div className="vr-table-wrap" aria-busy={loading}><table className="vr-table"><thead><tr><th><input type="checkbox" aria-label="选择本页" disabled={loading||dirty||running} checked={data.rows.length>0&&selected.length===data.rows.length} onChange={e=>setSelected(e.target.checked?data.rows.map(r=>r.id):[])}/></th>{shown.includes('subject')&&<th>主体</th>}<th>日期</th><th>凭证号</th><th>明细行数</th><th>制单人</th><th>状态</th>{shown.filter(c=>c!=='subject').map(c=><th key={c}>{columns[c]}</th>)}<th>操作</th></tr></thead><tbody>{data.rows.map(r=><tr key={r.id} className={selected.includes(r.id)?'selected':''}><td><input aria-label={'选择'+r.group+'-'+r.number} type="checkbox" disabled={loading||dirty||running} checked={selected.includes(r.id)} onChange={e=>setSelected(x=>e.target.checked?[...x,r.id]:x.filter(i=>i!==r.id))}/></td>{shown.includes('subject')&&<td title={r.subject}>{r.subject}</td>}<td>{String(r.date).slice(0,10)}</td><td><button className="vr-link" onClick={()=>openDetail(r)}>{r.group}-{r.number}</button></td><td className="vr-num">{Number(r.lines).toLocaleString()}</td><td>{r.creator}</td><td><span className={'vr-status s'+r.status}>{statusName[r.status]||r.status}</span>{r.tool_owned&&<small className="vr-owned">工作台审核</small>}</td>{shown.filter(c=>c!=='subject').map(c=><td key={c} className={['debit','credit','original'].includes(c)?'vr-num':''} title={aggregate[r.id]?.error||String(aggregate[r.id]?.[c]||'')}>{extras.includes(c)?(aggregate[r.id]?.[c] || (aggregate[r.id]?.status==='failed'?'汇总失败':'计算中…')):['debit','credit'].includes(c)?money(r[c]):r[c]||'—'}</td>)}<td><button className="vr-link" onClick={()=>openDetail(r)}>明细</button></td></tr>)}</tbody></table>{!data.rows.length&&<div className="vr-empty">{loading?'正在从金蝶查询…':applied?'没有符合条件的凭证':'选择主体与期间，查询金蝶凭证'}</div>}</div>
      <div className="vr-footer"><span>共 {data.total.toLocaleString()} 张 · 每页 <select aria-label="每页张数" value={size} disabled={loading} onChange={e=>applied?load(applied,1,Number(e.target.value)):setSize(Number(e.target.value))}>{[20,50,100].map(n=><option key={n} value={n}>{n}</option>)}</select> 张</span><div><button className="btn" disabled={loading||page<=1||!applied} onClick={()=>load(applied,page-1)}>上一页</button> <span>{page} / {Math.max(1,Math.ceil(data.total/size))}</span> <button className="btn" disabled={loading||page*size>=data.total||!applied} onClick={()=>load(applied,page+1)}>下一页</button></div></div>
      <div className="vr-batch"><span>已选 <b>{selected.length}</b> 张</span><button className="btn primary" disabled={!can('audit')||!chosen.some(r=>eligible(r,'audit'))||running||dirty||loading} onClick={()=>ask('audit')}>批量审核</button><button className="btn" disabled={!can('unaudit')||!chosen.some(r=>eligible(r,'unaudit'))||running||dirty||loading} onClick={()=>ask('unaudit')}>反审核选中凭证</button><small>反审仅限工作台成功审核且当前审核记录一致的凭证。</small></div>
      {job&&<div className="vr-results"><strong>{running?'处理中，请勿重复提交':'本次处理结果'}</strong><span> · {job.id.slice(0,8)}</span>{job.results.map((r,i)=><div key={i} className={'vr-result '+r.status}><b>{r.before ? r.before.group+'-'+r.before.number : '凭证 ID '+r.id}</b><span>{r.message}</span></div>)}{!running&&<button className="btn" onClick={()=>setJob(null)}>收起</button>}</div>}
    </div>
    {confirm&&<div className="vr-overlay"><div className="vr-dialog" role="dialog" aria-modal="true"><h3>确认{confirm.action==='audit'?'审核':'反审核'} {confirm.rows.length} 张凭证</h3><p>此操作将真实写入金蝶。系统会逐张核对实时状态，并记录每张凭证的处理结果。</p>{confirm.rows.length<chosen.length&&<p className="vr-note">已排除 {chosen.length-confirm.rows.length} 张不符合本操作条件的凭证。</p>}<div className="vr-confirm-list">{confirm.rows.map(r=><div key={r.id}>{r.subject} · {r.group}-{r.number} · {r.lines} 行</div>)}</div><p>{confirm.action==='audit'?'创建状态的凭证将先提交，再审核。':'反审成功后，凭证将退出已审核状态。'}</p><div className="vr-dialog-actions"><button className="btn" disabled={loading} onClick={()=>setConfirm(null)}>取消</button><button className="btn primary" disabled={loading} onClick={execute}>{loading?'提交中…':'确认执行'}</button></div></div></div>}
    {detail&&<div className="vr-overlay" onClick={()=>{detailSeq.current++;setDetail(null)}}><div className="vr-drawer" onClick={e=>e.stopPropagation()}><div className="vr-drawer-head"><div><h3>{detail.header.group}-{detail.header.number}</h3><span>{detail.header.subject} · {String(detail.header.date).slice(0,10)} · {statusName[detail.header.status]}</span></div><button className="btn" onClick={()=>{detailSeq.current++;setDetail(null)}}>关闭</button></div><div className="vr-detail-meta">制单人 {detail.header.creator}　审核人 {detail.header.checker||'—'}　共 {Number(detail.total||0).toLocaleString()} 行<br/>借方 {money(detail.header.debit)}　贷方 {money(detail.header.credit)}</div><div className="vr-table-wrap"><table className="vr-table"><thead><tr>{['行号','摘要','科目编码','科目名称','币别','汇率','原币金额','借方金额','贷方金额'].map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{detail.rows.map((r,i)=><tr key={i}><td>{r.seq}</td><td className="vr-summary">{mark(r.summary)}</td><td>{r.account}</td><td>{r.account_name}</td><td>{r.currency}</td><td>{r.rate}</td><td className="vr-num">{money(r.original)}</td><td className="vr-num">{money(r.debit)}</td><td className="vr-num">{money(r.credit)}</td></tr>)}</tbody></table>{detailLoading&&<div className="vr-empty">加载明细…</div>}</div><div className="vr-footer"><button className="btn" disabled={detailLoading||detailPage<=1} onClick={()=>openDetail(detail.header,detailPage-1)}>上一页</button><span>{detailPage} / {Math.max(1,Math.ceil(detail.total/50))}</span><button className="btn" disabled={detailLoading||detailPage*50>=detail.total} onClick={()=>openDetail(detail.header,detailPage+1)}>下一页</button></div></div></div>}
    {history&&<div className="vr-overlay"><div className="vr-dialog vr-history"><h3>操作记录</h3>{!history.length&&<p>暂无记录</p>}{history.map(j=><div className="vr-history-item" key={j.id}><b>{j.action==='audit'?'审核':'反审'}</b> · {j.operator} · {j.created.slice(0,19).replace('T',' ')} · {j.status==='done'?'已结束':'处理中/待核实'}{j.results.map((r,i)=><div key={i} className={'vr-result '+r.status}>{r.id} · {r.message}</div>)}</div>)}<button className="btn" onClick={()=>setHistory(null)}>关闭</button></div></div>}
  </div>
}
