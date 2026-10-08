import { storageName } from './HostingMode';

/**
 * Per-device display conveniences for the site shell (not part of the exported workspace):
 * color convention and recent searches. Storage failures (private mode, blocked storage) fall
 * back to defaults silently.
 */
export type ColorConvention = 'tw' | 'us';
export interface RecentSearch {
  symbol: string;
  name: string;
}
export interface SitePreferences {
  /** `tw`: 紅漲綠跌 (default); `us`: 綠漲紅跌. */
  colors: ColorConvention;
  recent: RecentSearch[];
}

const KEY = storageName('atlas-site-preferences');
const MAX_RECENT = 8;
const defaults: SitePreferences = { colors: 'tw', recent: [] };

function read(): SitePreferences {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return defaults;
    const data = JSON.parse(raw) as Partial<SitePreferences>;
    return {
      colors: data.colors === 'us' ? 'us' : 'tw',
      recent: Array.isArray(data.recent)
        ? data.recent
            .filter((item): item is RecentSearch => !!item && typeof item.symbol === 'string' && typeof item.name === 'string')
            .slice(0, MAX_RECENT)
        : [],
    };
  } catch {
    return defaults;
  }
}

let current = read();
const listeners = new Set<() => void>();

function write(next: SitePreferences) {
  current = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* Display preferences are optional; keep the in-memory value. */
  }
  applyColorConvention(next.colors);
  listeners.forEach((listener) => listener());
}

export const sitePreferences = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  get: () => current,
  setColors(colors: ColorConvention) {
    write({ ...current, colors });
  },
  addRecent(entry: RecentSearch) {
    write({
      ...current,
      recent: [entry, ...current.recent.filter((item) => item.symbol !== entry.symbol)].slice(0, MAX_RECENT),
    });
  },
  clearRecent() {
    write({ ...current, recent: [] });
  },
};

export interface UpDownColors {
  up: string;
  down: string;
}
/** Candle/volume colors; also mirrored as CSS variables `--up` / `--down`. */
export function upDownColors(colors: ColorConvention = current.colors): UpDownColors {
  return colors === 'tw' ? { up: '#f23645', down: '#22ab94' } : { up: '#22ab94', down: '#f23645' };
}

export function applyColorConvention(colors: ColorConvention = current.colors) {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.colors = colors;
}
