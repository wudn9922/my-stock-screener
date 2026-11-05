import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { archiveIndexFixture } from '../tests/fixtures/archive-index';
import { D5, intradayFilledPayload } from '../tests/fixtures/intraday-fill';

test('C3 review: #224 filled TWII sessions stay current through consecutive daily quote batches', async ({ page }, info) => {
  await page.clock.install({ time: new Date((D5+3600)*1000) });
  await mockSite(page);
  const body='Date,Open,High,Low,Close,Volume\n2026-11-03,44481.59,44614.64,43191.58,43307.54,4346200\n2026-11-04,43000,43102,42898,42948.91,200\n';
  const files: string[] = [], fiveYears: string[] = [];
  await page.route('https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/**', route => {
    const path=route.request().url().split('/kline-archive/')[1]!;files.push(path);
    if(path==='index/tw/%255ETWII.json')return route.fulfill({json:archiveIndexFixture('^TWII',{2026:body})});
    if(path==='tw/%5ETWII/2026.csv')return route.fulfill({contentType:'text/csv',body});
    return route.fulfill({status:404});
  });
  let batches=0;
  await page.route('https://edge.test/functions/v1/market-chart**',route=>{
    const params=new URL(route.request().url()).searchParams;
    if(!params.has('quotes')) {
      if(params.get('symbol')==='^TWII'&&params.get('interval')==='1d'&&params.get('range')==='5y')fiveYears.push(params.get('symbol')!);
      return route.fallback();
    }
    batches++;
    const packet=intradayFilledPayload(true);
    if(batches>1) {
      packet.chart.result[0]!.timestamp[2]=D5+300;
      packet.chart.result[0]!.indicators.quote[0]!.close[2]=43260;
      packet.chart.result[0]!.meta.regularMarketPrice=43260;
    }
    return route.fulfill({json:{results:{'^TWII':packet},errors:{}}});
  });
  await page.goto('./?page=chart&symbol=%5ETWII&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText(/43,?250\.00/);
  await expect(page.getByTestId('source-label')).toContainText('GitHub 永久封存');
  await page.clock.fastForward(61_000);
  await expect(page.getByTestId('ohlc-header')).toContainText(/43,?260\.00/);
  expect(batches).toBe(2);expect(fiveYears).toEqual([]);
  expect(files).not.toContain('manifest.json');
  expect(files.filter(path=>path.includes('ETWII')).sort()).toEqual(['index/tw/%255ETWII.json','tw/%5ETWII/2026.csv']);
  await expect(page.getByTestId('chart')).toBeVisible();
  await page.screenshot({path:info.outputPath('twii-intraday-filled.png')});
});
