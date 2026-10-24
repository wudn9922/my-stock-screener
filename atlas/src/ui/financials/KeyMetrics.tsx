import { useMemo, useState } from 'react';
import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { formatDateTime } from '../format';
import { barLayout, barPath } from './barGeometry';
import { initialBarViewport, panBarViewport, zoomBarViewport, type BarViewport } from './barViewport';
import { FoldCard } from './FoldCard';
import { FinancialNote } from './FinancialNote';
import {
  KEY_SPARK_PERIODS,
  formatValue,
  keyChangeText,
  keyMetricsSummary,
  keySummary,
  moneyScale,
  unitText,
  type FinancialsView,
  type KeyTile,
} from './financialsView';

/**
 * Folded by default: the newest period (annual or quarterly, as selected) for revenue, gross profit, net income,
 * EPS and the three margins. Mini bars start at the last eight periods; their shared time controls leave
 * the newest-period headline and YoY unchanged. Pure formatting lives in financialsView.ts.
 */
export function KeyMetrics({ history, view }: { history: FinancialHistory; view: FinancialsView }) {
  const summary = useMemo(() => keySummary(view), [view]);
  const period = summary.index === null ? null : (view.labels[summary.index] ?? null);
  const total = summary.index === null ? 0 : summary.index + 1;
  const resetKey = `${history.symbol}|${view.kind}|${total}`;
  const [savedViewport, setViewport] = useState<{ key: string; range: BarViewport } | null>(null);
  const viewport = savedViewport?.key === resetKey ? savedViewport.range : initialBarViewport(total, KEY_SPARK_PERIODS);
  const visibleCount = viewport.end - viewport.start;
  const setRange = (range: BarViewport) => setViewport({ key: resetKey, range });
  const rangeLabel = `${view.labels[viewport.start]?.long ?? ''}${visibleCount > 1 ? ` – ${view.labels[viewport.end - 1]?.long ?? ''}` : ''}`;
  const tiles = summary.tiles.map((tile) => ({
    ...tile,
    spark: view.metrics[tile.id].values.slice(viewport.start, viewport.end).map((value, index) => ({ index: viewport.start + index, value })),
  }));
  return (
    <FoldCard className="fin-key" title="關鍵指標" summary={keyMetricsSummary(view, history.currency)}>
      {period && (
        <div className="fin-key-head">
          <span className="fin-key-period">
            {period.long} <small>最新期</small>
          </span>
        </div>
      )}
      {summary.index === null ? (
        <p className="fin-empty">這檔股票沒有可用的期別。</p>
      ) : (
        <>
        <div className="fin-chart-time" role="group" aria-label="關鍵指標迷你柱時間軸">
          <button type="button" aria-label="往前移動時間軸" title="較早期間" disabled={viewport.start === 0} onClick={() => setRange(panBarViewport(viewport, total, -1))}>←</button>
          <button type="button" aria-label="縮小時間軸" title="顯示更多期間" disabled={visibleCount >= total} onClick={() => setRange(zoomBarViewport(viewport, total, 'out'))}>−</button>
          <button type="button" aria-label="放大時間軸" title="顯示更少期間" disabled={visibleCount <= 1} onClick={() => setRange(zoomBarViewport(viewport, total, 'in'))}>＋</button>
          <button type="button" aria-label="往後移動時間軸" title="較近期間" disabled={viewport.end >= total} onClick={() => setRange(panBarViewport(viewport, total, 1))}>→</button>
          <button type="button" aria-label="重設時間軸" onClick={() => setRange(initialBarViewport(total, KEY_SPARK_PERIODS))}>重設</button>
          <span className="fin-chart-window" aria-live="polite">{visibleCount} {view.kind === 'annual' ? '年' : '季'} · {rangeLabel}</span>
        </div>
        <ul className="fin-key-grid">
          {tiles.map((tile) => (
            <KeyCard key={tile.id} tile={tile} view={view} currency={history.currency} />
          ))}
        </ul>
        </>
      )}
      <FinancialNote className="fin-key-note" label="關鍵指標說明"><p>
        年增率：金額與 EPS 為同期成長率（季度對去年同季）；毛利率、營業利益率、淨利率為百分點差。迷你柱預設近 {KEY_SPARK_PERIODS} 期，由舊到新，可使用時間軸放大、縮小或查看較早期間；上方數值維持最新期。
        毛利財報未列示時，以營收減營業成本推算（標示「推算」）。資料更新日{' '}
        {history.checkedAt ? formatDateTime(history.checkedAt, false) : '—'}，僅供研究參考。
      </p></FinancialNote>
    </FoldCard>
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

/** Visible periods as mini columns (zero baseline, negatives downward); a missing period is a flat tick. */
function MiniBars({ tile, view, spoken }: { tile: KeyTile; view: FinancialsView; spoken: (value: number) => string }) {
  if (!tile.spark.length) return null;
  const layout = barLayout(
    tile.spark.map((point) => point.value),
    100,
    { plot: 30, labelPad: 2, axis: 0 },
  );
  return (
    <svg className="fin-spark" viewBox={`0 0 100 ${layout.height}`} preserveAspectRatio="none" role="img" aria-label={`${tile.label} ${view.labels[tile.spark[0]!.index]?.long ?? ''} 至 ${view.labels[tile.spark.at(-1)!.index]?.long ?? ''} · ${tile.spark.length} 期`}>
      <line className="fin-spark-zero" x1={0} x2={100} y1={layout.zeroY} y2={layout.zeroY} />
      {layout.bars.map((bar) => {
        const point = tile.spark[bar.index]!;
        const label = view.labels[point.index]?.long ?? '';
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
