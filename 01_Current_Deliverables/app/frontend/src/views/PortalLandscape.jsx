// [Change Log] Date:2026-09-07 Author:Claude/Reginald Zeng Version:V2.514
// 门户「系统架构」页 = 上半嵌架构图 + 下半可编辑的文字区块。
//
// **上半：iframe 原样嵌入你们导出的交互式架构图**（视图/透镜/路径/地图四种交互全保留）。
//   不在这里重画：那份图是专门工具导出的，重画一遍只会更差且丢掉交互。
//   要更新＝在「门户管理 › 系统架构」重新上传导出件，不改代码不发版。
//
// **两个槽位**：full=完整版（含服务器地址与端口）、public=对外脱敏版。
//   服务器 IP 与端口是**烤死在 HTML 里**的，没法按人剥字段，只能按人决定给哪一份——
//   判定在服务端（见 app.api_portal_landscape_diagram），前端只管嵌，
//   且 iframe 的 src 不带 slot 参数：给哪份由后端按权限决定，前端说了不算。
//
// **下半：结论 / 发现 / 建议顺序**，存 app_settings 的 landscape 键，门户管理里可改。
//   这半部分是那张图不回答的东西——图讲「怎么跑的」，这里讲「所以下一步该干什么」。
//   它的技术细节字段仍按 landscape_tech 在服务端剥离。
import React, { useEffect, useState } from 'react'
import { getLandscape } from '../api.js'
import PortalLandscapeMap from './PortalLandscapeMap.jsx'   // V2.514 系统全景视角（含商务线）

