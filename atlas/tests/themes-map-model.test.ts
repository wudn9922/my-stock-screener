import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseThemes, type Theme } from '../src/report/themes';
import { parseUniverse } from '../src/report/universe';
import {
  BALANCED_FLOOR,
  buildMapModel,
  formatTurnover,
  parseWeightMode,
  shortSymbol,
  ownTurnover,
  themesHaveTurnover,
  toGroupInputs,
  turnoverLookup,
} from '../src/pages/themesMapModel';
import { heat, heatWithCap } from '../src/pages/themesView';
import { fitLayout, median } from '../src/pages/treemap';

const parse = (path: string, market: 'us' | 'tw') => {
  const result = parseThemes(JSON.parse(readFileSync(path, 'utf8')), '', market);
  if (result.status !== 'ok') throw new Error('fixture must parse');
  return result.data;
};
const us = parse('src/report/fixtures/themes.sample.json', 'us');
const tw = parse('src/report/fixtures/themes-tw.sample.json', 'tw');

function theme(key: string, symbols: string[]): Theme {
  const base = us.themes[0]!;
  return {
    ...base,
    key,
    name: key,
    constituents: symbols.map((symbol) => ({ ...base.constituents[0]!, symbol, name: symbol })),
  };
}

describe('buildMapModel', () => {
  const themes = [theme('a', ['A1', 'A2', 'A3', 'A4']), theme('b', ['B1', 'B2']), theme('c', ['C1', 'C2'])];
  const lookup = new Map([
    ['A1', 10_000],
    ['A2', 400],
    ['A3', 100],
    ['A4', 4],
    ['B1', 900],
    // B2 unknown
  ]);

  it('equal mode: every stock weighs the same, nothing is imputed', () => {
    const model = buildMapModel(themes, lookup, 'equal');
    expect(model.mode).toBe('equal');
    expect(model.groups.flatMap((g) => g.leaves.map((l) => l.weight))).toEqual(Array(8).fill(1));
    expect(model.imputed).toBe(0);
  });

  it('balanced mode: sqrt of turnover relative to the theme median, floored at half the median weight', () => {
    const model = buildMapModel(themes, lookup, 'balanced');
    const a = model.groups[0]!.leaves.map((l) => l.weight);
    // theme A median turnover = 250 → sqrt(v / 250) = 6.32, 1.26, 0.63, 0.13; median weight 0.95 → floor 0.47
    expect(a[0]).toBeCloseTo(Math.sqrt(10_000 / 250));
    expect(a[1]).toBeCloseTo(Math.sqrt(400 / 250));
    expect(a[2]).toBeCloseTo(Math.sqrt(100 / 250));
    expect(a[3]).toBeCloseTo(BALANCED_FLOOR * median([a[0]!, a[1]!, a[2]!, Math.sqrt(4 / 250)]));
    expect(a[3]).toBeGreaterThan(Math.sqrt(4 / 250));
    // themes stay comparable: the median stock of every theme weighs about 1, whatever the money behind it
    expect(model.groups[1]!.leaves[0]!.weight).toBeCloseTo(1);
  });

  it('balanced mode: a missing turnover counts as the theme median; a theme with no data is laid out equally', () => {
    const model = buildMapModel(themes, lookup, 'balanced');
    const b2 = model.groups[1]!.leaves[1]!;
    expect(b2.imputed).toBe(true);
    expect(b2.weight).toBeCloseTo(1);
    expect(model.groups[2]!.leaves.map((l) => l.weight)).toEqual([1, 1]);
    expect(model.groups[2]!.leaves.every((l) => l.imputed)).toBe(true);
    expect(model.imputed).toBe(3);
  });

  it('turnover mode imputes with the theme median, and a theme with none with the global median', () => {
    const model = buildMapModel(themes, lookup, 'turnover');
    const b2 = model.groups[1]!.leaves[1]!;
    expect(b2.imputed).toBe(true);
    expect(b2.weight).toBeCloseTo(900); // theme B's only known value
    const c = model.groups[2]!.leaves;
    expect(c.every((l) => l.imputed)).toBe(true);
    expect(c[0]!.weight).toBeCloseTo(median([10_000, 400, 100, 4, 900]));
    expect(model.imputed).toBe(3);
  });

  it('turnover mode keeps proportions (apart from a negligible floor)', () => {
    const model = buildMapModel(themes, lookup, 'turnover');
    const a = model.groups[0]!.leaves.map((l) => l.weight);
    expect(a[0]! / a[1]!).toBeCloseTo(25);
  });

  it('falls back to equal sizes when no turnover data exists', () => {
    for (const none of [null, new Map<string, number>(), new Map([['ZZZ', 5]])]) {
      const model = buildMapModel(themes, none, 'balanced');
      expect(model.mode).toBe('equal');
      expect(model.groups.flatMap((g) => g.leaves.map((l) => l.weight))).toEqual(Array(8).fill(1));
      expect(model.imputed).toBe(0);
    }
  });

  it("prefers a constituent's own turnover20 over the universe lookup and works without a lookup", () => {
    const own = themes.map((t) => ({
      ...t,
      constituents: t.constituents.map((c) => ({ ...c, turnover20: c.symbol === 'B2' ? 50 : c.symbol === 'A1' ? 7 : null })),
    }));
    expect(themesHaveTurnover(themes)).toBe(false);
    expect(themesHaveTurnover(own)).toBe(true);
    const model = buildMapModel(own, lookup, 'turnover');
    const leaf = (key: string) => model.groups.flatMap((g) => g.leaves).find((l) => l.symbol === key)!;
    expect(leaf('A1').turnover).toBe(7); // own value beats the lookup's 10_000
    expect(leaf('B2').turnover).toBe(50); // fills a symbol the lookup lacks
    expect(leaf('A2').turnover).toBe(400); // lookup still fills the rest
    expect(buildMapModel(own, null, 'balanced').mode).toBe('balanced');
    expect(ownTurnover({ ...own[0]!.constituents[0]!, turnover20: -3 })).toBeNull();
  });

  it('is deterministic and handles an empty theme list', () => {
    expect(buildMapModel(themes, lookup, 'balanced')).toEqual(buildMapModel(themes, lookup, 'balanced'));
    const empty = buildMapModel([], lookup, 'balanced');
    expect(empty.groups).toEqual([]);
    expect(empty.mode).toBe('equal');
  });

  it('keeps a stock that sits in two themes as two distinct tiles', () => {
    const model = buildMapModel([theme('a', ['X']), theme('b', ['X'])], new Map([['X', 5]]), 'balanced');
    const ids = model.groups.flatMap((g) => g.leaves.map((l) => l.id));
    expect(new Set(ids).size).toBe(2);
  });
});

