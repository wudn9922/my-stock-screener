import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  EMPTY_FILTERS,
  FILTER_PARAM_MAX,
  GROWTH_PRESETS,
  accelCoverage,
  activeChips,
  applyFilters,
  epsAccelStatus,
  epsStreak,
  filterAvailability,
  gateCoverage,
  growthGatePasses,
  isCustomGrowthMin,
  needsGrowth,
  parseFilters,
  removeFilter,
  serializeFilters,
  streakPasses,
  NO_GROWTH_REASON,
  type FilterState,
} from '../src/pages/screenerFilters';
import { metricPill, rowMetricPills, filterAndSortItems } from '../src/pages/screenerItems';
import { parseUniverse } from '../src/report/universe';
import { groupItemSchema, type GroupItem } from '../src/report/schema';
import {
  GrowthProvider,
  growthKey,
  mergeGrowth,
  parseGrowthFile,
  parseReportGrowth,
  parseReportGrowthRow,
  type GrowthFile,
} from '../src/fundamentals/GrowthProvider';
import { parseRoute, serializeRoute } from '../src/app/routes';

const universe = parseUniverse(JSON.parse(readFileSync('tests/fixtures/screener/universe.json', 'utf8')));
if (universe.status !== 'ok') throw new Error('universe fixture');
const TW = universe.universe.markets.TW!.items;
const US = universe.universe.markets.US!.items;
const growthText = readFileSync('tests/fixtures/screener/growth.json', 'utf8');
const reportGrowth = parseReportGrowth(JSON.parse(growthText))!;
const yahoo = parseGrowthFile(JSON.parse(readFileSync('tests/fixtures/screener/us-growth.json', 'utf8')))!;
const merged = mergeGrowth(yahoo, reportGrowth)!;

const state = (f: string | null) => parseFilters(f);
const tw = { market: 'TW' as const, growth: reportGrowth };
const us = { market: 'US' as const, growth: merged };
const symbols = (rows: readonly GroupItem[]) => rows.map((row) => row.symbol);
const row = (symbol: string) => [...TW, ...US].find((item) => item.symbol === symbol)!;
const plain = (symbol: string): GroupItem => groupItemSchema.parse({ symbol, close: 10, changePct: 1, maList: [20], maValues: {} });

