import { useEffect, useState, useSyncExternalStore } from 'react';
import { accessState, startAccessRefresh } from './access';
import { LoginGate, loginGateState } from './LoginGate';
import { deviceSessionVersion, subscribeDeviceSession } from '../cloud/deviceSession';
import { Site } from '../app/Site';

/** Account/policy changes discard all rendered transient state before another account can interact. */
export function AccessBoundary() {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  useSyncExternalStore(subscribeDeviceSession, deviceSessionVersion);
  // Guest is chosen per launch and per account state: logging out shows the login page again.
  const [guestEpoch, setGuestEpoch] = useState<number | null>(null);
  useEffect(() => startAccessRefresh(), []);
  if (!access.ready) return <p role="status">正在確認帳號與儲存權限…</p>;
  const gate = loginGateState(access, guestEpoch === access.epoch);
  if (gate === 'wait') return <p className="login-gate" role="status">正在登入…</p>;
  if (gate === 'gate') return <LoginGate onGuest={() => setGuestEpoch(access.epoch)} />;
  return <Site key={access.epoch} />;
}
