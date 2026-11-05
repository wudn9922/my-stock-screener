import { archiveIndexFixture } from './fixtures/archive-index';
import { describe, expect, it, vi } from 'vitest';
import { ArchiveFirstProvider, archiveQuote, mergeArchivedLive } from '../src/market-data/ArchiveFirstProvider';
import { KlineArchive } from '../src/market-data/KlineArchive';
import { EdgeYahooProvider } from '../src/market-data/EdgeYahooProvider';
import { aggregateBars } from '../src/market-data/StaticSeriesProvider';
import { getMarketProfile, type BarResult, type MarketDataProvider } from '../src/market-data/MarketDataProvider';

const body = 'Date,Open,High,Low,Close,Volume\n2026-01-02,100,102,99,101,1000\n2026-01-05,101,104,100,103,2000\n';
const make = () => {
  const calls: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input); calls.push(url);
    if(url.endsWith('/NEW.json'))return new Response('',{status:404});
    return new Response(url.endsWith('.json') ? JSON.stringify(archiveIndexFixture('AAPL',{2026:body})) : body);
  }) as typeof fetch;
  const archive = new KlineArchive({ fetcher, cache: false });
  const fallback = { id: 'test', supportedTimeframes: ['1D','1W','1M','5m'],
    getBars: vi.fn(async () => { throw new Error('Yahoo 502'); }),
    getQuote: vi.fn(async () => { throw new Error('Yahoo 502'); }),
  } satisfies MarketDataProvider;
  return { archive, fallback, calls };
};

