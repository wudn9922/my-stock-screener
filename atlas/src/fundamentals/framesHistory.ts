import { reportUrl } from '../report/loadReport';
import {
  MAX_PERIODS,
  RAW_FIELDS,
  emptySeries,
  parseFinancials,
  type FinancialHistory,
  type FinancialSeries,
  type FinancialsLoadResult,
  type PeriodKind,
  type RawField,
} from './FinancialHistory';

/**
 * ~10-year US statement history from SEC XBRL frames (`report/fundamentals/us-history/NNN.json`, written by
 * atlas/scripts/build_fundamentals_sec.py). The financials page uses it for a US stock that has no
 * `financials/<KEY>.json`, or to add older periods to a shorter one:
 *
 * - Shard of a symbol key = FNV-1a 32-bit of the key mod `HISTORY_SHARDS` (the script's `history_shard`).
 * - Only the fields frames have: revenue, net income (US filers), EPS diluted / basic, weighted diluted shares.
 *   Every other field stays null, so the page shows gross margin, cash flow, balance sheet… as missing.
 * - Frames-only files are marked `frames-only` (quality "limited"); splices `frames-extended`, and a frames
 *   history that disagrees with the file where both have a period is not used (`frames-not-merged`).
 * - Never downgrades: a file's own periods are never replaced; frames only add periods older than its first one.
 */

export const HISTORY_SHARDS = 128;
export const FRAMES_SOURCE = 'SEC EDGAR XBRL frames';
/** Files with fewer periods than this get older periods from the frames history (when it has them). */
export const EXTEND_BELOW: Record<PeriodKind, number> = { annual: 10, quarterly: 40 };
/** Frames periods must end at least this many days before the file's first period to be added. */
const SPLICE_GAP_DAYS = 20;
/** Two periods are the same when their ends are this close (52/53-week years). */
const SAME_END_DAYS = 10;
/** Overlap periods (nearest the splice point) that must agree: revenue within 3%, EPS within 5% (or 0.02). */
const CHECK_OVERLAP = 4;
const REVENUE_TOLERANCE = 0.03;
const EPS_TOLERANCE = 0.05;
/** Fields a frames history can carry (the rest of RAW_FIELDS are always null there). */
export const FRAMES_FIELDS: readonly RawField[] = ['revenue', 'netIncome', 'epsDiluted', 'epsBasic', 'dilutedShares'];

/** FNV-1a 32-bit hash of an ASCII key mod HISTORY_SHARDS (same as the Python builder). */
export function historyShard(key: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    const code = key.charCodeAt(i);
    hash ^= code < 128 ? code : 63; // '?' like Python's encode('ascii', 'replace')
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash % HISTORY_SHARDS;
}

export function historyShardUrl(index: number, report = reportUrl()): string {
  return new URL(`fundamentals/us-history/${String(index).padStart(3, '0')}.json`, report).href;
}

export interface HistoryShard {
  generatedAt: string | null;
  asOf: string | null;
  fields: { q: string[]; y: string[] };
  symbols: Record<string, unknown>;
}

const recordOf = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const stringList = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((item) => typeof item === 'string') ? (value as string[]) : null;
const text = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : null);

/** Parses one shard; null when it is not a version-1 history shard. */
export function parseHistoryShard(data: unknown): HistoryShard | null {
  const raw = recordOf(data);
  if (raw.version !== 1) return null;
  const fields = recordOf(raw.fields);
  const q = stringList(fields.q);
  const y = stringList(fields.y);
  if (!q || !y || !q.includes('end') || !y.includes('end')) return null;
  const symbols = recordOf(raw.symbols);
  return { generatedAt: text(raw.generatedAt), asOf: text(raw.asOf), fields: { q, y }, symbols };
}

/** Rows of one kind → the raw block parseSeries understands (src "frames" for every period). */
function rawBlock(rows: unknown, fields: readonly string[], kind: PeriodKind) {
  const list = Array.isArray(rows) ? rows.filter((row): row is unknown[] => Array.isArray(row)) : [];
  const column = (name: string) => {
    const at = fields.indexOf(name);
    return list.map((row) => (at < 0 ? null : (row[at] ?? null)));
  };
  const values: Record<string, unknown[]> = {};
  for (const field of FRAMES_FIELDS) values[field] = column(field);
  return {
    end: column('end'),
    fy: column('fy'),
    fq: kind === 'quarterly' ? column('fq') : list.map(() => null),
    src: list.map(() => 'frames'),
    values,
  };
}

