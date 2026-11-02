/** Synthetic contract fixture: values exercise rendering, never published as research results. */
import type { SignalState } from '../../src/report/breadthSignals';
export const signalTrade = {
  signalDate: '2026-10-27',
  entryDate: '2026-10-28',
  entry: 6000,
  entryRef: 6000,
  stop: 5700,
  target: 6300,
  exitDate: null,
  exit: null,
  daysHeld: 3,
  maxHold: 60,
  expires: '2027-01-21',
  return: null,
};
export function signalIndex(
  state: SignalState = 'active',
  symbol = '^GSPC',
  name = 'S&P 500',
) {
  return {
    symbol,
    name,
    state,
    swing: 0.05,
    close: 6010,
    drawdown60: -0.051,
    breadthOversold: true,
    indexPulledBack: true,
    triggerPrice: 6015,
    trade: ['none', 'watch'].includes(state)
      ? null
      : {
          ...signalTrade,
          ...(state === 'new'
            ? { entry: null, entryDate: null, expires: null, daysHeld: 0 }
            : {}),
          ...(['target', 'stop', 'timeout'].includes(state)
            ? {
                exitDate: '2026-10-30',
                exit: state === 'target' ? 6300 : 5700,
                return: state === 'target' ? 0.05 : -0.05,
              }
            : {}),
        },
    stats: {
      trades: 70,
      hitRate: 0.66,
      baseRate: 0.48,
      hitRateEarly: 0.64,
      hitRateLate: 0.68,
      avgReturn: 0.012,
      medianDays: 18,
      winnersMaeP90: -0.04,
      nearSwingLowRate: 0.5,
    },
    recentEvents: [],
    markers: ['2026-10-27'],
  };
}
export function breadthSignalsFixture(state: SignalState = 'active') {
  return {
    version: 1,
    generatedAt: '2026-11-02T00:00:00Z',
    markets: {
      us: {
        applicable: true,
        asOf: '2026-10-30',
        readings: {
          ma20: { pct: 18, percentile: 6.5, zone: 'oversold' },
          ma60: { pct: 42, percentile: 36, zone: 'neutral' },
        },
        thresholds: {
          ma20: { p5: 14, p10: 22, p50: 50, p90: 76, p95: 81 },
          ma60: { p5: 12, p10: 18, p50: 48, p90: 78, p95: 84 },
        },
        lowSignal: {
          rule: '寬度低於歷史 P10 且指數回檔達波段幅度。',
          indices: [signalIndex(state)],
        },
        overbought: {
          active: false,
          note: '超買不是賣出訊號。',
          dropFirstRate: 0.4,
          baseRate: 0.41,
          events: 61,
        },
        history: {
          dates: [
            '2026-10-26',
            '2026-10-27',
            '2026-10-28',
            '2026-10-29',
            '2026-10-30',
          ],
          ma20Pct: [30, 18, 22, 19, 18],
          ma60Pct: [46, 45, 44, 43, 42],
        },
        calibration: {
          generatedAt: '2026-11-01T00:00:00Z',
          sample: { start: '2004-01-02', end: '2026-10-30' },
          rulesVersion: 1,
        },
      },
      tw: {
        applicable: false,
        reason: '台股回測未勝過隨便進場，因此不提供訊號；寬度數字僅供參考。',
      },
    },
  };
}
export function breadthFixture() {
  const market = {
    asOf: '2026-10-30',
    universe: '測試全市場名單',
    scope: 'full',
    universeCount: 1000,
    segments: {
      all: {
        count: 1000,
        ma20: { above: 180, total: 1000, pct: 18 },
        ma60: { above: 420, total: 1000, pct: 42 },
      },
    },
    history: {
      dates: ['2026-10-29', '2026-10-30'],
      ma20Pct: [19, 18],
      ma60Pct: [43, 42],
    },
  };
  return { version: 1, markets: { us: market, tw: structuredClone(market) } };
}
