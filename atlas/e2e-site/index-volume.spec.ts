import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mockSite } from './mocks';

const panel = (page: Page) => page.getByRole('region', { name: '多頭末期警示', exact: true });
const sample = JSON.parse(readFileSync(resolve(import.meta.dirname, '../tests/fixtures/market-warnings.sample.json'), 'utf8'));
async function fixtures(page: Page, raw: unknown = sample, status = 200) {
  await mockSite(page);
  await page.route('**/report/market-warnings.json', route => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(raw) }));
}
test('three warning charts replace short-window volume ratios, default to 60 days and remain usable on phones', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await fixtures(page);
  await page.goto('./?page=markets&market=tw');
  const card = panel(page);
  await expect(card.getByLabel('警示價量窗口')).toHaveValue('60');
  await expect(card.getByLabel('警示指數')).toHaveValue('^TWII');
  await expect(card.locator('.mw-plot')).toHaveCount(3);
  await expect(card.locator('.mw-plot canvas')).toHaveCount(21);
  await expect(card.locator('.mw-score')).toContainText('2 項亮燈');
  await expect(card.locator('.mw-values dd').first()).toHaveText('40.0%');
  await expect(card).toContainText('僅台股有歷史參考價值');
  await expect(card).toContainText('事後標註');
  expect(await page.locator('.markets-side > section.card').evaluateAll(nodes => nodes.slice(0,3).map(n => n.className)))
    .toEqual(['card market-warning-card','card breakout-card','card breadth-card']);
  await card.getByLabel('警示價量窗口').selectOption('5');
  await expect(card.getByRole('heading', { name: /指數價量/ })).toContainText('每週取一次');
  await expect(card.locator('.mw-values dd').first()).toHaveText('42.0%');
  await card.getByLabel('警示價量窗口').selectOption('10');
  await expect(card.getByRole('heading', { name: /指數價量/ })).toContainText('每週取一次');
  await card.getByLabel('警示價量窗口').selectOption('20');
  await expect(card.getByRole('heading', { name: /指數價量/ })).not.toContainText('每週取一次');
  await card.getByText('計算說明', { exact: true }).click();
  for (const text of ['存活者偏差','p≈0.07','2005–2026','5.6 降到 2.8']) await expect(card).toContainText(text);
  await card.getByText('計算說明', { exact: true }).click();
  for (const control of await card.locator('select').all()) expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  for (const plot of await card.locator('.mw-plot').all()) {
    const bounds = (await plot.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await plot.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
  }
  await page.setViewportSize({ width: page.viewportSize()!.width, height: 2100 });
  await card.scrollIntoViewIfNeeded();
  // Keep the real sticky market navigator above the card, instead of capturing
  // it over the heading. Only adjust the test's native scroller, not page styles.
  await card.evaluate(node => {
    const nav = document.querySelector('.market-section-nav');
    const offset = (nav?.getBoundingClientRect().bottom ?? 0) + 12 - node.getBoundingClientRect().top;
    for (let parent = node.parentElement; parent && offset > 0; parent = parent.parentElement) {
      if (parent.scrollHeight > parent.clientHeight && ['auto', 'scroll'].includes(getComputedStyle(parent).overflowY)) {
        parent.scrollTop -= offset;
        break;
      }
    }
  });
  await card.screenshot({ path: info.outputPath('market-warnings.png') });
  expect(errors).toEqual([]);
});
test('US volume warning is grey and SOX uses labelled SOXX volume while other index values stay separate', async ({ page }) => {
  await fixtures(page); await page.goto('./?page=markets&market=us');
  const card = panel(page);
  await expect(card.locator('.mw-lights li').first()).toHaveAttribute('data-state','na');
  await expect(card).toContainText('不適用（無歷史驗證）');
  await card.getByLabel('警示指數').selectOption('^SOX');
  await expect(card).toContainText('成交量代理 SOXX');
  await expect(card.locator('.mw-values dd').first()).toHaveText('42.0%');
  await card.getByLabel('警示指數').selectOption('^GSPC');
  await expect(card.locator('.mw-values dd').first()).toHaveText('40.0%');
  await expect(card).not.toContainText('成交量代理');
});
test('invalid or missing warnings never retain old numbers and explicit retry recovers', async ({ page }) => {
  await fixtures(page, { ...sample, version: 999 }); await page.goto('./?page=markets&market=tw');
  const card = panel(page);
  await expect(card.getByRole('status')).toContainText('格式無效');
  await expect(card.locator('.mw-values')).toHaveCount(0);
  await page.route('**/report/market-warnings.json', route => route.fulfill({ status:503,body:'offline' }));
  await card.getByRole('button', { name:'重試警示資料' }).click();
  await expect(card.getByRole('status')).toContainText('HTTP 503');
  await page.route('**/report/market-warnings.json', route => route.fulfill({ json:sample }));
  await card.getByRole('button', { name:'重試警示資料' }).click();
  await expect(card.locator('.mw-score')).toContainText('2 項亮燈');
});
