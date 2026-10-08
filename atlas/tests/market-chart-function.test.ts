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
} from '../../supabase/functions/market-chart/validate.ts';
import { yahooIntervals } from '../src/market-data/YahooIntervals';

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
