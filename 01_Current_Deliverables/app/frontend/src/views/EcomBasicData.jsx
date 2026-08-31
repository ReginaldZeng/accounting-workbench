// [Change Log] Date:2026-08-13 Author:Claude/c Version:V2.277(店铺管理名称列) / V2.251(首版) V2.253(令牌) V2.254(汇率录入同款骨架)
// 「电商对账 › 基础资料」页（条目⑤一期）。V2.254 与收款核销页同批改版式：
// 页头 title+sub、保存按钮右上角、页签分区（店铺对照 / 费目科目映射 / 识别与剔除规则）、内容铺满自适应。
// 权限：查看=进得来即可；维护=ec_base_edit（敏感点，默认不给）。整表保存、改动留痕。
import React, { useEffect, useState } from 'react'
import { getEcBasicdata, saveEcBasicdata } from '../api.js'

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
`}</style>

export default function EcomBasicData({ user }) {
  const canEdit = !!(user && (user.role === 'admin' || (user.perms || {}).ec_base_edit))
  const [tab, setTab] = useState('shop')       // shop / fee / voucher / rules
  const [shopMap, setShopMap] = useState([])
  const [feeMap, setFeeMap] = useState([])
  const [rules, setRules] = useState({})
  const [vcfg, setVcfg] = useState({})
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => getEcBasicdata().then(r => {
    setShopMap(r.shop_map || []); setFeeMap(r.fee_map || []); setRules(r.rules || {})
    setVcfg(r.voucher_cfg || {}); setDirty(false)
  }).catch(e => setMsg(String(e.message || e)))
  useEffect(() => { load() }, [])

  const save = async () => {
    try {
      setMsg('保存中…')
      await saveEcBasicdata({ shop_map: shopMap, fee_map: feeMap, rules, voucher_cfg: vcfg })
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
      <div className={'eb-tab' + (tab === 'fee' ? ' on' : '')} onClick={() => setTab('fee')}>费目科目映射</div>
      <div className={'eb-tab' + (tab === 'voucher' ? ' on' : '')} onClick={() => setTab('voucher')}>凭证配置</div>
      <div className={'eb-tab' + (tab === 'rules' ? ' on' : '')} onClick={() => setTab('rules')}>识别与剔除规则</div>
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
    </div>
  </div>
}
