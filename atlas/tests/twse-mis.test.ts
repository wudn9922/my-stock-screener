import { afterEach, describe, expect, it, vi } from 'vitest';
import { inMisSession, misChannel, misOnlyChart, overlayMis, parseMisRow, TwseMisClient } from '../../supabase/functions/market-chart/twse-mis';
import { liveDailySource, normalizeYahooResponse, TWSE_MIS_SOURCE } from '../src/market-data/YahooNormalizer';
import { normalizeLive } from '../src/market-data/LiveQuotes';
import { quoteStatus } from '../src/market-data/quoteStatus';
import { MIS_NOW, MIS_TODAY, misRows, misYahoo } from './fixtures/twse-mis';
import { mergeArchivedLive } from '../src/market-data/ArchiveFirstProvider';
import { ARCHIVE_SOURCE, type KlineArchive } from '../src/market-data/KlineArchive';
import type { BarResult } from '../src/market-data/MarketDataProvider';

afterEach(() => vi.useRealTimers());
const response = () => Response.json({ rtcode: '0000', msgArray: [...misRows(), { ...misRows()[1], c: '00632R', z: '1820' }] });

describe('TWSE MIS parsing and daily overlay', () => {
  it('maps listed, OTC, indices and letter-suffix ETFs without accepting foreign symbols', () => {
    expect(['2330.TW', '6488.TWO', '^TWII', '^TWOII', '00632R.TW', 'AAPL', '^GSPC'].map(misChannel))
      .toEqual(['tse_2330.tw', 'otc_6488.tw', 'tse_t00.tw', 'otc_o00.tw', 'tse_00632R.tw', null, null]);
    expect(parseMisRow({ ...misRows()[1], c: '00632R' }, '00632R.TW', MIS_NOW)).not.toBeNull();
  });
  it.each(['^TWII', '^TWOII'])('overlays %s once at 01:00 UTC, preserving history and unknown index volume', (symbol) => {
    const raw = misRows().find(row => row.c === (symbol === '^TWII' ? 't00' : 'o00'))!;
    const snapshot = parseMisRow(raw, symbol, MIS_NOW)!;
    const before = misYahoo(symbol, true), result = overlayMis(before, snapshot) as typeof before;
    const r = result.chart.result[0]!;
    expect(r.timestamp).toEqual([MIS_TODAY - 172800, MIS_TODAY - 86400, MIS_TODAY]);
    expect(r.indicators.quote[0]!.close.at(-1)).toBe(Number(raw.z));
    expect(r.indicators.quote[0]!.volume.at(-1)).toBeNull();
    expect(r.indicators.quote[0]!.close.slice(0, 2)).toEqual(before.chart.result[0]!.indicators.quote[0]!.close.slice(0, 2));
    expect(r.meta).toMatchObject({ regularMarketPrice: Number(raw.z), regularMarketTime: Date.parse('2026-11-05T01:46:35Z') / 1000, atlasLiveSource: 'twse-mis' });
    expect(before.chart.result[0]!.indicators.quote[0]!.close.at(-1)).toBeNull();
    expect(overlayMis(result, snapshot)).toEqual(result);
    const normalized = normalizeYahooResponse(result, MIS_NOW / 1000, '1D', symbol);
    expect(normalized.quote).toMatchObject({ source: TWSE_MIS_SOURCE, dataState: 'live', price: Number(raw.z) });
    expect(normalized.delayed).toBe(false);
  });
  it('deduplicates an existing #224 first-5m representation of today', () => {
    const snapshot = parseMisRow(misRows()[0], '^TWII', MIS_NOW)!;
    const base = misYahoo('^TWII', true), r = base.chart.result[0]!;
    r.timestamp.push(MIS_TODAY + 300);
    for (const field of Object.values(r.indicators.quote[0]!)) field.push(null);
    const out = overlayMis(base, snapshot) as typeof base;
    expect(out.chart.result[0]!.timestamp).toEqual([MIS_TODAY - 172800, MIS_TODAY - 86400, MIS_TODAY]);
  });
  it('uses shares for stock volume and never fabricates a close when z is empty', () => {
    const row = parseMisRow(misRows()[1], '2330.TW', MIS_NOW)!;
    expect(row.price).toBeNull(); expect(row.volume).toBe(1_234_000);
    const yesterday = misYahoo();
    expect(overlayMis(yesterday, row)).toBe(yesterday);
    expect(misOnlyChart(row)).toBeNull();
    const today = misYahoo('2330.TW', true), r = today.chart.result[0]!;
    r.indicators.quote[0]!.close[2] = 1818;
    const out = overlayMis(today, row) as typeof today;
    expect(out.chart.result[0]!.indicators.quote[0]).toMatchObject({ open: [1808, 1813, 1820], close: [1808, 1818, 1818], volume: [100000, 200000, 1234000] });
    expect(out.chart.result[0]!.meta).not.toHaveProperty('atlasLiveSource');
    expect(normalizeYahooResponse(out, MIS_NOW / 1000, '1D', '2330.TW').quote?.dataState).toBe('delayed');
  });
  it('reads the nested latest trade when the top-level z is "-" (observed 2026-11-06 shape)', () => {
    // MIS_NOW is 09:47 Taipei; the nested trade is two minutes earlier, inside today's high/low.
    const raw = { ...misRows()[1], trade: { t: '09:45:10', v: 1, z: '1835.0000', ft: 20 } };
    const row = parseMisRow(raw, '2330.TW', MIS_NOW)!;
    expect(row.price).toBe(1835);
    expect(row.priceTime).toBe(Date.parse('2026-11-05T09:45:10+08:00') / 1000);
    expect(row.volume).toBe(1_234_000);
    const today = misYahoo('2330.TW', true);
    const out = overlayMis(today, row) as typeof today;
    expect(out.chart.result[0]!.indicators.quote[0]!.close.at(-1)).toBe(1835);
    expect(out.chart.result[0]!.meta).toMatchObject({ atlasLiveSource: 'twse-mis', regularMarketPrice: 1835 });
    expect(normalizeYahooResponse(out, MIS_NOW / 1000, '1D', '2330.TW').quote?.dataState).toBe('live');
  });
  it.each([
    ['outside the high/low', { t: '09:45:10', z: '1900' }],
    ['before the session', { t: '08:59:59', z: '1820' }],
    ['after the session', { t: '13:30:01', z: '1820' }],
    ['in the future', { t: '09:53:00', z: '1820' }],
    ['malformed time', { t: '9:45', z: '1820' }],
    ['malformed price', { t: '09:45:10', z: '-' }],
    ['not an object', 'x'],
  ])('ignores a nested trade %s and keeps no price', (_, trade) => {
    const row = parseMisRow({ ...misRows()[1], trade }, '2330.TW', MIS_NOW)!;
    expect(row).not.toBeNull(); expect(row.price).toBeNull(); expect(row.priceTime).toBeNull();
  });
  it('keeps the top-level z when it is valid, even with a nested trade', () => {
    const row = parseMisRow({ ...misRows()[3], trade: { t: '09:40:00', z: '2870' } }, '6488.TWO', MIS_NOW)!;
    expect(row.price).toBe(2900); expect(row.priceTime).toBe(row.time);
    const index = parseMisRow(misRows()[0], '^TWII', MIS_NOW)!;
    expect(index.price).toBe(42306.23);
  });
  it('supports MIS-only OTC quotes with the actual previous close, without fake historic rows', () => {
    const row = parseMisRow(misRows()[2], '^TWOII', MIS_NOW)!;
    const normalized = normalizeLive(misOnlyChart(row), '^TWOII', MIS_NOW / 1000)!;
    expect(normalized.bars).toHaveLength(1);
    expect(normalized.quote).toMatchObject({ price: 329.65, change: expect.closeTo(-1.35), dataState: 'live' });
  });
  it.each([
    { d: '20261104' }, { d: '20260230' }, { t: '99:00:00' }, { t: '08:59:00' },
    { t: '12:00:00' }, { ex: 'otc' }, { h: '-' }, { l: '99999' }, { z: '99999' },
  ])('ignores stale, wrong-venue and malformed data %j', (change) => {
    expect(parseMisRow({ ...misRows()[0], ...change }, '^TWII', MIS_NOW)).toBeNull();
  });
  it('labels the source accurately, preserves historical provenance and restores Yahoo after MIS failure', () => {
    const row = parseMisRow(misRows()[0], '^TWII', MIS_NOW)!;
    const value = normalizeLive(misOnlyChart(row), '^TWII', MIS_NOW / 1000)!;
    const label = liveDailySource('TPEx 歷史', value.quote!);
    expect(label).toBe(`TPEx 歷史 · 今日來源：${TWSE_MIS_SOURCE}`);
    const fallback = normalizeLive(misYahoo('^TWII'), '^TWII', MIS_NOW / 1000)!;
    expect(liveDailySource(label, fallback.quote!)).not.toContain(TWSE_MIS_SOURCE);
    expect(quoteStatus('^TWII', value.quote, MIS_NOW, MIS_NOW)).toContain('即時報價');
    expect(quoteStatus('^TWII', fallback.quote, MIS_NOW, MIS_NOW)).toContain('延遲報價');
    const us = misYahoo('AAPL'); Object.assign(us.chart.result[0]!.meta, { atlasLiveSource: 'twse-mis' });
    expect(normalizeLive(us, 'AAPL', MIS_NOW / 1000)?.quote?.dataState).toBe('delayed');
  });
  it('keeps archived historical rows intact while applying MIS provenance, including a later Yahoo fallback', async () => {
    const history: BarResult = { ...normalizeYahooResponse(misYahoo('^TWII'), MIS_NOW / 1000, '1D', '^TWII'),
      source: ARCHIVE_SOURCE, archive: { lastDate: '2026-11-04', status: 'archive' } };
    const archive = { getDaily: vi.fn(async () => history) } as unknown as KlineArchive;
    const row = parseMisRow(misRows()[0], '^TWII', MIS_NOW)!;
    const current = normalizeLive(misOnlyChart(row), '^TWII', MIS_NOW / 1000)!;
    const merged = await mergeArchivedLive(history, current, '1D', archive);
    expect(merged.bars.slice(0, 2)).toEqual(history.bars);
    expect(merged.bars.at(-1)).toMatchObject({ time: MIS_TODAY, close: 42306.23 });
    expect(merged.source).toContain(ARCHIVE_SOURCE); expect(merged.source).toContain(TWSE_MIS_SOURCE);
    expect(merged).toMatchObject({ dataState: 'live', delayed: false, archive: { status: 'live' } });
    const fallback = await mergeArchivedLive(merged, normalizeLive(misYahoo('^TWII'), '^TWII', MIS_NOW / 1000)!, '1D', archive);
    expect(fallback.source).not.toContain(TWSE_MIS_SOURCE);
    expect(fallback).toMatchObject({ dataState: 'delayed', delayed: true });
  });
});

