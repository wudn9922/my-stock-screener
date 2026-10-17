import { describe, expect, it } from 'vitest';
import { parseSeries } from '../src/fundamentals/FinancialHistory';

describe('epsQuality parsing', () => {
  it('keeps each period grade and filing date aligned after the loader sorts the periods', () => {
    const raw = {
      end: ['2024-12-31', '2023-12-31'],
      fy: [2024, 2023],
      fq: [4, 4],
      src: ['sec', 'sec'],
      values: { epsDiluted: [0.25, 0.1] },
      epsQuality: [{ epsDiluted: 'A', epsDilutedFiled: '2025-02-05' }, { epsDiluted: 'C' }],
    };
    const series = parseSeries(raw, 'quarterly');
    expect(series.end).toEqual(['2023-12-31', '2024-12-31']);
    expect(series.epsQuality?.[0]?.epsDiluted).toBe('C');
    expect(series.epsQuality?.[1]?.epsDiluted).toBe('A');
    expect(series.epsQuality?.[1]?.epsDilutedFiled).toBe('2025-02-05');
  });

  it('drops grades it does not know', () => {
    const series = parseSeries({ end: ['2024-12-31'], fy: [2024], fq: [4], src: ['sec'], values: {}, epsQuality: [{ epsDiluted: 'Z' }] }, 'quarterly');
    expect(series.epsQuality?.[0]).toBeNull();
  });
});
