import { accessState } from '../auth/access';
import { getStorageContextKey, userStorage } from '../auth/storage';
import { Money, type Holding, type HoldingAccount } from './model';

/**
 * Display-only holdings visibility. Never touches the holdings document: hidden holdings stay
 * stored, synced and imported exactly as before, and are only left out of what this page shows
 * and sums. Stored through the account/mode-scoped userStorage like other view preferences
 * (none: RAM only; device and cloud: this browser's account space, not uploaded).
 */
export const VISIBILITY_KEY = 'holdings-visibility-v1';
export const DUST_PRESETS = ['1', '10', '100'] as const;
const MAX_KEYS = 2000;
const DECIMAL = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,18})?$/;
const KEY = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\|[A-Z0-9./-]{1,30}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export interface AccountVisibilityRules {
  /** 隱藏小額持倉: market value strictly below `threshold`, in the account's own currency. */
  dust: { enabled: boolean; threshold: string };
  /** 隱藏 USDT（計價單位）: crypto accounts only. */
  hideUsdt: boolean;
}
export interface HoldingsVisibility {
  version: 1;
  /** Manually hidden, keyed `${accountId}|${symbol}` so re-sync/import keeps them hidden. */
  hidden: string[];
  /** Explicitly unhidden although a rule would hide them. */
  shown: string[];
  accounts: Record<string, AccountVisibilityRules>;
}
export type HiddenReason = 'manual' | 'dust' | 'usdt';

export const defaultRules = (): AccountVisibilityRules => ({ dust: { enabled: false, threshold: '1' }, hideUsdt: false });
export const emptyVisibility = (): HoldingsVisibility => ({ version: 1, hidden: [], shown: [], accounts: {} });
export function holdingKey(accountId: string, symbol: string) {
  return `${accountId}|${symbol}`;
}
/** A positive threshold with the same precision limits as holdings decimals; null when invalid. */
export function dustThreshold(value: string): string | null {
  const text = value.trim();
  return DECIMAL.test(text) && /[1-9]/.test(text) ? text : null;
}
function keys(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > MAX_KEYS) throw new Error('invalid');
  if (value.some((key) => typeof key !== 'string' || !KEY.test(key))) throw new Error('invalid');
  return [...new Set(value as string[])];
}
export function validateVisibility(value: unknown): HoldingsVisibility {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid');
  const row = value as Record<string, unknown>;
  if (row.version !== 1 || !row.accounts || typeof row.accounts !== 'object' || Array.isArray(row.accounts))
    throw new Error('invalid');
  const accounts: Record<string, AccountVisibilityRules> = {};
  const entries = Object.entries(row.accounts as Record<string, unknown>);
  if (entries.length > 30) throw new Error('invalid');
  for (const [id, raw] of entries) {
    const rule = raw as Record<string, unknown> | null;
    const dust = rule?.dust as Record<string, unknown> | undefined;
    if (
      !UUID.test(id) ||
      !rule ||
      typeof rule.hideUsdt !== 'boolean' ||
      !dust ||
      typeof dust.enabled !== 'boolean' ||
      typeof dust.threshold !== 'string' ||
      dustThreshold(dust.threshold) !== dust.threshold
    )
      throw new Error('invalid');
    accounts[id] = { dust: { enabled: dust.enabled, threshold: dust.threshold }, hideUsdt: rule.hideUsdt };
  }
  return { version: 1, hidden: keys(row.hidden), shown: keys(row.shown), accounts };
}

/** Which rule (if any) hides this row. A holding without a price is never auto-hidden. */
export function ruleReason(
  holding: Holding,
  account: HoldingAccount | undefined,
  value: string | null,
  rules: AccountVisibilityRules,
): Exclude<HiddenReason, 'manual'> | null {
  if (rules.hideUsdt && account?.kind === 'crypto-exchange' && holding.symbol === 'USDT/USDT') return 'usdt';
  if (rules.dust.enabled && value !== null && new Money(value).lt(rules.dust.threshold)) return 'dust';
  return null;
}
export function hiddenReason(
  holding: Holding,
  account: HoldingAccount | undefined,
  value: string | null,
  preferences: HoldingsVisibility,
): HiddenReason | null {
  const key = holdingKey(holding.accountId, holding.symbol);
  if (preferences.hidden.includes(key)) return 'manual';
  if (preferences.shown.includes(key)) return null;
  return ruleReason(holding, account, value, preferences.accounts[holding.accountId] ?? defaultRules());
}
export function hide(preferences: HoldingsVisibility, key: string): HoldingsVisibility {
  return {
    ...preferences,
    hidden: [...preferences.hidden.filter((item) => item !== key), key].slice(-MAX_KEYS),
    shown: preferences.shown.filter((item) => item !== key),
  };
}
/** Unhide; if a rule would still hide it, remember an explicit "show" so one click is enough. */
export function unhide(preferences: HoldingsVisibility, key: string, ruleStillHides: boolean): HoldingsVisibility {
  return {
    ...preferences,
    hidden: preferences.hidden.filter((item) => item !== key),
    shown: ruleStillHides
      ? [...preferences.shown.filter((item) => item !== key), key].slice(-MAX_KEYS)
      : preferences.shown.filter((item) => item !== key),
  };
}
export function setRules(
  preferences: HoldingsVisibility,
  accountId: string,
  rules: AccountVisibilityRules,
): HoldingsVisibility {
  return { ...preferences, accounts: { ...preferences.accounts, [accountId]: rules } };
}
/** Drop preferences of a deleted account; the holdings themselves are handled by the holdings store. */
export function forgetAccount(preferences: HoldingsVisibility, accountId: string): HoldingsVisibility {
  const accounts = { ...preferences.accounts };
  delete accounts[accountId];
  const keep = (key: string) => !key.startsWith(accountId + '|');
  return { ...preferences, hidden: preferences.hidden.filter(keep), shown: preferences.shown.filter(keep), accounts };
}

/** Context-bound preference store; a new account/storage mode gets a fresh, separately stored copy. */
class VisibilityStore {
  private readonly context = getStorageContextKey();
  readonly mode = accessState.getSnapshot().storageMode;
  private listeners = new Set<() => void>();
  private snapshot: HoldingsVisibility;
  constructor() {
    let value = emptyVisibility();
    try {
      const raw = userStorage.getItem(VISIBILITY_KEY);
      if (raw) value = validateVisibility(JSON.parse(raw));
    } catch {
      /* Unreadable or invalid preferences fall back to "show everything"; nothing is written until a change. */
    }
    this.snapshot = value;
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  update(change: (current: HoldingsVisibility) => HoldingsVisibility) {
    if (this.context !== getStorageContextKey()) return;
    const next = validateVisibility(change(this.snapshot));
    this.snapshot = next;
    try {
      userStorage.setItem(VISIBILITY_KEY, JSON.stringify(next));
    } catch {
      /* A view preference is optional; keep it for this page when storage is blocked. */
    }
    this.listeners.forEach((listener) => listener());
  }
}
let current: { context: string; store: VisibilityStore } | undefined;
export function holdingsVisibility() {
  const context = getStorageContextKey();
  if (current?.context !== context) current = { context, store: new VisibilityStore() };
  return current.store;
}
