import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import { describe, expect, it, vi } from 'vitest';
import * as validation from '../../supabase/functions/market-chart/validate';
import * as mis from '../../supabase/functions/market-chart/twse-mis';
import { MIS_NOW, MIS_TODAY, misRows, misYahoo } from './fixtures/twse-mis';

function harness(options: { now?: number; fail?: string; fill?: boolean } = {}) {
  let now = options.now ?? MIS_NOW;
  let fail = options.fail;
  let serve!: (request: Request) => Promise<Response>;
  const calls: URL[] = [];
  const fetcher: typeof fetch = async (input) => {
    const url = new URL(String(input)); calls.push(url);
    if (url.hostname === 'mis.twse.com.tw') {
      if (fail === 'network' || fail === 'timeout') throw new DOMException('fixture failure', 'AbortError');
      if (fail === 'http') return new Response('offline', { status: 503 });
      if (fail === 'json') return new Response('{');
      if (fail === 'shape') return Response.json({ msgArray: {} });
      return Response.json({ msgArray: misRows().map(row => fail === 'stale' ? { ...row, d: '20261104' } : row) });
    }
    if (!validation.YAHOO_HOSTS.includes(url.hostname as typeof validation.YAHOO_HOSTS[number])) throw new Error('Unexpected network in offline fixture');
    const symbol = decodeURIComponent(url.pathname.split('/').at(-1)!);
    if (symbol === '^TWOII') return new Response('', { status: 404 });
    const raw = misYahoo(symbol, true);
    if (options.fill && symbol === '^TWII') {
      const r = raw.chart.result[0]!, q = r.indicators.quote[0]!;
      if (url.searchParams.get('interval') === '5m') {
        r.timestamp = [MIS_TODAY - 86400, MIS_TODAY - 86400 + 300];
        r.indicators.quote[0] = { open: [42900, 42920], high: [42940, 42960], low: [42890, 42910], close: [42920, 42948.91], volume: [100, 200] };
      } else for (const field of Object.values(q)) field[1] = null;
    }
    return Response.json(raw);
  };
  // Test dependency injection: every fetch, including the adapter's fetch, stays in this fixture.
  class BoundMis extends mis.TwseMisClient {
    constructor() { super({ fetcher, now: () => now, sleep: async (ms) => { now += ms; } }); }
  }
  class Clock extends Date { static now() { return now; } }
  const source = readFileSync(new URL('../../supabase/functions/market-chart/index.ts', import.meta.url), 'utf8')
    .replace(/^import[\s\S]*?from "[^"]+";\r?\n/gm, '');
  runInNewContext(transformSync(source, { loader: 'ts', target: 'es2022', format: 'cjs' }).code, {
    ...validation, ...mis, TwseMisClient: BoundMis,
    Deno: { env: { get: () => undefined }, serve: (handler: typeof serve) => { serve = handler; } },
    fetch: fetcher, Date: Clock, Request, Response, URL, URLSearchParams, AbortSignal, structuredClone,
    console: { log: vi.fn(), warn: vi.fn() },
  });
  return { calls, advance: (ms: number) => { now += ms; }, fail: (value: string) => { fail = value; },
    request: (query: string) => serve(new Request('https://offline.test/?' + query)),
    misCalls: () => calls.filter(url => url.hostname === 'mis.twse.com.tw') };
}

