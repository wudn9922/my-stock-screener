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
  v2Periods,
  type MaRecommendationLoadResult,
  type MaRecommendationRange,
  type MaRecommendationV2,
} from '../report/maRecommendation';
import { FixedStockParameters } from './FixedStockParameters';
import { parameterMAs, parameterStorageMessage, type ParameterStock } from '../cloud/settingsApi';
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
const beats = (rank: { pct: number } | null | undefined) => (rank ? `贏過 ${Math.round(rank.pct)}%` : '');

/** One toggle button per candidate period (shared by the current and the experimental v2 tables). */
function CandidateButton({ period, star, score, extra, applied, onToggle }: {
  period: number;
  star: boolean;
  score: number | null;
  /** Secondary score shown under the period, e.g. 「2年 63」. */
  extra?: string;
  applied: readonly number[];
  onToggle: (period: number) => void;
}) {
  const present = applied.includes(period);
  return <button type="button" className="ghost-button ws-ma-candidate"
    aria-label={present ? `取消套用 SMA ${period}` : `套用 SMA ${period}`}
    aria-pressed={present}
    onClick={() => onToggle(period)}>
    {star ? '★' : ''}{period}{scoreText(score)}{present ? ' ✓' : ''}
    {extra && <small className="ws-ma-candidate-extra">{extra}</small>}
  </button>;
}

/** Experimental selector v2 (not validated): shown for comparison, never replaces the current picks. */
function MaRecommendationV2({ v2, applied, onToggle, onApply }: {
  v2: MaRecommendationV2;
  applied: readonly number[];
  onToggle: (period: number) => void;
  onApply: (periods: readonly number[]) => void;
}) {
  const { short, medium, long, suitability, rank } = v2;
  const shortCandidates = [...short.candidates, ...short.relaxed.filter((c) => !short.candidates.some((d) => d.period === c.period))];
  const shortShown = shortCandidates.filter((c) => c.period === short.final1 || c.period === short.final2 || short.candidates.includes(c));
  const second = short.final2 === null ? '無第二條' : short.secondSource === 'relaxed' ? '第二條放寬' : null;
  const periods = v2Periods(v2);
  const missing = periods.filter((p) => !applied.includes(p));
  const suit = (label: string, value: number | null, r?: { pct: number } | null) =>
    value === null ? null : <li key={label}>{label} {Math.round(value)}{r ? `（${beats(r)}）` : ''}</li>;
  return (
    <details className="help-disclosure ws-ma-v2" data-testid="ma-recommendation-v2">
      <summary>實驗選線 v2（尚未驗證）：短 {short.final1}{short.final2 === null ? '' : `／${short.final2}`} · 中 {medium.final} · 長 {long.final}</summary>
      <div className="help-disclosure-body">
        <p className="ws-ma-reco-status">
          短期 {short.min}–{short.max} 看近 2 年選 2 條、中期 {medium.min}–{medium.max} 看近 3 年選 1 條，長期同現行。只供比較，不取代上面的建議；規則只在舊區間驗證過。
        </p>
        <table className="ws-ma-reco-table">
          <thead>
            <tr>
              <th scope="col">區間</th>
              <th scope="col">候選（2 年分數，★＝v2 選線）</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">短期<small>{short.min}–{short.max}</small>{second && <small>{second}</small>}</th>
              <td>
                <div className="ws-ma-candidates">
                  {shortShown.map((c) => <CandidateButton key={c.period} period={c.period} star={c.period === short.final1 || c.period === short.final2}
                    score={c.score2y} extra={c.score1y === null ? undefined : `1年 ${Math.round(c.score1y)}`} applied={applied} onToggle={onToggle} />)}
                </div>
              </td>
            </tr>
            <tr>
              <th scope="row">中期<small>{medium.min}–{medium.max}</small></th>
              <td>
                <div className="ws-ma-candidates">
                  {(medium.candidates.some((c) => c.period === medium.final) ? medium.candidates : [...medium.candidates, { period: medium.final, score3y: null }])
                    .map((c) => <CandidateButton key={c.period} period={c.period} star={c.period === medium.final}
                      score={c.score3y} extra="3年" applied={applied} onToggle={onToggle} />)}
                </div>
              </td>
            </tr>
            <tr>
              <th scope="row">長期</th>
              <td>
                <div className="ws-ma-candidates">
                  <CandidateButton period={long.final} star score={null} extra="同現行" applied={applied} onToggle={onToggle} />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <ul className="ws-ma-v2-suitability">
          {suit('短期 2 年最高分', suitability.short2y, rank?.short2y)}
          {suit('短期 1 年最高分', suitability.short1y, rank?.short1y)}
          {suit('中期 3 年最高分', suitability.mid3y, rank?.mid3y)}
        </ul>
        <button type="button" className="ghost-button" disabled={!missing.length} onClick={() => onApply(periods)}>
          {missing.length ? '套用 v2 全部' : 'v2 已全部套用'}
        </button>
      </div>
    </details>
  );
}

