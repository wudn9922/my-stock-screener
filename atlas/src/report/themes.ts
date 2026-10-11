import { useCallback, useEffect, useState } from 'react';
import { z } from 'zod';
import { reportUrl } from './loadReport';

/**
 * US theme rotation (`report/themes.json`, written by scripts/build_themes.py).
 *
 * Tolerant like the daily report: unknown fields are ignored, numbers are finite or null, and a
 * malformed theme or constituent is dropped instead of rejecting the file.
 */

export const PERIODS = ['d1', 'w1', 'm1', 'm3', 'm6', 'ytd'] as const;
export type PeriodKey = (typeof PERIODS)[number];
export const PERIOD_LABELS: Record<PeriodKey, string> = {
  d1: '1日',
  w1: '1週',
  m1: '1月',
  m3: '3月',
  m6: '6月',
  ytd: 'YTD',
};
export type Returns = Record<PeriodKey, number | null>;

export const THEME_KEY = /^[a-z0-9-]{1,40}$/;

function toFinite(value: unknown): number | null {
  const number = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return typeof number === 'number' && Number.isFinite(number) ? number : null;
}
const finiteOrNull = z.unknown().optional().transform(toFinite);
const text = z
  .unknown()
  .optional()
  .transform((value) => (typeof value === 'string' ? value.trim() : ''));
const returns = z
  .unknown()
  .optional()
  .transform((value): Returns => {
    const source = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
    const out = {} as Returns;
    for (const key of PERIODS) out[key] = toFinite(source[key]);
    return out;
  });
/** A series that is not all finite numbers is unusable as a whole (it must stay aligned with `dates`). */
const series = z
  .unknown()
  .optional()
  .transform((value): number[] =>
    Array.isArray(value) && value.every((n) => typeof n === 'number' && Number.isFinite(n)) ? (value as number[]) : [],
  );
const tolerantArray = <T extends z.ZodType>(item: T) =>
  z
    .unknown()
    .optional()
    .transform((value): z.output<T>[] => {
      if (!Array.isArray(value)) return [];
      const out: z.output<T>[] = [];
      for (const entry of value) {
        const parsed = item.safeParse(entry);
        if (parsed.success) out.push(parsed.data);
      }
      return out;
    });

const constituentSchema = z.object({
  symbol: z
    .string()
    .trim()
    .min(1)
    .max(16)
    .transform((value) => value.toUpperCase()),
  name: text,
  note: text,
  close: finiteOrNull,
  returns,
  rs3m: finiteOrNull,
  aboveMa50: z.unknown().optional().transform((value) => (typeof value === 'boolean' ? value : null)),
  fromHi52: finiteOrNull,
});
export type ThemeConstituent = z.output<typeof constituentSchema>;

const etfSchema = z.object({ symbol: z.string().trim().min(1).max(16), returns });

const themeSchema = z
  .object({
    key: z.string().regex(THEME_KEY),
    name: text,
    parent: text,
    description: text,
    etf: z.unknown().optional().transform((value) => {
      const parsed = etfSchema.safeParse(value);
      return parsed.success ? parsed.data : null;
    }),
    count: finiteOrNull,
    validCount: finiteOrNull,
    returns,
    median: returns,
    rs: z
      .unknown()
      .optional()
      .transform((value) => {
        const source = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
        return { m1: toFinite(source.m1), m3: toFinite(source.m3) };
      }),
    breadth50: finiteOrNull,
    momentum: finiteOrNull,
    rank: finiteOrNull,
    series,
    constituents: tolerantArray(constituentSchema),
  })
  .transform((theme) => ({
    ...theme,
    name: theme.name || theme.key,
    count: theme.count ?? theme.constituents.length,
    validCount: theme.validCount ?? theme.constituents.length,
  }));
export type Theme = z.output<typeof themeSchema>;

const parentSchema = z.object({
  key: z.string().min(1).max(40),
  name: text,
  order: finiteOrNull,
});
export type ThemeParent = { key: string; name: string; order: number };

const benchmarkSchema = z.object({ symbol: text, returns, series });

export type ThemesData = {
  generatedAt: string | null;
  asOf: string | null;
  dates: string[];
  benchmark: { symbol: string; returns: Returns; series: number[] };
  parents: ThemeParent[];
  themes: Theme[];
};

export type ThemesLoadResult =
  | { status: 'ok'; data: ThemesData; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

/** Sibling of `report/latest.json`: `/my-stock-screener/report/themes.json`. */
export function themesUrl(): string {
  return new URL('themes.json', reportUrl()).href;
}

const MISSING_MESSAGE = '資料會在每個交易日的報告更新後出現，請稍後再試。';

/** Parses an already-fetched body. Never throws. */
export function parseThemes(raw: unknown, url = ''): ThemesLoadResult {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw))
    return { status: 'invalid', message: '題材資料格式無法辨識。', url };
  const source = raw as Record<string, unknown>;
  const themes: Theme[] = [];
  const seen = new Set<string>();
  if (Array.isArray(source.themes))
    for (const entry of source.themes) {
      const parsed = themeSchema.safeParse(entry);
      if (!parsed.success || seen.has(parsed.data.key)) continue;
      seen.add(parsed.data.key);
      themes.push(parsed.data);
    }
  if (!themes.length) return { status: 'invalid', message: MISSING_MESSAGE, url };
  const parents: ThemeParent[] = [];
  if (Array.isArray(source.parents))
    for (const entry of source.parents) {
      const parsed = parentSchema.safeParse(entry);
      if (parsed.success)
        parents.push({ key: parsed.data.key, name: parsed.data.name || parsed.data.key, order: parsed.data.order ?? parents.length + 1 });
    }
  parents.sort((a, b) => a.order - b.order);
  const benchmark = benchmarkSchema.safeParse(source.benchmark);
  const emptyReturns = Object.fromEntries(PERIODS.map((key) => [key, null])) as Returns;
  const dates = Array.isArray(source.dates) ? source.dates.filter((d): d is string => typeof d === 'string') : [];
  return {
    status: 'ok',
    url,
    data: {
      generatedAt: typeof source.generatedAt === 'string' ? source.generatedAt : null,
      asOf: typeof source.asOf === 'string' ? source.asOf : (dates.at(-1) ?? null),
      dates,
      benchmark: benchmark.success
        ? { symbol: benchmark.data.symbol || 'SPY', returns: benchmark.data.returns, series: benchmark.data.series }
        : { symbol: 'SPY', returns: emptyReturns, series: [] },
      parents,
      themes,
    },
  };
}

export async function fetchThemes(
  url = themesUrl(),
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<ThemesLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得題材資料，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: MISSING_MESSAGE, url };
  if (!response.ok) return { status: 'error', message: `題材資料暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as "not generated yet".
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'missing', message: MISSING_MESSAGE, url };
  }
  return parseThemes(data, url);
}

let shared: Promise<ThemesLoadResult> | null = null;

/** One shared load per page session; `refresh` forces a new request. */
export function loadThemes(refresh = false): Promise<ThemesLoadResult> {
  if (!shared || refresh) shared = fetchThemes();
  return shared;
}

export type ThemesState = { status: 'loading' } | ThemesLoadResult;

export function useThemes() {
  const [state, setState] = useState<ThemesState>({ status: 'loading' });
  const run = useCallback((refresh: boolean) => {
    let active = true;
    if (refresh) setState({ status: 'loading' });
    void loadThemes(refresh).then((result) => {
      if (active) setState(result);
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => run(false), [run]);
  const reload = useCallback(() => {
    run(true);
  }, [run]);
  return { state, reload };
}
