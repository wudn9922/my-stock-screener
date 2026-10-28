import { accessState } from '../auth/access';
import { canPersist, getStorageContextKey, userStorage } from '../auth/storage';
import { holdingsContextApi } from './contextApi';
import {
  emptyHoldings,
  replaceAccount,
  validateHoldingsDocument,
  type HoldingAccount,
  type Holding,
  type HoldingsDocument,
} from './model';

const KEY = 'holdings:v1';
const inactiveSnapshot = { document: emptyHoldings(), error: '', loading: false };
export class HoldingsStore {
  private readonly context = getStorageContextKey();
  private readonly mode = accessState.getSnapshot().storageMode;
  private listeners = new Set<() => void>();
  private snapshot: { document: HoldingsDocument; error: string; loading: boolean } = {
    document: emptyHoldings(),
    error: '',
    loading: false,
  };
  private blocked = false;
  constructor() {
    if (this.mode === 'cloud') return;
    try {
      const raw = userStorage.getItem(KEY);
      if (raw) this.snapshot = { ...this.snapshot, document: validateHoldingsDocument(JSON.parse(raw)) };
    } catch {
      this.blocked = true;
      this.snapshot = { ...this.snapshot, error: '此裝置的持股資料無法讀取，已停止寫入以保護原資料。' };
    }
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => (this.context === getStorageContextKey() ? this.snapshot : inactiveSnapshot);
  private active() {
    if (this.context !== getStorageContextKey() || (this.mode === 'device' && !canPersist()))
      throw new Error('帳號或儲存權限已變更，請重新操作。');
  }
  private set(update: Partial<typeof this.snapshot>) {
    this.active();
    this.snapshot = { ...this.snapshot, ...update };
    this.listeners.forEach((listener) => listener());
  }
  async reload() {
    this.active();
    if (this.mode !== 'cloud') return;
    this.set({ loading: true, error: '' });
    try {
      const document = validateHoldingsDocument(await holdingsContextApi(this.context, 'holdings.list'));
      this.set({ document });
    } catch {
      if (this.context === getStorageContextKey()) this.set({ error: '無法載入雲端持股，請確認服務已啟用後重試。' });
    } finally {
      if (this.context === getStorageContextKey()) this.set({ loading: false });
    }
  }
  async replace(account: HoldingAccount, holdings: Holding[]) {
    this.active();
    if (this.blocked) throw new Error('儲存空間無法讀取，原資料保持不變。');
    const next = replaceAccount(this.snapshot.document, account, holdings);
    await this.write(next, 'holdings.replaceAccount', {
      account,
      holdings,
      expectedRevision: this.snapshot.document.accounts.find((a) => a.account.id === account.id)?.revision ?? null,
    });
  }
  async removeAccount(id: string) {
    this.active();
    const previous = this.snapshot.document.accounts.find((a) => a.account.id === id);
    if (!previous) return;
    await this.write(
      { version: 1, accounts: this.snapshot.document.accounts.filter((a) => a.account.id !== id) },
      'holdings.deleteAccount',
      { accountId: id, expectedRevision: previous.revision },
    );
  }
  /** MEXC replies are verified, same-owner cloud documents; never persisted locally. */
  acceptCloudDocument(document: unknown) {
    this.active();
    if (this.mode !== 'cloud') throw new Error('MEXC 同步需要雲端儲存授權。');
    this.set({ document: validateHoldingsDocument(document), error: '' });
  }
  private async write(next: HoldingsDocument, action: string, payload: Record<string, unknown>) {
    this.active();
    if (this.blocked || this.snapshot.loading || this.snapshot.error) throw new Error('請先成功載入持股，再進行修改。');
    let document: HoldingsDocument;
    try {
      if (this.mode === 'cloud')
        document = validateHoldingsDocument(await holdingsContextApi(this.context, action, payload));
      else {
        document = validateHoldingsDocument(next);
        userStorage.setItem(KEY, JSON.stringify(document));
      }
    } catch {
      throw new Error('無法確認持股儲存結果，請重新載入確認後再試。');
    }
    this.set({ document, error: '' });
  }
}
let current: { context: string; store: HoldingsStore } | undefined;
export function holdingsStore() {
  const context = getStorageContextKey();
  if (current?.context !== context) current = { context, store: new HoldingsStore() };
  return current.store;
}
