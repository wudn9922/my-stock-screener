import type { AlertDefinition } from '../storage/schema';
import type { Drawing } from '../drawing/DrawingModel';
import type { Bar } from '../market-data/MarketDataProvider';
import type { Timeframe } from '../market-data/MarketDataProvider';
import { sma } from '../indicators/MovingAverage';
import { ema } from '../indicators/ExponentialMovingAverage';
export interface AlertEvent { id: string; symbol: string; time: number; price: number; message: string }
export interface AlertEvaluation { definitions: AlertDefinition[]; events: AlertEvent[] }
function referencedDrawing(alert: AlertDefinition, drawings: readonly Drawing[]) {
  if (alert.kind !== 'drawing') return undefined;
  return drawings.find(d => d.id === alert.drawingId && d.symbol === alert.symbol && (d.type === 'horizontal' || d.type === 'ray'));
}
function drawingInAlertTimeframe(drawing: Drawing, timeframe: Timeframe) {
  return drawing.scope.timeframes === 'all' || drawing.scope.timeframes.includes(timeframe);
}
export function referenceLevel(alert: AlertDefinition, drawings: readonly Drawing[]) {
  if (alert.kind === 'drawing') {
    const drawing = referencedDrawing(alert, drawings);
    return drawing && drawingInAlertTimeframe(drawing, alert.timeframe) ? drawing.points[0].price : null;
  }
  return alert.kind === 'level' ? alert.level ?? null : null;
}
/** Called with closed bars only. Initial load establishes the latest baseline without replaying history. */
export function evaluateAlerts(definitions: readonly AlertDefinition[], bars: readonly Bar[], drawings: readonly Drawing[], initialize = false): AlertEvaluation {
  const events: AlertEvent[] = [];
  const latest = bars.at(-1);
  if (!latest) return { definitions: structuredClone([...definitions]), events };
  const averages = new Map<string, Map<number, number>>();
  const result = definitions.map(original => {
    let alert = { ...original };
    if (!alert.enabled || alert.invalidReason) return alert;
    const drawing = referencedDrawing(alert, drawings);
    const staticLevel = referenceLevel(alert, drawings);
    if (alert.kind === 'drawing' && !drawing) return { ...alert, enabled: false, invalidReason: 'Drawing reference unavailable', baseline: null };
    if (alert.kind === 'drawing' && drawing && !drawingInAlertTimeframe(drawing, alert.timeframe)) {
      return { ...alert, enabled: false, invalidReason: 'Referenced drawing is scoped to another timeframe', baseline: null };
    }
    if (alert.kind === 'drawing' && staticLevel === null) return { ...alert, enabled: false, invalidReason: 'Drawing reference unavailable', baseline: null };
    let series: Map<number, number> | undefined;
    if (alert.kind === 'ma') {
      const key = `${alert.maType}:${alert.period}`;
      series = averages.get(key);
      if (!series) {
        series = new Map((alert.maType === 'EMA' ? ema : sma)(bars, alert.period!, 'close').map(p => [p.time, p.value]));
        averages.set(key, series);
      }
    }
    const relation = (bar: Bar) => {
      const level = alert.kind === 'ma' ? series?.get(bar.time) : staticLevel;
      return level === null || level === undefined ? null : bar.close - level;
    };
    const levelChanged = alert.kind === 'drawing' && alert.referencePrice !== staticLevel;
    if (initialize || alert.lastEvaluated === null || levelChanged) return {
      ...alert, baseline: relation(latest), lastEvaluated: latest.time,
      referencePrice: alert.kind === 'drawing' ? staticLevel : alert.referencePrice,
    };
    for (const bar of bars) {
      if (bar.time <= alert.lastEvaluated!) continue;
      const next = relation(bar), previous = alert.baseline;
      const up = previous !== null && next !== null && previous <= 0 && next > 0;
      const down = previous !== null && next !== null && previous >= 0 && next < 0;
      if ((alert.direction === 'cross' && (up || down)) || (alert.direction === 'above' && up) || (alert.direction === 'below' && down)) {
        events.push({ id: alert.id, symbol: alert.symbol, time: bar.time, price: bar.close, message: `${alert.symbol} ${alert.kind} ${up ? 'crossed above' : 'crossed below'} · ${bar.close.toFixed(2)}` });
        alert.lastTriggered = bar.time;
      }
      alert = { ...alert, lastEvaluated: bar.time, baseline: next };
    }
    return alert;
  });
  return { definitions: result, events };
}
/** Deletion invalidates; moving the referenced level reinitializes without a synthetic price crossing. */
export function reconcileDrawingAlerts(alerts: readonly AlertDefinition[], symbol: string, drawings: readonly Drawing[]) {
  return alerts.map(a => {
    if (a.symbol !== symbol || a.kind !== 'drawing') return a;
    const drawing = referencedDrawing(a, drawings);
    if (!drawing) return { ...a, enabled: false, invalidReason: 'Referenced drawing was deleted', baseline: null };
    if (!drawingInAlertTimeframe(drawing, a.timeframe)) {
      return { ...a, enabled: false, invalidReason: 'Referenced drawing is scoped to another timeframe', baseline: null };
    }
    const price = drawing.points[0].price;
    return price !== a.referencePrice ? { ...a, referencePrice: price, baseline: null, lastEvaluated: null } : a;
  });
}
