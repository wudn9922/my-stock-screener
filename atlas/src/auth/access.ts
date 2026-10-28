import { SCREENER_HOSTING } from '../app/HostingMode';
import { refreshLineIdentity, type LineIdentity } from '../cloud/lineIdentity';
import { forgetDevice } from '../cloud/deviceSession';

export type StorageMode = 'none' | 'device' | 'cloud';
export interface AccessSnapshot {
  ready: boolean;
  /** The first server check has finished (ready may come earlier, as a temporary guest). */
  settled: boolean;
  userId: string | null;
  isAdmin: boolean;
  storageMode: StorageMode;
  revision: string;
  epoch: number;
  error: string | null;
}
export interface VerifiedAccess {
  userId: string;
  isAdmin: boolean;
  storageMode: StorageMode;
  revision: string;
}
const ACCESS_URL = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/user-settings';
const USER_ID = /^U[0-9a-f]{32}$/;

/** Every grant is resolved from a server-verified LINE or remembered-device token. Nothing is restored from browser grants. */
export class AccessController {
  private snapshot: AccessSnapshot;
  private listeners = new Set<() => void>();
  private identity: LineIdentity | null = null;
  private request = 0;
  private initial: Promise<AccessSnapshot> | null = null;
  constructor(
    private readonly scoped = SCREENER_HOSTING,
    private readonly identify: () => Promise<LineIdentity | null> = refreshLineIdentity,
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {
    this.snapshot = { ready: !scoped, settled: !scoped, userId: null, isAdmin: false, storageMode: scoped ? 'none' : 'device', revision: scoped ? 'guest' : 'standalone', epoch: 0, error: null };
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getSnapshot = () => this.snapshot;
  private publish(next: Omit<AccessSnapshot, 'epoch' | 'settled'>, settled = this.snapshot.settled) {
    const previous = this.snapshot;
    const changed = previous.ready !== next.ready || previous.userId !== next.userId || previous.storageMode !== next.storageMode || previous.isAdmin !== next.isAdmin || previous.revision !== next.revision;
    this.snapshot = { ...next, settled, epoch: previous.epoch + Number(changed) };
    this.listeners.forEach((listener) => listener());
    return this.snapshot;
  }
  private temporary(userId: string | null = null, error: string | null = null, settled = this.snapshot.settled) {
    return this.publish({ ready: true, userId, isAdmin: false, storageMode: 'none', revision: userId ? 'unverified' : 'guest', error }, settled);
  }
  allowGuestWhileLoading() {
    if (!this.snapshot.ready) this.temporary();
  }
  initialize() {
    this.initial ??= this.refresh();
    return this.initial.then(() => this.snapshot);
  }
  async refresh(): Promise<AccessSnapshot> {
    if (!this.scoped) return this.snapshot;
    const request = ++this.request;
    let candidate: LineIdentity | null;
    try { candidate = await this.identify(); }
    catch { candidate = null; }
    if (request !== this.request) return this.snapshot;
    // Drop old account views before asking for a new account's grant.
    if (candidate?.userId !== this.identity?.userId) {
      this.identity = null;
      this.temporary(candidate?.userId ?? null);
    }
    const token = candidate?.idToken();
    if (!candidate || !USER_ID.test(candidate.userId) || !token) {
      this.identity = null;
      return this.temporary(null, null, true);
    }
    this.identity = candidate;
    try {
      const response = await this.fetcher(import.meta.env?.VITE_SETTINGS_API_URL || ACCESS_URL, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'access.get', idToken: token }),
        signal: AbortSignal.timeout(15000), cache: 'no-store',
      });
      const envelope = await response.json() as { ok?: boolean; data?: Partial<VerifiedAccess>; error?: string };
      if (request !== this.request) return this.snapshot;
      // A revoked or expired remembered device is forgotten; network and server errors keep it.
      if (candidate.kind === 'device' && response.status === 401) {
        forgetDevice(false);
        this.identity = null;
        return this.temporary(null, '此裝置的登入已失效，請重新登入。', true);
      }
      if (!candidate.idToken()) {
        this.identity = null;
        return this.temporary(null, '登入身分已變更或過期，目前操作僅暫存。', true);
      }
      const grant = envelope.data;
      if (!response.ok || !envelope.ok || !grant || grant.userId !== candidate.userId || typeof grant.isAdmin !== 'boolean' || !['none', 'device', 'cloud'].includes(grant.storageMode ?? '') || typeof grant.revision !== 'string')
        throw new Error('無法確認儲存授權，目前操作僅暫存。');
      return this.publish({ ready: true, userId: grant.userId, isAdmin: grant.isAdmin, storageMode: grant.storageMode as StorageMode, revision: grant.revision, error: null }, true);
    } catch (error) {
      if (request !== this.request) return this.snapshot;
      return this.temporary(candidate.userId, error instanceof Error ? error.message : '無法確認儲存授權，目前操作僅暫存。', true);
    }
  }
  authenticatedIdentity() {
    if (!this.identity?.idToken() || this.identity.userId !== this.snapshot.userId) return null;
    return this.identity;
  }
  async requireCloud() {
    await this.initialize();
    const identity = this.authenticatedIdentity();
    if (!identity || this.snapshot.storageMode !== 'cloud') throw new Error('此帳號未獲雲端儲存授權。');
    return identity;
  }
}

export const accessState = new AccessController();
export const initializeAccess = () => accessState.initialize();
export const refreshAccess = () => accessState.refresh();
export const requireCloudAccess = () => accessState.requireCloud();
export const currentIdentity = () => accessState.authenticatedIdentity();

/** Recheck logout, account changes, token expiry and administrator revocation without persisting a grant. */
export function startAccessRefresh() {
  if (!SCREENER_HOSTING) return () => {};
  const refresh = () => { if (document.visibilityState === 'visible') void refreshAccess(); };
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('focus', refresh);
  const timer = setInterval(refresh, 60_000);
  return () => { clearInterval(timer); document.removeEventListener('visibilitychange', refresh); window.removeEventListener('focus', refresh); };
}