describe('f encoding of the fundamental conditions', () => {
  it('round-trips annual gates, acceleration, the turnaround switch and the 1-month RS', () => {
    const text = 'rs3:0;rs1:0;eps:4:30;epsy:2:15;rev:3:10;revy:3:0;acc:1;tn:0;nd:keep';
    const parsed = state(text);
    expect(parsed).toMatchObject({
      rs3: 0,
      rs1: 0,
      eps: { quarters: 4, min: 30 },
      epsY: { years: 2, min: 15 },
      rev: { quarters: 3, min: 10 },
      revY: { years: 3, min: 0 },
      accel: true,
      turn: false,
      keepMissing: true,
    });
    expect(serializeFilters(parsed)).toBe(text);
    expect(serializeFilters(state(serializeFilters(parsed)))).toBe(text);
  });

  it('keeps every old link working and canonical', () => {
    for (const old of ['eps:2:20;rev:1:10;nd:keep', 'chg:0:3;ema20:-5:5;tr:a20,stack;hi:10;rs:80;rs3:0;vr:1.2;eps:3:0', 'rev:10', 'eps:15']) {
      const parsed = state(old);
      expect(parsed.turn).toBe(true); // the default still counts a turnaround
      expect(parsed.accel).toBe(false);
      expect(parsed.epsY).toBeNull();
      expect(serializeFilters(parsed)).toBe(old === 'rev:10' ? 'rev:1:10' : old === 'eps:15' ? 'eps:1:15' : old);
    }
    // The documented annual shape with a single number means one year.
    expect(state('epsy:15').epsY).toEqual({ years: 1, min: 15 });
    expect(serializeFilters(state('epsy:15'))).toBe('epsy:1:15');
  });

  it('ignores bad annual / switch tokens and out-of-range periods', () => {
    expect(state('epsy:0:5').epsY).toBeNull();
    expect(state('epsy:11:5').epsY).toBeNull(); // the stored annual horizon is 10 years
    expect(state('epsy:10:5').epsY).toEqual({ years: 10, min: 5 });
    expect(state('revy:x:5').revY).toBeNull();
    expect(state('revy:2:').revY).toBeNull();
    expect(state('eps:5:20').eps).toBeNull();
    expect(state('eps:4:20').eps).toEqual({ quarters: 4, min: 20 });
    expect(state('rs1:abc').rs1).toBeNull();
    expect(state('acc:0').accel).toBe(false);
    expect(state('tn:1').turn).toBe(true);
    expect(state('foo:1;epsy:2:10;bar').epsY).toEqual({ years: 2, min: 10 });
    expect(state('epss:0').epss).toBeNull();
    expect(state('epss:11').epss).toBeNull();
    expect(state('epss:2.5').epss).toBeNull();
    expect(state('epss:').epss).toBeNull();
    expect(state('epss').epss).toBeNull();
    expect(state('epss:3:5').epss).toBeNull(); // one number only
    expect(state('epss:abc').epss).toBeNull();
    expect(state('epss:10').epss).toBe(10);
    // Upper case keys and a repeated token: the last one wins.
    expect(state('EPSY:1:5;epsy:3:7').epsY).toEqual({ years: 3, min: 7 });
  });

  it('only writes tn / nd when they matter', () => {
    expect(serializeFilters({ ...EMPTY_FILTERS, turn: false })).toBe('');
    expect(serializeFilters({ ...EMPTY_FILTERS, turn: false, revY: { years: 1, min: 5 } })).toBe('revy:1:5');
    expect(serializeFilters({ ...EMPTY_FILTERS, turn: false, accel: true })).toBe('acc:1;tn:0');
    expect(serializeFilters({ ...EMPTY_FILTERS, keepMissing: true, revY: { years: 1, min: 5 } })).toBe('revy:1:5;nd:keep');
    expect(serializeFilters({ ...EMPTY_FILTERS, keepMissing: true, accel: true })).toBe('acc:1;nd:keep');
    expect(serializeFilters({ ...EMPTY_FILTERS, keepMissing: true })).toBe('');
  });

  it('fits the longest realistic link and never cuts a token in half', () => {
    const everything = state(
      'chg:-12.35:100;ema20:-20:20;tr:a20,a50,a200,slope,stack,nobear;hi:20;rs:90;rs3:0;rs1:0;vr:2;eps:4:50;epsy:3:50;rev:4:50;revy:3:50;acc:1;tn:0;nd:keep',
    );
    const text = serializeFilters(everything);
    expect(text.length).toBeLessThanOrEqual(FILTER_PARAM_MAX);
    expect(state(text)).toEqual(everything);
    // Absurd numbers overflow the limit: later whole tokens are dropped, the rest stays parseable.
    const huge = { ...everything, chg: { min: -1e15, max: 1e15 }, ema20: { min: -1e14, max: 1e14 }, vr: 1e15, hi: 99.99 };
    const long = serializeFilters(huge);
    expect(long.length).toBeLessThanOrEqual(FILTER_PARAM_MAX);
    expect(long).toMatch(/^[A-Za-z0-9:.,;_~+-]+$/);
    for (const token of long.split(';')) expect(token).toMatch(/^(chg|ema20|tr|hi|rs|rs3|rs1|vr|eps|epsy|rev|revy|acc|tn|nd):/);
    expect(serializeFilters(state(long))).toBe(long);
  });

  it('is carried by the screener route', () => {
    const f = 'epsy:2:15;revy:1:0;acc:1;rs1:0';
    expect(parseRoute(serializeRoute({ page: 'screener', group: 'universe-tw', f })).route).toEqual({ page: 'screener', group: 'universe-tw', f });
  });
});

describe('growth gates', () => {
  it('treats a turnaround per period, with the old boolean meaning the latest one', () => {
    const gate = { quarters: 3, min: 20 } as const;
    expect(growthGatePasses([30, 25, 40], gate, false)).toBe(true);
    expect(growthGatePasses([30, 19.9, 40], gate, false)).toBe(false);
    expect(growthGatePasses([null, 25, 40], gate, false, true)).toBe(true);
    expect(growthGatePasses([null, 25, 40], gate, false, [true])).toBe(true);
    expect(growthGatePasses([30, null, 40], gate, false, [false, true])).toBe(true);
    expect(growthGatePasses([30, null, 40], gate, false, [true])).toBe(false);
    expect(growthGatePasses([30, 25], gate, false)).toBe(false); // history too short
    expect(growthGatePasses([30, 25], gate, true)).toBe(true); // …unless missing data is kept
    expect(growthGatePasses([30, 5], { years: 1, min: 20 }, false)).toBe(true);
    expect(growthGatePasses([30, 5], { years: 2, min: 20 }, false)).toBe(false);
    expect(growthGatePasses([], { years: 1, min: -Infinity }, false)).toBe(false);
  });

  it('judges acceleration from the latest two quarters', () => {
    const record = (epsYoY: (number | null)[], epsTurnQ: boolean[] = []) => ({ ...reportGrowth.items.get(growthKey('2330.TW'))!, epsYoY, epsTurnQ });
    expect(epsAccelStatus(record([25, 10]), true)).toBe('yes');
    expect(epsAccelStatus(record([10, 25]), true)).toBe('no');
    expect(epsAccelStatus(record([10, 10]), true)).toBe('no'); // strictly above
    expect(epsAccelStatus(record([-5, -20]), true)).toBe('yes'); // a smaller decline is still a higher YoY
    expect(epsAccelStatus(record([25]), true)).toBe('unknown');
    expect(epsAccelStatus(record([null, 30], [true]), true)).toBe('yes'); // turned to a profit after growth
    expect(epsAccelStatus(record([null, 30], [true]), false)).toBe('unknown');
    expect(epsAccelStatus(record([20, null], [false, true]), true)).toBe('no'); // the quarter before was the turnaround
    expect(epsAccelStatus(record([null, null], [true, true]), true)).toBe('unknown');
    expect(epsAccelStatus(null, true)).toBe('unknown');
  });
});

