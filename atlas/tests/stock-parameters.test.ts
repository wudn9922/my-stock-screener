import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stockParameterMap, stockParameterRows, parameterTickerKey } from '../src/cloud/stockParameters';
import { reconcileParameterIndicators } from '../src/app/stockParameterIndicators';
import { miniChartStart } from '../src/ui/miniChartRange';
import type { ParameterStock } from '../src/cloud/settingsApi';
import type { IndicatorInstance } from '../src/indicators/IndicatorRegistry';

const mocked = vi.hoisted(() => ({ api: vi.fn(), changed: vi.fn(), context: 'account-a' }));
vi.mock('../src/cloud/settingsApi', () => ({ settingsApi: mocked.api }));
vi.mock('../src/cloud/useStockParameters', () => ({ stockParametersChanged: mocked.changed }));
vi.mock('../src/auth/storage', () => ({ getStorageContextKey: () => mocked.context }));
import { saveStockParameters } from '../src/cloud/saveStockParameters';

const grouped: ParameterStock = { id: 12, ticker: '2330', group_id: 'tw_g1', ma1: 29, ma2: 34, ma3: 73, ma4: 0 };
const duplicate: ParameterStock = { ...grouped, id: 30, ticker: '2330.TW', group_id: 'ungrouped', ma1: 20, ma2: 50, ma3: 200 };
beforeEach(() => { mocked.api.mockReset(); mocked.changed.mockReset(); mocked.context = 'account-a'; });