/** One shard entry → a FinancialHistory (frames-only), or null when it has no usable period. */
export function framesEntryHistory(key: string, entry: unknown, shard: Pick<HistoryShard, 'fields' | 'generatedAt' | 'asOf'>): FinancialHistory | null {
  const raw = recordOf(entry);
  if (!Object.keys(raw).length) return null;
  const foreign = raw.f === 1;
  const issues = Array.isArray(raw.issues) ? raw.issues.filter((issue): issue is string => typeof issue === 'string') : [];
  const document = {
    version: 1,
    symbol: key,
    market: 'US',
    currency: text(raw.cur) ?? 'USD',
    foreignReporter: foreign,
    generatedAt: shard.generatedAt,
    checkedAt: shard.generatedAt?.slice(0, 10) ?? shard.asOf,
    source: FRAMES_SOURCE,
    fyEndMonth: raw.fyEnd,
    annual: rawBlock(raw.y, shard.fields.y, 'annual'),
    quarterly: rawBlock(raw.q, shard.fields.q, 'quarterly'),
    split: { adjusted: true, events: Array.isArray(raw.split) ? raw.split : [] },
    quality: {
      level: 'limited',
      issues: ['frames-only', ...issues, ...(foreign ? ['foreign-filer'] : [])],
    },
  };
  const parsed = parseFinancials(document);
  if (parsed.status !== 'ok') return null;
  const history = parsed.data;
  history.quality.annualYears = history.annual.end.length;
  history.quality.quarters = history.quarterly.end.length;
  return history;
}

const dayOf = (end: string) => Date.parse(`${end}T00:00:00Z`) / 86_400_000;
const near = (a: number | null, b: number | null, tolerance: number, floor = 0) =>
  a === null || b === null || Math.abs(a - b) <= Math.max(floor, tolerance * Math.abs(b));

/**
 * Whether the frames periods agree with the file where both have a period: the CHECK_OVERLAP overlapping
 * periods nearest the splice point must match on revenue and EPS (where both have the number), and at least
 * one number must have been compared. A different share basis or another revenue line fails here.
 */
export function framesAgree(file: FinancialSeries, frames: FinancialSeries): boolean {
  let compared = 0;
  let checked = 0;
  for (let i = 0; i < file.end.length && checked < CHECK_OVERLAP; i++) {
    const day = dayOf(file.end[i]!);
    const j = frames.end.findIndex((end) => Math.abs(dayOf(end) - day) <= SAME_END_DAYS);
    if (j < 0) continue;
    checked += 1;
    const pairs: [number | null, number | null, number, number][] = [
      [frames.values.revenue[j] ?? null, file.values.revenue[i] ?? null, REVENUE_TOLERANCE, 0],
      [frames.values.epsDiluted[j] ?? null, file.values.epsDiluted[i] ?? null, EPS_TOLERANCE, 0.02],
    ];
    for (const [a, b, tolerance, floor] of pairs) {
      if (a === null || b === null) continue;
      compared += 1;
      if (!near(a, b, tolerance, floor)) return false;
    }
  }
  return compared > 0;
}

/** The frames periods ending before the file's first period (minus SPLICE_GAP_DAYS), in file order. */
function olderPeriods(file: FinancialSeries, frames: FinancialSeries): number[] {
  if (!file.end.length) return frames.end.map((_, i) => i);
  const first = dayOf(file.end[0]!);
  return frames.end.flatMap((end, i) => (dayOf(end) <= first - SPLICE_GAP_DAYS ? [i] : []));
}

function prepend(file: FinancialSeries, frames: FinancialSeries, indices: number[], kind: PeriodKind): FinancialSeries {
  const out = emptySeries();
  const push = (series: FinancialSeries, i: number) => {
    out.end.push(series.end[i]!);
    out.fy.push(series.fy[i] ?? null);
    out.fq.push(series.fq[i] ?? null);
    out.src.push(series.src[i] ?? 'yahoo');
    for (const field of RAW_FIELDS) out.values[field].push(series.values[field][i] ?? null);
  };
  for (const i of indices) push(frames, i);
  file.end.forEach((_, i) => push(file, i));
  const drop = Math.max(0, out.end.length - MAX_PERIODS[kind]);
  if (drop) {
    out.end.splice(0, drop);
    out.fy.splice(0, drop);
    out.fq.splice(0, drop);
    out.src.splice(0, drop);
    for (const field of RAW_FIELDS) out.values[field].splice(0, drop);
  }
  return out;
}

