import { holdingsObject } from './holdings.ts';
import {
  FUTURES_SYMBOL,
  futuresDecimal,
  validateMexcFutures,
  type MexcFutures,
  type MexcFuturesPosition,
} from './mexcFuturesContract.ts';

// Official Futures V1 REST domain since 2026-01-19. Never accept a caller-supplied URL.
export const MEXC_FUTURES_ORIGIN = 'https://api.mexc.com';
export type FuturesStep = 'futures-assets' | 'futures-positions' | 'futures-detail';
type Diagnostic = (step: FuturesStep, status: number | null, value: unknown, reason: string) => void;
interface Credentials {
  apiKey: string;
  apiSecret: string;
}
type Path =
  | '/api/v1/private/account/assets'
  | '/api/v1/private/position/open_positions'
  | '/api/v1/contract/detail/country'
  | '/api/v1/contract/ticker';
/** Every futures response is capped; only the two public full-list fallbacks get more room. */
export const FUTURES_RESPONSE_CAP = 2 * 1024 * 1024;
export const FUTURES_FULL_LIST_CAP = 8 * 1024 * 1024;
/** Above this many distinct held symbols, per-symbol reads would not fit the shared deadline. */
export const FUTURES_PER_SYMBOL_LIMIT = 20;
export const FUTURES_CONCURRENCY = 4;
const SCALE = 10n ** 18n;
const positionKeys = new Set([
  'positionId', 'symbol', 'positionType', 'openType', 'state', 'holdVol', 'frozenVol', 'closeVol',
  'holdAvgPrice', 'holdAvgPriceFullyScale', 'openAvgPrice', 'openAvgPriceFullyScale', 'closeAvgPrice',
  'liquidatePrice', 'oim', 'im', 'holdFee', 'realised', 'leverage', 'adlLevel', 'createTime', 'updateTime',
  'autoAddIm', 'marginRatio', 'profitRatio', 'newOpenAvgPrice', 'newCloseAvgPrice', 'closeProfitLoss',
  'fee', 'totalFee', 'deductFeeList', 'zeroSaveTotalFeeBinance', 'zeroSaveTotalFeeMexc', 'version',
  'feeRates', 'unRealizedPnl',
]);
const diagnosticFields = new Set([...positionKeys, 'data', 'position', 'contractSize', 'fairPrice']);

/** Field names are data too: arbitrary upstream keys can contain assets or credentials. */
export function futuresDiagnosticMetadata(value: unknown): { field?: string; positionKeys?: string[] } {
  const row = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  return {
    ...(typeof row.field === 'string' && diagnosticFields.has(row.field) ? { field: row.field } : {}),
    ...(Array.isArray(row.positionKeys) ? {
      positionKeys: [...new Set(row.positionKeys.slice(0, 40).map((key) =>
        typeof key === 'string' && positionKeys.has(key) ? key : '[other]',
      ))].sort(),
    } : {}),
  };
}

class PositionFieldFailure extends Error {
  constructor(readonly field: string) {
    super('Invalid futures position field');
  }
}
function positionField<T>(field: string, task: () => T): T {
  try {
    return task();
  } catch (error) {
    throw error instanceof PositionFieldFailure ? error : new PositionFieldFailure(field);
  }
}
interface PendingPosition {
  raw: Record<string, unknown>;
  symbol: string;
  side: MexcFuturesPosition['side'];
  marginMode: MexcFuturesPosition['marginMode'];
  contracts: string;
  entryPrice: string;
  leverage: string;
  margin: string;
  liquidationPrice: string | null;
  reportedUnrealized: string | null;
}

export async function futuresAuth(
  auth: Credentials,
  time: number,
  parameters: Record<string, string | null> = {},
) {
  const query = new URLSearchParams(
    Object.entries(parameters)
      .filter((p): p is [string, string] => p[1] !== null)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  ).toString();
  const timestamp = String(time);
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(auth.apiSecret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = [
    ...new Uint8Array(
      await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(auth.apiKey + timestamp + query)),
    ),
  ]
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return { query, headers: { ApiKey: auth.apiKey, 'Request-Time': timestamp, Signature: signature } };
}

