import { z } from 'zod';
import type { GroupItem } from '../report/schema';
import type { GrowthFile } from '../fundamentals/GrowthProvider';
import type { ScreenerPeFigures } from '../app/dataSources';
import { activeChips, applyFilters, filterAvailability, FILTER_PARAM_MAX, parseFilters, serializeFilters, type FixedScreenerMas } from '../pages/screenerFilters';

const symbol = z.string().regex(/^[A-Z0-9^][A-Z0-9.^=-]{0,31}$/);
const member = z.object({ symbol, name: z.string().max(200) });
const date = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((value) => {
    const time = Date.parse(`${value}T00:00:00Z`);
    return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
  });
const snapshot = z.object({
  date,
  previousSession: date.nullable().optional(),
  members: z
    .array(member)
    .max(15000)
    .refine((items) => new Set(items.map((item) => item.symbol)).size === items.length),
});
export const watchlistSchema = z.object({
  id: z.string().uuid(),
  name: z.string().trim().min(1).max(40),
  market: z.enum(['TW', 'US']),
  group: z.string().min(1).max(80),
  kind: z.enum(['rules', 'manual']),
  filters: z.string().max(FILTER_PARAM_MAX),
  symbols: z.array(symbol).max(500).default([]),
  maList: z
    .array(z.number().int().min(1).max(1000))
    .min(1)
    .max(4)
    .refine((values) => new Set(values).size === values.length)
    .default([20, 50, 200]),
  monitorBreakouts: z.boolean().default(false),
  updatedAt: z.number().finite().nonnegative(),
  history: z
    .array(snapshot)
    .max(90)
    .refine((items) => items.every((item, i) => i === 0 || item.date > items[i - 1].date))
    .default([]),
  lastViewed: snapshot.nullable().default(null),
  lastOpenedAt: z.number().finite().nonnegative().nullable().default(null),
  cloud: z.object({ userId: z.string().min(1), version: z.string().min(1) }).optional(),
});
export type SavedWatchlist = z.output<typeof watchlistSchema>;
export type WatchlistMember = z.output<typeof member>;
export const watchlistDocumentSchema = z
  .object({ version: z.literal(1), lists: z.array(watchlistSchema).max(30) })
  .refine((doc) => new Set(doc.lists.map((list) => list.id)).size === doc.lists.length, '清單 ID 重複');
export type WatchlistDocument = z.output<typeof watchlistDocumentSchema>;

/** Never turn a missing source/filter into a successful, unfiltered evaluation. */
export function evaluateWatchlist(
  list: SavedWatchlist,
  items: readonly GroupItem[],
  growth: GrowthFile | null | undefined,
  fixedMAs?: FixedScreenerMas,
  pe?: ScreenerPeFigures | null,
): WatchlistMember[] {
  if (items.some((item) => item.status === 'failed')) throw new Error('部分股票行情載入失敗，暫不判定新入選或出榜；保留上次基準。');
  const filters = parseFilters(list.filters);
  if (filters.ema20 && fixedMAs === undefined) throw new Error('固定均線參數尚未取得，保留上次清單，待資料恢復後再比較。');
  const missingFixedSymbols = filters.ema20 ? items.filter(item => fixedMAs?.[item.symbol]?.distance === null).map(item => item.symbol) : [];
  if (missingFixedSymbols.length)
    throw new Error(`固定均線行情暫時無法取得（${missingFixedSymbols.join('、')}），保留上次清單，暫不判定出榜。`);
  const availability = filterAvailability(items, { market: list.market, growth, growthLoading: false, fixedMAs, pe });
  const blocked = activeChips(filters, availability, list.market, !!fixedMAs && Object.keys(fixedMAs).length > 0).find((chip) => chip.unavailable);
  if (blocked) throw new Error(`${blocked.label}：${blocked.unavailable}。保留上次清單，待資料恢復後再比較。`);
  const source = items.filter((item) => item.status !== 'failed');
  if (list.kind === 'manual') {
    const found = new Set(source.map((item) => item.symbol));
    if (list.symbols.some((ticker) => !found.has(ticker))) throw new Error('部分手動自選股缺少行情，保留上次結果，暫不判定出榜。');
  }
  return applyFilters(list.kind === 'manual' ? source.filter((item) => list.symbols.includes(item.symbol)) : source, filters, {
    market: list.market,
    growth,
    fixedMAs,
    pe,
  }).map(({ symbol, name }) => ({ symbol, name }));
}

