import { HelpDisclosure } from './HelpDisclosure';
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
  type MaRecommendationRange,
} from '../report/maRecommendation';
import { FixedStockParameters } from './FixedStockParameters';
import { parameterStorageMessage } from '../cloud/settingsApi';
import './research.css';
import './maRecommendation.css';

/** Recommended periods that are not yet a daily SMA on this symbol. */
export function missingRecommended(indicators: readonly IndicatorInstance[], periods: readonly number[]): number[] {
  const present = new Set(indicators.filter((i) => i.type === 'SMA' && i.scope.timeframe === '1D').map((i) => i.period));
  return periods.filter((period) => !present.has(period));
}

/** Fixed parameters represent daily SMAs; include only periods the user has already applied. */
export function appliedDailySmaPeriods(indicators: readonly IndicatorInstance[]): number[] {
  return [...new Set(indicators.filter((indicator) => indicator.type === 'SMA' && indicator.scope.timeframe === '1D')
    .map((indicator) => indicator.period))].sort((a, b) => a - b);
}

/** A final pick can be outside the scored candidates; it remains selectable as well. */
export function selectableCandidates(range: MaRecommendationRange) {
  return range.candidates.some((candidate) => candidate.period === range.final)
    ? range.candidates
    : [...range.candidates, { period: range.final, score: null, sarReturn: null, sarDrawdown: null, sarWinRate: null, sarTrades: null }];
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
      lineWidth: 2,
      widthMode: 'pixels',
      color: indicatorColors[(used + index) % indicatorColors.length]!,
      scope: { timeframe: '1D' },
    }),
  );
  if (store.symbol(symbol).preferences.timeframe !== '1D')
    store.updateSymbol(symbol, (s) => ({ ...s, preferences: { ...s.preferences, timeframe: '1D' } }));
  return missing;
}

/** Toggle only this period's daily SMAs, preserving locked instances and all other scopes. */
export function toggleRecommended(store: AppStore, symbol: string, period: number) {
  const matches = store.symbol(symbol).indicators.filter((indicator) =>
    indicator.type === 'SMA' && indicator.scope.timeframe === '1D' && indicator.period === period);
  if (!matches.length) {
    applyRecommended(store, symbol, [period]);
    return { added: true, removed: 0, locked: 0 };
  }
  const removable = matches.filter((indicator) => !indicator.locked);
  if (removable.length) {
    // The selector owns daily SMAs; the store removes only the active timeframe.
    applyRecommended(store, symbol, [period]);
    removable.forEach((indicator) => store.removeIndicator(symbol, indicator.id));
  }
  return { added: false, removed: removable.length, locked: matches.length - removable.length };
}