/**
 * The file with older frames periods added in front, per kind (only for a kind with fewer than EXTEND_BELOW
 * periods, in the same currency, when `framesAgree`). The file's own periods are never touched. Issues:
 * `frames-extended` when something was added, `frames-not-merged` when the frames history disagreed.
 */
export function extendWithFrames(file: FinancialHistory, frames: FinancialHistory): FinancialHistory {
  if (file.currency !== frames.currency || file.foreignReporter !== frames.foreignReporter) return file;
  const issues = new Set(file.quality.issues);
  const next: FinancialHistory = { ...file, quality: { ...file.quality } };
  for (const kind of ['annual', 'quarterly'] as const) {
    if (file[kind].end.length >= EXTEND_BELOW[kind]) continue;
    const indices = olderPeriods(file[kind], frames[kind]);
    if (!indices.length) continue;
    if (!framesAgree(file[kind], frames[kind])) {
      issues.add('frames-not-merged');
      continue;
    }
    next[kind] = prepend(file[kind], frames[kind], indices, kind);
    issues.add('frames-extended');
  }
  if (issues.size === file.quality.issues.length) return file;
  next.quality.issues = [...issues];
  next.quality.annualYears = next.annual.end.length;
  next.quality.quarters = next.quarterly.end.length;
  return next;
}

/** Whether a load result could use the frames history (US only: a missing file, or a short one). */
export function wantsFrames(result: FinancialsLoadResult, key: string): boolean {
  if (/\.TWO?$/.test(key)) return false;
  if (result.status === 'missing') return true;
  if (result.status !== 'ok' || result.data.market !== 'US') return false;
  return result.data.annual.end.length < EXTEND_BELOW.annual || result.data.quarterly.end.length < EXTEND_BELOW.quarterly;
}

/** The file's result combined with the frames history (null = none for this stock / unavailable). */
export function combineWithFrames(result: FinancialsLoadResult, frames: FinancialHistory | null, framesUrl: string): FinancialsLoadResult {
  if (!frames) return result;
  if (result.status === 'missing') return { status: 'ok', data: frames, url: framesUrl };
  if (result.status === 'ok') return { ...result, data: extendWithFrames(result.data, frames) };
  return result;
}

const RELOAD_AFTER_MS = 30 * 60_000;

export interface FramesHistoryLoaderOptions {
  fetcher?: typeof fetch;
  now?: () => number;
  urlOf?: (index: number) => string;
}

/** Loads shards (each reused for 30 minutes; a failure is not pinned) and returns one symbol's history. */
export class FramesHistoryLoader {
  private readonly fetcher: typeof fetch;
  private readonly now: () => number;
  readonly urlOf: (index: number) => string;
  private readonly shards = new Map<number, { at: number; promise: Promise<HistoryShard | null> }>();

  constructor(options: FramesHistoryLoaderOptions = {}) {
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.now = options.now ?? Date.now;
    this.urlOf = options.urlOf ?? ((index) => historyShardUrl(index));
  }

  private shard(index: number): Promise<HistoryShard | null> {
    const cached = this.shards.get(index);
    if (cached && this.now() - cached.at < RELOAD_AFTER_MS) return cached.promise;
    const promise = (async () => {
      try {
        const response = await this.fetcher(this.urlOf(index), { cache: 'no-cache' });
        if (!response.ok) {
          // 404 = no shard yet (pinned like a result); other statuses are retried next time
          if (response.status !== 404) this.shards.delete(index);
          return null;
        }
        const shard = parseHistoryShard(JSON.parse(await response.text()));
        if (!shard) this.shards.delete(index);
        return shard;
      } catch {
        this.shards.delete(index);
        return null;
      }
    })();
    this.shards.set(index, { at: this.now(), promise });
    return promise;
  }

  /** The frames history of a financials key, or null (no entry, no shard, offline). Never throws. */
  async load(key: string): Promise<{ history: FinancialHistory | null; url: string }> {
    const index = historyShard(key);
    const url = this.urlOf(index);
    const shard = await this.shard(index);
    if (!shard) return { history: null, url };
    return { history: framesEntryHistory(key, shard.symbols[key], shard), url };
  }
}
