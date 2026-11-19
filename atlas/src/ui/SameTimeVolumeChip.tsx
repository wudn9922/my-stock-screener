import type { SameTimeVolume } from '../market-data/SameTimeVolume';
import type { IntradayIndexVolume } from '../market-data/IntradayVolume';
import { formatMarketVolume } from './format';

/**
 * Compact readouts beside the volume legend: today's volume against the previous session's volume up
 * to the same exchange time, and why an index's live bar has no volume when its source publishes none.
 * Insufficient data renders nothing rather than a zero or a stale ratio.
 */
export function SameTimeVolumeChip({ symbol, ratio, indexVolume, indexVolumeMissing }: {
  symbol: string;
  ratio: SameTimeVolume | null;
  /** Only for an index daily chart whose live bar has no source volume. */
  indexVolume: IntradayIndexVolume | null;
  indexVolumeMissing: boolean;
}) {
  const showRatio = ratio?.status === 'ok' && !ratio.stale;
  const showMissing = indexVolumeMissing && indexVolume?.status === 'unavailable';
  if (!showRatio && !showMissing) return null;
  return <>
    {showRatio && <div className="volume-readout-chip" data-testid="same-time-volume"
      title={`今日 ${ratio.date} 開盤至 ${ratio.cutoff}（交易所時間）累計 ${formatMarketVolume(ratio.currentVolume, symbol)}，` +
        `前一交易日 ${ratio.previousDate} 同時段 ${formatMarketVolume(ratio.previousVolume, symbol)}。` +
        '只用已完成的正常交易時段 5 分 K；資料不足時不顯示。'}>
      <span className="indicator-name">量比(同時間)</span>
      <span className="indicator-value">{ratio.ratio.toFixed(2)}×</span>
    </div>}
    {showMissing && <div className="volume-readout-chip muted" data-testid="index-volume-missing"
      title={`${indexVolume.reason}。盤中只有交易所 MIS 指數列，沒有與歷史日線同口徑的成交量（只有成交金額或空白）；` +
        '單位不同不混用，因此今日 K 盤中不顯示量，待日線來源補上。'}>
      <span className="indicator-name">今日量：盤中來源未提供</span>
    </div>}
  </>;
}
