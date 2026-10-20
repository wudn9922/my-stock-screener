import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSite } from './mocks';

/**
 * P/E river card on the financials page. Financials come from the repo's real report files
 * (docs/report/financials), prices from the synthetic 10-year weekly mock — so assertions are about
 * structure and rules, never about particular multiples.
 */
const reportDir = resolve(import.meta.dirname, '../../docs/report/financials');
const body = (key: string) => (existsSync(resolve(reportDir, `${key}.json`)) ? readFileSync(resolve(reportDir, `${key}.json`), 'utf8') : null);
const syntheticTaiwanNoEps = (() => {
  const fixture = JSON.parse(readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials/tsmc-twd.json'), 'utf8')) as {
    symbol: string;
    annual: { values: Record<string, (number | null)[]> };
    quarterly: { values: Record<string, (number | null)[]> };
  };
  fixture.symbol = '1312.TW';
  for (const series of [fixture.annual, fixture.quarterly]) {
    for (const field of ['epsDiluted', 'epsBasic']) {
      series.values[field] = series.values[field]!.map(() => null);
    }
  }
  return JSON.stringify(fixture);
})();

async function mockPeRiver(page: Page, options: Parameters<typeof mockSite>[1] = {}, financials: Record<string, string> = {}) {
  await mockSite(page, options);
  await page.route('**/my-stock-screener/report/financials/*.json', (route) => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!.replace(/\.json$/, ''));
    const text = financials[key] ?? body(key);
    return text ? route.fulfill({ status: 200, contentType: 'application/json', body: text }) : route.fulfill({ status: 404, body: 'not found' });
  });
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => {
    const main = document.getElementById('main')!;
    return Math.max(document.scrollingElement!.scrollWidth - innerWidth, main.scrollWidth - main.clientWidth);
  });
const card = (page: Page) => page.locator('.pe-river');
/** The card is folded by default; its summary line is the toggle. An open card is left alone. */
async function openCard(section: Locator) {
  if (!(await section.evaluate((node) => (node as HTMLDetailsElement).open))) await section.locator(':scope > summary').click();
}
/** Element screenshots would include the sticky header and tab bar on top of the card. */
const unstick = (page: Page) => page.addStyleTag({ content: '.fin-head{position:static!important}.bottom-nav,nav[aria-label]{display:none!important}' });

test('US stock: annual-only river with five labelled multiples, percentile, basis block, no horizontal scroll', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockPeRiver(page);
  await page.goto('./?page=financials&symbol=AAPL');
  await openCard(card(page));
  await expect(card(page)).toHaveAttribute('data-state', 'ok');
  await expect(card(page).locator('h2')).toHaveText('本益比河流圖');
  // Folded head: the current multiple and its percentile in one line (before the chart mounts).
  await expect(card(page).locator('.fin-fold-summary')).toContainText(/目前本益比 [\d.]+x · 歷史第 \d+ 百分位/);
  await expect(card(page).locator('.fin-big-unit')).toHaveText('目前本益比');
  await expect(card(page).locator('.fin-period')).toContainText('年度');
  await expect(card(page).locator('.fin-period')).not.toContainText('TTM');
  await expect(card(page).locator('.fin-chip').first()).toContainText(/歷史第 \d+ 百分位/);
  await expect(card(page).locator('.fin-chip').nth(1)).toContainText(/相對歷史中位數（[\d.]+x）(溢價|折價)/);
  await expect(card(page).locator('.pe-river-legend li')).toHaveCount(5);
  await expect(card(page).locator('.pe-river-chart canvas').first()).toBeVisible();
  const basis = card(page).locator('.pe-river-basis');
  await expect(basis).toContainText('資料日');
  await expect(basis).toContainText('年度稀釋 EPS');
  await expect(basis).toContainText('有效樣本');
  await expect(basis).toContainText('涵蓋');
  await expect(card(page)).toContainText('歷史本益比不代表未來，僅供研究參考');
  await expect(page.locator('.fin-card[data-group="growth"]')).toBeVisible();
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  // The chart (and its right-hand axis) stays inside the card / viewport.
  const box = (await card(page).locator('.pe-river-chart').boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 0.5);
  // The crosshair readout shows date, price and P/E.
  await card(page).locator('.pe-river-chart').scrollIntoViewIfNeeded();
  const at = (await card(page).locator('.pe-river-chart').boundingBox())!;
  // Touch devices only get the crosshair on long-press, which is the library's gesture; hover is checked on desktop.
  if (info.project.name !== 'mobile') {
    await page.mouse.move(at.x + at.width * 0.8, at.y + at.height * 0.5);
    await expect(card(page).locator('.pe-river-readout')).toContainText(/\d{4}-\d{2}-\d{2} · 股價/);
  }
  await unstick(page);
  await card(page).scrollIntoViewIfNeeded();
  await card(page).screenshot({ path: info.outputPath(`pe-river-aapl-${info.project.name}.png`) });
  expect(errors).toEqual([]);
});

