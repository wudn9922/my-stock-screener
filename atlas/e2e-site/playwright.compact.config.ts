import { defineConfig, devices } from '@playwright/test';
import base from './playwright.config';
const chromiumLaunch = process.env.ATLAS_CHROMIUM_PATH ? { launchOptions: { executablePath: process.env.ATLAS_CHROMIUM_PATH } } : {};
/** Additional 375px coverage; the original desktop/390px suites remain unchanged. */
export default defineConfig({
  ...base,
  testDir: '.',
  testMatch: ['mexc.spec.ts', 'index-volume.spec.ts', 'breakouts.spec.ts', 'pr-a-*.spec.ts', 'live-quotes.spec.ts', 'market-details.spec.ts', 'legacy-import.spec.ts', 'watchlist-board.spec.ts', 'workspace-refinements.spec.ts'],
  outputDir: '../test-results/site-compact',
  projects: [
    { name: 'mobile375-chromium', use: { ...devices['iPhone SE'], viewport: { width: 375, height: 667 }, ...chromiumLaunch, defaultBrowserType: 'chromium' } },
    { name: 'iphone375-webkit', use: { ...devices['iPhone SE'], viewport: { width: 375, height: 667 } } },
  ],
});
