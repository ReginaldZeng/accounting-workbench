import React, { useEffect, useRef, useState } from 'react'
import { ecKdRefresh } from '../api.js'
import { requestJson, wb, query, count, useResource } from './ecomWorkbenchApi.js'
import EcomHistoricalDocuments from './EcomHistoricalDocuments.jsx'

export default function EcomPreparation({period,shop,shopInfo,revision,canEdit,refresh,notify,onBasic}) {
  // 自己的 tick + retainKey：金蝶应收同步期间每 5 秒悄悄重读这张清单，不动全页刷新号；重读时保留上一份，清单不闪
  const [tick,setTick]=useState(0)
  const result=useResource(`/api/ec/workbench/sources?${query({period,shop})}`,`${revision}:${tick}`,`prep:${period}:${shop}`)
  const [busy,setBusy]=useState(false),[showPickup,setShowPickup]=useState(false),[source,setSource]=useState(null)
  const [accounts,setAccounts]=useState({}),[autoResults,setAutoResults]=useState(null)
  const input=useRef(null),target=useRef(null),autoInput=useRef(null)
  const data=result.data, rows=data?.sources||[], pickup=data?.pickup
  useEffect(()=>{
    if (!data?.kingdee?.refreshing) return
    const timer=setInterval(refresh,5000);return()=>clearInterval(timer)
  },[data?.kingdee?.refreshing,refresh])
  async function act(fn) {
    setBusy(true)
    try {await fn();refresh()} catch(e) {notify(e.message)} finally {setBusy(false)}
  }
  function choose(row) {target.current=row;input.current?.click()}
  // 抖音：金蝶应收只读同步在后台跑。是不是还在跑、有没有失败，都以服务器说的为准（和收款核销页同一个来源），
  // 不在页面上自己猜；在跑就每 5 秒重读一次，读失败也照样 5 秒后再试
  const dyAr=data?.douyin_ar,dyJob=data?.douyin_job,syncing=!!dyJob?.running
  useEffect(()=>{
    if(!syncing||result.loading) return
    const timer=setTimeout(()=>setTick(t=>t+1),5000);return()=>clearTimeout(timer)
  },[syncing,result.loading,tick])
  async function syncDouyinAr(quiet) {
    const body=new FormData();body.append('period',period);body.append('shop',shop)
    try {await requestJson('/api/ec/douyin/ar-refresh',{method:'POST',body})} finally {setTick(t=>t+1)}
    if(quiet!==true) notify('已发起金蝶应收只读同步，通常十几秒，单子多时一两分钟；完成后下面的同步时间会更新。')
  }
  const spanText=s=>!s?.length?'—':s.length===1?`${s[0]} 起`:s[0]===s[1]?s[0]:`${s[0]} 至 ${s[1].slice(5)}`
  const douyin=shopInfo?.platform==='抖音'
  // 抖音两份动账明细：只看表头认类型，文件里有几个月就按月各存一份
  async function uploadDouyin(files) {
    // 一个文件发一个请求：四类资料一起选有五六十 MB，合成一个请求容易被网关挡回；也免得一个文件出错整批没有结果
    const all=[]
    for(const f of files) {
      const body=new FormData();body.append('files',f);body.append('shop',shop)
      try {const r=await requestJson('/api/ec/douyin/upload',{method:'POST',body});all.push(...(r.results||[]))}
      catch(e) {all.push({name:f.name,ok:false,error:e instanceof TypeError||/^请求失败（\d+）$/.test(e.message)?'这个文件没传上去（网络中断，或文件太大被服务器挡回），请重传；还不行就先解压、分开传':e.message})}
      setAutoResults([...all])
    }
    // 资料有新内容入库，金蝶应收跟着自动同步一次（只读），不用人再去点
    if(all.some(x=>x.ok&&!x.duplicate)) try {await syncDouyinAr(true)} catch(e) {
      if(!/正在同步/.test(e.message)) notify('资料已入库，但金蝶应收自动同步没发起成功，请点清单下面的同步按钮。')
    }
  }
  function uploadAuto(e) {
    const files=Array.from(e.target.files||[]);e.target.value=''
    if(!files.length) return
    setAutoResults(null)
    if(douyin) {act(()=>uploadDouyin(files));return}
    act(async()=>{
      const body=new FormData();files.forEach(f=>body.append('files',f))
      body.append('period',period);body.append('shop',shop)
      const r=await wb('upload-auto',{method:'POST',body})
      setAutoResults(r.results||[])
    })
  }
  function upload(e) {
    const files=Array.from(e.target.files||[]);e.target.value=''
    if (!files.length) return
    const row=target.current, cash=['alipay','fund'].includes(row.kind)
    if(row.kind.startsWith('dy_')) {setAutoResults(null);act(()=>uploadDouyin(files));return}
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
    <header><div><h2>月结资料清单</h2><p>{shopInfo?.name||shop} · {period}</p></div><div style={{display:'flex',gap:8}}>{canEdit&&<button disabled={busy} onClick={()=>autoInput.current?.click()} title={douyin?"一次选多个文件（csv、zip 或 xlsx），按表头自动识别动账明细／账户流水／抖店订单导出／旺店通订单明细，按月入库；带密码的压缩包要先解压":"一次选多个文件，按表头自动识别订单/子订单/退款/旺店通，逐个入库"}>批量上传 · 自动识别</button>}<button disabled={busy} onClick={()=>setShowPickup(v=>!v)} title="公盘取件机的运行状态与本店本期自动接入记录">取件记录</button></div></header>
    <input ref={input} type="file" hidden multiple accept=".xlsx,.xls,.zip,.csv" onChange={upload}/>
    <input ref={autoInput} type="file" hidden multiple accept=".xlsx,.xls,.zip,.csv" onChange={uploadAuto}/>
    {pickup&&<div className="ew-notice" style={{display:'flex',alignItems:'center',gap:6}}>{
      pickup.deployed&&pickup.alive
        ? <><span style={{color:'var(--green,#32a783)'}}>●</span><span>电商资料自动接入中 · 取件机最近扫描 {pickup.scan_at||'—'}{pickup.last_at?`　·　本店本期最近接入 ${pickup.last_at}（${pickup.files} 份）`:douyin?'　·　抖音店的资料不走公盘，请在这里上传':'　·　本店本期暂无自动接入'}</span></>
        : pickup.deployed
          ? <><span style={{color:'var(--amber,#a35a00)'}}>●</span><span>取件机最近 {pickup.scan_at||'—'} 后未再报平安（可能关机或任务停）· 期间可用「批量上传」兜底</span></>
          : <><span style={{color:'var(--ink-3,#8a8f99)'}}>○</span><span>公盘取件机未部署 · 可用「批量上传 · 自动识别」手工准备</span></>
    }</div>}
    {result.error&&<p className="ew-notice" role="alert">读取失败：{result.error}。齐套状态暂不可用。<button onClick={refresh}>重试</button></p>}
    <div className="ew-scroll ew-source-table"><table><thead><tr><th>资料类型</th><th>准备状态</th><th>有效行数</th>{douyin&&<th>数据日期</th>}<th>来源文件</th><th>上传人</th><th>最近更新</th><th>操作</th></tr></thead><tbody>
      {data&&rows.map(row=>{
        const cash=['alipay','fund'].includes(row.kind), kd=row.kind==='kingdee'
        const account=accounts[row.kind]||(row.accounts?.length===1?row.accounts[0].id:'')
        const status=!row.available?(row.optional?'按需补充':'待准备'):row.state==='ready'?'已准备':'待核对'
        return <tr key={row.kind}><td><strong>{row.label}</strong>{row.purpose&&<small>{row.purpose}</small>}{cash&&row.accounts?.length>1&&<select aria-label={`${row.label}账户`} value={account} onChange={e=>setAccounts({...accounts,[row.kind]:e.target.value})}><option value="">选择账户</option>{row.accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select>}{cash&&row.accounts?.length===1&&<small>{row.accounts[0].name}</small>}</td>
          <td><span className={`ew-status ${row.state}`}>{status}</span>{row.warnings?.length>0&&<small title={row.warnings.join('；')}>{row.warnings[0]}</small>}</td>
          <td className="ew-source-number">{row.available?count(row.rows):'—'}</td>
          {douyin&&<td>{spanText(row.span)}</td>}
          <td className="ew-src-files">{kd?'金蝶只读缓存':<button className="ew-link" onClick={()=>setSource(row)}>{row.file_count||0} 个 · 查看</button>}</td>
          <td>{row.by||'—'}</td>
          <td className="ew-src-time">{row.imported_at||'—'}</td>
          <td>{kd?<button disabled={!canEdit||busy||data?.kingdee?.refreshing} onClick={()=>act(async()=>{await ecKdRefresh(period);notify('已发起金蝶应收只读查询。')})}>{data?.kingdee?.refreshing?'读取中…':'只读同步'}</button>:cash&&!row.accounts?.length?<button onClick={onBasic}>关联账户</button>:<button disabled={!canEdit||busy||(cash&&!account)} onClick={()=>choose(row)}>{busy?'处理中…':row.available?'补充资料':'导入资料'}</button>}</td>
        </tr>
      })}
      {result.loading&&!data&&<tr><td colSpan={douyin?8:7} className="ew-empty">正在读取已保存资料…</td></tr>}
      {data&&!rows.length&&<tr><td colSpan={douyin?8:7} className="ew-empty">本店尚未配置所需资料，不能判定齐套。<button onClick={onBasic}>前往基础资料设置</button></td></tr>}
    </tbody></table></div>
    {douyin&&data&&<p className="ew-muted ew-padding" style={{display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}><span>金蝶应收不用准备，系统只读同步本页所选月份（传完资料会自动同步一次）：{syncing?'正在同步…':dyAr?`${count(dyAr.bills)} 张 · ${dyAr.since} 起 · ${dyAr.by||'—'} ${dyAr.ts} 同步`:'还没同步过'}</span>{!syncing&&dyJob?.error&&<span className="ew-open-red">{dyJob.error}{dyAr?`（现在看到的还是 ${dyAr.ts} 那次的）`:''}</span>}<button disabled={!canEdit||busy||syncing} title={canEdit?'只查询金蝶，不改任何单据':'需要电商对账的上传/跑批权限'} onClick={()=>act(()=>syncDouyinAr())}>{syncing?'同步中…':dyAr?'重新同步':'同步'}</button></p>}
    {showPickup&&<div className="ew-notice"><b>公盘取件机 · 取件记录</b>{pickup?.deployed?<>
      <div style={{marginTop:4}}>状态：{pickup.alive?'在跑':'已静默'} · 最近扫描 {pickup.scan_at||'—'}{pickup.host?` · ${pickup.host}`:''}</div>
      <div style={{marginTop:4}}>上轮全盘扫描：接入 {pickup.last?.ingested??'—'} 份 · 去重 {pickup.last?.duplicate??'—'} · 跳过/认不出 {pickup.last?.unresolved??'—'}{pickup.last?.shops?.length?` · 涉及 ${pickup.last.shops.join('、')}`:''}</div>
      <div style={{marginTop:4}}>本店本期已由取件机接入 {pickup.files||0} 份{pickup.last_at?`，最近 ${pickup.last_at}`:''}。</div>
      {douyin&&<div style={{marginTop:4}}><b>抖音店的资料目前不走公盘</b>，放进公盘文件夹不会接入，请用上面的「批量上传 · 自动识别」。</div>}<div style={{marginTop:4,color:'var(--ink-2)'}}>把文件放到公盘「年 / 年月 / 店铺-数据」文件夹即自动接入，无需手工。资金流水（支付宝/聚合）与金蝶不走此通道。若某店文件「认不出店铺」，去基础资料·店铺对照给该店补「公盘文件夹名」。</div>
    </>:<div style={{marginTop:4}}>未部署：服务器还没收到取件机回报。当前可用「批量上传 · 自动识别」或按行「补充资料」手工准备。</div>}</div>}
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
  return <div className="ew-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={ref} tabIndex="-1" className="ew-drawer ew-source-drawer" role="dialog" aria-modal="true" aria-labelledby="preparation-source-title"><header><h2 id="preparation-source-title">{source.label} · 来源</h2><button onClick={onClose}>关闭</button></header><ul className="ew-source-files">{source.files?.map((f,i)=><li key={i}>{f}</li>)}{!source.files?.length&&<li>{source.legacy?'原版本核算快照已保留':'本期尚无来源文件'}</li>}</ul>{!['alipay','fund','kingdee'].includes(source.kind)&&!source.kind.startsWith('dy_')&&<details><summary>补充跨期资料与查看历史来源</summary><EcomHistoricalDocuments shop={shop} canEdit={canEdit} onChanged={onChanged}/></details>}</section></div>
}
