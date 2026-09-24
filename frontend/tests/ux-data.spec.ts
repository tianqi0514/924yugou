import { expect, test } from '@playwright/test'

test('a new fact opens value entry, previews zero without writing, then commits', async ({ page }) => {
  const project = { id: 'ux-project', name: '交互验收', version: 0, has_corpus: false, is_builtin: false }
  const facts: Record<string, unknown>[] = []
  let previews = 0
  let commits = 0
  let ruleCreated = false
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/ux-project/facts' && request.method() === 'GET') return reply({ project_version: project.version, facts })
    if (path === '/projects/ux-project/facts' && request.method() === 'POST') {
      const body = request.postDataJSON()
      const fact = { id: `f${facts.length + 1}`, ...body, value: null, status: 'UNDEFINED', revision: 0, updated_at: null }
      facts.push(fact); project.version += 1
      return reply(fact, 201)
    }
    if (path === '/projects/ux-project/rules' && request.method() === 'GET') return reply({
      rules: ruleCreated ? [{ id: 'rule-1', name: '计划销售量', target_key: 'planned_sales', expression: 'min(first_year_demand, first_year_demand)', deps: ['first_year_demand'] }] : [],
      trace: ruleCreated ? [{ rule_id: 'rule-1', status: 'COMPUTED', result: '0', missing: [] }] : [],
    })
    if (path === '/projects/ux-project/rules' && request.method() === 'POST') {
      ruleCreated = true
      return reply({ id: 'rule-1' }, 201)
    }
    if (path === '/projects/ux-project/changes/preview' && request.method() === 'POST') {
      previews += 1
      expect(request.postDataJSON().value).toBe('0')
      return reply({ base_version: project.version, preview_token: 'preview-0', expires_at: 9999999999,
        changes: [{ key: 'first_year_demand', label: '首年需求', before: { value: null, status: 'UNDEFINED' },
          after: { value: '0', status: 'PROVIDED' } }], trace: [], report_impacts: [] })
    }
    if (path === '/projects/ux-project/changes/commit' && request.method() === 'POST') {
      commits += 1
      expect(request.postDataJSON().preview_token).toBe('preview-0')
      Object.assign(facts[0], { value: '0', status: 'PROVIDED', revision: 1 })
      project.version += 1
      return reply({ project_version: project.version })
    }
    return reply({ detail: `未模拟：${request.method()} ${path}` }, 501)
  })
  await page.goto('/')
  await page.getByRole('button', { name: '新增事实' }).click()
  await page.getByRole('textbox', { name: '名称', exact: true }).fill('首年需求')
  await page.getByRole('textbox', { name: '字段 key' }).fill('first_year_demand')
  await page.getByRole('combobox', { name: '类型' }).selectOption('integer')
  await page.getByRole('textbox', { name: '单位' }).fill('套')
  await page.getByRole('button', { name: '创建并录入值' }).click()
  await expect(page.getByRole('heading', { name: '录入 · 首年需求' })).toBeVisible()
  await page.getByRole('textbox', { name: '值', exact: true }).fill('0')
  await page.getByRole('textbox', { name: '来源' }).fill('本项目需求说明')
  await page.getByRole('button', { name: '预览影响' }).click()
  await expect(page.locator('.diff-row')).toContainText('0')
  expect(previews).toBe(1)
  expect(commits).toBe(0)
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByRole('row', { name: /首年需求/ })).toContainText('未定义')
  await page.getByRole('row', { name: /首年需求/ }).getByRole('button', { name: '编辑' }).click()
  await page.getByRole('textbox', { name: '值', exact: true }).fill('0')
  await page.getByRole('textbox', { name: '来源' }).fill('本项目需求说明')
  await page.getByRole('button', { name: '预览影响' }).click()
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('row', { name: /首年需求/ })).toContainText('0 套')
  expect(commits).toBe(1)
  const previewsBeforeResultFact = previews
  await page.getByRole('button', { name: '新增事实' }).click()
  await page.getByRole('textbox', { name: '名称', exact: true }).fill('计划销售量')
  await page.getByRole('textbox', { name: '字段 key' }).fill('planned_sales')
  await page.getByRole('textbox', { name: '单位' }).fill('套')
  await page.getByRole('button', { name: '仅创建' }).click()
  await expect(page.getByRole('row', { name: /计划销售量/ })).toContainText('未定义')
  await expect(page.getByRole('heading', { name: '录入 · 计划销售量' })).toHaveCount(0)
  expect(previews).toBe(previewsBeforeResultFact)
  expect(commits).toBe(1)
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '规则计算' }).click()
  await page.getByRole('button', { name: '新增规则' }).click()
  await page.getByRole('textbox', { name: '名称', exact: true }).fill('计划销售量')
  await page.getByRole('combobox', { name: '结果事实' }).selectOption('planned_sales')
  await page.getByRole('textbox', { name: '表达式' }).fill('min(first_year_demand, first_year_demand)')
  await page.getByRole('button', { name: '保存规则' }).click()
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '项目事实' }).click()
  const resultRow = page.getByRole('row', { name: /计划销售量/ })
  await expect(resultRow.getByRole('button', { name: '编辑' })).toHaveCount(0)
  await resultRow.getByRole('button', { name: '规则' }).click()
  await expect(page.getByRole('heading', { name: '规则计算' })).toBeVisible()
})

