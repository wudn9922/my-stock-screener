import { describe, expect, it, vi } from 'vitest';
import {
  addFrame,
  annualYearsToFetch,
  buildTaiwanRecords,
  computeTtm,
  computeUsValuation,
  deriveMissingQuarters,
  emptyFacts,
  fiscalYearOf,
  frameName,
  parseCompanyTickers,
  parseFramePoints,
  parseTaiwanAnnualEps,
  parseTaiwanClose,
  parseTaiwanPe,
  parseTaiwanSupplementalAnnual,
  quarterSlotsToFetch,
  slotFromDate,
  slotFromFrameName,
  slotOf,
  taiwanDateToIso,
  type CompanyEpsFacts,
} from '../scripts/valuation.ts';
import { ValuationProvider, computePe, describePe, summarizeValuation } from '../src/fundamentals/ValuationProvider';

const today = Date.parse('2026-10-08T12:00:00Z');

function facts(quarters: [string, string, string, number][], annuals: [string, string, number][] = []): CompanyEpsFacts {
  const result = emptyFacts();
  for (const [frame, start, end, val] of quarters) result.quarters.set(slotFromFrameName(frame)!, { start, end, val });
  for (const [start, end, val] of annuals) result.annuals.push({ start, end, val });
  return result;
}

describe('SEC frames periods', () => {
  it('maps frames and dates to calendar quarter slots', () => {
    expect(slotFromFrameName('CY2025Q3')).toBe(slotOf(2025, 3));
    expect(frameName(slotOf(2026, 1))).toBe('CY2026Q1');
    expect(slotFromFrameName('CY2025')).toBeUndefined();
    expect(slotFromDate(Date.parse('2026-10-08'))).toBe(slotOf(2026, 4));
    expect(quarterSlotsToFetch(today).map(frameName)).toEqual([
      'CY2024Q2', 'CY2024Q3', 'CY2024Q4', 'CY2025Q1', 'CY2025Q2', 'CY2025Q3',
      'CY2025Q4', 'CY2026Q1', 'CY2026Q2', 'CY2026Q3', 'CY2026Q4',
    ]);
    expect(annualYearsToFetch(today)).toEqual([2023, 2024, 2025, 2026]);
  });

  it('parses frame points and drops malformed ones', () => {
    const points = parseFramePoints({
      data: [
        { cik: 320193, start: '2025-03-30', end: '2025-06-28', val: 1.57 },
        { cik: '320193', end: '2025-06-28', val: 1 },
        { cik: 1, end: 'June', val: 1 },
        { cik: 2, end: '2025-06-30', val: Number.NaN },
        { cik: 3, end: '2025-06-30', val: 0.5 },
      ],
    });
    expect(points).toEqual([
      { cik: 320193, start: '2025-03-30', end: '2025-06-28', val: 1.57 },
      { cik: 3, end: '2025-06-30', val: 0.5 },
    ]);
    const target = new Map<number, CompanyEpsFacts>();
    addFrame(target, points, slotOf(2025, 2));
    addFrame(target, [{ cik: 3, start: '2025-01-01', end: '2025-12-31', val: 2 }]);
    expect(target.get(320193)!.quarters.get(slotOf(2025, 2))!.val).toBe(1.57);
    expect(target.get(3)!.annuals).toHaveLength(1);
    expect(parseFramePoints(null)).toEqual([]);
  });
});

