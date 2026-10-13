// 收件人加人·直接搜人（V2.543 起用于门户取件机监控；V2.933 抽成共用，汇率通知设置也用）：
// 输名字→下拉搜钉钉通讯录（姓名｜岗位｜部门）点一下加；输 11 位号码→回车/点「加外部号码」当外部人。
// 花名册由父层拉一次传进来（roster=null 表示还没拉到）。样式各页自带：cls 是类名前缀（门户深色 pa-、工作台 fx-）。
import React, { useState } from 'react'

export default function PersonSearchAdd({ roster, rosterErr, onFocusLoad, onPick, onAddNumber, cls = 'pa', inputClass = 'srch', disabled = false }) {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const kw = q.trim()
  const isNum = /^\d+$/.test(kw)
  const hits = (!isNum && kw && Array.isArray(roster))
    ? roster.filter(p => (p.name || '').includes(kw) || (p.title || '').includes(kw) || (p.dept || '').includes(kw)).slice(0, 8)
    : []
  const pick = (p) => { onPick(p); setQ('') }
  const addNum = () => { if (/^\d{11}$/.test(kw)) { onAddNumber(kw); setQ('') } }
  const open = focused && !!kw
  return (
    <div className={cls + '-srwrap'}>
      <input className={inputClass} value={q} disabled={disabled} placeholder="输名字搜人加，或填 11 位手机号"
        onFocus={() => { setFocused(true); onFocusLoad && onFocusLoad() }}
        onBlur={() => setTimeout(() => setFocused(false), 150)}
        onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && isNum) { e.preventDefault(); addNum() } }} />
      {open && (
        <div className={cls + '-sr'}>
          {isNum
            ? (kw.length === 11
              ? <div className={cls + '-sr-item'} onMouseDown={e => { e.preventDefault(); addNum() }}>➕ 加外部号码 {kw}</div>
              : <div className={cls + '-sr-msg'}>手机号 11 位，还差 {11 - kw.length} 位…</div>)
            : (roster === null
              ? <div className={cls + '-sr-msg'}>加载通讯录中…</div>
              : rosterErr
                ? <div className={cls + '-sr-msg'} style={{ color: 'var(--red)' }}>{rosterErr}</div>
                : hits.length === 0
                  ? <div className={cls + '-sr-msg'}>没搜到「{kw}」</div>
                  : hits.map(p => (
                    <div key={p.userid} className={cls + '-sr-item'} onMouseDown={e => { e.preventDefault(); pick(p) }}>
                      <span>👤 {p.name}</span>
                      {(p.title || p.dept) && <span style={{ color: 'var(--ink3, var(--ink-3))', marginLeft: 'auto', fontSize: 11.5 }}>{[p.title, p.dept].filter(Boolean).join(' · ')}</span>}
                    </div>
                  )))}
        </div>
      )}
    </div>
  )
}
