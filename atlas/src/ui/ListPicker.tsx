import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import type { AppStore } from '../app/AppStore';
import { createList, recommendedMas, setListMembership, useUnifiedLists } from '../lists/listActions';
import { listAvailability, listsContaining, MembershipToggler, symbolMarket, type UnifiedList } from '../lists/unifiedLists';
import { displayTicker } from './format';
import { useDialogFocus } from './useDialogFocus';
import './listPicker.css';

/**
 * The chart star's 「加入清單」 sheet: every editable list with its current membership. Each tap writes at once
 * (optimistic, rolled back to the stored state on failure); 「新增清單」 creates a list containing this symbol.
 */
export function ListPicker({ symbol, appStore, onClose, onManage }: {
  symbol: string;
  appStore: AppStore;
  onClose: () => void;
  onManage?: () => void;
}) {
  const data = useUnifiedLists(true, appStore);
  const latest = useRef(data);
  latest.current = data;
  const toggler = useMemo(() => new MembershipToggler(
    (key, checked) => {
      const list = latest.current.lists.find((entry) => entry.key === key);
      if (!list) return Promise.reject(new Error('此清單已不存在，請重新整理。'));
      return setListMembership(list, symbol, checked, appStore);
    },
    () => latest.current.reload(),
  ), [symbol, appStore]);
  useSyncExternalStore(toggler.subscribe, toggler.getVersion);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [asGroup, setAsGroup] = useState(false);
  const [creating, setCreating] = useState(false);
  useDialogFocus(true, 'list-picker');
  useEffect(() => {
    // Warm the 建議均線 file so a first group join does not wait for it.
    void recommendedMas(symbol);
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [symbol, onClose]);
  const base = listsContaining(data.lists, symbol);
  const members = toggler.members(base);
  const toggle = (list: UnifiedList, checked: boolean) => {
    setError('');
    setMessage('');
    const current = latest.current.lists;
    void toggler.toggle(current, listsContaining(current, symbol), list.key, checked).then((result) => {
      if (!result.ok) setError(`「${list.name}」未更新：${result.error}`);
      else setMessage(result.message ?? `${checked ? '已加入' : '已移出'}「${list.name}」`);
    });
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setError('');
    setMessage('');
    const market = symbolMarket(symbol);
    void createList({ name, type: asGroup ? 'group' : 'watchlist', market, symbols: asGroup ? [] : [symbol] })
      .then(async (created) => {
        setName('');
        if (!asGroup) {
          setMessage(created.message ?? `已建立「${name.trim()}」並加入 ${displayTicker(symbol)}`);
          return;
        }
        await data.reload();
        const list = latest.current.lists.find((entry) => entry.key === created.key);
        if (list) toggle(list, true);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : '清單建立失敗'))
      .finally(() => setCreating(false));
  };
  const lists = data.lists.filter((list) => list.kind !== 'rules');
  const plain = lists.filter((list) => list.kind !== 'group');
  const groups = lists.filter((list) => list.kind === 'group');
  const option = (list: UnifiedList) => {
    const unavailable = listAvailability(list, symbol);
    const checked = members.has(list.key);
    return (
      <li key={list.key}>
        <label className={`list-option${unavailable ? ' unavailable' : ''}`}>
          <input type="checkbox" checked={checked} disabled={!!unavailable && !checked}
            aria-describedby={list.kind === 'group' ? 'list-picker-group-hint' : undefined}
            onChange={(event) => toggle(list, event.target.checked)} />
          <span className="list-option-name">{list.name}</span>
          {list.feedsAlerts && <span className="list-badge">提醒／日報</span>}
          <small className="list-option-meta">{unavailable && !checked ? unavailable : `${list.symbols.length} 檔`}</small>
        </label>
      </li>
    );
  };
  // Portalled to <body>: the chart host's stacking context would otherwise leave the sheet under the tab bar.
  return createPortal(
    <div className="list-picker-backdrop" onClick={onClose}>
      <section className="list-picker" role="dialog" aria-modal="true" aria-labelledby="list-picker-title" data-dialog-focus="list-picker"
        data-testid="list-picker" onClick={(event) => event.stopPropagation()}>
        <header className="list-picker-head">
          <h2 id="list-picker-title">加入清單</h2>
          <span className="list-picker-symbol">{displayTicker(symbol)}</span>
          <button type="button" className="text-button" onClick={onClose}>完成</button>
        </header>
        <div className="list-picker-body">
          {data.loading && <p className="muted" role="status">載入分組…</p>}
          {data.error && <p className="console-message" role="alert">分組暫時無法載入：{data.error}</p>}
          {plain.length > 0 && <ul className="list-options" aria-label="自選清單">{plain.map(option)}</ul>}
          <h3 className="list-picker-section">分組</h3>
          <p id="list-picker-group-hint" className="list-picker-hint">
            一檔股票只在一個分組，勾選其他分組會移過去；均線沿用原設定，新股票套用建議均線。
            {data.cloud ? '分組會用於每日報告與突破提醒。' : '允許雲端保存後，分組才用於每日報告與提醒。'}
          </p>
          {groups.length > 0 && <ul className="list-options" aria-label="分組">{groups.map(option)}</ul>}
        </div>
        <form className="list-picker-create" onSubmit={create}>
          <input aria-label="新清單名稱" placeholder="新增清單" value={name} maxLength={30} required onChange={(event) => setName(event.target.value)} />
          <label className="list-picker-create-type">
            <input type="checkbox" checked={asGroup} onChange={(event) => setAsGroup(event.target.checked)} />分組
          </label>
          <button type="submit" className="primary-button" disabled={creating || !name.trim()}>新增</button>
        </form>
        {error && <p className="console-message" role="alert">{error}</p>}
        {!error && message && <p className="list-picker-status" role="status">{message}</p>}
        {onManage && <button type="button" className="text-button list-picker-manage" onClick={() => { onClose(); onManage(); }}>管理清單與均線</button>}
      </section>
    </div>,
    document.body,
  );
}