const CSS = `
.lsc{padding:26px 34px 80px;max-width:1240px;margin:0 auto}
.lsc .top{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap}
.lsc h2{font-size:22px;font-weight:800;margin:0}
.lsc .sf{margin:8px 0 0;font-size:13px;color:var(--ink2);line-height:1.85;max-width:74ch}
.lsc .tools{margin-left:auto;display:flex;align-items:center;gap:10px}
.lsc .back{font-size:12.5px;color:var(--brand2);cursor:pointer;white-space:nowrap}
.lsc .tgl{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--ink2);
  border:1px solid var(--line2);border-radius:999px;padding:5px 12px;cursor:pointer;transition:all var(--tr);white-space:nowrap}
.lsc .tgl:hover{border-color:var(--brand2);color:var(--ink)}
.lsc .tgl.on{background:rgba(124,92,255,.16);border-color:var(--brand);color:var(--ink)}
.lsc .tgl i{width:7px;height:7px;border-radius:50%;background:var(--gray)}
.lsc .tgl.on i{background:var(--brand2);box-shadow:0 0 6px var(--brand2)}

.lsc .verdict{margin:20px 0 0;padding:18px 20px;border-radius:14px;
  background:rgba(124,92,255,.10);border:1px solid rgba(124,92,255,.28)}
.lsc .verdict b{display:block;font-size:13px;color:var(--brand2);margin-bottom:6px}
.lsc .verdict p{margin:0;font-size:13.5px;color:var(--ink);line-height:1.9;max-width:80ch}

/* 架构图：原件是浅色主题，深色门户里直接贴会很突兀 —— 外面包一层深色卡片 + 页眉过渡，
   iframe 本身给个浅色圆角底，看起来像「嵌在暗色台面上的一张纸」。 */
.lsc .figwrap{margin:22px 0 0;border:1px solid var(--line);border-radius:16px;background:var(--card);overflow:hidden}
.lsc .fighead{display:flex;align-items:center;gap:10px;padding:11px 16px;border-bottom:1px solid var(--line);
  font-size:12.5px;color:var(--ink2)}
.lsc .fighead .who{margin-left:auto;font-size:11.5px;color:var(--ink3);white-space:nowrap}
.lsc .fighead .chip{font-size:11px;padding:2px 9px;border-radius:999px;white-space:nowrap}
.lsc .chip.full{color:var(--amber);background:rgba(251,191,36,.14)}
.lsc .chip.pub{color:var(--green);background:rgba(52,211,153,.14)}
.lsc .figwrap iframe{display:block;width:100%;border:0;background:transparent}
.lsc .seg{display:inline-flex;background:rgba(255,255,255,.04);border:1px solid var(--line2);
  border-radius:999px;padding:3px}
.lsc .seg span{padding:5px 14px;border-radius:999px;font-size:12px;color:var(--ink3);cursor:pointer;
  white-space:nowrap;transition:all var(--tr)}
.lsc .seg span:hover{color:var(--ink2)}
.lsc .seg span.on{background:var(--brand);color:#fff;font-weight:700}
.lsc .mapbox{padding:16px}
.lsc svg.map{display:block;width:100%;min-width:900px;height:auto;color:var(--ink2)}
.lsc .mapscroll{overflow-x:auto}
.lsc .figfoot{padding:9px 16px;border-top:1px solid var(--line);font-size:11.5px;color:var(--ink3);
  display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.lsc .figfoot a{color:var(--brand2);text-decoration:none}
.lsc .figfoot a:hover{text-decoration:underline}

.lsc h3.sec{margin:38px 0 4px;font-size:16px;font-weight:800}
.lsc .lede{margin:0 0 14px;font-size:12.5px;color:var(--ink3)}
.lsc .finds{display:grid;gap:12px;grid-template-columns:1fr 1fr}
@media(max-width:900px){.lsc .finds{grid-template-columns:1fr}}
.lsc .find{padding:16px 18px;border:1px solid var(--line);border-radius:14px;background:var(--card)}
.lsc .find.alert{border-color:rgba(224,133,133,.45);background:rgba(224,133,133,.07)}
.lsc .find .tag{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--brand2)}
.lsc .find.alert .tag{color:#E08585}
.lsc .find h4{margin:5px 0 7px;font-size:14.5px;font-weight:800}
.lsc .find p{margin:0;font-size:12.5px;color:var(--ink2);line-height:1.9}

.lsc ol.steps{list-style:none;margin:0;padding:0;display:grid;gap:10px;counter-reset:s}
.lsc ol.steps li{counter-increment:s;position:relative;padding:14px 18px 14px 50px;
  border:1px solid var(--line);border-radius:12px;background:var(--card)}
.lsc ol.steps li::before{content:counter(s);position:absolute;left:18px;top:14px;
  font-size:14px;font-weight:800;color:var(--brand2)}
.lsc ol.steps b{display:block;font-size:13.5px;margin-bottom:3px}
.lsc ol.steps span{font-size:12.5px;color:var(--ink2);line-height:1.85}

.lsc .note{margin-top:30px;padding:14px 16px;border:1px dashed var(--line2);border-radius:12px;
  font-size:12px;color:var(--ink3);line-height:1.9}
.lsc .tech{margin-top:26px}
.lsc .techtbl{width:100%;border-collapse:collapse;font-size:12.5px}
.lsc .techtbl th{text-align:left;padding:9px 12px;font-size:11px;letter-spacing:.06em;
  color:var(--ink3);background:rgba(255,255,255,.03);font-weight:700}
.lsc .techtbl td{padding:10px 12px;border-top:1px solid var(--line);color:var(--ink2);vertical-align:top}
.lsc .techtbl td.n{color:var(--ink);font-weight:700;white-space:nowrap}
.lsc .box{border:1px solid var(--line);border-radius:14px;background:var(--card);overflow:hidden}
.lsc .empty{padding:40px;text-align:center;color:var(--ink3);font-size:13px}
`

