// [Change Log] Date:2026-09-01 Author:Claude/c Version:V2.416
// 解析清单收纳：跳过的文件（第三方支付/回单/证明/理财等）折叠进「展开逐个看」，不再一屏十几行；
// 但「解析失败」和「财资未并入」不折叠——没并入的真流水必须一眼看见。财资未并入分两态：
// 「重复」=已解析且逐笔查重、内容全在更全导出里（灰字）；「⚠核对」=有笔数只在此文件、疑似漏账（红字）。
// [Change Log] Date:2026-07-04 Author:Claude/c Version:V1.3
// 数据接入页（四步工作流第1步，独立成页）：银行流水来源(导入目录+解析清单) / 金蝶序时账 / 每家银行覆盖对照。
import React, { useEffect, useState } from 'react'
import { getDataSources, syncDataSources, setConfig, getConfig, uploadBankZip, refreshKingdee, confirmBankDup } from '../api.js'
import Steps from '../components/Steps.jsx'
import PeriodPicker from '../components/PeriodPicker.jsx'

let _cache = null

export default function DataImport({ cfg, onChange, onPeriod, onNav, user }) {
  const canUpload = !!(user && user.perms && user.perms.bank_upload)
  const canKingdee = !!(user && user.perms && user.perms.kingdee_refresh)
  const [d, setD] = useState(_cache)
  const [dir, setDir] = useState(cfg.bank_import_dir || '')
  const [file, setFile] = useState(null)
  const [zpwd, setZpwd] = useState('')
  const [busy, setBusy] = useState(false), [up, setUp] = useState(false), [ref, setRef] = useState(false)
  const [msg, setMsg] = useState(null)
  const [dupOpen, setDupOpen] = useState(false), [dupBusy, setDupBusy] = useState(false)
  useEffect(() => { getDataSources().then(x => { _cache = x; setD(x) }).catch(() => {}) }, [cfg.source, cfg.year, cfg.period])
  useEffect(() => { setDir(cfg.bank_import_dir || '') }, [cfg.bank_import_dir])
  // 财资重复判定待人工确认：只要标记还挂着，进页面就再弹——不确认不算完，刷新躲不掉
  useEffect(() => { if (d && d.bank_meta && d.bank_meta['重复待确认']) setDupOpen(true) }, [d])

  const syncStatus = async () => { try { const c = await getConfig(); onChange && onChange(c) } catch (e) {} }  // 刷新胶囊/侧栏封存态
  const doUpload = async () => {
    if (!file) return
    setUp(true); setMsg(null)
    try {
      const r = await uploadBankZip(file, zpwd)
      if (r.ok) {
        const x = await syncDataSources(); _cache = x; setD(x); setDir(r.dir); await syncStatus()
        if (r.need_dup_confirm) setDupOpen(true)
        if (r.period_mismatch) {
          // 流水实际月份 ≠ 所选期间：多半选错月份了，红字提醒（数据已存进所选期间，需切对月份重传）
          setMsg({ ok: false, t: `⚠ 月份不符！你选的期间是 ${r.sel_ym}，但上传的流水主要是 ${r.bank_ym} 的。已按你选的 ${r.sel_ym} 存入——若选错了，请把上方期间切到 ${r.bank_ym} 再重新上传。` })
        } else {
          setMsg({ ok: true, t: `✓ 已导入并解析：并入逐笔 ${r['并入笔数']} 笔（流水月份 ${r.bank_ym || '—'}，与所选期间一致）` })
        }
      }
      else setMsg({ ok: false, t: '⚠ ' + (r.msg || '上传失败') })
    } catch (e) { setMsg({ ok: false, t: '⚠ ' + String(e) }) } finally { setUp(false) }
  }
  const reparse = async () => {
    setBusy(true); setMsg(null)
    try {
      const c = await setConfig({ bank_import_dir: dir }); onChange && onChange(c)
      const x = await syncDataSources(); _cache = x; setD(x)
    } finally { setBusy(false) }
  }
  const doRefresh = async () => {
    setRef(true)
    try { await refreshKingdee(); const x = await syncDataSources(); _cache = x; setD(x); await syncStatus() }
    finally { setRef(false) }
  }
  const doConfirmDup = async () => {
    setDupBusy(true)
    try {
      const r = await confirmBankDup()
      if (r.ok) { const x = await getDataSources(); _cache = x; setD(x); setDupOpen(false) }
    } catch (e) { /* 失败保持弹窗，用户可重试 */ } finally { setDupBusy(false) }
  }

  const inp = { flex: 1, height: 34, borderRadius: 8, border: '1px solid var(--line-strong)', background: 'var(--bg)', color: 'var(--ink)', padding: '0 12px', fontSize: 13, fontFamily: 'inherit' }
  const kd = cfg.source === 'kingdee'
  const man = (d && d.manifest) || []
  const joined = man.filter(m => m['并入逐笔'])
  // 未并入但必须看见的：解析失败 / 财资让位（多份导出里较不全的那份）——折叠会把问题藏起来
  const attention = man.filter(m => !m['并入逐笔'] && (m['类型'] === '解析失败' || String(m['类型'] || '').startsWith('财资平台')))
  // 其余未并入的（第三方支付走渠道总额 + 回单/证明/理财等）统一折叠
  const folded = man.filter(m => !m['并入逐笔'] && !attention.includes(m))
  const foldedThird = folded.filter(m => m['类型'] !== '跳过').length
  // 财资归并涉及的全部文件（并入/重复/⚠核对）——重复确认弹窗逐条列示用
  const fzRows = man.filter(m => String(m['类型'] || '').startsWith('财资平台'))
  const cov = (d && d.coverage) || []
  const bankCount = joined.reduce((s, m) => s + (Number(m['笔数']) || 0), 0)
  // 本期数据状态：银行流水(谁/何时上传)、金蝶数据(取自何时)——按期间存，切期间就看那个月的
  // 兜底：新后端给「谁/何时」精确元信息；拿不到就看"有没有并入笔数/金蝶笔数"，只要有数据也判为已备（不因旧后端一直显未）
  const bMeta = d && d.bank_meta
  const bankReady = kd && ((bMeta && bMeta.updated_at) || bankCount > 0)
  const kdReady = kd && d && (d.kd_synced_at || (d.kd_count || 0) > 0)
  // 期间选择器旁的胶囊：优先用后端算好的，拿不到(旧后端)就本页自己从 d 推，保证一定显示
  const pillStatus = (cfg && cfg['数据状态'])
    || ((cfg && cfg['封存'] && cfg['封存']['已封存']) ? '已封存'
      : !kd ? '样例数据'
        : (bankReady || kdReady) ? '数据已上传' : '数据未上传')
  const StatusPill = ({ ok, okText, badText }) => (
    <span style={{
      fontSize: 11.5, padding: '2px 9px', borderRadius: 20, whiteSpace: 'nowrap',
      color: ok ? 'var(--green)' : 'var(--amber)', background: ok ? 'var(--green-bg)' : 'var(--amber-bg)',
      border: '1px solid ' + (ok ? 'var(--green-line)' : 'var(--amber-line)'),
    }}>{ok ? okText : badText}</span>)

  return (<div>
    <div className="head">
      <div><div className="h-title">数据接入</div>
        <div className="h-sub">四步工作流第 1 步 · 把当月银行流水与金蝶序时账接进来，供逐笔稽核</div></div>
      {onPeriod && <PeriodPicker year={cfg.year} period={cfg.period} onChange={onPeriod} status={pillStatus} />}
    </div>
    <div className="body">
      <Steps current="import" onNav={onNav} sub={{
        import: kd ? `银行 ${bankCount} 笔 · 金蝶 ${d ? d.kd_count : '—'} 笔` : '样例',
        reconcile: '下一步 ›' }} />
      {!kd && <div className="banner" style={{ marginBottom: 14 }}>当前数据源为「样例」（演示数据，固定 2026 年 6 月）。要接真数据、上传流水，请到<b>设置</b>切到「金蝶真数据」。</div>}

      {/* 本期数据状态：一眼看清这个月的原料备没备齐（银行流水 + 金蝶数据，都按期间存、上传/取数一次就定格） */}
      {kd && <div className="cat" style={{ background: 'var(--bg-sub)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>本期数据状态 · {d ? d.period : cfg.period + '期'}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ width: 78, color: 'var(--ink-3)', fontSize: 12.5 }}>银行流水</span>
            <StatusPill ok={bankReady} okText="已上传" badText="未上传" />
            <span style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>
              {bankReady
                ? <>{bMeta && bMeta.updated_at ? <>{bMeta.updated_by || '?'} 上传于 {bMeta.updated_at}　·　</> : null}并入逐笔 {bankCount} 笔
                  {bMeta && bMeta['流水月份'] && bMeta['流水月份'] !== (d ? d.period : '') &&
                    <b style={{ color: 'var(--red)', marginLeft: 8 }}>⚠ 流水月份 {bMeta['流水月份']} 与本期({d && d.period})不符，可能选错月份</b>}
                  {bMeta && bMeta['重复待确认'] &&
                    <b style={{ color: 'var(--amber)', marginLeft: 8, cursor: 'pointer', textDecoration: 'underline' }}
                      onClick={() => setDupOpen(true)}>⚠ 财资重复判定待人工确认（点此确认）</b>}
                  {bMeta && bMeta['重复确认人'] &&
                    <span style={{ color: 'var(--ink-3)', marginLeft: 8 }}>· 财资重复判定已由 {bMeta['重复确认人']} 于 {bMeta['重复确认时间']} 确认</span>}</>
                : '本期还没上传银行流水包（下方①上传）'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ width: 78, color: 'var(--ink-3)', fontSize: 12.5 }}>金蝶数据</span>
            <StatusPill ok={kdReady} okText="已取数" badText="未取数" />
            <span style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>
              {kdReady
                ? <>{d.kd_synced_at ? <>取自 {d.kd_synced_at}{d.kd_synced_by ? `（${d.kd_synced_by} 刷新）` : ''}　·　</> : null}1002序时账 {d.kd_count} 笔 / 科目余额 {d.balance_count != null ? d.balance_count : '—'} 行</>
                : '本期还没从金蝶取数（下方②点「从金蝶更新」）'}</span>
          </div>
        </div>
        {(!bankReady || !kdReady) && <div className="foot" style={{ marginTop: 10, color: 'var(--amber)' }}>
          本期数据未备齐，逐笔稽核 / 余额调节等会提示"本期未取数"。备齐后各页直接读、不再重新解析。
        </div>}
      </div>}

      {/* ① 上传资金流水 */}
      <div className="cat">
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>① 上传资金流水</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input type="file" accept=".zip,.rar,.7z" onChange={e => { setFile(e.target.files[0]); setMsg(null) }}
            style={{ fontSize: 12.5, flex: 1, minWidth: 240 }} />
          <input type="password" value={zpwd} onChange={e => setZpwd(e.target.value)} placeholder="压缩包密码（无则留空）"
            autoComplete="off" style={{ ...inp, flex: '0 0 190px', height: 34 }} />
          <button className="btn btn-pri" onClick={doUpload} disabled={!file || up || !kd || !canUpload}>{up ? '上传解析中…' : '上传并解析'}</button>
          {kd && !canUpload && <span className="foot" style={{ color: 'var(--red)' }}>无「上传资金流水」权限</span>}
        </div>
        <div className="foot" style={{ marginTop: 8 }}>把出纳月底导出的<b>整个流水文件夹打成一个压缩包</b>（<b>.zip / .rar</b> 都行）传上来即可。<b>加密包</b>在右侧填「压缩包密码」自动解压（ZIP 支持 AES；RAR 需服务器装 7-Zip/p7zip）。财资平台（宁波+招商，同一包里导了多份会自动按账户取最全的那份）、中行 HISQRY 自动逐笔并入；支付宝/微信/抖音归第三方（走渠道总额）；PDF/理财/余额表自动跳过。</div>
        {msg && <div style={{ marginTop: 8, fontSize: 12.5, color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.t}</div>}
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--ink-3)' }}>或：直接填本机文件夹路径（本地调试用）</summary>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
            <input type="text" value={dir} onChange={e => setDir(e.target.value)}
              placeholder="C:\\Users\\...\\6月流水" style={inp} />
            <button className="btn" onClick={reparse} disabled={busy || !kd}>{busy ? '解析中…' : '重新解析'}</button>
          </div>
        </details>

        {d && kd && <div style={{ marginTop: 12, fontSize: 12.5 }}>
          {joined.map((m, i) => <div key={i} style={{ display: 'flex', gap: 8, padding: '3px 0', flexWrap: 'wrap' }}>
            <span style={{ width: 46, color: 'var(--green)' }}>▶并入</span>
            <span style={{ minWidth: 120 }}>[{m['类型']}]</span>
            <span style={{ flex: '1 1 200px', wordBreak: 'break-all' }}>{m['文件']}</span>
            <span style={{ color: 'var(--ink-3)' }}>{m['笔数']} 笔{m['自校验'] ? ' · ' + m['自校验'] : ''}</span>
            {m['说明'] && <span style={{ color: String(m['说明']).includes('⚠') ? 'var(--red)' : 'var(--ink-3)' }}>{m['说明']}</span>}
          </div>)}
          {attention.map((m, i) => {
            const warn = m['类型'] === '解析失败' || String(m['说明'] || '').includes('⚠')
            // 未并入的真流水（重复/⚠核对/失败）整行红字加粗——必须显眼，不许看漏
            return <div key={i} style={{ display: 'flex', gap: 8, padding: '3px 0', flexWrap: 'wrap', color: 'var(--red)', fontWeight: 600 }}>
              <span style={{ width: 46 }}>{m['类型'] === '解析失败' ? '⚠失败' : (warn ? '⚠核对' : '重复')}</span>
              <span style={{ minWidth: 120 }}>[{m['类型']}]</span>
              <span style={{ flex: '1 1 200px', wordBreak: 'break-all' }}>{m['文件']}</span>
              <span>{m['说明'] || ''}</span></div>
          })}
          {folded.length > 0 && <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>
              跳过 {folded.length} 个文件（第三方支付走渠道总额 {foldedThird} 个 · 回单/证明/理财等不解析 {folded.length - foldedThird} 个）—— 展开逐个看
            </summary>
            <div style={{ marginTop: 4 }}>
              {folded.map((m, i) => <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0', color: 'var(--muted)', flexWrap: 'wrap' }}>
                <span style={{ width: 46 }}>跳过</span><span style={{ minWidth: 120 }}>[{m['类型']}]</span>
                <span style={{ flex: '1 1 200px', wordBreak: 'break-all' }}>{m['文件']}</span><span>{m['说明'] || ''}</span></div>)}
            </div>
          </details>}
        </div>}
      </div>

      {/* ② 金蝶取数 */}
      <div className="cat">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>② 金蝶取数</div>
          <button className="btn" onClick={doRefresh} disabled={ref || !kd || !canKingdee} title={!canKingdee ? '无「从金蝶更新」权限' : ''}>{ref ? '更新中…' : '从金蝶更新'}</button>
        </div>
        {kd ? <div style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>
          金蝶云星空 · {d ? d.period : cfg.period + '期'} · 只读拉取{kdReady ? <span style={{ color: 'var(--ink-3)' }}>{d.kd_synced_at ? <>　·　取自 {d.kd_synced_at}{d.kd_synced_by ? `（${d.kd_synced_by} 刷新）` : ''}</> : <>　·　已取数</>}</span> : <span style={{ color: 'var(--amber)' }}>　·　本期未取数</span>}
          <div style={{ marginTop: 8 }}>银行存款(1002) 序时账 <b style={{ color: 'var(--ink)' }}>{d ? d.kd_count : '—'}</b> 笔 <span style={{ color: 'var(--ink-3)' }}>（给逐笔稽核）</span></div>
          <div style={{ marginTop: 6 }}>科目余额表 <b style={{ color: 'var(--ink)' }}>{d && d.balance_count != null ? d.balance_count : '—'}</b> 行 <span style={{ color: 'var(--ink-3)' }}>（给资金看板 / 余额调节，覆盖四类资金科目）</span></div>
          {d && d.balance_by_subject && d.balance_by_subject.length > 0 &&
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
              {d.balance_by_subject.map((s, i) => <span key={i} style={{ padding: '3px 9px', borderRadius: 6, background: 'var(--bg)', border: '1px solid var(--line)' }}>{s['科目']} <b style={{ color: 'var(--ink)' }}>{s['行数']}</b></span>)}
            </div>}
        </div> : <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>样例数据（在设置切「金蝶真数据」后实拉）</div>}
        {d && d.error && <div className="banner err" style={{ marginTop: 8 }}>金蝶取数失败：{d.error}</div>}
      </div>

      {/* ③ 数据源覆盖 */}
      {kd && cov.length > 0 && <div className="cat">
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>③ 数据源覆盖（每家银行 金蝶 ↔ 银行）</div>
        <div className="foot" style={{ marginBottom: 8 }}>金蝶有分录、银行侧也接进来 = 已覆盖；银行侧缺（花旗/建行 PDF）= 待补，走「账户缺银行流水」，不算漏账。</div>
        <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 420, fontSize: 12.5, borderCollapse: 'collapse' }}>
          <thead><tr>{['银行', '金蝶笔数', '银行笔数', '状态'].map((h, i) =>
            <th key={h} style={{ textAlign: i === 0 ? 'left' : (i === 3 ? 'left' : 'right'), padding: '6px 10px', color: 'var(--ink-3)', borderBottom: '1px solid var(--line)', fontWeight: 500 }}>{h}</th>)}</tr></thead>
          <tbody>{cov.map((c, i) => <tr key={i}>
            <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--line)' }}>{c['银行']}</td>
            <td style={{ padding: '6px 10px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{c['金蝶笔数']}</td>
            <td style={{ padding: '6px 10px', textAlign: 'right', borderBottom: '1px solid var(--line)' }}>{c['银行笔数'] || '—'}</td>
            <td style={{ padding: '6px 10px', borderBottom: '1px solid var(--line)', color: c['状态'] === '已覆盖' ? 'var(--green)' : 'var(--amber)' }}>
              {c['状态'] === '已覆盖' ? '✓ 已覆盖' : '⏳ 待补'}</td>
          </tr>)}</tbody>
        </table>
        </div>
      </div>}
    </div>

    {/* 财资重复判定 · 人工确认弹窗：系统查重只是初核，是否重复由人确认并留痕（确认人+时间入审计） */}
    {dupOpen && <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,18,25,.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ background: 'var(--bg)', border: '1px solid var(--line-strong)', borderRadius: 12, padding: '20px 22px', width: 'min(680px, 92vw)', maxHeight: '80vh', overflowY: 'auto', boxShadow: '0 12px 40px rgba(0,0,0,.25)' }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>财资流水重复判定 · 需人工确认</div>
        <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.7 }}>
          本期包里发现<b>多份财资平台导出</b>。系统已逐份解析、按账户取最全并入，未并入部分做了逐笔查重——但查重只是机器初核，<b style={{ color: 'var(--red)' }}>是否重复由你确认；确认留痕（确认人＋时间，入审计）</b>。
        </div>
        <div style={{ margin: '12px 0', fontSize: 12.5 }}>
          {fzRows.map((m, i) => { const warn = String(m['说明'] || '').includes('⚠'); return (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '5px 0', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
              <span style={{ width: 46, color: m['并入逐笔'] ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{m['并入逐笔'] ? '▶并入' : (warn ? '⚠核对' : '重复')}</span>
              <span style={{ flex: '1 1 180px', wordBreak: 'break-all' }}>{m['文件']}</span>
              <span style={{ color: warn ? 'var(--red)' : 'var(--ink-3)', flex: '2 1 260px' }}>
                {m['并入逐笔'] ? `${m['笔数']} 笔 / ${m['账户数']} 个账户${m['说明'] ? ' · ' + m['说明'] : ''}` : (m['说明'] || '')}</span>
            </div>) })}
        </div>
        {fzRows.some(m => String(m['说明'] || '').includes('⚠'))
          ? <div style={{ fontSize: 12.5, color: 'var(--red)', fontWeight: 600, marginBottom: 12 }}>
              ⚠ 存在"只在让位文件里的笔数"（疑漏）——请先人工核对；确属重复/无碍再点确认，若是导出范围问题请退回出纳重新全量导出后重传。</div>
          : <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginBottom: 12 }}>
              逐笔查重结论：未并入文件的每一笔都能在已并入数据里找到（疑漏 0 笔）。</div>}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <button className="btn" onClick={() => setDupOpen(false)} disabled={dupBusy}>暂不确认（下次进入仍会提醒）</button>
          <button className="btn btn-pri" onClick={doConfirmDup} disabled={dupBusy || !canUpload}
            title={!canUpload ? '需「上传资金流水」权限' : ''}>{dupBusy ? '确认中…' : '我已逐条核对，确认重复判定'}</button>
        </div>
      </div>
    </div>}
  </div>)
}
