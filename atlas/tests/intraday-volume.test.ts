import { describe, expect, it } from 'vitest';
import { INTRADAY_VOLUME_NOTE, intradayIndexVolume, todayVolumeMissing, withIntradayIndexVolume } from '../src/market-data/IntradayVolume';
import type { Bar, BarResult } from '../src/market-data/MarketDataProvider';

const t = (s: string) => Date.parse(s) / 1000;
const bar = (time: number, volume: number): Bar => ({ time, open: 100, high: 101, low: 99, close: 100, volume });
/** Taiwan regular session 09:00–13:30 = 54 five-minute buckets. */
const session = (date: string, count: number, volume: number): Bar[] =>
  Array.from({ length: count }, (_, i) => bar(t(`${date}T01:00:00Z`) + i * 300, volume));
const result = (bars: Bar[], extra: Partial<BarResult> = {}): BarResult => ({
  bars, source: 'fixture', session: 'regular', delayed: true, adjusted: false, dataState: 'delayed', ...extra,
});
const NOW = t('2026-11-19T02:32:00Z'); // 10:32 Taipei
const daily = (todayVolume: number, history = 54_000): Bar[] => [
  bar(t('2026-11-17T01:00:00Z'), history),
  bar(t('2026-11-18T01:00:00Z'), history),
  bar(t('2026-11-19T01:00:00Z'), todayVolume),
];
const intraday = (perBucket: number, today = perBucket) => result([
  ...session('2026-11-17', 54, perBucket),
  ...session('2026-11-18', 54, perBucket),
  ...session('2026-11-19', 18, today),
]);

describe('intraday index volume for the live daily bar', () => {
  it('sums today’s 5m volume when earlier complete sessions match the daily history unit', () => {
    expect(intradayIndexVolume(daily(0), intraday(1000, 2000), '^TWII', NOW)).toEqual({
      status: 'ok', date: '2026-11-19', volume: 36_000, ratio: 1, checkedSessions: 2,
    });
  });

  it('never mixes units: lots, trade value or zero intraday volume give no estimate', () => {
    // 5m bars in a unit 1000x away from the daily history (e.g. lots vs shares).
    expect(intradayIndexVolume(daily(0), intraday(1), '^TWII', NOW))
      .toMatchObject({ status: 'unavailable', reason: expect.stringContaining('口徑不一致') });
    // Yahoo publishes no ^TWII intraday volume: the bars exist but carry zeros.
    expect(intradayIndexVolume(daily(0), intraday(0), '^TWII', NOW))
      .toMatchObject({ status: 'unavailable', reason: expect.stringContaining('未提供') });
    // History without volume (the daily rows themselves are zero) cannot vouch for the unit.
    expect(intradayIndexVolume(daily(0, 0), intraday(1000), '^TWII', NOW))
      .toMatchObject({ status: 'unavailable', reason: expect.stringContaining('無法驗證') });
  });

  it('needs today’s bars, complete earlier sessions and known volume', () => {
    const before = result([...session('2026-11-17', 54, 1000), ...session('2026-11-18', 54, 1000)]);
    expect(intradayIndexVolume(daily(0), before, '^TWII', NOW)).toHaveProperty('status', 'unavailable');
    // An early-closed or partial earlier session is not a calibration observation.
    const partial = result([...session('2026-11-17', 30, 1800), ...session('2026-11-18', 54, 1000), ...session('2026-11-19', 18, 1000)]);
    expect(intradayIndexVolume(daily(0), partial, '^TWII', NOW)).toMatchObject({ reason: expect.stringContaining('無法驗證') });
    const unknown = intraday(1000);
    expect(intradayIndexVolume(daily(0), { ...unknown, missingVolumeAt: [unknown.bars.at(-1)!.time] }, '^TWII', NOW))
      .toHaveProperty('status', 'unavailable');
    expect(intradayIndexVolume(daily(0), null, '^TWII', NOW)).toHaveProperty('status', 'unavailable');
    expect(intradayIndexVolume(daily(0), intraday(1000), '2330.TW', NOW)).toHaveProperty('status', 'unavailable');
    expect(intradayIndexVolume(daily(0), { ...intraday(1000), dataState: 'simulated' }, '^TWII', NOW)).toHaveProperty('status', 'unavailable');
  });

  it('uses New York session buckets for US indices', () => {
    const us = (date: string, count: number, volume: number) =>
      Array.from({ length: count }, (_, i) => bar(t(`${date}T14:30:00Z`) + i * 300, volume));
    const history = [bar(t('2026-11-17T14:30:00Z'), 78_000), bar(t('2026-11-18T14:30:00Z'), 78_000), bar(t('2026-11-19T14:30:00Z'), 0)];
    const bars = result([...us('2026-11-17', 78, 1000), ...us('2026-11-18', 78, 1000), ...us('2026-11-19', 6, 500)]);
    expect(intradayIndexVolume(history, bars, '^GSPC', t('2026-11-19T15:01:00Z'))).toMatchObject({ status: 'ok', volume: 3000 });
  });

  it('patches only today’s zero or unknown volume, refreshes its own estimate and keeps source volume', () => {
    const base = result(daily(0), { missingVolumeAt: [t('2026-11-19T01:00:00Z')] });
    const estimate = intradayIndexVolume(daily(0), intraday(1000, 2000), '^TWII', NOW);
    const patched = withIntradayIndexVolume(base, estimate, '^TWII');
    expect(patched.bars.at(-1)!.volume).toBe(36_000);
    expect(patched.bars.slice(0, -1)).toEqual(base.bars.slice(0, -1));
    expect(patched.missingVolumeAt).toEqual([]);
    expect(patched.source).toContain(INTRADAY_VOLUME_NOTE);
    expect(withIntradayIndexVolume(patched, estimate, '^TWII')).toBe(patched);
    const later = withIntradayIndexVolume(patched, { ...estimate, volume: 40_000 } as typeof estimate, '^TWII');
    expect(later.bars.at(-1)!.volume).toBe(40_000);
    expect(later.source.split(INTRADAY_VOLUME_NOTE)).toHaveLength(2);
    // A positive source volume (e.g. after the close) is never replaced, and drops the estimate note.
    const sourced = { ...later, bars: [...later.bars.slice(0, -1), { ...later.bars.at(-1)!, volume: 55_000 }] };
    const kept = withIntradayIndexVolume(sourced, estimate, '^TWII');
    expect(kept.bars.at(-1)!.volume).toBe(55_000);
    expect(kept.source).not.toContain(INTRADAY_VOLUME_NOTE);
    expect(withIntradayIndexVolume(base, { status: 'unavailable', reason: 'x' }, '^TWII')).toBe(base);
    // An estimate for another date does not touch the newest bar.
    expect(withIntradayIndexVolume(result(daily(0).slice(0, 2)), estimate, '^TWII').bars[1]!.volume).toBe(54_000);
  });

  it('reports a missing live volume only for today’s bar', () => {
    expect(todayVolumeMissing(result(daily(0)), '^TWII', NOW)).toBe(true);
    expect(todayVolumeMissing(result(daily(5)), '^TWII', NOW)).toBe(false);
    expect(todayVolumeMissing(result(daily(0).slice(0, 2)), '^TWII', NOW)).toBe(false);
    expect(todayVolumeMissing(null, '^TWII', NOW)).toBe(false);
  });
});
