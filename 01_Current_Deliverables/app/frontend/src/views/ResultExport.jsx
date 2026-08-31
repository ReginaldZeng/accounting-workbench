// [Change Log] Date:2026-07-04 Author:Claude/c Version:V1.8
// 结果出具（四步工作流第4步）：本期对账结果概览 + 一键导出对账底稿 Excel。
import React, { useEffect, useState } from 'react'
import { getReconcile, getBalanceAdjust } from '../api.js'
import Steps from '../components/Steps.jsx'

function Stat({ label, v, color }) {
  return <div className="cat" style={{ margin: 0 }}>
    <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4, color: color || 'var(--ink)' }}>{v}</div>
  </div>
}

export default function ResultExport({ cfg, onNav }) {
  const [d, setD] = useState(null), [ba, setBa] = useState(null)
  useEffect(() => {
    getReconcile().then(setD).catch(() => {})
    getBalanceAdjust().then(setBa).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  const kd = cfg.source === 'kingdee'
  const s = (d && d.summary) || {}
  const diff = Object.entries(s).filter(([k]) => k !== '已匹配' && k !== '组合候选').reduce((a, [, v]) => a + (v || 0), 0)
  const exportReport = () => window.open('/api/export/report', '_blank')

  return (<div>
    <div className="head"><div>
      <div className="h-title">结果出具</div>
      <div className="h-sub">四步工作流第 4 步 · 一键导出正式对账底稿（差异清单 + 余额调节表 + 全部逐笔）</div>
    </div></div>
    <div className="body" style={{ maxWidth: 940 }}>
      <Steps current="result" onNav={onNav} />
      <div className="cat" style={{ marginTop: 4 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>本期对账结果 · {d ? d.period : cfg.period + '期'}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }}>
          <Stat label="待处理差异（笔）" v={diff} color="var(--amber)" />
          <Stat label="已匹配（笔）" v={s['已匹配'] || 0} color="var(--green)" />
          <Stat label="余额调节 · 对平（户）" v={(ba && ba['对平户数']) || 0} color="var(--green)" />
          <Stat label="余额调节 · 不平（户）" v={(ba && ba['不平户数']) || 0} color={ba && ba['不平户数'] ? 'var(--red)' : 'var(--ink-3)'} />
        </div>
      </div>
      <div className="cat">
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>导出对账底稿</div>
        <div className="foot" style={{ marginBottom: 12 }}>Excel 含 4 个工作表：<b>对账汇总</b> / <b>差异清单</b>（疑似漏账·做错金额·晚记·内部往来未做账·金蝶单边）/ <b>银行存款余额调节表</b> / <b>全部逐笔明细</b>。可直接存档或上报领导。</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button className="btn btn-pri" onClick={exportReport} disabled={!kd}>导出对账底稿（Excel）</button>
          {!kd && <span className="foot">样例数据源下不导出，请到设置切「金蝶真数据」。</span>}
        </div>
      </div>
    </div>
  </div>)
}
