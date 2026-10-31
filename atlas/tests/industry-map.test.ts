import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseThemes } from '../src/report/themes';
import { parseUniverse, type UniverseLoadResult, type UniverseMarket } from '../src/report/universe';
import {
  CAP_COVERAGE,
  MIN_INDUSTRY_STOCKS,
  buildIndustryModel,
  clampIndustryPeriod,
  colorValue,
  hasUsableProfile,
  industryGroups,
  resolveMapSource,
  sizeBasis,
  sizeOf,
  summarize,
  type IndustryStock,
  sectorLabel,
} from '../src/pages/industryModel';
import {
  BALANCED_FLOOR,
  buildMapModel,
  formatMoney,
  parseAggMode,
  parseWeightMode,
  themeGroups,
  themeTurnover,
  toGroupInputs,
} from '../src/pages/themesMapModel';
import { fitLayout } from '../src/pages/treemap';
import { universeBody } from './fixtures/industry/universeGen';

function parseOk(body: unknown): UniverseLoadResult & { status: 'ok' } {
  const result = parseUniverse(body);
  if (result.status !== 'ok') throw new Error(`universe must parse: ${result.message}`);
  return result;
}
const market = (body: unknown, key: 'TW' | 'US'): UniverseMarket => parseOk(body).universe.markets[key]!;

/** Hand-written rows: [symbol, sector, industry, marketCap, changePct, r5, r21, r63, turnover20]. */
function smallUniverse(key: 'TW' | 'US', list: [string, string | null, string | null, number | null, number | null, number | null, number | null, number | null, number | null][]) {
  const fields = ['symbol', 'name', 'changePct', 'r5', 'r21', 'r63', 'turnover20', 'sector', 'industry', 'marketCap'];
  return {
    fields,
    markets: {
      [key]: {
        asOf: '2026-10-13',
        universe: 'test',
        scope: 'full',
        rows: list.map(([symbol, sector, industry, cap, d1, w1, m1, m3, turnover]) => [symbol, symbol, d1, w1, m1, m3, turnover, sector, industry, cap]),
      },
    },
  };
}

const stock = (symbol: string, marketCap: number | null, d1: number | null, turnover: number | null = null): IndustryStock => ({
  symbol,
  name: symbol,
  marketCap,
  turnover,
  returns: { d1, w1: d1, m1: d1, m3: d1 },
});

describe('summarize', () => {
  it('weights by market cap, takes the equal-weight median, counts risers', () => {
    const stocks = [stock('A', 900, 10), stock('B', 90, -2), stock('C', 10, 4)];
    const s = summarize(stocks, 'd1', 'cap');
    expect(s.weighted).toBeCloseTo((900 * 10 + 90 * -2 + 10 * 4) / 1000);
    expect(s.median).toBe(4);
    expect(s.upShare).toBeCloseTo(2 / 3);
    expect(s.validCount).toBe(3);
    expect(s.marketCap).toBe(1000);
    expect(s.capCount).toBe(3);
  });

  it('ignores stocks without a return and weights only over those with a cap', () => {
    const s = summarize([stock('A', 100, 5), stock('B', null, 1), stock('C', 100, null)], 'd1', 'cap');
    expect(s.validCount).toBe(2);
    expect(s.weighted).toBe(5); // B has no weight, C has no return
    expect(s.median).toBe(3);
    expect(s.capCount).toBe(2);
    expect(s.count).toBe(3);
  });

  it('weights by traded value when that is the basis, and has no weighted number without a basis', () => {
    const stocks = [stock('A', null, 10, 300), stock('B', null, 0, 100)];
    expect(summarize(stocks, 'd1', 'turnover').weighted).toBeCloseTo(7.5);
    expect(summarize(stocks, 'd1', 'none').weighted).toBeNull();
    expect(summarize(stocks, 'd1', 'none').median).toBe(5);
  });

  it('handles an empty industry and one without any return', () => {
    const empty = summarize([], 'm1', 'cap');
    expect(empty).toMatchObject({ count: 0, validCount: 0, weighted: null, median: null, upShare: null, marketCap: null, best: [], worst: [] });
    const blank = summarize([stock('A', 5, null)], 'm1', 'cap');
    expect(blank.median).toBeNull();
    expect(blank.upShare).toBeNull();
    expect(blank.marketCap).toBe(5);
  });

  it('lists the best and worst stocks without overlap, even for tiny industries', () => {
    const ten = Array.from({ length: 10 }, (_, i) => stock(`S${i}`, 10, i - 5));
    const s = summarize(ten, 'd1', 'cap');
    expect(s.best.map((p) => p.symbol)).toEqual(['S9', 'S8', 'S7']);
    expect(s.worst.map((p) => p.symbol)).toEqual(['S0', 'S1', 'S2']);
    const two = summarize([stock('A', 1, 3), stock('B', 1, -3)], 'd1', 'cap');
    expect(two.best.map((p) => p.symbol)).toEqual(['A']);
    expect(two.worst.map((p) => p.symbol)).toEqual(['B']);
    const one = summarize([stock('A', 1, 3)], 'd1', 'cap');
    expect(one.best).toHaveLength(1);
    expect(one.worst).toHaveLength(0);
  });
});

