import { expect, test } from '@playwright/test'

const groupNames = ['基础信息', '原始资料与结构', '内容切块', '语义图谱', '证据与推演', '质量审查', '写作与索引']
const groupSizes = [3, 5, 3, 6, 4, 3, 6]
const categoryName = (number: number) => ({ 4: '章节大纲', 11: '向量文件', 13: '规则', 23: '冲突' } as Record<number, string>)[number] || `资料类别 ${number}`
const groupFor = (number: number) => groupNames[groupSizes.findIndex((_, index) => number <= groupSizes.slice(0, index + 1).reduce((a, b) => a + b, 0))]

test('30 类可逐项配置、查看作用并模拟移除；示例 0 与空值不混同', async ({ page }) => {
  test.setTimeout(90_000)
  const project = { id: 'material-project', name: '资料应用测试', version: 1, has_corpus: false, is_builtin: false }
  const report = { id: 'r1', project_id: project.id, title: '试验稿', version: 1,
    content: [{ type: 'p', id: 'intro', children: [{ text: '' }] }], reviewed: false,
    issues: [], fact_impacts: [], facts: [], updated_at: '2026-09-24T00:00:00Z' }
  let configVersion = 0
  let modes: Record<string, string> = {}
  let saveCalls = 0
  let packageCalls = 0
  const simulations: Record<string, unknown>[] = []
  const items = (section: string) => Array.from({ length: 30 }, (_, index) => {
    const number = index + 1
    const categoryId = `CAT-${String(number).padStart(2, '0')}`
    return { category_id: categoryId, number, name: categoryName(number), group: groupFor(number),
      record_count: number === 11 ? 0 : 2, artifact_count: 1, configured_mode: modes[categoryId] || 'auto',
      effective_role: number === 11 ? 'not_generated' : number === 4 ? 'input' : 'review',
      purpose: `资料 ${number} 的用途`, chapter_role: section === 'S4' && number === 4 ? '定位本章层级和标题' : '本章没有已验证的记录消费路径',
      limitations: number === 11 ? '向量零行' : '仅作历史参考', record_ids: number === 4 ? ['04:000004'] : [] }
  })
  const materialData = (section: string) => ({ project_id: project.id, section_id: section, config_version: configVersion,
    groups: groupNames.map((name, index) => ({ name, count: groupSizes[index] })), items: items(section),
    slots: section === 'S4' ? [{ slot_id: 'N017', label: '首年需求', unit: '套', value: null },
      { slot_id: 'N034', label: '合格能力', unit: '套', value: null }] : [] })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/projects/material-project/facts') return reply({ project_version: project.version, facts: [] })
    if (path === '/projects/material-project/reports') return reply([{ id: 'r1', title: report.title, version: 1, updated_at: report.updated_at }])
    if (path === '/projects/material-project/reports/r1') return reply(report)
    if (path === '/projects/material-project/reports/r1/versions') return reply([{ version: 1, reviewed: false, created_at: report.updated_at }])
    if (path === '/projects/material-project/writing/reference') return reply({ selected: true, corpus_id: 'chengyue', corpus_version: 'v2', source_project_id: 'history', target_report_type: 'feasibility', compatible: true })
    if (path === '/projects/material-project/writing/sections') return reply({ sections: [
      { id: 'S4', title: '需求与产能', level: 1, reusable: true, chunk_count: 3, guided_available: true, model_available: true },
      { id: 'S8', title: '其他章节', level: 1, reusable: false, chunk_count: 1, guided_available: false, model_available: false },
    ] })
    if (path.startsWith('/projects/material-project/writing/packages/')) {
      packageCalls += 1
      const section = path.split('/').at(-1)
      return reply({ section: { id: section, title: section === 'S4' ? '需求与产能' : '其他章节', level: 1 },
        project_id: project.id, project_version: project.version, corpus_id: 'chengyue', corpus_version: 'v2',
        source_project_id: 'history', items: [], required_item_ids: [], slots: [], issues: [], facts: [] })
    }
    if (path === '/projects/material-project/writing/materials' && request.method() === 'GET') return reply(materialData(url.searchParams.get('section_id') || 'S4'))
    if (path === '/projects/material-project/writing/materials/S4' && request.method() === 'PUT') {
      saveCalls += 1
      const body = request.postDataJSON()
      expect(body.base_version).toBe(configVersion)
      expect(body.settings).toHaveLength(30)
      modes = Object.fromEntries(body.settings.map((setting: { category_id: string; mode: string }) => [setting.category_id, setting.mode]))
      configVersion += 1
      project.version += 1
      return reply(materialData('S4'))
    }
    if (path === '/projects/material-project/writing/materials/S4/simulate' && request.method() === 'POST') {
      const body = request.postDataJSON()
      simulations.push(body)
      const zero = body.slot_values.N017 === '0'
      const missing = body.slot_values.N034 === null
      const text = missing ? '' : `首年计划销售量为 ${zero ? '0' : '254016'} 套。`
      const outcome = { status: missing ? 'blocked' : 'ready', text, used_categories: ['CAT-04'],
        used_records: Array.from({ length: 7 }, (_, index) => `04:00000${index + 1}`),
        issues: missing ? [{ message: '合格能力未定义' }] : [] }
      return reply({ baseline: outcome, configured: outcome,
        experiments: body.selected_categories.map((category_id: string) => ({ category_id, status: missing ? 'not_consumed' : 'blocked',
          reason: missing ? '输入不足' : '移除后无法成稿', before_text: text, after_text: '', used_records: ['04:000004'] })),
        facts: [{ slot_id: 'N017', label: '首年需求', unit: '套', value: zero ? '0' : '300000', status: 'PROVIDED' },
          { slot_id: 'N034', label: '合格能力', unit: '套', value: missing ? null : '254016', status: missing ? 'UNDEFINED' : 'PROVIDED' },
          { slot_id: 'N035', label: '计划销售量', unit: '套', value: missing ? null : zero ? '0' : '254016', status: missing ? 'UNEVALUABLE' : 'COMPUTED' }],
        trace: [{ rule_id: 'R006', name: '计划销售量', target: 'planned_sales', expression: 'min(first_year_demand, qualified_capacity)',
          inputs: missing ? undefined : { first_year_demand: zero ? '0' : '300000', qualified_capacity: '254016' },
          missing: missing ? ['qualified_capacity'] : [], result: missing ? null : zero ? '0' : '254016', status: missing ? 'UNEVALUABLE' : 'COMPUTED' }],
        simulation_only: true })
    }
    return reply({ detail: `未模拟的 API：${request.method()} ${path}` }, 501)
  })

  await page.goto('/')
  await page.getByRole('button', { name: '报告写作' }).click()
  await page.getByLabel('选择语料章节').selectOption('S4')
  const panel = page.locator('.material-panel')
  await expect(panel.getByRole('heading', { name: /资料应用/ })).toBeVisible()
  await panel.getByRole('button', { name: /配置与模拟/ }).click()
  await expect(panel.getByRole('tab')).toHaveCount(7)
  for (let number = 1; number <= 30; number++) {
    await panel.getByRole('tab', { name: new RegExp('^' + groupFor(number)) }).click()
    const row = panel.locator('.material-row').filter({ hasText: String(number).padStart(2, '0') + categoryName(number) })
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: '作用' }).click()
    const detail = page.getByRole('dialog', { name: categoryName(number) + '作用' })
    await expect(detail).toContainText(`资料 ${number} 的用途`)
    if (number === 1) { await detail.press('Escape'); await expect(detail).toBeHidden() }
    else await detail.getByRole('button', { name: '关闭作用' }).click()
  }
  await panel.getByRole('tab', { name: /原始资料与结构/ }).click()
  await panel.getByLabel('章节大纲使用方式').selectOption('review')
  await expect(panel).toContainText('配置未保存')
  await panel.getByRole('button', { name: '保存配置' }).click()
  await expect(panel).toContainText('配置已保存')
  expect(saveCalls).toBe(1)
  expect(modes['CAT-04']).toBe('review')
  await expect.poll(() => packageCalls).toBe(2)
  await panel.getByRole('tab', { name: /原始资料与结构/ }).click()
  await panel.getByLabel('模拟 章节大纲').check()
  await panel.getByRole('button', { name: '示例输入' }).click()
  await panel.getByRole('button', { name: '模拟对比', exact: true }).click()
  await expect(panel).toContainText('首年计划销售量为 254016 套。')
  await expect(panel.locator('.material-outcomes')).toContainText('默认适用')
  await expect(panel.locator('.material-trace')).toContainText('min(300000, 254016) = 254016')
  await expect(panel.locator('.material-trace')).not.toContainText('planned_sales')
  await expect(panel.locator('.material-trace span').first()).toHaveAttribute('title', 'planned_sales')
  const current = panel.locator('.material-outcome').nth(1)
  await expect(current.getByText('引用 7 条')).toBeVisible()
  await expect(current.getByText('04:000007')).toBeHidden()
  await current.getByText('引用 7 条').click()
  await expect(current.getByText('04:000007')).toBeVisible()
  await expect(panel).toContainText('逐类移除')
  expect(simulations[0].selected_categories).toEqual(['CAT-04'])
  expect(simulations[0].input_mode).toBe('example')
  expect(simulations[0].slot_values).toEqual({ N017: '300000', N034: '254016' })
  await panel.getByLabel('示例首年需求').fill('0')
  await panel.getByLabel('示例合格能力').fill('')
  await panel.getByRole('button', { name: '模拟对比', exact: true }).click()
  await expect(panel.locator('.material-facts')).toContainText('首年需求0套')
  await expect(panel.locator('.material-facts')).toContainText('合格能力未定义')
  await expect(panel.locator('.material-facts')).toContainText('计划销售量不可评估')
  await expect(panel.locator('.material-trace')).toContainText('缺失 qualified_capacity · 不可评估')
  const blocked = panel.locator('.material-outcome').nth(1)
  await expect(blocked.getByText('待核对 1 项')).toBeVisible()
  await expect(blocked.getByText('合格能力未定义')).toBeHidden()
  await blocked.getByText('待核对 1 项').click()
  await expect(blocked.getByText('合格能力未定义')).toBeVisible()
  expect(simulations[1].slot_values).toEqual({ N017: '0', N034: null })
  await page.getByLabel('选择语料章节').selectOption('S8')
  const unadapted = page.locator('.material-panel')
  await unadapted.getByRole('button', { name: /配置与模拟/ }).click()
  await expect(unadapted).toContainText('本章尚未验证推演')
  await expect(unadapted.getByRole('button', { name: '模拟对比', exact: true })).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await unadapted.getByRole('tab', { name: /原始资料与结构/ }).click()
  await expect(unadapted.getByLabel('章节大纲使用方式')).toBeInViewport()
  await unadapted.getByLabel('章节大纲使用方式').selectOption('exclude')
  await expect(unadapted).toContainText('配置未保存')
})
