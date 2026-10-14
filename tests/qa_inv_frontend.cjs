// [Change Log] Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家·审查修复）| 发票管家前端回归（浏览器夹具）
// [Change Log] Date: 2026-09-25 | Author: Claude / c | Version: V2.621 | 加申请人自助登记（#/invself）5 条：验证码登录→登记、重名、钉钉免登、手机配对先鉴权再免登、后补池入口
// 只用合成数据：接口全部在浏览器里拦截回假数据，不连后端、不发钉钉、不碰生产。
// 用法（仓库根目录）：node tests/qa_inv_frontend.cjs            —— 测工作区里的前端源码
//                    INV_FE_SRC=<另一份 frontend/src> node ...   —— 测别的版本（例如修复前的 HEAD，用来确认用例"修前红、修后绿"）
//                    INV_FE_ONLY=F1,C2 node ...                —— 只跑名字里含这些字样的用例
// 依赖：前端 node_modules 里的 esbuild/react（打包夹具）＋ Playwright（PLAYWRIGHT_PATH 可改路径；默认用本机 codex 运行时自带的那份）。
'use strict'
const path = require('path')
const fs = require('fs')
const os = require('os')
const assert = require('node:assert/strict')

const ROOT = path.resolve(__dirname, '..')
const FE = path.join(ROOT, '01_Current_Deliverables', 'app', 'frontend')
const SRC = path.resolve(process.env.INV_FE_SRC || path.join(FE, 'src'))
const PW = process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
const ONLY = (process.env.INV_FE_ONLY || '').split(',').map(s => s.trim()).filter(Boolean)
const ORIGIN = 'http://localhost:9'   // 不真起服务：页面和接口全由 page.route 拦截回应（localhost 算安全上下文，摄像头可用假设备）

const esbuild = require(path.join(FE, 'node_modules', 'esbuild'))
const { chromium } = require(PW)

// 1×1 PNG（看图器/缩略图要能 onLoad）
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')

const ENTRY = `
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import * as S from '%SRC%/views/invShared.jsx'
import InvDesk from '%SRC%/views/InvDesk.jsx'
import InvAudit from '%SRC%/views/InvAudit.jsx'
import InvLedger from '%SRC%/views/InvLedger.jsx'
import InvLater from '%SRC%/views/InvLater.jsx'
import InvSettings from '%SRC%/views/InvSettings.jsx'
import InvPhone from '%SRC%/views/InvPhone.jsx'
import InvSelf from '%SRC%/views/InvSelf.jsx'
import '%SRC%/styles.css'

function ViewerSwitch({ items }) {
  const [i, setI] = useState(0)
  return <div>
    <button id="qa-next" type="button" onClick={() => setI(x => Math.min(x + 1, items.length - 1))}>下一张</button>
    <S.InvViewer item={items[i]} height={320} />
  </div>
}
function StickyModal() {
  return <div style={{ display: 'flex' }}>
    <aside id="qa-aside" style={{ position: 'sticky', top: 0, zIndex: 20, width: 200, height: 400 }}>侧栏</aside>
    <div id="qa-sticky" style={{ position: 'sticky', top: 0 }}>
      <S.Modal title="弹窗" onClose={() => {}}><p>正文</p></S.Modal>
    </div>
  </div>
}
function ScanHarness() {
  return <div style={{ padding: 20 }}>
    <S.ScanInput keepFocus onScan={() => {}} />
    <p id="qa-copy" style={{ fontSize: 16 }}>审批编号 202609240001</p>
    <button id="qa-btn" type="button">别的按钮</button>
  </div>
}
function PairHarness(p) {
  const [open, setOpen] = useState(true)
  return open ? <S.PairModal {...p} onClose={() => setOpen(false)} /> : <div id="qa-closed">已关</div>
}
const VIEWS = { ViewerSwitch, StickyModal, ScanHarness, PairHarness, InvDesk, InvAudit, InvLedger, InvLater, InvSettings, InvPhone, InvSelf }
window.__mount = (name, props) => {
  const root = createRoot(document.getElementById('root'))
  root.render(React.createElement(VIEWS[name], props || {}))
}
`
const HTML = '<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/entry.css"></head>' +
  '<body><div id="root"></div><script src="/entry.js"></script></body></html>'

async function bundle() {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'inv-fe-qa-'))
  const entry = path.join(out, 'entry.jsx')
  fs.writeFileSync(entry, ENTRY.split('%SRC%').join(SRC.replace(/\\/g, '/')))
  await esbuild.build({
    entryPoints: [entry], bundle: true, outdir: out, format: 'iife', jsx: 'automatic', logLevel: 'error',
    loader: { '.js': 'jsx', '.jsx': 'jsx', '.png': 'empty', '.svg': 'empty', '.jpg': 'empty', '.woff': 'empty', '.woff2': 'empty', '.ttf': 'empty' },
    nodePaths: [path.join(FE, 'node_modules')], absWorkingDir: FE,
    define: { 'process.env.NODE_ENV': '"development"' },
  })
  const css = path.join(out, 'entry.css')
  return { js: fs.readFileSync(path.join(out, 'entry.js'), 'utf8'), css: fs.existsSync(css) ? fs.readFileSync(css, 'utf8') : '' }
}

function parseBody(req) {
  const raw = req.postData()
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return raw }
}

// 新开一个干净的页面（独立 context：localStorage/sessionStorage 互不串）
async function open(browser, B, api, { hash = '', clock = false, ua = '', init = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ['camera'], ...(ua ? { userAgent: ua } : {}) })
  const page = await ctx.newPage()
  if (init) await page.addInitScript(init)
  const calls = [], errors = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('dialog', d => d.accept())
  if (clock) await page.clock.install({ time: new Date('2026-09-24T10:00:00+08:00') })
  await page.route(ORIGIN + '/**', async route => {
    const req = route.request()
    const u = new URL(req.url())
    if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: HTML })
    if (u.pathname === '/entry.js') return route.fulfill({ contentType: 'application/javascript', body: B.js })
    if (u.pathname === '/entry.css') return route.fulfill({ contentType: 'text/css', body: B.css })
    if (u.pathname.startsWith('/img/') || u.pathname.startsWith('/api/inv/file/')) return route.fulfill({ contentType: 'image/png', body: PNG })
    if (u.pathname.startsWith('/api/')) {
      const call = { method: req.method(), path: u.pathname, query: Object.fromEntries(u.searchParams), headers: req.headers(), body: parseBody(req) }
      calls.push(call)
      let res
      try { res = await api(call) } catch (e) { res = { __status: 500, ok: false, msg: String(e) } }
      if (res === undefined) res = { ok: true }
      let status = 200
      if (res && res.__status) { status = res.__status; res = { ...res }; delete res.__status }
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(res) })
    }
    return route.fulfill({ status: 404, body: '' })
  })
  await page.goto(ORIGIN + '/' + hash)
  return { page, ctx, calls, errors }
}
const mount = (page, name, props) => page.evaluate(([n, p]) => window.__mount(n, p), [name, props || {}])
const n = (calls, pred) => calls.filter(pred).length
const sleep = ms => new Promise(r => setTimeout(r, ms))

// ───────────────────────── 合成夹具 ─────────────────────────

const CAN_ALL = { desk: true, later: true, audit: true, ledger: true, intake: true, receive: true, auditAct: true, deduct: true, unbind: true, opening: true, config: true }
const cfg = (can = {}) => ({
  ok: true, can: { ...CAN_ALL, ...can }, me: { name: '测试会计', isSuper: false },
  receivers: [{ account: '张三', dtName: '张三' }],
  settings: { company: [], templates: [], laterTemplates: [], remind: {}, blockNoInvoice: false }, dingtalk: false,
})
const inv = (o) => ({
  kind: 'invoice', review: 'pending', invType: 'special', typeLabel: '增值税专用发票', number: '26442000000000000' + String(o.id).padStart(3, '0'),
  date: '2026-09-01', sellerName: '合成销方有限公司', amount: 88.5, tax: 11.5, total: 100, taxRate: '13%', category: '办公用品',
  deductSuggest: 'yes', deductReason: '专票、办公用品', deductible: null, fieldSrc: {}, pending: [], flags: {}, procStatus: 'done',
  file: null, page: 0, lines: [], ...o,
})

// ───────────────────────── 用例 ─────────────────────────

const TESTS = []
const test = (name, fn) => TESTS.push({ name, fn })

