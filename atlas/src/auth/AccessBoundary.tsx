import { useEffect, useSyncExternalStore } from 'react';
import { accessState, startAccessRefresh } from './access';
import { Site } from '../app/Site';

/** Account/policy changes discard all rendered transient state before another account can interact. */
export function AccessBoundary() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  useEffect(() => startAccessRefresh(), []);
  if (!access.ready) return <p role="status">正在確認帳號與儲存權限…</p>;
  return <Site key={access.epoch} />;
}
