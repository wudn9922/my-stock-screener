import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
  RATES_CACHE_MS,
  createCachedLoader,
  fetchRates,
  fetchRatesHistory,
  parseRates,
  parseRatesHistory,
  ratesHistoryUrl,
  ratesUrl,
  type Rates,
  type RatesHistory,
} from '../src/report/rates';

// Synthetic fixtures shaped like the docs/report/rates*.json contract (not market data).
const fixture = (name: string) =>
  JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/rates', name), 'utf8')) as Record<string, unknown>;
const ratesRaw = fixture('rates.sample.json');
const historyRaw = fixture('rates-history.sample.json');

const okRates = (raw: unknown = ratesRaw): Rates => {
  const result = parseRates(raw);
  if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
  return result.data;
};
const okHistory = (raw: unknown = historyRaw): RatesHistory => {
  const result = parseRatesHistory(raw);
  if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
  return result.data;
};
const response = (body: string, status = 200) => new Response(body, { status });

describe('urls', () => {
  it('sit next to latest.json', () => {
    expect(ratesUrl('/my-stock-screener/atlas/', undefined, 'https://x.test')).toBe('https://x.test/my-stock-screener/report/rates.json');
    expect(ratesHistoryUrl('/my-stock-screener/atlas/', undefined, 'https://x.test')).toBe(
      'https://x.test/my-stock-screener/report/rates-history.json',
    );
    expect(ratesUrl('/', '/data/latest.json', 'https://x.test')).toBe('https://x.test/data/rates.json');
  });
});

describe('parseRates', () => {
  it('reads the full fixture', () => {
    const rates = okRates();
    expect(rates.asOf).toBe('2026-10-09');
    expect(rates.tenors).toHaveLength(11);
    expect(rates.realTenors).toEqual(['5Y', '7Y', '10Y', '20Y', '30Y']);
    const now = rates.curves.nominal.find((curve) => curve.key === 'now')!;
    expect(now.values).toHaveLength(11);
    expect(rates.curves.real.find((curve) => curve.key === 'now')!.values).toHaveLength(5);
    expect(rates.spreads.map((spread) => spread.key)).toEqual(['10y2y', '10y3m', '30y5y', '30y10y', '2y3m']);
    expect(rates.etfs.map((etf) => etf.symbol)).toEqual(['TLT', 'EDV', 'ZROZ', 'GOVZ', 'TMF']);
    const tmf = rates.etfs.find((etf) => etf.symbol === 'TMF')!;
    expect(tmf.leverage).toBe(3);
    expect(tmf.verified).toBe(false);
    expect(tmf.sensitivity.dgs30?.weekly3y?.durEmp).toBeGreaterThan(40);
    expect(rates.inflation['20Y']?.derived).toBe(true);
    expect(rates.inflation['10Y']?.derived).toBe(false);
    expect(rates.notes.length).toBeGreaterThan(0);
    expect(rates.policy?.upper).toBeCloseTo(3.75, 10);
  });

  it('ignores unknown fields and a legacy tmf block', () => {
    const rates = okRates({ ...ratesRaw, tmf: { windows: {} }, futureField: [1, 2, 3] });
    expect('tmf' in rates).toBe(false);
    expect('futureField' in rates).toBe(false);
  });

  it('coerces numeric strings, drops non-finite numbers and pads or trims curve values', () => {
    const raw = structuredClone(ratesRaw) as { curves: { nominal: { key: string; values: unknown[] }[] } };
    raw.curves.nominal[0]!.values = ['4.1', 'NaN', null, 'abc', 3, Number.POSITIVE_INFINITY];
    const rates = okRates(raw);
    const now = rates.curves.nominal[0]!;
    expect(now.values).toEqual([4.1, null, null, null, 3, null, null, null, null, null, null]);
    raw.curves.nominal[0]!.values = Array.from({ length: 14 }, () => 1);
    expect(okRates(raw).curves.nominal[0]!.values).toHaveLength(11);
  });

  it('drops a bad ETF or spread entry instead of failing the file', () => {
    const raw = structuredClone(ratesRaw) as { etfs: unknown[]; spreads: unknown[] };
    raw.etfs.push({ name: 'no symbol' }, 'garbage', null);
    raw.spreads.push({ valueBp: 3 }, 42);
    const rates = okRates(raw);
    expect(rates.etfs).toHaveLength(5);
    expect(rates.spreads).toHaveLength(5);
  });

  it('fills defaults for missing optional blocks', () => {
    const raw = structuredClone(ratesRaw) as Record<string, unknown>;
    delete raw.policy;
    delete raw.inflation;
    delete raw.missing;
    delete raw.tenors;
    delete raw.tenorYears;
    delete raw.notes;
    raw.sources = 'weird';
    const rates = okRates(raw);
    expect(rates.policy).toBeNull();
    expect(rates.inflation).toEqual({});
    expect(rates.missing).toEqual([]);
    expect(rates.tenors).toEqual(['1M', '3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y']);
    expect(rates.tenorYears[0]).toBeCloseTo(1 / 12, 3);
    expect(rates.sources).toEqual({ rates: null, etf: null, etfAsOf: null });
    expect(rates.notes).toEqual([]);
  });

  it('keeps an ETF with missing analytics as nulls', () => {
    const raw = structuredClone(ratesRaw) as { etfs: Record<string, unknown>[] };
    const govz = raw.etfs.find((etf) => etf.symbol === 'GOVZ')!;
    delete govz.returns;
    delete govz.risk;
    delete govz.sensitivity;
    govz.model = null;
    govz.expenseRatio = 'n/a';
    const parsed = okRates(raw).etfs.find((etf) => etf.symbol === 'GOVZ')!;
    expect(parsed.returns['1y']).toBeNull();
    expect(parsed.risk.vol1y).toBeNull();
    expect(parsed.risk.maxDrawdown).toBeNull();
    expect(parsed.sensitivity.dgs20).toBeNull();
    expect(parsed.model).toBeNull();
    expect(parsed.expenseRatio).toBeNull();
    expect(parsed.leverage).toBe(1);
  });

  it('reports missing or invalid states', () => {
    expect(parseRates(null).status).toBe('invalid');
    expect(parseRates([1, 2]).status).toBe('invalid');
    expect(parseRates('text').status).toBe('invalid');
    // An object with nothing usable means the generator has not produced data yet.
    expect(parseRates({ version: 1 }).status).toBe('missing');
    expect(parseRates({ version: 1, curves: { nominal: [] }, etfs: [] }).status).toBe('missing');
  });
});

