import { expect, test } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('正文事实提案先预览，确认后正文变为待核对', async ({ page, request }) => {
  const projectResponse = await request.post('/api/projects', { data: { name: '正文提案浏览器 QA' } })
  expect(projectResponse.ok(), await projectResponse.text()).toBeTruthy()
  const project = await projectResponse.json()
  const base = `/api/projects/${project.id}`
  expect((await request.post(`${base}/facts`, { data: {
    key: 'demand', label: '首年需求', data_type: 'integer', unit: '套', source: '项目原件',
  } })).ok()).toBeTruthy()
  const factChange = { fact_key: 'demand', value: '300000', source: '项目原件', reason: '原文录入' }
  const factPreview = await (await request.post(`${base}/changes/preview`, { data: factChange })).json()
  expect((await request.post(`${base}/changes/commit`, { data: {
    ...factChange, base_version: factPreview.base_version, preview_token: factPreview.preview_token,
  } })).ok()).toBeTruthy()
  const report = await (await request.post(`${base}/reports`, { data: { title: '需求分析' } })).json()
  const paragraph = { ...report.content[1], children: [{ text: '首年需求为 300000 套。' }], fact_keys: ['demand'] }
  const content = [report.content[0], paragraph]
  const savePreview = await (await request.post(`${base}/reports/${report.id}/preview`, {
    data: { content, base_version: report.version },
  })).json()
  const saved = await request.put(`${base}/reports/${report.id}`, { data: {
    content, base_version: report.version, preview_token: savePreview.preview_token,
  } })
  expect(saved.ok(), await saved.text()).toBeTruthy()

  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.locator('.plate-content').getByText('首年需求为 300000 套。').click()
  await page.getByRole('button', { name: '查看依据' }).click()
  await page.getByText('提出项目事实修订').click()
  await page.getByRole('spinbutton', { name: '提议的新数值' }).fill('200000')
  await page.getByRole('button', { name: '保存提案' }).click()
  await expect(page.getByText('项目事实尚未改变')).toBeVisible()
  expect((await (await request.get(`${base}/facts`)).json()).facts[0].value).toBe('300000')
  await page.getByRole('button', { name: '预览影响' }).click()
  await expect(page.getByText('300000 → 200000')).toBeVisible()
  await page.getByRole('button', { name: '确认修改事实' }).click()
  await expect(page.getByText('项目事实已修订')).toBeVisible()
  expect((await (await request.get(`${base}/facts`)).json()).facts[0].value).toBe('200000')
  const updated = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(updated.reviewed).toBe(false)
  expect(updated.issues.some((item: { code: string }) => item.code === 'FACT_CHANGED')).toBe(true)
})
