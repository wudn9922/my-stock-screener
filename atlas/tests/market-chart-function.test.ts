import { describe, expect, it } from 'vitest';
import {
  ALLOWED_INTERVAL_RANGES,
  buildYahooChartUrl,
  cacheControlFor,
  classifyUpstreamStatus,
  corsHeaders,
  isAllowedOrigin,
  isChartPayload,
  parseAllowedOrigins,
  validateChartRequest,
  fillDailyFromIntraday,
  INTRADAY_FILL_REQUEST,
  needsIntradayFill,
} from '../../supabase/functions/market-chart/validate.ts';
import { yahooIntervals } from '../src/market-data/YahooIntervals';
import { normalizeYahooResponse } from '../src/market-data/YahooNormalizer';

const params = (query: string) => new URLSearchParams(query);

describe('market-chart Edge Function request validation', () => {
  it.each(['NVDA', 'BRK-B', 'brk.b', 'A', 'GOOGL', '2330.TW', '6488.TWO', '00632R.TW', '^TWII', '^TWOII', '^GSPC', '^N225', '000001.SS', '399001.SZ'])(
    'accepts %s',
    (symbol) => {
      const result = validateChartRequest(params(`symbol=${encodeURIComponent(symbol)}&interval=1d&range=5y`));
      expect(result.ok).toBe(true);
      if (result.ok) expect(result.value.symbol).toBe(symbol.toUpperCase());
    },
  );

  it.each(['', 'NVDA;DROP', '../etc', 'https://evil', '2330', '2330.TT', '^', 'ABCDEFGHIJK', 'BRK-BBB', '<b>', '1234567.TW', 'nvda%00', '00001.SS'])(
    'rejects symbol %j',
    (symbol) => {
      const result = validateChartRequest(params(`symbol=${encodeURIComponent(symbol)}&interval=1d&range=5y`));
      expect(result).toMatchObject({ ok: false, status: 400, code: 'invalid_symbol' });
    },
  );

  it('whitelists interval/range pairs and covers every Atlas Yahoo interval', () => {
    for (const { interval, range } of Object.values(yahooIntervals)) {
      expect(ALLOWED_INTERVAL_RANGES[interval]).toContain(range);
      expect(validateChartRequest(params(`symbol=NVDA&interval=${interval}&range=${range}`)).ok).toBe(true);
    }
    expect(validateChartRequest(params('symbol=NVDA&interval=1m&range=1d'))).toMatchObject({ ok: false, code: 'invalid_interval' });
    expect(validateChartRequest(params('symbol=NVDA&interval=5m&range=5y'))).toMatchObject({ ok: false, code: 'invalid_range' });
    expect(validateChartRequest(params('symbol=NVDA&interval=constructor&range=1y'))).toMatchObject({ ok: false, code: 'invalid_interval' });
    expect(validateChartRequest(params('symbol=NVDA&interval=1d'))).toMatchObject({ ok: false, code: 'invalid_range' });
  });

  it('parses events and builds the Yahoo URL with fixed query parameters', () => {
    const on = validateChartRequest(params('symbol=%5ETWII&interval=1d&range=5y&events=1'));
    const off = validateChartRequest(params('symbol=2330.TW&interval=5m&range=1mo&events=0'));
    expect(validateChartRequest(params('symbol=NVDA&interval=1d&range=5y&events=yes'))).toMatchObject({ ok: false, code: 'invalid_events' });
    if (!on.ok || !off.ok) throw new Error('expected valid requests');
    expect(on.value.events).toBe(true);
    expect(buildYahooChartUrl('query2.finance.yahoo.com', on.value)).toBe(
      'https://query2.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&range=5y&events=div%2Csplits&includePrePost=false',
    );
    expect(buildYahooChartUrl('query1.finance.yahoo.com', off.value)).toBe(
      'https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?interval=5m&range=1mo&includePrePost=false',
    );
  });

  it('sets cache lifetimes by interval', () => {
    expect(cacheControlFor('5m')).toBe('public, max-age=60');
    expect(cacheControlFor('60m')).toBe('public, max-age=60');
    expect(cacheControlFor('1d')).toBe('public, max-age=600');
    expect(cacheControlFor('1mo')).toBe('public, max-age=600');
  });

  it('restricts CORS to the Pages origin and localhost', () => {
    expect(isAllowedOrigin('https://wudn9922.github.io')).toBe(true);
    expect(isAllowedOrigin('http://localhost:5173')).toBe(true);
    expect(isAllowedOrigin('http://127.0.0.1:4173')).toBe(true);
    expect(isAllowedOrigin('https://wudn9922.github.io.evil.com')).toBe(false);
    expect(isAllowedOrigin('https://example.com')).toBe(false);
    expect(isAllowedOrigin('http://localhost.evil.com')).toBe(false);
    expect(isAllowedOrigin(null)).toBe(false);
    expect(corsHeaders('https://wudn9922.github.io')['Access-Control-Allow-Origin']).toBe('https://wudn9922.github.io');
    expect(corsHeaders('https://example.com')['Access-Control-Allow-Origin']).toBeUndefined();
    expect(corsHeaders(null)).toMatchObject({ Vary: 'Origin', 'Access-Control-Allow-Methods': 'GET, OPTIONS' });
    expect(parseAllowedOrigins('https://a.example/, javascript:alert(1), https://b.example')).toEqual(['https://a.example', 'https://b.example']);
    expect(parseAllowedOrigins('')).toEqual(['https://wudn9922.github.io']);
  });

  it('maps upstream failures to sanitized statuses and checks the payload shape', () => {
    expect(classifyUpstreamStatus(404)).toMatchObject({ status: 404, retryOtherHost: false });
    expect(classifyUpstreamStatus(429)).toMatchObject({ status: 503, code: 'rate_limited', retryOtherHost: true });
    expect(classifyUpstreamStatus(500)).toMatchObject({ status: 502, retryOtherHost: true });
    expect(isChartPayload({ chart: { result: [{}] } })).toBe(true);
    expect(isChartPayload({ chart: { result: null, error: { code: 'Not Found' } } })).toBe(false);
    expect(isChartPayload('nope')).toBe(false);
  });
});

