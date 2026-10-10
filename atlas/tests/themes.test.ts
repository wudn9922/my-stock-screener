import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { fetchThemes, parseThemes, themesUrl, type ThemesData } from '../src/report/themes';
import {
  MIN_VALID_FOR_RANK,
  chartData,
  dataAgeDays,
  defaultDirection,
  filterByParent,
  heat,
  metricValue,
  relativeTo,
  sortThemes,
  sparkGeometry,
  sparkLength,
  sparkValues,
  themeByKey,
  tileOrder,
} from '../src/pages/themesView';

const sample: unknown = JSON.parse(readFileSync('src/report/fixtures/themes.sample.json', 'utf8'));

function load(): ThemesData {
  const result = parseThemes(sample);
  if (result.status !== 'ok') throw new Error('fixture must parse');
  return result.data;
}

function response(body: string, status = 200) {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } });
}

describe('themes.json schema', () => {
  it('parses the Python job sample', () => {
    const data = load();
    expect(data.themes).toHaveLength(30);
    expect(data.asOf).toBe('2026-10-09');
    expect(data.dates).toHaveLength(126);
    expect(data.benchmark.symbol).toBe('SPY');
    expect(data.benchmark.series).toHaveLength(126);
    expect(data.parents.map((p) => p.order)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    for (const theme of data.themes) {
      expect(theme.series.length === 0 || theme.series.length === 126).toBe(true);
      expect(theme.description.length).toBeGreaterThan(20);
      expect(theme.constituents.length).toBe(theme.validCount);
    }
    const chips = data.themes.find((t) => t.key === 'ai-chips')!;
    expect(chips.etf?.symbol).toBe('SMH');
    expect(chips.constituents[0]).toMatchObject({ symbol: expect.any(String), name: expect.any(String), note: expect.any(String) });
  });

  it('keeps ranked themes first and unranked (too few valid names) last', () => {
    const data = load();
    const ranks = data.themes.map((t) => t.rank);
    const firstNull = ranks.indexOf(null);
    expect(firstNull).toBeGreaterThan(0);
    expect(ranks.slice(0, firstNull)).toEqual(Array.from({ length: firstNull }, (_, i) => i + 1));
    expect(data.themes.at(-1)).toMatchObject({ key: 'critical-minerals', validCount: 2 });
  });

  it('tolerates unknown fields, bad entries and bad numbers', () => {
    const result = parseThemes({
      version: 9,
      extra: true,
      dates: ['2026-10-08', 5, '2026-10-09'],
      benchmark: { symbol: 'SPY', returns: { d1: '1.5', w1: 'x', m1: null }, series: [100, 'a'] },
      parents: [{ key: 'a', name: 'A', order: 2 }, { name: 'no key' }, { key: 'b', name: 'B', order: 1 }],
      themes: [
        { key: 'ok', name: 'OK', parent: 'a', returns: { d1: 1, m1: Number.NaN }, series: [1, 2], constituents: [{ symbol: 'x', returns: {} }, { nope: 1 }] },
        { key: 'Bad Key!', name: 'drop me' },
        { key: 'ok', name: 'duplicate' },
        'junk',
        { key: 'later', name: 'later', etf: { symbol: 'XYZ', returns: { m3: 4 } }, rs: { m3: 'x' } },
      ],
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const { data } = result;
    expect(data.dates).toEqual(['2026-10-08', '2026-10-09']);
    expect(data.benchmark.returns).toMatchObject({ d1: 1.5, w1: null, m1: null, ytd: null });
    expect(data.benchmark.series).toEqual([]);
    expect(data.parents.map((p) => p.key)).toEqual(['b', 'a']);
    expect(data.themes.map((t) => t.key)).toEqual(['ok', 'later']);
    const ok = data.themes[0]!;
    expect(ok.returns.d1).toBe(1);
    expect(ok.returns.m1).toBeNull();
    expect(ok.constituents.map((c) => c.symbol)).toEqual(['X']);
    expect(ok.count).toBe(1);
    expect(data.themes[1]!.etf).toEqual({ symbol: 'XYZ', returns: expect.objectContaining({ m3: 4, d1: null }) });
    expect(data.themes[1]!.rs).toEqual({ m1: null, m3: null });
  });

  it('rejects documents without any usable theme', () => {
    expect(parseThemes(null).status).toBe('invalid');
    expect(parseThemes([]).status).toBe('invalid');
    expect(parseThemes({ themes: [] }).status).toBe('invalid');
    expect(parseThemes({ themes: [{ key: 'BAD' }] }).status).toBe('invalid');
  });

  it('turns fetch failures into friendly states', async () => {
    const url = 'http://localhost/report/themes.json';
    expect((await fetchThemes(url, vi.fn(async () => response('not found', 404)))).status).toBe('missing');
    expect((await fetchThemes(url, vi.fn(async () => response('<!doctype html>')))).status).toBe('missing');
    expect((await fetchThemes(url, vi.fn(async () => response('oops', 500)))).status).toBe('error');
    expect((await fetchThemes(url, vi.fn(async () => Promise.reject(new Error('offline'))))).status).toBe('error');
    expect((await fetchThemes(url, vi.fn(async () => response('{"themes":[]}')))).status).toBe('invalid');
    expect((await fetchThemes(url, vi.fn(async () => response(JSON.stringify(sample))))).status).toBe('ok');
  });

  it('derives the file URL next to latest.json', () => {
    expect(themesUrl()).toMatch(/\/report\/themes\.json$/);
  });
});

describe('themes view helpers', () => {
  const data = load();

  it('computes relative strength like the Python job', () => {
    expect(relativeTo(10, 5)).toBeCloseTo(4.7619, 3);
    expect(relativeTo(-5, -5)).toBeCloseTo(0, 6);
    expect(relativeTo(null, 5)).toBeNull();
    expect(relativeTo(5, null)).toBeNull();
    const chips = data.themes.find((t) => t.key === 'ai-chips')!;
    expect(metricValue(chips, data, 'm3', 'return')).toBe(chips.returns.m3);
    // The file's own 3-month RS (rounded) matches the formula applied to the file's returns.
    expect(metricValue(chips, data, 'm3', 'rel')!).toBeCloseTo(chips.rs.m3!, 1);
  });

  it('maps values to a diverging heat scale clipped per period', () => {
    expect(heat(null, 'm1')).toEqual({ side: 'flat', mix: 0 });
    expect(heat(0, 'm1')).toEqual({ side: 'flat', mix: 0 });
    expect(heat(7, 'm1')).toMatchObject({ side: 'up', mix: 42 });
    expect(heat(-7, 'm1')).toMatchObject({ side: 'down', mix: 42 });
    expect(heat(500, 'm1')).toEqual({ side: 'up', mix: 70 });
    expect(heat(-500, 'm1')).toEqual({ side: 'down', mix: 70 });
    // The same move is hotter on a shorter period.
    expect(heat(3, 'd1').mix).toBeGreaterThan(heat(3, 'm3').mix);
  });

  it('orders tiles by the chosen metric and sinks thin themes', () => {
    const order = tileOrder(data.themes, data, 'm1', 'return');
    const values = order.map((t) => t.returns.m1!);
    const thin = order.findIndex((t) => t.validCount < MIN_VALID_FOR_RANK);
    expect(thin).toBe(order.length - 1);
    expect(values.slice(0, thin)).toEqual([...values.slice(0, thin)].sort((a, b) => b - a));
    const relative = tileOrder(data.themes, data, 'm1', 'rel');
    expect(relative.map((t) => t.key)).toHaveLength(data.themes.length);
    expect(relative[0]!.returns.m1).toBeGreaterThan(relative[5]!.returns.m1!);
  });

  it('sorts the list with missing values last whichever the direction', () => {
    const themes = data.themes.map((t, i) => (i === 3 ? { ...t, momentum: null } : t));
    const down = sortThemes(themes, data, 'momentum', 'desc');
    const up = sortThemes(themes, data, 'momentum', 'asc');
    expect(down.at(-1)!.momentum).toBeNull();
    expect(up.at(-1)!.momentum).toBeNull();
    expect(down[0]!.momentum!).toBeGreaterThanOrEqual(down[1]!.momentum!);
    expect(sortThemes(data.themes, data, 'rank', 'asc')[0]!.rank).toBe(1);
    expect(sortThemes(data.themes, data, 'name', 'asc')).toHaveLength(30);
    expect(defaultDirection('rank')).toBe('asc');
    expect(defaultDirection('m3')).toBe('desc');
  });

  it('filters by parent and finds themes by key', () => {
    const semis = filterByParent(data.themes, 'semis');
    expect(semis.map((t) => t.key).sort()).toEqual(['ai-chips', 'analog-auto', 'foundry', 'memory', 'semi-equip']);
    expect(filterByParent(data.themes, null)).toHaveLength(30);
    expect(themeByKey(data, 'nuclear')?.name).toBe('核能與鈾');
    expect(themeByKey(data, 'nope')).toBeUndefined();
    expect(themeByKey(data, undefined)).toBeUndefined();
  });

  it('sizes sparklines per period and builds sparkline paths', () => {
    expect(sparkLength('d1', data.dates)).toBe(22);
    expect(sparkLength('m1', data.dates)).toBe(22);
    expect(sparkLength('m3', data.dates)).toBe(64);
    expect(sparkLength('m6', data.dates)).toBe(126);
    const ytd = sparkLength('ytd', data.dates);
    expect(ytd).toBeGreaterThanOrEqual(22);
    expect(ytd).toBe(126); // the 126-day window starts after 2026-01-01
    const chips = data.themes.find((t) => t.key === 'ai-chips')!;
    expect(sparkValues(chips, data, 22, 'return')).toHaveLength(22);
    const relative = sparkValues(chips, data, 22, 'rel');
    expect(relative).toHaveLength(22);
    expect(relative[0]).toBeCloseTo((chips.series.at(-22)! / data.benchmark.series.at(-22)!) * 100, 6);
    const geometry = sparkGeometry([1, 3, 2], 100, 30);
    expect(geometry.line.startsWith('M2 ')).toBe(true);
    expect(geometry.last).toEqual({ x: 98, y: 15 });
    expect(geometry.area.endsWith('Z')).toBe(true);
    expect(sparkGeometry([1], 100, 30)).toEqual({ line: '', area: '', last: null });
    expect(sparkGeometry([5, 5, 5], 100, 30).last).not.toBeNull();
  });

  it('rebases the detail chart to 100 for each range', () => {
    const chips = data.themes.find((t) => t.key === 'ai-chips')!;
    for (const [range, points] of [['1m', 22], ['3m', 64], ['6m', 126]] as const) {
      const chart = chartData(chips, data, range);
      expect(chart.dates).toHaveLength(points);
      expect(chart.theme[0]).toBe(100);
      expect(chart.bench[0]).toBe(100);
      expect(chart.theme.at(-1)!).toBeCloseTo((chips.series.at(-1)! / chips.series.at(-points)!) * 100, 6);
    }
    expect(chartData({ ...chips, series: [] }, data, '3m')).toEqual({ dates: [], theme: [], bench: [] });
  });

  it('measures how old the data is', () => {
    expect(dataAgeDays('2026-10-09', new Date('2026-10-12T03:00:00Z'))).toBe(3);
    expect(dataAgeDays('2026-10-09', new Date('2026-10-09T23:00:00Z'))).toBe(0);
    expect(dataAgeDays(null, new Date())).toBeNull();
    expect(dataAgeDays('nope', new Date())).toBeNull();
  });
});
