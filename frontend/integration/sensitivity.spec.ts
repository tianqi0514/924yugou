import { expect, test } from '@playwright/test'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

test('单因素试算在写作工作台可用且不修改方案输入', async ({ page, request }) => {
  const projects = await (await request.get('/api/projects')).json()
  const project = projects.find((row: { has_corpus: boolean }) => row.has_corpus)
  expect(project).toBeTruthy()
  const base = `/api/projects/${project.id}`
  const scenarioResponse = await request.post(`${base}/analysis/scenarios`, {
    data: { name: '敏感性界面验收', source: 'historical' },
  })
  expect(scenarioResponse.ok(), await scenarioResponse.text()).toBeTruthy()
  const scenario = await scenarioResponse.json()
  const reportResponse = await request.post(`${base}/reports`, { data: { title: '敏感性报告' } })
  expect(reportResponse.ok(), await reportResponse.text()).toBeTruthy()
  const report = await reportResponse.json()
  const run = await request.post(`${base}/analysis/scenarios/${scenario.id}/runs`, {
    data: { scenario_revision: scenario.revision, request_key: 'sensitivity-browser-baseline' },
  })
  expect(run.ok(), await run.text()).toBeTruthy()
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: '推演结果' }).click()
  await page.getByText('敏感性试算').click()
  await page.getByRole('combobox', { name: '试算输入' }).selectOption('N017')
  await page.getByRole('combobox', { name: '试算结果' }).selectOption('N035')
  await page.getByRole('textbox', { name: '试算值' }).fill('300000，200000，0')
  await page.getByRole('button', { name: '试算', exact: true }).click()
  const table = page.locator('.scenario-sensitivity-rows')
  await expect(table).toContainText('254016')
  await expect(table).toContainText('200000')
  await expect(table).toContainText('0套/年')
  const unchanged = await (await request.get(`${base}/analysis/scenarios/${scenario.id}`)).json()
  expect(unchanged.revision).toBe(scenario.revision)
  expect(unchanged.inputs).toEqual(scenario.inputs)
  await table.getByRole('button').first().click()
  await expect(page.locator('.scenario-condition')).toContainText('需求高于产能')
})
