// [Change Log] Date:2026-11-04 Author:Kimi Version:V2.1046 物流复核台共享四步布局与真实指标卡
import React from 'react'
import './logisticsReviewRedesign.css'

export function ReviewHeader({ carrier, period, steps, activeStep, onStep, onBack, actions }) {
  return <header className="lrx-top">
    <div className="lrx-brand"><div><h1>物流账单复核台 · {carrier}</h1><p>数据接入 → 逐单核价核量 → 逐笔计提复核 → 发票审核·登记已复核</p></div><div className="lrx-actions">{onBack && <button className="lrx-btn ghost" onClick={onBack}>‹ 返回总表</button>}{actions}</div></div>
    <nav className="lrx-steps" aria-label="复核步骤">{steps.map((step, index) => <button key={step.key} className={'lrx-step' + (activeStep === step.key ? ' active' : '')} data-review-step={step.key} onClick={() => onStep(step.key)}><i>{step.done ? '✓' : index + 1}</i><span>{step.name}<small className={step.tone || ''}>{step.label}</small></span></button>)}</nav>
  </header>
}

export function ReviewMetrics({ items }) {
  return <section className="lrx-metrics" aria-label="当前步骤汇总">{items.map((item, index) => <div className={'lrx-metric ' + (item.tone || '')} key={item.label + index}><div className="lrx-metric-label">{item.label}</div><div className="lrx-metric-value">{item.value}</div>{item.note && <small>{item.note}</small>}</div>)}</section>
}

export function ReviewNotice({ children, tone = '' }) {
  return <div className={'lrx-notice ' + tone}>{children}</div>
}

export function ReviewPanel({ title, subtitle, actions, children, className = '' }) {
  return <section className={'lrx-panel ' + className}><div className="lrx-panel-head"><div><h2>{title}</h2>{subtitle && <small>{subtitle}</small>}</div>{actions && <div className="lrx-panel-actions">{actions}</div>}</div>{children}</section>
}

export function BottomNav({ info, previous, next, children }) {
  return <div className="lrx-bottom"><span className="lrx-bottom-info">{info}</span><span className="lrx-spacer" />{previous && <button className="lrx-btn" onClick={previous.onClick}>{previous.label}</button>}{children}{next && <button className="lrx-btn primary" onClick={next.onClick}>{next.label}</button>}</div>
}
