import React, { useEffect, useRef, useState } from 'react'
import { requestJson, query, post, money, count, useResource } from './ecomWorkbenchApi.js'
import './ecomFlowLedger.css'

const BUCKETS = { receipt:'交易收款',refund:'交易退款',fee:'平台费用',adjustment:'补贴 / 调整',ufirst_fee:'U先专属费用',qr:'收钱码收款',transfer:'划转候选',recharge:'充值 / 划转候选',other:'其他已知费目',unknown:'待识别流水' }
const EMPTY = { review_status:'',q:'',bucket:'',abnormal:false,direction:'',amount_min:'',amount_max:'',date_from:'',date_to:'' }

export function AccountTable({ data, onSelect }) {
  return <div className="ef-table-wrap"><table className="ef-table"><thead><tr><th>账户名称</th><th>类型 / 尾号</th><th>关联店铺</th><th className="num">流水笔数</th><th className="num">账面余额</th><th>余额截至 / 数据状态</th><th /></tr></thead><tbody>
    {(data?.accounts || []).map(a => <tr key={a.id}><td><strong>{a.name}</strong></td><td>{a.kind === 'alipay' ? '支付宝' : '聚合账户'}{a.suffix && ` · ${a.suffix}`}</td><td>{a.shops.map(id => data.shops?.find(s => s.id === id)?.name || id).join('、')}</td><td className="num">{count(a.rows)}</td><td className="num">{money(a.balance)}</td><td>{a.as_of || '未提供余额'}<small>{a.balance_basis}</small></td><td>{onSelect && <button type="button" className="ef-link" onClick={() => onSelect(a.id)}>查看流水</button>}</td></tr>)}
    {!data?.accounts?.length && <tr><td colSpan="7" className="ef-empty">尚未登记账户。支持一店多账户、一个账户关联多家店铺。</td></tr>}
  </tbody></table></div>
}

