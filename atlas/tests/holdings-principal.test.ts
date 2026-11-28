import { describe, expect, it } from 'vitest';
import { accountPrincipal, principalByCurrency } from '../src/holdings/principal';
import { emptyHoldings, replaceAccount, validateHoldingAccount, valueHolding, type Holding, type HoldingAccount } from '../src/holdings/model';
import { emptyVisibility, hiddenReason } from '../src/holdings/visibility';

const account: HoldingAccount = { id: '11111111-1111-4111-8111-111111111111', name: 'TW', kind: 'tw-broker', currency: 'TWD', broker: 'kgi', principal: '2000' };
const position: Holding = { id: '22222222-2222-4222-8222-222222222222', accountId: account.id, symbol: '2330.TW', market: 'TW', quantity: '10', averageCost: '100', costCurrency: 'TWD', source: 'manual', updatedAt: '2026-11-28T00:00:00Z' };
const quote = (price: string) => ({ price, previous: null, asOf: '2026-11-28T00:00:00Z', source: 'test' });
const row = (holding = position, price = '200') => ({ holding, result: valueHolding(holding, quote(price)) });
const empty: HoldingAccount = { ...account, id: '33333333-3333-4333-8333-333333333333', principal: '1000' };

describe('principal on evaluated visible holdings', () => {
  it('computes cash, account value, return and weight, including negative cash', () => {
    expect(accountPrincipal(account, [row()])).toMatchObject({ principal: '2000', cost: '1000', cash: '1000', value: '2000', profit: '1000', total: '3000', percent: '50', weight: '66.666666666666666667', belowCost: false });
    expect(accountPrincipal({ ...account, principal: '500' }, [row()])).toMatchObject({ cash: '-500', total: '1500', percent: '200', weight: '133.333333333333333333', belowCost: true });
    expect(accountPrincipal({ ...account, principal: undefined }, [row()])).toBeNull();
    expect(accountPrincipal({ ...account, principal: '500' }, [row(position, '50')])?.weight).toBeNull();
  });
  it('uses the same visible rows for costs and market values', () => {
    const dust = row({ ...position, symbol: '0050.TW', quantity: '0.1', averageCost: '900' }, '100');
    expect(hiddenReason(dust.holding, account, dust.result.value, emptyVisibility())).toBe('dust');
    const rows = [row(), dust].filter((r) => !hiddenReason(r.holding, account, r.result.value, emptyVisibility()));
    expect(accountPrincipal(account, rows)).toEqual(accountPrincipal(account, [row()]));
    expect(principalByCurrency([account], rows).get('TWD')?.cost).toBe('1000');
  });
  it('groups same-currency accounts including empty accounts, but never mixes currencies', () => {
    const usd: HoldingAccount = { ...empty, id: '44444444-4444-4444-8444-444444444444', kind: 'us-broker', currency: 'USD' };
    const groups = principalByCurrency([account, empty, usd], [row()]);
    expect(groups.get('TWD')).toMatchObject({ principal: '3000', cash: '2000', total: '4000', percent: '33.333333333333333333' });
    expect(groups.get('USD')).toMatchObject({ principal: '1000', cash: '1000', total: '1000', percent: '0' });
    expect(principalByCurrency([account, { ...empty, principal: undefined }], [row()]).get('TWD')).toMatchObject({ principal: null, percent: null, costPercent: '100', missingPrincipal: true });
  });
  it('does not invent returns from missing quotes or pending broker review', () => {
    expect(accountPrincipal(account, [{ holding: position, result: valueHolding(position, undefined) }])).toMatchObject({ cash: '1000', total: null, percent: null, incomplete: true });
    expect(accountPrincipal(account, [row()], new Set([account.id]))).toMatchObject({ cash: null, total: null, percent: null, incomplete: true });
  });
  it('validates decimal principal and preserves it when replacing an account without the field', () => {
    for (const principal of [null, 1, '0', '0.000', '-1', '+1', '1e3', '01', '1000000000000000', '0.1234567890123456789', '']) {
      expect(() => validateHoldingAccount({ ...account, principal })).toThrow();
    }
    expect(validateHoldingAccount({ ...account, principal: '0.000000000000000001' }).principal).toBe('0.000000000000000001');
    const original = replaceAccount(emptyHoldings(), account, [position]);
    const replacement = replaceAccount(original, { ...account, principal: undefined }, [{ ...position, quantity: '20' }]);
    expect(replacement.accounts[0]!.account.principal).toBe('2000');
    expect(replacement.accounts[0]!.holdings[0]!.quantity).toBe('20');
  });
});
