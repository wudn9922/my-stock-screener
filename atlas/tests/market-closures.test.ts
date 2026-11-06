import 'fake-indexeddb/auto';
import { describe, expect, it, vi } from 'vitest';
import { KlineArchive, mergeArchiveDaily, parseArchiveCsv } from '../src/market-data/KlineArchive';
import { archivePeriod, mergeArchivedLive } from '../src/market-data/ArchiveFirstProvider';
import { closedMarketDates, loadMarketClosures, marketClosuresSchema } from '../src/market-data/MarketClosures';
import { archiveIndexFixture } from './fixtures/archive-index';
import { calendarDate, getMarketProfile } from '../src/market-data/MarketProfile';
import type { BarResult } from '../src/market-data/MarketDataProvider';

const body='Date,Open,High,Low,Close,Volume\n2026-10-22,100,100,100,100,0\n2026-10-23,100,100,100,100,0\n2026-10-27,101,102,100,101,1000\n';
const day=(bar:{time:number})=>calendarDate(bar.time,getMarketProfile('2330.TW'));
const evidence={date:'2026-10-27',reasons:['flat-zero-cohort'],observedSymbols:100,eligibleSymbols:100,
  flatZeroSymbols:95,unknownVolumeSymbols:0,flatZeroShare:.95,coverageReference:100};
const policy={version:1,threshold:.95,markets:{tw:{closedSessions:[evidence]},us:{closedSessions:[]}}};

describe('market-wide closures are projections, never price edits',()=>{
  it('uses the verified legacy scan when the optional report is unavailable and caches the read once',async()=>{
    const fetcher=vi.fn(async()=>new Response('',{status:503}));
    const [a,b]=await Promise.all([loadMarketClosures(fetcher,Date.UTC(2026,10,6)),loadMarketClosures(fetcher,Date.UTC(2026,10,6))]);
    expect(a).toEqual(b);expect(fetcher).toHaveBeenCalledTimes(1);
    expect(closedMarketDates('2330.TW',a).has('2026-10-23')).toBe(true);
    expect(closedMarketDates('AAPL',a).has('2026-10-23')).toBe(false);
  });
  it('strictly validates reasons, dates and the shared 95% threshold',()=>{
    expect(marketClosuresSchema.safeParse(policy).success).toBe(true);
    for(const changed of [{...evidence,date:'2026-02-30'},{...evidence,reasons:[]},{...evidence,flatZeroShare:2}])
      expect(marketClosuresSchema.safeParse({...policy,markets:{tw:{closedSessions:[changed]}}}).success).toBe(false);
    expect(marketClosuresSchema.safeParse({...policy,threshold:.5}).success).toBe(false);
  });
  it('keeps previously validated dynamic evidence when the next UTC day fetch fails',async()=>{
    const fetcher=vi.fn(async()=>Response.json(policy));
    const first=await loadMarketClosures(fetcher,Date.UTC(2026,10,6));
    fetcher.mockImplementation(async()=>new Response('',{status:503}));
    const next=await loadMarketClosures(fetcher,Date.UTC(2026,10,7));
    expect(next).toEqual(first);
    expect(closedMarketDates('2330.TW',next).has('2026-10-27')).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('filters an already cached archive year and keeps an isolated zero-volume halt on a real day',async()=>{
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>String(url).endsWith('market-closures.json')?Response.json({version:1,threshold:.95,markets:{}})
      :String(url).includes('/index/')?Response.json(archiveIndexFixture('2330.TW',{2026:body})):new Response(body));
    const options={fetcher,now:()=>Date.UTC(2026,10,6),cache:{dbName:'closed-'+crypto.randomUUID()}};
    const raw=parseArchiveCsv(body,'2330.TW',2026);
    expect(raw.bars.map(day)).toContain('2026-10-23'); // immutable CSV parse is unchanged
    const first=await new KlineArchive(options).getDaily('2330.TW');
    expect(first!.bars.map(day)).toEqual(['2026-10-22','2026-10-27']);
    expect(first!.bars[0]!.volume).toBe(0); // not every zero-volume stock is a closure
    fetcher.mockClear();
    const cached=await new KlineArchive(options).getDaily('2330.TW');
    expect(cached!.bars.map(day)).toEqual(['2026-10-22','2026-10-27']);
    expect(fetcher).not.toHaveBeenCalled();
    expect(first!.source).toContain('已排除全市場休市日');
  });
  it('accepts newly observed closures without changing old raw files or reintroducing them through live merges',async()=>{
    const fetcher=async(url:RequestInfo|URL)=>String(url).endsWith('market-closures.json')?Response.json(policy)
      :String(url).includes('/index/')?Response.json(archiveIndexFixture('2330.TW',{2026:body})):new Response(body);
    const archive=new KlineArchive({fetcher,cache:false,now:()=>Date.UTC(2026,10,6)});
    const daily=await archive.getDaily('2330.TW');
    expect(daily!.bars.map(day)).toEqual(['2026-10-22']);
    const raw=parseArchiveCsv(body,'2330.TW',2026).bars;
    const merged=await mergeArchivedLive(daily!,{symbol:'2330.TW',bars:raw,retrievedAt:Date.UTC(2026,10,6)/1000},'1D',archive);
    expect(merged.bars.map(day)).toEqual(['2026-10-22']);
    expect(mergeArchiveDaily(raw,raw,'2330.TW',undefined,new Set(daily!.closedSessions)).bars.map(day)).toEqual(['2026-10-22']);
  });
  it('daily, weekly and monthly aggregation all start after closure projection',()=>{
    const raw:BarResult={...parseArchiveCsv(body,'2330.TW',2026),closedSessions:['2026-10-23'],archive:{status:'archive',lastDate:'2026-10-27'}};
    expect(archivePeriod(raw,'2330.TW','1D').bars.map(day)).toEqual(['2026-10-22','2026-10-27']);
    expect(archivePeriod(raw,'2330.TW','1W').bars).toHaveLength(2);
    expect(archivePeriod(raw,'2330.TW','1M').bars).toHaveLength(1);
    expect(raw.bars).toHaveLength(3);
  });
});
