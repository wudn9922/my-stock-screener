import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { EdgeYahooProvider } from '../src/market-data/EdgeYahooProvider';
import { MarketDataUnavailableError } from '../src/market-data/ProviderErrors';
import { LiveQuoteFeed } from '../src/market-data/LiveQuotes';
import { loadFailureReason } from '../src/market-data/loadFailureReason';
import {
  MARKET_TOKEN_HEADER,
  MarketLoginRequiredError,
  MarketProxyAccess,
  MarketQuotaError,
  type MarketTokenSource,
} from '../src/market-data/marketProxyAccess';

const PROXY = 'https://proj.supabase.co/functions/v1/market-chart';
const daily = JSON.parse(readFileSync(new URL('./fixtures/market/SMCI-current-1d.json', import.meta.url), 'utf8')) as unknown;

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

/** A switchable login: `token` null = signed out. */
function login(initial: string | null, settled = true) {
  const listeners = new Set<() => void>();
  let resolveReady!: () => void;
  const ready = new Promise<void>((resolve) => { resolveReady = resolve; });
  const state = { token: initial, settled, unauthorized: 0 };
  const source: MarketTokenSource = {
    token: () => state.token,
    ready: () => (state.settled ? Promise.resolve() : ready),
    settled: () => state.settled,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
    unauthorized: () => { state.unauthorized++; },
  };
  const set = (token: string | null) => { state.token = token; listeners.forEach((listener) => listener()); };
  const settle = () => { state.settled = true; resolveReady(); listeners.forEach((listener) => listener()); };
  return { state, source, set, settle };
}

/** Records every request and the token it carried. */
function recorder(respond: (url: URL) => Response = () => json(daily)) {
  const tokens: (string | null)[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    tokens.push(new Headers(init?.headers).get(MARKET_TOKEN_HEADER));
    return respond(new URL(String(input)));
  });
  return { fetcher: fetcher as unknown as typeof fetch, mock: fetcher, tokens };
}

