import type { ThemesData } from '../report/themes';

export type RotationState = 'leading' | 'weakening' | 'lagging' | 'improving';
export const ROTATION_LABELS: Record<RotationState, string> = { leading: '領先', weakening: '轉弱', lagging: '落後', improving: '轉強' };
/** Relative compounded 21-session return, aligned to published dates. */
export function relativeReturn(series: readonly number[], benchmark: readonly number[], end: number, window = 21): number | null {
  const start = end - window;
  if (start < 0 || ![series[start], series[end], benchmark[start], benchmark[end]].every((value) => Number.isFinite(value) && value > 0))
    return null;
  const value = (series[end] / series[start] / (benchmark[end] / benchmark[start]) - 1) * 100;
  return Number.isFinite(value) ? value : null;
}
export function themeRotation(data: ThemesData) {
  const end = data.dates.length - 1;
  if (data.benchmark.series.length !== data.dates.length) return [];
  const eligible = data.themes
    .filter((theme) => theme.series.length === data.dates.length)
    .flatMap((theme) => {
      const strength = relativeReturn(theme.series, data.benchmark.series, end);
      const previous = relativeReturn(theme.series, data.benchmark.series, end - 21);
      if (strength === null || previous === null) return [];
      const change = strength - previous;
      const state: RotationState = strength >= 0 ? (change >= 0 ? 'leading' : 'weakening') : change >= 0 ? 'improving' : 'lagging';
      return [{ key: theme.key, name: theme.name, strength, previous, change, state }];
    });
  const oldOrder = [...eligible].sort((a, b) => b.previous - a.previous || a.key.localeCompare(b.key));
  return eligible
    .sort((a, b) => b.strength - a.strength || a.key.localeCompare(b.key))
    .map((entry, index) => ({
      ...entry,
      rank: index + 1,
      rankChange: oldOrder.findIndex((previous) => previous.key === entry.key) - index,
      asOf: data.dates[end],
      previousDate: data.dates[end - 21],
    }));
}
