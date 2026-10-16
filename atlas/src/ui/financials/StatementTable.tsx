import { useMemo, useState } from 'react';
import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { STATEMENT_TABS, statementRows, type StatementId } from '../../fundamentals/financialStatements';
import { formatDateTime } from '../format';
import {
  changeCellText,
  digitsFor,
  displayValues,
  formatNumber,
  moneyScale,
  SOURCE_LABEL,
  unitText,
  type FinancialsView,
} from './financialsView';

type RowMode = 'value' | 'yoy';

/**
 * Three statement tabs (損益表, 資產負債, 現金流), every period of the file, latest period on the left.
 * Each line switches between its values and year-over-year changes. Lines the source does not carry are
 * labelled 「此資料源未提供」 instead of being estimated.
 */
export function StatementTable({ history, view }: { history: FinancialHistory; view: FinancialsView }) {
  const [statement, setStatement] = useState<StatementId>('income');
  const [modes, setModes] = useState<Record<string, RowMode>>({});
  const series = history[view.kind];
  const order = series.end.map((_, index) => index).reverse();
  const quarterly = view.kind === 'quarterly';
  const rows = useMemo(() => statementRows(history, view.kind, statement), [history, view.kind, statement]);
  const title = STATEMENT_TABS.find((tab) => tab.id === statement)!.label;
  const periodTitle = (index: number) => (quarterly ? view.labels[index]!.long.replace('FY20', 'FY') : view.labels[index]!.long);

  return (
    <div className="card fin-table-card">
      <div className="fin-tabs fin-statement-tabs" role="group" aria-label="報表">
        {STATEMENT_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={tab.id === statement ? 'active' : ''}
            aria-pressed={tab.id === statement}
            onClick={() => setStatement(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="table-scroll fin-table-scroll" tabIndex={0} aria-label={`${title}數字表，可左右捲動`}>
        <table className="fin-table">
          <thead>
            <tr>
              <th scope="col" className="fin-sticky">
                {title}
              </th>
              {order.map((index) => (
                <th key={series.end[index]} scope="col">
                  <b>{periodTitle(index)}</b>
                  <small>{formatDateTime(series.end[index]!, false)}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const key = `${statement}.${row.id}`;
              const mode = modes[key] ?? 'value';
              const scale = moneyScale(row.values);
              const shown = displayValues(row.values, row.unit, scale);
              const digits = digitsFor(shown, row.unit);
              return (
                <tr key={row.id} className={row.absent ? 'fin-absent-row' : undefined}>
                  <th scope="row" className="fin-sticky">
                    <div className="fin-row-head">
                      <span className="fin-row-name">{row.label}</span>
                      <button
                        type="button"
                        className="fin-row-toggle"
                        aria-label={`${row.label}，目前顯示${mode === 'value' ? '數值' : '年增率'}，點按切換為${mode === 'value' ? '年增率' : '數值'}`}
                        onClick={() => setModes((current) => ({ ...current, [key]: mode === 'value' ? 'yoy' : 'value' }))}
                      >
                        {mode === 'value' ? '數值' : '年增率'}
                      </button>
                    </div>
                    <small>
                      {unitText(row.unit, history.currency, scale)}
                      {row.derived ? ' · 部分推算' : ''}
                    </small>
                    {row.note && <small className="fin-row-note">{row.note}</small>}
                  </th>
                  {order.map((index) => {
                    const result = row.results[index]!;
                    if (mode === 'yoy') {
                      const cell = changeCellText(row.changes[index] ?? null);
                      return (
                        <td key={series.end[index]} className={cell.tone} title={row.changes[index]?.reason ?? undefined}>
                          {cell.text}
                        </td>
                      );
                    }
                    const value = shown[index] ?? null;
                    const derived = result.basis === 'derived' && value !== null;
                    const classes = [value !== null && value < 0 ? 'negative' : '', derived ? 'derived' : ''].filter(Boolean).join(' ');
                    return (
                      <td
                        key={series.end[index]}
                        className={classes || undefined}
                        title={value === null ? (result.reason ?? undefined) : derived ? `推算：${result.formula}` : undefined}
                      >
                        {value === null ? '—' : formatNumber(value, digits)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            <tr className="fin-source-row">
              <th scope="row" className="fin-sticky">
                來源
              </th>
              {order.map((index) => (
                <td key={series.end[index]} className={series.src[index]}>
                  {SOURCE_LABEL[series.src[index] ?? 'yahoo']}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="source-note fin-table-note">
        數字表顯示所有已取得期別（圖表只畫近期）。最新期在最左側，向右捲動看較早期別。資本支出、股利、庫藏股以正數表示流出。「—」為無資料或不適用，滑鼠移上或長按可看原因；每列右側的「數值／年增率」可切換，年增率為同期比較（季度對去年同季）。
      </p>
    </div>
  );
}