describe('EPS TTM from frames', () => {
  // Apple-like fiscal year ending late September: fiscal Q4 exists only in the 10-K.
  const apple = facts(
    [
      ['CY2024Q4', '2024-09-29', '2024-12-28', 2.4],
      ['CY2025Q1', '2024-12-29', '2025-03-29', 1.65],
      ['CY2025Q2', '2025-03-30', '2025-06-28', 1.57],
      ['CY2025Q4', '2025-09-28', '2025-12-27', 2.84],
      ['CY2026Q1', '2025-12-28', '2026-03-28', 1.65],
      ['CY2026Q2', '2026-03-29', '2026-06-27', 1.57],
    ],
    [['2024-09-29', '2025-09-27', 7.46]],
  );

  it('derives a missing fiscal Q4 as annual minus the other three quarters', () => {
    const derived = deriveMissingQuarters(apple);
    expect(derived.quarters.get(slotOf(2025, 3))).toEqual({ val: 1.84, end: '2025-09-27', derived: true });
    expect(apple.quarters.has(slotOf(2025, 3))).toBe(false);
    expect(computeTtm(derived, slotOf(2025, 4))).toEqual({ eps: 7.9, end: '2026-06-27', latestSlot: slotOf(2026, 2), derivedQuarters: 1 });
  });

  it('requires four contiguous quarters ending at the latest one', () => {
    const gap = facts([
      ['CY2025Q3', '2025-07-01', '2025-09-30', 1],
      ['CY2025Q4', '2025-10-01', '2025-12-31', 1],
      ['CY2026Q2', '2026-04-01', '2026-06-30', 1],
    ]);
    expect(computeTtm(deriveMissingQuarters(gap), slotOf(2025, 4))).toBeNull();
    // Two missing quarters in one fiscal year cannot be derived.
    const twoMissing = facts(
      [
        ['CY2025Q1', '2025-01-01', '2025-03-31', 1],
        ['CY2025Q2', '2025-04-01', '2025-06-30', 1],
      ],
      [['2025-01-01', '2025-12-31', 4]],
    );
    expect(deriveMissingQuarters(twoMissing).quarters.size).toBe(2);
    // A quarter outside the annual period blocks derivation.
    const outside = facts(
      [
        ['CY2025Q1', '2024-10-01', '2025-03-31', 1],
        ['CY2025Q2', '2025-04-01', '2025-06-30', 1],
        ['CY2025Q3', '2025-07-01', '2025-09-30', 1],
      ],
      [['2025-01-01', '2025-12-31', 4]],
    );
    expect(deriveMissingQuarters(outside).quarters.has(slotOf(2025, 4))).toBe(false);
  });

  it('rejects stale filers and keeps losses (negative TTM)', () => {
    const stale = facts([
      ['CY2024Q1', '2024-01-01', '2024-03-31', 1],
      ['CY2024Q2', '2024-04-01', '2024-06-30', 1],
      ['CY2024Q3', '2024-07-01', '2024-09-30', 1],
      ['CY2024Q4', '2024-10-01', '2024-12-31', 1],
    ]);
    expect(computeUsValuation(stale, undefined, today)).toBeNull();
    const loss = facts([
      ['CY2025Q3', '2025-07-01', '2025-09-30', -0.5],
      ['CY2025Q4', '2025-10-01', '2025-12-31', -0.5],
      ['CY2026Q1', '2026-01-01', '2026-03-31', -0.5],
      ['CY2026Q2', '2026-04-01', '2026-06-30', -0.25],
    ]);
    expect(computeUsValuation(loss, undefined, today)).toMatchObject({ epsTtm: -1.75, epsAnnual: null, asOf: '2026-06-30' });
  });

  it('prefers diluted EPS, falls back to basic, and labels fiscal years', () => {
    const basic = facts(
      [
        ['CY2025Q3', '2025-07-01', '2025-09-30', 1],
        ['CY2025Q4', '2025-10-01', '2025-12-31', 1],
        ['CY2026Q1', '2026-01-01', '2026-03-31', 1],
        ['CY2026Q2', '2026-04-01', '2026-06-30', 1.1],
      ],
      [['2025-01-01', '2025-12-31', 3.9]],
    );
    expect(computeUsValuation(undefined, basic, today)).toEqual({
      epsTtm: 4.1, epsAnnual: 3.9, fiscalYear: 2025, asOf: '2026-06-30', source: 'SEC frames', basis: 'basic',
    });
    const appleBasic = facts([...apple.quarters.entries()].map(([slot, q]) => [frameName(slot), q.start!, q.end, q.val + 0.01]), [['2024-09-29', '2025-09-27', 7.5]]);
    expect(computeUsValuation(apple, appleBasic, today)).toMatchObject({ epsTtm: 7.9, epsAnnual: 7.46, fiscalYear: 2025 });
    expect(computeUsValuation(apple, appleBasic, today)).not.toHaveProperty('basis');
    // Diluted TTM but a newer basic-only annual value: flagged as basic.
    expect(computeUsValuation(apple, basic, today)).toMatchObject({ epsTtm: 7.9, epsAnnual: 3.9, basis: 'basic' });
    expect(fiscalYearOf('2026-01-25')).toBe(2026);
    expect(fiscalYearOf('2026-01-03')).toBe(2025);
    expect(fiscalYearOf('2025-09-27')).toBe(2025);
  });

  it('maps CIKs to Yahoo tickers', () => {
    const map = parseCompanyTickers({
      0: { cik_str: 1067983, ticker: 'BRK.B', title: 'Berkshire' },
      1: { cik_str: 1067983, ticker: 'BRK-A', title: 'Berkshire' },
      2: { cik_str: 1652044, ticker: 'GOOGL', title: 'Alphabet' },
      3: { cik_str: 'x', ticker: 'BAD', title: '' },
      4: { cik_str: 5, ticker: 'bad^', title: '' },
    });
    expect(map.get(1067983)).toEqual(['BRK-B', 'BRK-A']);
    expect(map.get(1652044)).toEqual(['GOOGL']);
    expect(map.size).toBe(2);
  });
});

