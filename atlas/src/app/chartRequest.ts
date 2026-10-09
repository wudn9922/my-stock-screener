import type { Timeframe } from '../market-data/MarketDataProvider';

/** One navigation into the chart workspace (search, report lists, deep links). */
export interface ChartRequest {
  /** Navigation id; the workspace applies each id once. */
  id: number;
  /** Raw symbol text (canonical, or a bare Taiwan code resolved through the directory). */
  symbol: string;
  timeframe?: Timeframe;
  /** `url` (deep link, reload, back/forward) or `app` (search, report lists). */
  source: 'url' | 'app';
  /** Report moving averages applied as SMA indicators when the symbol has none yet. */
  maList?: number[];
}
