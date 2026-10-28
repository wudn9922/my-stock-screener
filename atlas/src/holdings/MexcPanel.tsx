import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { mexcStore } from './mexc';
import { type HoldingAccount, type HoldingAccountState } from './model';
import { parseNumber } from './import';
import { formatDateTime } from '../ui/format';

interface Props {
  account: HoldingAccount;
  confirmed?: HoldingAccountState;
  disabled: boolean;
  onConnected?: () => void;
  onCancel?: () => void;
}
export function MexcPanel({ account, confirmed, disabled, onConnected, onCancel }: Props) {
  const store = mexcStore(),
    { state, loading, busy, error } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const connection = state.connections.find((entry) => entry.accountId === account.id);
  const [keyForm, setKeyForm] = useState(false),
    [apiKey, setApiKey] = useState(''),
    [apiSecret, setApiSecret] = useState(''),
    [readOnly, setReadOnly] = useState(false);
  const [costs, setCosts] = useState<Record<string, string>>({}),
    [ack, setAck] = useState(false),
    [message, setMessage] = useState(''),
    [formError, setFormError] = useState('');
  const [tick, setTick] = useState(Date.now());
  const lastAttempt = useRef(0);
  const draftCosts = useRef(false);
  useEffect(() => {
    const timer = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const cooldown = Math.max(0, Math.ceil(((state.retryAt ? Date.parse(state.retryAt) : 0) - tick) / 1000));
  const blocked = disabled || busy || loading;
  const staleRevision = !!connection && connection.holdingRevision !== confirmed?.revision;
  useEffect(() => {
    if (!connection) return;
    setAck(false);
    if (draftCosts.current) return;
    setCosts(
      Object.fromEntries(
        connection.balances.map((balance) => [
          balance.asset,
          confirmed?.holdings.find((holding) => holding.symbol === balance.symbol)?.averageCost ?? '',
        ]),
      ),
    );
  }, [connection?.snapshotId, confirmed?.revision]);
  const run = async (task: () => Promise<unknown>, success: string) => {
    setFormError('');
    setMessage('');
    try {
      await task();
      setMessage(success);
    } catch {
      /* Store exposes only a fixed whitelist of safe error messages. */
    }
  };
  useEffect(() => {
    if (!connection || disabled || keyForm || connection.needsReview) return;
    const sync = () => {
      if (
        document.visibilityState !== 'visible' ||
        draftCosts.current ||
        store.getSnapshot().busy ||
        (store.getSnapshot().state.retryAt && Date.parse(store.getSnapshot().state.retryAt!) > Date.now()) ||
        Date.now() - lastAttempt.current < 300000
      )
        return;
      if (Date.now() - Date.parse(connection.syncedAt) < 300000 && !staleRevision) return;
      lastAttempt.current = Date.now();
      void store.call('sync', { accountId: account.id }).catch(() => {
        /* Safe status remains visible; no automatic rapid retry. */
      });
    };
    sync();
    const timer = setInterval(sync, 300000);
    window.addEventListener('focus', sync);
    document.addEventListener('visibilitychange', sync);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', sync);
      document.removeEventListener('visibilitychange', sync);
    };
  }, [
    connection?.snapshotId,
    connection?.needsReview,
    connection?.holdingRevision,
    account.id,
    disabled,
    keyForm,
    staleRevision,
    state.retryAt,
    store,
  ]);
  const validCosts =
    connection?.balances.every((balance) => {
      try {
        parseNumber(costs[balance.asset] ?? '');
        return true;
      } catch {
        return false;
      }
    }) ?? false;
  return (
    <section className="console-card mexc-panel" aria-label="MEXC 自動同步">
      <h3>MEXC 自動同步</h3>
      {error && (
        <p role="alert">
          {error}
          <button type="button" className="ghost-button" disabled={blocked} onClick={() => void store.load()}>
            重新載入連線狀態
          </button>
        </p>
      )}
      {formError && (
        <p role="alert" className="holdings-error">
          {formError}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {loading && <p role="status">載入 MEXC 連線中…</p>}
      {!state.configured && !loading && (
        <p role="note">同步服務尚未就緒。需要維護者先設定伺服器加密金鑰與資料表；請不要在其他地方貼上 API Secret。</p>
      )}
      {connection && (
        <>
          <p>
            已連線 · API Key 末四碼 <strong>{connection.keyLast4}</strong>
          </p>
          <p className="holdings-muted">
            上次同步 {formatDateTime(connection.syncedAt)}。停留此頁且可見時，每五分鐘讀取一次現貨餘額與報價。
          </p>
          <div className="holdings-toolbar">
            <button
              type="button"
              className="primary-button"
              disabled={blocked || cooldown > 0 || draftCosts.current}
              onClick={() => {
                lastAttempt.current = Date.now();
                void run(() => store.call('sync', { accountId: account.id }), '已讀取最新 MEXC 餘額。');
              }}
            >
              同步餘額{cooldown > 0 ? `（${cooldown} 秒）` : ''}
            </button>
            <button
              type="button"
              className="ghost-button"
              disabled={blocked || draftCosts.current}
              onClick={() => {
                setKeyForm(true);
                setMessage('');
              }}
            >
              更換 API 金鑰
            </button>
            <button
              type="button"
              className="text-button"
              disabled={blocked}
              onClick={() => {
                if (!confirm('中斷 MEXC 連線並刪除伺服器上的 API 金鑰？已確認的持股與均價會保留。')) return;
                void run(async () => {
                  await store.call('disconnect', { accountId: account.id });
                  setKeyForm(false);
                  setCosts({});
                  draftCosts.current = false;
                }, '已中斷連線並刪除伺服器金鑰，持股與均價仍保留。');
              }}
            >
              中斷連線
            </button>
          </div>
        </>
      )}
      {(!connection || keyForm) && (
        <form
          aria-label="連接 MEXC API"
          onSubmit={(event) => {
            event.preventDefault();
            if (blocked || !readOnly || !state.configured || cooldown > 0) return;
            const key = apiKey.trim(),
              secret = apiSecret.trim();
            setApiKey('');
            setApiSecret('');
            setReadOnly(false);
            void run(async () => {
              await store.call('connect', {
                account,
                expectedRevision: confirmed?.revision ?? null,
                apiKey: key,
                apiSecret: secret,
                readOnlyConfirmed: true,
              });
              setKeyForm(false);
              onConnected?.();
            }, 'MEXC 已連線。請確認餘額與平均成本。');
          }}
        >
          <p>
            <strong>只勾「讀取」權限，不要勾交易、轉帳或提幣。</strong>
            伺服器會檢查金鑰權限；這個功能只讀現貨帳戶，不下單、不提幣。
          </p>
          <div className="holdings-fields">
            <label>
              API Key
              <input
                type="password"
                aria-label="MEXC API Key"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={128}
                value={apiKey}
                disabled={blocked || !state.configured}
                onChange={(event) => setApiKey(event.target.value)}
              />
            </label>
            <label>
              Secret Key
              <input
                type="password"
                aria-label="MEXC Secret Key"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={256}
                value={apiSecret}
                disabled={blocked || !state.configured}
                onChange={(event) => setApiSecret(event.target.value)}
              />
            </label>
          </div>
          <label className="holdings-check">
            <input
              type="checkbox"
              checked={readOnly}
              disabled={blocked || !state.configured}
              onChange={(event) => setReadOnly(event.target.checked)}
            />
            我確認只開讀取權限，並同意以此 MEXC 現貨餘額取代這個帳戶的庫存
          </label>
          <div className="holdings-toolbar">
            <button
              type="submit"
              className="primary-button"
              disabled={
                blocked ||
                !state.configured ||
                !readOnly ||
                apiKey.trim().length < 8 ||
                apiSecret.trim().length < 8 ||
                cooldown > 0
              }
            >
              連接並讀取 MEXC{cooldown > 0 ? `（${cooldown} 秒）` : ''}
            </button>
            {onCancel && (
              <button type="button" className="ghost-button" disabled={busy} onClick={onCancel}>
                取消
              </button>
            )}
            {connection && (
              <button
                type="button"
                className="ghost-button"
                disabled={busy}
                onClick={() => {
                  setKeyForm(false);
                  setApiKey('');
                  setApiSecret('');
                }}
              >
                取消更換
              </button>
            )}
          </div>
          <p className="holdings-muted">
            金鑰只在按下連線時送往伺服器，AES-GCM 加密保存，不存瀏覽器。失敗後需重新輸入；畫面只保留末四碼。
          </p>
        </form>
      )}
      {connection && (connection.needsReview || staleRevision) && (
        <>
          <p role="status">有新的餘額或數量增加，請補上平均成本並確認。確認前，此帳戶暫不列入損益總計。</p>
          {staleRevision ? (
            <div>
              <p>持股已手動修改，請先重新同步餘額，再確認成本。</p>
              {draftCosts.current && (
                <button
                  type="button"
                  className="ghost-button"
                  disabled={blocked}
                  onClick={() => {
                    draftCosts.current = false;
                    setCosts({});
                    setAck(false);
                  }}
                >
                  捨棄未儲存成本，準備重新同步
                </button>
              )}
            </div>
          ) : (
            <form
              aria-label="確認 MEXC 成本"
              onSubmit={(event) => {
                event.preventDefault();
                if (blocked || !ack || !validCosts) return;
                const values: Record<string, string> = {};
                try {
                  for (const balance of connection.balances)
                    values[balance.asset] = parseNumber(costs[balance.asset] ?? '');
                } catch {
                  setFormError('請填妥每個幣種的平均成本。');
                  return;
                }
                void run(async () => {
                  await store.call('apply', {
                    accountId: account.id,
                    snapshotId: connection.snapshotId,
                    expectedRevision: connection.holdingRevision,
                    costs: values,
                  });
                  draftCosts.current = false;
                  setAck(false);
                }, '已確認成本並套用同步餘額。');
              }}
            >
              <div className="holdings-preview">
                {connection.balances.map((balance) => {
                  const previous = confirmed?.holdings.find((holding) => holding.symbol === balance.symbol);
                  return (
                    <fieldset key={balance.asset}>
                      <legend>
                        {balance.asset}
                        {previous ? ' · 請核對成本' : ' · 新增幣種'}
                      </legend>
                      <p>
                        餘額 {balance.quantity}（可用 {balance.free}、凍結 {balance.locked}）
                      </p>
                      <label>
                        平均成本（USDT，含買進費用）
                        <input
                          aria-label={`${balance.asset} 平均成本`}
                          autoComplete="off"
                          inputMode="decimal"
                          disabled={blocked}
                          value={costs[balance.asset] ?? ''}
                          onChange={(event) => {
                            draftCosts.current = true;
                            setCosts((values) => ({ ...values, [balance.asset]: event.target.value }));
                            setAck(false);
                          }}
                        />
                      </label>
                      {balance.asset === 'USDT' && (
                        <p className="holdings-muted">USDT 為計價幣，若按 USDT 記帳可填 1；這不代表 USDT＝USD。</p>
                      )}
                    </fieldset>
                  );
                })}
              </div>
              <label className="holdings-check">
                <input
                  type="checkbox"
                  checked={ack}
                  disabled={blocked}
                  onChange={(event) => setAck(event.target.checked)}
                />
                已補上新增餘額的成本，並核對所有平均成本
              </label>
              <button type="submit" className="primary-button" disabled={blocked || !ack || !validCosts}>
                確認成本並套用同步餘額
              </button>
              {draftCosts.current && (
                <button
                  type="button"
                  className="ghost-button"
                  disabled={blocked}
                  onClick={() => {
                    draftCosts.current = false;
                    setCosts(
                      Object.fromEntries(
                        connection.balances.map((balance) => [
                          balance.asset,
                          confirmed?.holdings.find((holding) => holding.symbol === balance.symbol)?.averageCost ?? '',
                        ]),
                      ),
                    );
                    setAck(false);
                  }}
                >
                  還原未儲存的成本
                </button>
              )}
            </form>
          )}
        </>
      )}
      <details>
        <summary>同步範圍與成本說明</summary>
        <p>
          只讀取現貨的可用＋凍結餘額，不含合約、理財或已實現損益。MEXC
          不提供買進均價，減少餘額時沿用原均價；新增或增加餘額需你補成本。相同餘額下的賣出再買回無法從餘額判斷，仍需自行核對均價。同步報價未提供前日收盤，今日損益顯示未知。
        </p>
        <a href="https://www.mexc.com/user/openapi" target="_blank" rel="noreferrer">
          開啟 MEXC API 管理
        </a>
      </details>
    </section>
  );
}
