/**
 * Pure helpers for scripts/build-valuation.ts: EPS TTM / annual EPS for P/E.
 *
 * US — SEC XBRL frames (one request per period covers every filer):
 *   https://data.sec.gov/api/xbrl/frames/us-gaap/EarningsPerShareDiluted/USD-per-shares/CY2025Q3.json
 *   Frames are calendar-aligned: SEC assigns each filer's fiscal quarter/year to the calendar period it
 *   best fits (CY2025Q3 ≈ a 3-month period centred in Jul–Sep 2025). Q4 is usually only in the 10-K, so a
 *   missing quarter is derived as annual − the other three quarters inside that annual period.
 *   EPS TTM = sum of the latest four contiguous quarters; EPS annual = latest full fiscal year.
 * Taiwan — official daily P/E (TWSE BWIBBU_ALL, TPEx peratio analysis). The exchanges compute P/E on the
 *   latest four quarters' EPS, so it is treated as TTM and EPS TTM = close / P/E.
 */

export interface ValuationRecord {
  epsTtm: number | null;
  epsAnnual: number | null;
  fiscalYear: number | null;
  /** Taiwan only: the exchange's own P/E (TTM basis) on `asOf`. */
  exchangePeTtm?: number | null;
  asOf: string | null;
  source: 'SEC frames' | 'TWSE' | 'TPEx';
  /** Present only when basic EPS had to be used because diluted EPS was not reported. */
  basis?: 'basic';
}

export interface ValuationDocument {
  version: 1;
  market: 'US' | 'TW';
  generatedAt: string;
  source: string;
  notes: string;
  items: Record<string, ValuationRecord>;
}

const DAY_MS = 86_400_000;

export function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function parseIsoDate(value: string | undefined): number | undefined {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const ms = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(ms) ? ms : undefined;
}

function isoDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

// ----------------------------------------------------------------------------- calendar slots ---

/** Quarter slot index: year * 4 + (quarter - 1). */
export function slotOf(year: number, quarter: number): number {
  return year * 4 + quarter - 1;
}

export function slotFromDate(ms: number): number {
  const date = new Date(ms);
  return slotOf(date.getUTCFullYear(), Math.floor(date.getUTCMonth() / 3) + 1);
}

export function frameName(slot: number): string {
  return `CY${Math.floor(slot / 4)}Q${(slot % 4) + 1}`;
}

/** Parses `CY2025Q3` → slot; returns undefined for anything else. */
export function slotFromFrameName(name: string): number | undefined {
  const match = /^CY(\d{4})Q([1-4])$/.exec(name);
  return match ? slotOf(Number(match[1]), Number(match[2])) : undefined;
}

/** Quarterly frames to request: the current calendar quarter and the `count` before it. */
export function quarterSlotsToFetch(todayMs: number, count = 10): number[] {
  const current = slotFromDate(todayMs);
  return Array.from({ length: count + 1 }, (_, index) => current - count + index);
}

/** Annual frames (CYyyyy) to request: this year and the `count` before it. */
export function annualYearsToFetch(todayMs: number, count = 3): number[] {
  const year = new Date(todayMs).getUTCFullYear();
  return Array.from({ length: count + 1 }, (_, index) => year - count + index);
}

// --------------------------------------------------------------------------------- SEC frames ---

export interface FramePoint {
  cik: number;
  start?: string;
  end: string;
  val: number;
}

/** Validates a frames payload ({ccp, data:[{cik,start,end,val,...}]}); malformed points are dropped. */
export function parseFramePoints(raw: unknown): FramePoint[] {
  const data = (raw as { data?: unknown })?.data;
  if (!Array.isArray(data)) return [];
  const points: FramePoint[] = [];
  for (const item of data) {
    const value = item as { cik?: unknown; start?: unknown; end?: unknown; val?: unknown };
    if (typeof value.cik !== 'number' || !Number.isInteger(value.cik) || value.cik <= 0) continue;
    if (typeof value.val !== 'number' || !Number.isFinite(value.val) || Math.abs(value.val) > 1e6) continue;
    if (typeof value.end !== 'string' || parseIsoDate(value.end) === undefined) continue;
    const start = typeof value.start === 'string' && parseIsoDate(value.start) !== undefined ? value.start : undefined;
    points.push({ cik: value.cik, end: value.end, val: value.val, ...(start ? { start } : {}) });
  }
  return points;
}

export interface PeriodFact {
  val: number;
  start?: string;
  end: string;
  derived?: boolean;
}

