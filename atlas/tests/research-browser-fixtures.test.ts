import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { researchFixtureBody } from '../e2e-site/research-fixtures';

describe('frozen research browser candle fixtures', () => {
  const csv = readFileSync(resolve(import.meta.dirname, '../../data/world/CMD-WTI.csv'), 'utf8');
  const series = JSON.parse(researchFixtureBody('world-assets.json').toString()).series as Record<string, {
    symbol: string; asOf: string; latest: number; candlePath?: string;
  }>;
  const snapshot = series.wti!;
  const candles = Object.entries(series).filter(([, asset]) => asset.candlePath);

  it('covers all twelve commodity, FX and BTC/USDT candle snapshots', () => {
    expect(candles).toHaveLength(12);
  });

  it.each(candles)('matches the frozen date and close for %s', (_id, asset) => {
    const body = JSON.parse(researchFixtureBody(asset.candlePath!).toString());
    expect(body.symbol).toBe(asset.symbol);
    expect(body.bars.at(-1)[0]).toBe(asset.asOf);
    expect(body.bars.at(-1)[4]).toBe(asset.latest);
    expect(body.bars.some(([day]: [string]) => day > asset.asOf)).toBe(false);
  });

  it('keeps the frozen snapshot coherent when the retained source has newer sessions', () => {
    // This synthetic future row is only a fixture-boundary regression input.
    const newerCsv = `${csv.trim()}\n2099-01-01,101,105,100,103,1\n`;
    const body = JSON.parse(researchFixtureBody('candles/wti.json', newerCsv).toString());
    expect(body.symbol).toBe(snapshot.symbol);
    expect(body.bars.at(-1)[0]).toBe(snapshot.asOf);
    expect(body.bars.at(-1)[4]).toBe(snapshot.latest);
    expect(body.bars.some(([day]: [string]) => day > snapshot.asOf)).toBe(false);
  });

  it('rejects a source missing the frozen session instead of inventing OHLC', () => {
    const beforeSnapshot = csv.trim().split(/\r?\n/).filter((line) => !line.startsWith(snapshot.asOf)).join('\n');
    expect(() => researchFixtureBody('candles/wti.json', beforeSnapshot)).toThrow(snapshot.symbol);
  });

  it('rejects a changed close at the frozen date instead of hiding a mismatch', () => {
    const changedClose = csv.trim().split(/\r?\n/).map((line) => {
      if (!line.startsWith(snapshot.asOf)) return line;
      const columns = line.split(',');
      columns[4] = String(snapshot.latest + 1);
      return columns.join(',');
    }).join('\n');
    expect(() => researchFixtureBody('candles/wti.json', changedClose)).toThrow(snapshot.symbol);
  });
});
