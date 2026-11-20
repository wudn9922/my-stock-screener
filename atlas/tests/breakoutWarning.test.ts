import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { parseBreakoutWarning } from '../src/report/breakoutWarning';
import { WarningBody } from '../src/ui/BreakoutWarningCard';

const params = { windowDays: 21, observationDays: 5, minSignals: 15, rule: 'hold5', percentileWindow: 504, percentileMinValid: 252,
  lowPercentile: 10, horizonDays: 40, drawdown: 0.07, episodeGapDays: 20, jumpLimit: 0.3 };
const rows = [
  { date: '2026-11-17', rate: 0.52, n: 600, pct: 30.2, low: false },
  { date: '2026-11-18', rate: 0.40, n: 650, pct: 4.1, low: true },
  { date: '2026-11-19', rate: 0.385, n: 678, pct: 0.6, low: true },
];
const stats = { episodes: 11, hits: 5, avgDrawdown: -0.084, baseHitRate: 0.2445, baseAvgDrawdown: -0.052, pending: 1,
  list: [{ date: '2022-05-13', rate: 0.5723, pct: 8.6, drawdown: -0.1188, hit: true }, { date: '2026-11-03', rate: 0.41, pct: 3, drawdown: null, hit: null }] };
const tw = { asOf: '2026-11-19', index: { symbol: '^TWII', name: '加權指數' }, stocks: 1988, failedStocks: 0, note: '台股只採用突破20EMA。',
  methods: { ema20: { label: '突破20EMA', rows, latest: { ...rows[2]!, lowDays: 2 }, stats } },
  closes: rows.map((r, i) => ({ date: r.date, close: 38000 + i * 100 })) };
const payload = { version: 1, generatedAt: '2026-11-20T00:00:00Z', params, markets: { tw } };

describe('突破達成率警示資料契約', () => {
  it('接受只含部分口徑與部分市場的檔案', () => {
    const result = parseBreakoutWarning(payload);
    expect(result.error).toBeUndefined();
    expect(result.data?.markets.us).toBeUndefined();
    expect(result.data?.markets.tw?.methods.ema20?.latest.lowDays).toBe(2);
  });
  it('拒絕狀態與門檻不一致、日期錯亂', () => {
    const bad = (method: object) => ({ ...payload, markets: { tw: { ...tw, methods: { ema20: { ...tw.methods.ema20, ...method } } } } });
    expect(parseBreakoutWarning(bad({ rows: [rows[0], { ...rows[1], low: false }, rows[2]] })).error).toBeTruthy();
    expect(parseBreakoutWarning(bad({ rows: [rows[1], rows[0], rows[2]] })).error).toBeTruthy();
    expect(parseBreakoutWarning(bad({ latest: { ...rows[2], lowDays: 0 } })).error).toBeTruthy();
    expect(parseBreakoutWarning(bad({ latest: { ...rows[1], lowDays: 1 } })).error).toBeTruthy();
  });
});

describe('突破達成率警示卡片', () => {
  it('顯示偏低狀態、歷史命中與風險說明', () => {
    const data = parseBreakoutWarning(payload).data!;
    const html = renderToStaticMarkup(createElement(WarningBody, { data, market: data.markets.tw! }));
    expect(html).toContain('▼ 偏低提醒 · 已連續 2 個交易日');
    expect(html).toContain('38.5%');
    expect(html).toContain('P0.6');
    expect(html).toContain('過去 11 次提醒，40 個交易日內大盤跌 7% 以上 <b>5 次</b>');
    expect(html).toContain('任一天約 24%');
    expect(html).toContain('另有 1 次尚在觀察中');
    expect(html).toContain('不是賣出訊號');
    expect(html).toContain('台股只採用突破20EMA。');
    expect(html).toContain('觀察中');
  });
});
