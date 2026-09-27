import { readFileSync } from 'node:fs'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { fixturePath } from './environment'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, method: 'post' | 'put', url: string, data: unknown) {
  const response = await request[method](url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('逐章待办与原件旁两项事实的预览、取消、确认、回链', async ({ page, request }) => {
  test.setTimeout(150_000)
  const project = await send(request, 'post', '/api/projects', { name: `逐章准备 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const upload = await request.post(`${base}/documents`, { multipart: {
    file: { name: 'QA合成原件.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: readFileSync(fixturePath) },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const source = await upload.json()
  let config = await send(request, 'post', `${base}/analysis/configs`, { name: '需求来源准备' })
  config = await send(request, 'put', `${base}/analysis/configs/${config.id}`, {
    revision: config.revision, name: config.name,
    definitions: [
      { key: 'first_year_demand', label: '首年需求', data_type: 'integer', unit: '套', group: '需求', computed: false },
      { key: 'qualified_capacity', label: '合格能力', data_type: 'integer', unit: '套', group: '产能', computed: false },
    ],
    rules: [], sections: [{ id: 'necessity', title: '建设背景与必要性', kind: 'narrative',
      result_keys: [], evidence_keys: ['first_year_demand', 'qualified_capacity'] }],
  })
  const trial = await send(request, 'post', `${base}/analysis/configs/${config.id}/test`, {
    revision: config.revision, sample_inputs: { first_year_demand: '300000', qualified_capacity: '254016' },
  })
  await send(request, 'post', `${base}/analysis/configs/${config.id}/publish`, {
    revision: config.revision, test_token: trial.test_token,
  })
  const report = await send(request, 'post', `${base}/reports`, { title: '建设必要性准备', report_type: 'government_feasibility' })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /建设背景与必要性 · 缺资料 · 查看准备情况/ }).click()
  await expect(page.getByRole('button', { name: '录入 首年需求' })).toBeVisible()
  await page.getByRole('button', { name: '录入 首年需求' }).click()
  await expect(page.getByRole('dialog', { name: '新增事实' })).toBeVisible()
  await expect(page.getByRole('dialog', { name: '新增事实' }).getByLabel('名称')).toHaveValue('首年需求')
  await page.getByRole('dialog', { name: '新增事实' }).getByRole('button', { name: '取消' }).click()

  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目文件' }).click()
  const first = page.locator('#segment-d-p2')
  const second = page.locator('#segment-d-p3')
  await first.getByRole('button', { name: '录入事实' }).click()
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目事实' }).click()
  await expect(page.locator('.document-fact-tray')).toBeVisible()
  expect(new URL(page.url()).searchParams.get('section')).toBe('documents')
  await second.getByRole('button', { name: '录入事实' }).click()
  await page.getByLabel('事实名称 1').fill('首年需求')
  await page.getByLabel('事实类型 1').selectOption('integer')
  await page.getByLabel('事实值 1').fill('300000')
  await page.getByLabel('事实单位 1').fill('套')
  await page.getByLabel('事实名称 2').fill('合格能力')
  await page.getByLabel('事实类型 2').selectOption('integer')
  await page.getByLabel('事实值 2').fill('254016')
  await page.getByLabel('事实单位 2').fill('套')
  // Chapter definitions use stable keys; the tray keeps them in the optional details.
  await page.locator('.document-fact-row').nth(0).locator('details summary').click()
  await page.locator('.document-fact-row').nth(1).locator('details summary').click()
  const keyInputs = page.locator('.document-fact-row input[aria-label^="事实 key"]')
  await keyInputs.nth(0).fill('first_year_demand')
  await keyInputs.nth(1).fill('qualified_capacity')
  await page.getByRole('button', { name: '预览录入' }).click()
  await expect(page.locator('.document-fact-preview')).toContainText('300000')
  expect((await (await request.get(`${base}/facts`)).json()).facts).toHaveLength(0)
  await page.getByRole('button', { name: '返回修改' }).click()
  expect((await (await request.get(`${base}/facts`)).json()).facts).toHaveLength(0)
  await page.getByRole('button', { name: '预览录入' }).click()
  await page.getByRole('button', { name: '确认录入' }).click()
  await expect(page.locator('.document-fact-tray')).toHaveCount(0)
  const facts = (await (await request.get(`${base}/facts`)).json()).facts
  expect(facts).toHaveLength(2)
  expect(facts.every((item: { evidence_status: string }) => item.evidence_status === 'SOURCE_LOCATOR_REVIEWED')).toBeTruthy()
  const evidence = (await (await request.get(`${base}/evidence?fact_key=first_year_demand`)).json()).items
  expect(evidence).toHaveLength(1)
  await send(request, 'post', `${base}/issues`, {
    kind: 'claim', title: '需求不是订单', statement: '预测需求尚非已签订单',
    section_ids: ['necessity'], support_evidence_ids: [evidence[0].id],
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /建设背景与必要性 · 可起草 · 查看准备情况/ }).click()
  await expect(page.locator('.scenario-preparation').getByRole('button', { name: '生成本章' })).toBeVisible()
  await page.locator('.scenario-preparation').getByRole('button', { name: /需求不是订单/ }).click()
  await expect(page.locator('.issue-detail')).toContainText('预测需求尚非已签订单')
  await expect(page.locator('.issue-detail')).toContainText('独立核实未记录')
  await page.locator('.issue-detail').getByRole('button', { name: /首年需求/ }).click()
  await expect(page.locator('#segment-d-p2')).toBeVisible()
  await page.setViewportSize({ width: 900, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