test('NVDA (split-adjusted file): river is drawn and split-adjustment is stated', async ({ page }, info) => {
  await mockPeRiver(page);
  await page.goto('./?page=financials&symbol=NVDA');
  await openCard(card(page));
  await expect(card(page)).toHaveAttribute('data-state', 'ok');
  await expect(page.locator('.fin-notes')).toContainText('已依股票分割還原');
  await expect(card(page).locator('.pe-river-basis')).toContainText('已還原分割');
  await unstick(page);
  await card(page).scrollIntoViewIfNeeded();
  await card(page).screenshot({ path: info.outputPath(`pe-river-nvda-${info.project.name}.png`) });
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('Taiwan 2330: current P/E uses the latest trailing-four-quarter EPS', async ({ page }, info) => {
  await mockPeRiver(page);
  await page.goto('./?page=financials&symbol=2330.TW');
  await openCard(card(page));
  await expect(card(page)).toHaveAttribute('data-state', 'ok');
  await expect(card(page).locator('.fin-period')).toContainText('TTM');
  await expect(card(page).locator('.pe-river-basis')).toContainText('TTM');
  await unstick(page);
  await card(page).scrollIntoViewIfNeeded();
  await card(page).screenshot({ path: info.outputPath(`pe-river-2330-${info.project.name}.png`) });
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('Taiwan synthetic file without EPS shows the empty state, not a chart', async ({ page }, info) => {
  await mockPeRiver(page, {}, { '1312.TW': syntheticTaiwanNoEps });
  await page.goto('./?page=financials&symbol=1312.TW');
  await openCard(card(page));
  await expect(card(page)).toHaveAttribute('data-state', 'unavailable');
  await expect(card(page)).toContainText('沒有可用的 EPS 資料，無法畫出本益比河流');
  await expect(card(page)).toContainText('歷史本益比不代表未來');
  await expect(card(page).locator('canvas')).toHaveCount(0);
  await unstick(page);
  await card(page).screenshot({ path: info.outputPath(`pe-river-1312-${info.project.name}.png`) });
  // The rest of the page is unaffected.
  await expect(page.locator('.fin-card[data-group="growth"]')).toBeVisible();
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('loading placeholder first, and a price failure is local to the card with a working retry', async ({ page }) => {
  await mockPeRiver(page, { failingSymbols: ['AAPL'] });
  let release!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  await page.route('https://edge.test/functions/v1/market-chart**', async (route) => {
    await responseGate;
    await route.fallback();
  });
  await page.goto('./?page=financials&symbol=AAPL');
  try {
    await openCard(card(page));
    await expect(card(page)).toHaveAttribute('data-state', 'loading');
    await expect(card(page).getByRole('status', { name: /載入股價與本益比河流中/ })).toBeVisible();
  } finally { release(); }
  await expect(card(page)).toHaveAttribute('data-state', 'error');
  await expect(card(page)).toContainText('股價資料載入失敗');
  await expect(card(page).getByRole('button', { name: '重新載入' })).toBeVisible();
  await expect(page.locator('.fin-card[data-group="growth"]')).toBeVisible();
  await expect(page.locator('.fin-title')).toContainText('AAPL');
});
