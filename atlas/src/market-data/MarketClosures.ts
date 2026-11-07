/** Derived market calendar only. Never deletes or rewrites a price/archive file. */
import { z } from 'zod';
import verified from './verified-market-closures.json';
import { defaultReportBase } from './StaticSeriesProvider';
import { getMarketProfile, calendarDate } from './MarketProfile';
import type { Bar } from './MarketDataProvider';

type OfficialClosureProof = { date: string; reasons: readonly string[]; officialEvidence?: {
  exchange: 'twse' | 'tpex'; url: string; sha256: string; quoteRows: number; stat: string; responseDate?: string | null;
}[] };
function officialClosure(row: OfficialClosureProof): boolean {
  const evidence = row.officialEvidence;
  if (!row.reasons.includes('official-no-quotes') || evidence?.length !== 2 || new Set(evidence.map(e => e.exchange)).size !== 2) return false;
  const [year, month, day] = row.date.split('-');
  const compact = row.date.replaceAll('-', '');
  return evidence.every(e => e.quoteRows === 0 && /^[a-f0-9]{64}$/.test(e.sha256) && (e.exchange === 'twse'
    ? (e.url === `https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=${compact}&type=ALLBUT0999`
      || e.url === `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${compact}&type=ALLBUT0999&response=json`) && e.stat === '很抱歉，沒有符合條件的資料!' && (e.responseDate == null || e.responseDate === compact)
    : e.url === `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=${Number(year) - 1911}%2F${month}%2F${day}&id=&response=json` && e.stat === 'ok' && e.responseDate === compact));
}
const officialEvidence = z.array(z.object({ exchange: z.enum(['twse', 'tpex']), url: z.url(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/), quoteRows: z.literal(0), stat: z.string(),
  responseDate: z.string().nullable().optional(),
})).length(2).optional();
const closure = z.object({ date: z.iso.date(), reasons: z.array(z.enum([
  'weekend', 'primary-index-missing', 'flat-zero-cohort', 'published-closure', 'exchange-calendar-closed', 'official-no-quotes',
])).min(1), observedSymbols: z.number().int().nonnegative(), flatZeroSymbols: z.number().int().nonnegative(),
  eligibleSymbols: z.number().int().nonnegative(), unknownVolumeSymbols: z.number().int().nonnegative(),
  positiveVolumeSymbols: z.number().int().nonnegative().default(0),
  calendar: z.enum(['XTAI', 'XNYS']), calendarSession: z.boolean(), officialEvidence,
  flatZeroShare: z.number().min(0).max(1).nullable(), coverageReference: z.number().nonnegative(),
}).refine(row => row.positiveVolumeSymbols < 50 && (!row.observedSymbols || row.positiveVolumeSymbols / row.observedSymbols < .05)
  && row.positiveVolumeSymbols <= row.observedSymbols
  && row.flatZeroSymbols <= row.observedSymbols && row.observedSymbols <= row.eligibleSymbols
  && row.unknownVolumeSymbols <= row.observedSymbols
  && (!row.calendarSession || row.calendar === 'XTAI' && officialClosure(row) || row.reasons.includes('primary-index-missing') && row.reasons.includes('flat-zero-cohort')
    && row.observedSymbols >= 2 && row.flatZeroShare !== null && row.flatZeroShare >= .95)
  && (!row.reasons.includes('official-no-quotes') || row.calendar === 'XTAI' && officialClosure(row))
  && (row.eligibleSymbols ? row.flatZeroShare !== null && Math.abs(row.flatZeroShare - row.flatZeroSymbols / row.eligibleSymbols) < 1e-9 : row.flatZeroShare === null)
  && (!row.reasons.includes('flat-zero-cohort') || row.observedSymbols >= 2 && row.flatZeroShare !== null && row.flatZeroShare >= .95));
const indexGap = z.object({ date: z.iso.date(), reason: z.literal('primary-index-gap-with-trading'),
  observedSymbols: z.number().int().positive(), positiveVolumeSymbols: z.number().int().nonnegative(),
}).refine(row => row.positiveVolumeSymbols <= row.observedSymbols && (row.positiveVolumeSymbols >= 50 || row.positiveVolumeSymbols / row.observedSymbols >= .05));
const missingSession = z.object({ date: z.iso.date(), reason: z.literal('missing-session-data'),
  calendar: z.enum(['XTAI', 'XNYS']), calendarSession: z.literal(true), primaryIndexMissing: z.boolean(),
  observedSymbols: z.number().int().nonnegative(), officialSources: z.array(z.object({ symbol: z.string().min(1), source: z.string().min(1) })),
});
const market = z.object({ closedSessions: z.array(closure), indexGapsWithTrading: z.array(indexGap).default([]),
  missingSessions: z.array(missingSession).default([]) });
export const marketClosuresSchema = z.object({ version: z.literal(1), threshold: z.literal(.95),
  markets: z.object({ tw: market.optional(), us: market.optional() }),
}).refine(policy => !policy.markets.us?.closedSessions.some(row => row.reasons.includes('official-no-quotes')));
export type MarketClosurePolicy = z.infer<typeof marketClosuresSchema>;
const seed = marketClosuresSchema.parse(verified);
const loads = new WeakMap<typeof fetch, { day: string; request: Promise<MarketClosurePolicy> }>();

/** One bounded read per UTC day/page, shared by all symbols; no quote polling. */
export function loadMarketClosures(fetcher: typeof fetch, now: number): Promise<MarketClosurePolicy> {
  const day = new Date(now).toISOString().slice(0, 10), previous = loads.get(fetcher);
  if (previous?.day === day) return previous.request;
  const request = (async () => {
    const fallback = () => previous?.request ?? seed;
    try {
      const response = await fetcher(`${defaultReportBase()}market-closures.json`, { signal: AbortSignal.timeout(1500) });
      if (!response.ok) return fallback();
      const parsed = marketClosuresSchema.safeParse(await response.json());
      return parsed.success ? parsed.data : fallback();
    } catch { return fallback(); }
  })();
  loads.set(fetcher, { day, request });
  return request;
}

export function closedMarketDates(symbol: string, policy: MarketClosurePolicy = seed): ReadonlySet<string> {
  const market = getMarketProfile(symbol).market === 'TW' ? 'tw' : 'us';
  // A verified legacy date remains excluded even when a future report is absent
  // or only covers recent observations. The seed is generated by the scan tool.
  const verifiedOfficial = new Set((seed.markets[market]?.closedSessions ?? []).filter(officialClosure).map(row => row.date));
  const missing = [...(policy.markets[market]?.missingSessions ?? []), ...(seed.markets[market]?.missingSessions ?? [])]
    .filter(row => !verifiedOfficial.has(row.date) || row.officialSources.length > 0);
  const traded = new Set([...(policy.markets[market]?.indexGapsWithTrading ?? []), ...missing].map(row => row.date));
  return new Set([...(seed.markets[market]?.closedSessions ?? []), ...(policy.markets[market]?.closedSessions ?? [])].map(row => row.date).filter(day => !traded.has(day)));
}

export function withoutClosedDates(bars: readonly Bar[], symbol: string, dates: ReadonlySet<string>): Bar[] {
  const market = getMarketProfile(symbol);
  return bars.filter(bar => !dates.has(calendarDate(bar.time, market)));
}
