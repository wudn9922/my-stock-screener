import { normalizeSymbol, timeframes, type Bar } from '../market-data/MarketDataProvider';
import { generateSignals } from './signals';
import type {
  ClosedTrade,
  ExecutionCosts,
  ExecutionMarker,
  EquityPoint,
  OpenPosition,
  StrategyInput,
  StrategyResult,
  StrategySignal,
} from './types';

function finiteNonnegative(value: number): boolean {
  return Number.isFinite(value) && value >= 0;
}

function validateBars(bars: readonly Bar[]) {
  let priorTime = -Infinity;
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    if (!Number.isFinite(bar.time) || !Number.isInteger(bar.time) || bar.time <= priorTime) {
      throw new Error(`closedBars must have unique, strictly increasing integer times (index ${i})`);
    }
    if (![bar.open, bar.high, bar.low, bar.close, bar.volume].every(finiteNonnegative)) {
      throw new Error(`closedBars contain a negative or non-finite OHLCV value (index ${i})`);
    }
    if (bar.high < Math.max(bar.open, bar.close, bar.low) || bar.low > Math.min(bar.open, bar.close, bar.high)) {
      throw new Error(`closedBars contain inconsistent OHLC values (index ${i})`);
    }
    priorTime = bar.time;
  }
}

function validateInput(input: StrategyInput): Required<ExecutionCosts> & { symbol: string } {
  const { config, initialCapital } = input;
  const symbol = normalizeSymbol(config.symbol);
  if (!(timeframes as readonly string[]).includes(config.timeframe)) throw new Error('Unsupported strategy timeframe');
  if (config.kind !== 'ma-cross' && config.kind !== 'price-cross') throw new Error('Unsupported strategy kind');
  if (config.direction !== 'long' && config.direction !== 'short') throw new Error('Unsupported strategy direction');
  if (!['SMA', 'EMA'].includes(config.fastType) || !['SMA', 'EMA'].includes(config.slowType)) {
    throw new Error('Moving average type must be SMA or EMA');
  }
  for (const [name, period] of [['fastPeriod', config.fastPeriod], ['slowPeriod', config.slowPeriod]] as const) {
    if (!Number.isInteger(period) || period < 1 || period > 5000) {
      throw new Error(`${name} must be an integer from 1 through 5000`);
    }
  }
  if (config.kind === 'ma-cross' && config.fastPeriod >= config.slowPeriod) {
    throw new Error('MA-cross fastPeriod must be less than slowPeriod');
  }
  if (!Number.isFinite(initialCapital) || initialCapital <= 0) throw new Error('initialCapital must be positive and finite');
  validateBars(input.closedBars);
  const commissionBps = input.costs?.commissionBps ?? 0;
  const slippageBps = input.costs?.slippageBps ?? 0;
  if (!finiteNonnegative(commissionBps)) throw new Error('commissionBps must be finite and nonnegative');
  if (commissionBps > 10_000) throw new Error('commissionBps must not exceed 10000');
  if (!finiteNonnegative(slippageBps) || slippageBps >= 10_000) {
    throw new Error('slippageBps must be finite and between 0 and 10000');
  }
  return { commissionBps, slippageBps, symbol };
}

interface ActivePosition {
  direction: 'long' | 'short';
  entrySignalTime: number;
  entryTime: number;
  entryPrice: number;
  quantity: number;
  entryCosts: number;
  equityAtEntry: number;
}

