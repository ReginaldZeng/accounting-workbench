// [Change Log] Date:2026-08-13 Author:Claude/c Version:V2.277(店铺管理名称列) / V2.251(首版) V2.253(令牌) V2.254(汇率录入同款骨架)
// 「电商对账 › 基础资料」页（条目⑤一期）。V2.254 与收款核销页同批改版式：
// 页头 title+sub、保存按钮右上角、页签分区（店铺对照 / 费目科目映射 / 识别与剔除规则）、内容铺满自适应。
// 权限：查看=进得来即可；维护=ec_base_edit（敏感点，默认不给）。整表保存、改动留痕。
import React, { useEffect, useState } from 'react'
import { getEcBasicdata, saveEcBasicdata } from '../api.js'
import { requestJson, post, money, count } from './ecomWorkbenchApi.js'

const FLOW_BUCKETS = { fee:'平台费用',adjustment:'补贴 / 调整',ufirst_fee:'U先专属费用',qr:'收钱码收款',transfer:'内部划转候选',recharge:'充值 / 划转候选',other:'其他已知费目' }

const EbStyle = () => <style>{`
.eb-wrap{padding:18px 24px 40px}
.eb-wrap .head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap}
.eb-wrap .h-title{font-size:17px;font-weight:600}
.eb-wrap .h-sub{font-size:12px;color:var(--ink-2);margin-top:3px}
.eb-tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-top:10px}
.eb-tab{padding:11px 15px;font-size:13px;font-weight:600;color:var(--ink-3);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.eb-tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.eb-tab:hover{color:var(--ink-2)}
.eb-body{padding-top:14px}
.eb-card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:16px 20px}
.eb-tblwrap{border:1px solid var(--line);border-radius:9px;overflow:auto}
.eb-wrap table{border-collapse:collapse;font-size:12.5px;width:100%}
.eb-wrap thead th{padding:9px 10px;font-weight:600;white-space:nowrap;text-align:left;color:var(--ink-2);background:var(--bg-sub);border-bottom:1px solid var(--line)}
.eb-wrap tbody td{padding:8px 10px;white-space:nowrap;border-top:1px solid var(--line)}
.eb-mono{font-family:var(--font-mono,ui-monospace,monospace);font-size:11.5px}
.eb-hint{font-size:12px;color:var(--ink-2)}
.eb-pill{font-size:11px;padding:1px 7px;border-radius:999px;background:var(--amber-bg,#f8f0e0);color:var(--amber,#a35a00);margin-left:8px}
.eb-rule-input,.eb-rule-select{width:100%;min-width:90px;box-sizing:border-box;border:1px solid var(--line);border-radius:6px;padding:6px 8px;background:var(--bg);color:var(--ink);font:inherit}
.eb-rule-actions{display:flex;gap:7px}.eb-preview{margin-top:14px;padding:14px;border:1px solid var(--line);border-radius:9px;background:var(--bg-sub)}
.eb-preview-summary{display:flex;gap:22px;flex-wrap:wrap;margin-bottom:12px}.eb-preview-summary strong{font-size:16px}
.eb-shop-cell strong,.eb-shop-cell small{display:block}.eb-shop-cell small{margin-top:3px;color:var(--ink-3);font-size:10.5px;font-weight:400}
`}</style>

