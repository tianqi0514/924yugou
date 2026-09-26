import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { execFileSync } from 'node:child_process'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { attach, watch } from './diagnostics'
import { backendEnvironment, integrationRoot, root } from './environment'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, method: 'post' | 'put', url: string, data: unknown) {
  const response = await request[method](url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('新建北京报告项目：原文定位、面积核对、方案变化、两章写作与交付', async ({ page, request }) => {
  test.setTimeout(240_000)
  await page.goto('/')
  await page.getByRole('button', { name: '新建项目' }).click()
  const projectName = `北京交通枢纽核对 ${Date.now()}`
  await page.getByRole('dialog', { name: '新建项目' }).getByLabel('项目名称').fill(projectName)
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '项目文件' })).toBeVisible()
  await expect(page.locator('.breadcrumb')).toContainText(projectName)
  const projectId = await page.getByLabel('切换项目').inputValue()
  const base = `/api/projects/${projectId}`
  expect((await (await request.get(`${base}/facts`)).json()).facts).toEqual([])
  const source = await readFile(resolve(process.cwd(), '../test-fixtures/public-reports/01_beijing_feasibility.pdf'))
  await page.locator('.upload-button input[type="file"]').setInputFiles({ name: '北京交通枢纽可研.pdf', mimeType: 'application/pdf', buffer: source })
  await expect(page.locator('.document-list')).toContainText('北京交通枢纽可研.pdf')
  const documentId = (await (await request.get(`${base}/documents`)).json())[0].id
  const searchPage = await (await request.get(`${base}/documents/${documentId}/search?q=面积&limit=1`)).json()
  expect(searchPage.results).toHaveLength(1)
  expect(searchPage.total).toBeGreaterThan(1)
  expect((await request.get(`/api/projects/other-project/documents/${documentId}/search?q=面积`)).status()).toBe(404)
  await page.getByLabel('搜索原文').fill('地上建筑面积')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.locator('.document-search-results')).toContainText('第 7 页')
  await page.locator('.document-search-results button').filter({ hasText: 'p7-s3' }).first().click()
  await expect(page.locator('#segment-p7-s3')).toHaveClass(/document-segment-active/)
  await page.reload()
  await expect(page.locator('#segment-p7-s3')).toHaveClass(/document-segment-active/)
  expect(new URL(page.url()).searchParams.get('page')).toBe('7')
  const sourcePage = await (await request.get(`${base}/documents/${documentId}?page=7`)).json()
  expect(sourcePage.segments.find((row: { ref: string }) => row.ref === 'p7-s3')?.text).toContain('17268.25')

  for (const [key, label, value, unit, sourceRef] of [
    ['aboveground_area', '地上建筑面积', '15756.25', '㎡', 'p7-s3'],
    ['underground_area', '地下建筑面积', '1512', '㎡', 'p7-s3'],
    ['reported_total_area', '原文总建筑面积', '17268.25', '㎡', 'p7-s3'],
    ['total_investment', '原文总投资', '31255.57', '万元', 'p8-s5'],
  ]) {
    await send(request, 'post', `${base}/facts`, { key, label, data_type: 'decimal', unit, source: '北京公开可研报告' })
    const change = { fact_key: key, value, source: '北京公开可研报告', reason: '原件位置核对' }
    const preview = await send(request, 'post', `${base}/changes/preview`, change)
    await send(request, 'post', `${base}/changes/commit`, { ...change, base_version: preview.base_version, preview_token: preview.preview_token })
    await send(request, 'post', `${base}/facts/${key}/evidence/bind`, { document_id: documentId, source_refs: [sourceRef] })
  }
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目事实' }).click()
  await expect(page.getByRole('row', { name: /地上建筑面积/ })).toContainText('原文位置已核对')
  await page.getByLabel('筛选事实状态').selectOption('needs-source')
  await expect(page.getByText('没有匹配事实')).toBeVisible()
  await page.getByLabel('筛选事实状态').selectOption('PROVIDED')
  await expect(page.locator('tbody tr')).toHaveCount(4)
  await page.getByRole('row', { name: /地上建筑面积/ }).getByRole('button', { name: '证据' }).click()
  await expect(page.getByLabel('证据页码')).toHaveValue('7')
  await expect(page.locator('.evidence-segments')).toContainText('17268.25')
  await expect(page.getByRole('checkbox', { name: /p7-s3/ })).toBeChecked()
  await page.getByRole('button', { name: '关闭' }).click()
  await page.getByRole('button', { name: '新增事实' }).click()
  await page.locator('.drawer details.project-more > summary').click()
  await expect(page.locator('.drawer').getByLabel('字段 key')).toHaveValue(/^fact_[a-f0-9]{12}$/)
  await page.locator('.drawer').getByLabel('字段 key').fill('area_check_custom')
  await expect(page.locator('.drawer').getByLabel('字段 key')).toHaveValue('area_check_custom')
  await page.getByRole('button', { name: '取消' }).click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目资料' }).click()
  await page.locator('.project-materials-row').filter({ hasText: '地上建筑面积' }).getByRole('button', { name: '查看来源' }).click()
  await page.getByRole('dialog', { name: '地上建筑面积来源' }).getByRole('button', { name: '查看原文位置' }).click()
  await expect(page.locator('#segment-p7-s3')).toHaveClass(/document-segment-active/)

  let config = await send(request, 'post', `${base}/analysis/configs`, { name: '建筑面积与投资核对' })
  config = await send(request, 'put', `${base}/analysis/configs/${config.id}`, {
    revision: config.revision, name: config.name,
    definitions: [
      { key: 'aboveground_area', label: '地上建筑面积', data_type: 'decimal', unit: '㎡', group: '原文', computed: false },
      { key: 'underground_area', label: '地下建筑面积', data_type: 'decimal', unit: '㎡', group: '原文', computed: false },
      { key: 'reported_total_area', label: '原文总建筑面积', data_type: 'decimal', unit: '㎡', group: '原文', computed: false },
      { key: 'total_investment', label: '原文总投资', data_type: 'decimal', unit: '万元', group: '原文', computed: false },
      { key: 'calculated_total_area', label: '分项计算建筑面积', data_type: 'decimal', unit: '㎡', group: '核对', computed: true },
      { key: 'area_difference', label: '原文与分项差额', data_type: 'decimal', unit: '㎡', group: '核对', computed: true },
    ],
    rules: [
      { id: 'area_total', name: '建筑面积分项合计', target_key: 'calculated_total_area', expression: 'aboveground_area + underground_area' },
      { id: 'area_check', name: '原文与分项差额', target_key: 'area_difference', expression: 'reported_total_area - calculated_total_area' },
    ],
    sections: [
      { id: 'area', title: '建筑面积核对', result_keys: ['reported_total_area', 'calculated_total_area', 'area_difference'],
        evidence_keys: ['aboveground_area', 'underground_area', 'reported_total_area'],
        conditions: [{ id: 'area_equal', expression: 'calculated_total_area == reported_total_area',
          when_true: '本方案建筑面积分项与原文总量一致。', when_false: '本方案建筑面积分项与原文总量不一致，需核对口径。' }] },
      { id: 'investment', title: '投资估算摘录', result_keys: ['total_investment'], evidence_keys: ['total_investment'] },
    ],
  })
  const trial = await send(request, 'post', `${base}/analysis/configs/${config.id}/test`, {
    revision: config.revision, sample_inputs: { aboveground_area: '15756.25', underground_area: '1512',
      reported_total_area: '17268.25', total_investment: '31255.57' },
  })
  expect(trial.snapshot.results.calculated_total_area.value).toBe('17268.25')
  expect(trial.snapshot.results.area_difference.value).toBe('0')
  await send(request, 'post', `${base}/analysis/configs/${config.id}/publish`, { revision: config.revision, test_token: trial.test_token })
  let scenario = await send(request, 'post', `${base}/analysis/scenarios`, { name: '原文复算', source: 'config', config_id: config.id })
  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision,
    changes: { aboveground_area: '15756.25', underground_area: '1512', reported_total_area: '17268.25', total_investment: '31255.57' },
  })
  const firstRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `beijing-${Date.now()}-1`,
  })
  expect(firstRun.snapshot.results.area_difference.value).toBe('0')
  const report = await send(request, 'post', `${base}/reports`, { title: '建筑面积与投资核对报告' })
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, { run_id: firstRun.id, base_version: report.version })
  await page.goto(`/?project=${projectId}&report=${report.id}`)
  await expect(page.locator('.scenario-title h1')).toHaveText('建筑面积与投资核对报告')
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('分项与原文总量一致')
  await page.getByRole('button', { name: '取消候选' }).click()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { section_id?: string }) => block.section_id === 'area')).toBeFalsy()
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('button', { name: '加入报告' }).click()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('combobox', { name: '生成章节' }).selectOption('investment')
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText('31255.57')
  const condition = page.locator('[contenteditable="true"] .slate-p').filter({ hasText: '本方案建筑面积分项与原文总量一致。' }).first()
  await condition.click()
  await condition.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    range.collapse(false)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  await page.keyboard.insertText(' 人工核对：面积口径来自公开原件。')
  await expect(condition).toContainText('人工核对：面积口径来自公开原件。')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  await page.reload()
  await expect(page.locator('[contenteditable="true"]')).toContainText('17268.25')
  await page.waitForTimeout(1500)
  const pendingAfterReload = await page.evaluate((id) => sessionStorage.getItem(`report-platform-report-draft:${id}`), `${projectId}:${report.id}`)
  expect(pendingAfterReload).toBeNull()
  const reviewed = await request.post(`${base}/reports/${report.id}/review`)
  expect(reviewed.ok(), await reviewed.text()).toBeTruthy()
  const exported = await request.get(`${base}/reports/${report.id}/export?level=scenario`)
  expect(exported.ok(), await exported.text()).toBeTruthy()
  const firstArchive = resolve(integrationRoot, 'exports', `beijing-${projectId}-first.zip`)
  await mkdir(resolve(integrationRoot, 'exports'), { recursive: true })
  await writeFile(firstArchive, await exported.body())
  const checked = execFileSync(resolve(root, '.venv/bin/python'), [resolve(root, 'backend/scripts/check_scenario_export.py'),
    firstArchive, '--project', projectId, '--report', report.id, '--run', firstRun.id,
    '--text', '17268.25', '--text', '31255.57', '--text', '人工核对：面积口径来自公开原件。',
    '--result', 'calculated_total_area=17268.25', '--result', 'area_difference=0',
  ], { cwd: root, env: { ...process.env, ...backendEnvironment }, encoding: 'utf8' })
  expect(JSON.parse(checked).docx_tables).toBeGreaterThan(0)
  await page.getByRole('button', { name: '返回报告列表' }).click()
  await expect(page.locator('.scenario-report-row').filter({ hasText: '建筑面积与投资核对报告' })).toContainText('已核对')
  await page.getByLabel('搜索报告').fill('不存在的标题')
  await expect(page.getByText('没有匹配报告')).toBeVisible()
  await page.getByLabel('搜索报告').fill('建筑面积')
  await page.getByLabel('筛选报告状态').selectOption('已核对')
  await expect(page.locator('.scenario-report-row')).toHaveCount(1)
  await page.locator('.scenario-report-row').click()

  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision, changes: { aboveground_area: '15000' },
  })
  const secondRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `beijing-${Date.now()}-2`,
  })
  expect(secondRun.snapshot.results.calculated_total_area.value).toBe('16512')
  expect(secondRun.snapshot.results.area_difference.value).toBe('756.25')
  const originalFact = (await (await request.get(`${base}/facts`)).json()).facts.find((item: { key: string }) => item.key === 'aboveground_area')
  expect(originalFact.value).toBe('15756.25')
  const current = await (await request.get(`${base}/reports/${report.id}`)).json()
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, { run_id: secondRun.id, base_version: current.version })
  expect((await request.get(`${base}/reports/${report.id}/export?level=scenario`)).status()).toBe(409)
  await page.reload()
  await page.getByRole('button', { name: /检查/ }).first().click()
  await expect(page.locator('.scenario-refresh')).toContainText('推演变化')
  await page.getByRole('button', { name: '处理变化' }).click()
  const choices = page.locator('.scenario-refresh input[type="checkbox"]')
  await expect(choices.first()).toBeVisible()
  const count = await choices.count()
  expect(count).toBeGreaterThan(0)
  for (let index = 0; index < count; index++) await choices.nth(index).check()
  await page.getByRole('button', { name: `更新所选 ${count} 处` }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText('16512')
  await expect(page.locator('[contenteditable="true"]')).toContainText('756.25')
  await expect(page.locator('[contenteditable="true"]')).toContainText('分项与原文总量不一致')
  await expect(page.locator('[contenteditable="true"]')).toContainText('人工核对：面积口径来自公开原件。')
  const afterRefresh = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(afterRefresh.issues.some((issue: { code: string }) => issue.code === 'ANALYSIS_RUN_STALE')).toBeTruthy()
  await page.getByRole('button', { name: '处理变化' }).click()
  await expect(page.locator('.scenario-refresh')).toContainText('相同旧值对应多个新结果')
  await page.locator('.scenario-refresh-action').filter({ hasText: '相同旧值对应多个新结果' }).getByRole('button', { name: '编辑正文' }).click()
  const summary = page.locator('[contenteditable="true"] .slate-p').filter({ hasText: '本方案建筑面积核对采用：' }).first()
  await summary.click()
  await summary.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  await page.keyboard.insertText('本方案建筑面积核对采用：原文总建筑面积17268.25㎡；分项计算建筑面积16512㎡；原文与分项差额756.25㎡。')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  await page.getByRole('button', { name: /检查/ }).first().click()
  await page.getByRole('button', { name: '处理变化' }).click()
  const rebind = page.locator('.scenario-refresh-action').filter({ hasText: '绑定当前推演' })
  await expect(rebind).toBeVisible()
  await rebind.getByRole('checkbox').check()
  await page.getByRole('button', { name: '更新所选 1 处' }).click()
  await expect.poll(async () => {
    const state = await (await request.get(`${base}/reports/${report.id}`)).json()
    return state.issues.some((issue: { code: string }) => issue.code === 'ANALYSIS_RUN_STALE')
  }).toBeFalsy()
  const changed = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(changed.issues.some((issue: { code: string; severity: string }) =>
    issue.code === 'CHAPTER_SCENARIO_ASSUMPTION' && issue.severity === 'note')).toBeTruthy()
  const reviewedAgain = await request.post(`${base}/reports/${report.id}/review`)
  expect(reviewedAgain.ok(), await reviewedAgain.text()).toBeTruthy()
  await page.reload()
  await expect(page.locator('.scenario-title')).toContainText('方案假设')
  const secondExport = await request.get(`${base}/reports/${report.id}/export?level=scenario`)
  expect(secondExport.ok(), await secondExport.text()).toBeTruthy()
  const secondArchive = resolve(integrationRoot, 'exports', `beijing-${projectId}-second.zip`)
  await writeFile(secondArchive, await secondExport.body())
  const checkedAgain = execFileSync(resolve(root, '.venv/bin/python'), [resolve(root, 'backend/scripts/check_scenario_export.py'),
    secondArchive, '--project', projectId, '--report', report.id, '--run', secondRun.id,
    '--text', '16512', '--text', '756.25', '--text', '分项与原文总量不一致',
    '--text', '人工核对：面积口径来自公开原件。',
    '--result', 'calculated_total_area=16512', '--result', 'area_difference=756.25',
  ], { cwd: root, env: { ...process.env, ...backendEnvironment }, encoding: 'utf8' })
  expect(JSON.parse(checkedAgain).docx_tables).toBeGreaterThan(0)
  expect((await request.get(`${base}/reports/${report.id}/export?level=formal`)).status()).toBe(409)
  await page.getByRole('button', { name: '返回报告列表' }).click()
  await expect(page.locator('.scenario-report-row').filter({ hasText: '建筑面积与投资核对报告' })).toContainText('已核对')
  await expect(page.locator('.scenario-report-row').filter({ hasText: '建筑面积与投资核对报告' })).toContainText('方案假设')
  const deliveries = await (await request.get(`${base}/reports/${report.id}/exports`)).json()
  expect(deliveries).toHaveLength(2)
  const firstSaved = await request.get(`${base}/reports/${report.id}/exports/${exported.headers()['x-export-id']}`)
  expect(await firstSaved.body()).toEqual(await readFile(firstArchive))
})