describe('one stock parameter source', () => {
  it('preserves the original grouped record regardless of response order, normalized ticker, or newer dormant duplicate', () => {
    for (const rows of [[duplicate, grouped], [grouped, duplicate]]) {
      expect(stockParameterRows(rows, '2330.TW')[0]).toBe(grouped);
      expect(stockParameterMap(rows)).toEqual({ '2330.TW': [29, 34, 73] });
    }
    expect(stockParameterRows([grouped, { ...grouped, id: 11, group_id: 'tw_g2' }], '2330')[0].id).toBe(11);
    expect(parameterTickerKey(' brk.b ')).toBe('BRK-B');
    expect(parameterTickerKey('6488.TWO')).toBe('6488.TWO');
    expect(stockParameterRows([{ ...grouped, group_id: 'admin_index' }], '2330')).toEqual([]);
    expect(stockParameterMap([duplicate])).toEqual({ '2330.TW': [20, 50, 200] });
  });

  it('updates the original id and group first, then deletes only the legacy dormant duplicate', async () => {
    mocked.api.mockImplementation(async action => action === 'stocks.list' ? [duplicate, grouped] : [{ id: 12 }]);
    await saveStockParameters('2330.TW', [13, 34], grouped);
    expect(mocked.api.mock.calls).toEqual([
      ['stocks.list'],
      ['stocks.save', { id: 12, ticker: '2330', groupId: 'tw_g1', mas: [13, 34] }],
      ['stocks.delete', { id: 30 }],
    ]);
    expect(mocked.changed).toHaveBeenCalledOnce();
  });

  it('never deletes a duplicate after a failed primary write; a failed cleanup is explicit and retryable', async () => {
    mocked.api.mockImplementation(async action => {
      if (action === 'stocks.list') return [duplicate, grouped];
      throw Error('offline');
    });
    await expect(saveStockParameters('2330.TW', [13], grouped)).rejects.toThrow('offline');
    expect(mocked.api.mock.calls.map(call => call[0])).toEqual(['stocks.list', 'stocks.save']);
    mocked.api.mockImplementation(async action => {
      if (action === 'stocks.list') return [duplicate, grouped];
      if (action === 'stocks.delete') throw Error('offline');
      return [{ id: 12 }];
    });
    await expect(saveStockParameters('2330.TW', [13], grouped)).rejects.toThrow('分組參數已儲存');
    expect(mocked.changed).toHaveBeenCalledOnce();
  });

  it('creates a dormant row only for a genuinely new stock; moving an existing dormant row keeps its id', async () => {
    mocked.api.mockResolvedValueOnce([]).mockResolvedValueOnce([{ id: 1 }]);
    await saveStockParameters('NVDA', [13, 34]);
    expect(mocked.api).toHaveBeenLastCalledWith('stocks.save', { ticker: 'NVDA', groupId: 'ungrouped', mas: [13, 34] });
    mocked.api.mockReset().mockResolvedValueOnce([duplicate]).mockResolvedValueOnce([{ id: 30 }]);
    await saveStockParameters('2330.TW', [20, 50], duplicate, 'tw_g2');
    expect(mocked.api).toHaveBeenLastCalledWith('stocks.save', { id: 30, ticker: '2330.TW', groupId: 'tw_g2', mas: [20, 50] });
  });

  it('refuses stale rows or a newly discovered record before overwriting, and stops cleanup across an account change', async () => {
    mocked.api.mockResolvedValue([{ ...grouped, ma1: 99 }]);
    await expect(saveStockParameters('2330', [13], grouped)).rejects.toThrow('已變更');
    expect(mocked.api).toHaveBeenCalledOnce();
    mocked.api.mockReset().mockResolvedValue([grouped]);
    await expect(saveStockParameters('2330', [13])).rejects.toThrow('確認原有參數');
    expect(mocked.api).toHaveBeenCalledOnce();
    mocked.api.mockReset().mockImplementation(async action => {
      if (action === 'stocks.list') return [grouped, duplicate];
      mocked.context = 'account-b';
      return [];
    });
    await expect(saveStockParameters('2330', [13], grouped)).rejects.toThrow('尚未清除');
    expect(mocked.api.mock.calls.map(call => call[0])).toEqual(['stocks.list', 'stocks.save']);
  });

  it('hydrates saved daily SMAs with 2px width, deduplicates existing lines and preserves manual, locked and other-timeframe indicators', () => {
    const manual: IndicatorInstance = { id: 'manual', symbol: '2330.TW', type: 'SMA', period: 29, source: 'close', visible: false, locked: true, lineWidth: 3, color: '#ffffff', scope: { timeframe: '1D' } };
    const weekly = { ...manual, id: 'weekly', scope: { timeframe: '1W' as const } };
    const initial = reconcileParameterIndicators('2330.TW', [manual, weekly], [29, 34, 73]);
    expect(initial).toHaveLength(4);
    expect(initial[0]).toEqual(manual); expect(initial[1]).toEqual(weekly);
    expect(initial.slice(2)).toEqual([34, 73].map(period => expect.objectContaining({ period, lineWidth: 2, scope: { timeframe: '1D' } })));
    expect(reconcileParameterIndicators('2330.TW', initial, [29, 34, 73])).toEqual(initial);
    expect(reconcileParameterIndicators('2330.TW', initial, [13])).toEqual([manual, weekly, expect.objectContaining({ period: 13 })]);
    expect(reconcileParameterIndicators('2330.TW', initial, [])).toEqual([manual, weekly]);
    expect(reconcileParameterIndicators('2330.TW', [], [0, NaN, 1.5, -1, 1001])).toEqual([]);
  });

  it('uses six calendar months from the last bar, handles month-end and shorter histories, and retains warmup bars', () => {
    const bars = Array.from({ length: 366 }, (_, i) => ({ time: Date.UTC(2026, 0, i + 1, 12) / 1000 }));
    const throughOct = bars.filter(bar => bar.time <= Date.UTC(2026, 9, 29, 12) / 1000);
    expect(new Date(throughOct[miniChartStart(throughOct, 6)].time * 1000).toISOString().slice(0, 10)).toBe('2026-04-29');
    const throughAug = bars.filter(bar => bar.time <= Date.UTC(2026, 7, 31, 12) / 1000);
    expect(new Date(throughAug[miniChartStart(throughAug, 6)].time * 1000).toISOString().slice(0, 10)).toBe('2026-02-28');
    expect(miniChartStart(throughOct.slice(-30), 6)).toBe(0);
    expect(miniChartStart(throughOct)).toBe(throughOct.length - 130);
    expect(miniChartStart([], 6)).toBe(0);
    const dst = ['2026-08-28T13:30:00Z', '2026-08-31T13:30:00Z', '2027-02-28T14:30:00Z'].map(date => ({ time: Date.parse(date) / 1000 }));
    expect(miniChartStart(dst, 6)).toBe(0);
  });
});
