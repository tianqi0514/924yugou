import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { backendEnvironment, integrationRoot, root, tableFixturePath } from './environment'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, url: string, data: unknown) {
  const response = await request.post(url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('表格确认、章节推演、Plate 与情景分析交付使用同一来源', async ({ page, request }) => {
  test.setTimeout(180_000)
  const project = await send(request, '/api/projects', { name: `表格写作 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const upload = await request.post(`${base}/documents`, { multipart: {
    file: { name: '项目业务表格.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: await readFile(tableFixturePath) },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const document = await upload.json()
  await page.goto(`/?project=${project.id}&section=documents&document=${document.id}`)
  await page.getByRole('button', { name: '识别本页表格' }).click()
  for (const label of ['首年需求', '合格能力']) {
    const card = page.locator('.candidate-card').filter({ hasText: label })
    await card.getByRole('button', { name: '核对' }).click()
    await card.getByRole('button', { name: '确认入台账' }).click()
    await expect(card).toContainText('已入事实台账')
  }
  const facts = (await (await request.get(`${base}/facts`)).json()).facts as {
    key: string; label: string; value: string; unit: string; evidence_status: string
  }[]
  expect(facts).toHaveLength(2)
  const demand = facts.find((fact) => fact.label === '首年需求')!
  const capacity = facts.find((fact) => fact.label === '合格能力')!
  expect([demand.value, capacity.value]).toEqual(['300000', '254016'])
  expect([demand.evidence_status, capacity.evidence_status]).toEqual([
    'SOURCE_LOCATOR_REVIEWED', 'SOURCE_LOCATOR_REVIEWED',
  ])
  expect((await (await request.get(`${base}/facts/${demand.key}/source`)).json()).source_ref).toBe('d-t1-r2')
  expect((await (await request.get(`${base}/facts/${capacity.key}/source`)).json()).source_ref).toBe('d-t1-r3')
  expect(facts.some((fact) => fact.label === '未提供报价')).toBeFalsy()
  await send(request, `${base}/facts`, {
    key: 'planned_sales', label: '计划销售量', data_type: 'integer', unit: '套',
  })
  await send(request, `${base}/rules`, {
    name: '销售量取小', target_key: 'planned_sales',
    expression: `min(${demand.key}, ${capacity.key})`,
  })
  const report = await send(request, `${base}/reports`, {
    title: '表格来源需求分析', report_type: 'government_feasibility',
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /需求分析与建设规模 · 待配置 · 查看准备情况/ }).click()
  await page.getByRole('button', { name: '配置本章' }).click()
  await page.getByLabel('用于本章 计划销售量').check()
  await page.getByLabel('展示指标 计划销售量').check()
  await page.getByRole('button', { name: '预览要求' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('254016')
  await expect(page.locator('.scenario-preview')).toContainText('首年需求')
  await expect(page.locator('.scenario-preview')).toContainText('合格能力')
  await page.getByRole('button', { name: '确认本章要求' }).click()
  await expect(page.getByRole('heading', { name: '输入数据' })).toBeVisible()
  await page.locator('.scenario-panel').getByRole('button', { name: '新建' }).click()
  await page.getByRole('button', { name: '创建方案' }).click()
  await page.getByRole('button', { name: '预览推演' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('254016')
  await page.getByRole('button', { name: '保存方案并运行' }).click()
  await page.getByRole('button', { name: '用于报告' }).click()
  await page.getByRole('button', { name: '确认采用' }).click()
  const adopted = await (await request.get(`${base}/reports/${report.id}`)).json()
  const runId = adopted.analysis_run_id as string
  expect(runId).toBeTruthy()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('254016')
  await page.getByRole('button', { name: '取消候选' }).click()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { analysis_refs?: unknown[] }) => (block.analysis_refs || []).length)).toBeFalsy()
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.locator('.scenario-candidate').getByRole('button', { name: /加入报告|更新本章/ }).click()
  await expect(page.locator('.plate-content')).toContainText('计划销售量254016套')
  await page.reload()
  await expect(page.locator('.plate-content')).toContainText('计划销售量254016套')
  const saved = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(saved.analysis_run_id).toBe(runId)
  expect(saved.content.some((block: { analysis_refs?: { run_id: string }[] }) =>
    block.analysis_refs?.some((ref) => ref.run_id === runId))).toBeTruthy()
  await page.locator('.scenario-canvas-tools').getByRole('button', { name: /检查/ }).click()
  await page.getByRole('button', { name: '我已核对' }).click()
  const exportResponse = await request.get(`${base}/reports/${report.id}/export?level=scenario`)
  expect(exportResponse.ok(), await exportResponse.text()).toBeTruthy()
  const archive = resolve(integrationRoot, 'exports', `table-writing-${project.id}.zip`)
  await mkdir(resolve(integrationRoot, 'exports'), { recursive: true })
  await writeFile(archive, await exportResponse.body())
  const checked = execFileSync(resolve(root, '.venv/bin/python'), [
    resolve(root, 'backend/scripts/check_scenario_export.py'), archive,
    '--project', project.id, '--report', report.id, '--run', runId,
    '--text', '计划销售量254016套', '--text', '推演依据',
    '--result', 'planned_sales=254016',
    '--input-source-ref', 'd-t1-r2', '--input-source-ref', 'd-t1-r3',
  ], { cwd: root, env: { ...process.env, ...backendEnvironment }, encoding: 'utf8' })
  expect(JSON.parse(checked).docx_tables).toBeGreaterThan(0)
})
