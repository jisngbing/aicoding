import test from 'node:test'
import assert from 'node:assert/strict'
import { csvAnalysisOptions, csvAnalysisPrompt } from '../src/lib/csvAnalytics.js'

test('CSV tool prompt preserves mapped fields, selected range and explicit confirmations', () => {
  const options = csvAnalysisOptions({ file_id: 'a'.repeat(32) }, { order_id: '订单号', paid_date: '', status: undefined }, {
    start: '2026-10-01', end: '2026-10-15', currency: 'cny', granularity: 'line', assumePaid: true, assumeNoRefunds: false,
  })
  assert.deepEqual(options.field_map, { order_id: '订单号', paid_date: '' })
  assert.equal(options.currency, 'CNY')
  assert.equal(options.assume_paid, true)
  assert.equal(options.assume_no_refunds, false)
  const prompt = csvAnalysisPrompt(options)
  assert.ok(prompt.startsWith('CSV销售分析任务\nCSV_FILE_ID='))
  assert.ok(prompt.includes('analyze_sales_csv'))
  assert.deepEqual(JSON.parse(prompt.slice(prompt.indexOf('{'))), options)
})

test('Absent confirmations remain false and dates stay automatic', () => {
  const options = csvAnalysisOptions({ file_id: 'b'.repeat(32) }, {}, {})
  assert.equal(options.assume_paid, false)
  assert.equal(options.assume_no_refunds, false)
  assert.equal(options.start_date, '')
})
