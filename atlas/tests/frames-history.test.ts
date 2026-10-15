import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { FinancialsProvider, RAW_FIELDS, emptySeries, parseFinancials, type FinancialHistory, type FinancialSeries } from '../src/fundamentals/FinancialHistory';
import {
  EXTEND_BELOW,
  FramesHistoryLoader,
  combineWithFrames,
  extendWithFrames,
  framesAgree,
  framesEntryHistory,
  historyShard,
  historyShardUrl,
  parseHistoryShard,
  wantsFrames,
} from '../src/fundamentals/framesHistory';
import { buildPeRiver, dateToSec, type PriceBar } from '../src/fundamentals/peRiver';
import { buildView, depthText, provenanceText } from '../src/ui/financials/financialsView';

// The builder's real output for a synthetic stock (tests/test_fundamentals_sec_history.py --write-fixture):
// ~10 years of frames, fiscal Q4s derived, a 4-for-1 split inferred from the weighted share counts.
const SHARD_FILE = resolve(import.meta.dirname, 'fixtures/frames/us-history-028.json');
const shardText = readFileSync(SHARD_FILE, 'utf8');
const shard = parseHistoryShard(JSON.parse(shardText))!;
const zfrm = () => framesEntryHistory('ZFRM', shard.symbols.ZFRM, shard)!;
const response = (status: number, body: string) => new Response(body, { status });

/** The last `annual` / `quarterly` periods of a history as a Yahoo-style file (with a gross profit column). */
function shortFile(history: FinancialHistory, annual: number, quarterly: number, epsFactor = 1): FinancialHistory {
  const slice = (series: FinancialSeries, count: number): FinancialSeries => {
    const from = series.end.length - count;
    const out = emptySeries();
    out.end = series.end.slice(from);
    out.fy = series.fy.slice(from);
    out.fq = series.fq.slice(from);
    out.src = out.end.map(() => 'yahoo' as const);
    for (const field of RAW_FIELDS) out.values[field] = series.values[field].slice(from);
    out.values.epsDiluted = out.values.epsDiluted.map((value) => (value === null ? null : value * epsFactor));
    out.values.grossProfit = out.values.revenue.map((value) => (value === null ? null : value * 0.4));
    return out;
  };
  return {
    ...history,
    source: 'Yahoo Finance fundamentals-timeseries',
    annual: slice(history.annual, annual),
    quarterly: slice(history.quarterly, quarterly),
    split: { adjusted: false, events: [] },
    quality: { ...history.quality, level: 'good', issues: [] },
  };
}

describe('history shards', () => {
  it('uses the same shard hash as the Python builder', () => {
    // Same values as tests/test_fundamentals_sec_history.py ShardTest
    expect(['AAPL', 'BRK-B', 'GOOGL', 'ZZZZ', 'A'].map(historyShard)).toEqual([11, 117, 39, 37, 76]);
    expect(historyShard('ZFRM')).toBe(28);
    expect(historyShardUrl(28, 'https://x.test/my-stock-screener/report/latest.json')).toBe(
      'https://x.test/my-stock-screener/report/fundamentals/us-history/028.json',
    );
  });

  it('rejects other documents', () => {
    expect(parseHistoryShard(null)).toBeNull();
    expect(parseHistoryShard({ version: 2, fields: shard.fields, symbols: {} })).toBeNull();
    expect(parseHistoryShard({ version: 1, fields: { q: ['x'], y: ['end'] }, symbols: {} })).toBeNull();
    expect(framesEntryHistory('NONE', undefined, shard)).toBeNull();
    expect(framesEntryHistory('BAD', { q: 'x', y: [[1, 2]] }, shard)).toBeNull();
  });

  it('turns an entry into a frames-only history with ~10 years and nothing invented', () => {
    const history = zfrm();
    expect(history.source).toBe('SEC EDGAR XBRL frames');
    expect(history.quality.level).toBe('limited');
    expect(history.quality.issues).toEqual(expect.arrayContaining(['frames-only', 'q4-derived', 'split-inferred']));
    expect(history.annual.end).toHaveLength(11);
    expect(history.annual.end[0]).toBe('2015-12-31');
    expect(history.quarterly.end).toHaveLength(42);
    expect(history.quarterly.src.every((src) => src === 'frames')).toBe(true);
    // Fields the frames do not have stay null: no gross profit, cash flow or balance sheet
    for (const field of ['grossProfit', 'operatingIncome', 'operatingCashFlow', 'capex', 'equity', 'totalAssets'] as const)
      expect(history.annual.values[field].every((value) => value === null)).toBe(true);
    expect(history.annual.values.netIncome.every((value) => value !== null)).toBe(true);
    // On the newest share basis (split adjusted)
    expect(history.split).toEqual({ adjusted: true, events: [{ date: '2017-12-31', ratio: 4 }] });
    expect(history.annual.values.dilutedShares.every((value) => value === 4e8)).toBe(true);
    expect(history.fyEndMonth).toBe(12);
    expect(history.checkedAt).toBe('2026-10-15');
  });

  it('page notes say where the numbers come from', () => {
    const history = zfrm();
    expect(provenanceText(history, 'annual')).toBe('資料來源：SEC XBRL frames');
    expect(depthText(history, 'annual')).toContain('SEC XBRL frames 約 11 年');
    const view = buildView(history, 'annual');
    expect(view.metrics.grossMargin.values.every((value) => value === null)).toBe(true);
    expect(view.metrics.netMargin.values.some((value) => value !== null)).toBe(true);
  });
});

