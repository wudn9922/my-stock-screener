import { describe, expect, it } from 'vitest';
import { TimeMapper } from '../src/chart/TimeMapper';

const stamp = (day: string) => Date.parse(`${day}T12:00:00Z`) / 1000;
const bar = (day: string) => ({ time: stamp(day), open: 100, high: 110, low: 90, close: 105, volume: 1.25 });
describe('BTC research future dates use its continuous UTC calendar', () => {
  it('advances Friday through Saturday and Sunday without creating OHLC bars', () => {
    const bars = [bar('2026-10-23')];
    const continuous = new TimeMapper(bars, '1D', 3, undefined, 'continuous-utc');
    expect(continuous.futureWhitespace().map(row => row.time)).toEqual(['2026-10-24', '2026-10-25', '2026-10-26'].map(stamp));
    expect(continuous.bars).toHaveLength(1);
    expect(continuous.toTime(1)).toBe(stamp('2026-10-24'));
    expect(continuous.toLogical(stamp('2026-10-25'))).toBe(2);
    expect(new TimeMapper(bars, '1D', 1).futureWhitespace()[0]?.time).toBe(stamp('2026-10-26'));
  });
  it('keeps UTC Monday weekly buckets seven days apart', () => {
    const mapper = new TimeMapper([bar('2026-10-19')], '1W', 2, undefined, 'continuous-utc');
    expect(mapper.futureWhitespace().map(row => row.time)).toEqual(['2026-10-26', '2026-11-02'].map(stamp));
  });
});
