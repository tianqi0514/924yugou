import { readFileSync } from 'node:fs'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { fixturePath } from './environment'
import { attach, watch } from './diagnostics'

test.beforeEach(async ({ page }) => watch(page))
test.afterEach(async ({ page }, info) => attach(page, info))

async function send(request: APIRequestContext, url: string, data: unknown) {
  const response = await request.post(url, { data })
  expect(response.ok(), `${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

test('仅有项目证据的事实可映射起草，替换为二手来源后禁止继续起草', async ({ page, request }) => {
  const project = await send(request, '/api/projects', { name: `项目证据 QA ${Date.now()}` })
  const base = `/api/projects/${project.id}`
  const upload = await request.post(`${base}/documents`, { multipart: {
    file: { name: 'QA原文.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: readFileSync(fixturePath) },
  } })
  expect(upload.ok(), await upload.text()).toBeTruthy()
  const documentId = (await upload.json()).id
  const segments = (await (await request.get(`${base}/documents/${documentId}`)).json()).segments
  const statement = '本项目首年客户预测需求为 300000 套，尚非已签订单。'
  const segment = segments.find((row: { text: string }) => row.text.includes(statement))
  expect(segment).toBeTruthy()
  await send(request, `${base}/facts`, { key: 'demand_statement', label: '需求预测表述',
    data_type: 'text', value: statement, source: 'QA原文' })
  const evidenceBody = { document_id: documentId, source_refs: [segment.ref],
    statement, label: '原文需求预测', fact_key: 'demand_statement' }
  const evidence = await send(request, `${base}/evidence`, evidenceBody)
  const report = await send(request, `${base}/reports`, {
    title: '证据来源验证', report_type: 'government_feasibility',
  })
  await page.goto(`/?project=${project.id}&report=${report.id}`)
  await page.getByRole('button', { name: /建设背景与必要性 · 待配置 · 查看准备情况/ }).click()
  await page.getByRole('button', { name: '配置本章' }).click()
  await expect(page.getByLabel('用于本章 需求预测表述')).toBeEnabled()
  await expect(page.locator('.chapter-mapping-fact')).toContainText('原文已定位')
  await page.getByLabel('用于本章 需求预测表述').check()
  await page.getByRole('button', { name: '预览要求' }).click()
  await page.getByRole('button', { name: '确认本章要求' }).click()
  await expect(page.getByRole('heading', { name: '生成本章' })).toBeVisible()
  await page.locator('.scenario-evidence-list').first().getByText('项目证据').click()
  await expect(page.getByLabel('选用证据 原文需求预测 QA原文.docx')).toBeEnabled()
  await page.getByRole('button', { name: '生成候选' }).click()
  await expect(page.locator('.scenario-candidate')).toContainText('尚非已签订单')

  const originalPreview = await send(request, `${base}/evidence/${evidence.id}/replacement/preview`, evidenceBody)
  const current = await send(request, `${base}/evidence/${evidence.id}/replacement/commit`, {
    ...evidenceBody, base_report_versions: originalPreview.base_report_versions,
    expires_at: originalPreview.expires_at, preview_token: originalPreview.preview_token,
  })
  await page.reload()
  await page.getByRole('button', { name: /建设背景与必要性 · 可起草 · 查看准备情况/ }).click()
  await page.locator('.scenario-preparation').getByRole('button', { name: '生成本章' }).click()
  await page.locator('.scenario-evidence-list').first().locator('summary').click()
  const oldChoice = page.locator('.scenario-evidence-choice').filter({ hasText: '已更新' })
  await expect(oldChoice.locator('input')).toBeDisabled()
  await expect(page.locator('.scenario-evidence-choice').filter({ hasNotText: '已更新' }).locator('input')).toBeEnabled()

  const replacement = { ...evidenceBody, source_type: 'secondary' }
  const before = await send(request, `${base}/evidence/${current.id}/replacement/preview`, replacement)
  await send(request, `${base}/evidence/${current.id}/replacement/commit`, {
    ...replacement, base_report_versions: before.base_report_versions,
    expires_at: before.expires_at, preview_token: before.preview_token,
  })
  await page.reload()
  await expect(page.getByRole('button', { name: '生成本章', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: /建设背景与必要性 · 缺资料 · 查看准备情况/ }).click()
  await expect(page.getByRole('button', { name: /核对 需求预测表述 来源/ })).toBeVisible()
  await page.getByRole('button', { name: '关闭面板' }).click()
  await page.getByRole('button', { name: '更多操作' }).click()
  await page.locator('.scenario-more-menu').getByRole('button', { name: '项目资料' }).click()
  await expect(page.locator('.scenario-material-summary')).toContainText('当前原文 0')
  await page.locator('.scenario-material-group').first().locator('summary').click()
  await expect(page.locator('.scenario-material-group').first()).toContainText('已更新')
  await expect(page.locator('.scenario-material-group').first()).toContainText('二手材料')
})
