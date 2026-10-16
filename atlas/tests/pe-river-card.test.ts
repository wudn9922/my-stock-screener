import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { DAY_SECONDS, buildPeRiver, dateToSec, type PeRiverModel, type PriceBar } from '../src/fundamentals/peRiver';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import { PeRiverPanel, type PriceLoad } from '../src/ui/financials/PeRiverCard';
import { BAND_COLORS, buildRiverChartData } from '../src/ui/financials/peRiverChartData';
import { coverageText, delayText, epsBasisText, excludedText, percentileText, premiumText, shortWarning, unavailableDetail } from '../src/ui/financials/peRiverText';

const fixture = (name: string) => {
  const result = parseFinancials(JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')));
  if (result.status !== 'ok') throw new Error(name);
  return result.data;
};
function weekly(from: string, to: string, price: (i: number) => number): PriceBar[] {
  const bars: PriceBar[] = [];
  let i = 0;
  for (let t = dateToSec(from); t <= dateToSec(to); t += 7 * DAY_SECONDS) bars.push({ time: t + 13.5 * 3600, close: price(i++) });
  return bars;
}
const ok = (bars: PriceBar[], priceBasis: string | undefined = 'split-adjusted'): PriceLoad => ({
  status: 'ok',
  bars,
  priceBasis,
  asOfSec: dateToSec('2025-10-01'),
  stale: false,
});
// The card is folded by default and its body mounts only once opened, so the states are rendered open here.
const render = (history: ReturnType<typeof fixture>, load: PriceLoad) =>
  renderToStaticMarkup(createElement(PeRiverPanel, { history, load, onRetry: () => undefined, defaultOpen: true }));

describe('PeRiverPanel states', () => {
  const us = fixture('us-full.json');
  const bars = weekly('2016-10-10', '2025-09-29', (i) => 30 + i * 0.3 + Math.sin(i / 7) * 6);

  it('folded by default: title and one-line summary show, the chart body does not mount', () => {
    const html = renderToStaticMarkup(createElement(PeRiverPanel, { history: us, load: ok(bars), onRetry: () => undefined }));
    expect(html).toContain('data-state="ok"');
    expect(html).toContain('本益比河流圖');
    expect(html).toMatch(/目前本益比 [\d.]+x · 歷史第 \d+ 百分位/);
    expect(html).not.toContain('目前本益比</span>');
    expect(html).not.toContain('pe-river-legend');
  });

  it('loading: a status placeholder, no numbers', () => {
    const html = render(us, { status: 'loading' });
    expect(html).toContain('本益比河流圖');
    expect(html).toContain('data-state="loading"');
    expect(html).toContain('載入股價與本益比河流中');
    expect(html).not.toContain('NaN');
  });

  it('error: says what failed, keeps the page alive and offers a retry', () => {
    const html = render(us, { status: 'error', message: '暫時無法取得 AAPL 行情，請稍後再試' });
    expect(html).toContain('data-state="error"');
    expect(html).toContain('股價資料載入失敗');
    expect(html).toContain('暫時無法取得 AAPL 行情');
    expect(html).toContain('重新載入');
  });

  it('ok: headline, percentile, premium, five legend rows and the full basis block', () => {
    const html = render(us, ok(bars));
    expect(html).toContain('data-state="ok"');
    expect(html).toContain('目前本益比');
    expect(html).toMatch(/歷史第 \d+ 百分位/);
    expect(html).toMatch(/相對歷史中位數（[\d.]+x）(溢價|折價) [\d.]+%/);
    expect(html.match(/<li>/g)).toHaveLength(5);
    for (const p of [10, 30, 50, 70, 90]) expect(html).toContain(`P${p} · `);
    expect(html).toContain('資料日');
    expect(html).toContain('股價至 2025-10-01');
    expect(html).toContain('EPS 口徑');
    expect(html).toContain('有效樣本');
    expect(html).toContain('涵蓋');
    expect(html).toContain('歷史本益比不代表未來，僅供研究參考');
    expect(html).not.toContain('NaN');
    expect(html).not.toContain('undefined');
  });

  it('unavailable (price basis unknown): explains instead of drawing a wrong line', () => {
    const html = render(us, ok(bars, 'unknown'));
    expect(html).toContain('data-state="unavailable"');
    expect(html).toContain('data-reason="price-basis"');
    expect(html).toContain('未確認已還原股票分割');
  });

  it('unavailable (too little history): the agreed message plus what there was', () => {
    const html = render(us, ok(weekly('2025-07-07', '2025-09-29', () => 200)));
    expect(html).toContain('歷史資料不足，無法畫出本益比河流');
    expect(html).toContain('data-reason="insufficient"');
    expect(html).toMatch(/有效本益比 \d+ 週（至少需要 8 週）/);
    expect(html).toContain('歷史本益比不代表未來');
  });

  it('unavailable (foreign issuer): ADR price vs ordinary-share EPS', () => {
    const html = render(fixture('short-history.json'), ok(bars));
    expect(html).toContain('data-reason="foreign"');
    expect(html).toContain('每 ADR');
  });

  it('a loss-making latest period shows no current P/E but still the river', () => {
    const loss = fixture('loss-maker.json');
    const html = render(loss, ok(weekly('2020-01-06', '2025-03-31', (i) => 5 + (i % 40) * 0.1)));
    expect(html).not.toContain('NaN');
    expect(html).toContain('本益比河流圖');
  });
});

describe('text helpers', () => {
  const model = (): PeRiverModel => {
    const result = buildPeRiver(fixture('us-full.json'), weekly('2016-10-10', '2025-09-29', (i) => 30 + i * 0.3 + Math.sin(i / 7) * 6), {
      asOfSec: dateToSec('2025-10-01'),
      priceBasis: 'split-adjusted',
    });
    if (result.status !== 'ok') throw new Error(result.message);
    return result.model;
  };
  it('describes the EPS basis for TTM-only, annual-only and mixed series', () => {
    const base = model();
    expect(epsBasisText({ ...base, basisCounts: { ttm: 10, annual: 0 }, usedBasic: false })).toBe('近四季單季稀釋 EPS合計（TTM）');
    expect(epsBasisText({ ...base, basisCounts: { ttm: 0, annual: 10 }, usedBasic: false })).toContain('年度稀釋 EPS');
    const mixed = epsBasisText({ ...base, basisCounts: { ttm: 7, annual: 3 }, usedBasic: true });
    expect(mixed).toContain('TTM）7 週');
    expect(mixed).toContain('年度 EPS 3 週');
    expect(mixed).toContain('基本 EPS');
  });
  it('states coverage, exclusions, delays and the short-history warning', () => {
    const base = model();
    expect(coverageText(base)).toContain(`有效樣本 ${base.validCount} 週`);
    expect(excludedText({ ...base, excluded: { 'no-eps': 0, loss: 0, outlier: 0 } })).toBe('');
    expect(excludedText({ ...base, excluded: { 'no-eps': 3, loss: 2, outlier: 1 } })).toBe('不計入：EPS ≤ 0（虧損）2 週、本益比 > 200 倍 1 週、尚無已公布 EPS 3 週');
    expect(delayText('US')).toContain('45 天');
    expect(delayText('TW')).toContain('90 天');
    expect(shortWarning({ ...base, short: false })).toBeNull();
    expect(shortWarning({ ...base, short: true, coverageYears: 2.2, epsPeriodCount: 4 })).toContain('涵蓋僅 2.2 年');
    expect(shortWarning({ ...base, short: true, coverageYears: 2.2, epsPeriodCount: 4 })).toContain('EPS 僅 4 組');
  });
  it('words the percentile and the premium / discount', () => {
    const current = { pe: 30, percentile: 62.6, premiumPct: 12.34, median: 26.7, eps: 5, basis: 'ttm' as const, epsPeriodEnd: '2025-06-28', close: 150, time: 0 };
    expect(percentileText(current)).toBe('歷史第 63 百分位');
    expect(premiumText(current)).toBe('相對歷史中位數（26.7x）溢價 12.3%');
    expect(premiumText({ ...current, premiumPct: -8.04 })).toBe('相對歷史中位數（26.7x）折價 8.0%');
    expect(premiumText({ ...current, premiumPct: 0 })).toContain('相當');
    expect(percentileText({ ...current, percentile: 100 })).toBe('歷史第 100 百分位');
    expect(unavailableDetail('foreign', {})).toBeNull();
    expect(unavailableDetail('insufficient', { validCount: 3, epsPeriodCount: 1, coverageYears: 0.5 })).toBe('有效本益比 3 週（至少需要 8 週） · 不同 EPS 1 組（至少需要 3 組） · 可用歷史 0.5 年');
  });
});

describe('chart data', () => {
  it('builds market-local ascending dates, five shaded bands and gaps where the P/E is invalid', () => {
    const result = buildPeRiver(fixture('us-full.json'), weekly('2014-10-06', '2025-09-29', (i) => 30 + i * 0.3 + Math.sin(i / 7) * 6), {
      asOfSec: dateToSec('2025-10-01'),
      priceBasis: 'split-adjusted',
    });
    if (result.status !== 'ok') throw new Error(result.message);
    const data = buildRiverChartData(result.model, getMarketProfile('AAPL'));
    expect(data.price).toHaveLength(result.model.points.length);
    const days = data.price.map((p) => p.time);
    expect([...days].sort()).toEqual(days);
    expect(new Set(days).size).toBe(days.length);
    expect(days[0]).toBe('2014-10-06');
    expect(data.bands.map((b) => b.p)).toEqual([10, 30, 50, 70, 90]);
    expect(data.bands.map((b) => b.color)).toEqual([10, 30, 50, 70, 90].map((p) => BAND_COLORS[p as 10]));
    for (const band of data.bands) {
      expect(band.label).toMatch(/^\d+\.\dx$/);
      expect(band.points).toHaveLength(days.length);
    }
    // The first weeks precede any published EPS: whitespace, no value.
    expect(data.bands[0]!.points[0]).toEqual({ time: '2014-10-06' });
    expect(data.bands[2]!.points.at(-1)!.value).toBeGreaterThan(0);
    // Higher percentile → higher band price at every valid date.
    data.bands[0]!.points.forEach((point, i) => {
      if (point.value === undefined) return;
      for (let b = 1; b < 5; b++) expect(data.bands[b]!.points[i]!.value!).toBeGreaterThanOrEqual(data.bands[b - 1]!.points[i]!.value!);
    });
  });
  it('uses Taiwan calendar dates for Taiwan weekly bars (Monday 00:00 local is Sunday in UTC)', () => {
    const history = fixture('tsmc-twd.json');
    const bars: PriceBar[] = [];
    for (let t = dateToSec('2021-01-03') + 16 * 3600; t <= dateToSec('2025-09-28') + 16 * 3600; t += 7 * DAY_SECONDS) bars.push({ time: t, close: 600 + (bars.length % 30) });
    const result = buildPeRiver(history, bars, { asOfSec: dateToSec('2025-10-01'), priceBasis: 'split-adjusted' });
    if (result.status !== 'ok') throw new Error(result.message);
    const data = buildRiverChartData(result.model, getMarketProfile('2330.TW'));
    expect(data.price[0]!.time).toBe('2021-01-04');
  });
});
