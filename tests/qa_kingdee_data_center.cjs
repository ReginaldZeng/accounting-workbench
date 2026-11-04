// [Change Log] Date:2026-11-04 Author:Kimi Version:V-draft 上传维护表与五类公共单据列表真实 Chrome 回归。
'use strict'
const path = require('path'), fs = require('fs'), os = require('os'), assert = require('node:assert/strict')
const ROOT = path.resolve(__dirname, '..'), FE = path.join(ROOT, '01_Current_Deliverables', 'app', 'frontend')
const esbuild = require(path.join(FE, 'node_modules', 'esbuild'))
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')
const ORIGIN = 'http://localhost:9'
const TYPES = ['SAL_OUTSTOCK', 'SAL_RETURNSTOCK', 'STK_MisDelivery', 'STK_TransferOut', 'STK_TransferIn', 'PUR_PurchaseOrder', 'STK_InStock', 'PUR_MRB']
const LABELS = ['销售出库单', '销售退货单', '其它出库单', '分布式调出单', '分布式调入单', '采购订单', '采购入库单', '采购退料单']

function bundle() {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'kingdee-data-center-')), entry = path.join(out, 'entry.jsx')
  const view = path.join(FE, 'src', 'views', 'LogisticsDocFreight.jsx').replace(/\\/g, '/')
  fs.writeFileSync(entry, `import React from 'react'; import {createRoot} from 'react-dom/client'; import View from '${view}'; createRoot(document.getElementById('root')).render(<View cfg={{year:2026,period:9}} onPeriod={() => {}} />)`)
  esbuild.buildSync({ entryPoints: [entry], bundle: true, outdir: out, format: 'iife', jsx: 'automatic', logLevel: 'silent',
    loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'css', '.png': 'empty', '.svg': 'empty', '.jpg': 'empty', '.woff': 'empty', '.woff2': 'empty', '.ttf': 'empty' },
    nodePaths: [path.join(FE, 'node_modules')], absWorkingDir: FE, define: { 'process.env.NODE_ENV': '"development"' } })
  return { js: fs.readFileSync(path.join(out, 'entry.js'), 'utf8'), css: fs.readFileSync(path.join(out, 'entry.css'), 'utf8') }
}

