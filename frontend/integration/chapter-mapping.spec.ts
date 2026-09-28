import { readFileSync } from 'node:fs'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { fixturePath } from './environment'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, url: string, data: unknown) {
  const response = await request.post(url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('从本项目原文事实配置章节、预览取消、生成候选并保存', async ({ page, request }) => {
  test.setTimeout(120_000)
  const project = await send(request, '/api/projects', { name: `章节映射 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const upload = await request.post(`${base}/documents`, { multipart: {
    file: { name: 'QA项目原文.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: readFileSync(fixturePath) },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const doc = await upload.json()
  const original = await (await request.get(`${base}/documents/${doc.id}`)).json()
  for (const [key, label, value] of [
    ['first_year_demand', '首年需求', '300000'], ['qualified_capacity', '合格能力', '254016'],
  ]) {
    const fact = await send(request, `${base}/facts`, { key, label, value, data_type: 'integer', unit: '套', source: 'QA原文' })
    expect(fact.value).toBe(value)
    const segment = original.segments.find((row: { text: string }) => row.text.includes(value))
    expect(segment).toBeTruthy()
    await send(request, `${base}/facts/${key}/evidence/bind`, {
      document_id: doc.id, source_refs: [segment.ref],
    })
  }
  const statement = '本项目首年客户预测需求为 300000 套，尚非已签订单。'
  const sourceSegment = original.segments.find((row: { text: string }) => row.text.includes(statement))
  await send(request, `${base}/facts`, { key: 'demand_statement', label: '需求预测表述',
    value: statement, data_type: 'text', source: 'QA原文' })
  await send(request, `${base}/facts/demand_statement/evidence/bind`, {
    document_id: doc.id, source_refs: [sourceSegment.ref],
  })
  const report = await send(request, `${base}/reports`, {
    title: '可研章节映射测试', report_type: 'government_feasibility',
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /建设背景与必要性 · 待配置 · 查看准备情况/ }).click()
  await page.getByRole('button', { name: '配置本章' }).click()
  await expect(page.getByRole('heading', { name: '本章资料映射' })).toBeVisible()
  await page.getByLabel('用于本章 需求预测表述').check()
  await page.getByRole('button', { name: '预览要求' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('1 项事实')
  const count = (await (await request.get(`${base}/analysis/configs`)).json()).length
  expect(count).toBe(0)
  await page.getByRole('button', { name: '返回修改' }).click()
  expect((await (await request.get(`${base}/analysis/configs`)).json()).length).toBe(0)
  await page.getByRole('button', { name: '预览要求' }).click()
  await page.getByRole('button', { name: '确认本章要求' }).click()
  await expect(page.getByRole('heading', { name: '生成本章' })).toBeVisible()
  const configs = await (await request.get(`${base}/analysis/configs`)).json()
  expect(configs).toHaveLength(1)
  expect(configs[0].status).toBe('PUBLISHED')
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('尚非已签订单')
  await page.locator('.scenario-candidate').getByRole('button', { name: '更新本章' }).click()
  await expect(page.locator('.plate-content')).toContainText('尚非已签订单')
  await page.reload()
  await expect(page.locator('.plate-content')).toContainText('尚非已签订单')
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).version).toBeGreaterThan(0)

  await send(request, `${base}/facts`, { key: 'planned_sales', label: '计划销售量', data_type: 'integer', unit: '套' })
  await send(request, `${base}/rules`, { name: '销售取小', target_key: 'planned_sales',
    expression: 'min(first_year_demand, qualified_capacity)' })
  await page.reload()
  await page.getByRole('button', { name: /需求分析与建设规模 · 待配置 · 查看准备情况/ }).click()
  await page.getByRole('button', { name: '配置本章' }).click()
  await page.getByLabel('用于本章 计划销售量').check()
  await page.getByLabel('展示指标 计划销售量').check()
  await page.getByRole('button', { name: '预览要求' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('254016')
  await page.locator('.scenario-preview').getByText('查看计算依据').click()
  await expect(page.locator('.scenario-preview')).toContainText('min(首年需求, 合格能力)')
  await page.getByRole('button', { name: '确认本章要求' }).click()
  await expect(page.getByRole('heading', { name: '输入数据' })).toBeVisible()
  await page.locator('.scenario-panel').getByRole('button', { name: '新建' }).click()
  await page.getByRole('button', { name: '创建方案' }).click()
  await page.getByRole('button', { name: '预览推演' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('254016')
  await page.getByRole('button', { name: '保存方案并运行' }).click()
  await page.getByRole('button', { name: '用于报告' }).click()
  await page.getByRole('button', { name: '确认采用' }).click()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('254016')
  await page.locator('.scenario-candidate').getByRole('button', { name: '更新本章' }).click()
  await page.reload()
  await expect(page.locator('.plate-content')).toContainText('尚非已签订单')
  await expect(page.locator('.plate-content')).toContainText('计划销售量254016套')
  await page.setViewportSize({ width: 900, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