/** Preserve JSON decimal tokens, including scientific notation, without a Number round trip. */
export function futuresJson(text: string): unknown {
  return JSON.parse(
    text.replace(/"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g, (token) =>
      token.startsWith('"') ? token : JSON.stringify(token),
    ),
  );
}
export function sourceDecimal(value: unknown, signed = false, positive = false): string {
  if (typeof value !== 'string' || !/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d{1,3})?$/.test(value))
    throw new Error('Invalid futures number');
  const negative = value.startsWith('-');
  if (negative && !signed) throw new Error('Invalid futures sign');
  const [coefficient, exponent = '0'] = (negative ? value.slice(1) : value).toLowerCase().split('e');
  const [whole, fraction = ''] = coefficient!.split('.');
  const point = whole!.length + Number(exponent),
    digits = whole! + fraction;
  if (Math.abs(Number(exponent)) > 60 || digits.length > 100) throw new Error('Invalid futures range');
  const expanded =
    point <= 0
      ? '0.' + '0'.repeat(-point) + digits
      : point >= digits.length
        ? digits + '0'.repeat(point - digits.length)
        : digits.slice(0, point) + '.' + digits.slice(point);
  const [integer, decimal = ''] = expanded.split('.');
  const magnitude =
    integer!.replace(/^0+(?=\d)/, '') + (decimal.replace(/0+$/, '') ? '.' + decimal.replace(/0+$/, '') : '');
  return futuresDecimal((negative && /[1-9]/.test(magnitude) ? '-' : '') + magnitude, signed, positive);
}
function units(value: string): bigint {
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole!) * SCALE + BigInt(fraction.padEnd(18, '0'));
}
/** Half-up, away from zero at ties. Never converts an amount through Number. */
export function roundedFuturesRatio(numerator: bigint, denominator: bigint, places: number): string {
  if (denominator <= 0n || !Number.isSafeInteger(places) || places < 0 || places > 18)
    throw new Error('Invalid futures precision');
  const scale = 10n ** BigInt(places);
  const magnitude = numerator < 0n ? -numerator : numerator;
  const rounded = (magnitude * scale + denominator / 2n) / denominator;
  const fraction = String(rounded % scale).padStart(places, '0').replace(/0+$/, '');
  return (numerator < 0n && rounded !== 0n ? '-' : '') + String(rounded / scale) + (fraction ? '.' + fraction : '');
}
function contractAmount(contracts: string, contractSize: string): string {
  const product = units(contracts) * units(contractSize);
  return sourceDecimal(roundedFuturesRatio(product, SCALE * SCALE, 18), false, true);
}
function abortable<T>(task: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const aborted = () => {
      reject(new Error('Futures request aborted'));
    };
    if (signal.aborted) aborted();
    else signal.addEventListener('abort', aborted, { once: true });
    task.then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted));
  });
}
/** Run tasks with bounded concurrency; stop scheduling after the first failure. */
async function mapLimited<T, R>(items: readonly T[], limit: number, task: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0,
    failed = false;
  const worker = async () => {
    while (!failed && next < items.length) {
      const index = next++;
      try {
        results[index] = await task(items[index]!);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}
class ReadFailure extends Error {
  constructor(readonly permission: boolean) {
    super('Futures unavailable');
  }
}

/** Optional read-only data. All failures stay outside the spot success/error contract. */
export async function readMexcFutures(
  auth: Credentials,
  permitted: boolean,
  fetcher: typeof fetch,
  signal: AbortSignal,
  now: () => number,
  diagnostic: Diagnostic,
): Promise<MexcFutures> {
  if (!permitted) return { status: 'no-permission' };
  const read = async (
    path: Path,
    step: FuturesStep,
    {
      query,
      cap = FUTURES_RESPONSE_CAP,
      readSignal = signal,
      report = diagnostic,
    }: { query?: Record<string, string>; cap?: number; readSignal?: AbortSignal; report?: Diagnostic } = {},
  ) => {
    let status: number | null = null,
      metadata: unknown,
      reason = 'network-or-timeout';
    const privateRead = path.startsWith('/api/v1/private/');
    try {
      readSignal.throwIfAborted();
      const authHeaders = privateRead ? (await futuresAuth(auth, now())).headers : {};
      // Fixed origin and path; the only query value is a symbol that already matched FUTURES_SYMBOL.
      const search = query ? '?' + new URLSearchParams(query).toString() : '';
      const response = await abortable(
        fetcher(MEXC_FUTURES_ORIGIN + path + search, {
          method: 'GET',
          headers: { 'Content-Type': 'application/json', ...authHeaders },
          signal: readSignal,
          redirect: 'error',
          cache: 'no-store',
          referrerPolicy: 'no-referrer',
        }),
        readSignal,
      );
      status = response.status;
      reason = 'response-size-or-empty';
      if (Number(response.headers.get('Content-Length')) > cap || !response.body) {
        void response.body?.cancel().catch(() => undefined);
        throw new Error();
      }
      const reader = response.body.getReader(),
        chunks: Uint8Array[] = [];
      let size = 0;
      reason = 'invalid-json-or-stream';
      try {
        while (true) {
          const part = await abortable(reader.read(), readSignal);
          if (part.done) break;
          size += part.value.byteLength;
          if (size > cap) {
            reason = 'response-size-or-empty';
            throw new Error();
          }
          chunks.push(part.value);
        }
      } catch (error) {
        void reader.cancel().catch(() => undefined);
        throw error;
      } finally {
        reader.releaseLock();
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      const text = new TextDecoder().decode(bytes),
        envelope = holdingsObject(JSON.parse(text));
      metadata = { code: envelope.code, msg: envelope.message ?? envelope.msg };
      reason = response.ok ? 'upstream-envelope' : 'upstream-http';
      const permission =
        privateRead &&
        (response.status === 403 ||
          (typeof envelope.code === 'number' && [511, 701, 703].includes(envelope.code)));
      if (!response.ok || envelope.success !== true || envelope.code !== 0) throw new ReadFailure(permission);
      return { data: holdingsObject(futuresJson(text)).data, status };
    } catch (error) {
      report(
        step,
        status,
        metadata,
        readSignal.aborted ? 'network-or-timeout' : status !== null && status >= 400 ? 'upstream-http' : reason,
      );
      throw error instanceof ReadFailure ? error : new ReadFailure(privateRead && status === 403);
    }
  };
  const parse = <T>(
    step: FuturesStep,
    response: { data: unknown; status: number },
    task: (data: unknown) => T,
    report: Diagnostic = diagnostic,
  ): T => {
    try {
      return task(response.data);
    } catch (error) {
      report(step, response.status, error instanceof PositionFieldFailure ? { field: error.field } : undefined, 'invalid-response');
      throw new ReadFailure(false);
    }
  };
  try {
    const assetsResponse = await read('/api/v1/private/account/assets', 'futures-assets');
    const account = parse('futures-assets', assetsResponse, (data) => {
      if (!Array.isArray(data) || data.length > 200) throw new Error();
      const accounts = data.map(holdingsObject).filter((a) => a.currency === 'USDT');
      if (accounts.length !== 1) throw new Error();
      const a = accounts[0]!;
      return {
        equity: sourceDecimal(a.equity),
        available: sourceDecimal(a.availableBalance),
        positionMargin: sourceDecimal(a.positionMargin),
        unrealized: sourceDecimal(a.unrealized, true),
      };
    });
    const positionsResponse = await read('/api/v1/private/position/open_positions', 'futures-positions');
    let skippedPositions = 0;
    const skipPosition = (error: unknown, raw: unknown) => {
      skippedPositions++;
      diagnostic('futures-positions', positionsResponse.status, futuresDiagnosticMetadata({
        field: error instanceof PositionFieldFailure ? error.field : 'position',
        positionKeys: raw && typeof raw === 'object' && !Array.isArray(raw) ? Object.keys(raw) : [],
      }), 'invalid-response');
    };
    const pending = parse('futures-positions', positionsResponse, (data) => {
      if (!Array.isArray(data) || data.length > 200) throw new PositionFieldFailure('data');
      return data.flatMap((raw): PendingPosition[] => {
        try {
          const p = positionField('position', () => holdingsObject(raw));
          if (typeof p.symbol !== 'string' || !FUTURES_SYMBOL.test(p.symbol)) {
            skippedPositions++;
            return [];
          }
          if (p.state !== '1' && p.state !== '2' && p.state !== '3') throw new PositionFieldFailure('state');
          if (p.state === '3') return [];
          const contracts = positionField('holdVol', () => sourceDecimal(p.holdVol));
          if (contracts === '0') return [];
          if (p.positionType !== '1' && p.positionType !== '2') throw new PositionFieldFailure('positionType');
          if (p.openType !== '1' && p.openType !== '2') throw new PositionFieldFailure('openType');
          const entryField = p.holdAvgPriceFullyScale == null || p.holdAvgPriceFullyScale === '' ? 'holdAvgPrice' : 'holdAvgPriceFullyScale';
          const entryPrice = positionField(entryField, () => sourceDecimal(p[entryField], false, true));
          const leverage = positionField('leverage', () => {
            const value = sourceDecimal(p.leverage, false, true);
            if (!/^\d+$/.test(value)) throw new Error();
            return value;
          });
          const margin = positionField('im', () => sourceDecimal(p.im));
          const liquidationPrice = positionField('liquidatePrice', () => {
            if (p.liquidatePrice == null || p.liquidatePrice === '') return null;
            // Cross-margin sentinels are unknown prices, including negative values
            // outside the range of an otherwise valid displayable positive price.
            if (typeof p.liquidatePrice === 'string' && /^-\d+(?:\.\d+)?(?:[eE][+-]?\d{1,3})?$/.test(p.liquidatePrice))
              return null;
            const value = sourceDecimal(p.liquidatePrice, true);
            return value === '0' || value.startsWith('-') ? null : value;
          });
          const reportedUnrealized = p.unRealizedPnl == null || p.unRealizedPnl === '' ? null
            : positionField('unRealizedPnl', () => sourceDecimal(p.unRealizedPnl, true));
          return [{ raw: p, contracts, symbol: p.symbol,
            side: p.positionType === '1' ? 'long' : 'short',
            marginMode: p.openType === '1' ? 'isolated' : 'cross',
            entryPrice, leverage, margin, liquidationPrice, reportedUnrealized,
          }];
        } catch (error) {
          skipPosition(error, raw);
          return [];
        }
      });
    });
    if (!pending.length) return { status: 'ok', account, positions: [], skippedPositions };
    const wanted = [...new Set(pending.map((p) => p.symbol))];
    // A per-symbol lookup may come back as one wrapped object or as an array; both are accepted.
    const lookup = (data: unknown, field: string, symbols: ReadonlySet<string>) => {
      const rows = Array.isArray(data) ? data : [data];
      if (rows.length > 20000) throw new Error();
      const result = new Map<string, string>();
      for (const raw of rows) {
        const row = holdingsObject(raw);
        if (typeof row.symbol !== 'string' || !symbols.has(row.symbol)) continue;
        if (result.has(row.symbol)) throw new Error();
        result.set(row.symbol, sourceDecimal(row[field], false, true));
      }
      if (result.size !== symbols.size) throw new Error();
      return result;
    };
    const sizes = new Map<string, string>(),
      prices = new Map<string, string>();
    const sources = [
      { path: '/api/v1/contract/detail/country', field: 'contractSize', into: sizes },
      { path: '/api/v1/contract/ticker', field: 'fairPrice', into: prices },
    ] as const;
    if (wanted.length <= FUTURES_PER_SYMBOL_LIMIT) {
      // Only the held contracts: the unfiltered lists outgrew the 2 MiB response cap.
      const child = new AbortController(),
        stop = () => child.abort();
      signal.addEventListener('abort', stop, { once: true });
      if (signal.aborted) stop();
      let reported = false;
      const once: Diagnostic = (...event) => {
        if (reported) return;
        reported = true;
        diagnostic(...event);
      };
      try {
        await mapLimited(
          wanted.flatMap((symbol) => sources.map((source) => ({ symbol, ...source }))),
          FUTURES_CONCURRENCY,
          async ({ symbol, path, field, into }) => {
            try {
              const response = await read(path, 'futures-detail', {
                query: { symbol },
                readSignal: child.signal,
                report: once,
              });
              const value = parse('futures-detail', response, (data) => lookup(data, field, new Set([symbol])), once);
              into.set(symbol, value.get(symbol)!);
            } catch (error) {
              // read/parse already reported it; abort the other in-flight lookups (their logs are suppressed).
              stop();
              throw error;
            }
          },
        );
      } finally {
        signal.removeEventListener('abort', stop);
      }
    } else {
      // Too many symbols for per-symbol reads inside the shared deadline: read each public list once.
      const symbols = new Set(wanted);
      for (const { path, field, into } of sources) {
        const response = await read(path, 'futures-detail', { cap: FUTURES_FULL_LIST_CAP });
        for (const [symbol, value] of parse('futures-detail', response, (data) => lookup(data, field, symbols)))
          into.set(symbol, value);
      }
    }
    const positions = pending.flatMap((p): MexcFuturesPosition[] => {
      try {
        const contractSize = sizes.get(p.symbol)!, markPrice = prices.get(p.symbol)!;
        const size = positionField('contractSize', () => contractAmount(p.contracts, contractSize));
        const unrealized = p.reportedUnrealized ?? positionField('unRealizedPnl', () => {
          const difference = units(markPrice) - units(p.entryPrice);
          const product = difference * units(p.contracts) * units(contractSize);
          return sourceDecimal(roundedFuturesRatio(p.side === 'long' ? product : -product, SCALE * SCALE * SCALE, 8), true);
        });
        const candidate = { symbol: p.symbol, side: p.side, marginMode: p.marginMode, contracts: p.contracts,
          size, entryPrice: p.entryPrice, markPrice, leverage: p.leverage, margin: p.margin,
          unrealized, liquidationPrice: p.liquidationPrice,
        };
        // Validate each row before assembling the response; one bad row cannot reject its neighbours.
        return positionField('position', () => {
          const checked = validateMexcFutures({ status: 'ok', account, positions: [candidate], skippedPositions: 0 });
          if (checked.status !== 'ok') throw new Error();
          return checked.positions;
        });
      } catch (error) {
        skipPosition(error, p.raw);
        return [];
      }
    });
    return parse('futures-positions', positionsResponse, () =>
      positionField('position', () => validateMexcFutures({
        status: 'ok',
        account,
        skippedPositions,
        positions,
      })),
    );
  } catch (error) {
    if (!(error instanceof ReadFailure))
      diagnostic('futures-positions', null, undefined, 'unexpected-internal-response');
    return { status: error instanceof ReadFailure && error.permission ? 'no-permission' : 'unavailable' };
  }
}
