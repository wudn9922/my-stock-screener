import { HelpDisclosure } from './HelpDisclosure';
import { useState, useSyncExternalStore, type FormEvent } from 'react';
import type { Timeframe } from '../market-data/MarketDataProvider';
import type { Drawing } from '../drawing/DrawingModel';
import type { AlertDefinition } from '../storage/schema';
import { AppStore } from '../app/AppStore';
import { accessState } from '../auth/access';
import { cloudSyncState } from '../cloud/startDrawingSync';
import { discordDrawingAvailability, priceAlertDrawing, type PriceAlertAnchor } from '../alerts/priceAlertDrawing';

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
  anchor,
}: {
  store: AppStore;
  symbol: string;
  timeframe: Timeframe;
  drawings: Drawing[];
  anchor?: PriceAlertAnchor;
}) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const access = useSyncExternalStore(accessState.subscribe, accessState.getSnapshot);
  const sync = useSyncExternalStore(cloudSyncState.subscribe, cloudSyncState.get);
  const discordUnavailable = discordDrawingAvailability(access, sync, !!anchor);
  const [discordNotice, setDiscordNotice] = useState('');
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

  const monitorPrice = (alert: AlertDefinition) => {
    setError(''); setDiscordNotice('');
    try {
      const currentAccess = accessState.getSnapshot();
      const unavailable = discordDrawingAvailability(currentAccess, cloudSyncState.get(), !!anchor);
      if (unavailable) throw new Error(unavailable);
      if (currentAccess.userId !== access.userId || store.getSnapshot().app.activeSymbol !== symbol || store.symbol(symbol).preferences.timeframe !== timeframe) throw new Error('股票、週期或保存授權已改變，請重新開啟提醒面板。');
      const current = store.symbol(symbol).drawings;
      const next = priceAlertDrawing(symbol, timeframe, alert.level!, anchor!, current, snapshot.app.drawingDefaults.horizontal);
      if (next.created) store.commitDrawings(symbol, [...current, next.drawing], 'create price alert', timeframe);
      setDiscordNotice(next.created ? '已建立水平線；同步完成後由伺服器監控突破／跌破。' : '此價格已有水平線，由既有雲端畫線同步與伺服器監控。');
    } catch (caught) { setError(caught instanceof Error ? caught.message : '無法建立監控水平線，請稍後重試。'); }
  };

  return (
    <section className="research-panel alert-panel" aria-label={`${symbol} 價格提醒`}>
      <div className="section-heading">
        <span>價格提醒</span>
        <span>{symbol} · {timeframe}</span>
      </div>
      <p className="small muted">瀏覽器提醒：只在開著此圖表時檢查</p>
      <p className="small muted">Discord 推播：由伺服器監控</p>
      <HelpDisclosure label="提醒執行範圍">
        瀏覽器提醒維持現有保存方式，在網頁前景以已收盤 K 棒檢查。Discord 按鈕會建立一條可雲端同步的水平線，由既有伺服器監控突破與跌破，不限瀏覽器提醒的單向設定。停用或刪除瀏覽器提醒不會刪除水平線；要停止 Discord 監控，請在「畫線物件」刪除或隱藏該線。
      </HelpDisclosure>
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
      {discordNotice && <p className="small" role="status">{discordNotice}</p>}
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
              {alert.kind === 'level' && <button type="button" disabled={!!discordUnavailable} title={discordUnavailable ?? '建立水平線並沿用現有雲端畫線同步'} onClick={() => monitorPrice(alert)} style={{ minHeight: 44 }}>推播到 Discord</button>}
              <button type="button" disabled={!!alert.invalidReason} aria-label={`${alert.enabled ? '停用' : '啟用'}提醒`} onClick={() => toggleAlert(alert)} style={{ minHeight: 44 }}>
                {alert.enabled ? '停用' : '啟用'}
              </button>
              <button type="button" onClick={() => deleteAlert(alert.id)} style={{ minHeight: 44 }}>刪除</button>
            </div>
            {alert.kind === 'level' && discordUnavailable && <p className="small muted">{discordUnavailable}</p>}
          </article>
        ))}
      </div>
    </section>
  );
}
