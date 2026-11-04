// [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 金蝶分类型人工取数与补核附件面板。
import React, { useEffect, useRef, useState } from 'react'
import { reviewFallbackFiles, reviewFallbackTemplateUrl, reviewFallbackDownloadUrl, reviewFallbackUpload, reviewRefreshDocs } from '../api.js'
import './kingdeeDataPanel.css'

const TYPES = [
  ['SAL_OUTSTOCK', '销售出库单'],
  ['STK_MisDelivery', '其它出库单'],
  ['STK_TransferOut', '分布式调出单'],
]
const sourceName = source => source === 'manual_attachment' ? '人工补核附件' : '金蝶刷新'

export default function KingdeeDataPanel({ carrier, period, locked, onChanged, flash }) {
  const [selected, setSelected] = useState(() => Object.fromEntries(TYPES.map(([key]) => [key, true])))
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const input = useRef(null)
  const load = () => reviewFallbackFiles(carrier, period).then(setData).catch(e => flash('取数状态读取失败：' + e.message))
  useEffect(() => { setData(null); load() }, [carrier, period])
  const n = Object.values(selected).filter(Boolean).length
  const refresh = async all => {
    const forms = all ? TYPES.map(([key]) => key) : TYPES.filter(([key]) => selected[key]).map(([key]) => key)
    if (!forms.length || busy) return
    setBusy('refresh')
    try {
      const result = await reviewRefreshDocs(carrier, period, forms)
      const errors = Object.values(result.refresh_errors || {})
      flash(errors.length ? `部分类型刷新失败：${errors.join('；')}。其他类型已保留快照` : `已刷新 ${forms.length} 类单据并写入 PGSQL，工作台继续读取快照`)
      await load()
      onChanged()
    } catch (e) { flash('刷新失败，原有快照未清除：' + e.message) } finally { setBusy('') }
  }
  const upload = async event => {
    const file = event.target.files && event.target.files[0]
    event.target.value = ''
    if (!file) return
    setBusy('upload')
    try {
      const result = await reviewFallbackUpload(carrier, period, file)
      flash(`补核附件已入库：${(result.refreshed || []).map(x => `${x.label} ${x.n} 单`).join('、')}${result.unknown && result.unknown.length ? `；${result.unknown.length} 个单号未匹配，已单列` : ''}`)
      await load()
      onChanged()
    } catch (e) { flash('补核附件上传失败：' + e.message) } finally { setBusy('') }
  }
  return <section className="lrx-panel lrx-kd-panel">
    <div className="lrx-panel-head"><div><h2>金蝶取数与补核</h2><small>默认只读 PGSQL；只有人工点击才调用金蝶，部分刷新不会覆盖其他类型</small></div><span className="lrx-tag info">{data ? `${data.docs || 0} 张快照单据` : '读取快照状态…'}</span></div>
    <div className="lrx-kd-body">
      <div className="lrx-kd-types">{TYPES.map(([key, label]) => {
        const meta = data && data.sources && data.sources[key]
        const missing = data && (data.missing || []).includes(key)
        const needed = !data || !data.required || data.required.includes(key)
        return <label key={key} className="lrx-kd-type"><span><input type="checkbox" aria-label={label} checked={!!selected[key]} disabled={!!locked || !!busy} onChange={e => setSelected(old => ({ ...old, [key]: e.target.checked }))} />{label}</span><small className={missing ? 'pending' : needed ? 'ok' : ''}>{meta ? `${sourceName(meta.source)} · ${meta.at || '时间未记录'}` : needed ? '待取数' : '本期无此类账单'}</small></label>
      })}</div>
      <div className="lrx-kd-actions"><button className="lrx-btn primary" disabled={!!locked || !!busy || !n} onClick={() => refresh(false)}>{busy === 'refresh' ? '刷新中…' : `刷新所选类型（${n}）`}</button><button className="lrx-btn" disabled={!!locked || !!busy} onClick={() => refresh(true)}>刷新全部类型</button><a className="lrx-btn" href={reviewFallbackTemplateUrl(carrier, period)}>下载补核模板</a><button className="lrx-btn" disabled={!!locked || !!busy} onClick={() => input.current && input.current.click()}>{busy === 'upload' ? '上传中…' : '上传金蝶补核附件'}</button><input ref={input} type="file" accept=".xlsx" hidden onChange={upload} /></div>
      {(data && (data.missing || []).length > 0) && <div className="lrx-kd-warning">以下类型尚未覆盖：{data.missing.map(key => (TYPES.find(([k]) => k === key) || [key, key])[1]).join('、')}。对应单据标为待取数，不按零数量或一致处理。</div>}
      {data && (data.files || []).length > 0 && <div className="lrx-kd-files"><b>补核附件</b>{data.files.map(file => <div key={file.id}><span>{file.name}<small>{file.by || '操作人未记录'} · {file.at || '时间未记录'}</small></span><a className="lrx-btn subtle" href={reviewFallbackDownloadUrl(file.id)}>下载</a></div>)}</div>}
    </div>
  </section>
}
