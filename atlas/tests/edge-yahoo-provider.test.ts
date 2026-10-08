import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  EDGE_PROXY_NOT_CONFIGURED,
  EDGE_YAHOO_SOURCE,
  EdgeYahooProvider,
  alignIndexCurrency,
} from '../src/market-data/EdgeYahooProvider';
import { MarketDataUnavailableError } from '../src/market-data/ProviderErrors';
import { normalizeYahooCalendarResponse, normalizeYahooResponse } from '../src/market-data/YahooNormalizer';

const PROXY = 'https://proj.supabase.co/functions/v1/market-chart';
const asOfMs = Date.parse('2026-10-07T20:05:00Z');

function fixture(name: string): unknown {
  return JSON.parse(readFileSync(new URL(`./fixtures/market/${name}`, import.meta.url), 'utf8'));
}

const smci: Record<string, unknown> = {
  '1d': fixture('SMCI-current-1d.json'),
  '1wk': fixture('SMCI-current-1wk.json'),
  '1mo': fixture('SMCI-current-1mo.json'),
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** Mock of the Edge Function: serves fixtures by `interval`, records every request. */
function edgeFetch(payloads: Record<string, unknown> = smci) {
  const calls: { url: URL; signal?: AbortSignal }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    calls.push({ url, signal: init?.signal ?? undefined });
    const payload = payloads[url.searchParams.get('interval') ?? ''];
    return payload ? json(payload) : json({ error: { code: 'not_found' } }, 404);
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls, mock: fetcher };
}

