import { spanLabel, spanOptions } from './financialsView';
import type { PeriodKind } from '../../fundamentals/FinancialHistory';

/**
 * Chart range: how many years (annual) or quarters (quarterly) the bars show. Options the file cannot fill
 * are disabled; when the chosen range is longer than the file, every period is shown and the note says so.
 */
export function SpanControl({
  kind,
  count,
  span,
  onSpan,
}: {
  kind: PeriodKind;
  /** Periods the file holds for this kind. */
  count: number;
  span: number;
  onSpan: (span: number) => void;
}) {
  const options = spanOptions(kind, count);
  const unit = kind === 'annual' ? '年' : '季';
  const short = count < span;
  return (
    <div className="fin-range">
      <span className="fin-range-label" id="fin-range-label">
        圖表區間
      </span>
      <div className="segmented compact" role="group" aria-labelledby="fin-range-label">
        {options.map(({ span: value, enabled }) => {
          const current = enabled && value === span;
          return (
            <button
              key={value}
              type="button"
              className={current ? 'active' : ''}
              aria-pressed={current}
              disabled={!enabled}
              title={enabled ? undefined : `這檔股票只有 ${count} ${unit}資料`}
              onClick={() => onSpan(value)}
            >
              {spanLabel(kind, value)}
            </button>
          );
        })}
      </div>
      <span className="fin-range-note">{short ? `共 ${count} ${unit}，已全部顯示` : `共 ${count} ${unit}，顯示近 ${span} ${unit}`}</span>
    </div>
  );
}