describe('Taiwan official P/E', () => {
  it('parses dates and numbers in TWSE/TPEx formats', () => {
    expect(taiwanDateToIso('1151007')).toBe('2026-10-07');
    expect(taiwanDateToIso('115/10/07')).toBe('2026-10-07');
    expect(taiwanDateToIso('115年10月7日')).toBe('2026-10-07');
    expect(taiwanDateToIso('20261007')).toBe('2026-10-07');
    expect(taiwanDateToIso('2026-10-07')).toBe('2026-10-07');
    expect(taiwanDateToIso('1151307')).toBeNull();
    expect(taiwanDateToIso('')).toBeNull();
  });

  it('builds EPS TTM = close / P/E, keeps annual EPS and skips empty rows', () => {
    const pe = parseTaiwanPe([
      { Date: '1151007', Code: '2330', Name: '台積電', PEratio: '25.10' },
      { Date: '1151007', Code: '2498', Name: '宏達電', PEratio: '' },
      { Date: '1151007', Code: '2881', PEratio: '12.50' },
      { Code: '00878', PEratio: '10' },
      { 證券代號: '1101', 本益比: '-' },
    ]);
    expect(pe.map((row) => row.code)).toEqual(['2330', '2498', '2881', '1101']);
    const tpexPe = parseTaiwanPe([{ Date: '1151007', SecuritiesCompanyCode: '6488', PriceEarningRatio: '18.20' }]);
    expect(tpexPe[0]).toEqual({ code: '6488', pe: 18.2, date: '2026-10-07' });
    const close = parseTaiwanClose([
      { Date: '1151007', Code: '2330', ClosingPrice: '1,255.00' },
      { Date: '1151006', Code: '2881', ClosingPrice: '95.50' },
      { Code: '2498', ClosingPrice: '--' },
    ]);
    expect(close.get('2330')).toEqual({ close: 1255, date: '2026-10-07' });
    const annual = parseTaiwanAnnualEps([
      { 年度: '114', 季別: '4', 公司代號: '2330', '基本每股盈餘(元)': '45.25' },
      { 年度: '115', 季別: '2', 公司代號: '2881', '基本每股盈餘(元)': '5.10' },
      { 年度: '114', 季別: '4', 公司代號: '2498', '基本每股盈餘(元)': '(1.25)' },
    ]);
    expect([...annual.entries()]).toEqual([
      ['2330', { eps: 45.25, fiscalYear: 2025 }],
      ['2498', { eps: -1.25, fiscalYear: 2025 }],
    ]);
    const items = buildTaiwanRecords({
      exchange: 'TWSE',
      pe,
      close,
      annual: new Map([['2498', { eps: -1.25, fiscalYear: 2025 }]]),
      previous: { '2330.TW': { epsTtm: 40, epsAnnual: 45.25, fiscalYear: 2025, exchangePeTtm: 22, asOf: '2026-03-31', source: 'TWSE' } },
      fallbackDate: '2026-10-08',
    });
    expect(items['2330.TW']).toEqual({ epsTtm: 50, epsAnnual: 45.25, fiscalYear: 2025, exchangePeTtm: 25.1, asOf: '2026-10-07', source: 'TWSE' });
    // Close from a different day than the P/E → no derived EPS, exchange P/E kept.
    expect(items['2881.TW']).toMatchObject({ epsTtm: null, exchangePeTtm: 12.5 });
    expect(items['2498.TW']).toMatchObject({ epsTtm: null, epsAnnual: -1.25, exchangePeTtm: null });
    expect(items['1101.TW']).toBeUndefined();
  });

  // Regression: between May and the next March the exchanges' latest statements are Q1–Q3 (year-to-date),
  // so a build without a carried-over Q4 value had epsAnnual = null for every Taiwan stock and the
  // 「本益比」 (annual EPS) field was always empty. Annual EPS from tw-annual.json fills that gap.
  it('fills annual EPS from tw-annual.json when the exchanges only publish a year-to-date quarter', () => {
    const pe = parseTaiwanPe([
      { Date: '1151008', Code: '2330', PEratio: '29.56' },
      { Date: '1151008', Code: '2317', PEratio: '16.41' },
      { Date: '1151008', Code: '2881', PEratio: '12.50' },
    ]);
    const close = parseTaiwanClose([
      { Date: '1151008', Code: '2330', ClosingPrice: '2,550.00' },
      { Date: '1151008', Code: '2317', ClosingPrice: '249.00' },
    ]);
    // October: t187ap14_L holds 2026 Q2 (year-to-date), which is not a full year.
    const annual = parseTaiwanAnnualEps([{ 年度: '115', 季別: '2', 公司代號: '2330', '基本每股盈餘(元)': '41.20' }]);
    expect(annual.size).toBe(0);
    const base = { exchange: 'TWSE' as const, pe, close, annual, fallbackDate: '2026-10-08' };
    const withoutSupplement = buildTaiwanRecords({ ...base, previous: {} });
    expect(withoutSupplement['2330.TW']).toMatchObject({ epsAnnual: null, fiscalYear: null });

    const items = buildTaiwanRecords({
      ...base,
      // An official Q4 value already stored for 2881 wins over the supplement for the same year.
      previous: { '2881.TW': { epsTtm: 9, epsAnnual: 8.88, fiscalYear: 2025, exchangePeTtm: 12, asOf: '2026-04-01', source: 'TWSE' } },
      supplementalAnnual: new Map([
        ['2330.TW', { eps: 64.11, fiscalYear: 2025 }],
        ['2881.TW', { eps: 8.9, fiscalYear: 2025 }],
        ['9999.TW', { eps: 1, fiscalYear: 2025 }],
      ]),
    });
    expect(items['2330.TW']).toEqual({
      epsTtm: 86.27, epsAnnual: 64.11, fiscalYear: 2025, annualSource: 'Yahoo Finance',
      exchangePeTtm: 29.56, asOf: '2026-10-08', source: 'TWSE',
    });
    expect(items['2317.TW']).toMatchObject({ epsAnnual: null, fiscalYear: null });
    expect(items['2881.TW']).toMatchObject({ epsAnnual: 8.88, fiscalYear: 2025 });
    expect(items['2881.TW']).not.toHaveProperty('annualSource');
    expect(items['9999.TW']).toBeUndefined();

    // A newer official full year replaces the supplement; an older one does not.
    const spring = buildTaiwanRecords({
      ...base,
      annual: new Map([['2330', { eps: 70.5, fiscalYear: 2026 }], ['2317', { eps: 10, fiscalYear: 2024 }]]),
      previous: {},
      supplementalAnnual: new Map([['2330.TW', { eps: 64.11, fiscalYear: 2025 }], ['2317.TW', { eps: 12.5, fiscalYear: 2025 }]]),
    });
    expect(spring['2330.TW']).toMatchObject({ epsAnnual: 70.5, fiscalYear: 2026 });
    expect(spring['2330.TW']).not.toHaveProperty('annualSource');
    expect(spring['2317.TW']).toMatchObject({ epsAnnual: 12.5, fiscalYear: 2025, annualSource: 'Yahoo Finance' });
  });

  it('parses tw-annual.json into a symbol map and ignores malformed entries', () => {
    expect([
      ...parseTaiwanSupplementalAnnual({
        version: 1,
        items: {
          '2330.TW': { epsAnnual: 64.11, fiscalYear: 2025, annualPeriodEnd: '2025-12-31', annualCheckedAt: '2026-10-10' },
          '6488.TWO': { epsAnnual: -1.5, fiscalYear: 2025 },
          '2317.TW': { epsAnnual: null, fiscalYear: null, annualCheckedAt: '2026-10-10' },
          AAPL: { epsAnnual: 7, fiscalYear: 2025 },
          '1101.TW': { epsAnnual: 'x', fiscalYear: 2025 },
        },
      }).entries(),
    ]).toEqual([
      ['2330.TW', { eps: 64.11, fiscalYear: 2025 }],
      ['6488.TWO', { eps: -1.5, fiscalYear: 2025 }],
    ]);
    expect(parseTaiwanSupplementalAnnual(undefined).size).toBe(0);
  });
});