describe('colorValue', () => {
  it('uses the weighted number, or the median when asked or when there are no weights', () => {
    const s = summarize([stock('A', 100, 10), stock('B', 100, 0), stock('C', 100, 2)], 'd1', 'cap');
    expect(colorValue(s, 'weighted')).toEqual({ value: 4, fellBack: false });
    expect(colorValue(s, 'median')).toEqual({ value: 2, fellBack: false });
    const unweighted = summarize([stock('A', null, 10), stock('B', null, 2)], 'd1', 'cap');
    expect(colorValue(unweighted, 'weighted')).toEqual({ value: 6, fellBack: true });
    expect(colorValue(summarize([], 'd1', 'cap'), 'weighted')).toEqual({ value: null, fellBack: false });
  });
});

describe('sizeBasis / sizeOf', () => {
  it('prefers market cap when it covers enough stocks, then traded value, then nothing', () => {
    expect(sizeBasis(100, 100, 100)).toBe('cap');
    expect(sizeBasis(100, CAP_COVERAGE * 100, 0)).toBe('cap');
    expect(sizeBasis(100, CAP_COVERAGE * 100 - 1, 90)).toBe('turnover');
    expect(sizeBasis(100, 0, 0)).toBe('none');
    expect(sizeBasis(0, 0, 0)).toBe('none');
  });
  it('sums the chosen measure and returns null when nothing is known', () => {
    const stocks = [stock('A', 5, 0, 7), stock('B', null, 0, 3)];
    expect(sizeOf(stocks, 'cap')).toBe(5);
    expect(sizeOf(stocks, 'turnover')).toBe(10);
    expect(sizeOf(stocks, 'none')).toBeNull();
    expect(sizeOf([stock('A', null, 0)], 'cap')).toBeNull();
  });
});

