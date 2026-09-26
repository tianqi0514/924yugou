import { expect, test } from '@playwright/test'

test('a changed project fact leaves an old reference blocked until token refresh, save and review', async ({ page }) => {
  const project = { id: 'impact-project', name: '事实变更验收', version: 1, has_corpus: false, is_builtin: false }
  const fact = { id: 'f1', key: 'test_qty', label: '测试数量', data_type: 'integer', value: '10', status: 'PROVIDED',
    unit: '套', caliber: '本次测试', as_of: '2026-09-24', source: '本项目原件', revision: 1, updated_at: null }
  const report: { id: string; project_id: string; title: string; version: number; content: Record<string, unknown>[];
    reviewed: boolean; issues: Record<string, unknown>[]; fact_impacts: Record<string, unknown>[];
    facts: Record<string, unknown>[]; updated_at: string } = {
    id: 'r1', project_id: project.id, title: '数量验证稿', version: 1,
    content: [{ type: 'p', id: 'qty-paragraph', fact_keys: ['test_qty'], children: [
      { text: '本次测试数量为' }, { type: 'fact_ref', fact_key: 'test_qty', display: '10', children: [{ text: '' }] }, { text: '套。' },
    ] }], reviewed: true, issues: [], fact_impacts: [], facts: [fact], updated_at: '2026-09-24T00:00:00Z',
  }
  let previewChanges = 0
  let commitChanges = 0
  let savedToken: string | undefined
  let reviewCalls = 0
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/impact-project/facts') return reply({ project_version: project.version, facts: [fact] })
    if (path === '/projects/impact-project/rules') return reply({ rules: [], trace: [] })
    if (path === '/projects/impact-project/changes/preview' && request.method() === 'POST') {
      previewChanges += 1
      return reply({ base_version: 1, preview_token: 'fact-change', expires_at: 9999999999, trace: [],
        changes: [{ key: fact.key, label: fact.label, before: { value: '10', status: 'PROVIDED' }, after: { value: '12', status: 'PROVIDED' } }],
        report_impacts: [{ report_id: report.id, report_title: report.title, report_version: report.version,
          position: 1, fact_key: fact.key, text: '本次测试数量为10套。', before: { value: '10', status: 'PROVIDED' }, after: { value: '12', status: 'PROVIDED' } }],
      })
    }
    if (path === '/projects/impact-project/changes/commit' && request.method() === 'POST') {
      commitChanges += 1
      fact.value = '12'; fact.revision = 2; project.version = 2
      report.facts = [{ ...fact }]; report.reviewed = false
      report.issues = [{ code: 'FACT_CHANGED', severity: 'block', message: '测试数量已改变' }]
      report.fact_impacts = [{ position: 1, text: '本次测试数量为10套。', fact_key: 'test_qty', before: { value: '10', revision: 1 }, after: { value: '12', revision: 2 } }]
      return reply({ project_version: 2 })
    }
    if (path === '/projects/impact-project/reports' && request.method() === 'GET') return reply([
      { id: report.id, title: report.title, version: report.version, updated_at: report.updated_at },
    ])
    if (path === '/projects/impact-project/reports/r1' && request.method() === 'GET') return reply(report)
    if (path === '/projects/impact-project/reports/r1/versions') return reply([{ version: report.version, reviewed: report.reviewed, created_at: report.updated_at }])
    if (path === '/projects/impact-project/writing/reference') return reply({ selected: false, corpus_id: 'cy', corpus_version: 'v2', source_project_id: 'historical' })
    if (path === '/projects/impact-project/reports/r1/preview' && request.method() === 'POST') {
      const content = request.postDataJSON().content as Record<string, unknown>[]
      savedToken = ((content[0].children as Record<string, unknown>[]).find((child) => child.type === 'fact_ref')?.display as string)
      return reply({ base_version: report.version, preview_token: 'save-token', fact_keys_affected: ['test_qty'],
        changes: [{ position: 1, before: '本次测试数量为10套。', after: '本次测试数量为12套。', fact_keys: ['test_qty'] }],
      })
    }
    if (path === '/projects/impact-project/reports/r1' && request.method() === 'PUT') {
      report.content = request.postDataJSON().content
      report.version += 1; report.fact_impacts = []; report.issues = [{ code: 'UNREVIEWED', severity: 'block', message: '正文保存后尚未人工核对' }]
      return reply({ report, changes: [] })
    }
    if (path === '/projects/impact-project/reports/r1/review' && request.method() === 'POST') {
      reviewCalls += 1; report.reviewed = true; report.issues = []
      return reply(report)
    }
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })

  await page.goto('/')
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目事实' }).click()
  await page.getByRole('row', { name: /测试数量/ }).getByRole('button', { name: '编辑' }).click()
  await page.getByLabel('值', { exact: true }).fill('12')
  await page.getByRole('button', { name: /预览影响/ }).click()
  await expect(page.getByText('1 处报告引用需更新')).toBeVisible()
  expect(previewChanges).toBe(1)
  expect(commitChanges).toBe(0)
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('row', { name: /测试数量/ })).toContainText('12')
  expect(commitChanges).toBe(1)

  await page.getByRole('button', { name: '报告写作' }).click()
  await expect(page.locator('.plate-content')).toContainText(/本次测试数量为10.*套。/)
  await expect(page.locator('.report-check-summary')).toContainText('正式导出阻断 1')
  await expect(page.locator('a.primary-button').filter({ hasText: '正式导出' })).toHaveClass(/disabled-link/)
  await page.getByRole('button', { name: '更新文内引用' }).click()
  await expect(page.locator('.plate-content')).toContainText(/本次测试数量为12.*套。/)
  await expect(page.locator('.plate-content')).not.toContainText('测试数量 12套套')
  await page.getByRole('button', { name: '预览改动' }).click()
  expect(savedToken).toBe('12')
  await page.getByRole('button', { name: '确认保存' }).click()
  await page.locator('.report-issues > summary').click()
  await expect(page.getByText('正文保存后尚未人工核对')).toBeVisible()
  await expect(page.getByRole('button', { name: '我已核对' })).toBeEnabled()
  await expect(page.locator('a.primary-button').filter({ hasText: '正式导出' })).toHaveClass(/disabled-link/)
  await page.getByRole('button', { name: '我已核对' }).click()
  await expect(page.getByRole('link', { name: '正式导出' })).not.toHaveClass(/disabled-link/)
  expect(reviewCalls).toBe(1)
  await page.locator('.plate-content').getByText('本次测试数量为').click()
  await page.getByRole('button', { name: '事实', exact: true }).click()
  await page.getByRole('menu', { name: '插入事实引用' }).getByRole('menuitem', { name: '测试数量 · 12套' }).click()
  await expect(page.locator('.plate-content .report-fact-token')).toHaveCount(2)
  await expect(page.getByText('有未保存修改')).toBeVisible()
})

