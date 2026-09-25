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

function auditApi(st) {
  return (c) => {
    if (c.path === '/api/inv/config') return cfg()
    if (c.path === '/api/inv/audit/queue') return { ok: true, total: st.queue.length, rows: st.queue }
    if (c.path === `/api/inv/folder/${st.folder.id}`) { st.folderGets = (st.folderGets || 0) + 1; return { ok: true, folder: st.folder, items: st.items, logs: st.logs || [] } }
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
  await page.locator('.inv-au-tbl tbody tr').first().click()
  const cat = page.locator('.inv-au-dlg-side .inv-fp-row', { hasText: '项目类别' }).locator('input')
  await cat.waitFor()
  assert.equal(await page.locator('.inv-au-dlg-side').getByRole('button', { name: '核对无误' }).count(), 0, '弹窗里用右上角「提交」，不再有「核对无误」')
  await cat.fill('餐饮')
  await page.locator('.inv-au-dlg-side .inv-fp-act').getByRole('button', { name: '保存' }).click()
  await page.getByText('建议不可抵扣', { exact: false }).first().waitFor()
  await page.locator('.inv-mhead').getByRole('button', { name: '提交', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('.inv-au-dlg'))
  assert.ok(calls.some(c => c.path === '/api/inv/audit/item/101/mark' && c.body.mark === 'ok'), '弹窗「提交」要记这张已核')
  await page.locator('.inv-au-bar2').getByRole('button', { name: '提交', exact: true }).click()
  await page.waitForFunction(() => document.body.innerText.includes('已通过'))
  const ap = calls.find(c => c.path === '/api/inv/audit/approve')
  assert.ok(ap, '没发出通过请求')
  assert.equal(ap.body.decisions['101'].deductible, false, '建议已变「不可抵扣」，没改判时应按新建议提交')
  assert.deepEqual(ap.body.itemIds, [101], '通过要带上看过的待审票 itemIds')
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
  await page.locator('.inv-au-bar2').getByRole('button', { name: '提交', exact: true }).click()
  await page.getByRole('button', { name: '不逐张看了，仍然提交' }).click()
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

test('审核改版：弹窗 Enter 提交这张并跳到下一张没核的；扫描人显示在弹窗标题', async (browser, B) => {
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
  assert.ok(calls.some(c => c.path === '/api/inv/audit/item/121/mark' && c.body.mark === 'ok'))
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
  await page.locator('.inv-mhead').getByRole('button', { name: '有疑问' }).click()
  await page.locator('.inv-au-qbox').getByRole('button', { name: '抬头不对' }).click()
  await page.getByRole('button', { name: '记下疑问' }).click()
  await page.locator('.inv-au-qbox', { hasText: '疑问：抬头不对' }).waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.inv-au-st-q').waitFor()
  const btn = page.locator('.inv-au-bar2').getByRole('button', { name: '提交（退回 1 个疑问）' })
  await btn.click()
  await page.getByRole('button', { name: '确认退回' }).click()
  await page.waitForFunction(() => document.body.innerText.includes('已退回'))
  const ret = calls.find(c => c.path === '/api/inv/audit/return')
  assert.ok(ret && ret.body.note.includes('抬头不对'), '退回原因要带上疑问：' + JSON.stringify(ret && ret.body))
  assert.equal(n(calls, c => c.path === '/api/inv/audit/approve'), 0)
  await ctx.close()
})

test('审核改版：有差额要写差额说明才能提交（点常用说明可快填），说明随 gapNote 发出；后补单列表照实列出', async (browser, B) => {
  const st = {
    folder: { id: 14, status: 'submitted', title: '有差额的单', amount: 160.8,
      gap: { pay: 160.8, special: 100, specialN: 1, normal: 0, normalN: 0, later: 60, gap: 0.8 },
      laters: [{ id: 12, filedBy: '张三', filedAt: '2026-09-10 10:00:00', expectAmount: 60, receivedAmount: 0, unregisteredAmount: 0,
        left: 60, expectDate: '2026-10-10', receiverName: '王五', status: 'open', statusText: '待收', remindCount: 1 }] },
    items: [inv({ id: 141, auditOk: { by: '测试会计', at: '2026-09-25 10:00:00' } })], logs: [],
  }
  st.queue = [{ id: 14, title: '有差额的单', amount: 160.8, anomalies: [], clean: false }]
  st.onApprove = () => ({ ok: true, folder: { ...st.folder, status: 'approved' } })
  const { page, ctx, calls } = await open(browser, B, auditApi(st))
  await mount(page, 'InvAudit', { user: { name: '测试会计' } })
  await page.locator('.inv-au-ltbl', { hasText: '#12' }).waitFor()
  assert.ok(await page.locator('.inv-au-ltbl', { hasText: '王五' }).isVisible())
  await page.locator('.inv-au-bar2').getByRole('button', { name: '提交', exact: true }).click()
  const ok = page.getByRole('button', { name: '确认差额并提交' })
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
  await page.getByRole('button', { name: '还没到票（1）' }).click()
  assert.equal(await page.locator('.inv-sf-tbl tbody tr').count(), 1)
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
