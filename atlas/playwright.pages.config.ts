import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: 'e2e-pages',
  timeout: 60000,
  expect: { timeout: 10000 },
  workers: 1,
  fullyParallel: false,
  outputDir: 'test-results/pages',
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report/pages' }]],
  use: {
    baseURL: 'http://127.0.0.1:4175/lightweight-drawing-lab/',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile-chromium', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
    { name: 'ipad-webkit', use: { ...devices['iPad Pro 11'] } },
    { name: 'iphone-webkit', use: { ...devices['iPhone 13'] } },
  ],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js preview --outDir dist-pages --host 127.0.0.1 --port 4175 --strictPort',
    env: { VITE_STATIC_HOSTING: '1', VITE_PUBLIC_BASE: '/lightweight-drawing-lab/' },
    url: 'http://127.0.0.1:4175/lightweight-drawing-lab/',
    reuseExistingServer: false,
  },
});
