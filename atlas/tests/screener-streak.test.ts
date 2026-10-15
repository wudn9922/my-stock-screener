import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  EMPTY_FILTERS,
  FILTER_PARAM_MAX,
  MAX_GATE_YEARS,
  activeChips,
  applyFilters,
  filterAvailability,
  gateCoverage,
  growthGatePasses,
  growthStreak,
  needsGrowth,
  parseFilters,
  removeFilter,
  serializeFilters,
  streakPasses,
} from '../src/pages/screenerFilters';
import { metricPill, rowMetricPills } from '../src/pages/screenerItems';
import { growthKey, parseReportGrowth, type GrowthFile } from '../src/fundamentals/GrowthProvider';
import { parseUniverse } from '../src/report/universe';

const universe = parseUniverse(JSON.parse(readFileSync('tests/fixtures/screener/universe.json', 'utf8')));
if (universe.status !== 'ok') throw new Error('universe fixture');
const US = universe.universe.markets.US!.items;
const state = (text: string) => parseFilters(text);

/** growth.json with ten annual slots for the first US rows of the fixture universe. */
function growthFile(rows: { eps: (number | null)[]; rev: (number | null)[]; tY?: number }[]): GrowthFile {
  return parseReportGrowth({
    version: 1,
    generatedAt: '2026-10-15T00:00:00Z',
    rows: rows.map((row, index) => [US[index]!.symbol, null, 'frames', '2026-06-30', '2025-12-31', [10], [10], row.eps, row.rev, 0, row.tY ?? 0, 'frames', 0]),
  })!;
}

describe('consecutive growth years', () => {
  it('counts from the latest year while YoY > 0 (EPS turnarounds count when allowed)', () => {
    expect(growthStreak([12, 5, -3, 8])).toBe(2);
    expect(growthStreak([12, 5, 0, 8])).toBe(2); // 0% is not growth
    expect(growthStreak([5, null, 10])).toBe(1); // a loss year / missing year stops the count
    expect(growthStreak([null, 5, 6], [true])).toBe(3); // loss → profit, then two growth years
    expect(growthStreak([null, 5, 6], [])).toBe(0);
    expect(growthStreak([])).toBe(0);
    expect(growthStreak(undefined)).toBe(0);
    expect(growthStreak(Array(10).fill(1))).toBe(10);
  });

  it('「≥ N 年」 follows the missing-data switch like the YoY gates', () => {
    expect(streakPasses([10, 5, 3], 3, false)).toBe(true);
    expect(streakPasses([10, 5], 3, false)).toBe(false); // only two years of history
    expect(streakPasses([10, 5], 3, true)).toBe(true);
    expect(streakPasses([10, -1, 5], 3, true)).toBe(false); // a known decline always fails
    expect(streakPasses([10, 0, 5], 2, true)).toBe(false);
    expect(streakPasses([null, 5], 2, false, [true])).toBe(true);
    expect(streakPasses(undefined, 2, false)).toBe(false);
    expect(streakPasses(undefined, 2, true)).toBe(true);
    // Same as 「近 N 年皆 > 0%」 except that 0% does not count as growth
    expect(growthGatePasses([10, 0], { years: 2, min: 0 }, false)).toBe(true);
    expect(streakPasses([10, 0], 2, false)).toBe(false);
  });
});

