import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { formatDateTime } from '../format';
import { provenanceText, quarterStrip, type FinancialsView } from './financialsView';
import { FinancialNote } from './FinancialNote';

/**
 * 「當季財報」: the newest quarter as one compact row of small cells (period in the head; EPS, revenue, gross
 * margin, net margin), each with its change against the same quarter a year earlier. Actual reported or derived
 * values only: no expectations or estimates, and a missing value stays a dash with its reason on hover.
 * `quarterly` is the quarterly view, whatever period type the rest of the page shows.
 */
export function QuarterStrip({ history, quarterly }: { history: FinancialHistory; quarterly: FinancialsView }) {
  const strip = quarterStrip(quarterly, history.currency);
  const hasQuarters = history.quarterly.end.length > 0;
  return (
    <section className="fin-quarter" aria-labelledby="fin-quarter-title">
      <div className="fin-quarter-head">
        <h2 id="fin-quarter-title">當季財報</h2>
        {strip.period && <span className="fin-quarter-period">{strip.period}</span>}
      </div>
      {strip.index === null ? (
        <p className="fin-empty">{hasQuarters ? '最新季度沒有可用的數字。' : '這檔股票沒有季度財報。'}</p>
      ) : (
        <ul className="fin-quarter-cells">
          {strip.cells.map((cell) => {
            const missing = cell.value === '—';
            const label = cell.unit && cell.unit !== '%' ? `${cell.label}·${cell.unit}` : cell.label;
            return (
              <li key={cell.id} className="fin-quarter-cell" title={cell.reason ?? undefined}>
                <span className="fin-quarter-label">{label}</span>
                <b className={`fin-quarter-value${missing ? ' null' : ''}`}>
                  {cell.value}
                  {!missing && cell.unit === '%' && <small>%</small>}
                </b>
                <span className={`fin-quarter-change ${cell.change.tone}`}>{cell.change.text}</span>
              </li>
            );
          })}
        </ul>
      )}
      {hasQuarters && (
        <FinancialNote className="fin-quarter-caption" label="當季資料來源"><p>
          資料日 {history.checkedAt ? formatDateTime(history.checkedAt, false) : '—'} · {provenanceText(history, 'quarterly')}
        </p></FinancialNote>
      )}
    </section>
  );
}
