import { expect, test } from '@playwright/test'

test('原件列表、页码恢复、待审导航与上传校验可操作（接口替身）', async ({ page }) => {
  const project = { id: 'document-ux', name: '文件体验验收', version: 0, has_corpus: false, is_builtin: false }
  const files = [
    { id: 'd1', filename: '交通枢纽可研.pdf', file_kind: 'pdf', pages: 3, segments: 3, status: 'READY' },
    { id: 'd2', filename: '补充原件.docx', file_kind: 'docx', pages: 1, segments: 1, status: 'READY' },
  ]
  let uploads = 0
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([project])
    if (path === '/model/status') return reply({ configured: false, ocr_configured: false })
    if (path === '/projects/document-ux/documents' && request.method() === 'POST') { uploads++; return reply({ detail: '不应上传' }, 400) }
    if (path === '/projects/document-ux/documents') return reply(files)
    if (path === '/projects/document-ux/documents/d1/pages') return reply({ pages: [
      { page: 1, segment_count: 1, char_count: 8, candidate_count: 0, pending_count: 0, invalid_count: 0, extractable: true, reason: '' },
      { page: 2, segment_count: 1, char_count: 8, candidate_count: 3, pending_count: 3, invalid_count: 0, extractable: true, reason: '' },
      { page: 3, segment_count: 1, char_count: 8, candidate_count: 1, pending_count: 1, invalid_count: 0, extractable: true, reason: '' },
    ] })
    if (path === '/projects/document-ux/documents/d2/pages') return reply({ pages: [
      { page: 1, segment_count: 1, char_count: 8, candidate_count: 0, pending_count: 0, invalid_count: 0, extractable: true, reason: '' },
    ] })
    if (path === '/projects/document-ux/documents/d1') {
      const number = Number(url.searchParams.get('page') || 1)
      const candidate = (id: string) => ({ id, label: `候选${id}`, value: '10', unit: '㎡', data_type: 'decimal',
        source_ref: `p${number}-s1`, excerpt: '面积 10 ㎡', source_valid: true, review_status: 'PENDING', fact_key: null,
        extraction_run_id: null, extraction_origin: 'TABLE', extraction_model: null, extraction_model_version: null })
      return reply({ document: files[0], page: number, segments: [{ ref: `p${number}-s1`, page: number, text: '面积 10 ㎡' }],
        candidates: number === 2 ? [candidate('c1'), candidate('c2'), candidate('c4')] : number === 3 ? [candidate('c3')] : [] })
    }
    if (path === '/projects/document-ux/documents/d2') return reply({ document: files[1], page: 1,
      segments: [{ ref: 'd-p1', page: 1, text: '补充正文' }], candidates: [] })
    return reply({ detail: `未模拟 ${path}` }, 404)
  })
  await page.goto('/?project=document-ux&section=documents&document=d1&page=3&segment=p3-s1')
  await expect(page.locator('#segment-p3-s1')).toHaveClass(/document-segment-active/)
  await page.getByLabel('搜索文件').fill('补充')
  await expect(page.locator('.document-list button').filter({ hasText: '交通枢纽可研' })).toHaveCount(0)
  await expect(page.locator('.document-list button').filter({ hasText: '补充原件' })).toBeVisible()
  await page.getByLabel('搜索文件').fill('')
  await page.getByRole('button', { name: '返回首个待审页' }).click()
  await expect(page.getByText('PDF 第 2 / 3 页')).toBeVisible()
  await page.getByRole('button', { name: '下一待审项' }).click()
  await expect(page.locator('#candidate-c1')).toHaveClass(/expanded/)
  await page.getByRole('button', { name: '下一待审项' }).click()
  await expect(page.locator('#candidate-c2')).toHaveClass(/expanded/)
  await page.getByRole('button', { name: '下一待审项' }).click()
  await expect(page.locator('#candidate-c4')).toHaveClass(/expanded/)
  await page.locator('.upload-button input[type="file"]').setInputFiles({ name: '不支持.txt', mimeType: 'text/plain', buffer: Buffer.from('text') })
  await expect(page.getByRole('alert')).toContainText('只支持 PDF 或 DOCX')
  await page.locator('.upload-button input[type="file"]').setInputFiles({ name: '空白.pdf', mimeType: 'application/pdf', buffer: Buffer.alloc(0) })
  await expect(page.getByRole('alert')).toContainText('文件为空')
  await page.locator('.upload-button input[type="file"]').setInputFiles({ name: '超限.pdf', mimeType: 'application/pdf', buffer: Buffer.alloc(30 * 1024 * 1024 + 1) })
  await expect(page.getByRole('alert')).toContainText('超过 30 MB')
  expect(uploads).toBe(0)
  await page.setViewportSize({ width: 500, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('切换原件后迟到的全文搜索结果不回填', async ({ page }) => {
  const files = [
    { id: 'd1', filename: '第一份.pdf', file_kind: 'pdf', pages: 1, segments: 1, status: 'READY' },
    { id: 'd2', filename: '第二份.pdf', file_kind: 'pdf', pages: 1, segments: 1, status: 'READY' },
  ]
  let releaseSearch: (() => void) | undefined
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
    const reply = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/projects') return reply([{ id: 'search-race', name: '搜索切换', version: 0, has_corpus: false, is_builtin: false }])
    if (path === '/model/status') return reply({ configured: false, ocr_configured: false })
    if (path === '/projects/search-race/documents') return reply(files)
    if (path.endsWith('/pages')) return reply({ pages: [{ page: 1, segment_count: 1, char_count: 3, candidate_count: 0, pending_count: 0, invalid_count: 0, extractable: true, reason: '' }] })
    if (path.endsWith('/search')) {
      await new Promise<void>((resolve) => { releaseSearch = resolve })
      return reply({ total: 1, results: [{ ref: 'p1-s1', page: 1, locator: '第1页', excerpt: '第一份中的旧结果' }] })
    }
    if (path.endsWith('/d1') || path.endsWith('/d2')) return reply({ document: files[path.endsWith('/d1') ? 0 : 1], page: 1, segments: [{ ref: 'p1-s1', page: 1, text: '正文' }], candidates: [] })
    return reply({ detail: path })
  })
  await page.goto('/?project=search-race&section=documents')
  await page.getByLabel('搜索原文').fill('旧结果')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect.poll(() => Boolean(releaseSearch)).toBeTruthy()
  await page.locator('.document-list button').filter({ hasText: '第二份.pdf' }).click()
  releaseSearch?.()
  await expect(page.getByLabel('搜索原文')).toHaveValue('')
  await expect(page.locator('.document-search-results')).toHaveCount(0)
  await expect(page.locator('.document-main')).toContainText('第二份.pdf')
})
