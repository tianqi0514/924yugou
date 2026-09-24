import { expect, test } from '@playwright/test'

test('reference, source review, cancel and confirmed candidate reach Plate without a hidden write', async ({ page }) => {
  const project = { id: 'qa-project', name: '写作包浏览器验收', version: 0, has_corpus: false, is_builtin: false }
  const corpusRef = { corpus_id: 'chengyue', corpus_version: 'v2', source_project_id: 'historical-project' }
  const l002 = { corpus_id: 'chengyue', corpus_version: 'v2', category_id: 'CAT-15', artifact_id: 'graph-claims', record_id: '15:000002', semantic_id: 'L002' }
  const report: { id: string; project_id: string; title: string; version: number; content: Record<string, unknown>[];
    reviewed: boolean; issues: Record<string, unknown>[]; fact_impacts: Record<string, unknown>[];
    facts: Record<string, unknown>[]; updated_at: string } = {
    id: 'r1', project_id: project.id, title: '写作验证报告', version: 1,
    content: [{ type: 'p', id: 'intro', children: [{ text: '' }] }], reviewed: false,
    issues: [], fact_impacts: [], facts: [], updated_at: '2026-09-24T00:00:00Z',
  }
  let referenceSelected = false
  let previewCalls = 0
  let commitCalls = 0
  let approvedItems: string[] = []
  let mappedDemand = false
  let savedContent: Record<string, unknown>[] | null = null
  const items = [
    { item_id: 'outline', category_id: 'CAT-04', category_number: 4, artifact_id: 'outline', record_id: '04:000001', semantic_id: 'S4', role: 'outline', decision: 'selectable', reason: '章节结构', location: { section: 'S4', page: 5 }, summary: '章节层级与顺序' },
    { item_id: 'claim', category_id: 'CAT-15', category_number: 15, artifact_id: 'graph-claims', record_id: '15:000002', semantic_id: 'L002', role: 'claim', decision: 'selectable', reason: '须核对项目事实', location: { chunk_id: 'C0027', page: 4 }, summary: '需求不是订单' },
    { item_id: 'evidence', category_id: 'CAT-18', category_number: 18, artifact_id: 'evidence', record_id: '18:000001', semantic_id: 'EV-01', role: 'historical_evidence', decision: 'review_only', reason: '未取得完整原件', location: { page: 4 }, summary: '历史证据目录' },
    { item_id: 'unsafe', category_id: 'CAT-10', category_number: 10, artifact_id: 'skeleton', record_id: '10:000001', semantic_id: 'SK1', role: 'skeleton', decision: 'excluded', reason: '历史条件不适用', location: null, summary: '旧条件句' },
  ]
  const conflictItems = [
    { item_id: 's52-outline', category_id: 'CAT-04', category_number: 4, artifact_id: 'outline', record_id: '04:000052', semantic_id: 'S5.2', role: 'outline', decision: 'selectable', reason: '仅形成预审披露', location: { section: 'S5.2' }, summary: '配电条件章节结构' },
    { item_id: 'x001', category_id: 'CAT-23', category_number: 23, artifact_id: 'conflicts', record_id: '23:000001', semantic_id: 'X001', role: 'conflict', decision: 'review_only', reason: '两个历史来源未解决', location: { page: 7 }, summary: '800 kW 与 650 kW 冲突' },
  ]
  const candidate = {
    section_id: 'S4', paragraphs: [
      { type: 'h2', id: 'heading-s4', section_id: 'S4', source_refs: [l002], children: [{ text: '需求与产能' }] },
      { type: 'p', id: 'paragraph-s4', section_id: 'S4', origin: 'guided', source_refs: [l002],
        project_rule_refs: [{ rule_id: 'rule-1', expression: 'min(demand, capacity)', target_key: 'sales', deps: ['demand', 'capacity'],
          input_fact_revisions: [{ fact_key: 'demand', revision: 1, value: '300000' }, { fact_key: 'capacity', revision: 1, value: '254016' }], target_fact_revision: 1 }],
        children: [{ text: '客户预测需求须与订单区分。' }] },
    ], issues: [{ code: 'REVIEW', severity: 'review', message: '历史证据原件仍需本项目核对', item_ids: ['evidence'] }],
    used_items: ['outline', 'claim'], rejected_items: [{ item_id: 'unsafe', reason: '历史条件不适用' }],
    base_version: 1, project_version: 0, preview_token: 'signed-preview', expires_at: 9999999999,
  }

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects' && request.method() === 'GET') return reply([project])
    if (path === '/projects/qa-project/facts' && request.method() === 'GET') return reply({ facts: [] })
    if (path === '/projects/qa-project/reports' && request.method() === 'GET') return reply([{ id: 'r1', title: report.title, version: report.version, updated_at: report.updated_at }])
    if (path === '/projects/qa-project/reports/r1' && request.method() === 'GET') return reply(report)
    if (path === '/projects/qa-project/reports/r1/versions') return reply(Array.from({ length: report.version }, (_, index) => ({ version: index + 1, reviewed: false, created_at: '2026-09-24T00:00:00Z' })))
    if (path === '/projects/qa-project/reports/r1/compare') return reply({ base_version: 1, current_version: report.version,
      changes: [{ position: 3, before: '', after: '客户预测需求须与订单区分。', fact_keys: [] }] })
    if (path === '/projects/qa-project/writing/reference' && request.method() === 'GET') return reply({ ...corpusRef, selected: referenceSelected, target_report_type: referenceSelected ? 'feasibility' : null, compatible: referenceSelected })
    if (path === '/projects/qa-project/writing/reference' && request.method() === 'PUT') {
      const body = request.postDataJSON()
      if (body.target_report_type !== 'feasibility') return reply({ detail: '该参考语料仅适用于项目可行性报告' }, 400)
      referenceSelected = true
      return reply({ ...corpusRef, selected: true, target_report_type: 'feasibility', compatible: true })
    }
    if (path === '/projects/qa-project/writing' && request.method() === 'GET') return reply({
      slots: [{ id: 'N017', label: '首年客户需求', data_type: 'integer', unit: '套' }],
      bindings: mappedDemand ? { N017: 'first_year_demand' } : {},
      facts: [{ key: 'first_year_demand', label: '本项目需求', data_type: 'integer', unit: '套', value: '300000' }],
    })
    if (path === '/projects/qa-project/writing/bindings' && request.method() === 'PUT') {
      mappedDemand = request.postDataJSON().bindings.N017 === 'first_year_demand'
      return reply({ bindings: mappedDemand ? { N017: 'first_year_demand' } : {} })
    }
    if (path === '/projects/qa-project/writing/sections') return reply({ ...corpusRef, sections: [
      { id: 'S4', title: '需求与产能', level: 1, reusable: true, chunk_count: 5, guided_available: true, model_available: true },
      { id: 'S5.2', title: '配电条件', level: 2, reusable: true, chunk_count: 2, guided_available: true, model_available: false },
      { id: 'S8', title: '其他章节', level: 1, reusable: false, chunk_count: 1, guided_available: false, model_available: false, availability_reason: '尚未验证起草条件' },
    ] })
    if (path === '/projects/qa-project/writing/packages/S4') return reply({ section: { id: 'S4', title: '需求与产能', level: 1 }, project_id: project.id, project_version: 0, ...corpusRef, items, required_item_ids: ['claim'], slots: [{ slot_id: 'N017', label: '首年客户需求', unit: '套', fact_key: mappedDemand ? 'first_year_demand' : null, value: mappedDemand ? '300000' : null, status: mappedDemand ? 'PROVIDED' : 'UNDEFINED' }], issues: [], facts: [] })
    if (path === '/projects/qa-project/writing/packages/S5.2') return reply({ section: { id: 'S5.2', title: '配电条件', level: 2 }, project_id: project.id, project_version: 0, ...corpusRef, items: conflictItems, required_item_ids: ['s52-outline'], slots: [], issues: [{ code: 'POWER_CONFLICT_UNRESOLVED', severity: 'block', message: '配电余量冲突尚未解决', item_ids: ['x001'] }], facts: [] })
    if (path === '/projects/qa-project/writing/sources/15/15%3A000002' || path === '/projects/qa-project/writing/sources/15/L002') return reply({ source_ref: l002, location: { chunk_id: 'C0027', page: 4 }, summary: '需求不是订单', payload: { id: 'L002', label: '需求不是订单', supports: ['N012'], must_cite: ['EV-01'], source_location: { chunk_id: 'C0027', page: 4 } } })
    if (path === '/projects/qa-project/writing/sources/12/N012') return reply({ source_ref: { ...l002, category_id: 'CAT-12', artifact_id: 'graph-nodes', record_id: '12:000012', semantic_id: 'N012' }, location: { page: 4 }, summary: '需求预测节点', payload: { id: 'N012', label: '需求预测' } })
    if (path === '/projects/qa-project/writing/sources/18/EV-01' || path === '/projects/qa-project/writing/sources/18/18%3A000001') return reply({ source_ref: { ...l002, category_id: 'CAT-18', artifact_id: 'evidence', record_id: '18:000001', semantic_id: 'EV-01' }, location: null, summary: '销售需求预测备忘录', payload: { id: 'EV-01', original_document_available: false, excerpt_ids: ['EX-01-01'], excerpt_locations: [{ chunk_id: 'C0161', page: 17 }], limitations: ['只有目录和摘录，未获得完整原始文件'] } })
    if (path === '/projects/qa-project/writing/sources/19/EX-01-01') return reply({ source_ref: { ...l002, category_id: 'CAT-19', artifact_id: 'excerpts', record_id: '19:000000', semantic_id: null }, location: null, summary: '需求备忘录摘录', payload: { file: 'evidence/excerpts/EX-01-01.md', link: { id: 'EX-01-01', page: 17, source_locator: { part: 'word/document.xml', xpath: '/w:document/w:body/w:p[177]' } }, text: '历史摘录' } })
    if (path === '/projects/qa-project/writing/sources/9/C0161') return reply({ source_ref: { ...l002, category_id: 'CAT-09', artifact_id: 'chunks', record_id: '09:000160', semantic_id: 'C0161' }, location: { part: 'word/document.xml', xpath: '/w:document/w:body/w:p[177]' }, summary: '原文切块 C0161', payload: { id: 'C0161', page: 17, text: '预测需求不是订单', source_locator: { part: 'word/document.xml', xpath: '/w:document/w:body/w:p[177]' } } })
    if (path === '/projects/qa-project/writing/sources/23/23%3A000001') return reply({ source_ref: { ...l002, category_id: 'CAT-23', artifact_id: 'conflicts', record_id: '23:000001', semantic_id: 'X001' }, location: { page: 7 }, summary: '同口径配电余量冲突', payload: { id: 'X001', nodes: ['N050', 'N051'], evidence: ['EV-06A', 'EV-06B'], source_locations: [{ chunk_id: 'C0063', page: 7 }] } })
    if (path === '/projects/qa-project/writing/sources/18/EV-06A') return reply({ source_ref: { ...l002, category_id: 'CAT-18', artifact_id: 'evidence', record_id: '18:000006', semantic_id: 'EV-06A' }, location: { page: 7 }, summary: '800 kW 来源', payload: { id: 'EV-06A', label: '配电资料 A' } })
    if (path === '/projects/qa-project/writing/impact') return reply({ record_id: new URL(request.url()).searchParams.get('record_id'), impacts: commitCalls ? [{ report_id: 'r1', report_title: report.title, report_version: report.version, block_id: 'paragraph-s4', section_id: 'S4', position: 3, text: '客户预测需求须与订单区分。' }] : [] })
    if (path === '/projects/qa-project/reports/r1/writing/preview' && request.method() === 'POST') {
      previewCalls += 1
      approvedItems = request.postDataJSON().approved_item_ids
      if (request.postDataJSON().section_id === 'S5.2') return reply({ ...candidate, section_id: 'S5.2',
        issues: [{ code: 'POWER_CONFLICT_UNRESOLVED', severity: 'block', message: '配电余量冲突尚未解决', item_ids: ['x001'] }],
        paragraphs: [{ type: 'p', id: 'preview-conflict', section_id: 'S5.2', origin: 'guided', source_refs: [], children: [{ text: '仅作预审披露，不作适配结论。' }] }],
      })
      return reply(candidate)
    }
    if (path === '/projects/qa-project/reports/r1/writing/commit' && request.method() === 'POST') {
      commitCalls += 1
      report.version += 1
      report.content = [...report.content, ...candidate.paragraphs]
      report.issues = [{ code: 'REVIEW', severity: 'review', message: '历史证据待核对' }]
      return reply(report)
    }
    if (path === '/projects/qa-project/reports/r1/preview' && request.method() === 'POST') return reply({
      base_version: report.version, preview_token: 'save-token', fact_keys_affected: [],
      changes: [{ position: 3, before: '客户预测需求须与订单区分。', after: '客户预测需求须与订单区分。补充', fact_keys: [] }],
    })
    if (path === '/projects/qa-project/reports/r1' && request.method() === 'PUT') {
      savedContent = request.postDataJSON().content
      report.content = savedContent || report.content
      report.version += 1
      return reply({ report, changes: [] })
    }
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })

  await page.goto('/')
  await page.getByRole('button', { name: '报告写作' }).click()
  await expect(page.getByRole('heading', { name: '章节起草' })).toBeVisible()
  await page.getByLabel('本项目报告类型').selectOption('accident_investigation')
  await page.getByRole('button', { name: '选择为只读参考' }).click()
  await expect(page.getByText('该参考语料仅适用于项目可行性报告')).toBeVisible()
  await page.getByLabel('本项目报告类型').selectOption('feasibility')
  await page.getByRole('button', { name: '选择为只读参考' }).click()
  await page.getByLabel('选择语料章节').selectOption('S4')
  await expect(page.getByText('未映射')).toBeVisible()
  await page.getByRole('button', { name: '映射事实' }).click()
  await page.getByLabel('映射 首年客户需求').selectOption('first_year_demand')
  await page.getByRole('button', { name: '保存映射' }).click()
  await expect(page.locator('.corpus-writing-slot')).toContainText('300000套')
  expect(mappedDemand).toBe(true)
  await expect(page.getByText('需求不是订单', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '预览章节候选' })).toBeDisabled()
  await expect(page.locator('.corpus-writing-item input:checked')).toHaveCount(0)
  await expect(page.getByText('必选：L002')).toBeVisible()

  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('button', { name: '来源' }).click()
  await expect(page.getByText('支撑节点')).toBeVisible()
  await expect(page.getByRole('button', { name: /N012/ })).toBeVisible()
  await page.getByRole('button', { name: /N012/ }).click()
  await expect(page.getByText('需求预测节点')).toBeVisible()
  await page.getByRole('button', { name: '关闭来源' }).click()
  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('button', { name: '来源' }).click()
  await page.getByRole('button', { name: /EV-01/ }).click()
  await expect(page.getByText('完整原件缺失')).toBeVisible()
  await expect(page.getByText('C0161 · 第 17 页', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /C0161/ }).click()
  await expect(page.getByText('原文切块 C0161')).toBeVisible()
  await expect(page.getByText(/Word 原文：word\/document.xml/)).toBeVisible()
  await page.getByRole('button', { name: '关闭来源' }).click()
  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('button', { name: '来源' }).click()
  await page.getByRole('button', { name: /EV-01/ }).click()
  await page.getByRole('button', { name: /EX-01-01/ }).click()
  await expect(page.getByText('需求备忘录摘录')).toBeVisible()
  await expect(page.locator('.corpus-source-text')).toContainText('历史摘录')
  await expect(page.getByText('第 17 页', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭来源' }).click()

  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('checkbox').check()
  await expect(page.getByRole('button', { name: '预览章节候选' })).toBeEnabled()
  await page.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.getByText('客户预测需求须与订单区分。')).toBeVisible()
  await page.getByRole('button', { name: '取消候选' }).click()
  expect(previewCalls).toBe(1)
  expect(commitCalls).toBe(0)
  expect(approvedItems).toEqual(['claim'])

  await page.getByRole('button', { name: '预览章节候选' }).click()
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('.plate-content')).toContainText('客户预测需求须与订单区分。')
  expect(commitCalls).toBe(1)
  await page.getByRole('navigation', { name: '写作步骤' }).getByRole('button', { name: '核对导出' }).click()
  await expect(page.locator('.report-check-summary')).toContainText('保留 1')
  await expect(page.getByText('历史证据待核对')).toBeHidden()
  await page.locator('.report-issues > summary').click()
  await expect(page.getByText('历史证据待核对')).toBeVisible()
  await page.getByRole('navigation', { name: '写作步骤' }).getByRole('button', { name: '编辑正文' }).click()
  await page.locator('.plate-content').getByText('客户预测需求须与订单区分。').click()
  await expect(page.locator('.report-corpus-refs')).toContainText('历史参考 · 待核对')
  await page.locator('.report-corpus-refs').getByRole('button', { name: /L002/ }).click()
  await expect(page.getByText('已引用于')).toBeVisible()
  await expect(page.getByRole('button', { name: /写作验证报告.*第 3 段/ })).toBeVisible()
  await page.getByRole('button', { name: '关闭语料来源' }).click()
  await expect(page.locator('a.primary-button').filter({ hasText: '正式导出' })).toHaveClass(/disabled-link/)
  await expect(page.locator('.report-check-summary')).toContainText('待人工核对')
  await expect(page.getByRole('link', { name: '导出预审稿' })).toHaveAttribute('href', /level=preview/)

  const firstParagraphText = page.locator('.plate-content [data-block-id="paragraph-s4"] [data-slate-string]').last()
  await firstParagraphText.click()
  await firstParagraphText.click()
  await expect.poll(() => page.evaluate(() => window.getSelection()?.anchorNode?.parentElement?.closest('[data-block-id]')?.getAttribute('data-block-id'))).toBe('paragraph-s4')
  await page.keyboard.press('Control+End')
  await page.keyboard.type('补充')
  await expect(page.getByText('有未保存修改')).toBeVisible()
  await page.getByRole('button', { name: '预览改动' }).click()
  await page.getByRole('button', { name: '确认保存' }).click()
  await expect(page.locator('.plate-content')).toContainText('补充')
  const savedParagraph = savedContent?.find((block) => block.id === 'paragraph-s4')
  expect(JSON.stringify(savedParagraph?.children)).toContain('补充')
  expect(savedParagraph?.source_refs).toEqual([l002])
  expect(savedParagraph?.section_id).toBe('S4')
  expect((savedParagraph?.project_rule_refs as { rule_id: string }[])?.[0].rule_id).toBe('rule-1')
  await page.reload()
  await page.getByRole('button', { name: '报告写作' }).click()
  await expect(page.locator('.plate-content')).toContainText('补充')
  const paragraphText = page.locator('.plate-content [data-block-id="paragraph-s4"] [data-slate-string]').last()
  await paragraphText.click()
  await paragraphText.click()
  await expect.poll(() => page.evaluate(() => window.getSelection()?.anchorNode?.parentElement?.closest('[data-block-id]')?.getAttribute('data-block-id'))).toBe('paragraph-s4')
  await page.keyboard.press('Control+End')
  await page.keyboard.press('Enter')
  await page.keyboard.type('/')
  await expect(page.getByRole('listbox', { name: '插入内容' })).toBeVisible()
  await page.getByRole('option', { name: /插入 2×2 表格/ }).click()
  await expect(page.locator('.plate-content table.report-table')).toBeVisible()
  await page.locator('.plate-content table.report-table th, .plate-content table.report-table td').first().click()
  await page.keyboard.type('测试表格单元格')
  await page.getByRole('button', { name: '预览改动' }).click()
  await page.getByRole('button', { name: '确认保存' }).click()
  const newTable = savedContent?.find((block) => block.type === 'table')
  expect(newTable?.source_refs).toBeUndefined()
  expect(newTable?.section_id).toBe('S4')
  expect(savedContent?.filter((block) => block.type === 'p' && JSON.stringify(block.children) === '[{"text":""}]')
    .every((block) => !block.fact_keys && !block.source_refs && !block.project_rule_refs)).toBe(true)
  await page.reload()
  await page.getByRole('button', { name: '报告写作' }).click()
  await expect(page.locator('.plate-content table.report-table')).toContainText('测试表格单元格')
  await page.locator('.report-history > summary').click()
  await page.getByLabel('比较历史版本').selectOption('1')
  await page.getByRole('button', { name: '与当前版本比较' }).click()
  await expect(page.locator('.report-history')).toContainText(`v1 → v${report.version}`)
  await page.getByLabel('选择语料章节').selectOption('S5.2')
  await page.getByRole('button', { name: '仅核对 1' }).click()
  await page.locator('.corpus-writing-item').filter({ hasText: 'X001' }).getByRole('button', { name: '来源' }).click()
  await expect(page.getByText('冲突节点')).toBeVisible()
  await expect(page.getByRole('button', { name: /N050/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /N051/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /EV-06A/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /EV-06B/ })).toBeVisible()
  await page.getByRole('button', { name: /EV-06A/ }).click()
  await expect(page.getByText('800 kW 来源')).toBeVisible()
  await page.getByRole('button', { name: '关闭来源' }).click()
  await page.getByRole('button', { name: '可选用 1' }).click()
  await page.locator('.corpus-writing-item').filter({ hasText: 'S5.2' }).getByRole('checkbox').check()
  await page.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.getByText('冲突未解决，仅可作为预审稿')).toBeVisible()
  await expect(page.getByRole('button', { name: '加入报告' })).toBeEnabled()
  await page.setViewportSize({ width: 900, height: 760 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
})

