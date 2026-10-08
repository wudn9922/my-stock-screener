import { describe, it, expect, vi } from 'vitest';
import { SecClient, SecUnavailableError } from '../src/fundamentals/SecClient';
describe('SEC server policy and generic resolution', () => {
  it('resolves arbitrary tickers and punctuation aliases, caches and deduplicates concurrent requests', async () => {
    const transport = vi.fn(async (url: string) =>
      url.includes('company_tickers')
        ? { '0': { ticker: 'BRK-B', cik_str: 1067983 }, '1': { ticker: 'NVO', cik_str: 353278 } }
        : { cik: Number(url.match(/CIK(\d+)/)?.[1]), facts: {} },
    );
    const client = new SecClient(transport, 'Atlas Test/1 test@example.test', 0);
    const [a, b] = await Promise.all([client.companyFacts('BRK.B'), client.companyFacts('BRK-B')]);
    expect(a).toEqual(b);
    expect(a!.cik).toBe('0001067983');
    expect(transport).toHaveBeenCalledTimes(2);
    expect(await client.resolveCik('NVO')).toBe('0000353278');
    expect(await client.resolveCik('UNKNOWN')).toBeNull();
    expect(transport).toHaveBeenCalledTimes(2);
    expect(transport.mock.calls[1][0]).toBe(
      'https://data.sec.gov/api/xbrl/companyfacts/CIK0001067983.json',
    );
  });
  it('serializes different requests and spaces request starts, and expired caches refetch', async () => {
    const starts: number[] = [];
    const transport = vi.fn(async (url: string) => {
      starts.push(Date.now());
      return url.includes('company_tickers')
        ? { '0': { ticker: 'AAPL', cik_str: 320193 }, '1': { ticker: 'MSFT', cik_str: 789019 } }
        : { cik: Number(url.match(/CIK(\d+)/)?.[1]), facts: {} };
    });
    const client = new SecClient(transport, 'Atlas Test', 30);
    await Promise.all([client.companyFacts('AAPL'), client.companyFacts('MSFT')]);
    expect(starts).toHaveLength(3);
    expect(starts[1] - starts[0]).toBeGreaterThanOrEqual(25);
    expect(starts[2] - starts[1]).toBeGreaterThanOrEqual(25);
    let now = 0;
    const cached = new SecClient(transport, 'Atlas Test', 0, () => now);
    await cached.resolveCik('AAPL');
    now = 86400001;
    await cached.resolveCik('AAPL');
    expect(transport).toHaveBeenCalledTimes(5);
  });
  it('rate-limit cooldown prevents retries and errors never become fake financials', async () => {
    const transport = vi.fn(async () => {
      throw new SecUnavailableError('SEC returned 429', 429);
    });
    const client = new SecClient(transport, 'Atlas Test', 0);
    await expect(client.companyFacts('AAPL')).rejects.toThrow('429');
    await expect(client.companyFacts('MSFT')).rejects.toThrow('cooldown');
    expect(transport).toHaveBeenCalledTimes(1);
  });
});

it('rejects a wrong-CIK response, invalidates it and permits a clean retry', async () => {
  let wrong = true;
  const transport = vi.fn(async (url: string) =>
    url.includes('company_tickers')
      ? { '0': { ticker: 'AAPL', cik_str: 320193 } }
      : { cik: wrong ? 1 : 320193, facts: {} },
  );
  const client = new SecClient(transport, 'Atlas Test', 0);
  await expect(client.companyFacts('AAPL')).rejects.toThrow('identity/schema mismatch');
  wrong = false;
  expect((await client.companyFacts('AAPL'))!.cik).toBe('0000320193');
  expect(transport).toHaveBeenCalledTimes(3);
});