export interface CompanyEpsFacts {
  quarters: Map<number, PeriodFact>;
  annuals: PeriodFact[];
}

export function emptyFacts(): CompanyEpsFacts {
  return { quarters: new Map(), annuals: [] };
}

/** Adds one frame's points to a per-CIK fact map (quarterly when `slot` is given, else annual). */
export function addFrame(target: Map<number, CompanyEpsFacts>, points: readonly FramePoint[], slot?: number): void {
  for (const point of points) {
    let facts = target.get(point.cik);
    if (!facts) {
      facts = emptyFacts();
      target.set(point.cik, facts);
    }
    const fact: PeriodFact = { val: point.val, end: point.end, ...(point.start ? { start: point.start } : {}) };
    if (slot === undefined) facts.annuals.push(fact);
    else facts.quarters.set(slot, fact);
  }
}

function durationDays(fact: PeriodFact): number | undefined {
  const start = parseIsoDate(fact.start),
    end = parseIsoDate(fact.end);
  return start === undefined || end === undefined ? undefined : (end - start) / DAY_MS;
}

/**
 * For each annual fact, the four calendar slots it covers end at the slot holding (end − 45 days).
 * When exactly one of those four quarters is missing and the other three lie inside the annual period,
 * the missing quarter (usually fiscal Q4, reported only in the 10-K) = annual − sum(other three).
 */
export function deriveMissingQuarters(facts: CompanyEpsFacts): CompanyEpsFacts {
  const quarters = new Map(facts.quarters);
  for (const annual of facts.annuals) {
    const end = parseIsoDate(annual.end);
    if (end === undefined) continue;
    const days = durationDays(annual);
    if (days !== undefined && (days < 340 || days > 380)) continue;
    const start = parseIsoDate(annual.start) ?? end - 364 * DAY_MS;
    const last = slotFromDate(end - 45 * DAY_MS);
    const slots = [last - 3, last - 2, last - 1, last];
    const missing = slots.filter((slot) => !quarters.has(slot));
    if (missing.length !== 1) continue;
    const present = slots.filter((slot) => quarters.has(slot)).map((slot) => quarters.get(slot)!);
    const inside = present.every((quarter) => {
      const quarterEnd = parseIsoDate(quarter.end)!;
      const quarterStart = parseIsoDate(quarter.start);
      return quarterEnd <= end + 7 * DAY_MS && (quarterStart === undefined || quarterStart >= start - 7 * DAY_MS) && !quarter.derived;
    });
    if (!inside) continue;
    const slot = missing[0]!;
    const value = annual.val - present.reduce((sum, quarter) => sum + quarter.val, 0);
    const slotEnd = slot === last ? annual.end : isoDate(Date.UTC(Math.floor(slot / 4), (slot % 4) * 3 + 3, 0));
    quarters.set(slot, { val: round(value), end: slotEnd, derived: true });
  }
  return { quarters, annuals: facts.annuals };
}

export interface TtmResult {
  eps: number;
  end: string;
  latestSlot: number;
  derivedQuarters: number;
}

/** Sum of the latest four contiguous quarters; null when the latest quarter is stale or any is missing. */
export function computeTtm(facts: CompanyEpsFacts, minLatestSlot: number): TtmResult | null {
  if (!facts.quarters.size) return null;
  const latest = Math.max(...facts.quarters.keys());
  if (latest < minLatestSlot) return null;
  const window = [latest - 3, latest - 2, latest - 1, latest].map((slot) => facts.quarters.get(slot));
  if (window.some((quarter) => quarter === undefined)) return null;
  return {
    eps: round(window.reduce((sum, quarter) => sum + quarter!.val, 0)),
    end: window[3]!.end,
    latestSlot: latest,
    derivedQuarters: window.filter((quarter) => quarter!.derived).length,
  };
}

/** Fiscal year label: the year the period ends in, except 52/53-week years ending in the first days of January. */
export function fiscalYearOf(end: string): number {
  const date = new Date(parseIsoDate(end)!);
  return date.getUTCMonth() === 0 && date.getUTCDate() <= 7 ? date.getUTCFullYear() - 1 : date.getUTCFullYear();
}

export function latestAnnual(facts: CompanyEpsFacts, notBeforeMs: number): { eps: number; fiscalYear: number; end: string } | null {
  const candidates = facts.annuals
    .filter((fact) => {
      const end = parseIsoDate(fact.end);
      const days = durationDays(fact);
      return end !== undefined && end >= notBeforeMs && (days === undefined || (days >= 340 && days <= 380));
    })
    .sort((a, b) => a.end.localeCompare(b.end));
  const latest = candidates.at(-1);
  return latest ? { eps: round(latest.val), fiscalYear: fiscalYearOf(latest.end), end: latest.end } : null;
}

