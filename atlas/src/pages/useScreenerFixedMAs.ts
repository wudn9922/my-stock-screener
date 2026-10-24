import { useEffect, useState, useSyncExternalStore } from 'react';
import { getDailyBars } from '../app/dataSources';
import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { settingsApi, type ParameterStock } from '../cloud/settingsApi';
import type { GroupItem } from '../report/schema';
import type { FixedScreenerMas } from './screenerFilters';
import { loadScreenerFixedMAs } from './screenerFixedMAs';

/** Loads only configured stocks, and only while MA filtering or inspection needs their references. */
export function useScreenerFixedMAs(wanted: boolean, items: readonly GroupItem[]) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const context = getStorageContextKey();
  const [state, setState] = useState<{ context: string; items: readonly GroupItem[]; mas: FixedScreenerMas; reason: string | null } | null>(null);
  useEffect(() => {
    if (!wanted || items.length === 0) return;
    let active = true;
    const controller = new AbortController();
    setState({ context, items, mas: {}, reason: '固定均線參數載入中…' });
    void settingsApi<ParameterStock[]>('stocks.list', {}, controller.signal)
      .then(stocks => loadScreenerFixedMAs(items, stocks, getDailyBars))
      .then(mas => { if (active) setState({ context, items, mas, reason: null }); })
      .catch(() => { if (active) setState({ context, items, mas: {}, reason: '固定均線參數無法載入，請重新整理頁面重試。' }); });
    return () => { active = false; controller.abort(); };
  }, [wanted, items, context, access.userId, access.storageMode]);
  const current = state?.context === context && state.items === items ? state : null;
  return { fixedMAs: current?.mas, maReferenceReason: wanted && items.length > 0 ? (current ? current.reason : '固定均線參數載入中…') : null };
}
