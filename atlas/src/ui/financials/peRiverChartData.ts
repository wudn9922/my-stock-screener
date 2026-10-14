import { calendarDate, type MarketProfile } from '../../market-data/MarketProfile';
import { formatMultiple, riverLine, type PeRiverModel, type RiverPercentile } from '../../fundamentals/peRiver';

/** Light (cheap multiple) to dark (expensive multiple) in one hue: P10 … P90. */
export const BAND_COLORS: Record<RiverPercentile, string> = {
  10: '#cfe3ff',
  30: '#9cc4fb',
  50: '#6ba3f2',
  70: '#3f7fd9',
  90: '#2a5ba8',
};
/** Price line: the site's warning amber, clearly apart from the blue bands and from 紅漲綠跌. */
export const PRICE_COLOR = '#e0a43a';

export interface BandSeriesData {
  p: RiverPercentile;
  multiple: number;
  /** Axis label, e.g. 「18.4x」. */
  label: string;
  color: string;
  /** Whitespace (time only) where the P/E is not valid, so the band breaks. */
  points: { time: string; value?: number }[];
}

export interface RiverChartData {
  price: { time: string; value: number }[];
  bands: BandSeriesData[];
  /** P/E at each date (null when not valid), for the hover readout. */
  peByDate: Record<string, number | null>;
}

/** Chart-ready series: market-local `YYYY-MM-DD` times, one point per week, strictly ascending. */
export function buildRiverChartData(model: PeRiverModel, market: MarketProfile): RiverChartData {
  const keep: number[] = [];
  const dates: string[] = [];
  model.points.forEach((point, index) => {
    const day = calendarDate(point.time, market);
    if (dates.length > 0 && day <= dates[dates.length - 1]!) return;
    dates.push(day);
    keep.push(index);
  });
  const price = keep.map((index, i) => ({ time: dates[i]!, value: model.points[index]!.close }));
  const peByDate: Record<string, number | null> = {};
  keep.forEach((index, i) => {
    peByDate[dates[i]!] = model.points[index]!.pe;
  });
  const bands = model.levels.map<BandSeriesData>((level) => {
    const values = riverLine(model, level);
    return {
      p: level.p,
      multiple: level.multiple,
      label: formatMultiple(level.multiple),
      color: BAND_COLORS[level.p],
      points: keep.map((index, i) => {
        const value = values[index];
        return value === null || value === undefined ? { time: dates[i]! } : { time: dates[i]!, value };
      }),
    };
  });
  return { price, bands, peByDate };
}