export default function PortalLandscape({ onBack }) {
  const [data, setData] = useState(null)
  const [tech, setTech] = useState(false)
  const [showTech, setShowTech] = useState(false)
  const [tall, setTall] = useState(false)      // 图放大到接近整屏
  // 两个视角互补：deploy=部署架构（上传的 archify 导出件，回答「怎么跑的」，只画财务侧）
  //              map   =系统全景（本地 SVG，回答「有哪些系统、卡在哪」，含商务线与存储层）
  const [view, setView] = useState('deploy')
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    getLandscape().then(r => {
      if (!alive) return
      if (r && r.ok) { setData(r.landscape); setTech(!!r.tech) }
      else setErr((r && r.msg) || '读取失败')
    }).catch(() => alive && setErr('读取失败'))
    return () => { alive = false }
  }, [])

  if (err) return <div className="lsc"><style>{CSS}</style><div className="empty">{err}</div></div>
  if (!data) return <div className="lsc"><style>{CSS}</style><div className="empty">加载中…</div></div>

  const hasTechText = (data.nodes || []).some(n => (n.tech || '').trim())

  return (
    <div className="lsc">
      <style>{CSS}</style>

      <div className="top">
        <div>
          <h2>{data.title || '系统架构'}</h2>
          <p className="sf">{data.standfirst}</p>
        </div>
        <div className="tools">
          {tech && hasTechText && (
            <span className={'tgl' + (showTech ? ' on' : '')} onClick={() => setShowTech(v => !v)}>
              <i />技术细节
            </span>
          )}
          <span className="back" onClick={onBack}>← 返回工作台门户</span>
        </div>
      </div>

      {data.verdict && (
        <div className="verdict"><b>一句话结论</b><p>{data.verdict}</p></div>
      )}

      {/* ── 上半：架构图 ── */}
      <div className="figwrap">
        <div className="fighead">
          <span className="seg">
            <span className={view === 'deploy' ? 'on' : ''} onClick={() => setView('deploy')}>部署架构</span>
            <span className={view === 'map' ? 'on' : ''} onClick={() => setView('map')}>系统全景</span>
          </span>
          <span style={{ color: 'var(--ink3)', fontSize: 11.5 }}>
            {view === 'deploy'
              ? '怎么跑的 —— 服务器 · 端口 · 反代 · 外部接口；图内可切视图 / 透镜 / 路径 / 地图'
              : '有哪些系统、卡在哪 —— 含商务线与存储层'}
          </span>
          {view === 'deploy' && (
            <span className={'chip ' + (tech ? 'full' : 'pub')}>{tech ? '完整版' : '对外版'}</span>
          )}
          <span className="who">
            {view === 'deploy' && (
              <span className="tgl" style={{ padding: '3px 10px' }} onClick={() => setTall(v => !v)}>
                {tall ? '缩小' : '放大'}
              </span>
            )}
          </span>
        </div>

        {/* 部署架构：iframe 嵌上传件。src 不带 slot——给完整版还是脱敏版由服务端按权限定，前端说了不算。
            theme=dark 是这个查看器自带的参数，跟门户深色底对齐；不加 embed=1，那会把引导视图藏掉。 */}
        {view === 'deploy' ? (
          <iframe title="部署架构图" src="/api/portal/landscape/diagram?theme=dark"
                  style={{ height: tall ? '82vh' : '560px' }} />
        ) : (
          <div className="mapbox mapscroll"><PortalLandscapeMap nodes={data.nodes} /></div>
        )}

        <div className="figfoot">
          {view === 'deploy' ? (<>
            <span>{tech
              ? '⚠ 完整版含服务器地址与端口 —— 对外演示前请切到对外版或收起本图。'
              : '本图为对外脱敏版。'}</span>
            <a href="/api/portal/landscape/diagram?theme=dark" target="_blank" rel="noreferrer">在新标签页打开 ↗</a>
          </>) : (
            <span>本图为对内盘点视角，与左侧「部署架构」互补：那张只画财务侧，这张补上商务线与存储层。</span>
          )}
        </div>
      </div>

      {/* 技术细节（仅授权且点开） */}
      {tech && showTech && hasTechText && (
        <div className="tech">
          <h3 className="sec">技术细节</h3>
          <p className="lede">补充上图未覆盖的系统（含商务线）及其技术栈与存储，仅授权账号可见——对外介绍时请收起本区块。</p>
          <div className="box">
            <table className="techtbl">
              <thead><tr><th style={{ width: '190px' }}>系统</th><th>技术栈 / 存储 / 端口</th></tr></thead>
              <tbody>
                {(data.nodes || []).filter(n => (n.tech || '').trim()).map(n => (
                  <tr key={n.key}><td className="n">{n.name}</td><td>{n.tech}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── 下半：图不回答的部分 ── */}
      {(data.findings || []).length > 0 && (<>
        <h3 className="sec">几条发现</h3>
        <p className="lede">上面那张图讲「怎么跑的」；这里讲「所以下一步该干什么」。</p>
        <div className="finds">
          {data.findings.map((f, i) => (
            <div key={i} className={'find' + (f.alert ? ' alert' : '')}>
              <div className="tag">{f.tag}</div>
              <h4>{f.title}</h4>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </>)}

      {(data.steps || []).length > 0 && (<>
        <h3 className="sec">建议顺序</h3>
        <p className="lede">每一步都能单独验证价值，不用等全部做完才见效。</p>
        <ol className="steps">
          {data.steps.map((s, i) => (
            <li key={i}><b>{s.title}</b><span>{s.body}</span></li>
          ))}
        </ol>
      </>)}

      {data.note && <div className="note"><b>这张图的边界。</b>{data.note}</div>}
    </div>
  )
}
