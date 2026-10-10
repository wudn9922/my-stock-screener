import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  EMPTY_FILTERS,
  FILTER_STORAGE_KEY,
  TW_FUNDAMENTALS_REASON,
  activeChips,
  applyFilters,
  countFilters,
  filterAvailability,
  growthGatePasses,
  maDeviation,
  parseFilters,
  readStoredFilters,
  removeFilter,
  serializeFilters,
  writeStoredFilters,
  type FilterState,
} from '../src/pages/screenerFilters';
import { filterAndSortItems, metricPill, rowMetricPills } from '../src/pages/screenerItems';
import { parseUniverse } from '../src/report/universe';
import { groupItemSchema, type GroupItem } from '../src/report/schema';
import { parseGrowthFile } from '../src/fundamentals/GrowthProvider';
import { parseRoute, serializeRoute } from '../src/app/routes';

const universe = parseUniverse(JSON.parse(readFileSync('tests/fixtures/screener/universe.json', 'utf8')));
if (universe.status !== 'ok') throw new Error('universe fixture');
const TW = universe.universe.markets.TW!.items;
const US = universe.universe.markets.US!.items;
const growth = parseGrowthFile(JSON.parse(readFileSync('tests/fixtures/screener/us-growth.json', 'utf8')))!;

const state = (f: string | null) => parseFilters(f);
const item = (symbol: string, metrics: Record<string, unknown> | null, extra: Record<string, unknown> = {}): GroupItem =>
  groupItemSchema.parse({ symbol, close: 100, changePct: 1, maList: [20], maValues: { 20: 95 }, metrics, ...extra });

describe('filter encoding', () => {
  it('parses the documented example and serializes it canonically', () => {
    const parsed = state('chg:0:3;ma50:-5:5;tr:a20,a50,stack;hi:10;rs:80;vr:1.5;eps:2:20;rev:10');
    expect(parsed).toEqual({
      chg: { min: 0, max: 3 },
      ma: { 50: { min: -5, max: 5 } },
      tr: ['a20', 'a50', 'stack'],
      hi: 10,
      rs: 80,
      rs3: null,
      vr: 1.5,
      eps: { quarters: 2, min: 20 },
      rev: { quarters: 1, min: 10 },
      keepMissing: false,
    });
    expect(serializeFilters(parsed)).toBe('chg:0:3;ma50:-5:5;tr:a20,a50,stack;hi:10;rs:80;vr:1.5;eps:2:20;rev:1:10');
  });

  it('round-trips open ranges, every flag and the missing-data switch', () => {
    const text = 'chg::-3;ma20:2:;ma200:-20:20;tr:a20,a50,a200,slope,stack,nobear;rs3:0;eps:3:0;nd:keep';
    const parsed = state(text);
    expect(parsed.chg).toEqual({ min: null, max: -3 });
    expect(parsed.ma['20']).toEqual({ min: 2, max: null });
    expect(parsed.keepMissing).toBe(true);
    expect(serializeFilters(parsed)).toBe(text);
    expect(serializeFilters(state(serializeFilters(parsed)))).toBe(text);
    expect(state('chg:~:-3').chg).toEqual({ min: null, max: -3 });
  });

  it('ignores junk without throwing and keeps the parameter within the route limits', () => {
    expect(state('')).toEqual(EMPTY_FILTERS);
    expect(state(null)).toEqual(EMPTY_FILTERS);
    const junk = state('zzz:1;chg:a:b;hi:0;hi:500;rs:0;rs:150;vr:-1;eps:9:20;eps:x;tr:up,a20,A50;ma50::;;:;rev:1:x');
    expect(junk).toEqual({ ...EMPTY_FILTERS, tr: ['a20', 'a50'] });
    // Reversed bounds are swapped; the last duplicate wins.
    expect(state('chg:5:-2;hi:5;hi:20')).toMatchObject({ chg: { min: -2, max: 5 }, hi: 20 });
    expect(serializeFilters(EMPTY_FILTERS)).toBe('');
    // nd only matters with a fundamental gate.
    expect(serializeFilters({ ...EMPTY_FILTERS, keepMissing: true })).toBe('');
    const full = serializeFilters(state('chg:-12.345:99.999;ma20:-20:20;ma50:-20:20;ma200:-20:20;tr:a20,a50,a200,slope,stack,nobear;hi:20;rs:90;rs3:0;vr:2;eps:3:25;rev:3:20;nd:keep'));
    expect(full.length).toBeLessThanOrEqual(300);
    expect(full).toMatch(/^[A-Za-z0-9:.,;_~-]+$/);
    expect(full.startsWith('chg:-12.35:100;')).toBe(true);
  });

  it('carries the conditions in the screener route', () => {
    const f = 'chg:0:3;tr:a20,stack;eps:2:20';
    const url = serializeRoute({ page: 'screener', group: 'universe-us', f });
    expect(parseRoute(url).route).toEqual({ page: 'screener', group: 'universe-us', f });
    expect(parseRoute(`?page=screener&f=${f}`).route).toEqual({ page: 'screener', f });
    expect(parseRoute('?page=screener&group=tw_g1').route).toEqual({ page: 'screener', group: 'tw_g1' });
    expect(parseRoute('?page=screener&f=<script>').route).toEqual({ page: 'screener' });
    expect(parseRoute(`?page=screener&f=${'a'.repeat(301)}`).route).toEqual({ page: 'screener' });
    expect(parseRoute(`?liff.state=${encodeURIComponent(`/?page=screener&group=us_g1&f=${f}`)}`).route).toEqual({ page: 'screener', group: 'us_g1', f });
  });
});

