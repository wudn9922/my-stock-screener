import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { AppStore } from '../src/app/AppStore';
import { IndexedDBStore } from '../src/storage/IndexedDBStore';
import {
  fetchMaRecommendation,
  maRecommendationUrl,
  maSelectorLink,
  parseMaRecommendation,
  recommendedPeriods,
} from '../src/report/maRecommendation';
import { appliedDailySmaPeriods, applyRecommended, missingRecommended, selectableCandidates, toggleRecommended } from '../src/ui/MaRecommendation';

// Shape written by ma-selector scripts/build_recommendations.py (core/recommend.py).
const file = (ticker = '2330.TW') => ({
  version: 1,
  ticker,
  kind: 'SMA',
  asof: '2026-10-17',
  bars: 1210,
  limited: false,
  referenceAsof: '2026-10-01',
  suitability: { short: 66.4, long: 99.4 },
  ranges: [
    { key: 'short', label: '短期', min: 15, max: 33, windowYears: 1, final: 24, best: 70.2, candidates: [
      { period: 20, score: 70.2, sarReturn: 0.12, sarDrawdown: -0.08, sarWinRate: 0.5, sarTrades: 10 },
      { period: 24, score: 69.1, sarReturn: 0.15, sarDrawdown: -0.05, sarWinRate: null, sarTrades: 8 },
    ] },
    { key: 'medium', label: '中期', min: 34, max: 45, windowYears: 2, final: 34, best: 60, candidates: [{ period: 34, score: 60, sarReturn: 0.3, sarDrawdown: -0.1, sarWinRate: 0.4, sarTrades: 12 }] },
    { key: 'long', label: '長期', min: 46, max: 110, windowYears: 3, final: 89, best: 99, candidates: [] },
  ],
});
const response = (status: number, body: string) => Promise.resolve(new Response(body, { status }));

