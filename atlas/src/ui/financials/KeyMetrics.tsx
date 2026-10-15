import { useMemo } from 'react';
import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { formatDateTime } from '../format';
import { barLayout, barPath } from './barGeometry';
import {
  KEY_SPARK_PERIODS,
  formatValue,
  keyChangeText,
  keySummary,
  moneyScale,
  unitText,
  type FinancialsView,
  type KeyTile,
} from './financialsView';

/**
 * First-screen summary: the newest period (annual or quarterly, as selected) for revenue, gross profit,
 * net income, EPS and the three margins. Each tile shows the value, its year-over-year change and the last
 * eight periods as mini bars. Pure formatting lives in financialsView.ts.
 */
export function KeyMetrics({ history, view }: { history: FinancialHistory; view: FinancialsView }) {
  const summary = useMemo(() => keySummary(view), [view]);
  const period = summary.index === null ? null : (view.labels[summary.index] ?? null);
  return (
    <section className="card fin-key" aria-labelledby="fin-key-title">
      <div className="fin-key-head">
        <h2 id="fin-key-title">關鍵指標</h2>
        {period && (
          <span className="fin-key-period">
            {period.long} <small>最新期</small>
          </span>
        )}
      </div>
      {summary.index === null ? (
        <p className="fin-empty">這檔股票沒有可用的期別。</p>
      ) : (
        <ul className="fin-key-grid">
          {summary.tiles.map((tile) => (
            <KeyCard key={tile.id} tile={tile} view={view} currency={history.currency} />
          ))}
        </ul>
      )}
      <p className="fin-key-note">
        年增率：金額與 EPS 為同期成長率（季度對去年同季）；毛利率、營業利益率、淨利率為百分點差。迷你柱為近 {KEY_SPARK_PERIODS} 期，由舊到新。
        毛利財報未列示時，以營收減營業成本推算（標示「推算」）。資料更新日{' '}
        {history.checkedAt ? formatDateTime(history.checkedAt, false) : '—'}，僅供研究參考。
      </p>
    </section>
  );
}

function KeyCard({ tile, view, currency }: { tile: KeyTile; view: FinancialsView; currency: string }) {
  const scale = moneyScale(tile.value === null ? [] : [tile.value]);
  const chip = keyChangeText(tile.change);
  const spokenValue = (value: number) => `${formatValue(value, tile.unit, moneyScale([value]))}${unitText(tile.unit, currency, moneyScale([value]))}`;
  return (
    <li className="fin-key-tile" title={tile.value === null ? (tile.reason ?? undefined) : undefined}>
      <div className="fin-key-top">
        <span className="fin-key-label">{tile.label}</span>
        {tile.basis === 'derived' && <span className="fin-chip basis">推算</span>}
      </div>
      {tile.value === null ? (
        <>
          <b className="fin-key-value null">—</b>
          <span className="fin-key-reason">{tile.reason}</span>
        </>
      ) : (
        <>
          <b className="fin-key-value">
            {formatValue(tile.value, tile.unit, scale)}
            <small>{unitText(tile.unit, currency, scale)}</small>
          </b>
          <span className={`fin-chip ${chip.tone}`}>{chip.text}</span>
        </>
      )}
      <MiniBars tile={tile} view={view} spoken={spokenValue} />
    </li>
  );
}

/** Eight periods as mini columns (zero baseline, negatives downward); a missing period is a flat tick. */
function MiniBars({ tile, view, spoken }: { tile: KeyTile; view: FinancialsView; spoken: (value: number) => string }) {
  if (!tile.spark.length) return null;
  const layout = barLayout(
    tile.spark.map((point) => point.value),
    100,
    { plot: 30, labelPad: 2, axis: 0 },
  );
  return (
    <svg className="fin-spark" viewBox={`0 0 100 ${layout.height}`} preserveAspectRatio="none" role="img" aria-label={`${tile.label}近 ${tile.spark.length} 期`}>
      <line className="fin-spark-zero" x1={0} x2={100} y1={layout.zeroY} y2={layout.zeroY} />
      {layout.bars.map((bar) => {
        const point = tile.spark[bar.index]!;
        const label = view.labels[point.index]?.short ?? '';
        if (point.value === null)
          return (
            <rect key={point.index} className="fin-spark-null" x={bar.cx - 2} y={layout.zeroY - 0.5} width={4} height={1}>
              <title>{`${label} 無資料`}</title>
            </rect>
          );
        return (
          <path
            key={point.index}
            className={`fin-spark-bar${bar.negative ? ' negative' : ''}`}
            d={barPath(bar, layout.zeroY, 1)}
          >
            <title>{`${label} ${spoken(point.value)}`}</title>
          </path>
        );
      })}
    </svg>
  );
}
