// [Change Log] Date:2026-07-04 Author:Claude/c Version:V1.3
// 四部曲进度条（共享）：数据导入 → 逐笔稽核 → 余额调节 → 结果出具。
// 兼作工作流导航：点「数据导入」进数据接入页、点「逐笔稽核」回稽核页（余额/结果二期）。
import React from 'react'

const ORDER = ['import', 'reconcile', 'balance', 'result']
const LABEL = { import: '数据导入', reconcile: '逐笔稽核', balance: '余额调节', result: '结果出具' }
const VIEW = { import: 'import', reconcile: 'reconcile', balance: 'fund', result: 'result' }   // 步骤 → 视图（余额调节=资金看板）
const NAV = { import: true, reconcile: true, balance: true, result: true }    // 已实现、可点跳转的步骤
const DEFSUB = { balance: '银行存款余额调节', result: '导出对账底稿' }

export default function Steps({ current, onNav, sub = {} }) {
  const ci = ORDER.indexOf(current)
  return (<div className="steps">
    {ORDER.map((id, i) => {
      const state = i < ci ? 'done' : (i === ci ? 'cur' : '')
      const clickable = NAV[id] && id !== current && onNav
      const sd = sub[id] || DEFSUB[id] || ''
      return <div className={'step ' + state} key={id}
        onClick={() => clickable && onNav(VIEW[id] || id)}
        title={clickable ? '点击进入「' + LABEL[id] + '」' : undefined}
        style={clickable ? { cursor: 'pointer' } : undefined}>
        <div className="num">{state === 'done' ? '✓' : (i + 1)}</div>
        <div><div className="sn">{LABEL[id]}{clickable ? ' ›' : ''}</div><div className="sd">{sd}</div></div>
      </div>
    })}
  </div>)
}
