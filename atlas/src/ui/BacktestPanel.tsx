import { HelpDisclosure } from './HelpDisclosure';
import { useEffect, useState, type FormEvent } from 'react';
import { closedBars, type BarResult, type Timeframe } from '../market-data/MarketDataProvider';
import { getMarketProfile, type MarketProfile } from '../market-data/MarketProfile';
import { runBacktest } from '../strategy/BacktestEngine';
import type {
  MovingAverageType,
  StrategyDirection,
  StrategyResult,
} from '../strategy/types';
import { reportError } from '../errors/UserErrors';

interface BacktestPanelProps {
  symbol: string;
  timeframe: Timeframe;
  result: BarResult | null;
  market?: MarketProfile;
  onResult: (result: StrategyResult | null) => void;
}

function numberText(value: number, digits = 2) {
  return Number.isFinite(value)
    ? value.toLocaleString(undefined, {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      })
    : '—';
}

function priceText(value: number, currency: string) {
  return `${currency} ${numberText(value)}`;
}

function percentText(value: number) {
  return `${(value * 100).toFixed(2)}%`;
}

function dateText(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : new Date(value * 1000).toLocaleString();
}

export function BacktestPanel({ symbol, timeframe, result, market, onResult }: BacktestPanelProps) {
  const currency =
    market?.currency ?? result?.market?.currency ?? getMarketProfile(symbol).currency;
  const [slowType, setSlowType] = useState<MovingAverageType>('SMA');
  const [slowPeriod, setSlowPeriod] = useState('21');
  const [entryAbovePct, setEntryAbovePct] = useState('0');
  const [exitBelowPct, setExitBelowPct] = useState('0');
  const [direction, setDirection] = useState<StrategyDirection>('long');
  const [initialCapital, setInitialCapital] = useState('10000');
  const [commissionBps, setCommissionBps] = useState('');
  const [slippageBps, setSlippageBps] = useState('');
  const [output, setOutput] = useState<StrategyResult | null>(null);
  const [closedBarCount, setClosedBarCount] = useState<number | null>(null);
  const [includedSessionCloseObservations, setIncludedSessionCloseObservations] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    setOutput(null);
    setClosedBarCount(null);
    setIncludedSessionCloseObservations(0);
    setError('');
    onResult(null);
  }, [symbol, timeframe, result, onResult]);

  const run = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    setIncludedSessionCloseObservations(0);
    try {
      if (!result) throw new Error('請等圖表 K 棒載入後再執行回測。');
      const asOf = result.asOf ?? Date.now() / 1000;
      const profile = market ?? result.market ?? getMarketProfile(symbol);
      const bars = closedBars({ ...result, market: profile }, timeframe, asOf);
      setClosedBarCount(bars.length);
      if (!bars.length) throw new Error('此週期沒有已收盤的 K 棒可用。');
      const slow = Number(slowPeriod),
        upper = Number(entryAbovePct),
        lower = Number(exitBelowPct),
        capital = Number(initialCapital);
      if (!Number.isInteger(slow) || slow < 1 || slow > 5000)
        throw new Error('參考均線週期必須是 1 到 5000 的整數。');
      if (!entryAbovePct.trim() || !Number.isFinite(upper) || upper < 0)
        throw new Error('均線上方百分比必須大於或等於 0。');
      if (!exitBelowPct.trim() || !Number.isFinite(lower) || lower < 0 || lower >= 100)
        throw new Error('均線下方百分比必須介於 0 到 100（不含 100）。');
      if (!Number.isFinite(capital) || capital <= 0)
        throw new Error('初始資金必須大於 0。');

      const optionalCost = (value: string, label: string, maximum: number) => {
        if (!value.trim()) return undefined;
        const parsed = Number(value);
        if (
          !Number.isFinite(parsed) ||
          parsed < 0 ||
          (label === 'Slippage' ? parsed >= maximum : parsed > maximum)
        ) {
          throw new Error(
            label === 'Slippage'
              ? '滑價必須介於 0 到 10000 個基點（不含 10000）。'
              : '手續費必須介於 0 到 10000 個基點。',
          );
        }
        return parsed;
      };
      const commission = optionalCost(commissionBps, 'Commission', 10_000);
      const slippage = optionalCost(slippageBps, 'Slippage', 10_000);
      const next = runBacktest({
        config: {
          symbol,
          timeframe,
          kind: 'price-cross',
          fastType: slowType,
          slowType,
          fastPeriod: slow,
          slowPeriod: slow,
          direction,
          entryAbovePct: upper,
          exitBelowPct: lower,
        },
        closedBars: bars,
        initialCapital: capital,
        costs:
          commission === undefined && slippage === undefined
            ? undefined
            : {
                ...(commission === undefined ? {} : { commissionBps: commission }),
                ...(slippage === undefined ? {} : { slippageBps: slippage }),
              },
      });
      setOutput(next);
      const observationTimes = new Set(result.sessionCloseObservations ?? []);
      setIncludedSessionCloseObservations(
        bars.filter((bar) => observationTimes.has(bar.time)).length,
      );
      onResult(next);
    } catch (caught) {
      setError(reportError('strategy', caught));
      setOutput(null);
      setClosedBarCount(0);
      onResult(null);
    }
  };

  const adjustedText = result?.adjusted
    ? '資料來源標示 OHLC 已調整。'
    : '除權息與分割調整的正確性未經驗證；本工具不另行調整 OHLC 價格。';

  return (
    <section className="research-panel backtest-panel" aria-label={`${symbol} 策略回測`}>
      <div className="section-heading">
        <span>策略回測</span>
        <span>
          {symbol} · {timeframe} · {currency}
        </span>
      </div>
      <p className="small muted">
        {result ? `來源：${result.source}${result.dataState === 'simulated' ? ' · 模擬資料' : ''}` : '等待圖表資料來源。'}
      </p>
      <HelpDisclosure label="回測執行與價格說明">
        研究用途：只使用已收盤 K 棒，以下一根 K 棒開盤價模擬成交；訊號不會下單。{adjustedText}
      </HelpDisclosure>
      <form className="research-form" onSubmit={run}>
        <p className="small">均線百分比進出場</p>
        <div className="research-field-row">
            <label>
              參考均線
              <select
                aria-label="參考均線類型"
                value={slowType}
                onChange={(event) => setSlowType(event.target.value as MovingAverageType)}
              >
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
              </select>
            </label>
            <label>
              參考均線週期
              <input
                aria-label="參考均線週期"
                type="number"
                min="1"
                max="5000"
                step="1"
                value={slowPeriod}
                onChange={(event) => setSlowPeriod(event.target.value)}
                required
              />
            </label>
          </div>
        <div className="research-field-row">
          <label>
            均線上方 %（{direction === 'long' ? '進場' : '出場'}）
            <input
              aria-label="均線上方百分比"
              type="number"
              min="0"
              step="any"
              value={entryAbovePct}
              onChange={(event) => setEntryAbovePct(event.target.value)}
              required
            />
          </label>
          <label>
            均線下方 %（{direction === 'long' ? '出場' : '進場'}）
            <input
              aria-label="均線下方百分比"
              type="number"
              min="0"
              max="99.99"
              step="any"
              value={exitBelowPct}
              onChange={(event) => setExitBelowPct(event.target.value)}
              required
            />
          </label>
        </div>
        <HelpDisclosure label="均線百分比進出場說明">
          做多：收盤由下往上超過均線上方指定百分比時進場，收盤由上往下低於均線下方指定百分比時出場；做空則相反。等於門檻不觸發，0% 表示均線本身。訊號均於下一根已收盤 K 棒的開盤價成交。
        </HelpDisclosure>
        <label>
          部位方向
          <select
            aria-label="部位方向"
            value={direction}
            onChange={(event) => setDirection(event.target.value as StrategyDirection)}
          >
            <option value="long">做多</option>
            <option value="short">做空</option>
          </select>
        </label>
        <label>
          初始資金（{currency}）
          <input
            aria-label="初始資金"
            type="number"
            min="0.01"
            step="any"
            value={initialCapital}
            onChange={(event) => setInitialCapital(event.target.value)}
            required
          />
        </label>
        <div className="research-field-row">
          <label>
            手續費（基點，選填）
            <input
              aria-label="手續費基點"
              type="number"
              min="0"
              max="10000"
              step="any"
              value={commissionBps}
              onChange={(event) => setCommissionBps(event.target.value)}
              placeholder="0"
            />
          </label>
          <label>
            滑價（基點，選填）
            <input
              aria-label="滑價基點"
              type="number"
              min="0"
              max="9999.99"
              step="any"
              value={slippageBps}
              onChange={(event) => setSlippageBps(event.target.value)}
              placeholder="0"
            />
          </label>
        </div>
        <p className="small muted">
          {closedBarCount === null
            ? '只會使用已過收盤時間的 K 棒。'
            : `已選用 ${closedBarCount} 根已收盤 K 棒；尚未收盤的 K 棒不納入。`}
        </p>
        <button className="primary-button" type="submit" style={{ minHeight: 44 }}>
          執行回測
        </button>
      </form>
      {error && (
        <p className="research-error" role="alert">
          {error}
        </p>
      )}
      {output && (
        <div className="backtest-results" aria-live="polite">
          <h3>模擬結果</h3>
          <p className="small muted">
            {output.status === 'insolvent' ? '已停止：資金不足' : '完成'} · 僅用已收盤 K 棒 ·
            以下一根 K 棒開盤價成交 · 不會自動交易。
          </p>
          {includedSessionCloseObservations > 0 && (
            <p className="research-disclaimer" role="status">
              包含 {includedSessionCloseObservations} 筆資料來源回報的 13:30 收盤觀測點，視為瞬時樣本而非一般 K 棒。
            </p>
          )}
          {output.warning && (
            <p className="research-error" role="status">
              {output.warning}
            </p>
          )}
          {output.openPosition && (
            <p className="research-open-position" role="status">
              未平倉{output.openPosition.direction === 'long' ? '多單' : '空單'} · 進場{' '}
              {dateText(output.openPosition.entryTime)} 價格{' '}
              {priceText(output.openPosition.entryPrice, currency)} · 市值價{' '}
              {priceText(output.openPosition.markPrice, currency)}
            </p>
          )}
          <dl className="backtest-metrics">
            <div>
              <dt>已平倉交易</dt>
              <dd>{output.metrics.trades}</dd>
            </div>
            <div>
              <dt>勝率</dt>
              <dd>{percentText(output.metrics.winRate)}</dd>
            </div>
            <div>
              <dt>總報酬</dt>
              <dd>{percentText(output.metrics.totalReturn)}</dd>
            </div>
            <div>
              <dt>最大回撤</dt>
              <dd>{percentText(output.metrics.maxDrawdown)}</dd>
            </div>
            <div>
              <dt>平均每筆損益（{currency}）</dt>
              <dd>{priceText(output.metrics.averageTrade, currency)}</dd>
            </div>
            <div>
              <dt>獲利因子</dt>
              <dd>
                {output.metrics.profitFactor === null
                  ? '不適用'
                  : numberText(output.metrics.profitFactor)}
              </dd>
            </div>
            <div>
              <dt>持倉時間比</dt>
              <dd>{percentText(output.metrics.exposure)}</dd>
            </div>
            <div>
              <dt>期間</dt>
              <dd>
                {output.metrics.dateRange
                  ? `${dateText(output.metrics.dateRange.from)} – ${dateText(output.metrics.dateRange.to)}`
                  : '無資料'}
              </dd>
            </div>
          </dl>
          <details className="research-details">
            <summary>訊號（{output.signals.length}）</summary>
            {output.signals.length ? (
              <ol className="research-record-list">
                {output.signals.map((signal, index) => (
                  <li key={`${signal.time}-${signal.action}-${index}`}>
                    <b>{signal.action}</b> · {dateText(signal.time)} · 收盤{' '}
                    {priceText(signal.close, currency)}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="small muted">此設定沒有任何訊號。</p>
            )}
          </details>
          <details className="research-details">
            <summary>已平倉交易（{output.trades.length}）</summary>
            {output.trades.length ? (
              <ol className="research-record-list">
                {output.trades.map((trade, index) => (
                  <li key={`${trade.entryTime}-${trade.exitTime}-${index}`}>
                    <b>{trade.direction === 'long' ? '多' : '空'}</b> · 進場 {dateText(trade.entryTime)}{' '}
                    {priceText(trade.entryPrice, currency)} · 出場 {dateText(trade.exitTime)}{' '}
                    {priceText(trade.exitPrice, currency)} · 損益{' '}
                    {priceText(trade.pnl, currency)} · 報酬 {percentText(trade.return)}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="small muted">此期間沒有已平倉交易。</p>
            )}
          </details>
        </div>
      )}
    </section>
  );
}
