// [Change Log]
// Date: 2026-10-06 | Author: Claude Opus 5.5 | Version: V2.850
// Description: 【其它模块 › 凭证装订】(先做物流试运行)。用户梳理纸质付款单的流转：钉钉审核 → 出纳付款 → 纸质单到实习生手里；
//   付款凭证工作台做、财务经理审核。定：「不需要贴条，实习生扫码知道是哪个凭证，标注一下就好」「这个扫码放在其他模块里面，命名为凭证装订」
//   「没审核的显示」「先做物流试运行」。
//   三个页签：① 扫码标注(扫码枪/审批编号 → 主体、凭证号、审没审核、要不要附计提更正单；这一轮已扫的清单)
//            ② 还没扫到的(这个凭证月份已有凭证号、但还没人扫过的纸质单——系统只知道扫没扫过，不知道有没有真写上去)
//   计提更正单不在这里打(V2.851，用户「在审批付款的时候一并打出来」)：审批请款单时就打好、订在付款单后面，凭证号留空；
//   这里扫到要附更正单的，提醒核对后面订没订、把凭证号填到更正单上；万一丢了可以补打。
//   手机用 #/vscan(钉钉扫一扫)，扫过的同样记到这里。扫码、取更正单都只读，不动金蝶。
import React, { useEffect, useRef, useState } from 'react'
import { voucherBindList, voucherBindAdjust } from '../api.js'
import { useScan, printAdjust, ymCn, SUBJ_TONE, LV_CSS } from './LogisticsVoucher.jsx'

