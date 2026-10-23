import { storageName } from './HostingMode';
import { accessState } from '../auth/access';
import { getStorageContextKey, userStorage } from '../auth/storage';

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
    const raw = userStorage.getItem(KEY);
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
let context = getStorageContextKey();
const listeners = new Set<() => void>();

function preferences() {
  const nextContext = getStorageContextKey();
  if (context !== nextContext) {
    context = nextContext;
    current = read();
  }
  return current;
}

accessState.subscribe(() => {
  preferences();
  applyColorConvention(current.colors);
  listeners.forEach((listener) => listener());
});

function write(next: SitePreferences) {
  preferences();
  current = next;
  try {
    userStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* Display preferences are optional; keep the in-memory value. */
  }
  applyColorConvention(next.colors);
  listeners.forEach((listener) => listener());
}

export const sitePreferences = {
  /** Re-read after an explicit legacy copy; ordinary startup never reads old keys. */
  refresh() {
    preferences();
    current = read();
    applyColorConvention(current.colors);
    listeners.forEach((listener) => listener());
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  get: preferences,
  setColors(colors: ColorConvention) {
    write({ ...preferences(), colors });
  },
  addRecent(entry: RecentSearch) {
    write({
      ...preferences(),
      recent: [entry, ...preferences().recent.filter((item) => item.symbol !== entry.symbol)].slice(0, MAX_RECENT),
    });
  },
  clearRecent() {
    write({ ...preferences(), recent: [] });
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
