import { Money, decimalText, type Holding, type HoldingAccount, type HoldingCurrency, type HoldingValue } from './model';

type Row = { holding: Holding; result: HoldingValue };
export interface PrincipalMetrics {
  principal: string | null;
  cost: string;
  cash: string | null;
  value: string | null;
  profit: string | null;
  total: string | null;
  percent: string | null;
  weight: string | null;
  costPercent: string | null;
  belowCost: boolean;
  missingPrincipal: boolean;
  incomplete: boolean;
}

function metrics(accounts: HoldingAccount[], rows: Row[], pending: ReadonlySet<string>): PrincipalMetrics {
  const ids = new Set(accounts.map((account) => account.id));
  // Callers pass the exact visible evaluated rows used by existing spot totals.
  const visible = rows.filter((row) => ids.has(row.holding.accountId));
  const waiting = accounts.some((account) => pending.has(account.id));
  const missingPrincipal = accounts.some((account) => account.principal === undefined);
  const principal = missingPrincipal || !accounts.length ? null
    : accounts.reduce((sum, account) => sum.plus(account.principal!), new Money(0));
  const cost = visible.reduce((sum, row) => sum.plus(row.result.cost), new Money(0));
  const incomplete = waiting || visible.some((row) => row.result.value === null || row.result.profit === null);
  const value = incomplete ? null : visible.reduce((sum, row) => sum.plus(row.result.value!), new Money(0));
  const profit = incomplete ? null : visible.reduce((sum, row) => sum.plus(row.result.profit!), new Money(0));
  const cash = principal === null || waiting ? null : principal.minus(cost);
  const total = cash === null || value === null ? null : cash.plus(value);
  return {
    principal: principal === null ? null : decimalText(principal),
    cost: decimalText(cost),
    cash: cash === null ? null : decimalText(cash),
    value: value === null ? null : decimalText(value),
    profit: profit === null ? null : decimalText(profit),
    total: total === null ? null : decimalText(total),
    percent: principal === null || profit === null ? null : decimalText(profit.div(principal).times(100)),
    weight: value === null || total === null || total.isZero() ? null : decimalText(value.div(total).times(100)),
    costPercent: profit === null || cost.isZero() ? null : decimalText(profit.div(cost).times(100)),
    belowCost: cash !== null && cash.isNegative(),
    missingPrincipal,
    incomplete,
  };
}

export function accountPrincipal(account: HoldingAccount, rows: Row[], pending: ReadonlySet<string> = new Set()) {
  return account.principal === undefined ? null : metrics([account], rows, pending);
}

export function principalByCurrency(accounts: HoldingAccount[], rows: Row[], pending: ReadonlySet<string> = new Set()) {
  const groups = new Map<HoldingCurrency, PrincipalMetrics>();
  for (const currency of ['TWD', 'USD', 'USDT'] as const) {
    const matching = accounts.filter((account) => account.currency === currency);
    if (matching.length) groups.set(currency, metrics(matching, rows, pending));
  }
  return groups;
}
