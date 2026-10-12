// [Change Log] Date: 2026-10-12 | Author: Claude / c | Version: V2.915（钉钉入口二选一）| 钉钉入口分流的前端回归（浏览器夹具）
// 从真正的入口 main.jsx 起整个前端，接口全部在浏览器里拦截回假数据：不连后端、不连钉钉、不碰生产。
// 「在钉钉里」＝浏览器标识带 DingTalk ＋ 页面上放一个假的 dd（要免登码就给一个假码）。
// 用法（仓库根目录）：node tests/qa_dd_entry.cjs
//                    DD_FE_ONLY=没有账号 node ...          —— 只跑名字里含这些字样的用例
//                    DD_FE_SHOTS=<目录> node ...           —— 顺手把二选一页的手机版、电脑版截图存到这个目录
// 依赖同 tests/qa_inv_frontend.cjs：前端 node_modules 里的 esbuild/react ＋ Playwright（PLAYWRIGHT_PATH 可改路径）。
'use strict'
const path = require('path')
const fs = require('fs')
const os = require('os')
const assert = require('node:assert/strict')

const ROOT = path.resolve(__dirname, '..')
const FE = path.join(ROOT, '01_Current_Deliverables', 'app', 'frontend')
const SRC = path.resolve(process.env.DD_FE_SRC || path.join(FE, 'src'))
const PW = process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
const ONLY = (process.env.DD_FE_ONLY || '').split(',').map(s => s.trim()).filter(Boolean)
const SHOTS = process.env.DD_FE_SHOTS || ''
const ORIGIN = 'http://localhost:9'   // 不真起服务：页面和接口全由 page.route 拦截回应

const esbuild = require(path.join(FE, 'node_modules', 'esbuild'))
const { chromium } = require(PW)

const HTML = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
  '<link rel="stylesheet" href="/entry.css"></head><body><div id="root"></div><script src="/entry.js"></script></body></html>'

async function bundle() {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-entry-qa-'))
  const entry = path.join(out, 'entry.jsx')
  fs.writeFileSync(entry, "import '" + SRC.replace(/\\/g, '/') + "/main.jsx'\n")
  await esbuild.build({
    entryPoints: [entry], bundle: true, outdir: out, format: 'iife', jsx: 'automatic', logLevel: 'error',
    loader: { '.js': 'jsx', '.jsx': 'jsx', '.png': 'empty', '.svg': 'empty', '.jpg': 'empty', '.woff': 'empty', '.woff2': 'empty', '.ttf': 'empty' },
    nodePaths: [path.join(FE, 'node_modules')], absWorkingDir: FE,
    define: { 'process.env.NODE_ENV': '"development"' },
  })
  const css = path.join(out, 'entry.css')
  return { js: fs.readFileSync(path.join(out, 'entry.js'), 'utf8'), css: fs.existsSync(css) ? fs.readFileSync(css, 'utf8') : '' }
}

const DD_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 Mobile/21G93 AliApp(DingTalk/9.0.1)'
const FAKE_DD = () => {
  window.dd = { ready: cb => setTimeout(cb, 5), error: () => {}, config: () => {},
    runtime: { permission: { requestAuthCode: o => o.onSuccess({ code: 'AUTH1' }) } } }
}
const USER = { name: '王会计', role: 'normal', grp: '核算组', perms: { enter_accounting: true }, must_change_pwd: false }

// st：session 工作台登录会话在不在；dd 钉钉身份对账号的结果 bound / unbound
function api(st) {
  return async c => {
    if (c.path === '/api/me') return st.session ? { ok: true, user: USER } : { __status: 401, ok: false, msg: '未登录' }
    if (c.path === '/api/dd/hello') return { ok: true, dingtalk: true, corpId: 'dingFAKE', clientId: '' }
    if (c.path === '/api/login/dd') {
      if (st.dd === 'bound') { st.session = true; return { ok: true, user: USER } }
      return { ok: false, code: 'unbound', ddName: '张三', ticket: 'TICKET1' }
    }
    if (c.path === '/api/inv/s/hello') return { ok: true, corpId: 'dingFAKE', dingtalk: true, clientId: '', me: st.selfMe || null, templates: ['付款申请（公对公）'] }
    if (c.path === '/api/inv/s/login/dd') { st.selfMe = { name: '张三', dept: '公司-销售部', via: 'dingtalk' }; return { ok: true, token: 'SELF-TOK', me: st.selfMe } }
    if (c.path === '/api/inv/s/payments') return { ok: true, msg: '', rows: [] }
    if (c.path === '/api/inv/s/laters' || c.path === '/api/inv/s/receivers') return { ok: true, rows: [] }
    if (c.path === '/api/nav-modules') return { ok: true, state: {}, modules: [], sections: [], posts: [] }
    if (c.path === '/api/config') return { source: 'sample', year: 2026, period: 6 }
    return { ok: true }
  }
}

