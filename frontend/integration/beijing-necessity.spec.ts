import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, method: 'post' | 'put', url: string, data: unknown) {
  const response = await request[method](url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('北京可研建设必要性：原文证据、候选取消、采用与 Plate 刷新', async ({ page, request }) => {
  test.setTimeout(180_000)
  const project = await send(request, 'post', '/api/projects', { name: '北京必要性浏览器 QA' })
  const base = `/api/projects/${project.id}`
  const sourceFile = await readFile(resolve(process.cwd(), '../test-fixtures/public-reports/01_beijing_feasibility.pdf'))
  const documentResponse = await request.post(`${base}/documents`, { multipart: {
    file: { name: '北京交通枢纽项目建议书.pdf', mimeType: 'application/pdf', buffer: sourceFile },
  } })
  expect(documentResponse.ok(), await documentResponse.text()).toBeTruthy()
  const document = await documentResponse.json()
  const sourcePage = await (await request.get(`${base}/documents/${document.id}?page=17`)).json()
  const segment = sourcePage.segments.find((item: { text: string }) =>
    item.text.includes('计划与地铁同期开通') && item.text.includes('枢纽具有公交'))
  expect(segment).toBeTruthy()
  const statements = [
    { key: 'station_plan', label: '地铁接驳计划', value: segment.text.match(/马昌营公共交通枢纽紧邻.{0,85}?承接地铁客流/)?.[0] },
    { key: 'transfer_functions', label: '规划接驳功能', value: segment.text.match(/枢纽具有公交.{0,150}?接驳换乘的功能/)?.[0] },
  ]
  expect(statements.every((item) => item.value)).toBeTruthy()
  for (const item of statements) {
    await send(request, 'post', `${base}/facts`, {
      key: item.key, label: item.label, data_type: 'text', value: item.value,
      source: '北京公开可研原文第17页',
    })
    await send(request, 'post', `${base}/facts/${item.key}/evidence/bind`, {
      document_id: document.id, source_refs: [segment.ref],
    })
    await send(request, 'post', `${base}/evidence`, {
      document_id: document.id, source_refs: [segment.ref], statement: item.value,
      label: item.label, subject: '马昌营公共交通枢纽', fact_key: item.key,
    })
  }
  let config = await send(request, 'post', `${base}/analysis/configs`, { name: '建设背景与必要性' })
  config = await send(request, 'put', `${base}/analysis/configs/${config.id}`, {
    revision: config.revision, name: config.name,
    definitions: statements.map((item) => ({ key: item.key, label: item.label, data_type: 'text',
      unit: '', group: '原文', computed: false })),
    rules: [], sections: [{ id: 'necessity', title: '建设背景与必要性', kind: 'narrative',
      result_keys: [], evidence_keys: statements.map((item) => item.key),
      forbidden_terms: ['已开通', '客流已达到', '资金已落实'] }],
  })
  const trial = await send(request, 'post', `${base}/analysis/configs/${config.id}/test`, {
    revision: config.revision, sample_inputs: Object.fromEntries(statements.map((item) => [item.key, item.value])),
  })
  await send(request, 'post', `${base}/analysis/configs/${config.id}/publish`, {
    revision: config.revision, test_token: trial.test_token,
  })
  const report = await send(request, 'post', `${base}/reports`, {
    title: '建设必要性来源核对', report_type: 'government_feasibility',
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.locator('.scenario-outline-title').filter({ hasText: '建设背景与必要性' }).click()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '原文摘录' }).click()
  await page.locator('.scenario-evidence-list').first().getByText('项目证据').click()
  await page.getByRole('checkbox', { name: /选用证据 地铁接驳计划/ }).check()
  await page.getByRole('checkbox', { name: /选用证据 规划接驳功能/ }).check()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('计划与地铁同期开通')
  await expect(page.locator('.scenario-candidate')).not.toContainText('已开通')
  await page.getByRole('button', { name: '取消候选' }).click()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).version).toBe(0)
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('button', { name: '更新本章' }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText('计划与地铁同期开通')
  await page.reload()
  await expect(page.locator('[contenteditable="true"]')).toContainText('计划与地铁同期开通')
  const stored = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(stored.content.filter((block: { section_id?: string; project_evidence_refs?: unknown[] }) =>
    block.section_id === 'necessity' && block.project_evidence_refs?.length).length).toBe(2)
  await page.setViewportSize({ width: 900, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})