describe('market-chart rebuilds empty recent daily rows from 5-minute bars', () => {
  // Shape observed for ^TWII on 2026-11-05 09:18 Taipei: 11/03 has a close, 11/04 and 11/05 are
  // all null, while meta already carries the 11/04 close.
  const D3 = 1793667600, D4 = 1793754000, D5 = 1793840400;
  const daily = () => ({ chart: { result: [{
    meta: { symbol: '^TWII', gmtoffset: 28800, regularMarketTime: 1793770395, regularMarketPrice: 42948.91 },
    timestamp: [D3, D4, D5],
    indicators: { quote: [{ open: [44481.59, null, null], high: [44614.64, null, null], low: [43191.58, null, null], close: [43307.54, null, null], volume: [4346200, null, null] }] },
  }], error: null } });
  const minute = (days: number[]) => {
    const timestamp: number[] = [], open: number[] = [], high: number[] = [], low: number[] = [], close: number[] = [], volume: (number | null)[] = [];
    for (const day of days) for (let i = 0; i < 3; i++) {
      timestamp.push(day + i * 300); open.push(43000 + i); high.push(43100 + i); low.push(42900 - i); close.push(43050 + i); volume.push(i === 1 ? null : 100);
    }
    return { chart: { result: [{ meta: { symbol: '^TWII', gmtoffset: 28800 }, timestamp, indicators: { quote: [{ open, high, low, close, volume }] } }], error: null } };
  };

  it('fills only empty rows, ends the quote session at meta and leaves rows with prices alone', () => {
    expect(needsIntradayFill(daily())).toBe(true);
    const { payload, filled } = fillDailyFromIntraday(daily(), minute([D3, D4]));
    expect(filled).toEqual(['2026-11-04']);
    const q = (payload as ReturnType<typeof daily>).chart.result[0]!.indicators.quote[0]!;
    expect(q.close.slice(0, 2)).toEqual([43307.54, 42948.91]);
    expect(q.open[1]).toBe(43000);
    expect(q.high[1]).toBe(43102);
    expect(q.low[1]).toBe(42898);
    expect(q.volume[1]).toBe(200);
    expect(q.close[2]).toBeNull();
    expect(daily().chart.result[0]!.indicators.quote[0]!.close[1]).toBeNull();
  });

  it('the site normalizer then shows the rebuilt 11/04 index bar and quote', () => {
    const base = daily();
    Object.assign(base.chart.result[0]!.meta, { currency: 'TWD', exchangeTimezoneName: 'Asia/Taipei' });
    const { payload } = fillDailyFromIntraday(base, minute([D3, D4]));
    const result = normalizeYahooResponse(payload, 1793771000, '1D', '^TWII');
    expect(result.bars.at(-1)).toMatchObject({ time: D4, close: 42948.91 });
    expect(result.quote).toMatchObject({ price: 42948.91 });
  });

  it('appends a session the daily series does not list yet', () => {
    const base = daily();
    base.chart.result[0]!.timestamp = [D3];
    for (const values of Object.values(base.chart.result[0]!.indicators.quote[0]!)) values.splice(1);
    const { payload, filled } = fillDailyFromIntraday(base, minute([D4, D5]));
    expect(filled).toEqual(['2026-11-04', '2026-11-05']);
    expect((payload as ReturnType<typeof daily>).chart.result[0]!.timestamp).toEqual([D3, D4, D5]);
  });

  it('does not request 5-minute bars when the latest session row already has prices', () => {
    // US after the close: 11/04 has open/high/low but a null close; the client heals it from meta.
    const us = { chart: { result: [{ meta: { symbol: 'GOOGL', gmtoffset: -18000, regularMarketTime: 1793826001, regularMarketPrice: 288.93 },
      timestamp: [1793716200, 1793802600], indicators: { quote: [{ open: [280.23, 283.51], high: [281.61, 296.49], low: [277.2, 283.51], close: [280.03, null], volume: [34974500, 51316963] }] } }], error: null } };
    expect(needsIntradayFill(us)).toBe(false);
    // Before the Taiwan open the quote is still yesterday's: nothing to rebuild.
    const early = daily();
    early.chart.result[0]!.indicators.quote[0]!.close[1] = 42948.91;
    early.chart.result[0]!.indicators.quote[0]!.open[1] = 43000;
    expect(needsIntradayFill(early)).toBe(false);
  });

  it('keeps the payload unchanged when 5-minute bars have nothing usable', () => {
    const { payload, filled } = fillDailyFromIntraday(daily(), { chart: { result: [{ timestamp: [], indicators: { quote: [{}] } }] } });
    expect(filled).toEqual([]);
    expect(payload).toEqual(daily());
    expect(INTRADAY_FILL_REQUEST).toEqual({ interval: '5m', range: '5d' });
  });
});
