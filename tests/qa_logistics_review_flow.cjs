'use strict'
const path = require('path')
const fs = require('fs')
const os = require('os')
const assert = require('node:assert/strict')

const ROOT = path.resolve(__dirname, '..')
const FE = path.join(ROOT, '01_Current_Deliverables', 'app', 'frontend')
const esbuild = require(path.join(FE, 'node_modules', 'esbuild'))
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')
const ORIGIN = 'http://localhost:9'

function bundle() {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'logistics-review-flow-'))
  const entry = path.join(out, 'entry.jsx')
  const review = path.join(FE, 'src', 'views', 'LogisticsReview.jsx').replace(/\\/g, '/')
  fs.writeFileSync(entry, `
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import LogisticsReview from '${review}'
function Harness() {
  const [cfg, setCfg] = useState({ year: 2026, period: 9 })
  window.__setPeriod = (year, period) => setCfg({ year, period })
  return <LogisticsReview cfg={cfg} onPeriod={(year, period) => setCfg({ year, period })} />
}
createRoot(document.getElementById('root')).render(<Harness />)
`)
  esbuild.buildSync({
    entryPoints: [entry], bundle: true, outdir: out, format: 'iife', jsx: 'automatic', logLevel: 'silent',
    loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'css', '.png': 'empty', '.svg': 'empty', '.jpg': 'empty', '.woff': 'empty', '.woff2': 'empty', '.ttf': 'empty' },
    nodePaths: [path.join(FE, 'node_modules')], absWorkingDir: FE,
    define: { 'process.env.NODE_ENV': '"development"' },
  })
  return { js: fs.readFileSync(path.join(out, 'entry.js'), 'utf8'), css: fs.readFileSync(path.join(out, 'entry.css'), 'utf8') }
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const resultData = period => ({
  ok: true, carrier: '合成物流', period, counts: {}, total_bill: 10, detail_total: 0, detail_docs: 0, size: 50,
  detail: [], docs: [], nodoc: [], by_box: false, codes: {},
})
const linesData = period => ({
  ok: true, carrier: '合成物流', period, rows: [
    { kind: 'gtotal', key: 'G-' + period, subject: '合成主体', amt: 10, bill: 10, diff: 0 },
    { kind: 'accr', key: 'L-' + period, subject: '合成主体', fee_type: '出库运费', fee: '出库运费', vno: 'V-' + period, amt: 10, bill: 10, diff: 0, bill_span: 1 },
  ],
  accr_total: 10, bill_total: 10, diff_total: 0, adj: [], fixes: [], points: '', signed: null, n_unexplained: 0,
})
const taskDoc = (lid, doc_no, extra = {}) => ({
  lid, doc_no, doc_fee: 10, bill_amt: 1, bill_unit: '千克', kd_sum: 1, kd_unit: '千克', state: 'ok',
  sub_fees: { 运费: 10, 标准: 10 }, materials: [{}], n_mat: 1, subject: '合成主体', fee_item: '出库运费', parties: [], ...extra,
})
const taskDocs = [
  taskDoc(1, 'Q1', { state: 'qtydiff', kd_sum: 0.5, sub_fees: { 运费: 10 } }),
  taskDoc(2, 'Q1', { state: 'qtydiff', kd_sum: 0.5, sub_fees: { 运费: 10 } }),
  taskDoc(3, 'M1', { state: 'miss', kd_sum: 0, n_mat: 0, materials: [] }),
  taskDoc(4, 'C1'), taskDoc(5, 'F1', { state: 'feediff' }),
  taskDoc(6, 'A1', { doc_fee: 21, sub_fees: { 运费: 21, 标准: 20, 核价差: 1 } }),
  taskDoc(7, 'D1'),
  taskDoc(8, 'X1', { state: 'qtydiff', kd_sum: 0.5, confirmed: { by: '已核人' } }),
]
const taskResultData = period => ({
  ok: true, carrier: '合成物流', period, counts: {}, doc_counts: {}, total_bill: 81, detail_total: taskDocs.length,
  detail_docs: 7, size: 50, detail: [], docs: taskDocs, ex_all: [], nodoc: [], by_box: true, codes: {}, facets: {},
})
const taskClsData = { ok: true, docs: [
  { key: 'Q1', v: '判不了', unk: '资料不足', txt: '判不了' },
  { key: 'C1', v: '判不了', unk: '资料不足', txt: '判不了' },
  { key: 'X1', v: '标注不对', diff: ['主体'], txt: '标注不对' },
], tab: [] }
const taskDupData = { ok: true, docs: { Q1: { same: [10, 10] }, D1: { same: [10, 10] }, X1: { same: [10, 10] } } }
const taskLinesData = period => {
  const fix = { key: 'A2', to_dept: 'D01 应改部门', snap: { vno: 'V-A2', amt_net: 20 }, live: true }
  return {
    ok: true, carrier: '合成物流', period, rows: [
      { kind: 'accr', key: 'A1', anc: 'A1', subject: '主体甲', fee_type: '出库运费', fee: '出库运费', vno: 'V-A1', amt: 10, bill: 40, diff: 10, bill_span: 2 },
      { kind: 'accr', key: 'A2', anc: 'A1', subject: '主体甲', fee_type: '出库运费', fee: '出库运费', vno: 'V-A2', amt: 20, bill: null, diff: null, fix },
      { kind: 'accr', key: 'A3', subject: '主体甲', fee_type: '仓储费', fee: '仓储费', vno: 'V-A3', amt: 10, bill: 10, diff: 0, bill_span: 1 },
      { kind: 'gtotal', key: 'G-A', subject: '主体甲', amt: 40, bill: 50, diff: 10 },
      { kind: 'accr', key: 'B1', subject: '主体乙', fee_type: '出库运费', fee: '出库运费', vno: 'V-B1', amt: 20, bill: 20, diff: 0, bill_span: 1, note: '已核对' },
      { kind: 'gtotal', key: 'G-B', subject: '主体乙', amt: 20, bill: 20, diff: 0, mark: { status: 'ok', by: '已核人' } },
    ],
    accr_total: 60, bill_total: 70, diff_total: 10, adj: [], fixes: [fix], points: '', signed: null, n_unexplained: 1,
  }
}
const intakeData = {
  ok: true, carrier: '合成物流', has_spec: true, verdict: 'ok', bill_total: 10, req_total: 0, diff: 0,
  bills: [{ src: '合成账单', amount: 10, n_doc: 1 }], payreqs: [], files: [], req_files: [], history: [],
}
const readinessData = {
  ok: true, can_sign: true, blockers: [],
  steps: Object.fromEntries(['intake', 'docs', 'lines', 'sign'].map(k => [k, { status: 'ok', label: '已完成', blockers: [] }])),
}

