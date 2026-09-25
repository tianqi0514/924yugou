import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { backendEnvironment, fixturePath, integrationRoot, root } from './environment'
import { attach, watch } from './diagnostics'

type Fact = { key: string; value: string | null; status: string; revision: number }
type Report = { id: string; version: number; content: Record<string, unknown>[];
  issues: { code: string; severity: string; message: string }[];
  fact_impacts: { fact_key: string; position: number }[]; reviewed: boolean }
type Package = { required_item_ids: string[];
  items: { item_id: string; semantic_id: string | null; decision: string }[] }

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function checkpoint(label: string) {
  await test.step(label, async () => { console.log(`[NEXT-13] ${label}`) })
}

async function caretAtEnd(page: Page, selector: string) {
  await page.evaluate((css) => {
    const element = document.querySelector(css)
    const leaf = element?.querySelector('[data-slate-string]')?.firstChild
    const editable = element?.closest('[contenteditable="true"]') as HTMLElement | null
    if (!leaf || !editable) throw new Error(`无法定位 Plate 光标：${css}`)
    editable.focus()
    const range = document.createRange()
    range.setStart(leaf, leaf.textContent?.length || 0)
    range.collapse(true)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  }, selector)
}

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const response = await request.get(url)
  expect(response.ok(), `${url}: ${response.status()} ${await response.text()}`).toBeTruthy()
  return response.json() as Promise<T>
}

async function createFact(page: Page, label: string, key: string, value: string | null, unit = '套') {
  await page.getByRole('button', { name: '新增事实' }).click()
  const drawer = page.locator('.drawer')
  await drawer.getByLabel('名称').fill(label)
  await drawer.getByLabel('字段 key').fill(key)
  await drawer.getByLabel('类型').selectOption('integer')
  await drawer.getByLabel('单位').fill(unit)
  if (value === null) {
    await drawer.getByRole('button', { name: '仅创建' }).click()
  } else {
    await drawer.getByRole('button', { name: '创建并录入值' }).click()
    await drawer.getByLabel('值', { exact: true }).fill(value)
    await drawer.getByLabel('来源').fill('QA 合成原件')
    await drawer.getByRole('button', { name: /预览影响/ }).click()
    await expect(drawer.locator('.preview-box')).toContainText(value)
    await drawer.getByRole('button', { name: '确认提交' }).click()
  }
  await expect(page.getByRole('row', { name: new RegExp(label) })).toBeVisible()
}

async function bindDocumentEvidence(page: Page, label: string, ref: string) {
  await page.getByRole('row', { name: new RegExp(label) }).getByRole('button', { name: '证据' }).click()
  const drawer = page.locator('.drawer')
  await drawer.getByRole('checkbox', { name: new RegExp(ref) }).check()
  await drawer.getByRole('button', { name: '核对片段并绑定' }).click()
  await expect(drawer).toContainText('原文位置已核对')
  await drawer.getByRole('button', { name: '关闭' }).click()
}

async function chooseRequiredItems(page: Page, request: APIRequestContext, projectId: string) {
  const pack = await getJson<Package>(request, `/api/projects/${projectId}/writing/packages/S4`)
  expect(pack.required_item_ids.length).toBeGreaterThan(0)
  expect(pack.required_item_ids.every((id) => pack.items.some((entry) => entry.item_id === id && entry.decision === 'selectable'))).toBe(true)
  const checkboxes = page.locator('.corpus-writing-item input[type="checkbox"]:enabled')
  await expect(checkboxes).toHaveCount(pack.items.filter((entry) => entry.decision === 'selectable').length)
  for (const checkbox of await checkboxes.all()) await checkbox.check()
  await expect(page.getByRole('button', { name: '预览章节候选' })).toBeEnabled()
}

