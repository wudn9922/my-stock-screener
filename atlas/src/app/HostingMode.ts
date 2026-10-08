export const STATIC_HOSTING = import.meta.env?.VITE_STATIC_HOSTING === '1';

/** Base path prefix of the my-stock-screener GitHub Pages deployment (see SCREENER_INTEGRATION.md). */
export const SCREENER_BASE_PREFIX = '/my-stock-screener/';

export function isScreenerBase(base: string | undefined): boolean {
  return !!base && base.startsWith(SCREENER_BASE_PREFIX);
}

/** Vite's public base (`VITE_PUBLIC_BASE`); `/` in tests and plain Node script bundles. */
export const PUBLIC_BASE: string = import.meta.env?.BASE_URL ?? '/';

/**
 * True for builds served under wudn9922.github.io/my-stock-screener/. That origin also hosts the
 * original Atlas deployment (/lightweight-drawing-lab/), so browser storage must stay separate.
 */
export const SCREENER_HOSTING = isScreenerBase(PUBLIC_BASE);

/**
 * IndexedDB names are per origin, not per path. Screener builds get their own databases so the two
 * deployments never share, migrate or evict each other's drawings, settings and caches.
 * Every other build keeps the original names unchanged.
 */
export function storageName(name: string, base: string | undefined = PUBLIC_BASE): string {
  return isScreenerBase(base) ? `${name}-screener` : name;
}

/**
 * Static financial snapshot layout:
 * - `pack`: upstream `financial-data/<SYMBOL>.json` packs (scripts/refresh-fundamentals.mjs).
 * - `per-period`: `fundamentals/<SYMBOL>-<annual|quarterly>.json` (scripts/build-fundamentals.mjs).
 * `VITE_STATIC_FUNDAMENTALS` overrides; screener builds default to `per-period`.
 */
export const STATIC_FUNDAMENTALS_LAYOUT: 'pack' | 'per-period' =
  import.meta.env?.VITE_STATIC_FUNDAMENTALS === 'pack'
    ? 'pack'
    : import.meta.env?.VITE_STATIC_FUNDAMENTALS === 'per-period' || SCREENER_HOSTING
      ? 'per-period'
      : 'pack';
