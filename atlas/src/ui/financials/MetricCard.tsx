import { useMemo, useState } from 'react';
import type { FinancialHistory, PeriodSource } from '../../fundamentals/FinancialHistory';
import { yoy, type MetricGroupId, type MetricId, type MetricInput, type MetricUnit } from '../../fundamentals/financialMetrics';
import { formatDateTime } from '../format';
import { BarChart } from './BarChart';
import { FoldCard } from './FoldCard';
import { FinancialNote } from './FinancialNote';
import {
  CHART_PERIODS,
  averageText,
  cagrText,
  digitsFor,
  displayValues,
  formatExact,
  formatNumber,
  formatValue,
  moneyScale,
  oiFieldText,
  periodSourceText,
  unitText,
  yoyText,
  type FinancialsView,
  type MoneyScale,
} from './financialsView';

export interface MetricCardProps {
  group: { id: MetricGroupId; title: string; metrics: MetricId[] };
  history: FinancialHistory;
  view: FinancialsView;
  metric: MetricId;
  onMetric: (id: MetricId) => void;
  /** Shown above the tabs, e.g. why bank statements have no gross margin. */
  note?: string | null;
  /** Open at first render (growth and profitability); the other cards start folded. */
  defaultOpen?: boolean;
  /** One line in the card head while folded or open. */
  summary?: string | null;
}

const BASIS_TEXT: Record<string, string> = {
  reported: '財報數字',
  derived: '推算',
  basic: '基本 EPS',
  approx: '近似值',
  'statutory-tax': '法定稅率',
  ttm: '近 4 季（TTM）',
};

function inputText(input: MetricInput, currency: string, scale: MoneyScale) {
  if (input.value === null) return '—';
  const unit: MetricUnit = input.unit;
  const value = formatValue(input.value, unit, unit === 'money' ? moneyScale([input.value]) : scale);
  const suffix = unit === 'money' ? unitText(unit, currency, moneyScale([input.value])) : unitText(unit, currency);
  return `${value} ${suffix}`;
}

/** One group (成長, 獲利能力…): metric tabs, headline value with YoY / CAGR chips, column chart and the tapped bar's details. */
export function MetricCard({ group, history, view, metric, onMetric, note, defaultOpen = false, summary }: MetricCardProps) {
  const data = view.metrics[metric];
  const series = history[view.kind];
  const windowValues = useMemo(() => data.window.map((index) => data.values[index] ?? null), [data]);
  const scale = useMemo(() => moneyScale(windowValues), [windowValues]);
  const shown = useMemo(() => displayValues(windowValues, data.unit, scale), [windowValues, data.unit, scale]);
  const digits = digitsFor(shown, data.unit);
  const texts = useMemo(() => shown.map((value) => (value === null ? null : formatNumber(value, digits))), [shown, digits]);
  // Money and EPS get a YoY line over the bars (ratios do not: a percentage of a percentage says little).
  const growthMetric = data.unit === 'money' || data.unit === 'eps';
  const yoyValues = useMemo(() => data.window.map((index) => data.changes[index]?.value ?? null), [data]);
  const overlay = useMemo(
    () => (growthMetric && yoyValues.some((value) => value !== null) ? { values: yoyValues, label: `${data.label}年增率` } : undefined),
    [growthMetric, yoyValues, data.label],
  );
  const [selectedState, setSelected] = useState<{ key: string; index: number } | null>(null);
  // The window is part of the key: a new range re-selects the latest bar instead of a shifted position.
  const resetKey = `${history.symbol}|${view.kind}|${metric}|${data.window[0] ?? ''}|${data.window.length}`;
  const latestInWindow = data.latestIndex !== null ? data.window.indexOf(data.latestIndex) : -1;
  const selected = selectedState?.key === resetKey ? selectedState.index : latestInWindow >= 0 ? latestInWindow : data.window.length - 1;
  const selectedPeriod = data.window[selected];
  const unit = unitText(data.unit, history.currency, scale);
  const latest = data.latestIndex === null ? null : data.results[data.latestIndex];
  const chip = yoyText(data.yoy, data.unit);
  const second = data.cagr ? cagrText(data.cagr) : averageText(data.average, data.unit, history.currency);
  const empty = windowValues.every((value) => value === null);
  const quarterly = view.kind === 'quarterly';
  // More than ten annual bars: two-digit years so neighbouring labels do not collide on a phone.
  const shortYears = !quarterly && data.window.length > CHART_PERIODS.annual;
  const axis = data.window.map((index, position) => {
    const label = view.labels[index]!;
    if (!quarterly) return { main: shortYears ? `'${String(label.fy).slice(2)}` : label.short };
    const showYear = label.fq === 1 || position === 0;
    return { main: label.short, sub: showYear ? `FY${String(label.fy).slice(2)}` : '' };
  });
  const ariaLabels = data.window.map((index) => {
    const value = data.values[index] ?? null;
    const change = growthMetric ? `，${yoyText(data.changes[index] ?? null, data.unit).text}` : '';
    return `${view.labels[index]!.long} ${data.label} ${value === null ? '無資料' : `${formatValue(value, data.unit, scale)} ${unit}`}${change}`;
  });
  const oiText = oiFieldText(latest ?? undefined);
  // Same scale and decimals as the chart labels, so the headline matches its bar.
  const headline = (value: number) => formatNumber(data.unit === 'money' ? value / scale.divisor : value, digits);
  const latestLabel = data.latestIndex === null ? null : view.labels[data.latestIndex];

  return (
    <FoldCard className="fin-card" title={group.title} summary={summary} defaultOpen={defaultOpen} dataAttributes={{ 'data-group': group.id }}>
      {note && <FinancialNote className="fin-card-note" label="財報口徑說明"><p>{note}</p></FinancialNote>}
      <div className="fin-tabs" role="group" aria-label={`${group.title}指標`}>
        {group.metrics.map((id) => (
          <button key={id} type="button" className={id === metric ? 'active' : ''} aria-pressed={id === metric} onClick={() => onMetric(id)}>
            {view.metrics[id].label}
          </button>
        ))}
      </div>
      <div className="fin-metric">
        {latest && latest.value !== null && (
          <>
            <div className="fin-headline">
              <b className="fin-big">{headline(latest.value)}</b>
              <span className="fin-big-unit">{unit}</span>
              {latestLabel && <span className="fin-period">{latestLabel.long}</span>}
            </div>
            <div className="fin-chips">
              <span className={`fin-chip ${chip.tone}`}>{chip.text}</span>
              <span className={`fin-chip ${second.tone}`}>{second.text}</span>
              {latest.abnormal && <span className="fin-chip warn">異常</span>}
              {latest.basis !== 'reported' && <span className="fin-chip basis">{BASIS_TEXT[latest.basis]}</span>}
            </div>
          </>
        )}
        {oiText && <p className="fin-sub">{oiText}</p>}
        <p className="fin-chart-title">
          {data.label}（{unit}）· {quarterly ? `近 ${data.window.length} 季` : `近 ${data.window.length} 年`}
        </p>
        {growthMetric && (
          <p className="fin-legend">
            {overlay ? (
              <>
                <span className="fin-legend-bar" aria-hidden="true" />
                {data.label}
                <span className="fin-legend-line" aria-hidden="true" />
                年增率（右軸，%）· 同期比較
              </>
            ) : (
              '年增率：此區間沒有可計算的點（去年同期缺值或 ≤ 0）'
            )}
          </p>
        )}
        {empty ? (
          <p className="fin-empty">{data.results[data.results.length - 1]?.reason ?? '沒有資料'}，此指標不顯示。</p>
        ) : (
          <BarChart
            values={shown}
            texts={texts}
            axis={axis}
            ariaLabels={ariaLabels}
            labelMode={quarterly ? 'extremes' : 'all'}
            selected={selected}
            onSelect={(index) => setSelected({ key: resetKey, index })}
            title={`${data.label}直條圖`}
            overlay={overlay}
          />
        )}
        {!empty && selectedPeriod !== undefined && (
          <Detail
            history={history}
            view={view}
            metric={metric}
            period={selectedPeriod}
            scale={scale}
            source={series.src[selectedPeriod] ?? 'yahoo'}
          />
        )}
      </div>
    </FoldCard>
  );
}

