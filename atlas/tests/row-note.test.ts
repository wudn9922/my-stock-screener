import { describe, expect, it } from 'vitest';
import { filterAndSortItems, isRedundantRowNote } from '../src/pages/screenerItems';
import type { GroupItem } from '../src/report/schema';

const row = (overrides: Partial<GroupItem>): GroupItem => ({
  symbol: '2330.TW', name: '台積電', maList: [20], close: 63.5, changePct: 0.5,
  maValues: { '20': 64.4 }, note: undefined, volume: 12345, asOf: '2026-11-09', metrics: undefined,
  ...overrides,
} as GroupItem);

describe('isRedundantRowNote', () => {
  it('matches the daily-report shapes that only repeat the close and MA distances', () => {
    expect(isRedundantRowNote('現價:63.50 | 距MA20:-1.41%')).toBe(true);
    expect(isRedundantRowNote('現價:239.00|距MA20:-1.72%|距MA60:+3.10%')).toBe(true);
  });

  it('matches the price alone, full-width colons, unsigned distances and thousands separators', () => {
    expect(isRedundantRowNote('現價:10')).toBe(true);
    expect(isRedundantRowNote('現價：10 | 距MA20：-1.41%')).toBe(true);
    expect(isRedundantRowNote('現價:73.80 | 距MA20:0.30%')).toBe(true);
    expect(isRedundantRowNote('現價:1,234.50 | 距MA20:+0.30%')).toBe(true);
  });

  it('ignores spaces around the note and around the bars', () => {
    expect(isRedundantRowNote('  現價:63.50 | 距MA20:-1.41%  ')).toBe(true);
    expect(isRedundantRowNote('現價:10 |距MA20:-1.41%')).toBe(true);
  });

  it('keeps any note with other text, so it stays visible', () => {
    for (const note of [
      '下載失敗',
      '現價:10 | 突破前高',
      '距MA20:-1.41%',
      '現價:10 | 距MA20:-1.41% 突破',
      '未符合均線條件 | 現價:129.50 | MA20差距(4.35%) | MA20:124.10',
      '現價: 10',
      '現價:10 |',
      '現價:10 | 距MA:-1.41%',
      '',
      '   ',
    ]) {
      expect(isRedundantRowNote(note), note).toBe(false);
    }
  });

  it('treats a missing note as not redundant', () => {
    expect(isRedundantRowNote(undefined)).toBe(false);
    expect(isRedundantRowNote(null)).toBe(false);
  });

  it('leaves the note on the item, so search still matches a row by it', () => {
    const items = [row({ note: '現價:63.50 | 距MA20:-1.41%' }), row({ symbol: '1101.TW', name: '台泥', note: undefined })];
    expect(filterAndSortItems(items, '距MA20', 'default').map((item) => item.symbol)).toEqual(['2330.TW']);
  });
});
