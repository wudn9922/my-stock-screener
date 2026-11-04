import { describe, expect, it } from 'vitest';
import { groupItemSchema } from '../src/report/schema';
import { createWatchlist, evaluateWatchlist, visitWatchlist } from '../src/watchlists/model';
import { explainWatchlistChanges } from '../src/watchlists/reasons';
import { parseFilters, serializeFilters } from '../src/pages/screenerFilters';
import { parseGrowthFile } from '../src/fundamentals/GrowthProvider';
import { readFileSync } from 'node:fs';

const row = (symbol: string, metrics: Record<string, unknown> = {}) => groupItemSchema.parse({ symbol, name: symbol, close: 100, maList: [20], maValues: { '20': 99 }, metrics: { rsRank: 90, volumeAvg20: 20_000, ema20: 1, ...metrics } });
const listOf = (filters = '', market: 'TW' | 'US' = 'US') => createWatchlist({ name: '測試清單', market, group: `universe-${market.toLowerCase()}`, filters });
const visit = (list: ReturnType<typeof listOf>, previous: string[], current: string[]) => visitWatchlist(
  visitWatchlist(list, previous.map(symbol => ({ symbol, name: symbol })), '2026-10-29', 1).list,
  current.map(symbol => ({ symbol, name: symbol })), '2026-10-30', 2,
);

describe('watchlist transition evidence', () => {
  it('explains current pass/fail with actual values and keeps the saved/cloud schema unchanged', () => {
    const list = listOf('rs:80;vol:10000');
    const items = [row('AAPL', { rsRank: 70 }), row('NVDA')];
    const members = evaluateWatchlist(list, items, undefined);
    const observed = visit(list, ['AAPL'], members.map(member => member.symbol));
    const before = JSON.stringify(observed.list);
    const reasons = explainWatchlistChanges(observed, list, items, { market: 'US' });
    expect(reasons.entered.NVDA.title).toContain('符合 2 項');
    expect(reasons.entered.NVDA.conditions.every(condition => condition.passed === true)).toBe(true);
    expect(reasons.exited.AAPL.title).toContain('RS');
    expect(reasons.exited.AAPL.conditions.find(condition => condition.label.startsWith('RS'))).toMatchObject({ passed: false, value: 'RS 百分位 70' });
    expect(JSON.stringify(observed.list)).toBe(before);
    expect(before).not.toContain('conditions');
  });
  it('uses fixed MA distance and Taiwan lots, keeping inclusive and strict comparison boundaries', () => {
    const list = listOf('ema20:-1:1;rs3:0;vol:10000', 'TW');
    const items = [row('2330.TW', { ema20: 10, rs63: 0 }), row('2317.TW', { rs63: 1 })];
    const fixedMAs = { '2330.TW': { period: 50, distance: 1 } };
    const reasons = explainWatchlistChanges(visit(list, ['2330.TW'], ['2317.TW']), list, items, { market: 'TW', fixedMAs });
    const checks = reasons.exited['2330.TW'].conditions;
    expect(checks.find(check => check.label.includes('乖離'))).toMatchObject({ passed: true, value: expect.stringContaining('MA50') });
    expect(checks.find(check => check.label.includes('3月'))?.passed).toBe(false);
    expect(checks.find(check => check.label.startsWith('20 日均量'))?.value).toBe('20 日平均成交量 20 張');
  });
  it('distinguishes source removal, manual removal and lists without numeric rules', () => {
    const rules = listOf();
    expect(explainWatchlistChanges(visit(rules, ['AAPL'], ['NVDA']), rules, [row('NVDA')], { market: 'US' }).exited.AAPL.title).toContain('來源名單');
    const manual = createWatchlist({ name: '手動', market: 'US', group: 'universe-us', filters: '', kind: 'manual', symbols: ['NVDA'] });
    const reasons = explainWatchlistChanges(visit(manual, ['AAPL'], ['NVDA']), manual, [row('AAPL'), row('NVDA')], { market: 'US' });
    expect(reasons.exited.AAPL.title).toContain('未列入手動');
    expect(reasons.entered.NVDA.title).toContain('本次在手動');
  });
  it('does not treat an individual missing metric as a passing condition', () => {
    const list = listOf('rs:80');
    const items = [row('AAPL', { rsRank: null }), row('NVDA')];
    const reasons = explainWatchlistChanges(visit(list, ['AAPL'], ['NVDA']), list, items, { market: 'US' });
    expect(reasons.exited.AAPL.conditions[0]).toMatchObject({ passed: false, value: 'RS 百分位 未提供' });
    expect(() => evaluateWatchlist(list, [row('AAPL', { rsRank: null })], undefined)).toThrow();
  });
  it('explains repeated growth gates separately using the original shared evaluator', () => {
    const growth = parseGrowthFile(JSON.parse(readFileSync('tests/fixtures/screener/us-growth.json', 'utf8')))!;
    const record = growth.items.get('AAPL')!;
    const context = { market: 'US' as const, growth: { ...growth, items: new Map([['AAPL', { ...record, epsYoY: [20, 5] }]]) } };
    const state = parseFilters('');
    state.extraGates = [
      { kind: 'eps', gate: { quarters: 2, min: 10, share: 100 } },
      { kind: 'eps', gate: { quarters: 1, min: 10, share: 100 } },
      { kind: 'eps', gate: { quarters: 1, min: 30, share: 100 } },
    ];
    const list = listOf(serializeFilters(state));
    const items = [row('AAPL')];
    expect(evaluateWatchlist(list, items, context.growth)).toEqual([]);
    const checks = explainWatchlistChanges(visit(list, ['AAPL'], []), list, items, context).exited.AAPL.conditions;
    expect(checks.map(check => check.passed)).toEqual([false, true, false]);
    expect(checks[0].value).toContain('1／2');
  });
  it('labels missing growth retained by policy without claiming positive growth or past crossings', () => {
    const growth = parseGrowthFile(JSON.parse(readFileSync('tests/fixtures/screener/us-growth.json', 'utf8')))!;
    const context = { market: 'US' as const, growth: { ...growth, items: new Map([['NVDA', growth.items.get('NVDA')!]]) } };
    const list = listOf('eps:4:10:100;nd:keep');
    const items = [row('AAPL'), row('NVDA')];
    const reason = explainWatchlistChanges(visit(list, [], ['AAPL']), list, items, context).entered.AAPL;
    expect(reason.conditions[0].passed).toBe(true);
    expect(reason.conditions[0].value).toContain('資料不足，依設定保留');
  });
});
