import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { industryPeers, peerFigures } from '../src/fundamentals/peerComparison';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { parseThemes } from '../src/report/themes';
import { relativeReturn, themeRotation } from '../src/pages/themeRotation';
import { healthAge } from '../src/report/dataHealth';
import type { GroupItem } from '../src/report/schema';
import { previousSourceSession } from '../scripts/refresh-watchlists.ts';

describe('comparison and rotation evidence', () => {
  it('uses a benchmark calendar only when it includes the current data day', () => {
    const csv = 'Date,Close\n2026-10-09,100\n2026-10-13,101\n';
    expect(previousSourceSession(csv, '2026-10-13')).toBe('2026-10-09');
    expect(previousSourceSession(csv, '2026-10-14')).toBeNull();
  });
  it('uses actual industry matches, never a broad-sector fallback', () => {
    const rows = [
      { symbol: 'AAPL', profile: { industry: 'Hardware', sector: 'Technology', marketCap: 100 } },
      { symbol: 'MSFT', profile: { industry: 'Software', sector: 'Technology', marketCap: 90 } },
      { symbol: 'DELL', profile: { industry: 'Hardware', sector: 'Technology', marketCap: 80 } },
    ] as GroupItem[];
    expect(industryPeers('AAPL', rows).map((row) => row.symbol)).toEqual(['DELL']);
    expect(industryPeers('UNKNOWN', rows)).toEqual([]);
  });
  it('retains quarter ends and leaves bank-inapplicable metrics blank', () => {
    const result = parseFinancials(JSON.parse(readFileSync(new URL('./fixtures/financials/bank.json', import.meta.url), 'utf8')));
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const figures = peerFigures(result.data, 'quarterly');
    expect(figures.date).toBe(result.data.quarterly.end.at(-1));
    expect(figures.figures[2].value).toBeNull();
  });
  it('computes relative compounded returns and declines to infer rotation from misaligned/short series', () => {
    const series = Array.from({ length: 64 }, (_, i) => 100 * 1.01 ** i);
    const benchmark = Array.from({ length: 64 }, (_, i) => 100 * 1.005 ** i);
    expect(relativeReturn(series, benchmark, 63)).toBeCloseTo(((1.01 / 1.005) ** 21 - 1) * 100);
    expect(relativeReturn([0, 0], [100, 100], 1, 1)).toBeNull();
    const parsed = parseThemes({
      dates: series.map((_, i) => `2026-${i}`),
      benchmark: { symbol: 'SPY', series: benchmark },
      themes: [{ key: 'hardware', name: '硬體', series }],
    });
    expect(parsed.status).toBe('ok');
    if (parsed.status !== 'ok') return;
    expect(themeRotation(parsed.data)).toHaveLength(1);
    expect(themeRotation({ ...parsed.data, dates: ['2026-01-01'] })).toEqual([]);
  });
  it('distinguishes an unknown timestamp from a recent or old observation', () => {
    expect(healthAge(null)).toBeNull();
    expect(healthAge('invalid')).toBeNull();
    expect(healthAge('2026-10-01', Date.parse('2026-10-10'))).toBe(9);
  });
});
