import { expect, test, type APIRequestContext } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function create(request: APIRequestContext, url: string, data: unknown) {
  const response = await request.post(url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('Plate 保存失败后刷新恢复，服务器新版出现时明确选择保留', async ({ page, request }) => {
  test.setTimeout(120_000)
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  const project = await create(request, '/api/projects', { name: `编辑恢复 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const report = await create(request, `${base}/reports`, { title: '编辑恢复报告' })
  const url = `${base}/reports/${report.id}`
  const previewRoute = `**${url}/preview`
  await page.route(previewRoute, (route) => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ detail: '模拟网络中断' }) }))
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  const paragraph = page.locator('.scenario-paper .slate-p').last()
  await paragraph.click()
  await page.keyboard.type('未保存的正文甲')
  await expect(page.locator('.scenario-title')).toContainText('保存失败')
  expect((await (await request.get(url)).json()).content.some(
    (block: { children: { text?: string }[] }) => block.children.some((leaf) => leaf.text?.includes('正文甲')))).toBeFalsy()
  await page.unroute(previewRoute)
  await page.reload()
  await expect(page.getByRole('dialog', { name: '恢复未保存正文' })).toBeVisible()
  await page.getByRole('button', { name: '恢复本地稿' }).click()
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  await expect(page.locator('.scenario-paper')).toContainText('未保存的正文甲')
  const afterRestore = await (await request.get(url)).json()
  expect(JSON.stringify(afterRestore.content)).toContain('未保存的正文甲')

  await page.locator('.scenario-paper .slate-p').last().click()
  const caretBefore = await page.evaluate(() => window.getSelection()?.anchorOffset)
  await page.getByRole('button', { name: '输入数据' }).click()
  await page.getByRole('button', { name: '关闭面板' }).click()
  expect(await page.evaluate(() => document.activeElement?.getAttribute('contenteditable'))).toBe('true')
  expect(await page.evaluate(() => window.getSelection()?.anchorOffset)).toBe(caretBefore)

  await page.route(previewRoute, (route) => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ detail: '模拟网络中断' }) }))
  await page.keyboard.type(' 未保存的正文乙')
  await expect(page.locator('.scenario-title')).toContainText('保存失败')
  const newer = await create(request, url + '/sections/change', {
    action: 'add', title: '服务器新增章节', base_version: afterRestore.version,
  })
  expect(newer.report.version).toBe(afterRestore.version + 1)
  await page.unroute(previewRoute)
  await page.reload()
  const recovery = page.getByRole('dialog', { name: '恢复未保存正文' })
  await expect(recovery).toContainText(`服务器已更新至 v${newer.report.version}`)
  await recovery.getByRole('button', { name: '复制本地文字' }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('未保存的正文乙')
  await recovery.getByRole('button', { name: '使用服务器版本' }).click()
  await expect(page.locator('.scenario-outline')).toContainText('服务器新增章节')
  expect(JSON.stringify((await (await request.get(url)).json()).content)).not.toContain('未保存的正文乙')
  await page.reload()
  await expect(page.getByRole('dialog', { name: '恢复未保存正文' })).toHaveCount(0)

  await page.route(previewRoute, (route) => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ detail: '模拟网络中断' }) }))
  await page.locator('.scenario-paper .slate-p').last().click()
  await page.keyboard.type('本地稿丙')
  await expect(page.locator('.scenario-title')).toContainText('保存失败')
  const server = await (await request.get(url)).json()
  const newerAgain = await create(request, url + '/sections/change', {
    action: 'add', title: '服务器第二次新增', base_version: server.version,
  })
  await page.unroute(previewRoute)
  await page.reload()
  await expect(page.getByRole('dialog', { name: '恢复未保存正文' })).toContainText(`v${newerAgain.report.version}`)
  await page.getByRole('button', { name: '恢复本地稿' }).click()
  await expect(page.locator('.scenario-title')).toContainText('已保存')
  expect(JSON.stringify((await (await request.get(url)).json()).content)).toContain('本地稿丙')
  const oldVersion = await (await request.get(`${url}/compare?base=${newerAgain.report.version}`)).json()
  expect(JSON.stringify(oldVersion.base_content)).toContain('服务器第二次新增')
})