async function open(browser, B, state) {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  const calls = [], errors = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('dialog', d => d.accept())
  await page.route(ORIGIN + '/**', async route => {
    const req = route.request(), u = new URL(req.url())
    if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div><script src="/entry.js"></script>' })
    if (u.pathname === '/entry.js') return route.fulfill({ contentType: 'application/javascript', body: B.js })
    if (u.pathname === '/entry.css') return route.fulfill({ contentType: 'text/css', body: B.css })
    if (!u.pathname.startsWith('/api/')) return route.fulfill({ status: 404, body: '' })
    const p = u.pathname, period = u.searchParams.get('period') || '2026-09'
    calls.push({ path: p, period, query: Object.fromEntries(u.searchParams) })
    const fail = kind => state.fail && state.fail[kind]-- > 0
    let body = { ok: true }, status = 200
    if (p === '/api/logistics-review/overview') body = { ok: true, period, subjects: [], rows: [{ code: 'C1', carrier: '合成物流', short: '合成物流', has_spec: true, status: 'billed', cells: {}, progress: {} }] }
    else if (p === '/api/logistics-review/scope') body = { ok: true, n: 0, groups: [] }
    else if (p === '/api/logistics-review/result') {
      if (fail('result')) { status = 502; body = { ok: false, msg: '合成逐单502' } }
      else body = state.taskData ? taskResultData(period) : resultData(period)
    } else if (p === '/api/logistics-review/intake') {
      if (fail('intake')) { status = 502; body = { ok: false, msg: '合成接入502' } }
      else body = intakeData
    } else if (p === '/api/logistics-review/lines') {
      if (period === '2026-09' && state.holdOldLines) await state.holdOldLines
      if (fail('lines')) { status = 502; body = { ok: false, msg: '合成逐笔502' } }
      else body = state.taskData ? taskLinesData(period) : linesData(period)
    } else if (p === '/api/logistics-review/readiness') {
      if (fail('readiness')) { status = 502; body = { ok: false, msg: '合成检查502' } }
      else body = readinessData
    } else if (p === '/api/logistics-review/invoices') {
      if (fail('invoices')) { status = 502; body = { ok: false, msg: '合成发票502' } }
      else body = { ok: true, blocks: [] }
    } else if (p === '/api/logistics-review/classify') body = state.taskData ? taskClsData : { ok: true, docs: [], tab: [] }
    else if (p === '/api/logistics-review/dups') body = state.taskData ? taskDupData : { ok: true, docs: {} }
    return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto(ORIGIN + '/')
  await page.getByRole('button', { name: '开始复核' }).click()
  return { page, ctx, calls, errors }
}

const step = (page, name) => page.locator('.steps .stepbtn').filter({ hasText: name }).click()
const count = (calls, path, period) => calls.filter(c => c.path === path && (!period || c.period === period)).length

async function resultRetry(browser, B) {
  const state = { fail: { result: 2 } }
  const { page, ctx, calls, errors } = await open(browser, B, state)
  try {
    await step(page, '逐单核价核量')
    await page.getByText('合成逐单502', { exact: false }).waitFor()
    assert.equal(await page.locator('.msg').filter({ hasText: '逐单取数失败' }).count(), 0, '逐单错误不能挂到顶部瞬时消息')
    await sleep(500)
    assert.equal(count(calls, '/api/logistics-review/lines'), 0, 'd.err 不能启动逐笔加载')
    await page.locator('.fxwarn').filter({ hasText: '合成逐单502' }).getByRole('button', { name: '重试' }).click()
    await page.getByText('合成逐单502', { exact: false }).waitFor({ state: 'detached' })
    await page.getByText('10.00', { exact: true }).first().waitFor()
    assert.equal(count(calls, '/api/logistics-review/result'), 3)
    await page.locator('.lrv-taskdegraded', { hasText: '不显示全月任务数' }).waitFor()
    assert.equal(await page.locator('[data-doc-task]').count(), 0, '无全量数据不得伪造任务数')
    assert.deepEqual(errors, [])
  } catch (e) { throw new Error(e.message + '\nPAGE:\n' + await page.locator('body').innerText()) } finally { await ctx.close() }
}

async function staleLines(browser, B) {
  let release
  const state = { fail: {}, holdOldLines: new Promise(resolve => { release = resolve }) }
  const { page, ctx, calls, errors } = await open(browser, B, state)
  try {
    for (let i = 0; i < 50 && !count(calls, '/api/logistics-review/lines', '2026-09'); i++) await sleep(100)
    assert.equal(count(calls, '/api/logistics-review/lines', '2026-09'), 1, '旧账期逐笔请求应已开始')
    await page.evaluate(() => window.__setPeriod(2026, 10))
    await step(page, '逐笔计提复核')
    await page.getByText('V-2026-10', { exact: true }).waitFor()
    release()
    await sleep(300)
    assert.equal(await page.getByText('V-2026-09', { exact: true }).count(), 0, '旧账期慢响应不能覆盖新账期')
    assert.equal(await page.getByText('V-2026-10', { exact: true }).count(), 1)
    assert.deepEqual(errors, [])
  } finally { release(); await ctx.close() }
}

async function stepRetries(browser, B) {
  const state = { fail: { intake: 1, lines: 1, readiness: 2, invoices: 1 } }
  const { page, ctx, errors } = await open(browser, B, state)
  try {
    await page.getByText('合成接入502', { exact: false }).waitFor()
    await page.locator('.fxwarn').filter({ hasText: '合成接入502' }).getByRole('button', { name: '重试' }).click()
    assert.equal(await page.getByText('合成接入502', { exact: false }).count(), 0, '接入重试开始时清掉旧错误')
    await page.getByText('接入完成 · 金额已勾稽', { exact: false }).waitFor()

    await step(page, '逐笔计提复核')
    await page.getByText('合成逐笔502', { exact: false }).waitFor()
    await page.locator('.msg').filter({ hasText: '合成逐笔502' }).getByRole('button', { name: '重试' }).click()
    assert.equal(await page.getByText('合成逐笔502', { exact: false }).count(), 0, '逐笔重试不能保留旧错误')
    await page.getByText('V-2026-09', { exact: true }).waitFor()

    await step(page, '发票审核')
    await page.getByText('合成检查502', { exact: false }).waitFor()
    const sign = page.getByRole('button', { name: '确认通过并登记已复核' })
    assert.equal(await sign.isDisabled(), true, 'readiness 失败必须禁止登记')
    await page.getByRole('button', { name: '重新检查' }).click()
    assert.equal(await page.getByText('合成检查502', { exact: false }).count(), 0, 'readiness 重试开始时清掉旧错误')
    await page.getByText('四步核对均已完成，可以登记。', { exact: false }).waitFor()

    await page.getByText('合成发票502', { exact: false }).first().waitFor()
    await page.locator('.fxwarn').filter({ hasText: '合成发票502' }).getByRole('button', { name: '重试' }).click()
    assert.equal(await page.getByText('合成发票502', { exact: false }).count(), 0, '发票重试不能保留旧错误或旧发票')
    await page.getByText('这家这月还没有钉钉请款单', { exact: true }).waitFor()
    assert.equal(await sign.isDisabled(), false)
    assert.deepEqual(errors, [])
  } catch (e) { throw new Error(e.message + '\nPAGE:\n' + await page.locator('body').innerText() + '\nERRORS:\n' + errors.join('\n')) } finally { await ctx.close() }
}

async function docTasks(browser, B) {
  const state = { taskData: true, fail: { result: 2 } }
  const { page, ctx, errors } = await open(browser, B, state)
  try {
    await step(page, '逐单核价核量')
    await page.locator('.fxwarn').filter({ hasText: '合成逐单502' }).getByRole('button', { name: '重试' }).click()
    await page.locator('[data-doc-task="qty"]').waitFor()
    const expected = { qty: '1', info: '1', cls: '2', fee: '1', amt: '1', dup: '2' }
    for (const [k, n] of Object.entries(expected)) assert.equal(await page.locator(`[data-doc-task="${k}"] b`).innerText(), n, `${k}任务数：502恢复后按doc_no去重并排除confirmed`)
    await page.locator('.ldd-kpi').filter({ hasText: '怀疑重复收费' }).click()
    assert.equal(await page.locator('.ldd-kpi.on').count(), 1)
    await page.locator('[data-doc-task="qty"]').click()
    assert.equal(await page.locator('.ldd-kpi.on').count(), 0, '选任务时清掉原普通卡片')
    assert.equal(await page.locator('.ldd-tbl tbody tr.drow').count(), 1, 'Q1两行只显示一次')
    assert.match(await page.locator('.ldd-tbl tbody').innerText(), /Q1/)
    assert.equal(await page.getByText('X1', { exact: true }).count(), 0, 'confirmed不进入任务')
    await page.locator('.ldd-kpi').filter({ hasText: '怀疑重复收费' }).click()
    assert.equal(await page.locator('.ldd-tbl tbody tr.drow').count(), 1, 'kcard可作为任务上的二级筛选')
    assert.equal(await page.locator('.ldd-kpi.on').count(), 1)
    await page.locator('[data-doc-task="qty"]').click()
    await page.locator('[data-doc-task="cls"]').click()
    assert.equal(await page.locator('.ldd-tbl tbody tr.drow').count(), 2, '判不了也进入归类待确认')
    assert.doesNotMatch(await page.locator('.ldd-tbl tbody').innerText(), /X1/)
    await page.setViewportSize({ width: 390, height: 844 })
    const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: innerWidth }))
    assert.ok(size.scroll <= size.width, `390px任务组页面横溢：${JSON.stringify(size)}`)
    assert.deepEqual(errors, [])
  } catch (e) { throw new Error(e.message + '\nPAGE:\n' + await page.locator('body').innerText()) } finally { await ctx.close() }
}

