import { expect, test } from '@playwright/test'

test('project switch, navigation and project creation remain usable on narrow screens', async ({ page }) => {
  const history = { id: 'history', name: '历史资料', version: 1, has_corpus: true, is_builtin: true }
  const work = { id: 'work', name: '新项目', version: 0, has_corpus: false, is_builtin: false }
  let created = false

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects' && request.method() === 'GET') return reply(created ? [history, work, { ...work, id: 'created', name: '演示项目' }] : [history, work])
    if (path === '/projects' && request.method() === 'POST') {
      created = true
      return reply({ ...work, id: 'created', name: '演示项目' })
    }
    if (path === '/projects/history/corpus/summary') return reply({
      version: 'v2', review_status: '已入库', groups: [], auxiliary_assets: [],
      integrity: { valid: true }, counts: { nodes: 0, relations: 0 },
    })
    if (path === '/projects/history/corpus/categories') return reply([])
    if (path === '/projects/history/corpus/articles/sections') return reply([{ id: 'S4', name: '产能与交付' }])
    if (path === '/projects/history/reports') return reply([])
    if (path === '/projects/history/facts') return reply({ project_version: 0, facts: [] })
    if (path === '/projects/history/rules') return reply({ rules: [], trace: [] })
    if (path === '/projects/history/documents') return reply([])
    if (/^\/projects\/(work|created)\/facts$/.test(path)) return reply({ project_version: 0, facts: [] })
    if (/^\/projects\/(work|created)\/rules$/.test(path)) return reply({ rules: [], trace: [] })
    if (/^\/projects\/(work|created)\/reports$/.test(path)) return reply([])
    if (/^\/projects\/(work|created)\/documents$/.test(path)) return reply([])
    if (path === '/model/status') return reply({ configured: false, ocr_configured: false })
    return reply({ detail: '测试未配置此接口' }, 404)
  })

  await page.goto('/')
  const primary = page.getByRole('navigation', { name: '主导航' })
  await expect(page.getByLabel('切换项目')).toHaveValue('work')
  await expect(primary.getByRole('button', { name: '报告写作' })).toBeVisible()
  await page.getByLabel('切换项目').selectOption('history')
  const labels = ['项目文件', '项目资料', '项目事实', '规则计算', '报告写作']
  for (const label of labels) await expect(primary.getByRole('button', { name: label })).toBeVisible()
  await expect(primary.getByRole('button')).toHaveCount(labels.length)
  await primary.getByRole('button', { name: '项目文件' }).click()
  await expect(page.getByRole('heading', { name: '项目文件' })).toBeVisible()
  await expect(page.getByText('暂无文件')).toBeVisible()
  await expect(page.getByText('上传 PDF / DOCX')).toHaveCount(0)
  await primary.getByRole('button', { name: '项目事实' }).click()
  await expect(page.getByText('暂无事实')).toBeVisible()
  await expect(page.getByRole('button', { name: '新增事实' })).toHaveCount(0)
  await primary.getByRole('button', { name: '规则计算' }).click()
  await expect(page.getByText('暂无规则')).toBeVisible()
  await expect(page.getByRole('button', { name: '新增规则' })).toHaveCount(0)
  await primary.getByRole('button', { name: '报告写作' }).click()
  await expect(page.getByRole('button', { name: '生成候选' })).toBeVisible()
  await page.reload()
  await expect(primary.getByRole('button', { name: '报告写作' })).toHaveAttribute('aria-current', 'page')

  await page.getByLabel('切换项目').selectOption('work')
  await expect(primary.getByRole('button')).toHaveCount(labels.length)
  for (const label of labels) {
    const button = primary.getByRole('button', { name: label })
    await button.click()
    await expect(button).toHaveAttribute('aria-current', 'page')
    if (label === '项目资料') await expect(page.getByText('暂无项目资料')).toBeVisible()
  }
  await expect(page.getByRole('heading', { name: '报告写作' })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('切换项目')).toHaveValue('work')
  await expect(primary.getByRole('button', { name: '报告写作' })).toHaveAttribute('aria-current', 'page')

  await primary.getByRole('button', { name: '项目资料' }).click()
  await page.reload()
  await expect(primary.getByRole('button', { name: '项目资料' })).toHaveAttribute('aria-current', 'page')
  await page.goBack()
  await expect(page).toHaveURL(/section=reports/)
  await expect(primary.getByRole('button', { name: '报告写作' })).toHaveAttribute('aria-current', 'page')

  await page.setViewportSize({ width: 900, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.setViewportSize({ width: 500, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()

  await page.getByRole('button', { name: '新建项目' }).click()
  await expect(page.getByRole('dialog', { name: '新建项目' })).toBeVisible()
  await page.getByPlaceholder('输入项目名称').fill('演示项目')
  await page.getByRole('dialog').getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByLabel('切换项目')).toHaveValue('created')
  await expect(primary.getByRole('button', { name: '项目事实' })).toHaveAttribute('aria-current', 'page')
})