test('a current visible token can rebind an old fact snapshot without a text edit', async ({ page }) => {
  const project = { id: 'rebind-project', name: '引用核对验收', version: 2, has_corpus: false, is_builtin: false }
  const fact = { id: 'f1', key: 'test_qty', label: '测试数量', data_type: 'integer', value: '12', status: 'PROVIDED',
    unit: '套', caliber: '本次测试', as_of: '2026-09-24', source: '本项目原件', revision: 2, updated_at: null }
  const report = { id: 'r1', project_id: project.id, title: '数量验证稿', version: 2,
    content: [{ type: 'p', id: 'qty-paragraph', fact_keys: ['test_qty'], children: [
      { text: '本次测试数量为' }, { type: 'fact_ref', fact_key: 'test_qty', display: '12', children: [{ text: '' }] }, { text: '套。' },
    ] }], reviewed: false,
    issues: [{ code: 'FACT_CHANGED', severity: 'block', message: '旧绑定仍指向 r1' }],
    fact_impacts: [{ position: 1, text: '本次测试数量为12套。', fact_key: 'test_qty', before: { value: '10', revision: 1 }, after: { value: '12', revision: 2 } }],
    facts: [fact], updated_at: '2026-09-24T00:00:00Z',
  }
  let previewCalls = 0
  let saveCalls = 0
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/rebind-project/facts') return reply({ project_version: 2, facts: [fact] })
    if (path === '/projects/rebind-project/reports' && request.method() === 'GET') return reply([
      { id: report.id, title: report.title, version: report.version, updated_at: report.updated_at },
    ])
    if (path === '/projects/rebind-project/reports/r1' && request.method() === 'GET') return reply(report)
    if (path === '/projects/rebind-project/reports/r1/versions') return reply([{ version: report.version, reviewed: report.reviewed, created_at: report.updated_at }])
    if (path === '/projects/rebind-project/writing/reference') return reply({ selected: false, corpus_id: 'cy', corpus_version: 'v2', source_project_id: 'historical' })
    if (path === '/projects/rebind-project/reports/r1/preview' && request.method() === 'POST') {
      previewCalls += 1
      return reply({ base_version: report.version, preview_token: 'rebind-token', changes: [], fact_keys_affected: [] })
    }
    if (path === '/projects/rebind-project/reports/r1' && request.method() === 'PUT') {
      saveCalls += 1
      report.version += 1; report.fact_impacts = []
      report.issues = [{ code: 'UNREVIEWED', severity: 'block', message: '正文保存后尚未人工核对' }]
      return reply({ report, changes: [] })
    }
    if (path === '/projects/rebind-project/reports/r1/review' && request.method() === 'POST') {
      report.reviewed = true; report.issues = []
      return reply(report)
    }
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })
  await page.goto('/')
  await page.getByRole('button', { name: '报告写作' }).click()
  await expect(page.getByText('当前正文已保存')).toBeVisible()
  await expect(page.getByRole('button', { name: '预览引用核对' })).toBeEnabled()
  await page.getByRole('button', { name: '预览引用核对' }).click()
  await expect(page.getByText('正文未变 · 核对当前引用')).toBeVisible()
  await page.getByRole('button', { name: '确认保存' }).click()
  expect(previewCalls).toBe(1)
  expect(saveCalls).toBe(1)
  await expect(page.getByText('过时引用 · 1 处')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '我已核对' })).toBeEnabled()
  await page.getByRole('button', { name: '我已核对' }).click()
  await expect(page.getByRole('link', { name: '正式导出' })).not.toHaveClass(/disabled-link/)
})