describe('EdgeYahooProvider', () => {
  it('requests the function with the Atlas interval/range and normalizes 1D exactly like the backend', async () => {
    const { fetcher, calls } = edgeFetch();
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => asOfMs });
    const result = await provider.getBars('smci', '1D');
    expect(calls[0]!.url.toString()).toBe(`${PROXY}?symbol=SMCI&interval=1d&range=5y&events=1`);
    const expected = normalizeYahooResponse(smci['1d'], asOfMs / 1000, '1D', 'SMCI');
    expect(result.bars).toEqual(expected.bars);
    expect(result.source).toBe(EDGE_YAHOO_SOURCE);
    expect(result.quote).toMatchObject({ symbol: 'SMCI', price: 44.94, source: EDGE_YAHOO_SOURCE });
    expect(result.market).toMatchObject({ market: 'US', currency: 'USD' });
    expect(result.latestBarAt).toBe(expected.bars.at(-1)!.time);
  });

  it('rebuilds the current week/month from verified daily rows (same as refresh-market)', async () => {
    const { fetcher, calls } = edgeFetch();
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => asOfMs });
    const weekly = await provider.getBars('SMCI', '1W');
    const expected = normalizeYahooCalendarResponse(smci['1wk'], smci['1d'], asOfMs / 1000, '1W', 'SMCI');
    expect(weekly.bars).toEqual(expected.bars);
    expect(weekly.bars.at(-1)).toMatchObject({ time: Date.parse('2026-10-05T00:00:00Z') / 1000, close: 44.94, volume: 90284251 });
    expect(weekly.normalization?.currentPeriod).toMatchObject({ method: 'daily-ohlcv', timeframe: '1W' });
    expect(weekly.source).toContain('本週 K由日 K彙總');
    expect(calls.map((call) => call.url.searchParams.get('interval')).sort()).toEqual(['1d', '1wk']);
    const monthly = await provider.getBars('SMCI', '1M');
    expect(monthly.bars.at(-1)!.time).toBe(Date.parse('2026-10-01T00:00:00Z') / 1000);
    // The cached daily payload is reused for the monthly request.
    expect(calls.map((call) => call.url.searchParams.get('interval')).sort()).toEqual(['1d', '1mo', '1wk']);
  });

  it('falls back to native aggregates when the current period cannot be verified', async () => {
    const brokenDaily = structuredClone(smci['1d']) as { chart: { result: { meta: { regularMarketTime: number } }[] } };
    brokenDaily.chart.result[0]!.meta.regularMarketTime = 1; // stale metadata → verification fails
    const { fetcher } = edgeFetch({ ...smci, '1d': brokenDaily });
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => asOfMs });
    const weekly = await provider.getBars('SMCI', '1W');
    expect(weekly.bars).toEqual(normalizeYahooResponse(smci['1wk'], asOfMs / 1000, '1W', 'SMCI').bars);
    expect(weekly.source).toContain('本週 K 未經日 K 校驗');
  });

  it('dedupes concurrent requests and serves bars, quote and events from one daily download', async () => {
    const { fetcher, mock } = edgeFetch();
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => asOfMs });
    const [bars, quote, events] = await Promise.all([
      provider.getBars('SMCI', '1D'),
      provider.getQuote('SMCI'),
      provider.getCorporateEvents('SMCI'),
    ]);
    expect(mock).toHaveBeenCalledTimes(1);
    expect(quote.price).toBe(bars.quote!.price);
    expect(events.status).toBe('available');
    if (events.status === 'available') {
      expect(events.events.some((event) => event.type === 'split')).toBe(true);
      expect(events.source).toBe(EDGE_YAHOO_SOURCE);
    }
  });

  it('keeps a short in-memory cache (60 s intraday, 5 min daily)', async () => {
    let now = asOfMs;
    const { fetcher, mock } = edgeFetch();
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => now });
    await provider.getBars('SMCI', '1D');
    now += 4 * 60_000;
    await provider.getBars('SMCI', '1D');
    expect(mock).toHaveBeenCalledTimes(1);
    now += 2 * 60_000;
    await expect(provider.getBars('SMCI', '1D')).resolves.toBeDefined();
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it('aborts the shared request only when every waiter has aborted', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const signals: AbortSignal[] = [];
    const fetcher = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init!.signal!);
      await gate;
      init!.signal!.throwIfAborted();
      return json(smci['1d']);
    }) as typeof fetch;
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => asOfMs });
    const first = new AbortController();
    const firstRequest = provider.getBars('SMCI', '1D', undefined, first.signal);
    const second = provider.getBars('SMCI', '1D');
    await Promise.resolve();
    first.abort();
    await expect(firstRequest).rejects.toMatchObject({ name: 'AbortError' });
    expect(signals[0]!.aborted).toBe(false);
    release();
    await expect(second).resolves.toMatchObject({ source: EDGE_YAHOO_SOURCE });

    let releaseLone!: () => void;
    const loneGate = new Promise<void>((resolve) => (releaseLone = resolve));
    const loneSignals: AbortSignal[] = [];
    const lone = new EdgeYahooProvider(PROXY, {
      now: () => asOfMs,
      fetcher: (async (_input: RequestInfo | URL, init?: RequestInit) => {
        loneSignals.push(init!.signal!);
        await loneGate;
        return json(smci['1d']);
      }) as typeof fetch,
    });
    const only = new AbortController();
    const onlyRequest = lone.getBars('SMCI', '1D', undefined, only.signal);
    await Promise.resolve();
    only.abort();
    await expect(onlyRequest).rejects.toMatchObject({ name: 'AbortError' });
    expect(loneSignals[0]!.aborted).toBe(true);
    releaseLone();
  });

  it('reports clear Chinese errors; transient failures are MarketDataUnavailableError', async () => {
    const failing = (status: number) =>
      new EdgeYahooProvider(PROXY, { now: () => asOfMs, fetcher: (async () => json({ error: {} }, status)) as typeof fetch });
    const unavailable = failing(503).getBars('NVDA', '1D');
    await expect(unavailable).rejects.toBeInstanceOf(MarketDataUnavailableError);
    await expect(unavailable).rejects.toThrow('暫時無法取得 NVDA 行情，請稍後再試');
    await expect(failing(502).getQuote('2330.TW')).rejects.toThrow('暫時無法取得 2330.TW 行情，請稍後再試');
    const notFound = failing(404).getBars('ZZZZ', '1D');
    await expect(notFound).rejects.toThrow('找不到 ZZZZ 的行情資料');
    await expect(notFound).rejects.not.toBeInstanceOf(MarketDataUnavailableError);
    const offline = new EdgeYahooProvider(PROXY, {
      now: () => asOfMs,
      fetcher: (async () => {
        throw new TypeError('Failed to fetch');
      }) as typeof fetch,
    });
    await expect(offline.getBars('NVDA', '5m')).rejects.toThrow('暫時無法取得 NVDA 行情，請稍後再試');
    const timeout = new EdgeYahooProvider(PROXY, {
      now: () => asOfMs,
      timeoutMs: 5,
      fetcher: ((_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_, reject) => init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason)))) as typeof fetch,
    });
    await expect(timeout.getBars('NVDA', '1D')).rejects.toBeInstanceOf(MarketDataUnavailableError);
    await expect(new EdgeYahooProvider('', { now: () => asOfMs }).getBars('NVDA', '1D')).rejects.toThrow(EDGE_PROXY_NOT_CONFIGURED);
    await expect(new EdgeYahooProvider(PROXY).getBars('NVDA', '4H')).rejects.toThrow('4H');
    await expect(new EdgeYahooProvider(PROXY).getBars('bad symbol!', '1D')).rejects.toThrow('請輸入有效的股票或指數代號');
  });

  it('wraps payloads that fail validation (e.g. symbol mismatch) with the symbol', async () => {
    const { fetcher } = edgeFetch();
    const provider = new EdgeYahooProvider(PROXY, { fetcher, now: () => asOfMs });
    await expect(provider.getBars('NVDA', '1D')).rejects.toThrow(/^NVDA 行情資料未通過驗證/);
  });

  it('serves Taiwan intraday bars with session-close provenance and TWD metadata', async () => {
    const now = Date.parse('2026-10-08T06:00:00Z');
    const raw = JSON.parse(readFileSync(new URL('./fixtures/market/taiwan/2330_TW_5m.json', import.meta.url), 'utf8'));
    const provider = new EdgeYahooProvider(PROXY, { now: () => now, fetcher: (async () => json(raw)) as typeof fetch });
    const result = await provider.getBars('2330.TW', '5m');
    const expected = normalizeYahooResponse(raw, now / 1000, '5m', '2330.TW');
    expect(result.bars).toEqual(expected.bars);
    expect(result.market).toMatchObject({ market: 'TW', currency: 'TWD', exchange: 'TWSE' });
    expect(result.source.startsWith(EDGE_YAHOO_SOURCE)).toBe(true);
    expect(result.sessionCloseObservations ?? []).toEqual(expected.sessionCloseObservations ?? []);
  });

  it('accepts world indices quoted in points but never relabels a stock currency', async () => {
    const raw = structuredClone(fixture('NFLX.json')) as { chart: { result: { meta: { symbol: string; currency: string } }[] } };
    raw.chart.result[0]!.meta.symbol = '^N225';
    raw.chart.result[0]!.meta.currency = 'JPY';
    const aligned = alignIndexCurrency(raw, '^N225') as typeof raw;
    expect(aligned.chart.result[0]!.meta.currency).toBe('USD');
    expect(raw.chart.result[0]!.meta.currency).toBe('JPY');
    const provider = new EdgeYahooProvider(PROXY, { now: () => Date.parse('2026-10-07T20:05:00Z'), fetcher: (async () => json(raw)) as typeof fetch });
    await expect(provider.getBars('^N225', '1D')).resolves.toMatchObject({ market: { market: 'US' } });
    const stock = structuredClone(fixture('NFLX.json')) as typeof raw;
    stock.chart.result[0]!.meta.currency = 'EUR';
    expect(alignIndexCurrency(stock, 'NFLX')).toBe(stock);
    const twIndex = structuredClone(raw);
    twIndex.chart.result[0]!.meta.symbol = '^TWII';
    expect(alignIndexCurrency(twIndex, '^TWII')).toBe(twIndex);
  });
});