describe('market-chart access gate', () => {
  it('signed out: charts and live quotes send nothing and say 登入 LINE 後可查看', async () => {
    const account = login(null);
    const access = new MarketProxyAccess(account.source);
    const { fetcher, mock } = recorder();
    const provider = new EdgeYahooProvider(PROXY, { fetcher, access });
    const error = await provider.getBars('SMCI', '1D').catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(MarketLoginRequiredError);
    expect(loadFailureReason(error)).toBe('登入 LINE 後可查看');
    await expect(provider.getCorporateEvents('SMCI')).rejects.toBeInstanceOf(MarketLoginRequiredError);
    await expect(provider.refreshBars('SMCI', '5m')).rejects.toBeInstanceOf(MarketLoginRequiredError);

    const feed = new LiveQuoteFeed(PROXY, { fetcher, access, visible: () => true, inSession: () => true });
    const stop = feed.subscribe('SMCI', () => undefined);
    await feed.poll(false);
    await feed.poll(true);
    await expect(feed.refresh('SMCI')).rejects.toBeInstanceOf(MarketLoginRequiredError);
    stop();
    expect(mock).not.toHaveBeenCalled();
    expect(access.getSnapshot()).toEqual({ status: 'signed-out' });
  });

  it('waits for the first account check instead of treating a remembered login as a guest', async () => {
    const account = login('atlasdev.remembered', false);
    const access = new MarketProxyAccess(account.source);
    expect(access.getSnapshot().status).toBe('pending');
    const { fetcher, tokens } = recorder();
    const pending = new EdgeYahooProvider(PROXY, { fetcher, access }).getBars('SMCI', '1D');
    await Promise.resolve();
    expect(tokens).toEqual([]);
    account.settle();
    await pending;
    expect(tokens).toEqual(['atlasdev.remembered']);
  });

  it('signed in: every request carries X-Atlas-Token, re-read per request', async () => {
    const account = login('line-id-token-1');
    const access = new MarketProxyAccess(account.source);
    const { fetcher, tokens } = recorder((url) => url.searchParams.has('quotes')
      ? json({ asOf: 0, results: {}, errors: {} }) : json(daily));
    const provider = new EdgeYahooProvider(PROXY, { fetcher, access });
    await provider.getBars('SMCI', '1D');
    account.set('atlasdev.second');
    await provider.refreshBars('SMCI', '1D');
    const feed = new LiveQuoteFeed(PROXY, { fetcher, access, visible: () => true, inSession: () => true });
    const stop = feed.subscribe('AAPL', () => undefined);
    await feed.poll(false);
    stop();
    expect(tokens).toEqual(['line-id-token-1', 'atlasdev.second', 'atlasdev.second']);
    expect(access.getSnapshot()).toEqual({ status: 'ready' });
  });

  it('401: that token counts as signed out (no more requests) until a new login', async () => {
    const account = login('expired');
    const access = new MarketProxyAccess(account.source);
    const { fetcher, mock } = recorder(() => json({ ok: false, code: 'LOGIN_REQUIRED', error: '登入 LINE 後可查看即時行情與更多股票。' }, 401));
    const provider = new EdgeYahooProvider(PROXY, { fetcher, access });
    await expect(provider.getBars('SMCI', '1D')).rejects.toBeInstanceOf(MarketLoginRequiredError);
    expect(account.state.unauthorized).toBe(1);
    expect(access.getSnapshot().status).toBe('signed-out');
    await expect(provider.getBars('SMCI', '1W')).rejects.toBeInstanceOf(MarketLoginRequiredError);
    expect(mock).toHaveBeenCalledTimes(1);
    account.set('fresh');
    expect(access.getSnapshot().status).toBe('ready');
  });

  it('429 window cap: shows the server text and sends nothing until retryAfterSeconds passed', async () => {
    let now = 1_000_000;
    const account = login('token');
    const access = new MarketProxyAccess(account.source, () => now);
    let limited = true;
    const { fetcher, mock } = recorder(() => limited
      ? json({ ok: false, code: 'USER_WINDOW', error: '查詢太頻繁，請稍後再試（每 10 分鐘上限）', retryAfterSeconds: 120 }, 429, { 'Retry-After': '120' })
      : json(daily));
    const provider = new EdgeYahooProvider(PROXY, { fetcher, access, now: () => now });
    const error = await provider.getBars('SMCI', '1D').catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(MarketQuotaError);
    expect(loadFailureReason(error)).toBe('查詢太頻繁，請稍後再試（每 10 分鐘上限）');
    expect(access.getSnapshot()).toMatchObject({ status: 'quota', code: 'USER_WINDOW', message: '查詢太頻繁，請稍後再試（每 10 分鐘上限）' });
    limited = false;
    now += 119_000;
    await expect(provider.getBars('SMCI', '1D')).rejects.toBeInstanceOf(MarketQuotaError);
    expect(mock).toHaveBeenCalledTimes(1);
    now += 2_000;
    await provider.getBars('SMCI', '1D');
    expect(mock).toHaveBeenCalledTimes(2);
    expect(access.getSnapshot().status).toBe('ready');
  });

  it('429 daily cap: no request for the rest of the session, live polling included', async () => {
    let now = 1_000_000;
    const account = login('token');
    const access = new MarketProxyAccess(account.source, () => now);
    const { fetcher, mock } = recorder(() => json({ ok: false, code: 'SITE_DAILY', error: '今日免費額度已用完，明天再試', retryAfterSeconds: 3600 }, 429));
    const feed = new LiveQuoteFeed(PROXY, { fetcher, access, now: () => now, visible: () => true, inSession: () => true });
    const errors: string[] = [];
    const stop = feed.subscribe('AAPL', () => undefined, { onError: () => errors.push('AAPL') });
    await feed.poll(false);
    expect(mock).toHaveBeenCalledTimes(1);
    expect(errors).toEqual(['AAPL']);
    expect(access.getSnapshot()).toMatchObject({ status: 'quota', message: '今日免費額度已用完，明天再試' });
    now += 24 * 3600_000;
    await feed.poll(true);
    await feed.poll(false);
    await expect(new EdgeYahooProvider(PROXY, { fetcher, access }).getBars('SMCI', '1D')).rejects.toBeInstanceOf(MarketQuotaError);
    stop();
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it('503 AUTH_UNAVAILABLE / QUOTA_UNAVAILABLE pause for retryAfterSeconds (not signed out), then resume', async () => {
    let now = 1_000_000;
    const account = login('token');
    const access = new MarketProxyAccess(account.source, () => now);
    let unavailable = true;
    const { fetcher, mock } = recorder(() => unavailable
      ? json({ ok: false, code: 'AUTH_UNAVAILABLE', error: '登入驗證暫時無法使用，請稍後再試', retryAfterSeconds: 60 }, 503, { 'Retry-After': '60' })
      : json(daily));
    const provider = new EdgeYahooProvider(PROXY, { fetcher, access, now: () => now });
    const error = await provider.getBars('SMCI', '1D').catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(MarketQuotaError);
    expect(loadFailureReason(error)).toBe('登入驗證暫時無法使用，請稍後再試');
    expect(access.getSnapshot()).toMatchObject({ status: 'quota', code: 'AUTH_UNAVAILABLE' });
    expect(account.state.unauthorized).toBe(0);
    unavailable = false;
    now += 30_000;
    await expect(provider.getBars('SMCI', '1D')).rejects.toBeInstanceOf(MarketQuotaError);
    expect(mock).toHaveBeenCalledTimes(1);
    now += 31_000;
    await provider.getBars('SMCI', '1D');
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it('older `{ error: { code } }` errors keep their existing handling and close nothing', async () => {
    const account = login('token');
    const access = new MarketProxyAccess(account.source);
    let status = 503;
    const { fetcher, mock } = recorder(() => json({ error: { code: status === 503 ? 'rate_limited' : 'upstream', message: 'x' } }, status));
    const provider = new EdgeYahooProvider(PROXY, { fetcher, access });
    await expect(provider.getBars('SMCI', '1D')).rejects.toBeInstanceOf(MarketDataUnavailableError);
    status = 502;
    await expect(provider.getBars('SMCI', '1W')).rejects.toBeInstanceOf(MarketDataUnavailableError);
    expect(access.getSnapshot().status).toBe('ready');
    expect(mock).toHaveBeenCalledTimes(3);
  });

  it('live quotes stay one batched request for many symbols (each request is one quota unit)', async () => {
    const account = login('token');
    const access = new MarketProxyAccess(account.source);
    const { fetcher, mock } = recorder(() => json({ asOf: 0, results: {}, errors: {} }));
    const feed = new LiveQuoteFeed(PROXY, { fetcher, access, visible: () => true, inSession: () => true });
    const stops = ['AAPL', 'MSFT', 'NVDA', '2330.TW'].map((symbol) => feed.subscribe(symbol, () => undefined));
    await feed.poll(false);
    stops.forEach((stop) => stop());
    expect(mock).toHaveBeenCalledTimes(1);
    expect(new URL(String(mock.mock.calls[0]![0])).searchParams.get('quotes')).toBe('AAPL,MSFT,NVDA,2330.TW');
  });
});
