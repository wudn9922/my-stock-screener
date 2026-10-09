/**
 * Price/time axis text. Readability first: the platform UI font (heavier strokes than the former
 * 70%-width Atlas Narrow Axis face, which was too thin on phones), a larger size on narrow screens
 * and a brighter colour. Width is still kept small with minimumWidth 0 and trimmed trailing zeros.
 */
export const AXIS_FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "Noto Sans TC", Arial, sans-serif';
export const AXIS_TEXT_COLOR = '#c3cad5';
export const AXIS_FONT_SIZE = 12;
export const AXIS_FONT_SIZE_NARROW = 13;
const NARROW_QUERY = '(max-width: 640px)';

/** Axis font size for the current viewport (phones get the larger size). */
export function axisFontSize(matches: (query: string) => boolean = defaultMatches): number {
  return matches(NARROW_QUERY) ? AXIS_FONT_SIZE_NARROW : AXIS_FONT_SIZE;
}

function defaultMatches(query: string): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
}

export function compactAxisPrice(price: number): string {
  return price.toFixed(2).replace(/\.00$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
}
