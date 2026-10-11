import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { formatNumber, formatValue, moneyScale, unitText, type FinancialsView } from './financialsView';

interface Tile {
  key: string;
  label: string;
  value: string;
  sub: string;
  tone: 'up' | 'down' | 'flat';
}

const toneOf = (value: number | null) => (value === null || Math.abs(value) < 1e-9 ? 'flat' : value > 0 ? 'up' : 'down');

/**
 * Headline figures: revenue and EPS CAGR and average ROIC from the annual chart window, latest gross
 * margin and free cash flow from the period type on screen.
 */
export function SummaryChips({ history, annual, current }: { history: FinancialHistory; annual: FinancialsView; current: FinancialsView }) {
  const tiles: Tile[] = [];
  for (const [id, label] of [
    ['revenue', '營收 CAGR'],
    ['eps', 'EPS CAGR'],
  ] as const) {
    const result = annual.metrics[id].cagr;
    tiles.push({
      key: id,
      label,
      value: result?.value === null || !result ? '—' : `${result.value > 0 ? '+' : ''}${formatNumber(result.value, 1)}%`,
      sub:
        result?.value !== null && result?.years
          ? `年度 · ${formatNumber(result.years, 1)} 年`
          : result?.reason === '期間有虧損，無法計算'
            ? '期間有虧損'
            : '年度資料不足',
      tone: toneOf(result?.value ?? null),
    });
  }
  const roic = annual.metrics.roic.average;
  tiles.push({
    key: 'roic',
    label: '平均 ROIC',
    value: roic?.value === null || !roic ? '—' : `${formatNumber(roic.value, 1)}%`,
    sub: roic && roic.count ? `年度 · ${roic.count} 年平均` : '不適用或無資料',
    tone: 'flat',
  });
  const gross = current.metrics.grossMargin;
  const grossLatest = gross.latestIndex === null ? null : gross.values[gross.latestIndex]!;
  tiles.push({
    key: 'gross',
    label: '最新毛利率',
    value: grossLatest === null ? '—' : `${formatNumber(grossLatest, 1)}%`,
    sub: gross.latestIndex === null ? '不適用或無資料' : current.labels[gross.latestIndex]!.long,
    tone: 'flat',
  });
  const fcf = current.metrics.freeCashFlow;
  const fcfLatest = fcf.latestIndex === null ? null : fcf.values[fcf.latestIndex]!;
  const scale = moneyScale([fcfLatest]);
  tiles.push({
    key: 'fcf',
    label: '最新 FCF',
    value: fcfLatest === null ? '—' : formatValue(fcfLatest, 'money', scale),
    sub: fcf.latestIndex === null ? '無資料' : `${unitText('money', history.currency, scale)} · ${current.labels[fcf.latestIndex]!.long}`,
    tone: fcfLatest !== null && fcfLatest < 0 ? 'down' : 'flat',
  });
  return (
    <div className="fin-summary" role="list" aria-label="重點數字">
      {tiles.map((tile) => (
        <div key={tile.key} className="fin-tile" role="listitem">
          <span className="fin-tile-label">{tile.label}</span>
          <b className={`fin-tile-value ${tile.tone}`}>{tile.value}</b>
          <span className="fin-tile-sub">{tile.sub}</span>
        </div>
      ))}
    </div>
  );
}
