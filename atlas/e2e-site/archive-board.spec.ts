import { archiveIndexFixture } from '../tests/fixtures/archive-index';
import { expect, test } from '@playwright/test';
import { clock, archives, manualList } from './archive-fixture';
import { dailyBars, mockSite } from './mocks';
import { mockDeviceAccount } from './account-fixture';

test('C3 a 47-symbol board reads six-month archives with no five-year request and unchanged daily changes',async({page},info)=>{
  await page.clock.install({time:clock});await mockDeviceAccount(page);await mockSite(page);
  const symbols=Array.from({length:47},(_,i)=>`T${i}`);
  const archiveRequests=await archives(page,symbols);
  const fiveYears:string[]=[];
  page.on('request',request=>{const p=new URL(request.url()).searchParams;if(p.get('interval')==='1d'&&p.get('range')==='5y')fiveYears.push(p.get('symbol')!);});
  await page.goto('./?page=watchlist');
  await manualList(page,symbols);
  const rows=page.locator('.wb-row');await expect(rows).toHaveCount(20);
  await expect(rows.first().locator('.wb-change')).toContainText('%');
  expect((await rows.first().locator('polyline').getAttribute('points'))!.split(' ')).toHaveLength(126);
  const bars=dailyBars('T0'),change=(bars.at(-1)!.close/bars.at(-2)!.close-1)*100;
  await expect(rows.first().locator('.wb-change')).toContainText(Math.abs(change).toFixed(2)+'%');
  await page.getByRole('button',{name:'顯示全部 47 檔',exact:true}).click();await expect(rows).toHaveCount(47);
  for(const index of [10,20,30,46]) {await rows.nth(index).scrollIntoViewIfNeeded();await expect(rows.nth(index).locator('.wb-change')).toContainText('%');}
  expect(fiveYears).toEqual([]);expect(archiveRequests).not.toContain('manifest.json');
  const indexes=archiveRequests.filter(p=>p.startsWith('index/'));expect(new Set(indexes).size).toBe(indexes.length);
  expect(indexes).toContain('index/us/T0.json');expect(indexes).toContain('index/us/T46.json');
  await rows.first().scrollIntoViewIfNeeded();await page.screenshot({path:info.outputPath('six-month-watchlist.png')});
  expect(await page.locator('#main').evaluate(el=>el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(0);
});

for (const failure of ['missing-file','network'] as const) test(`C3 ${failure} archive falls back to the existing Yahoo history`,async({page})=>{
  await page.clock.install({time:clock});await mockSite(page);
  await archives(page,['AAPL']);
  await page.route('https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/us/AAPL/*.csv',route=>failure==='network'?route.abort('failed'):route.fulfill({status:404}));
  await page.route('**/report/trending-stocks.json',route=>route.fulfill({json:{version:1,source:'line',fetchedAt:'2026-10-09T08:00:00Z',sentAt:'2026-10-09T08:00:00Z',symbols:['AAPL']}}));
  const fiveYears:string[]=[];page.on('request',request=>{const p=new URL(request.url()).searchParams;if(p.get('symbol')==='AAPL'&&p.get('interval')==='1d'&&p.get('range')==='5y')fiveYears.push(request.url());});
  await page.goto('./?page=watchlist');
  const row=page.locator('.wb-row');await expect(row.locator('.wb-change')).toContainText('%');
  expect(fiveYears).toHaveLength(1);await expect(row).toHaveAttribute('title',/Yahoo/);
});

test('C3 newly listed stock reads only its actual year and keeps two available sessions',async({page})=>{
  await page.clock.install({time:clock});await mockSite(page);
  const files:string[]=[];
  await page.route('https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/**',route=>{
    const path=route.request().url().split('/kline-archive/')[1]!;files.push(path);
    const body='Date,Open,High,Low,Close,Volume\n2026-10-07,100,102,99,100,1000\n2026-10-08,100,103,99,102,2000\n';
    return route.fulfill(path.startsWith('index/')?{json:archiveIndexFixture('NEW',{2026:body})}:{contentType:'text/csv',body});
  });
  await page.route('**/report/trending-stocks.json',route=>route.fulfill({json:{version:1,source:'line',fetchedAt:'2026-10-09T08:00:00Z',sentAt:'2026-10-09T08:00:00Z',symbols:['NEW']}}));
  const fiveYears:string[]=[];page.on('request',request=>{const p=new URL(request.url()).searchParams;if(p.get('interval')==='1d'&&p.get('range')==='5y')fiveYears.push(request.url());});
  await page.goto('./?page=watchlist');const row=page.locator('.wb-row');
  await expect(row.locator('.wb-change')).toContainText('2.00%');
  expect((await row.locator('polyline').getAttribute('points'))!.split(' ')).toHaveLength(2);
  expect(files.sort()).toEqual(['index/us/NEW.json','us/NEW/2026.csv']);expect(fiveYears).toEqual([]);
});