export default function EcomBasicData({ user }) {
  const canEdit = !!(user && (user.role === 'admin' || (user.perms || {}).ec_base_edit))
  const [tab, setTab] = useState('shop')       // shop / fee / voucher / rules
  const [shopMap, setShopMap] = useState([])
  const [feeMap, setFeeMap] = useState([])
  const [rules, setRules] = useState({})
  const [recognitionRules, setRecognitionRules] = useState({})
  const [flowRules, setFlowRules] = useState([])
  const [vcfg, setVcfg] = useState({})
  const [preview, setPreview] = useState(null)
  const [working, setWorking] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => getEcBasicdata().then(r => {
    setShopMap(r.shop_map || []); setFeeMap(r.fee_map || []); setRules(r.rules || {}); setRecognitionRules(r.recognition_rules || {}); setFlowRules(r.flow_rules || [])
    setVcfg(r.voucher_cfg || {}); setDirty(false)
  }).catch(e => setMsg(String(e.message || e)))
  useEffect(() => { load() }, [])

  const save = async () => {
    try {
      setMsg('保存中…')
      await saveEcBasicdata({ shop_map: shopMap, fee_map: feeMap, rules, recognition_rules: recognitionRules, flow_rules: flowRules, voucher_cfg: vcfg })
      setMsg('已保存'); load()
    } catch (e) { setMsg('保存失败：' + String(e.message || e)) }
  }

  const editCell = canEdit ? { cursor: 'pointer' } : {}
  const editRow = (rows, setRows, i, field, label) => {
    if (!canEdit) return
    const v = window.prompt(label, rows[i][field] || '')
    if (v === null) return
    const next = rows.slice(); next[i] = { ...next[i], [field]: v.trim() }; setRows(next); setDirty(true)
  }
  const delRow = (rows, setRows, i) => {
    if (!canEdit || !window.confirm('删除这一行？（保存后生效）')) return
    setRows(rows.filter((_, j) => j !== i)); setDirty(true)
  }
  const addShop = () => {
    const kd = window.prompt('金蝶客户名（与应收单客户一致）'); if (!kd) return
    const wdt = window.prompt('旺店通店铺名', kd); if (wdt === null) return
    const plat = window.prompt('平台（天猫/淘宝/抖音/小红书/线下…）', '天猫'); if (plat === null) return
    setShopMap([...shopMap, { kd_name: kd.trim(), wdt_name: wdt.trim(), platform: plat.trim() }]); setDirty(true)
  }
  const addFee = () => {
    const code = window.prompt('费目码（支付宝业务描述竖线前缀，如 0030003）'); if (!code) return
    const label = window.prompt('费目名', ''); if (label === null) return
    const account = window.prompt('记账科目（费用/应收账款/其他货币资金…）', '费用'); if (account === null) return
    setFeeMap([...feeMap, { code: code.trim(), label: label.trim(), account: account.trim() }]); setDirty(true)
  }
  const updateFlowRule = (i, values) => { const next=flowRules.slice();next[i]={...next[i],...values};setFlowRules(next);setPreview(null);setDirty(true) }
  const addFlowRule = () => { setFlowRules([...flowRules,{id:`flow_${Date.now()}`,name:'新分类规则',enabled:true,account_kind:'alipay',field:'remark',keywords:['关键词'],direction:'outgo',bucket:'fee',label:'平台费用'}]);setDirty(true);setPreview(null) }
  const previewRule = async id => {
    setWorking(true);setMsg('正在预览命中结果…')
    try { const r=await requestJson('/api/ec/flows/rules/preview',post({id}));setPreview(r);setMsg('') }
    catch(e){setMsg('预览失败：'+e.message)}finally{setWorking(false)}
  }
  const applyRule = async () => {
    if (!preview?.matched || !window.confirm(`确认将 ${preview.matched} 笔流水归为“${preview.rule.label}”？原始字段不会修改。`)) return
    setWorking(true);setMsg('正在批量归类…')
    try { const r=await requestJson('/api/ec/flows/rules/apply',post({id:preview.rule.id,expected_count:preview.matched,confirmed:true}));setMsg(`已批量归类 ${r.applied} 笔，跳过人工定性 ${r.skipped_reviewed} 笔、规则冲突 ${r.skipped_conflicts} 笔；未写金蝶。`);setPreview({...preview,applied:r.applied}) }
    catch(e){setMsg('批量处理失败：'+e.message)}finally{setWorking(false)}
  }
  const addBtn = onClick => <button className="btn-sec" style={{ marginLeft: 'auto', padding: '5px 13px', borderRadius: 8, fontSize: 12.5, cursor: 'pointer' }} onClick={onClick}>＋ 新增</button>

  return <div className="eb-wrap">
    <EbStyle />
    <div className="head">
      <div>
        <div className="h-title">基础资料</div>
        <div className="h-sub">电商对账全线取数口径的受控配置——改这里影响每一次跑批{canEdit ? '' : '（当前只读，维护需「维护基础资料」权限）'}</div>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {msg && <span style={{ fontSize: 12.5, color: msg.includes('失败') ? 'var(--red,#c0392b)' : 'var(--green,#1f7a55)' }}>{msg}</span>}
        {canEdit && <button className={dirty ? 'btn-primary' : 'btn-sec'} style={{ padding: '7px 18px', borderRadius: 8, fontSize: 12.5, cursor: dirty ? 'pointer' : 'default' }}
          onClick={save} disabled={!dirty}>{dirty ? '保存全部改动' : '无改动'}</button>}
      </div>
    </div>

    <div className="eb-tabs">
      <div className={'eb-tab' + (tab === 'shop' ? ' on' : '')} onClick={() => setTab('shop')}>店铺对照</div>
      <div className={'eb-tab' + (tab === 'recognition' ? ' on' : '')} onClick={() => setTab('recognition')}>收入确认口径</div>
      <div className={'eb-tab' + (tab === 'fee' ? ' on' : '')} onClick={() => setTab('fee')}>费目科目映射</div>
      <div className={'eb-tab' + (tab === 'voucher' ? ' on' : '')} onClick={() => setTab('voucher')}>凭证配置</div>
      <div className={'eb-tab' + (tab === 'rules' ? ' on' : '')} onClick={() => setTab('rules')}>识别与剔除规则</div>
      <div className={'eb-tab' + (tab === 'flowrules' ? ' on' : '')} onClick={() => setTab('flowrules')}>流水分类规则</div>
    </div>

    <div className="eb-body">
      {tab === 'shop' && <div className="eb-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <span className="eb-hint">金蝶客户名 ↔ 旺店通店铺名。任一侧出现<b>不在表内的新店铺 → 跑批报警</b>，不静默过滤（确认书① D8/D9）。</span>
          {canEdit && addBtn(addShop)}
        </div>
        <div className="eb-tblwrap">
          <table>
            <thead><tr><th>管理名称（显示用简称）</th><th>金蝶客户名</th><th>旺店通店铺名</th><th>平台</th><th>支付宝账号（自动认流水包文件）</th>{canEdit && <th style={{ width: 36 }}></th>}</tr></thead>
            <tbody>{shopMap.map((r, i) => <tr key={i}>
              {/* V2.277 管理名称：只做显示层（收款核销各处以此称呼店铺）；数据键仍是旺店通店铺名，改名不动历史 */}
              <td style={editCell} onClick={() => editRow(shopMap, setShopMap, i, 'mgmt_name', '管理名称（显示用简称，留空=用旺店通店铺名）')}>
                {(r.mgmt_name || '').trim() ? <b>{r.mgmt_name}</b> : <span style={{ color: 'var(--ink-3)' }}>未起（显示旺店通店铺名）</span>}</td>
              <td style={editCell} onClick={() => editRow(shopMap, setShopMap, i, 'kd_name', '金蝶客户名')}>{r.kd_name}</td>
              <td style={editCell} onClick={() => editRow(shopMap, setShopMap, i, 'wdt_name', '旺店通店铺名')}>
                {r.wdt_name}{r.kd_name !== r.wdt_name && <span className="eb-pill">两侧名称不同</span>}</td>
              <td style={{ ...editCell, color: 'var(--ink-2)' }} onClick={() => editRow(shopMap, setShopMap, i, 'platform', '平台')}>{r.platform}</td>
              <td className="eb-mono" style={editCell} onClick={() => editRow(shopMap, setShopMap, i, 'alipay_acct', '支付宝账号（2088 开头，银行对账流水包文件名里的账号）')}>
                {r.alipay_acct || <span style={{ color: 'var(--ink-3)' }}>未配（收款核销认不到该店流水）</span>}</td>
              {canEdit && <td><span style={{ cursor: 'pointer', color: 'var(--red,#c0392b)', fontSize: 12 }} onClick={() => delRow(shopMap, setShopMap, i)}>删</span></td>}
            </tr>)}</tbody>
          </table>
        </div>
      </div>}

      {tab === 'recognition' && <div className="eb-card">
        <div style={{ marginBottom: 10 }}><span className="eb-hint">按基础资料中的店铺设置收入确认口径。工作台据此判断是否应形成应收，不向金蝶写入任何单据。</span></div>
        <div className="eb-tblwrap"><table><thead><tr><th>店铺</th><th>平台</th><th>收入确认口径</th><th>工作台判断</th></tr></thead><tbody>{shopMap.map((r,i)=>{const key=r.wdt_name,value=recognitionRules[key]||'shipment';return <tr key={key||i}><td className="eb-shop-cell"><strong>{r.mgmt_name||r.wdt_name}</strong>{r.mgmt_name&&r.mgmt_name!==r.wdt_name&&<small>{r.wdt_name}</small>}</td><td>{r.platform||'未设置'}</td><td><select className="eb-rule-select" value={value} disabled={!canEdit} onChange={e=>{setRecognitionRules({...recognitionRules,[key]:e.target.value});setDirty(true)}}><option value="shipment">发货确认收入</option><option value="confirmed">确认收货后确认收入</option></select></td><td>{value==='shipment'?'旺店通已发货 → 应形成应收':'平台确认收货 → 应形成应收'}</td></tr>})}{!shopMap.length&&<tr><td colSpan="4">请先维护店铺对照。</td></tr>}</tbody></table></div>
      </div>}

      {tab === 'fee' && <div className="eb-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <span className="eb-hint">{feeMap.length} 条（种子=两月凭证区实证）。跑批遇到<b>新费目码 → 科目「待定」红标</b>，不套默认科目（确认书⑤ D9）。</span>
          {canEdit && addBtn(addFee)}
        </div>
        <div className="eb-tblwrap">
          <table>
            <thead><tr><th>费目码</th><th>费目名</th><th>记账科目</th><th>金蝶科目编码（一键录入用）</th>{canEdit && <th style={{ width: 36 }}></th>}</tr></thead>
            <tbody>{feeMap.map((r, i) => <tr key={i}>
              <td className="eb-mono" style={{ color: 'var(--ink-2)' }}>{r.code}</td>
              <td style={{ ...editCell, whiteSpace: 'normal' }} onClick={() => editRow(feeMap, setFeeMap, i, 'label', '费目名')}>{r.label}</td>
              <td style={{ ...editCell, color: !r.account || r.account === '待定' ? 'var(--red,#c0392b)' : undefined, fontWeight: !r.account || r.account === '待定' ? 700 : 400 }}
                onClick={() => editRow(feeMap, setFeeMap, i, 'account', '记账科目')}>{r.account || '待定'}</td>
              <td className="eb-mono" style={editCell} onClick={() => editRow(feeMap, setFeeMap, i, 'kd_code', '金蝶科目编码（如 6601 或 6601.01；以春艳实际记账口径为准）')}>
                {r.kd_code || (['0010001', '0020001'].includes(r.code) ? <span style={{ color: 'var(--ink-3)' }}>—（走凭证配置两侧科目）</span> : <span style={{ color: 'var(--amber,#a35a00)' }}>未配</span>)}</td>
              {canEdit && <td><span style={{ cursor: 'pointer', color: 'var(--red,#c0392b)', fontSize: 12 }} onClick={() => delRow(feeMap, setFeeMap, i)}>删</span></td>}
            </tr>)}</tbody>
          </table>
        </div>
      </div>}

      {tab === 'voucher' && <div className="eb-card" style={{ maxWidth: 720 }}>
        <div style={{ marginBottom: 10 }}>
          <span className="eb-hint">一键录入结算凭证的账套口径——<b>配不齐按钮不亮，不出半张报文</b>。科目编码请以春艳实际记账凭证为准（金蝶里未查到历史结算凭证模板，不预填猜测值）。</span></div>
        <div className="eb-tblwrap">
          <table>
            <tbody>
              {[['book_code', '账簿编码（FACCOUNTBOOKID，如深圳星期零账簿）'],
                ['voucher_group', '凭证字编码（FVOUCHERGROUPID，如 记/PRE001）'],
                ['currency', '币别编码（人民币通常 PRE001）'],
                ['cash_acct', '其他货币资金-支付宝 科目编码（贷方/借方两张凭证共用）'],
                ['ar_acct', '应收账款 科目编码（收款核销贷方）'],
                ['rate_type', '汇率类型编码（可空，默认 HLTX01_SYS）']].map(([k, label]) => <tr key={k}>
                  <td style={{ whiteSpace: 'normal', color: 'var(--ink-2)' }}>{label}</td>
                  <td className="eb-mono" style={{ ...editCell, minWidth: 120 }}
                    onClick={() => { if (!canEdit) return; const v = window.prompt(label, vcfg[k] || ''); if (v === null) return; setVcfg({ ...vcfg, [k]: v.trim() }); setDirty(true) }}>
                    {vcfg[k] || <span style={{ color: 'var(--amber,#a35a00)' }}>未配</span>}</td>
                </tr>)}
            </tbody>
          </table>
        </div>
      </div>}

      {tab === 'rules' && <div className="eb-card" style={{ maxWidth: 700 }}>
        <div style={{ marginBottom: 10 }}>
          <span className="eb-hint">U先主识别=专属费目码（0170155T 等，内置）；下面是兜底阈值。剔除项每期在「收款核销 › 核销总览」单列留痕。</span></div>
        <div className="eb-tblwrap">
          <table>
            <tbody>
              {[['ufirst_max', 'U先金额档兜底：单收入低于此值且查无应收 → 归U先桶（元）'],
                ['inner_min', '内部划转阈值：空描述大额转账收入 ≥ 此值 → 剔除留痕（元）'],
                ['qr_goods', '线下扫码直付的商品名（剔除留痕，如线下活动收钱码）']].map(([k, label]) => <tr key={k}>
                  <td style={{ whiteSpace: 'normal', color: 'var(--ink-2)' }}>{label}</td>
                  <td className="eb-mono" style={{ ...editCell, minWidth: 90, textAlign: 'right' }}
                    onClick={() => { if (!canEdit) return; const v = window.prompt(label, rules[k]); if (v === null) return; setRules({ ...rules, [k]: k === 'qr_goods' ? v : Number(v) }); setDirty(true) }}>
                    {String(rules[k] ?? '')}</td>
                </tr>)}
            </tbody>
          </table>
        </div>
      </div>}

      {tab === 'flowrules' && <div className="eb-card">
        <div style={{ display:'flex',alignItems:'center',gap:10,marginBottom:10,flexWrap:'wrap' }}>
          <span className="eb-hint">规则只处理“待识别流水”；已人工定性的自动跳过。先保存规则，再预览笔数和金额，最后确认批量应用。</span>
          {canEdit && addBtn(addFlowRule)}
        </div>
        <div className="eb-tblwrap"><table><thead><tr><th>启用</th><th>规则名称</th><th>账户</th><th>匹配字段</th><th>关键词（用 | 分隔，须全部命中）</th><th>方向</th><th>归入分桶</th><th>费用名称</th><th>操作</th></tr></thead>
          <tbody>{flowRules.map((r,i)=><tr key={r.id}>
            <td><input type="checkbox" checked={!!r.enabled} disabled={!canEdit} onChange={e=>updateFlowRule(i,{enabled:e.target.checked})}/></td>
            <td><input aria-label={`规则名称 ${i+1}`} className="eb-rule-input" value={r.name||''} disabled={!canEdit} onChange={e=>updateFlowRule(i,{name:e.target.value})}/></td>
            <td><select aria-label={`适用账户 ${i+1}`} className="eb-rule-select" value={r.account_kind||''} disabled={!canEdit} onChange={e=>updateFlowRule(i,{account_kind:e.target.value})}><option value="">全部</option><option value="alipay">支付宝</option><option value="fund">聚合账户</option></select></td>
            <td><select aria-label={`匹配字段 ${i+1}`} className="eb-rule-select" value={r.field||'remark'} disabled={!canEdit} onChange={e=>updateFlowRule(i,{field:e.target.value})}><option value="remark">备注 / 摘要</option><option value="desc">业务描述</option><option value="btype">账务类型</option><option value="goods">商品名称</option></select></td>
            <td><input aria-label={`关键词 ${i+1}`} className="eb-rule-input" value={(r.keywords||[]).join('|')} disabled={!canEdit} onChange={e=>updateFlowRule(i,{keywords:e.target.value.split('|').map(v=>v.trim()).filter(Boolean)})}/></td>
            <td><select aria-label={`收支方向 ${i+1}`} className="eb-rule-select" value={r.direction||''} disabled={!canEdit} onChange={e=>updateFlowRule(i,{direction:e.target.value})}><option value="outgo">支出</option><option value="income">收入</option><option value="">不限</option></select></td>
            <td><select aria-label={`目标分桶 ${i+1}`} className="eb-rule-select" value={r.bucket||'fee'} disabled={!canEdit} onChange={e=>updateFlowRule(i,{bucket:e.target.value})}>{Object.entries(FLOW_BUCKETS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></td>
            <td><input aria-label={`分类名称 ${i+1}`} className="eb-rule-input" value={r.label||''} disabled={!canEdit} onChange={e=>updateFlowRule(i,{label:e.target.value})}/></td>
            <td><div className="eb-rule-actions"><button className="btn-sec" disabled={dirty||working||!r.enabled} onClick={()=>previewRule(r.id)}>预览</button>{canEdit&&<button className="btn-sec" disabled={working} onClick={()=>{setFlowRules(flowRules.filter((_,j)=>j!==i));setPreview(null);setDirty(true)}}>删除</button>}</div></td>
          </tr>)}</tbody></table></div>
        {dirty&&<p className="eb-hint">规则有未保存改动；保存后才能按服务器当前流水预览。</p>}
        {preview&&<section className="eb-preview" aria-label="规则命中预览"><div className="eb-preview-summary"><span>命中 <strong>{count(preview.matched)}</strong> 笔</span><span>收入 <strong>¥{money(preview.income)}</strong></span><span>支出 <strong>¥{money(preview.outgo)}</strong></span><span>跳过人工定性 <strong>{count(preview.skipped_reviewed)}</strong> 笔</span><span>规则冲突 <strong>{count(preview.skipped_conflicts)}</strong> 笔</span></div>
          <div className="eb-tblwrap"><table><thead><tr><th>账户 / 时间</th><th>流水号</th><th>订单线索</th><th>命中文字</th><th>收入</th><th>支出</th></tr></thead><tbody>{preview.samples?.map(s=><tr key={s.id}><td>{s.account_name}<br/><small>{s.ts}</small></td><td className="eb-mono">{s.serial||'—'}</td><td className="eb-mono">{s.order_no||'—'}</td><td style={{whiteSpace:'normal'}}>{s.text||'—'}</td><td>{money(s.income)}</td><td>{money(s.outgo)}</td></tr>)}</tbody></table></div>
          {preview.applied?<p><strong>已处理 {count(preview.applied)} 笔。</strong>可到账户流水页按“平台费用”查看。</p>:<button className="btn-primary" disabled={!canEdit||working||!preview.matched} onClick={applyRule}>{working?'处理中…':`确认批量归类 ${count(preview.matched)} 笔`}</button>}
        </section>}
      </div>}
    </div>
  </div>
}