describe('market-chart endpoint MIS integration (all upstreams are fake)', () => {
  it('uses one MIS batch for mixed TW/US requests and supplies the otherwise absent OTC index', async () => {
    const h = harness(), response = await h.request('quotes=^TWII,2330.TW,^TWOII,6488.TWO,AAPL');
    expect(response.status).toBe(200);
    const { results, errors } = await response.json();
    expect(errors).toEqual({}); expect(h.misCalls()).toHaveLength(1);
    expect(h.misCalls()[0]!.searchParams.get('ex_ch')).toBe('tse_t00.tw|tse_2330.tw|otc_o00.tw|otc_6488.tw');
    expect(results['^TWII'].chart.result[0].meta).toMatchObject({ atlasLiveSource: 'twse-mis', regularMarketPrice: 42306.23 });
    expect(results['^TWOII'].chart.result[0].timestamp).toEqual([MIS_TODAY]);
    expect(results['6488.TWO'].chart.result[0].indicators.quote[0].volume.at(-1)).toBe(100000);
    expect(results['2330.TW'].chart.result[0].indicators.quote[0].close.at(-1)).toBeNull();
    expect(results.AAPL).toEqual(validation.slimChartPayload(misYahoo('AAPL', true)));
    await h.request('quotes=^TWII,6488.TWO'); expect(h.misCalls()).toHaveLength(1);
  });
  it.each(['^TWII', '6488.TWO', '^TWOII'])('overlays a daily chart request for %s with a short HTTP cache', async (symbol) => {
    const h = harness(), response = await h.request(`symbol=${encodeURIComponent(symbol)}&interval=1d&range=5y`);
    expect(response.status).toBe(200); expect(response.headers.get('Cache-Control')).toBe('public, max-age=15');
    const raw = await response.json();
    expect(raw.chart.result[0].timestamp.at(-1)).toBe(MIS_TODAY);
    expect(raw.chart.result[0].meta.atlasLiveSource).toBe('twse-mis'); expect(h.misCalls()).toHaveLength(1);
  });
  it.each(['network', 'timeout', 'http', 'json', 'shape', 'stale'])('keeps the full Yahoo payload after MIS %s', async (fail) => {
    const h = harness({ fail }), response = await h.request('symbol=%5ETWII&interval=1d&range=5y');
    expect(response.status).toBe(200); expect(await response.json()).toEqual(misYahoo('^TWII', true));
  });
  it('preserves #224 5m hole filling when MIS is unavailable', async () => {
    const h = harness({ fail: 'network', fill: true }), response = await h.request('symbol=%5ETWII&interval=1d&range=5y');
    const raw = await response.json();
    expect(raw.chart.result[0].meta.atlasIntradayFill).toContain('2026-11-04');
    expect(raw.chart.result[0].indicators.quote[0].close[1]).toBe(42948.91);
    expect(raw.chart.result[0].meta.atlasLiveSource).toBeUndefined();
    expect(h.calls.filter(url => url.searchParams.get('interval') === '5m')).toHaveLength(1);
  });
  it('does not cache the MIS overlay as Yahoo and restores the Yahoo source on failure', async () => {
    const h = harness();
    expect((await (await h.request('quotes=^TWII')).json()).results['^TWII'].chart.result[0].meta.atlasLiveSource).toBe('twse-mis');
    h.advance(21_000); h.fail('http');
    const next = await (await h.request('quotes=^TWII')).json();
    expect(next.results['^TWII']).toEqual(validation.slimChartPayload(misYahoo('^TWII', true)));
  });
  it.each(['2026-11-05T05:30:01Z', '2026-11-07T01:47:00Z'])('never calls MIS after the close or on a weekend: %s', async (date) => {
    const h = harness({ now: Date.parse(date) });
    const response = await h.request('symbol=2330.TW&interval=1d&range=5y');
    expect(await response.json()).toEqual(misYahoo('2330.TW', true)); expect(h.misCalls()).toHaveLength(0);
    expect(response.headers.get('Cache-Control')).toBe('public, max-age=600');
  });
  it('leaves US and intraday requests unchanged', async () => {
    const h = harness();
    for (const query of ['symbol=AAPL&interval=1d&range=5y', 'symbol=2330.TW&interval=5m&range=5d']) {
      const response = await h.request(query);
      expect(response.status).toBe(200);
      expect((await response.json()).chart.result[0].meta.atlasLiveSource).toBeUndefined();
    }
    expect(h.misCalls()).toHaveLength(0);
  });
});