describe('archive-first chart C2', () => {
  it('an intraday refresh is one fallback refresh, never a stale read first', async () => {
    const { archive, fallback } = make();
    const fresh = { bars: [], source: 'fresh', session: 'regular' as const, delayed: true, adjusted: true } as unknown as BarResult;
    const refreshing = { ...fallback, refreshBars: vi.fn(async () => fresh) };
    const provider = new ArchiveFirstProvider(refreshing, archive);
    await expect(provider.refreshBars('AAPL', '5m')).resolves.toBe(fresh);
    expect(refreshing.refreshBars).toHaveBeenCalledTimes(1);
    expect(fallback.getBars).not.toHaveBeenCalled();
  });
  it('BFCache restoration allows new requests without reviving an old pending fallback', async () => {
    const oldPage = new AbortController();
    let lifetime = oldPage.signal, finish!: (value: null) => void;
    const archive = {getDaily:vi.fn().mockReturnValueOnce(new Promise<null>(resolve=>{finish=resolve;})).mockResolvedValue(null)} as unknown as KlineArchive;
    const fallback = {id:'test',supportedTimeframes:['1D'] as const,
      getBars:vi.fn(async()=>({bars:[],source:'test',session:'regular' as const,delayed:true,adjusted:true})),
      getQuote:vi.fn(async()=>{throw new Error('unused');})};
    const provider=new ArchiveFirstProvider(fallback,archive,undefined,()=>lifetime);
    const pending=provider.getBars('AAPL','1D');
    oldPage.abort();lifetime=new AbortController().signal;finish(null);
    await expect(pending).rejects.toMatchObject({name:'AbortError'});
    expect(fallback.getBars).not.toHaveBeenCalled();
    await provider.getBars('AAPL','1D');
    expect(fallback.getBars).toHaveBeenCalledTimes(1);
  });
  it.each(['getBars','getQuote','getCorporateEvents'] as const)('does not start a late %s fallback after the owning document left', async method => {
    const page = new AbortController();
    let finish!: (value: null) => void;
    const archive = {getDaily:vi.fn(()=>new Promise<null>(resolve=>{finish=resolve;}))} as unknown as KlineArchive;
    const fallback = {id:'test',supportedTimeframes:['1D'] as const,
      getBars:vi.fn(async()=>{throw new Error('late fetch');}),getQuote:vi.fn(async()=>{throw new Error('late fetch');}),
      getCorporateEvents:vi.fn(async()=>{throw new Error('late fetch');})};
    const provider = new ArchiveFirstProvider(fallback,archive,undefined,()=>page.signal);
    const pending = method==='getBars' ? provider.getBars('AAPL','1D') : provider[method]('AAPL');
    page.abort();finish(null);
    await expect(pending).rejects.toMatchObject({name:'AbortError'});
    expect(fallback.getBars).not.toHaveBeenCalled();
    expect(fallback.getQuote).not.toHaveBeenCalled();
    expect(fallback.getCorporateEvents).not.toHaveBeenCalled();
  });
  it.each(['1D','1W','1M'] as const)('%s survives unavailable Yahoo without any five-year daily call', async timeframe => {
    const { archive, fallback } = make();
    const provider = new ArchiveFirstProvider(fallback, archive);
    const result = await provider.getBars('AAPL', timeframe);
    const daily = (await archive.getDaily('AAPL'))!;
    expect(result.bars).toEqual(timeframe === '1D' ? daily.bars : aggregateBars(daily.bars, timeframe, getMarketProfile('AAPL')));
    expect(result.archive).toMatchObject({ lastDate: '2026-01-05', status: 'archive' });
    expect(fallback.getBars).not.toHaveBeenCalled();
    expect((await provider.getQuote('AAPL')).price).toBe(103);
    expect(fallback.getQuote).not.toHaveBeenCalled();
  });
  it('falls back for absent symbols and preserves the intraday error', async () => {
    const { archive, fallback } = make();
    const provider = new ArchiveFirstProvider(fallback, archive);
    await expect(provider.getBars('NEW', '1D')).rejects.toThrow('Yahoo 502');
    await expect(provider.getBars('AAPL', '5m')).rejects.toThrow('Yahoo 502');
    expect(fallback.getBars).toHaveBeenCalledTimes(2);
  });
  it('older-period failure preserves the current archive bars and explains the limit', async () => {
    const { archive, fallback } = make();
    const yahoo = new EdgeYahooProvider('https://edge.test/market-chart', { fetcher: async () => new Response('', { status: 502 }) });
    const provider = new ArchiveFirstProvider(fallback, archive, yahoo);
    const current = await provider.getBars('AAPL', '1W');
    const extended = await provider.getOlderBars('AAPL', '1W', current);
    expect(extended.bars).toBe(current.bars);
    expect(extended.source).toContain('更早期間暫時無法取得');
    await expect(provider.getCorporateEvents('AAPL')).rejects.toThrow('502');
    expect((await provider.getBars('AAPL','1D')).bars).toHaveLength(2);
  });
  it('background events request monthly history with events and no 5-year daily request', async () => {
    const urls: string[] = [];
    const yahoo = new EdgeYahooProvider('/market-chart', { fetcher: async input => {
      urls.push(String(input));
      return new Response(JSON.stringify({ chart: { result: [{ meta: { symbol: 'AAPL' }, timestamp: [1767312000],
        indicators: { quote: [{ open:[100],high:[101],low:[99],close:[100],volume:[100] }] } }], error: null } }));
    } });
    await yahoo.getArchiveEvents('AAPL');
    expect(urls).toHaveLength(1);
    expect(urls[0]).toMatch(/^\/market-chart\?/);
    expect(new URL(urls[0]!, 'https://example.test').searchParams.get('interval')).toBe('1mo');
    expect(new URL(urls[0]!, 'https://example.test').searchParams.get('events')).toBe('1');
  });
  it('does not join older Yahoo periods on a different split basis; ignores the still-forming period for this check',async()=>{
    const {archive,fallback}=make();
    const yahoo=new EdgeYahooProvider('https://edge.test/market-chart');
    const provider=new ArchiveFirstProvider(fallback,archive,yahoo);
    const current=await provider.getBars('AAPL','1W');
    const half=(bar:typeof current.bars[number])=>({...bar,open:bar.open/2,high:bar.high/2,low:bar.low/2,close:bar.close/2});
    const native=vi.spyOn(yahoo,'getArchiveHistory').mockResolvedValue({...current,bars:current.bars.map((bar,i)=>i===current.bars.length-1?half(bar):bar)});
    const full=vi.spyOn(yahoo,'getBars').mockResolvedValue({...current,bars:(await archive.getDaily('AAPL'))!.bars.map(half)});
    expect((await provider.getOlderBars('AAPL','1W',current)).archive?.status).toBe('archive');
    expect(full).not.toHaveBeenCalled();
    native.mockResolvedValue({...current,bars:current.bars.map(half)});
    const result=await provider.getOlderBars('AAPL','1W',current);
    expect(result.archive?.status).toBe('split');expect(result.bars.every(bar=>bar.close<60)).toBe(true);
    expect(full).toHaveBeenCalledTimes(1);
  });
  it('merges missing dates, retains same-date archive values, and replaces a split price basis as a whole', async () => {
    const { archive, fallback } = make();
    const current = await new ArchiveFirstProvider(fallback, archive).getBars('AAPL', '1D');
    const last = current.bars.at(-1)!;
    const next = { ...last, time: last.time + 86400, close: 104 };
    const value = { symbol: 'AAPL', bars: [last, next], retrievedAt: 1800000000 };
    expect((await mergeArchivedLive(current, value, '1D', archive)).bars).toHaveLength(3);
    const split = { ...last, open: 50, high: 52, low: 49, close: 51 };
    const result = await mergeArchivedLive(current, { ...value, bars: [split] }, '1D', archive);
    expect(result.bars).toEqual([split]);
    expect(result.archive?.status).toBe('split'); expect(result.source).toContain('超過 2%');
    const yahoo = new EdgeYahooProvider('https://edge.test/market-chart');
    const fullBars = [{ ...split, time: split.time-86400 }, split];
    const fetchFull = vi.spyOn(yahoo, 'getBars').mockResolvedValue({ ...current, bars: fullBars });
    const full = await new ArchiveFirstProvider(fallback, archive, yahoo).getOlderBars('AAPL', '1D', result);
    expect(full.bars).toEqual(fullBars);
    expect(fetchFull).toHaveBeenCalledExactlyOnceWith('AAPL','1D',undefined,undefined);
    const updated = await mergeArchivedLive(full, { ...value, bars: [split] }, '1D', archive);
    expect(updated.bars).toEqual(fullBars); // later live ticks must not discard restored Yahoo history
  });
  it('archive quotes report the actual TW/US close (including daylight saving) rather than midnight', () => {
    const sample = (iso: string): BarResult => ({ bars: [{ time: Date.parse(iso)/1000, open:100,high:101,low:99,close:100,volume:1 }], source:'archive',session:'regular',delayed:true,adjusted:true });
    expect(new Date(archiveQuote('AAPL',sample('2026-01-05')).asOf*1000).toISOString()).toBe('2026-01-05T21:00:00.000Z');
    expect(new Date(archiveQuote('AAPL',sample('2026-07-06')).asOf*1000).toISOString()).toBe('2026-07-06T20:00:00.000Z');
    expect(new Date(archiveQuote('2330.TW',sample('2026-07-06')).asOf*1000).toISOString()).toBe('2026-07-06T05:30:00.000Z');
  });
});
