import { afterEach, expect, it, vi } from 'vitest';
import type { BarResult } from '../src/market-data/MarketDataProvider';
import { mergeArchiveDaily } from '../src/market-data/KlineArchive';
afterEach(()=>{vi.doUnmock('../src/market-data/KlineArchive');vi.doUnmock('../src/market-data/createScreenerMarketProvider');vi.resetModules();});
const sample = (): BarResult => ({bars:Array.from({length:200},(_,i)=>({time:Date.UTC(2026,0,i+1)/1000,open:100,high:101,low:99,close:100+i/1000,volume:1000})),source:'archive',session:'regular',delayed:true,adjusted:true});
it('keeps archived sessions immutable while updating the forming day on every live tick',()=>{
  const old=sample().bars.slice(0,2), cutoff=new Date(old.at(-1)!.time*1000).toISOString().slice(0,10);
  const today={...old[1]!,time:old[1]!.time+86400,close:100.1};
  const first=mergeArchiveDaily(old,[today],'AAPL',cutoff).bars;
  const tick={...today,close:100.2};
  expect(mergeArchiveDaily(first,[tick],'AAPL',cutoff).bars).toEqual([...old,tick]);
});
it('C3 de-duplicates six-month histories, limits to 126 rows and never downloads five years for archived symbols',async()=>{
  const getDaily=vi.fn(async()=>sample()),getBars=vi.fn(async()=>sample());
  vi.doMock('../src/market-data/KlineArchive',()=>({klineArchive:()=>({getDaily})}));
  vi.doMock('../src/market-data/createScreenerMarketProvider',()=>({createScreenerMarketProvider:()=>({getBars})}));
  const {getWatchlistDailyResult,sixMonthRange}=await import('../src/app/dataSources');
  const [a,b]=await Promise.all([getWatchlistDailyResult('AAPL'),getWatchlistDailyResult('AAPL')]);
  expect(a).toBe(b);expect(a.bars).toHaveLength(126);expect(a.archive?.status).toBe('archive');
  expect(getDaily).toHaveBeenCalledTimes(1);expect(getBars).not.toHaveBeenCalled();
  const from=sixMonthRange(Date.UTC(2026,7,31)).from;
  expect(new Date(from*1000).toISOString().slice(0,10)).toBe('2026-02-28');
});
it.each(['absent','network','missing-file'])('C3 %s archive uses the existing daily source and retains daily change',async(kind)=>{
  const getDaily=vi.fn(async()=>{if(kind==='absent')return null;throw new Error(kind);}),getBars=vi.fn(async()=>sample());
  vi.doMock('../src/market-data/KlineArchive',()=>({klineArchive:()=>({getDaily})}));
  vi.doMock('../src/market-data/createScreenerMarketProvider',()=>({createScreenerMarketProvider:()=>({getBars})}));
  const {getWatchlistDailyResult}=await import('../src/app/dataSources');
  const result=await getWatchlistDailyResult('NEW');
  expect(result.bars.slice(-2)).toEqual(sample().bars.slice(-2));expect(getBars).toHaveBeenCalledExactlyOnceWith('NEW','1D');
});
