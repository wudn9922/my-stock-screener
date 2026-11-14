import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  AMBUSH_MIN_VOLUME_TW,
  EMPTY_FILTERS,
  FILTER_PARAM_MAX,
  TREND_FLAGS,
  TREND_LABELS,
  FILTER_STORAGE_KEY,
  NO_METRICS_REASON,
  NO_GROWTH_REASON,
  NO_GROWTH_ROWS_REASON,
  VOLUME_PRESETS,
  activeChips,
  ambushPreset,
  applyFilters,
  countFilters,
  filterAvailability,
  growthGatePasses,
  isCustomVolume,
  maDeviation,
  parseFilters,
  readStoredFilters,
  removeFilter,
  serializeFilters,
  toggleVolume,
  volumeThresholdOf,
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
// The published file is still in the old format: none of the rows carries rsScore / atrPct / range20.
const OLD_FORMAT_ROWS = TW.every((row) => row.metrics!.atrPct === null && row.metrics!.range20 === null && row.metrics!.rsScore === null);
const growth = parseGrowthFile(JSON.parse(readFileSync('tests/fixtures/screener/us-growth.json', 'utf8')))!;

const state = (f: string | null) => parseFilters(f);
const item = (symbol: string, metrics: Record<string, unknown> | null, extra: Record<string, unknown> = {}): GroupItem =>
  groupItemSchema.parse({ symbol, close: 100, changePct: 1, maList: [20], maValues: { 20: 95 }, metrics, ...extra });

