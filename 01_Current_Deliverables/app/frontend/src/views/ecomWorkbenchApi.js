import { useEffect, useState } from 'react'
export async function requestJson(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options })
  const body = await response.json().catch(() => ({}))
  if (!response.ok || body.ok === false) throw new Error(body.detail || body.msg || `请求失败（${response.status}）`)
  return body
}
export const query = values => new URLSearchParams(Object.entries(values).filter(([,v]) => v !== '' && v != null)).toString()
export const wb = (path, options) => requestJson(`/api/ec/workbench/${path}`, options)
export const post = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const money = value => value == null || value === '' ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const count = value => value == null ? '—' : Number(value).toLocaleString('zh-CN')
export const percent = value => value == null ? '—' : `${(Number(value)*100).toFixed(2)}%`
export function useResource(url, revision = 0, retainKey = '') {
  const [state, setState] = useState({ data: null, loading: true, error: '', url:null, revision:null, retainKey:'' })
  useEffect(() => {
    if (!url) { setState({ data: null, loading: false, error: '',url,revision,retainKey }); return }
    const controller = new AbortController()
    setState(previous=>({ data:retainKey&&previous.retainKey===retainKey?previous.data:null,loading:true,error:'',url,revision,retainKey }))
    requestJson(url, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setState({ data, loading: false, error: '',url,revision,retainKey })
    }).catch(error => { if (!controller.signal.aborted) setState(previous=>({data:retainKey&&previous.retainKey===retainKey?previous.data:null, loading:false,error:error.message,url,revision,retainKey})) })
    return () => controller.abort()
  }, [url, revision, retainKey])
  const current=state.url===url&&state.revision===revision
  return current?state:{data:retainKey&&state.retainKey===retainKey?state.data:null,loading:!!url,error:''}
}