/** Simulates close-generated signals at the next explicitly closed bar's open. */
export function runBacktest(input: StrategyInput): StrategyResult {
  const costs = validateInput(input);
  const bars = input.closedBars;
  const commissionRate = costs.commissionBps / 10_000;
  const slippageRate = costs.slippageBps / 10_000;
  const signals = generateSignals(input.config, bars);
  const bySignalTime = new Map(signals.map((signal) => [signal.time, signal]));
  const trades: ClosedTrade[] = [];
  const markers: ExecutionMarker[] = [];
  const equity: EquityPoint[] = [];
  let cash = input.initialCapital;
  let position: ActivePosition | null = null;
  let peak = input.initialCapital;
  let status: StrategyResult['status'] = 'complete';
  let warning: string | null = null;
  let exposureBars = 0;
  let finalMarkTime: number | null = null;
  let finalMarkPrice: number | null = null;

  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    const pending = i > 0 ? bySignalTime.get(bars[i - 1].time) : undefined;
    if (pending && shouldExecute(pending, position)) {
      const isBuy = pending.action === 'BUY' || pending.action === 'COVER';
        const fillPrice = checked(bar.open * (isBuy ? 1 + slippageRate : 1 - slippageRate), 'execution price');
      const feeRate = commissionRate;
      if (pending.action === 'BUY' || pending.action === 'SELL') {
        if (fillPrice <= 0) throw new Error(`Cannot size entry at zero execution price (time ${bar.time})`);
        const equityBeforeEntry = currentEquity(cash, position, bars[i - 1].close);
        if (equityBeforeEntry <= 0) {
          status = 'insolvent';
          warning = 'Equity was nonpositive; simulation stopped before placing another order.';
          break;
        } else {
          const quantity = checked(equityBeforeEntry / (fillPrice * (1 + feeRate)), 'position quantity');
          const fee = checked(quantity * fillPrice * feeRate, 'entry commission');
          const direction = pending.action === 'BUY' ? 'long' : 'short';
          const signedQuantity = direction === 'long' ? quantity : -quantity;
          cash = checked(cash - signedQuantity * fillPrice - fee, 'cash after entry');
          position = {
            direction,
            entrySignalTime: pending.time,
            entryTime: bar.time,
            entryPrice: fillPrice,
            quantity: signedQuantity,
            entryCosts: fee,
            equityAtEntry: equityBeforeEntry,
          };
          markers.push({ action: pending.action, signalTime: pending.time, time: bar.time, price: fillPrice, costs: fee });
        }
      } else if (position) {
        const exitFee = checked(Math.abs(position.quantity) * fillPrice * feeRate, 'exit commission');
        cash = checked(cash + position.quantity * fillPrice - exitFee, 'cash after exit');
        const grossPnl = checked(position.quantity * (fillPrice - position.entryPrice), 'trade gross PnL');
        const totalCosts = checked(position.entryCosts + exitFee, 'trade costs');
        const pnl = checked(grossPnl - totalCosts, 'trade PnL');
        trades.push({
          direction: position.direction,
          entrySignalTime: position.entrySignalTime,
          entryTime: position.entryTime,
          entryPrice: position.entryPrice,
          exitSignalTime: pending.time,
          exitTime: bar.time,
          exitPrice: fillPrice,
          quantity: Math.abs(position.quantity),
          grossPnl,
          costs: totalCosts,
          pnl,
          return: checked(pnl / position.equityAtEntry, 'trade return'),
        });
        markers.push({ action: pending.action, signalTime: pending.time, time: bar.time, price: fillPrice, costs: exitFee });
        position = null;
      }
    }

    const markedEquity = checked(currentEquity(cash, position, bar.close), 'marked equity');
    peak = Math.max(peak, markedEquity);
    const drawdown = peak > 0
      ? checked((peak - markedEquity) / peak, 'drawdown')
      : 0;
    equity.push({ time: bar.time, equity: markedEquity, drawdown });
    if (position) exposureBars++;
    finalMarkTime = bar.time;
    finalMarkPrice = bar.close;
    if (markedEquity <= 0) {
      status = 'insolvent';
      warning = 'Equity reached zero or below at a closed-bar mark. Any open position is retained; later simulation and orders were stopped. No broker margin model is applied.';
      break;
    }
  }

  const openPosition: OpenPosition | null = position && finalMarkTime !== null && finalMarkPrice !== null
    ? {
        direction: position.direction,
        entrySignalTime: position.entrySignalTime,
        entryTime: position.entryTime,
        entryPrice: position.entryPrice,
        quantity: Math.abs(position.quantity),
        markTime: finalMarkTime,
        markPrice: finalMarkPrice,
        unrealizedGrossPnl: checked(position.quantity * (finalMarkPrice - position.entryPrice), 'unrealized gross PnL'),
        entryCosts: position.entryCosts,
      }
    : null;
  const lastEquity = equity.at(-1)?.equity ?? input.initialCapital;
  const grossWins = trades.filter((trade) => trade.pnl > 0)
    .reduce((sum, trade) => checked(sum + trade.pnl, 'gross wins'), 0);
  const grossLosses = trades.filter((trade) => trade.pnl < 0)
    .reduce((sum, trade) => checked(sum + trade.pnl, 'gross losses'), 0);
  const maxDrawdown = equity.reduce((maximum, point) => Math.max(maximum, point.drawdown), 0);
  const totalTradePnl = trades.reduce((sum, trade) => checked(sum + trade.pnl, 'total trade PnL'), 0);
  const metrics = {
    trades: trades.length,
    winRate: checked(trades.length ? trades.filter((trade) => trade.pnl > 0).length / trades.length : 0, 'win rate'),
    totalReturn: checked(
      checked(lastEquity - input.initialCapital, 'total return numerator') / input.initialCapital,
      'total return',
    ),
    maxDrawdown,
    averageTrade: checked(trades.length ? totalTradePnl / trades.length : 0, 'average trade'),
    profitFactor: grossLosses < 0 ? checked(grossWins / Math.abs(grossLosses), 'profit factor') : null,
    exposure: checked(equity.length ? exposureBars / equity.length : 0, 'exposure'),
    dateRange: equity.length ? { from: bars[0].time, to: equity.at(-1)!.time } : null,
  };
  return {
    config: { ...input.config, symbol: costs.symbol },
    assumptions: {
      execution: 'next-bar-open',
      commissionBps: costs.commissionBps,
      slippageBps: costs.slippageBps,
      initialCapital: input.initialCapital,
      positionSizing: '100%-of-current-equity',
    },
    status,
    warning,
    signals: status === 'insolvent' ? signals.filter((signal) => signal.time <= equity.at(-1)!.time) : signals,
    trades,
    markers,
    equity,
    openPosition,
    metrics,
  };
}

function shouldExecute(signal: StrategySignal, position: ActivePosition | null): boolean {
  if (signal.action === 'BUY' || signal.action === 'SELL') return position === null;
  return position !== null;
}

function currentEquity(cash: number, position: ActivePosition | null, markPrice: number): number {
  return cash + (position ? position.quantity * markPrice : 0);
}

function checked(value: number, label: string): number {
  if (!Number.isFinite(value)) throw new Error(`Strategy calculation produced a non-finite ${label}`);
  return value;
}
