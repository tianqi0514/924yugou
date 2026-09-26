import { expect, test } from '@playwright/test'

test('historical article candidate can be canceled then confirmed and opened in Plate', async ({ page }) => {
  const project = { id: 'history', name: '澄岳精密', version: 0, has_corpus: true, is_builtin: true }
  const source = { corpus_id: 'cy_tray_20260918', corpus_version: 'v2', category_id: 'CAT-09',
    artifact_id: 'ART-1', record_id: '09:000051', semantic_id: 'C0052', use: '历史原文' }
  const blocks = [
    { type: 'h1', id: 'title-1', children: [{ text: '产能专题整理' }] },
    { type: 'p', id: 'para-1', section_id: 'S4', origin: 'model', source_refs: [source],
      children: [{ text: '首年需求180,000套应按原文口径核对。' }] },
    { type: 'p', id: 'para-2', section_id: 'S4', origin: 'model', source_refs: [source],
      children: [{ text: '设备可用时长变化时需重算。' }] },
  ]
  const candidate = { title: '产能专题整理', section_id: 'S4', content: blocks, corpus_version: 'v2',
    input_sha256: 'digest', model_call: { model: 'test-model' }, expires_at: 9999999999,
    preview_token: 'signed', sources: [{ id: 'C0052', record_id: '09:000051', text: '首年需求180,000套。', page: 6 }] }
  let commits = 0
  await page.route('**/api/**', async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/history/corpus/summary') return reply({ groups: [], counts: {}, integrity: { valid: true } })
    if (path === '/projects/history/corpus/categories') return reply([])
    if (path === '/projects/history/corpus/articles/sections') return reply([{ id: 'S4', name: '产能与交付' }])
    if (path === '/projects/history/corpus/articles/preview') return reply(candidate)
    if (path === '/projects/history/corpus/articles/commit') { commits += 1; return reply({ report: { id: 'article-1' } }, 201) }
    if (path === '/projects/history/reports') return reply(commits ? [{ id: 'article-1', title: candidate.title, version: 0, updated_at: '' }] : [])
    if (path === '/projects/history/facts') return reply({ facts: [] })
    if (path === '/projects/history/reports/article-1') return reply({ id: 'article-1', project_id: 'history',
      title: candidate.title, version: 0, content: blocks, reviewed: false, issues: [], fact_impacts: [], facts: [], updated_at: '' })
    if (path === '/projects/history/reports/article-1/versions') return reply([{ version: 0, reviewed: false, created_at: '' }])
    return reply({ detail: '未配置接口' }, 404)
  })
  await page.goto('/')
  await page.getByRole('button', { name: '报告写作' }).click()
  await page.getByRole('textbox', { name: '文章标题' }).fill('产能专题整理')
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.getByText('首年需求180,000套应按原文口径核对。')).toBeVisible()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  expect(commits).toBe(0)
  await page.getByRole('button', { name: '生成候选' }).click()
  await page.getByRole('button', { name: '确认并保存文章' }).click()
  await expect(page.locator('#report-editor').getByRole('heading', { name: '产能专题整理', level: 2 })).toBeVisible()
  await expect(page.locator('.plate-content')).toContainText('首年需求180,000套')
  await page.setViewportSize({ width: 500, height: 750 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(commits).toBe(1)
})
