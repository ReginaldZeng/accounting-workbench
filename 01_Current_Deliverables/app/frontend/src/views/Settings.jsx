// [Change Log] Date:2026-07-17 Author:Claude/c Version:V2.52
// 设置页 =「导航模块上线管理」(一级板块 + 菜单树 + 状态机 + 岗位标签，限主管理员，整页宽) + 数据源 / 会计期间 / 金蝶连接。
// 状态（V2.173/174）：敬请期待 → 开发中 → 测试验证 → 人工并行 → 待验收 → 引擎正常；测试验证/人工并行/待验收/引擎正常 能进入。
// V2.242：「开发中」**只有主管理员能进**，其余人灰显不可点（业务方定）——在建的要能自己进去看，但别让同事撞见半成品。
// V2.52（确认书 20260717）：一级板块可增删改名排序；模块可改挂哪个一级 / 挂到哪个二级下（＝建三级）/ 排序，
//   内置模块也能改（存的是覆盖值）；岗位 key 化——名称随便改，模块上的标签和账号的绑定都不丢。
// 「引擎正常」可挂岗位标签——这是【标签】不是权限门，挡人用账号管理里的权限点。
import React, { useState, useEffect } from 'react'
import { setConfig, testKingdee, getNavModules, saveNavModules, addNavModule, delNavModule, moveNavModule, saveNavSections } from '../api.js'

// 状态色：灰=还没开放，琥珀=可进但未定稿，绿=正式可用
const ST_STYLE = {
  '敬请期待': { c: 'var(--ink-3)', bg: 'var(--bg)', b: 'var(--line)' },
  '开发中': { c: 'var(--blue)', bg: 'var(--blue-bg)', b: 'var(--blue-line)' },
  '待验收': { c: 'var(--amber)', bg: 'var(--amber-bg)', b: 'var(--amber-line)' },
  '测试验证': { c: 'var(--purple)', bg: 'var(--purple-bg)', b: 'var(--purple-line)' },
  '人工并行': { c: 'var(--teal)', bg: 'var(--teal-bg)', b: '#99e8dd' },
  '引擎正常': { c: 'var(--green)', bg: 'var(--green-bg)', b: 'var(--green-line)' },
  '等待部署': { c: 'var(--purple)', bg: 'var(--purple-bg)', b: 'var(--purple-line)' },     // 旧值兜底（后端已平移，防缓存）
  '已上线': { c: 'var(--green)', bg: 'var(--green-bg)', b: 'var(--green-line)' },
  '隐藏': { c: 'var(--ink-3)', bg: 'var(--line)', b: 'var(--line-strong)' },   // 从导航移除，仅本页可见
}
const pill = st => {
  const s = ST_STYLE[st] || ST_STYLE['敬请期待']
  return { color: s.c, background: s.bg, borderColor: s.b }
}

