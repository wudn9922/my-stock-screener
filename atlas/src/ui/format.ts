/** Display formatting shared by the site pages (zh-TW conventions, tabular numbers). */
const priceFormats = new Map<number, Intl.NumberFormat>();
function priceFormat(digits: number) {
  let format = priceFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    priceFormats.set(digits, format);
  }
  return format;
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  return priceFormat(abs >= 1000 ? 2 : abs >= 1 ? 2 : 4).format(value);
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const rounded = Number(value.toFixed(digits));
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}%`;
}

export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return value >= 1000 ? '>999' : value.toFixed(1);
}

/** CSS tone class for a signed change; colors follow the 紅漲綠跌 / 綠漲紅跌 preference. */
export function toneClass(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined || !Number.isFinite(value) || Math.abs(value) < 1e-9) return 'flat';
  return value > 0 ? 'up' : 'down';
}

/** `2026-10-08` / ISO timestamps → `2026/10/08 14:30` (local time); unknown text is kept. */
export function formatDateTime(value: string | number | null | undefined, withTime = true): string {
  if (value === null || value === undefined || value === '') return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const dateOnly = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  const y = dateOnly ? date.getUTCFullYear() : date.getFullYear();
  const m = dateOnly ? date.getUTCMonth() + 1 : date.getMonth() + 1;
  const d = dateOnly ? date.getUTCDate() : date.getDate();
  const day = `${y}/${pad(m)}/${pad(d)}`;
  return withTime && !dateOnly ? `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}` : day;
}

/** Strips the exchange suffix for compact display: `2330.TW` → `2330`. */
export function displayTicker(symbol: string): string {
  return symbol.replace(/\.(TW|TWO)$/i, '');
}

/** Moving-average value from a report record keyed `20`, `MA20`, `ma20` or `sma20`. */
export function maValue(values: Record<string, number>, period: number): number | null {
  for (const key of [String(period), `MA${period}`, `ma${period}`, `SMA${period}`, `sma${period}`]) {
    const value = values[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

/** Percent distance of the close above (+) or below (−) a moving average. */
export function maDistance(close: number | null, ma: number | null): number | null {
  if (close === null || ma === null || !Number.isFinite(close) || !Number.isFinite(ma) || ma <= 0) return null;
  return (close / ma - 1) * 100;
}