describe('computePe', () => {
  it('returns price / EPS, and null for missing or non-positive earnings', () => {
    expect(computePe(100, 4)).toBe(25);
    expect(computePe(1255, 52.3)).toBe(24);
    expect(computePe(100, 0)).toBeNull();
    expect(computePe(100, -2)).toBeNull();
    expect(computePe(100, null)).toBeNull();
    expect(computePe(undefined, 4)).toBeNull();
    expect(computePe(0, 4)).toBeNull();
    expect(describePe(100, -2)).toEqual({ pe: null, negativeEarnings: true, reason: 'negative-earnings' });
    expect(describePe(100, null)).toEqual({ pe: null, negativeEarnings: false, reason: 'missing-eps' });
    expect(describePe(Number.NaN, 2)).toEqual({ pe: null, negativeEarnings: false, reason: 'missing-price' });
    expect(describePe(30, 4)).toEqual({ pe: 7.5, negativeEarnings: false });
  });
});

describe('ValuationProvider', () => {
  const us = {
    version: 1,
    market: 'US',
    generatedAt: '2026-10-08T00:00:00Z',
    items: {
      AAPL: { epsTtm: 7.9, epsAnnual: 7.46, fiscalYear: 2025, asOf: '2026-06-27', source: 'SEC frames' },
      LOSS: { epsTtm: -2, epsAnnual: -1.8, fiscalYear: 2025, asOf: '2026-06-30', source: 'SEC frames' },
      BAD: { epsTtm: 'x', source: 'SEC frames' },
      MSFT: {
        epsTtm: 17.95, epsAnnual: 17.95, fiscalYear: 2026, asOf: '2026-10-09', source: 'Yahoo Finance',
        annualPeriodEnd: '2026-06-30', annualCheckedAt: '2026-10-09',
      },
    },
  };
  const tw = {
    version: 1,
    market: 'TW',
    items: { '2330.TW': { epsTtm: 50, epsAnnual: 45.25, fiscalYear: 2025, exchangePeTtm: 25.1, asOf: '2026-10-07', source: 'TWSE' } },
  };

  function provider(files: Record<string, unknown>, now = () => 0) {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      return Object.hasOwn(files, path) ? new Response(JSON.stringify(files[path])) : new Response('', { status: 404 });
    });
    return { provider: new ValuationProvider('/base/valuation/', { fetcher: fetcher as unknown as typeof fetch, now }), fetcher };
  }

  it('loads each market file once and returns typed records', async () => {
    const { provider: p, fetcher } = provider({ '/base/valuation/us.json': us, '/base/valuation/tw.json': tw });
    const [aapl, loss] = await Promise.all([p.getValuation('aapl'), p.getValuation('LOSS')]);
    expect(aapl).toEqual({
      symbol: 'AAPL', market: 'US', epsTtm: 7.9, epsAnnual: 7.46, fiscalYear: 2025, exchangePeTtm: null,
      asOf: '2026-06-27', source: 'SEC frames', basis: 'diluted',
    });
    expect(summarizeValuation(loss!, 50).peTtm).toEqual({ pe: null, negativeEarnings: true, reason: 'negative-earnings' });
    expect(summarizeValuation(aapl!, 237).peTtm.pe).toBe(30);
    expect(await p.getValuation('2330.TW')).toMatchObject({ market: 'TW', exchangePeTtm: 25.1, epsTtm: 50 });
    expect(await p.getValuation('MSFT')).toEqual({
      symbol: 'MSFT', market: 'US', epsTtm: 17.95, epsAnnual: 17.95, fiscalYear: 2026, exchangePeTtm: null,
      asOf: '2026-10-09', source: 'Yahoo Finance', basis: 'diluted',
    });
    expect(await p.getValuation('BAD')).toBeNull();
    expect(await p.getValuation('ZZZZ')).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('returns null for indices, invalid input and missing files, retrying a failed file after a minute', async () => {
    let now = 0;
    const { provider: p, fetcher } = provider({}, () => now);
    expect(await p.getValuation('^TWII')).toBeNull();
    expect(await p.getValuation('not a symbol!')).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
    expect(await p.getValuation('NVDA')).toBeNull();
    expect(await p.getValuation('NVDA')).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    now = 61_000;
    expect(await p.getValuation('NVDA')).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('accepts the deployed record shapes (ADR method, Taiwan without annual EPS)', async () => {
    // Verbatim from docs/atlas/valuation/{us,tw}.json (2026-10-09).
    const { provider: p } = provider({
      '/base/valuation/us.json': {
        version: 1,
        market: 'US',
        items: {
          TSM: {
            epsTtm: 13.76, epsAnnual: 10.2465, fiscalYear: 2025, asOf: '2026-10-09', source: 'Yahoo Finance',
            method: 'net income / ADR count', annualPeriodEnd: '2025-12-31', annualCheckedAt: '2026-10-09',
          },
        },
      },
      '/base/valuation/tw.json': {
        version: 1,
        market: 'TW',
        items: { '2330.TW': { epsTtm: 86.27, epsAnnual: null, fiscalYear: null, exchangePeTtm: 29.56, asOf: '2026-10-08', source: 'TWSE' } },
      },
    });
    expect(await p.getValuation('TSM')).toMatchObject({ market: 'US', epsTtm: 13.76, epsAnnual: 10.2465 });
    expect(await p.getValuation('2330.TW')).toMatchObject({ market: 'TW', epsAnnual: null, exchangePeTtm: 29.56 });
  });

  it('reloads a loaded file after its lifetime and keeps the last good copy when the reload fails', async () => {
    let now = 0;
    const files: Record<string, unknown> = { '/base/valuation/us.json': { version: 1, market: 'US', items: {} } };
    const { provider: p, fetcher } = provider(files, () => now);
    // A tab opened before TSM was added to us.json.
    expect(await p.getValuation('TSM')).toBeNull();
    files['/base/valuation/us.json'] = {
      version: 1,
      market: 'US',
      items: { TSM: { epsTtm: 13.76, epsAnnual: 10.2465, fiscalYear: 2025, asOf: '2026-10-09', source: 'Yahoo Finance' } },
    };
    now = 10 * 60_000;
    expect(await p.getValuation('TSM')).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    now = 31 * 60_000;
    expect(await p.getValuation('TSM')).toMatchObject({ epsAnnual: 10.2465 });
    expect(fetcher).toHaveBeenCalledTimes(2);
    delete files['/base/valuation/us.json'];
    now = 62 * 60_000;
    expect(await p.getValuation('TSM')).toMatchObject({ epsAnnual: 10.2465 });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
