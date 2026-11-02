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
export function monthBefore(date: string): string {
  const [y, m, d] = date.split('-').map(Number) as [number, number, number];
  const month = m === 1 ? 12 : m - 1;
  const year = m === 1 ? y - 1 : y;
  return `${year}-${String(month).padStart(2, '0')}-${String(Math.min(d, 28)).padStart(2, '0')}`;
}

export interface RateKeyNumber {
  key: string;
  label: string;
  value: string;
  change: number | null;
  badge?: string | null;
  title?: string;
}

/** Shared headline layout for US and Japanese sovereign rates. */
export function RatesKeyNumbers({ items }: { items: readonly RateKeyNumber[] }) {
  return (
    <ul className="rates-keys" aria-label="重點數字">
      {items.map((item) => (
        <li key={item.key} className="rates-key" title={item.title}>
          <span className="rates-key-label">
            <span className="rates-key-title">{item.label}</span>
            {item.badge && <span className="rates-badge warn"><AlertTriangle size={11} aria-hidden="true" />{item.badge}</span>}
          </span>
          <b className="rates-key-value">{item.value}</b>
          <ChangeBp bp={item.change} />
        </li>
      ))}
    </ul>
  );
}

/** Three headline chips: 10Y yield, 10Y−Fed at the selected bound, Fed target range. */
export function KeyNumbers({ rates, history, fedBound }: { rates: Rates; history: HistoryState; fedBound: 'upper' | 'lower' }) {
  const ten = curveValue(rates, 'nominal', 'now', '10Y');
  const fedRate = rates.policy?.[fedBound] ?? null;
  // Calculate from live chart yield and selected policy bound; never reuse an older midpoint summary.
  const spreadBp = finite(ten) && finite(fedRate) ? (ten - fedRate) * 100 : null;
  let spreadChange: number | null = null;
  if (history.status === 'ok' && rates.asOf && finite(spreadBp)) {
    const past = monthBefore(rates.asOf);
    const tenThen = valueOnOrBefore(history.data.daily, 'DGS10', past);
    const fedThen = valueOnOrBefore(history.data.daily, fedBound === 'upper' ? 'DFEDTARU' : 'DFEDTARL', past);
    if (tenThen && fedThen && tenThen.date === fedThen.date)
      spreadChange = spreadBp - (tenThen.value - fedThen.value) * 100;
  }
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
      key: '10yfed',
      label: `10Y−Fed（${fedBound === 'upper' ? '上限' : '下限'}）`,
      value: fmtBp(spreadBp),
      change: spreadChange,
      badge: finite(spreadBp) && spreadBp < 0 ? '倒掛' : null,
    },
    {
      key: 'fed',
      label: '聯準會目標區間',
      // "3.75–4.00%": one % sign keeps the chip inside a third of a phone screen
      value: policy ? `${fmtYield(policy.lower).replace('%', '')}–${fmtYield(policy.upper)}` : '—',
      change: policyChange,
    },
  ];
  return <RatesKeyNumbers items={items} />;
}
