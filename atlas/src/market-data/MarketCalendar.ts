import { reportUrl } from '../report/loadReport';
import { sessionDate, type MarketProfile } from './MarketProfile';

export interface CalendarSession { open: number; close: number; earlyClose: boolean }
export interface MarketCalendar {
  version: 1; generatedAt: string; validFrom: string; validTo: string;
  markets: Record<'TW' | 'US', { timezone: string; sessions: Record<string, CalendarSession> }>;
}
const zones = { TW: 'Asia/Taipei', US: 'America/New_York' } as const;
function validDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}

/** Invalid/incomplete calendars fail as a whole; they must not turn weekdays into holidays. */
export function parseMarketCalendar(value: unknown): MarketCalendar | null {
  const doc = value as Partial<MarketCalendar> | null;
  if (!doc || doc.version !== 1 || typeof doc.generatedAt !== 'string' || !Number.isFinite(Date.parse(doc.generatedAt))
    || !validDate(doc.validFrom) || !validDate(doc.validTo) || doc.validFrom > doc.validTo || !doc.markets) return null;
  for (const market of ['TW', 'US'] as const) {
    const part = doc.markets[market];
    if (!part || part.timezone !== zones[market] || !part.sessions || typeof part.sessions !== 'object' || Array.isArray(part.sessions)) return null;
    const entries = Object.entries(part.sessions);
    if (!entries.length) return null;
    for (const [date, row] of entries) {
      if (!validDate(date) || date < doc.validFrom || date > doc.validTo || !row
        || !Number.isSafeInteger(row.open) || !Number.isSafeInteger(row.close) || row.open >= row.close
        || row.close - row.open > 86400 || typeof row.earlyClose !== 'boolean') return null;
      // Reject timestamps assigned to another session date/timezone.
      const profile = { timezone: zones[market] } as MarketProfile;
      try { if (sessionDate(row.open, profile) !== date || sessionDate(row.close, profile) !== date) return null; }
      catch { return null; }
    }
  }
  return doc as MarketCalendar;
}

export interface MarketCalendarOptions { fetcher?: typeof fetch; url?: string; timeoutMs?: number }
/** One read per UTC day, shared by the existing feed; no additional interval or quote requests. */
export class MarketCalendarStore {
  private document: MarketCalendar | null = null;
  private day = '';
  private loading: Promise<void> | null = null;
  constructor(private readonly options: MarketCalendarOptions = {}) {}
  ensure(nowMs: number): Promise<void> {
    const day = new Date(nowMs).toISOString().slice(0, 10);
    if (day === this.day) return this.loading ?? Promise.resolve();
    this.day = day;
    const requestDay = day;
    this.loading = (async () => {
      try {
        const response = await (this.options.fetcher ?? fetch)(this.options.url ?? new URL('market-calendar.json', reportUrl()).href,
          { cache: 'no-cache', signal: AbortSignal.timeout(this.options.timeoutMs ?? 2500) });
        const parsed = response.ok ? parseMarketCalendar(await response.json()) : null;
        if (this.day === requestDay) this.document = parsed;
      } catch { if (this.day === requestDay) this.document = null; }
    })().finally(() => { if (this.day === requestDay) this.loading = null; });
    return this.loading;
  }
  /** undefined: unavailable/outside coverage (use old rules); null: known closed day. */
  session(profile: MarketProfile, nowMs: number): CalendarSession | null | undefined {
    const doc = this.document;
    if (!doc) return undefined;
    const date = sessionDate(nowMs / 1000, profile);
    if (date < doc.validFrom || date > doc.validTo) return undefined;
    return doc.markets[profile.market].sessions[date] ?? null;
  }
}

export const marketCalendar = new MarketCalendarStore();

/** Undefined preserves the weekday/hour fallback; early closes have no post-close grace. */
export function calendarSessionOpen(store: MarketCalendarStore, profile: MarketProfile, nowMs: number, graceMinutes = 15): boolean | undefined {
  const session = store.session(profile, nowMs);
  if (session === undefined) return undefined;
  if (session === null) return false;
  return nowMs >= session.open * 1000 && nowMs < session.close * 1000 + (session.earlyClose ? 0 : graceMinutes * 60_000);
}
