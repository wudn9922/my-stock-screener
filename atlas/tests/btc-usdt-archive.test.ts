import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import proof from '../src/report/btcUsdtArchive.proof.json';
import { parseBtcUsdtArchive } from '../src/report/btcUsdtArchive';

const csv = readFileSync(resolve(import.meta.dirname, '../../data/world/CRYPTO-BTCUSDT.csv'), 'utf8');
describe('genuine retained BTC/USDT spot pair', () => {
  it('verifies its source hash, true quote pair, fractional BTC volume and weekend sessions', async () => {
    const { asset, bars } = await parseBtcUsdtArchive(csv);
    expect(asset.providerSymbol).toBe('BTCUSDT');
    expect(asset.quoteCurrency).toBe('USDT');
    expect(asset.volumeUnit).toBe('BTC');
    expect(bars).toHaveLength(proof.asset.observations);
    expect(bars.length).toBeGreaterThan(1000);
    expect(bars.at(-1)?.close).toBe(proof.asset.latest);
    expect(bars.some(bar => new Date(bar.time * 1000).getUTCDay() === 0)).toBe(true);
    expect(bars.some(bar => bar.volume !== Math.trunc(bar.volume))).toBe(true);
  });
  it('clips appended newer source days at its actual saved observation date', async () => {
    const result = await parseBtcUsdtArchive(`${csv.trim()}\n2099-01-01,1,3,0.5,2,1\n`);
    expect(result.bars).toHaveLength(proof.asset.observations);
    expect(new Date(result.bars.at(-1)!.time * 1000).toISOString().slice(0, 10)).toBe(proof.asset.asOf);
  });
  it('refuses a changed historical price or a BTC/USD substitute', async () => {
    await expect(parseBtcUsdtArchive(csv.replace(String(proof.asset.latest), String(proof.asset.latest + 1)))).rejects.toThrow('不一致');
    await expect(parseBtcUsdtArchive(csv, { ...proof, asset: { ...proof.asset, quoteCurrency: 'USD' } })).rejects.toThrow('幣別不符');
  });
});
