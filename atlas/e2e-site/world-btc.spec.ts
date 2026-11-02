import { expect, test } from '@playwright/test';
import { researchFixtureBody } from './research-fixtures';
import { mockSite } from './mocks';

for (const savedOnly of [false, true]) {
  test(`commodities shows genuine BTC/USDT ${savedOnly ? 'from its dated retained source when the old index lacks BTC' : 'from the published world index'}`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mockSite(page);
    await page.route('**/my-stock-screener/report/research/**', route => {
      const path = new URL(route.request().url()).pathname.split('/research/')[1]!;
      let body = researchFixtureBody(path).toString();
      if (savedOnly && path === 'world-assets.json') {
        const index = JSON.parse(body);
        delete index.series.btcusdt;
        body = JSON.stringify(index);
      }
      return route.fulfill({ contentType: 'application/json', body });
    });
    await page.goto('./?page=world&view=commodities');
    await page.getByRole('button', { name: /連續看全部圖表/ }).click();
    const card = page.locator('.world-candle-card').filter({ has: page.getByRole('heading', { name: 'BTC/USDT', exact: true }) });
    await expect(card).toContainText('USDT/BTC');
    await expect(card).toContainText('成交量 BTC');
    await expect(card).toContainText('2026-10-24');
    if (savedOnly) await expect(card).toContainText('保存資料');
    await expect(async () => { await card.scrollIntoViewIfNeeded(); }).toPass({ timeout: 10000 });
    await expect(card.locator('.candle-ohlc')).toContainText('O ');
    const chart = card.locator('[data-chart-identity="world:CRYPTO-BTCUSDT"]');
    await expect(chart).toHaveAttribute('data-session-calendar', 'continuous-utc');
    await expect(chart.locator('canvas').first()).toBeVisible();
    await chart.getByRole('button', { name: '週K', exact: true }).click();
    await expect(chart.locator('.candle-ohlc')).toContainText('O ');
    await card.screenshot({ path: info.outputPath(`btc-usdt-${savedOnly ? 'retained' : 'published'}.png`) });
    expect(errors).toEqual([]);
  });
}

for (const mode of ['unavailable', 'invalid-candles', 'wrong-pair']) {
  test(`explicit published BTC ${mode} stays authoritative and never acquires the retained fallback`, async ({ page }) => {
    await mockSite(page);
    await page.route('**/my-stock-screener/report/research/**', route => {
      const path = new URL(route.request().url()).pathname.split('/research/')[1]!;
      const body = JSON.parse(researchFixtureBody(path).toString());
      if (mode === 'unavailable' && path === 'world-assets.json') {
        delete body.series.btcusdt.candlePath;
        body.series.btcusdt.status = 'unavailable';
        body.series.btcusdt.missingReason = 'Binance source temporarily unavailable';
      }
      if (mode === 'invalid-candles' && path === 'candles/btcusdt.json') body.bars.at(-1)[4] = 9999999;
      if (mode === 'wrong-pair' && path === 'world-assets.json') body.series.btcusdt.quoteCurrency = 'USD';
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto('./?page=world&view=commodities');
    await page.getByRole('button', { name: /連續看全部圖表/ }).click();
    const card = page.locator('.world-candle-card').filter({ has: page.getByRole('heading', { name: 'BTC/USDT', exact: true }) });
    await card.scrollIntoViewIfNeeded();
    if (mode === 'unavailable') await expect(card).toContainText('尚未取得真正的開高低收資料');
    else if (mode === 'wrong-pair') await expect(card).toContainText('來源商品或報價幣別不符');
    else await expect(card).toContainText('Invalid OHLC bounds');
    await expect(card).not.toContainText('保存資料');
    if (mode === 'wrong-pair') await expect(card.locator('.candle-ohlc')).toHaveCount(0);
    else await expect(card.locator('.candle-ohlc')).not.toContainText('O ');
  });
}