export function MaRecommendation({
  store,
  symbol,
  timeframe,
  indicators,
  savedStock,
  parameterError,
  onNotice,
}: {
  store: AppStore;
  symbol: string;
  timeframe: Timeframe;
  /** All of the symbol's indicators (every timeframe). */
  indicators: readonly IndicatorInstance[];
  savedStock?: ParameterStock;
  parameterError?: string | null;
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
      {savedStock && <p className="ws-ma-reco-status">已保存均線：{parameterMAs(savedStock).join(' / ')}（{savedStock.group_id === 'ungrouped' ? '固定參數' : '原有分組'}）</p>}
      {parameterError && <p role="status">{parameterError}</p>}
      <button type="button" className="ghost-button" disabled={!applied.length && !savedStock}
        title="先查看已保存的參數，再決定是否使用目前圖表的均線" onClick={() => setFixing(true)}>固定為個股參數</button>
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
                      {range.rank && <small title={`區間最高分 ${range.rank.best}，在 ${range.rank.n} 檔基準股票的區間最高分中${beats(range.rank)}`}>{beats(range.rank)}</small>}
                    </th>
                    <td>
                      <div className="ws-ma-candidates">
                        {selectableCandidates(range).map((candidate) => (
                          <CandidateButton key={candidate.period} period={candidate.period} star={candidate.period === range.final} score={candidate.score}
                            extra={'score2y' in candidate && candidate.score2y !== null ? `2年 ${Math.round(candidate.score2y)}` : undefined}
                            applied={applied} onToggle={toggle} />
                        ))}
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
          {data.v2 && <MaRecommendationV2 v2={data.v2} applied={applied} onToggle={toggle} onApply={apply} />}
          <HelpDisclosure label="建議均線與分數說明">
            {ranges.map((range) => `${MA_RANGE_LABELS[range.key]} ${range.min}–${range.max}`).join('、')}（每月更新），括號內為結構分數。分數＝結構分數（0–100）：突破、二日、回測、雜訊、糾結等指標相對 83 檔基準的百分位加權，越高越能分出多空。候選＝與最高分相差 ≤15 分；建議＝多空反手報酬距最佳
            ≤5 個百分點者中分數最高，所以★不一定是分數最高的那條。反手報酬為歷史模擬，不代表未來績效。股票適合度{' '}
            {data.suitability.short === null ? '—' : `${Math.round(data.suitability.short)} / 100`}。
            「贏過 X%」＝這檔的區間最高分（平滑後）在 83 檔基準股票各自的區間最高分中贏過幾 %；「2年」＝同一個短期公式在近 2 年的分數，短期與中期可以用同一把尺比較。兩者只是參考，不影響選線。
          </HelpDisclosure>
        </>
      )}
      <a href={link} target="_blank" rel="noopener noreferrer">
        {data ? '在 ma-selector 查看完整候選、判定圖與回測 ↗' : '在 ma-selector 即時計算這檔股票 ↗'}
      </a>
      {fixing && (
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