describe('growth.json', () => {
  it('parses the contract fixture, masks and quality', () => {
    expect(reportGrowth.generatedAt).toBe('2026-10-13T16:30:00Z');
    const tsmc = reportGrowth.items.get('2330-TW')!;
    expect(tsmc).toMatchObject({
      epsYoY: [30, 20],
      revYoY: [28, 15],
      epsYoYAnnual: [25, 15, 10],
      revYoYAnnual: [20, 12, 8],
      latest: '2026-06-30',
      latestAnnual: '2025-12-31',
      source: 'financials',
      originQ: { eps: 'mops', rev: 'mops' },
      originY: 'mops',
      derivedQ: [false, true, false, false],
      quality: 'good',
      epsTurn: false,
    });
    const plastics = reportGrowth.items.get('1301-TW')!;
    expect(plastics).toMatchObject({ epsTurn: true, epsTurnQ: [true, false, false, false], epsYoY: [null, 80] });
    expect(reportGrowth.items.get('2454-TW')!.quality).toBe('partial');
    expect(reportGrowth.items.has('2317-TW')).toBe(false);
  });

  it('is tolerant per row and strict about the document', () => {
    expect(parseReportGrowth(null)).toBeNull();
    expect(parseReportGrowth({ version: 2, rows: [] })).toBeNull();
    expect(parseReportGrowth({ version: 1, rows: 'x' })).toBeNull();
    expect(parseReportGrowthRow(['', 'good'])).toBeNull();
    expect(parseReportGrowthRow('x')).toBeNull();
    const file = parseReportGrowth({
      version: 1,
      fields: ['symbol', 'epsQ', 'tQ', 'lv'],
      rows: [['6488.TWO', [10, 'x', 5, 6, 7, 8], 6, 'weird'], 7, ['BRK.B', [1]]],
    })!;
    expect([...file.items.keys()]).toEqual(['6488-TWO', 'BRK-B']);
    expect(file.items.get('6488-TWO')).toMatchObject({ epsYoY: [10, null, 5, 6], epsTurnQ: [false, true, true, false], quality: null, revYoY: [] });
    // Without `fields` the contract order is assumed.
    expect(parseReportGrowth({ version: 1, rows: [['2330.TW', 'good', 'yahoo', '2026-06-30', '2025-12-31', [9], [8], [7], [6], 0, 0]] })!.items.get('2330-TW')!.epsYoYAnnual).toEqual([7]);
    expect(growthKey('6488.TWO')).toBe('6488-TWO');
  });

  it('loads through GrowthProvider with its own url and parser, keeping the last good copy', async () => {
    let fail = false;
    let now = 0;
    const fetcher = vi.fn(async () => (fail ? new Response('down', { status: 503 }) : new Response(growthText)));
    const provider = new GrowthProvider('', { url: '/my-stock-screener/report/growth.json', parse: parseReportGrowth, fetcher, now: () => now });
    expect(provider.url).toBe('/my-stock-screener/report/growth.json');
    const first = (await provider.load())!;
    expect(first.items.size).toBe(reportGrowth.items.size);
    fail = true;
    now = 31 * 60_000;
    expect(await provider.load()).toBe(first);
  });
});