export default function EcomFlowLedger({ user, period: parentPeriod = '', initialQuery = '', initialAccountId = '', onChanged = () => {} }) {
  const [revision, setRevision] = useState(0)
  const accounts = useResource('/api/ec/flows/accounts', revision)
  const [accountId, setAccountId] = useState(initialAccountId)
  const [period, setPeriod] = useState(parentPeriod)
  const [filters, setFilters] = useState({ ...EMPTY, q: initialQuery })
  const [search, setSearch] = useState(filters)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [create, setCreate] = useState(false)
  const [name, setName] = useState('')
  const [accountKind, setAccountKind] = useState('alipay')
  const [suffix, setSuffix] = useState('')
  const [shopIds, setShopIds] = useState([])
  const fileRef = useRef(null)
  const canEdit = user?.role === 'admin' || user?.perms?.ec_settle_upload
  useEffect(() => { setPeriod(parentPeriod); setPage(1) }, [parentPeriod])
  useEffect(() => { setAccountId(initialAccountId); setPage(1) }, [initialAccountId])
  useEffect(() => { setFilters(f => ({ ...f, q: initialQuery })); setSearch(f => ({ ...f, q: initialQuery })); setPage(1) }, [initialQuery])
  const result = useResource(`/api/ec/flows?${query({ account_id:accountId,period,...search,abnormal:search.abnormal ? 'true' : '',page,size:50 })}`, revision)
  const data = result.data
  const change = (key, value) => setFilters(f => ({ ...f, [key]:value }))
  const apply = e => { e?.preventDefault(); setSearch({ ...filters }); setPage(1) }
  const selectAccount = id => { setAccountId(id); setPage(1) }
  const act = async fn => {
    setBusy(true); setNotice('')
    try { await fn(); setRevision(v => v + 1); onChanged() } catch (error) { setNotice(error.message) } finally { setBusy(false) }
  }
  const upload = e => {
    const files = Array.from(e.target.files || []); e.target.value = ''
    if (!files.length) return
    act(async () => {
      const form = new FormData(); form.append('account_id', accountId); form.append('background','true'); files.forEach(file => form.append('files', file))
      let r = await requestJson('/api/ec/flows/import', { method:'POST',body:form })
      if (r.job_id) {
        const jobId=r.job_id
        setNotice(`已接收 ${files.length} 个文件，服务器正在后台解析与入库，请勿重复提交。`)
        try {
          while (true) {
            await new Promise(resolve => setTimeout(resolve,1500))
            const job=await requestJson(`/api/ec/flows/import-status/${jobId}`)
            if (job.status==='failed') throw new Error(job.error || '后台导入失败')
            if (job.status==='complete') {r=job.result;break}
          }
        } catch (error) { throw new Error(`${error.message}。若连接中断，任务可能仍在后台运行；请先刷新流水查看结果。`) }
      }
      setNotice(`导入完成：新增 ${count(r.added)} 笔，重复来源 ${count(r.duplicates)} 笔，内容冲突 ${count(r.conflicts)} 笔。原文件未修改。`)
    })
  }
  const saveAccount = e => {
    e.preventDefault(); act(async () => {
      const r = await requestJson('/api/ec/flows/accounts', post({ name,suffix,kind:accountKind,shops:shopIds }))
      selectAccount(r.id); setCreate(false); setName(''); setSuffix('');setShopIds([]);setNotice('账户已登记在工作台，未写入金蝶。')
    })
  }
  return <section className="ef-ledger" aria-label="账户流水查询">
    <div className="ef-heading"><div><h2>账户流水</h2><p>合并文件 · 全字段查找 · 流水分桶与异常检查</p></div><div className="ef-actions"><button disabled={!canEdit || busy} onClick={() => setCreate(v => !v)}>登记账户</button><button disabled={busy} onClick={() => setRevision(v => v+1)}>刷新</button></div></div>
    {(notice || accounts.error || result.error) && <div className="ef-notice" role="status">{notice || accounts.error || result.error}</div>}
    {create && <form className="ef-account-form" onSubmit={saveAccount}><label>账户类型<select value={accountKind} onChange={e => setAccountKind(e.target.value)}><option value="alipay">支付宝</option><option value="fund">聚合账户</option></select></label><label>账户名称<input required maxLength="120" value={name} onChange={e => setName(e.target.value)} placeholder="例如：星期零天猫支付宝" /></label><label>尾号<input maxLength="8" pattern="[0-9]{0,8}" value={suffix} onChange={e => setSuffix(e.target.value)} placeholder="可填末 4 位" /></label><fieldset><legend>关联店铺（可多选）</legend>{accounts.data?.shops?.map(s => <label className="ef-check" key={s.id}><input type="checkbox" checked={shopIds.includes(s.id)} onChange={e => setShopIds(ids => e.target.checked ? [...ids,s.id] : ids.filter(id => id!==s.id))} />{s.name}</label>)}</fieldset><button className="ef-primary" disabled={busy || !shopIds.length}>保存账户</button><button type="button" onClick={() => setCreate(false)}>取消</button></form>}
    <AccountTable data={accounts.data} onSelect={selectAccount} />
    <div className="ef-tools"><label>账户<select aria-label="流水账户" value={accountId} onChange={e => selectAccount(e.target.value)}><option value="">全部账户</option>{accounts.data?.accounts?.map(a => <option key={a.id} value={a.id}>{a.name}{a.suffix ? ` · ${a.suffix}` : ''}</option>)}</select></label><label>流水期间<input type="month" value={period} onChange={e => {setPeriod(e.target.value);setPage(1)}} /></label><button onClick={() => {setPeriod('');setPage(1)}}>跨月查找</button><span className="ef-spacer" /><input hidden type="file" ref={fileRef} accept=".xlsx,.xls,.zip" multiple onChange={upload} /><button className="ef-primary" disabled={!canEdit || !accountId || busy} onClick={() => fileRef.current?.click()}>{busy ? '正在处理…' : '合并导入流水'}</button><small>{accountId ? '一次可选多个分片' : '导入前请选择具体账户'}</small></div>
    <form className="ef-search" onSubmit={apply}><label className="ef-query">统一搜索<input value={filters.q} onChange={e => change('q',e.target.value)} placeholder="订单号、流水号、交易号、商户单号、商品、对方名称、摘要…" /></label><label>业务分桶<select value={filters.bucket} onChange={e => change('bucket',e.target.value)}><option value="">全部流水</option>{Object.entries(BUCKETS).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>人工定性<select value={filters.review_status} onChange={e => change('review_status',e.target.value)}><option value="">全部状态</option><option>待核对</option><option>正常</option><option>待追查</option></select></label><label>收支方向<select value={filters.direction} onChange={e => change('direction',e.target.value)}><option value="">全部</option><option value="income">收入</option><option value="outgo">支出</option></select></label><label>金额从<input inputMode="decimal" value={filters.amount_min} onChange={e => change('amount_min',e.target.value)} placeholder="不限" /></label><label>金额至<input inputMode="decimal" value={filters.amount_max} onChange={e => change('amount_max',e.target.value)} placeholder="不限" /></label><label>开始日期<input type="date" value={filters.date_from} onChange={e => change('date_from',e.target.value)} /></label><label>结束日期<input type="date" value={filters.date_to} onChange={e => change('date_to',e.target.value)} /></label><label className="ef-check"><input type="checkbox" checked={filters.abnormal} onChange={e => change('abnormal',e.target.checked)} />仅看异常 / 待核对</label><button className="ef-primary">搜索</button><button type="button" onClick={() => {setFilters({...EMPTY});setSearch({...EMPTY});setPage(1)}}>清空条件</button></form>
    <div className="ef-summary"><span>筛选结果 <strong>{count(data?.total)}</strong> 笔</span><span>收入 <strong>¥{money(data?.income)}</strong></span><span>支出 <strong>¥{money(data?.outgo)}</strong></span><span>异常 / 提醒 <strong>{count(data?.flagged)}</strong> 笔</span></div>
    <p className="ef-muted">{data?.notice || '流水分类不是订单核销分桶；候选与提醒不等于已确认错误。'} 金额不指定方向时按单笔收支净额的绝对值筛选。</p>
    <div className="ef-table-wrap"><table className="ef-table ef-flow-table"><thead><tr><th>入账时间 / 账户</th><th>业务基础订单号</th><th>支付宝流水号 / 交易号</th><th>业务描述 / 账务类型</th><th className="num">收入</th><th className="num">支出</th><th className="num">余额</th><th>流水分桶 / 提醒</th><th /></tr></thead><tbody>
      {data?.rows?.map(r => <tr key={r.id}><td>{r.ts}<small>{r.account_name}</small></td><td><button className="ef-link ef-id" onClick={() => setSelected(r.id)}>{r.order_no || '未提供订单号'}</button><small>{r.mch_no && `商户单号 ${r.mch_no}`}</small></td><td className="ef-id">{r.serial || '无流水号'}<small>{r.txn || '无交易号'}</small></td><td className="ef-desc">{r.desc || '业务描述为空'}<small>{r.btype} · {r.chan || '未提供渠道'}</small></td><td className="num">{money(r.income)}</td><td className="num">{money(r.outgo)}</td><td className="num">{money(r.balance)}</td><td><span className={r.flags?.length ? 'ef-tag warn' : 'ef-tag'}>{BUCKETS[r.bucket] || r.bucket}</span><small className="ef-warning">{r.flags?.join('；')}</small><small>人工定性：{r.review?.verdict || '待核对'}</small></td><td><button className="ef-link" onClick={() => setSelected(r.id)}>原始字段</button></td></tr>)}
      {(result.loading || !data?.rows?.length) && <tr><td colSpan="9" className="ef-empty">{result.loading ? '正在检索流水…' : result.error ? '读取失败，请重试' : '没有符合条件的流水；可调整筛选或先导入文件。'}</td></tr>}
    </tbody></table></div>
    <div className="ef-pagination"><span>每页 50 笔 · 仅加载当前页</span><button disabled={page<=1 || result.loading} onClick={() => setPage(p => p-1)}>上一页</button><span>{page} / {data?.pages || 1}</span><button disabled={page>=(data?.pages || 1) || result.loading} onClick={() => setPage(p => p+1)}>下一页</button></div>
    {selected && <FlowDetail id={selected} canEdit={canEdit} onSaved={() => setRevision(v => v+1)} onClose={() => setSelected(null)} />}
  </section>
}

function FlowDetail({ id, onClose, canEdit, onSaved }) {
  const [revision,setRevision] = useState(0)
  const result = useResource(`/api/ec/flows/detail/${id}`,revision)
  const ref = useRef(null)
  useEffect(() => {
    const previous = document.activeElement; const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'; ref.current?.focus()
    const key = event => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') {
        const nodes = ref.current?.querySelectorAll('button, input, select, [tabindex="0"]'); if (!nodes?.length) return
        const first=nodes[0],last=nodes[nodes.length-1]
        if (event.shiftKey && (document.activeElement===first || document.activeElement===ref.current)) {event.preventDefault();last.focus()}
        else if (!event.shiftKey && document.activeElement===last) {event.preventDefault();first.focus()}
      }
    }
    document.addEventListener('keydown',key)
    return () => {document.body.style.overflow=overflow;document.removeEventListener('keydown',key);previous?.focus()}
  }, [])
  const row=result.data?.row
  return <div className="ef-overlay" onMouseDown={e => {if (e.target===e.currentTarget) onClose()}}><section ref={ref} tabIndex="-1" role="dialog" aria-modal="true" aria-labelledby="flow-detail-title" className="ef-drawer"><header><div><h2 id="flow-detail-title">流水原始明细</h2><p>原始字段完整展示 · 来源可回查</p></div><button aria-label="关闭流水明细" onClick={onClose}>关闭</button></header>{result.loading ? <p>正在读取…</p> : result.error ? <p role="alert">{result.error}</p> : <><div className="ef-summary"><span>收入 ¥{money(row.income)}</span><span>支出 ¥{money(row.outgo)}</span><span>余额 ¥{money(row.balance)}</span></div><p><strong>{BUCKETS[row.bucket]}</strong> · {row.reason}</p>{row.flags?.length>0 && <div className="ef-notice">{row.flags.join('；')}</div>}<FlowReview data={result.data} id={id} canEdit={canEdit} onSaved={() => {setRevision(v=>v+1);onSaved()}}/><h3>来源文件</h3><ul>{result.data.sources.map((s,i) => <li key={i}>{s.filename}{s.aliases?.length>0 && `（同内容文件：${s.aliases.join('、')}）`} · {s.sheet || '工作表'} · 第 {s.row_number} 行<small>导入时间 {s.ts}</small></li>)}</ul><h3>原文件字段</h3><dl className="ef-fields">{Object.entries(row.raw).map(([name,value]) => <React.Fragment key={name}><dt>{name}</dt><dd>{value || '—'}</dd></React.Fragment>)}</dl></>}</section></div>
}
function FlowReview({data,id,canEdit,onSaved}) {
  const [verdict,setVerdict]=useState(data?.review?.verdict||'待核对'),[note,setNote]=useState(data?.review?.note||''),[busy,setBusy]=useState(false),[message,setMessage]=useState('')
  const save=async event=>{event.preventDefault();setBusy(true);try{await requestJson('/api/ec/flows/review',post({ids:[id],verdict,note}));setMessage('已登记在工作台，未写金蝶。');onSaved()}catch(error){setMessage(error.message)}finally{setBusy(false)}}
  return <section><h3>人工定性与活动依据</h3>{data?.review&&<p>{data.review.verdict} · {data.review.operator} · {data.review.ts}</p>}<form onSubmit={save} className="ef-account-form"><select aria-label="流水人工定性" value={verdict} onChange={e=>setVerdict(e.target.value)} disabled={!canEdit}><option>待核对</option><option>正常</option><option>待追查</option></select><input aria-label="活动或资金去向依据" value={note} onChange={e=>setNote(e.target.value)} maxLength="1000" required placeholder="活动名称、推广依据或资金去向说明" disabled={!canEdit}/><button disabled={!canEdit||busy||!note.trim()}>{busy?'保存中…':'保存定性'}</button></form>{message&&<p role="status">{message}</p>}{data?.historical_reviews?.length>0&&<><p>{data.history_note}</p>{data.historical_reviews.map((r,i)=><p key={i}>历史登记：{r.verdict} · {r.note} · {r.operator} · {r.ts}</p>)}</>}</section>
}
