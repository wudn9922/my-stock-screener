import 'fake-indexeddb/auto';
import { describe, expect, it, vi } from 'vitest';
import { KlineArchive, mergeArchiveDaily, parseArchiveCsv } from '../src/market-data/KlineArchive';
import { TimeMapper, regularSessionOpen } from '../src/chart/TimeMapper';
import { getMarketProfile } from '../src/market-data/MarketProfile';

const csv = (year = 2026, price = 100, volume = '1000') => `Date,Open,High,Low,Close,Volume\n${year}-01-02,${price},${price + 1},${price - 1},${price},${volume}\n`;
const manifest = { version: 1, markets: { us: { symbols: ['AAPL'], firstDate: '2024-01-02', lastDate: '2026-01-02' } } };
const now = () => Date.UTC(2026, 10, 4);
const range = { from: Date.UTC(2025, 6, 4) / 1000 };

describe('archive reader C1', () => {
  it.each([
    ['AAPL','2026-01-05','2026-01-06'], ['AAPL','2026-07-06','2026-07-07'],
    ['2330.TW','2026-01-05','2026-01-06'], ['6488.TWO','2026-01-05','2026-01-06'],
    ['^TWOII','2026-01-05','2026-01-06'],
  ])('keeps existing %s daily drawing anchors on %s', (symbol,day,next)=>{
    const data=`Date,Open,High,Low,Close,Volume\n${day},100,101,99,100,1000\n${next},100,101,99,100,1000\n`;
    const profile=getMarketProfile(symbol),bars=parseArchiveCsv(data,symbol,2026).bars;
    const [year,month,date]=day.split('-').map(Number);
    const savedTime=symbol==='^TWOII'?Date.parse(day+'T00:00:00Z')/1000:regularSessionOpen(year!,month!,date!,profile);
    const mapper=new TimeMapper(bars,'1D',0,profile);
    expect(mapper.toLogical(savedTime)).toBe(0);
    expect(bars[0]!.time).toBe(savedTime);
  });
  it('reuses previous-year CSV in the existing IndexedDB cache across reader instances; current year refreshes on a new day', async () => {
    let clock = now();
    const paths: string[] = [];
    const fetcher = (async (url: RequestInfo | URL) => {
      paths.push(String(url));
      return new Response(String(url).endsWith('manifest.json') ? JSON.stringify(manifest) : csv(Number(String(url).match(/(\d{4})\.csv/)![1])));
    }) as typeof fetch;
    const options = { fetcher, now: () => clock, cache: { dbName: 'archive-unit-' + crypto.randomUUID() } };
    await new KlineArchive(options).getDaily('AAPL');
    expect(paths.filter(url=>url.endsWith('.csv'))).toHaveLength(3);
    paths.length = 0;
    await new KlineArchive(options).getDaily('AAPL');
    expect(paths.filter(url=>url.endsWith('.csv'))).toHaveLength(0);
    clock += 86400000;
    paths.length = 0;
    await new KlineArchive(options).getDaily('AAPL');
    expect(paths.filter(url=>url.endsWith('.csv'))).toHaveLength(1);
    expect(paths.at(-1)).toContain('2026.csv');
  });
  it('reads manifest once and only needed year files; de-duplicates simultaneous consumers', async () => {
    const paths: string[] = [];
    const fetcher = vi.fn(async (url: RequestInfo | URL) => {
      const path = String(url).replace('https://archive.test/', ''); paths.push(path);
      return new Response(path === 'manifest.json' ? JSON.stringify(manifest) : csv(Number(path.match(/(\d{4})\.csv/)![1])));
    }) as typeof fetch;
    const archive = new KlineArchive({ fetcher, baseUrl: 'https://archive.test/', cache: false, now });
    const [first, second] = await Promise.all([archive.getDaily('AAPL', range), archive.getDaily('AAPL', range)]);
    expect(first?.bars).toEqual(second?.bars);
    expect(paths.sort()).toEqual(['manifest.json', 'us/AAPL/2025.csv', 'us/AAPL/2026.csv']);
    expect(await archive.getDaily('NEW')).toBeNull();
    expect(paths).toHaveLength(3);
  });
  it('v2 uses exact membership, encoded path, year hashes and actual IPO date', async () => {
    const body = csv(2026);
    const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body)))].map(b => b.toString(16).padStart(2, '0')).join('');
    const m = { version: 2, markets: { tw: { symbols: ['^TWII'], firstDate: '2021-01-01', lastDate: '2026-01-02' } }, bySymbol: {
      '^TWII': { market: 'tw', path: 'tw/%5ETWII', firstDate: '2026-01-02', lastDate: '2026-01-02', years: { 2026: { sha256 } } },
    } };
    const fetcher = vi.fn(async (url: RequestInfo | URL) => new Response(String(url).endsWith('manifest.json') ? JSON.stringify(m) : body)) as typeof fetch;
    expect((await new KlineArchive({ fetcher, cache: false, now }).getDaily('^TWII'))?.bars).toHaveLength(1);
    expect(vi.mocked(fetcher).mock.calls.map(call => String(call[0]))).toContain('https://raw.githubusercontent.com/wudn9922/my-stock-screener/kline-archive/tw/%5ETWII/2026.csv');
    m.bySymbol['^TWII'].years[2026].sha256 = '0'.repeat(64);
    await expect(new KlineArchive({ fetcher, cache: false, now }).getDaily('^TWII')).rejects.toThrow('年度檔');
  });
  it('keeps partial available history when an older year is missing; retries failed files', async () => {
    let fail = true;
    const fetcher = vi.fn(async (url: RequestInfo | URL) => new Response(String(url).endsWith('manifest.json') ? JSON.stringify(manifest)
      : String(url).endsWith('2026.csv') || !fail ? csv(Number(String(url).match(/(\d{4})\.csv/)![1])) : '', { status: String(url).endsWith('manifest.json') || String(url).endsWith('2026.csv') || !fail ? 200 : 404 })) as typeof fetch;
    const archive = new KlineArchive({ fetcher, cache: false, now });
    const first = await archive.getDaily('AAPL');
    expect(first?.bars).toHaveLength(1); expect(first?.source).toContain('部分年度');
    fail = false;
    expect((await archive.getDaily('AAPL'))?.bars).toHaveLength(3);
  });
  it('rejects bad dates, year mismatch and headers, distinguishes unknown from zero shares', () => {
    expect(parseArchiveCsv(csv(2026, 100, ''), 'AAPL', 2026).missingVolumeAt).toHaveLength(1);
    expect(parseArchiveCsv(csv(2026, 100, '0'), 'AAPL', 2026).missingVolumeAt).toHaveLength(0);
    expect(parseArchiveCsv(csv(2026), '2330.TW', 2026).bars[0]?.volume).toBe(1000);
    expect(() => parseArchiveCsv(csv(2026), 'AAPL', 2025)).toThrow('日期');
    expect(() => parseArchiveCsv(csv().replace('01-02', '02-30'), 'AAPL', 2026)).toThrow('日期');
    expect(() => parseArchiveCsv(csv().replace('Volume', 'Shares'), 'AAPL', 2026)).toThrow('欄位');
    expect(() => parseArchiveCsv(csv().replace(',101,99,', ',1,99,'), 'AAPL', 2026)).toThrow('有效');
  });
  it('compares dates across timestamp conventions; archives win small differences, splits never join price bases', () => {
    const old = parseArchiveCsv(csv(), 'AAPL', 2026).bars;
    const shifted = { ...old[0]!, time: Date.parse('2026-01-02T00:00:00Z')/1000, close: 100.01 };
    expect(mergeArchiveDaily(old, [shifted], 'AAPL')).toEqual({ bars: old, splitMismatch: false });
    const split = { ...shifted, open: 50, high: 51, low: 49, close: 50 };
    expect(mergeArchiveDaily(old, [split], 'AAPL')).toEqual({ bars: [split], splitMismatch: true });
    const next = { ...old[0]!, time: old[0]!.time + 86400 };
    expect(mergeArchiveDaily(old, [next], 'AAPL').bars).toHaveLength(2);
  });
  it('manifest unavailable fails explicitly; aborting one consumer leaves shared requests usable', async () => {
    const archive = new KlineArchive({ fetcher: async () => new Response('', { status: 503 }), cache: false });
    await expect(archive.getDaily('AAPL')).rejects.toThrow('HTTP 503');
    const controller = new AbortController(); controller.abort();
    await expect(archive.getDaily('AAPL', undefined, controller.signal)).rejects.toThrow();
  });
});
