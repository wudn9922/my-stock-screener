import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSite } from './mocks';

/** Synthetic financials files (made-up numbers in the builder's format), keyed by file name. */
const fixtures: Record<string, string> = Object.fromEntries(
  [
    ['AAPL', 'us-full.json'],
    ['BRK-B', 'us-full.json'],
    ['2330.TW', 'tsmc-twd.json'],
    ['JPM', 'bank.json'],
    ['ZLOS', 'loss-maker.json'],
    ['NVO', 'short-history.json'],
    ['TSM', 'tsmc-twd.json'],
  ].map(([key, file]) => [key, readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials', file!), 'utf8')]),
);
const trackedAAPL = readFileSync(resolve(import.meta.dirname, '../../docs/report/financials/AAPL.json'), 'utf8');
const syntheticTaiwan60 = (() => {
  const fixture = JSON.parse(fixtures['2330.TW']!) as {
    quarterly: { end: string[]; fy: number[]; fq: number[]; values: Record<string, (number | null)[]> };
    quality: { quarters: number };
  };
  const quarterIndex = 2010 * 4 + 2; // 2010 Q3 through 2025 Q2: 60 fiscal quarters.
  fixture.quarterly.end = Array.from({ length: 60 }, (_, index) => {
    const fiscalQuarterIndex = quarterIndex + index;
    const year = Math.floor(fiscalQuarterIndex / 4);
    const quarter = (fiscalQuarterIndex % 4) + 1;
    return new Date(Date.UTC(year, quarter * 3, 0)).toISOString().slice(0, 10);
  });
  fixture.quarterly.fy = fixture.quarterly.end.map((end) => new Date(`${end}T00:00:00Z`).getUTCFullYear());
  fixture.quarterly.fq = Array.from({ length: 60 }, (_, index) => ((quarterIndex + index) % 4) + 1);
  for (const [field, values] of Object.entries(fixture.quarterly.values)) {
    fixture.quarterly.values[field] = Array.from({ length: 60 }, (_, index) => values[index % values.length] ?? null);
  }
  fixture.quality.quarters = 60;
  return JSON.stringify(fixture);
})();
const mutateAaplFixture = (mutate: (fixture: { annual: { end: string[]; fy: (number | null)[]; fq: (number | null)[]; src: string[]; values: Record<string, (number | null)[]> }; quality: { issues: string[] } }) => void) => {
  const fixture = JSON.parse(fixtures.AAPL!) as { annual: { end: string[]; fy: (number | null)[]; fq: (number | null)[]; src: string[]; values: Record<string, (number | null)[]> }; quality: { issues: string[] } };
  mutate(fixture);
  return JSON.stringify(fixture);
};
const annualEpsMissing = mutateAaplFixture((fixture) => {
  fixture.annual.values.epsDiluted = fixture.annual.end.map(() => null);
  fixture.annual.values.epsBasic = fixture.annual.end.map(() => null);
});
const annualEpsSummed = mutateAaplFixture((fixture) => {
  fixture.quality.issues = [...fixture.quality.issues, 'eps-annual-summed'];
});
const noAnnualRows = mutateAaplFixture((fixture) => {
  fixture.annual.end = [];
  fixture.annual.fy = [];
  fixture.annual.fq = [];
  fixture.annual.src = [];
  for (const field of Object.keys(fixture.annual.values)) fixture.annual.values[field] = [];
});

async function mockFinancials(
  page: Page,
  overrides: Record<string, 'missing' | 'garbled'> = {},
  trackedFiles: Record<string, string> = {},
) {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/financials/*.json', (route) => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!.replace(/\.json$/, ''));
    if (overrides[key] === 'garbled') return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>x</title>' });
    const body = trackedFiles[key] ?? fixtures[key];
    if (!body || overrides[key] === 'missing') return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({ status: 200, contentType: 'application/json', body });
  });
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => {
    const main = document.getElementById('main')!;
    return Math.max(document.scrollingElement!.scrollWidth - innerWidth, main.scrollWidth - main.clientWidth);
  });

/** Folded sections open on their summary line; a section that is already open is left alone. */
async function openFold(section: Locator) {
  if (!(await section.evaluate((node) => (node as HTMLDetailsElement).open))) await section.locator(':scope > summary').click();
}

test('annual cards: ten labelled bars, tap shows formula inputs, notes are honest', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await expect(page.locator('.fin-title')).toContainText('AAPL');
  await expect(page.locator('.fin-title')).toContainText('USD');
  await expect(page.locator('.fin-card')).toHaveCount(6);
  const growth = page.locator('.fin-card[data-group="growth"]');
  // EPS leads the growth card; the revenue bars (億美元) are the amounts these checks read.
  await expect(growth.getByRole('button', { name: 'EPS', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await growth.getByRole('button', { name: '營收', exact: true }).click();
  await expect(growth.locator('.fin-bar')).toHaveCount(10);
  await expect(growth.locator('.fin-value')).toHaveCount(10);
  await expect(growth.locator('.fin-chart-title')).toContainText('億美元');
  await expect(page.locator('.fin-depth')).toContainText('之後每季自動累積');
  await expect(page.locator('.fin-notes')).toContainText('SEC EDGAR');
  await expect(page.locator('.fin-notes')).toContainText('資料更新日');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  // Labels must not overlap at phone width.
  const boxes = await growth.locator('.fin-value').evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
  for (let i = 1; i < boxes.length; i++) expect(boxes[i].left).toBeGreaterThanOrEqual(boxes[i - 1].right - 0.5);

  await growth.getByRole('button', { name: /^FY2016 營收/ }).click();
  const detail = growth.locator('.fin-detail');
  await expect(detail).toContainText('FY2016');
  await expect(detail).toContainText('2016/09/24');
  await expect(detail).toContainText('SEC');

  const profit = page.locator('.fin-card[data-group="profitability"]');
  await profit.getByRole('button', { name: '營業利益率', exact: true }).click();
  await expect(profit.locator('.fin-sub')).toContainText('優先採列示欄');
  await expect(profit.locator('.fin-detail')).toContainText('列示營業利益欄位 ÷ 營收');
  await profit.getByRole('button', { name: /^FY2015 營業利益率/ }).click();
  await expect(profit.locator('.fin-detail')).toContainText('營業利益欄位');
  expect(errors).toEqual([]);
});

test('US financials retain quarterly figures and safe web and app links', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=AAPL&p=q');
  await expect(page).toHaveURL(/page=financials&symbol=AAPL&p=q$/);
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toHaveCount(1);
  const growth = page.locator('.fin-card[data-group="growth"]');
  await expect(growth.locator('.fin-bar')).toHaveCount(15);
  const webLink = page.getByRole('link', { name: '在富途網頁查看財報', exact: true });
  await expect(webLink).toHaveAttribute('href', 'https://www.futunn.com/stock/AAPL-US');
  await expect(webLink).toHaveAttribute('target', '_blank');
  await expect(webLink).toHaveAttribute('rel', 'noopener noreferrer');
  const appLink = page.getByRole('link', { name: '在富途 App 開啟', exact: true });
  await expect(appLink).toHaveAttribute('href', 'ftnn://quote/2/AAPL/');
  expect(await appLink.getAttribute('target')).toBeNull();
  await expect(page.locator('.financials-page a[href^="https://www.futunn.com/"]')).toHaveCount(1);
  await expect(page.locator('.financials-page a[href^="ftnn://"]')).toHaveCount(1);

  await page.goto('./?page=financials&symbol=BRK.B');
  await expect(page.getByRole('link', { name: '在富途網頁查看財報', exact: true })).toHaveAttribute(
    'href',
    'https://www.futunn.com/stock/BRK.B-US',
  );
  await expect(page.getByRole('link', { name: '在富途 App 開啟', exact: true })).toHaveAttribute('href', 'ftnn://quote/2/BRK.B/');

  await page.goto('./?page=financials&symbol=BRK-B');
  await expect(page.getByRole('link', { name: '在富途網頁查看財報', exact: true })).toHaveAttribute(
    'href',
    'https://www.futunn.com/stock/BRK-B-US',
  );
  await expect(page.getByRole('link', { name: '在富途 App 開啟', exact: true })).toHaveAttribute('href', 'ftnn://quote/2/BRK-B/');
});

test('Taiwan quarterly view keeps sparse labels and trailing-four-quarter ROE', async ({ page }) => {
  await mockFinancials(page, {}, { '2330.TW': syntheticTaiwan60 });
  await page.goto('./?page=financials&symbol=2330.TW');
  await page.getByRole('group', { name: '期間' }).getByRole('button', { name: '季度' }).click();
  await expect(page).toHaveURL(/page=financials&symbol=2330\.TW&p=q/);
  const growth = page.locator('.fin-card[data-group="growth"]');
  await expect(growth.locator('.fin-bar')).toHaveCount(20);
  expect(await growth.locator('.fin-value').count()).toBeLessThanOrEqual(3);
  await expect(page.locator('.fin-depth')).toContainText('目前 60 季');
  const returns = page.locator('.fin-card[data-group="returns"]');
  await openFold(returns);
  await expect(returns.locator('.fin-detail')).toContainText('近 4 季');
});

test('missing annual EPS stays as a dash when quarterly EPS exists', async ({ page }) => {
  await mockFinancials(page, {}, { AAPL: annualEpsMissing });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toHaveCount(1);
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const epsRow = page.locator('.fin-table tbody tr').filter({ hasText: /^EPS/ });
  await expect(epsRow.locator('.fin-cell-value')).toHaveCount(10);
  await expect(epsRow.locator('.fin-cell-value')).toHaveText(Array(10).fill('—'));
  const keyMetrics = page.locator('.fin-key');
  await openFold(keyMetrics);
  await expect(keyMetrics.locator('.fin-key-tile').nth(3).locator('.fin-key-value')).toHaveText('—');
});

test('US quarterly-only files remain available when annual figures are absent', async ({ page }) => {
  await mockFinancials(page, {}, { AAPL: noAnnualRows });
  await page.goto('./?page=financials&symbol=AAPL&p=q');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toBeVisible();
  await expect(page.locator('.fin-card[data-group="growth"] .fin-bar')).toHaveCount(15);
  await expect(page.locator('.fin-depth')).toContainText('15 季');
});

test('US annual EPS marked as quarter-summed is hidden from statements and P/E', async ({ page }) => {
  await mockFinancials(page, {}, { AAPL: annualEpsSummed });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const epsRow = page.locator('.fin-table tbody tr').filter({ hasText: /^EPS/ });
  await expect(epsRow.locator('.fin-cell-value')).toHaveText(Array(10).fill('—'));
  const river = page.locator('.pe-river');
  await openFold(river);
  await expect(river).toHaveAttribute('data-state', 'unavailable');
  await expect(river.locator('.pe-river-empty')).toHaveAttribute('data-reason', 'no-eps');
  await expect(river).not.toContainText('TTM');
});

test('table mode: frozen metric column, latest period first, horizontal scroll inside the card', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const table = page.locator('.fin-table');
  await expect(table).toBeVisible();
  await expect(table.locator('thead th').nth(1)).toContainText('FY2024');
  await expect(table.locator('thead th').last()).toContainText('FY2015');
  await expect(table.locator('.fin-source-row td').first()).toHaveText('Yahoo');
  await expect(table.locator('.fin-source-row td').last()).toHaveText('SEC');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  const scroller = page.locator('.fin-table-scroll');
  const width = await scroller.evaluate((node) => ({ scroll: node.scrollWidth, client: node.clientWidth }));
  if (width.scroll > width.client) {
    const before = await table.locator('tbody th').first().boundingBox();
    await scroller.evaluate((node) => node.scrollBy(200, 0));
    const after = await table.locator('tbody th').first().boundingBox();
    expect(after!.x).toBeCloseTo(before!.x, 0);
  }
  // The view choice is remembered.
  await page.reload();
  await expect(page.locator('.fin-table')).toBeVisible();
});

test('tracked US file exposes all annual periods and retains the quarterly option', async ({ page }) => {
  await mockFinancials(page, {}, { AAPL: trackedAAPL });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toHaveCount(1);
  const table = page.locator('.fin-table');
  const annualHeaders = table.locator('thead th');
  expect(await annualHeaders.count()).toBeGreaterThanOrEqual(16);
  await annualHeaders.nth(15).scrollIntoViewIfNeeded();
  await expect(annualHeaders.nth(15)).toContainText(/^FY\d{4}/);
  await expect(page.locator('.fin-table-note')).toContainText('所有已取得期別');
});

test('Taiwan synthetic file exposes the oldest of 60 quarters inside the scrollable table', async ({ page }) => {
  await mockFinancials(page, {}, { '2330.TW': syntheticTaiwan60 });
  await page.goto('./?page=financials&symbol=2330.TW&p=q');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const table = page.locator('.fin-table');
  const quarterlyHeaders = table.locator('thead th');
  expect(await quarterlyHeaders.count()).toBeGreaterThanOrEqual(61);
  await quarterlyHeaders.nth(60).scrollIntoViewIfNeeded();
  await expect(quarterlyHeaders.nth(60)).toContainText(/Q[1-4]/);
  const reachable = await quarterlyHeaders.nth(60).evaluate((node) => {
    const cell = node.getBoundingClientRect();
    const viewport = node.closest('.fin-table-scroll')!.getBoundingClientRect();
    return cell.right > viewport.left && cell.left < viewport.right;
  });
  expect(reachable).toBe(true);
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('bank, loss-maker, Taiwan and short-history files', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=JPM&p=a');
  await expect(page.locator('.fin-quality')).toHaveText('部分缺漏');
  const profit = page.locator('.fin-card[data-group="profitability"]');
  await expect(profit.locator('.fin-card-note')).toContainText('金融業');
  // The card opens on the first metric that has data (net margin); gross margin explains why it is empty.
  await expect(profit.getByRole('button', { name: '淨利率', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await profit.getByRole('button', { name: '毛利率', exact: true }).click();
  await expect(profit.locator('.fin-empty')).toContainText('金融業');
  await expect(profit.locator('.fin-big')).toHaveCount(0);
  await page.locator('.fin-quality').click();
  await expect(page.locator('.fin-quality-panel')).toContainText('金融業財報格式');

  await page.goto('./?page=financials&symbol=ZLOS&p=a');
  const growth = page.locator('.fin-card[data-group="growth"]');
  await growth.getByRole('button', { name: '淨利', exact: true }).click();
  await expect(growth.locator('.fin-bar.negative')).toHaveCount(4);
  await expect(growth.locator('.fin-chips')).toContainText('轉盈');
  await expect(growth.locator('.fin-chips')).toContainText('期間有虧損');
  const tax = page.locator('.fin-card[data-group="nonOperating"]');
  await openFold(tax);
  await tax.getByRole('button', { name: '有效稅率', exact: true }).click();
  await expect(tax.locator('.fin-chips')).toContainText('異常');

  await page.goto('./?page=financials&symbol=2330.TW');
  await expect(page.locator('.fin-title')).toContainText('台積電');
  await page.locator('.fin-card[data-group="growth"]').getByRole('button', { name: '營收', exact: true }).click();
  await expect(page.locator('.fin-card[data-group="growth"] .fin-chart-title')).toContainText('兆元');
  await expect(page.locator('.fin-depth')).toContainText('目前約 4 年');

  await page.goto('./?page=financials&symbol=NVO&p=a');
  await expect(page.locator('.fin-quality')).toHaveText('資料有限');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-card[data-group="growth"] .fin-card-note')).toContainText('非每 ADR');
});

test('missing, garbled and index symbols', async ({ page }) => {
  await mockFinancials(page, { AAPL: 'garbled' });
  await page.goto('./?page=financials&symbol=MSFT');
  await expect(page.getByText('財報資料尚未產生', { exact: true })).toBeVisible();
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await expect(page.getByText('財報資料無法辨識', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '重新載入' })).toBeVisible();
  await page.goto('./?page=financials&symbol=%5EGSPC');
  await expect(page.getByText('沒有個股財報', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '在富途網頁查看財報' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '在富途 App 開啟' })).toHaveCount(0);
  await page.goto('./?page=financials');
  await expect(page.getByText('選擇一檔股票', { exact: true })).toBeVisible();
});

test('back to the chart and switching stocks preserves the selected quarterly period', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=AAPL&p=q');
  await expect(page).toHaveURL(/page=financials&symbol=AAPL&p=q$/);
  await page.getByRole('button', { name: '搜尋其他股票' }).click();
  await page.locator('.fin-search input').fill('2330');
  await page.locator('.fin-search .search-results li').filter({ hasText: '台積電' }).first().click();
  await expect(page).toHaveURL(/symbol=2330\.TW&p=q$/);
  await expect(page.locator('.fin-title')).toContainText('2330');
  await expect(page.getByRole('group', { name: '期間' }).getByRole('button', { name: '季度' })).toHaveAttribute('aria-pressed', 'true');
  // 「K線」 replaces the old back button: same destination, same symbol.
  await page.getByRole('navigation', { name: '個股頁面' }).getByRole('button', { name: 'K線', exact: true }).click();
  await expect(page).toHaveURL(/page=chart&symbol=2330\.TW/);
});

test('Taiwan financials retain the quarterly controls and do not show a Futu link', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=2330.TW&p=q');
  const period = page.getByRole('group', { name: '期間' });
  await expect(period.getByRole('button', { name: '季度' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.fin-quarter')).toBeVisible();
  await expect(page.getByRole('link', { name: '在富途網頁查看財報' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '在富途 App 開啟' })).toHaveCount(0);
});

const stockTabs = (page: Page) => page.getByRole('navigation', { name: '個股頁面' });

test('K線｜財務 keeps the symbol, timeframe and quarterly selection', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockFinancials(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1W');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ', { timeout: 20000 });
  const tabs = stockTabs(page);
  const chartTab = tabs.getByRole('button', { name: 'K線', exact: true });
  const financialsTab = tabs.getByRole('button', { name: '財務', exact: true });
  await expect(chartTab).toHaveAttribute('aria-current', 'page');
  await expect(financialsTab).not.toHaveAttribute('aria-current', 'page');
  // Touch screens: a 36px-plus target; desktop pointers get the compact one. Both stay on screen.
  const phone = (page.viewportSize()?.width ?? 1440) < 1100;
  for (const tab of [chartTab, financialsTab]) {
    const box = await tab.boundingBox();
    if (phone) expect(box!.height).toBeGreaterThanOrEqual(36);
    else expect(box!.height).toBeLessThanOrEqual(34);
    await expect(tab).toBeInViewport();
  }

  await financialsTab.click();
  await expect(page).toHaveURL(/page=financials&symbol=AAPL&tf=1W$/);
  await expect(page.locator('.fin-title')).toContainText('AAPL');
  await expect(financialsTab).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await page.goto('./?page=financials&symbol=AAPL&tf=1W&p=q');
  await expect(page).toHaveURL(/page=financials&symbol=AAPL&tf=1W&p=q$/);

  await tabs.getByRole('button', { name: 'K線', exact: true }).click();
  await expect(page).toHaveURL(/page=chart&symbol=AAPL&tf=1W$/);
  await expect(page.getByTestId('active-symbol')).toHaveText('AAPL');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ', { timeout: 20000 });

  // Returning to financials keeps the chart timeframe and still shows annual figures.
  await stockTabs(page).getByRole('button', { name: '財務', exact: true }).click();
  await expect(page).toHaveURL(/page=financials&symbol=AAPL&tf=1W&p=q$/);
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('key metrics card: seven tiles on the newest period, with YoY and eight mini bars', async ({ page }) => {
  await mockFinancials(page, {}, { AAPL: trackedAAPL });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  const key = page.locator('.fin-key');
  await openFold(key);
  await expect(key.locator('.fin-key-tile')).toHaveCount(7);
  await expect(key.locator('.fin-key-period')).toContainText('FY2025');
  const labels = await key.locator('.fin-key-label').allTextContents();
  expect(labels).toEqual(['營收', '毛利', '淨利', 'EPS', '毛利率', '營業利益率', '淨利率']);
  await expect(key.locator('.fin-key-tile').first().locator('.fin-chip')).toContainText('年增');
  await expect(key.locator('.fin-key-tile').nth(4).locator('.fin-chip')).toContainText('百分點');
  await expect(key.locator('.fin-spark')).toHaveCount(7);
  expect(await key.locator('.fin-spark-bar').count()).toBeGreaterThanOrEqual(40);
  expect(await key.textContent()).not.toContain('NaN');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('key metrics: a bank shows gross profit as not applicable, not as a number', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=JPM&p=a');
  const gross = page.locator('.fin-key-tile').filter({ hasText: '毛利' }).filter({ hasNotText: '毛利率' });
  await expect(gross.locator('.fin-key-reason')).toContainText('不適用（金融業不計毛利）');
  await expect(gross.locator('.fin-key-value')).toHaveText('—');
});

test('YoY line over money and EPS bars only, and the chart range follows the file length', async ({ page }) => {
  await mockFinancials(page, {}, { AAPL: trackedAAPL });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  const growth = page.locator('.fin-card[data-group="growth"]');
  await expect(growth.locator('.fin-yoy-line')).toHaveCount(1);
  expect(await growth.locator('.fin-yoy-dot').count()).toBeGreaterThanOrEqual(9);
  await expect(growth.locator('.fin-legend')).toContainText('年增率（右軸，%）');
  await expect(growth.locator('.fin-bar')).toHaveCount(10);

  const profit = page.locator('.fin-card[data-group="profitability"]');
  await profit.getByRole('button', { name: '毛利率', exact: true }).click();
  await expect(profit.locator('.fin-yoy-line')).toHaveCount(0);
  await profit.getByRole('button', { name: '毛利', exact: true }).click();
  await expect(profit.locator('.fin-yoy-line')).toHaveCount(1);

  const range = page.getByRole('group', { name: '圖表區間' });
  await expect(range.getByRole('button', { name: '10年', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(range.getByRole('button', { name: '15年', exact: true })).toBeEnabled();
  await range.getByRole('button', { name: '5年', exact: true }).click();
  await expect(growth.locator('.fin-bar')).toHaveCount(5);
  await expect(page.locator('.fin-range-note')).toContainText('顯示近 5 年');
  await range.getByRole('button', { name: '15年', exact: true }).click();
  await expect(growth.locator('.fin-bar')).toHaveCount(15);
  await growth.locator('.fin-bar').nth(0).click();
  await expect(growth.locator('.fin-detail')).toContainText('YoY');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toHaveCount(1);
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('Taiwan synthetic 60-quarter history enables the full chart range', async ({ page }) => {
  await mockFinancials(page, {}, { '2330.TW': syntheticTaiwan60 });
  await page.goto('./?page=financials&symbol=2330.TW&p=q');
  const quarterRange = page.getByRole('group', { name: '圖表區間' });
  await expect(quarterRange.getByRole('button', { name: '20季', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(quarterRange.getByRole('button', { name: '60季', exact: true })).toBeEnabled();
  await quarterRange.getByRole('button', { name: '60季', exact: true }).click();
  await expect(page.locator('.fin-card[data-group="growth"] .fin-bar')).toHaveCount(60);
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('a short file disables the ranges it cannot fill', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=2330.TW');
  const range = page.getByRole('group', { name: '圖表區間' });
  await expect(range.getByRole('button', { name: '5年', exact: true })).toBeDisabled();
  await expect(range.getByRole('button', { name: '10年', exact: true })).toBeDisabled();
  await expect(page.locator('.fin-range-note')).toContainText('已全部顯示');
  await expect(page.locator('.fin-card[data-group="growth"] .fin-bar')).toHaveCount(4);
});

test('three statements: lines switch between values and YoY, absent lines say 此資料源未提供', async ({ page }) => {
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const report = page.getByRole('group', { name: '報表' });
  await expect(report.getByRole('button', { name: '損益表' })).toHaveAttribute('aria-pressed', 'true');
  const table = page.locator('.fin-table');
  await expect(table.locator('tbody th').first()).toContainText('營收');

  await report.getByRole('button', { name: '資產負債' }).click();
  await expect(report.getByRole('button', { name: '資產負債' })).toHaveAttribute('aria-pressed', 'true');
  const liabilities = table.locator('tbody tr').filter({ hasText: '總負債' });
  await expect(liabilities).toContainText('此資料源未提供');
  await expect(liabilities.locator('td').first().locator('.fin-cell-value')).toHaveText('—');

  // Every cell carries its year-over-year change under the value; one switch shows or hides them all.
  const equity = table.locator('tbody tr').filter({ hasText: '權益' });
  await expect(equity.locator('td').first().locator('.fin-cell-yoy')).toHaveCount(1);
  const toggle = page.getByRole('button', { name: '顯示年增率' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.click();
  await expect(table.locator('.fin-cell-yoy')).toHaveCount(0);
  await toggle.click();
  await expect(table.locator('.fin-cell-yoy').first()).toBeVisible();

  await report.getByRole('button', { name: '現金流' }).click();
  await expect(table.locator('tbody tr').filter({ hasText: '資本支出' })).toBeVisible();
  await expect(table.locator('tbody tr').filter({ hasText: '庫藏股' })).toBeVisible();
  await expect(page.locator('.fin-table-note')).toContainText('所有已取得期別');
  await expect(table.locator('.fin-source-row td').last()).toHaveText('SEC');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
});

test('ADR page retains period controls and links to Futu and the Taiwan home listing', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=TSM&p=a');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.getByRole('link', { name: '在富途網頁查看財報', exact: true })).toHaveAttribute(
    'href',
    'https://www.futunn.com/stock/TSM-US',
  );
  await expect(page.getByRole('link', { name: '在富途 App 開啟', exact: true })).toHaveAttribute('href', 'ftnn://quote/2/TSM/');
  await expect(page.locator('.fin-home-listing')).toContainText('也可查看');
  await expect(page.locator('.fin-home-link')).toHaveText('2330.TW（台股）財報');
  await expect(page.locator('.fin-home-ratio')).toHaveText('1 ADR ＝ 5 股普通股');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await page.locator('.fin-home-link').click();
  await expect(page).toHaveURL(/symbol=2330\.TW(?:&p=[aq])?$/);
  await expect(page.locator('.fin-title')).toContainText('台積電');
  await expect(page.locator('.fin-home-link')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('foreign file with EPS on the ADR basis: the basis note replaces the per-ordinary-share remark', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockFinancials(page, {}, { TSM: readFileSync(resolve(import.meta.dirname, '../tests/fixtures/financials/adr-rescaled.json'), 'utf8') });
  await page.goto('./?page=financials&symbol=TSM&p=a');
  const notes = page.locator('.fin-notes');
  await expect(notes).toContainText('EPS 以每單位 ADR 計（1 ADR ＝ 5 股普通股，由 SEC 每股數字換算）');
  await expect(page.locator('.pe-river')).toHaveAttribute('data-state', 'unavailable');
  await expect(notes).not.toContainText('EPS 為每普通股');
  await expect(page.locator('.financials-page')).not.toContainText('EPS 為每普通股');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});

test('layout: annual growth (EPS first) and profitability (ratios first), then folded sections', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await mockFinancials(page, {}, { AAPL: trackedAAPL });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await expect(page.getByRole('group', { name: '期間' })).toHaveCount(1);
  await expect(page.locator('.fin-quarter')).toHaveCount(1);

  // Sample one layout frame so entrance animations cannot move between reads.
  const order = await page.evaluate(() => {
    const top = (query: string) => document.querySelector(query)!.getBoundingClientRect().top;
    return {
      growth: top('.fin-card[data-group="growth"]'),
      profit: top('.fin-card[data-group="profitability"]'),
      folds: top('.fin-folds'),
      notes: top('.fin-notes'),
    };
  });
  expect(order.growth).toBeLessThanOrEqual(order.profit);
  expect(order.profit).toBeLessThan(order.folds);
  expect(order.folds).toBeLessThan(order.notes);

  await expect(page.locator('.fin-card[data-group="growth"]')).toHaveAttribute('open', '');
  await expect(page.locator('.fin-card[data-group="profitability"]')).toHaveAttribute('open', '');
  expect(await page.locator('.fin-card[data-group="growth"] .fin-tabs button').allTextContents()).toEqual(['EPS', '營收', '淨利']);
  expect((await page.locator('.fin-card[data-group="profitability"] .fin-tabs button').allTextContents()).slice(0, 3)).toEqual([
    '毛利率',
    '營業利益率',
    '淨利率',
  ]);

  // Folded by default, each with a one-line summary in its head.
  for (const selector of [
    '.fin-key',
    '.fin-sumfold',
    '.pe-river',
    '.fin-card[data-group="nonOperating"]',
    '.fin-card[data-group="cashFlow"]',
    '.fin-card[data-group="returns"]',
    '.fin-card[data-group="strength"]',
  ]) {
    const section = page.locator(selector);
    await expect(section).not.toHaveAttribute('open', '');
    await expect(section.locator('.fin-fold-summary')).not.toHaveText('');
  }
  await expect(page.locator('.fin-key .fin-fold-summary')).toContainText('FY2025');

  // Opening a folded section shows its content; closing it folds the content away again.
  const key = page.locator('.fin-key');
  await openFold(key);
  await expect(key.locator('.fin-key-tile')).toHaveCount(7);
  await key.locator(':scope > summary').click();
  await expect(key).not.toHaveAttribute('open', '');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: info.outputPath(`financials-layout-${info.project.name}.png`), fullPage: true });
  expect(errors).toEqual([]);
});

test('table: each cell shows its value with the year-over-year change under it; one switch hides them and is remembered', async ({ page }, info) => {
  await mockFinancials(page, {}, { AAPL: trackedAAPL });
  await page.goto('./?page=financials&symbol=AAPL&p=a');
  await page.getByRole('group', { name: '顯示方式' }).getByRole('button', { name: '表格' }).click();
  const table = page.locator('.fin-table');
  const revenueCell = table.locator('tbody tr').filter({ hasText: '營收' }).first().locator('td').first();
  await expect(revenueCell.locator('.fin-cell-value')).not.toHaveText('—');
  await expect(revenueCell.locator('.fin-cell-yoy')).toHaveCount(1);
  const toggle = page.getByRole('button', { name: '顯示年增率' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.click();
  await expect(table.locator('.fin-cell-yoy')).toHaveCount(0);
  await expect(revenueCell.locator('.fin-cell-value')).not.toHaveText('—');
  await page.reload();
  await expect(page.locator('.fin-table')).toBeVisible();
  await expect(page.getByRole('button', { name: '顯示年增率' })).toHaveAttribute('aria-pressed', 'false');
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await page.getByRole('button', { name: '顯示年增率' }).click();
  await page.locator('.fin-table-card').screenshot({ path: info.outputPath(`financials-table-yoy-${info.project.name}.png`) });
});

test('Taiwan QuarterStrip on a 390px phone has one compact row without overflow', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockFinancials(page);
  await page.goto('./?page=financials&symbol=2330.TW');
  const strip = page.locator('.fin-quarter');
  await expect(strip.locator('.fin-quarter-cell')).toHaveCount(4);
  const box = (await strip.boundingBox())!;
  expect(box.height).toBeLessThan(230);
  const cells = await strip.locator('.fin-quarter-cell').evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
  for (let i = 1; i < cells.length; i++) expect(cells[i].left).toBeGreaterThanOrEqual(cells[i - 1].right - 0.5);
  const overflowing = await strip.locator('.fin-quarter-cell').evaluateAll((nodes) => nodes.filter((node) => node.scrollWidth > node.clientWidth + 1).length);
  expect(overflowing).toBe(0);
  expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(0);
  await strip.screenshot({ path: info.outputPath('financials-strip-390.png') });
});
