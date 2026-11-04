// [Change Log] Date:2026-11-04 Author:Kimi Version:V2.1047 物流复核四步重构真实组件与响应式回归
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
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'logistics-redesign-'))
  const entry = path.join(out, 'entry.jsx')
  const review = path.join(FE, 'src', 'views', 'LogisticsReview.jsx').replace(/\\/g, '/')
  fs.writeFileSync(entry, `import React from 'react'
import { createRoot } from 'react-dom/client'
import LogisticsReview from '${review}'
createRoot(document.getElementById('root')).render(<LogisticsReview cfg={{year:2026,period:9}} onPeriod={() => {}} />)`)
  esbuild.buildSync({ entryPoints: [entry], bundle: true, outdir: out, format: 'iife', jsx: 'automatic', logLevel: 'silent',
    loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'css', '.png': 'empty', '.svg': 'empty', '.jpg': 'empty', '.woff': 'empty', '.woff2': 'empty', '.ttf': 'empty' },
    nodePaths: [path.join(FE, 'node_modules')], absWorkingDir: FE, define: { 'process.env.NODE_ENV': '"development"' } })
  return { js: fs.readFileSync(path.join(out, 'entry.js'), 'utf8'), css: fs.readFileSync(path.join(out, 'entry.css'), 'utf8') }
}

const doc = { lid: 1, doc_no: 'Q1', doc_fee: 10, bill_amt: 10, bill_unit: '千克', kd_sum: 10, kd_unit: '千克', state: 'ok',
  qty_standard: '按重量', sub_fees: { 运费: 10 }, materials: [{ code: 'M1', name: '真实物料', kd: 10, base_kg: 10, kg_unit: '千克', fee: 10 }], n_mat: 1,
  subject: '主体甲', fee_item: '出库运费', parties: ['客户甲'], bbiz: '', bizline: '', can_mode: true, can_weight: true }
const rows = [
  { kind: 'accr', key: 'A1', anc: 'A1', subject: '主体甲', book_code: '101', fee_type: '出库运费', fee: '出库运费', fee_code: 'F001', vno: 'V-A1', amt: 20, bill: 40, diff: 0, bill_span: 2,
    bill_subject: '主体甲', bill_biz: '植物肉', biz: '小料', biz_code: 'CP01', proj: '项目甲', proj_code: 'P01', dept: '物流部', dept_code: 'D01', acct: '6601', acct_name: '销售费用', tax_rate: .09,
    od: { n: 2, ratio: null, fsub: '主体甲', ffee: '出库运费', fbiz: '植物肉', blabel: '植物肉' } },
  { kind: 'accr', key: 'A2', anc: 'A1', subject: '主体甲', fee_type: '出库运费', fee: '出库运费', fee_code: 'F001', vno: 'V-A2', amt: 20, bill: null, bill_span: 0, biz: '小料', tax_rate: .09 },
  { kind: 'accr', key: 'A3', subject: '主体甲', fee_type: '仓储费', fee: '仓储费', vno: 'V-A3', amt: 10, bill: 10, diff: 0, bill_span: 1, biz: '小料', tax_rate: .06,
    od: { n: 0, ratio: 0, fsub: '主体甲', ffee: '仓储费', fbiz: '-', blabel: '无产品线' } },
  { kind: 'gtotal', key: 'GA', subject: '主体甲', book_code: '101', amt: 50, bill: 50, diff: 0 },
  { kind: 'accr', key: 'B1', subject: '主体乙', fee_type: '出库运费', fee: '出库运费', vno: 'V-B1', amt: 20, bill: 20, diff: 0, bill_span: 1, biz: '鲜食', note: '已核对',
    od: { n: 1, ratio: .5, fsub: '主体乙', ffee: '出库运费', fbiz: '鲜食', blabel: '鲜食' } },
  { kind: 'gtotal', key: 'GB', subject: '主体乙', book_code: '102', amt: 20, bill: 20, diff: 0, mark: { status: 'ok', by: '已核人' } },
]
const result = { ok: true, carrier: '合成物流', period: '2026-09', counts: {}, doc_counts: { ex: 2, qtydiff: 1, miss: 1 }, total_bill: 81,
  detail_total: 1, detail_docs: 1, size: 50, detail: [], docs: [doc], ex_all: [], nodoc: [{ subject: '主体甲', fee_item: '仓储费', amount: 5, biz: '小料' }], by_box: true, codes: {}, facets: { subject: [['主体甲', 1]], fee: [['出库运费', 1]], biz: [['植物肉', 1]] } }
const readiness = { ok: true, can_sign: true, blockers: [], steps: Object.fromEntries(['intake', 'docs', 'lines', 'sign'].map(k => [k, { status: 'ok', label: '已完成', blockers: [] }])) }