test('a project fact binds an actual document segment only after explicit review', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 760 })
  const project = { id: 'evidence-project', name: '证据绑定验收', version: 1, has_corpus: false, is_builtin: false }
  const fact = { id: 'f1', key: 'first_year_demand', label: '首年需求', data_type: 'integer', value: '300000',
    status: 'PROVIDED', unit: '套', caliber: '首年', as_of: '2026-09-24', source: '人工录入', revision: 2, updated_at: null }
  let bindCalls = 0
  let received: { document_id: string; source_refs: string[] } | null = null
  let bound = false
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/evidence-project/facts') return reply({ project_version: 1, facts: [fact] })
    if (path === '/projects/evidence-project/rules') return reply({ rules: [], trace: [] })
    if (path === '/projects/evidence-project/facts/first_year_demand/evidence' && request.method() === 'GET') return reply({
      fact_key: fact.key, fact_revision: fact.revision, status: bound ? 'SOURCE_LOCATOR_REVIEWED' : 'UNVERIFIED',
      document_id: bound ? 'doc1' : null, source_refs: bound ? ['p1-s1'] : [],
    })
    if (path === '/projects/evidence-project/documents' && request.method() === 'GET') return reply([
      { id: 'doc1', filename: '本项目客户需求.pdf', pages: 1 },
    ])
    if (path === '/projects/evidence-project/documents/doc1') return reply({ segments: [
      { ref: 'p1-s1', page: 1, text: '本项目首年预测需求 300000 套，尚非已签订单。', locator: '第 1 页第 1 段' },
      { ref: 'p1-s2', page: 1, text: '其他说明。', locator: '第 1 页第 2 段' },
    ] })
    if (path === '/projects/evidence-project/facts/first_year_demand/evidence/bind' && request.method() === 'POST') {
      bindCalls += 1; received = request.postDataJSON(); bound = true
      return reply({ fact_key: fact.key, fact_revision: fact.revision, status: 'SOURCE_LOCATOR_REVIEWED',
        document_id: 'doc1', source_refs: ['p1-s1'], project_version: 1 })
    }
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })
  await page.goto('/')
  await page.getByRole('row', { name: /首年需求/ }).getByRole('button', { name: '证据' }).click()
  await expect(page.getByText('来源待核对')).toBeVisible()
  expect(bindCalls).toBe(0)
  await page.getByRole('checkbox', { name: /p1-s1/ }).check()
  await page.getByRole('button', { name: '核对片段并绑定' }).click()
  await expect(page.getByText('原文位置已核对')).toBeVisible()
  expect(bindCalls).toBe(1)
  expect(received).toEqual({ document_id: 'doc1', source_refs: ['p1-s1'] })
})
