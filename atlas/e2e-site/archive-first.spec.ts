import { expect, test, type Page } from '@playwright/test';
import { dailyBars, mockSite } from './mocks';

const archiveRoot = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/';
async function archived(page: Page, symbol = 'AAPL') {
  await mockSite(page);
  const bars = dailyBars(symbol, 150);
  const byYear = new Map<number, string[]>();
  for (const bar of bars) {
    const iso = new Date(bar.time*1000).toISOString().slice(0,10), year = Number(iso.slice(0,4));
    if (!byYear.has(year)) byYear.set(year, []);
    byYear.get(year)!.push([iso,bar.open,bar.high,bar.low,bar.close,bar.volume].join(','));
  }
  const requested: string[] = [];
  await page.route(archiveRoot + '**', route => {
    const path = route.request().url().slice(archiveRoot.length); requested.push(path);
    const market = symbol.endsWith('.TW') ? 'tw' : 'us';
    if (path === 'manifest.json') return route.fulfill({ json: { version:1, markets: { [market]: { symbols:[symbol],
      firstDate:new Date(bars[0]!.time*1000).toISOString().slice(0,10),lastDate:'2026-10-08' } } } });
    const rows = byYear.get(Number(path.match(/(\d{4})\.csv/)?.[1]));
    return route.fulfill(rows ? { contentType:'text/csv',body:'Date,Open,High,Low,Close,Volume\n'+rows.join('\n')+'\n' } : {status:404});
  });
  return requested;
}

for (const failure of ['502','timeout','invalid'] as const) test(`C1/C2 archive daily, weekly and monthly survive Yahoo ${failure}`, async ({page}, info) => {
  const archiveRequests = await archived(page);
  const market: string[] = [];
  await page.route('**/functions/v1/market-chart?**', route => {
    market.push(route.request().url());
    if (failure === 'timeout') return route.abort('timedout');
    return route.fulfill(failure === '502' ? {status:502,body:'unavailable'} : {json:{results:{AAPL:{chart:{result:[]}}}}});
  });
  for (const tf of ['1D','1W','1M']) {
    await page.goto(`./?page=chart&symbol=AAPL&tf=${tf}`);
    await expect(page.getByTestId('chart')).toBeVisible();
    await expect(page.locator('.chart-loading.error')).toHaveCount(0);
    const source = page.locator('[data-testid="source-label"]');
    await expect(source).toContainText('GitHub 永久封存');
    await expect(source).toContainText('即時報價暫時無法取得，顯示至 2026-10-08 收盤');
    await expect(page.getByTestId('archive-availability')).toBeVisible();
    const notice=(await page.getByTestId('archive-availability').boundingBox())!, chart=(await page.getByTestId('chart').boundingBox())!;
    expect(notice.width).toBeGreaterThanOrEqual(chart.width*0.9);
    expect(notice.y+notice.height).toBeLessThanOrEqual(chart.y+1);
    expect(chart.height).toBeGreaterThan(200);
    expect(await page.locator('#main').evaluate(el=>el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(0);
    if (tf==='1D' && failure==='502') await page.screenshot({path:info.outputPath('archive-offline-chart.png')});
  }
  expect(market.some(url=>new URL(url).searchParams.get('symbol')==='AAPL'
    && new URL(url).searchParams.get('interval')==='1d' && new URL(url).searchParams.get('range')==='5y')).toBe(false);
  expect(archiveRequests.filter(path=>path==='manifest.json')).toHaveLength(3); // one per independent navigation
});

test('C2 intraday failure explains the cause and offers daily archive recovery', async ({page}) => {
  await archived(page);
  await page.route('**/functions/v1/market-chart?**', route=>route.fulfill({status:502}));
  await page.goto('./?page=chart&symbol=AAPL&tf=5m');
  await expect(page.locator('.chart-loading.error')).toContainText('502');
  await page.getByRole('button',{name:'改看日 K',exact:true}).click();
  await expect(page.locator('.chart-loading.error')).toHaveCount(0);
  await expect(page.locator('[data-testid="source-label"]')).toContainText('GitHub 永久封存');
});
