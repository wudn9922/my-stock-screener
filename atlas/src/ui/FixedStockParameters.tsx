import { useEffect, useState, type FormEvent } from 'react';
import { FIXED_STOCK_GROUP, parameterMAs, parseMovingAverages, settingsApi, type ParameterStock } from '../cloud/settingsApi';
import { ConsoleDialog } from './ConsoleDialog';

export function FixedStockParameters({
  ticker,
  defaults,
  onClose,
  onSaved,
}: {
  ticker: string;
  defaults: number[];
  onClose: () => void;
  onSaved: (values: number[]) => void;
}) {
  const [mas, setMas] = useState(defaults.join(', '));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setReady(false);
    setError('');
    void settingsApi<ParameterStock[]>('stocks.list')
      .then((stocks) => {
        const fixed = stocks.find((stock) => stock.ticker === ticker && stock.group_id === FIXED_STOCK_GROUP);
        if (active) {
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
  }, [ticker, attempt]);
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
      <p>固定參數會保存到 Supabase，出榜或刪除清單後仍保留。只有此股票在分組或已啟用監控的清單中時才使用，優先於分組與清單的預設均線。</p>
      {loading && <p role="status">正在載入已保存的參數…</p>}
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
            確認並永久儲存
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
