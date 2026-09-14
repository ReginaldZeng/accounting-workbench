import React, { useEffect, useRef, useState } from 'react'
import { ecKdRefresh } from '../api.js'
import { requestJson, wb, query, count, useResource } from './ecomWorkbenchApi.js'
import EcomHistoricalDocuments from './EcomHistoricalDocuments.jsx'

export default function EcomPreparation({period,shop,shopInfo,revision,canEdit,refresh,notify,onBasic}) {
  const result=useResource(`/api/ec/workbench/sources?${query({period,shop})}`,revision)
  const [busy,setBusy]=useState(false),[inbox,setInbox]=useState(null),[source,setSource]=useState(null)
  const [accounts,setAccounts]=useState({}),[autoResults,setAutoResults]=useState(null)
  const input=useRef(null),target=useRef(null),autoInput=useRef(null)
  const data=result.data, rows=data?.sources||[]
  useEffect(()=>{
    if (!data?.kingdee?.refreshing) return
    const timer=setInterval(refresh,5000);return()=>clearInterval(timer)
  },[data?.kingdee?.refreshing,refresh])
  async function act(fn) {
    setBusy(true)
    try {await fn();refresh()} catch(e) {notify(e.message)} finally {setBusy(false)}
  }
  function choose(row) {target.current=row;input.current?.click()}
  function uploadAuto(e) {
    const files=Array.from(e.target.files||[]);e.target.value=''
    if(!files.length) return
    setAutoResults(null)
    act(async()=>{
      const body=new FormData();files.forEach(f=>body.append('files',f))
      body.append('period',period);body.append('shop',shop)
      const r=await wb('upload-auto',{method:'POST',body})
      setAutoResults(r.results||[])
    })
  }
  function pickInbox() {
    setAutoResults(null)
    act(async()=>{
      const body=new FormData();body.append('period',period);body.append('shop',shop)
      const r=await wb('inbox-import',{method:'POST',body})
      setAutoResults(r.results||[]);setInbox(null)
    })
  }
  function upload(e) {
    const files=Array.from(e.target.files||[]);e.target.value=''
    if (!files.length) return
    const row=target.current, cash=['alipay','fund'].includes(row.kind)
    act(async()=>{
      const body=new FormData();files.forEach(f=>body.append('files',f))
      if(cash) {
        const id=accounts[row.kind]||(row.accounts?.length===1?row.accounts[0].id:'')
        if(!id) throw new Error('请先选择本行的具体账户')
        body.append('account_id',id);body.append('background','true')
        let r=await requestJson('/api/ec/flows/import',{method:'POST',body})
        if(r.job_id) {
          notify('流水已接收，后台解析完成后更新清单，请勿重复上传。')
          while(true) {
            await new Promise(resolve=>setTimeout(resolve,1500))
            const job=await requestJson(`/api/ec/flows/import-status/${r.job_id}`)
            if(job.status==='failed')throw new Error(job.error||'解析失败')
            if(job.status==='complete')break
          }
        }
      } else {
        body.append('period',period);body.append('shop',shop);body.append('kind',row.kind)
        await wb('upload',{method:'POST',body})
      }
      notify('资料已保存，清单已更新；原始记录保留，未写入金蝶。')
    })
  }
  return <section className="ew-panel ew-preparation">
    <header><div><h2>月结资料清单</h2><p>{shopInfo?.name||shop} · {period}</p></div><div style={{display:'flex',gap:8}}>{canEdit&&<button disabled={busy} onClick={()=>autoInput.current?.click()} title="一次选多个文件，按表头自动识别订单/子订单/退款/旺店通，逐个入库">批量上传 · 自动识别</button>}<button disabled={busy} onClick={()=>act(async()=>setInbox(await wb(`inbox?${query({period,shop})}`)))}>查看公盘</button></div></header>
    <input ref={input} type="file" hidden multiple accept=".xlsx,.xls,.zip" onChange={upload}/>
    <input ref={autoInput} type="file" hidden multiple accept=".xlsx,.xls,.zip" onChange={uploadAuto}/>
    {result.error&&<p className="ew-notice" role="alert">读取失败：{result.error}。齐套状态暂不可用。<button onClick={refresh}>重试</button></p>}
    <div className="ew-scroll ew-source-table"><table><thead><tr><th>资料类型</th><th>准备状态</th><th>有效行数</th><th>来源文件</th><th>最近更新</th><th>操作</th></tr></thead><tbody>
      {!result.loading&&!result.error&&rows.map(row=>{
        const cash=['alipay','fund'].includes(row.kind), kd=row.kind==='kingdee'
        const account=accounts[row.kind]||(row.accounts?.length===1?row.accounts[0].id:'')
        const status=!row.available?'待准备':row.state==='ready'?'已准备':'待核对'
        return <tr key={row.kind}><td><strong>{row.label}</strong>{cash&&row.accounts?.length>1&&<select aria-label={`${row.label}账户`} value={account} onChange={e=>setAccounts({...accounts,[row.kind]:e.target.value})}><option value="">选择账户</option>{row.accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select>}{cash&&row.accounts?.length===1&&<small>{row.accounts[0].name}</small>}</td>
          <td><span className={`ew-status ${row.state}`}>{status}</span>{row.warnings?.length>0&&<small title={row.warnings.join('；')}>{row.warnings[0]}</small>}</td>
          <td className="ew-source-number">{row.available?count(row.rows):'—'}</td>
          <td>{kd?'金蝶只读缓存':<button className="ew-link" onClick={()=>setSource(row)}>{row.file_count||0} 个 · 查看</button>}</td>
          <td>{row.imported_at||'—'}</td>
          <td>{kd?<button disabled={!canEdit||busy||data?.kingdee?.refreshing} onClick={()=>act(async()=>{await ecKdRefresh(period);notify('已发起金蝶应收只读查询。')})}>{data?.kingdee?.refreshing?'读取中…':'只读同步'}</button>:cash&&!row.accounts?.length?<button onClick={onBasic}>关联账户</button>:<button disabled={!canEdit||busy||(cash&&!account)} onClick={()=>choose(row)}>{busy?'处理中…':row.available?'补充资料':'导入资料'}</button>}</td>
        </tr>
      })}
      {result.loading&&<tr><td colSpan="6" className="ew-empty">正在读取已保存资料…</td></tr>}
      {!result.loading&&!result.error&&!rows.length&&<tr><td colSpan="6" className="ew-empty">本店尚未配置所需资料，不能判定齐套。<button onClick={onBasic}>前往基础资料设置</button></td></tr>}
    </tbody></table></div>
    {inbox&&<div className="ew-notice">{inbox.message}{inbox.files?.map(f=><span key={f.name}>{f.name}<br/></span>)}{canEdit&&inbox.configured&&inbox.files?.length>0&&<div style={{marginTop:8}}><button disabled={busy} onClick={pickInbox} title="读公盘该期间/店铺的文件，按表头自动识别订单/子订单/退款/旺店通入库；只读原文件不改不删">一键取件 · 自动识别入库（{inbox.files.length} 个）</button></div>}</div>}
    {autoResults&&<div className="ew-notice"><b>批量识别结果</b>（识别只看列结构、不看文件名；只读原文件）{autoResults.map((x,i)=><span key={i} style={{display:'block',marginTop:3}}>{x.name}：{x.ok?<>识别为「{x.label}」 · {count(x.rows)} 行{x.duplicate?'（内容重复，已跳过）':''}{x.warnings?.length?` · ${x.warnings.join('、')}`:''}</>:<span style={{color:'var(--red,#c0392b)'}}>未入库 · {x.error||x.kind||'无法识别'}</span>}</span>)}</div>}
    {source&&<SourceDrawer source={source} shop={shop} canEdit={canEdit} onChanged={refresh} onClose={()=>setSource(null)}/>}
  </section>
}

function SourceDrawer({source,shop,canEdit,onChanged,onClose}) {
  const ref=useRef(null)
  useEffect(()=>{
    const previous=document.activeElement,overflow=document.body.style.overflow
    document.body.style.overflow='hidden';ref.current?.focus()
    const key=e=>{if(e.key==='Escape')onClose();if(e.key==='Tab'){const nodes=ref.current?.querySelectorAll('button,input,select,summary');if(!nodes?.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&(document.activeElement===first||document.activeElement===ref.current)){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}}
    document.addEventListener('keydown',key)
    return()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key);previous?.focus()}
  },[onClose])
  return <div className="ew-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={ref} tabIndex="-1" className="ew-drawer ew-source-drawer" role="dialog" aria-modal="true" aria-labelledby="preparation-source-title"><header><h2 id="preparation-source-title">{source.label} · 来源</h2><button onClick={onClose}>关闭</button></header><ul className="ew-source-files">{source.files?.map((f,i)=><li key={i}>{f}</li>)}{!source.files?.length&&<li>{source.legacy?'原版本核算快照已保留':'本期尚无来源文件'}</li>}</ul>{!['alipay','fund','kingdee'].includes(source.kind)&&<details><summary>补充跨期资料与查看历史来源</summary><EcomHistoricalDocuments shop={shop} canEdit={canEdit} onChanged={onChanged}/></details>}</section></div>
}