describe('backend sample files (written by scripts/build_rates.py on synthetic input)', () => {
  const backendRates = fixture('backend-sample-rates.json');
  const backendHistory = fixture('backend-sample-rates-history.json');

  it('parse without dropping entries', () => {
    const rates = okRates(backendRates);
    expect(rates.etfs).toHaveLength((backendRates.etfs as unknown[]).length);
    expect(rates.spreads).toHaveLength((backendRates.spreads as unknown[]).length);
    expect(rates.curves.nominal.map((curve) => curve.key)).toEqual(['now', '1w', '1m', '3m', '1y', '2y']);
    for (const spread of rates.spreads) {
      // Legs are FRED IDs that exist in the history.
      expect(spread.long && spread.short).toBeTruthy();
    }
    const tmf = rates.etfs.find((etf) => etf.symbol === 'TMF')!;
    expect(tmf.leverage).toBe(3);
    // The model describes the unlevered ladder; the scenario applies the leverage.
    expect(tmf.model!.duration).toBeLessThan(35);
    expect(rates.etfs.every((etf) => etf.risk.mdd5y === null || typeof etf.risk.mdd5y === 'number')).toBe(true);
    expect(rates.inflation['30Y']).toMatchObject({ derived: true });
    const history = okHistory(backendHistory);
    for (const spread of rates.spreads) {
      expect(history.weekly.series[spread.long!]).toBeDefined();
      expect(history.weekly.series[spread.short!]).toBeDefined();
    }
    expect(history.etf.series.TLT![0]).toBe(100);
    expect(history.weekly.series.DFII30!.some((value) => value === null)).toBe(true);
  });
});