async function open(browser, B, st, { hash = '', dingtalk = false, viewport = { width: 390, height: 760 } } = {}) {
  const ctx = await browser.newContext({ viewport, ...(dingtalk ? { userAgent: DD_UA } : {}) })
  const page = await ctx.newPage()
  if (dingtalk) await page.addInitScript(FAKE_DD)
  const calls = [], errors = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('dialog', d => d.accept())
  const handle = api(st)
  await page.route(ORIGIN + '/**', async route => {
    const req = route.request()
    const u = new URL(req.url())
    if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: HTML })
    if (u.pathname === '/entry.js') return route.fulfill({ contentType: 'application/javascript', body: B.js })
    if (u.pathname === '/entry.css') return route.fulfill({ contentType: 'text/css', body: B.css })
    if (u.pathname.startsWith('/api/')) {
      const call = { method: req.method(), path: u.pathname, headers: req.headers() }
      calls.push(call)
      let res = await handle(call)
      let status = 200
      if (res && res.__status) { status = res.__status; res = { ...res }; delete res.__status }
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(res) })
    }
    return route.fulfill({ status: 404, body: '' })
  })
  await page.goto(ORIGIN + '/' + hash)
  return { page, ctx, calls, errors }
}
const n = (calls, p) => calls.filter(c => c.path === p).length
const ASK = '今天要办哪件事？'
const hashOf = page => page.evaluate(() => window.location.hash)

const TESTS = []
const test = (name, fn) => TESTS.push({ name, fn })