describe('merging growth.json with the Yahoo quarters', () => {
  it('takes growth.json first and the Yahoo quarters only when they are a newer report', () => {
    // MSFT: both files have the quarter ending in June: growth.json (SEC) wins
    const msft = merged.items.get('MSFT')!;
    expect(msft.epsYoY).toEqual(reportGrowth.items.get('MSFT')!.epsYoY);
    expect(msft.revYoY).toEqual(reportGrowth.items.get('MSFT')!.revYoY);
    expect(msft.source).toBe('financials');
    expect(msft.originQ).toEqual({ eps: 'sec', rev: 'sec' });
    // AAPL: growth.json still ends in March, the Yahoo file already has June (> 45 days newer)
    const aapl = merged.items.get('AAPL')!;
    expect(aapl.epsYoY).toEqual(yahoo.items.get('AAPL')!.epsYoY); // not the -99 placeholders of the older quarter
    expect(aapl.revYoY).toEqual(yahoo.items.get('AAPL')!.revYoY);
    expect(aapl.latest).toBe('2026-06-30');
    expect(aapl.source).toBe('yahoo');
    expect(aapl.originQ).toEqual({ eps: 'us-growth', rev: 'us-growth' });
    expect(aapl.originY).toBe('sec');
    expect(aapl.epsYoYAnnual).toEqual(reportGrowth.items.get('AAPL')!.epsYoYAnnual);
    expect(aapl.latestAnnual).toBe('2025-09-27');
    expect(aapl.quality).toBe('good');
    // AMZN: growth.json has no EPS numbers for it, Yahoo neither; revenue stays growth.json's
    expect(merged.items.get('AMZN')!.originQ).toEqual({ eps: 'sec', rev: 'sec' });
    // RIVN is not in the Yahoo file: the growth.json record is used as it is.
    expect(merged.items.get('RIVN')).toEqual(reportGrowth.items.get('RIVN'));
    expect(merged.items.get('RIVN')!.originQ.eps).toBe('frames');
    expect(merged.items.get('RIVN')!.derivedQ[0]).toBe(true);
    // A Yahoo-only stock keeps its record.
    const onlyYahoo = [...yahoo.items.keys()].find((key) => !reportGrowth.items.has(key));
    if (onlyYahoo) expect(merged.items.get(onlyYahoo)).toEqual(yahoo.items.get(onlyYahoo));
    // Taiwan rows are untouched.
    expect(merged.items.get('2330-TW')).toEqual(reportGrowth.items.get('2330-TW'));
  });

  it('fills a metric growth.json has nothing for, per metric', () => {
    const msftYahoo = yahoo.items.get('MSFT')!;
    const msftFile = reportGrowth.items.get('MSFT')!;
    const fin: GrowthFile = { generatedAt: 'f', source: 'financials', items: new Map([['MSFT', { ...msftFile, epsYoY: [], epsTurnQ: [false] }]]) };
    const y: GrowthFile = { generatedAt: 'y', source: 'Yahoo Finance', items: new Map([['MSFT', msftYahoo]]) };
    const result = mergeGrowth(y, fin)!.items.get('MSFT')!;
    expect(result.epsYoY).toEqual(msftYahoo.epsYoY); // growth.json had no EPS numbers
    expect(result.revYoY).toEqual(msftFile.revYoY); // the same quarter: growth.json's revenue stays
    expect(result.originQ).toEqual({ eps: 'us-growth', rev: 'sec' });
    // Yahoo without the metric never replaces anything
    const empty: GrowthFile = { generatedAt: 'y', source: 'Yahoo', items: new Map([['MSFT', { ...msftYahoo, epsYoY: [null], revYoY: [], epsTurnQ: [false] }]]) };
    expect(mergeGrowth(empty, reportGrowth)!.items.get('MSFT')).toEqual(msftFile);
  });

  it('survives either file missing', () => {
    expect(mergeGrowth(null, null)).toBeNull();
    expect(mergeGrowth(yahoo, null)).toBe(yahoo);
    expect(mergeGrowth(null, reportGrowth)!.items.size).toBe(reportGrowth.items.size);
    expect(mergeGrowth(undefined, reportGrowth)!.items.get('2330-TW')).toBeDefined();
  });
});

