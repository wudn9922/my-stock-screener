/** Retire only this screener's old shell cache; blocked browser storage must not prevent RAM use. */
export async function retireScreenerWorker(scope: string) {
  if ('serviceWorker' in navigator) {
    const registrations = await navigator.serviceWorker.getRegistrations().catch(() => []);
    for (const registration of registrations) {
      if (registration.scope !== scope) continue;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          registration.update().catch(() => {}),
          new Promise((resolve) => { timer = setTimeout(resolve, 1000); }),
        ]);
        await registration.unregister();
      } catch {
        // Some privacy modes deny registration management; continue without creating a new worker.
      } finally {
        clearTimeout(timer);
      }
    }
  }
  if ('caches' in globalThis) {
    const prefix = `atlas-shell-${encodeURIComponent(scope)}-`;
    try {
      for (const name of await caches.keys()) if (name.startsWith(prefix)) await caches.delete(name);
    } catch {
      // CacheStorage can be disabled independently of service workers; do not block the RAM workspace.
    }
  }
}
