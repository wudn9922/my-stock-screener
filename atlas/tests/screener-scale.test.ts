import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildLargeUniverse } from '../e2e-site/largeUniverse';
import { activeChips, applyFilters, filterAvailability, parseFilters } from '../src/pages/screenerFilters';
import { filterAndSortItems } from '../src/pages/screenerItems';
import { parseUniverse, universeGroup } from '../src/report/universe';
import { mergeGrowth, parseReportGrowth } from '../src/fundamentals/GrowthProvider';

/**
 * The full-market lists (scripts/build_universe_full.py) hold about 1,850 Taiwan and 6,000 US rows. Parsing,
 * filtering and sorting must stay well inside one frame budget on a desktop CPU; the bounds below are generous
 * (CI machines vary) and the measured numbers are printed for the record.
 */
const time = <T,>(work: () => T): [T, number] => {
  const start = performance.now();
  const result = work();
  return [result, performance.now() - start];
};

describe('full-market universe at real size', () => {
  const document = buildLargeUniverse();
  const text = JSON.stringify(document);

  it('parses ~7,850 rows quickly and keeps every row and profile', () => {
    const [result, parseMs] = time(() => parseUniverse(JSON.parse(text)));
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const { TW, US } = result.universe.markets;
    expect(TW!.items).toHaveLength(1850);
    expect(US!.items).toHaveLength(6000);
    expect(TW!.scope).toBe('full');
    expect(TW!.note).toContain('不含 ETF');
    expect(US!.items[0]!.profile).toMatchObject({ sector: 'Technology' });
    expect(TW!.items[0]!.profile).toMatchObject({ sector: null, industry: '半導體業' });
    console.info(`universe.json ${(text.length / 1024).toFixed(0)} KB (synthetic), JSON.parse + parseUniverse ${parseMs.toFixed(1)} ms`);
    expect(parseMs).toBeLessThan(2000);
  });

  it('filters, sorts and searches 6,000 rows within a frame or two', () => {
    const result = parseUniverse(JSON.parse(text));
    if (result.status !== 'ok') throw new Error('synthetic universe must parse');
    const items = universeGroup(result.universe.markets.US!).items;
    const context = { market: 'US' as const, growth: undefined, growthLoading: false };
    const filters = parseFilters('rs:70;hi:-15;tr:a50,a200;vr:1.2');
    const [availability, availabilityMs] = time(() => filterAvailability(items, context));
    expect(activeChips(filters, availability).every((chip) => !chip.unavailable)).toBe(true);
    const [filtered, filterMs] = time(() => applyFilters(items, filters, context));
    expect(filtered.length).toBeGreaterThan(0);
    expect(filtered.length).toBeLessThan(items.length);
    const [sorted, sortMs] = time(() => filterAndSortItems(items, '', 'rs'));
    expect(sorted[0]!.metrics!.rsRank).toBe(99);
    const [found, searchMs] = time(() => filterAndSortItems(items, 'company 59', 'symbol'));
    expect(found.length).toBeGreaterThan(0);
    // The filter sheet recounts on every change: a full pass must stay cheap.
    const [, recountMs] = time(() => {
      for (let i = 0; i < 10; i++) filterAndSortItems(applyFilters(items, filters, context), '', 'default');
    });
    console.info(
      `6,000 US rows: availability ${availabilityMs.toFixed(1)} ms, filter ${filterMs.toFixed(1)} ms, ` +
        `sort by RS ${sortMs.toFixed(1)} ms, search ${searchMs.toFixed(1)} ms, 10 recounts ${recountMs.toFixed(1)} ms`,
    );
    expect(filterMs + sortMs + searchMs).toBeLessThan(500);
    expect(recountMs / 10).toBeLessThan(100);
  });
});