describe('extending a short file (never a downgrade)', () => {
  it('adds the older frames periods in front and keeps every period of the file', () => {
    const frames = zfrm();
    const file = shortFile(frames, 4, 6);
    expect(framesAgree(file.annual, frames.annual)).toBe(true);
    const merged = extendWithFrames(file, frames);
    expect(merged.annual.end).toHaveLength(11);
    expect(merged.annual.src).toEqual([...Array(7).fill('frames'), ...Array(4).fill('yahoo')]);
    expect(merged.annual.values.grossProfit.slice(-4)).toEqual(file.annual.values.grossProfit);
    expect(merged.annual.values.grossProfit.slice(0, 7).every((value) => value === null)).toBe(true);
    expect(merged.quarterly.end).toHaveLength(42);
    expect(merged.quarterly.end.slice(-6)).toEqual(file.quarterly.end);
    expect(merged.quality.issues).toContain('frames-extended');
    expect(merged.quality.annualYears).toBe(11);
    expect(provenanceText(merged, 'annual')).toBe('資料來源：SEC XBRL frames（FY2015–FY2021） ＋ Yahoo Finance（FY2022–FY2025）');
  });

  it('does not merge frames that disagree with the file (another share basis)', () => {
    const frames = zfrm();
    const file = shortFile(frames, 4, 6, 4); // EPS not on the frames' basis
    const merged = extendWithFrames(file, frames);
    expect(merged.annual.end).toEqual(file.annual.end);
    expect(merged.quality.issues).toContain('frames-not-merged');
    expect(merged.quality.issues).not.toContain('frames-extended');
  });

  it('leaves long files, other currencies and foreign reporters alone', () => {
    const frames = zfrm();
    const long = { ...frames, quality: { ...frames.quality, issues: [] } };
    expect(extendWithFrames(long, frames)).toBe(long);
    const twd = { ...shortFile(frames, 4, 6), currency: 'TWD' };
    expect(extendWithFrames(twd, frames)).toBe(twd);
    expect(EXTEND_BELOW).toEqual({ annual: 10, quarterly: 40 });
  });

  it('wantsFrames: US missing or short files only', () => {
    const frames = zfrm();
    expect(wantsFrames({ status: 'missing', message: '', url: '' }, 'ZFRM')).toBe(true);
    expect(wantsFrames({ status: 'missing', message: '', url: '' }, '2330.TW')).toBe(false);
    expect(wantsFrames({ status: 'error', message: '', url: '' }, 'ZFRM')).toBe(false);
    expect(wantsFrames({ status: 'ok', data: shortFile(frames, 4, 6), url: '' }, 'ZFRM')).toBe(true);
    expect(wantsFrames({ status: 'ok', data: frames, url: '' }, 'ZFRM')).toBe(false);
    expect(combineWithFrames({ status: 'missing', message: 'm', url: 'a' }, null, 'b')).toEqual({ status: 'missing', message: 'm', url: 'a' });
    expect(combineWithFrames({ status: 'missing', message: 'm', url: 'a' }, frames, 'b')).toMatchObject({ status: 'ok', url: 'b' });
  });
});

