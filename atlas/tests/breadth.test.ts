import { describe, expect, it, vi } from 'vitest';
import { breadthTone, breadthUrl, fetchBreadth, linePath, parseBreadth } from '../src/report/breadth';

const ratio = (above: number, total: number, pct: number | null) => ({ above, total, pct });

const sample = {
  version: 1,
  generatedAt: '2026-10-10T08:00:00+00:00',
  markets: {
    tw: {
      asOf: '2026-10-09',
      universe: '台股上市櫃普通股（4碼、非0開頭）',
      segments: {
        all: { count: 1000, ma20: ratio(612, 982, 62.3), ma60: ratio(540, 975, 55.4) },
        twse: { count: 700, ma20: ratio(440, 690, 63.8), ma60: ratio(380, 686, 55.4) },
        tpex: { count: 300, ma20: ratio(172, 292, 58.9), ma60: ratio(160, 289, 55.4) },
      },
      history: {
        dates: ['2026-10-07', '2026-10-08', '2026-10-09'],
        ma20Pct: [60.1, 61.0, 62.3],
        ma60Pct: [54.0, 55.0, 55.4],
      },
    },
    us: {
      asOf: '2026-10-09',
      universe: 'S&P 500 成分股',
      segments: { all: { count: 503, ma20: ratio(300, 500, 60.0), ma60: ratio(250, 498, 50.2) } },
      history: { dates: ['2026-10-09'], ma20Pct: [60.0], ma60Pct: [50.2] },
    },
  },
};

describe('breadth schema', () => {
  it('parses the job output with both markets and the TW listing segments', () => {
    const result = parseBreadth(sample);
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const tw = result.data.markets.tw!;
    expect(tw.asOf).toBe('2026-10-09');
    expect(tw.segments.all.ma20).toEqual(ratio(612, 982, 62.3));
    expect(tw.segments.twse?.count).toBe(700);
    expect(tw.segments.tpex?.ma60.pct).toBe(55.4);
    expect(tw.history.dates).toHaveLength(3);
    expect(result.data.markets.us!.segments.twse).toBeNull();
    expect(result.data.generatedAt).toBe('2026-10-10T08:00:00+00:00');
  });

  it('tolerates unknown fields, numeric strings and aligned-but-broken history entries', () => {
    const result = parseBreadth({
      version: 1,
      extra: { anything: true },
      markets: {
        tw: {
          asOf: '2026-10-08',
          universe: 'u',
          extra: 1,
          segments: { all: { count: '5', ma20: { above: 2, total: 4, pct: '50.0' }, ma60: {} } },
          history: {
            dates: ['2026-10-07', 'bad-date', '2026-10-08'],
            ma20Pct: [40, 'x', 50],
            ma60Pct: [null],
          },
        },
        us: { universe: 'no segments at all' },
        jp: { segments: { all: { count: 1, ma20: {}, ma60: {} } }, history: {} },
      },
    });
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    expect(Object.keys(result.data.markets)).toEqual(['tw']);
    const tw = result.data.markets.tw!;
    expect(tw.segments.all.count).toBe(5);
    expect(tw.segments.all.ma20).toEqual(ratio(2, 4, 50));
    expect(tw.segments.all.ma60).toEqual(ratio(0, 0, null));
    expect(tw.history.dates).toEqual(['2026-10-07', '2026-10-08']);
    expect(tw.history.ma20Pct).toEqual([40, 50]);
    expect(tw.history.ma60Pct).toEqual([null, null]);
  });

  it('reports a document without any market as missing and a non-object as invalid', () => {
    expect(parseBreadth({ version: 1, markets: {} }).status).toBe('missing');
    expect(parseBreadth({ version: 1 }).status).toBe('missing');
    expect(parseBreadth('<!doctype html>').status).toBe('invalid');
    expect(parseBreadth([]).status).toBe('invalid');
  });
});

describe('breadth helpers', () => {
  it('locates breadth.json next to latest.json, honouring the report URL override', () => {
    expect(breadthUrl('/my-stock-screener/atlas/', undefined, 'https://example.github.io')).toBe(
      'https://example.github.io/my-stock-screener/report/breadth.json',
    );
    expect(breadthUrl('/', 'https://cdn.example.com/r/latest.json', 'https://x.test')).toBe(
      'https://cdn.example.com/r/breadth.json',
    );
  });

  it('classifies the percentage: below 20 is weak, above 80 is hot, boundaries are neutral', () => {
    expect(breadthTone(15)).toBe('weak');
    expect(breadthTone(20)).toBe('neutral');
    expect(breadthTone(50)).toBe('neutral');
    expect(breadthTone(80)).toBe('neutral');
    expect(breadthTone(80.1)).toBe('hot');
    expect(breadthTone(null)).toBe('none');
  });

  it('draws the series as an SVG path that breaks at missing values', () => {
    expect(linePath([10, 20, null, 30], 100, 100)).toBe('M0,90 L33.3,80 M100,70');
    expect(linePath([null, null], 100, 100)).toBe('');
    expect(linePath([], 100, 100)).toBe('');
  });
});

describe('fetchBreadth', () => {
  const response = (body: string, status = 200) =>
    new Response(body, { status, headers: { 'content-type': 'application/json' } });

  it('returns ok for a valid file and friendly states for 404, HTTP errors, network errors and bad JSON', async () => {
    const url = 'https://example.test/report/breadth.json';
    await expect(fetchBreadth(url, vi.fn(async () => response(JSON.stringify(sample))))).resolves.toMatchObject({
      status: 'ok',
      url,
    });
    await expect(fetchBreadth(url, vi.fn(async () => response('', 404)))).resolves.toMatchObject({ status: 'missing' });
    await expect(fetchBreadth(url, vi.fn(async () => response('', 500)))).resolves.toMatchObject({ status: 'error' });
    await expect(
      fetchBreadth(
        url,
        vi.fn(async () => {
          throw new TypeError('offline');
        }),
      ),
    ).resolves.toMatchObject({ status: 'error' });
    await expect(fetchBreadth(url, vi.fn(async () => response('<html>spa fallback</html>')))).resolves.toMatchObject({
      status: 'invalid',
    });
  });
});