describe('full-market growth.json at real size', () => {
  /** One growth.json row per universe row (build_growth.py with the official sources: ~7,300 real rows). */
  const universe = parseUniverse(JSON.parse(JSON.stringify(buildLargeUniverse())));
  if (universe.status !== 'ok') throw new Error('synthetic universe must parse');
  let seed = 11;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const pct = () => Math.round((rand() * 380 - 80) * 10) / 10;
  const symbols = [...universe.universe.markets.TW!.items, ...universe.universe.markets.US!.items].map((item) => item.symbol);
  const text = JSON.stringify({
    version: 1,
    generatedAt: '2026-10-15T00:00:00Z',
    source: 'financials+official',
    fields: ['symbol', 'lv', 'src', 'qEnd', 'yEnd', 'epsQ', 'revQ', 'epsY', 'revY', 'tQ', 'tY', 'srcY', 'dQ'],
    rows: symbols.map((symbol) => {
      const tw = symbol.endsWith('.TW') || symbol.endsWith('.TWO');
      return [symbol, null, tw ? 'mops' : 'frames', '2026-06-30', '2025-12-31', [pct(), pct(), pct(), pct()], [pct(), pct(), pct(), pct()],
        [pct(), pct(), pct()], [pct(), pct(), pct()], rand() < 0.05 ? 1 : 0, 0, tw ? 'mops' : 'frames', rand() < 0.3 ? 1 : 0];
    }),
  });

  it('parses, merges and filters ~7,850 rows within the frame budget', () => {
    const [growth, parseMs] = time(() => parseReportGrowth(JSON.parse(text))!);
    expect(growth.items.size).toBe(symbols.length);
    const [merged, mergeMs] = time(() => mergeGrowth(null, growth)!);
    const items = universeGroup(universe.universe.markets.US!).items;
    const context = { market: 'US' as const, growth: merged, growthLoading: false };
    const filters = parseFilters('eps:4:20;epsy:3:10;rev:2:10;acc:1');
    const [filtered, filterMs] = time(() => applyFilters(items, filters, context));
    expect(filtered.length).toBeLessThan(items.length);
    const [, recountMs] = time(() => {
      for (let i = 0; i < 10; i++) filterAndSortItems(applyFilters(items, filters, context), '', 'eps', { growth: merged });
    });
    console.info(
      `growth.json ${(text.length / 1024).toFixed(0)} KB (synthetic, every slot filled): parse ${parseMs.toFixed(1)} ms, ` +
        `merge ${mergeMs.toFixed(1)} ms, 4 growth gates on 6,000 US rows ${filterMs.toFixed(1)} ms, 10 recounts + EPS sort ${recountMs.toFixed(1)} ms`,
    );
    expect(parseMs).toBeLessThan(1500);
    expect(filterMs).toBeLessThan(500);
    expect(recountMs / 10).toBeLessThan(150);
  });
});

describe('universe.json as the Python side writes it', () => {
  it('reads a mixed file: a full Taiwan list next to main.py\'s US liquidity list', () => {
    // tests/fixtures/screener/universe-mixed.json: report_metrics.keep_fresh_full_markets output (profile
    // columns for every market, the liquidity rows padded with nulls).
    const result = parseUniverse(JSON.parse(readFileSync('tests/fixtures/screener/universe-mixed.json', 'utf8')));
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const { TW, US } = result.universe.markets;
    expect(TW!.scope).toBe('full');
    expect(TW!.universe).toMatch(/^台股上市＋上櫃普通股（上市 [\d,]+、上櫃 [\d,]+ 檔）$/);
    expect(TW!.note).toContain('市值＝已發行普通股數×收盤價');
    expect(TW!.items[0]).toMatchObject({ symbol: '1101.TW', name: '台泥', profile: { sector: null, industry: '水泥工業' } });
    expect(TW!.items[0]!.profile!.marketCap).toBeGreaterThan(0);
    expect(TW!.items[5]!.profile).toEqual({ sector: null, industry: '食品工業', marketCap: null });
    expect(US!.scope).toBe('liquid');
    expect(US!.universe).toBe('S&P 500 成分股');
    expect(US!.note).toBeNull();
    expect(US!.items.length).toBeGreaterThan(0);
    expect(US!.items.every((item) => item.profile === undefined)).toBe(true);
  });
});
