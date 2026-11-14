import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { parseMarginTemperature } from '../src/report/marginTemperature';
import { TemperatureBody } from '../src/ui/MarginTemperatureCard';

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

const hotRow = { date: '2026-10-30', close: 45211.83, pct: 96.7741935483871, mdd: -3.5, hot: true, cold: false };
const hotLatest = { ...hotRow, ratioPct: 62.4, relHot: false, hotWeeks: 46 };
const earlier = { ...hotRow, date: '2026-10-22', hot: true };
const extra = { firstDate: '2007-01-05', hotEpisodes: 10 };
const bodyOf = (raw: unknown) => {
  const result = parseMarginTemperature(raw);
  if (!result.data) throw new Error(result.error);
  return renderToStaticMarkup(createElement(TemperatureBody, { data: result.data }));
};
const hotPayload = { version: 1, asOf: hotRow.date, provisional: false, scope: 'TWSE listed only', ...extra, latest: hotLatest, rows: [{ ...earlier, ratioPct: 50, relHot: false, hotWeeks: 45 }, hotLatest] };
describe('融資溫度新增欄位', () => {
  it('同時接受舊檔（無新欄位）與新檔', () => {
    expect(parseMarginTemperature(payload).error).toBeUndefined();
    const parsed = parseMarginTemperature(hotPayload);
    expect(parsed.error).toBeUndefined();
    expect(parsed.data).toMatchObject({ firstDate: '2007-01-05', hotEpisodes: 10 });
    expect(parsed.data?.latest).toMatchObject({ ratioPct: 62.4, relHot: false, hotWeeks: 46 });
    const insufficient = { ...hotLatest, ratioPct: null };
    expect(parseMarginTemperature({ ...hotPayload, latest: insufficient, rows: [insufficient] }).error).toBeUndefined();
  });
  it('拒絕與門檻或連續週數不一致的新欄位', () => {
    const badRel = { ...hotLatest, ratioPct: 95, relHot: false };
    expect(parseMarginTemperature({ ...hotPayload, latest: badRel, rows: [earlier, badRel] }).error).toBeTruthy();
    const badWeeks = { ...hotLatest, hotWeeks: 0 };
    expect(parseMarginTemperature({ ...hotPayload, latest: badWeeks, rows: [earlier, badWeeks] }).error).toBeTruthy();
    expect(parseMarginTemperature({ ...hotPayload, hotEpisodes: -1 }).error).toBeTruthy();
  });
  it('過熱且有新欄位：顯示連續週數、段數與相對大盤分位', () => {
    const html = bodyOf(hotPayload);
    expect(html).toContain('過熱提醒');
    expect(html).toContain('已連續 46 週（2007 年以來第 10 段）');
    expect(html).toContain('相對大盤分位');
    expect(html).toContain('P62 · 融資增速未明顯高於大盤');
    expect(html).toContain('不是市場總市值');
  });
  it('相對分位達 90 時顯示融資增速高於大盤', () => {
    const rel = { ...hotLatest, ratioPct: 93.9, relHot: true };
    expect(bodyOf({ ...hotPayload, latest: rel, rows: [rel] })).toContain('P93 · 融資增速高於大盤');
  });
  it('過熱但為舊檔：不顯示連續週數與相對分位，其餘照常', () => {
    const old = { ...hotRow };
    const html = bodyOf({ version: 1, asOf: old.date, provisional: false, scope: 'TWSE listed only', latest: old, rows: [old] });
    expect(html).toContain('過熱提醒');
    expect(html).not.toContain('已連續');
    expect(html).not.toContain('相對大盤分位');
    expect(html).not.toContain('不是市場總市值');
    expect(html).toContain('融資餘額會隨大盤規模一起成長');
  });
  it('過熱但缺少段數資訊：只省略不可得的片段', () => {
    const html = bodyOf({ ...hotPayload, firstDate: undefined, hotEpisodes: undefined });
    expect(html).toContain('已連續 46 週');
    expect(html).not.toContain('年以來');
  });
  it('未過熱：不顯示連續週數，仍顯示相對大盤分位', () => {
    const calm = { ...latest, ratioPct: 40.6, relHot: false, hotWeeks: 0 };
    const html = bodyOf({ ...payload, ...extra, latest: calm, rows: [{ ...first, ratioPct: 41, relHot: false, hotWeeks: 0 }, calm] });
    expect(html).toContain('中性');
    expect(html).not.toContain('已連續');
    expect(html).toContain('P40 · 融資增速未明顯高於大盤');
  });
});
