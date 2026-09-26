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

test('非可研样本按证据与条件生成，按段采用并在变更后更新判断', async ({ page, request }) => {
  const project = await send(request, 'post', '/api/projects', { name: `事故情景 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const source = await readFile(resolve(process.cwd(), '../test-fixtures/public-reports/04_jinjiang_accident.pdf'))
  const uploaded = await request.post(`${base}/documents`, { multipart: {
    file: { name: '晋江事故调查报告.pdf', mimeType: 'application/pdf', buffer: source },
  } })
  expect(uploaded.status(), await uploaded.text()).toBe(201)
  const documentId = (await uploaded.json()).id
  const sourcePage = await (await request.get(`${base}/documents/${documentId}?page=3`)).json()
  const sourceRef = sourcePage.segments.find((row: { text: string }) => row.text.includes('77.0239'))?.ref
  expect(sourceRef).toBeTruthy()
  await send(request, 'post', `${base}/facts`, {
    key: 'direct_loss', label: '直接经济损失', data_type: 'decimal', unit: '万元', source: '事故调查公开原件',
  })
  const factChange = { fact_key: 'direct_loss', value: '77.0239', source: '事故调查公开原件', reason: '原件定位核对' }
  const factPreview = await send(request, 'post', `${base}/changes/preview`, factChange)
  await send(request, 'post', `${base}/changes/commit`, {
    ...factChange, base_version: factPreview.base_version, preview_token: factPreview.preview_token,
  })
  await send(request, 'post', `${base}/facts/direct_loss/evidence/bind`, {
    document_id: documentId, source_refs: [sourceRef],
  })
  let config = await send(request, 'post', `${base}/analysis/configs`, { name: '事故损失情景' })
  config = await send(request, 'put', `${base}/analysis/configs/${config.id}`, {
    revision: config.revision, name: config.name,
    definitions: [
      { key: 'direct_loss', label: '直接经济损失', data_type: 'decimal', unit: '万元', group: '损失', computed: false },
      { key: 'loss_threshold', label: '方案阈值', data_type: 'decimal', unit: '万元', group: '假设', computed: false },
      { key: 'remaining', label: '距阈值差额', data_type: 'decimal', unit: '万元', group: '推演', computed: true },
    ],
    rules: [{ id: 'remaining_rule', name: '差额', target_key: 'remaining', expression: 'max(loss_threshold - direct_loss, 0)' }],
    sections: [{ id: 'loss', title: '经济损失情景', result_keys: ['direct_loss', 'loss_threshold', 'remaining'],
      evidence_keys: ['direct_loss'], forbidden_terms: ['事故等级'],
      conditions: [{ id: 'under_threshold', expression: 'direct_loss <= loss_threshold',
        when_true: '损失低于或等于本方案设定阈值。', when_false: '损失高于本方案设定阈值。' }],
    }],
  })
  const trial = await send(request, 'post', `${base}/analysis/configs/${config.id}/test`, {
    revision: config.revision, sample_inputs: { direct_loss: '77.0239', loss_threshold: '80' },
  })
  await send(request, 'post', `${base}/analysis/configs/${config.id}/publish`, {
    revision: config.revision, test_token: trial.test_token,
  })
  let scenario = await send(request, 'post', `${base}/analysis/scenarios`, {
    name: '80万元阈值情景', source: 'config', config_id: config.id,
  })
  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision, changes: { direct_loss: '77.0239', loss_threshold: '80' },
  })
  const firstRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `qa-${Date.now()}`,
  })
  const report = await send(request, 'post', `${base}/reports`, { title: '事故调查情景分析' })
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, {
    run_id: firstRun.id, base_version: report.version,
  })

  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await expect(page.locator('.scenario-title h1')).toHaveText('事故调查情景分析')
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await expect(page.getByRole('button', { name: '模型起草' })).toBeVisible()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('2.9761')
  await expect(page.locator('.scenario-candidate')).toContainText('证据 1/1')
  await page.getByRole('button', { name: '取消候选' }).click()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { section_id?: string }) => block.section_id === 'loss')).toBeFalsy()
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('checkbox', { name: '采用候选 2' }).uncheck()
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText('损失低于或等于本方案设定阈值')
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { type: string }) => block.type === 'table')).toBeFalsy()

  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('checkbox', { name: '采用候选 1' }).uncheck()
  await page.getByRole('checkbox', { name: '采用候选 3' }).uncheck()
  await page.getByRole('button', { name: '更新本章' }).click()
  await expect(page.locator('[contenteditable="true"] table')).toBeVisible()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { type: string }) => block.type === 'table')).toBeTruthy()
  await page.reload()
  await expect(page.locator('[contenteditable="true"]')).toContainText('2.9761')
  await page.getByRole('button', { name: '推演结果', exact: true }).click()
  const tableFits = await page.evaluate(() => {
    const table = document.querySelector('.scenario-paper .report-table')?.getBoundingClientRect()
    const panel = document.querySelector('.scenario-panel')?.getBoundingClientRect()
    return !!table && !!panel && table.right <= panel.left
  })
  expect(tableFits).toBeTruthy()
  await page.getByRole('button', { name: '关闭面板' }).click()

  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision, changes: { loss_threshold: '70' },
  })
  const secondRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `qa-${Date.now()}-2`,
  })
  const current = await (await request.get(`${base}/reports/${report.id}`)).json()
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, {
    run_id: secondRun.id, base_version: current.version,
  })
  await page.reload()
  await page.getByRole('button', { name: /检查/ }).first().click()
  await expect(page.locator('.scenario-refresh')).toContainText('推演变化')
  await page.getByRole('button', { name: '处理变化' }).click()
  await expect(page.locator('.scenario-refresh')).toContainText('损失高于本方案设定阈值')
  const preview = await (await request.get(`${base}/analysis/reports/${report.id}/refresh/preview`)).json()
  expect(preview.actions.some((row: { kind: string }) => row.kind === 'config_condition')).toBeTruthy()
  await page.setViewportSize({ width: 500, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})
