import { z } from 'zod';
import { accessState, currentIdentity, initializeAccess, requireCloudAccess } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { searchStocks } from '../app/dataSources';
import type { MonitorSignal } from '../../../supabase/functions/monitor-signals/contract';

const date = z.string().datetime({ offset: true });
const positive = z.number().finite().positive().nullable();
const signalSchema = z.object({
  groupId: z.string().min(1).max(80), groupName: z.string().min(1).max(100), ticker: z.string().regex(/^[A-Z0-9.^_-]{1,30}$/),
  name: z.string().min(1).max(100), market: z.enum(['TW', 'US']), direction: z.enum(['breakout_up', 'breakout_down']),
  maPeriod: z.number().int().min(1).max(1000), sentAt: date, price: positive, maValue: positive, volumeRatio: positive,
});
export const monitorSnapshotSchema = z.object({
  version: z.literal(1), generatedAt: date, windowStart: date, truncated: z.boolean(), signals: z.array(signalSchema).max(5000),
});
export type MonitorSnapshot = z.infer<typeof monitorSnapshotSchema>;
export type MonitorStock = {
  ticker: string; symbol: string | null; name: string; market: 'TW' | 'US'; signals: MonitorSignal[]; latest: string; maPeriods: number[];
};
export type MonitorGroup = { key: string; name: string; market: 'TW' | 'US'; stocks: MonitorStock[]; up: number; down: number; latest: string };
/** The exchange-local session date shown for each market (YYYY-MM-DD). */
export type MonitorSessions = Partial<Record<'TW' | 'US', string>>;
export type MonitorResult = { status: 'ok'; data: MonitorSnapshot; groups: MonitorGroup[]; sessions: MonitorSessions } | { status: 'error'; message: string };
export const MONITOR_API_URL = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/monitor-signals';

/** A Taiwan bare code must resolve in the real directory; never guess .TW vs .TWO. */
export async function monitorSymbol(ticker: string, market: 'TW' | 'US', search = searchStocks): Promise<string | null> {
  if (market === 'TW' && /^\d+$/.test(ticker)) {
    const hits = await search(ticker, 20).catch(() => []);
    const matches = hits.filter(hit => hit.market === 'TW' && hit.symbol.split('.')[0] === ticker);
    return matches.length === 1 ? matches[0]!.symbol : null;
  }
  const symbol = market === 'US' ? ticker.replace(/\./g, '-') : ticker;
  return /^(?:\^[A-Z0-9]+|[A-Z][A-Z0-9-]{0,14}|\d{4,6}\.(?:TW|TWO))$/.test(symbol) ? symbol : null;
}

const SESSION_ZONES = { TW: 'Asia/Taipei', US: 'America/New_York' } as const;
const sessionDate = (sentAt: string, market: 'TW' | 'US') =>
  new Intl.DateTimeFormat('en-CA', { timeZone: SESSION_ZONES[market], year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(sentAt));

/**
 * Only each market's latest session with a sent signal; the next session's signals replace it.
 * Dates are exchange-local, so US signals sent after midnight in Taipei stay on their own session.
 */
export function latestSessionSignals(signals: readonly MonitorSignal[]): { signals: MonitorSignal[]; sessions: MonitorSessions } {
  const sessions: MonitorSessions = {};
  for (const signal of signals) {
    const day = sessionDate(signal.sentAt, signal.market);
    if (!sessions[signal.market] || day > sessions[signal.market]!) sessions[signal.market] = day;
  }
  return { signals: signals.filter(signal => sessionDate(signal.sentAt, signal.market) === sessions[signal.market]), sessions };
}

/** Group by market + monitor group, merge multiple triggered MAs into one stock card. */
export function groupMonitorSignals(signals: readonly MonitorSignal[], symbols: ReadonlyMap<string, string | null> = new Map()): MonitorGroup[] {
  const groups = new Map<string, MonitorGroup>();
  const seen = new Set<string>();
  for (const signal of [...signals].sort((a, b) => b.sentAt.localeCompare(a.sentAt))) {
    const id = `${signal.market}:${signal.groupId}:${signal.ticker}:${signal.maPeriod}`;
    if (seen.has(id)) continue;
    seen.add(id);
    const key = `${signal.market}:${signal.groupId}`;
    let group = groups.get(key);
    if (!group) { group = { key, name: signal.groupName, market: signal.market, stocks: [], up: 0, down: 0, latest: signal.sentAt }; groups.set(key, group); }
    let stock = group.stocks.find(row => row.ticker === signal.ticker);
    if (!stock) {
      stock = { ticker: signal.ticker, symbol: symbols.get(`${signal.market}:${signal.ticker}`) ?? null,
        name: signal.name, market: signal.market, signals: [], latest: signal.sentAt, maPeriods: [] };
      group.stocks.push(stock);
    }
    stock.signals.push(signal);
    stock.maPeriods.push(signal.maPeriod);
  }
  for (const group of groups.values()) {
    group.up = group.stocks.filter(stock => stock.signals.some(signal => signal.direction === 'breakout_up')).length;
    group.down = group.stocks.filter(stock => stock.signals.some(signal => signal.direction === 'breakout_down')).length;
    group.stocks.forEach(stock => stock.maPeriods.sort((a, b) => a - b));
  }
  return [...groups.values()];
}

/** Bind before initialization, abort on any account/grant change, keep results in RAM only. */
export async function fetchMonitorSignals(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<MonitorResult> {
  const context = getStorageContextKey();
  const abort = new AbortController();
  const cancel = () => abort.abort();
  const unsubscribe = accessState.subscribe(() => { if (context !== getStorageContextKey()) cancel(); });
  signal?.addEventListener('abort', cancel, { once: true });
  const timeout = setTimeout(cancel, 15_000);
  try {
    if (signal?.aborted) cancel();
    await initializeAccess();
    if (abort.signal.aborted || context !== getStorageContextKey()) throw new Error('cancelled');
    const identity = await requireCloudAccess();
    const idToken = identity?.idToken();
    if (!idToken || currentIdentity()?.userId !== identity.userId || abort.signal.aborted || context !== getStorageContextKey()) throw new Error('cancelled');
    const response = await fetcher(import.meta.env.VITE_MONITOR_SIGNALS_API_URL || MONITOR_API_URL, {
      method: 'POST', body: JSON.stringify({ action: 'list', idToken }), headers: { 'Content-Type': 'application/json' },
      signal: abort.signal, cache: 'no-store', redirect: 'error',
    });
    if (response.status === 404) return { status: 'error', message: '監控訊號讀取服務尚未啟用，請維護者先完成設定。' };
    const result = await response.json();
    const parsed = monitorSnapshotSchema.safeParse(result?.data);
    if (!response.ok || !result?.ok || !parsed.success) throw new Error('unavailable');
    const latest = latestSessionSignals(parsed.data.signals);
    const unique = new Map(latest.signals.map(row => [`${row.market}:${row.ticker}`, row]));
    const symbols = new Map(await Promise.all([...unique].map(async ([key, row]) => [key, await monitorSymbol(row.ticker, row.market)] as const)));
    if (abort.signal.aborted || context !== getStorageContextKey()) throw new Error('cancelled');
    return { status: 'ok', data: parsed.data, groups: groupMonitorSignals(latest.signals, symbols), sessions: latest.sessions };
  } catch {
    return { status: 'error', message: '暫時無法取得突破／跌破訊號，請稍後重試。' };
  } finally {
    clearTimeout(timeout); unsubscribe(); signal?.removeEventListener('abort', cancel);
  }
}
