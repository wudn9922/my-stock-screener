import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { enrichTaiwanValuation, taipeiBuildDate } from '../scripts/taiwan-valuation-history';
import type { ValuationRecord } from '../scripts/valuation';
import { getPeFigures, screenerPeValue } from '../src/fundamentals/peFigures';
import { ValuationProvider } from '../src/fundamentals/ValuationProvider';

const record: ValuationRecord = { epsTtm: null, epsAnnual: 6.44, exchangePeTtm: null, fiscalYear: 2025, asOf: '2026-11-20', source: 'TPEx' };
const today = '2026-11-22';
const quarters = (symbol: string, eps: number[], end = ['2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30']) => ({
  version: 1, symbol, market: 'TW', stockDividends: { events: [] },
  quarterly: { end, values: { epsBasic: eps } },
});

describe('Taiwan valuation financial history', () => {
  it('uses Taipei calendar day at UTC midnight boundary', () => {
    expect(taipeiBuildDate(Date.parse('2026-11-21T16:00:00Z'))).toBe('2026-11-22');
    expect(taipeiBuildDate(Date.parse('2026-11-21T15:59:59Z'))).toBe('2026-11-21');
  });

  it('isolates missing and corrupt files; calculates loss, missing quarters, and the 2755 restatement', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'atlas-pe-history-'));
    try {
      await writeFile(join(directory, '2755.TWO.json'), await readFile(new URL('./fixtures/financials/tw-2755-stock-dividends.json', import.meta.url)));
      await writeFile(join(directory, '1001.TWO.json'), '{invalid');
      await writeFile(join(directory, '1002.TWO.json'), JSON.stringify(quarters('1002.TWO', [-1, -2, 1, 0])));
      await writeFile(join(directory, '1003.TWO.json'), JSON.stringify(quarters('1003.TWO', [1, 1, 1], ['2026-03-31','2026-06-30','2026-09-30'])));
      await writeFile(join(directory, '1004.TWO.json'), JSON.stringify(quarters('1004.TWO', [1, 1, 1, 1, 99], ['2025-12-31','2026-03-31','2026-06-30','2026-09-30','2026-12-31'])));
      const items = await enrichTaiwanValuation({
        '1000.TWO': record, '1001.TWO': record, '1002.TWO': record, '1003.TWO': record, '1004.TWO': record,
        '2755.TWO': { ...record, epsTtm: 6.42, exchangePeTtm: 12.3 },
      }, directory, today);
      expect(items['1000.TWO']).toEqual(record);
      expect(items['1001.TWO']).toEqual(record);
      expect(items['1002.TWO']).toMatchObject({ epsTtmCalculated: -2, epsNote: '近四季 EPS 由財報計算' });
      expect(items['1003.TWO']).not.toHaveProperty('epsTtmCalculated');
      expect(items['1004.TWO']).toMatchObject({ epsTtmCalculated: 4 });
      expect(items['2755.TWO']).toMatchObject({ epsTtmCalculated: 5.7437, epsAnnualRestated: 5.1666 });
      const provider = new ValuationProvider('/valuation/', { fetcher: (async () => new Response(JSON.stringify({ version: 1, market: 'TW', items }))) as typeof fetch });
      const source = provider.getValuation.bind(provider);
      const missing = await getPeFigures('1003.TWO', 100, source);
      expect(missing).toMatchObject({ peTtmLoss: false, peTtm: null, reason: '近四季 EPS 資料不足' });
      expect(screenerPeValue(missing)).toBeNull();
      expect(await getPeFigures('1002.TWO', 100, source)).toMatchObject({ peTtmLoss: true });
      const adjusted = await getPeFigures('2755.TWO', 100, source);
      expect(adjusted.peTtm).toBe(17.41);
      expect(screenerPeValue(adjusted)).toBe(17.41);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
