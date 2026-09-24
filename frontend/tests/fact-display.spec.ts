import { expect, test } from '@playwright/test'
import { refreshedFactDisplay } from '../src/fact-display'

test('fact token refresh retains the visible number, unit and grouping style', () => {
  expect(refreshedFactDisplay('10', '10', '12', 'integer', '套')).toBe('12')
  expect(refreshedFactDisplay('测试数量 10 套', '10', '12', 'integer', '套')).toBe('测试数量 12 套')
  expect(refreshedFactDisplay('0', '0', '12', 'integer', '套')).toBe('12')
  expect(refreshedFactDisplay('1,234.50', '1234.50', '1250.75', 'decimal', '万元/条')).toBe('1,250.75')
  expect(refreshedFactDisplay('12,345,000', '1234.50', '1250.75', 'decimal', '万元/条')).toBe('12,507,500')
  expect(refreshedFactDisplay('供应商：禾进装备', '禾进装备', '新供应商', 'string', '')).toBe('供应商：新供应商')
  expect(refreshedFactDisplay('2027 年预计 10 套', '10', '12', 'integer', '套')).toBeNull()
  expect(refreshedFactDisplay('1,234.50', '1234.50', '1250.755', 'decimal', '万元/条')).toBeNull()
})
