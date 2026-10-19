export function money(value) {
  if (value == null) return '—'
  const [integer, fraction = ''] = String(value).split('.')
  if (!/^-?\d+$/.test(integer)) return '—'
  return `${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0').slice(0, 2)}`
}

export function percent(value) {
  return value == null || !Number.isFinite(Number(value)) ? '—' : `${(Number(value) * 100).toFixed(1)}%`
}

export function trendPoints(daily, key, maxValue) {
  const values = daily.map(item => Math.max(0, Number(item[key]) || 0))
  const max = maxValue || Math.max(...values, 1)
  return values.map((value, index) => [60 + (values.length === 1 ? 300 : index / (values.length - 1) * 630), 190 - value / max * 150])
}

export function categorySegments(categories, totalPaid, limit = 5) {
  const total = Number(totalPaid)
  if (!(total > 0) || !categories.length) return []
  const top = [...categories].filter(item => Number(item.paid) > 0).sort((a, b) => Number(b.paid) - Number(a.paid)).slice(0, limit)
  const result = top.map(item => ({ name: item.name, value: Number(item.paid), share: Number(item.paid) / total }))
  const remaining = total - result.reduce((sum, item) => sum + item.value, 0)
  if (remaining > 0.005) result.push({ name: '其他品类', value: remaining, share: remaining / total })
  return result
}
