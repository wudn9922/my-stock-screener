import { Fragment } from 'react';
import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { METRIC_GROUPS } from '../../fundamentals/financialMetrics';
import { formatDateTime } from '../format';
import { SOURCE_SHORT, digitsFor, displayValues, formatNumber, moneyScale, unitText, type FinancialsView } from './financialsView';

/**
 * Every metric × every period: metric names frozen in the first column, latest period on the left,
 * horizontal scroll for older periods. Each row picks its own 億 / 兆 scale (named in the row label).
 */
export function FinancialsTable({ history, view }: { history: FinancialHistory; view: FinancialsView }) {
  const series = history[view.kind];
  const order = series.end.map((_, index) => index).reverse();
  const quarterly = view.kind === 'quarterly';
  return (
    <div className="card fin-table-card">
      <div className="table-scroll fin-table-scroll" tabIndex={0} aria-label="財報數字表，可左右捲動">
        <table className="fin-table">
          <thead>
            <tr>
              <th scope="col" className="fin-sticky">
                指標
              </th>
              {order.map((index) => (
                <th key={series.end[index]} scope="col">
                  <b>{quarterly ? view.labels[index]!.long.replace('FY20', 'FY') : view.labels[index]!.long}</b>
                  <small>{formatDateTime(series.end[index]!, false)}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {METRIC_GROUPS.map((group) => (
              <Fragment key={group.id}>
                <tr className="fin-group-row">
                  <th scope="rowgroup" className="fin-sticky">
                    {group.title}
                  </th>
                  <td colSpan={order.length} />
                </tr>
                {group.metrics.map((id) => {
                  const data = view.metrics[id];
                  const scale = moneyScale(data.values);
                  const shown = displayValues(data.values, data.unit, scale);
                  const digits = digitsFor(shown, data.unit);
                  return (
                    <tr key={id}>
                      <th scope="row" className="fin-sticky">
                        {data.label}
                        <small>{unitText(data.unit, history.currency, scale)}</small>
                      </th>
                      {order.map((index) => {
                        const value = shown[index] ?? null;
                        const result = data.results[index]!;
                        return (
                          <td
                            key={series.end[index]}
                            className={`${value !== null && value < 0 ? 'negative' : ''}${result.abnormal ? ' abnormal' : ''}`}
                            title={value === null ? result.reason : result.notes.join('；') || undefined}
                          >
                            {value === null ? '—' : formatNumber(value, digits)}
                            {result.abnormal ? '*' : ''}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </Fragment>
            ))}
            <tr className="fin-source-row">
              <th scope="row" className="fin-sticky">
                來源
              </th>
              {order.map((index) => (
                <td key={series.end[index]} className={series.src[index]}>
                  {SOURCE_SHORT[series.src[index] ?? 'yahoo']}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="source-note fin-table-note">
        最新期別在最左側，向右捲動看較早期別。「*」為異常的有效稅率（一次性稅務項目）；「—」為無資料或不適用，長按或滑鼠移上可看原因。
      </p>
    </div>
  );
}
