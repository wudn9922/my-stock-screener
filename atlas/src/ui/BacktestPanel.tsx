import { useEffect, useState, type FormEvent } from 'react';
import { closedBars, type BarResult, type Timeframe } from '../market-data/MarketDataProvider';
import { getMarketProfile, type MarketProfile } from '../market-data/MarketProfile';
import { runBacktest } from '../strategy/BacktestEngine';
import type {
  MovingAverageType,
  StrategyDirection,
  StrategyKind,
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
  const [kind, setKind] = useState<StrategyKind>('ma-cross');
  const [fastType, setFastType] = useState<MovingAverageType>('SMA');
  const [slowType, setSlowType] = useState<MovingAverageType>('SMA');
  const [fastPeriod, setFastPeriod] = useState('9');
  const [slowPeriod, setSlowPeriod] = useState('21');
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
      if (!result) throw new Error('Wait for chart bars to load before running a backtest.');
      const asOf = result.asOf ?? Date.now() / 1000;
      const profile = market ?? result.market ?? getMarketProfile(symbol);
      const bars = closedBars({ ...result, market: profile }, timeframe, asOf);
      setClosedBarCount(bars.length);
      if (!bars.length) throw new Error('There are no closed bars available for this timeframe.');
      const fast = Number(fastPeriod),
        slow = Number(slowPeriod),
        capital = Number(initialCapital);
      if (!Number.isInteger(fast) || fast < 1 || fast > 5000)
        throw new Error('Fast period must be an integer from 1 through 5000.');
      if (!Number.isInteger(slow) || slow < 1 || slow > 5000)
        throw new Error('Slow period must be an integer from 1 through 5000.');
      if (kind === 'ma-cross' && fast >= slow)
        throw new Error('For an MA cross, the fast period must be less than the slow period.');
      if (!Number.isFinite(capital) || capital <= 0)
        throw new Error('Initial capital must be a positive amount.');

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
              ? 'Slippage must be zero or a positive number below 10000 basis points.'
              : 'Commission must be from 0 through 10000 basis points.',
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
          kind,
          fastType,
          slowType,
          fastPeriod: fast,
          slowPeriod: slow,
          direction,
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
    ? 'Provider marks OHLC as adjusted.'
    : 'Corporate-action adjustment accuracy is not certified; the app does not adjust these OHLC prices.';

  return (
    <section className="research-panel backtest-panel" aria-label={`${symbol} backtest`}>
      <div className="section-heading">
        <span>BACKTEST RESEARCH</span>
        <span>
          {symbol} · {timeframe} · {currency}
        </span>
      </div>
      <p className="research-disclaimer">
        Research prototype. Results use closed bars and next-bar-open simulated fills; signals do
        not place orders. Corporate-action coverage is unknown.{' '}
        {result
          ? `${adjustedText} Source: ${result.source}${result.dataState === 'simulated' ? ' · SIMULATED' : ''}.`
          : 'Waiting for the chart data source.'}
      </p>
      <form className="research-form" onSubmit={run}>
        <label>
          Strategy
          <select
            aria-label="Strategy kind"
            value={kind}
            onChange={(event) => setKind(event.target.value as StrategyKind)}
          >
            <option value="ma-cross">Moving average cross</option>
            <option value="price-cross">Price crosses moving average</option>
          </select>
        </label>
        {kind === 'ma-cross' && (
          <>
            <div className="research-field-row">
              <label>
                Fast average
                <select
                  aria-label="Fast average type"
                  value={fastType}
                  onChange={(event) => setFastType(event.target.value as MovingAverageType)}
                >
                  <option value="SMA">SMA</option>
                  <option value="EMA">EMA</option>
                </select>
              </label>
              <label>
                Fast period
                <input
                  aria-label="Fast period"
                  type="number"
                  min="1"
                  max="5000"
                  step="1"
                  value={fastPeriod}
                  onChange={(event) => setFastPeriod(event.target.value)}
                  required
                />
              </label>
            </div>
            <div className="research-field-row">
              <label>
                Slow average
                <select
                  aria-label="Slow average type"
                  value={slowType}
                  onChange={(event) => setSlowType(event.target.value as MovingAverageType)}
                >
                  <option value="SMA">SMA</option>
                  <option value="EMA">EMA</option>
                </select>
              </label>
              <label>
                Slow period
                <input
                  aria-label="Slow period"
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
          </>
        )}
        {kind === 'price-cross' && (
          <div className="research-field-row">
            <label>
              Reference average
              <select
                aria-label="Reference average type"
                value={slowType}
                onChange={(event) => setSlowType(event.target.value as MovingAverageType)}
              >
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
              </select>
            </label>
            <label>
              Reference period
              <input
                aria-label="Reference period"
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
        )}
        <label>
          Position direction
          <select
            aria-label="Position direction"
            value={direction}
            onChange={(event) => setDirection(event.target.value as StrategyDirection)}
          >
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
        </label>
        <label>
          Initial capital ({currency})
          <input
            aria-label="Initial capital"
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
            Commission (bps, optional)
            <input
              aria-label="Commission basis points"
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
            Slippage (bps, optional)
            <input
              aria-label="Slippage basis points"
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
            ? 'The run will use only bars whose scheduled session close has passed.'
            : `Closed bars selected: ${closedBarCount}. The current forming bar is excluded.`}
        </p>
        <button className="primary-button" type="submit" style={{ minHeight: 44 }}>
          Run backtest
        </button>
      </form>
      {error && (
        <p className="research-error" role="alert">
          {error}
        </p>
      )}
      {output && (
        <div className="backtest-results" aria-live="polite">
          <h3>Simulation results</h3>
          <p className="small muted">
            {output.status === 'insolvent' ? 'Stopped: insolvent' : 'Complete'} · only closed bars ·
            fills at the next available bar open · no automatic trading.
          </p>
          {includedSessionCloseObservations > 0 && (
            <p className="research-disclaimer" role="status">
              {includedSessionCloseObservations} source-reported 13:30 session-close points were
              included as instant samples, not ordinary duration bars.
            </p>
          )}
          {output.warning && (
            <p className="research-error" role="status">
              {output.warning}
            </p>
          )}
          {output.openPosition && (
            <p className="research-open-position" role="status">
              Open {output.openPosition.direction} position · entry{' '}
              {dateText(output.openPosition.entryTime)} at{' '}
              {priceText(output.openPosition.entryPrice, currency)} · marked{' '}
              {priceText(output.openPosition.markPrice, currency)}
            </p>
          )}
          <dl className="backtest-metrics">
            <div>
              <dt>Closed trades</dt>
              <dd>{output.metrics.trades}</dd>
            </div>
            <div>
              <dt>Win rate</dt>
              <dd>{percentText(output.metrics.winRate)}</dd>
            </div>
            <div>
              <dt>Total return</dt>
              <dd>{percentText(output.metrics.totalReturn)}</dd>
            </div>
            <div>
              <dt>Max drawdown</dt>
              <dd>{percentText(output.metrics.maxDrawdown)}</dd>
            </div>
            <div>
              <dt>Average trade P&amp;L ({currency})</dt>
              <dd>{priceText(output.metrics.averageTrade, currency)}</dd>
            </div>
            <div>
              <dt>Profit factor</dt>
              <dd>
                {output.metrics.profitFactor === null
                  ? 'N/A'
                  : numberText(output.metrics.profitFactor)}
              </dd>
            </div>
            <div>
              <dt>Exposure</dt>
              <dd>{percentText(output.metrics.exposure)}</dd>
            </div>
            <div>
              <dt>Date range</dt>
              <dd>
                {output.metrics.dateRange
                  ? `${dateText(output.metrics.dateRange.from)} – ${dateText(output.metrics.dateRange.to)}`
                  : 'No bars'}
              </dd>
            </div>
          </dl>
          <details className="research-details">
            <summary>Signals ({output.signals.length})</summary>
            {output.signals.length ? (
              <ol className="research-record-list">
                {output.signals.map((signal, index) => (
                  <li key={`${signal.time}-${signal.action}-${index}`}>
                    <b>{signal.action}</b> · {dateText(signal.time)} · close{' '}
                    {priceText(signal.close, currency)}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="small muted">No signals for these settings.</p>
            )}
          </details>
          <details className="research-details">
            <summary>Closed trades ({output.trades.length})</summary>
            {output.trades.length ? (
              <ol className="research-record-list">
                {output.trades.map((trade, index) => (
                  <li key={`${trade.entryTime}-${trade.exitTime}-${index}`}>
                    <b>{trade.direction.toUpperCase()}</b> · Entry {dateText(trade.entryTime)} at{' '}
                    {priceText(trade.entryPrice, currency)} · Exit {dateText(trade.exitTime)} at{' '}
                    {priceText(trade.exitPrice, currency)} · P&amp;L{' '}
                    {priceText(trade.pnl, currency)} · return {percentText(trade.return)}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="small muted">No closed trades in this date range.</p>
            )}
          </details>
        </div>
      )}
    </section>
  );
}
