import type { Drawing, DrawingProjection, Point } from '../drawing/DrawingModel';
import { getMarketProfile } from '../market-data/MarketProfile';

export type MeasurementKind = 'price-range' | 'date-range' | 'price-date-range';

export interface MeasurementValues {
  priceDelta: number;
  pricePercent: number | null;
  barDelta: number;
  elapsedSeconds: number;
  humanDuration: string;
}

export function measurementCorners(
  drawing: Drawing,
  projection: DrawingProjection,
): [Point, Point] | null {
  if (drawing.points.length < 2) return null;
  const a = projection.toPoint(drawing.points[0]);
  const b = projection.toPoint(drawing.points[1]);
  if (!a || !b || ![a.x, a.y, b.x, b.y].every(Number.isFinite)) return null;
  return [a, b];
}

function signedNumber(value: number, decimals = 0): string {
  const rounded = Number(value.toFixed(decimals));
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
  return `${sign}${Math.abs(rounded).toFixed(decimals)}`;
}

function humanDuration(seconds: number): string {
  const total = Math.floor(Math.abs(seconds));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const secs = total % 60;
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes && parts.length < 2) parts.push(`${minutes}m`);
  if (!parts.length) parts.push(`${secs}s`);
  return parts.slice(0, 2).join(' ');
}

function signedDuration(seconds: number): string {
  const sign = seconds > 0 ? '+' : seconds < 0 ? '−' : '';
  return `${sign}${humanDuration(seconds)}`;
}

export function measurementValues(
  drawing: Drawing,
  projection: DrawingProjection,
): MeasurementValues | null {
  if (drawing.points.length < 2) return null;
  const [p1, p2] = drawing.points;
  const priceDelta = p2.price - p1.price;
  const firstLogical = projection.logicalAt ? projection.logicalAt(p1) : p1.logical;
  const secondLogical = projection.logicalAt ? projection.logicalAt(p2) : p2.logical;
  const barDelta = secondLogical - firstLogical;
  const elapsedSeconds = p2.time - p1.time;
  if (![priceDelta, barDelta, elapsedSeconds].every(Number.isFinite)) return null;
  return {
    priceDelta,
    pricePercent: p1.price === 0 ? null : (priceDelta / p1.price) * 100,
    barDelta,
    elapsedSeconds,
    humanDuration: signedDuration(elapsedSeconds),
  };
}

/** Returns a concise, signed label; bar distance and wall-clock time stay explicit. */
export function measurementLabel(
  drawing: Drawing,
  projection: DrawingProjection,
): string | null {
  if (
    drawing.type !== 'price-range' &&
    drawing.type !== 'date-range' &&
    drawing.type !== 'price-date-range'
  )
    return null;
  const values = measurementValues(drawing, projection);
  if (!values) return null;

  const priceDeltaSign = values.priceDelta > 0 ? '+' : values.priceDelta < 0 ? '−' : '';
  const price = `${priceDeltaSign}${getMarketProfile(drawing.symbol).currency === 'TWD' ? 'TWD ' : '$'}${Math.abs(values.priceDelta).toFixed(2)} ${
    values.pricePercent === null ? '(N/A)' : `(${signedNumber(values.pricePercent, 2)}%)`
  }`;
  const barsDecimals = Number.isInteger(values.barDelta) ? 0 : 1;
  const bars = `${signedNumber(values.barDelta, barsDecimals)} bars`;
  const wall = `wall ${signedNumber(values.elapsedSeconds)}s (${values.humanDuration})`;
  if (drawing.type === 'price-range') return price;
  if (drawing.type === 'date-range') return `${bars} · ${wall}`;
  return `${price} · ${bars} · ${wall}`;
}

/** A string shortened only when needed by a narrow chart label. */
export function compactMeasurementLabel(label: string): string {
  return label.replaceAll(' · ', ' | ').replace(/\s+/g, ' ').trim();
}
