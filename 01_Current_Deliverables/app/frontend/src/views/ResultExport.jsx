// [Change Log] Date:2026-09-09 Author:Claude/c Version:V2.550
// 结果出具（四步工作流第4步）：本期对账结果概览 + 一键导出对账底稿 Excel。
// V2.550：数据源从旧「未达调节·1002 窄引擎」改为全科目《银行余额调节表》(getBalanceStatement)，
//         与第3步、单独导出完全一致（在用/对平/待补/真实差异）；逐笔"未匹配"拆成"时间差已调平" vs "需人工"，
//         不再把本月晚记吓成"待处理差异"；页面排版对齐其它页。
import React, { useEffect, useState } from 'react'
import { getReconcile, getBalanceStatement } from '../api.js'
import Steps from '../components/Steps.jsx'

// 逐笔"未匹配"里属于时间差/两侧口径差、已在《余额调节表》由未达账项调平的状态（非真实差异）
const TIMING = new Set(['晚记·本月', '跨期晚记', '内部往来·未做账', '内部划转·对应他账户', '汇兑损益·账面调整'])

function Stat({ label, v, color, hint }) {
  return <div className="cat" style={{ margin: 0, minWidth: 0 }} title={hint || undefined}>
    <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{label}{hint ? ' ⓘ' : ''}</div>
    <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4, color: color || 'var(--ink)' }}>{v}</div>
  </div>
}

export default function ResultExport({ cfg, onNav }) {
  const [d, setD] = useState(null), [bs, setBs] = useState(null)
  useEffect(() => {
    getReconcile().then(setD).catch(() => {})
    getBalanceStatement().then(setBs).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  const kd = cfg.source === 'kingdee'

  // 余额调节：全科目口径（与第3步一致）
  const flat = ((bs && bs.groups) || []).flatMap(g => (g.accounts || [])).filter(a => !a['全零'])
  const nUse = flat.length
  const nDiff = (bs && bs['差异户数']) || 0
  const nWait = flat.filter(a => a['银行侧缺']).length
  const nTie = Math.max(0, nUse - nDiff - nWait)

  // 逐笔稽核：已匹配 / 未匹配（拆：时间差已调平 vs 需人工）
  const s = (d && d.summary) || {}
  const matched = s['已匹配'] || 0
  let timing = 0, problem = 0
  Object.entries(s).forEach(([k, v]) => {
    if (k === '已匹配' || k === '组合候选') return
    if (TIMING.has(k)) timing += (v || 0); else problem += (v || 0)
  })

  const exportReport = () => window.open('/api/export/report', '_blank')

  return (<div>
    <div className="head"><div>
      <div className="h-title">结果出具</div>
      <div className="h-sub">四步工作流第 4 步 · 一键导出正式对账底稿（对账汇总 + 差异清单 + 银行余额调节表 + 各户末笔流水 + 全部逐笔）</div>
    </div></div>
    <div className="body" style={{ maxWidth: 940 }}>
      <Steps current="result" onNav={onNav} />

      <div className="cat" style={{ marginTop: 4 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
          余额调节 · 全科目（与第3步同一张表）<span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}> · {bs ? (bs.period || cfg.period) : cfg.period}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }}>
          <Stat label="在用账户（户）" v={nUse} color="var(--ink)" hint="本期在用、非全零的全部账户（银行存款+其它货币资金+交易性金融资产+库存现金）" />
          <Stat label="已对平（户）" v={nTie} color="var(--green)" hint="银行侧有数且调节后差额为 0 的账户" />
          <Stat label="待补银行侧（户）" v={nWait} color="var(--amber)" hint="本月无流水/属理财对账单/电商结算——银行侧余额待末笔流水或手工补" />
          <Stat label="真实差异（户）" v={nDiff} color={nDiff ? 'var(--red)' : 'var(--green)'} hint="调节后仍对不平、需核查的账户" />
        </div>
        <div className="foot" style={{ marginTop: 10 }}>
          逐笔稽核：已匹配 <b>{matched}</b> 笔
          {timing > 0 && <> · 本月时间差 <b>{timing}</b> 笔（银行与金蝶记账月份不同，已在《余额调节表》由未达账项调平，<b>非真实差异</b>）</>}
          {problem > 0
            ? <> · <span style={{ color: 'var(--red)' }}>需人工处理 <b>{problem}</b> 笔</span>（见「差异清单」）</>
            : <> · 需人工处理 <b>0</b> 笔</>}
        </div>
      </div>

      <div className="cat">
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>导出对账底稿</div>
        <div className="foot" style={{ marginBottom: 12 }}>
          Excel 含 5 个工作表：<b>对账汇总</b> / <b>差异清单</b>（疑似漏账·做错金额·晚记·内部往来未做账·金蝶单边）/
          <b>银行余额调节表</b>（全四类科目，与第3步一致）/ <b>各户末笔流水</b>（每户流水末行佐证期末余额）/ <b>全部逐笔明细</b>。可直接存档或上报领导。
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button className="btn btn-pri" onClick={exportReport} disabled={!kd}>导出对账底稿（Excel）</button>
          {!kd && <span className="foot">样例数据源下不导出，请到设置切「金蝶真数据」。</span>}
        </div>
      </div>
    </div>
  </div>)
}
