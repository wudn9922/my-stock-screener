/** A header tap survives until the lazy-loaded watchlist page consumes it. */
let pending = false;
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

export function requestWatchlistActions() {
  pending = true;
  notify();
}

export const watchlistActionsRequest = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot: () => pending,
  consume() {
    if (!pending) return;
    pending = false;
    notify();
  },
};
