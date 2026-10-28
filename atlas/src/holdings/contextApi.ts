import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';
import { settingsApi } from '../cloud/settingsApi';

/** Bind before initializeAccess can await: sensitive payloads must never move to a new identity. */
export async function holdingsContextApi<T>(
  context: string,
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  if (context !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，已取消操作。');
  const controller = new AbortController();
  const unsubscribe = accessState.subscribe(() => {
    if (context !== getStorageContextKey()) controller.abort(new Error('帳號或儲存權限已變更，已取消操作。'));
  });
  try {
    return await settingsApi<T>(action, payload, controller.signal);
  } finally {
    unsubscribe();
  }
}
