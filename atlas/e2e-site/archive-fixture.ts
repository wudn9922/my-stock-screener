import { archiveIndexFixture } from '../tests/fixtures/archive-index';
import type { Page } from '@playwright/test';
import { dailyBars, yahooChart } from './mocks';

const root = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/';
export const clock = new Date('2026-10-09T16:00:00Z');
export async function archives(page: Page, symbols: string[]) {
  const requests: string[]=[];
  await page.route(root+'**',route=>{
    const path=route.request().url().slice(root.length); requests.push(path);
    if(path.startsWith('index/')) {
      const symbol=decodeURIComponent(decodeURIComponent(path.split('/').at(-1)!.slice(0,-5)));
      if(!symbols.includes(symbol))return route.fulfill({status:404});
      const files:Record<number,string>={};
      for(const row of dailyBars(symbol)) {
        const year=new Date(row.time*1000).getUTCFullYear();
        files[year]??='Date,Open,High,Low,Close,Volume\n';
        files[year]+=[new Date(row.time*1000).toISOString().slice(0,10),row.open,row.high,row.low,row.close,row.volume].join(',')+'\n';
      }
      return route.fulfill({json:archiveIndexFixture(symbol,files)});
    }
    const parts=path.split('/'),symbol=decodeURIComponent(parts[1]??''),year=Number(parts[2]?.slice(0,4));
    const rows=dailyBars(symbol).filter(bar=>new Date(bar.time*1000).getUTCFullYear()===year);
    return route.fulfill({contentType:'text/csv',body:'Date,Open,High,Low,Close,Volume\n'+rows.map(b=>[new Date(b.time*1000).toISOString().slice(0,10),b.open,b.high,b.low,b.close,b.volume].join(',')).join('\n')+'\n'});
  });
  return requests;
}
export async function manualList(page:Page,symbols:string[]) {
  await openListActions(page);
  await page.getByRole('button',{name:'建立手動清單',exact:true}).click();
  await page.getByRole('textbox',{name:'手動清單名稱'}).fill('封存驗收');
  await page.getByRole('combobox',{name:'手動清單市場'}).selectOption('US');
  await page.getByRole('textbox',{name:'手動清單股票'}).fill(symbols.join(','));
  await page.getByRole('button',{name:'儲存手動清單',exact:true}).click();
  await page.locator('.wb-row').first().scrollIntoViewIfNeeded();
}
function packet(symbol:string, tick:number) {
  const result=yahooChart(symbol,'1d');
  if(symbol.endsWith('.TW'))return result;
  const raw=result.chart.result[0]!, q=raw.indicators.quote[0]!;
  const previous=q.close.at(-1)!; const price=Number((previous+tick).toFixed(2));
  raw.timestamp.push(Date.parse('2026-10-09T13:30:00Z')/1000);
  q.open.push(previous);q.high.push(Math.max(price,previous)+1);q.low.push(Math.min(price,previous)-1);q.close.push(price);q.volume.push(10000+tick);
  raw.meta.regularMarketTime=Date.parse('2026-10-09T15:59:00Z')/1000;
  raw.meta.regularMarketPrice=price;raw.meta.chartPreviousClose=previous;
  return result;
}
export async function live(page:Page) {
  const batches:string[][]=[],dailyFiveYears:string[]=[];
  await page.route('**/functions/v1/market-chart?**',route=>{
    const params=new URL(route.request().url()).searchParams;
    if(!params.has('quotes')) {
      if(params.get('interval')==='1d'&&params.get('range')==='5y')dailyFiveYears.push(params.get('symbol')!);
      return route.fallback();
    }
    const symbols=params.get('quotes')!.split(',');batches.push(symbols);
    return route.fulfill({json:{results:Object.fromEntries(symbols.map(symbol=>[symbol,packet(symbol,batches.length)])),errors:{}}});
  });
  return {batches,dailyFiveYears};
}

/** Opens the phone-only list actions sheet; desktop actions remain in the page. */
export async function openListActions(page: Page) {
  const add = page.getByRole('button', { name: '新增清單', exact: true });
  if (await add.isVisible()) await add.click();
}