function NavModules({ onModsChanged }) {
  const [d, setD] = useState(null), [st, setSt] = useState({}), [posts, setPosts] = useState([])
  const [secs, setSecs] = useState([])
  const [busy, setBusy] = useState(false), [msg, setMsg] = useState('')
  const [showSecs, setShowSecs] = useState(false), [nsec, setNsec] = useState({ key: '', label: '' })
  const [nm, setNm] = useState({ key: '', label: '', sec: '', parent: '', status: '开发中' })   // 自建模块表单（hook 必须在早退之前）
  const load = r => { setD(r); setSt(r.state); setPosts(r.posts); setSecs(r.sections) }
  useEffect(() => { getNavModules().then(load).catch(() => {}) }, [])
  if (!d) return <div className="cat"><div className="loading" style={{ padding: 12, fontSize: 12.5 }}>加载中…</div></div>

  const editable = d['可编辑']
  const eq = (a, b) => a?.status === b?.status && (a?.posts || []).join() === (b?.posts || []).join()
  const dirty = d.modules.some(m => !m.always && !eq(st[m.key], d.state[m.key]))   // 岗位已移门户维护，本页不再算它的 dirty
  const secLabel = k => (secs.find(s => s.key === k) || {}).label || k

  const setStatus = (k, status) =>
    setSt(s => ({ ...s, [k]: { ...s[k], status, posts: status === '引擎正常' ? (s[k]?.posts || []) : [] } }))
  const togglePost = (k, p) => setSt(s => {
    const cur = s[k]?.posts || []
    return { ...s, [k]: { ...s[k], posts: cur.includes(p) ? cur.filter(x => x !== p) : [...cur, p] } }
  })
  // 岗位名单维护已整体搬到【门户 › 账号管理 › 岗位模板设置】（V2.146，业务方定"岗位名单应该放到门户"）。
  // 这里不再能增删改岗位——两个地方都能改＝不知道谁生效；本页保存也只发模块状态（posts 不再上送）。
  const doSave = async () => {
    setBusy(true); setMsg('')
    const r = await saveNavModules(st)
    setBusy(false); setMsg(r.msg || (r.ok ? '✓ 已保存，全员生效' : '保存失败'))
    if (r.ok) { setD({ ...d, state: r.state, posts: r.posts }); setSt(r.state); setPosts(r.posts); onModsChanged?.(); setTimeout(() => setMsg(''), 2200) }
  }
  const reset = () => { setSt(d.state); setPosts(d.posts); setSecs(d.sections); setMsg('') }

  // 菜单树改动（一级/模块位置/自建）：即时生效，后端回传全量，不走上面的「保存」按钮
  const applyDef = r => {
    setD(v => ({ ...v, modules: r.modules, sections: r.sections || v.sections, state: r.state || v.state }))
    if (r.state) setSt(r.state)
    if (r.sections) setSecs(r.sections)
    onModsChanged?.()
  }
  const addModule = async () => {
    if (!nm.label.trim()) { setMsg('填模块名称'); return }
    if (!nm.key.trim()) { setMsg('填标识 key（当路由用，如 vat_check）'); return }
    if (!nm.parent && !nm.sec) { setMsg('选一个一级板块，或选一个父模块（＝建三级）'); return }
    setBusy(true); setMsg('')
    const r = await addNavModule({ key: nm.key.trim(), label: nm.label.trim(), sec: nm.sec, parent: nm.parent || null, status: nm.status })
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '新增失败'); return }
    applyDef(r); setNm({ key: '', label: '', sec: '', parent: '', status: '开发中' })
    setMsg(`✓ 已新增（「${nm.status}」，侧栏${d['可进入状态']?.includes(nm.status) ? '可进入·但还没接代码会显示规划占位' : '灰显不可点'}，接上代码后可用）`); setTimeout(() => setMsg(''), 3200)
  }
  const delModule = async (m) => {
    if (!window.confirm(`删除自建模块「${m.label}」？（内置工具不可删；这是自建的规划占位）`)) return
    const r = await delNavModule(m.key)
    if (!r.ok) { setMsg(r.msg || '删除失败'); return }
    applyDef(r); setMsg('✓ 已删除'); setTimeout(() => setMsg(''), 2000)
  }
  const move = async (key, patch) => {
    setBusy(true); setMsg('')
    const r = await moveNavModule({ key, ...patch })
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '调整失败'); return }
    applyDef(r); setMsg('✓ 已调整'); setTimeout(() => setMsg(''), 1600)
  }
  const saveSecs = async () => {
    setBusy(true); setMsg('')
    const r = await saveNavSections(secs)
    setBusy(false)
    if (!r.ok) { setMsg(r.msg || '保存失败'); return }
    applyDef(r); setMsg('✓ 一级板块已保存'); setTimeout(() => setMsg(''), 2000)
  }
  const addSec = () => {
    const { key, label } = nsec
    if (!key.trim() || !label.trim()) { setMsg('一级板块要填标识和名称'); return }
    if (secs.some(s => s.key === key.trim())) { setMsg('标识已存在'); return }
    setSecs(v => [...v.filter(s => !s.bottom), { key: key.trim(), label: label.trim(), order: 500, builtin: false },
                  ...v.filter(s => s.bottom)])
    setNsec({ key: '', label: '' })
  }
  // 可当父项的：非纯分组之外都行？——不，父项自己不能已经是三级（菜单只到三级）
  const parentOptions = d.modules.filter(m => !m.parent && !m.always)

  return (<div className="cat">
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>导航模块上线管理</div>
      {editable && <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        {dirty && <button className="btn" onClick={reset} disabled={busy}>撤销改动</button>}
        <button className="btn btn-pri" disabled={!dirty || busy} onClick={doSave}>{busy ? '保存中…' : '保存'}</button>
      </div>}
    </div>
    <div className="foot" style={{ marginBottom: 12, lineHeight: 1.8 }}>
      状态流转：<b>敬请期待 → 开发中 → 测试验证 → 人工并行 → 待验收 → 引擎正常</b>。
      <b>测试验证 / 人工并行 / 待验收 / 引擎正常</b> 全员都能从左侧导航进入（待验收要让人进去才验得了）；
      <b style={{ color: 'var(--amber)' }}>「开发中」只有指定的人进得去，其他人灰显、点不动</b>——在建的东西自己要能进去看效果，
      但半成品不该让同事撞见（撞见就会问"这怎么用／怎么报错了"，白白多一轮解释）。其余状态所有人都灰显、不可点。
      {/* 这个闸读不到配置时是放宽的（回落全体主管理员）。**必须把服务器实际读到的东西摆出来**，
          否则"我明明配了却不生效"在页面上一点线索都没有（V2.242 联调实际卡在这里）。 */}
      {'开发者名单说明' in d && <div style={{
        marginTop: 8, padding: '8px 12px', borderRadius: 8, fontSize: 12.5,
        background: (d['开发者名单'] || []).length ? 'var(--amber-bg)' : 'var(--bg-sub)',
        border: '1px solid ' + ((d['开发者名单'] || []).length ? 'var(--amber-line)' : 'var(--line)'),
      }}>
        <b>「开发中」当前谁进得去</b>
        {(d['开发者名单'] || []).length > 0
          ? <>名单：<b style={{ color: 'var(--amber)' }}>{d['开发者名单'].join('、')}</b>——{d['开发者名单说明']}</>
          : <>没有名单 → <b>全体主管理员</b>都能进。<span style={{ color: 'var(--ink-3)' }}>原因：{d['开发者名单说明']}</span></>}
        <br />
        <span style={{ color: 'var(--ink-3)' }}>
          你现在的账号名是 <b style={{ color: 'var(--ink-2)' }}>{d['我是谁'] || '（未登录）'}</b>，
          {d['我能进开发中'] ? '进得去。' : '进不去。'}
          名单要写的是<b>账号名</b>（就是这个名字），不是岗位或角色。
          在服务器 conf.ini <code>[nav] dev_users</code> 里改，<b>页面上改不了</b>——能在页面改的话，任何主管理员都能把自己加回去，这个限制就等于没有。
          改完<b>要重启服务</b>才生效。
        </span>
      </div>}
      <b>「隐藏」</b>＝从左侧导航整个移除（本页仍可见、可随时改回），用来收起排期很后、暂不想露出的模块。
      「引擎正常」（＝正式运行，左侧导航显呼吸绿灯）可勾选服务岗位；「人工并行」＝与手工流程并行试跑；「测试验证」＝测试期亦可进入查看。设置存在服务端，<b>全员生效</b>。
      <br />这是<b>上线状态，不是权限</b>——它只管导航显示与岗位标注，<b>不挡接口</b>；要挡人请用「账号管理」里的权限点。
      「开发中·仅主管理员」也一样：它挡的是<b>菜单点不点得动</b>，不是接口。真要防人访问，仍然靠权限点。
      {!editable && <><br /><span style={{ color: 'var(--ink-3)' }}>只有主管理员能修改，以下仅供查看。</span></>}
    </div>

    {/* 一级板块维护 */}
    <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, background: 'var(--bg)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>
          一级板块 <span style={{ fontWeight: 400, color: 'var(--ink-3)' }}>（{secs.filter(s => !s.bottom).length} 个；数字小的排前面）</span>
        </div>
        <span className="lk" style={{ fontSize: 12 }} onClick={() => setShowSecs(v => !v)}>{showSecs ? '收起' : '展开调整'}</span>
      </div>
      {showSecs && <div style={{ marginTop: 10 }}>
        {secs.filter(s => !s.bottom).map(s => (
          <div key={s.key} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <input value={s.label} disabled={!editable || busy} maxLength={20}
              onChange={e => setSecs(v => v.map(x => x.key === s.key ? { ...x, label: e.target.value } : x))}
              style={{ height: 28, width: 130, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 8px', fontSize: 12.5 }} />
            <input type="number" value={s.order} disabled={!editable || busy}
              onChange={e => setSecs(v => v.map(x => x.key === s.key ? { ...x, order: Number(e.target.value) } : x))}
              style={{ height: 28, width: 66, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 8px', fontSize: 12.5 }} />
            <span style={{ fontSize: 11, color: 'var(--ink-3)', fontFamily: 'ui-monospace,Consolas,monospace' }}>{s.key}</span>
            {s.builtin
              ? <span style={{ fontSize: 11, color: 'var(--ink-3)' }} title="内置板块不可删；要清空请把它下面的模块挪走">内置</span>
              : editable && <span className="lk" style={{ color: 'var(--red)', fontSize: 12 }}
                onClick={() => setSecs(v => v.filter(x => x.key !== s.key))}>删除</span>}
          </div>))}
        {editable && <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
          <input value={nsec.label} onChange={e => setNsec({ ...nsec, label: e.target.value })} placeholder="新板块名，如 税务模块" maxLength={20}
            style={{ height: 28, width: 130, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 8px', fontSize: 12.5 }} />
          <input value={nsec.key} onChange={e => setNsec({ ...nsec, key: e.target.value })} placeholder="标识，如 tax"
            style={{ height: 28, width: 100, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 8px', fontSize: 12.5, fontFamily: 'ui-monospace,Consolas,monospace' }} />
          <button className="btn" style={{ height: 28, padding: '0 12px', fontSize: 12 }} onClick={addSec} disabled={busy}>添加</button>
          <button className="btn btn-pri" style={{ height: 28, padding: '0 14px', fontSize: 12 }} onClick={saveSecs} disabled={busy}>保存一级板块</button>
        </div>}
      </div>}
    </div>

    {/* 岗位名单：只读展示 + 指路（V2.146 维护已搬门户，不能两个地方都能改） */}
    <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, background: 'var(--bg)' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 8 }}>
        岗位名单 <span style={{ fontWeight: 400, color: 'var(--ink-3)' }}>
          （{posts.length} 个 · 新增/改名/删除请到 <b>门户 › 账号管理 › 📋 岗位模板设置</b>——岗位与套用模板在那边一起管）</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {posts.map(p => (
          <span key={p.key} className="pill-src" style={{ color: 'var(--accent)', background: 'var(--accent-soft)', borderColor: 'var(--accent)' }}
            title={'标识 ' + p.key}>{p.label}</span>))}
      </div>
    </div>

    <div style={{ border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex', gap: 12, padding: '8px 14px', background: 'var(--bg)', fontSize: 11.5, color: 'var(--ink-3)', borderBottom: '1px solid var(--line)' }}>
        <span style={{ flex: '0 0 180px' }}>模块</span>
        <span style={{ flex: '0 0 130px' }}>状态</span>
        <span style={{ flex: 1 }}>服务岗位（仅「引擎正常」可挂）</span>
        <span style={{ flex: '0 0 210px' }}>位置（挂到哪 · 排序）</span>
        <span style={{ flex: '0 0 60px' }} />
      </div>
      {secs.map(sec => {
        const g = sec.key
        const raw = d.modules.filter(m => m.sec === g)
        if (!raw.length) return null
        // 排序：父项在前、子项（parent 指向它）紧随其后并缩进——与侧栏一致
        const items = []
        raw.filter(m => !m.parent).forEach(p => {
          items.push(p)
          raw.filter(c => c.parent === p.key).forEach(c => items.push(c))
        })
        raw.filter(m => m.parent && !raw.some(x => x.key === m.parent) && !items.includes(m)).forEach(m => items.push(m))
        return (<React.Fragment key={g}>
          <div style={{ padding: '7px 14px', background: 'var(--bg)', fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>
            {sec.label}{sec.bottom && <span style={{ fontWeight: 400, color: 'var(--ink-3)' }}> · 钉在侧栏底部</span>}</div>
          {items.map(m => {
            const cur = st[m.key] || {}
            const live = cur.status === '引擎正常' && !m.always   // always 的（设置）后端不存岗位，别显示可点标签
            const kids = d.modules.filter(x => x.parent === m.key)
            return (<div key={m.key} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 14px', borderTop: '1px solid var(--line)' }}>
              <span style={{ flex: '0 0 180px', fontSize: 13, paddingLeft: m.parent ? 18 : 0, color: m.parent ? 'var(--ink-2)' : 'var(--ink-1)' }}>
                {m.parent && <span style={{ color: 'var(--ink-3)', marginRight: 4 }}>└</span>}{m.label}
                {m.group_only
                  ? <span style={{ marginLeft: 5, fontSize: 10.5, color: 'var(--ink-3)' }} title="纯分组父项：点了展开子项，本身没有页面">分组</span>
                  : kids.length > 0 && <span style={{ marginLeft: 5, fontSize: 10.5, color: 'var(--accent)' }} title="点文字进页面，点箭头展开子项">可进入</span>}
              </span>
              <span style={{ flex: '0 0 130px' }}>
                {m.always
                  ? <span className="pill-src" style={pill('引擎正常')}>始终开启</span>
                  : editable
                    ? <select value={cur.status || m.default} onChange={e => setStatus(m.key, e.target.value)} disabled={busy}
                      style={{ height: 30, borderRadius: 7, border: '1px solid var(--line-strong)', background: 'var(--bg)', color: ST_STYLE[cur.status]?.c || 'var(--ink)', fontSize: 12.5, padding: '0 6px', fontWeight: 600 }}>
                      {d.statuses.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                    : <span className="pill-src" style={pill(cur.status)}>{cur.status}</span>}
              </span>
              <span style={{ flex: 1, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {!live
                  ? <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>—</span>
                  : posts.map(p => {     // 用当前编辑中的名单，新增的岗位立刻能勾
                    const sel = (cur.posts || []).includes(p.key)
                    return (<span key={p.key} onClick={() => editable && !busy && togglePost(m.key, p.key)}
                      className="pill-src"
                      style={{
                        cursor: editable ? 'pointer' : 'default', userSelect: 'none',
                        color: sel ? 'var(--accent)' : 'var(--ink-3)',
                        background: sel ? 'var(--accent-soft)' : 'var(--bg)',
                        borderColor: sel ? 'var(--accent)' : 'var(--line)',
                        fontWeight: sel ? 600 : 400,
                      }}>{sel ? '✓ ' : ''}{p.label}</span>)
                  })}
              </span>
              {/* 位置：改一下即时生效（内置模块存的是覆盖值，代码里那份只当默认） */}
              <span style={{ flex: '0 0 210px', display: 'flex', gap: 5, alignItems: 'center' }}>
                {m.always
                  ? <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>平台基础设施 · 固定</span>
                  : editable ? <>
                    <select value={m.parent || ('sec:' + m.sec)} disabled={busy}
                      title="挂到哪个一级板块，或挂在哪个二级模块下面（＝当三级）"
                      onChange={e => {
                        const v = e.target.value
                        move(m.key, v.startsWith('sec:') ? { sec: v.slice(4), parent: null } : { parent: v })
                      }}
                      style={{ height: 28, maxWidth: 128, borderRadius: 7, border: '1px solid var(--line-strong)', background: 'var(--bg)', fontSize: 11.5, padding: '0 4px' }}>
                      {secs.filter(s => !s.bottom).map(s => <option key={s.key} value={'sec:' + s.key}>{s.label}</option>)}
                      {parentOptions.filter(p => p.key !== m.key && !kids.length)
                        .map(p => <option key={p.key} value={p.key}>└ {p.label} 下</option>)}
                    </select>
                    <input type="number" defaultValue={m.order} disabled={busy} title="排序：数字小的排前面"
                      onBlur={e => Number(e.target.value) !== m.order && move(m.key, { order: Number(e.target.value) })}
                      style={{ height: 28, width: 58, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 6px', fontSize: 11.5 }} />
                  </> : <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                    {m.parent ? '└ ' + (d.modules.find(x => x.key === m.parent) || {}).label : secLabel(m.sec)} · {m.order}</span>}
              </span>
              <span style={{ flex: '0 0 60px', textAlign: 'right' }}>
                {editable && m.builtin === false
                  ? <span className="lk" style={{ color: 'var(--red)', fontSize: 12 }} onClick={() => !busy && delModule(m)} title="删除这个自建模块">删除</span>
                  : m.builtin === false ? null : <span style={{ fontSize: 11, color: 'var(--ink-3)' }} title="内置工具由代码持有、不可删（位置可以改）；要隐藏请设「敬请期待」">内置</span>}
              </span>
            </div>)
          })}
        </React.Fragment>)
      })}
    </div>

    {editable && <div style={{ marginTop: 12, border: '1px dashed var(--line-strong)', borderRadius: 9, padding: '12px 14px', background: 'var(--bg-sub)' }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>+ 自建模块（规划占位，不用等开发）</div>
      <div style={{ fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.6, marginBottom: 10 }}>
        加一个还没开发的模块，它在侧栏灰显、点不进去；等开发接上同名<b>标识（key）</b>就自动可用。标识当路由用——小写字母开头，只含小写字母/数字/下划线。
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input value={nm.label} onChange={e => setNm({ ...nm, label: e.target.value })} placeholder="模块名称，如 进项税核对" maxLength={20}
          style={{ height: 32, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 10px', fontSize: 12.5, width: 170 }} />
        <input value={nm.key} onChange={e => setNm({ ...nm, key: e.target.value })} placeholder="标识 key，如 vat_check"
          style={{ height: 32, borderRadius: 7, border: '1px solid var(--line-strong)', padding: '0 10px', fontSize: 12.5, width: 150, fontFamily: 'ui-monospace,Consolas,monospace' }} />
        <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>归入</span>
        <select value={nm.parent ? 'p:' + nm.parent : 'sec:' + nm.sec}
          onChange={e => {
            const v = e.target.value
            setNm(v.startsWith('sec:') ? { ...nm, sec: v.slice(4), parent: '' } : { ...nm, parent: v.slice(2), sec: '' })
          }}
          style={{ height: 32, borderRadius: 7, border: '1px solid var(--line-strong)', background: 'var(--bg)', padding: '0 8px', fontSize: 12.5, maxWidth: 210 }}>
          <option value="sec:">— 选一级板块，或挂到某个二级下 —</option>
          {secs.filter(s => !s.bottom).map(s => <option key={s.key} value={'sec:' + s.key}>{s.label}（当二级）</option>)}
          {parentOptions.map(p => <option key={p.key} value={'p:' + p.key}>└ {p.label} 下（当三级）</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>状态</span>
        <select value={nm.status} onChange={e => setNm({ ...nm, status: e.target.value })}
          style={{ height: 32, borderRadius: 7, border: '1px solid var(--line-strong)', background: 'var(--bg)', color: ST_STYLE[nm.status]?.c, fontWeight: 600, padding: '0 8px', fontSize: 12.5 }}>
          {d.statuses.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <button onClick={addModule} disabled={busy}
          style={{ height: 32, padding: '0 14px', border: 0, borderRadius: 7, background: 'var(--accent)', color: '#fff', fontSize: 12.5, fontWeight: 600, cursor: 'pointer' }}>新增</button>
      </div>
    </div>}
    {msg && <div style={{ marginTop: 10, fontSize: 12.5, color: msg.startsWith('✓') ? 'var(--green)' : 'var(--red)' }}>{msg}</div>}
  </div>)
}

export default function Settings({ cfg, onChange, onModsChanged }) {
  const [src, setSrc] = useState(cfg.source || 'sample')
  const [year, setYear] = useState(cfg.year || 2026), [period, setPeriod] = useState(cfg.period || 6)
  const [test, setTest] = useState(null), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false)
  const save = async () => { const c = await setConfig({ source: src, year: Number(year), period: Number(period) }); onChange(c); setSaved(true); setTimeout(() => setSaved(false), 1600) }
  const doTest = async () => { setBusy(true); try { setTest(await testKingdee()) } catch (e) { setTest({ ok: false, msg: String(e) }) } finally { setBusy(false) } }
  const inp = { width: 90, height: 32, borderRadius: 8, border: '1px solid var(--line-strong)', background: 'var(--bg)', color: 'var(--ink)', padding: '0 10px', fontSize: 13 }

  return (<div>
    <div className="head"><div><div className="h-title">系统设置</div>
      <div className="h-sub">导航模块上线管理 · 数据源 · 会计期间 · 金蝶连接 · 仅主管理员可进入</div></div></div>
    {/* 模块表要横向铺满整页（状态+岗位一行放得下）；下面几张小卡片仍限宽，免得输入框拉得老长 */}
    <div className="body">
      <NavModules onModsChanged={onModsChanged} />
      <div style={{ maxWidth: 660 }}>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>数据源</div>
          <label className="ck" style={{ marginBottom: 8 }}><input type="radio" name="src" checked={src === 'sample'} onChange={() => setSrc('sample')} /> 样例数据（内置，离线可用，用于演示/验收界面）</label>
          <label className="ck"><input type="radio" name="src" checked={src === 'kingdee'} onChange={() => setSrc('kingdee')} /> 金蝶真数据（调金蝶云星空 WebAPI 拉取，只读）</label>
          <div className="foot" style={{ marginTop: 10 }}>conf 路径：{cfg.conf || '(未找到 conf.ini，请放到 backend/ 或设 KD_CONF_PATH)'}</div>
        </div>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>会计期间</div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span className="flabel">年</span><input type="number" value={year} onChange={e => setYear(e.target.value)} style={inp} />
            <span className="flabel">期</span><input type="number" min="1" max="12" value={period} onChange={e => setPeriod(e.target.value)} style={{ ...inp, width: 70 }} />
          </div>
        </div>
        <div className="cat">
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>金蝶连接测试</div>
          <button className="btn" onClick={doTest} disabled={busy}>{busy ? '测试中…' : '测试连接'}</button>
          {test && <div style={{ marginTop: 10, fontSize: 12.5, color: test.ok ? 'var(--green)' : 'var(--red)' }}>{test.ok ? '✓ 连接成功' : '⚠ 连接失败'} · {test.msg}</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button className="btn btn-pri" onClick={save}>保存设置</button>
          {saved && <span style={{ fontSize: 12.5, color: 'var(--green)' }}>✓ 已保存，切换数据源后各页会自动刷新</span>}
        </div>
      </div>
    </div>
  </div>)
}
