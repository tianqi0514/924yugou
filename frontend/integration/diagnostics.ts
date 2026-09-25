import type { Page, TestInfo } from '@playwright/test'

type Entry = { kind: 'http' | 'request' | 'page' | 'console'; detail: string }
const events = new WeakMap<Page, Entry[]>()

export function watch(page: Page) {
  const rows: Entry[] = []
  events.set(page, rows)
  page.on('response', (response) => {
    if (response.status() >= 400) rows.push({ kind: 'http', detail: `${response.status()} ${response.request().method()} ${response.url()}` })
  })
  page.on('requestfailed', (request) => rows.push({ kind: 'request', detail: `${request.method()} ${request.url()} ${request.failure()?.errorText || ''}` }))
  page.on('pageerror', (error) => rows.push({ kind: 'page', detail: error.stack || error.message }))
  page.on('console', (message) => { if (message.type() === 'error') rows.push({ kind: 'console', detail: message.text() }) })
}

export async function attach(page: Page, info: TestInfo) {
  const rows = events.get(page) || []
  await info.attach('browser-and-http-events', { body: Buffer.from(JSON.stringify(rows, null, 2)), contentType: 'application/json' })
}