describe('filter encoding', () => {
  it('converts Taiwan fractional lots exactly while US thresholds stay integer shares', () => {
    expect(volumeThresholdOf('1.001', 'TW')).toBe(1001);
    expect(volumeThresholdOf('0.001', 'TW')).toBe(1);
    expect(volumeThresholdOf('1000', 'TW')).toBe(1_000_000);
    expect(volumeThresholdOf('1.001', 'US')).toBeUndefined();
    expect(volumeThresholdOf('1001', 'US')).toBe(1001);
    expect(volumeThresholdOf('0', 'TW')).toBeNull();
    expect(volumeThresholdOf('', 'US')).toBeNull();
    expect(volumeThresholdOf('9007199254740992', 'US')).toBeUndefined();
  });
  it('round-trips share thresholds and rejects invalid values while preserving old links', () => {
    expect(serializeFilters(state('vol:250000;vr:1.2'))).toBe('vr:1.2;vol:250000');
    for (const value of ['0', '-1', '1.5', 'Infinity', '9007199254740992', '10:20', '']) {
      expect(state(`vol:${value}`).vol).toBeNull();
    }
    expect(state('vr:1.2').vol).toBeNull();
    const data: Record<string, string> = {};
    const store = { getItem: (key: string) => data[key] ?? null, setItem: (key: string, value: string) => { data[key] = value; } } as unknown as Storage;
    writeStoredFilters('TW', 'vol:1000000', store);
    writeStoredFilters('US', 'vol:100000', store);
    expect(state(readStoredFilters('TW', store)).vol).toBe(1_000_000);
    expect(state(readStoredFilters('US', store)).vol).toBe(100_000);
  });
  it('parses the documented example and serializes it canonically', () => {
    const parsed = state('chg:0:3;ema20:-5:5;tr:a20,a50,stack;hi:10;rs:80;vr:1.5;eps:2:20;rev:10');
    expect(parsed).toEqual({
      chg: null,
      ema20: { min: -5, max: 5 },
      tr: ['a20', 'a50', 'stack'],
      hi: 10,
      rs: 80,
      rs3: null,
      rs1: null,
      vr: 1.5,
      vol: null,
      eps: { quarters: 2, min: 20, share: 100 },
      rev: { quarters: 1, min: 10, share: 100 },
      epsY: null,
      revY: null,
      epss: null,
      accel: false,
      turn: true,
      keepMissing: false,
    });
    expect(serializeFilters(parsed)).toBe('ema20:-5:5;tr:a20,a50,stack;hi:10;rs:80;vr:1.5;eps:2:20;rev:1:10');
  });

  it('round-trips open ranges, every flag and the missing-data switch', () => {
    const text = 'ema20:2:;tr:a20,a50,a200,slope,stack,nobear;rs3:0;eps:3:0;nd:keep';
    const parsed = state(text);
    expect(parsed.chg).toBeNull();
    expect(parsed.ema20).toEqual({ min: 2, max: null });
    expect(parsed.keepMissing).toBe(true);
    expect(serializeFilters(parsed)).toBe(text);
    expect(serializeFilters(state(serializeFilters(parsed)))).toBe(text);
    expect(state('chg:~:-3').chg).toBeNull();
  });

  it('drops the retired MA20 / MA50 / MA200 ranges instead of migrating them', () => {
    expect(state('ma20:-5:5;ma50:-5:5;ma200:-20:20')).toEqual(EMPTY_FILTERS);
    const mixed = state('chg:0:3;ma50:-5:5;ema20:1:4;ma200::;vr:1.1');
    expect(mixed).toEqual({ ...EMPTY_FILTERS, chg: null, ema20: { min: 1, max: 4 }, vr: 1.1 });
    expect(serializeFilters(mixed)).toBe('ema20:1:4;vr:1.1');
    expect(serializeFilters(mixed)).not.toMatch(/(^|;)ma\d/);
    // Stored (localStorage) values go through the same parser.
    const store = { getItem: () => JSON.stringify({ TW: 'ma50:-5:5;hi:10' }) } as unknown as Storage;
    expect(state(readStoredFilters('TW', store))).toEqual({ ...EMPTY_FILTERS, hi: 10 });
    // An old volume-ratio number is kept, with the new meaning.
    expect(state('vr:2').vr).toBe(2);
  });

  it('ignores junk without throwing and keeps the parameter within the route limits', () => {
    expect(state('')).toEqual(EMPTY_FILTERS);
    expect(state(null)).toEqual(EMPTY_FILTERS);
    const junk = state('zzz:1;chg:a:b;hi:0;hi:500;rs:0;rs:150;vr:-1;eps:9:20;eps:x;tr:up,a20,A50;ema20::;;:;rev:1:x');
    expect(junk).toEqual({ ...EMPTY_FILTERS, tr: ['a20', 'a50'], eps: { quarters: 9, min: 20, share: 100 } });
    // Reversed bounds are swapped; the last duplicate wins.
    expect(state('ema20:5:-2;hi:5;hi:20')).toMatchObject({ ema20: { min: -2, max: 5 }, hi: 20 });
    expect(serializeFilters(EMPTY_FILTERS)).toBe('');
    // nd only matters with a fundamental gate.
    expect(serializeFilters({ ...EMPTY_FILTERS, keepMissing: true })).toBe('');
    const full = serializeFilters(state('chg:-12.345:99.999;ema20:-20:20;tr:a20,a50,a200,slope,stack,nobear;hi:20;rs:90;rs3:0;vr:2;eps:3:25;rev:3:20;nd:keep'));
    expect(full.length).toBeLessThanOrEqual(300);
    expect(full).toMatch(/^[A-Za-z0-9:.,;_~-]+$/);
    expect(full.startsWith('ema20:-20:20;')).toBe(true);
    expect(full).not.toContain('chg:');
  });

  it('carries the conditions in the screener route', () => {
    const f = 'chg:0:3;tr:a20,stack;eps:2:20';
    const url = serializeRoute({ page: 'screener', group: 'universe-us', f });
    expect(parseRoute(url).route).toEqual({ page: 'screener', group: 'universe-us', f });
    expect(parseRoute(`?page=screener&f=${f}`).route).toEqual({ page: 'screener', f });
    expect(parseRoute('?page=screener&group=tw_g1').route).toEqual({ page: 'screener', group: 'tw_g1' });
    expect(parseRoute('?page=screener&f=<script>').route).toEqual({ page: 'screener' });
    expect(parseRoute(`?page=screener&f=${'a'.repeat(FILTER_PARAM_MAX + 1)}`).route).toEqual({ page: 'screener' });
    expect(parseRoute(`?liff.state=${encodeURIComponent(`/?page=screener&group=us_g1&f=${f}`)}`).route).toEqual({ page: 'screener', group: 'us_g1', f });
  });
});

