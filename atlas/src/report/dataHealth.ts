export interface HealthEntry {
  id: string;
  label: string;
  status: 'ok' | 'missing' | 'invalid' | 'error' | 'loading';
  date?: string | null;
  source?: string | null;
  detail?: string;
}
let entries: readonly HealthEntry[] = [];
const listeners = new Set<() => void>();
export const dataHealth = {
  subscribe: (listener: () => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot: () => entries,
  record(entry: HealthEntry) {
    entries = [...entries.filter((current) => current.id !== entry.id), entry];
    listeners.forEach((listener) => listener());
  },
};
export function healthAge(date: string | null | undefined, now = Date.now()): number | null {
  const time = date ? Date.parse(date) : NaN;
  return Number.isFinite(time) ? Math.max(0, Math.floor((now - time) / 86400000)) : null;
}
