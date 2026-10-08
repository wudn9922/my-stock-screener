/**
 * `ATLAS_TIMEFRAMES` (optional, e.g. `1D,1W,1M`) limits which Yahoo intervals refresh-market collects.
 * Unset or blank keeps the original behaviour: every configured interval.
 * `1D` is always collected because the snapshot schema and the current 1W/1M period rely on it.
 */
export interface AtlasTimeframeSelection<T extends string> {
  timeframes: T[];
  warnings: string[];
}

export function parseAtlasTimeframes<T extends string>(
  value: string | undefined,
  available: readonly T[],
): AtlasTimeframeSelection<T> {
  const warnings: string[] = [];
  if (value === undefined || !value.trim()) return { timeframes: [...available], warnings };
  const requested = new Set<T>();
  for (const raw of value.split(',')) {
    const item = raw.trim();
    if (!item) continue;
    // Exact Atlas names; H/D/W are also accepted in lower case. `1m` stays ambiguous (minute vs month).
    const match =
      available.find((timeframe) => timeframe === item) ??
      (/^[0-9]+[hdw]$/.test(item) ? available.find((timeframe) => timeframe === item.toUpperCase()) : undefined);
    if (!match) {
      throw new Error(
        `ATLAS_TIMEFRAMES contains unsupported timeframe "${item}"; use a comma list of ${available.join(', ')}`,
      );
    }
    requested.add(match);
  }
  if (!requested.size) throw new Error('ATLAS_TIMEFRAMES is set but lists no timeframe');
  const daily = available.find((timeframe) => timeframe === '1D');
  if (daily && !requested.has(daily)) {
    requested.add(daily);
    warnings.push('ATLAS_TIMEFRAMES omitted 1D; it is always collected because snapshots require daily bars.');
  }
  return { timeframes: available.filter((timeframe) => requested.has(timeframe)), warnings };
}
