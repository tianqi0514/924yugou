import Decimal from 'decimal.js'

/** Keep the token's visible unit, grouping and precision when a bound fact changes. */
export function refreshedFactDisplay(display: string, before: string | null, after: string | null,
  dataType: string, unit: string): string | null {
  if (before === null || after === null || before === '') return null
  if (!['integer', 'decimal'].includes(dataType)) {
    const first = display.indexOf(before)
    return first < 0 || display.indexOf(before, first + before.length) >= 0
      ? null : display.slice(0, first) + after + display.slice(first + before.length)
  }
  const tokens = display.match(/[+-]?\d[\d,]*(?:\.\d+)?/g) || []
  if (tokens.length !== 1) return null
  const token = tokens[0]
  try {
    const previous = new Decimal(before)
    const visible = new Decimal(token.replaceAll(',', ''))
    const multiplier = visible.eq(previous) ? new Decimal(1)
      : unit === '万元/条' && visible.eq(previous.times(10000)) ? new Decimal(10000) : null
    if (!multiplier) return null
    const next = new Decimal(after).times(multiplier)
    const decimals = token.includes('.') ? token.split('.')[1].length : 0
    if (next.decimalPlaces() > decimals) return null
    let formatted = next.toFixed(decimals)
    if (token.includes(',')) {
      const [whole, fraction] = formatted.split('.')
      formatted = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : `.${fraction}`)
    }
    if (token.startsWith('+') && !formatted.startsWith('-')) formatted = `+${formatted}`
    return display.replace(token, formatted)
  } catch { return null }
}
