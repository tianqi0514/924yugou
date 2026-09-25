import { defineConfig } from '@playwright/test'
import { backendEnvironment } from './integration/environment'

export default defineConfig({
  testDir: './integration',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 12_000 },
  globalSetup: './integration/global-setup.ts',
  use: {
    baseURL: 'http://127.0.0.1:5175',
    browserName: 'chromium',
    headless: true,
    actionTimeout: 12_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: '../.venv/bin/uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8001',
      url: 'http://127.0.0.1:8001/api/health',
      env: backendEnvironment,
      timeout: 90_000,
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --port 5175 --strictPort',
      url: 'http://127.0.0.1:5175',
      env: { REPORT_PLATFORM_API_TARGET: 'http://127.0.0.1:8001' },
      timeout: 45_000,
      reuseExistingServer: false,
    },
  ],
  reporter: 'list',
  outputDir: '../tmp/integration/playwright-results',
})