describe('real fixtures', () => {
  // Synthetic universe: turnover spans four orders of magnitude, so a raw layout would hide the small stocks.
  function universeFor(symbols: string[], market: 'TW' | 'US') {
    const rows = symbols.map((symbol, i) => {
      const row = Array(22).fill(null) as (string | number | null)[];
      row[0] = symbol;
      row[1] = symbol;
      row[2] = 10;
      row[21] = 1e6 * 10 ** ((i * 7) % 5);
      return row;
    });
    const result = parseUniverse({ markets: { [market]: { asOf: '2026-10-13', rows } } });
    if (result.status !== 'ok') throw new Error('universe must parse');
    return result.universe;
  }

  for (const [label, data, key] of [
    ['US', us, 'US'],
    ['TW', tw, 'TW'],
  ] as const) {
    it(`${label}: balanced mode meets the 22px minimum on a phone and a desktop width`, () => {
      const symbols = [...new Set(data.themes.flatMap((t) => t.constituents.map((c) => c.symbol)))];
      const lookup = turnoverLookup(universeFor(symbols, key), data.market);
      expect(lookup?.asOf).toBe('2026-10-13');
      const model = buildMapModel(data.themes, lookup!.values, 'balanced');
      expect(model.imputed).toBe(0);
      for (const width of [390, 1100]) {
        const fit = fitLayout(toGroupInputs(model), width, { baseHeight: width < 600 ? 520 : 600, maxHeight: 9000, minSide: 22 });
        expect(fit.satisfied).toBe(true);
        expect(fit.minLeafSide).toBeGreaterThanOrEqual(22);
        expect(fit.groups).toHaveLength(data.themes.length);
        const leaves = fit.groups.reduce((n, g) => n + g.leaves.length, 0);
        expect(leaves).toBe(model.leafCount);
      }
    });
  }
});

describe('turnoverLookup', () => {
  it('returns null for a missing market and skips non-positive values', () => {
    expect(turnoverLookup(null, 'tw')).toBeNull();
    const rows = [
      ['AAA', 'a', 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 'up', 0, 0, 0, 0, 0, 0, 0, 100],
      ['BBB', 'b', 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 'up', 0, 0, 0, 0, 0, 0, 0, 0],
    ];
    const parsed = parseUniverse({ markets: { US: { rows } } });
    if (parsed.status !== 'ok') throw new Error('must parse');
    expect(turnoverLookup(parsed.universe, 'tw')).toBeNull();
    expect([...turnoverLookup(parsed.universe, 'us')!.values.keys()]).toEqual(['AAA']);
  });
});

describe('helpers', () => {
  it('parses stored weight modes defensively', () => {
    expect(parseWeightMode('equal')).toBe('equal');
    expect(parseWeightMode('turnover')).toBe('turnover');
    expect(parseWeightMode('nope')).toBe('balanced');
    expect(parseWeightMode(null)).toBe('balanced');
  });
  it('shortens Taiwan tickers only', () => {
    expect(shortSymbol('2330.TW')).toBe('2330');
    expect(shortSymbol('6488.TWO')).toBe('6488');
    expect(shortSymbol('NVDA')).toBe('NVDA');
  });
  it('formats turnover in 億 with the right currency', () => {
    expect(formatTurnover(12_345_678_901, 'tw')).toBe('123 億元');
    expect(formatTurnover(456_000_000, 'us')).toBe('4.6 億美元');
    expect(formatTurnover(25_000_000, 'tw')).toBe('2,500 萬元');
    expect(formatTurnover(null, 'tw')).toBe('—');
  });
  it('heatWithCap saturates at the cap and matches heat()', () => {
    expect(heatWithCap(5, 5).mix).toBe(70);
    expect(heatWithCap(50, 5).mix).toBe(70);
    expect(heatWithCap(-2.5, 5)).toEqual({ side: 'down', mix: 42 });
    expect(heatWithCap(null, 5)).toEqual({ side: 'flat', mix: 0 });
    expect(heat(1.5, 'd1')).toEqual(heatWithCap(1.5, 3));
  });
});
