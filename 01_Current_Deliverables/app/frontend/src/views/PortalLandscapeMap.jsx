// [Change Log] Date:2026-09-07 Author:Claude/Reginald Zeng Version:V2.514
// 「系统全景」视角：源头 → 自建 → 底座 → 存储 的资产盘点图。
//
// 它与「部署架构」视角（上传的 archify 导出件）**互补，不重复**：
//   · 部署架构 回答「怎么跑的」——服务器、端口、反代、权限头注入、外部 API。只画财务侧。
//   · 本图     回答「有哪些系统、卡在哪」——**含商务线**（星桥智联 / BOM 价管 / 销售报价接单）、
//              存储层横向对比、以及那条靠解析 Excel 才走得通的脆弱链路。
//
// 图的结构（坐标、连线）写死在下面的 LAYOUT / EDGES；节点的名称/状态/版本/说明来自
// /api/portal/landscape，在「门户管理 › 系统架构」里改。LAYOUT 里没有的 key 忽略，
// 有 key 而数据缺的画成灰色占位——后端加节点不会把前端画崩。
//
// 走线原则（踩过坑，别退回去）：连往存储的线一律走**框外左右走廊**，不要从框内直下——
// 那会穿过工作台方框和网关横条，看着像连错了。
import React from 'react'

const LAYOUT = {
  starblend:  { x: 44,  y: 30,  w: 168, h: 46, band: 'src' },
  proto:      { x: 224, y: 30,  w: 132, h: 46, band: 'src' },
  jde:        { x: 368, y: 30,  w: 116, h: 46, band: 'src' },
  dingtalk:   { x: 496, y: 30,  w: 132, h: 46, band: 'src' },
  kingdee:    { x: 640, y: 30,  w: 116, h: 46, band: 'src' },
  starbridge: { x: 44,  y: 150, w: 196, h: 62, band: 'biz' },
  bomprice:   { x: 260, y: 150, w: 172, h: 62, band: 'biz' },
  salesquote: { x: 452, y: 150, w: 168, h: 62, band: 'biz' },
  acc:        { x: 60,  y: 260, w: 184, h: 58, band: 'fin' },
  bp:         { x: 262, y: 260, w: 184, h: 58, band: 'fin' },
  legal:      { x: 464, y: 260, w: 152, h: 58, band: 'fin' },
  pricer:     { x: 634, y: 260, w: 152, h: 58, band: 'fin' },
  gateway:    { x: 240, y: 358, w: 360, h: 44, band: 'base' },
  mysql:      { x: 44,  y: 470, w: 176, h: 52, band: 'store' },
  sqlite:     { x: 240, y: 470, w: 196, h: 52, band: 'store' },
  dtdata:     { x: 456, y: 470, w: 152, h: 52, band: 'store' },
  excel:      { x: 628, y: 470, w: 152, h: 52, band: 'store' },
}

// [起, 终, 路径, 标注, 类型, 标注坐标?]  solid=已实现 dash=规划未实现 weak=脆弱
const EDGES = [
  ['starblend', 'starbridge', 'M128 76 L128 150',                                   'BOM 发布（未实现）', 'dash', [134, 100]],
  ['proto',     'starbridge', 'M290 76 L290 112 L176 112 L176 150',                 '手工样编码',         'dash', [296, 100]],
  ['jde',       'starbridge', 'M426 76 L426 130 L216 130 L216 150',                 '物料码 JOIN',        'solid', [432, 100]],
  ['dingtalk',  'pricer',     'M562 76 L562 108 L880 108 L880 280 L786 280',        '只读审批单',         'solid', [568, 100]],
  ['kingdee',   'acc',        'M698 76 L698 96 L930 96 L930 224 L152 224 L152 260', '取数 / 回写',        'solid', [704, 92]],
  ['starbridge','mysql',      'M100 212 L100 228 L22 228 L22 446 L110 446 L110 470', '',                  'solid'],
  ['bomprice',  'mysql',      'M320 212 L320 240 L36 240 L36 430 L164 430 L164 470', '隧道',              'solid', [42, 426]],
  ['acc',       'sqlite',     'M152 318 L152 428 L300 428 L300 470',                '',                   'solid'],
  ['bp',        'sqlite',     'M354 318 L354 344 L216 344 L216 452 L340 452 L340 470', '',                'solid'],
  ['pricer',    'dtdata',     'M710 318 L710 452 L532 452 L532 470',                '',                   'solid'],
  ['fin',       'gateway',    'M420 332 L420 358',                                  'AI 能力统一出口',     'solid', [428, 350]],
  ['excel',     'pricer',     'M704 470 L704 440 L744 440 L744 318',                '解析附件建成本台账', 'weak', [752, 436]],
]

const BAND_LABEL = [
  { y: 56, t: '源头' }, { y: 186, t: '自建' }, { y: 386, t: '底座' }, { y: 498, t: '存储' },
]

const SST = {
  ok:    { c: 'var(--green)', t: '在跑' },
  par:   { c: 'var(--amber)', t: '人工并行' },
  beta:  { c: 'var(--brand2)', t: '开发中' },
  soon:  { c: 'var(--gray)',  t: '规划中' },
  empty: { c: '#E08585',      t: '空壳' },
  weak:  { c: '#E08585',      t: '' },
  ext:   { c: '',             t: '' },
}

