import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildLargeUniverse } from '../e2e-site/largeUniverse';
import { activeChips, applyFilters, filterAvailability, parseFilters } from '../src/pages/screenerFilters';
import { filterAndSortItems } from '../src/pages/screenerItems';
import { parseUniverse, universeGroup } from '../src/report/universe';

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
