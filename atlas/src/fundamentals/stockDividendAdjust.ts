import type { StockDividendEvent } from './FinancialHistory';

/**
 * 追溯調整 EPS（含配股）. A Taiwan stock dividend (盈餘／資本公積轉增資) of D NT$ per share (par 10) adds D/10 new
 * shares per old share on its ex-rights date, so EPS printed before it sits on a smaller share basis. The
 * restated EPS = reported EPS ÷ Π(1 + D/10) over every stock dividend whose ex-rights date is after the report
 * was due (period end + delay; earlier ones are already in the reported figure). Mirror of
 * atlas/scripts/tw_stock_dividends.py. Events null (no data) → no factor: the caller shows null, never a guess.
 * Not handled: capital reductions (減資) and cash capital increases (no event data; nothing is shown for them).
 */
export const PAR_VALUE = 10;

const DAY_MS = 86_400_000;
const parse = (day: string): number => Date.parse(`${day}T00:00:00Z`);

/** Days after a period end before its report is due (same as PUBLISH_DELAY_DAYS.TW). */
export function reportDelayDays(end: string): number {
  const month = Number(end.slice(5, 7));
  return month === 12 ? 90 : month === 6 ? 60 : 45;
}

export interface DividendFactor {
  factor: number;
  events: StockDividendEvent[];
}

/**
 * Factor of the period ending `end`; `asOf` (YYYY-MM-DD) restricts to events up to that day, i.e. the share basis
 * an unadjusted price of that day is on. `annual`: an annual period, whose report is always due after 90 days.
 * Null when `events` is null.
 */
export function dividendFactor(
  end: string,
  events: readonly StockDividendEvent[] | null,
  asOf?: string,
  annual = false,
): DividendFactor | null {
  if (events === null) return null;
  const due = parse(end) + (annual ? 90 : reportDelayDays(end)) * DAY_MS;
  const limit = asOf === undefined ? Infinity : parse(asOf);
  const applied = events.filter((event) => {
    const ex = parse(event.exDate);
    return ex > due && ex <= limit;
  });
  const factor = applied.reduce((product, event) => product * (1 + event.perShare / PAR_VALUE), 1);
  return { factor: Math.round(factor * 1e6) / 1e6, events: applied };
}

/** Reported EPS ÷ factor (4 decimals); null when the EPS or the dividend data is missing. */
export function adjustEps(
  eps: number | null,
  end: string,
  events: readonly StockDividendEvent[] | null,
  asOf?: string,
  annual = false,
): number | null {
  const info = dividendFactor(end, events, asOf, annual);
  if (eps === null || info === null) return null;
  return Math.round((eps / info.factor) * 1e4) / 1e4 + 0;
}

/** 「配股 0.5 元（除權 2025-08-20）」 lines of the applied events, for tooltips. */
export function eventText(events: readonly StockDividendEvent[]): string {
  return events.map((event) => `配股 ${event.perShare} 元（除權 ${event.exDate}）`).join('、');
}

export const ADJUSTED_EPS_HELP = '追溯調整：依之後的配股（盈餘／資本公積轉增資）調整股數，讓前後期可比較';
