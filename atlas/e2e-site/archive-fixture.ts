import type { Page } from '@playwright/test';
import { dailyBars, yahooChart } from './mocks';

const root = 'https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/';
export const clock = new Date('2026-10-09T16:00:00Z');
export async function archives(page: Page, symbols: string[]) {
  const requests: string[]=[];
  await page.route(root+'**',route=>{
    const path=route.request().url().slice(root.length); requests.push(path);
    if(path==='manifest.json') return route.fulfill({json:{version:1,markets:Object.fromEntries(['tw','us'].map(m=>[m,{
      symbols:symbols.filter(s=>s.endsWith('.TW')===(m==='tw')),firstDate:'2026-01-01',lastDate:'2026-10-08',
    }]))}});
    const parts=path.split('/'),symbol=decodeURIComponent(parts[1]??''),year=Number(parts[2]?.slice(0,4));
    const rows=dailyBars(symbol).filter(bar=>new Date(bar.time*1000).getUTCFullYear()===year);
    return route.fulfill({contentType:'text/csv',body:'Date,Open,High,Low,Close,Volume\n'+rows.map(b=>[new Date(b.time*1000).toISOString().slice(0,10),b.open,b.high,b.low,b.close,b.volume].join(',')).join('\n')+'\n'});
  });
  return requests;
}
export async function manualList(page:Page,symbols:string[]) {
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
