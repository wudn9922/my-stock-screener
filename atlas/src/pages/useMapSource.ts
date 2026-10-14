import { useEffect, useMemo, useState } from 'react';
import type { ThemesMarket } from '../report/themes';
import { loadUniverse, type UniverseLoadResult } from '../report/universe';
import { resolveMapSource, type MapSource } from './industryModel';

/** Loads the shared universe (only while the heatmap is open) and decides what the map is made of. */
export function useMapSource(market: ThemesMarket, enabled: boolean): MapSource {
  const [result, setResult] = useState<UniverseLoadResult | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    void loadUniverse().then((next) => {
      if (active) setResult(next);
    });
    return () => {
      active = false;
    };
  }, [enabled]);
  return useMemo(() => resolveMapSource(result, market), [result, market]);
}
