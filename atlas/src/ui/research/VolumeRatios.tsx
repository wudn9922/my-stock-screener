import { useState, type ReactNode } from 'react';
import { volumeStructure, type DirectionalVolumeFeature } from '../../report/research';
import { num, ResearchNote, Stat } from './Common';

/** Shared directional ratios for stocks and indices, independently of abnormal volume. */
export function VolumeRatios({ features, children }: { features: readonly DirectionalVolumeFeature[]; children: ReactNode }) {
  const [window, setWindow] = useState(20);
  const structure = volumeStructure(features, window);
  const ratio = (value: typeof structure.upShrink) =>
    `${value.numerator}/${value.denominator} = ${value.pct === null ? 'N/A' : num(value.pct, 1) + '%'}`;
  return <>
    <h3>上漲／下跌放量與縮量比例</h3>
    <div className="research-controls">
      <label>觀測窗口
        <select aria-label="價量觀測窗口" value={window} onChange={event => setWindow(Number(event.target.value))}>
          {[5, 10, 20].map(days => <option key={days} value={days}>{days} 個交易日</option>)}
        </select>
      </label>
    </div>
    <dl className="research-stats">
      <Stat label="上漲縮量比例" value={ratio(structure.upShrink)} />
      <Stat label="下跌放量比例" value={ratio(structure.downExpand)} />
      <Stat label="上漲放量比例" value={ratio(structure.upExpand)} />
      <Stat label="下跌縮量比例" value={ratio(structure.downShrink)} />
    </dl>
    <ResearchNote title={`計算說明 · 有效觀測 ${structure.observations}/${window} 日${structure.observations < Math.max(5, window * 0.8) ? ' · 樣本不足' : ''}`}>
      {children}
    </ResearchNote>
  </>;
}
