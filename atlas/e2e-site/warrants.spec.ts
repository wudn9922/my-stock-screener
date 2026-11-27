import { expect, test } from '@playwright/test';
import { mockMarketLogin } from './account-fixture';
import { mockSite } from './mocks';

test('warrant page filters and sorts valid MIS quotes, with a missing price last', async ({ page }, info) => {
  await mockMarketLogin(page);
  await mockSite(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const future = '2099-01-01';
  const rows = ['066186','066187','066188'].map((code, i) => ({
    code, symbol: code + '.TW', name: '測試權證 ' + code, type: i === 2 ? 'put' : 'call',
    strikePrice: 100, exerciseRatio: 0.01, expiryDate: future, lastTradingDate: future, issuer: '測試券商', market: 'TWSE',
  }));
  await page.route('**/report/warrants/2330.TW.json', route => route.fulfill({ json: { version: 1, underlying: '2330.TW', asOf: { TWSE: '2026-11-26', TPEx: '2026-11-26' }, warrants: rows } }));
  await page.route('https://edge.test/functions/v1/market-chart**', route => {
    if (!new URL(route.request().url()).searchParams.has('warrantQuotes')) return route.fallback();
    const quote = (bid: number, ask: number | null) => ({ status: 'available', bid, ask, bidSize: 1, askSize: ask === null ? null : 1, quoteTime: 1795661400 });
    return route.fulfill({ json: { asOf: 1795661400, source: 'twse-mis', session: 'closed', results: { '066186.TW': quote(0.45,0.70), '066187.TW': quote(0.68,0.69), '066188.TW': quote(1,null) } } });
  });
  await page.goto('./?page=warrants&symbol=2330.TW');
  await expect(page.getByRole('navigation', { name: '市場分頁' }).getByRole('link', { name: '權證' })).toHaveAttribute('aria-current', 'page');
  const list = page.getByLabel('權證清單').getByRole('article');
  await expect(list.first()).toHaveAttribute('aria-label', '066187.TW');
  await expect(list.last()).toHaveAttribute('aria-label', '066188.TW');
  await expect(list.first()).toContainText('1.46%');
  await page.getByLabel('認購／認售').selectOption('call');
  await expect(list).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  if (info.project.name.includes('webkit') && process.env.ATLAS_WARRANT_SCREENSHOT === '1')
    await page.screenshot({ path: info.outputPath('warrants-390.png') });
});
