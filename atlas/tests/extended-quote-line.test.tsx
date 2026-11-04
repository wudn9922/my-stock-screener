import { afterEach, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ExtendedQuoteLine, extendedLineText } from '../src/ui/ExtendedQuotes';
import { loadExtendedSnapshot, selectExtendedQuote, type ExtendedSnapshot } from '../src/market-data/extendedQuotes';
import type { Quote } from '../src/market-data/MarketDataProvider';
const now = Date.parse('2026-11-04T22:30:00Z') / 1000;
const quote: Quote = { symbol: 'AAPL', price: 100, change: 1, changePercent: 1, asOf: now - 5400 };
const snapshot: ExtendedSnapshot = { version: 1, symbol: 'AAPL', checkedAt: '2026-11-04T22:30:00Z', source: 'Nasdaq', pre: { status: 'ok', price: 99, asOf: '2026-11-04T14:00:00Z' }, post: { status: 'ok', price: 102, asOf: '2026-11-04T21:45:00Z' } };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it('selects the latest valid session, prefers a newer batch quote, and does not change the regular quote', () => {
  expect(selectExtendedQuote('AAPL', quote, snapshot, now)).toMatchObject({ mode: 'post', price: 102, changePercent: expect.closeTo(2, 10), source: 'Nasdaq' });
  const live = { ...quote, extended: { post: { price: 105, asOf: now - 1800 } } };
  expect(selectExtendedQuote('AAPL', live, snapshot, now)).toMatchObject({ price: 105, changePercent: expect.closeTo(5, 10), source: 'Yahoo 延遲行情' });
  expect(live.price).toBe(100);
  expect(selectExtendedQuote('AAPL', { ...live, extended: { post: { price: 999, asOf: now + 3600 } } }, snapshot, now)?.price).toBe(102);
});
it('rejects mismatched symbols, invalid prices/dates, and omits an invalid comparison with a later regular trade', () => {
  expect(selectExtendedQuote('NVDA', quote, snapshot, now)).toBeUndefined();
  expect(selectExtendedQuote('AAPL', { ...quote, extended: { post: { price: NaN, asOf: now } } }, undefined, now)).toBeUndefined();
  expect(selectExtendedQuote('AAPL', { ...quote, asOf: now }, snapshot, now)?.changePercent).toBeUndefined();
  expect(selectExtendedQuote('AAPL', null, { ...snapshot, post: { status: 'unavailable', price: 999, asOf: snapshot.post.asOf } }, now)?.mode).toBe('pre');
});
it('renders no placeholder when there is no trade, and exposes a price directly when one exists', () => {
  vi.useFakeTimers(); vi.setSystemTime(now * 1000);
  expect(renderToStaticMarkup(<ExtendedQuoteLine symbol="AAPL" quote={quote} />)).toBe('');
  const html = renderToStaticMarkup(<ExtendedQuoteLine symbol="AAPL" quote={{ ...quote, extended: { post: { price: 105, asOf: now - 1800 } } }} />);
  expect(html).toContain('盤後 105.00 +5.00%');
  expect(html).not.toContain('盤前／後');
});
it('labels historic dates in New York instead of presenting them as today', () => {
  expect(extendedLineText({ mode: 'post', price: 102, asOf: now - 2700 }, false, now * 1000)).toBe('盤後 102.00（16:45 紐約）');
  expect(extendedLineText({ mode: 'post', price: 102, asOf: now - 2700 }, true, (now + 86400) * 1000)).toContain('11/04 16:45紐約');
});
it('shares one static request between row/header/details, with explicit retry and no timer', async () => {
  const fetcher = vi.fn(async () => Response.json({ ...snapshot, symbol: 'CACHE' }));
  vi.stubGlobal('fetch', fetcher);
  const [a, b] = await Promise.all([loadExtendedSnapshot('CACHE'), loadExtendedSnapshot('CACHE')]);
  expect(a).toBe(b);
  await loadExtendedSnapshot('CACHE');
  expect(fetcher).toHaveBeenCalledTimes(1);
  await loadExtendedSnapshot('CACHE', true);
  expect(fetcher).toHaveBeenCalledTimes(2);
});