describe('applyFilters', () => {
  const tw = { market: 'TW' as const };
  const us = { market: 'US' as const, growth };

  it('returns everything with no conditions', () => {
    expect(applyFilters(TW, EMPTY_FILTERS, tw)).toHaveLength(TW.length);
  });

  it('matches each technical condition against the fixture', () => {
    const check = (f: string, predicate: (item: GroupItem) => boolean) => {
      const result = applyFilters(TW, state(f), tw);
      expect(result.length).toBeGreaterThan(0);
      expect(result.length).toBeLessThan(TW.length);
      expect(result).toEqual(TW.filter(predicate));
    };
    check('chg:0:3', (i) => i.changePct! >= 0 && i.changePct! <= 3);
    check('chg::-3', (i) => i.changePct! <= -3);
    check('ma50:-5:5', (i) => i.metrics!.ma['50'] !== null && Math.abs(i.metrics!.ma['50']) <= 5);
    check('tr:a20', (i) => i.metrics!.ma['20']! > 0);
    check('tr:a200', (i) => (i.metrics!.ma['200'] ?? -1) > 0);
    check('tr:slope', (i) => i.metrics!.ma20Slope5! > 0);
    check('tr:stack', (i) => {
      const { 20: a, 50: b, 200: c } = i.metrics!.ma;
      return a !== null && b !== null && c !== null && 0 < a && a < b && b < c;
    });
    check('tr:nobear', (i) => i.metrics!.trend !== 'down');
    check('hi:10', (i) => i.metrics!.fromHi52 !== null && i.metrics!.fromHi52 >= -10);
    check('rs:80', (i) => (i.metrics!.rsRank ?? 0) >= 80);
    check('rs3:0', (i) => (i.metrics!.rs63 ?? -1) > 0);
    check('vr:1.5', (i) => (i.metrics!.volRatio ?? 0) >= 1.5);
  });

  it('combines conditions with AND', () => {
    const combined = applyFilters(TW, state('tr:a20,a50;rs:50'), tw);
    expect(combined).toEqual(
      TW.filter((i) => i.metrics!.ma['20']! > 0 && (i.metrics!.ma['50'] ?? 0) > 0 && (i.metrics!.rsRank ?? 0) >= 50),
    );
    expect(applyFilters(TW, state('rs:99;vr:50'), tw)).toEqual([]);
  });

  it('excludes stocks without the needed value (short history) except for 「排除空頭」', () => {
    const young = item('9999.TW', { ma: { 20: 3, 50: 4, 200: null }, trend: null, hiBars: 80 });
    const old = item('1111.TW', { ma: { 20: 3, 50: 4, 200: 6 }, trend: 'up', hiBars: 201, fromHi52: -2 });
    expect(applyFilters([young, old], state('tr:stack'), tw)).toEqual([old]);
    expect(applyFilters([young, old], state('ma200:-20:20'), tw)).toEqual([old]);
    expect(applyFilters([young, old], state('hi:5'), tw)).toEqual([old]);
    expect(applyFilters([young, old], state('tr:nobear'), tw)).toEqual([young, old]);
  });

  it('falls back to the group MA values when a report row has no metrics', () => {
    const plain = item('2330.TW', null, { maList: [20, 50], maValues: { 20: 95, 50: 110 } });
    expect(maDeviation(plain, 20)).toBeCloseTo(5.263, 3);
    expect(applyFilters([plain], state('tr:a20'), tw)).toEqual([plain]);
    expect(applyFilters([plain], state('tr:a50'), tw)).toEqual([]);
    // Conditions no row can evaluate are skipped (and flagged), not applied as "exclude all".
    expect(applyFilters([plain], state('rs:80;vr:2;hi:10'), tw)).toEqual([plain]);
    const availability = filterAvailability([plain], tw);
    expect(availability).toMatchObject({ chg: null, ma20: null, ma50: null, tr: expect.any(String), rs: expect.any(String), ma200: expect.any(String) });
    expect(applyFilters([plain], state('tr:slope'), tw)).toEqual([plain]);
    expect(activeChips(state('rs:80;tr:a20,slope'), availability).map((chip) => [chip.id, !!chip.unavailable])).toEqual([
      ['tr:a20', false],
      ['tr:slope', true],
      ['rs', true],
    ]);
  });

  it('applies EPS / revenue gates to US stocks only', () => {
    const gate = state('eps:2:20');
    const passing = applyFilters(US, gate, us);
    expect(passing.length).toBeGreaterThan(0);
    for (const row of passing) {
      const record = growth.items.get(row.symbol)!;
      expect(record.epsYoY[0]! >= 20 || (record.epsYoY[0] === null && record.epsTurn)).toBe(true);
      expect(record.epsYoY[1]!).toBeGreaterThanOrEqual(20);
    }
    // Taiwan: not available → skipped with an explanation.
    expect(applyFilters(TW, gate, { market: 'TW', growth })).toHaveLength(TW.length);
    expect(filterAvailability(TW, { market: 'TW', growth }).eps).toBe(TW_FUNDAMENTALS_REASON);
    // US file missing or still loading → skipped, flagged.
    expect(applyFilters(US, gate, { market: 'US', growth: null })).toHaveLength(US.length);
    expect(filterAvailability(US, { market: 'US', growth: null }).eps).toMatch(/尚未產生/);
    expect(filterAvailability(US, { market: 'US', growthLoading: true }).rev).toMatch(/載入中/);
    const revenue = applyFilters(US, state('rev:1:10'), us);
    expect(revenue).toEqual(US.filter((row) => (growth.items.get(row.symbol)?.revYoY[0] ?? -1) >= 10));
  });

  it('excludes or keeps stocks with insufficient fundamentals per the switch', () => {
    const rivian = US.find((row) => row.symbol === 'RIVN')!; // no record at all
    const tesla = US.find((row) => row.symbol === 'TSLA')!; // a missing third quarter
    const intel = US.find((row) => row.symbol === 'INTC')!; // loss → profit, YoY undefined
    const strict = applyFilters([rivian, tesla, intel], state('eps:3:-1000'), us);
    expect(strict.map((row) => row.symbol)).toEqual([]);
    const keep = applyFilters([rivian, tesla, intel], state('eps:3:-1000;nd:keep'), us);
    expect(keep.map((row) => row.symbol)).toEqual(['RIVN', 'TSLA', 'INTC']);
    // A loss-to-profit turn passes a one-quarter gate even when strict.
    expect(applyFilters([intel], state('eps:1:25'), us)).toEqual([intel]);
    expect(growthGatePasses([30, null], { quarters: 2, min: 20 }, false)).toBe(false);
    expect(growthGatePasses([30, null], { quarters: 2, min: 20 }, true)).toBe(true);
    expect(growthGatePasses([30, 10], { quarters: 2, min: 20 }, true)).toBe(false);
    expect(growthGatePasses([null, 40], { quarters: 2, min: 20 }, false, true)).toBe(true);
    expect(growthGatePasses(undefined, { quarters: 1, min: 0 }, false)).toBe(false);
  });
});

