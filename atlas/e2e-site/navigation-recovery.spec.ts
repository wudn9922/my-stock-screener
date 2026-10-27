import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

async function fixtures(page: Page) {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  for (const market of ['', '-tw']) {
    const body = readFileSync(resolve(import.meta.dirname, `../src/report/fixtures/themes${market}.sample.json`), 'utf8');
    await page.route(`**/my-stock-screener/report/themes${market}.json`, route => route.fulfill({ contentType: 'application/json', body }));
  }
}

async function openDestination(page: Page, nav: ReturnType<Page['getByRole']>, label: string) {
  if (label === '全球' || label === '題材' || label === '台股') {
    await nav.getByRole('button', { name: '市場', exact: true }).click();
    await page.getByRole('navigation', { name: '市場分頁' }).getByRole('link', { name: label, exact: true }).click();
  } else await nav.getByRole('button', { name: label, exact: true }).click();
}

test('cold lazy pages and repeated bottom navigation retain a usable shell', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await fixtures(page);
  await page.goto('./?page=markets&market=tw');
  const nav = page.getByRole('navigation', { name: '主要頁面' }).filter({ visible: true });
  for (const [label, route, content] of [
    ['全球', 'world', '.research-world-tabs'], ['題材', 'themes', '.theme-tile'],
    ['選股', 'screener', '.screener-row'], ['台股', 'markets', '.index-card'],
    ['選股', 'screener', '.screener-row'], ['題材', 'themes', '.theme-tile'],
    ['全球', 'world', '.research-world-tabs'],
  ]) {
    await openDestination(page, nav, label!);
    await expect(page.locator('.site')).toHaveAttribute('data-page', route!);
    await expect(page.locator(content!).first()).toBeVisible();
    await expect(page.getByRole('alert').filter({ hasText: '此頁暫時無法開啟' })).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test('a missing old deployment bundle offers reload into the new deployment and leaves navigation usable', async ({ page }) => {
  await fixtures(page);
  let fail = true;
  let newDeployment = false;
  // Model an open old tab across a deployment: reload serves the new graph's
  // fresh lazy URL. WebKit retains failed module URLs even across reloads, so
  // merely making the exact old URL available again does not model a release.
  await page.route('**/assets/index-*.js', async route => {
    const response = await route.fetch();
    let body = await response.text();
    if (!fail) {
      body = body.replace(/WorldPage-[\w-]+\.js/g, match => `${match}?deployment=new`);
      newDeployment = body.includes('?deployment=new');
    }
    await route.fulfill({ response, body });
  });
  await page.route('**/assets/WorldPage-*.js', route => fail
    ? route.fulfill({ status: 404, headers: { 'cache-control': 'no-store' }, body: 'temporary bundle unavailable' }) : route.continue());
  await page.goto('./?page=markets&market=tw');
  const nav = page.getByRole('navigation', { name: '主要頁面' }).filter({ visible: true });
  await openDestination(page, nav, '全球');
  const fallback = page.getByRole('alert').filter({ hasText: '此頁暫時無法開啟' });
  await expect(fallback).toBeVisible();
  await nav.getByRole('button', { name: '選股', exact: true }).click();
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await openDestination(page, nav, '全球');
  await expect(fallback).toBeVisible();
  fail = false;
  await fallback.getByRole('button', { name: '重新載入', exact: true }).click();
  await expect.poll(() => newDeployment).toBe(true);
  await expect(page.locator('.research-world-tabs')).toBeVisible();
  await expect(fallback).toHaveCount(0);
});
