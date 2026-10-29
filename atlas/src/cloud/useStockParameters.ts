import { useEffect, useState, useSyncExternalStore } from 'react';
import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { settingsApi, type ParameterStock } from './settingsApi';

let revision = 0;
const listeners = new Set<() => void>();
export function stockParametersChanged() {
  revision++;
  listeners.forEach(listener => listener());
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

/** No cross-account cache or fallback to someone else's report settings. */
export function useStockParameters(enabled = true) {
  useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const version = useSyncExternalStore(subscribe, () => revision);
  const context = getStorageContextKey();
  const [state, setState] = useState<{ context: string; stocks: ParameterStock[]; error: string | null }>();
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    void settingsApi<ParameterStock[]>('stocks.list', {}, abort.signal).then(stocks => {
      if (!abort.signal.aborted && context === getStorageContextKey()) setState({ context, stocks, error: null });
    }).catch(() => {
      if (!abort.signal.aborted && context === getStorageContextKey()) setState(previous => ({ context,
        stocks: previous?.context === context ? previous.stocks : [], error: '無法載入已保存的均線參數，請重新載入後再試。' }));
    });
    return () => abort.abort();
  }, [enabled, context, version]);
  return enabled && state?.context === context ? { ...state, ready: !state.error } : { context, stocks: [], error: null, ready: false };
}
