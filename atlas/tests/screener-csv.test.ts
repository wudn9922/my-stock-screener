import { describe, expect, it } from 'vitest';
import { screenerCsv, screenerCsvName } from '../src/pages/screenerCsv';
import type { GroupItem } from '../src/report/schema';

const item = (overrides: Partial<GroupItem>): GroupItem => ({
  symbol: '2330.TW', name: '台積電', maList: [20, 60], close: 1000, changePct: 1.234,
  maValues: { '20': 800, '60': 1250 }, note: undefined, volume: 12345, asOf: '2026-11-09', metrics: undefined,
  ...overrides,
} as GroupItem);

describe('screener CSV export', () => {
  it('writes the rows in order with MA values and distances', () => {
    const lines = screenerCsv([item({}), item({ symbol: 'NVDA', name: 'NVIDIA', maList: [20], maValues: { '20': 100 }, close: 110 })]).split('\r\n');
    expect(lines[0]).toBe('﻿代號,名稱,收盤,漲跌幅%,成交量,MA20,距MA20%,MA60,距MA60%,資料日,備註');
    expect(lines[1]).toBe('2330,台積電,1000,1.23,12345,800,25,1250,-20,2026-11-09,');
    // A period the row does not use stays empty instead of borrowing another row's column.
    expect(lines[2]).toBe('NVDA,NVIDIA,110,1.23,12345,100,10,,,2026-11-09,');
  });
  it('leaves missing numbers empty', () => {
    const line = screenerCsv([item({ close: null, changePct: null, volume: null })]).split('\r\n')[1];
    expect(line).toBe('2330,台積電,,,,800,,1250,,2026-11-09,');
  });
  it('adds the RS score, ATR% and 20-day range columns only when some row has them', () => {
    const metrics = (rsScore: number | null, atrPct: number | null, range20: number | null) =>
      ({ rsScore, atrPct, range20 } as unknown as GroupItem['metrics']);
    const lines = screenerCsv([
      item({ metrics: metrics(12.3456, 2.5, 18.754) }),
      item({ symbol: 'NVDA', name: 'NVIDIA', maList: [20], maValues: { '20': 100 }, close: 110, metrics: metrics(null, 3, null) }),
      item({ symbol: 'AMD', name: 'AMD', maList: [20], maValues: { '20': 100 }, close: 110, metrics: undefined }),
    ]).split('\r\n');
    expect(lines[0]).toBe('﻿代號,名稱,收盤,漲跌幅%,成交量,MA20,距MA20%,MA60,距MA60%,資料日,備註,RS分數%（3/6/9/12月相對報酬加權40/20/20/20）,ATR%,20日區間寬度%');
    expect(lines[1]).toBe('2330,台積電,1000,1.23,12345,800,25,1250,-20,2026-11-09,,12.35,2.5,18.75');
    expect(lines[2]).toBe('NVDA,NVIDIA,110,1.23,12345,100,10,,,2026-11-09,,,3,');
    expect(lines[3]).toBe('AMD,AMD,110,1.23,12345,100,10,,,2026-11-09,,,,');
    // Old-format rows (no value anywhere) export exactly as before, whatever the metric object looks like.
    const old = screenerCsv([item({ metrics: metrics(null, null, null) })]).split('\r\n');
    expect(old[0]).toBe('﻿代號,名稱,收盤,漲跌幅%,成交量,MA20,距MA20%,MA60,距MA60%,資料日,備註');
    expect(old[1]).toBe('2330,台積電,1000,1.23,12345,800,25,1250,-20,2026-11-09,');
  });
  it('quotes separators and neutralises spreadsheet formulas', () => {
    const line = screenerCsv([item({ name: '=HYPERLINK("http://x","a")', note: '現價:10, 距MA20:"1%"', maList: [], maValues: {} })]).split('\r\n')[1];
    expect(line).toBe('2330,"\'=HYPERLINK(""http://x"",""a"")",1000,1.23,12345,2026-11-09,"現價:10, 距MA20:""1%"""');
  });
  it('builds a portable file name', () => {
    expect(screenerCsvName('tw_g1', '2026/11/09')).toBe('atlas-tw_g1-2026-11-09.csv');
    expect(screenerCsvName('../x y', null)).toBe('atlas-x-y.csv');
  });
});
