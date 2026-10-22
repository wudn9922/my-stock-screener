import { useEffect, useState } from 'react';
import type { AppStore } from '../app/AppStore';
import { indicatorColors, type IndicatorInstance } from '../indicators/IndicatorRegistry';
import type { Timeframe } from '../market-data/MarketDataProvider';
import {
  MA_RANGE_KEYS,
  MA_RANGE_LABELS,
  fetchMaRecommendation,
  maSelectorLink,
  recommendedPeriods,
  type MaRecommendationLoadResult,
} from '../report/maRecommendation';
import { FixedStockParameters } from './FixedStockParameters';
import './research.css';

/** Recommended periods that are not yet a daily SMA on this symbol. */
export function missingRecommended(indicators: readonly IndicatorInstance[], periods: readonly number[]): number[] {
  const present = new Set(indicators.filter((i) => i.type === 'SMA' && i.scope.timeframe === '1D').map((i) => i.period));
  return periods.filter((period) => !present.has(period));
}

/** Adds the missing recommended SMAs to the daily chart and switches to it (the selector works on daily bars). */
export function applyRecommended(store: AppStore, symbol: string, periods: readonly number[]): number[] {
  const state = store.symbol(symbol);
  const missing = missingRecommended(state.indicators, periods);
  const used = state.indicators.filter((i) => i.scope.timeframe === '1D' && i.type !== 'Volume').length;
  missing.forEach((period, index) =>
    store.addIndicator({
      id: crypto.randomUUID(),
      symbol,
      type: 'SMA',
      period,
      source: 'close',
      visible: true,
      locked: false,
      lineWidth: 1,
      widthMode: 'pixels',
      color: indicatorColors[(used + index) % indicatorColors.length]!,
      scope: { timeframe: '1D' },
    }),
  );
  if (store.symbol(symbol).preferences.timeframe !== '1D')
    store.updateSymbol(symbol, (s) => ({ ...s, preferences: { ...s.preferences, timeframe: '1D' } }));
  return missing;
}

const percent = (value: number | null) => (value === null ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)}%`);

export function MaRecommendation({
  store,
  symbol,
  timeframe,
  indicators,
  onNotice,
}: {
  store: AppStore;
  symbol: string;
  timeframe: Timeframe;
  /** All of the symbol's indicators (every timeframe). */
  indicators: readonly IndicatorInstance[];
  onNotice: (message: string) => void;
}) {
  const [loaded, setLoaded] = useState<{ symbol: string; result: MaRecommendationLoadResult }>();
  const [fixing, setFixing] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    setFixing(false);
    void fetchMaRecommendation(symbol, undefined, fetch, abort.signal).then((result) => {
      if (!abort.signal.aborted) setLoaded({ symbol, result });
    });
    return () => abort.abort();
  }, [symbol]);
  const result = loaded?.symbol === symbol ? loaded.result : undefined;
  const data = result?.status === 'ok' ? result.data : undefined;
  const periods = data ? recommendedPeriods(data) : [];
  const missing = missingRecommended(indicators, periods);
  const link = maSelectorLink(symbol);
  const summary = !result ? '建議均線載入中' : data ? `日線至 ${data.asof}${data.limited ? ' · 資料不足 3.5 年' : ''} · 套用／候選` : '尚無建議 · 即時計算';
  return (
    <div className="ws-ma-reco" data-testid="ma-recommendation" aria-busy={!result}>
      <dl className="ws-volatility-values">
        <div title="ma-selector 依日線選出「最能分出多空」的 SMA：短期 15–33、中期 34–45、長期 46–110（每週更新）。">
          <dt>建議均線</dt>
          <dd>{data ? MA_RANGE_KEYS.map((key, index) => `${MA_RANGE_LABELS[key].slice(0, 1)} ${periods[index]}`).join(' · ') : '—'}</dd>
        </div>
      </dl>
      <details className="ws-ma-reco-info">
        <summary>{summary}</summary>
        <div className="ws-ma-reco-panel">
          {result && result.status !== 'ok' && <p>{result.message}</p>}
          {data && (
            <>
              <div className="ws-ma-reco-actions">
                <button
                  type="button"
                  className="primary-button"
                  disabled={!missing.length && timeframe === '1D'}
                  onClick={() => {
                    const added = applyRecommended(store, symbol, periods);
                    onNotice(
                      added.length
                        ? `已在日線圖加入 SMA ${added.join(' / ')}。`
                        : `日線圖已有 SMA ${periods.join(' / ')}，已切換到日線。`,
                    );
                  }}
                >
                  {missing.length ? '套用到圖表' : timeframe === '1D' ? '已套用' : '切到日線查看'}
                </button>
                <button type="button" className="ghost-button" onClick={() => setFixing(true)}>
                  固定為個股參數
                </button>
              </div>
              <table className="ws-ma-reco-table">
                <thead>
                  <tr>
                    <th scope="col">區間</th>
                    <th scope="col">候選（★＝建議）</th>
                    <th scope="col">反手報酬</th>
                  </tr>
                </thead>
                <tbody>
                  {MA_RANGE_KEYS.map((key) => {
                    const range = data.ranges.find((r) => r.key === key)!;
                    const final = range.candidates.find((c) => c.period === range.final);
                    return (
                      <tr key={key}>
                        <th scope="row">
                          {MA_RANGE_LABELS[key]}
                          <small>
                            {range.min}–{range.max}
                          </small>
                        </th>
                        <td>
                          {range.candidates.length
                            ? range.candidates.map((c) => `${c.period === range.final ? '★' : ''}${c.period}`).join('、')
                            : `★${range.final}`}
                        </td>
                        <td>
                          {percent(final?.sarReturn ?? null)}
                          {range.windowYears ? <small>近 {range.windowYears} 年</small> : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p>
                候選＝平滑後結構分數與最高分相差 ≤15 分；建議＝多空反手報酬距最佳 ≤5 個百分點者中結構分數最高（短期另有固定的 rescue 規則）。
                反手報酬為歷史模擬，不代表未來績效。股票適合度 {data.suitability.short === null ? '—' : `${Math.round(data.suitability.short)} / 100`}。
              </p>
            </>
          )}
          <a href={link} target="_blank" rel="noopener noreferrer">
            {data ? '在 ma-selector 查看完整候選、判定圖與回測 ↗' : '在 ma-selector 即時計算這檔股票 ↗'}
          </a>
        </div>
      </details>
      {fixing && data && (
        <FixedStockParameters
          ticker={symbol}
          defaults={periods}
          prefill
          onClose={() => setFixing(false)}
          onSaved={(values) => {
            setFixing(false);
            onNotice(`${symbol} 固定均線 ${values.join(' / ')} 已永久保存到 Supabase（無分組）。`);
          }}
        />
      )}
    </div>
  );
}