describe('buildIndustryModel', () => {
  it('Taiwan: one flat group of 產業別, small industries and unclassified stocks fold into 其他', () => {
    let serial = 0;
    const big = (industry: string, n: number) => Array.from({ length: n }, (): [string, null, string, number, number, number, number, number, number] => [`T${serial++}`, null, industry, 100, 1, 1, 1, 1, 10]);
    const body = smallUniverse('TW', [
      ...big('半導體業', 5),
      ...big('航運業', 3),
      ...big('水泥工業', 2),
      ...big('玻璃陶瓷', 1),
      ['X1', null, null, 50, 1, 1, 1, 1, 10],
    ]);
    const model = buildIndustryModel(market(body, 'TW'))!;
    expect(model.twoLevel).toBe(false);
    expect(model.groups).toHaveLength(1);
    const labels = model.groups[0]!.cells.map((c) => c.label);
    expect(labels).toEqual(['半導體業', '航運業', '其他']);
    const other = model.groups[0]!.cells.find((c) => c.merged)!;
    expect(other.stocks).toHaveLength(2 + 1 + 1);
    expect(other.mergedFrom).toEqual(['水泥工業', '玻璃陶瓷']);
    expect(model.coverage).toMatchObject({ total: 12, classified: 11, withCap: 12 });
    expect(MIN_INDUSTRY_STOCKS).toBe(3);
  });

  it('Taiwan falls back to the sector column when a row has no industry', () => {
    const body = smallUniverse('TW', Array.from({ length: 4 }, (_, i): [string, string, null, number, number, number, number, number, number] => [`T${i}`, '半導體業', null, 1, 1, 1, 1, 1, 1]));
    const model = buildIndustryModel(market(body, 'TW'))!;
    expect(model.groups[0]!.cells.map((c) => c.label)).toEqual(['半導體業']);
  });

  it('US: sector outside, industry inside, small industries fold into 其他 of the same sector only', () => {
    const rows: Parameters<typeof smallUniverse>[1] = [];
    const add = (sector: string | null, industry: string | null, n: number) => {
      for (let i = 0; i < n; i++) rows.push([`${sector}${industry}${i}`.replace(/\W/g, ''), sector, industry, 10, 1, 1, 1, 1, 1]);
    };
    add('Technology', 'Semiconductors', 4);
    add('Technology', 'Software', 3);
    add('Technology', 'Hardware', 1);
    add('Energy', 'Oil', 3);
    add('Energy', 'Coal', 2);
    add('Utilities', 'Gas', 1);
    add('Utilities', null, 1);
    add(null, null, 2);
    const model = buildIndustryModel(market(smallUniverse('US', rows), 'US'))!;
    expect(model.twoLevel).toBe(true);
    const byGroup = Object.fromEntries(model.groups.map((g) => [g.label, g.cells.map((c) => c.label)]));
    expect(byGroup).toEqual({
      '科技 Technology': ['Semiconductors', 'Software', '其他'],
      '能源 Energy': ['Oil', '其他'],
      '公用事業 Utilities': ['其他'],
      未分類: ['其他'],
    });
    const tech = model.groups.find((g) => g.label === '科技 Technology')!;
    expect(tech.cells.find((c) => c.merged)!.mergedFrom).toEqual(['Hardware']);
    expect(tech.stocks).toHaveLength(8);
    expect(model.cellCount).toBe(7);
  });

  it('allows compact Chinese heatmap labels and preserves other research source labels', () => {
    expect(sectorLabel('Health Care')).toBe('醫療保健 Health Care');
    expect(sectorLabel('Health Care', true)).toBe('醫療保健');
    expect(sectorLabel('Something New')).toBe('Something New');
  });

  it('returns null for an empty market and keeps every stock exactly once', () => {
    expect(buildIndustryModel({ market: 'TW', benchmark: '^TWII', asOf: null, universe: 'x', scope: 'full', note: null, items: [] })).toBeNull();
    const m = buildIndustryModel(market(universeBody({ twCount: 300, twIndustries: 12, usCount: 600, usIndustries: 22 }), 'US'))!;
    const symbols = m.groups.flatMap((g) => g.cells.flatMap((c) => c.stocks.map((s) => s.symbol)));
    expect(symbols).toHaveLength(600);
    expect(new Set(symbols).size).toBe(600);
    expect(m.groups.every((g) => g.cells.length >= 1)).toBe(true);
  });

  it('uses traded value when market cap is mostly missing, and reports the coverage', () => {
    const m = buildIndustryModel(market(universeBody({ twCount: 300, twIndustries: 12, capMissing: 0.8 }), 'TW'))!;
    expect(m.basis).toBe('turnover');
    expect(m.coverage.withCap).toBeLessThan(m.coverage.total * CAP_COVERAGE);
    const cellSizes = industryGroups(m)[0]!.cells.map((c) => c.size);
    expect(cellSizes.every((v) => v !== null && v > 0)).toBe(true);
  });
});

describe('resolveMapSource (the fallback decision)', () => {
  const themes = parseThemes(JSON.parse(readFileSync('src/report/fixtures/themes-tw.sample.json', 'utf8')), '', 'tw');
  if (themes.status !== 'ok') throw new Error('fixture must parse');

  it('waits while the universe is loading', () => {
    expect(resolveMapSource(null, 'tw')).toEqual({ kind: 'loading' });
  });

  it('uses the industry map when the universe carries profile data for the market', () => {
    const source = resolveMapSource(parseOk(universeBody({ twCount: 300, twIndustries: 12, usCount: 600, usIndustries: 22 })), 'tw');
    expect(source.kind).toBe('industry');
    expect(resolveMapSource(parseOk(universeBody({ twCount: 300, twIndustries: 12, usCount: 600, usIndustries: 22 })), 'us').kind).toBe('industry');
  });

  it('falls back to themes when today\'s universe has no profile columns, keeping its turnover for sizing', () => {
    const source = resolveMapSource(parseOk(universeBody({ withoutProfile: true, twCount: 300, twIndustries: 12 })), 'tw');
    expect(source.kind).toBe('themes');
    if (source.kind !== 'themes') return;
    expect(source.reason).toBe('no-profile');
    expect(source.lookup?.size).toBeGreaterThan(0);
  });

  it('falls back when too few stocks are classified, or the market is missing from the file', () => {
    const sparse = smallUniverse('TW', Array.from({ length: 40 }, (_, i): [string, null, string | null, number, number, number, number, number, number] => [`T${i}`, null, i < 5 ? '半導體業' : null, 1, 1, 1, 1, 1, 1]));
    expect(resolveMapSource(parseOk(sparse), 'tw').kind).toBe('themes');
    expect(resolveMapSource(parseOk(sparse), 'us')).toMatchObject({ kind: 'themes', reason: 'no-profile', lookup: null });
  });

  it('falls back when the universe failed to load', () => {
    for (const status of ['missing', 'invalid', 'error'] as const) {
      expect(resolveMapSource({ status, message: 'x', url: '' }, 'tw')).toMatchObject({ kind: 'themes', reason: 'failed', lookup: null });
    }
  });

  it('hasUsableProfile rejects null and thin models', () => {
    expect(hasUsableProfile(null)).toBe(false);
    const thin = buildIndustryModel(market(smallUniverse('TW', [['A', null, '半導體業', 1, 1, 1, 1, 1, 1]]), 'TW'));
    expect(hasUsableProfile(thin)).toBe(false);
  });
});