test('钉钉里、账号对上了：出二选一；选「财务工作台」进门户，刷新不再问', async (browser, B) => {
  const { page, ctx, errors } = await open(browser, B, { session: false, dd: 'bound' }, { dingtalk: true })
  await page.getByText(ASK).waitFor({ timeout: 6000 })
  assert.ok(await page.getByText('你好，王会计').isVisible())
  assert.equal(await page.locator('.dde-card').count(), 2)
  assert.equal(await page.getByText('我的工作台').count(), 0, '没选之前不该进门户')
  await page.getByRole('button', { name: /财务工作台/ }).click()
  await page.getByText('我的工作台').first().waitFor({ timeout: 6000 })
  assert.equal(await page.locator('.dde').count(), 0)
  await page.reload()
  await page.getByText('我的工作台').first().waitFor({ timeout: 6000 })
  assert.equal(await page.locator('.dde').count(), 0, '这次打开期间选过工作台，刷新不该再问')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('钉钉里、本来就登着：也出二选一；选「发票后补单」落到后补登记页并自动认人，再点「进财务工作台」回门户', async (browser, B) => {
  const { page, ctx, calls, errors } = await open(browser, B, { session: true }, { dingtalk: true })
  await page.getByText(ASK).waitFor({ timeout: 6000 })
  assert.equal(n(calls, '/api/login/dd'), 0, '已经登着就不用再向钉钉要身份')
  await page.getByRole('button', { name: /发票后补单/ }).click()
  await page.getByText('登记后补单').first().waitFor({ timeout: 6000 })
  assert.equal(await hashOf(page), '#/invself')
  assert.equal(n(calls, '/api/inv/s/login/dd'), 1, '后补登记页在钉钉里自动认人')
  await page.getByRole('button', { name: '进财务工作台 ›' }).click()
  await page.getByText('我的工作台').first().waitFor({ timeout: 6000 })
  assert.equal(await page.locator('.dde').count(), 0, '从后补单点回来就是要进工作台，不再问')
  assert.equal(await hashOf(page), '')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('钉钉里、认出人但没有账号：不出二选一，直接进发票后补单；点「进财务工作台」到登录框，不再被送回去', async (browser, B) => {
  const { page, ctx, calls, errors } = await open(browser, B, { session: false, dd: 'unbound' }, { dingtalk: true })
  await page.getByText('登记后补单').first().waitFor({ timeout: 6000 })
  assert.equal(await hashOf(page), '#/invself')
  assert.equal(await page.locator('.dde').count(), 0)
  assert.ok(await page.getByText('有工作台账号？').isVisible())
  await page.getByRole('button', { name: '进财务工作台 ›' }).click()
  await page.getByText('还没对上工作台账号').waitFor({ timeout: 6000 })
  assert.ok(await page.getByPlaceholder('请输入用户名').isVisible())
  assert.equal(await hashOf(page), '')
  await page.waitForTimeout(400)
  assert.equal(await page.getByText('还没对上工作台账号').count(), 1, '停在登录框，没有再跳回后补单')
  assert.equal(n(calls, '/api/login/dd'), 2)
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('钉钉里、链接带着具体去处：不问，直接去那一页', async (browser, B) => {
  const { page, ctx, errors } = await open(browser, B, { session: true }, { dingtalk: true, hash: '#/invledger' })
  await page.waitForFunction(() => document.getElementById('root').children.length > 0 && !document.body.innerText.includes('加载中'), null, { timeout: 6000 })
  await page.waitForTimeout(300)
  assert.equal(await page.locator('.dde').count(), 0)
  assert.equal(await page.getByText(ASK).count(), 0)
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('不在钉钉里、没登录：照旧是登录页，不出二选一；登录页能去发票后补登记', async (browser, B) => {
  const { page, ctx, calls, errors } = await open(browser, B, { session: false }, { viewport: { width: 1280, height: 800 } })
  await page.getByPlaceholder('请输入用户名').waitFor({ timeout: 6000 })
  assert.equal(await page.locator('.dde').count(), 0)
  assert.equal(n(calls, '/api/login/dd'), 0)
  await page.getByText('不用工作台账号，从这里进 ›').click()
  await page.getByRole('heading', { name: '发票后补登记' }).waitFor({ timeout: 6000 })
  assert.equal(await hashOf(page), '#/invself')
  assert.ok(await page.getByText('有工作台账号？').isVisible(), '后补页的登录界面也留着回工作台的入口')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('不在钉钉里、已登录：直接进门户，不出二选一', async (browser, B) => {
  const { page, ctx, errors } = await open(browser, B, { session: true }, { viewport: { width: 1280, height: 800 } })
  await page.getByText('我的工作台').first().waitFor({ timeout: 6000 })
  assert.equal(await page.locator('.dde').count(), 0)
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('二选一页：手机一屏放得下、两张卡够大；电脑钉钉是左右两栏', async (browser, B) => {
  for (const [tag, viewport] of [['手机', { width: 375, height: 603 }], ['电脑', { width: 1280, height: 800 }]]) {
    const { page, ctx, errors } = await open(browser, B, { session: true }, { dingtalk: true, viewport })
    await page.getByText(ASK).waitFor({ timeout: 6000 })
    const m = await page.evaluate(() => {
      const root = document.querySelector('.dde'), r = el => el.getBoundingClientRect()
      const cards = [...document.querySelectorAll('.dde-card')].map(r), hero = r(document.querySelector('.dde-hero')), sheet = r(document.querySelector('.dde-sheet'))
      return { sh: root.scrollHeight, ch: root.clientHeight, heroH: hero.height, sheetH: sheet.height, cardHs: cards.map(c => Math.round(c.height)), lblH: r(document.querySelector('.dde-lbl')).height, scroll: root.scrollHeight > root.clientHeight + 1, minCard: Math.min(...cards.map(c => c.height)), cardW: cards[0].width,
        side: sheet.left >= hero.right - 1, footBottom: r(document.querySelector('.dde-foot')).bottom, vh: window.innerHeight,
        logo: document.querySelector('.dde-brand img').naturalWidth > 0, overflowX: document.documentElement.scrollWidth > window.innerWidth }
    })
    assert.equal(m.overflowX, false, tag + '：不该有横向滚动')
    assert.ok(m.minCard >= 88, tag + '：卡片高度 ' + m.minCard)
    assert.ok(m.logo, tag + '：标志图要显示得出来')
    if (tag === '手机') { assert.equal(m.scroll, false, '手机 375×603 要一屏放下：' + JSON.stringify(m)); assert.ok(m.footBottom <= m.vh); assert.equal(m.side, false) }
    else assert.equal(m.side, true, '电脑：选入口在品牌面板右边')
    // 猫看向正要点的那张卡
    await page.locator('.dde-card').nth(1).hover()
    assert.ok(await page.locator('.dde-stage.look-b').count(), tag + '：悬停在第二张卡，猫要看过去')
    if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.mouse.move(2, 2); await page.screenshot({ path: path.join(SHOTS, '二选一_' + tag + '.png') }) }
    assert.deepEqual(errors, [])
    await ctx.close()
  }
})

;(async () => {
  const B = await bundle()
  const browser = await chromium.launch({ headless: true })
  const results = []
  for (const t of TESTS) {
    if (ONLY.length && !ONLY.some(k => t.name.includes(k))) continue
    const t0 = Date.now()
    try { await t.fn(browser, B); results.push({ name: t.name, ok: true, ms: Date.now() - t0 }) }
    catch (e) { results.push({ name: t.name, ok: false, ms: Date.now() - t0, err: (e && e.message ? e.message : String(e)).split('\n').slice(0, 3).join(' | ') }) }
  }
  await browser.close()
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + r.name + (r.ok ? '' : '\n     → ' + r.err))
  const bad = results.filter(r => !r.ok).length
  console.log(`\n${results.length - bad}/${results.length} 通过（源码：${SRC}）`)
  process.exit(bad ? 1 : 0)
})().catch(e => { console.error(e); process.exit(2) })