describe('FinancialsProvider with the frames history', () => {
  const longSec = JSON.parse(readFileSync(resolve(import.meta.dirname, 'fixtures/financials/us-full.json'), 'utf8')) as Record<string, unknown>;

  it('falls back to the frames history for a missing US file', async () => {
    const frames = vi.fn(async () => ({ history: zfrm(), url: '/shard/028.json' }));
    const provider = new FinancialsProvider({ fetcher: (async () => response(404, '')) as typeof fetch, urlOf: (key) => key, frames });
    const result = await provider.load('ZFRM');
    expect(result).toMatchObject({ status: 'ok', url: '/shard/028.json' });
    expect(result.status === 'ok' && result.data.quality.issues).toContain('frames-only');
    expect(frames).toHaveBeenCalledWith('ZFRM');
  });

  it('never asks for frames for a long file or a Taiwan stock, and survives a frames failure', async () => {
    const frames = vi.fn(async () => ({ history: null, url: '' }));
    // A long SEC file (≥ 10 years, ≥ 40 quarters): the shard is not even fetched
    const history = zfrm();
    const sec = {
      ...history,
      annual: { ...history.annual, src: history.annual.src.map(() => 'sec') },
      quarterly: { ...history.quarterly, src: history.quarterly.src.map(() => 'sec') },
      split: { adjusted: true, events: [] },
    };
    const long = new FinancialsProvider({ fetcher: (async () => response(200, JSON.stringify(sec))) as typeof fetch, urlOf: (key) => key, frames });
    expect((await long.load('ZFRM')).status).toBe('ok');
    expect(frames).not.toHaveBeenCalled();
    // A file with 15 quarters asks; without frames data the file is returned as it is
    const parsed = parseFinancials(longSec);
    const short = new FinancialsProvider({ fetcher: (async () => response(200, JSON.stringify(longSec))) as typeof fetch, urlOf: (key) => key, frames });
    expect(await short.load('AAPL')).toEqual({ ...parsed, url: 'AAPL' });
    expect(frames).toHaveBeenCalledTimes(1);
    const missing = new FinancialsProvider({ fetcher: (async () => response(404, '')) as typeof fetch, urlOf: (key) => key, frames });
    expect((await missing.load('2330.TW')).status).toBe('missing');
    const failing = new FinancialsProvider({
      fetcher: (async () => response(404, '')) as typeof fetch,
      urlOf: (key) => key,
      frames: async () => {
        throw new Error('offline');
      },
    });
    expect((await failing.load('ZFRM')).status).toBe('missing');
  });
});

describe('FramesHistoryLoader', () => {
  it('loads a shard once, returns null for a symbol it does not hold, and does not pin failures', async () => {
    let calls = 0;
    let fail = true;
    const fetcher = vi.fn(async () => {
      calls += 1;
      if (fail) throw new Error('offline');
      return response(200, shardText);
    });
    const loader = new FramesHistoryLoader({ fetcher: fetcher as unknown as typeof fetch, now: () => 0, urlOf: (index) => `/s/${index}` });
    expect((await loader.load('ZFRM')).history).toBeNull();
    fail = false;
    const loaded = await loader.load('ZFRM');
    expect(loaded.url).toBe('/s/28');
    expect(loaded.history?.annual.end).toHaveLength(11);
    await loader.load('ZFRM');
    expect(calls).toBe(2);
    // Another key of the same shard that the shard does not hold
    const other = ['ZA', 'ZB', 'ZC', 'ZD', 'ZE', 'ZF', 'ZG', 'ZH', 'ZI', 'ZJ', 'ZK', 'ZL'].flatMap((key) => (historyShard(key) === 28 ? [key] : []));
    for (const key of other) expect((await loader.load(key)).history).toBeNull();
    const missing = new FramesHistoryLoader({ fetcher: (async () => response(404, '')) as typeof fetch, urlOf: (index) => `/s/${index}` });
    expect((await missing.load('AAPL')).history).toBeNull();
  });
});

describe('P/E river on a frames-only history', () => {
  it('draws ten years with a provenance note', () => {
    const history = zfrm();
    const bars: PriceBar[] = [];
    const start = dateToSec('2016-10-03');
    const end = dateToSec('2026-10-05');
    for (let time = start, i = 0; time <= end; time += 7 * 86_400, i++) bars.push({ time, close: 40 * Math.pow(4, i / 521) * (1 + 0.08 * Math.sin(i / 13)) });
    const result = buildPeRiver(history, bars, { asOfSec: end, priceBasis: 'split-adjusted' });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.model.notes.join('')).toContain('SEC XBRL frames');
    expect(result.model.notes.join('')).toContain('分割依加權股數推定');
    const valid = result.model.points.filter((point) => point.pe !== null);
    expect(valid.length).toBeGreaterThan(400);
    expect(valid[0]!.time).toBeLessThan(dateToSec('2017-06-01'));
  });
});
