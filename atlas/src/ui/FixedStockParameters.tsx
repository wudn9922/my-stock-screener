import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import { FIXED_STOCK_GROUP, parameterMAs, parseMovingAverages, settingsApi, type ParameterStock } from '../cloud/settingsApi';
import { ConsoleDialog } from './ConsoleDialog';
import { accessState } from '../auth/access';

export function FixedStockParameters({
  ticker,
  defaults,
  onClose,
  prefill = false,
  onSaved,
}: {
  ticker: string;
  defaults: number[];
  /** Keep `defaults` in the input (e.g. a recommendation) and show the saved values separately instead of loading them. */
  prefill?: boolean;
  onClose: () => void;
  onSaved: (values: number[]) => void;
}) {
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const [mas, setMas] = useState(defaults.join(', '));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [saved, setSaved] = useState<number[] | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setReady(false);
    setError('');
    void settingsApi<ParameterStock[]>('stocks.list')
      .then((stocks) => {
        const fixed = stocks.find((stock) => stock.ticker === ticker && stock.group_id === FIXED_STOCK_GROUP);
        if (active) {
          setSaved(fixed ? parameterMAs(fixed) : null);
          if (fixed && !prefill) setMas(parameterMAs(fixed).join(', '));
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
  }, [ticker, attempt, prefill]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const values = parseMovingAverages(mas);
      await settingsApi('stocks.save', {
        ticker,
        groupId: FIXED_STOCK_GROUP,
        mas: values,
      });
      onSaved(values);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '固定參數儲存失敗');
    } finally {
      setBusy(false);
    }
  };
  return (
    <ConsoleDialog title={`${ticker} 固定均線參數`} busy={busy} onClose={onClose}>
      <p>
        {access.storageMode === 'cloud'
          ? '固定參數會保存到你的 Supabase 帳號，出榜或刪除清單後仍保留。'
          : access.storageMode === 'device'
            ? '固定參數只保存在這個帳號的裝置空間，不會上傳。'
            : '固定參數只在本次瀏覽套用，重新載入後清除，不會保存或上傳。'}
        均線優先於分組與清單的預設值。
      </p>
      {loading && <p role="status">正在載入已保存的參數…</p>}
      {prefill && ready && (
        <p role="status">{saved ? `目前已保存：${saved.join(' / ')}；儲存後會被取代。` : '目前沒有已保存的固定參數。'}</p>
      )}
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
        <p>填入 1～4 個不重複的整數（1～1000），用逗號分隔。</p>
        <div className="console-actions">
          <button type="submit" className="primary-button" disabled={loading || busy || !ready}>
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