async function main() {
  const B = bundle(), browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 } }), page = await ctx.newPage(), calls = [], errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route(ORIGIN + '/**', route => {
    const u = new URL(route.request().url()), p = u.pathname
    if (p === '/') return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div><link rel="stylesheet" href="/entry.css"><script src="/entry.js"></script>' })
    if (p === '/entry.js') return route.fulfill({ contentType: 'application/javascript', body: B.js })
    if (p === '/entry.css') return route.fulfill({ contentType: 'text/css', body: B.css })
    if (!p.startsWith('/api/')) return route.fulfill({ status: 404, body: '' })
    calls.push({ path: p, query: Object.fromEntries(u.searchParams), method: route.request().method(), body: route.request().postData() || '' })
    let body = { ok: true }
    if (p === '/api/logistics-review/kingdee-data/status') body = { ok: true, period: '2026-09', docs: 13, required: TYPES.slice(0, 3), required_docs: Object.fromEntries(TYPES.map((x, i) => [x, i < 3 ? i + 5 : 0])), missing: ['STK_MisDelivery'],
      sources: { SAL_OUTSTOCK: { at: '2026-11-04 12:00', by: '测试甲', source: 'kingdee', docs: 8, version: 'v1' }, SAL_RETURNSTOCK: { at: '2026-11-04 12:10', by: '测试乙', source: 'manual_attachment', docs: 5, version: 'v2' } },
      files: [{ id: 9, name: '公共补核.xlsx', by: '测试乙', at: '2026-11-04 12:10' }] }
    else if (p === '/api/logistics-review/kingdee-data/refresh') body = { ok: true, refreshed: [], errors: {}, msg: '已写入公共快照' }
    else if (p === '/api/logistics-review/kingdee-data/fallback') body = { ok: true, refreshed: [{ form: 'SAL_OUTSTOCK', label: '销售出库单', n: 8 }], unknown: [] }
    else if (p === '/api/logistics-review/kingdee-data/documents') {
      const q = u.searchParams.get('q') || ''
      const rows = q ? [] : [{ row_key: 'r1', doc_no: 'DOC1', form: 'SAL_OUTSTOCK', form_label: '销售出库单', subject: '深圳星期零', party: '客户甲', org: '深圳', date: '2026-09-01', material_group: '植物肉', code: 'M1', name: '植物肉', base_qty: 10, base_unit: '千克', sales: 100,
        carrier: '承运商甲', fee_tr: 60, fee_ld: 10, fee: 70, unit_fee: 7, ratio: .7, snapshot_at: '2026-11-04 12:00', snapshot_by: '测试甲', source: 'kingdee' }]
      body = { ok: true, rows, total: rows.length, page: 1, size: 25, pages: 1, sources: {}, missing: [] }
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  })
  try {
    await page.goto(ORIGIN + '/')
    await page.getByText('单据源表维护', { exact: true }).waitFor()
    assert.equal(await page.locator('.kdc-subtabs button').count(), 7)
    assert.equal(await page.locator('.kdc-metrics').count(), 0, '上传维护不再使用状态卡')
    assert.equal(await page.locator('.kdc-table tbody tr').count(), 9, '八类来源加公共附件均为表格行')
    let response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/logistics-review/kingdee-data/refresh')
    await page.locator('.kdc-table tbody tr').first().getByRole('button', { name: '重取金蝶', exact: true }).click(); await response
    let call = calls.filter(x => x.path === '/api/logistics-review/kingdee-data/refresh').at(-1)
    assert.deepEqual(JSON.parse(call.body).forms, ['SAL_OUTSTOCK'])
    response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/logistics-review/kingdee-data/refresh')
    await page.getByRole('button', { name: '刷新全部', exact: true }).click(); await response
    call = calls.filter(x => x.path === '/api/logistics-review/kingdee-data/refresh').at(-1)
    assert.deepEqual(JSON.parse(call.body).forms, TYPES)
    const choosing = page.waitForEvent('filechooser')
    await page.locator('.kdc-table tbody tr').first().getByRole('button', { name: '手工上传', exact: true }).click()
    const chooser = await choosing
    await chooser.setFiles({ name: '公共补核.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('xlsx-test') })
    await page.waitForTimeout(200)
    call = calls.filter(x => x.path === '/api/logistics-review/kingdee-data/fallback').at(-1)
    assert.ok(call, '公共补核必须发出上传请求')
    assert.match(call.body, /filename="公共补核\.xlsx"/)

    for (const [label, forms] of [['销售出库单', ['SAL_OUTSTOCK', 'SAL_RETURNSTOCK']], ['其它出库单', ['STK_MisDelivery']], ['分布式调出单', ['STK_TransferOut']], ['分布式调入单', ['STK_TransferIn']], ['采购订单', ['PUR_PurchaseOrder', 'STK_InStock', 'PUR_MRB']]]) {
      await page.locator('.kdc-subtabs').getByRole('tab', { name: label, exact: true }).click()
      await page.locator('.kdc-code').filter({ hasText: 'DOC1' }).waitFor()
      const current = calls.filter(x => x.path === '/api/logistics-review/kingdee-data/documents').at(-1)
      assert.deepEqual(current.query.forms.split(','), forms)
    }
    assert.equal(await page.locator('.kdc-table thead th').count(), 15)
    assert.match(await page.locator('.kdc-table').innerText(), /深圳星期零[\s\S]*植物肉[\s\S]*M1[\s\S]*承运商甲[\s\S]*60\.00[\s\S]*10\.00[\s\S]*7\.00[\s\S]*70\.00%/)
    await page.getByLabel('搜索单据', { exact: true }).fill('不存在')
    await page.getByRole('button', { name: '查询', exact: true }).click()
    await page.getByText('没有符合条件的单据', { exact: true }).waitFor()
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 844 })
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth)
      assert.ok(scroll <= width, `${width}px 公共中心横向溢出：${scroll}`)
    }
    assert.deepEqual(errors, [])
    console.log('PASS 上传维护表、八类刷新、中文上传、五类列表搜索明细及390/768/1440px布局')
  } finally { await ctx.close(); await browser.close() }
}
main().catch(e => { console.error(e); process.exit(1) })
