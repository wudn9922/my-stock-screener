import type { Bar } from '../market-data/MarketDataProvider';

export function volume(bars: readonly Bar[]): { time: number; value: number }[] {
  return bars.map((bar) => ({ time: bar.time, value: bar.volume }));
}

export interface ColoredVolumeValue {
  time: number;
  value: number;
  color: string;
}

export const VOLUME_SMA_PERIOD = 20;

const volumePalette = { up: '#39baa0b3', down: '#ef6b7bb3' };

/** Up/down volume colors (`#rrggbb`), shared by the chart's legacy volume and Volume indicator. */
export function setVolumeColors(up: string, down: string) {
  volumePalette.up = `${up}b3`;
  volumePalette.down = `${down}b3`;
}

export function coloredVolume(bars: readonly Bar[]): ColoredVolumeValue[] {
  return bars.map((bar) => ({
    time: bar.time,
    value: bar.volume,
    color: bar.close >= bar.open ? volumePalette.up : volumePalette.down,
  }));
}

export function volumeSma(
  bars: readonly Bar[],
  period = VOLUME_SMA_PERIOD,
): { time: number; value: number }[] {
  if (!Number.isInteger(period) || period < 1)
    throw new Error('Volume SMA period must be a positive integer');
  let sum = 0;
  const result: { time: number; value: number }[] = [];
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].volume;
    if (i >= period) sum -= bars[i - period].volume;
    if (i >= period - 1) result.push({ time: bars[i].time, value: sum / period });
  }
  return result;
}