describe('parseRatesHistory', () => {
  it('reads the weekly, daily and ETF blocks', () => {
    const history = okHistory();
    expect(history.weekly.dates.length).toBeGreaterThan(1200);
    expect(history.weekly.dates[0]!.startsWith('2003-01')).toBe(true);
    expect(history.daily.dates).toHaveLength(260);
    expect(history.weekly.series.DGS10).toHaveLength(history.weekly.dates.length);
    // DFII30 was discontinued before 2010: leading nulls survive.
    expect(history.weekly.series.DFII30![0]).toBeNull();
    expect(history.etf.series.TLT).toHaveLength(history.etf.dates.length);
    expect(history.etf.series.GOVZ![0]).toBeNull();
  });

  it('drops points with bad dates, keeps the series aligned and sorts by date', () => {
    const history = okHistory({
      version: 1,
      weekly: {
        dates: ['2024-01-12', 'bad', '2024-01-05', '2024-01-19'],
        series: { DGS10: [4.1, 9, '4.0', null], DGS2: [4.3], JUNK: 'x' },
      },
    });
    expect(history.weekly.dates).toEqual(['2024-01-05', '2024-01-12', '2024-01-19']);
    expect(history.weekly.series.DGS10).toEqual([4.0, 4.1, null]);
    expect(history.weekly.series.DGS2).toEqual([null, 4.3, null]);
    expect(history.weekly.series.JUNK).toBeUndefined();
    expect(history.daily).toEqual({ dates: [], series: {} });
  });

  it('reports invalid and missing bodies', () => {
    expect(parseRatesHistory('<!doctype html>').status).toBe('invalid');
    expect(parseRatesHistory({ version: 1 }).status).toBe('missing');
  });
});

describe('fetch', () => {
  it('maps HTTP and body failures to friendly states', async () => {
    const notFound = vi.fn(async () => response('nope', 404));
    expect((await fetchRates('u', notFound as typeof fetch)).status).toBe('missing');
    const serverError = vi.fn(async () => response('oops', 503));
    const error = await fetchRates('u', serverError as typeof fetch);
    expect(error.status).toBe('error');
    expect(error.status !== 'ok' && error.message).toContain('503');
    const html = vi.fn(async () => response('<!doctype html><title>x</title>'));
    expect((await fetchRates('u', html as typeof fetch)).status).toBe('invalid');
    const offline = vi.fn(async () => {
      throw new TypeError('network');
    });
    expect((await fetchRatesHistory('u', offline as typeof fetch)).status).toBe('error');
    const good = vi.fn(async () => response(JSON.stringify(ratesRaw)));
    expect((await fetchRates('u', good as typeof fetch)).status).toBe('ok');
  });
});

describe('createCachedLoader', () => {
  it('caches a good result for 30 minutes and keeps it when a refresh fails', async () => {
    let clock = 1_000_000;
    const results = [
      { status: 'ok' as const, data: 'v1', url: 'u' },
      { status: 'error' as const, message: '暫時無法取得', url: 'u' },
      { status: 'ok' as const, data: 'v2', url: 'u' },
    ];
    const fetchOnce = vi.fn(async () => results.shift()!);
    const loader = createCachedLoader(fetchOnce, () => clock);
    expect(RATES_CACHE_MS).toBe(30 * 60 * 1000);
    const first = await loader.load();
    expect(first).toMatchObject({ status: 'ok', data: 'v1', stale: false });
    clock += RATES_CACHE_MS - 1;
    expect(await loader.load()).toBe(first);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    clock += 2;
    const second = await loader.load();
    expect(second).toMatchObject({ status: 'ok', data: 'v1', stale: true, message: '暫時無法取得' });
    expect(fetchOnce).toHaveBeenCalledTimes(2);
    // A failure is not pinned: the next call retries and picks up the new file.
    const third = await loader.load();
    expect(third).toMatchObject({ status: 'ok', data: 'v2', stale: false });
    expect(fetchOnce).toHaveBeenCalledTimes(3);
  });

  it('shares one request between concurrent callers and passes failures through without a good copy', async () => {
    const fetchOnce = vi.fn(async () => ({ status: 'missing' as const, message: '尚未產生', url: 'u' }));
    const loader = createCachedLoader(fetchOnce, () => 0);
    const [a, b] = await Promise.all([loader.load(), loader.load()]);
    expect(a).toBe(b);
    expect(a.status).toBe('missing');
    expect(fetchOnce).toHaveBeenCalledTimes(1);
    await loader.load();
    expect(fetchOnce).toHaveBeenCalledTimes(2);
  });

  it('refresh forces a new request inside the cache window', async () => {
    const fetchOnce = vi.fn(async () => ({ status: 'ok' as const, data: 1, url: 'u' }));
    const loader = createCachedLoader(fetchOnce, () => 0);
    await loader.load();
    await loader.load(true);
    expect(fetchOnce).toHaveBeenCalledTimes(2);
  });
});
