import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { mockSite, buildReport, yahooChart } from './mocks';
import { searchSymbol } from '../e2e/site-helpers';
import { mockDeviceAccount } from './account-fixture';

async function candlePixels(page: Page) {
  return page.getByTestId('chart').evaluate(host => {
    const canvases = [...host.querySelectorAll('canvas')].filter(canvas => canvas.clientHeight > 100);
    const largest = Math.max(...canvases.map(canvas => canvas.clientWidth));
    return canvases.filter(canvas => canvas.clientWidth === largest).reduce((sum, canvas) => {
      const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, Math.floor(canvas.height * 0.7)).data;
      let count = 0;
      for (let i = 0; i < data.length; i += 16) {
        if (data[i + 3]! > 200 && ((data[i] === 242 && data[i + 1] === 54 && data[i + 2] === 69)
          || (data[i] === 34 && data[i + 1] === 171 && data[i + 2] === 148))) count++;
      }
      return sum + count;
    }, 0);
  });
}

test('switching from a manually scaled low-price stock to a high-price stock shows its candles', async ({ page }) => {
  await mockSite(page);
  await page.route('https://edge.test/functions/v1/market-chart**', route => {
    const url = new URL(route.request().url()), symbol = url.searchParams.get('symbol')!;
    const payload = yahooChart(symbol, url.searchParams.get('interval') ?? '1d');
    const data = payload.chart.result[0]!, quote = data.indicators.quote[0]!;
    const factor = (symbol === 'AAPL' ? 20 : 2000) / data.meta.regularMarketPrice;
    for (const field of ['open', 'high', 'low', 'close'] as const) quote[field] = quote[field].map(value => value * factor);
    data.meta.regularMarketPrice *= factor; data.meta.chartPreviousClose *= factor;
    return route.fulfill({ json: payload });
  });
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect.poll(() => candlePixels(page)).toBeGreaterThan(100);
  const box = (await page.getByTestId('chart').boundingBox())!;
  await page.mouse.move(box.x + box.width - 8, box.y + box.height * 0.45);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 8, box.y + box.height * 0.7, { steps: 12 });
  await page.mouse.up();
  await searchSymbol(page, 'NVDA');
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  await expect(page.getByTestId('ohlc-header')).toContainText('2000.00');
  await expect.poll(() => candlePixels(page)).toBeGreaterThan(100);
});

const board = (page: Page) => page.getByRole('region', { name: '自選行情' });
async function createManual(page: Page, name: string, symbol: string) {
  await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
  await page.getByRole('textbox', { name: '手動清單名稱' }).fill(name);
  await page.getByRole('combobox', { name: '手動清單市場' }).selectOption('US');
  await page.getByRole('textbox', { name: '手動清單股票' }).fill(symbol);
  await page.getByRole('button', { name: '儲存手動清單', exact: true }).click();
  await expect(board(page).locator('.wb-caption')).toContainText(name);
}

