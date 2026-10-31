import { describe, expect, it, vi } from 'vitest';
import { researchFixtureBody } from '../e2e-site/research-fixtures';
import { WorldAssetProvider } from '../src/market-data/WorldAssetProvider';
import { createScreenerMarketProvider } from '../src/market-data/createScreenerMarketProvider';
import { WORLD_ASSETS } from '../src/market-data/worldAssets';
import { researchDrawingSymbol } from '../src/report/researchCandles';
import { isCanonicalSymbol } from '../src/market-data/MarketDataProvider';
import { displayTicker, formatMarketVolume } from '../src/ui/format';
import { cellLabelFit } from '../src/pages/treemap';
import { industryName, US_INDUSTRY_ZH } from '../src/pages/industryNames';

function fetcher(change?: (path: string, payload: Record<string, unknown>) => void) {
  return vi.fn<typeof fetch>(async input => {
    const path = String(input).split('/research/')[1];
    if (!path) throw new Error(`Unexpected provider request: ${input}`);
    const payload = JSON.parse(researchFixtureBody(path).toString());
    change?.(path, payload);
    return Response.json(payload);
  });
}
describe('read-only world asset workbench provider', () => {
  it('preserves every old drawing identity, source identity and human label without collisions', () => {
    expect(new Set(WORLD_ASSETS.map(asset => asset.symbol)).size).toBe(12);
    for (const asset of WORLD_ASSETS) {
      expect(asset.symbol).toBe(researchDrawingSymbol(`world:${asset.sourceSymbol}`));
      expect(isCanonicalSymbol(asset.symbol)).toBe(true);
      expect(displayTicker(asset.symbol)).toBe(asset.ticker);
    }
    expect(formatMarketVolume(1234, WORLD_ASSETS[0]!.symbol)).toBe('1,234 口');
    expect(formatMarketVolume(0.125, WORLD_ASSETS[6]!.symbol)).toBe('0.125 BTC');
  });
  it('serves all real-shaped asset fixtures, matching daily quotes and aggregating weekly/monthly volume', async () => {
    const provider = new WorldAssetProvider('/report/', fetcher());
    for (const asset of WORLD_ASSETS) {
      const daily = await provider.getBars(asset.symbol, '1D');
      expect(daily.bars.length).toBeGreaterThan(2);
      expect(daily.quote!.symbol).toBe(asset.symbol);
      expect(daily.quote!.price).toBe(daily.bars.at(-1)!.close);
      for (const tf of ['1W', '1M'] as const) {
        const result = await provider.getBars(asset.symbol, tf);
        expect(result.bars.at(-1)!.close).toBe(daily.bars.at(-1)!.close);
        expect(result.bars.reduce((sum, bar) => sum + bar.volume, 0)).toBeCloseTo(daily.bars.reduce((sum, bar) => sum + bar.volume, 0), 4);
      }
    }
    await expect(provider.getBars(WORLD_ASSETS[0]!.symbol, '1H')).rejects.toThrow('只提供');
  });
  it('routes recognized assets directly and never falls back to live stocks when published data is missing', async () => {
    const request = vi.fn<typeof fetch>(async () => new Response('', { status: 404 }));
    const provider = createScreenerMarketProvider({ cache: false, reportBase: '/report/', fetcher: request });
    await expect(provider.getBars(WORLD_ASSETS[0]!.symbol, '1D')).rejects.toThrow('清單');
    expect(request).toHaveBeenCalledTimes(1);
    expect(String(request.mock.calls[0]![0])).toBe('/report/research/world-assets.json');
  });
  it('uses the same configured report root as the existing world page', async () => {
    vi.stubEnv('VITE_REPORT_URL', 'https://reports.example/v2/latest.json');
    try {
      const request = fetcher();
      const provider = createScreenerMarketProvider({ cache: false, fetcher: request });
      await provider.getBars(WORLD_ASSETS[0]!.symbol, '1D');
      expect(request.mock.calls.map(([url]) => String(url))).toEqual([
        'https://reports.example/v2/research/world-assets.json', 'https://reports.example/v2/research/candles/wti.json',
      ]);
    } finally { vi.unstubAllEnvs(); }
  });
  it('validates ranges, normalizes quote identity and refetches source files for explicit refresh', async () => {
    const request = fetcher();
    const provider = new WorldAssetProvider('/report/', request);
    const symbol = WORLD_ASSETS[0]!.symbol;
    await expect(provider.getBars(symbol, '1D', { from: NaN })).rejects.toThrow('finite');
    expect(request).not.toHaveBeenCalled();
    expect((await provider.getQuote(symbol.toLowerCase())).symbol).toBe(symbol);
    await provider.refreshBars(symbol, '1D');
    expect(request).toHaveBeenCalledTimes(4);
    expect(request.mock.calls.every(([, init]) => init?.cache === 'no-cache')).toBe(true);
  });
  it('can draw a single source bar without inventing the missing daily change', async () => {
    const provider = new WorldAssetProvider('/report/', fetcher((path, raw) => {
      if (path.startsWith('candles/')) raw.bars = (raw.bars as unknown[]).slice(-1);
    }));
    const result = await provider.getBars(WORLD_ASSETS[0]!.symbol, '1D');
    expect(result.bars).toHaveLength(1);
    expect(result.quote).toBeUndefined();
    await expect(provider.getQuote(WORLD_ASSETS[0]!.symbol)).rejects.toThrow('前一日');
  });
  it.each(['wrong-pair', 'wrong-symbol', 'mismatch'])('rejects %s rather than showing another instrument or an inconsistent snapshot', async mode => {
    const provider = new WorldAssetProvider('/report/', fetcher((path, raw) => {
      if (path !== 'world-assets.json') return;
      const series = raw.series as Record<string, Record<string, unknown>>;
      if (mode === 'wrong-pair') series.btcusdt!.quoteCurrency = 'USD';
      if (mode === 'wrong-symbol') series.btcusdt!.symbol = 'CMD-GOLD';
      if (mode === 'mismatch') series.btcusdt!.latest = 1;
    }));
    await expect(provider.getBars(WORLD_ASSETS[6]!.symbol, '1D')).rejects.toThrow();
  });
});
describe('Chinese heatmap labels', () => {
  it('covers the current 152 Nasdaq industry names without changing Taiwan or unknown source names', () => {
    expect(Object.keys(US_INDUSTRY_ZH)).toHaveLength(152);
    expect(Object.values(US_INDUSTRY_ZH).every(name => /[\u3400-\u9fff]/.test(name))).toBe(true);
    expect(industryName('Semiconductors', 'US')).toBe('半導體');
    expect(industryName('Semiconductors', 'TW')).toBe('Semiconductors');
    expect(industryName('New source industry', 'US')).toBe('New source industry');
  });
  it('uses a readable abbreviation in tight cells and leaves only physically unreadable cells unlabeled', () => {
    const fit = cellLabelFit('很長很長的生物科技產業分類', 42, 30, 14);
    expect(fit.fit).not.toBe('none');
    expect(fit.text).toMatch(/…$/);
    expect(fit.fontSize).toBeGreaterThanOrEqual(10);
    expect(cellLabelFit('半導體', 8, 8, 14).fit).toBe('none');
    expect(cellLabelFit('半導體', 100, 80, 14).text).toBe('半導體');
  });
});
