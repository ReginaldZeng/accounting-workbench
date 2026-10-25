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

const { judge } = require(out)
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

const entry = path.join(path.dirname(out), 'entry.jsx')
const browserOut = path.join(path.dirname(out), 'entry.js')
fs.writeFileSync(entry, `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { DocDialog } from '${path.join(FE, 'src', 'views', 'LogisticsDocPanel.jsx').replace(/\\/g, '/')}'
const ctx = {
  saveClass() {}, saveMode() {}, saveNote() {}, doConfirm() {}, carryOf() { return null },
  carryForm: {}, setCarryForm() {}, addCarry() {}, delCarry() {}, subjOpts: ['深圳星期零'],
  FEE_CANON: ['出库运费'], withCur: (a, x) => a.includes(x) ? a : [x, ...a], onDupOk() {},
}
const root = createRoot(document.getElementById('root'))
window.__mount = x => root.render(React.createElement(DocDialog, {
  x, c: null, dup: null, pos: 1, total: 1, onPrev: null, onNext: null,
  onClose() {}, locked: false, ctx,
}))
`)
esbuild.buildSync({
  entryPoints: [entry], bundle: true, format: 'iife', outfile: browserOut,
  jsx: 'automatic', logLevel: 'silent', nodePaths: [path.join(FE, 'node_modules')],
})

const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')

async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
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
    assert.deepEqual(errors, [])
    console.log('物流逐单回归通过：双轴、仅核价、仅核量的判定与真实弹窗渲染均正常')
  } finally {
    await browser.close()
  }
}

main().catch(e => { console.error(e); process.exitCode = 1 })
