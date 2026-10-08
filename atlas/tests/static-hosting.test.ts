import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

it('rejects Yahoo bars, quotes, and events before network access on static hosting; SEC reads only static files', async () => {
  vi.stubEnv('VITE_STATIC_HOSTING', '1');
  vi.resetModules();
  const [{ YahooProvider }, { SecEdgarProvider }, { MarketDataUnavailableError }] =
    await Promise.all([
      import('../src/market-data/YahooProvider'),
      import('../src/fundamentals/SecEdgarProvider'),
      import('../src/market-data/ProviderErrors'),
    ]);
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  const yahoo = new YahooProvider();
  const sec = new SecEdgarProvider();

  for (const request of [
    yahoo.getBars('AAPL', '1D'),
    yahoo.getQuote('AAPL'),
    yahoo.getCorporateEvents('AAPL'),
  ]) {
    const error = await request.catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(MarketDataUnavailableError);
    expect(error).toMatchObject({ message: expect.stringContaining('需要 backend') });
  }
  expect(fetchMock).not.toHaveBeenCalled();
  // my-stock-screener: static SEC financials come from pre-built files, never the /api backend.
  fetchMock.mockResolvedValue(new Response('Not found', { status: 404 }));
  await expect(sec.getFinancials('AAPL', 'quarterly')).rejects.toThrow('沒有預先下載的 SEC 財報');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(String(fetchMock.mock.calls[0][0])).toBe('/fundamentals/AAPL-quarterly.json');
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/'))).toBe(false);
});
