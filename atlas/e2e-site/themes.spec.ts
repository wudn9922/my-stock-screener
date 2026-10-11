import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

const themesBody = readFileSync(resolve(import.meta.dirname, '../src/report/fixtures/themes.sample.json'), 'utf8');

async function mockThemes(page: Page, status: 'ok' | 'missing' = 'ok') {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/themes.json', (route) =>
    status === 'ok'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: themesBody })
      : route.fulfill({ status: 404, body: 'not found' }),
  );
}

test('tiles view: periods, relative mode and parent filter', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockThemes(page);
  await page.goto('./?page=themes');
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  const first = await page.locator('.theme-tile .tile-value').first().textContent();
  await page.getByRole('group', { name: '期間' }).getByRole('button', { name: '3月' }).click();
  await expect(page.locator('.theme-tile .tile-value').first()).not.toHaveText(first ?? '');
  await page.getByRole('group', { name: '指標' }).getByRole('button', { name: '相對 SPY' }).click();
  await expect(page.locator('.theme-tile').first()).toContainText('報酬');
  await page.getByRole('group', { name: '產業大類' }).getByRole('button', { name: /半導體/ }).click();
  await expect(page.locator('.theme-tile')).toHaveCount(5);
  expect(errors).toEqual([]);
});

test('a tile opens the detail sheet with description, constituents and an expandable chart', async ({ page }) => {
  await mockThemes(page);
  await page.goto('./?page=themes');
  await page.locator('.theme-tile', { hasText: '光通訊' }).click();
  await expect(page).toHaveURL(/page=themes&theme=optical/);
  const sheet = page.getByRole('dialog', { name: /光通訊/ });
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('光纖');
  await expect(sheet.locator('.versus-svg')).toBeVisible();
  await expect(sheet.locator('.constituent')).toHaveCount(6);
  await sheet.locator('.constituent-head').first().click();
  await expect(sheet.locator('.mini-chart[data-status="ready"]')).toBeVisible();
  await sheet.getByRole('button', { name: '開啟圖表' }).click();
  await expect(page).toHaveURL(/page=chart&symbol=/);
  await page.goBack();
  await expect(page.getByRole('dialog', { name: /光通訊/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/page=themes$/);
});

test('deep link, unknown theme and missing data', async ({ page }) => {
  await mockThemes(page);
  await page.goto('./?page=themes&theme=nuclear');
  await expect(page.getByRole('dialog', { name: /核能/ })).toBeVisible();
  await page.goto('./?page=themes&theme=no-such-theme');
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(/page=themes$/);
});

test('missing themes.json shows the not-generated state', async ({ page }) => {
  await mockThemes(page, 'missing');
  await page.goto('./?page=themes');
  await expect(page.getByText('題材資料尚未產生')).toBeVisible();
});

test('phone tab bar keeps six buttons on one row', async ({ page }) => {
  await mockThemes(page);
  await page.goto('./?page=themes');
  const bar = page.locator('.site-tabbar');
  if (!(await bar.isVisible())) test.skip(true, 'desktop layout has the side rail instead');
  const boxes = await bar.locator('button').evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().toJSON()));
  expect(boxes).toHaveLength(6);
  expect(new Set(boxes.map((b) => Math.round(b.top))).size).toBe(1);
  for (const box of boxes) expect(box.width).toBeGreaterThanOrEqual(44);
});
