import { expect, test } from '@playwright/test'

test('a computed token opens its project rule and reaches reviewed upstream document evidence', async ({ page }) => {
  const project = { id: 'source-project', name: '事实来源回链验收', version: 2, has_corpus: false, is_builtin: false }
  const demand = { id: 'f1', key: 'first_year_demand', label: '首年需求', data_type: 'integer', value: '300000',
    status: 'PROVIDED', unit: '套', caliber: '首年', as_of: '2026-09-24', source: '人工录入', revision: 2, updated_at: null }
  const planned = { id: 'f2', key: 'planned_sales', label: '计划销售量', data_type: 'integer', value: '254016',
    status: 'COMPUTED', unit: '套', caliber: '首年', as_of: '2026-09-24', source: '', revision: 2, updated_at: null }
  const report = { id: 'r1', project_id: project.id, title: '来源验证稿', version: 1,
    content: [{ type: 'p', id: 'sales', fact_keys: ['planned_sales'], children: [
      { text: '计划销售量 ' }, { type: 'fact_ref', fact_key: 'planned_sales', display: '254016', children: [{ text: '' }] }, { text: ' 套。' },
    ] }], reviewed: false, issues: [], fact_impacts: [], facts: [planned], updated_at: '2026-09-24T00:00:00Z',
  }
  let ruleSourceCalls = 0
  let documentSourceCalls = 0
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/source-project/facts') return reply({ project_version: 2, facts: [demand, planned] })
    if (path === '/projects/source-project/reports') return reply([{ id: report.id, title: report.title, version: report.version, updated_at: report.updated_at }])
    if (path === '/projects/source-project/reports/r1') return reply(report)
    if (path === '/projects/source-project/reports/r1/versions') return reply([{ version: 1, reviewed: false, created_at: report.updated_at }])
    if (path === '/projects/source-project/writing/reference') return reply({ selected: false, corpus_id: 'cy', corpus_version: 'v2', source_project_id: 'historical' })
    if (path === '/projects/source-project/facts/planned_sales/source') {
      ruleSourceCalls += 1
      return reply({ kind: 'rule', fact: planned, description: '项目规则计算', review_status: 'DERIVED_FROM_REVIEWED_INPUTS',
        rule: { id: 'r-min', name: '销售量取需求与合格能力较小值', target_key: 'planned_sales',
          expression: 'min(first_year_demand, qualified_capacity)', deps: ['first_year_demand', 'qualified_capacity'] },
        input_facts: [{ key: demand.key, label: demand.label, value: demand.value, unit: demand.unit, revision: demand.revision,
          review_status: 'SOURCE_LOCATOR_REVIEWED', source_url: '/api/projects/source-project/facts/first_year_demand/source' }],
        excerpt: null, original_url: null })
    }
    if (path === '/projects/source-project/facts/first_year_demand/source') {
      documentSourceCalls += 1
      return reply({ kind: 'document', fact: demand, description: '本项目原文证据绑定',
        review_status: 'SOURCE_LOCATOR_REVIEWED', document_id: 'doc1', document_sha256: 'abc123', original_source: '人工录入',
        filename: '本项目需求.pdf', source_ref: 'd-p3', source_refs: ['d-p3'], page: 3,
        locator: '第 3 页第 2 段', excerpt: '【d-p3】首年预测需求 300000 套。',
        original_url: '/api/projects/source-project/documents/doc1/original#page=3' })
    }
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })
  await page.goto('/')
  await page.getByRole('button', { name: '报告写作' }).click()
  await page.locator('.plate-content .report-fact-token [role="button"]').click()
  await expect(page.getByRole('heading', { name: '事实来源 · 计划销售量' })).toBeVisible()
  await expect(page.getByText('依据已核对的输入事实计算')).toBeVisible()
  await expect(page.getByText('min(first_year_demand, qualified_capacity)')).toBeVisible()
  await page.getByRole('button', { name: /首年需求.*查看来源/ }).click()
  await expect(page.getByRole('heading', { name: '事实来源 · 首年需求' })).toBeVisible()
  await expect(page.getByText('本项目原文位置已核对（当前修订）')).toBeVisible()
  await expect(page.getByText('【d-p3】首年预测需求 300000 套。')).toBeVisible()
  await expect(page.getByRole('link', { name: '打开原件位置' })).toHaveAttribute('href', /documents\/doc1\/original#page=3/)
  expect(ruleSourceCalls).toBe(1)
  expect(documentSourceCalls).toBe(1)
})
