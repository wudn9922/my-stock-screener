import { describe, expect, it } from 'vitest';
import { formatHoldingPnl, formatHoldingPrice } from '../src/holdings/displayFormat';
import { formatAmount } from '../src/holdings/model';
describe('holdings display only rounding', () => {
  it.each([['-0.53499', '-0.53'], ['1.235', '+1.24'], ['-1.235', '-1.24'], ['0.005', '+0.01'], ['-0.005', '-0.01'], ['1234.567', '+1,234.57'], ['-0.0001', '0.00']])('PnL %s → %s', (raw, expected) => expect(formatHoldingPnl(raw)).toBe(expected));
  it.each([['1', '1.00'], ['1234.56789', '1,234.57'], ['1.005', '1.01'], ['0.043212345', '0.04321'], ['0.000123456789', '0.0001235'], ['0.005', '0.005'], ['0.00000000123456789', '0.000000001235'], ['0', '0.00']])('price %s → %s', (raw, expected) => expect(formatHoldingPrice(raw)).toBe(expected));
  it('keeps amounts fixed at two places without mutating source', () => {
    const raw = '1234.567890123456789'; expect(formatAmount(raw)).toBe('1,234.57');
    expect(formatAmount('0.005')).toBe('0.01'); expect(formatAmount('-0.005')).toBe('-0.01');
    expect(formatHoldingPrice(null)).toBe('—'); expect(raw).toBe('1234.567890123456789');
  });
});
