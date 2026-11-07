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
const evidence={date:'2026-10-27',reasons:['flat-zero-cohort','primary-index-missing'],calendar:'XTAI',calendarSession:true,observedSymbols:100,eligibleSymbols:100,
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
    const { calendar, calendarSession, ...legacy } = evidence;
    void calendar; void calendarSession;
    expect(marketClosuresSchema.safeParse({...policy,markets:{tw:{closedSessions:[legacy]}}}).success).toBe(false);
    expect(marketClosuresSchema.safeParse({...policy,markets:{tw:{closedSessions:[{...evidence,reasons:['primary-index-missing'],flatZeroSymbols:0,flatZeroShare:0}]}}}).success).toBe(false);
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
  it('keeps November 4 stock bars when only the primary index has a gap',async()=>{
    const csv='Date,Open,High,Low,Close,Volume\n2026-11-03,100,101,99,100,1000\n2026-11-04,101,102,100,101,1000\n2026-11-05,102,103,101,102,1000\n';
    const gap={version:1,threshold:.95,markets:{tw:{closedSessions:[],indexGapsWithTrading:[{
      date:'2026-11-04',reason:'primary-index-gap-with-trading',observedSymbols:100,positiveVolumeSymbols:100,
    }]}}};
    const fetcher=async(url:RequestInfo|URL)=>String(url).endsWith('market-closures.json')?Response.json(gap)
      :String(url).includes('/index/')?Response.json(archiveIndexFixture('2330.TW',{2026:csv})):new Response(csv);
    const daily=await new KlineArchive({fetcher,cache:false,now:()=>Date.UTC(2026,10,6)}).getDaily('2330.TW');
    expect(daily!.bars.map(day)).toEqual(['2026-11-03','2026-11-04','2026-11-05']);
    expect(daily!.closedSessions).not.toContain('2026-11-04');
    expect(marketClosuresSchema.safeParse({...policy,markets:{tw:{closedSessions:[{...evidence,positiveVolumeSymbols:5}]}}}).success).toBe(false);
  });
  it('retains the official April 6 index bar, including when the optional report fails',async()=>{
    const csv='Date,Open,High,Low,Close,Volume\n2021-04-01,207,208,206,207,\n2021-04-06,207.79,209.81,207.79,209.70,\n';
    for (const reportAvailable of [true,false]) {
      const missing={version:1,threshold:.95,markets:{tw:{closedSessions:[],missingSessions:[{
        date:'2021-04-06',reason:'missing-session-data',calendar:'XTAI',calendarSession:true,
        primaryIndexMissing:true,observedSymbols:0,officialSources:[{symbol:'^TWOII',source:'TPEx official cash-index OHLC'}],
      }]}}};
      const fetcher=async(url:RequestInfo|URL)=>String(url).endsWith('market-closures.json')
        ?reportAvailable?Response.json(missing):new Response('',{status:503})
        :String(url).includes('/index/')?Response.json(archiveIndexFixture('^TWOII',{2021:csv})):new Response(csv);
      const result=await new KlineArchive({fetcher,cache:false,now:()=>Date.UTC(2026,10,6)}).getDaily('^TWOII');
      expect(result!.bars.map(bar=>calendarDate(bar.time,getMarketProfile('^TWOII')))).toEqual(['2021-04-01','2021-04-06']);
      expect(result!.bars.at(-1)!.close).toBe(209.7);
      expect(result!.closedSessions).not.toContain('2021-04-06');
      for(const tf of ['1D','1W','1M'] as const) expect(archivePeriod(result!,'^TWOII',tf).bars.at(-1)!.close).toBe(209.7);
    }
  });
  it('daily, weekly and monthly aggregation all start after closure projection',()=>{
    const raw:BarResult={...parseArchiveCsv(body,'2330.TW',2026),closedSessions:['2026-10-23'],archive:{status:'archive',lastDate:'2026-10-27'}};
    expect(archivePeriod(raw,'2330.TW','1D').bars.map(day)).toEqual(['2026-10-22','2026-10-27']);
    expect(archivePeriod(raw,'2330.TW','1W').bars).toHaveLength(2);
    expect(archivePeriod(raw,'2330.TW','1M').bars).toHaveLength(1);
    expect(raw.bars).toHaveLength(3);
  });
});

