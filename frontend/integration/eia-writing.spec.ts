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

test('环评样本两章引用同一原件，预算变化后条件与正文待更新', async ({ page, request }) => {
  test.setTimeout(240_000)
  const project = await send(request, 'post', '/api/projects', { name: `环评写作 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const source = await readFile(resolve(process.cwd(), '../test-fixtures/public-reports/02_jiangmen_eia.pdf'))
  const uploaded = await request.post(`${base}/documents`, { multipart: {
    file: { name: '江门环境影响报告表.pdf', mimeType: 'application/pdf', buffer: source },
  } })
  expect(uploaded.status(), await uploaded.text()).toBe(201)
  const documentId = (await uploaded.json()).id
  const sourcePage = await (await request.get(`${base}/documents/${documentId}?page=5`)).json()
  const sourceRef = sourcePage.segments.find((row: { text: string }) => row.text.includes('总投资') && row.text.includes('500') && row.text.includes('17'))?.ref
  expect(sourceRef, '原件表格中的总投资和环保投资应位于同一可追溯行').toBeTruthy()
  for (const [key, label, value] of [
    ['total_investment', '总投资', '500'], ['environmental_investment', '环保投资', '17'],
  ]) {
    await send(request, 'post', `${base}/facts`, { key, label, data_type: 'decimal', unit: '万元', source: '江门公开环评报告表' })
    const change = { fact_key: key, value, source: '江门公开环评报告表', reason: '原件定位核对' }
    const preview = await send(request, 'post', `${base}/changes/preview`, change)
    await send(request, 'post', `${base}/changes/commit`, {
      ...change, base_version: preview.base_version, preview_token: preview.preview_token,
    })
    await send(request, 'post', `${base}/facts/${key}/evidence/bind`, {
      document_id: documentId, source_refs: [sourceRef],
    })
  }
  let config = await send(request, 'post', `${base}/analysis/configs`, { name: '环评投资情景' })
  config = await send(request, 'put', `${base}/analysis/configs/${config.id}`, {
    revision: config.revision, name: config.name,
    definitions: [
      { key: 'total_investment', label: '总投资', data_type: 'decimal', unit: '万元', group: '原件', computed: false },
      { key: 'environmental_investment', label: '环保投资', data_type: 'decimal', unit: '万元', group: '原件', computed: false },
      { key: 'budget_limit', label: '方案预算上限', data_type: 'decimal', unit: '万元', group: '假设', computed: false },
      { key: 'budget_remaining', label: '预算内剩余空间', data_type: 'decimal', unit: '万元', group: '推演', computed: true },
    ],
    rules: [{ id: 'remaining_budget', name: '预算差额', target_key: 'budget_remaining', expression: 'max(budget_limit - environmental_investment, 0)' }],
    sections: [
      { id: 'investment', title: '投资基本情况', result_keys: ['total_investment', 'environmental_investment'],
        evidence_keys: ['total_investment', 'environmental_investment'], forbidden_terms: ['禾进装备', '已获批复'] },
      { id: 'budget', title: '环保投入情景', result_keys: ['environmental_investment', 'budget_limit', 'budget_remaining'],
        evidence_keys: ['environmental_investment'], forbidden_terms: ['排放达标', '法定合规', '项目已获批复'],
        conditions: [{ id: 'within_budget', expression: 'environmental_investment <= budget_limit',
          when_true: '本方案环保投资未超过预算上限。', when_false: '本方案环保投资超过预算上限。' }] },
    ],
  })
  const trial = await send(request, 'post', `${base}/analysis/configs/${config.id}/test`, {
    revision: config.revision, sample_inputs: { total_investment: '500', environmental_investment: '17', budget_limit: '20' },
  })
  expect(trial.snapshot.results.budget_remaining.value).toBe('3')
  await send(request, 'post', `${base}/analysis/configs/${config.id}/publish`, {
    revision: config.revision, test_token: trial.test_token,
  })
  let scenario = await send(request, 'post', `${base}/analysis/scenarios`, {
    name: '20万元预算情景', source: 'config', config_id: config.id,
  })
  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision,
    changes: { total_investment: '500', environmental_investment: '17', budget_limit: '20' },
  })
  const firstRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `eia-${Date.now()}`,
  })
  expect(firstRun.snapshot.results.budget_remaining.value).toBe('3')
  const report = await send(request, 'post', `${base}/reports`, { title: '环保投资情景分析' })
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, {
    run_id: firstRun.id, base_version: report.version,
  })

  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await expect(page.locator('.scenario-title h1')).toHaveText('环保投资情景分析')
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('证据 2/2')
  await page.getByRole('button', { name: '取消候选' }).click()
  expect((await (await request.get(`${base}/reports/${report.id}`)).json()).content.some(
    (block: { section_id?: string }) => block.section_id === 'investment')).toBeFalsy()
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('button', { name: '加入报告' }).click()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('combobox', { name: '生成章节' }).selectOption('budget')
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('预算内剩余空间3万元')
  await expect(page.locator('.scenario-candidate')).toContainText('证据 1/1')
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText('本方案环保投资未超过预算上限')
  await page.reload()
  await expect(page.locator('[contenteditable="true"]')).toContainText('预算内剩余空间3万元')
  const condition = page.locator('[contenteditable="true"] .slate-p').filter({ hasText: '本方案环保投资未超过预算上限。' }).first()
  await expect(condition).toBeVisible()
  await condition.click()
  await condition.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    range.collapse(false)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  await page.keyboard.type(' 人工补充：用途另行核对。')
  await expect(condition).toContainText('人工补充：用途另行核对。')
  const resultLabel = page.locator('[contenteditable="true"] table').last()
    .getByRole('cell', { name: '预算内剩余空间' }).locator('.slate-p')
  await resultLabel.click()
  await resultLabel.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    range.collapse(false)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  await page.keyboard.type('（人工标记）')
  await expect(resultLabel).toContainText('预算内剩余空间（人工标记）')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  await page.reload()
  await expect(page.locator('[contenteditable="true"]')).toContainText('人工补充：用途另行核对。')
  await expect(page.locator('[contenteditable="true"]')).toContainText('预算内剩余空间（人工标记）')

  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision, changes: { budget_limit: '15' },
  })
  const secondRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `eia-${Date.now()}-2`,
  })
  expect(secondRun.snapshot.results.budget_remaining.value).toBe('0')
  const current = await (await request.get(`${base}/reports/${report.id}`)).json()
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, {
    run_id: secondRun.id, base_version: current.version,
  })
  await page.reload()
  await page.getByRole('button', { name: /检查/ }).first().click()
  await expect(page.locator('.scenario-refresh')).toContainText('推演变化')
  await page.getByRole('button', { name: '处理变化' }).click()
  await expect(page.locator('.scenario-refresh')).toContainText('本方案环保投资超过预算上限')
  await expect(page.locator('.scenario-refresh-compare').filter({ hasText: '人工补充：用途另行核对。' })).toBeVisible()
  await expect(page.locator('.scenario-refresh-compare').filter({ hasText: '预算内剩余空间（人工标记）' })).toBeVisible()
  const beforeUpdate = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(beforeUpdate.issues.some((issue: { severity: string }) => issue.severity === 'block')).toBeTruthy()
  expect((await request.get(`${base}/reports/${report.id}/export?level=scenario`)).status()).toBe(409)
  await page.locator('.scenario-refresh').getByRole('button', { name: '取消' }).click()
  const afterCancel = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(afterCancel.version).toBe(beforeUpdate.version)
  expect(afterCancel.content).toEqual(beforeUpdate.content)
  await page.getByRole('button', { name: '处理变化' }).click()
  const selectable = page.locator('.scenario-refresh input[type="checkbox"]')
  await expect(selectable.first()).toBeVisible()
  const count = await selectable.count()
  expect(count).toBeGreaterThan(0)
  for (let index = 0; index < count; index++) await selectable.nth(index).check()
  await page.getByRole('button', { name: `更新所选 ${count} 处` }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText('本方案环保投资超过预算上限。')
  await expect(page.locator('[contenteditable="true"]')).toContainText('人工补充：用途另行核对。')
  await expect(page.locator('[contenteditable="true"]')).toContainText('预算内剩余空间（人工标记）')
  await expect(page.locator('[contenteditable="true"]')).toContainText('预算内剩余空间0万元')
  const updated = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(updated.issues.some((issue: { code: string }) => issue.code === 'ANALYSIS_RUN_STALE')).toBeFalsy()
  const reviewedResponse = await request.post(`${base}/reports/${report.id}/review`)
  expect(reviewedResponse.ok(), await reviewedResponse.text()).toBeTruthy()
  const exported = await request.get(`${base}/reports/${report.id}/export?level=scenario`)
  expect(exported.ok(), await exported.text()).toBeTruthy()
  expect((await exported.body()).byteLength).toBeGreaterThan(1000)
  const oldVersion = await request.get(`${base}/reports/${report.id}/versions`)
  expect((await oldVersion.json()).length).toBeGreaterThan(2)

  scenario = await send(request, 'put', `${base}/analysis/scenarios/${scenario.id}`, {
    base_revision: scenario.revision, changes: { budget_limit: '25' },
  })
  const thirdRun = await send(request, 'post', `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `eia-${Date.now()}-3`,
  })
  const beforeRebind = await (await request.get(`${base}/reports/${report.id}`)).json()
  await send(request, 'post', `${base}/analysis/reports/${report.id}/select`, {
    run_id: thirdRun.id, base_version: beforeRebind.version,
  })
  await page.reload()
  const revisedCondition = page.locator('[contenteditable="true"] .slate-p')
    .filter({ hasText: /本方案环保投资超过预算上限。.*人工补充：用途另行核对。/ }).first()
  await revisedCondition.click()
  await revisedCondition.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  await page.keyboard.type('本方案环保投资未超过预算上限。人工补充：用途另行核对。')
  await expect(page.locator('[contenteditable="true"]')).toContainText('本方案环保投资未超过预算上限。人工补充：用途另行核对。')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  await page.getByRole('button', { name: /检查/ }).first().click()
  await page.getByRole('button', { name: '处理变化' }).click()
  const rebindChoice = page.locator('.scenario-refresh-action').filter({ hasText: '绑定当前推演' })
  await expect(rebindChoice).toContainText('15万元 → 25万元')
  await rebindChoice.getByRole('checkbox').check()
  await page.getByRole('button', { name: '更新所选 1 处' }).click()
  await expect.poll(async () => {
    const state = await (await request.get(`${base}/reports/${report.id}`)).json()
    const row = state.content.find((block: { section_id?: string; children?: { text?: string }[] }) =>
      block.section_id === 'budget' && block.children?.some((leaf) => leaf.text?.includes('人工补充：用途另行核对。')))
    return row?.analysis_refs?.every((ref: { run_id: string }) => ref.run_id === thirdRun.id) || false
  }).toBeTruthy()
  const rebound = await (await request.get(`${base}/reports/${report.id}`)).json()
  const reboundCondition = rebound.content.find((block: { section_id?: string; children?: { text?: string }[] }) =>
    block.section_id === 'budget' && block.children?.some((leaf) => leaf.text?.includes('人工补充：用途另行核对。')))
  expect(reboundCondition.analysis_refs.every((ref: { run_id: string }) => ref.run_id === thirdRun.id)).toBeTruthy()
  expect(rebound.issues.some((issue: { code: string }) => issue.code === 'ANALYSIS_RUN_STALE')).toBeTruthy()
  await page.clock.install()
  const editor = page.locator('[contenteditable="true"]')
  await editor.click()
  await editor.press('End')
  await editor.type('待保存测试')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目资料' }).click()
  await expect(page.locator('.scenario-title h1')).toHaveText('环保投资情景分析')
  await expect(page.getByRole('alert')).toContainText('请先保存正文')
  await page.clock.runFor(1500)
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目资料' }).click()
  await expect(page.locator('.project-materials-summary')).toContainText('原件 1')
  await expect(page.locator('.project-materials-summary')).toContainText('项目事实 2')
  await expect(page.locator('.project-materials-section').first()).toContainText('江门环境影响报告表.pdf')
  await page.getByRole('button', { name: '查看来源' }).first().click()
  await expect(page.getByRole('dialog', { name: '总投资来源' })).toContainText('原文位置已核对')
  await expect(page.getByRole('dialog', { name: '总投资来源' })).toContainText('第 5 页')
  await page.getByRole('dialog', { name: '总投资来源' }).getByRole('button', { name: '关闭' }).click()
  await page.setViewportSize({ width: 500, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.getByRole('button', { name: '查看内容' }).click()
  await expect(page).toHaveURL(new RegExp(`document=${documentId}`))
  await expect(page.getByRole('heading', { name: '江门环境影响报告表.pdf' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '江门环境影响报告表.pdf' })).toBeVisible()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '报告写作' }).click()
  await expect(page.locator('.scenario-title h1')).toHaveText('环保投资情景分析')
  await page.setViewportSize({ width: 500, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})