describe('fundamental filters on the market lists', () => {
  const record = (symbol: string) => reportGrowth.items.get(growthKey(symbol))!;

  it('filters Taiwan stocks on quarterly EPS growth', () => {
    const result = applyFilters(TW, state('eps:2:20'), tw);
    expect(result.length).toBeGreaterThan(0);
    expect(result.length).toBeLessThan(TW.length);
    expect(symbols(result)).toEqual(
      symbols(TW.filter((item) => {
        const r = reportGrowth.items.get(growthKey(item.symbol));
        return !!r && [0, 1].every((slot) => (r.epsYoY[slot] ?? -Infinity) >= 20 || r.epsTurnQ[slot]);
      })),
    );
    expect(symbols(result)).toContain('2330.TW');
    expect(symbols(result)).not.toContain('2317.TW'); // no financials at all → excluded
    expect(symbols(result)).not.toContain('2412.TW'); // 5% latest quarter
  });

  it('filters on annual EPS / revenue growth and reports the periods it needs', () => {
    expect(symbols(applyFilters(TW, state('epsy:3:10'), tw))).toContain('2330.TW');
    expect(symbols(applyFilters(TW, state('epsy:3:10'), tw))).not.toContain('2454.TW'); // two fiscal years only
    expect(symbols(applyFilters(TW, state('epsy:2:20'), tw))).toContain('2454.TW');
    expect(symbols(applyFilters(TW, state('epsy:1:0;revy:3:0'), tw))).not.toContain('2412.TW');
    const strict = applyFilters(TW, state('epsy:3:0'), tw);
    const lenient = applyFilters(TW, state('epsy:3:0;nd:keep'), tw);
    expect(lenient.length).toBeGreaterThan(strict.length);
    expect(symbols(lenient)).toContain('2317.TW'); // kept although it has no data
    for (const kept of strict) expect(symbols(lenient)).toContain(kept.symbol);
  });

  it('accelerates: latest quarter YoY above the previous quarter', () => {
    const result = symbols(applyFilters(TW, state('acc:1'), tw));
    expect(result).toContain('2308.TW'); // 40 vs 10
    expect(result).toContain('1301.TW'); // turned to a profit after +80%
    expect(result).not.toContain('2412.TW'); // 5 vs 25
    expect(result).not.toContain('2454.TW'); // one quarter only
    expect(symbols(applyFilters(TW, state('acc:1;tn:0'), tw))).not.toContain('1301.TW');
    expect(symbols(applyFilters(TW, state('acc:1;nd:keep'), tw))).toContain('2454.TW');
    // Combined with a growth gate: AND.
    const gate = symbols(applyFilters(TW, state('eps:2:20'), tw));
    expect(symbols(applyFilters(TW, state('acc:1;eps:2:20'), tw))).toEqual(result.filter((symbol) => gate.includes(symbol)));
    expect(symbols(applyFilters(TW, state('acc:1;eps:2:20'), tw))).toContain('2330.TW'); // 30 vs 20, both ≥ 20
  });

  it('counts a loss-to-profit turnaround only while 「含虧轉盈」 is on', () => {
    expect(record('1301.TW').epsTurnQ[0]).toBe(true);
    expect(symbols(applyFilters(TW, state('eps:1:25'), tw))).toContain('1301.TW');
    expect(symbols(applyFilters(TW, state('eps:1:25;tn:0'), tw))).not.toContain('1301.TW');
    expect(symbols(applyFilters(TW, state('eps:1:25;tn:0;nd:keep'), tw))).toContain('1301.TW'); // unknown → kept
    // The previous quarter (+80%) passes, the latest one is the turnaround
    expect(symbols(applyFilters(TW, state('eps:2:50'), tw))).toContain('1301.TW');
    expect(symbols(applyFilters(TW, state('eps:2:50;tn:0'), tw))).not.toContain('1301.TW');
    // Revenue gates do not know turnarounds.
    expect(symbols(applyFilters(TW, state('rev:1:15;tn:0'), tw))).toContain('1301.TW');
  });

  it('uses growth.json for the US list, the Yahoo quarters where they are newer', () => {
    const result = symbols(applyFilters(US, state('eps:1:20;rev:1:10'), us));
    // RIVN only exists in growth.json (+45% EPS, +60% revenue)
    expect(result).toContain('RIVN');
    // AAPL's growth.json quarter is older (-99%); the newer Yahoo numbers decide
    expect(result).not.toContain('AAPL');
    // Annual growth comes from growth.json for the Yahoo stocks too
    const annual = symbols(applyFilters(US, state('epsy:3:-1000'), us));
    expect(annual).toContain('AAPL');
  });

  it('tells which condition has data and what is missing', () => {
    const availability = filterAvailability(TW, tw);
    expect(availability).toMatchObject({ eps: null, rev: null, epsY: null, revY: null, accel: null });
    expect(filterAvailability(TW, { market: 'TW', growth: undefined }).eps).toMatch(/載入中/);
    expect(filterAvailability(TW, { market: 'TW', growth: null }).epsY).toBe(NO_GROWTH_REASON);
    // A group none of whose rows is in the file
    const unknown = [plain('9999.TW')];
    expect(filterAvailability(unknown, tw).accel).toMatch(/沒有/);
    expect(applyFilters(unknown, state('epsy:3:10'), tw)).toEqual(unknown); // skipped, not "exclude all"
    // Only annual data around: quarterly conditions are flagged, annual ones work
    const annualOnly: GrowthFile = {
      generatedAt: null,
      source: null,
      items: new Map([['9999-TW', { ...record('2330.TW'), epsYoY: [], revYoY: [], epsTurnQ: [], epsTurn: false }]]),
    };
    const partial = filterAvailability(unknown, { market: 'TW', growth: annualOnly });
    expect(partial.eps).not.toBeNull();
    expect(partial.accel).not.toBeNull();
    expect(partial.epsY).toBeNull();
  });

  it('reports how many rows a condition can actually cover', () => {
    const covered = (kind: 'eps' | 'rev' | 'epsY' | 'revY', n: number, turn = true) => gateCoverage(TW, reportGrowth, kind, n, turn);
    expect(covered('eps', 1)).toBeGreaterThan(covered('eps', 2) - 1);
    expect(covered('eps', 2)).toBeGreaterThan(0);
    expect(covered('eps', 3)).toBe(0); // Taiwan files only reach two quarters
    expect(covered('eps', 4)).toBe(0);
    expect(covered('epsY', 3)).toBeGreaterThan(0);
    expect(covered('epsY', 1)).toBeGreaterThanOrEqual(covered('epsY', 3));
    expect(covered('eps', 2, false)).toBeLessThanOrEqual(covered('eps', 2, true)); // turnarounds count as data only when allowed
    expect(accelCoverage(TW, reportGrowth, true)).toBeGreaterThan(0);
    expect(accelCoverage(TW, reportGrowth, true)).toBeLessThan(TW.length);
    expect(gateCoverage(TW, null, 'eps', 1, true)).toBe(0);
  });

  it('draws chips for the new conditions and removes them one by one', () => {
    const s = state('rs1:0;eps:2:20;epsy:3:10;rev:1:0;revy:2:5;acc:1;tn:0;nd:keep');
    const chips = activeChips(s, filterAvailability(TW, tw));
    expect(chips.map((chip) => chip.label)).toEqual([
      '1月強於大盤',
      'EPS YoY 近2季皆≥20%',
      'EPS 年增 近3年皆≥10%',
      '營收 YoY ≥0%',
      '營收 年增 近2年皆≥5%',
      'EPS 成長加速',
      '不含虧轉盈',
    ]);
    expect(chips.every((chip) => chip.unavailable === null)).toBe(true);
    let next: FilterState = s;
    for (const chip of chips) next = removeFilter(next, chip.id);
    expect(serializeFilters(next)).toBe('');
    expect(next.turn).toBe(true);
    expect(needsGrowth(state('epsy:1:0'))).toBe(true);
    expect(needsGrowth(state('acc:1'))).toBe(true);
    expect(needsGrowth(state('rs1:0'))).toBe(false);
    // The turnaround chip is only shown while it changes something
    expect(activeChips(state('tn:0;rs:80')).map((chip) => chip.id)).toEqual(['rs']);
    expect(activeChips(state('tn:0;eps:1:5')).map((chip) => chip.id)).toEqual(['eps', 'turn']);
  });

  it('offers 0 / 10 / 20 / 30 / 50 % and treats other numbers from a link as custom', () => {
    expect(GROWTH_PRESETS).toEqual([0, 10, 20, 30, 50]);
    expect(isCustomGrowthMin(25)).toBe(true);
    expect(isCustomGrowthMin(20)).toBe(false);
    expect(isCustomGrowthMin(null)).toBe(false);
  });
});

