import { afterEach, expect, it, vi } from 'vitest';
import type { KlineArchive } from '../src/market-data/KlineArchive';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

it.each(['absent', 'cancelled'] as const)(
  'beforeunload prevents a late %s archive fallback before pagehide; BFCache gets a new lifetime',
  async outcome => {
    const page = new EventTarget();
    vi.stubGlobal('window', page);
    const { ArchiveFirstProvider } = await import('../src/market-data/ArchiveFirstProvider');
    let finish!: () => void;
    const getDaily = vi.fn().mockImplementationOnce(() => new Promise<null>((resolve, reject) => {
      finish = () => outcome === 'absent' ? resolve(null) : reject(new TypeError('Load failed'));
    })).mockResolvedValue(null);
    const fallback = {
      id: 'test', supportedTimeframes: ['1D'] as const,
      getBars: vi.fn().mockResolvedValue({ bars: [], source: 'fallback' }),
      getQuote: vi.fn(),
    };
    const provider = new ArchiveFirstProvider(fallback, { getDaily } as unknown as KlineArchive);
    const pending = provider.getBars('^TWII', '1D');
    const aborted = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    // WebKit cancels archive loads after beforeunload, before pagehide. The
    // rejection used to start series/index.json in a document that cannot fetch.
    page.dispatchEvent(new Event('beforeunload'));
    finish();
    await aborted;
    expect(fallback.getBars).not.toHaveBeenCalled();
    await expect(provider.getBars('^GSPC', '1D')).rejects.toMatchObject({ name: 'AbortError' });
    expect(getDaily).toHaveBeenCalledTimes(1);

    page.dispatchEvent(new Event('pagehide'));
    page.dispatchEvent(new Event('pageshow'));
    await provider.getBars('^GSPC', '1D');
    expect(fallback.getBars).toHaveBeenCalledExactlyOnceWith('^GSPC', '1D', undefined, undefined);
  },
);
