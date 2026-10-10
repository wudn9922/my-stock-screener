import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseReport } from '../src/report/loadReport';
import { groupItemSchema, parseMetrics } from '../src/report/schema';

const sample: unknown = JSON.parse(readFileSync('src/report/fixtures/latest.sample.json', 'utf8'));

describe('group item metrics', () => {
  it('parses volume, asOf and metrics from the Python sample', () => {
    const result = parseReport(sample);
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const items = result.report.groups.flatMap((group) => group.items);
    expect(items.length).toBeGreaterThan(10);
    for (const item of items) {
      expect(item.volume).toBeTypeOf('number');
      expect(item.asOf).toBe('2026-10-08');
      expect(item.metrics).not.toBeNull();
      expect(item.metrics!.fromHi52!).toBeLessThanOrEqual(0);
      expect(item.metrics!.rsRank!).toBeGreaterThanOrEqual(1);
      expect(item.metrics!.rsRank!).toBeLessThanOrEqual(99);
    }
    const tw = result.report.groups.find((group) => group.key === 'tw_all')!.items[0]!;
    expect(tw.metrics).toMatchObject({ bars: 201, hiBars: 201, rsBench: '^TWII', ma: { 20: -0.04 } });
    const us = result.report.groups.find((group) => group.market === 'US' && group.items.length)!.items[0]!;
    expect(us.metrics!.rsBench).toBe('SPY');
  });

  it('keeps old reports working: no metrics → null, no volume → null', () => {
    const item = groupItemSchema.parse({ symbol: '2330.tw', close: 1000, maList: [20], maValues: { 20: 990 } });
    expect(item).toMatchObject({ symbol: '2330.TW', volume: null, asOf: null, metrics: null });
  });

  it('turns a broken metrics value into null without dropping the row', () => {
    for (const bad of ['x', 42, [1, 2], null, true])
      expect(groupItemSchema.parse({ symbol: 'AAPL', metrics: bad }).metrics).toBeNull();
  });

  it('parses each metric field tolerantly', () => {
    const metrics = parseMetrics({
      bars: '201',
      r5: '1.5',
      r21: 'n/a',
      r63: Infinity,
      ma: { 20: 2.1, 50: 'bad', 200: '-3' },
      ma20Slope5: 0.4,
      trend: ' UP ',
      hi52: 120,
      fromHi52: 0.004, // rounding noise above the high
      hiBars: 201.7,
      rs21: null,
      rs63: 12,
      rsBench: 'spy',
      rsRank: 140,
      volRatio: 1.8,
      turnover20: 1e9,
      unknown: 'ignored',
    })!;
    expect(metrics).toEqual({
      bars: 201,
      r5: 1.5,
      r21: null,
      r63: null,
      ma: { 20: 2.1, 50: null, 200: -3 },
      ma20Slope5: 0.4,
      trend: 'up',
      hi52: 120,
      fromHi52: 0,
      hiBars: 201,
      rs21: null,
      rs63: 12,
      rsBench: 'SPY',
      rsRank: 99,
      volRatio: 1.8,
      turnover20: 1e9,
    });
    expect(parseMetrics({ trend: 'sideways', rsBench: 'QQQ', ma: 'x', bars: -5 })).toMatchObject({
      trend: null,
      rsBench: null,
      ma: { 20: null, 50: null, 200: null },
      bars: 0,
      hiBars: 0,
    });
  });
});
