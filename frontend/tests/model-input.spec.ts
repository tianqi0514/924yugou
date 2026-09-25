import { expect, test } from '@playwright/test'

test('model basis is reviewed, stale hash is rejected, and candidate stays cancelable until commit', async ({ page }) => {
  test.setTimeout(90_000)
  const project = { id: 'model-project', name: '模型依据验收', version: 1, has_corpus: false, is_builtin: false }
  const corpus = { corpus_id: 'cy_tray_20260918', corpus_version: 'v2', source_project_id: 'historical-project' }
  const claimRef = { corpus_id: corpus.corpus_id, corpus_version: 'v2', category_id: 'CAT-15', artifact_id: 'claims', record_id: '15:000002', semantic_id: 'L002' }
  const outlineRef = { ...claimRef, category_id: 'CAT-04', artifact_id: 'outline', record_id: '04:000004', semantic_id: 'S4' }
  const items = [
    { item_id: '04:000004', category_id: 'CAT-04', category_number: 4, artifact_id: 'outline', record_id: '04:000004', semantic_id: 'S4', role: 'outline', decision: 'selectable', reason: '', location: { section: 'S4' }, summary: '需求与产能章节' },
    { item_id: '15:000002', category_id: 'CAT-15', category_number: 15, artifact_id: 'claims', record_id: '15:000002', semantic_id: 'L002', role: 'claim', decision: 'selectable', reason: '', location: { page: 4 }, summary: '预测需求不是订单' },
    { item_id: '10:000001', category_id: 'CAT-10', category_number: 10, artifact_id: 'skeleton', record_id: '10:000001', semantic_id: 'SK1', role: 'skeleton', decision: 'excluded', reason: '历史条件不适用', location: null, summary: '历史条件句' },
  ]
  const report: { id: string; project_id: string; title: string; version: number; content: Record<string, unknown>[];
    reviewed: boolean; issues: Record<string, unknown>[]; fact_impacts: Record<string, unknown>[];
    facts: Record<string, unknown>[]; updated_at: string } = {
    id: 'r1', project_id: project.id, title: '模型写作试验', version: 1,
    content: [{ type: 'p', id: 'intro', children: [{ text: '' }] }], reviewed: false,
    issues: [], fact_impacts: [], facts: [], updated_at: '2026-09-24T00:00:00Z',
  }
  let currentHash = 'a'.repeat(64)
  let projectVersion = 1
  let factRevision = 1
  let modelInputCalls = 0
  let previewCalls = 0
  let commitCalls = 0
  let configVersion = 1
  let materialSaveCalls = 0
  let provenanceMode = 'review'
  const previewHashes: string[] = []
  let commitBody: Record<string, unknown> | null = null
  const makeInput = () => ({ schema_version: 'writing-input/v1', project_id: project.id, project_version: projectVersion,
    report_id: report.id, report_version: report.version, section: { id: 'S4', title: '需求与产能' },
    corpus: { id: corpus.corpus_id, version: corpus.corpus_version },
    facts: [{ key: 'demand', label: '首年需求', value: '300000', unit: '套', revision: factRevision,
      evidence: { status: 'source_locator_reviewed' } }],
    rules: [{ rule_id: 'min_sales', expression: 'min(demand, capacity)', target_key: 'planned_sales', deps: ['demand', 'capacity'], result: '254016' }],
    materials: [
      { item_id: '04:000004', role: 'outline', source_ref: outlineRef, review_status: 'selected_for_project_pattern', usage: '仅复用章节层级', content: { title: '需求与产能', paragraph_order: ['需求', '能力', '结论'] } },
      { item_id: '15:000002', role: 'claim', source_ref: claimRef, review_status: 'selected_for_project_pattern', usage: '仅作预测与订单的边界提醒', content: { text: '预测需求不是已签订单。', must_cite: ['EV-01'] } },
    ], constraints: { historical_values: 'excluded', new_claims: 'forbidden', candidate_status: 'pending_review' },
  })
  const materialResponse = () => ({
      project_id: project.id, section_id: 'S4', config_version: configVersion,
      groups: ['基础信息', '语义图谱'], slots: [], items: [
        { category_id: 'CAT-03', number: 3, name: '来源过程', group: '基础信息', record_count: 1, artifact_count: 1,
          configured_mode: provenanceMode, effective_role: 'review', purpose: '核对抽取过程', chapter_role: '核对来源', limitations: [], record_ids: ['03:000001'] },
        { category_id: 'CAT-04', number: 4, name: '章节结构', group: '基础信息', record_count: 1, artifact_count: 1,
          configured_mode: 'auto', effective_role: 'input', purpose: '确定章节顺序', chapter_role: '写作输入', limitations: [], record_ids: ['04:000004'] },
        { category_id: 'CAT-15', number: 15, name: '论断', group: '语义图谱', record_count: 1, artifact_count: 1,
          configured_mode: 'auto', effective_role: 'input', purpose: '控制论断边界', chapter_role: '写作输入', limitations: [], record_ids: ['15:000002'] },
      ],
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects' && request.method() === 'GET') return reply([project])
    if (path === '/projects/model-project/facts') return reply({ project_version: projectVersion, facts: [] })
    if (path === '/projects/model-project/reports') return reply([{ id: report.id, title: report.title, version: report.version, updated_at: report.updated_at }])
    if (path === '/projects/model-project/reports/r1' && request.method() === 'GET') return reply(report)
    if (path === '/projects/model-project/reports/r1/versions') return reply([{ version: report.version, reviewed: report.reviewed, created_at: report.updated_at }])
    if (path === '/projects/model-project/writing/reference') return reply({ ...corpus, selected: true, target_report_type: 'feasibility', compatible: true })
    if (path === '/projects/model-project/writing/sections') return reply({ sections: [
      { id: 'S4', title: '需求与产能', level: 1, reusable: true, chunk_count: 2, guided_available: true, model_available: true },
    ] })
    if (path === '/projects/model-project/writing/packages/S4') return reply({ section: { id: 'S4', title: '需求与产能', level: 1 }, project_id: project.id, project_version: projectVersion,
      ...corpus, items, required_item_ids: ['15:000002'], slots: [], issues: [], facts: [] })
    if (path === '/projects/model-project/writing/materials' && request.method() === 'GET') return reply(materialResponse())
    if (path === '/projects/model-project/writing/materials/S4' && request.method() === 'PUT') {
      materialSaveCalls += 1
      const body = request.postDataJSON()
      expect(body.base_version).toBe(configVersion)
      expect(body.settings).toContainEqual({ category_id: 'CAT-03', mode: 'exclude' })
      configVersion += 1; provenanceMode = 'exclude'; projectVersion += 1; currentHash = 'c'.repeat(64)
      return reply(materialResponse())
    }
    if (path === '/projects/model-project/reports/r1/writing/model-input' && request.method() === 'POST') {
      modelInputCalls += 1
      expect(request.postDataJSON()).toEqual({ section_id: 'S4', approved_item_ids: ['04:000004', '15:000002'], mode: 'model' })
      return reply({ model_input: makeInput(), input_sha256: currentHash, excluded_items: [{ item_id: '10:000001', reason: '历史条件不适用' }] })
    }
    if (path === '/projects/model-project/reports/r1/writing/preview' && request.method() === 'POST') {
      previewCalls += 1
      const body = request.postDataJSON()
      previewHashes.push(body.expected_input_sha256)
      if (body.expected_input_sha256 !== currentHash) return reply({ detail: '输入版本已变化，请刷新依据' }, 409)
      const modelInput = makeInput()
      return reply({ section_id: 'S4', paragraphs: [{ type: 'p', id: 'model-p1', section_id: 'S4', origin: 'model', fact_keys: ['demand'], source_refs: [claimRef], children: [{ text: '预测需求为 300000 套，尚非已签订单。' }] }],
        issues: [], used_items: ['04:000004', '15:000002'], rejected_items: [], base_version: report.version,
        project_version: modelInput.project_version, preview_token: 'signed-model', expires_at: 9999999999,
        model_input: modelInput, input_sha256: currentHash, model_call: { model: 'configured-text' },
        model_usage: [{ paragraph_id: 'model-p1', source_item_ids: ['15:000002'], fact_keys: ['demand'] }],
      })
    }
    if (path === '/projects/model-project/reports/r1/writing/commit' && request.method() === 'POST') {
      commitCalls += 1; commitBody = request.postDataJSON()
      report.version += 1
      report.content = [...report.content, ...(commitBody?.paragraphs as Record<string, unknown>[])]
      return reply(report)
    }
    if (path === '/projects/model-project/writing/sources/15/15%3A000002') return reply({ source_ref: claimRef,
      location: { page: 4 }, summary: '预测需求不是订单', payload: { pattern: '客户预测不能改写为已签订单或最低采购承诺。', must_cite: ['EV-01'] } })
    if (path === '/projects/model-project/writing/impact') return reply({ impacts: [] })
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })

  await page.goto('/')
  await page.getByRole('button', { name: '报告写作' }).click()
  await page.setViewportSize({ width: 900, height: 700 })
  const foldedHeights = await page.locator('.report-optional-draft, .report-history').evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().height))
  expect(foldedHeights).toHaveLength(2)
  expect(foldedHeights.every((height) => height < 100)).toBe(true)
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.getByLabel('选择语料章节').selectOption('S4')
  await page.locator('.corpus-writing-item').filter({ hasText: 'S4' }).getByRole('checkbox').check()
  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('checkbox').check()
  await page.getByLabel('起草方式').selectOption('model')
  await expect(page.getByRole('button', { name: '预览章节候选' })).toBeDisabled()
  await page.getByRole('button', { name: '起草依据' }).click()
  const basis = page.getByRole('dialog', { name: '起草依据' })
  await expect(basis).toContainText('首年需求 · 300000套')
  await expect(basis).toContainText('r1 · 原文已核对')
  await expect(basis).toContainText('min(demand, capacity)')
  await expect(basis).toContainText('审核材料')
  await expect(basis).toContainText('历史数值已排除')
  await basis.locator('.model-input-material').filter({ hasText: 'L002' }).locator('summary').click()
  await expect(basis).toContainText('仅作预测与订单的边界提醒')
  await basis.getByText('未发送 1').click()
  await expect(basis).toContainText('SK1')
  await basis.locator('.model-input-material').filter({ hasText: 'L002' }).getByRole('button', { name: '来源' }).click()
  await expect(page.locator('.drawer-content.corpus-writing-source').getByText('预测需求不是订单', { exact: true })).toBeVisible()
  await expect(page.locator('.drawer-content.corpus-writing-source .corpus-source-text')).toContainText('客户预测不能改写为已签订单或最低采购承诺。')
  await page.getByRole('button', { name: '关闭来源' }).click()
  await page.getByRole('button', { name: '起草依据' }).click()
  expect(modelInputCalls).toBe(1)
  await basis.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.getByText('预测需求为 300000 套，尚非已签订单。')).toBeVisible()
  await expect(page.locator('.candidate-model-summary')).toContainText('输入材料 2 · 实际引用 1')
  await expect(page.locator('.candidate-paragraph')).toContainText('实际引用 L002 · 项目事实 demand')
  expect(previewHashes).toEqual(['a'.repeat(64)])
  await page.getByRole('button', { name: '取消候选' }).click()
  expect(commitCalls).toBe(0)

  currentHash = 'b'.repeat(64); projectVersion = 2; factRevision = 2
  await page.getByRole('button', { name: '预览章节候选' }).click()
  await expect(page.getByText('输入版本已变化，请刷新依据')).toBeVisible()
  expect(modelInputCalls).toBe(1)
  expect(previewHashes.at(-1)).toBe('a'.repeat(64))
  await page.getByRole('button', { name: '起草依据' }).click()
  await basis.getByRole('button', { name: '刷新依据' }).click()
  await expect(basis).toContainText('项目 v2')
  expect(modelInputCalls).toBe(2)
  await basis.getByRole('button', { name: '预览章节候选' }).click()
  expect(previewHashes.at(-1)).toBe('b'.repeat(64))
  await page.getByRole('button', { name: '配置与模拟' }).click()
  await page.getByLabel('来源过程使用方式').selectOption('exclude')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('配置已保存')).toBeVisible()
  expect(materialSaveCalls).toBe(1)
  await expect(page.locator('.corpus-writing-candidate')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '预览章节候选' })).toBeDisabled()
  await page.locator('.corpus-writing-item').filter({ hasText: 'S4' }).getByRole('checkbox').check()
  await page.locator('.corpus-writing-item').filter({ hasText: 'L002' }).getByRole('checkbox').check()
  await page.getByRole('button', { name: '起草依据' }).click()
  expect(modelInputCalls).toBe(3)
  await basis.getByRole('button', { name: '预览章节候选' }).click()
  expect(previewHashes.at(-1)).toBe('c'.repeat(64))
  await page.getByRole('button', { name: '加入报告' }).click()
  await expect(page.locator('.plate-content')).toContainText('预测需求为 300000 套，尚非已签订单。')
  expect(commitCalls).toBe(1)
  expect(commitBody?.input_sha256).toBe('c'.repeat(64))
  expect((commitBody?.model_input as { materials: unknown[] }).materials).toHaveLength(2)
  expect(commitBody?.model_usage).toEqual([{ paragraph_id: 'model-p1', source_item_ids: ['15:000002'], fact_keys: ['demand'] }])
  await expect(page.getByRole('button', { name: '预览章节候选' })).toBeDisabled()
})
