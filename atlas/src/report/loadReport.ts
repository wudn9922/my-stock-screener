import { isEmptyReport, reportSchema, type Report } from './schema';

export type ReportLoadResult =
  | { status: 'ok'; report: Report; url: string }
  | { status: 'missing' | 'invalid' | 'error'; message: string; url: string };

/**
 * `report/latest.json` lives next to the Atlas folder on the screener site:
 * `/my-stock-screener/atlas/` → `/my-stock-screener/report/latest.json`.
 * `VITE_REPORT_URL` overrides it (absolute or relative to the page).
 */
export function reportUrl(
  base: string = import.meta.env?.BASE_URL ?? '/',
  override: string | undefined = import.meta.env?.VITE_REPORT_URL,
  origin: string = typeof location === 'undefined' ? 'http://localhost' : location.origin,
): string {
  if (override) return new URL(override, `${origin}${base}`).href;
  const normalizedBase = base.endsWith('/') ? base : `${base}/`;
  return new URL('../report/latest.json', `${origin}${normalizedBase}`).href;
}

/** Parses an already-fetched report body. Never throws. */
export function parseReport(data: unknown, url = ''): ReportLoadResult {
  if (!data || typeof data !== 'object' || Array.isArray(data))
    return { status: 'invalid', message: '報告格式無法辨識。', url };
  const parsed = reportSchema.safeParse(data);
  if (!parsed.success) return { status: 'invalid', message: '報告格式無法辨識。', url };
  if (isEmptyReport(parsed.data)) return { status: 'missing', message: '今日報告尚未產生。', url };
  return { status: 'ok', report: parsed.data, url };
}

/** Fetches and validates the report. Network, HTTP and JSON failures resolve to a friendly state. */
export async function fetchReport(
  url = reportUrl(),
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<ReportLoadResult> {
  let response: Response;
  try {
    response = await fetcher(url, { cache: 'no-cache', signal });
  } catch {
    return { status: 'error', message: '無法連線取得每日報告，請稍後再試。', url };
  }
  if (response.status === 404) return { status: 'missing', message: '今日報告尚未產生。', url };
  if (!response.ok)
    return { status: 'error', message: `每日報告暫時無法取得（HTTP ${response.status}）。`, url };
  let data: unknown;
  try {
    // An SPA fallback can answer with index.html; treat non-JSON as "no report".
    data = JSON.parse(await response.text());
  } catch {
    return { status: 'invalid', message: '報告格式無法辨識。', url };
  }
  return parseReport(data, url);
}

let shared: Promise<ReportLoadResult> | null = null;

/** One shared load per page session; `refresh` forces a new request. */
export function loadReport(refresh = false): Promise<ReportLoadResult> {
  if (!shared || refresh) shared = fetchReport();
  return shared;
}
