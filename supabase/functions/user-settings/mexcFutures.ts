import { holdingsObject } from './holdings.ts';
import {
  FUTURES_SYMBOL,
  futuresDecimal,
  validateMexcFutures,
  type MexcFutures,
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
const SCALE = 10n ** 18n;

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
function contractAmount(contracts: string, contractSize: string): string {
  const product = units(contracts) * units(contractSize);
  if (product % SCALE) throw new Error('Inexact futures size');
  const amount = product / SCALE;
  return sourceDecimal(`${amount / SCALE}.${String(amount % SCALE).padStart(18, '0')}`, false, true);
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
  const read = async (path: Path, step: FuturesStep) => {
    let status: number | null = null,
      metadata: unknown,
      reason = 'network-or-timeout';
    const privateRead = path.startsWith('/api/v1/private/');
    try {
      signal.throwIfAborted();
      const authHeaders = privateRead ? (await futuresAuth(auth, now())).headers : {};
      const response = await abortable(
        fetcher(MEXC_FUTURES_ORIGIN + path, {
          method: 'GET',
          headers: { 'Content-Type': 'application/json', ...authHeaders },
          signal,
          redirect: 'error',
          cache: 'no-store',
          referrerPolicy: 'no-referrer',
        }),
        signal,
      );
      status = response.status;
      reason = 'response-size-or-empty';
      if (Number(response.headers.get('Content-Length')) > 2 * 1024 * 1024 || !response.body) {
        void response.body?.cancel().catch(() => undefined);
        throw new Error();
      }
      const reader = response.body.getReader(),
        chunks: Uint8Array[] = [];
      let size = 0;
      reason = 'invalid-json-or-stream';
      try {
        while (true) {
          const part = await abortable(reader.read(), signal);
          if (part.done) break;
          size += part.value.byteLength;
          if (size > 2 * 1024 * 1024) {
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
      diagnostic(
        step,
        status,
        metadata,
        signal.aborted ? 'network-or-timeout' : status !== null && status >= 400 ? 'upstream-http' : reason,
      );
      throw error instanceof ReadFailure ? error : new ReadFailure(privateRead && status === 403);
    }
  };
  const parse = <T>(
    step: FuturesStep,
    response: { data: unknown; status: number },
    task: (data: unknown) => T,
  ): T => {
    try {
      return task(response.data);
    } catch {
      diagnostic(step, response.status, undefined, 'invalid-response');
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
    const pending = parse('futures-positions', positionsResponse, (data) => {
      if (!Array.isArray(data) || data.length > 200) throw new Error();
      return data
        .map(holdingsObject)
        .flatMap((p): (Record<string, unknown> & { contracts: string; symbol: string })[] => {
          if (typeof p.symbol !== 'string' || !FUTURES_SYMBOL.test(p.symbol)) {
            skippedPositions++;
            return [];
          }
          if (p.state !== '1' && p.state !== '2' && p.state !== '3') throw new Error();
          if (p.state === '3') return [];
          const contracts = sourceDecimal(p.holdVol);
          if (contracts === '0') return [];
          return [{ ...p, contracts, symbol: p.symbol }];
        });
    });
    if (!pending.length) return { status: 'ok', account, positions: [], skippedPositions };
    const detailsResponse = await read('/api/v1/contract/detail/country', 'futures-detail');
    const tickersResponse = await read('/api/v1/contract/ticker', 'futures-detail');
    const wanted = new Set(pending.map((p) => p.symbol));
    const lookup = (data: unknown, field: string) => {
      const rows = Array.isArray(data) ? data : [data];
      if (rows.length > 20000) throw new Error();
      const result = new Map<string, string>();
      for (const raw of rows) {
        const row = holdingsObject(raw);
        if (typeof row.symbol !== 'string' || !wanted.has(row.symbol)) continue;
        if (result.has(row.symbol)) throw new Error();
        result.set(row.symbol, sourceDecimal(row[field], false, true));
      }
      if (result.size !== wanted.size) throw new Error();
      return result;
    };
    const sizes = parse('futures-detail', detailsResponse, (data) => lookup(data, 'contractSize'));
    const prices = parse('futures-detail', tickersResponse, (data) => lookup(data, 'fairPrice'));
    return parse('futures-positions', positionsResponse, () =>
      validateMexcFutures({
        status: 'ok',
        account,
        skippedPositions,
        positions: pending.map((p) => ({
          symbol: p.symbol,
          side: p.positionType === '1' ? 'long' : p.positionType === '2' ? 'short' : null,
          marginMode: p.openType === '1' ? 'isolated' : p.openType === '2' ? 'cross' : null,
          contracts: p.contracts,
          size: contractAmount(p.contracts, sizes.get(p.symbol)!),
          entryPrice: sourceDecimal(p.holdAvgPriceFullyScale ?? p.holdAvgPrice, false, true),
          markPrice: prices.get(p.symbol),
          leverage: sourceDecimal(p.leverage, false, true),
          margin: sourceDecimal(p.im),
          unrealized: sourceDecimal(p.unRealizedPnl, true),
          liquidationPrice:
            p.liquidatePrice == null || sourceDecimal(p.liquidatePrice) === '0'
              ? null
              : sourceDecimal(p.liquidatePrice, false, true),
        })),
      }),
    );
  } catch (error) {
    if (!(error instanceof ReadFailure))
      diagnostic('futures-positions', null, undefined, 'unexpected-internal-response');
    return { status: error instanceof ReadFailure && error.permission ? 'no-permission' : 'unavailable' };
  }
}
