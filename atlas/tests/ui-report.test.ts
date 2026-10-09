import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { fetchReport, parseReport, reportUrl } from '../src/report/loadReport';
import { isEmptyReport, reportSchema } from '../src/report/schema';
import { hasStructuredRows, parseLineText } from '../src/report/lineText';

const sample: unknown = JSON.parse(readFileSync('src/report/fixtures/latest.sample.json', 'utf8'));

function response(body: string, status = 200) {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } });
}

describe('report schema', () => {
  it('parses the Python job sample with every page section', () => {
    const result = parseReport(sample);
    expect(result.status).toBe('ok');
    if (result.status !== 'ok') return;
    const report = result.report;
    expect(report.markets.map((m) => m.key)).toEqual(expect.arrayContaining(['tw', 'us']));
    const tw = report.markets.find((m) => m.key === 'tw')!;
    expect(tw.indices[0]).toMatchObject({ symbol: '^TWII', maList: [23, 29, 61] });
    expect(tw.indices[0]!.maValues['23']).toBeTypeOf('number');
    expect(report.worldIndices.length).toBeGreaterThan(5);
    expect(report.groups.some((g) => g.kind === 'scan')).toBe(true);
    // US items without a name fall back to the ticker.
    const us = report.groups.find((g) => g.market === 'US' && g.items.length)!;
    expect(us.items.every((item) => item.name.length > 0)).toBe(true);
  });

  it('tolerates unknown fields, missing optionals and bad entries without failing the report', () => {
    const parsed = reportSchema.parse({
      version: 2,
      extra: { anything: true },
      markets: [
        { key: 'TW', name: '台灣', indices: [{ symbol: '^twii', close: '22,000', trend: 'BULL' }, { name: 'no symbol' }] },
        { key: 'xx', name: 'unknown market' },
        { key: 'tw', name: 'duplicate' },
      ],
      worldIndices: 'not a list',
      groups: [
        { key: 'tw_g1', name: '台股-權值', market: 'tw', kind: 'weird', items: [{ symbol: '2330.TW', close: '1255.5', changePct: null, maList: [20, '60', -1, 20], maValues: { 20: '1200', bad: 'x' } }, 42] },
        { key: 'tw_g1', name: 'dup', items: [] },
        { name: 'missing key' },
      ],
      sectors: [{ symbol: 'XLK', rank: '1' }],
    });
    expect(parsed.markets).toHaveLength(1);
    expect(parsed.markets[0]).toMatchObject({ key: 'tw', lineText: '', flag: '' });
    expect(parsed.markets[0]!.indices).toEqual([
      expect.objectContaining({ symbol: '^TWII', name: '^TWII', close: null, trend: 'bull', maList: [], scoreLabel: null }),
    ]);
    expect(parsed.worldIndices).toEqual([]);
    expect(parsed.groups).toHaveLength(1);
    expect(parsed.groups[0]).toMatchObject({ market: 'TW', kind: 'fixed' });
    expect(parsed.groups[0]!.items[0]).toMatchObject({
      symbol: '2330.TW',
      close: 1255.5,
      changePct: null,
      maList: [20, 60],
      maValues: { 20: 1200 },
      note: null,
    });
    expect(parsed.sectors[0]).toMatchObject({ symbol: 'XLK', rank: 1, changePct: null });
    expect(parsed.lineMessages).toEqual({ index: null, sectors: null });
  });

  it('treats non-objects and empty documents as unusable', () => {
    expect(parseReport(null).status).toBe('invalid');
    expect(parseReport([1, 2]).status).toBe('invalid');
    expect(parseReport('text').status).toBe('invalid');
    expect(parseReport({ version: 1 }).status).toBe('missing');
    expect(isEmptyReport(reportSchema.parse({}))).toBe(true);
  });
});

