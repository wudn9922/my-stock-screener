import { useState } from 'react';
import { toneClass } from '../ui/format';
import { formatAmount, holdingDecimal, type HoldingAccount } from './model';
import type { PrincipalMetrics } from './principal';

function percent(value: string | null) {
  return value === null ? '—' : formatAmount(value) + '%';
}

export function PrincipalSummary({ account, metrics }: { account: HoldingAccount; metrics: PrincipalMetrics }) {
  const money = (value: string | null) => value === null ? '—' : formatAmount(value) + ' ' + account.currency;
  return (
    <section className="holdings-principal-summary" aria-label="帳戶本金摘要">
      <dl className="portfolio-total-values">
        <div><dt>本金</dt><dd>{money(metrics.principal)}</dd></div>
        <div><dt>現金</dt><dd>{money(metrics.cash)}</dd></div>
        <div><dt>帳戶總值</dt><dd>{money(metrics.total)}</dd></div>
        <div><dt>本金報酬率</dt><dd className={toneClass(metrics.percent === null ? null : Number(metrics.percent))}>{percent(metrics.percent)}</dd></div>
        <div><dt>持股比重</dt><dd>{percent(metrics.weight)}</dd></div>
      </dl>
      {metrics.belowCost && <p role="note" className="holdings-error">本金小於持股成本</p>}
      {metrics.incomplete && <p className="holdings-muted">持股估值尚未完整，暫不計算本金報酬率。</p>}
      <p className="holdings-muted">本金報酬率只含未實現盈虧；依可見現貨持股計算，不含合約權益。</p>
    </section>
  );
}

export function PrincipalGroupSummary({ currency, metrics }: { currency: string; metrics: PrincipalMetrics }) {
  return (
    <div className="holdings-principal-group" aria-label={currency + ' 本金小計'}>
      {metrics.missingPrincipal ? (
        <p className="holdings-muted">{currency} 成本報酬率 {percent(metrics.costPercent)}；部分帳戶未填本金，無法計算本金合計及本金報酬率。</p>
      ) : (
        <>
          <p>{currency} 本金合計 {formatAmount(metrics.principal)} · 本金報酬率 <span className={toneClass(metrics.percent === null ? null : Number(metrics.percent))}>{percent(metrics.percent)}</span></p>
          {metrics.incomplete && <p className="holdings-muted">持股估值尚未完整，暫不計算本金報酬率。</p>}
          <p className="holdings-muted">本金報酬率只含未實現盈虧；僅同幣別現貨。</p>
        </>
      )}
    </div>
  );
}

export function PrincipalSettings({ account, disabled, onSave }: {
  account: HoldingAccount;
  disabled: boolean;
  onSave: (value: string | null) => Promise<void>;
}) {
  const [value, setValue] = useState(account.principal ?? '');
  const [error, setError] = useState('');
  const save = async (clear = false) => {
    setError('');
    const candidate = clear ? '' : value.trim();
    let principal: string | null = null;
    if (candidate) {
      try { principal = holdingDecimal(candidate); }
      catch { setError('本金須為大於 0 的十進位金額，最多 15 位整數及 18 位小數。'); return; }
    }
    try { await onSave(principal); if (clear) setValue(''); }
    catch { setError('本金儲存未完成，請重新載入後重試。'); }
  };
  return (
    <form className="holdings-principal-settings" aria-label="本金設定" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <label>
        本金（{account.currency}，選填）
        <input aria-label={'本金（' + account.currency + '）'} inputMode="decimal" value={value} disabled={disabled}
          onChange={(event) => { setValue(event.target.value); setError(''); }} />
      </label>
      <div className="holdings-toolbar">
        <button type="submit" className="primary-button" disabled={disabled || value.trim() === (account.principal ?? '')}>儲存本金</button>
        <button type="button" className="ghost-button" disabled={disabled || account.principal === undefined} onClick={() => void save(true)}>清除本金</button>
      </div>
      {error && <p role="alert" className="holdings-error">{error}</p>}
    </form>
  );
}
