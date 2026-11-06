import { describe, expect, it, vi } from 'vitest';
import { KlineArchive } from '../src/market-data/KlineArchive';
import { ArchiveFirstProvider } from '../src/market-data/ArchiveFirstProvider';
import { archiveIndexFixture } from './fixtures/archive-index';

const csv = 'Date,Open,High,Low,Close,Volume\n2026-01-02,100,101,99,100,1000\n';
const index = archiveIndexFixture('AAPL', { 2026: csv });
const fallback = () => ({ id: 'fallback', supportedTimeframes: ['1D'] as const,
  getBars: vi.fn(async () => ({ bars: [], source: 'fallback', session: 'regular' as const, delayed: true, adjusted: true })),
  getQuote: vi.fn(async () => { throw new Error('unused'); }),
});

describe('archive document cancellation', () => {
  it.each(['index', 'body'] as const)('preserves cancellation during %s before pagehide, without starting fallback', async stage => {
    const page = new EventTarget();
    let started!: () => void;
    const ready = new Promise<void>(resolve => { started = resolve; });
    const fetcher: typeof fetch = async (url, init) => {
      if (stage === 'body' && String(url).includes('/index/')) return Response.json(index);
      const pending = new Promise<never>((_, reject) => {
        init!.signal!.addEventListener('abort', () => reject(new TypeError('Load failed')), { once: true });
      });
      started();
      // Exercise the period after headers arrive, while Response.text is pending.
      const response = new Response();
      vi.spyOn(response, 'text').mockReturnValue(pending);
      return stage === 'index' ? pending : response;
    };
    const live = fallback();
    const archive = new KlineArchive({ page, fetcher, cache: false });
    const result = new ArchiveFirstProvider(live, archive).getBars('AAPL', '1D');
    const rejected = expect(result).rejects.toMatchObject({ name: 'AbortError' });
    await ready;
    const event = new Event('beforeunload', { cancelable: true });
    page.dispatchEvent(event);
    await rejected;
    expect(event.defaultPrevented).toBe(false); // This listener never creates a leave-page prompt.
    expect(live.getBars).not.toHaveBeenCalled();
  });

  it('retries the same shared index after a cancelled leave and removes completed request listeners', async () => {
    const page = new EventTarget();
    const add = vi.spyOn(page, 'addEventListener'), remove = vi.spyOn(page, 'removeEventListener');
    let started!: () => void;
    const ready = new Promise<void>(resolve => { started = resolve; });
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(async (_url, init) => {
      started();
      return new Promise((_, reject) => init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true }));
    }).mockImplementation(async url => String(url).includes('/index/') ? Response.json(index) : new Response(csv));
    const archive = new KlineArchive({ page, fetcher, cache: false });
    const aborted = expect(archive.getDaily('AAPL')).rejects.toMatchObject({ name: 'AbortError' });
    await ready;
    page.dispatchEvent(new Event('beforeunload'));
    await aborted;
    expect((await archive.getDaily('AAPL'))?.bars).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/index/'))).toHaveLength(2);
    expect(remove.mock.calls).toHaveLength(add.mock.calls.length);
    for (const [event, listener] of add.mock.calls) expect(remove).toHaveBeenCalledWith(event, listener);
  });

  it('aborting one chart consumer does not abort another consumer\'s shared archive download', async () => {
    let finish!: (response: Response) => void;
    let networkSignal!: AbortSignal;
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(async (_url, init) => {
      networkSignal = init!.signal!;
      return new Promise(resolve => { finish = resolve; });
    }).mockResolvedValue(new Response(csv));
    const archive = new KlineArchive({ page: new EventTarget(), fetcher, cache: false });
    const chart = new AbortController();
    const cancelled = expect(archive.getDaily('AAPL', undefined, chart.signal)).rejects.toMatchObject({ name: 'AbortError' });
    const active = archive.getDaily('AAPL');
    chart.abort();
    expect(networkSignal.aborted).toBe(false);
    finish(Response.json(index));
    await cancelled;
    expect((await active)?.bars).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each(['404', '503', 'network', 'timeout'])('still falls back on genuine %s unavailability', async failure => {
    const fetcher: typeof fetch = async (_url, init) => {
      if (failure === 'network') throw new TypeError('Load failed');
      if (failure === 'timeout') return new Promise((_, reject) => {
        init!.signal!.addEventListener('abort', () => reject(new TypeError('Load failed')), { once: true });
      });
      return new Response('', { status: Number(failure) });
    };
    const live = fallback();
    const archive = new KlineArchive({ page: new EventTarget(), fetcher, cache: false, timeoutMs: 1 });
    expect((await new ArchiveFirstProvider(live, archive).getBars('AAPL', '1D')).source).toBe('fallback');
    expect(live.getBars).toHaveBeenCalledTimes(1);
  });
});
