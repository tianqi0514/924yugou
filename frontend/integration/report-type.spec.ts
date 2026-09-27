import { expect, test } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('空项目可选择政府投资章纲，刷新后保持结构且不继承历史数值', async ({ page, request }) => {
  const response = await request.post('/api/projects', { data: { name: `章纲 QA ${Date.now()}` } })
  expect(response.ok(), await response.text()).toBeTruthy()
  const project = await response.json()
  await page.goto(`/?project=${project.id}`)
  await page.getByRole('button', { name: '报告写作' }).click()
  await page.getByRole('button', { name: '新建报告' }).click()
  await page.getByRole('dialog', { name: '新建报告' }).getByRole('textbox', { name: '报告标题' }).fill('政府投资可研初稿')
  await page.getByRole('combobox', { name: '报告类型' }).selectOption('government_feasibility')
  await page.getByRole('dialog', { name: '新建报告' }).getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.locator('.scenario-outline')).toContainText('建设背景与必要性')
  await expect(page.locator('.scenario-outline')).toContainText('投资估算与资金筹措')
  const reportId = new URL(page.url()).searchParams.get('report')
  expect(reportId).toBeTruthy()
  const stored = await (await request.get(`/api/projects/${project.id}/reports/${reportId}`)).json()
  expect(stored.report_type).toBe('government_feasibility')
  expect(stored.template_snapshot.sections).toHaveLength(9)
  expect(stored.content.every((block: { fact_keys?: string[]; source_refs?: unknown[] }) => !block.fact_keys && !block.source_refs)).toBeTruthy()
  await page.reload()
  await expect(page.locator('.scenario-outline')).toContainText('建设背景与必要性')
  await page.setViewportSize({ width: 900, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