describe('f encoding: 10-year gates and streak tokens', () => {
  it('parses epss / revs and serializes canonically next to their annual gate', () => {
    const parsed = state('revs:3;epss:5;rev:1:10;epsy:10:15;eps:2:20');
    expect(parsed).toMatchObject({ epsStreak: 5, revStreak: 3, epsY: { years: 10, min: 15 } });
    expect(serializeFilters(parsed)).toBe('eps:2:20;epsy:10:15;epss:5;rev:1:10;revs:3');
    expect(serializeFilters(state(serializeFilters(parsed)))).toBe(serializeFilters(parsed));
    expect(state(`epss:${MAX_GATE_YEARS}`).epsStreak).toBe(10);
  });

  it('ignores bad streak tokens; old links are unchanged', () => {
    for (const bad of ['epss:0', 'epss:11', 'epss:2.5', 'epss:x', 'epss:', 'epss', 'revs:-3', 'revs:1e9']) {
      const parsed = state(bad);
      expect(parsed.epsStreak).toBeNull();
      expect(parsed.revStreak).toBeNull();
      expect(serializeFilters(parsed)).toBe('');
    }
    for (const old of ['eps:2:20;rev:1:10;nd:keep', 'epsy:3:10;revy:2:5', 'chg:0:3;hi:10']) expect(serializeFilters(state(old))).toBe(old);
  });

  it('tn / nd follow the streak conditions, chips can be removed, and the length cap holds', () => {
    expect(serializeFilters({ ...EMPTY_FILTERS, epsStreak: 3, turn: false })).toBe('epss:3;tn:0');
    expect(serializeFilters({ ...EMPTY_FILTERS, revStreak: 3, turn: false })).toBe('revs:3'); // tn is about EPS only
    expect(serializeFilters({ ...EMPTY_FILTERS, revStreak: 3, keepMissing: true })).toBe('revs:3;nd:keep');
    expect(needsGrowth({ ...EMPTY_FILTERS, revStreak: 3 })).toBe(true);
    const chips = activeChips({ ...EMPTY_FILTERS, epsStreak: 5, revStreak: 10, turn: false });
    expect(chips.map((chip) => chip.label)).toEqual(['EPS 連續成長 ≥5年', '營收 連續成長 ≥10年', '不含虧轉盈']);
    expect(removeFilter({ ...EMPTY_FILTERS, epsStreak: 5 }, 'epsS').epsStreak).toBeNull();
    expect(removeFilter({ ...EMPTY_FILTERS, revStreak: 5 }, 'revS').revStreak).toBeNull();
    const long = serializeFilters({
      ...state('chg:-12.34:56.78;ema20:-12.34:56.78;tr:a20,a50,a200,slope,stack,nobear;hi:12.34;rs:80;rs3:12.34;rs1:12.34;vr:12.34'),
      eps: { quarters: 4, min: -1234.56 },
      epsY: { years: 10, min: -1234.56 },
      rev: { quarters: 4, min: -1234.56 },
      revY: { years: 10, min: -1234.56 },
      epsStreak: 10,
      revStreak: 10,
      accel: true,
      turn: false,
      keepMissing: true,
    });
    expect(long.length).toBeLessThanOrEqual(FILTER_PARAM_MAX);
    expect(long.split(';').every((token) => token.split(':').length >= 2)).toBe(true); // whole tokens only
  });
});

describe('filtering with ten annual slots', () => {
  const growth = growthFile([
    { eps: [20, 15, 12, 30, 8, 5, 9, 11, 14, 7], rev: [10, 9, 8, 7, 6, 5, 4, 3, 2, 1] }, // 10 years of growth
    { eps: [20, 15, -4, 30], rev: [10, 9, 8] }, // a decline in year 3; 3 years of revenue
    { eps: [null, 12, 11], rev: [5, -2], tY: 1 }, // turned to a profit last year
  ]);
  const items = US.slice(0, 4); // the fourth row has no growth data

  it('epsy:10 and the streaks', () => {
    const symbols = (text: string) => applyFilters(items, state(text), { market: 'US', growth }).map((item) => item.symbol);
    expect(symbols('epsy:10:0')).toEqual([US[0]!.symbol]);
    expect(symbols('epsy:5:5')).toEqual([US[0]!.symbol]);
    expect(symbols('epss:2')).toEqual([US[0]!.symbol, US[1]!.symbol, US[2]!.symbol]);
    expect(symbols('epss:3')).toEqual([US[0]!.symbol, US[2]!.symbol]);
    expect(symbols('epss:3;tn:0')).toEqual([US[0]!.symbol]);
    expect(symbols('epss:10')).toEqual([US[0]!.symbol]);
    expect(symbols('revs:3')).toEqual([US[0]!.symbol, US[1]!.symbol]);
    expect(symbols('revs:5;nd:keep')).toEqual([US[0]!.symbol, US[1]!.symbol, US[3]!.symbol]); // short history kept, a decline is not
  });

  it('coverage and availability count the rows with N annual values', () => {
    expect(gateCoverage(items, growth, 'epsY', 10, true)).toBe(1);
    expect(gateCoverage(items, growth, 'epsS', 3, true)).toBe(3);
    expect(gateCoverage(items, growth, 'epsS', 3, false)).toBe(2);
    expect(gateCoverage(items, growth, 'revS', 5, true)).toBe(1);
    const available = filterAvailability(items, { market: 'US', growth });
    expect(available.epsS).toBeNull();
    expect(available.revS).toBeNull();
  });

  it('row pills show the streak and the annual history', () => {
    const pills = rowMetricPills('default', state('epss:3;revs:3'), []);
    expect(pills).toEqual(['epsS', 'revS']);
    const first = metricPill(items[0]!, 'epsS', growth)!;
    expect(first.text).toBe('EPS連增 10年');
    expect(first.title).toContain('年度（最新→較舊）+20% / +15% / +12% / +30% / +8% / +5% / +9% / +11% / +14% / +7%');
    expect(metricPill(items[2]!, 'epsS', growth)!.text).toBe('EPS連增 3年');
    expect(metricPill(items[2]!, 'revS', growth)!.text).toBe('營收連增 1年');
    expect(metricPill(items[3]!, 'epsS', growth)).toBeNull();
    expect(growth.items.get(growthKey(US[0]!.symbol))!.epsYoYAnnual).toHaveLength(10);
  });
});
