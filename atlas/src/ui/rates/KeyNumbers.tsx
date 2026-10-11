import { AlertTriangle } from 'lucide-react';
import type { Rates } from '../../report/rates';
import { valueOnOrBefore } from '../../rates/ratesView';
import { ChangeBp, fmtBp, fmtYield, type HistoryState } from './shared';

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);

function curveValue(rates: Rates, kind: 'nominal' | 'real', key: string, tenor: string): number | null {
  const tenors = kind === 'nominal' ? rates.tenors : rates.realTenors;
  const index = tenors.indexOf(tenor);
  const curve = rates.curves[kind].find((item) => item.key === key);
  return index >= 0 && curve ? (curve.values[index] ?? null) : null;
}
const changeBp = (now: number | null, then: number | null) => (finite(now) && finite(then) ? (now - then) * 100 : null);

/** One month back from YYYY-MM-DD (day clamped to 28 so it always exists). */
function monthBefore(date: string): string {
  const [y, m, d] = date.split('-').map(Number) as [number, number, number];
  const month = m === 1 ? 12 : m - 1;
  const year = m === 1 ? y - 1 : y;
  return `${year}-${String(month).padStart(2, '0')}-${String(Math.min(d, 28)).padStart(2, '0')}`;
}

/** Horizontally scrolling key-number chips: 10Y, 2Y, 10Y−2Y, 10Y real, 10Y breakeven, Fed target range. */
export function KeyNumbers({ rates, history }: { rates: Rates; history: HistoryState }) {
  const ten = curveValue(rates, 'nominal', 'now', '10Y');
  const two = curveValue(rates, 'nominal', 'now', '2Y');
  const real = curveValue(rates, 'real', 'now', '10Y');
  const spread = rates.spreads.find((item) => item.key === '10y2y');
  const breakeven = rates.inflation['10Y'];
  const policy = rates.policy;
  let policyChange: number | null = null;
  if (policy && finite(policy.upper) && history.status === 'ok' && rates.asOf) {
    const before = valueOnOrBefore(history.data.daily, 'DFEDTARU', monthBefore(rates.asOf));
    policyChange = before ? (policy.upper - before.value) * 100 : null;
  }
  const items = [
    {
      key: '10y',
      label: '10年期',
      value: fmtYield(ten),
      change: changeBp(ten, curveValue(rates, 'nominal', '1m', '10Y')),
    },
    {
      key: '2y',
      label: '2年期',
      value: fmtYield(two),
      change: changeBp(two, curveValue(rates, 'nominal', '1m', '2Y')),
    },
    {
      key: '10y2y',
      label: '10Y−2Y 利差',
      value: fmtBp(spread?.valueBp ?? null),
      change: spread?.chg1mBp ?? null,
      badge: spread?.inverted ? '倒掛' : null,
    },
    {
      key: 'real',
      label: '10年實質',
      value: fmtYield(real),
      change: changeBp(real, curveValue(rates, 'real', '1m', '10Y')),
    },
    {
      key: 'be',
      label: '10年通膨預期',
      value: fmtYield(breakeven?.value ?? null),
      // chg1m is in percentage points like the value itself.
      change: finite(breakeven?.chg1m) ? breakeven.chg1m * 100 : null,
    },
    {
      key: 'fed',
      label: '聯準會目標區間',
      value: policy ? `${fmtYield(policy.lower)}–${fmtYield(policy.upper)}` : '—',
      change: policyChange,
    },
  ];
  return (
    <ul className="rates-keys" aria-label="重點數字">
      {items.map((item) => (
        <li key={item.key} className="rates-key">
          <span className="rates-key-label">
            {item.label}
            {item.badge && (
              <span className="rates-badge warn">
                <AlertTriangle size={11} aria-hidden="true" />
                {item.badge}
              </span>
            )}
          </span>
          <b className="rates-key-value">{item.value}</b>
          <ChangeBp bp={item.change} />
        </li>
      ))}
    </ul>
  );
}