describe('TWSE MIS per-instance batching, caching and fallback', () => {
  it('coalesces a mixed batch, reuses concurrent calls, caches for 15s, and preserves the last actual trade time for z=-', async () => {
    let now = MIS_NOW;
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => response());
    const client = new TwseMisClient({ fetcher, now: () => now });
    const [batch] = await Promise.all([client.get(['^TWII', '2330.TW', '^TWOII', '6488.TWO', '00632R.TW', 'AAPL']), client.get(['^TWII'])]);
    expect(batch.get('00632R.TW')?.price).toBe(1820);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const url = new URL(String(fetcher.mock.calls[0]?.[0]));
    expect(url.searchParams.get('ex_ch')).toBe('tse_t00.tw|tse_2330.tw|otc_o00.tw|otc_6488.tw|tse_00632R.tw');
    expect(url.searchParams.get('delay')).toBe('0');
    now += 15001;
    fetcher.mockImplementation(async () => Response.json({ msgArray: [{ ...misRows()[0], z: '-', t: '09:47:10' }] }));
    const next = (await client.get(['^TWII'])).get('^TWII')!;
    expect(next.price).toBe(42306.23);
    expect(next.priceTime).toBe(Date.parse('2026-11-05T01:46:35Z') / 1000);
    now = Date.parse('2026-11-06T01:47:00Z');
    fetcher.mockImplementation(async () => Response.json({ msgArray: [{ ...misRows()[0], d: '20261106', z: '-' }] }));
    expect((await client.get(['^TWII'])).get('^TWII')!.price).toBeNull();
  });
  it('splits >40 symbols and spaces requests by at least one second', async () => {
    let now = MIS_NOW;
    const times: number[] = [];
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => { times.push(now); return Response.json({ msgArray: [] }); });
    const sleep = vi.fn(async (ms: number) => { now += ms; });
    const client = new TwseMisClient({ fetcher, now: () => now, sleep });
    await client.get(Array.from({ length: 83 }, (_, i) => `${1000 + i}.TW`));
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(times).toEqual([MIS_NOW, MIS_NOW + 1000, MIS_NOW + 2000]);
    for (const args of fetcher.mock.calls) expect(new URL(String(args[0])).searchParams.get('ex_ch')!.split('|').length).toBeLessThanOrEqual(40);
  });
  it.each(['http', 'json', 'shape', 'stale', 'network', 'api'])('fails softly and negatively caches %s failures', async (failure) => {
    const fetcher = vi.fn(async () => {
      if (failure === 'network') throw new Error('offline');
      if (failure === 'http') return new Response('no', { status: 503 });
      if (failure === 'json') return new Response('{');
      if (failure === 'shape') return Response.json({ bad: [] });
      if (failure === 'api') return Response.json({ rtcode: '5000', msgArray: misRows() });
      return Response.json({ msgArray: [{ ...misRows()[0], d: '20261104' }] });
    });
    const client = new TwseMisClient({ fetcher, now: () => MIS_NOW });
    expect(await client.get(['^TWII'])).toEqual(new Map());
    expect(await client.get(['^TWII'])).toEqual(new Map());
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('has a bounded timeout, with no network beyond the injected fake', async () => {
    const fetcher: typeof fetch = async (_url, init) => new Promise((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')), { once: true });
    });
    const client = new TwseMisClient({ fetcher, now: () => MIS_NOW, timeoutMs: 10 });
    expect(await client.get(['^TWII'])).toEqual(new Map());
  });
  it.each(['2026-11-05T00:59:59Z', '2026-11-05T05:30:01Z', '2026-11-07T01:47:00Z'])('never calls MIS outside regular session %s', async (date) => {
    const fetcher = vi.fn(async () => response());
    const now = Date.parse(date), client = new TwseMisClient({ fetcher, now: () => now });
    expect(inMisSession(now)).toBe(false);
    expect(await client.get(['^TWII'])).toEqual(new Map()); expect(fetcher).not.toHaveBeenCalled();
  });
});