async function lineTasks(browser, B) {
  const state = { taskData: true, fail: {} }
  const { page, ctx, errors } = await open(browser, B, state)
  const rows = page.locator('.ltbl tbody tr:not(.ghead):not(.total)')
  try {
    await step(page, '逐笔计提复核')
    await page.getByText('V-A1', { exact: true }).waitFor()
    assert.equal(await page.locator('[data-line-task="unexpl"] b').innerText(), '1')
    assert.equal(await page.locator('[data-line-task="fixes"] b').innerText(), '1')
    assert.equal(await page.locator('[data-line-task="subject"] b').innerText(), '1')
    await page.locator('[data-line-task="fixes"]').click()
    assert.equal(await rows.count(), 1)
    await page.getByText('V-A2', { exact: true }).waitFor()
    assert.equal(await page.getByText('V-A1', { exact: true }).count(), 0)
    assert.equal(await rows.first().locator('td[rowspan="1"]').count(), 3, '非锚点更正行筛出后提升为锚点，三列rowspan均为1')
    assert.match(await rows.first().innerText(), /40\.00/, '提升后的锚点保留原账单金额')
    assert.match(await page.locator('.ltbl tr.ghead').innerText(), /40\.00[\s\S]*50\.00[\s\S]*10\.00/, '组头合计不随筛选重算')
    await page.locator('[data-line-task="unexpl"]').click()
    assert.equal(await rows.count(), 1)
    await page.getByText('V-A1', { exact: true }).waitFor()
    assert.equal(await rows.first().locator('td[rowspan="1"]').count(), 3)
    await page.locator('[data-line-task="subject"]').click()
    assert.equal(await rows.count(), 3, '主体任务保留该主体的全部明细')
    assert.equal(await page.locator('.ltbl tr.ghead').count(), 1)
    assert.equal(await page.getByText('V-B1', { exact: true }).count(), 0)
    assert.equal(await rows.first().locator('td[rowspan="2"]').count(), 3, '主体筛选保留原合并组跨度')
    assert.match(await page.locator('.lrv-line-tasks').innerText(), /合计仍为原始全量/)
    assert.deepEqual(errors, [])
  } catch (e) { throw new Error(e.message + '\nPAGE:\n' + await page.locator('body').innerText()) } finally { await ctx.close() }
}

