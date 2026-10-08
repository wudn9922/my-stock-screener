import { describe, expect, it } from 'vitest';
import { TickMarkType, type Time, type UTCTimestamp } from 'lightweight-charts';
import { marketTimeOptions } from '../src/chart/MarketTimeLabels';
import type { Drawing, DrawingProjection } from '../src/drawing/DrawingModel';
import { getMarketProfile } from '../src/market-data/MarketProfile';
import { measurementLabel } from '../src/tools/Measurement';

type PublicTimeLabelOptions = {
  localization?: { timeFormatter?: (time: Time) => string };
  timeScale?: {
    tickMarkFormatter?: (time: Time, type: TickMarkType) => string;
  };
};

function labels(symbol: string, timeframe: '1D' | '1M' | '5m') {
  return marketTimeOptions(getMarketProfile(symbol), timeframe) as PublicTimeLabelOptions;
}

function priceRange(symbol: string): Drawing {
  return {
    id: `${symbol}-range`,
    symbol,
    type: 'price-range',
    points: [
      { time: 1_790_000_000, logical: 10, price: 100, timeframe: '1D' },
      { time: 1_790_086_400, logical: 11, price: 110, timeframe: '1D' },
    ],
    locked: false,
    visible: true,
    scope: { timeframes: 'all' },
    style: { color: '#5ca9ff', lineWidth: 2 },
  };
}

const projection: DrawingProjection = {
  toPoint: (anchor) => ({ x: anchor.logical * 10, y: anchor.price }),
  toAnchor: () => null,
  logicalAt: (anchor) => anchor.logical,
  width: () => 800,
  height: () => 400,
};

describe('market-aware chart and measurement labels', () => {
  it('uses Taipei calendar dates for Taiwan monthly crosshair and month ticks', () => {
    const timestamp = (Date.parse('2026-09-30T16:00:00Z') / 1000) as UTCTimestamp;
    const options = labels('2330.TW', '1M');

    expect(options.localization?.timeFormatter?.(timestamp)).toBe('01/10/2026');
    expect(options.timeScale?.tickMarkFormatter?.(timestamp, TickMarkType.Month)).toBe('Oct');
  });

  it('uses Taipei wall time for Taiwan intraday crosshair and time ticks', () => {
    const timestamp = (Date.parse('2026-10-08T01:00:00Z') / 1000) as UTCTimestamp;
    const options = labels('6488.TWO', '5m');
    const crosshair = options.localization?.timeFormatter?.(timestamp) ?? '';

    expect(crosshair).toContain('09:00');
    expect(options.timeScale?.tickMarkFormatter?.(timestamp, TickMarkType.Time)).toBe('09:00');
  });

  it('keeps US date labels on canonical UTC dates', () => {
    const timestamp = (Date.parse('2026-10-01T00:00:00Z') / 1000) as UTCTimestamp;
    const options = labels('AAPL', '1M');

    expect(options.localization?.timeFormatter?.(timestamp)).toBe('01/10/2026');
    expect(options.timeScale?.tickMarkFormatter?.(timestamp, TickMarkType.Month)).toBe('Oct');
  });

  it('shows TWD on Taiwan measurements and the established dollar label for US measurements', () => {
    const taiwanLabel = measurementLabel(priceRange('2330.TW'), projection);
    const usLabel = measurementLabel(priceRange('AAPL'), projection);

    expect(taiwanLabel).toContain('TWD 10.00');
    expect(usLabel).toContain('$10.00');
    expect(usLabel).not.toContain('USD');
  });
});