test('my lists opens the default list and selects other groups directly above the stock rows', async ({ page }) => {
  await mockDeviceAccount(page); await mockSite(page, { fundamentals: true });
  await page.goto('./?page=watchlist');
  await createManual(page, '預設清單', 'AAPL'); await createManual(page, '第二分組', 'NVDA');
  const picker = board(page).getByRole('combobox', { name: '選擇自選清單' });
  await expect(picker.getByRole('option')).toHaveCount(2);
  const defaultId = await picker.getByRole('option').first().getAttribute('value');
  await board(page).getByRole('button', { name: 'LINE 熱門', exact: true }).click();
  await board(page).getByRole('button', { name: '我的清單', exact: true }).click();
  await expect(picker).toHaveValue(defaultId!);
  await picker.selectOption({ label: '第二分組（美股）' });
  await expect(board(page).getByRole('list', { name: '自選行情列表' }).getByRole('link')).toHaveAccessibleName('NVIDIA NVDA 行情，開啟個股');
  await expect(board(page).getByRole('button', { name: '最近搜尋', exact: true })).toHaveCount(0);
  const positions = await board(page).evaluate(element => ({
    sources: element.querySelector('.wb-tabs')!.getBoundingClientRect().bottom,
    picker: element.querySelector('.wb-list-picker')!.getBoundingClientRect().top,
    toggle: element.querySelector('.wb-chart-toggle')!.getBoundingClientRect().top,
    rows: element.querySelector('.wb-rows')!.getBoundingClientRect().top,
  }));
  expect(positions.picker).toBeGreaterThanOrEqual(positions.sources);
  expect(positions.toggle).toBeGreaterThan(positions.picker);
  expect(positions.rows).toBeGreaterThan(positions.toggle);
  expect((await picker.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
});

test('all stocks have a continuous chart view with automatic paging and disposable offscreen charts', async ({ page }) => {
  await mockDeviceAccount(page); await mockSite(page, { report: buildReport(60), fundamentals: true });
  await page.goto('./?page=screener&group=tw_all');
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('連續六十檔');
  await page.getByRole('button', { name: '儲存並追蹤清單', exact: true }).click();
  const toggle = board(page).getByRole('button', { name: '連續看全部股票圖表（60 檔）', exact: true });
  await toggle.click();
  const charts = board(page).getByRole('region', { name: '連續股票圖表' });
  await expect(charts.locator('.stock-chart-card')).toHaveCount(20);
  const first = charts.locator('.stock-chart-card').first(); await first.scrollIntoViewIfNeeded();
  await expect(first.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await expect(first.getByText('日 K · 成交量', { exact: true })).toBeVisible();
  for (const count of [40, 60]) {
    await charts.getByRole('button', { name: /繼續載入圖表/ }).scrollIntoViewIfNeeded();
    await expect(charts.locator('.stock-chart-card')).toHaveCount(count);
  }
  const last = charts.locator('.stock-chart-card').last(); await last.scrollIntoViewIfNeeded();
  await expect(last.locator('.mini-chart')).toHaveAttribute('data-status', 'ready');
  await expect(first.locator('.mini-chart')).toHaveCount(0);
  expect(await charts.locator('.mini-chart').count()).toBeLessThan(10);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: 'test-results/site/watchlist-continuous-charts.png' });
  await last.getByRole('button', { name: '開啟個股', exact: true }).click();
  await expect(page.getByTestId('active-symbol')).toBeVisible();
});

test('financials uses the same stock tab sizing and identity-first order as the three chart views', async ({ page }) => {
  await mockSite(page);
  await page.route('**/report/financials/*.json', route => route.fulfill({ contentType: 'application/json',
    body: readFileSync(new URL('../tests/fixtures/financials/us-full.json', import.meta.url), 'utf8') }));
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  const measure = (button: HTMLElement) => { const css = getComputedStyle(button); return { width: button.getBoundingClientRect().width, font: css.fontSize, padding: css.padding, minHeight: css.minHeight }; };
  const chartTabs = await page.getByTestId('stock-tab-financials').evaluate(measure);
  await page.getByTestId('stock-tab-financials').click();
  await expect(page.locator('.fin-head')).toBeVisible();
  const tabs = page.locator('.fin-head').getByRole('navigation', { name: '個股頁面' });
  await expect(tabs.getByRole('button')).toHaveText(['K線', '財務', '價量', '研究']);
  expect(await tabs.getByRole('button', { name: '財務', exact: true }).evaluate(measure)).toEqual(chartTabs);
  const order = await page.locator('.fin-head').evaluate(header => ({
    identity: header.querySelector('.fin-title')!.getBoundingClientRect().top,
    tabs: header.querySelector('.stock-tabs')!.getBoundingClientRect().top,
  }));
  expect(order.tabs).toBeGreaterThan(order.identity);
  await expect(page.locator('.fin-head').getByRole('button', { name: '搜尋其他股票' })).toBeVisible();
  await expect(page.locator('.fin-head').getByRole('group', { name: '期間', exact: true })).toBeVisible();
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: 'test-results/site/financials-shared-stock-tabs.png' });
});
