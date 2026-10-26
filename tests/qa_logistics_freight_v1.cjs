// [Change Log] Date:2026-10-26 Author:Kimi Code Version:V-draft
// 逐单运费 V1 真实 JSX 浏览器回归：列表、上传预览、阻断、重叠确认、撤回原因与销售出库版本展示。
'use strict'
const path = require('path')
const fs = require('fs')
const os = require('os')
const assert = require('node:assert/strict')

const ROOT = path.resolve(__dirname, '..')
const FE = fs.existsSync(path.join(ROOT, 'app', 'frontend'))
  ? path.join(ROOT, 'app', 'frontend')
  : path.join(ROOT, '01_Current_Deliverables', 'app', 'frontend')
const esbuild = require(path.join(FE, 'node_modules', 'esbuild'))
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'logistics-freight-v1-qa-'))
const entry = path.join(temp, 'entry.jsx')
const browserOut = path.join(temp, 'entry.js')

fs.writeFileSync(entry, `
import React from 'react'
import { createRoot } from 'react-dom/client'
import LogisticsV1Upload from '${path.join(FE, 'src', 'views', 'LogisticsV1Upload.jsx').replace(/\\/g, '/')}'
import { OutstockView } from '${path.join(FE, 'src', 'views', 'LogisticsOutstockFreight.jsx').replace(/\\/g, '/')}'
const root = createRoot(document.getElementById('root'))
window.__mountV1 = () => root.render(React.createElement(LogisticsV1Upload, { period: '2026-10' }))
window.__mountOutstock = d => {
  const f = { internal: 0, q: '', org: '', btype: '', carrier: '', state: '', brand: '', stock: '', version: '', sort: 'date', dir: 'asc', page: 1 }
  root.render(React.createElement(OutstockView, {
    d, f, setF() {}, open: new Set(d.rows.map(x => x.no)), toggle() {}, onSearch() {},
    qInput: '', setQInput() {}, onFresh() {}, busy: false,
  }))
}
`)
esbuild.buildSync({
  entryPoints: [entry], bundle: true, format: 'iife', outfile: browserOut,
  jsx: 'automatic', logLevel: 'silent', nodePaths: [path.join(FE, 'node_modules')],
})
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')

const batches = [
  { id: 1, filename: '跨越十月.xlsx', template: '逐单标准模板', status: 'draft', doc_count: 2, row_count: 4, amount: 120, category_amounts: { 运费: 100, 装卸费: 20 }, uploader: '测试会计', uploaded_at: '2026-10-26 10:00' },
  { id: 2, filename: '异常批次.xlsx', template: '逐单标准模板', status: 'draft', doc_count: 1, row_count: 1, amount: 30 },
  { id: 4, filename: '已生效批次.xlsx', template: '旧版模板', status: 'confirmed', doc_count: 3, row_count: 8, amount: 500, uploaded_by: '管理员', created_at: '2026-10-25 09:00' },
]
const details = {
  1: { ...batches[0], period: '2026-10', summary: { status: 'blocked' }, valid_rows: 4, original_amount: 120, recalculated_amount: 120, reconciliation_diff: 0, issues: [{ severity: 'error', code: 'period_mismatch', msg: '当前选择 2026-10，文件属于 2026-09' }], overlaps: [{ code: 'overlap', doc_no: 'XSCKD001', msg: 'XSCKD001 与批次 9 重叠' }] },
  2: { ...batches[1], summary: { status: 'blocked' }, valid_rows: 1, original_amount: 30, recalculated_amount: 28, reconciliation_diff: 2, issues: [{ severity: 'error', msg: '原表与重算相差 2 元' }, { severity: 'warning', msg: '第 8 行缺少费用类型' }, { severity: 'info', code: 'carrier_normalized', msg: '链盟4.2 → 链盟' }] },
}
const stats = { n: 3, n_out: 3, n_ret: 0, n_has: 1, n_none: 2, n_ldonly: 0, n_nofee: 0, fee: 120, fee_tr: 120, fee_ld: 0, fee_ot: 0, fee_ok: 120, fee_pending: 0, amount: 1000, ratio: .12, amount_none: 0, v1_amount: 100, v2_amount: 200, version_diff: -100, n_unsubmitted: 1 }
const outstockRow = (no, extra) => ({ no, org: '深圳星期零', date: '2026-10-01', brand: '星期零', customer: '客户甲', stock: '成品仓', addr: '深圳', btype: '销售出库单', kind: 'out', qty_txt: '1 件', amount: 100, fee_tr: 0, fee_ld: 0, fee_ot: 0, fee: 0, ratio: 0, lines: [], carriers: [], ...extra })
const outstockData = {
  rows: [
    outstockRow('XSCKD001', { fee: 120, fee_tr: 120, v1_amount: 100, v2_amount: 120, version_diff: 20, effective_version: 'v2', version_sources: [{ version: 'v2', filename: '跨越十月.xlsx', amount: 120 }] }),
    outstockRow('XSCKD002', { effective_version: 'unsubmitted', unsubmitted: true, v1_amount: null, v2_amount: 80, version_diff: -80 }),
    outstockRow('XSCKD003', { v1_amount: 0, v2_amount: 0, version_diff: 0, effective_version: 'v1' }),
  ],
  all: stats, cur: stats, facets: { orgs: [], btypes: [], brands: [], stocks: [], carriers: [] },
  page: 1, pages: 1, size: 50, n_internal: 0, fetched_at: '2026-10-26 10:00', orphan: { n: 0 },
}