/** Diluted EPS preferred; basic only when diluted cannot produce the value (or is older). */
export function computeUsValuation(
  diluted: CompanyEpsFacts | undefined,
  basic: CompanyEpsFacts | undefined,
  todayMs: number,
): ValuationRecord | null {
  const minLatestSlot = slotFromDate(todayMs) - 4;
  const notBefore = todayMs - 2 * 366 * DAY_MS;
  const d = diluted ? deriveMissingQuarters(diluted) : undefined;
  const b = basic ? deriveMissingQuarters(basic) : undefined;
  const ttmD = d ? computeTtm(d, minLatestSlot) : null;
  const ttmB = b ? computeTtm(b, minLatestSlot) : null;
  const useBasicTtm = !!ttmB && (!ttmD || ttmB.latestSlot > ttmD.latestSlot);
  const ttm = useBasicTtm ? ttmB : ttmD;
  const annualD = d ? latestAnnual(d, notBefore) : null;
  const annualB = b ? latestAnnual(b, notBefore) : null;
  const useBasicAnnual = !!annualB && (!annualD || annualB.end > annualD.end);
  const annual = useBasicAnnual ? annualB : annualD;
  if (!ttm && !annual) return null;
  return {
    epsTtm: ttm?.eps ?? null,
    epsAnnual: annual?.eps ?? null,
    fiscalYear: annual?.fiscalYear ?? null,
    asOf: ttm?.end ?? annual?.end ?? null,
    source: 'SEC frames',
    ...((ttm && useBasicTtm) || (annual && useBasicAnnual) ? { basis: 'basic' as const } : {}),
  };
}

/** company_tickers.json → CIK → Yahoo tickers (BRK.B → BRK-B); invalid tickers are dropped. */
export function parseCompanyTickers(raw: unknown): Map<number, string[]> {
  const map = new Map<number, string[]>();
  if (typeof raw !== 'object' || raw === null) return map;
  for (const value of Object.values(raw as Record<string, unknown>)) {
    const entry = value as { cik_str?: unknown; ticker?: unknown };
    const cik = typeof entry.cik_str === 'number' ? entry.cik_str : Number(entry.cik_str);
    if (!Number.isInteger(cik) || cik <= 0 || typeof entry.ticker !== 'string') continue;
    const ticker = entry.ticker.trim().toUpperCase().replace(/[./]/g, '-');
    if (!/^[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,2})?$/.test(ticker)) continue;
    const list = map.get(cik) ?? [];
    if (!list.includes(ticker)) list.push(ticker);
    map.set(cik, list);
  }
  return map;
}

// -------------------------------------------------------------------------------------- Taiwan ---

function field(record: Record<string, unknown>, keys: readonly string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return '';
}

/** Parses "12.34", "1,234.5"; "-", "N/A", "", "0.00" and non-positive values → null. */
export function parsePositiveNumber(value: string): number | null {
  const cleaned = value.replace(/,/g, '').trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(cleaned)) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) && number > 0 ? number : null;
}

export function parseSignedNumber(value: string): number | null {
  const cleaned = value.replace(/,/g, '').replace(/[()]/g, (match) => (match === '(' ? '-' : '')).trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(cleaned)) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
}

/** ROC "1151007" / "115/10/07" / "115年10月07日", or "20261007" / "2026-10-07" → "2026-10-07". */
export function taiwanDateToIso(value: string): string | null {
  const text = value.trim();
  let year: number, month: number, day: number;
  let match = /^(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})$/.exec(text);
  if (match) {
    [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  } else {
    match = /^(\d{2,3})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})日?$/.exec(text);
    if (!match) return null;
    [year, month, day] = [Number(match[1]) + 1911, Number(match[2]), Number(match[3])];
  }
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

export interface TaiwanPeRow {
  code: string;
  pe: number | null;
  date: string | null;
}

const CODE_KEYS = ['Code', '證券代號', '股票代號', 'SecuritiesCompanyCode', '公司代號'];
const DATE_KEYS = ['Date', '資料日期', '日期', '出表日期'];

function rowsOf(raw: unknown): Record<string, unknown>[] {
  return Array.isArray(raw) ? raw.filter((row): row is Record<string, unknown> => typeof row === 'object' && row !== null) : [];
}

