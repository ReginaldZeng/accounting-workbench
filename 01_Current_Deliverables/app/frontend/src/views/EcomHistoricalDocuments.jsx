import React, { useRef, useState } from 'react'
import { requestJson, query, post, useResource, count } from './ecomWorkbenchApi.js'

export default function EcomHistoricalDocuments({ shop, canEdit, onChanged }) {
  const input=useRef(null)
  const [revision,setRevision]=useState(0),[busy,setBusy]=useState(false),[message,setMessage]=useState('')
  const result=useResource(shop?`/api/ec/documents?${query({shop})}`:null,revision)
  async function run(path,options) {
    setBusy(true);setMessage('正在后台处理，完成后自动更新关联；原始收支不变。')
    try {
      const started=await requestJson(path,options)
      while (true) {
        await new Promise(resolve=>setTimeout(resolve,1500))
        const job=await requestJson(`/api/ec/documents/jobs/${started.job_id}`)
        if (job.status==='failed') throw new Error(job.error)
        if (job.status==='complete') {
          const r=job.result
          setMessage(`处理完成：${r.added!==undefined?`新增 ${count(r.added)} 条单据，重复 ${count(r.duplicates)} 条，版本冲突 ${count(r.conflicts)} 条；`:''}更新 ${count(r.reconcile?.updated??r.updated)} 笔流水核对结果。`)
          setRevision(v=>v+1);onChanged?.();break
        }
      }
    } catch(e) {setMessage(`${e.message}。若连接中断，请刷新查看已入库资料，再点重新核对。`)}
    finally {setBusy(false)}
  }
  function upload(e) {
    const files=Array.from(e.target.files||[]);e.target.value='';if (!files.length)return
    const body=new FormData();body.append('shop',shop);files.forEach(f=>body.append('files',f))
    run('/api/ec/documents/import',{method:'POST',body})
  }
  return <section className="ew-panel"><header><div><h2>跨期单据资料库</h2><p>按列识别订单、子订单、退款及出库资料。上传一次，跨月关联；账户流水在对应账户导入。</p></div><div className="ew-prep-actions"><input ref={input} hidden type="file" accept=".xlsx" multiple onChange={upload}/><button disabled={!canEdit||!shop||busy} onClick={()=>input.current?.click()}>导入历史资料</button><button disabled={!canEdit||busy} onClick={()=>run('/api/ec/documents/reconcile',post({}))}>重新核对流水</button></div></header>
    {(message||result.error)&&<p className="ew-notice" role="status">{message||result.error}</p>}
    <details className="ew-padding"><summary>已保存 {count(result.data?.files?.length)} 个来源文件 · 查看期间与来源</summary><div className="ew-scroll"><table><thead><tr><th>来源文件</th><th>识别类型</th><th>有效行数</th><th>入库时间</th></tr></thead><tbody>{result.data?.files?.map(f=><tr key={f.id}><td>{f.filename}</td><td>{f.kinds?.split(',').map(k=>({order:'主订单',item:'子订单',refund:'退款',wdt:'出库'}[k]||k)).join('、')}</td><td>{count(f.row_count)}</td><td>{f.ts}</td></tr>)}</tbody></table></div><p>{result.data?.coverage?.map(c=>`${c.period} ${ {order:'主订单',item:'子订单',refund:'退款',wdt:'出库'}[c.kind]||c.kind} ${count(c.rows)}条`).join('；')}</p></details>
  </section>
}
