import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Page, Route } from '@playwright/test';

/**
 * Test doubles for the screener site: the daily report (Python sample + a long scan list), the symbol
 * directory, valuation files, and the Supabase market-chart function (deterministic synthetic,
 * Yahoo-shaped chart JSON — not market data).
 */
const root = resolve(import.meta.dirname, '..');
const readJson = (path: string) => JSON.parse(readFileSync(resolve(root, path), 'utf8')) as unknown;

type Json = Record<string, unknown>;
const taiwanStocks = (readJson('public/symbols/taiwan.json') as { entries: { symbol: string; name: string; market: string; kind: string }[] }).entries.filter(
  (entry) => entry.kind === 'stock',
);

export function buildReport(longList = 600): Json {
  const report = structuredClone(readJson('src/report/fixtures/latest.sample.json')) as Json & { groups: Json[] };
  const twAll = report.groups.find((group) => group.key === 'tw_all')!;
  twAll.items = taiwanStocks.slice(0, longList).map((entry, index) => {
    const close = 50 + ((index * 37) % 900);
    const ma = close * (0.94 + ((index * 13) % 12) / 100);
    return {
      symbol: entry.symbol,
      name: entry.name,
      maList: [20],
      close,
      changePct: (((index * 7) % 19) - 9) / 2,
      maValues: { 20: Number(ma.toFixed(2)) },
      note: `現價:${close}`,
    };
  });
  return report;
}

const US = [
  ['NVDA', 'NVIDIA Corporation', 'NASDAQ'],
  ['AAPL', 'Apple Inc.', 'NASDAQ'],
  ['MSFT', 'Microsoft Corporation', 'NASDAQ'],
  ['NVO', 'Novo Nordisk A/S', 'NYSE'],
  ['TSM', 'Taiwan Semiconductor Manufacturing Company Limited', 'NYSE'],
];
export const directory = {
  version: 1,
  generatedAt: '2026-10-08T00:00:00Z',
  items: [...taiwanStocks.map((entry) => [entry.symbol, entry.name, entry.market]), ...US],
};
export const valuation = {
  TW: {
    version: 1,
    market: 'TW',
    items: { '2330.TW': { epsTtm: 55.5, epsAnnual: 45.25, fiscalYear: 2025, exchangePeTtm: 22.6, asOf: '2026-10-07', source: 'TWSE' } },
  },
  US: {
    version: 1,
    market: 'US',
    items: {
      NVDA: { epsTtm: 4.9, epsAnnual: 4.1, fiscalYear: 2026, asOf: '2026-07-26', source: 'SEC frames', basis: 'diluted' },
      MSFT: { epsTtm: -1, epsAnnual: -0.5, fiscalYear: 2026, asOf: '2026-06-30', source: 'SEC frames', basis: 'diluted' },
    },
  },
};

const TAIWAN = (symbol: string) => /\.(TW|TWO)$/.test(symbol) || symbol === '^TWII' || symbol === '^TWOII';
function hash(text: string) {
  let h = 2166136261;
  for (const c of text) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}
/** Deterministic weekday bars ending 2026-10-08 (synthetic). */
export function dailyBars(symbol: string, count = 600) {
  let seed = hash(symbol) || 1;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const offset = TAIWAN(symbol) ? 3600 : 13.5 * 3600;
  const days: number[] = [];
  for (let t = Date.UTC(2026, 9, 8); days.length < count; t -= 86400000) {
    const weekday = new Date(t).getUTCDay();
    if (weekday !== 0 && weekday !== 6) days.unshift(t / 1000 + offset);
  }
  let price = 50 + (hash(symbol) % 900);
  return days.map((time) => {
    const open = price;
    const close = Math.max(1, open * (1 + (rand() - 0.48) * 0.03));
    price = close;
    return { time, open, high: Math.max(open, close) * 1.01, low: Math.min(open, close) * 0.99, close, volume: Math.round(1e6 + rand() * 1e7) };
  });
}
export function yahooChart(symbol: string, interval: string) {
  const taiwan = TAIWAN(symbol);
  const bars = dailyBars(symbol);
  const last = bars.at(-1)!;
  const round = (value: number) => Number(value.toFixed(2));
  return {
    chart: {
      result: [
        {
          meta: {
            currency: taiwan ? 'TWD' : 'USD',
            symbol,
            exchangeName: taiwan ? 'TAI' : 'NMS',
            instrumentType: symbol.startsWith('^') ? 'INDEX' : 'EQUITY',
            regularMarketTime: Math.round(last.time + 4 * 3600),
            gmtoffset: taiwan ? 28800 : -14400,
            timezone: taiwan ? 'CST' : 'EDT',
            exchangeTimezoneName: taiwan ? 'Asia/Taipei' : 'America/New_York',
            regularMarketPrice: round(last.close),
            chartPreviousClose: round(bars.at(-2)!.close),
            priceHint: 2,
            dataGranularity: interval,
            range: '5y',
          },
          timestamp: bars.map((bar) => Math.round(bar.time)),
          indicators: {
            quote: [
              {
                open: bars.map((bar) => round(bar.open)),
                high: bars.map((bar) => round(bar.high)),
                low: bars.map((bar) => round(bar.low)),
                close: bars.map((bar) => round(bar.close)),
                volume: bars.map((bar) => bar.volume),
              },
            ],
          },
        },
      ],
      error: null,
    },
  };
}

export interface MockOptions {
  report?: Json | 'missing' | 'broken';
  failingSymbols?: string[];
}
/** Installs all site mocks on the page; returns the list of market symbols requested. */
export async function mockSite(page: Page, options: MockOptions = {}) {
  const requested: string[] = [];
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/my-stock-screener/report/latest.json', (route) =>
    options.report === 'missing'
      ? route.fulfill({ status: 404, body: 'not found' })
      : options.report === 'broken'
        ? route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>x</title>' })
        : json(route, options.report ?? buildReport()),
  );
  await page.route('**/my-stock-screener/report/series/index.json', (route) => json(route, { symbols: [] }));
  await page.route('**/my-stock-screener/atlas/symbols/directory.json', (route) => json(route, directory));
  await page.route('**/my-stock-screener/atlas/valuation/tw.json', (route) => json(route, valuation.TW));
  await page.route('**/my-stock-screener/atlas/valuation/us.json', (route) => json(route, valuation.US));
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const url = new URL(route.request().url());
    const symbol = url.searchParams.get('symbol') ?? '';
    requested.push(symbol);
    if (options.failingSymbols?.includes(symbol)) return json(route, { error: 'upstream' }, 502);
    return json(route, yahooChart(symbol, url.searchParams.get('interval') ?? '1d'));
  });
  return requested;
}