describe('applyFilters', () => {
  const tw = { market: 'TW' as const };
  const us = { market: 'US' as const, growth };

  it('returns everything with no conditions', () => {
    expect(applyFilters(TW, EMPTY_FILTERS, tw)).toHaveLength(TW.length);
  });

  it('filters 20-session average volume inclusively in both markets without substituting daily volume', () => {
    const rows = [
      item('LOW', { volumeAvg20: 99_999 }, { volume: 50_000_000 }),
      item('BOUNDARY', { volumeAvg20: 100_000 }, { volume: 1 }),
      item('HIGH', { volumeAvg20: 1_000_000 }, { volume: 1 }),
      item('ZERO', { volumeAvg20: 0 }, { volume: 1 }),
      item('UNKNOWN', null, { volume: 50_000_000 }),
    ];
    for (const market of ['TW', 'US'] as const) {
      expect(applyFilters(rows, EMPTY_FILTERS, { market })).toEqual(rows);
      expect(applyFilters(rows, state('vol:100000'), { market }).map((row) => row.symbol)).toEqual(['BOUNDARY', 'HIGH']);
    }
    expect(filterAvailability([rows[3]!], tw).vol).toBeNull();
    expect(filterAvailability([rows[4]!], tw).vol).toContain('沒有近 20 日平均成交量資料');
    expect(activeChips(state('vol:1000000'))[0]?.label).toBe('20 日均量 ≥1,000,000 股')
    expect(activeChips(state('vol:1000000'), undefined, 'TW')[0]?.label).toBe('20 日均量 ≥1,000 張');
    expect(serializeFilters(removeFilter(state('vol:1000000'), 'vol'))).toBe('');
  });

  it('matches each technical condition against the fixture', () => {
    const check = (f: string, predicate: (item: GroupItem) => boolean) => {
      const result = applyFilters(TW, state(f), tw);
      expect(result.length).toBeGreaterThan(0);
      expect(result.length).toBeLessThan(TW.length);
      expect(result).toEqual(TW.filter(predicate));
    };
    expect(applyFilters(TW, state('chg:0:3'), tw)).toEqual(TW);
    expect(applyFilters(TW, { ...EMPTY_FILTERS, chg: { min: 3, max: null } }, tw)).toEqual(TW);
    check('ema20:-5:5', (i) => i.metrics!.ema20 !== null && Math.abs(i.metrics!.ema20) <= 5);
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
    check('vr:1.2', (i) => (i.metrics!.volVsPrev ?? 0) >= 1.2);
  });

  it('combines conditions with AND', () => {
    const combined = applyFilters(TW, state('tr:a20,a50;rs:50'), tw);
    expect(combined).toEqual(
      TW.filter((i) => i.metrics!.ma['20']! > 0 && (i.metrics!.ma['50'] ?? 0) > 0 && (i.metrics!.rsRank ?? 0) >= 50),
    );
    expect(applyFilters(TW, state('rs:99;vr:50'), tw)).toEqual([]);
  });

  it('excludes stocks without the needed value (short history) except for 「排除空頭」', () => {
    const young = item('9999.TW', { ma: { 20: 3, 50: 4, 200: null }, ema20: null, trend: null, hiBars: 80 });
    const old = item('1111.TW', { ma: { 20: 3, 50: 4, 200: 6 }, ema20: 2, trend: 'up', hiBars: 201, fromHi52: -2 });
    expect(applyFilters([young, old], state('tr:stack'), tw)).toEqual([old]);
    expect(applyFilters([young, old], state('ema20:-20:20'), tw)).toEqual([old]);
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

  it('filters the EMA20 distance and the volume-vs-previous-day thresholds', () => {
    const rows = [
      item('A', { ema20: 2, volVsPrev: 1.1 }),
      item('B', { ema20: -3, volVsPrev: 1.2 }),
      item('C', { ema20: 6.5, volVsPrev: 1.49 }),
      item('D', { ema20: null, volVsPrev: null }),
    ];
    const symbols = (f: string) => applyFilters(rows, state(f), tw).map((row) => row.symbol);
    expect(symbols('ema20:-5:5')).toEqual(['A', 'B']);
    expect(symbols('ema20:5:')).toEqual(['C']);
    expect(symbols('ema20::0')).toEqual(['B']);
    expect(symbols('vr:1.1')).toEqual(['A', 'B', 'C']);
    expect(symbols('vr:1.2')).toEqual(['B', 'C']);
    expect(symbols('vr:1.5')).toEqual([]);
    expect(symbols('ema20:0:;vr:1.2')).toEqual(['C']);
  });

  it('offers 1.1 / 1.2 / 1.5 as single-choice presets that toggle off', () => {
    expect(VOLUME_PRESETS).toEqual([1.1, 1.2, 1.5]);
    expect(toggleVolume(null, 1.2)).toBe(1.2);
    expect(toggleVolume(1.2, 1.2)).toBeNull(); // tap the applied preset again
    expect(toggleVolume(1.2, 1.5)).toBe(1.5); // single choice: replaces
    expect(toggleVolume(1.5, null)).toBeNull(); // 「不限」
    expect(toggleVolume(null, null)).toBeNull();
    // A number from an old link stays applied but lights no preset.
    expect(isCustomVolume(state('vr:2').vr)).toBe(true);
    expect(isCustomVolume(state('vr:1.2').vr)).toBe(false);
    expect(isCustomVolume(null)).toBe(false);
    expect(toggleVolume(2, 1.1)).toBe(1.1);
  });

  it('skips EMA20 / volume conditions that old reports cannot evaluate, and flags them', () => {
    // A report written before ema20 / volVsPrev existed (its volRatio meant something else).
    const old = item('OLD', { ma: { 20: 3, 50: 4, 200: 6 }, volRatio: 5, trend: 'up' });
    expect(old.metrics!.ema20).toBeNull();
    expect(old.metrics!.volVsPrev).toBeNull();
    const availability = filterAvailability([old], tw);
    expect(availability.ema20).toBe(NO_METRICS_REASON);
    expect(availability.vr).toBe(NO_METRICS_REASON);
    expect(availability.ma20).toBeNull(); // trend flags keep working on the SMA distances
    expect(applyFilters([old], state('ema20:-1:1;vr:1.5;tr:a20'), tw)).toEqual([old]);
    expect(activeChips(state('ema20:-1:1;vr:1.5'), availability).map((chip) => [chip.id, !!chip.unavailable])).toEqual([
      ['ema20', true],
      ['vr', true],
    ]);
    // Once some row has the data the condition applies, and rows without a value drop out.
    const fresh = item('NEW', { ema20: 0.5, volVsPrev: 1.0 });
    expect(filterAvailability([old, fresh], tw)).toMatchObject({ ema20: null, vr: null });
    expect(applyFilters([old, fresh], state('ema20:-1:1'), tw)).toEqual([fresh]);
    expect(applyFilters([old, fresh], state('vr:1.1'), tw)).toEqual([]);
  });

  it('applies EPS / revenue gates from the growth data', () => {
    const gate = state('eps:2:20');
    const passing = applyFilters(US, gate, us);
    expect(passing.length).toBeGreaterThan(0);
    for (const row of passing) {
      const record = growth.items.get(row.symbol)!;
      // The latest two quarters with data: each grows 20% or more, a turnaround counts; a missing one is left out
      const judged = [0, 1].filter((slot) => record.epsYoY[slot] !== null || (slot === 0 && record.epsTurn));
      expect(judged.length).toBeGreaterThanOrEqual(1);
      for (const slot of judged) expect(record.epsYoY[slot] === null ? record.epsTurn : record.epsYoY[slot]! >= 20).toBe(true);
    }
    // Taiwan rows have no record in the US file: nothing there to filter on, so the condition is skipped and flagged.
    expect(applyFilters(TW, gate, { market: 'TW', growth })).toHaveLength(TW.length);
    expect(filterAvailability(TW, { market: 'TW', growth }).eps).toBe(NO_GROWTH_ROWS_REASON);
    // Data missing or still loading → skipped, flagged.
    expect(applyFilters(US, gate, { market: 'US', growth: null })).toHaveLength(US.length);
    expect(filterAvailability(US, { market: 'US', growth: null }).eps).toBe(NO_GROWTH_REASON);
    expect(filterAvailability(US, { market: 'US', growthLoading: true }).rev).toMatch(/載入中/);
    const revenue = applyFilters(US, state('rev:1:10'), us);
    expect(revenue).toEqual(US.filter((row) => (growth.items.get(row.symbol)?.revYoY[0] ?? -1) >= 10));
  });

  it('excludes or keeps stocks with insufficient fundamentals per the switch', () => {
    const rivian = US.find((row) => row.symbol === 'RIVN')!; // no record at all
    const tesla = US.find((row) => row.symbol === 'TSLA')!; // a missing third quarter: two of three have data
    const intel = US.find((row) => row.symbol === 'INTC')!; // loss → profit: the only number is the turnaround
    // Fewer than ceil(3 / 2) = 2 periods with data is 「資料不足」: excluded, or kept with nd:keep
    const strict = applyFilters([rivian, tesla, intel], state('eps:3:-1000'), us);
    expect(strict.map((row) => row.symbol)).toEqual(['TSLA']);
    const keep = applyFilters([rivian, tesla, intel], state('eps:3:-1000;nd:keep'), us);
    expect(keep.map((row) => row.symbol)).toEqual(['RIVN', 'TSLA', 'INTC']);
    // A loss-to-profit turn is growth in a one-quarter gate (含虧轉盈 on) …
    expect(applyFilters([intel], state('eps:1:25'), us)).toEqual([intel]);
    // … and a decline with the switch off, which the keep switch does not rescue (it is not missing data)
    expect(applyFilters([intel], state('eps:1:25;tn:0;nd:keep'), us)).toEqual([]);
    expect(growthGatePasses([30, null], { quarters: 2, min: 20 }, false)).toBe(true); // one of two with data is enough
    expect(growthGatePasses([30, null, null], { quarters: 3, min: 20 }, false)).toBe(false); // one of three is not
    expect(growthGatePasses([30, null, null], { quarters: 3, min: 20 }, true)).toBe(true);
    expect(growthGatePasses([30, 10], { quarters: 2, min: 20 }, true)).toBe(false); // one of two grows: 50% < 100%
    expect(growthGatePasses([null, 40], { quarters: 2, min: 20 }, false, [true], true)).toBe(true);
    expect(growthGatePasses(undefined, { quarters: 1, min: 0 }, false)).toBe(false);
  });
});

describe('active chips', () => {
  it('labels a legacy volume threshold plainly', () => {
    expect(activeChips(state('vr:1.3')).map((chip) => chip.label)).toEqual(['量比 ≥1.3']);
    expect(activeChips(state('vr:1.1')).map((chip) => chip.label)).toEqual(['量比 ≥1.1']);
    expect(removeFilter(state('vr:1.3;ema20:1:2'), 'ema20').ema20).toBeNull();
  });

  it('lists one removable chip per condition', () => {
    const s = state('chg:0:3;ema20:-5:5;tr:a20,stack;hi:10;rs:80;rs3:0;vr:1.5;eps:2:20;rev:1:10;nd:keep');
    const chips = activeChips(s);
    expect(chips.map((chip) => chip.label)).toEqual([
      'EMA20 乖離 -5～+5%',
      '站上MA20',
      '多頭排列',
      '距52週高 ≤10%',
      'RS 百分位 ≥80',
      '3月強於大盤',
      '量比 ≥1.5',
      'EPS YoY 近2季皆≥20%',
      '營收 YoY ≥10%',
    ]);
    expect(countFilters(s)).toBe(9);
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
    expect(filterAndSortItems(TW, '', 'vr')[0]!.metrics!.volVsPrev).toBe(Math.max(...TW.map((row) => row.metrics!.volVsPrev ?? 0)));
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
    expect(rowMetricPills('default', state('ema20:-5:5'), [20])).toEqual(['ema20']);
    expect(rowMetricPills('rs', state('ema20::5;vr:1.2'), [20])).toEqual(['rs', 'vr']);
    expect(rowMetricPills('eps', state('rev:1:10'), [20])).toEqual(['eps', 'rev']);
    expect(rowMetricPills('ma50', EMPTY_FILTERS, [20, 50, 200])).toEqual([]);
    expect(rowMetricPills('gain', EMPTY_FILTERS, [20])).toEqual([]);
  });

  it('formats the pills', () => {
    const row = item('AAPL', { rsRank: 87, rs63: 12.3, rsBench: 'SPY', fromHi52: -4.26, hiBars: 201, volVsPrev: 2.345, ema20: 3.14, r21: 6.1 });
    expect(metricPill(row, 'rs')).toMatchObject({ text: 'RS 87', tone: 'up' });
    expect(metricPill(row, 'hi')).toMatchObject({ text: '距高 -4.3%', title: expect.stringContaining('近201日高點') });
    expect(metricPill(row, 'vr')?.text).toBe('量比 2.3');
    expect(metricPill(row, 'vr')?.title).toContain('前一交易日');
    expect(metricPill(row, 'ema20')).toMatchObject({ text: 'EMA20 +3.1%', tone: 'up' });
    expect(metricPill(item('X', null), 'ema20')).toBeNull();
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

describe('volatility conditions: ATR% and 20-day range width', () => {
  const tw = { market: 'TW' as const };
  const rows = [
    item('LOW', { atrPct: 2, range20: 8 }),
    item('EDGE', { atrPct: 3, range20: 15 }),
    item('OVER', { atrPct: 3.01, range20: 15.01 }),
    item('NONE', { atrPct: null, range20: null }),
    item('ATRONLY', { atrPct: 1 }),
  ];
  const symbols = (f: string, list = rows) => applyFilters(list, state(f), tw).map((row) => row.symbol);

  it('encodes atr:N and rng:N, drops unusable values and keeps old links untouched', () => {
    const parsed = state('atr:3;rng:15');
    expect(parsed).toMatchObject({ atr: 3, rng: 15 });
    expect(serializeFilters(parsed)).toBe('atr:3;rng:15');
    // Canonical order: after the volume threshold, before the fundamentals.
    expect(serializeFilters(state('eps:2:20;rng:12.5;atr:2.5;vol:250000;rs:80'))).toBe('rs:80;vol:250000;atr:2.5;rng:12.5;eps:2:20');
    expect(serializeFilters(state(serializeFilters(state('atr:2.5;rng:12.5'))))).toBe('atr:2.5;rng:12.5');
    for (const bad of ['atr:0', 'atr:-1', 'atr:x', 'atr:', 'atr:1:2', 'rng:0', 'rng:-3', 'rng:Infinity', 'rng:~']) {
      const parsed = state(bad);
      expect(parsed.atr ?? null).toBeNull();
      expect(parsed.rng ?? null).toBeNull();
      expect(serializeFilters(parsed)).toBe('');
    }
    // An old link has neither key, and still equals the empty state.
    expect(state('ema20:-5:5;tr:a20;hi:10;rs:80;vr:1.2;vol:100000')).not.toHaveProperty('atr');
    expect(state('hi:10')).toEqual({ ...EMPTY_FILTERS, hi: 10 });
    expect(serializeFilters({ ...EMPTY_FILTERS, atr: 3, rng: null })).toBe('atr:3');
    const url = serializeRoute({ page: 'screener', group: 'universe-tw', f: 'atr:3;rng:15' });
    expect(parseRoute(url).route).toEqual({ page: 'screener', group: 'universe-tw', f: 'atr:3;rng:15' });
  });

  it('keeps stocks at or below the bound (inclusive) and excludes missing values', () => {
    expect(symbols('atr:3')).toEqual(['LOW', 'EDGE', 'ATRONLY']);
    expect(symbols('rng:15')).toEqual(['LOW', 'EDGE']);
    expect(symbols('atr:3;rng:15')).toEqual(['LOW', 'EDGE']);
    expect(symbols('atr:1')).toEqual(['ATRONLY']);
    expect(symbols('atr:0.5')).toEqual([]);
    // Combined with another condition by AND.
    expect(symbols('atr:3;rs:80', [item('A', { atrPct: 2, rsRank: 90 }), item('B', { atrPct: 2, rsRank: 10 })])).toEqual(['A']);
  });

  it('is unavailable (skipped and flagged) when the loaded file lacks the column', () => {
    const old = [item('OLD1', { rsRank: 50 }), item('OLD2', { rsRank: 60 })];
    const availability = filterAvailability(old, tw);
    expect(availability.atr).toBe(NO_METRICS_REASON);
    expect(availability.rng).toBe(NO_METRICS_REASON);
    expect(applyFilters(old, state('atr:3;rng:10'), tw)).toEqual(old);
    expect(activeChips(state('atr:3;rng:10'), availability).map((chip) => [chip.id, chip.unavailable])).toEqual([
      ['atr', NO_METRICS_REASON],
      ['rng', NO_METRICS_REASON],
    ]);
    // One row with a value switches the condition on for the whole group.
    const mixed = [...old, item('NEW', { atrPct: 2, range20: 5 })];
    expect(filterAvailability(mixed, tw)).toMatchObject({ atr: null, rng: null });
    expect(applyFilters(mixed, state('atr:3'), tw).map((row) => row.symbol)).toEqual(['NEW']);
    // The published (old-format) universe file behaves the same way.
    expect(OLD_FORMAT_ROWS).toBe(true);
    expect(filterAvailability(TW, tw).atr).toBe(NO_METRICS_REASON);
    expect(filterAvailability(TW, tw).rng).toBe(NO_METRICS_REASON);
    expect(applyFilters(TW, state('atr:3;rng:10'), tw)).toEqual(TW);
  });

  it('labels, counts and clears each condition on its own', () => {
    const s = state('hi:10;atr:2.5;rng:15');
    expect(activeChips(s).map((chip) => [chip.id, chip.label])).toEqual([
      ['hi', '距52週高 ≤10%'],
      ['atr', 'ATR% ≤2.5'],
      ['rng', '20 日區間寬度 ≤15%'],
    ]);
    expect(countFilters(s)).toBe(3);
    expect(serializeFilters(removeFilter(s, 'atr'))).toBe('hi:10;rng:15');
    expect(serializeFilters(removeFilter(s, 'rng'))).toBe('hi:10;atr:2.5');
    expect(s).toMatchObject({ atr: 2.5, rng: 15 }); // not mutated
    let next: FilterState = s;
    for (const chip of activeChips(s)) next = removeFilter(next, chip.id);
    expect(serializeFilters(next)).toBe('');
    expect(countFilters(next)).toBe(0);
  });
});

describe('trend flag near20', () => {
  const tw = { market: 'TW' as const };
  const row = (symbol: string, d20: number | null) => item(symbol, { ma: { 20: d20, 50: null, 200: null } });
  const symbols = (rows: GroupItem[], f = 'tr:near20') => applyFilters(rows, state(f), tw).map((r) => r.symbol);

  it('is exactly one new flag, labelled and serialized in canonical order', () => {
    expect(TREND_FLAGS).toHaveLength(7);
    expect(TREND_FLAGS.filter((flag) => flag === 'near20')).toHaveLength(1);
    expect(TREND_LABELS.near20).toBe('貼近MA20（−2%～+1%）');
    expect(serializeFilters(state('tr:near20,slope,a20'))).toBe('tr:a20,slope,near20');
    expect(state('tr:near20,slope').tr).toEqual(['slope', 'near20']);
    expect(activeChips(state('tr:near20')).map((chip) => [chip.id, chip.key, chip.label])).toEqual([['tr:near20', 'tr', '貼近MA20（−2%～+1%）']]);
    expect(serializeFilters(removeFilter(state('tr:near20,slope'), 'tr:near20'))).toBe('tr:slope');
    // The retired MA distance ranges stay retired.
    expect(state('ma20:-2:1')).toEqual(EMPTY_FILTERS);
  });

  it('includes −2 and +1, excludes just outside, and excludes a missing MA20', () => {
    const rows = [
      row('M2', -2), row('P1', 1), row('IN', -0.5), row('ZERO', 0),
      row('LOW', -2.01), row('HIGH', 1.01), row('FAR', 8), row('NULL', null),
    ];
    expect(symbols(rows)).toEqual(['M2', 'P1', 'IN', 'ZERO']);
    expect(filterAvailability(rows, tw).ma20).toBeNull();
    // Combined with the rising-MA20 flag by AND.
    const mixed = [
      item('A', { ma: { 20: 0.5 }, ma20Slope5: 0.4 }),
      item('B', { ma: { 20: 0.5 }, ma20Slope5: -0.1 }),
      item('C', { ma: { 20: 5 }, ma20Slope5: 0.4 }),
    ];
    expect(symbols(mixed, 'tr:near20,slope')).toEqual(['A']);
  });

  it('falls back to the group MA values like a20 does, and is unavailable like a20 when no row has an MA20', () => {
    const near = item('NEAR', null, { close: 100, maValues: { 20: 101 } }); // −0.99 %
    const far = item('FAR', null, { close: 100, maValues: { 20: 95 } }); // +5.26 %
    expect(symbols([near, far])).toEqual(['NEAR']);
    const none = item('NONE', null, { maList: [], maValues: {} });
    const availability = filterAvailability([none], tw);
    expect(availability.ma20).toBe(NO_METRICS_REASON);
    expect(applyFilters([none], state('tr:near20'), tw)).toEqual([none]); // skipped, as tr:a20 is
    expect(applyFilters([none], state('tr:a20'), tw)).toEqual([none]);
    expect(activeChips(state('tr:near20'), availability)[0]!.unavailable).toBe(NO_METRICS_REASON);
    // With some data the missing row is excluded, exactly as for a20.
    expect(symbols([near, none])).toEqual(['NEAR']);
    expect(symbols([near, none], 'tr:a20')).toEqual([]);
  });

  it('matches the fixture rows by the same distance as the a20 flag', () => {
    const result = applyFilters(TW, state('tr:near20'), tw);
    expect(result).toEqual(TW.filter((row) => {
      const d = row.metrics!.ma['20'];
      return d !== null && d >= -2 && d <= 1;
    }));
  });
});

describe('「潛伏」 preset', () => {
  const rows = [
    item('R50', { ma: { 20: 0.5 }, ma20Slope5: 0.3, rsRank: 50, volumeAvg20: 2_000_000 }),
    item('R90', { ma: { 20: -1.5 }, ma20Slope5: 0.1, rsRank: 90, volumeAvg20: 1_000_000 }),
    item('R70', { ma: { 20: 1 }, ma20Slope5: 0.2, rsRank: 70, volumeAvg20: 5_000_000 }),
    item('THIN', { ma: { 20: 0 }, ma20Slope5: 0.2, rsRank: 99, volumeAvg20: 999_999 }),
    item('FALL', { ma: { 20: 0 }, ma20Slope5: -0.2, rsRank: 95, volumeAvg20: 9_000_000 }),
    item('FAR', { ma: { 20: 6 }, ma20Slope5: 0.2, rsRank: 80, volumeAvg20: 9_000_000 }),
  ];

  it('Taiwan: near20 + slope, volume 1,000,000 shares, sorted by RS descending', () => {
    const preset = ambushPreset('TW');
    expect(preset.sort).toBe('rs');
    expect(AMBUSH_MIN_VOLUME_TW).toBe(1_000_000);
    expect(preset.filters).toEqual({ ...EMPTY_FILTERS, tr: ['slope', 'near20'], vol: 1_000_000 });
    expect(serializeFilters(preset.filters)).toBe('tr:slope,near20;vol:1000000');
    expect(activeChips(preset.filters, undefined, 'TW').map((chip) => chip.label)).toEqual(['MA20上彎', '貼近MA20（−2%～+1%）', '20 日均量 ≥1,000 張']);
    const shown = applyFilters(rows, preset.filters, { market: 'TW' });
    expect(filterAndSortItems(shown, '', preset.sort).map((row) => row.symbol)).toEqual(['R90', 'R70', 'R50']);
  });

  it('US: the volume condition is left unset', () => {
    const preset = ambushPreset('US');
    expect(preset.sort).toBe('rs');
    expect(preset.filters.vol).toBeNull();
    expect(serializeFilters(preset.filters)).toBe('tr:slope,near20');
    const shown = applyFilters(rows, preset.filters, { market: 'US' });
    expect(filterAndSortItems(shown, '', preset.sort).map((row) => row.symbol)).toEqual(['THIN', 'R90', 'R70', 'R50']);
  });

  it('is ordinary filter state: shares through the link and each condition removes individually', () => {
    const preset = ambushPreset('TW');
    const text = serializeFilters(preset.filters);
    expect(parseFilters(text)).toEqual(preset.filters);
    expect(parseRoute(serializeRoute({ page: 'screener', group: 'universe-tw', f: text })).route).toEqual({ page: 'screener', group: 'universe-tw', f: text });
    const ids = activeChips(preset.filters, undefined, 'TW').map((chip) => chip.id);
    expect(ids).toEqual(['tr:slope', 'tr:near20', 'vol']);
    expect(serializeFilters(removeFilter(preset.filters, 'tr:near20'))).toBe('tr:slope;vol:1000000');
    expect(serializeFilters(removeFilter(preset.filters, 'tr:slope'))).toBe('tr:near20;vol:1000000');
    expect(serializeFilters(removeFilter(preset.filters, 'vol'))).toBe('tr:slope,near20');
    // The preset object is not shared state: removing a condition never changes the next preset.
    expect(ambushPreset('TW').filters.tr).toEqual(['slope', 'near20']);
    expect(preset.filters.tr).toEqual(['slope', 'near20']);
  });
});
