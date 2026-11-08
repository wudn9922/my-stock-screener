import { Money, formatAmount } from './model';

/** Display only. Never feed these rounded strings back into valuation or storage. */
export function formatHoldingPrice(value: string | null | undefined): string {
  if (value == null) return '—';
  const price = new Money(value);
  return price.isZero() || price.abs().gte(1)
    ? formatAmount(value, 2)
    : price.toSignificantDigits(4, Money.ROUND_HALF_UP).toFixed();
}
export function formatHoldingPnl(value: string): string {
  const rounded = new Money(value).toDecimalPlaces(2, Money.ROUND_HALF_UP);
  return (rounded.gt(0) ? '+' : '') + formatAmount(rounded.isZero() ? '0' : rounded.toFixed(), 2);
}