export default function PortalLandscapeMap({ nodes }) {
  const byKey = {}
  for (const n of (nodes || [])) byKey[n.key] = n

  return (
    <svg className="map" viewBox="0 0 1000 660" role="img"
         aria-label="系统全景图：源头系统流向自建系统，再落到各类存储。">
      <defs>
        <marker id="lsAr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
                orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="currentColor" /></marker>
        <marker id="lsArR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
                orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#E08585" /></marker>
      </defs>

      {BAND_LABEL.map(b => (
        <text key={b.t} x="6" y={b.y} fontSize="11" fill="currentColor" opacity=".5">{b.t}</text>
      ))}

      <rect x="46" y="248" width="754" height="84" rx="14" fill="none"
            stroke="var(--brand)" strokeWidth="1.2" strokeDasharray="6 5" opacity=".6" />
      <text x="560" y="241" fontSize="11" fill="var(--brand2)">风控中心 AI 赋能中台 · 已有的集成门户</text>

      {EDGES.map(([a, b, d, label, kind], i) => {
        const weak = kind === 'weak'
        return (
          <path key={i} d={d} fill="none"
                stroke={weak ? '#E08585' : 'currentColor'}
                strokeWidth={weak ? 2 : 1.3}
                strokeDasharray={kind === 'dash' ? '5 4' : undefined}
                opacity={weak ? 1 : .6}
                markerEnd={weak ? 'url(#lsArR)' : 'url(#lsAr)'} />
        )
      })}

      {Object.entries(LAYOUT).map(([key, g]) => {
        const n = byKey[key]
        const missing = !n
        const st = SST[(n && n.status) || 'ext'] || SST.ext
        const strong = n && (n.status === 'ok' || n.status === 'weak')
        const stroke = missing ? 'currentColor'
          : n.status === 'weak' ? '#E08585'
            : key === 'gateway' ? 'var(--ai)' : 'currentColor'
        const cx = g.x + g.w / 2
        const tall = g.h > 50
        const line2 = missing ? '' : (n.sub || '')
        const stTxt = missing ? '' : (st.t ? (st.t + (n.ver ? ' · ' + n.ver : '')) : (n.ver || ''))
        return (
          <g key={key} opacity={missing ? .35 : 1}>
            <rect x={g.x} y={g.y} width={g.w} height={g.h} rx="9" fill="none"
                  stroke={stroke} strokeWidth={strong ? 1.8 : 1.2}
                  opacity={g.band === 'src' || g.band === 'store' ? .6 : 1} />
            <text x={cx} y={g.y + (tall ? 22 : 19)} fontSize="12.5" textAnchor="middle"
                  fill="currentColor" fontWeight="700">{missing ? key : n.name}</text>
            {/* 矮节点只有两行位：说明与状态并成一行，否则第二三行会压在一起 */}
            {!missing && !tall && (line2 || stTxt) && (
              <text x={cx} y={g.y + 35} fontSize="10.5" textAnchor="middle">
                {line2 && <tspan fill="currentColor" opacity=".62">{line2}</tspan>}
                {line2 && stTxt && <tspan fill="currentColor" opacity=".4">{'　·　'}</tspan>}
                {stTxt && <tspan fill={st.c || 'currentColor'} fontWeight="600">{stTxt}</tspan>}
              </text>
            )}
            {!missing && tall && (<>
              {n.sub && <text x={cx} y={g.y + 39} fontSize="10.5" textAnchor="middle"
                              fill="currentColor" opacity=".62">{n.sub}</text>}
              {stTxt && <text x={cx} y={g.y + g.h - 8} fontSize="10" textAnchor="middle"
                              fill={st.c || 'currentColor'} fontWeight={st.t ? 600 : 400}
                              opacity={st.t ? 1 : .5}>{stTxt}</text>}
            </>)}
          </g>
        )
      })}

      {/* 标注画在节点之上，避免被框盖住 */}
      {EDGES.filter(e => e[3]).map(([a, b, d, label, kind, at], i) => {
        const m = /M(\d+)\s+(\d+)/.exec(d) || []
        const lx = at ? at[0] : Number(m[1]) + 6
        const ly = at ? at[1] : Number(m[2]) + 22
        return (
          <text key={'l' + i} x={lx} y={ly} fontSize="10"
                fill={kind === 'weak' ? '#E08585' : 'currentColor'}
                opacity={kind === 'weak' ? 1 : .62}
                fontWeight={kind === 'weak' ? 600 : 400}>{label}</text>
        )
      })}

      <g fontSize="10.5">
        <line x1="838" y1="160" x2="866" y2="160" stroke="currentColor" strokeWidth="1.3" opacity=".6" />
        <text x="874" y="164" fill="currentColor" opacity=".6">已实现</text>
        <line x1="838" y1="182" x2="866" y2="182" stroke="currentColor" strokeWidth="1.3"
              strokeDasharray="5 4" opacity=".6" />
        <text x="874" y="186" fill="currentColor" opacity=".6">规划未实现</text>
        <line x1="838" y1="204" x2="866" y2="204" stroke="#E08585" strokeWidth="2" />
        <text x="874" y="208" fill="#E08585">脆弱环节</text>
      </g>
    </svg>
  )
}
