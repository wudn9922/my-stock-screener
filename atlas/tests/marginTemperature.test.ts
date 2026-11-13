import { describe, expect, it } from 'vitest';
import { parseMarginTemperature } from '../src/report/marginTemperature';

const first = { date: '2026-11-06', close: 39701.64, pct: 85.80645161290322, mdd: -17.381208636401414, hot: false, cold: false };
const latest = { date: '2026-11-12', close: 43091.87, pct: 85.16129032258064, mdd: -17.47062661957971, hot: false, cold: false };
const payload = { version: 1, asOf: latest.date, provisional: true, scope: 'TWSE listed only', latest, rows: [first, latest] };
describe('融資溫度資料契約', () => {
  it('保留參考資料中性判定與最新值', () => {
    const result = parseMarginTemperature(payload);
    expect(result.error).toBeUndefined();
    expect(result.data?.latest.pct).toBeCloseTo(85.16129032258064);
    expect(result.data?.latest.mdd).toBeCloseTo(-17.47062661957971);
  });
  it('同時過熱與過冷時兩個狀態均保留', () => {
    const dual = { ...latest, pct: 94, mdd: -40, hot: true, cold: true };
    expect(parseMarginTemperature({ ...payload, latest: dual, rows: [first, dual] }).data?.latest).toMatchObject({ hot: true, cold: true });
  });
  it('拒絕錯誤門檻與重複日期', () => {
    expect(parseMarginTemperature({ ...payload, rows: [first, { ...latest, hot: true }] }).error).toBeTruthy();
    expect(parseMarginTemperature({ ...payload, rows: [latest, latest] }).error).toBeTruthy();
  });
});
