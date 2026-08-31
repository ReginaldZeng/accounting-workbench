// [Change Log] Date:2026-07-12 Author:Claude/c Version:V2.91
// 会计期间选择器（自定义下拉，替代原生 select）：点开是 年度切换 + 12 个【彩色状态胶囊】月份。
// 颜色＝该月数据状态：灰=已封存 / 绿=数据已上传 / 琥珀=数据未上传 / 浅灰=样例。触发按钮显当前年月+当前月状态小胶囊。
// 全项目 7 处共用；不再需要外挂的独立胶囊。
import React, { useState, useEffect, useRef } from 'react'
import { getPeriodStatuses } from '../api.js'

const YEARS = [2025, 2026, 2027]
const SHORT = { '已封存': '已封存', '数据已上传': '已上传', '数据未上传': '未导入', '样例数据': '样例',
  '已计提': '已计提', '未计提': '未计提', '已上传': '已上传',   // V2.219/221 物流计提月份态（source="logi"）
  '已导出': '已导出', '未导出': '未导出',                       // V2.241 报表导出月份态（statusMap 外部喂）
  '已核': '已核', '未核': '未核' }                              // V2.338 临时工考勤月份态（同为外部喂）
// 封存=灰蓝(锁定)、已上传/已计提=绿、未导入/未计提=琥珀、样例=浅灰——三态颜色分明
const CHIP = {
  '已封存': { c: 'var(--ink-2)', bg: 'var(--bg-rail)', b: 'var(--line-strong)' },
  '数据已上传': { c: 'var(--green)', bg: 'var(--green-bg)', b: 'var(--green-line)' },
  '数据未上传': { c: 'var(--amber)', bg: 'var(--amber-bg)', b: 'var(--amber-line)' },
  '样例数据': { c: 'var(--ink-3)', bg: 'var(--bg-sub)', b: 'var(--line)' },
  '已计提': { c: 'var(--green)', bg: 'var(--green-bg)', b: 'var(--green-line)' },
  '未计提': { c: 'var(--amber)', bg: 'var(--amber-bg)', b: 'var(--amber-line)' },
  '已上传': { c: 'var(--blue)', bg: 'var(--blue-bg)', b: 'var(--blue-line)' },   // 账单已传未录入（V2.221）
  '已导出': { c: 'var(--green)', bg: 'var(--green-bg)', b: 'var(--green-line)' },
  '未导出': { c: 'var(--amber)', bg: 'var(--amber-bg)', b: 'var(--amber-line)' },
  '已核': { c: 'var(--green)', bg: 'var(--green-bg)', b: 'var(--green-line)' },
  '未核': { c: 'var(--amber)', bg: 'var(--amber-bg)', b: 'var(--amber-line)' },
}

