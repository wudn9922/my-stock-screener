import { expect, it } from 'vitest';
import { mergeArchiveDaily, parseArchiveCsv } from '../src/market-data/KlineArchive';
import { normalizeYahooResponse } from '../src/market-data/YahooNormalizer';
import { calendarDate, getMarketProfile } from '../src/market-data/MarketProfile';

// PR #224 real response shape: ^TWII 11/04 and 11/05 originally all-null;
// the Edge response rebuilds them from 5m and annotates the dates in meta.
import { D5, intradayFilledPayload as payload } from './fixtures/intraday-fill';
it.each(['2026-11-03','2026-11-04'])('merges #224 filled sessions by exchange date with archive through %s', cutoff => {
  const now = D5+3600, market=getMarketProfile('^TWII');
  expect(normalizeYahooResponse(payload(false),now,'1D','^TWII').bars.map(b=>calendarDate(b.time,market))).toEqual(['2026-11-03']);
  const rows='2026-11-03,44481.59,44614.64,43191.58,43307.54,4346200\n'
    +(cutoff.endsWith('04')?'2026-11-04,43000,43102,42898,42948.91,200\n':'');
  const archived=parseArchiveCsv('Date,Open,High,Low,Close,Volume\n'+rows,'^TWII',2026).bars;
  const recent=normalizeYahooResponse(payload(true),now,'1D','^TWII').bars;
  const first=mergeArchiveDaily(archived,recent,'^TWII',cutoff);
  expect(first.splitMismatch).toBe(false);
  expect(first.bars.map(b=>calendarDate(b.time,market))).toEqual(['2026-11-03','2026-11-04','2026-11-05']);
  const tick={...recent.at(-1)!,time:D5+300,close:43260,high:43300};
  const next=mergeArchiveDaily(first.bars,[...recent,tick],'^TWII',cutoff);
  expect(next.bars).toHaveLength(3);
  expect(next.bars.map(b=>calendarDate(b.time,market))).toEqual(['2026-11-03','2026-11-04','2026-11-05']);
  expect(next.bars.at(-1)?.close).toBe(43260);
  for(const bar of archived) expect(next.bars.find(b=>calendarDate(b.time,market)===calendarDate(bar.time,market))).toEqual(bar);
});