test('F1/C8 看图器从这张票所在的页打开，换票跟着换页；那页没预览图时明说', async (browser, B) => {
  const file = { id: 9, name: '合并.pdf', pages: 3, preview: ['/img/p0.png', '/img/p1.png', '/img/p2.png'], thumb: '/img/t.png', orig: '/api/inv/file/9?v=o' }
  const items = [{ id: 1, page: 2, file }, { id: 2, page: 0, file }, { id: 3, page: 6, file }]
  const { page, ctx, errors } = await open(browser, B, () => ({ ok: true }))
  await mount(page, 'ViewerSwitch', { items })
  const img = page.locator('.inv-vw-wrap img')
  await img.waitFor({ state: 'attached' })
  assert.match(await img.getAttribute('src'), /p2\.png$/, '第 3 张票（item.page=2）应显示第 3 页')
  assert.ok(await page.getByText('第 3 / 3 页').isVisible())
  await page.click('#qa-next')
  await page.waitForFunction(() => /p0\.png$/.test(document.querySelector('.inv-vw-wrap img')?.getAttribute('src') || ''))
  await page.click('#qa-next')
  await page.getByText('原件第 7 页没有生成预览图', { exact: false }).waitFor({ timeout: 3000 })
  assert.equal(await page.locator('.inv-vw-wrap img').count(), 0, '没有预览图的页不能拿别的页顶替')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('F13 弹窗挂在 body 上，不被 sticky 容器困住', async (browser, B) => {
  const { page, ctx } = await open(browser, B, () => ({ ok: true }))
  await mount(page, 'StickyModal')
  await page.locator('.inv-ov').waitFor()
  assert.equal(await page.evaluate(() => document.querySelector('.inv-ov').parentElement === document.body), true)
  assert.equal(await page.evaluate(() => document.getElementById('qa-sticky').contains(document.querySelector('.inv-ov'))), false)
  await ctx.close()
})

test('F16 扫码框不抢焦点：页面有选中文字时不动，选区清掉后才拿回', async (browser, B) => {
  const { page, ctx } = await open(browser, B, () => ({ ok: true }))
  await mount(page, 'ScanHarness')
  await page.waitForFunction(() => document.activeElement && document.activeElement.dataset.invScan === '1')
  await page.evaluate(() => {
    document.activeElement.blur()
    const r = document.createRange(); r.selectNodeContents(document.getElementById('qa-copy'))
    const s = getSelection(); s.removeAllRanges(); s.addRange(r)
  })
  await sleep(1600)   // 失焦 180ms 抢一次 + 每秒兜底一次，都应让路
  assert.equal(await page.evaluate(() => String(getSelection())), '审批编号 202609240001', '选中的审批编号不能被清掉')
  assert.notEqual(await page.evaluate(() => document.activeElement && document.activeElement.dataset.invScan), '1')
  await page.evaluate(() => getSelection().removeAllRanges())
  await page.waitForFunction(() => document.activeElement && document.activeElement.dataset.invScan === '1', null, { timeout: 2500 })
  await ctx.close()
})

test('F5 已连着手机时打开「手机当相机」只看状态、不生成新码；点「换一部手机」才生成', async (browser, B) => {
  const api = (c) => (c.path === '/api/inv/pair' ? { ok: true, pair: { id: 6, expiresIn: 600 }, qr: '/img/qr.png', url: 'https://x/#/invpair?t=abc' } : { ok: true })
  const { page, ctx, calls } = await open(browser, B, api)
  await mount(page, 'PairHarness', { pairState: { id: 5, bound: true, dtName: '王五', device: 'iPhone', active: true } })
  await page.getByText('王五', { exact: false }).waitFor()
  await sleep(600)
  assert.equal(n(calls, c => c.path === '/api/inv/pair'), 0, '打开弹窗不能悄悄生成新码（会把正在用的手机踢掉）')
  await page.getByRole('button', { name: '换一部手机' }).click()
  await page.locator('.inv-pair-qr img').waitFor()
  assert.equal(n(calls, c => c.path === '/api/inv/pair' && c.method === 'POST'), 1)
  await ctx.close()
})

function deskApi(state) {
  return (c) => {
    if (c.path === '/api/inv/config') return cfg(state.can || {})
    if (c.path === '/api/inv/desk') return { ok: true, folder: state.folder || null, items: state.items || [], recent: state.recent || [], pair: state.pair || null }
    if (c.path === '/api/inv/desk/open') { state.folder = state.openFolder; state.items = state.openItems || []; return { ok: true, folder: state.folder, items: state.items } }
    if (c.path === '/api/inv/desk/close') { state.folder = null; state.items = []; return { ok: true } }
    if (c.path.endsWith('/submit')) return state.submit
    return { ok: true }
  }
}
async function countDeskPolls(browser, B, pair, seconds) {
  const { page, ctx, calls } = await open(browser, B, deskApi({ can: { intake: false }, pair }), { clock: true })
  await mount(page, 'InvDesk', { user: { name: '测试会计' } })
  await page.waitForFunction(() => document.body.innerText.includes('收票工作台'))
  await sleep(150)
  const start = n(calls, c => c.path === '/api/inv/desk')
  for (let t = 0; t < seconds * 2; t += 1) { await page.clock.runFor(500); await sleep(25) }
  const got = n(calls, c => c.path === '/api/inv/desk') - start
  await ctx.close()
  return got
}

test('C4/GOV-1 手机连着但闲着：收票台 10 秒一轮，不再 1.5 秒一轮', async (browser, B) => {
  const got = await countDeskPolls(browser, B, { id: 5, bound: true, active: false, dtName: '王五', lastSeen: '2026-09-24 09:30:00' }, 9)
  assert.ok(got <= 1, `闲着的 9 秒里拉了 ${got} 次 /api/inv/desk（应 ≤1）`)
})

test('C4 手机正在用（active）：1.5 秒一轮', async (browser, B) => {
  const got = await countDeskPolls(browser, B, { id: 5, bound: true, active: true, dtName: '王五' }, 6)
  assert.ok(got >= 3, `手机在用的 6 秒里只拉了 ${got} 次（应 ≥3）`)
})

test('F6 配对码已生成、弹窗提前关了：照样轮询等手机连上', async (browser, B) => {
  const got = await countDeskPolls(browser, B, { id: 5, bound: false, expiresIn: 500 }, 21)
  assert.ok(got >= 2, `21 秒里只拉了 ${got} 次（弹窗关了也要等到连上）`)
})

test('F8 提交被拦：按 itemIds 给「去看这张」并能打开那张票', async (browser, B) => {
  const folder = { id: 20, status: 'collecting', title: '差旅报销', amount: 100, stats: {} }
  const state = {
    folder, items: [inv({ id: 201, review: 'draft', flags: { dup: { folderId: 3, title: '别的单' } } })], pair: null,
    submit: { __status: 400, ok: false, msg: '有 1 张重复票，不能提交', blockers: [{ code: 'dup', itemIds: [201], msg: '重复票不能挂在这张单上' }] },
  }
  const { page, ctx } = await open(browser, B, deskApi(state))
  await mount(page, 'InvDesk', { user: { name: '测试会计' } })
  await page.getByRole('button', { name: '提交', exact: true }).click()
  await page.getByText('重复票不能挂在这张单上').waitFor()
  await page.getByText('去看这张').click()
  await page.locator('.inv-mtitle', { hasText: '第 1 张' }).waitFor()
  await ctx.close()
})

test('F14/C6 只有查看权限：能打开「我的票夹」里的单子看（只读，标「只能看」），也能收起', async (browser, B) => {
  const state = {
    can: { intake: false }, pair: null, recent: [{ id: 40, title: '查看用票夹', status: 'submitted', amount: 50 }],
    openFolder: { id: 40, status: 'submitted', title: '查看用票夹', amount: 50, stats: {} }, openItems: [inv({ id: 401 })],
  }
  const { page, ctx, calls } = await open(browser, B, deskApi(state))
  await mount(page, 'InvDesk', { user: { name: '只读' } })
  await page.getByRole('button', { name: /查看用票夹/ }).click()
  await page.locator('.inv-dk-fhead').waitFor()
  assert.equal(n(calls, c => c.path === '/api/inv/desk/open' && c.body && c.body.folderId === 40), 1)
  assert.ok(await page.getByText('只能看', { exact: true }).isVisible(), '只读打开要标明「只能看」')
  assert.equal(await page.getByRole('button', { name: '选文件' }).count(), 0, '只读不能往里放票')
  assert.equal(await page.getByRole('button', { name: '提交', exact: true }).count(), 0, '只读不能提交')
  await page.getByRole('button', { name: '收起', exact: true }).click()
  await page.getByText('在左边「我的票夹」里点一个票夹查看').waitFor({ timeout: 3000 })
  assert.equal(n(calls, c => c.path === '/api/inv/desk/close'), 1)
  await ctx.close()
})

test('收票台：有差额（付款−专票−普票−后补≠0）提交前先确认，提交后审核会重点关注', async (browser, B) => {
  const state = {
    pair: null, recent: [],
    folder: { id: 50, status: 'collecting', title: '差额单', amount: 100, stats: { invoices: 1 },
      gap: { pay: 100, special: 90, specialN: 1, normal: 0, normalN: 0, later: 0, gap: 10 } },
    items: [inv({ id: 501, review: 'draft', total: 90 })],
    submit: { ok: true, msg: '已提交，等会计审核', folder: { id: 50, status: 'submitted', title: '差额单' }, items: [], warnings: [] },
  }
  const { page, ctx, calls } = await open(browser, B, deskApi(state))
  await mount(page, 'InvDesk', { user: { name: '测试收票员' } })
  await page.getByText('票比付款少，提交后审核会重点关注').waitFor()
  await page.getByRole('button', { name: '提交', exact: true }).click()
  await page.getByText('提交后会计会重点审核', { exact: false }).waitFor()
  assert.equal(n(calls, c => c.path.endsWith('/submit')), 0, '先确认，不能直接提交')
  await page.getByRole('button', { name: '确定提交' }).click()
  await page.waitForFunction(() => true)
  await sleep(300)
  assert.equal(n(calls, c => c.path.endsWith('/submit')), 1)
  await ctx.close()
})

// 审票分工：夹具里的票夹默认「归我审」（老用例不关心分工）；用例自己给了 owner 的照它的
const OWN_ME = { assigned: true, name: '测试会计', mine: true, canAct: true, note: '', via: 'intake', at: '2026-09-25 09:00:00', by: '系统' }
function auditApi(st) {
  return (c) => {
    if (c.path === '/api/inv/config') return cfg(st.can || {})
    if (c.path === '/api/inv/audit/queue') {
      st.scopes = (st.scopes || []).concat([(c.query && c.query.scope) || ''])
      st.sources = (st.sources || []).concat([(c.query && c.query.source) || ''])
      st.amounts = (st.amounts || []).concat([(c.query && c.query.amount) || ''])
      return { ok: true, total: st.queue.length, rows: st.queue.map(r => ({ owner: OWN_ME, ...r })) }
    }
    if (c.path === '/api/inv/audit/backlog') return st.backlog || { ok: true, canAssign: false, people: [{ key: 'a:测试会计', name: '测试会计', mine: true, count: st.queue.length, oldest: '2026-09-20 10:00:00', days: 5, note: '' }], unassigned: { count: 0, oldest: '', days: null }, total: st.queue.length }
    if (c.path === '/api/inv/audit/people') return { ok: true, people: [{ account: '测试会计', name: '测试会计' }, { account: '李四', name: '李四' }, { account: '王五', name: '王五' }], me: '测试会计', meCanAudit: true }
    if (c.path === '/api/inv/audit/claim') return st.onClaim(c)
    if (c.path === '/api/inv/audit/assign') return st.onAssign(c)
    if (c.path === `/api/inv/folder/${st.folder.id}`) { st.folderGets = (st.folderGets || 0) + 1; return { ok: true, folder: { owner: OWN_ME, ...st.folder }, items: st.items, logs: st.logs || [] } }
    if (/^\/api\/inv\/item\/\d+\/update$/.test(c.path)) return st.onUpdate(c)
    const mk = c.path.match(/^\/api\/inv\/audit\/item\/(\d+)\/mark$/)
    if (mk) {
      const id = +mk[1], b = c.body || {}
      st.items = st.items.map(it => it.id !== id ? it : {
        ...it, pending: b.mark === 'ok' ? [] : it.pending,
        auditOk: b.mark === 'ok' ? { by: '测试会计', at: '2026-09-25 10:00:00' } : null,
        doubt: b.mark === 'doubt' ? { text: b.text, by: '测试会计', at: '2026-09-25 10:00:00' } : null,
      })
      return { ok: true, item: st.items.find(it => it.id === id) }
    }
    if (c.path === '/api/inv/audit/items/approve') {     // 审一张过一张：勾的票当场通过；发票都过完、差额为 0 → 整单结掉
      const ids = (c.body && c.body.itemIds) || [], dec = (c.body && c.body.decisions) || {}
      st.items = st.items.map(it => !ids.includes(it.id) ? it : {
        ...it, review: 'approved', pending: [], paper: true, reviewBy: '测试会计', reviewAt: '2026-09-25 10:00:00',
        deductible: dec[it.id] ? !!dec[it.id].deductible : true,
        deductStatus: dec[it.id] && 'otherSystem' in dec[it.id] ? (dec[it.id].otherSystem ? 'other' : '') : (it.deductStatus || ''),
      })
      const left = st.items.filter(it => (it.kind === 'invoice' || it.kind === 'receipt') && it.review === 'pending')
      const gap = st.folder.gap ? st.folder.gap.gap : null
      const done = !left.length && gap !== null && Math.abs(gap) <= 0.005
      if (done) st.folder = { ...st.folder, status: 'approved' }
      return { ok: true, items: st.items.filter(it => ids.includes(it.id)), folder: st.folder, folderDone: done }
    }
    if (c.path === '/api/inv/audit/approve') return st.onApprove(c)
    if (c.path === '/api/inv/audit/return') return st.onReturn ? st.onReturn(c) : { ok: true, folder: { ...st.folder, status: 'returned', reviewNote: c.body.note } }
    return { ok: true }
  }
}
const GAP0 = (pay) => ({ pay, special: pay, specialN: 1, normal: 0, normalN: 0, later: 0, gap: 0 })

test('F2/C2 弹窗里改字段后建议变「不可抵扣」，没手动改判的票跟着变；提交时带 itemIds 和抵扣判定', async (browser, B) => {
  const st = {
    folder: { id: 10, status: 'submitted', title: '报销单甲', businessId: '20260101000000000010', amount: 100, gap: GAP0(100), laters: [] },
    items: [inv({ id: 101, pending: ['category'] })], logs: [],
  }
  st.queue = [{ id: 10, title: '报销单甲', amount: 100, anomalies: [], clean: false, stats: { invoices: 1 } }]
  st.onUpdate = (c) => {
    st.items = [inv({ id: 101, ...(c.body.fields || {}), pending: [], deductSuggest: 'no', deductReason: '餐饮服务不能抵扣' })]
    return { ok: true, item: st.items[0] }
  }
  st.onApprove = () => ({ ok: true, folder: { ...st.folder, status: 'approved' } })
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-hd2-t .inv-au-bid', { hasText: '（20260101000000000010）' }).waitFor()   // 标题后面要带审批编号
  // 发票号码显示全＋复制按钮（会计要复制去金蝶）
  assert.match((await page.locator('.inv-au-fullno').first().innerText()).trim(), /^26442000000000000\d{3}$/, '发票号码显示全（20 位，不截断）')
  await page.locator('.inv-au-tbl tbody tr').first().locator('.inv-au-copy').click()
  await page.locator('.inv-au-copy.done', { hasText: '已复制' }).first().waitFor({ timeout: 2000 })
  assert.equal(await page.locator('.inv-au-dlg-side').count(), 0, '点复制不打开核对弹窗')
  await page.locator('.inv-au-tbl tbody tr').first().click()
  const cat = page.locator('.inv-au-dlg-side .inv-fp-row', { hasText: '项目类别' }).locator('input')
  await cat.waitFor()
  assert.equal(await page.locator('.inv-au-dlg-side').getByRole('button', { name: '核对无误' }).count(), 0, '弹窗里用右上角「审核」，不再有「核对无误」')
  await cat.fill('餐饮')
  await page.locator('.inv-au-dlg-side .inv-fp-act').getByRole('button', { name: '保存' }).click()
  await page.getByText('建议不可抵扣', { exact: false }).first().waitFor()
  assert.equal(await page.locator('.inv-mhead .inv-au-hck', { hasText: '可抵扣' }).locator('input').isChecked(), false, '建议变「不可抵扣」后，右上角「可抵扣」的勾跟着去掉')
  await page.locator('.inv-mhead').getByRole('button', { name: '审核', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-au-dlg'))
  // 审一张过一张：弹窗「审核」＝这张当场通过；这张单只有这一张票、金额一致，整单跟着结掉，不用再点底部
  await page.waitForFunction(() => document.body.innerText.includes('这张单已整单通过'))
  const ap = calls.find(c => c.path === '/api/inv/audit/items/approve')
  assert.ok(ap, '没发出通过请求')
  assert.equal(calls.filter(c => c.path === '/api/inv/audit/approve').length, 0, '不用再发整单通过')
  assert.equal(ap.body.decisions['101'].deductible, false, '建议已变「不可抵扣」，没改判时应按新建议提交')
  assert.deepEqual(ap.body.itemIds, [101], '通过要带上看过的待审票 itemIds')
  await ctx.close()
})

test('审一张过一张：已收发票表上勾选要审的票、点「审核所选」当场通过；票都过完且金额一致整单自动结掉，带上勾的「票总管」「可抵扣」', async (browser, B) => {
  const st = {
    folder: { id: 14, status: 'submitted', title: '七张票的报销单', amount: 400, gap: { pay: 400, special: 400, specialN: 4, normal: 0, normalN: 0, later: 0, gap: 0 }, laters: [] },
    items: [inv({ id: 141, pending: ['sellerName', 'amount'] }), inv({ id: 142 }), inv({ id: 143 }),
      inv({ id: 144, flags: { buyerNotCompany: true } })], logs: [],                       // 第 4 张抬头不是本公司：系统核对有问题
  }
  st.queue = [{ id: 14, title: '七张票的报销单', amount: 400, anomalies: [], clean: false, stats: { invoices: 4 } }]
  st.onApprove = () => ({ ok: true, folder: { ...st.folder, status: 'approved' } })
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  const passCalls = () => calls.filter(c => c.path === '/api/inv/audit/items/approve')
  const marks = () => passCalls().flatMap(c => c.body.itemIds)                          // 一共送去审核通过的票，按先后
  const rows = page.locator('.inv-au-tbl tbody tr')
  await rows.first().waitFor()
  const sel = i => rows.nth(i).locator('.inv-au-sel input')
  const go = n => page.getByRole('button', { name: n ? `审核所选（${n}）` : '审核所选', exact: true })
  assert.equal(await rows.locator('.inv-au-sel input').count(), 4, '四张没审的票，每行最左一个选择框')
  assert.ok(await go(0).isDisabled(), '没勾票时「审核所选」点不了')
  // 只勾一张：只审这一张，不打开弹窗
  await sel(1).check()
  assert.equal(await page.locator('.inv-au-dlg').count(), 0, '勾选不打开核对弹窗')
  await go(1).click()
  await rows.nth(1).locator('.inv-au-st-ok', { hasText: '已审核' }).waitFor()
  assert.deepEqual(marks(), [142])
  assert.deepEqual(passCalls()[0].body.decisions, { 142: { deductible: true } }, '审这张时带上它的「可抵扣」（没动过「票总管」就不带）')
  assert.equal(await rows.locator('.inv-au-sel input').count(), 3, '审过的那张没有选择框了')
  await page.getByText('已审核 1/4').first().waitFor()
  // 表头全选：只选系统核对正常的（第 1、3 张），抬头不对的第 4 张不带
  await page.getByText('系统核对有问题的 1 张「全选」不带').waitFor()
  await page.locator('.inv-au-tbl thead .inv-au-sel input').check()
  assert.deepEqual([await sel(0).isChecked(), await sel(2).isChecked(), await sel(3).isChecked()], [true, true, false])
  await go(2).click()
  await rows.nth(2).locator('.inv-au-st-ok').waitFor()
  assert.deepEqual(marks(), [142, 141, 143])
  assert.deepEqual(passCalls()[1].body.itemIds, [141, 143], '勾的两张一次送去')
  assert.equal(await rows.locator('.inv-au-sel input').count(), 1, '只剩抬头不对的那张还能勾')
  // 审过的票：表里的勾变成结果（不能再改），弹窗右上角也只显示结果
  assert.equal(await rows.nth(0).getByRole('checkbox').count(), 0, '审过的票表里没有能勾的框了')
  await page.getByText('金额一致：票都审核完，这张单自动整单通过').waitFor()
  // 最后一张（抬头不对）：点开弹窗，右上角票总管默认不勾、可抵扣默认按建议勾上；改了再点「审核」
  await rows.nth(3).click()
  const hck = t => page.locator('.inv-mhead .inv-au-hck', { hasText: t }).locator('input')
  assert.deepEqual([await hck('票总管').isChecked(), await hck('可抵扣').isChecked()], [false, true])
  await hck('票总管').check()
  await hck('可抵扣').uncheck()
  await page.keyboard.press('Escape')
  const tck = (i, t) => rows.nth(i).getByRole('checkbox', { name: new RegExp(t) })
  assert.deepEqual([await tck(3, '票总管').isChecked(), await tck(3, '可抵扣').isChecked()], [true, false], '弹窗右上角勾的，表里那一行跟着变')
  await tck(3, '可抵扣').check()                                                      // 表里也能直接勾：点勾不打开弹窗
  assert.equal(await page.locator('.inv-au-dlg').count(), 0, '在表里勾选不打开核对弹窗')
  // 这张也审了、金额一致：整单跟着结掉，不用点底部
  await sel(3).check()
  await go(1).click()
  await page.waitForFunction(() => document.body.innerText.includes('这张单已整单通过'))
  assert.deepEqual(passCalls()[2].body, { folderId: 14, itemIds: [144], decisions: { 144: { deductible: true, otherSystem: true } } }, '亲手改过的两个勾都带上')
  assert.equal(calls.filter(c => c.path === '/api/inv/audit/approve').length, 0, '票都逐张过了、金额一致：不用再发整单通过')
  await ctx.close()
})

test('C2 审核期间进了新票：409 后提示并重读票夹、打开新票', async (browser, B) => {
  const st = {
    folder: { id: 11, status: 'submitted', title: '付款单乙', amount: 300, gap: GAP0(300), laters: [] },
    items: [inv({ id: 111 })], logs: [],
  }
  st.queue = [{ id: 11, title: '付款单乙', amount: 300, anomalies: [], clean: false }]
  st.onApprove = () => {
    st.items = [inv({ id: 111 }), inv({ id: 112, total: 200, sellerName: '后补进来的销方' })]
    return { __status: 409, ok: false, msg: '审核期间这个票夹又进了新票，请刷新后再审', newItems: [112] }
  }
  const { page, ctx } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-tbl tbody tr').first().waitFor()
  const before = st.folderGets
  await page.locator('.inv-au-bar2').getByRole('button', { name: '整单通过', exact: true }).click()
  await page.getByRole('button', { name: '不逐张看了，仍然整单通过' }).click()
  await page.getByText('审核期间这个票夹又进了新票', { exact: false }).first().waitFor()
  assert.ok(st.folderGets > before, '409 之后要重读票夹')
  await page.locator('.inv-au-dlg-h', { hasText: '第 2 张' }).waitFor({ timeout: 3000 })
  await ctx.close()
})

const DEEP_LOGS = [{ id: 2, ts: '2026-09-24 10:00:00', user: '李会计', action: '改票面字段', detail: { note: '改了税额' }, itemId: 771 },
  { id: 1, ts: '2026-09-24 09:00:00', user: '王收票', action: '提交审核', detail: {}, itemId: null }]
const deepAudit = () => {
  const st = {
    folder: { id: 77, status: 'submitted', title: '深链票夹', amount: 150, laters: [],
      gap: { pay: 150, special: 100, specialN: 1, normal: 0, normalN: 0, later: 0, gap: 50 } },
    items: [inv({ id: 771 })], logs: DEEP_LOGS,
  }
  st.queue = []
  return st
}

test('F9 深链打开不在队列里的票夹：单据头和金额核对（差额重点关注）照样显示', async (browser, B) => {
  const { page, ctx } = await open(browser, B, auditApi(deepAudit()), { hash: '#/invaudit?folder=77' })
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.getByText('深链票夹').first().waitFor({ timeout: 4000 })
  await page.locator('.inv-au-focus', { hasText: '有差额 50.00' }).waitFor({ timeout: 4000 })
  assert.ok(await page.locator('.inv-au-fig.diff', { hasText: '50.00' }).isVisible())
  await ctx.close()
})

test('C7 弹窗里的留痕只列这张票自己的（按 log.itemId）', async (browser, B) => {
  const { page, ctx } = await open(browser, B, auditApi(deepAudit()), { hash: '#/invaudit?folder=77' })
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-tbl tbody tr').first().click({ timeout: 4000 })
  await page.locator('.inv-au-dlg-logs summary').click()
  assert.equal(await page.locator('.inv-au-dlg-logs li').count(), 1)
  assert.ok(await page.locator('.inv-au-dlg-logs li', { hasText: '改票面字段' }).isVisible())
  await ctx.close()
})

test('审核改版：弹窗 Enter 审核这张并跳到下一张没审的；扫描人显示在弹窗标题', async (browser, B) => {
  const st = {
    folder: { id: 12, status: 'submitted', title: '两张票', amount: 200, gap: GAP0(200), laters: [] },
    items: [inv({ id: 121, createdBy: '李四', origin: 'camera', createdAt: '2026-09-25 09:01:00' }), inv({ id: 122 })], logs: [],
  }
  st.queue = [{ id: 12, title: '两张票', amount: 200, anomalies: [], clean: false }]
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-tbl tbody tr').first().click()
  await page.locator('.inv-au-who', { hasText: '李四' }).waitFor()
  assert.ok(await page.locator('.inv-au-who', { hasText: '高拍仪' }).isVisible())
  await page.locator('.inv-au-dlg').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('Enter')
  await page.locator('.inv-au-dlg-h', { hasText: '第 2 张' }).waitFor({ timeout: 3000 })
  assert.ok(calls.some(c => c.path === '/api/inv/audit/items/approve' && String(c.body.itemIds) === '121'), 'Enter＝这张当场审核通过')
  await ctx.close()
})

test('后补票「核销暂估」：弹窗里亮出系统到金蝶找到的原凭证；找到两张时选一张，再点「现在演练一次」', async (browser, B) => {
  const cand = (vno, picked) => ({ key: '2026-09-' + vno, year: 2026, month: 9, vno, expl: '某某提起支付运费', gross: 500, net: 495, tax: 5, tax06: 0, picked })
  const view = (kind, src, extra) => ({ ok: true, laterId: 8, mode: 'dry', modeText: '演练', ready: true, lines: [],
    plan: { ok: kind === 'exact', kind, why: kind === 'exact' ? '发票税额和账上暂估进项税都是 5.00，对得上' : '金蝶里有 2 张都对得上，请选是哪一张',
      book: '合成主体', cands: [cand('401', src.includes('2026-09-401')), cand('402', src.includes('2026-09-402'))], src, taxInv: 5, taxEst: 5 },
    state: {}, ...extra })
  const st = {
    folder: { id: 13, status: 'collecting', title: '后补票的单', amount: 500, gap: GAP0(500), laters: [] },
    items: [inv({ id: 131, laterId: 8, origin: 'self', selfPending: true, total: 500, tax: 5 })], logs: [],
  }
  st.queue = [{ id: 13, title: '后补票的单', amount: 500, anomalies: [], clean: false }]
  const base = auditApi(st)
  const api = c => {
    if (c.path === '/api/inv/later/8/voucher-plan') return view('multi', [])
    if (c.path === '/api/inv/later/8/voucher-source') return view('exact', c.body.picks, { lines: [
      { dc: '借', acct: '2221.01.06', acctName: '待认证进项税额', amount: 5, expl: '2644核销9/402#某某提起支付运费', supplier: '' },
      { dc: '贷', acct: '2221.01.07', acctName: '暂估进项税', amount: 5, expl: '核销9/402#某某提起支付运费', supplier: '合成物流' }] })
    if (c.path === '/api/inv/later/8/voucher-run') return { ...view('exact', ['2026-09-402']), status: 'dry', msg: '演练（没写金蝶）', state: { status: 'dry', msg: '演练（没写金蝶）：会新建一张凭证' } }
    return base(c)
  }
  const { page, ctx, calls, errors } = await open(browser, B, api)
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-tbl tbody tr').first().click()
  const box = page.locator('.inv-au-l3')
  await box.locator('.inv-au-l3bad', { hasText: '请选是哪一张' }).waitFor({ timeout: 4000 })
  assert.ok((await box.textContent()).includes('自动转待认证：演练'))
  assert.equal(await box.locator('.inv-au-l3t tbody tr').count(), 2)
  await box.getByLabel('核销 2026年9月 记-402').click()      // 选中状态等后端回来才变（受控），所以用 click 不用 check
  await box.locator('.inv-au-l3ok', { hasText: '对得上' }).waitFor({ timeout: 4000 })
  assert.deepEqual(calls.find(c => c.path === '/api/inv/later/8/voucher-source').body.picks, ['2026-09-402'])
  assert.equal(await box.locator('.inv-au-l3l > div').count(), 2)
  await box.getByRole('button', { name: '现在演练一次' }).click()
  await box.getByText('上一次：演练（没写金蝶）').waitFor({ timeout: 4000 })
  assert.equal(calls.filter(c => c.path === '/api/inv/later/8/voucher-run' && c.method === 'POST').length, 1)
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('审核改版：记了疑问 → 底部「提交」变成退回，并带上疑问', async (browser, B) => {
  const st = {
    folder: { id: 13, status: 'submitted', title: '有疑问的单', amount: 100, gap: GAP0(100), laters: [] },
    items: [inv({ id: 131 })], logs: [],
  }
  st.queue = [{ id: 13, title: '有疑问的单', amount: 100, anomalies: [], clean: false }]
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-tbl tbody tr').first().click()
  await page.locator('.inv-mhead').getByRole('button', { name: '存疑' }).click()
  await page.locator('.inv-au-qbox').getByRole('button', { name: '抬头不对' }).click()
  await page.getByRole('button', { name: '记下疑问' }).click()
  await page.locator('.inv-au-qbox', { hasText: '疑问：抬头不对' }).waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.inv-au-st-q').waitFor()
  const btn = page.locator('.inv-au-bar2').getByRole('button', { name: '整单退回（1 张存疑）' })
  await btn.click()
  await page.getByRole('button', { name: '确认退回' }).click()
  await page.waitForFunction(() => document.body.innerText.includes('已退回'))
  const ret = calls.find(c => c.path === '/api/inv/audit/return')
  assert.ok(ret && ret.body.note.includes('抬头不对'), '退回原因要带上疑问：' + JSON.stringify(ret && ret.body))
  assert.equal(n(calls, c => c.path === '/api/inv/audit/approve'), 0)
  await ctx.close()
})

test('票都审核通过了但金额有差额：整单不自动结，仍要人点底部写差额说明', async (browser, B) => {
  const st = {
    folder: { id: 15, status: 'submitted', title: '差十块的单', amount: 110, gap: { pay: 110, special: 100, specialN: 1, normal: 0, normalN: 0, later: 0, gap: 10 }, laters: [] },
    items: [inv({ id: 151 })], logs: [],
  }
  st.queue = [{ id: 15, title: '差十块的单', amount: 110, anomalies: [], clean: false }]
  st.onApprove = () => ({ ok: true, folder: { ...st.folder, status: 'approved' } })
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  const row = page.locator('.inv-au-tbl tbody tr').first()
  await row.locator('.inv-au-sel input').check()
  await page.getByRole('button', { name: '审核所选（1）', exact: true }).click()
  await row.locator('.inv-au-st-ok', { hasText: '已审核' }).waitFor()
  await page.waitForTimeout(400)
  assert.equal(calls.filter(c => c.path === '/api/inv/audit/items/approve').length, 1, '票照样当场审核通过')
  assert.equal(calls.filter(c => c.path === '/api/inv/audit/approve').length, 0, '有差额：票过了整单也不自动结')
  assert.equal(await page.locator('.inv-au-bar2').getByText('这张单自动整单通过').count(), 0, '有差额时底部不提示会自动通过')
  await page.getByText('票都审核了，但金额有差额').waitFor()
  await page.locator('.inv-au-bar2').getByRole('button', { name: '整单通过', exact: true }).click()
  await page.getByRole('button', { name: '确认差额并通过' }).waitFor()
  await ctx.close()
})

test('审核改版：有差额要写差额说明才能提交（点常用说明可快填），说明随 gapNote 发出；后补单列表照实列出', async (browser, B) => {
  const st = {
    folder: { id: 14, status: 'submitted', title: '有差额的单', amount: 160.8,
      gap: { pay: 160.8, special: 100, specialN: 1, normal: 0, normalN: 0, later: 60, gap: 0.8 },
      laters: [{ id: 12, filedBy: '张三', filedAt: '2026-09-10 10:00:00', expectAmount: 60, receivedAmount: 0, unregisteredAmount: 0,
        left: 60, expectDate: '2026-10-10', receiverName: '王五', status: 'open', statusText: '待收', remindCount: 1 }] },
    items: [inv({ id: 141, review: 'approved', deductible: true })], logs: [],
  }
  st.queue = [{ id: 14, title: '有差额的单', amount: 160.8, anomalies: [], clean: false }]
  st.onApprove = () => ({ ok: true, folder: { ...st.folder, status: 'approved' } })
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-ltbl', { hasText: '#12' }).waitFor()
  assert.ok(await page.locator('.inv-au-ltbl', { hasText: '王五' }).isVisible())
  await page.locator('.inv-au-bar2').getByRole('button', { name: '整单通过', exact: true }).click()
  const ok = page.getByRole('button', { name: '确认差额并通过' })
  await ok.waitFor()
  assert.ok(await ok.isDisabled(), '没写差额说明不能提交')
  await page.locator('.inv-au-ask').getByRole('button', { name: '付款抹零' }).click()
  await ok.click()
  await page.waitForFunction(() => document.body.innerText.includes('已通过'))
  const ap = calls.find(c => c.path === '/api/inv/audit/approve')
  assert.equal(ap.body.gapNote, '付款抹零')
  await ctx.close()
})

test('审核改版：没有后补单时写「没有发票后补单」', async (browser, B) => {
  const st = { folder: { id: 15, status: 'submitted', title: '无后补', amount: 100, gap: GAP0(100), laters: [] }, items: [inv({ id: 151 })], logs: [] }
  st.queue = [{ id: 15, title: '无后补', amount: 100, anomalies: [], clean: false }]
  const { page, ctx } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.getByText('没有发票后补单').waitFor()
  await ctx.close()
})

// ── 审票分工（发票审核按人分） ──
const auditSt = (owner, extra = {}) => {
  const st = {
    folder: { id: 10, status: 'submitted', source: 'dingtalk', title: '报销单甲', businessId: '20260101000000000010', amount: 100, gap: GAP0(100), laters: [], owner },
    items: [inv({ id: 101 })], logs: [], ...extra,
  }
  st.queue = [{ id: 10, source: 'dingtalk', title: '报销单甲', amount: 100, anomalies: [], clean: true, stats: { invoices: 1 }, owner }]
  st.onApprove = () => ({ ok: true, folder: { ...st.folder, status: 'approved' } })
  return st
}

test('审票分工：别人名下的单只能看——写明归谁审，没有审核/退回按钮；默认只看归我的，搜的时候才连别人的一起搜', async (browser, B) => {
  const other = { assigned: true, name: '李四', mine: false, canAct: false, note: '', via: 'intake', at: '2026-09-25 09:00:00' }
  const st = auditSt(other)
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-owner.other', { hasText: '归李四审，你只能看' }).waitFor()
  assert.equal(await page.locator('.inv-au-row .inv-au-own', { hasText: '归李四' }).count(), 1, '队列每行写归谁')
  assert.equal(await page.locator('.inv-au-row .inv-au-src', { hasText: '钉钉接入' }).count(), 1, '队列每行写来源')
  await page.locator('.inv-au-kv2', { hasText: '来源' }).waitFor()
  assert.equal(await page.locator('.inv-au-owner').getByRole('button').count(), 0, '普通会计对别人名下的单没有转交按钮')
  assert.equal(await page.locator('.inv-au-row-ck input').count(), 0, '别人名下的不能勾批量通过')
  assert.equal(await page.locator('.inv-au-tbl tbody input[type=checkbox]:not([disabled])').count(), 0, '已收发票表上不能勾选审核')
  await page.locator('.inv-au-fbar', { hasText: '归我审' }).waitFor()
  assert.equal(await page.locator('.inv-au-scope').count(), 0, '普通会计没有「归谁审」的下拉（只有归我审）')
  assert.equal(await page.locator('.inv-au-kpis').count(), 0, '顶上不再是大卡片')
  assert.deepEqual([...new Set(st.scopes)], ['mine'], '不搜的时候只取归我的')
  await page.locator('.inv-au-search input').fill('报销')
  await page.locator('.inv-au-search input').press('Enter')
  await page.waitForFunction(() => true)
  await sleep(300)
  assert.equal(st.scopes[st.scopes.length - 1], 'all', '搜的时候别人名下的也搜得到（只读）')
  assert.equal(n(calls, c => /audit\/(approve|return|items)/.test(c.path)), 0)
  await ctx.close()
})

test('审票分工：没指派上的先点「我来审」；归我的可以转交，原因必填', async (browser, B) => {
  const none = { assigned: false, name: '', mine: false, canAct: false, note: '' }
  const st = auditSt(none)
  st.onClaim = () => { st.folder = { ...st.folder, owner: { ...OWN_ME, via: 'claim' } }; st.queue = st.queue.map(r => ({ ...r, owner: st.folder.owner })); return { ok: true, msg: '已归到你名下' } }
  st.onAssign = (c) => { st.assigned = c.body; st.queue = []; return { ok: true, msg: '已转给李四' } }
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-owner.none', { hasText: '没指派上' }).waitFor()
  assert.equal(await page.locator('.inv-au-owner').getByRole('button', { name: '派给别人…' }).count(), 0, '普通会计不能派给别人')
  await page.locator('.inv-au-owner').getByRole('button', { name: '我来审' }).click()
  await page.locator('.inv-au-owner.mine', { hasText: '测试会计（你）' }).waitFor()
  assert.equal(n(calls, c => c.path === '/api/inv/audit/claim' && c.body.folderId === 10), 1)
  // 转交：不写原因不让转
  await page.locator('.inv-au-owner').getByRole('button', { name: '转交…' }).click()
  const dlg = page.locator('.inv-modal', { hasText: '转交审核' })
  await dlg.locator('select').selectOption('李四')
  await dlg.getByRole('button', { name: '转交', exact: true }).click()
  await dlg.getByText('写一下为什么转交', { exact: false }).waitFor()
  assert.equal(n(calls, c => c.path === '/api/inv/audit/assign'), 0)
  assert.equal(await dlg.locator('select option', { hasText: '测试会计' }).count(), 0, '下拉里不列现在归的那位')
  await dlg.locator('textarea').fill('下周请假')
  await dlg.getByRole('button', { name: '转交', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-modal'))
  assert.deepEqual(st.assigned, { folderId: 10, to: '李四', reason: '下周请假' })
  await ctx.close()
})

test('审票分工：有「审核分派」权限的人——多一张「全部」卡片、能按人看、别人名下的可以直接审也可以转交', async (browser, B) => {
  const other = { assigned: true, name: '李四', mine: false, canAct: true, note: '这位还没开通发票审核', via: 'intake', at: '2026-09-25 09:00:00' }
  const st = auditSt(other, { can: { assign: true } })
  st.backlog = { ok: true, canAssign: true, people: [{ key: 'a:李四', name: '李四', mine: false, count: 3, oldest: '2026-09-10 10:00:00', days: 15, note: '这位还没开通发票审核' }],
    unassigned: { count: 2, oldest: '2026-09-01 10:00:00', days: 24 }, total: 5 }
  const { page, ctx } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  const scopeSel = page.locator('.inv-au-fbar .inv-au-scope')
  await scopeSel.waitFor()
  assert.match(await scopeSel.innerText(), /归我审[\s\S]*全部（5）[\s\S]*没指派上（2）[\s\S]*李四（3 张 · 最久等了 15 天）/)
  await page.locator('.inv-au-fwarn', { hasText: '有 2 张没指派上' }).waitFor()
  await scopeSel.selectOption('all')
  await sleep(300)
  assert.equal(st.scopes[st.scopes.length - 1], 'all')
  // 来源也能筛
  await page.locator('.inv-au-fbar select').nth(1).selectOption('dingtalk')
  await sleep(300)
  assert.equal(st.sources[st.sources.length - 1], 'dingtalk')
  // 发票金额：回车才查，逗号去掉
  const amtBox = page.locator('.inv-au-amt input')
  await amtBox.fill('3,151.50')
  await amtBox.press('Enter')
  await sleep(300)
  assert.equal(st.amounts[st.amounts.length - 1], '3151.50')
  await scopeSel.selectOption('a:李四')
  await sleep(300)
  assert.equal(st.scopes[st.scopes.length - 1], 'a:李四', '按人看：队列只取这一位的')
  await page.locator('.inv-au-row').first().click()
  await page.locator('.inv-au-owner.other', { hasText: '可以直接审' }).waitFor()
  await page.locator('.inv-au-owner .inv-badge', { hasText: '这位还没开通发票审核' }).waitFor()
  assert.equal(await page.locator('.inv-au-owner').getByRole('button', { name: '转交…' }).count(), 1)
  assert.ok(await page.locator('.inv-au-tbl tbody input[type=checkbox]:not([disabled])').count() > 0, '别人名下的，管理员能勾选审核')
  await ctx.close()
})

test('收票台新建票夹：会计默认归自己、实习生必须选审核会计；名字系统起；扫到没定归谁审的付款单让人选一位再建', async (browser, B) => {
  const mk = (meCanAudit) => {
    const state = { can: {}, posts: [] }
    const base = deskApi(state)
    const api = (c) => {
      if (c.path === '/api/inv/config') return { ...cfg(), settings: { company: [{ name: '孝感市星期九食品科技有限公司' }, { name: '深圳市星期零食品科技有限公司' }], templates: [], laterTemplates: [], remind: {} } }
      if (c.path === '/api/inv/audit/people') return { ok: true, people: [{ account: '陈梓华', name: '陈梓华' }, { account: '李志鹏', name: '李志鹏' }], me: meCanAudit ? '陈梓华' : '实习生甲', meCanAudit }
      if (c.path === '/api/inv/folder/manual') {
        const b = c.body
        const co = b.company === 'auto' ? '主体待定' : b.company.replace(/[省市]/g, '').slice(0, 5)
        const name = `${co}-${b.auditor}-${b.month.replace('-', '')}进项认证（${b.way === 'single' ? '单张手工' : '批量手工'}）`
        if (b.dry) return { ok: true, dry: true, title: name, reuse: false, names: b.company === 'auto' ? [name, name.replace('主体待定', '孝感星期九'), name.replace('主体待定', '深圳星期零')] : [name] }
        state.posts.push(b)
        return { ok: true, folder: { id: 77, title: name, status: 'collecting', stats: {} } }
      }
      if (c.path === '/api/inv/desk/scan') {
        state.scans = (state.scans || []).concat([c.body])
        if (!c.body.auditor) return { __status: 400, ok: false, code: 'needAuditor', msg: '这张付款单还没走到（已启用的）接入审批人，请指定一位审核会计' }
        return { ok: true, action: 'folder', msg: '已打开票夹', folder: { id: 78, title: '付款单', status: 'collecting', stats: {} }, items: [] }
      }
      return base(c)
    }
    return { state, api }
  }
  // 会计：默认已经选好自己，名字跟着选的变
  let { state, api } = mk(true)
  let { page, ctx } = await open(browser, B, api)
  await mount(page, 'InvDesk', { user: { name: '陈梓华' } })
  await page.getByRole('button', { name: '新建票夹' }).first().click()
  let dlg = page.locator('.inv-modal', { hasText: '新建票夹' })
  await dlg.locator('.inv-dk-name', { hasText: '主体待定-陈梓华-' }).waitFor()      // 默认「自动识别」：先建主体待定的收票口
  await dlg.locator('.inv-dk-name', { hasText: '孝感星期九-陈梓华-' }).waitFor()    // 也列出分出去以后各主体的票夹名
  assert.equal(await dlg.locator('select').first().inputValue(), '陈梓华')
  assert.equal(await dlg.locator('select').nth(1).inputValue(), 'auto')
  await dlg.locator('input[type=month]').fill('2026-08')
  await dlg.locator('select').nth(1).selectOption('孝感市星期九食品科技有限公司')
  await dlg.locator('.inv-dk-name', { hasText: '孝感星期九-陈梓华-202608进项认证（批量手工）' }).waitFor()
  await dlg.getByRole('button', { name: '建票夹' }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-modal'))
  assert.deepEqual(state.posts[0], { month: '2026-08', company: '孝感市星期九食品科技有限公司', way: 'batch', auditor: '陈梓华' })
  await ctx.close()
  // 实习生：没有默认，不选建不了
  ;({ state, api } = mk(false))
  ;({ page, ctx } = await open(browser, B, api))
  await mount(page, 'InvDesk', { user: { name: '实习生甲' } })
  await page.getByRole('button', { name: '新建票夹' }).first().click()
  dlg = page.locator('.inv-modal', { hasText: '新建票夹' })
  await dlg.locator('select option', { hasText: '请选一位审核会计' }).waitFor({ state: 'attached' })
  assert.equal(await dlg.locator('select').first().inputValue(), '')
  await dlg.getByRole('button', { name: '建票夹' }).click()
  await dlg.getByText('选一位审核会计', { exact: false }).last().waitFor()
  assert.equal(state.posts.length, 0, '没选审核会计不发请求')
  await dlg.locator('select').first().selectOption('李志鹏')
  await dlg.getByRole('button', { name: '建票夹' }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-modal'))
  assert.equal(state.posts[0].auditor, '李志鹏')
  assert.equal(state.posts[0].company, 'auto', '默认按抬头自动分主体')
  // 扫一张没定归谁审的付款单：弹窗选一位，带着再扫
  await page.locator('.inv-scan input, input[placeholder*="扫审批单"]').first().fill('202609201716000443127')
  await page.locator('.inv-scan input, input[placeholder*="扫审批单"]').first().press('Enter')
  const pick = page.locator('.inv-modal', { hasText: '这张付款单归谁审' })
  await pick.waitFor()
  await pick.locator('select').selectOption('陈梓华')
  await pick.getByRole('button', { name: '建票夹' }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-modal'))
  assert.deepEqual(state.scans.map(x => x.auditor || ''), ['', '陈梓华'])
  await ctx.close()
})

test('按抬头分主体：收票口里看得到这一批分到了哪几个票夹；读不出抬头的票点一下是哪家就挪过去；收票口不用提交', async (browser, B) => {
  const XG = '孝感市星期九食品科技有限公司', SZ = '深圳市星期零食品科技有限公司'
  const state = {
    can: {},
    folder: { id: 50, status: 'collecting', source: 'manual', title: '主体待定-陈梓华-202608进项认证（批量手工）', company: '', stats: {},
      batch: { month: '202608', way: 'batch', hold: true, siblings: [{ id: 51, title: '孝感星期九-陈梓华-202608进项认证（批量手工）', company: XG, hold: false, invoices: 3 }] } },
    items: [inv({ id: 501, review: 'draft', number: '26442000000000000501', sellerName: '读不出抬头的销方' })],
    openFolder: { id: 51, status: 'collecting', source: 'manual', title: '孝感星期九-陈梓华-202608进项认证（批量手工）', company: XG, stats: {},
      batch: { month: '202608', way: 'batch', hold: false, siblings: [{ id: 50, title: '主体待定-陈梓华-202608进项认证（批量手工）', company: '', hold: true, invoices: 0 }] } },
    openItems: [],
  }
  const base = deskApi(state)
  const picked = []
  const api = (c) => {
    if (c.path === '/api/inv/config') return { ...cfg(), settings: { company: [{ name: XG }, { name: SZ }], templates: [], laterTemplates: [], remind: {} } }
    if (c.path === '/api/inv/item/501/company') {
      picked.push(c.body)
      state.items = []
      return { ok: true, msg: '已放到「深圳星期零-陈梓华-202608进项认证（批量手工）」', moved: { id: 52, title: '深圳星期零-陈梓华-202608进项认证（批量手工）' } }
    }
    return base(c)
  }
  const { page, ctx, calls } = await open(browser, B, api)
  await mount(page, 'InvDesk', { user: { name: '陈梓华' } })
  await page.locator('.inv-dk-batch.hold', { hasText: '这里是「主体待定」的收票口' }).waitFor()
  await page.locator('.inv-dk-batch-sib', { hasText: XG }).waitFor()
  assert.match(await page.locator('.inv-dk-batch-sib', { hasText: XG }).innerText(), /3 张/)
  const hold = page.locator('.inv-dk-hold')
  await hold.getByText('还有 1 张没分出是哪家的票').waitFor()
  assert.equal(await page.getByRole('button', { name: '提交', exact: true }).count(), 0, '收票口不用提交')
  await hold.getByRole('button', { name: SZ }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-dk-hold'))
  assert.deepEqual(picked, [{ company: SZ }])
  // 点这一批的另一个票夹：直接打开它；那边是一家主体的票夹，写明只装这一家的
  await page.locator('.inv-dk-batch-sib', { hasText: XG }).click()
  await page.locator('.inv-dk-batch', { hasText: '这个票夹只装' }).waitFor()
  assert.equal(n(calls, c => c.path === '/api/inv/desk/open' && c.body.folderId === 51), 1)
  await ctx.close()
})

test('设置：接入审批人每人一行、带自己的启用日；留空＝还没启用；保存时一起带上', async (browser, B) => {
  const settings = {
    people: [], company: [{ name: '合成测试有限公司', taxId: '91440300MA5DXXXXX1' }], templates: [],
    remind: { enabled: true, beforeDays: 3, everyDays: 7, hour: 10 }, blockNoInvoice: false, portalUrl: '',
    intake: { approvers: [{ dtUserid: 'u1', dtName: '曾某', since: '2026-09-09' }, { dtUserid: 'u2', dtName: '李某', since: '' }], firstReviewers: [], since: '2026-09-09' },
  }
  let saved = null
  const api = (c) => {
    if (c.path === '/api/inv/settings' && c.method === 'GET') return { ok: true, settings }
    if (c.path === '/api/inv/settings' && c.method === 'POST') { saved = c.body.settings; return { ok: true, settings: saved } }
    if (c.path === '/api/inv/intake') return { ok: true, last: null, waiting: 0 }
    return { ok: true }
  }
  const { page, ctx } = await open(browser, B, api)
  await mount(page, 'InvSettings', { user: { name: '管理员' } })
  const rows = page.locator('.inv-set-aps tbody tr')
  await rows.first().waitFor()
  assert.equal(await rows.count(), 2)
  assert.match(await rows.nth(0).innerText(), /曾某[\s\S]*已启用 · 只接 2026-09-09 当天及以后提交的单/)
  assert.match(await rows.nth(1).innerText(), /李某[\s\S]*还没启用 · 单经过这位不接/)
  assert.equal(await page.getByText('从这天提交的单子起').count(), 0, '全局起始日不再单独出现')
  await rows.nth(0).locator('input[type=date]').fill('2026-09-01')
  await page.getByRole('button', { name: '保存', exact: true }).last().click()
  await page.waitForFunction(() => true)
  await sleep(400)
  assert.deepEqual(saved && saved.intake.approvers, [{ dtUserid: 'u1', dtName: '曾某', since: '2026-09-01' }, { dtUserid: 'u2', dtName: '李某', since: '' }])
  await ctx.close()
})

// ── 对纸质票 ──
test('对纸质票（电脑）：勾票夹开始 → 扫码枪扫到的那一行变绿、手机扫到的跟着变绿；不相干的票红字提醒；扫完「审核扫到的」', async (browser, B) => {
  const st = auditSt(OWN_ME)
  const items = [
    { id: 101, folderId: 10, kind: 'invoice', typeLabel: '专票', number: 'N101', sellerName: '销方甲', total: 100, review: 'pending', hit: null },
    { id: 102, folderId: 10, kind: 'invoice', typeLabel: '专票', number: 'N102', sellerName: '销方乙', total: 200, review: 'pending', hit: null },
    { id: 103, folderId: 10, kind: 'receipt', typeLabel: '收据', number: '', sellerName: '销方丙', total: 30, review: 'pending', hit: null },
  ]
  const ck = { open: false, strangers: [], approved: [] }
  const view = () => {
    const hit = items.filter(i => i.hit).length
    return { ok: true, check: ck.open ? { id: 7, open: !ck.closed, by: '测试会计', at: '2026-10-14 09:00:00', closedAt: ck.closed ? '2026-10-14 09:30:00' : '', entry: 'audit' } : null,
      folders: [{ id: 10, title: '报销单甲', payee: '武汉某某公司', amount: 330, source: 'dingtalk', ownerName: '测试会计', total: items.length, hit }],
      items, strangers: ck.strangers, stats: { total: items.length, hit, left: items.length - hit, strangers: ck.strangers.length } }
  }
  const hitIt = (id, via) => { const it = items.find(i => i.id === id); it.hit = { at: '2026-10-14 09:10:00', by: '测试会计', via, viaLabel: via === 'phone' ? '手机' : via === 'manual' ? '手工勾' : '扫码枪' }; return it }
  const base = auditApi(st)
  const api = (c) => {
    if (c.path === '/api/inv/check/current') return ck.open && !ck.closed ? view() : { ok: true, check: null }
    if (c.path === '/api/inv/check/start') { ck.open = true; ck.started = c.body; return view() }
    if (c.path === '/api/inv/check/7') return view()
    if (c.path === '/api/inv/check/7/scan') {
      const code = c.body.code
      if (code === 'N101') { const dup = !!items[0].hit; const it = hitIt(101, 'scanner'); return { ok: true, result: dup ? 'dup' : 'ok', say: dup ? '重复' : '确认', msg: dup ? '这张扫过了：N101' : '对上了：N101', item: it } }
      if (code === 'OTHER') { ck.strangers.push({ kind: 'other', number: 'N999', total: 55, folderId: 99, payee: '别的收款方', ownerName: '李四' }); return { ok: true, result: 'other', say: '不在这里', msg: '不在选中的票夹里：N999 这张在「别的收款方」里（归李四审）。没有登记，也没有挪' } }
      return { ok: true, result: 'none', say: '没有这张', msg: '系统里没有这张票' }
    }
    if (c.path === '/api/inv/check/7/mark') { const it = items.find(i => i.id === c.body.itemId); if (c.body.on) hitIt(it.id, 'manual'); else it.hit = null; return { ok: true, item: it } }
    if (c.path === '/api/inv/check/7/close') { ck.closed = true; return view() }
    if (c.path === '/api/inv/audit/items/approve') { ck.approved.push(c.body); items.forEach(i => { if (c.body.itemIds.includes(i.id)) i.review = 'approved' }); return { ok: true, items: [], folder: st.folder, folderDone: false } }
    return base(c)
  }
  const { page, ctx } = await open(browser, B, api)
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.getByRole('button', { name: '对纸质票', exact: true }).click()
  const start = page.locator('.inv-modal', { hasText: '选要对的范围' })
  await start.locator('.inv-ck-pickrow').first().waitFor()
  assert.equal(await start.locator('.inv-ck-pickrow input:checked').count(), 1, '默认把列表里的票夹都勾上')
  await start.getByRole('button', { name: '开始对' }).click()
  const dlg = page.locator('.inv-modal').filter({ has: page.locator('.inv-ck') })
  await dlg.getByText('扫到 0 / 3').waitFor()
  assert.deepEqual(ck.started, { folderIds: [10], entry: 'audit' })
  assert.equal(await dlg.locator('.inv-ck-tbl tr.hit').count(), 0, '一开始都没扫到（白的）')
  const scan = async (code) => { const box = dlg.locator('.inv-ck-scan input'); await box.fill(code); await box.press('Enter') }
  await scan('N101')
  await dlg.locator('#inv-ck-row-101.hit').waitFor()
  await dlg.getByText('扫到 1 / 3').waitFor()
  await scan('N101')
  await dlg.locator('.inv-ck-alert.warn', { hasText: '这张扫过了' }).waitFor()
  await scan('OTHER')
  await dlg.locator('.inv-ck-alert.err', { hasText: '不在选中的票夹里' }).waitFor()
  await dlg.locator('.inv-ck-str', { hasText: '别的收款方' }).waitFor()
  // 手机那边扫到一张：这里不用动，跟着变绿
  hitIt(102, 'phone')
  await dlg.locator('#inv-ck-row-102.hit').waitFor({ timeout: 6000 })
  assert.match(await dlg.locator('#inv-ck-row-102').innerText(), /手机/)
  // 没有二维码的收据：手工勾
  await dlg.locator('#inv-ck-row-103').getByRole('button', { name: '手工勾' }).click()
  await dlg.locator('#inv-ck-row-103.hit').waitFor()
  await dlg.getByText('扫到 3 / 3').waitFor()
  // 审核扫到的：按票夹送去审核通过
  await dlg.getByRole('button', { name: '审核扫到的（3）' }).click()
  await dlg.locator('.inv-ck-alert.ok', { hasText: '已审核通过 3 张' }).waitFor()
  assert.deepEqual(ck.approved.map(b => [b.folderId, b.itemIds.slice().sort()]), [[10, [101, 102, 103]]])
  await dlg.getByRole('button', { name: '对完了，收' }).click()
  await dlg.getByText('这次点验已经收了', { exact: false }).waitFor()
  await ctx.close()
})

test('对纸质票（手机）：电脑上在对时出现「连续扫纸质票」；扫一张念一声（确认／重复／不在这里），点取消就停', async (browser, B) => {
  const init = () => {
    window.__said = []; window.__ddScans = 0; window.__ddCfg = null
    window.SpeechSynthesisUtterance = function (t) { this.text = t }
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: { cancel() {}, speak(u) { window.__said.push(u.text) } } })
    const codes = ['C-OK', 'C-OK', 'C-OTHER']
    window.dd = {
      config: c => { window.__ddCfg = c }, ready: cb => setTimeout(cb, 10), error: () => {},
      runtime: { permission: { requestAuthCode: o => o.onSuccess({ code: 'AUTH1' }) } },
      biz: { util: { scan: o => { const code = codes[window.__ddScans++]; setTimeout(() => (code ? o.onSuccess({ text: code }) : o.onFail({ errorCode: '300001', errorMessage: 'user cancel' })), 10) } } },
    }
  }
  const seen = []
  const base = ddPhoneApi(() => ({ ok: true, agentId: '123', corpId: 'dingFAKE', timeStamp: '1700000000000', nonceStr: 'n1', signature: 'sig1' }))
  const api = async (c) => {
    if (c.path === '/api/inv/m/check') return { ok: true, check: { id: 7, open: true }, stats: { total: 5, hit: seen.filter(x => x === 'ok').length, left: 5 }, folders: 2 }
    if (c.path === '/api/inv/m/check/scan') {
      const code = c.body.code
      const r = code === 'C-OTHER' ? 'other' : seen.includes('ok') ? 'dup' : 'ok'
      seen.push(r)
      return { ok: true, result: r, say: { ok: '确认', dup: '重复', other: '不在这里' }[r], msg: { ok: '对上了：N1', dup: '这张扫过了：N1', other: '不在选中的票夹里：N9' }[r], stats: { total: 5, hit: 1, left: 4 } }
    }
    return base(c)
  }
  const { page, ctx, calls } = await open(browser, B, api, { hash: '#/invpair?t=SESS9', ua: DD_UA, init })
  await mount(page, 'InvPhone')
  await page.locator('.inv-ph-chk', { hasText: '电脑上正在对纸质票' }).waitFor({ timeout: 8000 })
  await page.getByRole('button', { name: '连续扫纸质票' }).click()
  await page.locator('.inv-ph-chk-last.err', { hasText: '不在这里' }).waitFor({ timeout: 8000 })
  await page.getByRole('button', { name: '连续扫纸质票' }).waitFor({ timeout: 8000 })      // 第四次点了取消：自己停下来
  assert.deepEqual(await page.evaluate(() => window.__said), ['确认', '重复', '不在这里'])
  assert.deepEqual(calls.filter(c => c.path === '/api/inv/m/check/scan').map(c => c.body.code), ['C-OK', 'C-OK', 'C-OTHER'])
  assert.match(await page.locator('.inv-ph-chk-h').innerText(), /已扫到 1 \/ 5/)
  await ctx.close()
})

test('对纸质票·选认证清单：对着一份清单开始，按凭证排；清单里的念「确认」，别的月做账的念「9 月的」并写明是哪张凭证', async (browser, B) => {
  const st = auditSt(OWN_ME)
  const items = [
    { id: 201, folderId: 0, kind: 'invoice', typeLabel: '专票', number: 'N201', sellerName: '销方甲', total: 100, review: 'approved', voucher: '深圳星期零-10-1#', bookMonth: '2026-10', hit: null },
    { id: 202, folderId: 0, kind: 'invoice', typeLabel: '专票', number: 'N202', sellerName: '销方乙', total: 200, review: 'approved', voucher: '深圳星期零-10-8#', bookMonth: '2026-10', hit: null },
  ]
  const ck = { started: null, strangers: [] }
  const view = () => {
    const hit = items.filter(i => i.hit).length
    return { ok: true, check: ck.started ? { id: 9, open: true, by: '测试会计', at: '2026-10-31 09:00:00', closedAt: '', entry: 'audit', listId: 5, listName: '深圳星期零 2026-10 认证清单' } : null,
      folders: [{ id: 0, title: '深圳星期零 2026-10 认证清单', isList: true, total: items.length, hit }], items, strangers: ck.strangers,
      stats: { total: items.length, hit, left: items.length - hit, strangers: ck.strangers.length } }
  }
  const base = auditApi(st)
  const api = (c) => {
    if (c.path === '/api/inv/ticklists') return { ok: true, rows: [{ id: 5, name: '深圳星期零 2026-10 认证清单', rows: 2, bookMonth: '2026-10', by: '测试会计', at: '2026-10-31 08:50:00', tax: 30 }] }
    if (c.path === '/api/inv/check/current') return ck.started ? view() : { ok: true, check: null }
    if (c.path === '/api/inv/check/start') { ck.started = c.body; return view() }
    if (c.path === '/api/inv/check/9') return view()
    if (c.path === '/api/inv/check/9/scan') {
      if (c.body.code === 'N202') { items[1].hit = { at: '2026-10-31 09:01:00', by: '测试会计', via: 'scanner', viaLabel: '扫码枪' }; return { ok: true, result: 'ok', say: '确认', msg: '对上了：N202', item: items[1] } }
      ck.strangers.push({ kind: 'other', number: 'N900', total: 66, bookMonth: '2026-09', voucher: '深圳星期零-9-230#' })
      return { ok: true, result: 'other', say: '9 月的', msg: '不在这份清单里：N900 这张是 2026-09 做账的（深圳星期零-9-230#）。没有登记，也没有挪' }
    }
    return base(c)
  }
  const init = () => {
    window.__said = []
    window.SpeechSynthesisUtterance = function (t) { this.text = t }
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: { cancel() {}, speak(u) { window.__said.push(u.text) } } })
  }
  const { page, ctx } = await open(browser, B, api, { init })
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.getByRole('button', { name: '对纸质票', exact: true }).click()
  const start = page.locator('.inv-modal', { hasText: '选要对的范围' })
  await start.getByRole('tab', { name: '选认证清单（按做账月份）' }).click()
  await start.locator('.inv-ck-pickrow', { hasText: '深圳星期零 2026-10 认证清单' }).waitFor()
  await start.getByRole('button', { name: '开始对' }).click()
  const dlg = page.locator('.inv-modal').filter({ has: page.locator('.inv-ck') })
  await dlg.getByText('对纸质票 · 深圳星期零 2026-10 认证清单').waitFor()
  assert.deepEqual(ck.started, { listId: 5, entry: 'audit' })
  assert.match(await dlg.locator('#inv-ck-row-201').innerText(), /深圳星期零-10-1#/, '每行写着凭证')
  const scan = async (code) => { const box = dlg.locator('.inv-ck-scan input'); await box.fill(code); await box.press('Enter') }
  await scan('N202')
  await dlg.locator('#inv-ck-row-202.hit').waitFor()
  await scan('N900')
  await dlg.locator('.inv-ck-alert.err', { hasText: '2026-09 做账的' }).waitFor()
  await dlg.locator('.inv-ck-str', { hasText: '深圳星期零-9-230#' }).waitFor()
  assert.deepEqual(await page.evaluate(() => window.__said), ['确认', '9 月的'], '电脑上也念出来')
  await ctx.close()
})

test('抵扣勾选·方式一：清单按做账月份出（请求里带 bookMonth）；预览写凭证；导出过的认证清单列出来、能重新下载、能对纸质票', async (browser, B) => {
  const st = { rows: [], logs: [] }
  const posts = []
  const base = ledgerApi(st)
  const api = (c) => {
    if (c.path === '/api/inv/deduct/tick/prepare') {
      posts.push(c.body)
      return { ok: true, token: 'TOK', buyers: [{ taxId: '91440300X', name: '深圳市星期零食品科技有限公司', count: 1 }],
        summary: { rows: 1, tax: 13.09, amount: 1322, byType: [], warned: 0, marked: 0, skipped: [{ key: 'unbooked', label: '还没做账（系统在金蝶没找到这张票的凭证）', count: 2 }, { key: 'otherMonth', label: '不是这个月做账的（是别的月份的凭证）', count: 1 }] },
        preview: [{ number: '26952000004308149266', date: '2026-09-28', sellerName: '深圳市禾享国际物流有限公司', taxType: '电子发票(增值税专用发票)', amount: 1322, tax: 13.09, buyerTaxId: '91440300X', marked: false, warn: '', bookMonth: '2026-10', voucher: '深圳星期零-10-8#' }] }
    }
    if (c.path === '/api/inv/ticklists') return { ok: true, rows: [{ id: 5, name: '深圳星期零 2026-10 认证清单', rows: 12, bookMonth: '2026-10', by: '测试会计', at: '2026-10-31 08:50:00', tax: 300 }] }
    if (c.path === '/api/inv/check/current') return { ok: true, check: null }
    if (c.path === '/api/inv/other') return { ok: true, count: 0 }
    return base(c)
  }
  const { page, ctx } = await open(browser, B, api)
  await mount(page, 'InvLedger', { user: { name: '测试会计' } })
  await page.getByRole('tab', { name: '抵扣勾选' }).click()
  const sec = page.locator('.inv-lg-sec', { hasText: '方式一' })
  await sec.locator('input[type=month]').fill('2026-10')
  await sec.getByRole('button', { name: '生成清单' }).click()
  await sec.getByText('深圳星期零-10-8#').waitFor()
  assert.equal(posts[0].bookMonth, '2026-10')
  await sec.locator('.inv-lg-warn', { hasText: '还没做账' }).waitFor()
  const tl = sec.locator('.inv-lg-tl')
  await tl.getByText('深圳星期零 2026-10 认证清单').waitFor()
  assert.equal(await tl.locator('a', { hasText: '重新下载' }).getAttribute('href'), '/api/inv/ticklists/5/download')
  assert.equal(await tl.getByRole('button', { name: '对纸质票' }).count(), 1)
  await ctx.close()
})

function ledgerApi(st) {
  return (c) => {
    if (c.path === '/api/inv/config') return cfg()
    if (c.path === '/api/inv/ledger') return { ok: true, total: st.rows.length, sum: { amount: 88.5, tax: 11.5, total: 100 }, rows: st.rows }
    if (c.path.startsWith('/api/inv/folder/')) return { ok: true, folder: { id: 30, title: '付款单丙' }, items: [], logs: st.logs }
    if (/\/void$/.test(c.path)) return st.onVoid(c)
    return { ok: true }
  }
}

test('F3/C3 勾「含作废」只多要作废票（review=withvoid），不再用 all 把草稿/待审拉进来', async (browser, B) => {
  const st = { rows: [inv({ id: 301, review: 'approved', folder: { id: 30 }, folderId: 30 })], logs: [] }
  const { page, ctx, calls } = await open(browser, B, ledgerApi(st))
  await mount(page, 'InvLedger', { user: { name: '测试会计' } })
  await page.locator('.inv-lg-tbl').waitFor()
  await page.getByLabel('含作废').check()
  await page.waitForFunction(() => true)
  await sleep(700)
  const last = calls.filter(c => c.path === '/api/inv/ledger').pop()
  assert.equal(last.query.review, 'withvoid')
  const href = await page.getByRole('link', { name: '导出 Excel' }).getAttribute('href')
  assert.match(href, /review=withvoid/)
  assert.doesNotMatch(href, /review=all/)
  await ctx.close()
})

test('V2.966 刷新全部金蝶凭证：后台跑，按钮显示进度、再点不报错、查完提示，不再写「台账没读出来」', async (browser, B) => {
  const st = { rows: [inv({ id: 301, review: 'approved', folder: { id: 30 }, folderId: 30 })], logs: [] }
  const job = { running: false, total: 0, checked: 0, matched: 0, changed: 0, error: '' }
  let posts = 0
  const base = ledgerApi(st)
  const api = (c) => {
    if (c.path === '/api/inv/vouchers/refresh' && c.method === 'POST') {
      posts++
      if (!job.running) Object.assign(job, { running: true, total: 30, checked: 12 })
      return { ok: true, msg: '', job: { ...job } }
    }
    if (c.path === '/api/inv/vouchers/refresh') return { ok: true, job: { ...job } }
    return base(c)
  }
  const { page, ctx, calls } = await open(browser, B, api)
  await mount(page, 'InvLedger', { user: { name: '测试会计' } })
  await page.locator('.inv-lg-tbl').waitFor()
  await page.getByRole('button', { name: '刷新全部金蝶凭证' }).click()
  await page.getByRole('button', { name: '正在查金蝶 12/30' }).waitFor()
  assert.equal(await page.getByRole('button', { name: /正在查金蝶/ }).isDisabled(), true)
  const before = calls.filter(c => c.path === '/api/inv/ledger').length
  Object.assign(job, { checked: 20 })
  await page.getByRole('button', { name: '正在查金蝶 20/30' }).waitFor({ timeout: 5000 })
  assert.ok(calls.filter(c => c.path === '/api/inv/ledger').length > before, '查到新的就重读这一页')
  Object.assign(job, { running: false, checked: 30, matched: 25, changed: 3 })
  await page.getByText('30 张里对上 25 张，这次有变化的 3 张').waitFor({ timeout: 5000 })
  await page.getByRole('button', { name: '刷新全部金蝶凭证' }).waitFor()
  assert.equal(posts, 1)
  assert.equal(await page.getByText('台账没读出来').count(), 0)
  await ctx.close()
})

test('V2.966 刷新全部：定时同步正占着 → 提示等一下（不当成台账读不出来）；打开页面时后台正在跑就接着显示进度', async (browser, B) => {
  const st = { rows: [inv({ id: 301, review: 'approved', folder: { id: 30 }, folderId: 30 })], logs: [] }
  const base = ledgerApi(st)
  let running = false
  const api = (c) => {
    if (c.path === '/api/inv/vouchers/refresh' && c.method === 'POST') return { ok: false, msg: '系统自己的定时同步正在查金蝶，一两分钟后再点', job: { running: false } }
    if (c.path === '/api/inv/vouchers/refresh') return { ok: true, job: { running, total: 8, checked: 3 } }
    return base(c)
  }
  let r = await open(browser, B, api)
  await mount(r.page, 'InvLedger', { user: { name: '测试会计' } })
  await r.page.locator('.inv-lg-tbl').waitFor()
  await r.page.getByRole('button', { name: '刷新全部金蝶凭证' }).click()
  await r.page.getByText('定时同步正在查金蝶').waitFor()
  assert.equal(await r.page.getByText('台账没读出来').count(), 0)
  await r.ctx.close()
  running = true
  r = await open(browser, B, api)
  await mount(r.page, 'InvLedger', { user: { name: '测试会计' } })
  await r.page.getByRole('button', { name: '正在查金蝶 3/8' }).waitFor()
  await r.ctx.close()
})

const ledgerRow = (o) => inv({ id: 301, review: 'approved', deductStatus: 'checked', folder: { id: 30, title: '付款单丙' }, folderId: 30, ...o })
const LEDGER_LOGS = [{ id: 2, ts: '2026-09-24 10:00:00', user: '李会计', action: '改票面字段', detail: { note: '改了税额' }, itemId: 301 },
  { id: 1, ts: '2026-09-24 09:00:00', user: '王收票', action: '提交审核', detail: {}, itemId: null }]

test('F10/C7 台账详情「留痕」按 log.itemId 列出这张票自己的记录', async (browser, B) => {
  const st = { rows: [ledgerRow()], logs: LEDGER_LOGS }
  const { page, ctx } = await open(browser, B, ledgerApi(st))
  await mount(page, 'InvLedger', { user: { name: '测试会计' } })
  await page.locator('.inv-lg-tbl tbody tr').first().click()
  await page.locator('.inv-lg-side').getByText('改票面字段', { exact: false }).waitFor({ timeout: 3000 })
  assert.equal(await page.locator('.inv-lg-side').getByText('提交审核').count(), 0, '票夹级日志不往单张票里塞')
  await ctx.close()
})

test('台账「做账 / 凭证」直接写凭证的短写法（主体简称-月-号#）；详情里「金蝶做账凭证」同样写法、压成一行', async (browser, B) => {
  const bk = { status: 'booked', source: 'kingdee', at: '2026-10-14 10:20:00', vouchers: [{ book: '深圳市星期零食品科技有限公司', period: '2026-10', number: '记-8' }] }
  const st = { rows: [ledgerRow({ bookkeeping: bk, voucherLabels: ['深圳星期零-10-8#'] }), ledgerRow({ id: 902, number: '26442000000000000902', bookkeeping: { status: 'unbooked', source: 'kingdee', vouchers: [] }, voucherLabels: [] })], logs: [] }
  const { page, ctx } = await open(browser, B, ledgerApi(st))
  await mount(page, 'InvLedger', { user: { name: '测试会计' } })
  const rows = page.locator('.inv-lg-tbl tbody tr')
  await rows.first().locator('.inv-lg-vno', { hasText: '深圳星期零-10-8#' }).waitFor()
  assert.equal(await rows.first().getByText('已匹配凭证').count(), 0, '不再写「已匹配凭证」，直接写凭证')
  assert.equal(await rows.first().locator('.inv-lg-vno').getAttribute('title'), '深圳市星期零食品科技有限公司 · 2026-10 · 记-8', '鼠标停上去看全称')
  assert.match(await rows.nth(1).innerText(), /暂未匹配/)
  await rows.first().click()
  const box = page.locator('.inv-lg-bk')
  await box.locator('.inv-lg-vno', { hasText: '深圳星期零-10-8#' }).waitFor()
  assert.equal(await box.getByText('已匹配到凭证').count(), 0)
  assert.equal(await box.getByRole('button', { name: '刷新' }).count(), 1)
  await ctx.close()
})

test('F4/F13 台账作废：弹窗挂在 body；作废后「进项税额转出」提醒常驻页面', async (browser, B) => {
  const st = { rows: [ledgerRow()], logs: [] }
  st.onVoid = () => ({ ok: true, msg: '已作废，号码已放出来（以后这张票可以重新登记）；这张票已在税局勾选抵扣，记得在税局做进项税额转出',
    item: ledgerRow({ review: 'void' }) })
  const { page, ctx } = await open(browser, B, ledgerApi(st))
  await mount(page, 'InvLedger', { user: { name: '测试会计' } })
  await page.locator('.inv-lg-tbl tbody tr').first().click()
  await page.getByRole('button', { name: '作废这张票' }).click()
  await page.locator('.inv-ov').waitFor()
  const onBody = await page.evaluate(() => document.querySelector('.inv-ov').parentElement === document.body)
  await page.locator('.inv-ov textarea').fill('重复报销')
  await page.getByRole('button', { name: '确认作废' }).click()
  await page.getByText('记得在税局做进项税额转出', { exact: false }).first().waitFor({ timeout: 3000 })
  await sleep(3200)   // toast 2.6 秒就没了；提醒要留在页面上
  assert.ok(await page.getByText('记得在税局做进项税额转出', { exact: false }).first().isVisible(), '进项税额转出提醒不能 2 秒就消失')
  assert.equal(onBody, true, '作废弹窗要挂在 body（台账右侧详情栏是 sticky，弹窗在里面会被左侧导航盖住）')
  await ctx.close()
})

test('F7 设置：名单里没绑工作台账号的人挡保存并在行内说清楚；催票天数按后端范围', async (browser, B) => {
  const settings = {
    people: [{ account: '', dtUserid: 'u1', dtName: '赵六', receiver: false }],
    company: [{ name: '合成测试有限公司', taxId: '91440300MA5DXXXXX1' }], templates: [],
    remind: { enabled: true, beforeDays: 3, everyDays: 7, hour: 10 }, blockNoInvoice: false, portalUrl: '',
  }
  const api = (c) => {
    if (c.path === '/api/inv/settings' && c.method === 'GET') return { ok: true, settings }
    if (c.path === '/api/inv/accounts') return { ok: true, rows: [{ name: '李会计' }] }
    if (c.path === '/api/inv/settings' && c.method === 'POST') return { __status: 400, ok: false, msg: '财务人员名单第 1 行没填工作台账号' }
    return { ok: true }
  }
  const { page, ctx, calls } = await open(browser, B, api)
  await mount(page, 'InvSettings', { user: { name: '管理员' } })
  await page.getByText('赵六').waitFor()
  const before = page.locator('.inv-set-rule input').first()
  await before.fill('45')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await sleep(400)
  assert.equal(n(calls, c => c.path === '/api/inv/settings' && c.method === 'POST'), 0, '前端就该挡住（后端会整张拒掉）')
  assert.ok(await page.getByText('请选他的工作台账号', { exact: false }).first().isVisible())
  assert.ok(await page.getByText('填 0～30 的整数').isVisible())
  await ctx.close()
})

function laterApi(st) {
  return (c) => {
    if (c.path === '/api/inv/config') return cfg()
    if (c.path === '/api/inv/later') return { ok: true, total: st.rows.length, rows: st.rows }
    if (c.path === '/api/inv/later/resolve') return { ok: true, prefill: { instId: 'inst-1', amount: 500, payee: { name: '供应商甲' }, template: '付款申请（公对公）', businessId: '202609240099' } }
    if (c.path === '/api/inv/later/create') return st.create
    if (/^\/api\/inv\/later\/\d+$/.test(c.path)) return { ok: true, later: st.rows[0] || { id: 7, status: 'open' }, items: [], docs: [], logs: [] }
    if (/receive-upload$/.test(c.path)) return { ok: true, results: [{ name: 'camera.jpg', action: 'item', msg: '已登记' }], later: st.rows[0] }
    return { ok: true }
  }
}

test('申请人上传发票：传完弹 5 秒提醒（如果为专票打印 2 张、普票 1 张，张数红字）；关掉后能看到传了哪些，传错的能删、财务确认过的不能删', async (browser, B) => {
  const later = { id: 5, status: 'open', businessId: '202610120001', payee: { name: '合成物流' }, expectAmount: 600, receivedAmount: 0, unregisteredAmount: 0,
    remaining: 600, expectDate: '2026-10-20', receiverName: '财务甲', filedVia: 'self', createdAt: '2026-10-12 10:00:00', selfCount: 0, selfAmount: 0 }
  const tk = (id, o) => ({ id, kind: 'invoice', number: '2644200000000000000' + id, typeLabel: '电子发票（增值税专用发票）', total: 600, mine: true, pending: true, canRemove: true, procStatus: 'done', ...o })
  const st = { laters: [later], tickets: [tk(8, { total: 100, pending: false, canRemove: false })] }
  const api = async c => {
    if (c.path === '/api/inv/s/hello') return { ok: true, corpId: '', dingtalk: true, me: { name: '申请人甲', dept: '公司-采购部', via: 'code' }, templates: [] }
    if (c.path === '/api/inv/s/later/5/invoices' && c.method === 'GET') return { ok: true, rows: st.tickets }
    if (c.path === '/api/inv/s/later/5/invoices/9/remove') { st.tickets = st.tickets.filter(x => x.id !== 9); st.laters = [later]; return { ok: true, msg: '已删除', later } }
    if (c.path === '/api/inv/s/payments') return { ok: true, msg: '', rows: [] }
    if (c.path === '/api/inv/s/receivers') return { ok: true, rows: [] }
    if (c.path === '/api/inv/s/laters') return { ok: true, rows: st.laters }
    if (c.path === '/api/inv/s/later/5/invoices') {
      st.laters = [{ ...later, selfCount: 1, selfAmount: 600 }]
      st.tickets = [...st.tickets, tk(9)]
      return { ok: true, uploaded: 1, later: st.laters[0], results: [{ name: '票.pdf', action: 'item', msg: '已登记', item: { id: 9, procStatus: 'done' } },
        { name: '合同.pdf', action: 'other', msg: '已留存（不是发票：合同）', item: { id: 10, procStatus: 'done' } }] }
    }
    return { ok: true }
  }
  const { page, ctx, calls, errors } = await open(browser, B, api, { hash: '#/invself', clock: true, init: () => { sessionStorage.setItem('inv_self_t', 'SELF-TOK') } })
  await mount(page, 'InvSelf')
  await page.getByRole('button', { name: /我的后补单/ }).click()
  assert.equal(await page.locator('.inv-sf-tip').count(), 0)
  const pdf = n => ({ name: n, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 fake') })
  await page.locator('label.inv-sf-upl.pri input[type=file]').setInputFiles([pdf('票.pdf'), pdf('合同.pdf')])
  const tip = page.locator('.inv-sf-tip')
  await tip.waitFor({ timeout: 4000 })
  assert.deepEqual(await tip.locator('li').allTextContents(), ['如果为专票，请打印 2 张，交给 财务甲', '如果为普票，请打印 1 张，交给 财务甲'])
  const reds = await tip.locator('li > b').evaluateAll(els => els.map(e => [e.textContent, getComputedStyle(e).color.match(/\d+/g).map(Number)]))
  assert.deepEqual(reds.map(r => r[0]), ['2 张', '1 张'])
  for (const [, [r, g, b]] of reds) assert.ok(r > 170 && g < 110 && b < 110, '张数要是红字，实际 rgb(' + [r, g, b] + ')')
  assert.ok(await tip.getByText('5 秒后自动关闭').isVisible())
  for (let i = 0; i < 6; i += 1) { await page.clock.runFor(500); await sleep(25) }
  assert.ok(await tip.getByText('2 秒后自动关闭').isVisible(), '3 秒时还在')
  for (let i = 0; i < 5; i += 1) { await page.clock.runFor(500); await sleep(25) }
  await tip.waitFor({ state: 'detached', timeout: 2000 })
  // 关了之后同样的话还留在页面上；没算发票的那份把原因说出来；这张单写着等财务确认
  const note = page.locator('.inv-sf-note')
  assert.ok((await note.textContent()).includes('如果为专票，请打印 2 张；如果为普票，请打印 1 张；交给 财务甲'), await note.textContent())
  assert.ok((await note.textContent()).includes('「合同.pdf」已留存（不是发票：合同）'))
  assert.ok(await page.getByText('已上传 600.00，等财务确认').isVisible())
  const up = calls.filter(c => c.path === '/api/inv/s/later/5/invoices' && c.method === 'POST')
  assert.equal(up.length, 1); assert.equal(up[0].headers['x-inv-self'], 'SELF-TOK')
  // 传完自动把这张单上的发票列出来：刚传的等财务确认、能删；财务确认过的那张不能删
  const lis = page.locator('.inv-sf-tk li')
  await lis.nth(1).waitFor({ timeout: 3000 })
  assert.ok((await lis.nth(0).textContent()).includes('财务已确认')); assert.ok((await lis.nth(0).textContent()).includes('不能删了'))
  assert.equal(await lis.nth(0).getByRole('button', { name: '删除' }).count(), 0)
  assert.ok((await lis.nth(1).textContent()).includes('26442000000000000009')); assert.ok((await lis.nth(1).textContent()).includes('等财务确认'))
  await lis.nth(1).getByRole('button', { name: '删除' }).click()       // 确认框由夹具自动点「确定」
  await page.locator('.inv-sf-note', { hasText: '已删除一张发票' }).waitFor({ timeout: 3000 })
  assert.equal(calls.filter(c => c.path === '/api/inv/s/later/5/invoices/9/remove' && c.method === 'POST').length, 1)
  await page.waitForFunction(() => document.querySelectorAll('.inv-sf-tk li').length === 1, null, { timeout: 3000 })
  assert.equal(await page.getByText('已上传 600.00，等财务确认').count(), 0)
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('后补单详情：钉钉流程里取到的发票（财务初审后才到）标出来、也是待确认；「去钉钉找发票」能点', async (browser, B) => {
  const base = { id: 7, instId: 'PI-QA-LATE', status: 'open', payee: { name: '合成物流' }, payAmount: 600, expectAmount: 600, receivedAmount: 0, unregisteredAmount: 0,
    remaining: 600, expectDate: '2026-10-20', invKind: 'special', receiverName: '财务甲', reason: '运费' }
  const late = { at: '2026-10-13 10:19:00', after: '2026-10-12 13:51:00', by: 'first' }
  const st = { later: { ...base, selfCount: 0, selfAmount: 0, flowCount: 0 }, items: [] }
  const api = c => {
    if (c.path === '/api/inv/config') return cfg()
    if (c.path === '/api/inv/later') return { ok: true, total: 1, rows: [st.later] }
    if (c.path === '/api/inv/later/7') return { ok: true, later: st.later, items: st.items, docs: [], logs: [] }
    if (c.path === '/api/inv/later/7/flow-check') {
      st.later = { ...base, selfCount: 1, selfAmount: 600, flowCount: 1 }
      st.items = [inv({ id: 41, origin: 'attachment', selfPending: true, fromFlow: true, laterId: 7, total: 600, flags: { lateFlow: late } })]
      return { ok: true, msg: '钉钉流程里有 1 张财务初审后才到的发票，已记到后补单上、进了发票审核', new: 1, linked: 1, later: st.later }
    }
    return { ok: true }
  }
  const { page, ctx, calls, errors } = await open(browser, B, api, { hash: '#/invlater?id=7' })
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  const btn = page.locator('.inv-modal').getByRole('button', { name: '去钉钉找发票' })
  await btn.waitFor({ timeout: 4000 })
  assert.equal(await page.locator('.inv-lt-selfbar').count(), 0, '还没取到票时不出提示条')
  await btn.click()
  const bar = page.locator('.inv-lt-selfbar')
  await bar.waitFor({ timeout: 4000 })
  assert.equal(calls.filter(c => c.path === '/api/inv/later/7/flow-check' && c.method === 'POST').length, 1)
  const t = await bar.textContent()
  assert.ok(t.includes('申请人已经交来 1 张发票（600.00）') && t.includes('其中 1 张是系统从钉钉流程里取到的'), t)
  assert.ok(await page.locator('.inv-modal .inv-badge', { hasText: '钉钉流程里到了 600.00 待确认' }).first().isVisible())
  assert.ok(await page.locator('.inv-lt-cur .inv-badge', { hasText: '钉钉流程里取到 · 待确认' }).isVisible())
  assert.ok(await page.locator('.inv-lt-cur .inv-badge', { hasText: '初审后才到' }).isVisible())
  assert.equal((await page.locator('.inv-lt-nums > div').nth(2).locator('b').textContent()), '0.00', '审核/确认前不算已到')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('后补单详情：申请人传上来的发票要财务点「确认收到」才算已到', async (browser, B) => {
  const base = { id: 6, status: 'open', payee: { name: '合成物流' }, payAmount: 600, expectAmount: 600, receivedAmount: 0, unregisteredAmount: 0, remaining: 600,
    expectDate: '2026-10-20', invKind: 'special', receiverName: '财务甲', reason: '运费' }
  const st = { later: { ...base, selfCount: 2, selfAmount: 600 }, items: [inv({ id: 31, origin: 'self', selfPending: true, total: 300 }), inv({ id: 32, origin: 'self', selfPending: true, total: 300 })] }
  const api = c => {
    if (c.path === '/api/inv/config') return cfg()
    if (c.path === '/api/inv/later') return { ok: true, total: 1, rows: [st.later] }
    if (c.path === '/api/inv/later/6') return { ok: true, later: st.later, items: st.items, docs: [], logs: [] }
    if (c.path === '/api/inv/later/6/confirm-self') {
      st.later = { ...base, status: 'done', receivedAmount: 600, remaining: 0, selfCount: 0, selfAmount: 0 }
      st.items = st.items.map(x => ({ ...x, selfPending: false }))
      return { ok: true, msg: '已确认收到 2 张', later: st.later }
    }
    return { ok: true }
  }
  const { page, ctx, calls, errors } = await open(browser, B, api, { hash: '#/invlater?id=6' })
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  const bar = page.locator('.inv-lt-selfbar')
  await bar.waitFor({ timeout: 4000 })
  assert.ok((await bar.textContent()).includes('申请人已经传上来 2 张发票（600.00）'))
  assert.ok(await page.locator('.inv-modal .inv-badge', { hasText: '申请人已传 600.00 待确认' }).first().isVisible())
  assert.ok(await page.locator('.inv-lt-cur .inv-badge', { hasText: '申请人上传 · 待确认' }).isVisible())
  assert.equal((await page.locator('.inv-lt-nums > div').nth(2).locator('b').textContent()), '0.00', '确认前不算已到')
  await bar.getByRole('button', { name: '确认收到' }).click()
  await bar.waitFor({ state: 'detached', timeout: 4000 })
  assert.equal(calls.filter(c => c.path === '/api/inv/later/6/confirm-self' && c.method === 'POST').length, 1)
  assert.equal((await page.locator('.inv-lt-nums > div').nth(2).locator('b').textContent()), '600.00')
  assert.equal(await page.locator('.inv-lt-cur .inv-badge', { hasText: '待确认' }).count(), 0)
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('后补单详情：金额排成一排、字段分两张卡、长事由按编号断行并先收起', async (browser, B) => {
  const reason = '一、费用名称：样品甲 1、发货时间：8月10日 2、费用明细：首重89+22*41斤=991元 3、物流查询单号：HX001 4、签收时间：8月13日 ' +
    '二、费用名称：样品乙 1、发货时间：8月25日 2、费用明细：单价 12.5 元，共 3 箱 3、物流查询单号：HX002 4、签收时间：8月28日'
  const later = {
    id: 2, status: 'partial', overdue: true, daysLeft: -2, businessId: '202609231721000398295', template: '付款申请（公对公）',
    applicant: '申请人甲', dept: '合成公司-供应中心-物流部', company: '合成公司', payee: { name: '合成物流', bank: '合成银行某支行', account: '8110301014200104827' },
    payAmount: 1322, expectAmount: 1322, receivedAmount: 800, unregisteredAmount: 200, remaining: 322, expectDate: '2026-10-10', invKind: 'special', taxRate: '1%',
    receiverName: '财务甲', filedBy: '申请人甲', filedVia: 'self', createdAt: '2026-10-12 12:18:00', reason, note: '',
  }
  const { page, ctx, errors } = await open(browser, B, laterApi({ rows: [later] }), { hash: '#/invlater?id=2' })
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  await page.locator('.inv-lt-nums').waitFor({ timeout: 4000 })
  const tiles = await page.locator('.inv-lt-nums > div').evaluateAll(els => els.map(e => [e.querySelector('span').textContent, e.querySelector('b').textContent, e.className]))
  assert.deepEqual(tiles.map(t => t[0]), ['付款金额', '预计到票金额', '已到', '已收未登记', '还差', '预计到票日'])
  assert.deepEqual([tiles[2][1], tiles[3][1], tiles[4][1]], ['800.00', '200.00', '322.00'])
  assert.equal(tiles[4][2], 'hot'); assert.equal(tiles[5][2], 'err', '超期的到票日标红')
  assert.ok(await page.locator('.inv-lt-nums em', { hasText: '超期2天' }).isVisible())
  assert.equal(await page.locator('.inv-lt-prog b').textContent(), '61%')
  assert.equal(await page.locator('.inv-lt-cards .inv-lt-card').count(), 2)
  // 账号、审批编号各占一行不折断
  for (const v of ['8110301014200104827', '202609231721000398295']) {
    const h = await page.getByText(v, { exact: true }).evaluate(e => e.getBoundingClientRect().height)
    assert.ok(h < 26, v + ' 折行了，高 ' + h)
  }
  // 事由：在「一、」「1、」这类编号前断行；小数 12.5 不能被当成编号断开
  const lines = await page.locator('.inv-lt-reason-tx p').allTextContents()
  assert.equal(lines.length, 10)
  assert.equal(lines[0], '一、费用名称：样品甲'); assert.equal(lines[5], '二、费用名称：样品乙')
  assert.ok(lines.includes('2、费用明细：单价 12.5 元，共 3 箱'), JSON.stringify(lines))
  assert.equal(lines.join(''), reason.replace(/\s+(?=(?:[一二]|\d)、)/g, ''), '只加换行，内容一个字不少')
  assert.ok(await page.locator('.inv-lt-reason-tx.fold').count(), '长事由先收起')
  await page.getByRole('button', { name: '展开全部（共 10 行）' }).click()
  assert.equal(await page.locator('.inv-lt-reason-tx.fold').count(), 0)
  assert.ok(await page.getByRole('button', { name: '收起' }).isVisible())
  assert.equal(await page.locator('.inv-lt-note').count(), 0, '没有备注就不占地方')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('F12/C5 新建后补单：接收人没收到钉钉消息要如实说，不能报「已通知」', async (browser, B) => {
  const st = {
    rows: [],
    create: { ok: true, later: { id: 7, status: 'open', payee: { name: '供应商甲' } }, notify: { sent: false, msg: '对方没在设置里绑定钉钉，没发通知' },
      notified: false, notifyMsg: '对方没在设置里绑定钉钉，没发通知', msg: '已登记后补单 #7（接收人没收到钉钉消息：对方没在设置里绑定钉钉，没发通知）' },
  }
  const { page, ctx } = await open(browser, B, laterApi(st))
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  await page.getByRole('button', { name: '新建后补单' }).click()
  const scan = page.locator('.inv-modal input[data-inv-scan="1"]')
  await scan.fill('202609240099')
  await scan.press('Enter')
  await page.getByText('供应商甲').first().waitFor()
  await page.locator('.inv-modal input[type="date"]').fill('2026-10-10')
  await page.getByRole('button', { name: '登记后补单' }).click()
  await page.locator('.inv-lt-fe', { hasText: '请选税率' }).waitFor({ timeout: 3000 })
  await page.locator('.inv-modal select').first().selectOption('13%')
  await page.getByRole('button', { name: '登记后补单' }).click()
  await page.getByText('接收人没收到钉钉消息', { exact: false }).first().waitFor({ timeout: 3000 })
  assert.equal(await page.getByText('已通知接收人', { exact: false }).count(), 0)
  await ctx.close()
})

test('F17 「收到」弹窗可以用高拍仪拍，照片走 receive-upload', async (browser, B) => {
  const st = { rows: [{ id: 8, status: 'open', payee: { name: '供应商乙' }, payAmount: 300, expectAmount: 300, receivedAmount: 0, invKind: 'special' }] }
  const { page, ctx, calls } = await open(browser, B, laterApi(st))
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  await page.locator('.inv-lt-tbl').getByRole('button', { name: '收到' }).click()
  await page.getByRole('button', { name: '用高拍仪拍' }).click()
  const shoot = page.locator('.inv-modal').getByRole('button', { name: '拍照' })
  await shoot.waitFor()
  await page.waitForFunction(() => { const b = [...document.querySelectorAll('.inv-modal button')].find(x => x.textContent === '拍照'); return b && !b.disabled }, null, { timeout: 8000 })
  await shoot.click()
  await page.waitForFunction(() => document.body.innerText.includes('已登记'), null, { timeout: 5000 })
  const up = calls.find(c => c.path === '/api/inv/later/8/receive-upload')
  assert.ok(up, '拍的照片要传到 receive-upload')
  assert.match(String(up.body), /filename="camera\.jpg"/)
  await ctx.close()
})

function phoneApi(st) {
  return (c) => {
    const tok = c.headers['x-inv-pair']
    st.tokens.push(c.path + ' ' + tok)
    if (c.path === '/api/inv/m/hello') return tok === 'QR1' || tok === 'SESS2' ? { ok: true, bound: tok === 'SESS2', user: '测试收票员', corpId: '' } : { __status: 401, ok: false, msg: '手机配对已失效（过期或已断开）' }
    if (c.path === '/api/inv/m/bind') return st.bind(tok)
    if (c.path === '/api/inv/m/state') return tok === 'SESS2'
      ? { ok: true, user: '测试收票员', folder: { id: 1, title: '手机票夹', stats: { invoices: 1 } }, items: [{ id: 1, kind: 'other', file: { id: 55, thumb: null, name: '清单.xlsx' }, flags: {} }] }
      : { __status: 401, ok: false, msg: '手机配对已失效（过期或已断开）：请在电脑上重新点「手机当相机」生成配对码' }
    return { __status: 404, ok: false, msg: 'nope' }
  }
}

test('C1/F15 手机配对后换用会话令牌；没有缩略图的文件显示「无预览」、不去硬取', async (browser, B) => {
  const st = { tokens: [], bind: tok => (tok === 'QR1' ? { ok: true, user: '测试收票员', dtName: '', msg: '没认出手机上的钉钉身份', session: 'SESS2' } : { __status: 409, ok: false, msg: '这个配对码已经用过了，请在电脑上重新点「手机当相机」' }) }
  const { page, ctx, calls } = await open(browser, B, phoneApi(st), { hash: '#/invpair?t=QR1' })
  await mount(page, 'InvPhone')
  await page.getByText('手机票夹').waitFor({ timeout: 4000 })
  await sleep(2500)
  const states = calls.filter(c => c.path === '/api/inv/m/state')
  assert.ok(states.length >= 1)
  assert.ok(states.every(c => c.headers['x-inv-pair'] === 'SESS2'), '配对后要用会话令牌：' + states.map(c => c.headers['x-inv-pair']).join(','))
  assert.equal(await page.evaluate(() => sessionStorage.getItem('inv_pair_t')), 'SESS2')
  assert.ok(await page.locator('.inv-ph-item-img', { hasText: '无预览' }).isVisible())
  assert.equal(n(calls, c => c.path.startsWith('/api/inv/m/file/')), 0, '没有缩略图就别去取（必然 404）')
  await ctx.close()
})

test('C1 配对码已被用过（409）：停在「用过了」，不给没用的重试', async (browser, B) => {
  const st = { tokens: [], bind: () => ({ __status: 409, ok: false, msg: '这个配对码已经用过了，请在电脑上重新点「手机当相机」' }) }
  const { page, ctx } = await open(browser, B, phoneApi(st), { hash: '#/invpair?t=QR1' })
  await mount(page, 'InvPhone')
  await page.locator('.inv-ph-stop-t', { hasText: '这个配对码已经用过了' }).waitFor({ timeout: 4000 })
  assert.equal(await page.getByRole('button', { name: '重新试一次' }).count(), 0)
  await ctx.close()
})

// 钉钉里打开手机页：假的 dd（记下 dd.config 参数、扫一扫回一条审批链接）
const DD_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 Mobile/21G93 AliApp(DingTalk/9.0.1)'
const FAKE_DD = () => {
  window.__ddCfg = null; window.__ddScans = 0
  window.dd = {
    config: c => { window.__ddCfg = c },
    ready: cb => setTimeout(cb, 10),
    error: () => {},
    runtime: { permission: { requestAuthCode: o => o.onSuccess({ code: 'AUTH1' }) } },
    biz: { util: { scan: o => { window.__ddScans++; setTimeout(() => o.onSuccess({ text: 'https://aflow.dingtalk.com/qr/FAKEQR' }), 10) } } },
  }
}
const ddPhoneApi = (jsconfig) => async c => {
  if (c.path === '/api/inv/m/hello') return { ok: true, bound: true, user: '测试收票员', dtName: '张三', corpId: 'dingFAKE' }
  if (c.path === '/api/inv/m/state') return { ok: true, user: '测试收票员', folder: { id: 7, title: '合成付款单', stats: { invoices: 0 } }, items: [] }
  if (c.path === '/api/inv/m/jsconfig') return jsconfig(c)
  if (c.path === '/api/inv/m/scan') return { ok: true, action: 'folder', msg: '这是审批单：已打开票夹「合成付款单」', folder: { id: 7, title: '合成付款单' } }
  return { __status: 404, ok: false, msg: 'nope' }
}

test('扫审批单：钉钉里先按本页地址鉴权，再调「扫一扫」实时扫码，扫到的码直接开票夹（不用拍照）', async (browser, B) => {
  const { page, ctx, calls } = await open(browser, B, ddPhoneApi(() => ({ ok: true, agentId: '123', corpId: 'dingFAKE', timeStamp: '1700000000000', nonceStr: 'n1', signature: 'sig1' })),
    { hash: '#/invpair?t=SESS9', ua: DD_UA, init: FAKE_DD })
  await mount(page, 'InvPhone')
  await page.getByText('合成付款单').first().waitFor({ timeout: 4000 })
  await page.getByRole('button', { name: '扫审批单' }).click()
  await page.getByText('已打开票夹').waitFor({ timeout: 4000 })
  const cfgArgs = await page.evaluate(() => window.__ddCfg)
  assert.ok(cfgArgs && cfgArgs.jsApiList.includes('biz.util.scan'), '要先 dd.config：' + JSON.stringify(cfgArgs))
  assert.equal(cfgArgs.signature, 'sig1'); assert.equal(cfgArgs.agentId, '123'); assert.equal(cfgArgs.corpId, 'dingFAKE')
  const jc = calls.find(c => c.path === '/api/inv/m/jsconfig')
  assert.ok(jc && !jc.query.url.includes('#'), '签名地址不能带 #：' + (jc && jc.query.url))
  const sc = calls.filter(c => c.path === '/api/inv/m/scan')
  assert.equal(sc.length, 1)
  assert.equal(sc[0].body.code, 'https://aflow.dingtalk.com/qr/FAKEQR')
  assert.equal(n(calls, c => c.path === '/api/inv/m/upload'), 0, '实时扫码成功就不该走拍照上传')
  assert.equal(await page.locator('.inv-ph-fb').count(), 0)
  await ctx.close()
})

test('扫审批单：钉钉鉴权没过 → 说清原因、给「拍审批单二维码」按钮，不去调扫一扫', async (browser, B) => {
  const { page, ctx, calls } = await open(browser, B, ddPhoneApi(() => ({ ok: false, msg: '还不知道公司的钉钉企业编号' })),
    { hash: '#/invpair?t=SESS9', ua: DD_UA, init: FAKE_DD })
  await mount(page, 'InvPhone')
  await page.getByText('合成付款单').first().waitFor({ timeout: 4000 })
  await page.getByRole('button', { name: '扫审批单' }).click()
  await page.locator('.inv-ph-fb', { hasText: '还不知道公司的钉钉企业编号' }).waitFor({ timeout: 4000 })
  assert.ok(await page.getByRole('button', { name: '拍审批单二维码' }).isVisible())
  assert.equal(await page.evaluate(() => window.__ddScans), 0)
  assert.equal(n(calls, c => c.path === '/api/inv/m/scan'), 0)
  await ctx.close()
})

// ───────────────────────── 申请人自助登记发票后补（V2.621，#/invself） ─────────────────────────

function selfApi(st) {
  return async c => {
    if (c.path === '/api/inv/s/hello') return { ok: true, corpId: st.corpId || '', dingtalk: true, me: st.me || null, templates: ['付款申请（公对公）', '费用报销'] }
    if (c.path === '/api/inv/s/jsconfig') return { ok: true, agentId: '9', corpId: 'dingSELF', timeStamp: '1', nonceStr: 'n', signature: 's9' }
    if (c.path === '/api/inv/s/login/dd') { st.me = { name: '申请人甲', dept: '公司-采购部', via: 'dingtalk' }; return { ok: true, token: 'SELF-TOK', me: st.me } }
    if (c.path === '/api/inv/s/login/send') {
      if (c.body.name === '重名人' && (c.body.pick === null || c.body.pick === undefined)) return { ok: true, need: 'pick', choices: [{ i: 0, dept: '公司-销售部', title: '' }, { i: 1, dept: '公司-生产部', title: '' }] }
      st.sent = c.body
      return { ok: true, ticket: 'TK1', to: c.body.name + '（采购部）', expiresIn: 300 }
    }
    if (c.path === '/api/inv/s/login/verify') {
      if (c.body.code !== '123456') return { __status: 400, ok: false, msg: '验证码不对：请核对钉钉消息里的 6 位数字' }
      st.me = { name: '申请人甲', dept: '公司-采购部', via: 'code' }
      return { ok: true, token: 'SELF-TOK', me: st.me }
    }
    if (c.headers['x-inv-self'] !== 'SELF-TOK') return { __status: 401, ok: false, msg: '登录已过期或没登录' }
    if (c.path === '/api/inv/s/payments') return { ok: true, msg: '', rows: st.pays }
    if (c.path === '/api/inv/s/receivers') return { ok: true, rows: [{ account: 'cw1', name: '财务甲' }, { account: 'cw2', name: '财务乙' }] }
    if (c.path === '/api/inv/s/laters') return { ok: true, rows: st.laters }
    if (c.path === '/api/inv/s/later') {
      st.created = c.body
      const l = { id: 31, businessId: '202609250001', payee: { name: '合成供应商' }, expectAmount: c.body.expectAmount, receivedAmount: 0,
        unregisteredAmount: 0, remaining: c.body.expectAmount, expectDate: c.body.expectDate, receiverName: '财务乙', status: 'open', filedVia: 'self' }
      st.laters = [l]; st.pays = st.pays.map(p => p.procInstId === c.body.instId ? { ...p, laterId: 31 } : p)
      return { ok: true, later: l, notified: true }
    }
    if (c.path === '/api/inv/s/logout') return { ok: true }
    return { __status: 404, ok: false, msg: 'nope' }
  }
}
const SELF_PAYS = () => [
  { procInstId: 'PI-A', businessId: '202609250001', title: '申请人甲提交的付款申请（公对公）', template: '付款申请（公对公）', createTime: '2026-09-20 10:00', amount: 800, payeeName: '合成供应商', laterId: null, hasInvoice: false, approvalStatus: 'COMPLETED', approvalResult: 'agree' },
  { procInstId: 'PI-B', businessId: '202609250002', title: '申请人甲提交的费用报销', template: '费用报销', createTime: '2026-09-18 09:00', amount: 66.5, payeeName: '申请人甲', laterId: 12, laterStatus: 'done', hasInvoice: false },
  { procInstId: 'PI-C', businessId: '202609250003', title: '申请人甲提交的付款申请（公对公）', template: '付款申请（公对公）', createTime: '2026-09-10 09:00', amount: 12345.6, payeeName: '另一家合成物流有限公司', laterId: null, hasInvoice: false, approvalStatus: 'RUNNING' },
]

test('自助登记：电脑浏览器写姓名收钉钉验证码登录 → 选自己的付款单 → 默认值带好 → 提交进「我的后补单」', async (browser, B) => {
  const st = { pays: SELF_PAYS(), laters: [] }
  const { page, ctx, calls, errors } = await open(browser, B, selfApi(st), { hash: '#/invself', clock: true })
  await mount(page, 'InvSelf')
  await page.getByPlaceholder('你在钉钉上的姓名').fill('申请人甲')
  await page.getByRole('button', { name: '发验证码到钉钉' }).click()
  await page.getByText('验证码已发到').waitFor({ timeout: 4000 })
  assert.equal(st.sent.name, '申请人甲')
  await page.getByPlaceholder('6 位数字').fill('000000')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByText('验证码不对').waitFor({ timeout: 4000 })
  await page.getByPlaceholder('6 位数字').fill('123456')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByText('申请人甲提交的付款申请（公对公）').first().waitFor({ timeout: 4000 })
  // 搜索＋分类：默认只看未登记；按模板分；搜收款方/金额
  assert.equal(await page.locator('.inv-sf-pay').count(), 2, '默认只列未登记的')
  assert.ok(await page.getByText('审批中').isVisible())
  await page.getByRole('button', { name: '已登记后补（1）' }).click()
  assert.ok(await page.getByRole('button', { name: '已登记 #12 · 已收齐' }).isVisible(), '登记过的单不能再登记')
  await page.getByRole('button', { name: '全部', exact: true }).click()
  await page.getByRole('button', { name: '费用报销（1）' }).click()
  assert.equal(await page.locator('.inv-sf-pay').count(), 1)
  await page.getByRole('button', { name: '全部（3）' }).click()
  await page.getByPlaceholder('搜标题、审批编号、收款方、金额').fill('12,345')
  assert.equal(await page.locator('.inv-sf-pay').count(), 1, '按金额搜（带不带逗号都行）')
  await page.getByPlaceholder('搜标题、审批编号、收款方、金额').fill('合成供应商')
  assert.equal(await page.locator('.inv-sf-pay').count(), 1)
  await page.getByPlaceholder('搜标题、审批编号、收款方、金额').fill('没有这家')
  await page.getByText('没有符合条件的单子').waitFor({ timeout: 2000 })
  await page.getByPlaceholder('搜标题、审批编号、收款方、金额').fill('')
  await page.getByRole('button', { name: /^未登记后补/ }).click()
  const before = calls.filter(c => c.path === '/api/inv/s/payments').length
  await page.locator('.inv-sf-days').selectOption('120')
  await page.waitForFunction(n => true, before)
  for (let i = 0; i < 30 && calls.filter(c => c.path === '/api/inv/s/payments').length === before; i++) await sleep(100)
  assert.equal(calls.filter(c => c.path === '/api/inv/s/payments').pop().query.days, '120', '改时间范围重新取')
  await page.getByText('合成供应商').first().waitFor({ timeout: 4000 })
  await page.locator('.inv-sf-pay', { hasText: '合成供应商' }).getByRole('button', { name: '登记后补', exact: true }).click()
  assert.equal(await page.locator('input[type=date]').inputValue(), '2026-10-09', '预计到票默认 15 天后')
  assert.equal(await page.locator('.inv-sf-f input[inputmode=decimal]').inputValue(), '800', '预计金额默认付款金额')
  await page.getByRole('button', { name: '提交后补单' }).click()
  await page.getByText('请选发票交给哪位财务').waitFor({ timeout: 3000 })
  await page.getByText('请选税率').waitFor({ timeout: 3000 })
  assert.equal(st.created, undefined, '没选接收人、没选税率不能提交')
  await page.getByRole('button', { name: '收据' }).click()
  assert.equal(await page.getByText('请选税率').count(), 0, '收据不用填税率')
  await page.getByRole('button', { name: '普票' }).click()
  await page.locator('.inv-sf-f select').first().selectOption('3%')
  await page.locator('.inv-sf-f select').nth(1).selectOption('cw2')
  await page.getByRole('button', { name: '提交后补单' }).click()
  await page.locator('.inv-sf-ok', { hasText: '已登记后补单 #31' }).waitFor({ timeout: 4000 })
  assert.deepEqual({ ...st.created, expectDate: undefined },
    { instId: 'PI-A', invKind: 'normal', taxRate: '3%', expectDate: undefined, expectAmount: 800, receiver: 'cw2', note: '' })
  assert.ok(await page.locator('.inv-sf-tbl').getByText('财务乙').isVisible(), '提交后跳到「我的后补单」')
  // 我的后补单：搜索＋按状态分类
  await page.getByPlaceholder('搜审批编号、收款方、交给谁、金额、日期').fill('没有这家')
  await page.getByText('没有符合条件的后补单').waitFor({ timeout: 2000 })
  await page.getByPlaceholder('搜审批编号、收款方、交给谁、金额、日期').fill('合成供应')
  assert.equal(await page.locator('.inv-sf-tbl tbody tr').count(), 1)
  await page.getByRole('button', { name: '我还欠的票（1）' }).click()
  assert.equal(await page.locator('.inv-sf-tbl tbody tr').count(), 1)
  assert.equal(await page.getByText('号码待登记').count(), 0, '财务内部的「号码待登记」不出现在申请人页')
  const authed = calls.filter(c => ['/api/inv/s/payments', '/api/inv/s/later', '/api/inv/s/laters'].includes(c.path))
  assert.ok(authed.length >= 3 && authed.every(c => c.headers['x-inv-self'] === 'SELF-TOK'), '会话令牌走请求头')
  assert.ok(calls.every(c => !c.path.includes('SELF-TOK') && !Object.values(c.query).includes('SELF-TOK')), '令牌不进地址')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('自助登记：钉钉里有重名 → 先选部门再发码', async (browser, B) => {
  const st = { pays: [], laters: [] }
  const { page, ctx } = await open(browser, B, selfApi(st), { hash: '#/invself' })
  await mount(page, 'InvSelf')
  await page.getByPlaceholder('你在钉钉上的姓名').fill('重名人')
  await page.getByRole('button', { name: '发验证码到钉钉' }).click()
  await page.getByRole('button', { name: '公司-生产部' }).click()
  await page.getByText('验证码已发到').waitFor({ timeout: 4000 })
  assert.equal(st.sent.pick, 1)
  await ctx.close()
})

test('自助登记：在钉钉里打开 → 先 dd.config 鉴权再免登，直接进，不用验证码', async (browser, B) => {
  const st = { pays: SELF_PAYS(), laters: [] }
  const { page, ctx, calls } = await open(browser, B, selfApi(st), { hash: '#/invself', ua: DD_UA, init: FAKE_DD })
  await mount(page, 'InvSelf')
  await page.getByText('申请人甲提交的付款申请（公对公）').first().waitFor({ timeout: 5000 })
  const cfgArgs = await page.evaluate(() => window.__ddCfg)
  assert.equal(cfgArgs && cfgArgs.corpId, 'dingSELF')
  const i = calls.findIndex(c => c.path === '/api/inv/s/jsconfig'), k = calls.findIndex(c => c.path === '/api/inv/s/login/dd')
  assert.ok(i >= 0 && k > i, '先鉴权后免登')
  assert.equal(calls[k].body.code, 'AUTH1')
  assert.equal(await page.getByPlaceholder('你在钉钉上的姓名').count(), 0)
  await ctx.close()
})

test('手机配对：钉钉里还没绑定时先 dd.config（拿到企业编号）再要免登码，绑定带上身份', async (browser, B) => {
  const st = { bound: false }
  const api = async c => {
    if (c.path === '/api/inv/m/hello') return { ok: true, bound: st.bound, user: '测试收票员', corpId: '' }
    if (c.path === '/api/inv/m/jsconfig') return { ok: true, agentId: '123', corpId: 'dingFAKE', timeStamp: '1', nonceStr: 'n', signature: 's' }
    if (c.path === '/api/inv/m/bind') { st.bind = c.body; st.bound = true; return { ok: true, user: '测试收票员', dtName: '张三', session: 'SESS2', msg: '已配对' } }
    if (c.path === '/api/inv/m/state') return { ok: true, user: '测试收票员', folder: null, items: [] }
    return { __status: 404, ok: false, msg: 'nope' }
  }
  const { page, ctx, calls } = await open(browser, B, api, { hash: '#/invpair?t=QR7', ua: DD_UA, init: FAKE_DD })
  await mount(page, 'InvPhone')
  await page.waitForFunction(() => true)
  for (let i = 0; i < 40 && !st.bind; i++) await sleep(100)
  assert.equal(st.bind && st.bind.code, 'AUTH1', '要带上钉钉免登码：' + JSON.stringify(st.bind))
  const jc = calls.findIndex(c => c.path === '/api/inv/m/jsconfig'), bd = calls.findIndex(c => c.path === '/api/inv/m/bind')
  assert.ok(jc >= 0 && jc < bd, '先鉴权再绑定')
  assert.equal(calls[jc].headers['x-inv-pair'], 'QR7', '鉴权用的是还没绑定的配对码')
  await ctx.close()
})

test('后补池：「业务自助登记入口」给网址和二维码', async (browser, B) => {
  const st = { rows: [] }
  const base = laterApi(st)
  const api = async c => (c.path === '/api/inv/s/link' ? { ok: true, url: 'http://10.0.0.8/#/invself', qr: '/api/inv/s/qr.png' } : base(c))
  const { page, ctx } = await open(browser, B, api)
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  await page.getByRole('button', { name: '业务自助登记入口' }).click()
  const inp = page.locator('.inv-lt-self-url input')
  await inp.waitFor({ timeout: 4000 })
  assert.equal(await inp.inputValue(), 'http://10.0.0.8/#/invself')
  assert.ok((await page.locator('.inv-lt-self-qr').getAttribute('src')).startsWith('/api/inv/s/qr.png'))
  await ctx.close()
})

test('后补池：管理员「以某人身份预览」整页查某人能选哪些单、收款方列＋搜索＋标后补情况', async (browser, B) => {
  const st = { rows: [] }
  const base = laterApi(st)
  const api = async c => {
    if (c.path === '/api/inv/s/preview') {
      if (c.query.name === '重名') return { ok: true, need: 'pick', choices: [{ i: 0, dept: '公司-A部' }, { i: 1, dept: '公司-B部' }] }
      return { ok: true, person: { name: '申请人甲', dept: '公司-采购部' }, days: Number(c.query.days) || 60, truncated: false,
        templates: ['付款申请（公对公）'],
        rows: [{ procInstId: 'PI-1', title: '付款申请一', template: '付款申请（公对公）', payeeName: '甲供应商', businessId: '202610050001', amount: 500, approvalStatus: 'COMPLETED', approvalResult: 'agree', laterId: null, laterStatus: '', hasInvoice: false },
          { procInstId: 'PI-2', title: '付款申请二', template: '付款申请（公对公）', payeeName: '乙物流', businessId: '202610050002', amount: 800, approvalStatus: 'COMPLETED', laterId: 7, laterStatus: 'open', hasInvoice: false }] }
    }
    return base(c)
  }
  const { page, ctx, calls } = await open(browser, B, api)
  await mount(page, 'InvLater', { user: { name: '测试会计' } })
  await page.getByRole('button', { name: '以某人身份预览' }).click()
  await page.getByPlaceholder('钉钉上的姓名').fill('申请人甲')
  await page.locator('.inv-lt-pv-days').selectOption('120')
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await page.locator('.inv-lt-pv-tbl tbody tr').first().waitFor({ timeout: 4000 })
  assert.equal(await page.locator('.inv-lt-pv-tbl tbody tr').count(), 2)
  assert.equal(await page.locator('.inv-lt-pv-todo').count(), 1, '一张「可登记」')
  assert.ok(await page.getByText('已登记 #7', { exact: false }).isVisible(), '另一张标已登记')
  assert.equal(calls.filter(c => c.path === '/api/inv/s/preview').pop().query.days, '120')
  assert.ok(await page.locator('.inv-lt-pv-payee', { hasText: '甲供应商' }).isVisible(), '收款方列')
  // 页内搜索（按收款方筛）
  await page.getByPlaceholder('搜审批单、模板、收款方、审批编号、金额').fill('乙物流')
  assert.equal(await page.locator('.inv-lt-pv-tbl tbody tr').count(), 1, '搜收款方筛出一张')
  await page.getByPlaceholder('搜审批单、模板、收款方、审批编号、金额').fill('')
  await page.getByPlaceholder('钉钉上的姓名').fill('重名')
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await page.getByRole('button', { name: '公司-B部' }).waitFor({ timeout: 4000 })
  await ctx.close()
})

// ───────────────────────── 跑 ─────────────────────────

;(async () => {
  const B = await bundle()
  const browser = await chromium.launch({ headless: true, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] })
  const results = []
  for (const t of TESTS) {
    if (ONLY.length && !ONLY.some(k => t.name.includes(k))) continue
    const t0 = Date.now()
    try { await t.fn(browser, B); results.push({ name: t.name, ok: true, ms: Date.now() - t0 }) }
    catch (e) { results.push({ name: t.name, ok: false, ms: Date.now() - t0, err: (e && e.message ? e.message : String(e)).split('\n')[0] }) }
  }
  await browser.close()
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + r.name + (r.ok ? '' : '\n     → ' + r.err))
  const bad = results.filter(r => !r.ok).length
  console.log(`\n${results.length - bad}/${results.length} 通过（源码：${SRC}）`)
  process.exit(bad ? 1 : 0)
})().catch(e => { console.error(e); process.exit(2) })