describe('1-month relative strength', () => {
  const rows = [
    groupItemSchema.parse({ symbol: 'A', close: 1, changePct: 0, maList: [], maValues: {}, metrics: { rs21: 3 } }),
    groupItemSchema.parse({ symbol: 'B', close: 1, changePct: 0, maList: [], maValues: {}, metrics: { rs21: -2 } }),
    groupItemSchema.parse({ symbol: 'C', close: 1, changePct: 0, maList: [], maValues: {}, metrics: { rs21: null } }),
    groupItemSchema.parse({ symbol: 'D', close: 1, changePct: 0, maList: [], maValues: {}, metrics: { rs21: 0 } }),
  ];
  it('keeps only stocks that beat the benchmark over one month (strictly above)', () => {
    expect(symbols(applyFilters(rows, state('rs1:0'), { market: 'TW' }))).toEqual(['A']);
    expect(symbols(applyFilters(rows, state('rs1:-5'), { market: 'TW' }))).toEqual(['A', 'B', 'D']);
    expect(symbols(applyFilters(rows, state('rs1:2.5'), { market: 'TW' }))).toEqual(['A']);
  });
  it('is skipped and flagged when no row has the value, and works on the full-market lists', () => {
    expect(filterAvailability([plain('X')], { market: 'TW' }).rs1).not.toBeNull();
    expect(applyFilters([plain('X')], state('rs1:0'), { market: 'TW' })).toHaveLength(1);
    const result = applyFilters(TW, state('rs1:0'), tw);
    expect(result).toEqual(TW.filter((item) => (item.metrics?.rs21 ?? -Infinity) > 0));
    expect(result.length).toBeGreaterThan(0);
    expect(applyFilters(US, state('rs1:0'), us)).toEqual(US.filter((item) => (item.metrics?.rs21 ?? -Infinity) > 0));
  });
});

