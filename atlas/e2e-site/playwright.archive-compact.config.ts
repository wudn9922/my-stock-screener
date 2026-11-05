import { defineConfig, devices } from '@playwright/test';
import original from './playwright.config';

export default defineConfig({
  ...original,
  testMatch: [
    /archive-(?:first|board|sidebar|mini-charts|intraday-fill)\.spec\.ts$/,
    /(?:nav-inventory|workspace-refinements|chart-watchlist-followups|market-coverage-charts)\.spec\.ts$/,
  ],
  outputDir: '../test-results/archive-compact',
  projects: [
    { name: 'mobile-chromium-375', use: { ...devices['iPhone SE'], defaultBrowserType: 'chromium' } },
    { name: 'iphone-webkit-375', use: { ...devices['iPhone SE'] } },
  ],
});
