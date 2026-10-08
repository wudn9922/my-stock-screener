export type WidthMode = 'pixels' | 'atr';
export type PriceToCoordinate = (price: number) => number | null;

export const ATR_STROKE_FRACTION = 0.02;

export function clampCssStrokeWidth(width: number): number {
  return Number.isFinite(width) ? Math.max(0.5, Math.min(4, width)) : 1;
}

/** Map 0.02 ATR price distance through the chart's public price scale API. */
export function atrStrokeWidthCss(
  atr: number | null,
  referencePrice: number,
  priceToCoordinate: PriceToCoordinate,
): number | null {
  if (atr === null || !Number.isFinite(atr) || atr <= 0 || !Number.isFinite(referencePrice)) {
    return null;
  }
  const offsetPrice = referencePrice + atr * ATR_STROKE_FRACTION;
  if (!Number.isFinite(offsetPrice)) return null;
  const referenceY = priceToCoordinate(referencePrice);
  const atrY = priceToCoordinate(offsetPrice);
  if (
    referenceY === null ||
    atrY === null ||
    !Number.isFinite(referenceY) ||
    !Number.isFinite(atrY)
  ) {
    return null;
  }
  return clampCssStrokeWidth(Math.abs(atrY - referenceY));
}

export function resolveStrokeWidth(
  mode: WidthMode | undefined,
  fallbackWidth: number,
  atr: number | null,
  referencePrice: number,
  priceToCoordinate: PriceToCoordinate,
): number {
  const fallback = clampCssStrokeWidth(fallbackWidth);
  if (mode !== 'atr') return fallback;
  return atrStrokeWidthCss(atr, referencePrice, priceToCoordinate) ?? fallback;
}

export function nativeLineWidth(cssWidth: number): 1 | 2 | 3 | 4 {
  return Math.max(1, Math.min(4, Math.round(clampCssStrokeWidth(cssWidth)))) as 1 | 2 | 3 | 4;
}

export function bitmapLineWidth(cssWidth: number, horizontalPixelRatio: number): number {
  const ratio = Number.isFinite(horizontalPixelRatio) && horizontalPixelRatio > 0 ? horizontalPixelRatio : 1;
  return Math.max(1, Math.round(clampCssStrokeWidth(cssWidth) * ratio));
}
