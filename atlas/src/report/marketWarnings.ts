import { marketWarningsSchema, type MarketWarnings } from './schema';
import { reportUrl } from './loadReport';
export type WarningsResult = { data: MarketWarnings; error?: never } | { data?: never; error: string };
export function parseMarketWarnings(raw: unknown): WarningsResult {
  const parsed = marketWarningsSchema.safeParse(raw);
  return parsed.success ? { data: parsed.data } : { error: '警示資料格式無效，無法顯示數值。' };
}
export async function loadMarketWarnings(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<WarningsResult> {
  try {
    const response = await fetcher(new URL('market-warnings.json', reportUrl()).href, { cache: 'no-cache', signal });
    if (!response.ok) return { error: `警示資料暫時無法取得（HTTP ${response.status}），請稍後重試。` };
    return parseMarketWarnings(await response.json());
  } catch {
    return { error: '警示資料無法讀取，請檢查連線後重試。' };
  }
}
/** Keep every date as whitespace; short-window lines draw only the last session of each week. */
export function weeklySample<T extends { date: string }>(rows: T[], weekly: boolean): Set<string> {
  if (!weekly) return new Set(rows.map(row => row.date));
  const weeks = new Map<string, string>();
  for (const row of rows) {
    const date = new Date(`${row.date}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
    weeks.set(date.toISOString().slice(0, 10), row.date);
  }
  return new Set(weeks.values());
}
