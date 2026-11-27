import { useEffect, useState, useSyncExternalStore } from 'react';
import { inRegularSession } from '../market-data/LiveQuotes';
import { getMarketProfile } from '../market-data/MarketProfile';
import { isMarketLoginRequired, isMarketQuotaError, marketProxyAccess } from '../market-data/marketProxyAccess';
import { loadWarrantQuotes, waitForWarrantRefresh, WARRANT_BATCH, type WarrantQuoteSample, type WarrantQuotes } from '../report/warrants';

const taiwan = getMarketProfile('^TWII');
/**
 * Larger result sets are scanned once and then only on request: repeating a scan of hundreds of
 * warrants all session would spend most of the shared daily quote quota and keep loading TWSE MIS.
 */
export const WARRANT_AUTO_REFRESH_MAX = WARRANT_BATCH * 10;
const documentVisible = () => !document.hidden;
const subscribeVisibility = (listener: () => void) => {
  document.addEventListener('visibilitychange', listener);
  return () => document.removeEventListener('visibilitychange', listener);
};

type Scan = {
  key: string;
  loading: boolean;
  attempted: number;
  received: number;
  firstAsOf: number | null;
  lastAsOf: number | null;
  session: WarrantQuotes['session'] | null;
  samples: Record<string, WarrantQuoteSample>;
};

export function useWarrantQuotes(active: boolean, key: string) {
  const access = useSyncExternalStore(marketProxyAccess.subscribe, marketProxyAccess.getSnapshot);
  const visible = useSyncExternalStore(subscribeVisibility, documentVisible, () => true);
  const [scan, setScan] = useState<Scan | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const signedIn = access.status === 'ready' || access.status === 'quota';
  const enabled = active && visible && signedIn && !!key;

  useEffect(() => {
    if (!active || !visible) return;
    setNow(Date.now());
    const clock = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(clock);
  }, [active, visible]);

  useEffect(() => {
    if (!enabled) {
      setScan(null);
      return;
    }
    const scope = new AbortController();
    const { signal } = scope;
    const symbols = key.split(',');

    async function scanAll(): Promise<WarrantQuotes['session'] | null> {
      const snapshot: Scan = {
        key, loading: true, attempted: 0, received: 0, firstAsOf: null, lastAsOf: null, session: null, samples: {},
      };
      const publish = () => {
        if (!signal.aborted) setScan({ ...snapshot, samples: { ...snapshot.samples } });
      };
      publish();
      while (snapshot.attempted < symbols.length && !signal.aborted) {
        const batch = symbols.slice(snapshot.attempted, snapshot.attempted + WARRANT_BATCH);
        const request = new AbortController();
        const abort = () => request.abort();
        signal.addEventListener('abort', abort, { once: true });
        const timeout = setTimeout(abort, 15_000);
        let resumeAt: number | undefined;
        try {
          const data = await loadWarrantQuotes(batch, request.signal);
          if (signal.aborted) return null;
          snapshot.firstAsOf ??= data.asOf;
          snapshot.lastAsOf = data.asOf;
          snapshot.session = data.session;
          for (const [symbol, book] of Object.entries(data.results)) {
            snapshot.samples[symbol] = { book, asOf: data.asOf, session: data.session };
            if (book.status !== 'source_unavailable') snapshot.received++;
          }
        } catch (error) {
          if (signal.aborted || isMarketLoginRequired(error)) return null;
          if (isMarketQuotaError(error)) resumeAt = error.until;
          // An ordinary batch failure advances the scan without counting it as a source result.
        } finally {
          clearTimeout(timeout);
          signal.removeEventListener('abort', abort);
        }
        if (resumeAt !== undefined) {
          if (!Number.isFinite(resumeAt)) {
            snapshot.loading = false;
            publish();
            return null;
          }
          // Retry the same batch after the shared gate reopens without replaying completed requests.
          await waitForWarrantRefresh(Math.max(0, resumeAt - Date.now()) + 50, signal);
          continue;
        }
        snapshot.attempted += batch.length;
        snapshot.loading = snapshot.attempted < symbols.length;
        publish();
      }
      return snapshot.session;
    }

    async function refresh() {
      while (!signal.aborted) {
        const session = await scanAll();
        if (signal.aborted || marketProxyAccess.getSnapshot().until === Infinity) return;
        if (symbols.length > WARRANT_AUTO_REFRESH_MAX) return;
        // Larger scans spend more time idle between rounds to leave room for the daily quota.
        await waitForWarrantRefresh(Math.max(30_000, Math.ceil(symbols.length / WARRANT_BATCH) * 8_000), signal);
        if (session === 'closed') {
          while (!inRegularSession(taiwan, Date.now(), 0)) await waitForWarrantRefresh(60_000, signal);
        }
      }
    }

    void refresh().catch(() => undefined);
    return () => scope.abort();
  }, [enabled, key, attempt]);

  return {
    access, now, scan: enabled && scan?.key === key ? scan : null,
    autoRefresh: key.split(',').length <= WARRANT_AUTO_REFRESH_MAX,
    refresh: () => setAttempt(value => value + 1),
  };
}
