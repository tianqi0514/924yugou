import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('可研表格无需模型即可识别、核对单元格并入事实台账', async ({ page, request }) => {
  test.setTimeout(150_000)
  const projectResponse = await request.post('/api/projects', { data: { name: `可研表格 QA ${Date.now()}` } })
  expect(projectResponse.ok(), await projectResponse.text()).toBeTruthy()
  const project = await projectResponse.json()
  const source = readFileSync(resolve(process.cwd(), '../test-fixtures/public-reports/01_beijing_feasibility.pdf'))
  const upload = await request.post(`/api/projects/${project.id}/documents`, { multipart: {
    file: { name: '北京可研.pdf', mimeType: 'application/pdf', buffer: source },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const document = await upload.json()
  await page.goto(`/?project=${project.id}&section=documents&document=${document.id}&page=7`)
  await expect(page.getByText('PDF 第 7 / 205 页')).toBeVisible()
  await expect(page.getByText('模型未配置')).toBeVisible()
  await page.getByRole('button', { name: '识别本页表格' }).click()
  const sourceRow = page.locator('#segment-p7-t1-r1')
  await expect(sourceRow).toContainText('用地总面积')
  await sourceRow.getByText('查看单元格').click()
  await expect(sourceRow.getByRole('table')).toContainText('73431.105')
  await expect(sourceRow.getByRole('table')).toContainText('㎡')
  const candidate = page.locator('.candidate-card').filter({ hasText: '用地总面积' })
  await expect(candidate).toContainText('待审核')
  await candidate.getByRole('button', { name: '核对' }).click()
  await expect(candidate.locator('.candidate-excerpt')).toContainText('73431.105')
  await candidate.getByRole('button', { name: '定位原文行' }).click()
  await expect(sourceRow).toHaveClass(/document-segment-active/)
  expect(new URL(page.url()).searchParams.get('segment')).toBe('p7-t1-r1')
  await expect(candidate.getByRole('link', { name: '打开原件页' })).toHaveAttribute('href', /#page=7$/)
  await candidate.getByRole('button', { name: '确认入台账' }).click()
  await expect(candidate).toContainText('已入事实台账')
  const facts = (await (await request.get(`/api/projects/${project.id}/facts`)).json()).facts
  const area = facts.find((fact: { label: string }) => fact.label === '用地总面积')
  expect(area.value).toBe('73431.105')
  expect(area.unit).toBe('㎡')
  expect(area.evidence_status).toBe('SOURCE_LOCATOR_REVIEWED')
  const provenance = await (await request.get(`/api/projects/${project.id}/facts/${area.key}/source`)).json()
  expect(provenance.source_ref).toBe('p7-t1-r1')
  expect(provenance.original_url).toContain('#page=7')
  expect(provenance.review_status).toBe('SOURCE_LOCATOR_REVIEWED')
  await page.getByRole('button', { name: '识别本页表格' }).click()
  await expect(page.locator('.candidate-card')).toHaveCount(18)
  await page.reload()
  await expect(page.locator('#segment-p7-t1-r1').getByText('查看单元格')).toBeVisible()
  await page.setViewportSize({ width: 900, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
