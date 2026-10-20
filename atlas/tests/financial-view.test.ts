import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseFinancials } from '../src/fundamentals/FinancialHistory';
import { barLayout, barPath, labelPlan, labelWidth } from '../src/ui/financials/barGeometry';
import {
  buildView,
  cagrText,
  depthText,
  digitsFor,
  formatValue,
  moneyScale,
  oiFieldText,
  provenanceText,
  revisionText,
  unitText,
  yoyText,
} from '../src/ui/financials/financialsView';

// Synthetic fixtures (made-up numbers), see tests/fixtures/financials/.
const fixture = (name: string) => {
  const result = parseFinancials(JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials', name), 'utf8')));
  if (result.status !== 'ok') throw new Error(name);
  return result.data;
};

describe('financials view model', () => {
  it('describes operating-income precedence without assigning a source to a mixed-period cell', () => {
    expect(oiFieldText({ oiField: 'operatingIncome', value: 5, basis: 'reported', formula: '', inputs: [], notes: [] }))
      .toBe('營業利益優先採列示欄，缺漏時採一般欄');
    expect(oiFieldText({ oiField: 'operatingIncomeReported', value: 5, basis: 'reported', formula: '', inputs: [], notes: [] }))
      .toBe('營業利益優先採列示欄，缺漏時採一般欄');
    expect(oiFieldText(undefined)).toBeNull();
  });

  it('charts the latest 10 years / 20 quarters and keeps every period for the table', () => {
    const us = fixture('us-full.json');
    const annual = buildView(us, 'annual');
    expect(annual.metrics.revenue.window).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const quarterly = buildView(us, 'quarterly');
    expect(quarterly.metrics.revenue.window).toEqual(Array.from({ length: 15 }, (_, index) => index));
    expect(quarterly.metrics.revenue.values).toHaveLength(15);
    // Quarterly CAGR compares the same fiscal quarter (FY2023 Q3 → FY2025 Q3).
    expect(quarterly.metrics.revenue.cagr).toMatchObject({ from: '2022-06-25', to: '2025-06-28' });
    // Margins get a window average instead of a CAGR.
    expect(annual.metrics.grossMargin.cagr).toBeNull();
    expect(annual.metrics.grossMargin.average!.count).toBe(10);
  });

  it('latest value skips trailing gaps; loss-maker YoY and CAGR read 轉盈 / 期間有虧損', () => {
    const us = buildView(fixture('us-full.json'), 'annual');
    expect(us.metrics.interestCoverage.latestIndex).toBe(8);
    const loss = buildView(fixture('loss-maker.json'), 'annual');
    expect(yoyText(loss.metrics.netIncome.yoy, 'money')).toEqual({ text: '轉盈', tone: 'up' });
    expect(cagrText(loss.metrics.netIncome.cagr).text).toBe('期間有虧損，無法計算 CAGR');
    expect(cagrText(loss.metrics.revenue.cagr).text).toMatch(/^CAGR \+\d+\.\d%（4\.0年）$/);
    const bank = buildView(fixture('bank.json'), 'annual');
    expect(bank.metrics.grossMargin.latestIndex).toBeNull();
  });

  it('chip texts: growth in %, margins in pp, multiples in 倍', () => {
    expect(yoyText({ value: 2.06, mode: 'growth', label: null, baseIndex: 0 }, 'money')).toEqual({ text: 'YoY +2.1%', tone: 'up' });
    expect(yoyText({ value: -1.24, mode: 'diff', label: null, baseIndex: 0 }, 'pct')).toEqual({ text: 'YoY -1.2pp', tone: 'down' });
    expect(yoyText({ value: 0.1, mode: 'diff', label: null, baseIndex: 0 }, 'times').text).toBe('YoY +0.10倍');
    expect(yoyText({ value: -150, mode: 'growth', label: '轉虧', baseIndex: 0 }, 'money').text).toBe('轉虧 -150.0%');
    expect(yoyText(null, 'money').text).toBe('YoY —');
  });

  it('amounts in 億, or 兆 once a value reaches 1 兆, in the statement currency', () => {
    expect(moneyScale([3.93e11, null])).toEqual({ divisor: 1e8, unit: '億' });
    expect(moneyScale([2.894e12])).toEqual({ divisor: 1e12, unit: '兆' });
    expect(formatValue(3.93e11, 'money', moneyScale([3.93e11]))).toBe('3,930');
    expect(formatValue(2.894e12, 'money', moneyScale([2.894e12]))).toBe('2.89');
    expect(formatValue(-0.04, 'pct', moneyScale([]))).toBe('0.0'); // never "-0.0"
    expect(unitText('money', 'USD')).toBe('億美元');
    expect(unitText('money', 'TWD', { divisor: 1e12, unit: '兆' })).toBe('兆元');
    expect(unitText('money', 'DKK')).toBe('億 DKK');
    expect(unitText('eps', 'TWD')).toBe('元');
    expect(digitsFor([3930, 2140], 'money')).toBe(0);
    expect(digitsFor([8, 51], 'money')).toBe(1);
    expect(digitsFor([0.45, 1.2], 'times')).toBe(2);
  });

  it('honest depth and provenance notes', () => {
    const us = fixture('us-full.json');
    expect(depthText(us, 'annual')).toBe('含 SEC 申報歷史，共約 10 年（FY2015 起）；之後每季自動累積。');
    expect(provenanceText(us, 'annual')).toBe('資料來源：SEC EDGAR（FY2015–FY2020） ＋ Yahoo Finance（FY2021–FY2024）');
    const tsmc = fixture('tsmc-twd.json');
    expect(depthText(tsmc, 'annual')).toBe('目前約 4 年（FY2021 起）；Yahoo 只回溯約 4 年，之後每季自動累積。');
    expect(depthText(tsmc, 'quarterly')).toBe('目前 10 季（FY2023 Q1 起）；之後每季自動累積。');
    expect(provenanceText(tsmc, 'quarterly')).toBe('資料來源：Yahoo Finance');
    expect(revisionText(us, us.quality.revisions[0]!)).toBe('FY2020 營收：2,760.00億 → 2,815.00億（保留 SEC 值，Yahoo 不同）');
  });
});

describe('bar geometry', () => {
  it('draws negatives below a zero baseline that sits inside the plot', () => {
    const layout = barLayout([10, -5, null, 20], 200, { plot: 120, labelPad: 16, axis: 18 });
    expect(layout.top).toBe(16);
    expect(layout.zeroY).toBeCloseTo(16 + (20 / 25) * 120);
    expect(layout.bars[0]!.yEnd).toBeLessThan(layout.zeroY);
    expect(layout.bars[1]!.negative).toBe(true);
    expect(layout.bars[1]!.yEnd).toBeCloseTo(136);
    expect(layout.bars[2]!.yEnd).toBe(layout.zeroY);
    expect(layout.height).toBe(136 + 16 + 18);
    expect(barPath(layout.bars[2]!, layout.zeroY)).toBe('');
    // Rounded at the data end only: the path starts and ends on the baseline.
    expect(barPath(layout.bars[1]!, layout.zeroY)).toMatch(new RegExp(`^M[\\d.]+,${layout.zeroY.toFixed(1).replace(/\.0$/, '')}`));
  });

  it('all-positive charts keep the baseline at the bottom; slots split the width', () => {
    const layout = barLayout([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 330);
    expect(layout.zeroY).toBe(layout.bottom);
    expect(layout.bars[9]!.slotX).toBeCloseTo(297);
    expect(layout.bars[0]!.width).toBeCloseTo(330 / 10 * 0.62);
  });

  it('labels every annual bar when they fit, the extremes otherwise; quarterly only latest / max / min', () => {
    const values = [3930, 2140, 2280, 2640, 2590, 2760, 3620, 3920, 3850, 3930];
    const texts = values.map((value) => value.toLocaleString('en-US'));
    expect(labelWidth('3,930', 10)).toBeLessThan(33);
    expect(labelPlan(texts, 33, 'all', values, 9).indices.size).toBe(10);
    const wide = ['-12,345', '12,345', '22,345'];
    expect(labelPlan(wide, 30, 'all', [-12345, 12345, 22345], null).indices).toEqual(new Set([2, 0]));
    const quarterly = labelPlan(['1', '5', '2', '9', '3'], 28, 'extremes', [1, 5, 2, 9, 3], 1);
    expect([...quarterly.indices].sort()).toEqual([0, 1, 3, 4]);
  });
});