describe('pills and sorting with growth', () => {
  it('names the FinMind source of Taiwan blocks (republished MOPS data)', () => {
    const file = parseReportGrowth({
      version: 1,
      fields: ['symbol', 'lv', 'src', 'qEnd', 'yEnd', 'epsQ', 'revQ', 'epsY', 'revY', 'tQ', 'tY', 'srcY', 'dQ'],
      rows: [['2330.TW', null, 'finmind', '2026-06-30', '2025-12-31', [30, 20], [28], [25, 15, 10, 5, 4, 3, 2, 1, 1], [20], 0, 0, 'finmind', 4]],
    })!;
    const record = file.items.get('2330-TW')!;
    expect(record).toMatchObject({ originQ: { eps: 'finmind', rev: 'finmind' }, originY: 'finmind' });
    const pill = metricPill(row('2330.TW'), 'eps', file)!;
    expect(pill.title).toContain('來源：季度 FinMind（源自公開資訊觀測站）、年度 FinMind（源自公開資訊觀測站）');
  });

  it('shows the growth numbers of the active filters, with annual ones in the tooltip', () => {
    const tsmc = row('2330.TW');
    expect(metricPill(tsmc, 'eps', reportGrowth)).toMatchObject({ text: 'EPS +30%', tone: expect.stringMatching(/up|down/) });
    const eps = metricPill(tsmc, 'eps', reportGrowth)!;
    expect(eps.title).toContain('季度（最新→較舊）+30% / +20%');
    expect(eps.title).toContain('最新季 2026-06-30');
    expect(eps.title).toContain('來源：季度 公開資訊觀測站（官方財報）、年度 公開資訊觀測站（官方財報）');
    expect(eps.title).toContain('含以全年減前三季推算的第 4 季');
    expect(metricPill(row('2454.TW'), 'eps', reportGrowth)!.title).toContain('來源：季度 Yahoo 財報、年度 Yahoo 財報');
    expect(metricPill(row('2454.TW'), 'eps', reportGrowth)!.title).not.toContain('推算');
    expect(metricPill(tsmc, 'epsY', reportGrowth)).toMatchObject({ text: 'EPS年 +25%' });
    expect(metricPill(tsmc, 'epsY', reportGrowth)!.title).toContain('年度（最新→較舊）+25% / +15% / +10%');
    expect(metricPill(tsmc, 'revY', reportGrowth)?.text).toBe('營收年 +20%');
    expect(metricPill(row('1301.TW'), 'eps', reportGrowth)).toMatchObject({ text: 'EPS 轉盈', tone: 'up' });
    expect(metricPill(row('2317.TW'), 'eps', reportGrowth)).toBeNull();
    expect(metricPill(tsmc, 'eps')).toBeNull();
    expect(metricPill(row('AAPL'), 'eps', merged)!.title).toContain('Yahoo Finance');
  });

  it('adds pills for the new growth filters without crowding out the technical ones', () => {
    expect(rowMetricPills('default', state('epsy:2:10'), [20])).toEqual(['epsY']);
    expect(rowMetricPills('default', state('acc:1'), [20])).toEqual(['eps']);
    expect(rowMetricPills('default', state('rs:80;hi:10;vr:1.2;eps:2:10;epsy:2:10;rev:1:5;revy:1:5'), [20])).toEqual([
      'rs', 'hi', 'eps', 'epsY', 'rev', 'revY',
    ]);
    expect(rowMetricPills('default', state('rs1:0'), [20])).toEqual(['rs']);
  });

  it('sorts Taiwan stocks by the latest EPS YoY once the growth data is there', () => {
    const sorted = symbols(filterAndSortItems(TW, '', 'eps', { growth: reportGrowth }));
    expect(sorted[0]).toBe('1301.TW'); // the turnaround first
    const withValue = sorted.filter((symbol) => reportGrowth.items.get(growthKey(symbol))?.epsYoY[0] != null);
    const values = withValue.map((symbol) => reportGrowth.items.get(growthKey(symbol))!.epsYoY[0]!);
    expect(values).toEqual([...values].sort((a, b) => b - a));
    expect(sorted.indexOf('2317.TW')).toBeGreaterThan(sorted.indexOf('2330.TW'));
  });
});

