import { researchDrawingSymbol } from './researchIdentity';

export const WORLD_ASSETS = [
  { id: 'wti', sourceSymbol: 'CMD-WTI', name: 'WTI 原油', ticker: 'WTI', unit: 'USD／桶', kind: 'commodities', precision: 2 },
  { id: 'brent', sourceSymbol: 'CMD-BRENT', name: '布蘭特原油', ticker: 'BRENT', unit: 'USD／桶', kind: 'commodities', precision: 2 },
  { id: 'gas', sourceSymbol: 'CMD-GAS', name: '天然氣', ticker: 'GAS', unit: 'USD／MMBtu', kind: 'commodities', precision: 3 },
  { id: 'gold', sourceSymbol: 'CMD-GOLD', name: '黃金', ticker: 'GOLD', unit: 'USD／金衡盎司', kind: 'commodities', precision: 2 },
  { id: 'silver', sourceSymbol: 'CMD-SILVER', name: '白銀', ticker: 'SILVER', unit: 'USD／金衡盎司', kind: 'commodities', precision: 3 },
  { id: 'copper', sourceSymbol: 'CMD-COPPER', name: '銅', ticker: 'COPPER', unit: 'USD／磅', kind: 'commodities', precision: 4 },
  { id: 'btcusdt', sourceSymbol: 'CRYPTO-BTCUSDT', name: 'BTC/USDT', ticker: 'BTC/USDT', unit: 'USDT／BTC', kind: 'commodities', precision: 2 },
  { id: 'usdtwd', sourceSymbol: 'FX-USDTWD', name: '美元／台幣', ticker: 'USD/TWD', unit: 'TWD／USD', kind: 'fx', precision: 4 },
  { id: 'usdjpy', sourceSymbol: 'FX-USDJPY', name: '美元／日圓', ticker: 'USD/JPY', unit: 'JPY／USD', kind: 'fx', precision: 4 },
  { id: 'eurusd', sourceSymbol: 'FX-EURUSD', name: '歐元／美元', ticker: 'EUR/USD', unit: 'USD／EUR', kind: 'fx', precision: 4 },
  { id: 'usdcny', sourceSymbol: 'FX-USDCNY', name: '美元／人民幣', ticker: 'USD/CNY', unit: 'CNY／USD', kind: 'fx', precision: 4 },
  { id: 'usdkrw', sourceSymbol: 'FX-USDKRW', name: '美元／韓元', ticker: 'USD/KRW', unit: 'KRW／USD', kind: 'fx', precision: 4 },
].map(asset => ({ ...asset, symbol: researchDrawingSymbol(`world:${asset.sourceSymbol}`), calendar: asset.id === 'btcusdt' ? 'continuous-utc' as const : 'regular' as const }));

export function worldAssetFor(symbol: string) {
  return WORLD_ASSETS.find(asset => asset.symbol === symbol.toUpperCase());
}
