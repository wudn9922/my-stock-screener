import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
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
    expect(item.metrics).toMatchObject({ r5: raw[5], rs63: raw[17], rsRank: raw[18], hiBars: raw[15], bars: raw[15], rsBench: '^TWII' });
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

  it('drops bad rows and duplicates, keeps nulls, and defaults missing fields', () => {
    const result = parseUniverse({
      markets: {
        TW: {
          rows: [
            ['2330.tw', '台積電', 1000, 1.2, 3e7, null, null, null, 5, null, null, null, 'weird', null, null, 120],
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