describe('buildMapModel (cell weights)', () => {
  const groups = [
    { id: 'g', label: 'g', cells: [{ id: 'a', label: 'a', size: 10_000 }, { id: 'b', label: 'b', size: 400 }, { id: 'c', label: 'c', size: 100 }, { id: 'd', label: 'd', size: 1 }] },
    { id: 'h', label: 'h', cells: [{ id: 'e', label: 'e', size: null }] },
  ];
  const weightsOf = (mode: Parameters<typeof buildMapModel>[1]) => buildMapModel(groups, mode).groups.flatMap((g) => g.cells.map((c) => c.weight));

  it('equal mode: every cell weighs the same and nothing is imputed', () => {
    const model = buildMapModel(groups, 'equal');
    expect(weightsOf('equal')).toEqual([1, 1, 1, 1, 1]);
    expect(model.imputed).toBe(0);
  });

  it('balanced mode: square-root compression with a floor at half the median weight', () => {
    // known sizes 10000/400/100/1 → median 250
    const w = weightsOf('balanced');
    expect(w[0]).toBeCloseTo(Math.sqrt(10_000 / 250));
    expect(w[1]).toBeCloseTo(Math.sqrt(400 / 250));
    expect(w[4]).toBeCloseTo(1); // unknown → the median
    const floor = BALANCED_FLOOR * [...w].sort((x, y) => x - y)[2]!;
    expect(w[3]).toBeGreaterThanOrEqual(floor - 1e-9);
    expect(buildMapModel(groups, 'balanced').imputed).toBe(1);
  });

  it('size mode: proportional to the raw size', () => {
    const w = weightsOf('size');
    expect(w[0]! / w[1]!).toBeCloseTo(10_000 / 400);
    expect(w[3]).toBeGreaterThan(0);
  });

  it('falls back to equal when there is no size at all, and survives no groups', () => {
    const none = buildMapModel([{ id: 'g', label: 'g', cells: [{ id: 'a', label: 'a', size: null }] }], 'balanced');
    expect(none.mode).toBe('equal');
    expect(buildMapModel([], 'balanced')).toMatchObject({ groups: [], mode: 'equal', cellCount: 0 });
  });

  it('is deterministic and feeds the treemap one leaf per cell', () => {
    expect(buildMapModel(groups, 'balanced')).toEqual(buildMapModel(groups, 'balanced'));
    expect(toGroupInputs(buildMapModel(groups, 'balanced'))).toHaveLength(2);
  });
});

describe('theme fallback cells', () => {
  const themes = parseThemes(JSON.parse(readFileSync('src/report/fixtures/themes-tw.sample.json', 'utf8')), '', 'tw');
  if (themes.status !== 'ok') throw new Error('fixture must parse');
  const data = themes.data;

  it('one cell per theme, grouped by parent in the file order, no stock cells', () => {
    const groups = themeGroups(data, data.themes, null);
    expect(groups.flatMap((g) => g.cells).map((c) => c.id).sort()).toEqual(data.themes.map((t) => t.key).sort());
    expect(groups.map((g) => g.id)).toEqual(data.parents.filter((p) => data.themes.some((t) => t.parent === p.key)).map((p) => p.key));
    expect(groups.flatMap((g) => g.cells).every((c) => c.size === null)).toBe(true);
  });

  it('sizes a theme by its members\' turnover: the themes file first, the universe list for the gaps', () => {
    const theme = data.themes[0]!;
    const [first, second] = theme.constituents;
    const withOwn = { ...theme, constituents: theme.constituents.map((c) => (c.symbol === first!.symbol ? { ...c, turnover20: 7 } : c)) };
    const lookup = new Map([[first!.symbol, 1000], [second!.symbol, 30]]);
    expect(themeTurnover(withOwn, lookup)).toEqual({ total: 37, known: 2 });
    expect(themeTurnover(theme, null)).toEqual({ total: null, known: 0 });
    const sized = themeGroups(data, [withOwn], lookup);
    expect(sized[0]!.cells[0]!.size).toBe(37);
  });

  it('puts themes whose parent is unknown into a 其他 group', () => {
    const groups = themeGroups(data, [{ ...data.themes[0]!, parent: 'gone' }], null);
    expect(groups.map((g) => g.label)).toEqual(['其他']);
  });
});