describe('ma-selector recommendations', () => {
  it('reads files next to the screener report and links to the Streamlit app', () => {
    expect(maRecommendationUrl('BRK-B', '/my-stock-screener/atlas/', undefined, 'https://wudn9922.github.io')).toBe(
      'https://wudn9922.github.io/my-stock-screener/report/ma-selector/BRK-B.json',
    );
    expect(maRecommendationUrl('6488.TWO', '/my-stock-screener/atlas/', undefined, 'https://x.io')).toBe('https://x.io/my-stock-screener/report/ma-selector/6488.TWO.json');
    expect(maSelectorLink('2330.TW', 'https://ma.example.app/')).toBe('https://ma.example.app/?ticker=2330.TW');
  });

  it('parses a valid file, tolerates nulls and returns short/medium/long finals', () => {
    const result = parseMaRecommendation(file(), '2330.tw');
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(recommendedPeriods(result.data)).toEqual([24, 34, 89]);
    expect(result.data.ranges[0]!.candidates[1]!.sarWinRate).toBeNull();
    expect(selectableCandidates(result.data.ranges[0]!).map((candidate) => candidate.period)).toEqual([20, 24]);
    expect(selectableCandidates(result.data.ranges[2]!).map((candidate) => candidate.period)).toEqual([89]);
    expect(selectableCandidates({ ...result.data.ranges[0]!, final: 30 }).map((candidate) => candidate.period)).toEqual([20, 24, 30]);
  });

  it('rejects malformed files, missing ranges and another ticker', () => {
    expect(parseMaRecommendation({ ...file(), ranges: file().ranges.slice(0, 2) }, '2330.TW').status).toBe('invalid');
    expect(parseMaRecommendation({ ...file(), kind: 'EMA' }, '2330.TW').status).toBe('invalid');
    expect(parseMaRecommendation(file('AAPL'), '2330.TW')).toMatchObject({ status: 'invalid', message: expect.stringContaining('不符') });
    expect(parseMaRecommendation(null, '2330.TW').status).toBe('invalid');
  });

  it('maps 404, SPA fallback HTML, HTTP and network errors to friendly states', async () => {
    expect((await fetchMaRecommendation('2330.TW', 'u', () => response(404, ''))).status).toBe('missing');
    expect((await fetchMaRecommendation('2330.TW', 'u', () => response(200, '<!doctype html>'))).status).toBe('missing');
    expect(await fetchMaRecommendation('2330.TW', 'u', () => response(503, ''))).toMatchObject({ status: 'error', message: expect.stringContaining('503') });
    expect((await fetchMaRecommendation('2330.TW', 'u', () => Promise.reject(new Error('offline')))).status).toBe('error');
    expect((await fetchMaRecommendation('2330.TW', 'u', () => response(200, JSON.stringify(file())))).status).toBe('ok');
  });

  it('adds only the missing daily SMAs and switches the chart to daily', async () => {
    const store = new AppStore(new IndexedDBStore('ma-reco-' + crypto.randomUUID()));
    await store.initialize();
    store.updateSymbol('2330.TW', (s) => ({ ...s, preferences: { ...s.preferences, timeframe: '1W' } }));
    const base = { symbol: '2330.TW', source: 'close', visible: true, locked: false, lineWidth: 1, widthMode: 'pixels', color: '#ffffff' } as const;
    store.addIndicator({ ...base, id: 'a', type: 'SMA', period: 34, scope: { timeframe: '1D' } });
    store.addIndicator({ ...base, id: 'b', type: 'SMA', period: 24, scope: { timeframe: '1W' } });
    store.addIndicator({ ...base, id: 'c', type: 'EMA', period: 89, scope: { timeframe: '1D' } });
    expect(missingRecommended(store.symbol('2330.TW').indicators, [24, 34, 89])).toEqual([24, 89]);
    expect(applyRecommended(store, '2330.TW', [24, 34, 89])).toEqual([24, 89]);
    const state = store.symbol('2330.TW');
    expect(state.preferences.timeframe).toBe('1D');
    expect(state.indicators.filter((i) => i.type === 'SMA' && i.scope.timeframe === '1D').map((i) => i.period).sort()).toEqual([24, 34, 89]);
    expect(state.indicators.filter((indicator) => indicator.id !== 'a' && indicator.type === 'SMA' && indicator.scope.timeframe === '1D'))
      .toEqual(expect.arrayContaining([expect.objectContaining({ lineWidth: 2, widthMode: 'pixels' })]));
    expect(state.indicators.find((indicator) => indicator.id === 'a')!.lineWidth).toBe(1);
    expect(applyRecommended(store, '2330.TW', [24, 34, 89])).toEqual([]);
    expect(store.symbol('2330.TW').indicators).toHaveLength(5);
  });

  it('applies a non-recommended candidate alone and fixes only the applied daily SMAs', async () => {
    const store = new AppStore(new IndexedDBStore('ma-candidate-' + crypto.randomUUID()));
    await store.initialize();
    expect(appliedDailySmaPeriods(store.symbol('2330.TW').indicators)).toEqual([]);
    const result = parseMaRecommendation(file(), '2330.TW');
    if (result.status !== 'ok') throw new Error('Invalid fixture');
    const candidate = selectableCandidates(result.data.ranges[0]!).find((value) => value.period === 20)!;
    expect(applyRecommended(store, '2330.TW', [candidate.period])).toEqual([20]);
    expect(store.symbol('2330.TW').indicators).toHaveLength(1);
    const applied = store.symbol('2330.TW').indicators[0]!;
    expect(applied).toMatchObject({ type: 'SMA', period: 20, lineWidth: 2, widthMode: 'pixels', scope: { timeframe: '1D' } });
    store.addIndicator({ ...applied, id: 'weekly', period: 89, scope: { timeframe: '1W' } });
    store.addIndicator({ ...applied, id: 'ema', type: 'EMA', period: 34 });
    store.addIndicator({ ...applied, id: 'duplicate', period: 20 });
    expect(appliedDailySmaPeriods(store.symbol('2330.TW').indicators)).toEqual([20]);
    expect(missingRecommended(store.symbol('2330.TW').indicators, recommendedPeriods(result.data))).toEqual([24, 34, 89]);
  });

  it('toggles a candidate off and back on without changing other periods, types, symbols or timeframe scopes', async () => {
    const database = 'ma-toggle-' + crypto.randomUUID();
    const store = new AppStore(new IndexedDBStore(database));
    await store.initialize();
    expect(toggleRecommended(store, '2330.TW', 20)).toEqual({ added: true, removed: 0, locked: 0 });
    const applied = store.symbol('2330.TW').indicators[0]!;
    store.addIndicator({ ...applied, id: 'weekly', scope: { timeframe: '1W' } });
    store.addIndicator({ ...applied, id: 'other-period', period: 34 });
    store.addIndicator({ ...applied, id: 'ema', type: 'EMA' });
    store.addIndicator({ ...applied, id: 'locked', period: 89, locked: true });
    store.addIndicator({ ...applied, id: 'other-symbol', symbol: 'AAPL' });
    const retained = store.symbol('2330.TW').indicators.slice(1);
    const otherSymbol = store.symbol('AAPL');
    store.updateSymbol('2330.TW', (state) => ({ ...state, preferences: { ...state.preferences, timeframe: '1W' } }));
    expect(toggleRecommended(store, '2330.TW', 20)).toEqual({ added: false, removed: 1, locked: 0 });
    expect(store.symbol('2330.TW').indicators).toEqual(retained);
    expect(store.symbol('2330.TW').preferences.timeframe).toBe('1D');
    expect(store.symbol('AAPL')).toEqual(otherSymbol);
    await store.flush();
    const restored = new AppStore(new IndexedDBStore(database));
    await restored.initialize();
    expect(restored.symbol('2330.TW').indicators).toEqual(retained);
    expect(toggleRecommended(restored, '2330.TW', 20)).toEqual({ added: true, removed: 0, locked: 0 });
    expect(appliedDailySmaPeriods(restored.symbol('2330.TW').indicators)).toEqual([20, 34, 89]);
  });

  it('removes only unlocked duplicate SMAs and leaves a locked candidate unchanged on further clicks', async () => {
    const store = new AppStore(new IndexedDBStore('ma-toggle-lock-' + crypto.randomUUID()));
    await store.initialize();
    applyRecommended(store, '2330.TW', [24]);
    const applied = store.symbol('2330.TW').indicators[0]!;
    const locked = { ...applied, id: 'locked', locked: true };
    store.addIndicator(locked);
    expect(toggleRecommended(store, '2330.TW', 24)).toEqual({ added: false, removed: 1, locked: 1 });
    expect(store.symbol('2330.TW').indicators).toEqual([locked]);
    store.updateSymbol('2330.TW', (state) => ({ ...state, preferences: { ...state.preferences, timeframe: '1W' } }));
    const before = store.symbol('2330.TW');
    expect(toggleRecommended(store, '2330.TW', 24)).toEqual({ added: false, removed: 0, locked: 1 });
    expect(store.symbol('2330.TW')).toEqual(before);
  });
});
