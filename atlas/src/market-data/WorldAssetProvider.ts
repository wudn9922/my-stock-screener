import { worldAssetsSchema, type WorldAsset } from '../report/research';
import { researchBars } from '../report/researchCandles';
import { aggregateBars, STATIC_SERIES_TIMEFRAMES } from './StaticSeriesProvider';
import { reportUrl } from '../report/loadReport';
import { filterBarsByRange, getMarketProfile, normalizeMarketRange, type Bar, type BarResult, type MarketDataProvider, type MarketRange, type Quote, type Timeframe } from './MarketDataProvider';
import { worldAssetFor } from './worldAssets';

export function validateWorldAsset(asset: WorldAsset, sourceSymbol: string) {
  if (asset.symbol !== sourceSymbol) throw new Error('商品代號不符，暫不顯示行情。');
  if (sourceSymbol === 'CRYPTO-BTCUSDT' && (asset.providerSymbol !== 'BTCUSDT' || asset.baseCurrency !== 'BTC' ||
      asset.quoteCurrency !== 'USDT' || asset.instrumentType !== 'crypto-spot'))
    throw new Error('BTC/USDT 來源商品或報價幣別不符，暫不顯示行情。');
}

/** Read-only published research OHLC. Never substitute a stock/Yahoo quote for a missing asset. */
export class WorldAssetProvider implements MarketDataProvider {
  readonly id = 'world-assets';
  readonly cacheVersion = 'world-assets-v1';
  readonly supportedTimeframes = STATIC_SERIES_TIMEFRAMES;
  readonly capabilities = { corporateEvents: 'unavailable' } as const;
  constructor(private readonly base = new URL('.', reportUrl()).href, private readonly fetcher: typeof fetch = (input, init) => fetch(input, init)) {}

  async load(symbol: string, signal?: AbortSignal, snapshot?: WorldAsset): Promise<{ asset: WorldAsset; bars: Bar[] }> {
    signal?.throwIfAborted();
    const definition = worldAssetFor(symbol);
    if (!definition) throw new Error('不支援的商品。');
    const root = this.base.endsWith('/') ? this.base : `${this.base}/`;
    let asset = snapshot;
    if (!asset) {
      const response = await this.fetcher(`${root}research/world-assets.json`, { signal, cache: 'no-cache' });
      if (!response.ok) throw new Error('商品清單暫時無法取得。');
      asset = worldAssetsSchema.parse(await response.json()).series[definition.id];
    }
    if (!asset && definition.id === 'btcusdt') {
      const { loadBtcUsdtArchive } = await import('../report/btcUsdtArchive');
      const retained = await loadBtcUsdtArchive();
      signal?.throwIfAborted();
      return retained;
    }
    if (!asset?.candlePath) throw new Error('尚未取得真正的開高低收資料。');
    validateWorldAsset(asset, definition.sourceSymbol);
    const candles = await this.fetcher(`${root}research/${asset.candlePath}`, { signal, cache: 'no-cache' });
    if (!candles.ok) throw new Error(`K線資料暫時無法取得（HTTP ${candles.status}）。`);
    const payload = await candles.json();
    const bars = researchBars(payload, definition.sourceSymbol);
    if (!bars.length || payload.bars.at(-1)?.[0] !== asset.asOf || bars.at(-1)?.close !== asset.latest)
      throw new Error('K線與報價快照正在更新，請重新載入。');
    return { asset, bars };
  }

  async getBars(symbol: string, timeframe: Timeframe, range?: MarketRange, signal?: AbortSignal): Promise<BarResult> {
    const requested = normalizeMarketRange(range);
    if (!(this.supportedTimeframes as readonly string[]).includes(timeframe)) throw new Error('此商品只提供日 K、週 K、月 K。');
    const { asset, bars: daily } = await this.load(symbol, signal);
    const bars = filterBarsByRange(timeframe === '1D' ? daily : aggregateBars(daily, timeframe as '1W' | '1M', getMarketProfile(symbol)), requested);
    if (!bars.length) throw new Error('此區間沒有商品 K 線資料。');
    const quote = daily.length > 1 ? this.quote(symbol, daily, asset) : undefined;
    return { bars, quote, source: `${asset.source}${asset.status === 'stale' ? '（來源過期）' : ''}`, session: 'regular', delayed: true, adjusted: false,
      priceBasis: 'unknown', dataState: 'delayed', asOf: daily.at(-1)!.time, latestBarAt: bars.at(-1)!.time };
  }

  private quote(symbol: string, bars: Bar[], asset: WorldAsset): Quote {
    const last = bars.at(-1)!;
    const previous = bars.at(-2)?.close;
    // With only one source row there is no daily change; do not invent a previous close.
    if (previous === undefined) throw new Error('缺少前一日報價，暫無法計算漲跌。');
    return { symbol: worldAssetFor(symbol)!.symbol, price: last.close, change: last.close - previous, changePercent: (last.close / previous - 1) * 100,
      asOf: last.time, source: asset.source, dataState: 'delayed' };
  }
  async getQuote(symbol: string, signal?: AbortSignal) {
    const loaded = await this.load(symbol, signal);
    return this.quote(symbol, loaded.bars, loaded.asset);
  }
  refreshBars(symbol: string, timeframe: Timeframe, signal?: AbortSignal) { return this.getBars(symbol, timeframe, undefined, signal); }
  async getCorporateEvents() { return { status: 'unavailable' as const, events: [] as [], source: '商品不適用個股公司事件' }; }
  async getSymbolTimeframes() { return this.supportedTimeframes; }
}
