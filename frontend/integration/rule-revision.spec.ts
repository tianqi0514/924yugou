import { expect, test } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('规则修改先预览再保存修订，取消和刷新均可核对', async ({ page, request }) => {
  const projectResponse = await request.post('/api/projects', { data: { name: '规则修订交互 QA' } })
  expect(projectResponse.ok()).toBeTruthy()
  const project = await projectResponse.json()
  const base = `/api/projects/${project.id}`
  for (const [key, value] of [['demand', '300000'], ['capacity', '254016'],
                              ['buffer', '1000'], ['sales', null]] as const) {
    const fact = await request.post(`${base}/facts`, { data: {
      key, label: key, data_type: 'integer', unit: '套', value,
    } })
    expect(fact.ok(), await fact.text()).toBeTruthy()
  }
  const created = await request.post(`${base}/rules`, { data: {
    name: '计划销售', target_key: 'sales', expression: 'min(demand, capacity)',
  } })
  expect(created.ok(), await created.text()).toBeTruthy()

  await page.goto(`/?project=${project.id}&section=rules`)
  await expect(page.locator('.rule-card').filter({ hasText: '计划销售' })).toContainText('254016')
  await page.locator('.rule-card').filter({ hasText: '计划销售' }).click()
  await page.getByRole('dialog', { name: '计划销售' }).getByRole('button', { name: '修改规则' }).click()
  const edit = page.getByRole('dialog', { name: '修改规则 · v1' })
  await edit.getByLabel('名称').fill('计划销售（含预留）')
  await edit.getByLabel('计算式').fill('min(demand, capacity) - buffer')
  await edit.getByRole('button', { name: '预览计算' }).click()
  await expect(edit.locator('.rule-preview')).toContainText('253016')
  await edit.getByRole('button', { name: '取消' }).click()
  expect((await (await request.get(`${base}/rules`)).json()).rules[0].revision).toBe(1)

  await page.locator('.rule-card').filter({ hasText: '计划销售' }).click()
  await page.getByRole('dialog', { name: '计划销售' }).getByRole('button', { name: '修改规则' }).click()
  const again = page.getByRole('dialog', { name: '修改规则 · v1' })
  await again.getByLabel('名称').fill('计划销售（含预留）')
  await again.getByLabel('计算式').fill('min(demand, capacity) - buffer')
  await again.getByRole('button', { name: '预览计算' }).click()
  await again.getByRole('button', { name: '确认新修订' }).click()
  await expect(page.locator('.rule-card').filter({ hasText: '计划销售（含预留）' })).toContainText('253016')
  await page.reload()
  await page.locator('.rule-card').filter({ hasText: '计划销售（含预留）' }).click()
  await page.getByRole('button', { name: '修订记录' }).click()
  const history = page.getByRole('dialog', { name: '计划销售（含预留） · 修订记录' })
  await expect(history).toContainText('v2')
  await expect(history).toContainText('v1')
  await expect(history).toContainText('min(demand, capacity) - buffer')
  await expect(history).toContainText('min(demand, capacity)')
  const current = (await (await request.get(`${base}/rules`)).json()).rules[0]
  expect(current.revision).toBe(2)
})
