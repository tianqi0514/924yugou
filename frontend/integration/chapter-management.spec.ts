import { expect, test, type APIRequestContext } from '@playwright/test'
import { attach, watch } from './diagnostics'

const projectId = '92b09c9d-801c-5fcb-90e6-67bbcd5179a6'
const base = `/api/projects/${projectId}`

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, url: string, data: unknown) {
  const response = await request.post(url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('章节目录可新增、改名、整章移动，刷新后引用与位置不丢失', async ({ page, request }) => {
  test.setTimeout(150_000)
  let scenario = await send(request, `${base}/analysis/scenarios`, { name: '章节目录浏览器验收', source: 'historical' })
  const revised = await request.put(`${base}/analysis/scenarios/${scenario.id}`, {
    data: { base_revision: scenario.revision, changes: { N017: '300000' } },
  })
  expect(revised.ok(), await revised.text()).toBeTruthy()
  scenario = await revised.json()
  const run = await send(request, `${base}/analysis/scenarios/${scenario.id}/runs`, {
    scenario_revision: scenario.revision, request_key: `chapter-${Date.now()}`,
  })
  const created = await send(request, `${base}/reports`, { title: '章节目录交互验收' })
  await send(request, `${base}/analysis/reports/${created.id}/select`, { run_id: run.id, base_version: created.version })
  for (const sectionId of ['S4', 'S7.1']) {
    const preview = await send(request, `${base}/analysis/reports/${created.id}/draft/preview`, {
      run_id: run.id, section_id: sectionId, mode: 'computed',
    })
    const { issues: _issues, ...candidate } = preview
    await send(request, `${base}/analysis/reports/${created.id}/draft/commit`, candidate)
  }
  const before = await (await request.get(`${base}/reports/${created.id}`)).json()
  const linked = before.content.find((block: { analysis_refs?: unknown[] }) => block.analysis_refs?.length)
  expect(linked?.id).toBeTruthy()

  await page.goto(`/?project=${projectId}&report=${created.id}`)
  await expect(page.locator('.scenario-outline-row')).toHaveCount(2)
  await page.getByRole('button', { name: '添加章节' }).click()
  await page.getByRole('dialog', { name: '添加章节' }).getByLabel('章节标题').fill('人工核对事项')
  await page.getByRole('dialog', { name: '添加章节' }).getByRole('button', { name: '取消' }).click()
  await expect(page.locator('.scenario-outline-row')).toHaveCount(2)
  await page.getByRole('button', { name: '添加章节' }).click()
  await page.getByRole('dialog', { name: '添加章节' }).getByLabel('章节标题').fill('人工核对事项')
  await page.getByRole('dialog', { name: '添加章节' }).getByRole('button', { name: '添加', exact: true }).click()
  await expect(page.locator('.scenario-outline-row')).toHaveCount(3)
  await page.getByRole('button', { name: '生成本章', exact: true }).click()
  await expect(page.locator('.scenario-panel select').first()).toHaveValue('')
  await expect(page.getByRole('button', { name: '生成候选' })).toBeDisabled()
  await page.getByRole('button', { name: '关闭面板' }).click()

  await page.locator('.scenario-outline-row').nth(1).hover()
  await page.getByRole('button', { name: '重命名章节 设备购置与报价' }).click()
  await page.getByRole('dialog', { name: '重命名章节' }).getByLabel('章节标题').fill('采购方案复核')
  await page.getByRole('dialog', { name: '重命名章节' }).getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.scenario-outline-row').nth(1)).toContainText('采购方案复核')
  await page.locator('.scenario-outline-row').nth(1).hover()
  await page.getByRole('button', { name: '上移章节 采购方案复核' }).click()
  await expect(page.locator('.scenario-outline-row').first()).toContainText('采购方案复核')
  await page.reload()
  await expect(page.locator('.scenario-outline-row')).toHaveCount(3)
  await expect(page.locator('.scenario-outline-row').first()).toContainText('采购方案复核')
  const after = await (await request.get(`${base}/reports/${created.id}`)).json()
  expect(after.content.find((block: { id: string }) => block.id === linked.id)).toEqual(linked)
  expect(after.content.filter((block: { type: string }) => block.type === 'h2').map((block: { children: { text: string }[] }) => block.children[0].text))
    .toEqual(['采购方案复核', '产能与交付', '人工核对事项'])
  await page.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('button', { name: '版本', exact: true }).click()
  await page.getByRole('button', { name: `v${before.version} ·`, exact: false }).click()
  await expect(page.locator('.scenario-diff')).toContainText('采购方案复核')
  await page.locator('.scenario-canvas-tools').getByRole('button', { name: /检查/ }).click()
  await page.getByRole('button', { name: '我已核对' }).click()
  const firstDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '情景分析报告' }).click()
  expect((await firstDownload).suggestedFilename()).toContain('scenario.zip')
  await page.getByRole('button', { name: '历史交付' }).click()
  await expect(page.locator('.scenario-export-row')).toHaveCount(1)
  const oldDownload = page.waitForEvent('download')
  await page.locator('.scenario-export-row').click()
  expect((await oldDownload).suggestedFilename()).toContain('scenario.zip')
  await page.route(`**${base}/reports/${created.id}/preview`, (route) => route.fulfill({ status: 503,
    contentType: 'application/json', body: JSON.stringify({ detail: '模拟保存失败' }) }))
  await page.locator('.scenario-paper .slate-p').last().click()
  await page.keyboard.type('未保存的交付后补充')
  await expect(page.locator('.scenario-title')).toContainText('保存失败')
  const unchangedDownload = page.waitForEvent('download')
  await page.locator('.scenario-export-row').click()
  expect((await unchangedDownload).suggestedFilename()).toContain('scenario.zip')
  await page.setViewportSize({ width: 500, height: 800 })
  await expect(page.locator('.scenario-outline-row').first()).toBeVisible()
  await expect(page.locator('.scenario-paper')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})
