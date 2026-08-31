// [Change Log] Date:2026-08-07 Author:Claude/c Version:V2.241
// 报表导出·前端页。两页签（业务方定：放页面内，不做四级菜单）：
//   ①一键导出：选期间 → 勾主体（默认全选）→ 导出，后台跑、进度条实时回执
//   ②通知设置：落地路径（留空回落 conf.ini 兜底）——改路径须口令，同通知设置那把
// 一次一个月（业务方定）；境外主体只出本位币（后端已按账簿本位币去重）。
import React, { useEffect, useRef, useState } from 'react'
import { getRptExportOrgs, getRptExportConfig, saveRptExportConfig, runRptExport, getRptExportProgress, testRptExportNotify, requestRptExportSync, listRptExportFiles, deleteRptExportFiles, getRptExportPeriodStatus } from '../api.js'
import PeriodPicker from '../components/PeriodPicker.jsx'

// 秒 → 人话。跑 8 个主体要一分多钟，纯秒数（"87.3 秒"）读起来要在脑子里换算一次
const fmtSec = s => {
  const n = Math.round(Number(s) || 0)
  return n < 60 ? `${n} 秒` : `${Math.floor(n / 60)} 分 ${String(n % 60).padStart(2, '0')} 秒`
}

// "多久以前"。差值由**服务端**算好下发（ago_sec）——前端拿本机时钟去减服务器时间戳，
// 两边差几秒就会出负数或跳变（进度耗时那处踩过同一个坑）。
// 只给时长（「47 分钟」），"前/没回报"由调用处接，免得拼出「已 47 分钟前没回报」这种话
const fmtAgo = s => {
  if (s == null) return '不知多久'
  if (s < 90) return `${Math.max(0, Math.round(s))} 秒`
  if (s < 5400) return `${Math.round(s / 60)} 分钟`
  if (s < 172800) return `${Math.round(s / 3600)} 小时`
  return `${Math.round(s / 86400)} 天`
}

function defaultYM() {          // 默认上一个已结束的月——月结出报表就是导上个月的
  const n = new Date()
  let y = n.getFullYear(), m = n.getMonth()
  if (m === 0) { m = 12; y -= 1 }
  return { y, m }
}