// source：缺省=全局账本（银行对账那一套）；传 'cl:<账簿代码>'=成本台账某主体的账本。
// 成本台账按主体各记各的期间，不指定账本会把别人的月份状态显示在这里。
//
// statusMap/countMap/disabled 是 V2.241 加的可选参数，**不传就是原行为**（7 处旧调用不受影响）：
//   statusMap  自己喂 {'1':'已导出',...}，喂了就不再请求 /api/period-statuses——
//              有些工具的"月份状态"不在全局账本里（如报表导出＝落地目录里有没有文件），
//              硬塞进那个共享接口会让 7 个页面跟着一起变，风险不划算。
//   countMap   {'7': 8} → 胶囊显示「已导出 8」。只写"已导出"看不出这月是全导了还是只导了两家。
//   disabled   跑批过程中锁住，别让人中途切期间（切了进度条还挂在上一期上）。
export default function PeriodPicker({ year, period, onChange, status, source, statusMap, countMap, disabled,
                                      countLabel = '个主体',
                                      countNote = '数字＝该月已导出的主体个数' }) {
  // V2.338：countLabel 用于悬停（「：40 人」），countNote 是图例那一句。
  // 分成两个是因为一句话套不住两处语法——「数字＝该月的人」读不通。默认值＝报表导出原文，旧调用零变化。
  const y = year || 2026, p = period || 6
  const own = !!statusMap                       // 状态由调用方喂＝本组件不自己去拉
  const [st, setSt] = useState({})     // {'1':'已封存', ...}
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  // 拉这一年 12 个月状态；年度变、账本变（切主体）、或本期状态变（上传/封存后）都重拉
  useEffect(() => {
    if (own) return
    let live = true
    // ⚠取状态失败**不能静悄悄**：这个 catch 曾把后端一个 NameError（app.py 里 cl: 分支引用了
    //   已搬进 routers 的函数）吞成"没有状态"，于是成本台账右上角十二个月一个胶囊都不显示，
    //   看着像"这功能压根没做"，一直没人报（V2.299 才查出来）。UI 仍降级不挡人用，但要留声。
    getPeriodStatuses(y, source).then(r => { if (live) setSt(r.statuses || {}) })
      .catch(e => { console.warn('[PeriodPicker] 取期间状态失败，胶囊将不显示：', source, e); if (live) setSt({}) })
    return () => { live = false }
  }, [y, source, status, own])
  useEffect(() => {
    if (!open) return
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  const all = own ? statusMap : st
  const cnt = m => (countMap || {})[String(m)]
  const curSt = all[String(p)] || status
  const curChip = CHIP[curSt]
  const pick = (m) => { setOpen(false); onChange(y, m) }

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button type="button" disabled={disabled} onClick={() => setOpen(o => !o)}
        title={disabled ? '正在跑批，跑完再切期间' : '选择会计年月（各月带数据状态）'} style={{
          height: 34, display: 'inline-flex', alignItems: 'center', gap: 8, padding: '0 12px',
          borderRadius: 8, border: '1px solid var(--line-strong)', background: 'var(--bg)',
          color: 'var(--ink)', fontSize: 13, fontFamily: 'inherit',
          cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .55 : 1,
        }}>
        <span style={{ fontWeight: 500 }}>{y} 年 {p} 期</span>
        {curChip && <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, color: curChip.c, background: curChip.bg, border: '1px solid ' + curChip.b }}>
          {SHORT[curSt]}{cnt(p) ? ' ' + cnt(p) : ''}</span>}
        <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && <div style={{
        position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 50, width: 300,
        background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10,
        boxShadow: '0 10px 30px rgba(0,0,0,.16)', padding: 12,
      }}>
        {/* 年度切换 */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          {YEARS.map(yy => <span key={yy} onClick={() => onChange(yy, p)} style={{
            flex: 1, textAlign: 'center', padding: '5px 0', borderRadius: 7, fontSize: 12.5, cursor: 'pointer',
            fontWeight: yy === y ? 600 : 400, color: yy === y ? 'var(--accent)' : 'var(--ink-2)',
            background: yy === y ? 'var(--accent-soft)' : 'var(--bg-sub)',
            border: '1px solid ' + (yy === y ? 'var(--accent)' : 'var(--line)'),
          }}>{yy} 年</span>)}
        </div>
        {/* 12 个月·彩色状态胶囊 */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
          {Array.from({ length: 12 }, (_, i) => i + 1).map(m => {
            const s = all[String(m)], ch = CHIP[s] || CHIP['样例数据'], sel = m === p, n = cnt(m)
            return <div key={m} onClick={() => pick(m)} title={(s || '') + (n ? `：${n} ${countLabel}` : '')} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
              padding: '7px 10px', borderRadius: 8, cursor: 'pointer', background: ch.bg,
              border: '2px solid ' + (sel ? 'var(--accent)' : ch.b),
            }}>
              <span style={{ fontWeight: 600, color: 'var(--ink)', fontSize: 12.5 }}>{m} 期</span>
              {s && <span style={{ fontSize: 10.5, color: ch.c }}>
                {SHORT[s]}{n ? <b style={{ marginLeft: 4, fontVariantNumeric: 'tabular-nums' }}>{n}</b> : ''}</span>}
            </div>
          })}
        </div>
        {/* 图例：自己喂状态的（statusMap）按实际出现的状态生成，别把"已封存"这种它根本没有的态列出来 */}
        {own
          ? <div style={{ display: 'flex', gap: 12, marginTop: 10, fontSize: 10.5, color: 'var(--ink-3)', flexWrap: 'wrap' }}>
            {[...new Set(Object.values(statusMap))].filter(s => CHIP[s]).map(s =>
              <span key={s}>● <span style={{ color: CHIP[s].c }}>{SHORT[s]}</span></span>)}
            {countMap && <span>{countNote}</span>}
          </div>
          : <div style={{ display: 'flex', gap: 12, marginTop: 10, fontSize: 10.5, color: 'var(--ink-3)', flexWrap: 'wrap' }}>
            <span>● <span style={{ color: 'var(--green)' }}>绿</span>={source === 'logi' ? '已计提' : '已上传'}</span>
            {source === 'logi' && <span>● <span style={{ color: 'var(--blue)' }}>蓝</span>=已上传未录</span>}
            <span>● <span style={{ color: 'var(--amber)' }}>琥珀</span>={source === 'logi' ? '未计提' : '未导入'}</span>
            <span>● <span style={{ color: 'var(--ink-2)' }}>灰</span>=已封存</span>
          </div>}
      </div>}
    </div>
  )
}
