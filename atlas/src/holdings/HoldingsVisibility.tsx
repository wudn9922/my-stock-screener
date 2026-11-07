import { useState } from 'react';
import type { StorageMode } from '../../../supabase/functions/_shared/storageAccess';
import { formatAmount, type Holding, type HoldingAccount, type HoldingValue } from './model';
import {
  DUST_PRESETS,
  defaultRules,
  dustThreshold,
  ruleReason,
  type AccountVisibilityRules,
  type HiddenReason,
} from './visibility';

const storageNote: Record<StorageMode, string> = {
  none: '隱藏設定只在本次瀏覽有效，重新開啟就會恢復顯示全部。',
  device: '隱藏設定保存在此裝置的帳號空間，不會上傳。',
  cloud: '隱藏設定保存在此裝置的帳號空間（只有帳戶與商品代號，不含數量或成本），不會上傳，也不會同步到其他裝置。',
};

/** Per-account rules; the threshold is in this account's own currency. */
export function VisibilitySettings({
  account,
  rules = defaultRules(),
  storageMode,
  onChange,
}: {
  account: HoldingAccount;
  rules?: AccountVisibilityRules;
  storageMode: StorageMode;
  onChange: (rules: AccountVisibilityRules) => void;
}) {
  const preset = (DUST_PRESETS as readonly string[]).includes(rules.dust.threshold);
  const [custom, setCustom] = useState(!preset);
  const [draft, setDraft] = useState(preset ? '' : rules.dust.threshold);
  const invalid = custom && draft.trim() !== '' && dustThreshold(draft) === null;
  const active = rules.dust.enabled || (account.kind === 'crypto-exchange' && rules.hideUsdt);
  return (
    <details className="holdings-visibility">
      <summary>隱藏設定{active ? '（已啟用）' : ''}</summary>
      <label className="holdings-check">
        <input
          type="checkbox"
          checked={rules.dust.enabled}
          onChange={(event) => onChange({ ...rules, dust: { ...rules.dust, enabled: event.target.checked } })}
        />
        隱藏小額持倉
      </label>
      {rules.dust.enabled && (
        <div className="holdings-fields">
          <label>
            市值低於（{account.currency}）
            <select
              aria-label="小額門檻"
              value={custom ? 'custom' : rules.dust.threshold}
              onChange={(event) => {
                if (event.target.value === 'custom') {
                  setCustom(true);
                  setDraft(rules.dust.threshold);
                  return;
                }
                setCustom(false);
                onChange({ ...rules, dust: { enabled: true, threshold: event.target.value } });
              }}
            >
              {DUST_PRESETS.map((value) => (
                <option key={value} value={value}>
                  &lt; {value} {account.currency}
                </option>
              ))}
              <option value="custom">自訂</option>
            </select>
          </label>
          {custom && (
            <label>
              自訂門檻（{account.currency}）
              <input
                aria-label={`自訂小額門檻（${account.currency}）`}
                inputMode="decimal"
                autoComplete="off"
                value={draft}
                onChange={(event) => {
                  setDraft(event.target.value);
                  const threshold = dustThreshold(event.target.value);
                  if (threshold) onChange({ ...rules, dust: { enabled: true, threshold } });
                }}
              />
            </label>
          )}
        </div>
      )}
      {invalid && (
        <p role="alert" className="holdings-error">
          門檻請填大於 0 的數字（最多 15 位整數、18 位小數）；目前沿用 &lt; {rules.dust.threshold} {account.currency}。
        </p>
      )}
      {account.kind === 'crypto-exchange' && (
        <label className="holdings-check">
          <input
            type="checkbox"
            checked={rules.hideUsdt}
            onChange={(event) => onChange({ ...rules, hideUsdt: event.target.checked })}
          />
          隱藏 USDT（計價單位）
        </label>
      )}
      <p className="holdings-muted">
        市值以本頁顯示的報價計算；沒有報價的持股不會自動隱藏。隱藏只影響顯示與加總，不會刪除或修改持股，同步與匯入仍包含全部持股。
        {storageNote[storageMode]}
      </p>
    </details>
  );
}

const reasonText = (reason: HiddenReason, rules: AccountVisibilityRules, currency: string) =>
  reason === 'manual'
    ? '手動隱藏'
    : reason === 'usdt'
      ? 'USDT 計價單位'
      : `小額（< ${rules.dust.threshold} ${currency}）`;

/** Collapsed list at the bottom of an account; each row can be unhidden. */
export function HiddenHoldings({
  account,
  rows,
  rules = defaultRules(),
  onUnhide,
}: {
  account: HoldingAccount;
  rows: { holding: Holding; result: HoldingValue; hidden: HiddenReason }[];
  rules?: AccountVisibilityRules;
  onUnhide: (holding: Holding, ruleStillHides: boolean) => void;
}) {
  if (!rows.length) return null;
  return (
    <section className="holdings-hidden" aria-label="已隱藏持股">
      <details>
        <summary>已隱藏 {rows.length} 檔</summary>
        <p role="note">這些持股不計入總市值、損益、市場小計與帳戶小計；資料仍保留，同步與匯入照常包含。</p>
        <ul>
          {rows.map(({ holding, result, hidden }) => (
            <li key={holding.id} aria-label={`${holding.symbol} 已隱藏`}>
              <span className="holdings-hidden-symbol">{holding.symbol}</span>
              <span className="holdings-muted">
                {reasonText(hidden, rules, account.currency)} · 市值 {formatAmount(result.value)}{' '}
                {holding.costCurrency}
              </span>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onUnhide(holding, ruleReason(holding, account, result.value, rules) !== null)}
              >
                取消隱藏
              </button>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
