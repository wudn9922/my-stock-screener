import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { validateMexcFutures } from '../../supabase/functions/user-settings/mexcFuturesContract';
import { MexcFuturesPanel, futuresReturn } from '../src/holdings/MexcFuturesPanel';

const sample = () => ({
  status: 'ok' as const,
  skippedPositions: 0,
  account: { equity: '100', available: '80', positionMargin: '20', unrealized: '-2' },
  positions: [
    {
      symbol: 'BTC_USDT',
      side: 'long' as const,
      contracts: '1',
      size: '0.0001',
      entryPrice: '60000',
      markPrice: '59000',
      leverage: '10',
      margin: '20',
      unrealized: '-0.1',
      liquidationPrice: null,
      marginMode: 'isolated' as const,
    },
  ],
});
describe('separate futures response and display', () => {
  it('accepts signed unrealized values only and discards private extra fields', () => {
    const value = sample();
    expect(validateMexcFutures({ ...value, secret: 'never-render' })).toEqual(value);
    for (const field of ['equity', 'available', 'positionMargin'])
      expect(() => validateMexcFutures({ ...value, account: { ...value.account, [field]: '-1' } })).toThrow();
    for (const field of [
      'entryPrice',
      'markPrice',
      'size',
      'contracts',
      'leverage',
      'margin',
      'liquidationPrice',
    ])
      expect(() =>
        validateMexcFutures({ ...value, positions: [{ ...value.positions[0], [field]: '-1' }] }),
      ).toThrow();
    expect(() => validateMexcFutures({ ...value, account: { ...value.account, equity: 100 } })).toThrow();
    expect(() => validateMexcFutures({ ...value, skippedPositions: -1 })).toThrow();
    expect(() =>
      validateMexcFutures({ ...value, positions: Array.from({ length: 201 }, () => value.positions[0]) }),
    ).toThrow();
  });
  it('shows signed PnL, margin return and unavailable liquidation without inventing a zero price', () => {
    expect(futuresReturn('-0.5', '30')).toBe('-1.67%');
    expect(futuresReturn('1.25', '20')).toBe('+6.25%');
    expect(futuresReturn('0', '0')).toBe('—');
    const html = renderToStaticMarkup(<MexcFuturesPanel futures={sample()} />);
    expect(html).toContain('僅供查看，未計入持股總值');
    expect(html).toContain('-0.1');
    expect(html).toContain('-0.50%');
    expect(html).toContain('強平價');
    expect(html).toContain('—');
    expect(html).not.toMatch(/Infinity|NaN/);
  });
  it.each([
    [{ status: 'no-permission' as const }, '這把 API Key 沒有合約讀取權限'],
    [{ status: 'unavailable' as const }, '合約資料暫時無法讀取，現貨已同步'],
    [{ ...sample(), positions: [] }, '目前沒有合約持倉'],
  ])('renders the appropriate state without a position card', (futures, expected) => {
    const html = renderToStaticMarkup(<MexcFuturesPanel futures={futures} />);
    expect(html).toContain(expected);
    expect(html).not.toContain('<article');
  });
});
