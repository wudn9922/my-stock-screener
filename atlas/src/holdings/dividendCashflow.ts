import { Money, decimalText, type Holding } from './model';
import type { CashDividends } from './twCashDividends';

export const DIVIDEND_WINDOW_DAYS = 90;
const DAY = 86400000;

export function taipeiDay(now: number): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}

export function supplementaryPremium(amount: string): string {
  const value = new Money(amount);
  if (value.lt(20000)) return '0';
  return decimalText(Money.min(value, 10000000).times('0.0211'));
}

/** Holdings already store shares, including imports converted from lots. No additional multiplier. */
export function dividendCashflow(holdings: readonly Holding[], data: CashDividends, today: string) {
  const book = new Map<string, { shares: InstanceType<typeof Money>; cost: InstanceType<typeof Money> }>();
  for (const h of holdings) {
    if (h.market !== 'TW') continue;
    const shares = new Money(h.quantity);
    const row = book.get(h.symbol) ?? { shares: new Money(0), cost: new Money(0) };
    book.set(h.symbol, { shares: row.shares.plus(shares), cost: row.cost.plus(shares.times(h.averageCost)) });
  }
  const end = new Date(Date.parse(today) + DIVIDEND_WINDOW_DAYS * DAY).toISOString().slice(0, 10);
  const prior = new Date(today);
  const day = prior.getUTCDate();
  prior.setUTCDate(1);
  prior.setUTCFullYear(prior.getUTCFullYear() - 1);
  const last = new Date(Date.UTC(prior.getUTCFullYear(), prior.getUTCMonth() + 1, 0)).getUTCDate();
  prior.setUTCDate(Math.min(day, last));
  const start = prior.toISOString().slice(0, 10);
  const upcoming = [];
  const yields = [];
  let total = new Money(0);
  for (const [symbol, { shares, cost }] of book) {
    const block = data[symbol];
    const events = block?.events ?? [];
    const history = events.filter(row => row.exDate >= start && row.exDate < today);
    const checked = block?.checked;
    const fresh = !!checked && checked <= today && Date.parse(today) - Date.parse(checked) <= 45 * DAY;
    const perShare = history.reduce((sum, row) => sum.plus(row.perShare), new Money(0));
    const yieldPct = fresh && history.length && cost.gt(0)
      ? decimalText(perShare.times(shares).div(cost).times(100)) : null;
    yields.push({ symbol, yieldPct, checked: checked ?? null });
    for (const row of events) {
      if (row.exDate < today || row.exDate > end) continue;
      const amount = decimalText(shares.times(row.perShare));
      total = total.plus(amount);
      upcoming.push({ ...row, symbol, shares: decimalText(shares), amount, premium: supplementaryPremium(amount) });
    }
  }
  upcoming.sort((a, b) => a.exDate.localeCompare(b.exDate) || a.symbol.localeCompare(b.symbol) || a.year.localeCompare(b.year));
  yields.sort((a, b) => a.symbol.localeCompare(b.symbol));
  return { upcoming, yields, total: decimalText(total) };
}
