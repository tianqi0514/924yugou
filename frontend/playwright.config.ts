import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://127.0.0.1:5174', browserName: 'chromium', headless: true },
  webServer: {
    command: 'npm run dev -- --port 5174 --strictPort',
    url: 'http://127.0.0.1:5174',
    timeout: 30_000,
    reuseExistingServer: false,
  },
  reporter: 'list',
})
