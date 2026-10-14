import { describe, expect, it, vi } from 'vitest';
import {
  chipsForSymbol,
  fetchTwChips,
  formatCountChange,
  formatPoints,
  isChipsPageSymbol,
  isForeignStale,
  isHoldersStale,
  monthDay,
  parseTwChips,
  sparkline,
  twChipsUrl,
  weekdaysBetween,
  type ChipsFile,
} from '../src/report/twChips';

const sample = {
  version: 1,
  generatedAt: '2026-10-14T11:41:00+00:00',
  sources: {
    twse: { label: '證交所', asOf: '2026-10-14', status: 'ok', message: null },
    tpex: { label: '櫃買中心', asOf: null, status: 'unavailable', message: '資料來源暫時無法取得' },
    tdcc: { label: '集保結算所', asOf: '2026-10-10', status: 'ok', message: null },
  },
  axes: {
    foreign: { twse: ['2026-10-12', '2026-10-13', '2026-10-14'], tpex: [] },
    holders: ['2026-10-03', '2026-10-10'],
  },
  stocks: {
    '2330.TW': {
      foreign: { pct: 73.27, asOf: '2026-10-14', d1: 0.05, d1Date: '2026-10-13', m1: null, m1Date: null, series: [73.1, 73.22, 73.27] },
      holders: { count: 1_200_000, asOf: '2026-10-10', wow: -3000, wowDate: '2026-10-03', series: [1_203_000, 1_200_000] },
    },
    '6488.TWO': { holders: { count: 20_000, asOf: '2026-10-10', wow: null, wowDate: null, series: [null, 20_000] } },
  },
};

function load(raw: unknown): ChipsFile {
  const result = parseTwChips(raw);
  if (result.status !== 'ok') throw new Error(`expected ok, got ${result.status}`);
  return result.data;
}

describe('tw-chips schema', () => {
  it('parses the job output and reads one stock', () => {
    const chips = chipsForSymbol(load(sample), '2330.TW');
    expect(chips.foreign).toMatchObject({ pct: 73.27, asOf: '2026-10-14', d1: 0.05, m1: null });
    expect(chips.foreign?.dates).toEqual(['2026-10-12', '2026-10-13', '2026-10-14']);
    expect(chips.foreign?.series).toEqual([73.1, 73.22, 73.27]);
    expect(chips.holders).toMatchObject({ count: 1_200_000, wow: -3000 });
    expect(chips.foreignSourceKey).toBe('twse');
    expect(chips.holdersSource?.asOf).toBe('2026-10-10');
  });

  it('uses the OTC source for .TWO and leaves foreign empty when only holders exist', () => {
    const chips = chipsForSymbol(load(sample), '6488.TWO');
    expect(chips.foreign).toBeNull();
    expect(chips.foreignSourceKey).toBe('tpex');
    expect(chips.foreignSource?.status).toBe('unavailable');
    expect(chips.holders?.series).toEqual([null, 20_000]);
  });

  it('returns an empty result for an unknown symbol instead of throwing', () => {
    const chips = chipsForSymbol(load(sample), '9999.TW');
    expect(chips.foreign).toBeNull();
    expect(chips.holders).toBeNull();
  });

  it('is tolerant: strings as numbers, bad entries, out-of-range values, unknown fields', () => {
    const file = load({
      generatedAt: 5,
      extra: 1,
      sources: { twse: { status: 'weird' }, tdcc: 'nope' },
      axes: { foreign: { twse: ['2026-10-13', '2026-10-14'] }, holders: ['not a date'] },
      stocks: {
        '2330.TW': { foreign: { pct: '73.27', asOf: '2026-10-14', d1: '0.05', series: ['73.1', '150', '--'] }, holders: { count: '1,200', asOf: '2026-10-10' } },
        '2317.TW': 'garbage',
        '2454.TW': { foreign: { pct: 120, asOf: '2026-10-14' }, holders: { count: -5, asOf: '2026-10-10' } },
        '1101.TW': { foreign: { pct: 10, asOf: 'yesterday' } },
      },
    });
    expect(file.generatedAt).toBeNull();
    expect(file.sources.twse?.status).toBe('unknown');
    expect(file.sources.tdcc).toBeUndefined();
    expect(file.axes.holders).toEqual([]);
    const tsmc = chipsForSymbol(file, '2330.TW');
    expect(tsmc.foreign?.pct).toBe(73.27);
    expect(tsmc.foreign?.d1).toBe(0.05);
    expect(tsmc.foreign?.series).toEqual([null, null]); // length 3 vs axis 2: trimmed to the newest two; 150 and `--` are dropped
    expect(tsmc.holders).toBeNull(); // "1,200" is not a number
    for (const symbol of ['2317.TW', '2454.TW', '1101.TW']) {
      const chips = chipsForSymbol(file, symbol);
      expect(chips.foreign).toBeNull();
      expect(chips.holders).toBeNull();
    }
  });

  it('aligns a series to the end of its axis when lengths differ', () => {
    const file = load({
      axes: { foreign: { twse: ['2026-10-13', '2026-10-14'] }, holders: [] },
      stocks: { '2330.TW': { foreign: { pct: 73.3, asOf: '2026-10-14', series: [72, 73, 73.3] } } },
    });
    const foreign = chipsForSymbol(file, '2330.TW').foreign;
    expect(foreign?.dates).toEqual(['2026-10-13', '2026-10-14']);
    expect(foreign?.series).toEqual([73, 73.3]);
  });

  it('classifies unusable bodies without throwing', () => {
    expect(parseTwChips(null).status).toBe('invalid');
    expect(parseTwChips([]).status).toBe('invalid');
    expect(parseTwChips('x').status).toBe('invalid');
    expect(parseTwChips({}).status).toBe('missing');
    expect(parseTwChips({ stocks: {} }).status).toBe('missing');
    expect(parseTwChips({ stocks: [] }).status).toBe('missing');
  });
});

