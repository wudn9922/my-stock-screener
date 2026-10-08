import { useState, useSyncExternalStore, type FormEvent } from 'react';
import type { Timeframe } from '../market-data/MarketDataProvider';
import type { Drawing } from '../drawing/DrawingModel';
import type { AlertDefinition } from '../storage/schema';
import { AppStore } from '../app/AppStore';

type AlertKind = 'level' | 'ma' | 'drawing';
type AlertDirection = 'above' | 'below' | 'cross';

function dateText(value: number | null) {
  return value === null ? 'Never' : new Date(value * 1000).toLocaleString();
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
    <section className="research-panel alert-panel" aria-label={`${symbol} alerts`}>
      <div className="section-heading">
        <span>PRICE ALERTS</span>
        <span>{symbol} · {timeframe}</span>
      </div>
      <p className="research-disclaimer">
        Alerts are stored locally and evaluate the selected symbol/timeframe once per minute while this app is active and visible, using newly closed bars. They do not run in the background or send push notifications.
      </p>
      <form className="research-form" onSubmit={create}>
        <label>
          Alert condition
          <select aria-label="Alert condition" value={kind} onChange={(event) => setKind(event.target.value as AlertKind)}>
            <option value="level">Price level</option>
            <option value="ma">Moving average</option>
            <option value="drawing">Horizontal drawing</option>
          </select>
        </label>
        {kind === 'level' && (
          <>
            <label>
              Price
              <input aria-label="Alert price level" type="number" step="any" value={level} onChange={(event) => setLevel(event.target.value)} required />
            </label>
            <label>
              Trigger direction
              <select aria-label="Level trigger direction" value={direction} onChange={(event) => setDirection(event.target.value as AlertDirection)}>
                <option value="above">Cross above</option>
                <option value="below">Cross below</option>
                <option value="cross">Cross either direction</option>
              </select>
            </label>
          </>
        )}
        {kind === 'ma' && (
          <div className="research-field-row">
            <label>
              Average type
              <select aria-label="Alert average type" value={maType} onChange={(event) => setMaType(event.target.value as 'SMA' | 'EMA')}>
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
              </select>
            </label>
            <label>
              Period
              <input aria-label="Alert average period" type="number" min="1" max="5000" step="1" value={period} onChange={(event) => setPeriod(event.target.value)} required />
            </label>
          </div>
        )}
        {kind === 'drawing' && (
          <label>
            Horizontal line or ray
            <select aria-label="Alert drawing reference" value={drawingId} onChange={(event) => setDrawingId(event.target.value)} required disabled={!alertDrawings.length}>
              <option value="">{alertDrawings.length ? 'Choose a drawing' : 'No eligible drawing available'}</option>
              {alertDrawings.map((drawing, index) => (
                <option key={drawing.id} value={drawing.id}>
                  {drawing.type === 'ray' ? 'Horizontal ray' : 'Horizontal line'} {index + 1} · {drawing.points[0]?.price.toFixed(2)}
                </option>
              ))}
            </select>
            {!alertDrawings.length && <small className="small muted">Create a horizontal line or ray on this symbol first. No placeholder reference is created.</small>}
          </label>
        )}
        <button className="primary-button" type="submit" disabled={kind === 'drawing' && !alertDrawings.length} style={{ minHeight: 44 }}>Create alert</button>
      </form>
      {error && <p className="research-error" role="alert">{error}</p>}
      <div className="alert-list" aria-live="polite">
        <h3>Alerts for {symbol} · {timeframe}</h3>
        {!alerts.length ? <p className="small muted">No alerts for this symbol and timeframe.</p> : alerts.map((alert) => (
          <article className="alert-card" key={alert.id}>
            <div className="alert-card-heading">
              <b>{alert.kind === 'level' ? `Price ${alert.direction} ${alert.level}` : alert.kind === 'ma' ? `${alert.maType} ${alert.period} cross` : 'Drawing cross'}</b>
              <span>{alert.enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
            {alert.kind === 'drawing' && <p className="small muted">Reference: {alertDrawings.find((drawing) => drawing.id === alert.drawingId)?.type ?? 'unavailable'} · {alert.referencePrice === null ? '—' : alert.referencePrice.toFixed(2)}</p>}
            {alert.invalidReason && <p className="research-error" role="status">Invalid: {alert.invalidReason}</p>}
            <dl className="alert-state">
              <div><dt>Last triggered</dt><dd>{dateText(alert.lastTriggered)}</dd></div>
              <div><dt>Last evaluated</dt><dd>{dateText(alert.lastEvaluated)}</dd></div>
            </dl>
            <div className="alert-actions">
              <button type="button" disabled={!!alert.invalidReason} aria-label={`${alert.enabled ? 'Disable' : 'Enable'} alert`} onClick={() => toggleAlert(alert)} style={{ minHeight: 44 }}>
                {alert.enabled ? 'Disable' : 'Enable'}
              </button>
              <button type="button" onClick={() => deleteAlert(alert.id)} style={{ minHeight: 44 }}>Delete</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
