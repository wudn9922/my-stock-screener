import type { Bar, Timeframe } from '../market-data/MarketDataProvider';

export type MovingAverageType = 'SMA' | 'EMA';
export type StrategyKind = 'ma-cross' | 'price-cross';
export type StrategyDirection = 'long' | 'short';

export interface StrategyConfig {
  symbol: string;
  timeframe: Timeframe;
  kind: StrategyKind;
  fastType: MovingAverageType;
  slowType: MovingAverageType;
  fastPeriod: number;
  slowPeriod: number;
  direction: StrategyDirection;
}

/** Bars explicitly supplied by the caller as closed. Times are Unix seconds. */
export interface StrategyInput {
  config: StrategyConfig;
  closedBars: readonly Bar[];
  initialCapital: number;
  costs?: ExecutionCosts;
}

export interface ExecutionCosts {
  commissionBps?: number;
  slippageBps?: number;
}

export type SignalAction = 'BUY' | 'EXIT' | 'SELL' | 'COVER';
export interface StrategySignal {
  action: SignalAction;
  time: number;
  close: number;
  fastValue: number | null;
  slowValue: number | null;
}

export interface ExecutionMarker {
  action: SignalAction;
  signalTime: number;
  time: number;
  price: number;
  costs: number;
}

export interface ClosedTrade {
  direction: StrategyDirection;
  entrySignalTime: number;
  entryTime: number;
  entryPrice: number;
  exitSignalTime: number;
  exitTime: number;
  exitPrice: number;
  quantity: number;
  grossPnl: number;
  costs: number;
  pnl: number;
  return: number;
}

export interface OpenPosition {
  direction: StrategyDirection;
  entrySignalTime: number;
  entryTime: number;
  entryPrice: number;
  quantity: number;
  markTime: number;
  markPrice: number;
  unrealizedGrossPnl: number;
  entryCosts: number;
}

export interface EquityPoint {
  time: number;
  equity: number;
  drawdown: number;
}

export interface StrategyMetrics {
  trades: number;
  winRate: number;
  totalReturn: number;
  maxDrawdown: number;
  averageTrade: number;
  /** Net winning trade PnL / absolute net losing trade PnL after execution costs; null without losses. */
  profitFactor: number | null;
  exposure: number;
  dateRange: { from: number; to: number } | null;
}

export interface StrategyResult {
  config: StrategyConfig;
  assumptions: {
    execution: 'next-bar-open';
    commissionBps: number;
    slippageBps: number;
    initialCapital: number;
    positionSizing: '100%-of-current-equity';
  };
  status: 'complete' | 'insolvent';
  warning: string | null;
  signals: StrategySignal[];
  trades: ClosedTrade[];
  markers: ExecutionMarker[];
  equity: EquityPoint[];
  openPosition: OpenPosition | null;
  metrics: StrategyMetrics;
}

export type ClosedBar = Bar;
