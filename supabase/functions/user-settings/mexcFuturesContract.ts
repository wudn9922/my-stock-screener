import { holdingDecimal, holdingsObject } from './holdings.ts';

export const FUTURES_SYMBOL = /^[A-Z0-9]{1,20}_USDT$/;
export interface MexcFuturesAccount {
  equity: string;
  available: string;
  positionMargin: string;
  unrealized: string;
}
export interface MexcFuturesPosition {
  symbol: string;
  side: 'long' | 'short';
  contracts: string;
  size: string;
  entryPrice: string;
  markPrice: string;
  leverage: string;
  margin: string;
  unrealized: string;
  liquidationPrice: string | null;
  marginMode: 'isolated' | 'cross';
}
export type MexcFutures =
  | { status: 'no-permission' }
  | { status: 'unavailable' }
  | {
      status: 'ok';
      account: MexcFuturesAccount;
      positions: MexcFuturesPosition[];
      skippedPositions: number;
    };

export function futuresDecimal(value: unknown, signed = false, positive = false): string {
  if (typeof value !== 'string') throw new Error('Invalid futures decimal');
  const negative = value.startsWith('-');
  if (negative && !signed) throw new Error('Invalid futures sign');
  const magnitude = negative ? value.slice(1) : value;
  holdingDecimal(magnitude, !positive);
  return value;
}

/** Separate RAM-only response contract; never extend the spot holdings document. */
export function validateMexcFutures(value: unknown): MexcFutures {
  const row = holdingsObject(value);
  if (row.status === 'no-permission' || row.status === 'unavailable') return { status: row.status };
  if (
    row.status !== 'ok' ||
    !Array.isArray(row.positions) ||
    row.positions.length > 200 ||
    !Number.isSafeInteger(row.skippedPositions) ||
    Number(row.skippedPositions) < 0 ||
    Number(row.skippedPositions) > 200 ||
    row.positions.length + Number(row.skippedPositions) > 200
  )
    throw new Error('Invalid futures response');
  const a = holdingsObject(row.account);
  const account = {
    equity: futuresDecimal(a.equity),
    available: futuresDecimal(a.available),
    positionMargin: futuresDecimal(a.positionMargin),
    unrealized: futuresDecimal(a.unrealized, true),
  };
  const positions = row.positions.map((raw): MexcFuturesPosition => {
    const p = holdingsObject(raw);
    if (
      typeof p.symbol !== 'string' ||
      !FUTURES_SYMBOL.test(p.symbol) ||
      (p.side !== 'long' && p.side !== 'short') ||
      (p.marginMode !== 'isolated' && p.marginMode !== 'cross')
    )
      throw new Error('Invalid futures position');
    const leverage = futuresDecimal(p.leverage, false, true);
    if (!/^\d+$/.test(leverage)) throw new Error('Invalid futures leverage');
    return {
      symbol: p.symbol,
      side: p.side,
      marginMode: p.marginMode,
      contracts: futuresDecimal(p.contracts, false, true),
      size: futuresDecimal(p.size, false, true),
      entryPrice: futuresDecimal(p.entryPrice, false, true),
      markPrice: futuresDecimal(p.markPrice, false, true),
      leverage,
      margin: futuresDecimal(p.margin),
      unrealized: futuresDecimal(p.unrealized, true),
      liquidationPrice: p.liquidationPrice === null ? null : futuresDecimal(p.liquidationPrice, false, true),
    };
  });
  return { status: 'ok', account, positions, skippedPositions: Number(row.skippedPositions) };
}
