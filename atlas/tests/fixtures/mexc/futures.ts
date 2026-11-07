/** Synthetic read-only Futures V1 fixture, fields cross-checked with official 2026-11-07 docs. */
export const assetsPath = '/api/v1/private/account/assets';
export const positionsPath = '/api/v1/private/position/open_positions';
export const detailPath = '/api/v1/contract/detail/country';
export const tickerPath = '/api/v1/contract/ticker';
export function futuresResponses(): Record<string, unknown> {
  return {
    [assetsPath]: [
      {
        currency: 'USDT',
        equity: '1000.12',
        availableBalance: '700.12',
        positionMargin: '300',
        unrealized: '-12.34',
      },
    ],
    [positionsPath]: [
      {
        symbol: 'BTC_USDT',
        positionType: 1,
        openType: 1,
        state: 1,
        holdVol: '5',
        holdAvgPrice: '60000',
        leverage: 10,
        im: '30',
        unRealizedPnl: '-0.5',
        liquidatePrice: '54000',
      },
      {
        symbol: 'ETH_USDT',
        positionType: 2,
        openType: 2,
        state: 1,
        holdVol: '2.5',
        holdAvgPrice: '3500',
        leverage: 5,
        im: '20',
        unRealizedPnl: '1.25',
      },
    ],
    [detailPath]: [
      { symbol: 'BTC_USDT', contractSize: '0.0001' },
      { symbol: 'ETH_USDT', contractSize: '0.01' },
    ],
    [tickerPath]: [
      { symbol: 'BTC_USDT', fairPrice: '59000' },
      { symbol: 'ETH_USDT', fairPrice: '3450' },
    ],
  };
}