const money = n => (n == null || n === '' ? '' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const tone = s => SUBJ_TONE[s] || 'g'
const hm = t => String(t || '').slice(5, 16)
const TABS = [['scan', '扫码标注'], ['todo', '还没扫到的']]

export default function VoucherBind({ user }) {
  const [tab, setTab] = useState('scan')
  const ref = useRef(null)
  const focus = () => { if (ref.current) ref.current.focus() }
  const S = useScan(() => setTimeout(focus, 30))
  const [L, setL] = useState(null)
  const [lerr, setLerr] = useState('')
  const [all, setAll] = useState(false)          // 「还没扫到的」页签：也显示扫过的
  const monthRef = useRef('')
  const loadL = m => voucherBindList(m != null ? m : monthRef.current).then(r => { setL(r); monthRef.current = r.month; setLerr('') }).catch(e => setLerr(e.message))
  useEffect(() => { loadL(''); focus() }, [])
  useEffect(() => { if (S.hist.length) loadL() }, [S.hist.length])
  useEffect(() => { if (tab === 'scan') focus() }, [tab])
  const fileRef = useRef(null)
  const printInsts = (insts, title) => printAdjust(Promise.all(insts.map(i => voucherBindAdjust(i))).then(ds => { loadL(); return ds }), title || '计提更正单')

  const c = S.cur, vs = (c && c.vouchers) || [], a = vs[0]
  const needAdj = c && c.ok && (c.n_adjust > 0 || c.has_xred)
  const items = (L && L.items) || []
  const paper = items.filter(x => x.paper)
  const todo = paper.filter(x => !x.scanned)
  const phoneUrl = `${window.location.origin}${window.location.pathname}#/vscan`

  return (
    <div className="lv vb">
      <style>{LV_CSS}</style><style>{CSS}</style>
      <div className="head"><div><div className="h-title">凭证装订</div>
        <div className="h-sub">纸质付款单到手后：扫右上角的钉钉二维码 → 看它是哪个主体、哪张凭证 → 手写标到单子上。只读，不动金蝶、不做账。试运行：目前认得物流请款单和发票管家收过票的审批单。</div></div>
        <div className="h-tools"><span className="lv-seg">{TABS.map(([k, t]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{t}
          {k === 'todo' && L ? ` ${todo.length}` : ''}</button>)}</span></div></div>
      <div className="body">
        {lerr && <div className="lv-msg bad">清单没读出来：{lerr}</div>}

        {tab === 'scan' && <div className="vb-grid">
          <div className="vb-card" onClick={focus}>
            <div className="vb-t">① 扫码<span style={{ flex: 1 }} /><label className="sc-say"><input type="checkbox" checked={S.say} onChange={e => S.setSay(e.target.checked)} /> 扫到就念出凭证号</label></div>
            <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={S.shot} />
            <div className="sc-in">
              <input ref={ref} value={S.v} onChange={e => S.setV(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') S.go() }} autoComplete="off" spellCheck={false}
                placeholder="光标停在这里，用扫码枪扫付款单（或计提更正单）右上角的二维码；或输入 20 位审批编号后回车" />
              <button className="btn btn-pri" disabled={S.busy || !S.v.trim()} onClick={S.go}>{S.busy ? '查询中…' : '查'}</button>
              <button className="btn" disabled={S.busy} title="没有扫码枪：选一张拍了二维码的照片 / 截图，系统认码" onClick={e => { e.stopPropagation(); fileRef.current && fileRef.current.click() }}>传照片</button>
            </div>
            {S.busy ? <div className="vb-res empty">正在查…</div>
              : !c ? <div className="vb-res empty">还没扫。扫一张，这里显示主体和凭证号<br /><span className="dim">扫码枪要在英文输入法下用；扫一张出一张，不用点鼠标</span></div>
                : !c.ok ? <div className="vb-res bad"><div className="vb-big">没查到</div><div style={{ marginTop: 6 }}>{c.msg}</div></div>
                  : !a ? <div className="vb-res warn"><div className="vb-big">{c.state}</div>
                    <div style={{ marginTop: 6 }}>{c.subject} · {c.payee}{c.amount != null && <> · <b className="mono">{money(c.amount)}</b></>}<br />付款凭证还没做——<b>先放一边</b>，做完再扫。</div></div>
                    : <div className={'vb-res tone-' + tone(a.subject)}>
                      {c.sheet != null && <div className="vb-sheet">这是<b>计提更正单上{c.sheet === 1 ? '「记错主体 · 红冲」' : c.sheet === 2 ? '「记错主体 · 补提」' : ''}的那一项</b>：下面是它的凭证，把凭证号填到更正单这一项的「调整凭证」上{c.sheet === 1 ? '（红冲凭证没有纸质付款单）' : ''}</div>}
                      {c.dup && <div className="vb-dup">这张刚才扫过了</div>}
                      <span className="vb-subj">{a.subject || '主体未知'}</span>
                      <div className="vb-vno">记-{a.vno}</div>
                      <div className="vb-mon">{ymCn(a.month)}凭证 · {a.what}{a.src === '金蝶已有' ? '（金蝶已有，不是本系统写的）' : ''}</div>
                      {vs.slice(1).map((x, i) => <div key={i} className="vb-more">另有　<b>{x.subject} 记-{x.vno}</b>　{ymCn(x.month)} · {x.what}</div>)}
                      {c.sheet == null && c.has_xred && <div className="vb-sheet">这张是<b>主体更正</b>：本主体补提的更正单订在这张付款单后面；另一个主体那张<b>红冲凭证</b>没有纸质付款单，它那一项写在另一个主体的更正单上（更正单按主体出，一个主体一张）。</div>}
                      {c.sheet_note && <div className="vb-sheet">{c.sheet_note}</div>}
                      <div className="vb-tags">
                        {a.checker ? <span className="vb-tag ok">已审核 · {a.checker}</span> : a.audited === false ? <span className="vb-tag warn">还没审核 · 凭证号可能会变</span> : null}
                        {needAdj && <span className="vb-tag warn">后面应订着计提更正单 · 把凭证号填上去</span>}
                        {c.scanned_before && !c.dup && <span className="vb-tag neu">之前扫过 · {c.scanned_before.by} {hm(c.scanned_before.at)}</span>}
                      </div>
                      <dl className="vb-kv"><dt>收款方</dt><dd>{c.payee}</dd><dt>金额</dt><dd className="mono">{money(c.amount)}</dd>
                        {c.bid && <><dt>审批编号</dt><dd className="mono">{c.bid}</dd></>}
                        {a.maker && <><dt>制单人</dt><dd>{a.maker}{a.operator ? `（经办 ${a.operator}）` : ''}</dd></>}
                        {needAdj && <><dt>更正单</dt><dd>审批时已经打好、订在付款单后面；没找到的话 <button className="lnk" onClick={e => { e.stopPropagation(); printInsts([c.inst]) }}>补打一张</button>
                          {c.adj_printed && <span className="dim">　补打过：{c.adj_printed.by} {hm(c.adj_printed.at)}</span>}</dd></>}
                      </dl>
                    </div>}
          </div>
          <div className="vb-card">
            <div className="vb-t">② 这一轮已扫 <span className="dim">{S.hist.length} 张{Object.keys(S.bySubj).length > 0 && '：' + Object.entries(S.bySubj).map(([k, n]) => `${k} ${n}`).join(' · ')}</span>
              <span style={{ flex: 1 }} />{S.hist.length > 0 && <button className="lnk" onClick={() => { S.setHist([]); S.setCur(null) }}>清空</button>}</div>
            <table className="lv-t"><thead><tr><th>#</th><th>主体</th><th>凭证号</th><th>凭证月份</th><th>收款方</th><th className="num">金额</th><th>更正单</th><th>时间</th></tr></thead>
              <tbody>{S.hist.map((h, i) => { const x = (h.vouchers || [])[0]; return <tr key={h.inst}>
                <td className="dim">{S.hist.length - i}</td><td>{x ? <span className={'vb-pill tone-' + tone(x.subject)}>{x.subject}</span> : '—'}</td>
                <td className="mono"><b>{x ? '记-' + x.vno : '还没做账'}</b>{(h.vouchers || []).length > 1 && <span className="dim"> +{h.vouchers.length - 1}</span>}</td>
                <td>{x ? ymCn(x.month) : ''}</td><td className="ell">{h.payee}</td><td className="num">{money(h.amount)}</td>
                <td>{(h.n_adjust > 0 || h.has_xred) ? <span className="lv-pill warn">附</span> : ''}</td><td className="dim">{h.at}</td></tr> })}
                {!S.hist.length && <tr><td colSpan="8" className="lv-empty">扫过的单子按顺序列在这里，方便回头核对有没有漏标</td></tr>}</tbody></table>
            <div className="dim" style={{ marginTop: 10, lineHeight: 1.8 }}>手机也能扫：钉钉里打开 <span className="mono" style={{ userSelect: 'all' }}>{phoneUrl}</span>，点「扫一扫」，扫过的同样记在这里。
              <br />一册装完了，到「凭证归档 › 登记新册」登记起止凭证号。</div>
          </div>
        </div>}

        {tab !== 'scan' && <div className="vb-bar">
          <span>凭证月份</span>
          <select className="lv-sel" value={(L && L.month) || ''} onChange={e => { setL(null); loadL(e.target.value) }}>{((L && L.months) || []).map(m => <option key={m} value={m}>{ymCn(m)}</option>)}</select>
          {tab === 'todo' && L && <><span>有纸质付款单的凭证 <b>{paper.length}</b> 张 · 扫过 <b>{paper.length - todo.length}</b> · 还没扫到 <b className={todo.length ? 'warn' : 'ok'}>{todo.length}</b></span>
            <label className="sc-say"><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} /> 扫过的也列出来</label></>}
          <span style={{ flex: 1 }} /><button className="btn" onClick={() => loadL()}>刷新</button>
        </div>}

        {tab === 'todo' && <>
          <div className="vb-note">这里按「扫没扫过」算：有人拿着纸质单扫了码、看到了凭证号，就记一次。系统<b>不知道他有没有真写上去</b>——所以这张表是用来找「单子还没到手 / 漏扫」的，不是装订完成的凭据。</div>
          <div className="tbl-wrap"><table className="lv-t">
            <thead><tr><th>主体</th><th>凭证号</th><th>收款方</th><th className="num">金额</th><th>钉钉审批编号</th><th>审核</th><th>更正单</th><th>扫码</th></tr></thead>
            <tbody>
              {!L && <tr><td colSpan="8" className="lv-empty">读取中…（审核状态要读金蝶）</td></tr>}
              {L && items.filter(x => all || !x.paper || !x.scanned).map(x => <tr key={x.key} className={x.paper ? '' : 'nopaper'}>
                <td><span className={'vb-pill tone-' + tone(x.subject)}>{x.subject}</span></td><td className="mono"><b>记-{x.vno}</b></td>
                <td>{x.paper ? x.payee : <span className="dim">{x.what} · {x.payee}</span>}</td><td className="num">{money(x.amount)}</td><td className="mono dim">{x.paper ? x.bid : '—'}</td>
                <td>{x.checker ? `已审核 · ${x.checker}` : x.audited === false ? <span className="warn">还没审核</span> : <span className="dim">—</span>}</td>
                <td>{x.adj > 0 ? <span className="lv-pill warn">附</span> : ''}</td>
                <td>{!x.paper ? <span className="lv-pill neu">不用扫</span> : x.scanned ? <span className="lv-pill ok">扫过 · {x.scanned.by} {hm(x.scanned.at)}</span> : <span className="lv-pill neu">还没扫到</span>}</td></tr>)}
              {L && !items.filter(x => all || !x.paper || !x.scanned).length && <tr><td colSpan="8" className="lv-empty">{items.length ? '这个月有凭证号的纸质单都扫到过了' : '这个月还没有做好的付款凭证'}</td></tr>}
            </tbody></table></div>
        </>}

      </div>
    </div>
  )
}

const CSS = `
.vb .h-tools{margin-left:auto}
.vb .vb-grid{display:grid;grid-template-columns:minmax(440px,1.05fr) minmax(420px,1fr);gap:12px;align-items:start}
@media (max-width:1180px){.vb .vb-grid{grid-template-columns:1fr}}
.vb .vb-card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.vb .vb-t{display:flex;align-items:center;gap:8px;font-weight:700;font-size:13.5px;margin-bottom:6px}
.vb .tone-b{--tc:var(--blue);--tb:var(--blue-bg);--tl:var(--blue-line)}.vb .tone-t{--tc:var(--teal);--tb:var(--teal-bg);--tl:#b5e8df}
.vb .tone-o{--tc:#b25c00;--tb:#fdf1e0;--tl:#f1d3a6}.vb .tone-g{--tc:var(--green);--tb:var(--green-bg);--tl:var(--green-line)}
.vb .vb-res{border-radius:14px;padding:18px 20px;margin-top:6px;border:1px solid var(--tl,var(--line));border-left:8px solid var(--tc,var(--line-strong));background:var(--tb,var(--bg));min-height:190px}
.vb .vb-res.empty{border-style:dashed;border-left-width:1px;color:var(--ink-3);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:4px;background:transparent}
.vb .vb-res.warn{background:var(--amber-bg);border-color:var(--amber-line);border-left-color:var(--amber)}.vb .vb-res.warn .vb-big{color:var(--amber)}
.vb .vb-res.bad{background:var(--red-bg);border-color:var(--red-line);border-left-color:var(--red);color:var(--red)}
.vb .vb-big{font-size:24px;font-weight:800;line-height:1.3}
.vb .vb-subj{display:inline-block;background:var(--tc);color:#fff;font-size:16px;font-weight:700;border-radius:8px;padding:2px 12px}
.vb .vb-vno{font-size:56px;font-weight:800;line-height:1.1;margin-top:6px;letter-spacing:1px;color:var(--ink)}
.vb .vb-mon{font-size:14.5px;font-weight:700;margin-top:2px;color:var(--tc)}.vb .vb-more{margin-top:6px;font-size:13px;color:var(--ink-2)}.vb .vb-more b{color:var(--ink);font-size:15px}
.vb .vb-sheet{margin:8px 0;padding:7px 10px;border-radius:8px;background:rgba(255,255,255,.75);border:1px dashed var(--tc,var(--line-strong));font-size:13px;line-height:1.7}
.vb .vb-dup{display:inline-block;background:var(--red);color:#fff;font-size:12.5px;font-weight:700;border-radius:6px;padding:2px 10px;margin-bottom:8px}
.vb .vb-tags{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.vb .vb-tag{display:inline-block;border-radius:6px;padding:2px 9px;font-size:12.5px;font-weight:700;border:1px solid}
.vb .vb-tag.ok{background:var(--green-bg);color:var(--green);border-color:var(--green-line)}.vb .vb-tag.warn{background:var(--amber-bg);color:var(--amber);border-color:var(--amber-line)}
.vb .vb-tag.neu{background:var(--bg);color:var(--ink-2);border-color:var(--line-strong);font-weight:400}
.vb .vb-kv{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;margin:12px 0 0;padding-top:10px;border-top:1px solid rgba(0,0,0,.08);font-size:12.5px}
.vb .vb-kv dt{color:var(--ink-2);white-space:nowrap}.vb .vb-kv dd{margin:0}
.vb .vb-pill{display:inline-block;font-size:11.5px;border-radius:999px;padding:1px 8px;background:var(--tb);color:var(--tc);border:1px solid var(--tl);white-space:nowrap}
.vb .vb-bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:8px 12px;border:1px solid var(--line);border-radius:9px;background:var(--bg-sub);font-size:12.5px}
.vb .vb-note{font-size:12.5px;line-height:1.7;color:var(--ink-2);background:var(--amber-bg);border:1px solid var(--amber-line);border-radius:8px;padding:8px 12px}
.vb tr.nopaper td{background:var(--bg-sub)}.vb .lv-t td{vertical-align:middle}.vb .lv-t td.ell{max-width:200px}
.vb .sc-in{margin:6px 0 4px}
`
