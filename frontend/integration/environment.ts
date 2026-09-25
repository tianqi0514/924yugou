import { fileURLToPath } from 'node:url'
import path from 'node:path'

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
export const databaseUrl = process.env.REPORT_PLATFORM_TEST_DATABASE_URL
  || 'postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test'

let databaseName: string
try {
  databaseName = new URL(databaseUrl).pathname.replace(/^\//, '')
} catch {
  throw new Error('REPORT_PLATFORM_TEST_DATABASE_URL 不是有效的数据库 URL')
}
if (databaseName !== 'report_platform_test') {
  throw new Error('集成测试只允许连接 report_platform_test')
}

export const integrationRoot = path.join(root, 'tmp', 'integration')
export const documentStorage = path.join(integrationRoot, 'documents')
export const modelSettings = path.join(integrationRoot, 'model-settings')
export const fixturePath = path.join(integrationRoot, 'fixtures', '本项目事实_QA合成原件.docx')
export const backendEnvironment = {
  DATABASE_URL: databaseUrl,
  DOCUMENT_STORAGE_DIR: documentStorage,
  MODEL_SETTINGS_DIR: modelSettings,
}
