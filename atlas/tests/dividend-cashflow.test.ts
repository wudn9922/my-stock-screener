import { describe, expect, it } from 'vitest';
import { dividendCashflow, supplementaryPremium, taipeiDay } from '../src/holdings/dividendCashflow';
import type { Holding } from '../src/holdings/model';
import { cashDividendsSchema } from '../src/events/UpcomingEvents';

const holding: Holding = { id: 'a', accountId: 'b', symbol: '2330.TW', market: 'TW', costCurrency: 'TWD', quantity: '1000', averageCost: '100', source: 'manual', updatedAt: '2026-01-01T00:00:00Z' };
const event = (exDate: string, perShare: number, paymentDate: string | null = null) => ({ exDate, perShare, paymentDate, year: exDate });
const today = '2026-11-28';

describe('Taiwan dividend cashflow', () => {
  it('uses shares, includes day 0 and 90, excludes day 91, and retains unknown payment date', () => {
    const result = dividendCashflow([holding, { ...holding, market: 'US', symbol: 'AAPL', costCurrency: 'USD' }], {
      '2330.TW': { checked: today, events: [event(today, 20), event('2027-02-26', 2, '2027-03-01'), event('2027-02-27', 5)] },
    }, today);
    expect(result.upcoming.map(row => [row.exDate, row.shares, row.amount, row.paymentDate])).toEqual([
      [today, '1000', '20000', null], ['2027-02-26', '1000', '2000', '2027-03-01'],
    ]);
    expect(result.total).toBe('22000');
    expect(result.upcoming[0]?.premium).toBe('422');
  });

  it('applies exact threshold and cap', () => {
    expect(supplementaryPremium('19999.99')).toBe('0');
    expect(supplementaryPremium('20000')).toBe('422');
    expect(supplementaryPremium('10000000')).toBe('211000');
    expect(supplementaryPremium('10000001')).toBe('211000');
  });

  it('uses trailing calendar year and weighted average cost across accounts; unknown stays null', () => {
    const data = { '2330.TW': { checked: today, events: [event('2025-11-27', 99), event('2025-11-28', 2), event('2026-05-01', 3), event(today, 20)] } };
    const result = dividendCashflow([holding, { ...holding, accountId: 'c', averageCost: '300' }], data, today);
    expect(result.yields[0]?.yieldPct).toBe('2.5');
    expect(dividendCashflow([holding], {}, today).yields[0]?.yieldPct).toBeNull();
    expect(dividendCashflow([holding], { '2330.TW': { checked: '2026-01-01', events: data['2330.TW'].events } }, today).yields[0]?.yieldPct).toBeNull();
    expect(dividendCashflow([{ ...holding, averageCost: '0' }], data, today).yields[0]?.yieldPct).toBeNull();
  });

  it('validates structured source and Taipei date', () => {
    expect(cashDividendsSchema.safeParse({ '2330.TW': { checked: today, events: [event(today, 20)] } }).success).toBe(true);
    expect(cashDividendsSchema.safeParse({ '2330.TW': { checked: today, events: [event(today, 20, '2026-11-27')] } }).success).toBe(false);
    expect(taipeiDay(Date.parse('2026-11-27T16:00:00Z'))).toBe(today);
  });
});