test('a document page is selected once for extraction and its candidate enters the fact ledger only after review', async ({ page }) => {
  const project = { id: 'ux-project', name: '交互验收', version: 0, has_corpus: false, is_builtin: false }
  const document = { id: 'd1', filename: '本项目需求.pdf', file_kind: 'pdf', sha256: 'abc', pages: 2, segments: 2, status: 'READY', created_at: '' }
  let extracted = false
  let approvals = 0
  const candidate = { id: 'c1', label: '首年需求', value: '300', unit: '套', data_type: 'integer', source_ref: 'p2-s1',
    excerpt: '首年需求 300 套。', source_valid: true, review_status: 'PENDING', fact_key: null as string | null,
    extraction_run_id: 'run1', extraction_origin: 'TABLE', extraction_model: null, extraction_model_version: null }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/ux-project/facts') return reply({ project_version: 0, facts: [] })
    if (path === '/projects/ux-project/rules') return reply({ rules: [], trace: [] })
    if (path === '/projects/ux-project/documents') return reply([document])
    if (path === '/model/status') return reply({ configured: true, ocr_configured: false })
    if (path === '/projects/ux-project/documents/d1/pages') return reply({ pages: [
      { page: 1, segment_count: 1, char_count: 6, candidate_count: 0, pending_count: 0, invalid_count: 0, extractable: true, reason: '' },
      { page: 2, segment_count: 1, char_count: 12, candidate_count: extracted ? 1 : 0, pending_count: extracted && !candidate.fact_key ? 1 : 0, invalid_count: 0, extractable: true, reason: '' },
    ] })
    if (path === '/projects/ux-project/documents/d1' && request.method() === 'GET') {
      const second = url.searchParams.get('page') === '2'
      return reply({ document, page: second ? 2 : 1,
        segments: [{ ref: second ? 'p2-s1' : 'p1-s1', page: second ? 2 : 1, text: second ? '首年需求 300 套。' : '项目说明。' }],
        candidates: second && extracted ? [candidate] : [] })
    }
    if (path === '/projects/ux-project/documents/d1/extract' && request.method() === 'POST') {
      expect(url.searchParams.get('page')).toBe('2')
      extracted = true
      return reply({ created: 1, refreshed: 0 })
    }
    if (path === '/projects/ux-project/documents/d1/candidates/c1/approve' && request.method() === 'POST') {
      approvals += 1
      expect(request.postDataJSON().source_ref).toBe('p2-s1')
      candidate.review_status = 'APPROVED'; candidate.fact_key = 'first_year_demand'
      return reply({ candidate })
    }
    return reply({ detail: `未模拟：${request.method()} ${path}` }, 501)
  })
  await page.goto('/')
  await page.getByRole('button', { name: '项目文件' }).click()
  await expect(page.getByRole('heading', { name: '本项目需求.pdf' })).toBeVisible()
  await page.getByRole('button', { name: '下一页' }).click()
  await expect(page.getByRole('textbox', { name: '待抽取页码' })).toHaveValue('2')
  await page.getByRole('button', { name: '抽取', exact: true }).click()
  await expect(page.getByText('首年需求', { exact: true })).toBeVisible()
  expect(approvals).toBe(0)
  await page.getByRole('button', { name: '核对' }).click()
  await expect(page.locator('.candidate-excerpt').first()).toContainText('首年需求 300 套。')
  await page.getByRole('button', { name: '确认入台账' }).click()
  await expect(page.getByText('已入事实台账')).toBeVisible()
  expect(approvals).toBe(1)
})
