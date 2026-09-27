import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { fixturePath } from './environment'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('文字章节从本项目原文起草、取消、采用、定位并刷新，且不需要推演', async ({ page, request }) => {
  const check = async (response: Awaited<ReturnType<typeof request.post>>) => {
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const project = await check(await request.post('/api/projects', { data: { name: `文字章节交互 QA ${Date.now()}` } }))
  const base = `/api/projects/${project.id}`
  const original = await check(await request.post(`${base}/documents`, { multipart: {
    file: { name: '文字章原件.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: readFileSync(fixturePath) },
  } }))
  const detail = await (await request.get(`${base}/documents/${original.id}`)).json()
  const statement = '本项目首年客户预测需求为 300000 套，尚非已签订单。'
  const segment = detail.segments.find((row: { text: string }) => row.text.includes(statement))
  expect(segment).toBeTruthy()
  await check(await request.post(`${base}/facts`, { data: { key: 'demand_statement', label: '预测需求性质', data_type: 'text', source: '原件' } }))
  const change = { fact_key: 'demand_statement', value: statement, source: '原件', reason: '核对原文' }
  const preview = await check(await request.post(`${base}/changes/preview`, { data: change }))
  await check(await request.post(`${base}/changes/commit`, { data: { ...change, base_version: preview.base_version, preview_token: preview.preview_token } }))
  await check(await request.post(`${base}/facts/demand_statement/evidence/bind`, { data: {
    document_id: original.id, source_refs: [segment.ref],
  } }))
  const config = await check(await request.post(`${base}/analysis/configs`, { data: { name: '文字章配置' } }))
  const saved = await check(await request.put(`${base}/analysis/configs/${config.id}`, { data: {
    revision: config.revision, name: config.name,
    definitions: [{ key: 'demand_statement', label: '预测需求性质', data_type: 'text', unit: '', group: '背景', computed: false }],
    rules: [], sections: [{ id: 'background', title: '项目背景', kind: 'narrative', result_keys: [], evidence_keys: ['demand_statement'] }],
  } }))
  const trial = await check(await request.post(`${base}/analysis/configs/${config.id}/test`, { data: {
    revision: saved.revision, sample_inputs: { demand_statement: statement },
  } }))
  await check(await request.post(`${base}/analysis/configs/${config.id}/publish`, { data: {
    revision: saved.revision, test_token: trial.test_token,
  } }))
  const report = await check(await request.post(`${base}/reports`, { data: { title: '文字章交互报告' } }))

  await page.goto(`/?project=${project.id}&section=documents&document=${original.id}`)
  const sourceSegment = page.locator(`#segment-${segment.ref}`)
  await expect(sourceSegment).toContainText(statement)
  await sourceSegment.getByRole('button', { name: '登记证据' }).click()
  await sourceSegment.getByRole('textbox', { name: '证据名称' }).fill('预测需求原文')
  await sourceSegment.getByRole('combobox', { name: '关联事实' }).selectOption('demand_statement')
  await sourceSegment.getByRole('button', { name: '确认登记' }).click()
  await expect(sourceSegment).toContainText('已登记')
  const registered = await (await request.get(`${base}/evidence?fact_key=demand_statement`)).json()
  expect(registered.items).toHaveLength(1)

  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await expect(page.getByRole('button', { name: '生成本章', exact: true })).toBeEnabled()
  await expect(page.locator('.scenario-run-badge')).toContainText('文字章节可直接起草')
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await expect(page.getByRole('button', { name: '原文摘录' })).toBeVisible()
  await page.locator('.scenario-evidence-list').first().getByText('项目证据').click()
  await page.getByRole('checkbox', { name: /选用证据 预测需求原文/ }).check()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('尚非已签订单')
  await page.getByRole('button', { name: '取消候选' }).click()
  const unchanged = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(unchanged.version).toBe(0)
  expect(await (await request.get(`${base}/analysis/reports/${report.id}/draft/candidates?section_id=background`)).json()).toHaveLength(0)
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.reload()
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await page.getByRole('button', { name: /继续上次候选/ }).click()
  await page.locator('.scenario-candidate').getByText('查看项目证据').click()
  await page.locator('.scenario-candidate').getByRole('button', { name: '预测需求原文' }).click()
  await expect(page.locator('.scenario-evidence-preview')).toContainText(statement)
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('[contenteditable="true"]')).toContainText(statement)
  await page.reload()
  await expect(page.locator('[contenteditable="true"]')).toContainText(statement)
  const stored = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(stored.content.some((block: { fact_keys?: string[]; project_evidence_refs?: { evidence_id: string }[] }) =>
    block.fact_keys?.includes('demand_statement') && block.project_evidence_refs?.[0]?.evidence_id === registered.items[0].id)).toBeTruthy()
  await page.locator('[contenteditable="true"]').getByText(/据本项目原文记载/).click()
  expect(await page.evaluate(() => window.getSelection()?.anchorNode?.parentElement?.textContent || '')).toContain('据本项目原文记载')
  await page.keyboard.press('End')
  await page.keyboard.insertText(' 此判断仍需复核。')
  await expect.poll(async () => {
    const current = await (await request.get(`${base}/reports/${report.id}`)).json()
    return current.content.some((block: { source_review_required?: boolean }) => block.source_review_required)
  }).toBeTruthy()
  await page.getByRole('button', { name: '查看依据' }).click()
  await expect(page.getByRole('button', { name: '核对本段来源' })).toBeVisible()
  await page.getByRole('button', { name: '核对本段来源' }).click()
  await expect.poll(async () => {
    const current = await (await request.get(`${base}/reports/${report.id}`)).json()
    return current.content.some((block: { source_review_required?: boolean }) => block.source_review_required)
  }).toBeFalsy()
  await page.getByRole('button', { name: '关闭面板' }).click()
  await page.getByRole('button', { name: '更多操作' }).click()
  await page.locator('.scenario-more-menu').getByRole('button', { name: '项目资料', exact: true }).click()
  await expect(page.locator('.scenario-material-summary')).toContainText('项目证据 1')
  await page.getByText('原始资料与结构').click()
  await page.locator('.scenario-material-category').filter({ hasText: '原始报告' }).getByRole('button', { name: '作用' }).click()
  await expect(page.getByRole('dialog', { name: '原始报告的作用' })).toContainText('未选择历史资料')
  await page.getByRole('dialog', { name: '原始报告的作用' }).getByRole('button', { name: '关闭' }).click()
  await page.getByRole('button', { name: '关闭面板' }).click()
  await page.locator('.scenario-canvas-tools').getByRole('button', { name: /^检查/ }).click()
  await page.getByRole('button', { name: '记录问题' }).click()
  await page.getByRole('textbox', { name: '问题标题' }).fill('订单证据待补')
  await page.getByRole('textbox', { name: '问题内容' }).fill('尚无已签订单原件')
  await page.getByRole('button', { name: '保存问题' }).click()
  await expect(page.locator('.issue-panel')).toContainText('订单证据待补')
  const withGap = await (await request.get(`${base}/reports/${report.id}`)).json()
  expect(withGap.issues.some((row: { code: string }) => row.code === 'PROJECT_GAP_OPEN')).toBeTruthy()
  await page.getByRole('button', { name: '预审稿' }).click()
  await expect(page.locator('.scenario-task-status')).toContainText('已完成')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载文件' }).click()
  expect((await download).suggestedFilename()).toContain('preview')
  const taskRows = await (await request.get(`${base}/tasks?report_id=${report.id}`)).json()
  expect(taskRows.some((row: { kind: string; status: string; result: { export_id?: string } }) =>
    row.kind === 'export' && row.status === 'COMPLETED' && !!row.result.export_id)).toBeTruthy()
  await page.setViewportSize({ width: 640, height: 760 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})
