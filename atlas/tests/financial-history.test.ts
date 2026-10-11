import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
  FinancialsProvider,
  fetchFinancials,
  financialsKey,
  financialsUrl,
  hasSecHistory,
  parseFinancials,
  provenance,
} from '../src/fundamentals/FinancialHistory';

// Synthetic fixtures (made-up numbers in the contract's shape), see tests/fixtures/financials/.
const fixture = (name: string) =>
  JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')) as Record<string, unknown>;

const ok = (data: unknown) => {
  const result = parseFinancials(data);
  if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
  return result.data;
};
const response = (status: number, body: string) => new Response(body, { status });

describe('financialsKey', () => {
  it('maps symbols to file keys like growthKey', () => {
    expect(financialsKey('aapl')).toBe('AAPL');
    expect(financialsKey(' BRK.B ')).toBe('BRK-B');
    expect(financialsKey('BRK-B')).toBe('BRK-B');
    expect(financialsKey('2330.TW')).toBe('2330.TW');
    expect(financialsKey('6488.two')).toBe('6488.TWO');
    expect(financialsKey('00632R.TW')).toBe('00632R.TW');
  });
  it('rejects indices, other exchanges and junk', () => {
    expect(financialsKey('^GSPC')).toBeNull();
    expect(financialsKey('^TWII')).toBeNull();
    expect(financialsKey('000001.SS')).toBeNull();
    expect(financialsKey('')).toBeNull();
    expect(financialsKey(undefined)).toBeNull();
    expect(financialsKey('../etc/passwd')).toBeNull();
  });
  it('builds the URL next to latest.json', () => {
    expect(financialsUrl('AAPL', '/my-stock-screener/atlas/', undefined, 'https://example.test')).toBe(
      'https://example.test/my-stock-screener/report/financials/AAPL.json',
    );
    expect(financialsUrl('2330.TW', '/', undefined, 'https://example.test')).toBe('https://example.test/report/financials/2330.TW.json');
  });
});

describe('parseFinancials', () => {
  it('parses the full US fixture with mixed SEC / Yahoo provenance', () => {
    const data = ok(fixture('us-full.json'));
    expect(data.symbol).toBe('AAPL');
    expect(data.market).toBe('US');
    expect(data.currency).toBe('USD');
    expect(data.fyEndMonth).toBe(9);
    expect(data.annual.end).toHaveLength(10);
    expect(data.quarterly.end).toHaveLength(15);
    expect(data.annual.src.slice(0, 6)).toEqual(Array(6).fill('sec'));
    expect(data.annual.src.slice(6)).toEqual(Array(4).fill('yahoo'));
    expect(data.split.events).toEqual([{ date: '2020-08-31', ratio: 4 }]);
    expect(data.quality.issues).toContain('yahoo-differs');
    expect(hasSecHistory(data)).toBe(true);
    expect(provenance(data.annual)).toEqual([
      { source: 'sec', from: '2015-09-26', to: '2020-09-26', count: 6 },
      { source: 'yahoo', from: '2021-09-25', to: '2024-09-28', count: 4 },
    ]);
    // Fields absent from the file become all-null columns, aligned with `end`.
    expect(data.annual.values.operatingIncomeReported.slice(0, 6)).toEqual(Array(6).fill(null));
    expect(data.annual.values.revenue.every((value) => typeof value === 'number')).toBe(true);
  });

  it('parses TWD, bank, loss-maker and short-history fixtures', () => {
    const tsmc = ok(fixture('tsmc-twd.json'));
    expect(tsmc.currency).toBe('TWD');
    expect(tsmc.market).toBe('TW');
    expect(tsmc.annual.src).toEqual(Array(4).fill('yahoo'));
    expect(hasSecHistory(tsmc)).toBe(false);
    const bank = ok(fixture('bank.json'));
    expect(bank.annual.values.grossProfit).toEqual(Array(5).fill(null));
    expect(bank.quality.issues).toContain('financial-sector-format');
    const loss = ok(fixture('loss-maker.json'));
    expect(loss.annual.values.equity).toContain(-0.3e9);
    const short = ok(fixture('short-history.json'));
    expect(short.foreignReporter).toBe(true);
    expect(short.currency).toBe('DKK');
    expect(short.quality.level).toBe('limited');
  });

  it('drops bad dates with their values, sorts, de-duplicates and coerces numbers', () => {
    const data = ok({
      version: 1,
      symbol: 'abc',
      annual: {
        end: ['2023-12-31', 'garbage', '2021-12-31', '2022-02-30', '2022-12-31', '2023-12-31'],
        fy: [2023, 0, 2021, 0, '2022', 2023],
        src: ['yahoo', 'sec', 'sec', 'sec', 'other', 'sec'],
        values: {
          revenue: ['100', 1, 80, 2, 90, 110],
          capex: [-5, 0, 4, 0, 3, -6],
          netIncome: ['NaN', null, 8, null, 9],
          unknownField: [1, 2, 3, 4, 5, 6],
        },
      },
    });
    expect(data.symbol).toBe('ABC');
    expect(data.market).toBe('US');
    expect(data.currency).toBe('USD');
    expect(data.annual.end).toEqual(['2021-12-31', '2022-12-31', '2023-12-31']);
    // The later duplicate of 2023-12-31 wins.
    expect(data.annual.values.revenue).toEqual([80, 90, 110]);
    expect(data.annual.values.capex).toEqual([4, 3, 6]);
    expect(data.annual.values.netIncome).toEqual([8, 9, null]);
    expect(data.annual.fy).toEqual([2021, 2022, 2023]);
    expect(data.annual.src).toEqual(['sec', 'yahoo', 'sec']);
    expect(data.annual.values.equity).toEqual([null, null, null]);
    expect(data.quarterly.end).toEqual([]);
  });

  it('a missing or short src array means Yahoo', () => {
    const data = ok({ annual: { end: ['2023-12-31', '2024-12-31'], src: ['sec'], values: { revenue: [1, 2] } } });
    expect(data.annual.src).toEqual(['sec', 'yahoo']);
    expect(provenance(data.annual)).toHaveLength(2);
    const plain = ok({ annual: { end: ['2023-12-31'], values: {} } });
    expect(plain.annual.src).toEqual(['yahoo']);
  });

  it('keeps the newest 20 annual and 60 quarterly periods', () => {
    const years = Array.from({ length: 25 }, (_, index) => `${2000 + index}-12-31`);
    const quarters = Array.from({ length: 70 }, (_, index) => {
      const year = 2000 + Math.floor(index / 4);
      return `${year}-${['03-31', '06-30', '09-30', '12-31'][index % 4]}`;
    });
    const data = ok({
      annual: { end: years, values: { revenue: years.map((_, index) => index) } },
      quarterly: { end: quarters, fq: quarters.map((_, index) => (index % 4) + 1), values: {} },
    });
    expect(data.annual.end).toHaveLength(20);
    expect(data.annual.end[0]).toBe('2005-12-31');
    expect(data.annual.values.revenue[0]).toBe(5);
    expect(data.quarterly.end).toHaveLength(60);
    expect(data.quarterly.fq[0]).toBe(3);
  });

  it('infers the market and currency for Taiwan files and validates header fields', () => {
    const data = ok({
      symbol: '6488.TWO',
      fyEndMonth: 13,
      checkedAt: 'yesterday',
      quality: { level: 'excellent', issues: ['short-history', 'short-history', 'Bad Code!', 7] },
      split: { adjusted: 'yes', events: [['2024-06-10', 10], ['bad', 2], ['2024-01-01', -1]] },
      annual: { end: ['2024-12-31'], values: {} },
    });
    expect(data.market).toBe('TW');
    expect(data.currency).toBe('TWD');
    expect(data.fyEndMonth).toBeNull();
    expect(data.checkedAt).toBeNull();
    expect(data.quality).toEqual({ level: null, annualYears: null, quarters: null, issues: ['short-history'] });
    expect(data.split).toEqual({ adjusted: false, events: [{ date: '2024-06-10', ratio: 10 }] });
  });

  it('reports garbled and empty files', () => {
    expect(parseFinancials(null).status).toBe('invalid');
    expect(parseFinancials([1, 2]).status).toBe('invalid');
    expect(parseFinancials('text').status).toBe('invalid');
    expect(parseFinancials({ version: 2, annual: { end: ['2024-12-31'] } }).status).toBe('invalid');
    expect(parseFinancials({}).status).toBe('missing');
    expect(parseFinancials({ version: 1, annual: { end: ['nope'] }, quarterly: 'x' }).status).toBe('missing');
  });
});

