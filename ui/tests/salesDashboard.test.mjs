import test from 'node:test'
import assert from 'node:assert/strict'
import { money, percent, trendPoints, categorySegments } from '../src/lib/salesDashboard.js'

test('Money preserves cents and large decimal strings without float rounding', () => {
  assert.equal(money('9131.90'), '9,131.90')
  assert.equal(money('12345678901234567890.15'), '12,345,678,901,234,567,890.15')
  assert.equal(money(null), '—')
  assert.equal(percent(null), '—')
  assert.equal(percent('0.12823'), '12.8%')
})

test('Category visual accounts for omitted groups and never invents missing categories', () => {
  const segments = categorySegments([{ name: '数码', paid: '600' }, { name: '家居', paid: '200' }], '1000', 1)
  assert.deepEqual(segments.map(item => item.name), ['数码', '其他品类'])
  assert.equal(segments.reduce((sum, item) => sum + item.share, 0), 1)
  assert.deepEqual(categorySegments([], '0'), [])
  assert.deepEqual(categorySegments([], '1000'), [])
})

test('Empty and single-day trends produce no invalid SVG coordinates', () => {
  assert.deepEqual(trendPoints([], 'net'), [])
  assert.deepEqual(trendPoints([{ net: '50.00' }], 'net', 100), [[360, 115]])
  const result = trendPoints([{ net: '0.00' }, { net: '0.00' }], 'net')
  assert.ok(result.flat().every(Number.isFinite))
  assert.deepEqual(result, [[60, 190], [690, 190]])
})
