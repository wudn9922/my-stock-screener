import { Money, decimalText, type Holding, type HoldingValue, type FxRate } from './model';
import type { MexcFutures } from '../../../supabase/functions/user-settings/mexcFuturesContract';
export interface PortfolioAmount {
  value: string;
  cost: string;
  profit: string;
  percent: string | null;
  missing: number;
}
export interface FuturesTotalInput {
  id: string;
  data?: MexcFutures;
  failed?: boolean;
  at?: string;
}
export function portfolioTotals(
  rows: { holding: Holding; result: HoldingValue }[],
  futures: FuturesTotalInput[],
  rates: FxRate[],
) {
  const buckets: Record<'TWD' | 'USD' | 'USDT', PortfolioAmount> = {
    TWD: { value: '0', cost: '0', profit: '0', percent: null, missing: 0 },
    USD: { value: '0', cost: '0', profit: '0', percent: null, missing: 0 },
    USDT: { value: '0', cost: '0', profit: '0', percent: null, missing: 0 },
  };
  const add = (currency: keyof typeof buckets, value: string, cost: string, profit: string) => {
    const b = buckets[currency];
    b.value = decimalText(new Money(b.value).plus(value));
    b.cost = decimalText(new Money(b.cost).plus(cost));
    b.profit = decimalText(new Money(b.profit).plus(profit));
  };
  for (const { holding, result } of rows) {
    const currency = holding.costCurrency;
    if (result.value === null || result.profit === null) {
      buckets[currency].missing++;
      buckets[currency].cost = decimalText(new Money(buckets[currency].cost).plus(result.cost));
      continue;
    }
    add(currency, result.value, result.cost, result.profit);
  }
  let excludedFutures = 0;
  for (const f of futures) {
    if (f.failed || f.data?.status !== 'ok') {
      excludedFutures++;
      continue;
    }
    const account = f.data.account;
    // Equity already contains unrealized PnL and available cash. Never add positions/notional again.
    add(
      'USDT',
      account.equity,
      decimalText(new Money(account.equity).minus(account.unrealized)),
      account.unrealized,
    );
  }
  const usd = rates.find((r) => r.currency === 'USD'),
    usdt = rates.find((r) => r.currency === 'USDT');
  const factor = usd && usdt ? new Money(usdt.twd).div(usd.twd) : null;
  const foreign = { ...buckets.USD };
  if (factor) {
    foreign.value = decimalText(new Money(foreign.value).plus(new Money(buckets.USDT.value).times(factor)));
    foreign.cost = decimalText(new Money(foreign.cost).plus(new Money(buckets.USDT.cost).times(factor)));
    foreign.profit = decimalText(
      new Money(foreign.profit).plus(new Money(buckets.USDT.profit).times(factor)),
    );
  }
  foreign.missing += buckets.USDT.missing;
  for (const b of [...Object.values(buckets), foreign])
    b.percent = b.missing === 0 && new Money(b.cost).gt(0) ? decimalText(new Money(b.profit).div(b.cost).times(100)) : null;
  return {
    tw: buckets.TWD,
    foreign,
    usdt: buckets.USDT,
    factor: factor ? decimalText(factor) : null,
    excludedFutures,
  };
}
