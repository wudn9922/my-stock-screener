import { afterEach, describe, expect, it, vi } from 'vitest';
import { retireScreenerWorker } from '../src/auth/retireWorker';
const scope = 'https://example.test/my-stock-screener/atlas/';
afterEach(() => vi.unstubAllGlobals());

describe('retirement of the old screener worker', () => {
  it('unregisters only its exact scope and deletes only its own caches without opening storage', async () => {
    const own = { scope, update: vi.fn(async () => {}), unregister: vi.fn(async () => true) };
    const other = { scope: 'https://example.test/lightweight-drawing-lab/', update: vi.fn(), unregister: vi.fn() };
    const remove = vi.fn(async () => true), open = vi.fn();
    vi.stubGlobal('navigator', { serviceWorker: { getRegistrations: async () => [own, other] } });
    const old = `atlas-shell-${encodeURIComponent(scope)}-atlas-shell-v1`;
    vi.stubGlobal('caches', { keys: async () => [old, 'another-app'], delete: remove, open });
    await retireScreenerWorker(scope);
    expect(own.update).toHaveBeenCalledOnce(); expect(own.unregister).toHaveBeenCalledOnce();
    expect(other.update).not.toHaveBeenCalled(); expect(other.unregister).not.toHaveBeenCalled();
    expect(remove.mock.calls).toEqual([[old]]); expect(open).not.toHaveBeenCalled();
  });

  it('still opens the RAM workspace when browser registration or cache access is blocked', async () => {
    vi.stubGlobal('navigator', { serviceWorker: { getRegistrations: async () => { throw new DOMException('Blocked', 'SecurityError'); } } });
    vi.stubGlobal('caches', { keys: async () => { throw new DOMException('Blocked', 'SecurityError'); } });
    await expect(retireScreenerWorker(scope)).resolves.toBeUndefined();
  });

  it('tolerates failed unregistration and still removes a readable old cache', async () => {
    vi.stubGlobal('navigator', { serviceWorker: { getRegistrations: async () => [{
      scope, update: async () => { throw new Error('offline'); }, unregister: async () => { throw new Error('denied'); },
    }] } });
    const remove = vi.fn(async () => true);
    const old = `atlas-shell-${encodeURIComponent(scope)}-atlas-shell-v1`;
    vi.stubGlobal('caches', { keys: async () => [old], delete: remove });
    await expect(retireScreenerWorker(scope)).resolves.toBeUndefined();
    expect(remove).toHaveBeenCalledWith(old);
  });
});
