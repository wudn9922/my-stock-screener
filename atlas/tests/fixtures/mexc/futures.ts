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
/** Documented position fields with PnL omitted and liquidation sentinels; all values are synthetic. */
export function documentedFuturesResponses(): Record<string, unknown> {
  const data = futuresResponses();
  data[positionsPath] = [
    {
      positionId: 1394650,
      symbol: 'BTC_USDT',
      positionType: 1,
      openType: 1,
      state: 1,
      holdVol: '5',
      frozenVol: 0,
      closeVol: 0,
      holdAvgPrice: '60000',
      openAvgPrice: '60000',
      closeAvgPrice: 0,
      liquidatePrice: 0,
      oim: '30',
      im: '30',
      holdFee: 0,
      realised: '-0.0073',
      leverage: 10,
      adlLevel: 1,
      createTime: 1609991676000,
      updateTime: 1609991676000,
      autoAddIm: false,
    },
    {
      positionId: 1394651,
      symbol: 'ETH_USDT',
      positionType: 2,
      openType: 2,
      state: 1,
      holdVol: '2.5',
      frozenVol: 0,
      closeVol: 0,
      holdAvgPrice: '3500',
      openAvgPrice: '3500',
      closeAvgPrice: 0,
      liquidatePrice: -1,
      oim: '20',
      im: '20',
      holdFee: 0,
      realised: 0,
      leverage: 5,
      adlLevel: null,
      createTime: 1609991676000,
      updateTime: 1609991676000,
      autoAddIm: false,
    },
  ];
  return data;
}
/** Contract rows for `count` distinct held symbols, each with an exact contract size and fair price. */
export function manyFuturesResponses(count: number): Record<string, unknown> {
  const base = futuresResponses();
  const position = (base[positionsPath] as Record<string, unknown>[])[0]!;
  const symbols = Array.from({ length: count }, (_, i) => `HELD${i}_USDT`);
  return {
    ...base,
    [positionsPath]: symbols.map((symbol) => ({ ...position, symbol })),
    [detailPath]: symbols.map((symbol) => ({ symbol, contractSize: '0.0001' })),
    [tickerPath]: symbols.map((symbol) => ({ symbol, fairPrice: '59000' })),
  };
}
/**
 * Public list endpoint double: a `symbol=` query returns that one contract as a wrapped object;
 * the unfiltered list pads its rows so the whole body is at least `bytes` long, like the real
 * all-contracts list that outgrew the 2 MiB response cap.
 */
export function publicList(rows: Record<string, unknown>[], bytes: number) {
  return (input: RequestInfo | URL) => {
    const symbol = new URL(String(input)).searchParams.get('symbol');
    if (symbol !== null)
      return Response.json({ success: true, code: 0, data: rows.find((row) => row.symbol === symbol) ?? null });
    const filler = 'x'.repeat(Math.ceil(bytes / Math.max(rows.length, 1)));
    return Response.json({ success: true, code: 0, data: rows.map((row) => ({ ...row, displayName: filler })) });
  };
}