async function diffDialog(browser, B) {
  const { page, ctx, errors } = await open(browser, B, { taskData: true, fail: {} })
  try {
    await step(page, '逐笔计提复核')
    await page.getByRole('button', { name: /本主体通过/ }).first().click()
    const dlg = page.getByRole('dialog', { name: '差异怎么办' })
    await dlg.waitFor()
    assert.equal(await dlg.locator('[data-dv]').count(), 2)
    assert.match(await dlg.innerText(), /按复核金额更正[\s\S]*保留计提，差异另行处理/)
    assert.equal(await dlg.evaluate(el => el.scrollWidth <= el.clientWidth), true, '差异方案卡不能横向溢出')
    await dlg.locator('[data-dv="fix"]').click()
    await dlg.waitFor({ state: 'detached' })
    assert.deepEqual(errors, [])
  } finally { await ctx.close() }
}

async function main() {
  const B = bundle()
  const browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const tests = [
    ['首次逐单502后重试成功且不启动错误逐笔', resultRetry],
    ['旧账期逐笔慢响应不覆盖新账期', staleLines],
    ['接入/逐笔/readiness/发票均可重试且失败禁登记', stepRetries],
    ['逐单任务组502恢复、去重、重叠排除confirmed及二级卡片', docTasks],
    ['逐笔lineTask筛选与bill_span重算、组头全量合计', lineTasks],
    ['差异处理方案卡不溢出且可选择', diffDialog],
  ]
  const results = []
  try {
    for (const [name, fn] of tests) {
      try { await fn(browser, B); results.push({ name, ok: true }) }
      catch (e) { results.push({ name, ok: false, err: e && e.stack || String(e) }) }
    }
  } finally { await browser.close() }
  for (const r of results) console.log((r.ok ? 'PASS ' : 'FAIL ') + r.name + (r.ok ? '' : '\n' + r.err))
  if (results.some(r => !r.ok)) process.exitCode = 1
  else console.log(`物流四步加载与错误恢复 QA 通过：${results.length}/${results.length}`)
}

main().catch(e => { console.error(e); process.exitCode = 1 })