async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  try {
    const page = await browser.newPage()
    const errors = []
    const calls = { preview: [], confirm: [], withdraw: [], supplement: [], bulk: [] }
    page.on('pageerror', e => errors.push(e.message))
    const newFiles = [
      { id: 8, filename: '新表A.xlsx', status: 'draft', summary: { status: 'ready' }, valid_rows: 2, doc_count: 2, amount: 20, original_amount: 20, recalculated_amount: 20 },
      { id: 9, filename: '新表B.xlsx', status: 'draft', summary: { status: 'ready' }, valid_rows: 1, doc_count: 1, amount: 10, original_amount: 10, recalculated_amount: 10 },
    ]
    let uploaded = false
    await page.route('http://localhost:9/**', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.pathname === '/entry.js') return route.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(browserOut) })
      if (url.pathname === '/api/logistics-freight/v1/carriers') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ carriers: [{ name: '天鹰', full_name: '天鹰物流' }] }) })
      if (url.pathname === '/api/logistics-freight/v1/bulk-supplement') {
        const body = JSON.parse(request.postData() || '{}'); calls.bulk.push(body)
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batches: body.batch_ids.map(id => ({ id, filename: `新表${id === 8 ? 'A' : 'B'}.xlsx`, status: 'draft', carrier: body.carrier, summary: { status: 'ready' } })) }) })
      }
      if (url.pathname === '/api/logistics-freight/v1/batches') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batches: [...batches, ...(uploaded ? newFiles : [])] }) })
      const detail = url.pathname.match(/^\/api\/logistics-freight\/v1\/batches\/(\d+)$/)
      if (detail && request.method() === 'GET') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batch: details[detail[1]] }) })
      if (detail && request.method() === 'POST') {
        const body = JSON.parse(request.postData() || '{}')
        calls.supplement.push(body)
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batch: { ...details[detail[1]], ...body, summary: { status: 'ready' }, issues: [] } }) })
      }
      if (url.pathname === '/api/logistics-freight/v1/preview') {
        calls.preview.push(request.postData() || '')
        uploaded = true
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batches: newFiles }) })
      }
      if (url.pathname.endsWith('/confirm')) {
        calls.confirm.push(JSON.parse(request.postData() || '{}'))
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batch: { ...details[1], status: 'confirmed' } }) })
      }
      if (url.pathname.endsWith('/withdraw')) {
        calls.withdraw.push(JSON.parse(request.postData() || '{}'))
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ batch: { ...batches[2], status: 'withdrawn' } }) })
      }
      return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div><script src="/entry.js"></script>' })
    })
    await page.goto('http://localhost:9/')
    await page.evaluate(() => window.__mountV1())
    await page.locator('.lv1-table').waitFor()
    assert.equal(await page.locator('.lv1-table tbody > tr').count(), 3)
    assert.match(await page.locator('.lv1-table').innerText(), /跨越十月\.xlsx/)
    assert.match(await page.locator('.lv1-table').innerText(), /逐单标准模板/)
    assert.match(await page.locator('.lv1-table').innerText(), /测试会计 · 2026-10-26 10:00/)
    assert.match(await page.locator('.lv1-table').innerText(), /运费 100\.00/)

    const files = page.locator('input[type=file]').first()
    await files.setInputFiles([
      { name: '新表A.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('A') },
      { name: '新表B.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('B') },
    ])
    await page.getByText('上传预览完成：2 个文件，请逐文件检查后确认').waitFor()
    assert.equal(calls.preview.length, 1)
    assert.match(calls.preview[0], /name="files"; filename="新表A\.xlsx"/)
    assert.match(calls.preview[0], /name="files"; filename="新表B\.xlsx"/)
    assert.match(calls.preview[0], /name="period"\r\n\r\n2026-10/)
    assert.match(calls.preview[0], /name="operation"\r\n\r\nnew/)
    assert.equal(await page.locator('.lv1-preview').count(), 0)
    const newARow = page.locator('.lv1-table tbody > tr').filter({ hasText: '新表A.xlsx' }).first()
    await newARow.getByRole('button', { name: '预览', exact: true }).click()
    const supplementPreview = page.getByRole('dialog', { name: '逐单明细预览与确认' })
    await supplementPreview.waitFor()
    await supplementPreview.getByPlaceholder('天鹰等表没有供应商时在这里补').fill('天鹰')
    await supplementPreview.getByRole('button', { name: '保存补充并重新校验' }).click()
    await page.getByText('补充信息已保存，已重新校验', { exact: true }).waitFor()
    assert.equal(calls.supplement[0].operation, 'supplement')
    assert.equal(calls.supplement[0].carrier, '天鹰')
    await supplementPreview.getByRole('button', { name: '收起', exact: true }).click()
    await page.getByRole('checkbox', { name: '选择 新表A.xlsx' }).check()
    await page.getByRole('checkbox', { name: '选择 新表B.xlsx' }).check()
    await page.getByRole('button', { name: '批量指定物流商（2）' }).click()
    const bulkDialog = page.getByRole('dialog', { name: '批量指定物流商' })
    await bulkDialog.getByRole('button', { name: '确认指定 2 个批次' }).click()
    await page.getByText('已为 2 个待确认批次指定物流商：天鹰', { exact: true }).waitFor()
    assert.deepEqual(calls.bulk, [{ batch_ids: ['8', '9'], carrier: '天鹰' }])

    const firstRow = page.locator('.lv1-table tbody > tr').filter({ hasText: '跨越十月.xlsx' }).first()
    await firstRow.getByRole('button', { name: '预览', exact: true }).click()
    const overlapPreview = page.locator('.lv1-preview').filter({ hasText: 'XSCKD001 与批次 9 重叠' })
    await overlapPreview.waitFor()
    assert.match(await overlapPreview.innerText(), /有效行[\s\S]*重算－原表/)
    await overlapPreview.getByRole('button', { name: '确属跨期，仍按 2026-10 继续' }).click()
    await page.getByText('已确认跨期并重新校验', { exact: true }).waitFor()
    assert.equal(calls.supplement[1].operation, 'period_override')
    const overlapConfirm = overlapPreview.getByRole('button', { name: '确认此文件' })
    assert.equal(await overlapConfirm.isDisabled(), true)
    await overlapPreview.getByRole('checkbox').check()
    assert.equal(await overlapConfirm.isEnabled(), true)
    await overlapConfirm.click()
    await page.getByText('已确认并进入有效 V1', { exact: true }).waitFor()
    assert.deepEqual(calls.confirm, [{ allow_overlap: true }])

    const blockedRow = page.locator('.lv1-table tbody > tr').filter({ hasText: '异常批次.xlsx' }).first()
    await blockedRow.getByRole('button', { name: '预览', exact: true }).click()
    const blockedPreview = page.locator('.lv1-preview').filter({ hasText: '原表与重算相差 2 元' })
    await blockedPreview.waitFor()
    assert.match(await blockedPreview.innerText(), /第 8 行缺少费用类型/)
    assert.match(await blockedPreview.innerText(), /异常 \/ 阻断（2）/)
    assert.match(await blockedPreview.innerText(), /物流商识别记录（1）· 仅提示，不算异常/)
    assert.equal(await blockedPreview.getByRole('button', { name: '确认此文件' }).isDisabled(), true)
    await blockedPreview.getByRole('button', { name: '收起', exact: true }).click()

    const effectiveRow = page.locator('.lv1-table tbody > tr').filter({ hasText: '已生效批次.xlsx' }).first()
    await effectiveRow.getByRole('button', { name: '撤回', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '撤回物流表批次' })
    assert.equal(await dialog.getByRole('button', { name: '确认撤回' }).isDisabled(), true)
    await dialog.locator('textarea').fill('供应商重发了账单')
    await dialog.getByRole('button', { name: '确认撤回' }).click()
    await page.getByText('批次已撤回', { exact: true }).waitFor()
    assert.deepEqual(calls.withdraw, [{ reason: '供应商重发了账单' }])

    await page.evaluate(d => window.__mountOutstock(d), outstockData)
    await page.locator('.lof-stats').waitFor()
    assert.match(await page.locator('.lof-stats').innerText(), /V1 100\.00 · V2 200\.00 · 差异 -100\.00/)
    assert.match(await page.locator('.lof-stats').innerText(), /V1 未提交 1 张（不等同于零费用）/)
    const versionSelect = page.locator('.lof-bar select').filter({ has: page.locator('option[value=v1]') })
    assert.deepEqual(await versionSelect.locator('option').evaluateAll(xs => xs.map(x => x.value)), ['', 'v1', 'v2', 'diff', 'unsubmitted'])
    const detailText = await page.locator('.lof-version-detail').allInnerTexts()
    assert.match(detailText[0], /V1 100\.00[\s\S]*V2 120\.00[\s\S]*差异 20\.00[\s\S]*当前生效 V2/)
    assert.match(detailText[0], /跨越十月\.xlsx/)
    const pendingRow = page.locator('.lof-tw tbody > tr').filter({ hasText: 'XSCKD002' }).first()
    const zeroRow = page.locator('.lof-tw tbody > tr').filter({ hasText: 'XSCKD003' }).first()
    assert.match(await pendingRow.innerText(), /未提交/)
    assert.match(await zeroRow.innerText(), /0\.00/)
    assert.deepEqual(errors, [])
    console.log('逐单运费 V1 前端 QA 通过：多文件列表/预览、阻断、重叠确认、撤回原因、版本展示均正常，无 pageerror')
  } finally {
    await browser.close()
  }
}

main().catch(e => { console.error(e); process.exitCode = 1 })