describe('helpers', () => {
  it('clamps the periods the universe does not have to 3 months', () => {
    expect(clampIndustryPeriod('d1')).toBe('d1');
    expect(clampIndustryPeriod('m3')).toBe('m3');
    expect(clampIndustryPeriod('m6')).toBe('m3');
    expect(clampIndustryPeriod('ytd')).toBe('m3');
  });
  it('parses stored modes defensively and migrates the old 成交值 value', () => {
    expect(parseWeightMode('turnover')).toBe('size');
    expect(parseWeightMode('size')).toBe('size');
    expect(parseWeightMode('equal')).toBe('equal');
    expect(parseWeightMode('nope')).toBe('balanced');
    expect(parseWeightMode(null)).toBe('balanced');
    expect(parseAggMode('median')).toBe('median');
    expect(parseAggMode('x')).toBe('weighted');
  });
  it('formats money with 兆 / 億 / 萬 and the market currency', () => {
    expect(formatMoney(4.2e13, 'TW')).toBe('42.00 兆元');
    expect(formatMoney(12_345_678_901, 'TW')).toBe('123 億元');
    expect(formatMoney(456_000_000, 'US')).toBe('4.6 億美元');
    expect(formatMoney(5_000_000, 'US')).toBe('500 萬美元');
    expect(formatMoney(null, 'US')).toBe('—');
  });
});

describe('large synthetic universe (profile columns)', () => {
  const body = universeBody(); // TW ≈ 1800 stocks / 30 industries, US ≈ 5000 / 11 sectors / 120 industries
  const tw = buildIndustryModel(market(body, 'TW'))!;
  const us = buildIndustryModel(market(body, 'US'))!;

  it('has the advertised shape', () => {
    expect(tw.coverage.total).toBe(1800);
    expect(tw.cellCount).toBe(30);
    expect(us.coverage.total).toBe(5000);
    expect(us.groups).toHaveLength(11);
    expect(us.cellCount).toBe(120);
    expect(tw.basis).toBe('cap');
    expect(hasUsableProfile(tw) && hasUsableProfile(us)).toBe(true);
  });

  for (const [label, model, flat] of [['TW', tw, true], ['US', us, false]] as const) {
    for (const width of [390, 768, 1280]) {
      it(`${label}: balanced and equal layouts reach the 22px minimum at ${width}px and stay fast`, () => {
        for (const mode of ['balanced', 'equal'] as const) {
          const started = performance.now();
          const mapModel = buildMapModel(industryGroups(model), mode);
          const layout = fitLayout(toGroupInputs(mapModel), width, {
            baseHeight: width < 600 ? 520 : 600,
            maxHeight: 9000,
            minSide: 22,
            layout: flat ? { headerHeight: 0, pad: 0, groupFloor: 0.5 } : undefined,
          });
          const elapsed = performance.now() - started;
          expect(layout.satisfied).toBe(true);
          expect(layout.minLeafSide).toBeGreaterThanOrEqual(22);
          expect(layout.groups.reduce((n, g) => n + g.leaves.length, 0)).toBe(model.cellCount);
          expect(elapsed).toBeLessThan(1500);
          // The page must stay a sane length on a phone.
          if (width === 390) expect(layout.height).toBeLessThan(6000);
        }
      });
    }
  }

  it('aggregates 5000 stocks for every period quickly', () => {
    const started = performance.now();
    for (const period of ['d1', 'w1', 'm1', 'm3'] as const) for (const group of us.groups) for (const cell of group.cells) summarize(cell.stocks, period, us.basis);
    expect(performance.now() - started).toBeLessThan(1500);
  });
});
