import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';

const params = { windowDays: 21, observationDays: 5, minSignals: 15, rule: 'hold5', percentileWindow: 504, percentileMinValid: 252,
  lowPercentile: 10, horizonDays: 40, drawdown: 0.07, episodeGapDays: 20, jumpLimit: 0.3 };
const days = Array.from({ length: 300 }, (_, i) => new Date(Date.UTC(2025, 8, 1) + i * 86_400_000).toISOString().slice(0, 10));
function method(label: string, lowFrom: number) {
  const rows = days.map((date, i) => {
    const pct = i < 20 ? null : i >= lowFrom ? 3 : 20 + (i % 60);
    return { date, rate: 0.5, n: 400 + i, pct, low: pct !== null && pct <= 10 };
  });
  const lowDays = rows.length - lowFrom > 0 ? rows.length - lowFrom : 0;
  return { label, rows, latest: { ...rows.at(-1)!, lowDays },
    stats: { episodes: 9, hits: 4, avgDrawdown: -0.068, baseHitRate: 0.2016, baseAvgDrawdown: -0.0432, pending: 0,
      list: [{ date: '2022-03-15', rate: 0.6667, pct: 4.8, drawdown: -0.0783, hit: true }] } };
}
const payload = { version: 1, generatedAt: '2026-11-20T00:00:00Z', params, markets: {
  us: { asOf: days.at(-1), index: { symbol: '^GSPC', name: 'S&P 500' }, stocks: 5389, failedStocks: 0, note: null,
    methods: { swing: method('突破波段前高', 400), ema20: method('突破20EMA', 290) },
    closes: days.map((date, i) => ({ date, close: 6000 + 4 * i })) },
} };

test('US breakout achievement-rate warning shows both methods, history and fits the phone', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await mockSite(page);
  await page.route('**/my-stock-screener/report/breakout-warning.json', route => route.fulfill({ json: payload }));
  await page.goto('./?page=markets&market=us');
  const card = page.getByTestId('breakout-warning-card');
  await expect(card).toBeVisible();
  if (info.project.name !== 'desktop-chromium') {
    await expect(card.locator('.bw-summary')).toContainText('1／2 項偏低');
    await card.locator('.bw-summary').click();
  }
  await expect(card.locator('.bw-method')).toHaveCount(2);
  await expect(card.locator('.bw-method[data-state="low"]')).toContainText('已連續 10 個交易日');
  await expect(card.locator('.bw-method[data-state="normal"]')).toContainText('未偏低');
  await expect(card).toContainText('過去 9 次提醒，40 個交易日內大盤跌 7% 以上 4 次');
  await expect(card).toContainText('不是賣出訊號');
  const combined = card.locator('.bw-method[data-state="low"]');
  const chart = combined.getByRole('img', { name: /合併圖/ });
  await expect(chart).toHaveAttribute('data-chart-bands', '10');
  await expect(chart).toHaveAttribute('data-chart-series', /指數:left\|分位:right\|P10 門檻:right/);
  await expect(chart.locator('canvas').first()).toBeVisible();
  const count = combined.getByLabel('顯示資料點數');
  await expect(count).toHaveText('300');
  await combined.getByRole('button', {name:'放大圖表'}).click();
  await expect.poll(async () => Number(await count.textContent())).toBeLessThan(300);
  const zoomed = Number(await count.textContent());
  await combined.getByRole('button', {name:'縮小圖表'}).click();
  await expect.poll(async () => Number(await count.textContent())).toBeGreaterThan(zoomed);
  await combined.getByRole('button', {name:'重設'}).click();
  await expect(count).toHaveText('300');
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  if (info.project.name === 'iphone-webkit') {
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('breakout-warning-390.png') });
  }
  expect(errors).toEqual([]);
});

test('a missing warning file leaves a quiet status line', async ({ page }) => {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/breakout-warning.json', route => route.fulfill({ status: 404, body: '' }));
  await page.goto('./?page=markets&market=tw');
  await expect(page.getByTestId('breakout-warning-card')).toContainText('尚未產生');
});
