import { HelpDisclosure } from './HelpDisclosure';
import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import { FIXED_STOCK_GROUP, FIXED_PARAMETER_GROUPS, parameterMAs, parseMovingAverages, settingsApi, type ParameterStock } from '../cloud/settingsApi';
import { stockParameterRows } from '../cloud/stockParameters';
import { saveStockParameters } from '../cloud/saveStockParameters';
import { ConsoleDialog } from './ConsoleDialog';
import { accessState } from '../auth/access';
import { getStorageContextKey } from '../auth/storage';

export function FixedStockParameters({
  ticker,
  defaults,
  onClose,
  prefill = false,
  onSaved,
}: {
  ticker: string;
  defaults: number[];
  /** Offer the applied chart values as an explicit replacement; existing values always open first. */
  prefill?: boolean;
  onClose: () => void;
  onSaved: (values: number[]) => void;
}) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const context = getStorageContextKey();
  const [loadedContext, setLoadedContext] = useState('');
  const [mas, setMas] = useState(defaults.join(', '));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [rows, setRows] = useState<ParameterStock[]>([]);
  const current = rows[0];
  const saved = current ? parameterMAs(current) : null;
  useEffect(() => {
    let active = true;
    setLoading(true);
    setReady(false);
    setError('');
    void settingsApi<ParameterStock[]>('stocks.list')
      .then((stocks) => {
        const matches = stockParameterRows(stocks, ticker);
        const fixed = matches[0];
        if (active) {
          setRows(matches);
          setLoadedContext(context);
          if (fixed) setMas(parameterMAs(fixed).join(', '));
          setReady(true);
        }
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : '無法載入固定參數');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [ticker, attempt, prefill, context]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (!ready || loadedContext !== getStorageContextKey()) throw new Error('帳號或儲存權限已變更，請重新載入參數。');
      const values = parseMovingAverages(mas);
      await saveStockParameters(ticker, values, current);
      onSaved(values);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '固定參數儲存失敗');
    } finally {
      setBusy(false);
    }
  };
  return (
    <ConsoleDialog title={`${ticker} 固定均線參數`} busy={busy} onClose={onClose}>
      <HelpDisclosure label="固定參數保存與優先順序">
        {access.storageMode === 'cloud'
          ? '固定參數會保存到你的 Supabase 帳號，出榜或刪除清單後仍保留。'
          : access.storageMode === 'device'
            ? '固定參數只保存在這個帳號的裝置空間，不會上傳。'
            : '固定參數只在本次瀏覽套用，重新載入後清除，不會保存或上傳。'}
        有分組時更新原有股票參數；沒有分組才保存為無分組固定參數。
      </HelpDisclosure>
      {loading && <p role="status">正在載入已保存的參數…</p>}
      {ready && (
        <p role="status">{saved ? `目前已保存：${saved.join(' / ')}；保留${current.group_id === FIXED_STOCK_GROUP ? '無分組' : `原有分組「${FIXED_PARAMETER_GROUPS[current.group_id as keyof typeof FIXED_PARAMETER_GROUPS] ?? current.group_id}」`}，確認後才更新。` : '目前沒有已保存的固定參數。'}</p>
      )}
      {current?.group_id !== FIXED_STOCK_GROUP && rows.some(row => row.group_id === FIXED_STOCK_GROUP) && <p role="status">
        發現重複的無分組參數：{rows.filter(row => row.group_id === FIXED_STOCK_GROUP).map(row => parameterMAs(row).join(' / ')).join('；')}。
        確認儲存會更新原有分組，並清除這些無分組重複筆；取消不會更動資料。
      </p>}
      {prefill && ready && saved && <button type="button" className="ghost-button" disabled={busy}
        onClick={() => setMas(defaults.join(', '))}>改用目前圖表的均線（{defaults.join(' / ')}）</button>}
      {error && (
        <p role="alert" className="console-message">
          {error}
        </p>
      )}
      <form onSubmit={(event) => void save(event)}>
        <label>
          均線參數
          <textarea
            aria-label="個股固定均線參數"
            value={mas}
            onChange={(event) => setMas(event.target.value)}
            required
            disabled={loading || busy}
            placeholder="20, 50, 200"
          />
        </label>
        <HelpDisclosure label="參數輸入格式">填入 1～4 個不重複的整數（1～1000），用逗號分隔。</HelpDisclosure>
        <div className="console-actions">
          <button type="submit" className="primary-button" disabled={loading || busy || !ready || loadedContext !== context}>
            {access.storageMode === 'none'
              ? '確認並套用本次參數'
              : access.storageMode === 'device'
                ? '確認並儲存到此裝置'
                : '確認並儲存到雲端'}
          </button>
          {error && (
            <button type="button" className="ghost-button" disabled={busy} onClick={() => setAttempt((value) => value + 1)}>
              重新載入
            </button>
          )}
          <button type="button" className="ghost-button" disabled={busy} onClick={onClose}>
            取消編輯
          </button>
        </div>
      </form>
    </ConsoleDialog>
  );
}
