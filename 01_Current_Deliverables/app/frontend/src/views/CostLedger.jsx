// [Change Log] Date:2026-07-17 Author:Claude/c Version:V2.141 (原 V2.58/…/V2.130/V2.131/V2.140)
// V2.141 新增第④步「事务类型透视」：本期收入/发出按业务类型（FBusinessType）归集，随🅰取数落库；
//   七步→八步，其后步骤序号顺延。导出同步新增「收发存·按事务类型」表。
// V2.130 新增第④步「收发存明细」：按 仓库/存货类别/物料关键字/只看负结存 查，服务端筛+分页；
//   仓库透视的仓库名与格子可点 → 跳到第④步并带上筛选。六步→七步，其后步骤序号顺延。
// V2.129 右上角改用全站共用 PeriodPicker（12 期彩色状态胶囊），账本指到本主体（cl:<账簿代码>）；
//   去掉与第⑥步重复的导出按钮，导出只留在「台账出具」那一步，并补上漏掉的 年/期/主体 参数。
// V2.257 新增第⑧步「制造费用」：成本三道勾稽（制造费用归集/完工结转/投入归集）+ 车间×成本项目
//   + 费用项目构成；八步→九步，台账出具顺延为第⑨步。**刻意不与存货那三道同屏**——两边是不同的账。
// 存货台账 › 台账导出（原成本台账·月结核对）九步工作流页。
//   数据接入 → 三道勾稽 → 仓库透视 → 事务类型透视 → 收发存明细 → 异常稽核 → 损益归集 → 制造费用 → 台账出具。
// V2.118 加入「仓库透视」屏（仓库 × 存货类别，金额+数量，按仓库类型分组小计）。
// V2.122 期间化 + 封存：进页面/切期间直接读 /state（数据按期落库、全员看到同一份、重启不丢）；
//   已封存 → 读快照、本期只读（不再取金蝶/不能重传）；导出按【页面上这一期】取数（不再是全局最后一次）。
// 内核=kernels/cost_ledger.py；科目余额走金蝶只读；导出复用后端 /api/cost-ledger/export。
import React, { useEffect, useState } from 'react'
import PeriodPicker from '../components/PeriodPicker.jsx'
import { analyzeCostLedgerKingdee, getCostLedgerState, getCostLedgerOrgs,
  getCostLedgerDetail, closeCostLedgerPeriod, reopenCostLedgerPeriod, yuan } from '../api'

const STEPS = [
  { k: 'import', n: '数据接入', d: '一键从金蝶只读取回本期六项' },
  { k: 'tie', n: '三道勾稽', d: '两表互勾 / 收发存自平 / 账实勾稽' },
  { k: 'wh', n: '仓库透视', d: '仓库 × 存货类别 · 数量与金额' },
  { k: 'btype', n: '事务类型透视', d: '收入 / 发出按业务类型归集' },
  { k: 'detail', n: '收发存明细', d: '按仓库 / 类别 / 物料查' },
  { k: 'anomaly', n: '异常稽核', d: '负结存 / 挂账尾差 / 对照缺失' },
  { k: 'pnl', n: '损益归集', d: '货损 / 处置 · 业务↔总账互核' },
  { k: 'mfg', n: '制造费用', d: '成本三道勾稽 · 车间 × 成本项目' },
  { k: 'export', n: '台账出具', d: '导出《成本台账》Excel' },
]

const fmt = n => Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
// 单价：显示 2 位（金蝶原值是 6 位，导出的 Excel 里存的仍是 6 位全精度）。
// null＝金蝶没给（数量为 0 时如此）→「—」，不写 0——0 会被读成"单价真是零"。
const price = p => (p === null || p === undefined) ? '—' : fmt(p)
// 数量：整数位分组、最多 2 位小数（数量不像金额那样固定 2 位，避免 1,953,324.45 变 ...4500）
const qty = n => Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })

export default function CostLedger({ user }) {
  const [step, setStep] = useState('import')
  const [year, setYear] = useState(2026)
  const [period, setPeriod] = useState(5)
  const [org, setOrg] = useState('')          // 账簿代码；空=还没拿到主体清单
  const [orgs, setOrgs] = useState([])
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [slice, setSlice] = useState('全部')
  const [sealing, setSealing] = useState(false)   // 封存/解封面板展开
  const [note, setNote] = useState('')
  // 第⑤步 收发存明细（V2.130）：筛选条件 + 结果。仓库透视的格子点一下就跳进来并带上筛选
  const [dq, setDq] = useState({ wh: '', cat: '', q: '', neg: 0, offset: 0 })
  const [dData, setDData] = useState(null)        // {rows,total,sum,whs,cats,offset,limit}
  const [dBusy, setDBusy] = useState(false)

  const closed = !!(res && res.closed)
  // 硬校验（V2.124）：本期有数据的仓库必须都配了仓库类型，否则不能封存、不能导出。
  // 已封存的读快照（封存时已过校验），照常可导出。
  const missWh = (res && res.missing_wh) || []
  // 能力位来自后端（V2.128 权限重分）：没权限就【不给按钮】，而不是点了才被拒
  const can = (res && res.can) || {}
  const canExport = !!(res && res.has_data && (res.closed || res.can_seal))

  // 进页面/切期间：直接读本期状态（数据在库里、全员一份），不取金蝶、不重算
  const loadState = async (y, p) => {
    setBusy(true); setErr(''); setMsg(''); setSealing(false); setNote('')
    try {
      const r = await getCostLedgerState(y, p, org)
      if (!r.ok) { setErr(r.msg || '读取本期状态失败'); setRes(null) }
      else if (!r.has_data) { setRes(r); setStep('import') }   // 保留 closed 等状态，只是没数据
      else { setRes(r); setStep(s => (s === 'import' ? 'tie' : s)) }
    } catch (e) { setErr('读取本期状态失败：' + e.message); setRes(null) }
    setBusy(false)
  }
  useEffect(() => {
    (async () => {
      try {
        const r = await getCostLedgerOrgs()
        if (r.ok) { setOrgs(r.orgs || []); setOrg(o => o || r.default) }
        else setErr(r.msg || '读取主体清单失败')
      } catch (e) { setErr('读取主体清单失败：' + e.message) }
    })()
  }, [])
  // 切主体/切期间都重读本期状态（各主体各期一档，互不串）
  useEffect(() => { if (org) loadState(year, period) }, [year, period, org])

  // 切主体/期间后旧明细与旧筛选必须丢掉——否则会把上一个主体的料显示在新主体名下
  useEffect(() => { setDq({ wh: '', cat: '', q: '', neg: 0, offset: 0 }); setDData(null) }, [year, period, org])
  // 只在明细步才拉数：别的步骤没人看，拉了白拉
  useEffect(() => {
    if (step !== 'detail' || !org || !(res && res.has_data)) return
    let live = true
    setDBusy(true)
    getCostLedgerDetail({ year, period, org, ...dq })
      .then(r => { if (!live) return; if (r.ok) setDData(r); else { setDData(null); setErr(r.msg || '读取明细失败') } })
      .catch(e => { if (live) { setDData(null); setErr('读取明细失败：' + e.message) } })
      .finally(() => { if (live) setDBusy(false) })
    return () => { live = false }
  }, [step, dq, year, period, org, res && res.has_data])
  // 改任一筛选条件都要回到第 1 页——否则会停在第 3 页却只剩 2 页数据，显示空白
  const setF = (patch) => setDq(d => ({ ...d, ...patch, offset: 0 }))
  // 仓库透视格子 → 跳进明细步并带上筛选（看到可疑的数，就地能查）
  const jumpDetail = (wh, cat = '') => { setDq({ wh, cat, q: '', neg: 0, offset: 0 }); setStep('detail') }

  const runKingdee = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await analyzeCostLedgerKingdee(year, period, org)
      if (!r.ok) { setErr(r.msg || '取数失败') }
      else { setRes(r); setStep('tie') }
    } catch (e) { setErr('金蝶取数失败：' + e.message) }
    setBusy(false)
  }

  // 成本计算单原表（V2.288）：可选补件，传了导出多一张原样底稿；不传不影响任何勾稽。

  const doSeal = async (force) => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await closeCostLedgerPeriod({ year, period, org, note, force: !!force })
      if (!r.ok) setErr(r.msg || '封存失败')
      else { await loadState(year, period); setMsg(r.msg) }
    } catch (e) { setErr('封存失败：' + e.message) }
    setBusy(false)
  }

  const doReopen = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await reopenCostLedgerPeriod({ year, period, org, reason: note })
      if (!r.ok) setErr(r.msg || '解封失败')
      else { await loadState(year, period); setMsg(r.msg) }
    } catch (e) { setErr('解封失败：' + e.message) }
    setBusy(false)
  }

  const Steps = () => (
    <div className="steps">
      {STEPS.map((s, i) => {
        const has = !!(res && res.has_data)
        const done = has && STEPS.findIndex(x => x.k === step) > i
        const cur = step === s.k
        const clickable = s.k === 'import' || has
        return (
          <div key={s.k} className={'step' + (cur ? ' cur' : '') + (done ? ' done' : '')}
            style={{ cursor: clickable ? 'pointer' : 'default' }}
            onClick={() => clickable && setStep(s.k)}>
            <div className="num">{done ? '✓' : i + 1}</div>
            <div><div className="sn">{s.n}</div><div className="sd">{s.d}</div></div>
          </div>
        )
      })}
    </div>
  )

  const ties = res && res.has_data && res.ties
  const bva = ties && ties.book_vs_actual
  const an = res && res.has_data ? { counts: res.anomaly_counts || {}, items: res.anomaly_items || [] } : null
  const anTotal = an ? Object.values(an.counts).reduce((a, b) => a + b, 0) : 0
  // 待处理 = 负结存+挂账尾差+对照缺失（成本调整提示为仅提示、不算待处理）
  const actionable = an ? (an.counts['负结存'] || 0) + (an.counts['挂账尾差'] || 0) + (an.counts['对照缺失'] || 0) : 0
  const shownItems = an ? (slice === '全部' ? an.items : an.items.filter(i => i.status === slice)) : []

  return (
    <>
      <div className="head">
        <div>
          <div className="h-title">成本台账 · 存货月结核对</div>
          <div className="h-sub">存货收发存汇总表 × 总账科目余额 · 三道勾稽到分毫，异常自动成清单 · 金蝶只读</div>
        </div>
        <div className="h-tools">
          <span className="selctl"><span className="k">主体</span>
            <select value={org} onChange={e => setOrg(e.target.value)} style={sel} disabled={!orgs.length}>
              {orgs.map(o => <option key={o.code} value={o.code}>{o.name}</option>)}
            </select>
          </span>
          {/* 期间＝全站共用选择器：点开是 12 期彩色状态胶囊。source 指到本主体的账本，
              否则显示的会是银行对账的月份状态。切主体时账本跟着换，状态自动重拉。 */}
          {org && <PeriodPicker year={year} period={period} source={'cl:' + org}
            status={res && res.data_status}
            onChange={(y, p) => { setYear(y); setPeriod(p) }} />}
          {/* 正常状态选择器自己就带了胶囊；只有"封存了却找不到快照"这种异常才另外提示 */}
          {res && res.data_status === '已封存·快照缺失' && <span className="pill" style={pillOf(res)}>{res.data_status}</span>}
        </div>
      </div>

      <div className="scroll" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Steps />
        {err && <div className="trust" style={{ color: 'var(--red)', borderColor: 'var(--red-line)', background: 'var(--red-bg)' }}>{err}</div>}
        {msg && <div className="trust" style={{ color: 'var(--green)', borderColor: 'var(--green-line)', background: 'var(--green-bg)' }}>✓ {msg}</div>}

        {/* 硬校验告警：有仓库没配类型 → 这些仓的钱落进「（属性缺失）」，封存/导出都卡住 */}
        {!closed && missWh.length > 0 && <div className="trust" style={{ color: 'var(--amber)', borderColor: 'var(--amber-line)', background: 'var(--amber-bg)' }}>
          ⚠ <b>{missWh.length} 个仓库没配仓库类型</b>，它们的结存在仓库透视里落进「（属性缺失）」：
          {missWh.slice(0, 8).join('、')}{missWh.length > 8 ? ` 等 ${missWh.length} 个` : ''}。
          <b>补齐前不能封存、也不能导出台账。</b>
          <span style={{ fontSize: 12, color: 'var(--ink-3)', marginLeft: 6 }}>
            请到「存货台账 › 基础资料」配好（新仓库工具已自动上档、标了「新」，类型要你填——工具不替你猜）。</span>
        </div>}

        {/* 封存状态条：已封存＝本期只读、读的是封存那一刻的快照 */}
        {closed && <div className="trust" style={{ color: 'var(--ink-2)', borderColor: 'var(--line-strong)', background: 'var(--bg-sub)' }}>
          <b style={{ color: 'var(--accent)' }}>🔒 {year} 年第 {period} 期已封存</b>
          <span style={{ fontSize: 12, marginLeft: 8 }}>
            {(res.period_info || {})['封存人'] || '?'} 于 {(res.period_info || {})['封存时间'] || '?'} 封存
            {(res.period_info || {})['金蝶取数时点'] ? ` · 数据取回时点 ${res.period_info['金蝶取数时点']}` : ''}
            {(res.period_info || {})['封存说明'] ? ` · ${res.period_info['封存说明']}` : ''}
          </span>
          <span style={{ fontSize: 12, marginLeft: 8, color: 'var(--ink-3)' }}>
            本期只读：不再取金蝶、不能重传，看到的是封存那一刻拍照存下的数据。</span>
          {can.reopen
            ? <span className="chip" style={{ marginLeft: 'auto' }} onClick={() => setSealing(!sealing)}>解封…</span>
            : <span className="chip" style={{ marginLeft: 'auto', opacity: .5, cursor: 'not-allowed' }}
              title="解封是高危操作，限主管理员/核算子管理员">解封（无权限）</span>}
        </div>}
        {closed && sealing && <div className="card" style={{ ...cardS, borderColor: 'var(--amber-line)' }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>解封 {year} 年第 {period} 期</div>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 8 }}>
            解封是<b>高危操作</b>（只有主管理员/核算子管理员可执行），必须填理由、全程留痕。
            封存前那一版快照会保留可查；改完记得<b>重新封存</b>。
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input placeholder="解封理由（至少 5 个字，供领导与审计核查）" value={note} onChange={e => setNote(e.target.value)}
              style={{ flex: 1, padding: '5px 8px', borderRadius: 6, border: '0.5px solid var(--line-strong)', fontSize: 12 }} />
            <button className="btn-sec" disabled={busy || note.trim().length < 5} onClick={doReopen}>确认解封</button>
          </div>
        </div>}

        {/* 本期状态条：只报状态、不带动作。
            V2.131 起【封存按钮挪到台账出具（现第⑧步）】——封存是出完台账才做的收尾动作，
            挂在每一页顶上等于每屏都在催你封存，还容易在中途误点。 */}
        {!closed && res && res.has_data && step !== 'import' && <div className="trust" style={{ fontSize: 12 }}>
          <span className="lead">本期状态</span>
          <span style={{ color: 'var(--ink-2)' }}>
            进行中 · <b>{(res.meta || {}).updated_by || '?'}</b> 于 {(res.meta || {}).updated_at || '?'}
            通过<b>{res.source === 'kingdee' ? '一键金蝶取数' : '上传报表（旧通道，V2.317 已停用）'}</b>接入。
            有成本台账权限的同事看到的是同一份。
          </span>
          {step !== 'export' && <span style={{ marginLeft: 'auto', color: 'var(--ink-3)' }}>
            核对完在第⑧步「台账出具」导出与封存
          </span>}
        </div>}

        {/* 第①步 数据接入 · 双通道（已封存则不给取数入口——点了也会被后端拦，不如不给） */}
        {step === 'import' && closed && (
          <div className="card" style={cardS}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>① 数据接入</div>
            <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7 }}>
              {year} 年第 {period} 期<b>已封存</b>，本期不再接入数据。
              {res.has_data ? '上方各屏看到的是封存时拍照存下的那一版。' : '但本期没有封存快照——数据可能在封存前就缺失，请解封后重新接入。'}
              要改动请先在上方<b>解封</b>。
            </div>
          </div>
        )}
        {step === 'import' && !closed && (<>
          <div className="card" style={cardS}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>① 数据接入 · 使用说明</div>
            <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.8 }}>
              <b>这个工具干什么</b>：把金蝶的<b>存货收发存汇总表</b>和<b>总账科目余额</b>摆在一起核——三道勾稽核到分毫、
              异常自动成清单，最后出《成本台账》Excel。<b>全程金蝶只读，工具不往金蝶写任何东西。</b>
              <div style={{ margin: '6px 0 2px' }}>
                <b>怎么走</b>：右上角选<b>主体</b>和<b>期间</b> → 下面点「一键金蝶取数」 → 顺着上方九步看过去 → 第⑨步导出台账。
              {res && !can.fetch && <span style={{ color: 'var(--ink-3)' }}>（你没有取数权限，可以查看与导出；要取数请找管理员授予「成本台账·取数/上传」）</span>}
              </div>
              <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid var(--line)' }}>
                <b>三件要先知道的</b>
                <div style={{ marginLeft: 2 }}>
                  {/* V2.131 订正：不是"全员"——进这页要有「成本台账·查看与导出」权限，共享范围＝被授权的人 */}
                  · <b>数据在有权限的同事之间共享</b>：按「主体＋期间」存在服务器上，谁取的数、
                  <b>拿到「成本台账·查看与导出」的同事</b>看到的都是同一份，服务重启也不丢（没授权的人进不来这页）。
                  {res && res.has_data
                    ? <span style={{ color: 'var(--amber)' }}> 本期已有数据（{(res.meta || {}).updated_by || '?'} 于 {(res.meta || {}).updated_at || '?'} 接入），<b>再取一次会覆盖它</b>，覆盖后所有有权限的人看到的都是新的那份。</span>
                    : <> 再取一次会覆盖上一次的。</>}<br />
                  · <b>仓库类型没配全会卡住</b>：本期有结存的仓库必须都配了仓库类型，否则<b>不能封存、也不能导出</b>——
                  新仓库工具会自动上档并标「新」，但类型要你填（工具不替你猜，猜错了钱会默默归错小计）。去「存货台账 › 基础资料」补。<br />
                  · <b>封存＝把这个月定死</b>：结果拍照存下，之后本期只读、不再取金蝶，谁进来看到的都是封存那一刻那份数据。
                  <b>每个主体一条封存线</b>，互不影响；要改得先解封（高危、留痕）。
                </div>
              </div>
            </div>
          </div>

          <div>
            <div className="card" style={cardS}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>一键金蝶取数<span style={{ fontSize: 11, fontWeight: 400, color: 'var(--green)', marginLeft: 6 }}>免导表 · 一次取全</span></div>
              <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, minHeight: 96 }}>
                点一下，工具替你从金蝶<b>只读</b>把这一期要用的都取回来，你什么都不用导：
                <div style={{ margin: '3px 0 3px 2px' }}>
                  ① <b>存货收发存汇总表（跨维度）</b>——它<b>就是明细</b>：拆到 物料×规格×仓库×批号×库存状态，
                  第⑤步的收发存明细就出自它<br />
                  ② <b>存货收发存汇总表（按日期）</b>——跟①互相验证合计，即勾稽①两表互勾<br />
                  ③ <b>存货类科目余额（14xx）</b>——账实勾稽的"账"<br />
                  ④ <b>收发存流水（按业务类型）</b>——第④步事务类型透视<br />
                  ⑤ <b>成本计算单 + 成本类科目</b>——第⑧步制造费用的三道成本勾稽<br />
                  ⑥ <b>管理费用/营业外支出的凭证分录</b>——第⑦步损益归集
                </div>
                {/* V2.292 起物料编码与存货类别改由报表直出，不再靠物料档案按"名称+规格"回查——
                    那个 join 撞名会把整行挂到别的物料名下（3 月实测 109 个物料金额两两对调）。 */}
                <b>物料编码与存货类别报表直接给</b>，不再靠物料档案关联（同名同规格会撞错）。
                <div style={{ marginTop: 3 }}>
                  <b>三道勾稽全跑</b>：两表互勾、收发存自平、账实勾稽。
                  <span style={{ color: 'var(--ink-3)' }}>差异不为 0 时页面会指出差在哪——例如存货类别被改过、
                  而总账的科目结转没同步追溯，就会留下几元到几千元的差额（第⑥步「类别漂移」会点名是哪些物料）。</span>
                </div>
              </div>
              {can.fetch
                ? <button className="btn-pri" disabled={busy} onClick={runKingdee}>
                  {busy ? '取数中…' : '一键金蝶取数'}</button>
                : <span className="btn-pri" style={noPerm} title="需「成本台账·取数/上传」权限">一键金蝶取数</span>}
            </div>

          </div>
        </>)}

        {/* 第②步 三道勾稽 */}
        {step === 'tie' && res && res.has_data && ties && (<>
          <div className="trust">
            <span className="lead">可信度</span>
            <span className="checks">
              {ties.two_reports && <span className={'chk ' + (ties.two_reports.pass ? 'pass' : 'warn')}>
                {ties.two_reports.pass ? '✓' : '✗'} 勾稽① 两表互勾</span>}
              <span className={'chk ' + (ties.self_balance.pass ? 'pass' : 'warn')}>
                {ties.self_balance.pass ? '✓' : '✗'} 勾稽② 收发存自平</span>
              {bva && <span className={'chk ' + (bva.pass ? 'pass' : 'warn')}>
                {bva.pass ? '✓' : '✗'} 勾稽③ 账实勾稽</span>}
            </span>
            <span className="verdict" style={{ marginLeft: 'auto', color: res.credible ? 'var(--green)' : 'var(--amber)', background: res.credible ? 'var(--green-bg)' : 'var(--amber-bg)', border: '1px solid ' + (res.credible ? 'var(--green-line)' : 'var(--amber-line)') }}>
              {res.credible ? '本期台账可信 · 可出台账' : '存在未过勾稽 · 待复核'}</span>
          </div>
          {/* gl_msg 可能是多行诊断（如账簿代码在金蝶查无此账簿，会把金蝶现有账簿都列出来）→ pre-line
              保住换行，否则整段挤成一行、最该看的"金蝶那边有哪些"淹在里面。
              V2.253：账簿改按代码认，原先"全称差一个字"那类诊断已不复存在 */}
          {res.gl_msg && <div className="trust" style={{ fontSize: 12, color: 'var(--amber)',
            whiteSpace: 'pre-line', lineHeight: 1.7, alignItems: 'flex-start' }}>⚠ {res.gl_msg}</div>}
          {/* V2.255：按日期表取数失败＝勾稽①这道没跑。必须单独说——它跟科目余额是两回事，
              混在一条里会让人以为只是账实勾稽出问题，而实际少的是两表互勾 */}
          {res.bd_msg && <div className="trust" style={{ fontSize: 12, color: 'var(--amber)',
            whiteSpace: 'pre-line', lineHeight: 1.7, alignItems: 'flex-start' }}>⚠ {res.bd_msg}</div>}
          {res.bt_msg && <div className="trust" style={{ fontSize: 12, color: 'var(--amber)',
            whiteSpace: 'pre-line', lineHeight: 1.7, alignItems: 'flex-start' }}>⚠ {res.bt_msg}</div>}
          {res.source === 'kingdee' && <div className="trust" style={{ fontSize: 12, color: 'var(--ink-2)', background: 'var(--bg-sub)' }}>
            <b style={{ color: 'var(--accent)' }}>一键金蝶取数</b>：跨维度表与按日期表<b>都由工具从金蝶只读取回</b>，
            带仓库/批号维度，与你自己从金蝶导表的口径一致；<b>三道勾稽全跑</b>。
            <b>损益归集也在本通道内</b>——工具从管理费用/营业外支出的科目余额下钻取凭证分录，
            不需要报表里带货损/盘盈亏页。</div>}

          <div className="kpis" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
            <div className="kpi"><div className="kl">期末结存（收发存）</div><div className="kv">{yuan(res.pivot_category['合计'] && res.pivot_category['合计'].ea)}</div><div className="kt">{res.rows_cross.toLocaleString()} 行明细</div></div>
            <div className="kpi"><div className="kl">总账存货合计</div><div className="kv">{bva ? yuan(bva.book_total) : '—'}</div><div className="kt">含在途 / 委托加工</div></div>
            <div className="kpi prio"><div className="kl">待处理异常</div><div className="kv">{actionable}</div><div className="kt">负结存 {an.counts['负结存'] || 0} · 尾差 {an.counts['挂账尾差'] || 0} · 提示 {an.counts['成本调整提示'] || 0}</div></div>
            <div className="kpi"><div className="kl">损益归集</div><div className="kv">{res.pnl ? ((res.pnl.other && res.pnl.other.total !== 0) ? '3 组' : '2 组') : '—'}</div><div className="kt">{res.pnl ? ((res.pnl.other && res.pnl.other.total !== 0) ? '管理费用 / 营业外 / 其他' : '管理费用 / 营业外') : '本次未含损益页'}</div></div>
          </div>

          {bva && <div className="twrap">
            <table>
              <thead><tr><th>总账科目</th><th className="r">科目余额</th><th>对应存货类别</th><th className="r">收发存结存</th><th className="r">差异</th><th>状态</th></tr></thead>
              <tbody>
                {Object.entries(bva.subjects).map(([subj, s]) => (
                  <tr key={subj}>
                    <td><b>{subj}</b></td><td className="r">{fmt(s.book)}</td>
                    <td style={{ fontSize: 12, color: 'var(--ink-2)' }}>{s.cats.join(' ＋ ')}</td>
                    <td className="r">{fmt(s.actual)}</td><td className="r">{fmt(s.diff)}</td>
                    <td><span className={'pill ' + (s.pass ? 'ok' : 'bad')}>{s.pass ? '✓ 勾稽平' : '✗ 不平'}</span></td>
                  </tr>
                ))}
                {Object.entries(bva.extra).map(([subj, v]) => (
                  <tr key={subj}><td>{subj}</td><td className="r">{fmt(v)}</td><td style={{ fontSize: 12, color: 'var(--ink-3)' }}>—（科目单列并入合计）</td><td className="r">—</td><td className="r">—</td><td><span className="pill mut">单列</span></td></tr>
                ))}
                <tr className="total"><td><b>总账存货合计</b></td><td className="r"><b>{fmt(bva.book_total)}</b></td><td>＝ 收发存结存 ＋ 在途 ＋ 委托加工</td><td className="r"><b>{fmt(bva.actual_total)}</b></td><td className="r"><b>{fmt(bva.book_total - bva.actual_total)}</b></td><td /></tr>
              </tbody>
            </table>
          </div>}
          {bva && bva.unmapped && bva.unmapped.length > 0 && <div className="trust" style={{ color: 'var(--amber)' }}>⚠ 对照缺失：{bva.unmapped.map(u => u.cat).join('、')} 未配置科目对照，请到配置补对照关系（不硬归）。</div>}
        </>)}

        {/* 第③步 仓库透视 · 仓库 × 存货类别 */}
        {step === 'wh' && res && res.has_data && res.pivot_wh_category && (() => {
          const P = res.pivot_wh_category
          const cell = (v) => v
            ? <><div>{fmt(v.ea)}</div><div style={qs}>{qty(v.eq)}</div></>
            : <span style={{ color: 'var(--ink-3)' }}>—</span>
          return (<>
            <div className="trust" style={{ fontSize: 12, color: 'var(--ink-2)', background: 'var(--bg-sub)' }}>
              每格<b>上行＝结存金额</b>、<span style={qs}>下行＝结存数量</span>。仓库按<b>仓库类型</b>分组，组内按金额降序；
              仓库类型取自对照表（基础数据可维护）。<b>总计 {fmt(P.total.ea)} 元 / {qty(P.total.eq)}</b>，与账实勾稽的收发存结存同源。
              <div style={{ marginTop: 4 }}>
                👉 <b>点仓库名</b>查这个仓有哪些料；<b>点某一格</b>只查该仓这个类别——都会跳到第⑤步「收发存明细」，筛选已填好。
              </div>
            </div>
            <div className="twrap">
              <table style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ minWidth: 130 }}>仓库</th>
                    <th style={{ minWidth: 76 }}>仓库类型</th>
                    {P.cats.map(c => <th key={c} className="r" style={{ minWidth: 96 }}>{c}</th>)}
                    <th className="r" style={{ minWidth: 104 }}>合计</th>
                  </tr>
                </thead>
                <tbody>
                  {P.types.map(t => (
                    <React.Fragment key={t.type}>
                      <tr style={{ background: 'var(--bg-sub)' }}>
                        <td><b>{t.type}</b><span style={{ ...qs, marginLeft: 6 }}>{t.whs.length} 个仓</span></td>
                        <td style={{ color: 'var(--ink-3)' }}>小计</td>
                        {P.cats.map(c => <td key={c} className="r"><b>{cell(t.cells[c])}</b></td>)}
                        <td className="r"><b>{cell(t.total)}</b></td>
                      </tr>
                      {P.rows.filter(r => r.type === t.type).map(r => (
                        <tr key={r.wh}>
                          {/* 仓库名可点＝去明细步看这个仓全部类别的料 */}
                          <td style={{ paddingLeft: 18 }}>
                            <span onClick={() => jumpDetail(r.wh)} title={`查看 ${r.wh} 的物料明细`}
                              style={{ cursor: 'pointer', textDecoration: 'underline dotted', textUnderlineOffset: 3 }}>
                              {r.wh}</span>
                          </td>
                          <td style={{ color: 'var(--ink-3)' }}>{r.type}</td>
                          {/* 有数的格子可点＝去明细步、仓库和类别都已填好 */}
                          {P.cats.map(c => {
                            const v = r.cells[c]
                            return <td key={c} className="r"
                              onClick={v ? () => jumpDetail(r.wh, c) : undefined}
                              title={v ? `查看明细：${r.wh} · ${c}` : ''}
                              style={{ cursor: v ? 'pointer' : 'default' }}>{cell(v)}</td>
                          })}
                          <td className="r"><b>{cell(r.total)}</b></td>
                        </tr>
                      ))}
                    </React.Fragment>
                  ))}
                  <tr className="total">
                    <td><b>总计</b></td>
                    <td style={{ color: 'var(--ink-3)' }}>{P.rows.length} 个仓</td>
                    {P.cats.map(c => <td key={c} className="r"><b>{cell(P.cat_total[c])}</b></td>)}
                    <td className="r"><b>{cell(P.total)}</b></td>
                  </tr>
                </tbody>
              </table>
            </div>
            {P.types.some(t => t.type === '（属性缺失）') && <div className="trust" style={{ color: 'var(--amber)' }}>
              ⚠ 有仓库未配置仓库类型，已归入「（属性缺失）」：
              {(P.types.find(t => t.type === '（属性缺失）') || {}).whs.join('、')}。请到基础数据补对照（不硬归）。</div>}
            <div className="trust" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
              说明：各格单独四舍五入到分，横竖相加与合计可能有 1～2 分的显示尾差；合计与总计均按未舍入值算，故总计与账实勾稽分毫一致。
            </div>
          </>)
        })()}

        {/* 第④步 事务类型透视（V2.141）：本期收入/发出按业务类型归集。
            数据来自流水级报表（取数时已在服务端聚合成 ~21 行落库）；只列发生额、不碰它的"结存"
            （那是按物料累计的滚动结存，算结存必错——结存以仓库透视/账实勾稽为准）。 */}
        {step === 'btype' && res && res.has_data && (() => {
          const B = res.btypes
          if (!B || !B.length) return (
            <div className="card" style={cardS}>
              本期没有事务类型数据。它随「一键金蝶取数」一起取回——
              {res.source === 'kingdee'
                ? '本期数据是加此功能之前取的，重新取一次数即有。'
                : '本期数据是早期由已停用的上传通道接入的（那张表里没有事务类型），要看这屏请重新一键金蝶取数。'}
            </div>
          )
          const tIa = B.reduce((s, a) => s + a.ia, 0)
          const tDa = B.reduce((s, a) => s + a.da, 0)
          const tN = B.reduce((s, a) => s + a.n, 0)
          // 与汇总表（勾稽口径）如实对比——不写死"同数"：
          // 5 期实测收入分毫一致、发出差 1.59 元（金蝶流水报表自身合计行与成员行之和的尾差，非工具算错）
          const tt2 = res.pivot_category && res.pivot_category['合计']
          const dIa = tt2 ? tIa - tt2.ia : 0
          const dDa = tt2 ? tDa - tt2.da : 0
          const tieTxt = (d) => Math.abs(d) < 0.005
            ? <b style={{ color: 'var(--green)' }}>与汇总表分毫一致</b>
            : <span style={{ color: 'var(--amber)' }}>与汇总表差 <b>{fmt(d)}</b> 元（金蝶流水报表自身尾差，非工具计算误差）</span>
          return (<>
            <div className="trust" style={{ fontSize: 12, color: 'var(--ink-2)', background: 'var(--bg-sub)' }}>
              本期 <b>{tN.toLocaleString()}</b> 笔业务按<b>事务类型</b>归集（同一本账的另一种切法）：
              收入合计 <b>{fmt(tIa)}</b>（{tieTxt(dIa)}）/ 发出合计 <b>{fmt(tDa)}</b>（{tieTxt(dDa)}）。
              负数行＝红字冲销（退货/退料），是正常业务。
            </div>
            <div className="twrap">
              <table style={{ fontSize: 12 }}>
                <thead><tr>
                  <th style={{ minWidth: 150 }}>事务类型</th>
                  <th className="r" style={{ minWidth: 66 }}>笔数</th>
                  <th className="r" style={{ minWidth: 96 }}>收入数量</th>
                  <th className="r" style={{ minWidth: 110 }}>收入金额</th>
                  <th className="r" style={{ minWidth: 82 }}>收入占比</th>
                  <th className="r" style={{ minWidth: 96 }}>发出数量</th>
                  <th className="r" style={{ minWidth: 110 }}>发出金额</th>
                  <th className="r" style={{ minWidth: 82 }}>发出占比</th>
                </tr></thead>
                <tbody>
                  {B.map(a => (
                    <tr key={a.bt} style={(a.ia < 0 || a.da < 0) ? { background: 'var(--red-bg)' } : null}>
                      <td>{a.bt}</td>
                      <td className="r">{a.n.toLocaleString()}</td>
                      <td className="r">{a.iq ? qty(a.iq) : <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                      <td className="r">{a.ia ? <b>{fmt(a.ia)}</b> : <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                      <td className="r" style={{ color: 'var(--ink-2)' }}>{a.ia && tIa ? (100 * a.ia / tIa).toFixed(1) + '%' : '—'}</td>
                      <td className="r">{a.dq ? qty(a.dq) : <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                      <td className="r">{a.da ? <b>{fmt(a.da)}</b> : <span style={{ color: 'var(--ink-3)' }}>—</span>}</td>
                      <td className="r" style={{ color: 'var(--ink-2)' }}>{a.da && tDa ? (100 * a.da / tDa).toFixed(1) + '%' : '—'}</td>
                    </tr>
                  ))}
                  <tr className="total">
                    <td><b>合计</b></td>
                    <td className="r"><b>{tN.toLocaleString()}</b></td>
                    <td className="r"><b>{qty(B.reduce((s, a) => s + a.iq, 0))}</b></td>
                    <td className="r"><b>{fmt(tIa)}</b></td>
                    <td className="r">100%</td>
                    <td className="r"><b>{qty(B.reduce((s, a) => s + a.dq, 0))}</b></td>
                    <td className="r"><b>{fmt(tDa)}</b></td>
                    <td className="r">100%</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="trust" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
              说明：数量为各物料【原单位】之和（千克/个/Pcs 混加），仅供参考；金额才是可加的。
              本屏只归集<b>发生额</b>，结存请看「仓库透视」与「三道勾稽」。
            </div>
          </>)
        })()}

        {/* 第⑤步 收发存明细（V2.130）：服务端按 仓库/类别/关键字/负结存 筛+分页 */}
        {step === 'detail' && res && res.has_data && (() => {
          const D = dData
          const page = D ? Math.floor(D.offset / D.limit) + 1 : 1
          const pages = D ? Math.max(1, Math.ceil(D.total / D.limit)) : 1
          const filtered = !!(dq.wh || dq.cat || dq.q || dq.neg)
          return (<>
            <div className="card" style={cardS}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span className="selctl"><span className="k">仓库</span>
                  <select value={dq.wh} onChange={e => setF({ wh: e.target.value })} style={sel}>
                    <option value="">全部仓库</option>
                    {(D ? D.whs : []).map(w => <option key={w} value={w}>{w}</option>)}
                  </select>
                </span>
                <span className="selctl"><span className="k">存货类别</span>
                  <select value={dq.cat} onChange={e => setF({ cat: e.target.value })} style={sel}>
                    <option value="">全部类别</option>
                    {(D ? D.cats : []).map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </span>
                <input value={dq.q} onChange={e => setF({ q: e.target.value })}
                  placeholder="物料名称 / 编码 关键字"
                  style={{ padding: '5px 9px', borderRadius: 7, border: '1px solid var(--line-strong)',
                    fontSize: 12.5, width: 190, fontFamily: 'inherit' }} />
                <label style={{ fontSize: 12.5, display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                  <input type="checkbox" checked={!!dq.neg} onChange={e => setF({ neg: e.target.checked ? 1 : 0 })} />
                  只看负结存
                </label>
                {filtered && <span className="chip" onClick={() => setF({ wh: '', cat: '', q: '', neg: 0 })}>清空筛选</span>}
                {D && <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-2)' }}>
                  {D.total} 种物料 · 结存 <b>{fmt(D.sum.ea)}</b> 元 / {qty(D.sum.eq)}
                </span>}
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 6 }}>
                合计按<b>筛出来的全部行</b>算，不随翻页变；口径与仓库透视同源，故「某仓 × 某类别」的合计与透视格子里的数分毫一致。
                按结存金额绝对值降序——负结存排前面、不沉底。
                <b>单价取自金蝶（加权平均结转价），不是金额÷数量算的</b>——显示 2 位，导出的 Excel 里存的是金蝶原值 6 位；
                数量为 0 时金蝶本就没有单价，显示「—」。
              </div>
            </div>

            {dBusy && <div className="card" style={cardS}>读取明细中…</div>}
            {!dBusy && D && (D.rows.length === 0
              ? <div className="card" style={cardS}>没有符合条件的物料。{filtered && '试试放宽筛选。'}</div>
              : <>
                <div className="twrap">
                  <table style={{ fontSize: 12 }}>
                    {/* 17 列＝业务方底稿口径。三段(期初/收入/发出/结存)各 数量·单价·金额 并排，
                        用细分隔线分段，否则 12 个数字列糊成一片认不出哪段是哪段。 */}
                    <thead>
                      <tr>
                        <th colSpan={8} style={{ borderRight: '2px solid var(--line-strong)' }}>物料</th>
                        <th colSpan={3} className="r" style={{ borderRight: '1px solid var(--line)' }}>期初</th>
                        <th colSpan={3} className="r" style={{ borderRight: '1px solid var(--line)' }}>收入</th>
                        <th colSpan={3} className="r" style={{ borderRight: '1px solid var(--line)' }}>发出</th>
                        <th colSpan={3} className="r">结存</th>
                      </tr>
                      <tr>
                        <th style={{ minWidth: 92 }}>物料编码</th><th style={{ minWidth: 170 }}>物料名称</th>
                        <th style={{ minWidth: 104 }}>规格型号</th><th style={{ minWidth: 84 }}>存货类别</th>
                        <th style={{ minWidth: 92 }}>物料分组</th><th style={{ minWidth: 112 }}>仓库</th>
                        <th style={{ minWidth: 74 }}>批号</th>
                        <th style={{ minWidth: 56, borderRight: '2px solid var(--line-strong)' }}>单位</th>
                        <th className="r" style={{ minWidth: 78 }}>数量</th>
                        <th className="r" style={{ minWidth: 74 }}>单价</th>
                        <th className="r" style={{ minWidth: 88, borderRight: '1px solid var(--line)' }}>金额</th>
                        <th className="r" style={{ minWidth: 78 }}>数量</th>
                        <th className="r" style={{ minWidth: 74 }}>单价</th>
                        <th className="r" style={{ minWidth: 88, borderRight: '1px solid var(--line)' }}>金额</th>
                        <th className="r" style={{ minWidth: 78 }}>数量</th>
                        <th className="r" style={{ minWidth: 74 }}>单价</th>
                        <th className="r" style={{ minWidth: 88, borderRight: '1px solid var(--line)' }}>金额</th>
                        <th className="r" style={{ minWidth: 78 }}>数量</th>
                        <th className="r" style={{ minWidth: 74 }}>单价</th>
                        <th className="r" style={{ minWidth: 92 }}>金额</th>
                      </tr>
                    </thead>
                    <tbody>
                      {D.rows.map((r, i) => {
                        const neg = r.eq < 0
                        const sep = { borderRight: '1px solid var(--line)' }
                        return (
                          // 负结存标红：这是要跟仓管对的第一批料
                          <tr key={i} style={neg ? { background: 'var(--red-bg)' } : null}>
                            <td>{r.code || '—'}</td><td>{r.name}</td>
                            <td style={{ color: 'var(--ink-3)' }}>{r.spec || '—'}</td>
                            <td style={{ color: 'var(--ink-3)' }}>{r.cat || '—'}</td>
                            <td style={{ color: 'var(--ink-3)' }}>{r.grp || '—'}</td>
                            <td>{r.wh || '—'}</td>
                            <td style={{ color: 'var(--ink-3)' }}>{r.batch || '—'}</td>
                            {/* 单位＝数量所用的那个单位（报表口径，99.7% 即物料档案的基本单位）；
                                紧挨着后面的数量列，看数时不用横跨半张表去找它是千克还是个 */}
                            <td style={{ borderRight: '2px solid var(--line-strong)' }}>{r.unit || '—'}</td>
                            <td className="r">{qty(r.oq)}</td>
                            <td className="r" style={{ color: 'var(--ink-2)' }}>{price(r.op)}</td>
                            <td className="r" style={sep}>{fmt(r.oa)}</td>
                            <td className="r">{qty(r.iq)}</td>
                            <td className="r" style={{ color: 'var(--ink-2)' }}>{price(r.ip)}</td>
                            <td className="r" style={sep}>{fmt(r.ia)}</td>
                            <td className="r">{qty(r.dq)}</td>
                            <td className="r" style={{ color: 'var(--ink-2)' }}>{price(r.dp)}</td>
                            <td className="r" style={sep}>{fmt(r.da)}</td>
                            <td className="r" style={neg ? { color: 'var(--red)', fontWeight: 600 } : null}>{qty(r.eq)}</td>
                            <td className="r" style={{ color: 'var(--ink-2)' }}>{price(r.ep)}</td>
                            <td className="r"><b>{fmt(r.ea)}</b></td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                {pages > 1 && <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'center', fontSize: 12.5 }}>
                  <span className="chip" style={D.offset <= 0 ? { opacity: .4, cursor: 'not-allowed' } : null}
                    onClick={() => D.offset > 0 && setDq(d => ({ ...d, offset: Math.max(0, d.offset - D.limit) }))}>‹ 上一页</span>
                  <span style={{ color: 'var(--ink-2)' }}>第 {page} / {pages} 页（每页 {D.limit} 种）</span>
                  <span className="chip" style={page >= pages ? { opacity: .4, cursor: 'not-allowed' } : null}
                    onClick={() => page < pages && setDq(d => ({ ...d, offset: d.offset + D.limit }))}>下一页 ›</span>
                </div>}
              </>)}
          </>)
        })()}

        {/* 第⑥步 异常稽核 */}
        {step === 'anomaly' && res && res.has_data && an && (<>
          <div className="chips">
            {['全部', '负结存', '挂账尾差', '对照缺失', '成本调整提示'].map(s => {
              const n = s === '全部' ? an.items.length : (an.counts[s] || 0)
              return <span key={s} className={'chip' + (slice === s ? ' on' : '')} onClick={() => setSlice(s)}>{s} <span className="c-n">{n}</span></span>
            })}
            <span className="chip">正常（不打扰）<span className="c-n">{(an.counts['正常'] || 0).toLocaleString()}</span></span>
          </div>
          <div className="twrap">
            <table>
              <thead><tr><th>状态</th><th>物料编码</th><th>物料名称</th><th>仓库</th><th className="r">结存数量</th><th className="r">结存金额</th><th>说明</th></tr></thead>
              <tbody>
                {shownItems.length === 0 && <tr><td colSpan={7} style={{ color: 'var(--ink-3)', padding: 16 }}>该类别下暂无异常</td></tr>}
                {shownItems.map((it, i) => (
                  <tr key={i} style={it.status === '负结存' ? { background: 'var(--red-bg)' } : undefined}>
                    <td><span className={'pill ' + (it.status === '负结存' ? 'bad' : it.status === '成本调整提示' ? 'info' : 'warn')}>{it.status}</span></td>
                    <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{it.code || '—'}</td>
                    <td>{it.name}</td><td>{it.wh}</td>
                    <td className="r">{Number(it.eq).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
                    <td className="r">{Number(it.ea).toLocaleString('en-US', { maximumFractionDigits: 4 })}</td>
                    <td style={{ fontSize: 12, color: 'var(--ink-2)' }}>{it.note}{it.mirror ? `（镜像：${it.mirror.wh} ${it.mirror.ea}）` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="foot" style={{ fontSize: 11, color: 'var(--ink-3)' }}>护栏：Σ各态 {anTotal.toLocaleString()} = 明细总行数 {res.rows_cross.toLocaleString()}，不重不漏。成本调整负数行如实列示、不算异常。</div>
        </>)}

        {/* 第⑦步 损益归集 */}
        {/* 类别漂移（V2.282）：与上期档案比。**只报不判**——档案归正多半是对的，
            工具的职责是让它当月被看见，而不是替业务方判定对错。
            ⚠只对"上期已落库"的情况有效：金蝶报表按当前档案追溯归集，
            若两期都是同一天取的，天然无差异——这个机制**从落库那一刻起才生效**。 */}
        {step === 'anomaly' && res && res.has_data && res.drift && res.drift.n > 0 && (
          <div className="card" style={{ ...cardS, borderColor: 'var(--amber-line)', background: 'var(--amber-bg)' }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              ⚑ 类别漂移：{res.drift.n} 个物料的存货类别/物料分组与{res.drift.prev}不同
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
              涉及本期结存 <b>{fmt(res.drift.amount)}</b> 元。金蝶报表按<b>当前</b>物料档案归集类别，
              改档案会<b>追溯改变历史月份的报表</b>；而总账凭证记的是当时的科目、不会追溯变——
              两边就此错开，账实勾稽会出现差异。<b>这里只报不判</b>：归正档案本身多半是对的。
            </div>
            <div className="twrap">
              <table>
                <thead><tr><th>物料编码</th><th>物料名称</th><th>原类别</th><th>现类别</th>
                  <th>原分组</th><th>现分组</th><th className="r">本期结存</th></tr></thead>
                <tbody>
                  {res.drift.items.slice(0, 50).map(x => (
                    <tr key={x.code}>
                      <td>{x.code}</td><td>{x.name}</td>
                      <td style={{ color: x.cat_changed ? 'var(--amber)' : undefined }}>{x.old_cat || '—'}</td>
                      <td style={{ color: x.cat_changed ? 'var(--amber)' : undefined, fontWeight: x.cat_changed ? 600 : 400 }}>{x.new_cat || '—'}</td>
                      <td style={{ color: x.grp_changed ? 'var(--amber)' : undefined }}>{x.old_grp || '—'}</td>
                      <td style={{ color: x.grp_changed ? 'var(--amber)' : undefined }}>{x.new_grp || '—'}</td>
                      <td className="r">{fmt(x.ea)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {res.drift.items.length > 50 && <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>
              仅显示前 50 个，完整清单见导出的《类别漂移》表。</div>}
          </div>
        )}

        {step === 'pnl' && res && res.has_data && (
          res.pnl ? <div className="twrap">
            <table>
              <thead><tr><th>归属</th><th>类别 / 项目</th><th className="r">金额（元）</th></tr></thead>
              <tbody>
                {Object.entries(res.pnl.loss.by_cat).map(([c, v]) => (
                  <tr key={c}><td>管理费用 · 货损</td><td>{c}</td><td className="r">{fmt(v)}</td></tr>
                ))}
                <tr className="total"><td><b>货损合计</b></td><td>→ 管理费用</td><td className="r"><b>{fmt(res.pnl.loss.total)}</b></td></tr>
                <tr><td>营业外支出（6711）</td><td>固定资产处置</td><td className="r">{fmt(res.pnl.disposal.total)}</td></tr>
                {/* 第三档（V2.312）：既非货损也非处置的存货出损益——福利领用、捐赠等。
                    ⚠**这块以前只在导出里有、页面上没有**：V2.307 拆出这一档时只落进了 _raw，
                    res.pnl 仍是两档，于是 101 星期零在这一页看着是"啥都没有"
                    （它货损和处置恰好都是 0），业务方原话「福利领用还是不出来」。 */}
                {res.pnl.other && res.pnl.other.total !== 0 && <>
                  {Object.entries(res.pnl.other.by_item).map(([c, v]) => (
                    <tr key={'o' + c}><td>其他存货出库（非货损）</td><td>{c}</td><td className="r">{fmt(v)}</td></tr>
                  ))}
                  <tr className="total"><td><b>其他合计</b></td>
                    <td>{Object.keys(res.pnl.other.by_acct || {}).join(' / ') || '—'}</td>
                    <td className="r"><b>{fmt(res.pnl.other.total)}</b></td></tr>
                </>}
                {/* 口径外（V2.314）：6602 管理费用里非货损的（福利领用等）。Owner 定案不算口径内。
                    ⚠**列出来但不计入任何合计**——静悄悄扔掉的话，哪天跟手工表对不上就没人查得到它去哪了。 */}
                {res.pnl.excluded && res.pnl.excluded.total !== 0 && Object.entries(res.pnl.excluded.by_item).map(([c, v]) => (
                  <tr key={'x' + c} style={{ color: 'var(--ink-3)' }}>
                    <td>口径外 · 未计入</td><td>{c}（6602 管理费用）</td><td className="r">{fmt(v)}</td></tr>
                ))}
              </tbody>
            </table>
            {res.pnl.excluded && res.pnl.excluded.total !== 0 && <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.7, marginTop: 8 }}>
              「口径外」＝6602 管理费用里既非货损也非盘盈亏的分录（{res.pnl.excluded.n} 笔，合计 {fmt(res.pnl.excluded.total)}）。
              按口径<b>不计入本页任何合计、也不进明细页</b>，此处列出仅为留痕——避免与手工表核对时找不到它的去向。
            </div>}
            {res.pnl.other && res.pnl.other.total !== 0 && <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginTop: 8 }}>
              「其他存货出库」判据＝凭证的<b>核算维度·费用项目</b>，不含「货损 / 盘盈亏 / 处置」字样的归此档。
              <b>不计入货损与处置合计</b>，但它们仍是本期从存货流向损益的金额，故单列。
              物料级明细见导出的「原始·货损与处置明细」页，归属列写「其他存货出库（非货损）」。
            </div>}
          </div> : <div className="card" style={cardS}>本期未取到损益归集数据，请先在第①步取数。</div>
        )}

        {/* 第⑧步 制造费用（V2.257）——成本三道勾稽 + 车间 × 成本项目。
            **刻意不与存货那三道同屏**：两边是不同的账（存货科目 vs 成本科目），
            混在一起看容易串；也不并进 credible，一边不平不该把另一边判为不可信。 */}
        {step === 'mfg' && res && res.has_data && (
          res.cost ? <>
            <div className="card" style={cardS}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>⑧ 制造费用 · 三道成本勾稽</div>
              <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
                数据来自金蝶<b>成本计算单</b>（本期 {res.cost.n.toLocaleString()} 行明细，工具已聚合）。
                三条等式均由真数据实证，容差 0.5 元（树形报表各层独立四舍五入）。
              </div>
              <div className="twrap">
                <table>
                  <thead><tr><th>勾稽</th><th>总账侧</th><th className="r">金额</th>
                    <th>业务侧</th><th className="r">金额</th><th className="r">差异</th><th>结论</th></tr></thead>
                  <tbody>
                    {[['mfg_collect', '① 制造费用归集', '5101 制造费用 借方', '成本计算单：制造费用＋间接材料'],
                      ['complete', '② 完工结转', '5001 生产成本 贷方', '流水表：汇报入库＋生产退库'],
                      ['wip_input', '③ 投入归集', '5001 生产成本 借方', '本期投入（剔委外）＋期末在产品成本调整']]
                      .map(([k, name, bookSide, bizSide]) => {
                        const x = res.cost.ties[k]
                        if (!x) return <tr key={k}><td>{name}</td><td colSpan={6} style={{ color: 'var(--ink-3)' }}>本期未取到，该道跳过</td></tr>
                        return (
                          <tr key={k}>
                            <td>{name}</td><td style={{ fontSize: 12 }}>{bookSide}</td><td className="r">{fmt(x.book)}</td>
                            <td style={{ fontSize: 12 }}>{bizSide}</td><td className="r">{fmt(x.biz)}</td>
                            <td className="r" style={{ color: x.pass ? 'var(--green)' : 'var(--red)' }}>{fmt(x.diff)}</td>
                            <td style={{ color: x.pass ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>{x.pass ? '✓ 通过' : '✗ 不平'}</td>
                          </tr>
                        )
                      })}
                  </tbody>
                </table>
              </div>
              {res.cost.ties.complete && res.cost.ties.complete.parts && (
                <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 8 }}>
                  ②的业务侧明细：{Object.entries(res.cost.ties.complete.parts).map(([k, v]) => `${k} ${fmt(v)}`).join('　')}
                  {'　'}——「生产退库」为负、常被漏算：没有这类单据的月份等式会碰巧成立，有的月份才露馅。
                </div>
              )}
            </div>

            <div className="card" style={cardS}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>车间 × 成本项目</div>
              <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
                本期投入金额。<b>委外单独一行</b>——委外订单不走生产成本科目，并进车间会让合计对不上总额。
              </div>
              <div className="twrap">
                <table>
                  <thead><tr><th>车间（成本中心）</th>
                    {res.cost.pivot_cc.items.map(i => <th key={i} className="r">{i}</th>)}
                    <th className="r">合计</th></tr></thead>
                  <tbody>
                    {res.cost.pivot_cc.rows.map(r => (
                      <tr key={r.cc}>
                        <td>{r.cc}</td>
                        {res.cost.pivot_cc.items.map(i => <td key={i} className="r">{r.cells[i] ? fmt(r.cells[i]) : '—'}</td>)}
                        <td className="r"><b>{fmt(r.total)}</b></td>
                      </tr>
                    ))}
                    {res.cost.pivot_cc.outsourced.total !== 0 && (
                      <tr style={{ color: 'var(--ink-2)' }}>
                        <td>委外（不走生产成本）</td>
                        {res.cost.pivot_cc.items.map(i => <td key={i} className="r">{res.cost.pivot_cc.outsourced.cells[i] ? fmt(res.cost.pivot_cc.outsourced.cells[i]) : '—'}</td>)}
                        <td className="r"><b>{fmt(res.cost.pivot_cc.outsourced.total)}</b></td>
                      </tr>
                    )}
                    <tr className="total">
                      <td><b>总计</b></td>
                      {res.cost.pivot_cc.items.map(i => <td key={i} className="r"><b>{fmt(res.cost.pivot_cc.item_total[i])}</b></td>)}
                      <td className="r"><b>{fmt(res.cost.pivot_cc.total)}</b></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div className="card" style={cardS}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>费用项目构成</div>
              <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
                本期投入按子项费用项目归集，从大到小。<b>这是唯一能看到"产品成本里到底装了什么"的地方</b>。
              </div>
              <div className="twrap">
                <table>
                  <thead><tr><th>费用项目</th><th className="r">金额（元）</th><th className="r">占比</th></tr></thead>
                  <tbody>
                    {(() => {
                      const tot = res.cost.expenses.reduce((a, x) => a + x.amount, 0)
                      return res.cost.expenses.map(x => (
                        <tr key={x.exp}>
                          <td>{x.exp}</td><td className="r">{fmt(x.amount)}</td>
                          <td className="r">{tot ? (x.amount / tot * 100).toFixed(1) + '%' : '—'}</td>
                        </tr>
                      ))
                    })()}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 成本计算单【原表】补件（V2.288）。
                接口只到「工单 × 成本项目 × 子项费用项目」；界面导出多 6 列——来源单据类型/编号/行号、
                费用分配标准、分配标准值、分配标准值总量、作业活动、来源/承担组织，
                合起来是**制造费用怎么摊到工单上的证据链**。2026-08 实测：这些字段名在
                FSHOWWAY ''/0/1/2/3/4 × FSumGist ''/0/1/2/3 × 各明细开关的所有组合下都报"字段不存在"。
                金额两边对得上（107/2026-3：3,900,207.56 分毫不差），差的只是粒度，
                **所以做成可选补件：不传照常出台账，勾稽结论不受影响**。 */}
          </> : <div className="card" style={cardS}>
            本期没有取到成本计算单，制造费用勾稽跳过。
            {res.cc_msg && <div style={{ color: 'var(--amber)', marginTop: 6 }}>⚠ {res.cc_msg}</div>}
          </div>
        )}

        {/* 第⑧步 台账出具＝导出 + 封存（V2.131：封存从「本期状态」条挪到这里，作为月结收尾） */}
        {step === 'export' && res && res.has_data && (<>
          <div className="card" style={cardS}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>⑨ 导出《成本台账》</div>
            <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
              {/* V2.290：页数不再逐张数——同类已合并成可折叠的一页，逐张列反而看不出结构。
                  按「结论 / 原始底表」两段说，与文件里的页签配色一一对应。 */}
              <b>结论段</b>（页签蓝）：核对结论（可信度报告）/ <b>勾稽与归集</b>（账实·{res.cost ? '成本·' : ''}{res.pnl ? '损益·' : ''}三块可折叠）
              / <b>汇总透视</b>（按类别·按仓库{res.btypes ? '·按事务类型' : ''}{res.cost ? '·车间×成本项目·费用项目构成' : ''}，各块可折叠、注明来源）
              / 异常清单 / 收发存明细（{res.rows_cross.toLocaleString()} 行）。
              <div style={{ marginTop: 4 }}>
                <b>原始底表段</b>（页签灰＝金蝶接口取回的原样底表）：原始·收发存跨维度 / 原始·收发存按日期
                {res.cost ? ' / 原始·制造费用明细(接口)' : ''}
                {res.pnl ? ' / 原始·货损与处置明细' : ''}。每张都带金蝶元数据头与取数留痕。
              </div>
              {/* V2.291：汇总不再是"工具算好的数"，而是引用灰表的活公式——业务方定的口径 */}
              <div style={{ marginTop: 4 }}>
                <b>汇总透视各块是引用原始底表的 SUMIFS 公式</b>，不是算好摆上去的值——
                点开任一格就能看见完整口径，改了底表汇总立刻跟着变。
                <span style={{ color: 'var(--ink-3)' }}>（「按事务类型」没有对应底表页，只能是值。）</span>
              </div>
              {!res.pnl && <span style={{ color: 'var(--ink-3)' }}>（本期无货损/盘盈亏，损益归集块不出）</span>}
              <div style={{ marginTop: 4, color: 'var(--ink-3)' }}>
                导出的是<b>页面上这一期</b>：{res.org_name || org} · {year} 年第 {period} 期。
              </div>
            </div>
            {/* 链接必须带 年/期/主体——不带的话后端按默认期取，导出的会是另一期的账（曾经的坑） */}
            {canExport
              ? <a className="btn-pri" href={`/api/cost-ledger/export?year=${year}&period=${period}&org=${org}`}>
                下载成本台账 Excel</a>
              : <span className="btn-pri" title={`${(res.missing_wh || []).length} 个仓库没配仓库类型，补齐后才能导出`}
                style={{ opacity: .5, cursor: 'not-allowed' }}>下载成本台账 Excel（待补仓库类型）</span>}
          </div>

          {/* 封存本期：月结收尾。已封存时本区不出（顶部另有封存状态条与解封入口） */}
          {!closed && <div className="card" style={cardS}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>封存本期</div>
            <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
              封存＝这个月的存货核对做完了，把结果<b>拍照存死</b>。之后本期只读：不再取金蝶、不能重传，
              谁进来看到的都是封存那一刻这份数据——审计问"你当时凭什么这么算"，调得出当时那张表。
              <b>每个主体自己一条封存线</b>——封 {res.org_name} 不影响别的主体，也与银行对账互不影响。
              {!res.credible && <span style={{ color: 'var(--amber)' }}> 本期三道勾稽<b>未全过</b>，需填理由强制封存。</span>}
            </div>
            {missWh.length > 0
              ? <span className="btn-pri" style={{ opacity: .5, cursor: 'not-allowed' }}
                title={`${missWh.length} 个仓库没配仓库类型，补齐后才能封存`}>封存本期（待补仓库类型）</span>
              : !can.close
                ? <span className="btn-pri" style={{ opacity: .5, cursor: 'not-allowed' }}
                  title="需「成本台账·封存本期」权限">封存本期（无权限）</span>
                : !sealing
                  ? <button className="btn-pri" onClick={() => setSealing(true)}>封存本期…</button>
                  : <div style={{ display: 'flex', gap: 8 }}>
                    <input placeholder={res.credible ? '封存说明（选填）' : '强制封存理由（至少 5 个字，必填）'}
                      value={note} onChange={e => setNote(e.target.value)}
                      style={{ flex: 1, maxWidth: 420, padding: '0 11px', height: 32, borderRadius: 8,
                        border: '1px solid var(--line-strong)', fontSize: 12.5, fontFamily: 'inherit' }} />
                    <button className="btn-pri" disabled={busy || (!res.credible && note.trim().length < 5)}
                      onClick={() => doSeal(!res.credible)}>{res.credible ? '确认封存' : '强制封存'}</button>
                    <button className="btn-sec" onClick={() => { setSealing(false); setNote('') }}>取消</button>
                  </div>}
          </div>}
        </>)}
      </div>
    </>
  )
}

const sel = { marginLeft: 4, padding: '3px 6px', borderRadius: 6, border: '0.5px solid var(--line-strong)', background: '#fff' }
// 期间状态胶囊：已封存=强调色 / 数据已接入=绿 / 未接入=灰
const pillOf = (r) => r.closed
  ? { color: 'var(--accent)', background: 'var(--bg-sub)', border: '1px solid var(--line-strong)' }
  : r.has_data
    ? { color: 'var(--green)', background: 'var(--green-bg)', border: '1px solid var(--green-line)' }
    : { color: 'var(--ink-3)', background: 'var(--bg-sub)', border: '1px solid var(--line)' }
// 仓库透视：每格下行的结存数量（弱化，与上行金额区分）
const qs = { fontSize: 11, color: 'var(--ink-3)', fontWeight: 400 }
const noPerm = { opacity: .4, cursor: 'not-allowed' }
const cardS = { border: '1px solid var(--line)', borderRadius: 9, background: '#fff', padding: '16px 18px' }