describe('active chips', () => {
  it('lists one removable chip per condition', () => {
    const s = state('chg:0:3;ma50:-5:5;tr:a20,stack;hi:10;rs:80;rs3:0;vr:1.5;eps:2:20;rev:1:10;nd:keep');
    const chips = activeChips(s);
    expect(chips.map((chip) => chip.label)).toEqual([
      '今日漲跌 0～+3%',
      'MA50 乖離 -5～+5%',
      '站上MA20',
      '多頭排列',
      '距52週高 ≤10%',
      'RS 百分位 ≥80',
      '3月強於大盤',
      '量比 ≥1.5x',
      'EPS YoY 近2季皆≥20%',
      '營收 YoY ≥10%',
    ]);
    expect(countFilters(s)).toBe(10);
    let next: FilterState = s;
    for (const chip of chips) next = removeFilter(next, chip.id);
    expect(serializeFilters(next)).toBe('');
    expect(serializeFilters(removeFilter(s, 'tr:a20'))).toContain('tr:stack;');
    expect(s.tr).toEqual(['a20', 'stack']); // not mutated
  });
});

describe('screener sorting and row pills', () => {
  it('sorts by the new metric modes with missing values last', () => {
    const symbols = (rows: GroupItem[]) => rows.map((row) => row.symbol);
    const rs = filterAndSortItems(TW, '', 'rs');
    expect(rs[0]!.metrics!.rsRank).toBe(Math.max(...TW.map((row) => row.metrics!.rsRank ?? 0)));
    const hi = filterAndSortItems(TW, '', 'hi');
    expect(hi[0]!.metrics!.fromHi52).toBe(0);
    const ranks = hi.map((row) => row.metrics!.fromHi52 ?? -Infinity);
    expect(ranks).toEqual([...ranks].sort((a, b) => b - a));
    const ma50 = filterAndSortItems(TW, '', 'ma50').map((row) => row.metrics!.ma['50']);
    const firstNull = ma50.indexOf(null);
    if (firstNull >= 0) expect(ma50.slice(firstNull).every((value) => value === null)).toBe(true);
    expect(filterAndSortItems(TW, '', 'vr')[0]!.metrics!.volRatio).toBe(Math.max(...TW.map((row) => row.metrics!.volRatio ?? 0)));
    expect(filterAndSortItems(TW, '', 'r21')[0]!.metrics!.r21).toBe(Math.max(...TW.map((row) => row.metrics!.r21 ?? -Infinity)));
    const eps = symbols(filterAndSortItems(US, '', 'eps', { growth }));
    expect(eps[0]).toBe('INTC'); // the loss-to-profit turn sorts first
    const value = (symbol: string) => {
      const record = growth.items.get(symbol);
      return record ? (record.epsYoY[0] ?? (record.epsTurn ? Infinity : null)) : null;
    };
    const firstMissing = eps.findIndex((symbol) => value(symbol) === null);
    expect(firstMissing).toBeGreaterThan(0);
    expect(eps.slice(firstMissing).every((symbol) => value(symbol) === null)).toBe(true);
    expect(eps.slice(firstMissing)).toContain('RIVN'); // no record
    // Without the growth file the order is unchanged.
    expect(symbols(filterAndSortItems(US, '', 'eps'))).toEqual(symbols(US));
  });

  it('chooses at most two metric pills: the sort first, then the filters', () => {
    expect(rowMetricPills('rs', EMPTY_FILTERS, [20])).toEqual(['rs']);
    expect(rowMetricPills('default', state('hi:10;vr:2;rs:80'), [20])).toEqual(['rs', 'hi']);
    expect(rowMetricPills('eps', state('rev:1:10'), [20])).toEqual(['eps', 'rev']);
    expect(rowMetricPills('ma50', EMPTY_FILTERS, [20, 50, 200])).toEqual([]);
    expect(rowMetricPills('gain', EMPTY_FILTERS, [20])).toEqual([]);
  });

  it('formats the pills', () => {
    const row = item('AAPL', { rsRank: 87, rs63: 12.3, rsBench: 'SPY', fromHi52: -4.26, hiBars: 201, volRatio: 2.345, r21: 6.1 });
    expect(metricPill(row, 'rs')).toMatchObject({ text: 'RS 87', tone: 'up' });
    expect(metricPill(row, 'hi')).toMatchObject({ text: '距高 -4.3%', title: expect.stringContaining('近201日高點') });
    expect(metricPill(row, 'vr')?.text).toBe('量比 2.3x');
    expect(metricPill(row, 'r21')?.text).toBe('1月 +6.1%');
    expect(metricPill(row, 'ma50')).toBeNull();
    expect(metricPill(item('X', null), 'rs')).toBeNull();
    const intel = US.find((r) => r.symbol === 'INTC')!;
    expect(metricPill(intel, 'eps', growth)?.text).toBe('EPS 轉盈');
    expect(metricPill(intel, 'eps')).toBeNull();
  });
});