describe('official closure evidence and physical removal', () => {
  const days = ['2022-02-04', '2023-01-18', '2024-10-31', '2026-07-10'];
  it('ignores stale missing-session markers for verified official closures', async () => {
    const stale = { version: 1, threshold: .95, markets: { tw: { closedSessions: [], missingSessions: days.map(date => ({
      date, reason: 'missing-session-data', calendar: 'XTAI', calendarSession: true,
      primaryIndexMissing: true, observedSymbols: 0, officialSources: [],
    })) } } };
    const policy = await loadMarketClosures(async () => Response.json(stale), Date.UTC(2026, 10, 7));
    for (const date of days) expect(closedMarketDates('2330.TW', policy).has(date)).toBe(true);
    expect(closedMarketDates('2330.TW', policy).has('2026-10-23')).toBe(true);
    expect(closedMarketDates('2330.TW', policy).has('2021-04-06')).toBe(false);
    expect(closedMarketDates('AAPL', policy).has('2024-10-31')).toBe(false);
  });
  it('requires both official dated empty responses and hashes', async () => {
    const seed = await loadMarketClosures(async () => new Response('', { status: 503 }), Date.UTC(2026, 10, 7));
    const proof = seed.markets.tw!.closedSessions.find(row => row.date === '2024-10-31')!;
    expect(proof.officialEvidence).toHaveLength(2);
    const validate = (row: unknown) => marketClosuresSchema.safeParse({ version: 1, threshold: .95, markets: { tw: { closedSessions: [row] } } }).success;
    expect(validate(proof)).toBe(true);
    expect(marketClosuresSchema.safeParse({ version: 1, threshold: .95, markets: { us: { closedSessions: [proof] } } }).success).toBe(false);
    expect(validate({ ...proof, officialEvidence: undefined })).toBe(false);
    expect(validate({ ...proof, date: '2024-10-30' })).toBe(false);
    for (const update of [{ url: 'https://example.com/' }, { quoteRows: 1 }, { sha256: 'invalid' }, { stat: 'unknown' }, { responseDate: '20241030' }])
      expect(validate({ ...proof, officialEvidence: [{ ...proof.officialEvidence![0], ...update }, proof.officialEvidence![1]] })).toBe(false);
  });
  it('retains the positive trading evidence veto', async () => {
    const policy = await loadMarketClosures(async () => Response.json({ version: 1, threshold: .95, markets: { tw: {
      closedSessions: [], indexGapsWithTrading: [{ date: '2024-10-31', reason: 'primary-index-gap-with-trading', observedSymbols: 100, positiveVolumeSymbols: 100 }],
    } } }), Date.UTC(2026, 10, 7));
    expect(closedMarketDates('2330.TW', policy).has('2024-10-31')).toBe(false);
  });
  it('keeps identical daily/weekly/monthly bars before and after physical removal', () => {
    const before = parseArchiveCsv(body, '2330.TW', 2026);
    const after = parseArchiveCsv(body.replace('2026-10-23,100,100,100,100,0\n', ''), '2330.TW', 2026);
    for (const tf of ['1D', '1W', '1M'] as const) {
      const a = archivePeriod(before, '2330.TW', tf), b = archivePeriod(after, '2330.TW', tf);
      expect(a.bars).toEqual(b.bars);
      expect(a.bars.every((bar, index, values) => index === 0 || bar.time > values[index - 1]!.time)).toBe(true);
      expect(a.bars.at(-1)!.close).toBe(101);
    }
  });
});
