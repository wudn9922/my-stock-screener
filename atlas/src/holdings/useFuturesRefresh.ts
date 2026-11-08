import { useEffect } from 'react';
import { usePageActive } from '../app/pageActivity';
import type { MexcStore } from './mexc';
/** The total includes every connected account, so refresh even accounts outside the selected list. */
export function useFuturesRefresh(store: MexcStore, accountIds: string, disabled: boolean, updated: string) {
  const active = usePageActive();
  useEffect(() => {
    if (disabled || !active || !accountIds) return;
    const refresh = () => {
      if (document.visibilityState === 'visible')
        for (const id of accountIds.split(',')) void store.loadFutures(id);
    };
    refresh();
    const timer = setInterval(refresh, 60000);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [store, accountIds, disabled, active, updated]);
}
