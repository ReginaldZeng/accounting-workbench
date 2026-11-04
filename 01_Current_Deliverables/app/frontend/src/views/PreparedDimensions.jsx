// [Change Log] Date:2026-11-04 Author:Kimi Version:V2.1047 准备做账维度按主体分组并追溯逐单来源
import React, { useMemo, useState } from 'react'
import { ReviewPanel } from './LogisticsReviewLayout.jsx'

const money = value => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const uniq = values => [...new Set(values.filter(value => value != null && String(value).trim()))]
const labels = (rows, code, name, empty = '—') => {
  const values = uniq(rows.map(row => `${row[code] || ''} ${row[name] || ''}`.trim())).filter(value => !value.startsWith('（'))
  return values.length ? values.join('、') : empty
}

function sourceButton(row, onSource) {
  const od = row.od
  if (!od) return <span className="lrx-muted">—</span>
  if (!od.n) return <span className="lrx-tag">汇总核</span>
  const ratio = od.ratio == null || !Number.isFinite(Number(od.ratio)) ? null : Math.max(0, Math.min(1, Number(od.ratio)))
  const label = ratio == null ? `${od.n} 单` : ratio >= .995 ? `逐笔 · ${od.n} 单` : ratio < .005 ? `${od.n} 单 · 只核量` : `部分 ${Math.round(ratio * 100)}% · ${od.n} 单`
  return <button className="lrx-btn subtle" title="去第②步查看这组真实单据" onClick={() => onSource(od)}>{label}</button>
}

export default function PreparedDimensions({ groups, period, locked, onSource, onEdit, onMark }) {
  const [active, setActive] = useState('')
  const [filter, setFilter] = useState('all')
  const subjects = useMemo(() => groups.map((group, index) => {
    const head = group.head || {}
    const key = head.subject || `未分组 ${index}`
    // 后端只在每个账单匹配组的首行给 bill；其余行不能再次展示同一金额。
    const rows = group.lines.filter(row => row.bill != null).map(row => ({
      ...row,
      matched: row.kind === 'accr' ? group.lines.filter(line => line.kind === 'accr' && (line.anc || line.key) === (row.anc || row.key)) : [row],
    }))
    const pending = !head.mark || head.mark.status !== 'ok'
    return { key, head, rows, pending }
  }), [groups])
  const visibleSubjects = filter === 'pending' ? subjects.filter(subject => subject.pending) : subjects
  const current = visibleSubjects.find(subject => subject.key === active) || visibleSubjects[0]
  if (!subjects.length) return null
  const pendingCount = subjects.filter(subject => subject.pending).length
  return <>
    <div className="lrx-toolbar"><div className="lrx-seg"><button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部明细 <b>{subjects.length}</b></button><button className={filter === 'pending' ? 'active' : ''} onClick={() => setFilter('pending')}>主体待确认 <b>{pendingCount}</b></button></div><span className="lrx-spacer" /><span className="lrx-muted">金额单位：元 · 同一账单匹配组只计一次</span></div>
    <ReviewPanel title="准备做账维度" subtitle="按复核后的账单归属汇总，来源逐行可追；计提更正仍在原流程登记" actions={<span className="lrx-tag info">正式接口数据</span>}>
      {!current && <div className="lrx-empty">所有主体均已确认，可切回全部明细查看。</div>}
      <div className="lrx-groups">{visibleSubjects.map(subject => {
        const head = subject.head, open = current && current.key === subject.key
        return <section className="lrx-group" key={subject.key}><button className="lrx-group-head" aria-expanded={open} onClick={() => setActive(open ? '' : subject.key)}><span className="lrx-toggle">{open ? '−' : '+'}</span><span className="lrx-group-title">{head.book_code} {head.subject || '待确认主体'}</span><span className={'lrx-tag ' + (subject.pending ? 'pending' : 'ok')}>{subject.pending ? '主体待确认' : '主体已通过'}</span><span className="lrx-group-amount"><b>{money(head.bill ?? head.amt)}</b><small>计提 {money(head.amt)} · 差异 {money(head.diff)}</small></span></button>
          {open && <div className="lrx-table-scroll"><table className="lrx-table lrx-line-table"><thead><tr><th>主体 / 期间</th><th>费用项目 / 凭证</th><th>费用类型</th><th>产品分类</th><th>产品细目</th><th>部门</th><th>科目</th><th className="num">准备做账金额</th><th>税率</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody>{subject.rows.length ? subject.rows.map(row => {
            const matched = row.matched, shared = matched.length > 1
            const subjectName = row.bill_subject || head.subject || '待确认主体'
            const biz = row.bill_biz || row.biz || '无产品分类'
            const taxRates = uniq(matched.map(row => row.tax_rate))
            const state = subject.pending ? '主体待确认' : '主体已通过'
            return <tr key={row.key} data-prepared-row={row.key}><td><b>{subjectName}</b><span className="lrx-cell-meta">{period}</span></td><td><b>{labels(matched, 'fee_code', 'fee', row.fee_type)}</b><span className="lrx-cell-meta">{shared ? `合并 ${matched.length} 笔计提` : matched[0].vno || '账单有、计提无'}</span></td><td>{row.fee_type || '—'}</td><td>{row.biz_code && !row.bill_biz ? <span className="lrx-code">{row.biz_code} </span> : ''}{biz}</td><td>{labels(matched, 'proj_code', 'proj')}</td><td>{labels(matched, 'dept_code', 'dept')}</td><td>{labels(matched, 'acct', 'acct_name')}</td><td className="num"><b>{money(row.bill)}</b><span className="lrx-cell-meta">复核账单 · 含税</span></td><td>{taxRates.length === 1 ? `${Number(taxRates[0]) * 100}%` : taxRates.length ? '多税率' : '—'}</td><td>{sourceButton(row, onSource)}</td><td><span className={'lrx-tag ' + (subject.pending ? 'pending' : 'ok')}>{state}</span>{row.fix && <span className="lrx-cell-meta">已登记更正</span>}</td><td>{!locked && row.kind === 'accr' && !shared ? <button className="lrx-btn subtle" onClick={() => onEdit({ key: row.key })}>调整 / 拆分</button> : <span className="lrx-muted">{shared ? '见下方逐笔' : locked ? '已锁定' : '见下方处理'}</span>}</td></tr>
          }) : <tr><td colSpan="12" className="lrx-empty">本主体没有可展示的账单匹配组</td></tr>}</tbody></table></div>}
          {open && <div className="lrx-group-foot"><span className="lrx-muted">{subject.rows.length} 个账单匹配组 · 来源逐行可追</span><span className="lrx-spacer" />{subject.pending ? <button className="lrx-btn primary" data-subject-confirm={head.subject} disabled={locked || !head.subject} onClick={() => onMark(head.subject, 'ok', '', head.diff || 0)}>确认本主体维度</button> : <button className="lrx-btn" data-subject-confirm={head.subject} disabled={locked} onClick={() => onMark(head.subject, '')}>撤销主体确认</button>}</div>}
        </section>
      })}</div>
    </ReviewPanel>
  </>
}
