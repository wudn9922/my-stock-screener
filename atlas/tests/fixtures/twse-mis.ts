/** Synthetic quotes in the observed 2026-11-05 MIS msgArray shape; never fetched from TWSE. */
export const MIS_NOW = Date.parse('2026-11-05T01:47:00Z');
export const MIS_TODAY = Date.parse('2026-11-05T01:00:00Z') / 1000;
export const misRows = () => [
  { c: 't00', ex: 'tse', n: '加權指數', z: '42306.23', o: '42400', h: '42500', l: '42000', y: '42948.91', v: '-', d: '20261105', t: '09:46:35' },
  { c: '2330', ex: 'tse', n: '測試台股', z: '-', o: '1820', h: '1845', l: '1810', y: '1805', v: '1,234', d: '20261105', t: '09:46:35' },
  { c: 'o00', ex: 'otc', n: '櫃買指數', z: '329.65', o: '330', h: '332', l: '328', y: '331', v: '-', d: '20261105', t: '09:46:35' },
  { c: '6488', ex: 'otc', n: '測試上櫃股', z: '2900', o: '2880', h: '2920', l: '2860', y: '2850', v: '100', d: '20261105', t: '09:46:35' },
];
export function misYahoo(symbol = '2330.TW', today = false) {
  const price = symbol === '^TWII' ? 42948.91 : symbol === '^TWOII' ? 331 : symbol === '6488.TWO' ? 2850 : 1818;
  const timestamp = [MIS_TODAY - 2 * 86400, MIS_TODAY - 86400];
  const q: Record<'open' | 'high' | 'low' | 'close' | 'volume', (number | null)[]> = {
    open: [price - 10, price - 5], high: [price + 20, price + 20], low: [price - 20, price - 20],
    close: [price - 10, price], volume: [100000, 200000],
  };
  if (today) { timestamp.push(MIS_TODAY); for (const field of Object.values(q)) field.push(null); }
  return { chart: { result: [{ meta: { symbol, currency: symbol === 'AAPL' ? 'USD' : 'TWD',
    exchangeTimezoneName: symbol === 'AAPL' ? 'America/New_York' : 'Asia/Taipei', gmtoffset: symbol === 'AAPL' ? -18000 : 28800,
    regularMarketPrice: price, regularMarketTime: MIS_TODAY - 86400 + 4 * 3600 }, timestamp,
    indicators: { quote: [q] } }], error: null } };
}
