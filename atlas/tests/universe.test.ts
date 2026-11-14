import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { parseMetrics } from '../src/report/schema';
import {
  UNIVERSE_EXTRA_METRIC_FIELDS,
  UNIVERSE_FIELDS,
  UNIVERSE_GROUP_KEYS,
  fetchUniverse,
  parseUniverse,
  universeGroup,
  universeMarketOf,
  universeUrl,
} from '../src/report/universe';
import { maDistance, maValue } from '../src/ui/format';

const fixture = JSON.parse(readFileSync('tests/fixtures/screener/universe.json', 'utf8')) as {
  fields: string[];
  markets: Record<string, { rows: unknown[][] }>;
};

function response(body: string, status = 200) {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } });
}

describe('universe.json', () => {
  it('turns the column rows into report-shaped items for both markets', () => {
    const result = parseUniverse(fixture);
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const { TW, US } = result.universe.markets;
    expect(TW!.items).toHaveLength(fixture.markets.TW!.rows.length);
    expect(US!.items).toHaveLength(fixture.markets.US!.rows.length);
    expect(TW).toMatchObject({ benchmark: '^TWII', asOf: '2026-10-08', universe: '台股流動性名單（當日成交≥100萬股）' });
    expect(US!.benchmark).toBe('SPY');
    expect(result.universe.reportDate).toBe('2026-10-08');

    const raw = fixture.markets.TW!.rows[0]!;
    const item = TW!.items[0]!;
    expect(item).toMatchObject({ symbol: raw[0], name: raw[1], close: raw[2], changePct: raw[3], volume: raw[4], maList: [20, 50, 200], note: null, asOf: '2026-10-08' });
    const col = (field: string) => raw[fixture.fields.indexOf(field)];
    expect(item.metrics).toMatchObject({ r5: raw[5], rs63: col('rs63'), rsRank: col('rsRank'), hiBars: col('hiBars'), bars: col('hiBars'), rsBench: '^TWII', ema20: col('ema20'), volVsPrev: col('volVsPrev') });
    expect(item.metrics).not.toHaveProperty('volRatio');
    // MA values are derived back from the distances, so the row pills show the same numbers.
    for (const [period, column] of [[20, 8], [50, 9], [200, 10]] as const) {
      const distance = maDistance(item.close, maValue(item.maValues, period));
      if (raw[column] === null) expect(distance).toBeNull();
      else expect(distance).toBeCloseTo(raw[column] as number, 6);
    }
  });

  it('reads columns by name, so a reordered or extended field list still works', () => {
    const fields: string[] = [...UNIVERSE_FIELDS].reverse();
    fields.push('extra');
    const row = Object.fromEntries(UNIVERSE_FIELDS.map((field, i) => [field, fixture.markets.US!.rows[0]![i]]));
    const result = parseUniverse({
      fields,
      markets: { US: { benchmark: 'SPY', rows: [[...fields.slice(0, -1).map((field) => row[field]), 'x']] } },
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(result.universe.markets.US!.items[0]).toMatchObject({ symbol: 'AAPL', close: row.close, metrics: { rsRank: row.rsRank } });
    expect(result.universe.markets.TW).toBeUndefined();
  });

  it('reads average volume in shares and keeps old files missing it as null', () => {
    const result = parseUniverse({
      fields: ['symbol', 'volume', 'volumeAvg20'],
      markets: { TW: { rows: [['2330.TW', 900000, 123456.75]] } },
    });
    if (result.status !== 'ok') throw new Error('average-volume row must parse');
    expect(result.universe.markets.TW!.items[0]!.metrics!.volumeAvg20).toBe(123456.75);
    const old = parseUniverse(fixture);
    if (old.status !== 'ok') throw new Error('old fixture must parse');
    expect(old.universe.markets.TW!.items[0]!.metrics!.volumeAvg20).toBeNull();
  });

  it('reads the optional rsScore / atrPct / range20 columns and leaves them null in old files', () => {
    expect(UNIVERSE_EXTRA_METRIC_FIELDS).toEqual(['rsScore', 'atrPct', 'range20']);
    const fields = [...UNIVERSE_FIELDS, ...UNIVERSE_EXTRA_METRIC_FIELDS];
    const base = UNIVERSE_FIELDS.map((field) => {
      const at = fixture.fields.indexOf(field);
      return at < 0 ? null : fixture.markets.US!.rows[0]![at];
    });
    const result = parseUniverse({
      fields,
      markets: {
        US: {
          benchmark: 'SPY',
          rows: [
            [...base, 12.34, 2.5, 18.75],
            ['MSFT', ...base.slice(1), '7.5', 'x', null],
            ['NVDA', ...base.slice(1), Infinity, -1, 0],
          ],
        },
      },
    });
    if (result.status !== 'ok') throw new Error('extended columns must parse');
    const [first, second, third] = result.universe.markets.US!.items;
    expect(first!.metrics).toMatchObject({ rsScore: 12.34, atrPct: 2.5, range20: 18.75 });
    // Numeric strings are read, junk and nulls become null, and rsRank keeps its own column.
    expect(second!.metrics).toMatchObject({ rsScore: 7.5, atrPct: null, range20: null, rsRank: first!.metrics!.rsRank });
    expect(third!.metrics).toMatchObject({ rsScore: null, atrPct: -1, range20: 0 });
    // The published file is still in the old format: every row parses, with the new fields null.
    const old = parseUniverse(fixture);
    if (old.status !== 'ok') throw new Error('old fixture must parse');
    for (const market of Object.values(old.universe.markets))
      for (const item of market!.items) expect(item.metrics).toMatchObject({ rsScore: null, atrPct: null, range20: null });
    expect(old.universe.markets.TW!.items[0]!.metrics).toMatchObject({ rsRank: fixture.markets.TW!.rows[0]![fixture.fields.indexOf('rsRank')] });
  });

  it('parseMetrics accepts the new fields and defaults them to null', () => {
    expect(parseMetrics({ rsScore: 3.2, atrPct: '1.5', range20: 9 })).toMatchObject({ rsScore: 3.2, atrPct: 1.5, range20: 9 });
    expect(parseMetrics({ rsScore: 'abc', atrPct: undefined, range20: NaN })).toMatchObject({ rsScore: null, atrPct: null, range20: null });
    expect(parseMetrics({ rsRank: 80, rs63: 4 })).toMatchObject({ rsRank: 80, rs63: 4, rsScore: null, atrPct: null, range20: null });
  });

  it('reads the optional profile columns and the full-market scope', () => {
    const fields = [...UNIVERSE_FIELDS, 'sector', 'industry', 'marketCap'];
    const base = UNIVERSE_FIELDS.map(field => {
      const at = (fixture.fields as string[]).indexOf(field);
      return at < 0 ? null : fixture.markets.US!.rows[0]![at];
    });
    const result = parseUniverse({
      fields,
      markets: {
        US: {
          benchmark: 'SPY',
          scope: 'full',
          rows: [
            [...base, 'Technology', 'Computer Manufacturing', 3.1e12],
            ['MSFT', ...base.slice(1), null, '  ', -5],
          ],
        },
      },
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const us = result.universe.markets.US!;
    expect(us.scope).toBe('full');
    expect(us.items[0]!.profile).toEqual({ sector: 'Technology', industry: 'Computer Manufacturing', marketCap: 3.1e12 });
    // Blank text and non-positive caps carry no profile at all.
    expect(us.items[1]!.profile).toBeUndefined();
    // main.py's files have neither: the liquidity scope and no profiles.
    const plain = parseUniverse(fixture);
    if (plain.status !== 'ok') throw new Error('fixture must parse');
    expect(plain.universe.markets.US!.scope).toBe('liquid');
    expect(plain.universe.markets.US!.items.every((item) => item.profile === undefined)).toBe(true);
  });

  it('drops bad rows and duplicates, keeps nulls, and defaults missing fields', () => {
    const result = parseUniverse({
      markets: {
        TW: {
          rows: [
            ['2330.tw', '台積電', 1000, 1.2, 3e7, null, null, null, 5, null, null, null, null, 'weird', null, null, 120],
            ['2330.TW', 'dup'],
            ['<script>', 'x'],
            'not a row',
            [42],
            [],
          ],
        },
        US: { rows: 'nope' },
        JP: { rows: [['7203.T']] },
      },
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const tw = result.universe.markets.TW!;
    expect(tw.benchmark).toBe('^TWII');
    expect(tw.universe).toBe('台股');
    expect(tw.items).toHaveLength(1);
    expect(tw.items[0]).toMatchObject({ symbol: '2330.TW', maValues: { 20: 1000 / 1.05 }, metrics: { trend: null, ma: { 20: 5, 50: null }, hiBars: 120, rsRank: null } });
    expect(Object.keys(tw.items[0]!.maValues)).toEqual(['20']);
    expect(result.universe.markets.US).toBeUndefined();
  });

  it('reports unusable documents instead of throwing', () => {
    expect(parseUniverse(null).status).toBe('invalid');
    expect(parseUniverse([1]).status).toBe('invalid');
    expect(parseUniverse({ markets: [] }).status).toBe('invalid');
    expect(parseUniverse({ fields: ['name'], markets: { TW: { rows: [['x']] } } }).status).toBe('invalid');
    expect(parseUniverse({ version: 1, markets: {} }).status).toBe('missing');
    expect(parseUniverse({ markets: { TW: { rows: [] } } }).status).toBe('missing');
  });

  it('fetches universe.json next to latest.json and maps failures to friendly states', async () => {
    expect(universeUrl('https://x.io/my-stock-screener/report/latest.json')).toBe('https://x.io/my-stock-screener/report/universe.json');
    const url = 'http://h/report/universe.json';
    const ok = vi.fn(async () => response(JSON.stringify(fixture)));
    expect((await fetchUniverse(url, ok)).status).toBe('ok');
    expect(ok).toHaveBeenCalledWith(url, expect.objectContaining({ cache: 'no-cache' }));
    expect((await fetchUniverse(url, vi.fn(async () => response('no', 404)))).status).toBe('missing');
    expect((await fetchUniverse(url, vi.fn(async () => response('no', 500)))).status).toBe('error');
    expect((await fetchUniverse(url, vi.fn(async () => { throw new TypeError('offline'); }))).status).toBe('error');
    expect((await fetchUniverse(url, vi.fn(async () => response('<!doctype html>')))).status).toBe('invalid');
  });

  it('exposes each market as a screener group with its own route key', () => {
    const result = parseUniverse(fixture);
    if (result.status !== 'ok') throw new Error('fixture');
    const group = universeGroup(result.universe.markets.US!);
    expect(group).toMatchObject({ key: 'universe-us', market: 'US', maList: [20, 50, 200] });
    expect(universeMarketOf(UNIVERSE_GROUP_KEYS.TW)).toBe('TW');
    expect(universeMarketOf('us_g1')).toBeNull();
    expect(universeMarketOf(undefined)).toBeNull();
  });
});