const scoreText = (score: number | null) => (score === null ? '' : `（${Math.round(score)}）`);
/** The pick's own structural score (candidates carry it; a pick outside the list has none). */
const scoreOf = (range: MaRecommendationRange) => range.candidates.find((c) => c.period === range.final)?.score ?? null;
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
  const applied = appliedDailySmaPeriods(indicators);
  const apply = (selected: readonly number[]) => {
    const added = applyRecommended(store, symbol, selected);
    onNotice(added.length
      ? `已在日線圖加入 SMA ${added.join(' / ')}。`
      : `日線圖已有 SMA ${selected.join(' / ')}，已切換到日線。`);
  };
  const toggle = (period: number) => {
    const changed = toggleRecommended(store, symbol, period);
    onNotice(changed.added ? `已在日線圖加入 SMA ${period}。`
      : changed.removed ? `已從日線圖移除 SMA ${period}${changed.locked ? ' 的未鎖定均線；同參數鎖定均線仍保留。' : '。'}`
        : `SMA ${period} 已鎖定，請先解鎖後再取消套用。`);
  };
  const link = maSelectorLink(symbol);
  const ranges = data ? MA_RANGE_KEYS.map((key) => data.ranges.find((range) => range.key === key)!) : [];
  return (
    <div className="ws-ma-reco" data-testid="ma-recommendation" aria-busy={!result}>
      <dl className="ws-volatility-values">
        <div title="ma-selector 依日線選出「最能分出多空」的 SMA：短期 15–33、中期 34–45、長期 46–110（每月更新）。括號內為結構分數。">
          <dt>建議均線</dt>
          <dd>
            {data
              ? ranges.map((range) => `${MA_RANGE_LABELS[range.key].slice(0, 1)} ${range.final}${scoreText(scoreOf(range))}`).join(' · ')
              : '—'}
          </dd>
        </div>
      </dl>
      <p className="ws-ma-reco-status">
        {!result
          ? '建議均線載入中'
          : result.status === 'ok'
            ? `日線至 ${result.data.asof}${result.data.limited ? ' · 資料不足 3.5 年，中長期僅供參考' : ''}`
            : result.message}
      </p>
      {data && (
        <>
          <div className="ws-ma-reco-actions">
            <button
              type="button"
              className="primary-button"
              disabled={!missing.length && timeframe === '1D'}
              onClick={() => apply(periods)}
            >
              {missing.length ? '套用全部建議' : timeframe === '1D' ? '建議已套用' : '切到日線查看'}
            </button>
            <button type="button" className="ghost-button" disabled={!applied.length}
              title={applied.length ? '以已套用的日線 SMA 編輯固定參數' : '請先套用日線 SMA'}
              onClick={() => setFixing(true)}>
              固定為個股參數
            </button>
          </div>
          <table className="ws-ma-reco-table">
            <thead>
              <tr>
                <th scope="col">區間</th>
                <th scope="col">候選（點選套用／取消，★＝建議）</th>
                <th scope="col">反手報酬</th>
              </tr>
            </thead>
            <tbody>
              {ranges.map((range) => {
                const final = range.candidates.find((c) => c.period === range.final);
                return (
                  <tr key={range.key}>
                    <th scope="row">
                      {MA_RANGE_LABELS[range.key]}
                      <small>
                        {range.min}–{range.max}
                      </small>
                    </th>
                    <td>
                      <div className="ws-ma-candidates">
                        {selectableCandidates(range).map((candidate) => {
                          const present = applied.includes(candidate.period);
                          return <button key={candidate.period} type="button" className="ghost-button ws-ma-candidate"
                            aria-label={present ? `取消套用 SMA ${candidate.period}` : `套用 SMA ${candidate.period}`}
                            aria-pressed={present}
                            onClick={() => toggle(candidate.period)}>
                            {candidate.period === range.final ? '★' : ''}{candidate.period}{scoreText(candidate.score)}
                            {present ? ' ✓' : ''}
                          </button>;
                        })}
                      </div>
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
          <HelpDisclosure label="建議均線與分數說明">
            {ranges.map((range) => `${MA_RANGE_LABELS[range.key]} ${range.min}–${range.max}`).join('、')}（每月更新），括號內為結構分數。分數＝結構分數（0–100）：突破、二日、回測、雜訊、糾結等指標相對 83 檔基準的百分位加權，越高越能分出多空。候選＝與最高分相差 ≤15 分；建議＝多空反手報酬距最佳
            ≤5 個百分點者中分數最高，所以★不一定是分數最高的那條。反手報酬為歷史模擬，不代表未來績效。股票適合度{' '}
            {data.suitability.short === null ? '—' : `${Math.round(data.suitability.short)} / 100`}。
          </HelpDisclosure>
        </>
      )}
      <a href={link} target="_blank" rel="noopener noreferrer">
        {data ? '在 ma-selector 查看完整候選、判定圖與回測 ↗' : '在 ma-selector 即時計算這檔股票 ↗'}
      </a>
      {fixing && data && (
        <FixedStockParameters
          ticker={symbol}
          defaults={applied}
          prefill
          onClose={() => setFixing(false)}
          onSaved={(values) => {
            setFixing(false);
            onNotice(parameterStorageMessage(`${symbol} 固定均線 ${values.join(' / ')}`));
          }}
        />
      )}
    </div>
  );
}
