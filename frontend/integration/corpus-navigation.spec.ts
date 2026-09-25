import { test, expect } from '@playwright/test'
import { attach, watch } from './diagnostics'

type Category = { number: number; name: string; group: string; record_count: number }

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('数据库内 30 类逐类预览与作用、图谱列表、配置模拟', async ({ page, request }) => {
  test.setTimeout(240_000)
  const projectsResponse = await request.get('/api/projects')
  expect(projectsResponse.ok()).toBeTruthy()
  const projects = await projectsResponse.json() as { id: string; is_builtin: boolean; has_corpus: boolean }[]
  const builtin = projects.find((project) => project.is_builtin && project.has_corpus)
  expect(builtin).toBeTruthy()
  const categoriesResponse = await request.get(`/api/projects/${builtin!.id}/corpus/categories`)
  expect(categoriesResponse.ok()).toBeTruthy()
  const categories = await categoriesResponse.json() as Category[]
  expect(categories).toHaveLength(30)
  expect(new Set(categories.map((item) => item.number)).size).toBe(30)

  await page.addInitScript((projectId) => {
    window.localStorage.setItem('report-platform-project', projectId)
  }, builtin!.id)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '项目资料' })).toBeVisible()
  await expect(page.locator('.catalog-card')).toHaveCount(30)
  await expect(page.locator('.corpus-metrics')).toContainText('30')
  await expect(page.locator('.corpus-metrics')).toContainText('7 组')
  await page.screenshot({ path: test.info().outputPath('corpus-overview.png'), fullPage: true })

  for (const category of categories.sort((left, right) => left.number - right.number)) {
    await page.getByRole('button', { name: `查看${category.name}内容` }).click()
    await expect(page.locator('.page-header h1')).toHaveText(category.name)
    await expect(page.locator('.category-workspace')).toBeVisible()
    await expect(page.locator('.category-meta')).toContainText(`${category.record_count} 条记录`)
    await page.locator('.category-header-actions').getByRole('button', { name: '作用说明' }).click()
    const purpose = page.getByRole('dialog', { name: '作用说明' })
    await expect(purpose).toContainText(category.name)
    await expect(purpose).toContainText('作用')
    await purpose.getByRole('button', { name: '关闭作用说明' }).click()
    await page.getByRole('button', { name: '返回目录' }).click()
  }

  const relation = categories.find((item) => item.number === 14)!
  await page.getByRole('button', { name: `查看${relation.name}内容` }).click()
  await page.locator('.category-workspace > .workspace-toolbar').getByRole('button', { name: '图谱' }).click()
  await expect(page.locator('.graph-stats')).toContainText('关系')
  await page.getByLabel('搜索图谱 ID 或名称').fill('N034')
  await page.getByLabel('搜索图谱 ID 或名称').press('Enter')
  await expect(page.locator('.graph-stats')).toContainText('对象')
  await page.getByLabel('邻居跳数').selectOption('2')
  await page.locator('.graph-search').locator('..').getByRole('button', { name: '列表' }).click()
  await expect(page.locator('.graph-edge-list')).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('corpus-graph-list.png'), fullPage: true })
})
