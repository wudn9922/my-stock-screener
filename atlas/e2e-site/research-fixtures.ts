import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export const researchFixtureRoot = resolve(import.meta.dirname, '../tests/fixtures/research-public');
const symbols: Record<string, string> = {
  wti: 'CMD-WTI', brent: 'CMD-BRENT', gas: 'CMD-GAS', gold: 'CMD-GOLD',
  silver: 'CMD-SILVER', copper: 'CMD-COPPER', usdtwd: 'FX-USDTWD',
  usdjpy: 'FX-USDJPY', eurusd: 'FX-EURUSD', usdcny: 'FX-USDCNY', usdkrw: 'FX-USDKRW',
};

/** Browser mocks use frozen source excerpts and main's retained OHLC evidence.
 * Keep candles at the frozen snapshot date when main appends newer sessions.
 * The optional CSV override exercises that boundary in fixture contract tests.
 */
export function researchFixtureBody(path: string, candleCsv?: string): Buffer {
  const match = /^candles\/([a-z]+)\.json$/.exec(path);
  if (!match) return readFileSync(resolve(researchFixtureRoot, path));
  const symbol = symbols[match[1]!];
  if (!symbol) throw new Error(`Unknown research candle fixture: ${path}`);
  const snapshot = JSON.parse(readFileSync(resolve(researchFixtureRoot, 'world-assets.json'), 'utf8'))
    .series[match[1]!] as { symbol: string; asOf: string; latest: number };
  const csv = candleCsv ?? readFileSync(resolve(import.meta.dirname, `../../data/world/${symbol}.csv`), 'utf8');
  const bars = csv.trim().split(/\r?\n/).slice(1).map((line) => {
    const [day, ...values] = line.split(',');
    return [day!, ...values.map((v) => v === '' ? null : Number(v))] as [string, ...Array<number | null>];
  }).filter(([day]) => day <= snapshot.asOf);
  if (snapshot.symbol !== symbol || bars.at(-1)?.[0] !== snapshot.asOf || bars.at(-1)?.[4] !== snapshot.latest)
    throw new Error(`Retained OHLC does not match frozen research snapshot: ${symbol}`);
  return Buffer.from(JSON.stringify({ version: 1, symbol, interval: '1D', bars }));
}
