// [Change Log] Date:2026-10-30 Author:Kimi Version:V2.1033 增加业务线下拉保存与恢复账单原值的浏览器回归
'use strict'
const path = require('path')
const fs = require('fs')
const os = require('os')
const assert = require('node:assert/strict')

const ROOT = path.resolve(__dirname, '..')
const FE = path.join(ROOT, '01_Current_Deliverables', 'app', 'frontend')
const esbuild = require(path.join(FE, 'node_modules', 'esbuild'))
const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'logistics-doc-qa-')), 'panel.cjs')

esbuild.buildSync({
  entryPoints: [path.join(FE, 'src', 'views', 'LogisticsDocPanel.jsx')],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  outfile: out,
  jsx: 'automatic',
  logLevel: 'silent',
  nodePaths: [path.join(FE, 'node_modules')],
})

const { judge, docTaskMatch, uniqueDocs } = require(out)
const doc = {
  lid: 1,
  doc_no: 'XSCKD1',
  doc_fee: 21,
  bill_amt: 1,
  bill_unit: '千克',
  kd_sum: 1,
  kd_unit: '千克',
  state: 'ok',
  sub_fees: { 运费: 21 },
  materials: [],
}

const both = judge({ ...doc, qty_enabled: true, price_enabled: true })
assert.equal(both.v, '价待确认')
const noQty = judge({ ...doc, qty_enabled: false, price_enabled: true })
assert.equal(noQty.v, '未核量')
assert.equal(noQty.q.top, '未核量')
const noPrice = judge({ ...doc, qty_enabled: true, price_enabled: false })
assert.equal(noPrice.v, '未核价')
assert.equal(noPrice.p.top, '未核价')
assert.equal(noPrice.q.top, '重量对上')

const overlap = { ...doc, state: 'qtydiff', kd_sum: 0.5, sub_fees: { 运费: 21, 标准: 20, 核价差: 1 } }
const overlapCls = { XSCKD1: { v: '判不了' } }, overlapDups = { XSCKD1: { same: [21, 21] } }
for (const task of ['qty', 'amt', 'cls', 'dup']) assert.equal(docTaskMatch(overlap, task, overlapCls, overlapDups), true, `同一单可重叠进入 ${task}`)
assert.equal(uniqueDocs([overlap, { ...overlap, lid: 2 }]).length, 1, '任务按doc_no去重')
for (const task of ['qty', 'info', 'cls', 'fee', 'amt', 'dup']) {
  assert.equal(docTaskMatch({ ...overlap, confirmed: { by: '测试' } }, task, { ...overlapCls, XSCKD1: { v: '标注不对' } }, overlapDups), false, `confirmed不得进入 ${task}`)
}
assert.equal(docTaskMatch({ ...doc, state: 'miss' }, 'info'), true)
assert.equal(docTaskMatch({ ...doc, state: 'miss' }, 'qty'), false, '金蝶查无单列资料不足')
assert.equal(docTaskMatch({ ...doc, state: 'feediff' }, 'fee'), true)

const entry = path.join(path.dirname(out), 'entry.jsx')
const browserOut = path.join(path.dirname(out), 'entry.js')
fs.writeFileSync(entry, `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { DocDialog } from '${path.join(FE, 'src', 'views', 'LogisticsDocPanel.jsx').replace(/\\/g, '/')}'
window.__classPatches = []
const ctx = {
  saveClass(docNo, patch) { window.__classPatches.push({ docNo, patch }) }, saveMode() {}, saveNote() {}, doConfirm() {}, carryOf() { return null },
  carryForm: {}, setCarryForm() {}, addCarry() {}, delCarry() {}, subjOpts: ['深圳星期零'],
  FEE_CANON: ['出库运费'], bizOpts: ['植物肉', '零售'], withCur: (a, x) => a.includes(x) ? a : [x, ...a], onDupOk() {},
}
const root = createRoot(document.getElementById('root'))
window.__mount = (x, c = null) => root.render(React.createElement(DocDialog, {
  x, c, dup: null, pos: 1, total: 1, onPrev: null, onNext: null,
  onClose() {}, locked: false, ctx,
}))
`)
esbuild.buildSync({
  entryPoints: [entry], bundle: true, format: 'iife', outfile: browserOut,
  jsx: 'automatic', logLevel: 'silent', nodePaths: [path.join(FE, 'node_modules')],
})

const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')

async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' })
  try {
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.route('http://localhost:9/**', route => {
      if (route.request().url().endsWith('/entry.js')) {
        return route.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(browserOut) })
      }
      return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div><script src="/entry.js"></script>' })
    })
    await page.goto('http://localhost:9/')
    for (const [flags, expected] of [
      [{ qty_enabled: true, price_enabled: true }, '价待确认'],
      [{ qty_enabled: false, price_enabled: true }, '未核量'],
      [{ qty_enabled: true, price_enabled: false }, '未核价'],
    ]) {
      await page.evaluate(x => window.__mount(x), { ...doc, ...flags })
      await page.locator('.ldd-dlg').waitFor()
      assert.match(await page.locator('.ldd-vbar').innerText(), new RegExp(expected))
    }
    for (const [source, label] of [
      ['raw', '物流原文'], ['default', '系统默认（取数说明）'], ['none', '未标注'],
      ['manual', '人工登记'], [null, '历史标注·来源未记录'],
    ]) {
      await page.evaluate(({ d, source }) => window.__mount(d, {
        v: '归类一致', sys: '深圳星期零 · 零售', sys_d: '', annot: '销售单-零售其他', annot_source: source,
        bill: '深圳星期零 · 零售', bill_d: '', diff: [], acc_miss: false, hint: '', txt: '', unk: '', how: [], amount: 21,
      }), { d: doc, source })
      const text = await page.locator('.ldd-chain').innerText()
      assert.match(text, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
      assert.doesNotMatch(text, /物流部标注/)
      if (source === 'default' || source === 'none') assert.doesNotMatch(text, /要改/)
    }
    await page.evaluate(d => window.__mount(d), { ...doc, subject: '深圳星期零', fee_item: '出库运费', bbiz: '植物肉', biz_bill: '植物肉' })
    await page.getByRole('button', { name: /^改归类/ }).click()
    const bizSelect = page.getByLabel('业务线', { exact: true })
    assert.equal(await bizSelect.inputValue(), '植物肉')
    await bizSelect.selectOption('零售')
    assert.deepEqual(await page.evaluate(() => window.__classPatches.at(-1)), { docNo: 'XSCKD1', patch: { bizline: '零售' } })
    await page.evaluate(d => window.__mount(d), { ...doc, subject: '深圳星期零', fee_item: '出库运费', bbiz: '零售', biz_bill: '植物肉', biz_ovr: '零售', ovr_reason: '特批' })
    await page.getByRole('button', { name: '恢复账单原值', exact: true }).click()
    assert.deepEqual(await page.evaluate(() => window.__classPatches.at(-1)), { docNo: 'XSCKD1', patch: { restore: true } })
    assert.deepEqual(errors, [])
    console.log('物流逐单回归通过：双轴、标注来源、业务线调整及恢复账单原值的真实 Chrome 弹窗渲染均正常')
  } finally {
    await browser.close()
  }
}

main().catch(e => { console.error(e); process.exitCode = 1 })
