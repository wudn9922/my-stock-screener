import { it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { SecEdgarProvider } from '../src/fundamentals/SecEdgarProvider';
const records = new FinancialNormalizer().normalize(
  JSON.parse(readFileSync('tests/fixtures/sec/AAPL.json', 'utf8')),
  'AAPL',
  'annual',
);
afterEach(() => vi.unstubAllGlobals());
it('browser SEC provider caches normalized records by symbol/period and returns independent snapshots', async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(records), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const provider = new SecEdgarProvider(),
    controller = new AbortController();
  const first = await provider.getFinancials('aapl', 'annual', controller.signal);
  first[0].revenue = 0;
  expect((await provider.getFinancials('AAPL', 'annual'))[0].revenue).toBe(records[0].revenue);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await expect(provider.getFinancials('NVDA', 'annual')).rejects.toThrow('mismatched');
  await expect(provider.getFinancials('AAPL', 'quarterly')).rejects.toThrow('mismatched');
});
it('SEC failures and unauditable payloads throw; an unknown company may return an empty normalized list', async () => {
  const provider = new SecEdgarProvider();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{}', { status: 502 })),
  );
  await expect(provider.getFinancials('AAPL', 'annual')).rejects.toThrow('SEC');
  const invalid = structuredClone(records);
  delete invalid[0].sourceConcepts.revenue;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(invalid))),
  );
  await expect(provider.getFinancials('AAPL', 'annual')).rejects.toThrow('provenance');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('[]')),
  );
  expect(await provider.getFinancials('NODATA', 'annual')).toEqual([]);
});
