import { HelpDisclosure } from './HelpDisclosure';
import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import { FIXED_STOCK_GROUP, parameterMAs, parseMovingAverages, settingsApi, type ParameterStock } from '../cloud/settingsApi';
import { stockParameterRows } from '../cloud/stockParameters';
import { saveStockParameters, retryStockParameterCleanup, StockParameterCleanupError } from '../cloud/saveStockParameters';
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
  const [cleanupFailure, setCleanupFailure] = useState<StockParameterCleanupError | null>(null);
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
    setCleanupFailure(null);
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
      await saveStockParameters(ticker, values, current, undefined, rows);
      onSaved(values);
    } catch (caught) {
      if (caught instanceof StockParameterCleanupError) setCleanupFailure(caught);
      setError(caught instanceof Error ? caught.message : '個股均線儲存失敗');
    } finally {
      setBusy(false);
    }
  };
  return (
    <ConsoleDialog title={`${ticker} 個股均線（所有清單共用）`} busy={busy} onClose={onClose}>
      <HelpDisclosure label="個股均線（所有清單共用）">
        {access.storageMode === 'cloud'
          ? '個股均線會保存到你的 Supabase 帳號，出榜或刪除清單後仍保留。'
          : access.storageMode === 'device'
            ? '個股均線只保存在這個帳號的裝置空間，不會上傳。'
            : '個股均線只在本次瀏覽套用，重新載入後清除，不會保存或上傳。'}
        每檔正規化股票代號只保存一筆參數；分組股票更新該列，未分組股票保存於無分組列。所有清單、圖表、選股、報告與監控共用。
      </HelpDisclosure>
      {loading && <p role="status">正在載入已保存的參數…</p>}
      {ready && (
        <p role="status">{saved ? `個股均線（所有清單共用）：${saved.join(' / ')}；確認後直接覆蓋。` : '目前沒有已保存的個股均線。'}</p>
      )}
      {rows.some(row => row.group_id !== FIXED_STOCK_GROUP) && rows.some(row => row.group_id === FIXED_STOCK_GROUP) && <p role="status">
        發現重複的無分組參數：{rows.filter(row => row.group_id === FIXED_STOCK_GROUP).map(row => parameterMAs(row).join(' / ')).join('；')}。
        目前以這筆較新的固定均線為準；確認後覆蓋分組列，成功才清除無分組重複筆；取消不會更動資料。
      </p>}
      {prefill && ready && saved && <button type="button" className="ghost-button" disabled={busy}
        onClick={() => setMas(defaults.join(', '))}>改用目前圖表的均線（{defaults.join(' / ')}）</button>}
      {error && (
        <p role="alert" className="console-message">
          {error}
        </p>
      )}
      {cleanupFailure && (
        <button type="button" className="ghost-button" disabled={busy} onClick={() => {
          setBusy(true);
          void retryStockParameterCleanup(cleanupFailure).then(() => onSaved(cleanupFailure.mas))
            .catch(caught => setError(caught instanceof Error ? caught.message : '重複資料清除失敗'))
            .finally(() => setBusy(false));
        }}>重試清除重複資料</button>
      )}
      <form onSubmit={(event) => void save(event)}>
        <label>
          均線參數
          <textarea
            aria-label="個股均線（所有清單共用）"
            value={mas}
            onChange={(event) => setMas(event.target.value)}
            required
            disabled={loading || busy || !!cleanupFailure}
            placeholder="20, 50, 200"
          />
        </label>
        <HelpDisclosure label="參數輸入格式">填入 1～4 個不重複的整數（1～1000），用逗號分隔。</HelpDisclosure>
        <div className="console-actions">
          <button type="submit" className="primary-button" disabled={loading || busy || !!cleanupFailure || !ready || loadedContext !== context}>
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