describe('tw-chips fetch', () => {
  const ok = (body: string, status = 200) => vi.fn(async () => new Response(body, { status })) as unknown as typeof fetch;
  it('sits next to latest.json', () => {
    expect(twChipsUrl('/my-stock-screener/atlas/', undefined, 'https://example.test')).toBe('https://example.test/my-stock-screener/report/tw-chips.json');
    expect(twChipsUrl('/x/atlas/', 'https://cdn.test/a/latest.json', 'https://example.test')).toBe('https://cdn.test/a/tw-chips.json');
  });
  it('maps 404, other HTTP errors, HTML bodies and network failures to friendly states', async () => {
    expect((await fetchTwChips('u', ok('', 404))).status).toBe('missing');
    const failed = await fetchTwChips('u', ok('', 500));
    expect(failed.status).toBe('error');
    expect((await fetchTwChips('u', ok('<!doctype html>'))).status).toBe('invalid');
    const thrown = (async () => {
      throw new TypeError('offline');
    }) as unknown as typeof fetch;
    expect((await fetchTwChips('u', thrown)).status).toBe('error');
    expect((await fetchTwChips('u', ok(JSON.stringify(sample)))).status).toBe('ok');
  });
});

describe('tw-chips helpers', () => {
  it('only Taiwan stock pages get the chips section', () => {
    for (const symbol of ['2330.TW', '6488.TWO', '2330.tw', '00878.TW']) expect(isChipsPageSymbol(symbol)).toBe(true);
    for (const symbol of ['AAPL', 'TSM', '^TWII', '', undefined, null, '2330']) expect(isChipsPageSymbol(symbol)).toBe(false);
  });

  it('counts weekdays between dates', () => {
    expect(weekdaysBetween('2026-10-09', '2026-10-12')).toBe(1); // Fri → Mon
    expect(weekdaysBetween('2026-10-05', '2026-10-12')).toBe(5);
    expect(weekdaysBetween('2026-10-12', '2026-10-12')).toBe(0);
    expect(weekdaysBetween('2026-10-14', '2026-10-12')).toBe(0);
    expect(weekdaysBetween('bad', '2026-10-12')).toBe(0);
  });

  it('flags foreign data older than 5 weekdays and holder data older than 10 days', () => {
    const today = new Date(2026, 9, 14); // Wed 2026-10-14, local
    expect(isForeignStale('2026-10-14', today)).toBe(false);
    expect(isForeignStale('2026-10-07', today)).toBe(false); // exactly 5 weekdays
    expect(isForeignStale('2026-10-06', today)).toBe(true);
    expect(isHoldersStale('2026-10-04', today)).toBe(false); // 10 days
    expect(isHoldersStale('2026-10-03', today)).toBe(true);
  });

  it('formats changes without NaN', () => {
    expect(formatPoints(0.034)).toBe('+0.03 個百分點');
    expect(formatPoints(-0.2)).toBe('-0.20 個百分點');
    expect(formatPoints(0)).toBe('0.00 個百分點');
    expect(formatPoints(null)).toBe('—');
    expect(formatPoints(Number.NaN)).toBe('—');
    expect(formatCountChange(-3000, 1_200_000)).toBe('-3,000 人（-0.25%）');
    expect(formatCountChange(1200, 5000)).toBe('+1,200 人（+31.58%）');
    expect(formatCountChange(0, 5000)).toBe('0 人（0.00%）');
    expect(formatCountChange(null, 5000)).toBe('—');
    expect(monthDay('2026-10-03')).toBe('10/03');
    expect(monthDay(null)).toBe('—');
  });

  it('builds a sparkline over the series range and breaks at gaps', () => {
    expect(sparkline([1])).toBeNull();
    expect(sparkline([null, 5, null])).toBeNull();
    const line = sparkline([10, 20, null, 15], 300, 64, 4)!;
    expect(line.min).toBe(10);
    expect(line.max).toBe(20);
    expect(line.points).toBe(3);
    expect(line.d.split('M').length - 1).toBe(2); // pen lifted at the gap
    expect(line.d.startsWith('M0,60')).toBe(true); // minimum sits at the bottom padding
    const flat = sparkline([5, 5, 5], 300, 64)!;
    expect(flat.d).toBe('M0,32 L150,32 L300,32');
    expect(flat.d).not.toContain('NaN');
  });
});
