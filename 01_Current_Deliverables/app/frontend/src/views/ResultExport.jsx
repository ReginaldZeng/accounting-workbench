// [Change Log] Date:2026-09-09 Author:Claude/c Version:V2.551
// 结果出具（四步工作流第4步）：本期对账结果概览 + 一键导出对账底稿 Excel。
// V2.550：数据源改全科目《银行余额调节表》(getBalanceStatement)，与第3步/单独导出一致。
// V2.551：铺满宽度（去掉 maxWidth:940 的窄栏），右侧补「按科目分解」+「待补银行侧清单」，页面不再空半屏。
import React, { useEffect, useState } from 'react'
import { getReconcile, getBalanceStatement } from '../api.js'
import Steps from '../components/Steps.jsx'

const TIMING = new Set(['晚记·本月', '跨期晚记', '内部往来·未做账', '内部划转·对应他账户', '汇兑损益·账面调整'])

function waitReason(a) {
  const cat = a['科目'] || ''
  const nm = (a['账户名称'] || '') + (a['开户行'] || '')
  if (cat === '银行存款') return '本月无流水 · 待截图/上期结转'
  if (cat === '交易性金融资产') return '资管/理财 · 走对账单'
  if (cat === '其它货币资金') {
    if (/天猫|抖音|京东|拼多多|快手|店铺|结算|支付宝|微信/.test(nm)) return '电商结算 · 走电商线'
    return '理财 · 走对账单'
  }
  return '待对账单/手工'
}

function Stat({ label, v, color, hint }) {
  return <div className="cat" style={{ margin: 0, minWidth: 0 }} title={hint || undefined}>
    <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{label}{hint ? ' ⓘ' : ''}</div>
    <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4, color: color || 'var(--ink)' }}>{v}</div>
  </div>
}

export default function ResultExport({ cfg, onNav }) {
  const [d, setD] = useState(null), [bs, setBs] = useState(null)
  useEffect(() => {
    getReconcile().then(setD).catch(() => {})
    getBalanceStatement().then(setBs).catch(() => {})
  }, [cfg.source, cfg.year, cfg.period])
  const kd = cfg.source === 'kingdee'

  const groups = (bs && bs.groups) || []
  const flat = groups.flatMap(g => (g.accounts || [])).filter(a => !a['全零'])
  const nUse = flat.length
  const nDiff = (bs && bs['差异户数']) || 0
  const waitList = flat.filter(a => a['银行侧缺'])
  const nWait = waitList.length
  const nTie = Math.max(0, nUse - nDiff - nWait)

  // 按科目分解
  const catRows = groups.map(g => {
    const acc = (g.accounts || []).filter(a => !a['全零'])
    const w = acc.filter(a => a['银行侧缺']).length
    const df = acc.filter(a => a['有差异']).length
    return { 科目: g['科目'], 在用: acc.length, 对平: Math.max(0, acc.length - w - df), 待补: w, 差异: df }
  }).filter(r => r.在用 > 0)

  const s = (d && d.summary) || {}
  const matched = s['已匹配'] || 0
  let timing = 0, problem = 0
  Object.entries(s).forEach(([k, v]) => {
    if (k === '已匹配' || k === '组合候选') return
    if (TIMING.has(k)) timing += (v || 0); else problem += (v || 0)
  })

  const exportReport = () => window.open('/api/export/report', '_blank')
  const th = { textAlign: 'left', fontSize: 11.5, color: 'var(--ink-2)', fontWeight: 600, padding: '5px 8px', borderBottom: '1px solid var(--line)' }
  const td = { fontSize: 12.5, padding: '5px 8px', borderBottom: '1px solid var(--line)' }
  const tdr = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

  return (<div>
    <div className="head"><div>
      <div className="h-title">结果出具</div>
      <div className="h-sub">四步工作流第 4 步 · 一键导出正式对账底稿（对账汇总 + 差异清单 + 银行余额调节表 + 各户末笔流水 + 全部逐笔）</div>
    </div></div>
    <div className="body">
      <Steps current="result" onNav={onNav} />

      {/* 概览统计 */}
      <div className="cat">
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
          余额调节 · 全科目（与第3步同一张表）
          <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}> · {bs ? (bs.period || cfg.period) : cfg.period}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12 }}>
          <Stat label="在用账户（户）" v={nUse} hint="本期在用、非全零的全部账户（四类科目）" />
          <Stat label="已对平（户）" v={nTie} color="var(--green)" hint="银行侧有数且调节后差额为 0" />
          <Stat label="待补银行侧（户）" v={nWait} color="var(--amber)" hint="本月无流水/理财对账单/电商结算——银行侧余额待补" />
          <Stat label="真实差异（户）" v={nDiff} color={nDiff ? 'var(--red)' : 'var(--green)'} hint="调节后仍对不平、需核查" />
        </div>
        <div className="foot" style={{ marginTop: 10 }}>
          逐笔稽核：已匹配 <b>{matched}</b> 笔
          {timing > 0 && <> · 本月时间差 <b>{timing}</b> 笔（银行与金蝶记账月份不同，已在《余额调节表》由未达账项调平，<b>非真实差异</b>）</>}
          {problem > 0
            ? <> · <span style={{ color: 'var(--red)' }}>需人工处理 <b>{problem}</b> 笔</span>（见「差异清单」）</>
            : <> · 需人工处理 <b>0</b> 笔</>}
        </div>
      </div>

      {/* 左右两栏：按科目分解 / 待补清单 —— 铺满宽度 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(340px,1fr))', gap: 16, alignItems: 'start' }}>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>按科目分解</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={th}>科目</th><th style={{ ...th, textAlign: 'right' }}>在用</th>
              <th style={{ ...th, textAlign: 'right' }}>对平</th><th style={{ ...th, textAlign: 'right' }}>待补</th>
              <th style={{ ...th, textAlign: 'right' }}>差异</th>
            </tr></thead>
            <tbody>
              {catRows.map(r => <tr key={r.科目}>
                <td style={td}>{r.科目}</td>
                <td style={tdr}>{r.在用}</td>
                <td style={{ ...tdr, color: 'var(--green)' }}>{r.对平}</td>
                <td style={{ ...tdr, color: r.待补 ? 'var(--amber)' : 'var(--ink-3)' }}>{r.待补}</td>
                <td style={{ ...tdr, color: r.差异 ? 'var(--red)' : 'var(--ink-3)' }}>{r.差异}</td>
              </tr>)}
              {!catRows.length && <tr><td style={td} colSpan={5}>（加载中…）</td></tr>}
            </tbody>
          </table>
          <div className="foot" style={{ marginTop: 8 }}>差异全为 0＝有银行侧数据的户都对平；待补＝银行侧余额还没进来的户。</div>
        </div>

        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            待补银行侧 · {nWait} 户 <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 400 }}>（银行侧余额待补，不影响已对平的户）</span>
          </div>
          <div style={{ maxHeight: 340, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={th}>账户</th><th style={th}>主体</th><th style={th}>怎么补</th>
              </tr></thead>
              <tbody>
                {waitList.map((a, i) => <tr key={a['账号'] || i}>
                  <td style={td}>{a['账户名称'] || a['账号']}</td>
                  <td style={{ ...td, color: 'var(--ink-2)' }}>{a['主体'] || '—'}</td>
                  <td style={{ ...td, color: 'var(--ink-2)' }}>{waitReason(a)}</td>
                </tr>)}
                {!waitList.length && <tr><td style={td} colSpan={3}>（无待补，全部有银行侧数据）</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 导出 */}
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