export default function RptExport({ user }) {
  const can = k => !!(user && (user.role === 'admin' || (user.perms || {})[k]))
  const d0 = defaultYM()
  const [tab, setTab] = useState('run')
  const [year, setYear] = useState(d0.y)
  const [period, setPeriod] = useState(d0.m)
  const [orgs, setOrgs] = useState([])
  const [sel, setSel] = useState(new Set())
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  // 回执**跟着触发它的按钮走**，不都往页顶那个通栏塞：
  //   点的是页面底部的「立即同步」/「删除」，结果却弹在页顶——人点完还得抬头去找（业务方指出）。
  //   顶上那条只留跟主体清单/启动导出有关的（那些本来就在顶上）。
  const [syncMsg, setSyncMsg] = useState('')
  const [delMsg, setDelMsg] = useState('')
  const [prog, setProg] = useState(null)
  const [cfg, setCfg] = useState(null)
  const [cfgErr, setCfgErr] = useState('')      // 设置读不出来时的真实原因，见下方 useEffect
  // 期间选择器上的月份状态：这一年哪几个月导过、各导了几个主体。
  // 不走全局的 /api/period-statuses——那个是"账本上传/封存"的口径，跟"报表导出过没有"是两回事。
  const [pst, setPst] = useState({ statuses: {}, counts: {} })
  const [dir, setDir] = useState('')
  const [mails, setMails] = useState('')
  const [pass, setPass] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  // 已导文件管理（删除）——只有拿到敏感点 rpt_export_del 的人才看得见这一块
  const [mgr, setMgr] = useState(false)          // 展开/收起
  const [dfiles, setDfiles] = useState([])
  const [dsel, setDsel] = useState(new Set())
  const [alsoShare, setAlsoShare] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [dtOn, setDtOn] = useState(false)
  const [mailOn, setMailOn] = useState(true)
  const [dtMobiles, setDtMobiles] = useState('')
  const timer = useRef(null)
  // poll 是在 setInterval 闭包里跑的，直接读 cfg 会一直读到注册那一刻的旧值 → 用 ref 拿最新的
  const cfgRef = useRef(null)
  const applyCfg = c => {
    setCfg(c); cfgRef.current = c
    setDir(c.out_dir || ''); setMails((c.emails || []).join('; '))
    setDtMobiles((c.mobiles || []).join('; ')); setDtOn(!!c.dingtalk); setMailOn(c.email !== false)
  }

  // 同 cfgRef：poll 在 setInterval 闭包里跑，直接读 year 会读到注册那一刻的旧值
  const yearRef = useRef(year)
  useEffect(() => { yearRef.current = year }, [year])
  // 只刷「共享盘」那块，**不碰 ②页那几个输入框**——applyCfg 会把 dir/收件人 重设回服务器的值，
  // 定时跑的话会在人打字打到一半时把内容冲掉。
  const refreshSync = () => getRptExportConfig()
    .then(c => { setCfg(c); cfgRef.current = c }).catch(() => {})
  // 取件机每分钟一轮：导出刚跑完时它还没去搬，页面这时拿到的必然是"本期还没有文件"。
  // 不自动刷的话，人看到的就是"明明导好了，这里却说没有"（业务方实际撞到）。30 秒一次足够跟上。
  useEffect(() => {
    if (tab !== 'run') return undefined
    const t = setInterval(refreshSync, 30000)
    return () => clearInterval(t)
  }, [tab])

  const loadPst = () => getRptExportPeriodStatus(yearRef.current)
    .then(r => setPst({ statuses: r.statuses || {}, counts: r.counts || {} })).catch(() => {})

  const loadOrgs = () => {
    setLoading(true); setMsg('')
    getRptExportOrgs(year, period)
      .then(r => {
        setOrgs(r.orgs || [])
        setSel(new Set((r.orgs || []).map(o => o.org)))     // 默认全选（业务方：全选也可勾选）
        if (!r.ok) setMsg(r.msg || '')
        else if (!(r.orgs || []).length) setMsg(`金蝶里没有 ${year}年${period}期 的「财务报表」——请先在金蝶把该期报表生成出来。`)
      })
      .catch(e => setMsg('取主体清单失败：' + e.message))
      .finally(() => setLoading(false))
  }
  // ⚠ 换期间＝换了一整套上下文，**所有跟期间绑的状态都得跟着清**，不只是主体清单。
  //   同类错误已经犯过一次：只重载了主体清单，进度区还挂着上一期的结果，
  //   看上去像"切了期间但内容没变"。凡是与 year/period 有关的，一律在这里归零。
  useEffect(() => {
    loadOrgs()
    setProg(null)                       // 进度＝上一期那次跑的结果，换期即作废
    setDfiles([]); setDsel(new Set())   // 已导文件列表也是按期取的
    setSyncMsg(''); setDelMsg('')       // 回执同理，别把上一期的"已删除 8 个"留在新期间的页面上
    if (mgr) listRptExportFiles(year, period).then(r => setDfiles(r.files || [])).catch(() => {})
  }, [year, period])
  useEffect(() => { loadPst() }, [year])       // 月份状态按年取，切月不用重拉
  // ⚠ 这一发失败必须留下痕迹。原来是 .catch(()=>{}) 一吞了之，cfg 停在 null，
  //   于是 ②页 `!cfg?.can_edit` 恒真 → 页面言之凿凿地说"你没有改设置的权限"，
  //   而真实原因可能是 403（缺的是别的权限点）、500、或网络断——**报错报到了错的地方**，
  //   照着它去开权限永远开不对。现在把实际错误原样摆出来。
  useEffect(() => { getRptExportConfig().then(c => { setCfgErr(''); applyCfg(c) }).catch(e => setCfgErr(String(e?.message || e))) }, [])

  // 进度轮询：只在跑的时候轮，跑完自动停——别让页面一直空转打后端
  const poll = () => getRptExportProgress().then(p => {
    setProg(p)
    if (!p.running && timer.current) {
      clearInterval(timer.current); timer.current = null
      // 导出一跑完就自动通知取件机来取（这就是把「立即同步」并进主按钮：
      // 人点一次，导出＋同步一条龙，不用记得再去点第二个按钮）。
      // 服务器主动连内网取件机是被防火墙挡的方向，所以只能留标记、等它下轮来问。
      loadPst()                           // 刚导完，期间选择器上那个月要从"未导出"翻成"已导出 N"
      const after = () => getRptExportConfig().then(applyCfg).catch(() => {})
      if (cfgRef.current?.pull_token_set && (p.files || []).length) {
        requestRptExportSync().then(() => setTimeout(after, 1500)).catch(after)
      } else { after() }
    }
  }).catch(() => {})
  useEffect(() => { poll(); return () => timer.current && clearInterval(timer.current) }, [])
  const startPoll = () => { if (!timer.current) timer.current = setInterval(poll, 1500) }

  const run = async () => {
    setMsg('')
    const r = await runRptExport({ year, period, orgs: [...sel] })
    if (!r.ok) { setMsg(r.msg || '启动失败'); return }
    startPoll(); poll()
  }
  const save = async () => {
    setSaving(true); setMsg('')
    try {
      const r = await saveRptExportConfig({
        out_dir: dir, emails: mails, mobiles: dtMobiles,
        email_on: mailOn, dingtalk_on: dtOn, passcode: pass,
      })
      setMsg(r.msg || (r.ok ? '已保存' : '保存失败'))
      if (r.ok) { setPass(''); getRptExportConfig().then(applyCfg) }
    } finally { setSaving(false) }
  }

  // 取件机按期分桶回报的那一格。key 与落地目录同款：2026年07月
  const mth = cfg?.sync?.months?.[`${year}年${String(period).padStart(2, '0')}月`] || null
  const toggle = k => setSel(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n })
  const running = prog && prog.running
  const pct = prog && prog.total ? Math.round(prog.done * 100 / prog.total) : 0
  const box = { border: '1px solid var(--line)', borderRadius: 10, padding: 14, background: 'var(--bg-sub)' }
  // 框铺满整宽，但输入框限宽——一个邮箱地址拉成 1900px 既难看也难点中
  const inp = { width: '100%', maxWidth: 720, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--line)', fontSize: 13, boxSizing: 'border-box' }
  const tag = c => ({ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 5, marginLeft: 'auto', whiteSpace: 'nowrap', color: `var(--${c})`, background: `var(--${c}-bg)` })
  const tagOk = tag('green'), tagWarn = tag('amber')
  const tabBtn = k => ({
    padding: '7px 16px', borderRadius: 8, cursor: 'pointer', fontSize: 13.5, border: '1px solid var(--line)',
    fontWeight: tab === k ? 600 : 400,
    background: tab === k ? 'var(--accent-soft)' : 'transparent', color: tab === k ? 'var(--accent)' : 'var(--ink-2)',
  })

  return (<div>
    {/* 期间放标题行右上角（业务方定，与「账单上传」同款）。.head 本来就是 space-between，直接塞进去。 */}
    <div className="head">
      <div><div className="h-title">报表导出</div>
        <div className="h-sub">一个主体一个文件，五个表页：资产负债表 · 利润表 · 现金流量表 · 科目余额 · 序时账簿。从金蝶直取，只读不写。</div></div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 0 auto' }}>
        <PeriodPicker year={year} period={period} disabled={running}
          statusMap={pst.statuses} countMap={pst.counts}
          onChange={(y, m) => { setYear(y); setPeriod(m) }} />
      </div>
    </div>
    <div className="body">
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <div style={tabBtn('run')} onClick={() => setTab('run')}>① 一键导出</div>
        <div style={tabBtn('cfg')} onClick={() => setTab('cfg')}>② 通知设置</div>
      </div>

      {tab === 'run' && <>

        {/* 两个目录并排摆出来：文件先落服务器、再由取件机搬到共享盘。
            同事去哪儿拿文件＝看第二行；第二行的路径来自取件机自己回报的 dest，
            服务器并不知道那台电脑把文件放哪了，只能由它上报。 */}
        {cfg && <div style={{ ...box, marginBottom: 12, fontSize: 12.5, display: 'grid', gap: 6 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--ink-3)', flex: '0 0 auto', minWidth: 96 }}>① 服务器目录</span>
            <b style={{ color: cfg.dir_ok ? 'var(--ink)' : 'var(--amber)', wordBreak: 'break-all' }}>{cfg.effective || '未设置'}</b>
            <span style={{ color: 'var(--ink-3)' }}>
              /{year}年{String(period).padStart(2, '0')}月/
              {cfg.effective && !cfg.out_dir ? ' · 来自 conf.ini 兜底' : ''}
              {cfg.dir_ok ? ' · 可写 ✓' : ''}
            </span>
          </div>
          {!cfg.dir_ok && cfg.dir_msg && <div style={{ color: 'var(--amber)' }}>⚠ {cfg.dir_msg}</div>}
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--ink-3)', flex: '0 0 auto', minWidth: 96 }}>② 共享盘目录</span>
            {cfg.sync?.dest
              ? <>
                <b style={{ wordBreak: 'break-all' }}>{cfg.sync.dest}</b>
                <span style={{ color: 'var(--ink-3)' }}>/{year}年{String(period).padStart(2, '0')}月/ · 同事在这儿拿文件</span>
              </>
              : <span style={{ color: 'var(--ink-3)' }}>取件机还没回报过——它跑起来后这里会显示实际路径</span>}
          </div>
        </div>}

        {msg && <div className="banner" style={{ marginBottom: 12 }}>{msg}</div>}

        {loading ? <div className="loading">读取主体清单…</div> : orgs.length > 0 && <div style={{ ...box, marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <b style={{ fontSize: 13.5 }}>主体（{sel.size}/{orgs.length}）</b>
            <span style={{ fontSize: 12.5, color: 'var(--accent)', cursor: 'pointer' }}
              onClick={() => setSel(new Set(orgs.map(o => o.org)))}>全选</span>
            <span style={{ fontSize: 12.5, color: 'var(--accent)', cursor: 'pointer' }} onClick={() => setSel(new Set())}>清空</span>
            <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>只出本位币（境外主体＝美元）</span>
            {/* 主按钮就摆在主体清单这一行的最右（业务方定）：勾完主体，眼睛不用离开就点得到导出，
                不必回到页面顶部去找按钮。 */}
            <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
              {!can('rpt_export') && <span style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>（你没有「报表导出·一键导出」权限）</span>}
              <button className="btn-pri" onClick={run}
                disabled={running || loading || !sel.size || !can('rpt_export')}
                style={{ height: 34, padding: '0 18px', fontSize: 13.5, borderRadius: 8 }}>
                {running ? `导出中… ${prog?.done || 0}/${prog?.total || sel.size}`
                  : (cfg?.pull_token_set ? `导出并同步共享盘（${sel.size} 个主体）` : `导出选中的 ${sel.size} 个主体`)}
              </button>
            </span>
          </div>
          {/* 竖排一主体一行（业务方定）：横向铺开时境外主体名会被截成「Starfield Food and Science Technol…」，
              而主体名正是勾选时唯一的判据，截了就得靠猜。行数只有 8 行，竖排不占地方。 */}
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {orgs.map((o, i) => (
              <label key={o.org} style={{
                display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, padding: '7px 8px', borderRadius: 7,
                cursor: running ? 'default' : 'pointer',
                borderTop: i ? '1px solid var(--line)' : 'none',
                background: sel.has(o.org) ? 'var(--accent-soft)' : undefined,
              }}>
                <input type="checkbox" checked={sel.has(o.org)} disabled={running} onChange={() => toggle(o.org)} />
                <span style={{ color: 'var(--ink-3)', minWidth: 34, fontVariantNumeric: 'tabular-nums' }}>{o.org}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.org_name}</span>
                {/* 币别做成色标：美元＝蓝色实心标，人民币＝灰字。境外主体只有 3 家，一眼扫得出来是哪几家 */}
                <span style={o.cur === '美元'
                  ? { fontSize: 11.5, fontWeight: 600, color: 'var(--blue)', background: 'var(--blue-bg)', border: '1px solid var(--blue-line)', padding: '2px 8px', borderRadius: 5, whiteSpace: 'nowrap' }
                  : { fontSize: 11.5, color: 'var(--ink-3)', padding: '2px 8px', whiteSpace: 'nowrap' }}>{o.cur}</span>
              </label>))}
          </div>
        </div>}

        {prog && (prog.running || prog.total > 0 || prog.msg) && <div style={box}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
            <b style={{ fontSize: 13.5 }}>进度</b>
            <span style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>
              {prog.done}/{prog.total || '?'}{prog.cur ? ` · 正在 ${prog.cur}` : ''}
            </span>
            {/* 耗时由**服务端**算好下发：前端拿本机时钟去减服务器时间戳，两边差几秒就会出负数或跳变 */}
            {prog.elapsed != null && <span style={{
              marginLeft: 'auto', fontSize: 12.5, fontVariantNumeric: 'tabular-nums',
              color: running ? 'var(--accent)' : 'var(--ink-2)', fontWeight: 600,
            }}>
              {running ? '已用 ' : '总耗时 '}{fmtSec(prog.elapsed)}
            </span>}
          </div>
          {(prog.started || prog.finished) && <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 8 }}>
            开始 {prog.started}{prog.finished ? ` · 结束 ${prog.finished}` : ''}
          </div>}
          <div style={{ height: 8, borderRadius: 4, background: 'var(--line)', overflow: 'hidden', marginBottom: 10 }}>
            <div style={{ width: pct + '%', height: '100%', background: 'var(--accent)', transition: 'width .3s' }} />
          </div>
          {prog.msg && <div style={{ fontSize: 13, marginBottom: 8 }}>{prog.msg}</div>}
          {prog.files && prog.files.length > 0 && <div style={{ fontSize: 12.5, color: 'var(--ink-2)' }}>
            {prog.files.map(f => <div key={f.org} style={{ padding: '2px 0', display: 'flex', gap: 8 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                ✓ {f.name} <span style={{ color: 'var(--ink-3)' }}>
                  （科目余额 {f.rows?.['科目余额']} 行 · 序时账簿 {f.rows?.['序时账簿']} 行）</span>
              </span>
              {/* 逐主体耗时：101 要 40 多秒、其它 1 秒内，摆出来就知道慢在哪个主体，不会以为卡死了 */}
              {f.sec != null && <span style={{ color: 'var(--ink-3)', fontVariantNumeric: 'tabular-nums', flex: '0 0 auto' }}>
                {fmtSec(f.sec)}</span>}
            </div>)}
          </div>}
          {prog.errors && prog.errors.length > 0 && <div style={{ fontSize: 12.5, color: 'var(--red,#c0392b)', marginTop: 8 }}>
            {prog.errors.map(e => <div key={e.org} style={{ padding: '2px 0' }}>✗ {e.org} {e.name}：{e.err}</div>)}
          </div>}
        </div>}

        {/* 取件机状态。**只回答两个问题**（业务方定）：
              ① 共享盘上最新的文件是什么时候的　② 它现在还在不在干活。
            原来这里摆的是取件机的内部账（本轮搬几个、跳过几个）——那是它的工作量，
            不是人要的答案；而且计数是【全部期间合计】，摆在"2026年5期"下面直接被误读
            （业务方实际问过"5 期明明 8 个，怎么写 16"）。内部账降级成小灰字放最后。 */}
        {cfg?.pull_token_set && <div style={{ ...box, marginTop: 12, fontSize: 12.5 }}>
          <div className="row" style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: cfg.sync ? 10 : 0 }}>
            <b style={{ fontSize: 13.5 }}>共享盘</b>
            <span style={{ color: 'var(--accent)', cursor: 'pointer', marginLeft: 'auto' }}
              onClick={async () => {
                const r = await requestRptExportSync()
                // 回执写在这一行旁边，不发去页顶那个通栏——它是「立即同步」的结果，
                // 摆在页面顶上等于让人点完之后还要抬头去找（业务方指出）。
                setSyncMsg(r.msg || (r.ok ? '已通知取件机' : '请求失败'))
                setTimeout(() => getRptExportConfig().then(applyCfg).catch(() => {}), 1500)
              }}>立即同步 ↻</span>
          </div>
          {!cfg.sync
            ? <span style={{ color: 'var(--amber)' }}>还没收到取件机回报——那台内网电脑上的定时任务可能没跑起来。</span>
            : <div style={{ display: 'grid', gap: 7 }}>
              {/* ① 本期最新的文件是什么时候的。**只看本期**（业务方定）——这一屏本来就是按期间看的，
                  给全部期间合计只会被读错（"选的是 5 期只有 8 个，怎么写 16"）。 */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--ink-3)', flex: '0 0 auto', minWidth: 112 }}>本期最新文件</span>
                {/* ⚠ 三种情况必须分清。第一种最容易被误判成第三种：老版取件机不发 months，
                    后端存成 {}，而 `{}` 在 JS 里是真值——用 `!months` 判断就会让页面
                    言之凿凿地说"共享盘上没有"，其实是它根本不知道。故靠后端的 months_ok。 */}
                {!cfg.sync.months_ok
                  ? <span style={{ color: 'var(--amber)' }}>
                    取件机还是旧版，不会报这一项——<b>请把「报表取件机」的包也升级</b>（升级后下一轮即有）
                  </span>
                  : mth?.newest_at
                    ? <>
                      <b style={{ color: 'var(--ink)' }}>{mth.newest_at}</b>
                      <span style={{ color: 'var(--ink-2)', wordBreak: 'break-all' }}>{mth.newest}</span>
                      <span style={{ color: 'var(--ink-3)' }}>· 本期共 {mth.n} 个文件</span>
                    </>
                    : <span style={{ color: 'var(--amber)' }}>
                      共享盘上还没有 {year}年{period}期 的文件
                      <span style={{ color: 'var(--ink-3)' }}>
                        　——这一期还没导，或刚导完、取件机下一轮才去搬（约 1 分钟）。本区每 30 秒自动刷新。
                      </span>
                    </span>}
              </div>
              {/* ② 还在不在干活 */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--ink-3)', flex: '0 0 auto', minWidth: 112 }}>取件机</span>
                {cfg.sync.alive
                  ? <><b style={{ color: 'var(--green,#1f7a55)' }}>✓ 在跑</b>
                    <span style={{ color: 'var(--ink-3)' }}>
                      {fmtAgo(cfg.sync.ago_sec)}前回报过 · {cfg.sync.host || '未知'} · 每分钟一轮
                    </span></>
                  : <><b style={{ color: 'var(--amber)' }}>⚠ 停了</b>
                    <span style={{ color: 'var(--amber)' }}>
                      已 {fmtAgo(cfg.sync.ago_sec)}没回报（{cfg.sync.host || '未知'}）——
                      那台电脑可能关机了，或定时任务被停了。<b>共享盘上的文件不会再更新。</b>
                    </span></>}
              </div>
              {/* 待重取≠失败：你点导出时取件机正好在取，拿到的是刚被覆盖的新文件、
                  大小与清单对不上。下一轮自动重取即可，不该刷红字吓人。 */}
              {(cfg.sync.retry || []).length > 0 && <div style={{ color: 'var(--ink-3)' }}>
                ↻ {cfg.sync.retry.length} 个文件在取件时正好被重导覆盖，下一轮自动重取（不是错误）
              </div>}
              {(cfg.sync.errors || []).map((e, i) => <div key={i} style={{ color: 'var(--amber)' }}>⚠ {e}</div>)}
              {/* 内部账：排查时才看，平时不该抢视线。同样**只算本期**。 */}
              {mth && <div style={{ color: 'var(--ink-3)', fontSize: 11.5, borderTop: '1px solid var(--line)', paddingTop: 6 }}>
                本期最近一轮：新搬 {mth.copied} 个、原样跳过 {mth.skipped} 个
                {!mth.copied ? '（新搬 0 个＝这一期没有新东西要搬，是正常的）' : ''}
              </div>}
            </div>}
          {syncMsg && <div style={{ marginTop: 6, color: 'var(--accent)' }}>{syncMsg}</div>}
        </div>}
      </>}

      {/* 已导文件管理：列出该期服务器上已有的文件，可勾选删除。
          有这个入口是因为——只有管理员进得去宝塔，普通会计根本删不了服务器上的文件，
          "整份作废"那条路对使用者等于不存在。 */}
      {tab === 'run' && can('rpt_export_del') && <div style={{ ...box, marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <b style={{ fontSize: 13.5 }}>已导出的文件</b>
          <span style={{ fontSize: 12.5, color: 'var(--accent)', cursor: 'pointer' }}
            onClick={() => {
              const n = !mgr; setMgr(n); setDelMsg('')
              if (n) listRptExportFiles(year, period).then(r => { setDfiles(r.files || []); setDsel(new Set()) }).catch(() => {})
            }}>{mgr ? '收起' : `查看 / 删除 ${year}年${period}期 ↓`}</span>
          <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>
            数据错了直接重导即可，不用删；这里是给「整份作废」用的
          </span>
        </div>
        {mgr && <div style={{ marginTop: 10 }}>
          {!dfiles.length ? <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>服务器上没有 {year}年{period}期 的导出文件。</div>
            : <>
              <div style={{ display: 'flex', flexDirection: 'column', marginBottom: 10 }}>
                {dfiles.map((f, i) => (
                  <label key={f.rel} style={{
                    display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5, padding: '6px 8px',
                    borderRadius: 6, cursor: 'pointer', borderTop: i ? '1px solid var(--line)' : 'none',
                    background: dsel.has(f.rel) ? 'var(--red-bg)' : undefined,
                  }}>
                    <input type="checkbox" checked={dsel.has(f.rel)} onChange={() => setDsel(s => {
                      const n = new Set(s); n.has(f.rel) ? n.delete(f.rel) : n.add(f.rel); return n
                    })} />
                    <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.rel}</span>
                    {/* 散件＝还没按月份分目录那会儿导的，平铺在根目录上。标出来，免得人以为"怎么又冒出来一批" */}
                    {f.loose && <span style={{ fontSize: 11, flex: '0 0 auto', color: 'var(--amber)', background: 'var(--amber-bg)', border: '1px solid var(--amber-line)', padding: '1px 7px', borderRadius: 5 }}>旧散件</span>}
                    <span style={{ color: 'var(--ink-3)', flex: '0 0 auto' }}>{Math.round(f.size / 1024)} KB · {f.mtime}</span>
                  </label>))}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12.5, color: 'var(--accent)', cursor: 'pointer' }}
                  onClick={() => setDsel(new Set(dfiles.map(f => f.rel)))}>全选</span>
                <span style={{ fontSize: 12.5, color: 'var(--accent)', cursor: 'pointer' }} onClick={() => setDsel(new Set())}>清空</span>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, cursor: 'pointer' }}>
                  <input type="checkbox" checked={alsoShare} onChange={e => setAlsoShare(e.target.checked)} />
                  同时删共享盘上那份
                </label>
                <button className="btn" disabled={!dsel.size || deleting}
                  style={dsel.size ? { borderColor: 'var(--red)', color: 'var(--red)', fontWeight: 600 } : undefined}
                  onClick={async () => {
                    // 破坏性且不可撤销，删之前把文件名摆出来让人自己核一遍
                    if (!window.confirm(`确定删除以下 ${dsel.size} 个文件？此操作不可撤销。\n\n${[...dsel].join('\n')}\n\n${alsoShare ? '共享盘上那份也会一并删除。' : '共享盘上那份保留不动。'}`)) return
                    setDeleting(true); setDelMsg('')
                    try {
                      const r = await deleteRptExportFiles({ rels: [...dsel], also_share: alsoShare })
                      setDelMsg(r.msg || (r.ok ? '已删除' : '删除失败'))
                      const l = await listRptExportFiles(year, period)
                      setDfiles(l.files || []); setDsel(new Set())
                      loadPst()                  // 删完个数要跟着降，删光了要翻回"未导出"
                    } finally { setDeleting(false) }
                  }}>{deleting ? '删除中…' : `删除选中的 ${dsel.size} 个`}</button>
              </div>
            </>}
          {/* 删除回执就摆在按钮下面。删的是共享盘上的文件，回执跑到页顶去，人根本看不到删成没成 */}
          {delMsg && <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--ink)' }}>{delMsg}</div>}
        </div>}
      </div>}


      {/* 每块**独占一整行、横向铺满**（业务方定）。
          先前做成三栏并排，每栏只剩 500 来像素，说明文字被挤成 5、6 行的竖条，路径还断行——
          宽度是有了，可读性反而更差。改成竖向堆叠、每块占满整宽：说明文字一两行就写完，
          长路径也不断行。 */}
      {tab === 'cfg' && <div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* ── 落地路径 ── */}
          <div style={box}>
            <b style={{ fontSize: 13.5 }}>导出文件落到哪儿</b>
            <div style={{ fontSize: 12.5, color: 'var(--ink-2)', margin: '8px 0 12px', lineHeight: 1.7 }}>
              ⚠ 这是<b>服务器上</b>的路径，不是你自己电脑上的——后端在服务器上写文件。
              服务器是 Linux，要写成 <code>/www/wwwroot/...</code>，不能用 <code>D:\</code> 或 <code>\\NAS\</code> 这种 Windows 写法。
              <br />文件按月分子目录存放（<code>2026年07月/</code>），取件机在共享盘上原样镜像同一结构。
              <br /><b>数据错了要更正：直接重新导出即可，不要去共享盘删文件。</b>
              服务器是原件、共享盘是镜子——重导后取件机会自动覆盖；而只删镜子的话，它下一轮会照着原件再照出来。
              整份作废才需要删，且必须<b>先删服务器上的、再删共享盘上的</b>（顺序反了会被补回来）。
              {/* 别用「当前」称呼兜底值——「当前」会被读成"眼下生效的"，而生效的是上面输入框那个。
                  业务方实际读错过：输入框填了 2 个人，看到括号里「当前 1 人」以为没保存上。 */}
              <br /><b>留空</b>时才回落服务器 <code>conf.ini</code> 里配的兜底路径{cfg?.fallback ? <>（<b>兜底配的是</b>：<b>{cfg.fallback}</b>）</> : '（未配兜底）'}。
            </div>
            <input value={dir} onChange={e => setDir(e.target.value)} placeholder={cfg?.fallback || '留空则用 conf.ini 兜底路径'}
              disabled={!cfg?.can_edit} style={inp} />
            {cfg && <div style={{ fontSize: 12.5, marginTop: 10, color: cfg.dir_ok ? 'var(--green,#1f7a55)' : 'var(--amber)' }}>
              实际生效：<b>{cfg.effective || '未设置'}</b>{cfg.dir_ok ? ' · 目录可写 ✓' : (cfg.dir_msg ? ' · ' + cfg.dir_msg : '')}
            </div>}
          </div>

          {/* ── 邮件 ── */}
          <div style={box}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: cfg?.can_edit ? 'pointer' : 'default' }}>
              <input type="checkbox" checked={mailOn} disabled={!cfg?.can_edit} onChange={e => setMailOn(e.target.checked)} />
              <b style={{ fontSize: 13.5 }}>邮件通知</b>
              {cfg && <span style={cfg.smtp_configured ? tagOk : tagWarn}>
                {cfg.smtp_configured ? '服务器已配 SMTP' : '服务器未配 SMTP'}</span>}
            </label>
            <div style={{ fontSize: 12.5, color: 'var(--ink-2)', margin: '8px 0 12px', lineHeight: 1.7 }}>
              每次导出结束发一封，<b>成功和失败都发</b>：期间、成功几个主体、失败几个及原因、落地路径、逐主体行数。
              <br />多个收件人用分号隔开。留空 ＝ 回落 <code>conf.ini</code> 的 <code>[smtp] to</code>
              {cfg?.fallback_to?.length ? <>（<b>兜底配的是</b>：<b>{cfg.fallback_to.join('; ')}</b>）</> : '（未配）'}。
            </div>
            <input value={mails} onChange={e => setMails(e.target.value)} disabled={!cfg?.can_edit || !mailOn}
              placeholder="张三@example.com; 李四@example.com" style={{ ...inp, opacity: mailOn ? 1 : .5 }} />
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 8 }}>
              发件邮箱：{cfg?.smtp_from || '未配置'} · 账号密码只在服务器 conf.ini，本页不经手、不回显。
            </div>
          </div>

          {/* ── 钉钉 ── */}
          <div style={box}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: cfg?.can_edit ? 'pointer' : 'default' }}>
              <input type="checkbox" checked={dtOn} disabled={!cfg?.can_edit} onChange={e => setDtOn(e.target.checked)} />
              <b style={{ fontSize: 13.5 }}>钉钉通知</b>
              {cfg && <span style={cfg.dingtalk_configured ? tagOk : tagWarn}>
                {cfg.dingtalk_configured ? '服务器已配钉钉' : '服务器未配钉钉'}</span>}
            </label>
            <div style={{ fontSize: 12.5, color: 'var(--ink-2)', margin: '8px 0 12px', lineHeight: 1.7 }}>
              与邮件同样内容，走钉钉工作通知发到人。<b>填手机号</b>（钉钉账号绑定的那个），多个用分号隔开。
              <br />留空 ＝ 回落 <code>conf.ini</code> 的 <code>[dingtalk] to_mobiles</code>
              {/* 兜底手机号要把号码本身摆出来，光写「当前 N 人」等于没说——
                  人要判断的是"我留空的话会发给谁"，一个计数回答不了这个问题
                  （旁边邮箱那行就是 join 出来的，两边口径本该一致）。
                  中间四位打码：这一屏可能被投屏或截图，号码是个人信息。 */}
              {cfg?.dt_fallback?.length
                ? <>（<b>兜底配的是 {cfg.dt_fallback.length} 人</b>：<b>{cfg.dt_fallback
                    .map(m => String(m).replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2')).join('；')}</b>）</>
                : '（未配）'}。
            </div>
            <input value={dtMobiles} onChange={e => setDtMobiles(e.target.value)} disabled={!cfg?.can_edit || !dtOn}
              placeholder="13800138000; 13900139000" style={{ ...inp, opacity: dtOn ? 1 : .5 }} />
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 8 }}>
              钉钉应用凭证（AppKey/Secret）只在服务器 conf.ini，本页不经手、不回显。
            </div>
            {cfg && !cfg.dingtalk_configured && dtOn && <div className="banner" style={{ marginTop: 10 }}>
              服务器 <code>conf.ini</code> 的 <code>[dingtalk]</code> 段还没配（appkey/appsecret/agentid），
              打开开关也发不出去。请联系管理员配置。
            </div>}
          </div>
        </div>

        {/* ── 保存 + 测试：放最底下，一次管三块 ── */}
        <div style={{ ...box, marginTop: 14 }}>
          {/* 先分清"读不出来"和"没权限改"——混成一句话的后果是照着提示去开权限，永远开不对 */}
          {cfgErr && <div className="banner" style={{ color: 'var(--amber)' }}>
            <b>设置读不出来，上面显示的都不是服务器上的真实值。</b><br />
            实际错误：<code>{cfgErr}</code><br />
            <span style={{ fontSize: 12 }}>
              末尾是 <b>403</b> ＝ 你这个账号缺「报表导出·一键导出」权限（不是"改设置"那个）；
              <b>500</b> ＝ 服务器出错，看后端日志；连不上 ＝ 服务没起来或网络不通。
            </span>
          </div>}
          {!cfgErr && cfg && !cfg.can_edit && <div className="banner">
            你（<b>{user?.name}</b>）没有「报表导出·改落地路径与通知」权限（敏感点，默认不给）——上面几项只能看不能改。要改请找主管理员开通。
          </div>}
          {cfg?.can_edit && !cfg?.passcode_set && <div className="banner">
            服务器未设置口令（<code>conf.ini</code> 的 <code>[notify] passcode</code> 为空），暂不能从页面改，请联系管理员配置。
          </div>}
          {cfg?.can_edit && cfg?.passcode_set && <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <input type="password" value={pass} onChange={e => setPass(e.target.value)} placeholder="口令（与通知设置同一把）"
              style={{ ...inp, width: 240 }} />
            <button className="btn-pri" onClick={save} disabled={saving || !pass}>{saving ? '保存中…' : '保存设置'}</button>
            <button className="btn" disabled={testing || (!mailOn && !dtOn)}
              onClick={async () => {
                setTesting(true); setMsg('')
                try { const r = await testRptExportNotify(); setMsg(r.msg || (r.ok ? '已发出' : '发送失败')) }
                finally { setTesting(false) }
              }}>{testing ? '发送中…' : '发送测试通知'}</button>
            <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>路径、邮件、钉钉一起存</span>
          </div>}
          {msg && <div className="banner" style={{ marginTop: 12 }}>{msg}</div>}
        </div>
      </div>}
    </div>
  </div>)
}