export function nextWeekday(day: string): string {
  const value = new Date(`${day}T00:00:00Z`);
  do value.setUTCDate(value.getUTCDate() + 1);
  while (value.getUTCDay() === 0 || value.getUTCDay() === 6);
  return value.toISOString().slice(0, 10);
}

export interface WatchlistVisit {
  firstVisit: boolean;
  previousDate: string | null;
  previousOpenedAt: number | null;
  entered: WatchlistMember[];
  exited: WatchlistMember[];
  members: (WatchlistMember & { streak: number })[];
  list: SavedWatchlist;
}

/** A daily collector can append observations without moving the user's last-opening baseline. */
export function recordWatchlistData(
  list: SavedWatchlist,
  members: WatchlistMember[],
  dataDate: string,
  previousSession?: string | null,
): SavedWatchlist {
  const parsedDate = date.parse(dataDate);
  const previous = list.history.at(-1);
  if (previous && previous.date > parsedDate) throw new Error('目前資料比上次開啟時更舊，沒有覆寫上次結果。');
  const distinct = [...new Map(members.map((item) => [item.symbol, member.parse(item)])).values()];
  const sameDay = list.history.find((entry) => entry.date === parsedDate);
  return {
    ...list,
    history: [
      ...list.history.filter((entry) => entry.date < parsedDate),
      { date: parsedDate, previousSession: previousSession === undefined ? sameDay?.previousSession : previousSession, members: distinct },
    ].slice(-90),
  };
}

/** The previous opening is the baseline; reloads on one data day never add another day. */
export function visitWatchlist(list: SavedWatchlist, members: WatchlistMember[], dataDate: string, openedAt = Date.now()): WatchlistVisit {
  const parsedDate = date.parse(dataDate);
  if (!Number.isFinite(Date.parse(`${parsedDate}T00:00:00Z`))) throw new Error('資料日期無法辨識，沒有覆寫上次結果。');
  const previous = list.lastViewed ?? (list.lastOpenedAt !== null ? list.history.at(-1) : undefined);
  const distinct = [...new Map(members.map((item) => [item.symbol, member.parse(item)])).values()];
  const currentKeys = new Set(distinct.map((item) => item.symbol));
  const previousKeys = new Set(previous?.members.map((item) => item.symbol));
  const recorded = recordWatchlistData(list, distinct, dataDate);
  const history = recorded.history;
  return {
    firstVisit: !previous,
    previousDate: previous?.date ?? null,
    previousOpenedAt: list.lastOpenedAt,
    entered: previous ? distinct.filter((item) => !previousKeys.has(item.symbol)) : [],
    exited: previous?.members.filter((item) => !currentKeys.has(item.symbol)) ?? [],
    members: distinct.map((item) => {
      let streak = 1;
      for (let i = history.length - 2; i >= 0; i--) {
        const next = history[i + 1];
        const adjacent = next.previousSession ? history[i].date === next.previousSession : nextWeekday(history[i].date) === next.date;
        if (!adjacent || !history[i].members.some((entry) => entry.symbol === item.symbol)) break;
        streak++;
      }
      return { ...item, streak };
    }),
    list: { ...recorded, lastViewed: history.at(-1)!, lastOpenedAt: openedAt },
  };
}

export function createWatchlist(input: {
  name: string;
  market: 'TW' | 'US';
  group: string;
  filters: string;
  symbols?: string[];
  kind?: 'rules' | 'manual';
  maList?: number[];
}): SavedWatchlist {
  return watchlistSchema.parse({
    ...input,
    id: crypto.randomUUID(),
    kind: input.kind ?? 'rules',
    filters: serializeFilters(parseFilters(input.filters)),
    monitorBreakouts: false,
    updatedAt: Date.now(),
    history: [],
    lastOpenedAt: null,
  });
}