async function open(browser, B, resultOverride = null) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const page = await ctx.newPage(), calls = [], errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route(ORIGIN + '/**', route => {
    const u = new URL(route.request().url()), p = u.pathname
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div><link rel="stylesheet" href="/entry.css"><script src="/entry.js"></script>' })
    if (p === '/entry.js') return route.fulfill({ contentType: 'application/javascript', body: B.js })
    if (p === '/entry.css') return route.fulfill({ contentType: 'text/css', body: B.css })
    if (!p.startsWith('/api/')) return route.fulfill({ status: 404, body: '' })
    calls.push({ path: p, query: Object.fromEntries(u.searchParams) })
    let body = { ok: true }
    if (p === '/api/logistics-review/overview') body = { ok: true, period: '2026-09', subjects: ['主体甲', '主体乙'], rows: [{ code: 'C1', carrier: '合成物流', short: '合成物流', has_spec: true, status: 'billed', cells: {}, progress: {} }] }
    else if (p === '/api/logistics-review/scope') body = { ok: true, n: 0, groups: [] }
    else if (p === '/api/logistics-review/result') body = resultOverride ? { ...result, ...resultOverride } : result
    else if (p === '/api/logistics-review/intake') body = { ok: true, carrier: '合成物流', has_spec: true, verdict: 'ok', bill_total: 81, req_total: 81, diff: 0,
      bills: [{ src: '标准账单', amount: 81, n_detail: 8, n_doc: 7 }], payreqs: [], files: [{ id: 1, src: '标准账单', name: '标准账单.xlsx', report: { sheets: [{ sheet: '计费页1', role: 'detail', n: 8, amount: 81, verdict: 'ok', total: { sum: 81, how: 'self' }, money: [] }], duplicates: [] } }], req_files: [], history: [] }
    else if (p === '/api/logistics-review/lines') body = { ok: true, carrier: '合成物流', period: '2026-09', rows, accr_total: 70, bill_total: 70, diff_total: 0, od_total: { ratio: .5 }, adj: [], fixes: [], points: '', signed: null, n_unexplained: 0 }
    else if (p === '/api/logistics-review/readiness') body = readiness
    else if (p === '/api/logistics-review/invoices') body = { ok: true, blocks: [{ inst: 'P1', bid: 'B1', subject: '主体甲', subject_full: '主体甲公司', payee: '合成物流', amount: 70, inv_total: 70,
      invoices: [{ id: 1, number: 'INV1', seller: '合成物流', buyer: '主体甲公司', gross: 70, review: 'approved' }] }] }
    else if (p === '/api/logistics-review/classify') body = { ok: true, docs: [{ key: 'Q1', v: '归类一致', sys: '主体甲 · 植物肉', txt: '归类一致' }], tab: [] }
    else if (p === '/api/logistics-review/dups') body = { ok: true, docs: {} }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto(ORIGIN + '/')
  assert.equal(await page.locator('.lrx-top h1').innerText(), '物流账单复核台')
  assert.equal(await page.locator('.lrx-metric').count(), 4, '总表也使用新页头和真实指标卡')
  await page.getByRole('button', { name: '开始复核' }).click()
  await page.locator('.lrx-top').waitFor()
  return { ctx, page, calls, errors }
}

const step = (page, key) => page.locator(`[data-review-step="${key}"]`).click()
const metric = (page, label) => page.locator('.lrx-metric').filter({ has: page.locator('.lrx-metric-label', { hasText: label }) })
async function noOverflow(page, label) {
  const size = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
    ancestors: (() => { const out = []; let el = document.querySelector('.lrx-line-table'); while (el) { const r = el.getBoundingClientRect(), s = getComputedStyle(el); out.push({ tag: el.tagName, cls: el.className, left: r.left, right: r.right, client: el.clientWidth, scroll: el.scrollWidth, display: s.display, minWidth: s.minWidth, overflow: s.overflow }); el = el.parentElement } return out })(),
    offenders: [...document.querySelectorAll('body *')].map(el => ({ tag: el.tagName, cls: el.className, left: Math.round(el.getBoundingClientRect().left), right: Math.round(el.getBoundingClientRect().right), width: el.scrollWidth })).filter(x => x.right > innerWidth + 2 || x.left < -2).slice(0, 18) }))
  assert.ok(size.scroll <= size.width, `${label} 横向溢出：${JSON.stringify(size)}`)
}

