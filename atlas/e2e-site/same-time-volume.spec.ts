import { expect, test, type Page } from '@playwright/test';
import { mockMarketLogin } from './account-fixture';
import { dailyBars, mockSite, yahooChart } from './mocks';

// Thursday 2026-10-08 10:32 in Taipei: the fixture's last daily bar is today's, the session is open.
const NOW = new Date('2026-10-08T02:32:00Z');
const open = (date: string) => Date.parse(`${date}T01:00:00Z`) / 1000;
type Session = { date: string; buckets: number; volume: (i: number) => number };

/** Yahoo-shaped 5m payload for the given Taiwan sessions (synthetic). */
function fiveMinute(symbol: string, sessions: Session[]) {
  const payload = yahooChart(symbol, '5m');
  const data = payload.chart.result[0]!;
  const timestamp: number[] = [], volume: number[] = [];
  for (const s of sessions) for (let i = 0; i < s.buckets; i++) {
    timestamp.push(open(s.date) + i * 300);
    volume.push(s.volume(i));
  }
  const price = data.meta.regularMarketPrice;
  data.timestamp = timestamp;
  data.meta.regularMarketTime = timestamp.at(-1)!;
  data.meta.range = '1mo';
  data.indicators.quote[0] = { open: timestamp.map(() => price), high: timestamp.map(() => price), low: timestamp.map(() => price),
    close: timestamp.map(() => price), volume };
  return payload;
}

async function chart(page: Page, symbol: string, sessions: Session[], todayDailyVolume?: number) {
  await page.clock.install({ time: NOW });
  await mockSite(page);
  const intraday: string[] = [];
  await page.route('https://edge.test/functions/v1/market-chart**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    if (params.has('quotes') || params.get('symbol') !== symbol) return route.fallback();
    if (params.get('interval') === '5m') {
      intraday.push(symbol);
      return route.fulfill({ json: fiveMinute(symbol, sessions) });
    }
    const payload = yahooChart(symbol, params.get('interval') ?? '1d');
    const volume = payload.chart.result[0]!.indicators.quote[0]!.volume;
    if (todayDailyVolume !== undefined) volume[volume.length - 1] = todayDailyVolume;
    return route.fulfill({ json: payload });
  });
  await page.goto(`./?page=chart&symbol=${encodeURIComponent(symbol)}&tf=1D`);
  await expect(page.getByTestId('ohlc-header')).not.toBeEmpty();
  return intraday;
}

// market-chart needs a LINE login; these tests are about the market data it returns.
test.beforeEach(({ page }) => mockMarketLogin(page));

test('the volume legend shows today’s volume against the previous session at the same time', async ({ page }) => {
  const intraday = await chart(page, '2330.TW', [
    { date: '2026-10-07', buckets: 54, volume: () => 1000 },
    { date: '2026-10-08', buckets: 18, volume: () => 1350 },
  ]);
  await expect(page.getByTestId('same-time-volume')).toHaveText('量比(同時間)1.35×');
  await expect(page.getByTestId('same-time-volume')).toHaveAttribute('title', /開盤至 10:30/);
  expect(intraday).toEqual(['2330.TW']);
});

const history = dailyBars('^TWII');
const daily = (date: string) => history.find((bar) => new Date(bar.time * 1000).toISOString().startsWith(date))!.volume;
const twii = (today: number): Session[] => [
  { date: '2026-10-06', buckets: 54, volume: () => daily('2026-10-06') / 54 },
  { date: '2026-10-07', buckets: 54, volume: () => daily('2026-10-07') / 54 },
  { date: '2026-10-08', buckets: 18, volume: () => today },
];

test('a live ^TWII bar without source volume takes the verified 5m volume', async ({ page }) => {
  await chart(page, '^TWII', twii(100_000), 0);
  await expect(page.locator('[data-legacy-volume] [data-volume-value]')).toHaveText('1,800 張');
  await expect(page.getByTestId('index-volume-missing')).toHaveCount(0);
  await expect(page.getByTestId('same-time-volume')).toBeVisible();
});

test('a live ^TWII bar says so when no same-unit intraday volume exists, with no ratio', async ({ page }) => {
  await chart(page, '^TWII', twii(0), 0);
  await expect(page.getByTestId('index-volume-missing')).toHaveText('今日量：盤中來源未提供');
  await expect(page.getByTestId('same-time-volume')).toHaveCount(0);
});
