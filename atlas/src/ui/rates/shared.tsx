import type { ReactNode } from 'react';
import type { RatesEtf, RatesHistory } from '../../report/rates';
import { toneClass } from '../format';

/** rates-history.json loads after the summary; sections that need it show their own state. */
export type HistoryState =
  | { status: 'loading' }
  | { status: 'ok'; data: RatesHistory; stale: boolean }
  | { status: 'missing' | 'invalid' | 'error'; message: string };

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);

/** Yield / rate in percent with two decimals. */
export function fmtYield(value: number | null | undefined, digits = 2): string {
  return finite(value) ? `${value.toFixed(digits)}%` : '—';
}
/** Stored rates are in basis points; present their size in percentage points for the user. */
export function fmtBp(value: number | null | undefined, signed = true): string {
  if (!finite(value)) return '—';
  const percent = value / 100;
  const rounded = Number(percent.toFixed(2));
  return `${signed && rounded > 0 ? '+' : ''}${rounded.toFixed(2)}%`;
}
/** Plain number with fixed decimals (durations, ratios). */
export function fmtNum(value: number | null | undefined, digits = 1): string {
  return finite(value) ? value.toFixed(digits) : '—';
}
/** `2024-01-05` → `2024/01/05`. */
export const slashDate = (date: string | null | undefined) => (date ? date.replaceAll('-', '/') : '—');
/** `2024-01-05` → `2024/01`. */
export const slashMonth = (date: string | null | undefined) => (date ? `${date.slice(0, 4)}/${date.slice(5, 7)}` : '—');

/** A signed rate change in percentage points, colored by direction. */
export function ChangeBp({ bp, label = '1月' }: { bp: number | null | undefined; label?: string }) {
  return (
    <span className={`rates-chg ${toneClass(finite(bp) ? Math.round(bp) : null)}`}>
      {label ? `${label} ` : ''}
      {fmtBp(bp)}
    </span>
  );
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  className = '',
}: {
  label: string;
  options: readonly { value: T; label: ReactNode; disabled?: boolean }[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div className={`segmented compact rates-seg ${className}`} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={value === option.value ? 'active' : ''}
          aria-pressed={value === option.value}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** 「資料日 …、待核對」 next to hand-entered ETF fields (expense ratio, published duration, index…). */
export function StaticMark({ etf }: { etf: Pick<RatesEtf, 'staticAsOf' | 'staticSource' | 'verified'> }) {
  const text = `資料日 ${slashDate(etf.staticAsOf)}${etf.verified ? '' : '、待核對'}`;
  const safe = etf.staticSource && /^https:\/\//.test(etf.staticSource) ? etf.staticSource : null;
  return safe ? (
    <a className="rates-static" href={safe} target="_blank" rel="noreferrer noopener" title="發行商頁面（靜態資料出處）">
      {text}
    </a>
  ) : (
    <span className="rates-static">{text}</span>
  );
}

/** Leverage badge for daily-reset funds. */
export function LeverageBadge({ leverage }: { leverage: number }) {
  if (leverage <= 1) return null;
  return (
    <span className="rates-badge lev" title="每日重置的槓桿 ETF">
      {leverage}× 每日重置
    </span>
  );
}

export function SectionState({ history, what }: { history: HistoryState; what: string }) {
  if (history.status === 'ok') return null;
  return (
    <p className="rates-section-state" role="status">
      {history.status === 'loading' ? `${what}載入中…` : `${what}暫時無法顯示：${history.message}`}
    </p>
  );
}
