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
  await expect.poll(async () => (await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id)
    .toBeTruthy()
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

test('真实可研 PDF 工程费表格行进入投资章节及交付', async ({ page, request }) => {
  test.setTimeout(180_000)
  const project = await send(request, '/api/projects', { name: `北京可研表格写作 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const upload = await request.post(`${base}/documents`, { multipart: {
    file: { name: '北京交通枢纽可研.pdf', mimeType: 'application/pdf',
      buffer: await readFile(resolve(root, 'test-fixtures/public-reports/01_beijing_feasibility.pdf')) },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const document = await upload.json()
  await page.goto(`/?project=${project.id}&section=documents&document=${document.id}&page=177`)
  await page.getByRole('button', { name: '识别本页表格' }).click()
  const row = page.locator('#segment-p177-t1-r1')
  await expect(row).toContainText('工程费')
  await row.getByText('查看单元格').click()
  await expect(row.getByRole('table')).toContainText('22122.45')
  const candidate = page.locator('.candidate-card').filter({
    has: page.locator('.candidate-title strong').getByText('工程费', { exact: true }),
  })
  await expect(candidate).toContainText('22122.45万元')
  await candidate.getByRole('button', { name: '核对' }).click()
  await candidate.getByRole('button', { name: '确认入台账' }).click()
  await expect(candidate).toContainText('已入事实台账')
  const facts = (await (await request.get(`${base}/facts`)).json()).facts as {
    key: string; label: string; value: string; unit: string; evidence_status: string
  }[]
  expect(facts).toHaveLength(1)
  const cost = facts[0]
  expect([cost.label, cost.value, cost.unit, cost.evidence_status]).toEqual([
    '工程费', '22122.45', '万元', 'SOURCE_LOCATOR_REVIEWED',
  ])
  expect((await (await request.get(`${base}/facts/${cost.key}/source`)).json()).source_ref).toBe('p177-t1-r1')
  const report = await send(request, `${base}/reports`, {
    title: '北京可研工程费原文核对', report_type: 'government_feasibility',
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /投资估算与资金筹措 · 待配置 · 查看准备情况/ }).click()
  await page.getByRole('button', { name: '配置本章' }).click()
  await page.getByLabel('用于本章 工程费').check()
  await page.getByLabel('展示指标 工程费').check()
  await page.getByRole('button', { name: '预览要求' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('22122.45万元')
  await page.getByRole('button', { name: '确认本章要求' }).click()
  await page.locator('.scenario-panel').getByRole('button', { name: '新建' }).click()
  await page.getByRole('button', { name: '创建方案' }).click()
  await page.getByRole('button', { name: '预览推演' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('22122.45')
  await expect(page.locator('.scenario-preview')).toContainText('本次无计算规则')
  await page.getByRole('button', { name: '保存方案并运行' }).click()
  await expect(page.locator('.scenario-results-list')).toContainText('工程费')
  await page.getByRole('button', { name: '用于报告' }).click()
  await page.getByRole('button', { name: '确认采用' }).click()
  await expect.poll(async () => (await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id)
    .toBeTruthy()
  const runId = (await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id as string
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('22122.45')
  await page.getByRole('button', { name: '取消候选' }).click()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { analysis_refs?: unknown[] }) => (block.analysis_refs || []).length)).toBeFalsy()
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.locator('.scenario-candidate').getByRole('button', { name: /加入报告|更新本章/ }).click()
  await expect(page.locator('.plate-content')).toContainText('工程费22122.45万元')
  await page.reload()
  await expect(page.locator('.plate-content')).toContainText('工程费22122.45万元')
  await page.locator('.scenario-canvas-tools').getByRole('button', { name: /检查/ }).click()
  await page.getByRole('button', { name: '我已核对' }).click()
  const exportResponse = await request.get(`${base}/reports/${report.id}/export?level=scenario`)
  expect(exportResponse.ok(), await exportResponse.text()).toBeTruthy()
  const archive = resolve(integrationRoot, 'exports', `pdf-table-writing-${project.id}.zip`)
  await mkdir(resolve(integrationRoot, 'exports'), { recursive: true })
  await writeFile(archive, await exportResponse.body())
  const checked = execFileSync(resolve(root, '.venv/bin/python'), [
    resolve(root, 'backend/scripts/check_scenario_export.py'), archive,
    '--project', project.id, '--report', report.id, '--run', runId,
    '--text', '工程费22122.45万元', '--text', '所选原文列示', '--text', '原文数值',
    '--text', '专业结论待核',
    '--absent-text', '资金已落实', '--absent-text', '总投资已复核',
    '--result', `${cost.key}=22122.45`,
    '--input-source-ref', 'p177-t1-r1',
  ], { cwd: root, env: { ...process.env, ...backendEnvironment }, encoding: 'utf8' })
  expect(JSON.parse(checked).checked_source_refs).toEqual(['p177-t1-r1'])
  await page.setViewportSize({ width: 900, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
})

test('真实可研投资表五行核对、分项复算与差额 0 进入交付', async ({ page, request }) => {
  test.setTimeout(240_000)
  const rows = [
    { label: '工程费', value: '22122.45', ref: 'p177-t1-r1' },
    { label: '工程建设其他费', value: '2701.55', ref: 'p177-t1-r9' },
    { label: '预备费', value: '1241.20', ref: 'p177-t1-r10' },
    { label: '征地拆迁费', value: '5190.37', ref: 'p177-t1-r11' },
    { label: '总投资', value: '31255.57', ref: 'p177-t1-r12' },
  ]
  const project = await send(request, '/api/projects', { name: `北京投资表复算 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const upload = await request.post(`${base}/documents`, { multipart: {
    file: { name: '北京交通枢纽可研.pdf', mimeType: 'application/pdf',
      buffer: await readFile(resolve(root, 'test-fixtures/public-reports/01_beijing_feasibility.pdf')) },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const document = await upload.json()
  await page.goto(`/?project=${project.id}&section=documents&document=${document.id}&page=177`)
  await page.getByRole('button', { name: '识别本页表格' }).click()
  for (const row of rows) {
    const card = page.locator('.candidate-card').filter({
      has: page.locator('.candidate-title strong').getByText(row.label, { exact: true }),
    })
    await expect(card).toContainText(`${row.value}万元`)
    await expect(card).toContainText(row.ref)
    await card.getByRole('button', { name: '核对' }).click()
    await expect(card.locator('.candidate-excerpt').first()).toContainText(row.value)
    await card.getByRole('button', { name: '确认入台账' }).click()
    await expect(card).toContainText('已入事实台账')
  }
  const facts = (await (await request.get(`${base}/facts`)).json()).facts as {
    key: string; label: string; value: string; unit: string; evidence_status: string
  }[]
  expect(facts).toHaveLength(5)
  const byName = (label: string) => facts.find((fact) => fact.label === label)!
  for (const row of rows) {
    const fact = byName(row.label)
    expect([fact.value, fact.unit, fact.evidence_status]).toEqual([
      row.value, '万元', 'SOURCE_LOCATOR_REVIEWED',
    ])
    expect((await (await request.get(`${base}/facts/${fact.key}/source`)).json()).source_ref).toBe(row.ref)
  }
  await send(request, `${base}/facts`, {
    key: 'component_sum', label: '投资分项合计', data_type: 'decimal', unit: '万元',
  })
  await send(request, `${base}/facts`, {
    key: 'investment_gap', label: '原文总额与分项差额', data_type: 'decimal', unit: '万元',
  })
  await send(request, `${base}/rules`, {
    name: '四项投资求和', target_key: 'component_sum',
    expression: rows.slice(0, 4).map((row) => byName(row.label).key).join(' + '),
  })
  await send(request, `${base}/rules`, {
    name: '原文总额减分项合计', target_key: 'investment_gap',
    expression: `${byName('总投资').key} - component_sum`,
  })
  const calculated = (await (await request.get(`${base}/facts`)).json()).facts as {
    key: string; value: string; status: string
  }[]
  expect(calculated.find((fact) => fact.key === 'component_sum')?.value).toBe('31255.57')
  expect(calculated.find((fact) => fact.key === 'investment_gap')).toMatchObject({
    value: '0.00', status: 'COMPUTED',
  })
  const report = await send(request, `${base}/reports`, {
    title: '北京可研投资分项复算', report_type: 'government_feasibility',
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /投资估算与资金筹措 · 待配置 · 查看准备情况/ }).click()
  await page.getByRole('button', { name: '配置本章' }).click()
  await page.getByLabel('用于本章 投资分项合计').check()
  await page.getByLabel('展示指标 投资分项合计').check()
  await page.getByLabel('用于本章 原文总额与分项差额').check()
  await page.getByLabel('展示指标 原文总额与分项差额').check()
  await page.getByRole('button', { name: '预览要求' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('31255.57')
  await expect(page.locator('.scenario-preview')).toContainText('原文总额与分项差额0万元')
  await page.getByRole('button', { name: '确认本章要求' }).click()
  await page.locator('.scenario-panel').getByRole('button', { name: '新建' }).click()
  await page.getByRole('button', { name: '创建方案' }).click()
  await page.getByRole('button', { name: '预览推演' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('31255.57')
  await expect(page.locator('.scenario-preview')).toContainText('0')
  await page.getByRole('button', { name: '保存方案并运行' }).click()
  await expect(page.locator('.scenario-results-list')).toContainText('原文总额与分项差额')
  await page.getByRole('button', { name: '用于报告' }).click()
  await page.getByRole('button', { name: '确认采用' }).click()
  await expect.poll(async () => (await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id)
    .toBeTruthy()
  const runId = (await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id as string
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('31255.57')
  await expect(page.locator('.scenario-candidate')).toContainText('按本项目事实计算')
  await page.locator('.scenario-candidate').getByRole('button', { name: /加入报告|更新本章/ }).click()
  await expect(page.locator('.plate-content')).toContainText('31255.57')
  await page.reload()
  await expect(page.locator('.plate-content')).toContainText('31255.57')
  await page.locator('.scenario-canvas-tools').getByRole('button', { name: /检查/ }).click()
  await page.getByRole('button', { name: '我已核对' }).click()
  const exported = await request.get(`${base}/reports/${report.id}/export?level=scenario`)
  expect(exported.ok(), await exported.text()).toBeTruthy()
  const archive = resolve(integrationRoot, 'exports', `pdf-investment-reconcile-${project.id}.zip`)
  await mkdir(resolve(integrationRoot, 'exports'), { recursive: true })
  await writeFile(archive, await exported.body())
  const checked = execFileSync(resolve(root, '.venv/bin/python'), [
    resolve(root, 'backend/scripts/check_scenario_export.py'), archive,
    '--project', project.id, '--report', report.id, '--run', runId,
    '--text', '31255.57', '--text', '按本项目事实计算', '--text', '推演依据',
    '--absent-text', '资金已落实', '--absent-text', '独立审定',
    '--result', 'component_sum=31255.57', '--result', 'investment_gap=0',
    ...rows.flatMap((row) => ['--input-source-ref', row.ref]),
  ], { cwd: root, env: { ...process.env, ...backendEnvironment }, encoding: 'utf8' })
  expect(JSON.parse(checked).checked_source_refs).toEqual(rows.map((row) => row.ref))

  await page.getByRole('button', { name: '关闭面板' }).click()
  await page.getByRole('button', { name: '输入数据', exact: true }).click()
  await page.locator('.scenario-panel').getByRole('button', { name: '新建' }).click()
  await page.getByLabel('方案输入来源').selectOption('copy')
  await page.getByLabel('方案名称').fill('工程费调整方案')
  await page.getByRole('button', { name: '创建方案' }).click()
  await page.getByLabel('工程费', { exact: true }).fill('22022.45')
  await page.getByRole('button', { name: '预览推演' }).click()
  await expect(page.locator('.scenario-preview')).toContainText('31155.57')
  await expect(page.locator('.scenario-preview')).toContainText('100万元')
  const scenarioRows = (await (await request.get(`${base}/analysis/scenarios`)).json()) as {
    id: string; name: string; inputs: Record<string, { value: string; origin: string }>
  }[]
  const changed = scenarioRows.find((item) => item.name === '工程费调整方案')!
  const baseline = scenarioRows.find((item) => item.id !== changed.id)!
  await page.getByRole('button', { name: '取消预览' }).click()
  expect((await (await request.get(`${base}/analysis/scenarios/${changed.id}/runs`)).json())).toHaveLength(0)
  expect((await (await request.get(`${base}/analysis/scenarios/${changed.id}`)).json()).inputs[byName('工程费').key].value)
    .toBe('22122.45')
  await page.getByRole('button', { name: '预览推演' }).click()
  await page.getByRole('button', { name: '保存方案并运行' }).click()
  await expect(page.locator('.scenario-results-list')).toContainText('31155.57')
  await expect(page.locator('.scenario-results-list')).toContainText('100万元')
  const changedRun = (await (await request.get(`${base}/analysis/scenarios/${changed.id}/runs`)).json())[0]
  expect(changedRun.snapshot.inputs[byName('工程费').key].origin).toBe('scenario_assumption')
  await page.getByRole('button', { name: '加入比较' }).click()
  await page.getByLabel('查看方案').selectOption(baseline.id)
  await page.getByRole('button', { name: '加入比较' }).click()
  await expect(page.locator('.scenario-compare')).toContainText('31255.57')
  await expect(page.locator('.scenario-compare')).toContainText('31155.57')
  await expect(page.locator('.scenario-compare')).toContainText('100万元')
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id).toBe(runId)
  expect((await (await request.get(`${base}/facts`)).json()).facts.find(
    (fact: { key: string }) => fact.key === byName('工程费').key).value).toBe('22122.45')
  await page.getByLabel('查看方案').selectOption(changed.id)
  await page.getByRole('button', { name: '用于报告' }).click()
  await expect(page.locator('.scenario-confirm')).toContainText('待更新')
  await page.getByRole('button', { name: '确认采用' }).click()
  await expect.poll(async () => (await (await request.get(`${base}/reports/${report.id}`)).json()).analysis_run_id)
    .toBe(changedRun.id)
  const stale = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(stale.analysis_run_id).toBe(changedRun.id)
  expect(stale.reviewed).toBeFalsy()
  expect(stale.content.some((block: { analysis_refs?: { run_id: string }[] }) =>
    block.analysis_refs?.some((ref) => ref.run_id === runId))).toBeTruthy()
  expect((await request.get(`${base}/reports/${report.id}/export?level=scenario`)).status()).toBe(409)
  await page.getByRole('button', { name: '关闭面板' }).click()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('本方案投资估算与资金筹措采用')
  await expect(page.locator('.scenario-candidate')).toContainText('31155.57')
  await expect(page.locator('.scenario-candidate')).toContainText('100万元')
  await page.getByRole('button', { name: '取消候选' }).click()
  const afterCancel = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(afterCancel.content).toEqual(stale.content)
  expect((await (await request.get(`${base}/analysis/runs/${runId}`)).json()).snapshot.results.component_sum.value)
    .toBe('31255.57')
})
