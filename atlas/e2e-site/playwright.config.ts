import { defineConfig, devices } from '@playwright/test';

/** Screener-site shell (report pages, search, navigation) against mocked report and market data. */
export default defineConfig({
  testDir: '.',
  timeout: 60000,
  expect: { timeout: 10000 },
  workers: 1,
  fullyParallel: false,
  outputDir: '../test-results/site',
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4177/my-stock-screener/atlas/',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    serviceWorkers: 'block',
    locale: 'zh-TW',
    timezoneId: 'Asia/Taipei',
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile-chromium', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
    { name: 'iphone-webkit', use: { ...devices['iPhone 13'] } },
  ],
  webServer: {
    command: 'node e2e-site/serve.mjs',
    cwd: '..',
    url: 'http://127.0.0.1:4177/my-stock-screener/atlas/',
    reuseExistingServer: false,
    timeout: 180000,
  },
});