/** TWSE BWIBBU_ALL (Code, Name, PEratio, DividendYield, PBratio[, Date]) or TPEx peratio analysis. */
export function parseTaiwanPe(raw: unknown): TaiwanPeRow[] {
  return rowsOf(raw).flatMap((row) => {
    const code = field(row, CODE_KEYS);
    if (!/^[0-9]{4}$/.test(code)) return [];
    const pe = parsePositiveNumber(field(row, ['PEratio', 'PeRatio', 'PriceEarningRatio', 'PERatio', '本益比']));
    const date = taiwanDateToIso(field(row, DATE_KEYS));
    return [{ code, pe, date }];
  });
}

/** TWSE STOCK_DAY_ALL (ClosingPrice) / TPEx mainboard daily close quotes (Close). */
export function parseTaiwanClose(raw: unknown): Map<string, { close: number; date: string | null }> {
  const map = new Map<string, { close: number; date: string | null }>();
  for (const row of rowsOf(raw)) {
    const code = field(row, CODE_KEYS);
    if (!/^[0-9]{4}$/.test(code)) continue;
    const close = parsePositiveNumber(field(row, ['ClosingPrice', 'Close', '收盤價']));
    if (close !== null) map.set(code, { close, date: taiwanDateToIso(field(row, DATE_KEYS)) });
  }
  return map;
}

/**
 * Latest-quarter financial statements (TWSE t187ap14_L / TPEx mopsfin_t187ap14_O): 年度 (ROC), 季別,
 * 基本每股盈餘(元). Taiwan EPS is cumulative year-to-date, so only 季別 4 is a full fiscal year.
 */
export function parseTaiwanAnnualEps(raw: unknown): Map<string, { eps: number; fiscalYear: number }> {
  const map = new Map<string, { eps: number; fiscalYear: number }>();
  for (const row of rowsOf(raw)) {
    const code = field(row, ['公司代號', 'SecuritiesCompanyCode', 'Code']);
    if (!/^[0-9]{4}$/.test(code)) continue;
    const quarter = Number(field(row, ['季別', 'Season', 'Quarter']));
    const rocYear = Number(field(row, ['年度', 'Year']));
    const eps = parseSignedNumber(field(row, ['基本每股盈餘(元)', '基本每股盈餘（元）', '基本每股盈餘', 'EPS', 'BasicEarningsPerShare']));
    if (quarter !== 4 || !Number.isInteger(rocYear) || rocYear < 90 || eps === null) continue;
    map.set(code, { eps: round(eps, 2), fiscalYear: rocYear + 1911 });
  }
  return map;
}

export interface TaiwanBuildInput {
  exchange: 'TWSE' | 'TPEx';
  pe: readonly TaiwanPeRow[];
  close: ReadonlyMap<string, { close: number; date: string | null }>;
  annual: ReadonlyMap<string, { eps: number; fiscalYear: number }>;
  /** Previous tw.json items, so a full-year EPS published in spring survives until the next one. */
  previous: Readonly<Record<string, ValuationRecord>>;
  fallbackDate: string;
}

export function buildTaiwanRecords(input: TaiwanBuildInput): Record<string, ValuationRecord> {
  const suffix = input.exchange === 'TWSE' ? 'TW' : 'TWO';
  const items: Record<string, ValuationRecord> = {};
  for (const row of input.pe) {
    const symbol = `${row.code}.${suffix}`;
    const close = input.close.get(row.code);
    const sameDay = !close?.date || !row.date || close.date === row.date;
    const epsTtm = row.pe !== null && close && sameDay ? round(close.close / row.pe, 2) : null;
    const annual = input.annual.get(row.code);
    const previous = input.previous[symbol];
    const epsAnnual = annual?.eps ?? previous?.epsAnnual ?? null;
    const fiscalYear = annual?.fiscalYear ?? (previous?.epsAnnual !== null && previous?.epsAnnual !== undefined ? previous.fiscalYear : null);
    // A row without any usable figure (loss-making or suspended: the exchange leaves P/E blank) is omitted.
    if (epsTtm === null && epsAnnual === null && row.pe === null) continue;
    items[symbol] = {
      epsTtm,
      epsAnnual,
      fiscalYear: epsAnnual === null ? null : fiscalYear ?? null,
      exchangePeTtm: row.pe,
      asOf: row.date ?? close?.date ?? input.fallbackDate,
      source: input.exchange,
    };
  }
  return items;
}
