import { defineConfig, devices } from '@playwright/test';
import original from './playwright.config';

export default defineConfig({
  ...original,
  testMatch: /(?:market-calendar|live-quotes|closed-market|nav-inventory)\.spec\.ts$/,
  outputDir: '../test-results/calendar-compact',
  projects: [
    { name: 'mobile-chromium-375', use: { ...devices['iPhone SE'], defaultBrowserType: 'chromium' } },
    { name: 'iphone-webkit-375', use: { ...devices['iPhone SE'] } },
  ],
});