function Detail({
  history,
  view,
  metric,
  period,
  scale,
  source,
}: {
  history: FinancialHistory;
  view: FinancialsView;
  metric: MetricId;
  period: number;
  scale: MoneyScale;
  source: PeriodSource;
}) {
  const data = view.metrics[metric];
  const result = data.results[period]!;
  const label = view.labels[period]!;
  const change = yoy(data.values, history[view.kind].end, period, data.unit);
  const changeChip = yoyText(change, data.unit);
  const valueScale = data.unit === 'money' ? moneyScale([result.value]) : scale;
  return (
    <div className="fin-detail" aria-live="polite">
      <div className="fin-detail-head">
        <b>{label.long}</b>
        <span>期末 {formatDateTime(label.end, false)}</span>
        <span className={`fin-source ${source}`}>{periodSourceText(history[view.kind], period)}</span>
      </div>
      <div className="fin-detail-value">
        <b>
          {formatValue(result.value, data.unit, valueScale)} {result.value === null ? '' : unitText(data.unit, history.currency, valueScale)}
        </b>
        {result.value !== null && data.unit === 'money' && <small>= {formatExact(result.value, data.unit)} {history.currency}</small>}
        <span className={`fin-chip ${changeChip.tone}`}>{changeChip.text}</span>
      </div>
      <FinancialNote label="公式與計算說明">
      <p className="fin-formula">
        <span>公式</span>
        {result.formula}
      </p>
      {result.inputs.length > 0 && (
        <dl className="fin-inputs">
          {result.inputs.map((input) => (
            <div key={input.label}>
              <dt>{input.label}</dt>
              <dd>{inputText(input, history.currency, scale)}</dd>
            </div>
          ))}
        </dl>
      )}
      {result.reason && <p className="fin-reason">未計算：{result.reason}</p>}
      {result.notes.map((note) => (
        <p key={note} className={`fin-note${result.abnormal && note.startsWith('異常') ? ' warn' : ''}`}>
          {note}
        </p>
      ))}
      </FinancialNote>
    </div>
  );
}