async function legacySmoke(browser, B) {
  const override = { by_box: false, docs: [], nodoc: [], doc_counts: null, detail_total: 1,
    detail: [{ doc_no: 'LEGACY1', carrier_sub: '顺丰', prov: '广东', charge_wt: 2, qty: 2, kd_qty: 2, amount: 12.34, std_amount: 12.34, price_diff: 0, qty_state: 'ok', price_state: 'pass' }] }
  const { ctx, page, errors } = await open(browser, B, override)
  try {
    await step(page, 'docs')
    const row = page.locator('.ldx-table tbody tr.ldx-row')
    await row.waitFor()
    assert.match(await row.innerText(), /LEGACY1[\s\S]*按重量[\s\S]*2 千克[\s\S]*2 千克[\s\S]*12\.34/)
    assert.equal(await page.locator('.ldx-table thead th').count(), 12)
    await page.setViewportSize({ width: 390, height: 844 })
    await noOverflow(page, '390px 旧格式 detail 承运商')
    assert.deepEqual(errors, [])
  } finally { await ctx.close() }
}

async function main() {
  const B = bundle(), browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const { ctx, page, calls, errors } = await open(browser, B)
  try {
    assert.equal(await page.locator('.lrx-metric').count(), 4)
    assert.match(await metric(page, '账单金额').innerText(), /81\.00/)
    assert.match(await metric(page, '计费页').innerText(), /1/)
    assert.match(await metric(page, '标准逐笔行').innerText(), /8/)
    assert.equal(await page.locator('.lik-verdict').count(), 0, '旧数据接入流程卡不再默认出现')
    await page.getByText('文件与来源', { exact: false }).first().waitFor()
    await page.getByText('供应商原始账单', { exact: false }).first().waitFor()
    await page.getByText('逐页金额勾稽', { exact: false }).first().waitFor()
    await page.getByText('A–J 固定取数列', { exact: false }).first().waitFor()
    await noOverflow(page, '1440px 数据接入')

    await step(page, 'docs')
    await page.locator('.ldx-table tbody tr.ldx-row').first().waitFor()
    assert.equal(await page.locator('.ldx-table thead th').count(), 12, '默认使用样机十二列逐单表')
    assert.match(await metric(page, '全部账单金额').innerText(), /81\.00/)
    assert.match(await metric(page, '当前筛选金额').innerText(), /81\.00/, '未筛选时使用后端总额而非前端浮点求和')
    assert.match(await page.locator('.ldx-table tbody tr.ldx-row').first().innerText(), /植物肉/, '空 bbiz 不得遮蔽 bizline')
    assert.match(await metric(page, '金蝶物料已接入').innerText(), /1 \/ 1/)
    assert.match(await metric(page, '待确认逐笔行').innerText(), /2/)
    await page.locator('.ldx-table').getByRole('button', { name: '明细', exact: true }).first().click()
    const dialog = page.getByRole('dialog', { name: '单据 Q1' })
    await dialog.waitFor()
    assert.match(await dialog.locator('.ldd-kv').innerText(), /数量标准[\s\S]*按重量/)
    await dialog.locator('.ldd-fold summary').click()
    assert.match(await dialog.innerText(), /真实物料/)
    const box = await dialog.boundingBox()
    assert.ok(box.x > 100 && box.x + box.width < 1340, `桌面端必须是中央弹窗而非抽屉：${JSON.stringify(box)}`)
    await dialog.getByRole('button', { name: '关闭', exact: true }).last().click()
    await page.getByRole('button', { name: '展开明细', exact: true }).click()
    await page.locator('.ldx-table').getByRole('button', { name: '展开', exact: true }).first().click()
    await page.getByText('真实物料', { exact: true }).waitFor()
    await page.getByRole('button', { name: '打开完整处理', exact: true }).click()
    await dialog.waitFor()
    await dialog.getByRole('button', { name: '关闭', exact: true }).last().click()
    await page.getByRole('button', { name: /无金蝶单号 1/ }).click()
    const noDocRow = page.locator('.ldx-row-nodoc')
    await noDocRow.waitFor()
    assert.match(await noDocRow.innerText(), /无金蝶单号[\s\S]*5\.00[\s\S]*只读/)
    assert.equal(await noDocRow.getByRole('button', { name: '确认', exact: true }).count(), 0)
    await page.getByRole('button', { name: /全部逐笔 2/ }).click()
    await page.getByLabel('单号 / 客户', { exact: true }).fill('Q1')
    assert.equal(await page.locator('.ldx-table tbody tr.ldx-row').count(), 1)
    await metric(page, '当前筛选金额').filter({ hasText: '10.00' }).waitFor()
    assert.match(await metric(page, '当前筛选金额').innerText(), /10\.00/, '筛选金额跟随新工作区')
    for (const [label, value] of [['主体', '主体甲'], ['费用类型', '出库运费'], ['产品分类', '植物肉'], ['数量标准', '按重量'], ['确认状态', 'pending']]) {
      await page.getByLabel(label, { exact: true }).selectOption(value)
      assert.equal(await page.locator('.ldx-table tbody tr.ldx-row').count(), 1, `${label}筛选`)
    }
    await page.getByRole('button', { name: '清除筛选', exact: true }).click()
    assert.equal(await page.locator('.ldx-table tbody tr.ldx-row').count(), 2)
    await metric(page, '当前筛选金额').filter({ hasText: '81.00' }).waitFor()
    assert.match(await metric(page, '当前筛选金额').innerText(), /81\.00/, '清除筛选后恢复后端总额')
    const confirmBefore = calls.length
    await page.getByRole('button', { name: '确认本页（1）', exact: true }).click()
    await page.waitForTimeout(100)
    assert.ok(calls.slice(confirmBefore).some(call => call.path.includes('doc-confirm')), '确认本页继续调用正式接口')

    await step(page, 'lines')
    await page.locator('[data-prepared-row="A1"]').waitFor()
    assert.equal(await page.locator('[data-prepared-row="A2"]').count(), 0, '共享账单金额的后续计提行不能重复进准备做账表')
    assert.match(await page.locator('[data-prepared-row="A1"]').innerText(), /40\.00[\s\S]*2 单/)
    assert.doesNotMatch(await page.locator('[data-prepared-row="A1"]').innerText(), /NaN/)
    assert.match(await page.locator('[data-prepared-row="A3"]').innerText(), /10\.00[\s\S]*汇总核/)
    const fold = page.locator('.lrx-detail-toggle').filter({ hasText: '当前计提 金蝶原分录' })
    assert.equal(await fold.getAttribute('open'), null, '当前计提默认收起，不再与准备做账双主表并列')
    await fold.locator(':scope > summary').click()
    assert.equal(await fold.getAttribute('open'), '')
    await fold.locator(':scope > summary').click()
    assert.equal(await fold.getAttribute('open'), null)
    await page.locator('[data-prepared-row="A3"]').getByRole('button', { name: '调整 / 拆分' }).click()
    await page.getByRole('dialog', { name: '计提更正' }).waitFor()
    await page.getByRole('dialog', { name: '计提更正' }).getByRole('button', { name: '取消', exact: true }).click()
    await fold.locator(':scope > summary').click()
    await page.locator('.lrx-seg').getByRole('button', { name: /主体待确认 1/ }).click()
    assert.equal(await page.locator('.lrx-group').count(), 1)
    assert.match(await page.locator('.lrx-group').innerText(), /主体甲/)
    await page.locator('.lrx-seg').getByRole('button', { name: /全部明细 2/ }).click()
    await page.locator('[data-subject-confirm="主体甲"]').click()
    await page.getByRole('button', { name: '撤销主体确认', exact: true }).waitFor()
    assert.match(await page.locator('.lrx-group').filter({ hasText: '主体甲' }).innerText(), /主体已通过/)
    await page.locator('[data-prepared-row="A1"]').getByRole('button', { name: '2 单', exact: true }).click()
    await page.locator('.ldx-source-filter').waitFor()
    const last = calls.filter(c => c.path === '/api/logistics-review/result').at(-1).query
    assert.equal(last.fsub, '主体甲')
    assert.equal(last.ffee, '出库运费')
    assert.equal(last.fbiz, '植物肉')

    await step(page, 'sign')
    await page.locator('.lir-invoices').waitFor()
    assert.match(await metric(page, '请款金额').innerText(), /70\.00/)
    assert.match(await metric(page, '已到发票').innerText(), /70\.00/)
    assert.match(await page.locator('.lir-invoices').innerText(), /INV1/)
    assert.equal(await page.locator('.lia-work').count(), 0, '旧左右发票队列不再渲染')
    assert.equal(await page.locator('.lir-request[data-inst="P1"]').count(), 1, '请款卡继续按 inst 独立识别')

    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 })
      for (const key of ['intake', 'docs', 'lines', 'sign']) {
        await step(page, key)
        if (key === 'lines') await page.locator('[data-prepared-row="A1"]').waitFor()
        if (key === 'sign') await page.locator('.lir-invoices').waitFor()
        await noOverflow(page, `${width}px ${key}`)
      }
    }
    await legacySmoke(browser, B)
    assert.deepEqual(errors, [])
    console.log('PASS 四步真实指标、来源跳转、账单金额去重、主体筛选与计提折叠')
    console.log('PASS 中央单据弹窗数量标准与物料、390/768/1440px 四步无横向溢出')
    console.log('PASS 旧格式 detail 承运商十二列适配与 390px 布局')
  } catch (e) {
    console.error(e.stack || e)
    console.error('PAGE:\n' + await page.locator('body').innerText())
    process.exitCode = 1
  } finally { await ctx.close(); await browser.close() }
}
main().catch(e => { console.error(e); process.exitCode = 1 })