describe('report loader', () => {
  it('resolves latest.json next to the Atlas folder, with an override', () => {
    expect(reportUrl('/my-stock-screener/atlas/', undefined, 'https://wudn9922.github.io')).toBe(
      'https://wudn9922.github.io/my-stock-screener/report/latest.json',
    );
    expect(reportUrl('/my-stock-screener/atlas', undefined, 'https://x.io')).toBe('https://x.io/my-stock-screener/report/latest.json');
    expect(reportUrl('/', undefined, 'http://localhost:5173')).toBe('http://localhost:5173/report/latest.json');
    expect(reportUrl('/a/', '/fixtures/r.json', 'http://h')).toBe('http://h/fixtures/r.json');
  });

  it('maps fetch outcomes to friendly states and never throws', async () => {
    const url = 'http://h/report/latest.json';
    expect((await fetchReport(url, vi.fn(async () => response(JSON.stringify(sample))))).status).toBe('ok');
    expect(await fetchReport(url, vi.fn(async () => response('nope', 404)))).toMatchObject({ status: 'missing', message: '今日報告尚未產生。' });
    expect((await fetchReport(url, vi.fn(async () => response('boom', 503)))).status).toBe('error');
    expect((await fetchReport(url, vi.fn(async () => { throw new TypeError('offline'); }))).status).toBe('error');
    // GitHub Pages SPA fallback or a truncated upload.
    expect((await fetchReport(url, vi.fn(async () => response('<!doctype html><html></html>')))).status).toBe('invalid');
    expect((await fetchReport(url, vi.fn(async () => response('{"version":1,')))).status).toBe('invalid');
  });

  it('requests without the HTTP cache so a new daily report shows up', async () => {
    const fetcher = vi.fn(async () => response(JSON.stringify(sample)));
    await fetchReport('http://h/r.json', fetcher);
    expect(fetcher).toHaveBeenCalledWith('http://h/r.json', expect.objectContaining({ cache: 'no-cache' }));
  });
});

describe('LINE text', () => {
  const text = [
    '🌍 2026-10-05 全球大盤多空量化報告',
    '========================',
    '【 🇹🇼 台灣市場 】',
    '💡 台灣加權指數',
    '   ├ 均線: 偏空 (-2/3MA - 23/29/61)',
    '   └  多頭趨勢中的空頭走勢',
    '🔺 美國道瓊工業',
    '   ├ 均線: 看多 (3/3MA - 20/23/55)',
    '⚪ 台灣櫃買指數(OTC): 數據不足無法分析',
    '',
    '🟢 【強勢前三名】',
    '1. XLU 公用事業',
    '   ├ 13週：+25.93%',
    '   └ 週線多頭｜上漲縮量',
    '🔗 完整圖表：',
    'https://example.com/report',
  ].join('\n');

  it('parses headings, status rows, details and ranked rows without rewording', () => {
    const blocks = parseLineText(text);
    expect(blocks[0]).toEqual({ type: 'heading', text: '🌍 2026-10-05 全球大盤多空量化報告', tone: 'info' });
    expect(blocks[1]).toEqual({ type: 'divider' });
    expect(blocks[2]).toMatchObject({ type: 'heading', text: '🇹🇼 台灣市場' });
    expect(blocks[3]).toEqual({
      type: 'status',
      icon: '💡',
      tone: 'pullback',
      title: '台灣加權指數',
      details: [
        { label: '均線', value: '偏空 (-2/3MA - 23/29/61)' },
        { label: null, value: '多頭趨勢中的空頭走勢' },
      ],
    });
    expect(blocks[4]).toMatchObject({ icon: '🔺', tone: 'bull' });
    expect(blocks[5]).toMatchObject({ icon: '⚪', title: '台灣櫃買指數(OTC)', details: [{ label: null, value: '數據不足無法分析' }] });
    expect(blocks[6]).toMatchObject({ type: 'heading', text: '🟢 強勢前三名', tone: 'bull' });
    expect(blocks[7]).toMatchObject({ type: 'status', icon: '1.', title: 'XLU 公用事業', details: [{ label: '13週', value: '+25.93%' }, { label: null, value: '週線多頭｜上漲縮量' }] });
    expect(blocks.at(-1)).toEqual({ type: 'text', text: 'https://example.com/report' });
    expect(hasStructuredRows(blocks)).toBe(true);
  });

  it('falls back to plain text for unstructured messages', () => {
    const blocks = parseLineText('今日休市\r\n明日恢復');
    expect(blocks).toEqual([
      { type: 'text', text: '今日休市' },
      { type: 'text', text: '明日恢復' },
    ]);
    expect(hasStructuredRows(blocks)).toBe(false);
    expect(parseLineText('')).toEqual([]);
  });
});