describe('fetchFinancials', () => {
  it('maps HTTP outcomes to states', async () => {
    const body = JSON.stringify(fixture('tsmc-twd.json'));
    expect((await fetchFinancials('u', async () => response(200, body))).status).toBe('ok');
    expect((await fetchFinancials('u', async () => response(404, 'not found'))).status).toBe('missing');
    expect((await fetchFinancials('u', async () => response(500, 'boom'))).status).toBe('error');
    expect((await fetchFinancials('u', async () => response(200, '<!doctype html>'))).status).toBe('invalid');
    expect(
      (
        await fetchFinancials('u', async () => {
          throw new TypeError('offline');
        })
      ).status,
    ).toBe('error');
  });
});

describe('FinancialsProvider', () => {
  const body = JSON.stringify(fixture('tsmc-twd.json'));

  it('caches each symbol for 30 minutes', async () => {
    let now = 0;
    const fetcher = vi.fn(async (url: string | URL | Request) => response(200, String(url).includes('2330') ? body : '{}'));
    const provider = new FinancialsProvider({ fetcher: fetcher as typeof fetch, now: () => now, urlOf: (key) => `/f/${key}.json` });
    expect((await provider.load('2330.TW')).status).toBe('ok');
    await provider.load('2330.tw');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect((await provider.load('AAPL')).status).toBe('missing');
    expect(fetcher).toHaveBeenCalledTimes(2);
    now = 29 * 60_000;
    await provider.load('2330.TW');
    expect(fetcher).toHaveBeenCalledTimes(2);
    now = 31 * 60_000;
    await provider.load('2330.TW');
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls[0]![0]).toBe('/f/2330.TW.json');
  });

  it('does not pin errors and falls back to the last good copy', async () => {
    let fail = true;
    const fetcher = vi.fn(async () => (fail ? response(503, 'busy') : response(200, body)));
    const provider = new FinancialsProvider({ fetcher: fetcher as typeof fetch, now: () => 0, urlOf: (key) => key });
    expect((await provider.load('2330.TW')).status).toBe('error');
    fail = false;
    expect((await provider.load('2330.TW')).status).toBe('ok');
    fail = true;
    expect((await provider.load('2330.TW', true)).status).toBe('ok');
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('answers indices without a request', async () => {
    const fetcher = vi.fn();
    const provider = new FinancialsProvider({ fetcher: fetcher as unknown as typeof fetch });
    expect((await provider.load('^GSPC')).status).toBe('missing');
    expect(fetcher).not.toHaveBeenCalled();
  });
});
