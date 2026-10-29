import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { groupItemSchema } from '../src/report/schema';
import { loadScreenerFixedMAs } from '../src/pages/screenerFixedMAs';
import { applyFilters, parseFilters, screenerMa } from '../src/pages/screenerFilters';
import { createWatchlist, evaluateWatchlist } from '../src/watchlists/model';
import { fixedMaBarsFromCsv, readCollectorFixedMaBars } from '../scripts/refresh-watchlists.ts';

const stock = (ticker: string, group_id = 'ungrouped') => ({ id: 1, ticker, group_id, ma1: 50, ma2: 3, ma3: 20, ma4: 0 });
const row = (symbol: string, extra = {}) => groupItemSchema.parse({ symbol, close: 110, asOf: '2026-10-20', metrics: { ema20: -15 }, ...extra });

beforeEach(() => vi.stubEnv('UNIVERSE_CACHE_DIR', ''));
afterEach(() => vi.unstubAllEnvs());

describe('screener fixed moving-average references', () => {
  it('uses the smallest saved stock parameter from fixed or grouped rows and needs no unrelated histories', async () => {
    const fixed = row('FIXED', { maValues: { 3: 100, 50: 90 } });
    const grouped = row('GROUPED', { maList: [3, 20], maValues: { 3: 100 } });
    const plain = row('PLAIN');
    const loadBars = vi.fn();
    const references = await loadScreenerFixedMAs([fixed, grouped, plain], [stock('FIXED'), stock('GROUPED', 'us_g1')], loadBars);
    expect(loadBars).not.toHaveBeenCalled();
    expect(screenerMa(fixed, references).label).toBe('MA3');
    expect(screenerMa(fixed, references).distance).toBeCloseTo(10);
    expect(screenerMa(grouped, references).label).toBe('MA3');
    expect(screenerMa(grouped, references).distance).toBeCloseTo(10);
    expect(screenerMa(plain, references)).toEqual({ label: 'EMA20', distance: -15 });
    expect(applyFilters([fixed, grouped, plain], parseFilters('ema20:0:20'), { market: 'US', fixedMAs: references })).toEqual([fixed, grouped]);
    const list = createWatchlist({ name: '固定參數', market: 'US', group: 'universe-us', filters: 'ema20:0:20' });
    expect(() => evaluateWatchlist(list, [fixed, grouped], undefined)).toThrow('固定均線參數尚未取得');
    expect(evaluateWatchlist(list, [fixed, grouped], undefined, references).map(item => item.symbol)).toEqual(['FIXED', 'GROUPED']);
  });

  it('aligns history to the report date, excludes newer prices and never substitutes EMA20 for a missing fixed MA', async () => {
    const history = ['2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21'].map((date, index) => ({ time: Date.parse(`${date}T20:00:00Z`) / 1000, close: index === 3 ? 1000 : 100 }));
    const items = [row('OK'), row('MISSING', { metrics: { ema20: 5 } }), row('PLAIN', { metrics: { ema20: 5 } })];
    const references = await loadScreenerFixedMAs(items, [stock('OK'), stock('MISSING')], async symbol => symbol === 'OK' ? history : history.slice(0, 2));
    expect(references.OK.distance).toBeCloseTo(10);
    expect(references.MISSING.distance).toBeNull();
    expect(applyFilters(items, parseFilters('ema20:0:20'), { market: 'US', fixedMAs: references }).map(item => item.symbol)).toEqual(['OK', 'PLAIN']);
    const list = createWatchlist({ name: '保留基準', market: 'US', group: 'universe-us', filters: 'ema20:0:20' });
    const recovered = { ...references, MISSING: { period: 3, distance: 10 } };
    expect(evaluateWatchlist(list, items, undefined, recovered).map(item => item.symbol)).toEqual(['OK', 'MISSING', 'PLAIN']);
    expect(() => evaluateWatchlist(list, items, undefined, references)).toThrow('暫不判定出榜');
  });

  it('parses collector CSV session dates consistently for Taiwan and US fixed references', async () => {
    const bars = fixedMaBarsFromCsv('Date,Open,Close,Volume\n2026-10-16,1,100,0\n2026-10-19,1,100,0\n2026-10-20,1,100,0\n2026-10-21,1,1000,0');
    for (const symbol of ['2330.TW', 'AAPL']) {
      const result = await loadScreenerFixedMAs([row(symbol)], [stock(symbol)], async () => bars);
      expect(result[symbol].distance).toBeCloseTo(10);
    }
  });

  it.each(['OUTSIDE', '6488.TWO'])('evaluates %s from the full-market cache when its data CSV is absent, aligned to the report date', async symbol => {
    const root = await mkdtemp(join(tmpdir(), 'atlas-fixed-cache-'));
    try {
      const cacheDir = join(root, '.cache/universe-full/bars');
      await mkdir(cacheDir, { recursive: true });
      // Same serialized columns as universe_cache.write_bars; the newer session must be excluded.
      await writeFile(join(cacheDir, `${symbol}.csv`), 'Date,Open,High,Low,Close,Volume\n2026-10-16,100.0,101.0,99.0,100.0,1000\n2026-10-19,100.0,101.0,99.0,100.0,1000\n2026-10-20,100.0,101.0,99.0,100.0,1000\n2026-10-21,1000.0,1001.0,999.0,1000.0,1000\n');
      const item = row(symbol);
      const references = await loadScreenerFixedMAs([item], [stock(symbol)], ticker => readCollectorFixedMaBars(ticker, root));
      expect(references[symbol].period).toBe(3);
      expect(references[symbol].distance).toBeCloseTo(10);
      const list = createWatchlist({ name: '全市場快取', market: symbol.endsWith('.TWO') ? 'TW' : 'US', group: symbol.endsWith('.TWO') ? 'universe-tw' : 'universe-us', filters: 'ema20:0:20' });
      expect(evaluateWatchlist(list, [item], undefined, references).map(member => member.symbol)).toEqual([symbol]);

      // The existing primary source keeps priority even when a cache file exists.
      await mkdir(join(root, 'data'));
      await writeFile(join(root, 'data', `${symbol}.csv`), 'Date,Open,High,Low,Close,Volume\n2026-10-20,90,91,89,90,1000\n');
      expect((await readCollectorFixedMaBars(symbol, root)).map(bar => bar.close)).toEqual([90]);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });

  it('names the missing fixed stock and preserves the previous evaluation when both CSV sources are absent', async () => {
    const root = await mkdtemp(join(tmpdir(), 'atlas-fixed-missing-'));
    try {
      const item = row('MISSING', { metrics: { ema20: 5 } });
      const references = await loadScreenerFixedMAs([item], [stock('MISSING')], symbol => readCollectorFixedMaBars(symbol, root));
      expect(references.MISSING.distance).toBeNull();
      const list = createWatchlist({ name: '保留基準', market: 'US', group: 'universe-us', filters: 'ema20:0:20' });
      expect(() => evaluateWatchlist(list, [item], undefined, references)).toThrow(/MISSING.*保留上次清單.*暫不判定出榜/);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
