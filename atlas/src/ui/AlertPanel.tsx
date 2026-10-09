import { useState, useSyncExternalStore, type FormEvent } from 'react';
import type { Timeframe } from '../market-data/MarketDataProvider';
import type { Drawing } from '../drawing/DrawingModel';
import type { AlertDefinition } from '../storage/schema';
import { AppStore } from '../app/AppStore';

type AlertKind = 'level' | 'ma' | 'drawing';
type AlertDirection = 'above' | 'below' | 'cross';

/** Stored reasons stay in English (data format); the panel shows them in Chinese. */
const invalidReasonText: Record<string, string> = {
  'Drawing reference unavailable': '參考的畫線已不存在',
  'Referenced drawing was deleted': '參考的畫線已刪除',
  'Referenced drawing is scoped to another timeframe': '參考的畫線屬於其他週期',
};

function dateText(value: number | null) {
  return value === null ? '尚未' : new Date(value * 1000).toLocaleString('zh-TW');
}

export function AlertPanel({
  store,
  symbol,
  timeframe,
  drawings,
}: {
  store: AppStore;
  symbol: string;
  timeframe: Timeframe;
  drawings: Drawing[];
}) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [kind, setKind] = useState<AlertKind>('level');
  const [direction, setDirection] = useState<AlertDirection>('cross');
  const [level, setLevel] = useState('');
  const [maType, setMaType] = useState<'SMA' | 'EMA'>('SMA');
  const [period, setPeriod] = useState('20');
  const [drawingId, setDrawingId] = useState('');
  const [error, setError] = useState('');
  const alerts = snapshot.app.alerts.filter((alert) => alert.symbol === symbol && alert.timeframe === timeframe);
  const alertDrawings = drawings.filter((drawing) => drawing.symbol === symbol && (drawing.type === 'horizontal' || drawing.type === 'ray'));

  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    try {
      const definition: AlertDefinition = {
        id: crypto.randomUUID(),
        symbol,
        timeframe,
        kind,
        direction: kind === 'level' ? direction : 'cross',
        ...(kind === 'level' ? { level: Number(level) } : {}),
        ...(kind === 'ma' ? { maType, period: Number(period) } : {}),
        ...(kind === 'drawing' ? { drawingId } : {}),
        enabled: true,
        invalidReason: null,
        lastTriggered: null,
        lastEvaluated: null,
        baseline: null,
        referencePrice: null,
      };
      store.addAlert(definition);
      setLevel('');
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const updateAlert = (id: string, change: Partial<AlertDefinition>) => {
    const all = store.getSnapshot().app.alerts.map((alert) => alert.id === id ? { ...alert, ...change } : alert);
    store.updateAlerts(all);
  };

  const toggleAlert = (alert: AlertDefinition) => {
    const enabling = !alert.enabled;
    updateAlert(alert.id, {
      enabled: enabling,
      ...(enabling ? { lastEvaluated: null, baseline: null } : {}),
    });
  };

  const deleteAlert = (id: string) => {
    store.updateAlerts(store.getSnapshot().app.alerts.filter((alert) => alert.id !== id));
  };

  return (
    <section className="research-panel alert-panel" aria-label={`${symbol} 價格提醒`}>
      <div className="section-heading">
        <span>價格提醒</span>
        <span>{symbol} · {timeframe}</span>
      </div>
      <p className="research-disclaimer">
        提醒只儲存在這台裝置。網頁開啟且在前景時，每分鐘以新收盤的 K 棒檢查一次目前股票與週期；不會在背景執行，也不會推播通知。
      </p>
      <form className="research-form" onSubmit={create}>
        <label>
          提醒條件
          <select aria-label="提醒條件" value={kind} onChange={(event) => setKind(event.target.value as AlertKind)}>
            <option value="level">價格</option>
            <option value="ma">均線</option>
            <option value="drawing">水平畫線</option>
          </select>
        </label>
        {kind === 'level' && (
          <>
            <label>
              價格
              <input aria-label="提醒價格" type="number" step="any" value={level} onChange={(event) => setLevel(event.target.value)} required />
            </label>
            <label>
              觸發方向
              <select aria-label="觸發方向" value={direction} onChange={(event) => setDirection(event.target.value as AlertDirection)}>
                <option value="above">向上穿越</option>
                <option value="below">向下穿越</option>
                <option value="cross">任一方向穿越</option>
              </select>
            </label>
          </>
        )}
        {kind === 'ma' && (
          <div className="research-field-row">
            <label>
              均線類型
              <select aria-label="均線類型" value={maType} onChange={(event) => setMaType(event.target.value as 'SMA' | 'EMA')}>
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
              </select>
            </label>
            <label>
              週期
              <input aria-label="均線週期" type="number" min="1" max="5000" step="1" value={period} onChange={(event) => setPeriod(event.target.value)} required />
            </label>
          </div>
        )}
        {kind === 'drawing' && (
          <label>
            水平線或水平射線
            <select aria-label="提醒參考畫線" value={drawingId} onChange={(event) => setDrawingId(event.target.value)} required disabled={!alertDrawings.length}>
              <option value="">{alertDrawings.length ? '選擇畫線' : '沒有可用的水平畫線'}</option>
              {alertDrawings.map((drawing, index) => (
                <option key={drawing.id} value={drawing.id}>
                  {drawing.type === 'ray' ? '水平射線' : '水平線'} {index + 1} · {drawing.points[0]?.price.toFixed(2)}
                </option>
              ))}
            </select>
            {!alertDrawings.length && <small className="small muted">請先在此股票畫一條水平線或水平射線。</small>}
          </label>
        )}
        <button className="primary-button" type="submit" disabled={kind === 'drawing' && !alertDrawings.length} style={{ minHeight: 44 }}>建立提醒</button>
      </form>
      {error && <p className="research-error" role="alert">{error}</p>}
      <div className="alert-list" aria-live="polite">
        <h3>{symbol} · {timeframe} 的提醒</h3>
        {!alerts.length ? <p className="small muted">此股票與週期尚無提醒。</p> : alerts.map((alert) => (
          <article className="alert-card" key={alert.id}>
            <div className="alert-card-heading">
              <b>{alert.kind === 'level' ? `價格${{ above: '向上穿越', below: '向下穿越', cross: '穿越' }[alert.direction]} ${alert.level}` : alert.kind === 'ma' ? `穿越 ${alert.maType} ${alert.period}` : '穿越畫線'}</b>
              <span>{alert.enabled ? '啟用中' : '已停用'}</span>
            </div>
            {alert.kind === 'drawing' && <p className="small muted">參考：{(() => { const type = alertDrawings.find((drawing) => drawing.id === alert.drawingId)?.type; return type === 'ray' ? '水平射線' : type === 'horizontal' ? '水平線' : '已不存在'; })()} · {alert.referencePrice === null ? '—' : alert.referencePrice.toFixed(2)}</p>}
            {alert.invalidReason && <p className="research-error" role="status">已失效：{invalidReasonText[alert.invalidReason] ?? alert.invalidReason}</p>}
            <dl className="alert-state">
              <div><dt>最近觸發</dt><dd>{dateText(alert.lastTriggered)}</dd></div>
              <div><dt>最近檢查</dt><dd>{dateText(alert.lastEvaluated)}</dd></div>
            </dl>
            <div className="alert-actions">
              <button type="button" disabled={!!alert.invalidReason} aria-label={`${alert.enabled ? '停用' : '啟用'}提醒`} onClick={() => toggleAlert(alert)} style={{ minHeight: 44 }}>
                {alert.enabled ? '停用' : '啟用'}
              </button>
              <button type="button" onClick={() => deleteAlert(alert.id)} style={{ minHeight: 44 }}>刪除</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