describe('stored conditions', () => {
  function memoryStorage(initial: Record<string, string> = {}): Storage {
    const data = new Map(Object.entries(initial));
    return {
      get length() {
        return data.size;
      },
      clear: () => data.clear(),
      getItem: (key) => data.get(key) ?? null,
      key: (index) => [...data.keys()][index] ?? null,
      removeItem: (key) => void data.delete(key),
      setItem: (key, value) => void data.set(key, String(value)),
    };
  }

  it('keeps Taiwan and US conditions apart and survives corrupt data', () => {
    const store = memoryStorage();
    expect(readStoredFilters('TW', store)).toBeNull();
    writeStoredFilters('TW', 'tr:a20', store);
    writeStoredFilters('US', 'eps:1:15', store);
    expect(readStoredFilters('TW', store)).toBe('tr:a20');
    expect(readStoredFilters('US', store)).toBe('eps:1:15');
    writeStoredFilters('TW', '', store);
    expect(readStoredFilters('TW', store)).toBe('');
    expect(readStoredFilters('US', store)).toBe('eps:1:15');
    const corrupt = memoryStorage({ [FILTER_STORAGE_KEY]: '{oops' });
    expect(readStoredFilters('TW', corrupt)).toBeNull();
    writeStoredFilters('TW', 'hi:5', corrupt);
    expect(readStoredFilters('TW', corrupt)).toBe('hi:5');
    expect(readStoredFilters('US', memoryStorage({ [FILTER_STORAGE_KEY]: '{"US":"<b>"}' }))).toBeNull();
    expect(readStoredFilters('TW', null)).toBeNull();
    expect(() => writeStoredFilters('TW', 'x', null)).not.toThrow();
  });
});
