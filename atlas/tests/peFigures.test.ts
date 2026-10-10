import { afterEach, describe, expect, it, vi } from 'vitest';
import { getPeFigures } from '../src/app/dataSources';

// Record shapes as deployed in docs/atlas/valuation/{us,tw}.json (2026-10-09), plus 2317.TW after the
// annual EPS supplement (annualSource) has been merged.
const files: Record<string, unknown> = {
  '/valuation/us.json': {
    version: 1,
    market: 'US',
    items: {
      AAPL: { epsTtm: 8.72, epsAnnual: 7.46, fiscalYear: 2025, asOf: '2026-10-09', source: 'Yahoo Finance', annualPeriodEnd: '2025-09-30' },
      TSM: {
        epsTtm: 13.76, epsAnnual: 10.2465, fiscalYear: 2025, asOf: '2026-10-09', source: 'Yahoo Finance',
        method: 'net income / ADR count', annualPeriodEnd: '2025-12-31', annualCheckedAt: '2026-10-09',
      },
    },
  },
  '/valuation/tw.json': {
    version: 1,
    market: 'TW',
    items: {
      '2330.TW': { epsTtm: 86.27, epsAnnual: null, fiscalYear: null, exchangePeTtm: 29.56, asOf: '2026-10-08', source: 'TWSE' },
      '2317.TW': {
        epsTtm: 15.17, epsAnnual: 12.5, fiscalYear: 2025, annualSource: 'Yahoo Finance', exchangePeTtm: 16.41,
        asOf: '2026-10-08', source: 'TWSE',
      },
    },
  },
};

describe('getPeFigures (chart header / screener P/E)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows P/E for US stocks, ADRs and Taiwan stocks from the deployed record shapes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        return Object.hasOwn(files, path) ? new Response(JSON.stringify(files[path])) : new Response('', { status: 404 });
      }),
    );
    expect(await getPeFigures('AAPL', 223.8)).toMatchObject({ pe: 30, peTtm: 25.67, reason: null });
    expect(await getPeFigures('TSM', 301.1)).toMatchObject({ pe: 29.39, peTtm: 21.88, reason: null, fiscalYear: 2025 });
    // Exchange P/E (TTM) needs no price; annual P/E needs annual EPS.
    expect(await getPeFigures('2330.TW', 2550)).toMatchObject({ pe: null, peTtm: 29.56, reason: '暫無 EPS 資料' });
    expect(await getPeFigures('2317.TW', 249)).toMatchObject({ pe: 19.92, peTtm: 16.41, reason: null, fiscalYear: 2025 });
    expect(await getPeFigures('^TWII', 20000)).toMatchObject({ pe: null, peTtm: null, reason: '指數不適用本益比' });
  });
});