describe('ten-year annual windows and the EPS growth streak (epss)', () => {
  const rec = (epsYoYAnnual: (number | null)[], epsTurnAnnual: boolean[] = []) => ({
    ...reportGrowth.items.get(growthKey('2330.TW'))!,
    epsYoYAnnual,
    epsTurnAnnual,
  });

  it('parses up to ten annual YoY values and their turnaround bits from growth.json', () => {
    const fields = ['symbol', 'lv', 'src', 'qEnd', 'yEnd', 'epsQ', 'revQ', 'epsY', 'revY', 'tQ', 'tY', 'srcY', 'dQ'];
    const ten = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const file = parseReportGrowth({
      version: 1,
      fields,
      rows: [['AAA', 'good', 'sec', '2026-06-30', '2025-12-31', [], [], [...ten, 11, 12], ten, 0, 1 << 9, 'sec', 0]],
    })!;
    const record = file.items.get('AAA')!;
    expect(record.epsYoYAnnual).toEqual(ten); // the eleventh value is beyond the horizon
    expect(record.revYoYAnnual).toEqual(ten);
    expect(record.epsTurnAnnual).toHaveLength(10);
    expect(record.epsTurnAnnual[9]).toBe(true);
    expect(record.epsTurnAnnual.filter(Boolean)).toHaveLength(1);
  });

  it('serializes the streak token canonically, next to the annual gates', () => {
    expect(serializeFilters(state('epss:3;epsy:2:10;tn:0'))).toBe('epsy:2:10;epss:3;tn:0');
    expect(serializeFilters({ ...EMPTY_FILTERS, turn: false, epss: 3 })).toBe('epss:3;tn:0');
    expect(serializeFilters({ ...EMPTY_FILTERS, keepMissing: true, epss: 10 })).toBe('epss:10;nd:keep');
    expect(serializeFilters(state('epss:10'))).toBe('epss:10');
  });

  it('counts the streak from the latest year and stops at the first year that does not grow', () => {
    expect(epsStreak(rec([30, 12, 5, -3, 40]), true)).toEqual({ years: 3, stop: 'decline' });
    expect(epsStreak(rec([30, null, 5]), true)).toEqual({ years: 1, stop: 'missing' });
    expect(epsStreak(rec([0, 5]), true)).toEqual({ years: 0, stop: 'decline' }); // flat is not growth
    expect(epsStreak(rec([10, 10, 10, 10, 10, 10, 10, 10, 10, 10]), true)).toEqual({ years: 10, stop: null });
    expect(epsStreak(rec([10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10]), true).years).toBe(10); // capped at the horizon
    // Only three stored years: the fourth is missing, not a decline
    expect(epsStreak(rec([25, 15, 10]), true)).toEqual({ years: 3, stop: 'missing' });
    expect(epsStreak(null, true)).toEqual({ years: 0, stop: 'missing' });
  });

  it('counts a loss-to-profit turn as growth only while 含虧轉盈 is on', () => {
    // The newest year turned from a loss; the two years before grew
    expect(epsStreak(rec([null, 20, 10], [true, false, false]), true)).toEqual({ years: 3, stop: 'missing' });
    expect(epsStreak(rec([null, 20, 10], [true, false, false]), false)).toEqual({ years: 0, stop: 'missing' });
    // A turn in the middle counts with the turn on; the fourth year is not in the file
    expect(epsStreak(rec([20, null, 10], [false, true, false]), true)).toEqual({ years: 3, stop: 'missing' });
    expect(epsStreak(rec([20, null, 10], [false, true, false]), false)).toEqual({ years: 1, stop: 'missing' });
  });

  it('passes when the streak reaches N; a missing stretch passes only with 資料不足時排除 off', () => {
    const grown = rec([30, 12, 5, -3]);
    expect(streakPasses(grown, 3, false, true)).toBe(true);
    expect(streakPasses(grown, 4, false, true)).toBe(false); // the decline in year 4 ends the streak at 3
    expect(streakPasses(grown, 4, true, true)).toBe(false); // a decline is never kept
    const short = rec([30, null, 5]);
    expect(streakPasses(short, 2, false, true)).toBe(false);
    expect(streakPasses(short, 2, true, true)).toBe(true); // missing data, kept
    expect(streakPasses(short, 1, false, true)).toBe(true);
    expect(streakPasses(rec([-5, null]), 2, true, true)).toBe(false); // the newest year declined
    expect(streakPasses(null, 1, false, true)).toBe(false);
    expect(streakPasses(null, 1, true, true)).toBe(true); // no record: missing
  });

  it('filters the Taiwan list on the streak, and keeps stocks whose history is too short only when asked', () => {
    // The fixture has three annual years for TSMC (+25 / +15 / +10): a streak of 3, not 4
    expect(symbols(applyFilters(TW, state('epss:3'), tw))).toContain('2330.TW');
    expect(symbols(applyFilters(TW, state('epss:4'), tw))).not.toContain('2330.TW');
    expect(symbols(applyFilters(TW, state('epss:4;nd:keep'), tw))).toContain('2330.TW');
    // Taiwan has no annual history beyond three years here: a ten-year streak only comes from keeping missing data
    expect(symbols(applyFilters(TW, state('epss:10'), tw))).toEqual([]);
    expect(symbols(applyFilters(TW, state('epss:10;nd:keep'), tw)).length).toBeGreaterThan(0);
    expect(gateCoverage(TW, reportGrowth, 'epsY', 5, true)).toBe(0);
  });

  it('is a chip, a removable condition and a row pill, and it needs the growth data', () => {
    const chips = activeChips(state('epss:3;tn:0'));
    expect(chips.map((chip) => chip.label)).toEqual(['EPS 連續成長 ≥3 年', '不含虧轉盈']);
    expect(removeFilter(state('epss:3'), 'epss').epss).toBeNull();
    expect(needsGrowth(state('epss:2'))).toBe(true);
    expect(filterAvailability(TW, tw).epss).toBeNull();
    expect(filterAvailability(TW, { market: 'TW', growth: null }).epss).toBe(NO_GROWTH_REASON);
    expect(rowMetricPills('default', state('epss:3'), [20])).toEqual(['epss']);
    expect(metricPill(row('2330.TW'), 'epss', reportGrowth)).toMatchObject({ text: 'EPS連增 3年', tone: 'up' });
    expect(metricPill(row('2330.TW'), 'epss', reportGrowth)!.title).toContain('年度 EPS 連續成長 3 年');
    expect(metricPill(row('2330.TW'), 'epss', reportGrowth)!.title).toContain('年度（最新→較舊）+25% / +15% / +10%');
    expect(metricPill(row('2330.TW'), 'epss')).toBeNull();
  });

  it('shows the streak in the pill with a plus at the stored horizon, and follows the 含虧轉盈 switch', () => {
    const file: GrowthFile = {
      generatedAt: null,
      source: null,
      items: new Map([
        ['TEN', rec([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])],
        ['TURN', rec([null, 20], [true, false])],
      ]),
    };
    expect(metricPill(plain('TEN'), 'epss', file, true)).toMatchObject({ text: 'EPS連增 10+年' });
    expect(metricPill(plain('TURN'), 'epss', file, true)).toMatchObject({ text: 'EPS連增 2年' });
    expect(metricPill(plain('TURN'), 'epss', file, false)).toBeNull();
    // A stock the streak fails still shows its (short) streak, so the list explains the exclusion
    expect(metricPill(plain('TEN'), 'epss', file)!.title).toContain('含虧轉盈');
  });
});
