import Decimal from 'decimal.js';
export * from '../../../supabase/functions/user-settings/holdings';
import {
  validateHoldingsDocument,
  type HoldingAccount,
  type Holding,
  type HoldingsDocument,
} from '../../../supabase/functions/user-settings/holdings';

export const Money = Decimal.clone({ precision: 80, rounding: Decimal.ROUND_HALF_UP });
export const emptyHoldings = (): HoldingsDocument => ({ version: 1, accounts: [] });
export function decimalText(value: Decimal.Value): string {
  return new Money(value).toFixed(18).replace(/\.?0+$/, '') || '0';
}
export function replaceAccount(
  document: HoldingsDocument,
  account: HoldingAccount,
  holdings: Holding[],
): HoldingsDocument {
  const previous = document.accounts.find((a) => a.account.id === account.id);
  return validateHoldingsDocument({
    version: 1,
    accounts: [
      ...document.accounts.filter((a) => a.account.id !== account.id),
      { account, holdings, revision: (previous?.revision ?? 0) + 1 },
    ],
  });
}
export interface HoldingQuote {
  price: string;
  previous: string | null;
  asOf: string;
  source: string;
}
export interface SellCosts {
  enabled: boolean;
  discount: string;
  etfs: ReadonlySet<string>;
}
export interface HoldingValue {
  value: string | null;
  cost: string;
  profit: string | null;
  percent: string | null;
  today: string | null;
  fee: string | null;
}
export function valueHolding(holding: Holding, quote: HoldingQuote | undefined, costs?: SellCosts): HoldingValue {
  const quantity = new Money(holding.quantity),
    cost = quantity.times(holding.averageCost);
  const empty = { value: null, cost: decimalText(cost), profit: null, percent: null, today: null, fee: null };
  if (!quote || !new Money(quote.price).isPositive()) return empty;
  const value = quantity.times(quote.price);
  let fee = new Money(0);
  if (costs?.enabled && holding.market === 'TW') {
    const discount = new Money(costs.discount);
    if (discount.isNegative() || discount.gt(1)) throw new Error('手續費折扣請填 0～1。');
    fee = value.times(new Money('0.001425').times(discount).plus(costs.etfs.has(holding.symbol) ? '0.001' : '0.003'));
  }
  const profit = value.minus(fee).minus(cost);
  return {
    value: decimalText(value),
    cost: decimalText(cost),
    profit: decimalText(profit),
    percent: cost.isZero() ? null : decimalText(profit.div(cost).times(100)),
    today: quote.previous === null ? null : decimalText(quantity.times(new Money(quote.price).minus(quote.previous))),
    fee: decimalText(fee),
  };
}
export interface FxRate {
  currency: 'USD' | 'USDT';
  twd: string;
  asOf: string;
}
export function summarizeHoldings(rows: { holding: Holding; result: HoldingValue }[], rates: FxRate[]) {
  const currencies = new Map<
    string,
    { value: Decimal; cost: Decimal; profit: Decimal; today: Decimal; missing: number; missingToday: number }
  >();
  for (const { holding, result } of rows) {
    const group = currencies.get(holding.costCurrency) ?? {
      value: new Money(0),
      cost: new Money(0),
      profit: new Money(0),
      today: new Money(0),
      missing: 0,
      missingToday: 0,
    };
    group.cost = group.cost.plus(result.cost);
    if (result.value !== null && result.profit !== null) {
      group.value = group.value.plus(result.value);
      group.profit = group.profit.plus(result.profit);
    } else group.missing++;
    if (result.today !== null) group.today = group.today.plus(result.today);
    else group.missingToday++;
    currencies.set(holding.costCurrency, group);
  }
  let twd = new Money(0),
    profitTwd = new Money(0),
    missing = 0;
  for (const [currency, group] of currencies) {
    const rate = currency === 'TWD' ? '1' : rates.find((rate) => rate.currency === currency)?.twd;
    missing += group.missing;
    if (rate) {
      twd = twd.plus(group.value.times(rate));
      profitTwd = profitTwd.plus(group.profit.times(rate));
    } else missing++;
  }
  return { currencies, twd: decimalText(twd), profitTwd: decimalText(profitTwd), partial: missing > 0 };
}
