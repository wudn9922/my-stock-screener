import csv from '../../../data/world/CRYPTO-BTCUSDT.csv?raw';
import proof from './btcUsdtArchive.proof.json';
import { worldAssetsSchema } from './research';
import { researchBars } from './researchCandles';

/** A dated, genuine retained pair for deployments whose older world index has
 * not collected BTC yet. Existing published BTC snapshots remain authoritative.
 */
export async function parseBtcUsdtArchive(source: string, receipt = proof) {
  const pair = receipt.asset;
  if (pair.symbol !== 'CRYPTO-BTCUSDT' || pair.providerSymbol !== 'BTCUSDT' ||
      pair.baseCurrency !== 'BTC' || pair.quoteCurrency !== 'USDT' || pair.volumeUnit !== 'BTC' ||
      pair.instrumentType !== 'crypto-spot' || pair.unit !== 'USDT/BTC' || pair.timezone !== 'UTC')
    throw new Error('BTC/USDT 保存報價的商品或幣別不符。');
  const [header, ...lines] = source.trimEnd().split(/\r?\n/);
  if (header !== 'Date,Open,High,Low,Close,Volume') throw new Error('BTC/USDT 保存行情格式不符。');
  // New source sessions may be appended later. Validate this frozen receipt's
  // exact original prefix, never draw future bars against older metadata.
  const retained = lines.filter(line => line.split(',')[0]! <= pair.asOf);
  const bytes = new TextEncoder().encode([header, ...retained].join('\n') + '\n');
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  if (hash !== receipt.csvSha256) throw new Error('BTC/USDT 保存行情與來源紀錄不一致。');
  const rows = retained.map(line => {
    const [day, ...prices] = line.split(',');
    return [day, ...prices.map(value => value === '' ? null : Number(value))];
  });
  if (rows.length !== pair.observations || rows[0]?.[0] !== pair.coverageFrom ||
      rows.at(-1)?.[0] !== pair.asOf || rows.at(-1)?.[4] !== pair.latest)
    throw new Error('BTC/USDT 保存行情與報價日期不一致。');
  const asset = worldAssetsSchema.parse({ version: 1, generatedAt: pair.generatedAt,
    series: { btcusdt: pair }, limitations: '' }).series.btcusdt!;
  return { asset, bars: researchBars({ version: 1, symbol: pair.symbol, interval: '1D', bars: rows }, pair.symbol) };
}

export const loadBtcUsdtArchive = () => parseBtcUsdtArchive(csv);