test('真实用户写作闭环：空项目、原件、事实、规则、章节、Plate、修订与交付', async ({ page, request }) => {
  test.setTimeout(180_000)
  await checkpoint('1/9 空项目与合成原件')
  const runName = `NEXT13 QA ${Date.now()}`
  await page.goto('/')
  await page.getByRole('button', { name: '新建项目' }).click()
  await page.getByRole('dialog', { name: '新建项目' }).getByLabel('项目名称').fill(runName)
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '项目事实' })).toBeVisible()
  const projectId = await page.getByLabel('切换项目').inputValue()
  expect(projectId).toMatch(/^[a-f\d-]{36}$/)
  const initially = await getJson<{ facts: Fact[] }>(request, `/api/projects/${projectId}/facts`)
  expect(initially.facts).toEqual([])

  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目文件' }).click()
  await page.locator('.upload-button input[type="file"]').setInputFiles(fixturePath)
  await expect(page.locator('.document-list')).toContainText('本项目事实_QA合成原件.docx')
  const documents = await getJson<{ id: string; filename: string }[]>(request, `/api/projects/${projectId}/documents`)
  expect(documents).toHaveLength(1)
  expect(documents[0].filename).toBe('本项目事实_QA合成原件.docx')

  await checkpoint('2/9 人工事实、原文证据与 min 规则')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目事实' }).click()
  await createFact(page, '首年客户需求', 'first_year_demand', '300000')
  await createFact(page, '合格能力', 'qualified_capacity', '254016')
  await createFact(page, '首年计划销售量', 'planned_sales', null)
  await bindDocumentEvidence(page, '首年客户需求', 'd-p2')
  await bindDocumentEvidence(page, '合格能力', 'd-p3')

  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '规则计算' }).click()
  await page.getByRole('button', { name: '新增规则' }).click()
  const ruleDrawer = page.locator('.drawer')
  await ruleDrawer.getByLabel('名称').fill('首年计划销售量')
  await ruleDrawer.getByLabel('结果事实').selectOption('planned_sales')
  await ruleDrawer.getByLabel('表达式').fill('min(first_year_demand, qualified_capacity)')
  await ruleDrawer.getByRole('button', { name: '保存规则' }).click()
  await expect(page.locator('.rule-card')).toContainText('254016')
  const calculated = await getJson<{ facts: Fact[] }>(request, `/api/projects/${projectId}/facts`)
  expect(calculated.facts.find((fact) => fact.key === 'planned_sales')).toMatchObject({ value: '254016', status: 'COMPUTED' })

  await checkpoint('3/9 章节写作包与 30 类资料配置模拟')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '报告写作' }).click()
  await page.getByLabel('新报告名称').fill('QA 写作闭环报告')
  await page.getByRole('button', { name: '创建报告' }).click()
  await expect(page.getByRole('heading', { name: '章节起草' })).toBeVisible()
  const reports = await getJson<{ id: string; title: string }[]>(request, `/api/projects/${projectId}/reports`)
  const reportId = reports.find((item) => item.title === 'QA 写作闭环报告')?.id
  expect(reportId).toBeTruthy()

  await page.getByRole('button', { name: '选择为只读参考' }).click()
  await page.getByLabel('选择语料章节').selectOption('S4')
  await expect(page.locator('.corpus-writing-summary')).toContainText('S4')

  await page.getByRole('button', { name: '映射事实' }).click()
  await page.getByLabel('映射 首年客户需求').selectOption('first_year_demand')
  await page.getByLabel('映射 合格能力').selectOption('qualified_capacity')
  await page.getByLabel('映射 首年计划销售量').selectOption('planned_sales')
  await page.getByRole('button', { name: '保存映射' }).click()
  await expect(page.locator('.corpus-writing-slot').filter({ hasText: '合格能力' })).toContainText('254016套')

  await page.getByRole('button', { name: /配置与模拟/ }).click()
  const materialGroups = page.locator('.material-groups [role="tab"]')
  await expect(materialGroups).toHaveCount(7)
  let materialCount = 0
  for (let index = 0; index < 7; index += 1) {
    await materialGroups.nth(index).click()
    materialCount += await page.locator('.material-row').count()
  }
  expect(materialCount).toBe(30)
  await materialGroups.first().click()
  const material = page.locator('.material-row').first()
  await material.getByRole('button', { name: '作用' }).click()
  await expect(page.getByRole('dialog', { name: /作用/ })).toContainText('资料用途')
  await page.getByRole('button', { name: '关闭作用' }).click()
  await material.getByRole('checkbox').check()
  await page.getByRole('button', { name: '模拟对比' }).click()
  await expect(page.locator('.material-results')).toContainText('未写入项目或报告')
  await expect(page.locator('.material-results')).toContainText('254016')
  await page.getByRole('button', { name: '清除' }).click()
  const setting = material.getByRole('combobox')
  const currentSetting = await setting.inputValue()
  await setting.selectOption(currentSetting === 'review' ? 'auto' : 'review')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.locator('.material-panel')).toContainText('配置已保存')
  await page.getByRole('button', { name: '收起' }).click()
  await chooseRequiredItems(page, request, projectId)
  const selectedBeforeCancel = await page.locator('.corpus-writing-item input:checked').count()
  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('button', { name: '来源' }).click()
  await expect(page.locator('.corpus-writing-source')).toContainText('客户预测不能改写为已签订单或最低采购承诺。')
  await expect(page.locator('.corpus-writing-source')).toContainText('EV-01')
  await expect(page.locator('.corpus-writing-source')).toContainText('历史参考 · 待本项目核对')
  await page.getByRole('button', { name: '关闭来源' }).click()
  await checkpoint('4/9 候选取消、重新预览、确认')
  const reportBefore = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
  await page.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.locator('.corpus-writing-candidate')).toContainText('254,016')
  await expect(page.locator('.corpus-writing-candidate')).not.toContainText('禾进装备')
  await page.getByRole('button', { name: '取消候选' }).click()
  const afterCancel = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
  expect(afterCancel.version).toBe(reportBefore.version)
  expect(afterCancel.content).toEqual(reportBefore.content)
  await expect(page.locator('.corpus-writing-list-toolbar')).toContainText(`已选 ${selectedBeforeCancel}`)
  await page.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.locator('.corpus-writing-candidate')).toBeVisible()
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('.plate-content')).toContainText('254,016')
  let report = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
  const initialGuidedIds = report.content.filter((block) => block.origin === 'guided' && block.section_id === 'S4').map((block) => block.id)
  expect(initialGuidedIds).toHaveLength(3)

  await checkpoint('5/9 Plate 事实引用、斜杠菜单、表格、保存与版本')
  await page.getByRole('navigation', { name: '写作步骤' }).getByRole('button', { name: '编辑正文' }).click()
  // Insert a separate paragraph immediately after the report title, outside
  // S4. The initial empty paragraph can be normalized away when S4 is added.
  await caretAtEnd(page, '.plate-content h1')
  await page.keyboard.press('Enter')
  await page.keyboard.type('QA 编辑补充：')
  await expect(page.locator('.plate-content h1').first()).toHaveText('QA 写作闭环报告')
  await expect(page.locator('.plate-content > [data-slate-node="element"]').filter({ hasText: 'QA 编辑补充：' })).toHaveCount(1)
  await page.keyboard.press('Enter')
  await page.keyboard.type('/')
  await expect(page.getByRole('listbox', { name: '插入内容' })).toBeVisible()
  await page.getByRole('option', { name: /插入 2×2 表格/ }).click()
  await page.locator('.plate-content table.report-table th, .plate-content table.report-table td').first().click()
  await page.keyboard.type('QA_TABLE')
  await caretAtEnd(page, '.plate-content > [data-slate-node="element"]:nth-child(2)')
  await page.locator('.plate-toolbar').getByRole('button', { name: '事实' }).click()
  await page.getByRole('menu', { name: '插入事实引用' }).getByRole('menuitem', { name: /首年客户需求/ }).click()
  await expect(page.locator('.plate-content .report-fact-token')).toHaveCount(4)
  await expect(page.locator('.plate-content > [data-slate-node="element"]:nth-child(2)')).toContainText('QA 编辑补充：')
  await page.getByRole('button', { name: '预览改动' }).click()
  await page.getByRole('button', { name: '确认保存' }).click()
  await page.reload()
  await expect(page.locator('.plate-content table.report-table')).toContainText('QA_TABLE')
  await expect(page.locator('.plate-content .report-fact-token')).toHaveCount(4)
  const edited = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
  expect(edited.content.filter((block) => Array.isArray(block.fact_keys) &&
    (block.fact_keys as string[]).includes('first_year_demand')).every((block) =>
    JSON.stringify(block.children || []).includes('"fact_key":"first_year_demand"'))).toBe(true)
  await page.locator('.report-history > summary').click()
  await page.getByLabel('比较历史版本').selectOption('0')
  await page.getByRole('button', { name: '与当前版本比较' }).click()
  await expect(page.locator('.report-history')).toContainText('v0 → v')
  await page.screenshot({ path: test.info().outputPath('report-edited.png'), fullPage: true })

  await checkpoint('6/9 事实改为 0，预览取消与提交')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目事实' }).click()
  await page.getByRole('row', { name: /首年客户需求/ }).getByRole('button', { name: '编辑' }).click()
  let edit = page.locator('.drawer')
  await edit.getByLabel('值', { exact: true }).fill('0')
  await edit.getByRole('button', { name: /预览影响/ }).click()
  await expect(edit.locator('.preview-box')).toContainText('0')
  await expect(edit.locator('.report-impact-preview')).toBeVisible()
  await edit.getByRole('button', { name: '取消' }).click()
  let facts = await getJson<{ facts: Fact[] }>(request, `/api/projects/${projectId}/facts`)
  expect(facts.facts.find((fact) => fact.key === 'first_year_demand')?.value).toBe('300000')
  await page.getByRole('row', { name: /首年客户需求/ }).getByRole('button', { name: '编辑' }).click()
  edit = page.locator('.drawer')
  await edit.getByLabel('值', { exact: true }).fill('0')
  await edit.getByRole('button', { name: /预览影响/ }).click()
  await edit.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('row', { name: /首年客户需求/ })).toContainText('0 套')
  await expect.poll(async () => {
    const current = await getJson<{ facts: Fact[] }>(request, `/api/projects/${projectId}/facts`)
    return current.facts.find((fact) => fact.key === 'first_year_demand')
  }).toMatchObject({ value: '0', status: 'PROVIDED' })
  facts = await getJson<{ facts: Fact[] }>(request, `/api/projects/${projectId}/facts`)
  expect(facts.facts.find((fact) => fact.key === 'first_year_demand')).toMatchObject({ value: '0', status: 'PROVIDED' })
  expect(facts.facts.find((fact) => fact.key === 'planned_sales')).toMatchObject({ value: '0', status: 'COMPUTED' })
  await bindDocumentEvidence(page, '首年客户需求', 'd-p4')

  await checkpoint('7/9 旧引用阻断与 UI 替换本章')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '报告写作' }).click()
  await expect(page.locator('.report-impact-preview')).toContainText('过时引用')
  const blocked = await request.get(`/api/projects/${projectId}/reports/${reportId}/export?level=preview`)
  expect(blocked.status()).toBe(409)
  await page.screenshot({ path: test.info().outputPath('stale-reference-blocked.png'), fullPage: true })

  await page.getByLabel('选择语料章节').selectOption('S4')
  await chooseRequiredItems(page, request, projectId)
  await page.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.locator('.corpus-writing-candidate')).toContainText('首年计划销售量为0套')
  await expect(page.locator('.corpus-writing-candidate')).toContainText('将替换 3 段')
  await page.getByRole('button', { name: '替换本章' }).click()
  await expect(page.locator('.plate-content')).toContainText(/首年计划销售量为0[\uFEFF\u200B]*套/)
  await expect.poll(async () => {
    const current = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
    return initialGuidedIds.every((id) => !current.content.some((block) => block.id === id))
  }).toBe(true)
  report = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
  expect(initialGuidedIds.every((id) => !report.content.some((block) => block.id === id))).toBe(true)
  await expect(page.locator('.plate-content table.report-table')).toContainText('QA_TABLE')
  await expect(page.locator('.report-impact-preview')).toContainText('过时引用')
  await page.locator('.report-impact-preview').getByRole('button', { name: '更新文内引用' }).click()
  await page.getByRole('button', { name: '预览改动' }).click()
  await page.getByRole('button', { name: '确认保存' }).click()
  await expect(page.locator('.plate-content .report-fact-token').filter({ hasText: '0套' }).first()).toBeVisible()
  await checkpoint('8/9 人工核对与正式导出闸门')
  await page.getByRole('navigation', { name: '写作步骤' }).getByRole('button', { name: '核对导出' }).click()
  await expect(page.locator('.report-check-summary')).not.toContainText('过时引用')
  await page.getByRole('button', { name: '我已核对' }).click()
  await expect(page.locator('.report-check-summary')).toContainText('正式导出可用')
  report = await getJson<Report>(request, `/api/projects/${projectId}/reports/${reportId}`)
  expect(report.reviewed).toBe(true)
  await page.screenshot({ path: test.info().outputPath('report-reviewed.png'), fullPage: true })

  await checkpoint('9/9 解包核对 DOCX、PDF 与审计')
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '正式导出' }).click()
  const download = await downloadPromise
  const output = path.join(integrationRoot, 'exports', `${projectId}.zip`)
  await mkdir(path.dirname(output), { recursive: true })
  await download.saveAs(output)
  const checked = execFileSync(path.join(root, '.venv', 'bin', 'python'), [
    path.join(root, 'backend', 'scripts', 'check_integration_export.py'), output,
    '--project', projectId, '--report', reportId!, '--level', 'formal',
    '--text', 'QA_TABLE',
    '--text', '首年客户需求0套', '--text', '规划合格能力254,016套', '--text', '首年计划销售量为0套',
    '--fact', 'first_year_demand=0',
    '--fact', 'qualified_capacity=254016', '--fact', 'planned_sales=0',
  ], { cwd: root, env: { ...process.env, ...backendEnvironment }, encoding: 'utf8' })
  await writeFile(path.join(integrationRoot, 'exports', `${projectId}.json`), checked)
  expect(JSON.parse(checked).docx_tables).toBeGreaterThan(0)
})
