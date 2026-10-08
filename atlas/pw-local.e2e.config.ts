// Temporary local wrapper (not committed): pins the preinstalled Chromium build.
import base from './playwright.config';
import pages from './playwright.pages.config';
import { defineConfig } from '@playwright/test';
const executablePath = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const cfg = process.env.PW_PAGES ? pages : base;
export default defineConfig({
  ...cfg,
  projects: (cfg.projects ?? []).filter((p) => p.name?.includes('chromium')).map((p) => ({
    ...p,
    use: { ...p.use, launchOptions: { executablePath } },
  })),
});
