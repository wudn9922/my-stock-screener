import { HelpDisclosure } from './HelpDisclosure';
import { useState } from 'react';
import type { AppStore } from '../app/AppStore';
import { canPersist } from '../auth/storage';
import type { Timeframe } from '../market-data/Timeframes';
import type { Drawing } from '../drawing/DrawingModel';

const ownedDrawing = (drawing: Drawing, timeframe: Timeframe) =>
  Array.isArray(drawing.scope.timeframes) && drawing.scope.timeframes.length === 1 && drawing.scope.timeframes[0] === timeframe;

/** Existing chart settings own these objects; daily monitor parameters are independent. */
export function setTimeframeStudiesLocked(store: AppStore, symbol: string, timeframe: Timeframe, locked: boolean) {
  const state = store.symbol(symbol);
  if (state.preferences.timeframe !== timeframe) throw new Error('週期已切換，請在目前週期重新操作。');
  const averages = state.indicators.filter(indicator => indicator.scope.timeframe === timeframe && indicator.type !== 'Volume');
  for (const indicator of averages) if (indicator.locked !== locked) store.updateIndicator(symbol, indicator.id, { locked });
  if (state.drawings.some(drawing => ownedDrawing(drawing, timeframe) && drawing.locked !== locked)) {
    store.commitDrawings(symbol, state.drawings.map(drawing => ownedDrawing(drawing, timeframe) ? { ...drawing, locked } : drawing), locked ? 'lock' : 'unlock', timeframe);
  }
}

export function TimeframeStudyLock({ store, symbol, timeframe }: { store: AppStore; symbol: string; timeframe: Timeframe }) {
  const [error, setError] = useState('');
  const state = store.symbol(symbol);
  const averages = state.indicators.filter(indicator => indicator.scope.timeframe === timeframe && indicator.type !== 'Volume');
  const drawings = state.drawings.filter(drawing => ownedDrawing(drawing, timeframe));
  const objects = [...averages, ...drawings];
  const locked = objects.length > 0 && objects.every(object => object.locked);
  return <section className="timeframe-study-lock" aria-label={`${timeframe} 週期專屬設定`}>
    <p><b>{timeframe}</b> · 均線 {averages.length} 條／畫線 {drawings.length} 條 <small>{canPersist() ? '變更自動保存' : '僅本次瀏覽，重新開啟即消失'}</small></p>
    <button type="button" className="ghost-button" disabled={!objects.length} aria-pressed={locked} onClick={() => {
      try { setTimeframeStudiesLocked(store, symbol, timeframe, !locked); setError(''); }
      catch (caught) { setError(caught instanceof Error ? caught.message : '設定尚未完成，請重試。'); }
    }}>{locked ? '解除固定此週期' : '固定此週期設定'}</button>
    {error && <p role="status">{error}</p>}
    <HelpDisclosure label="週期保存與固定說明">
      每檔股票的每個週期各自保留均線、畫線與鎖定狀態。新增及修改會依既有儲存授權自動保存；固定後可隱藏或解除固定，再修改參數與畫線。畫線請用圖表的繪圖工具新增，或點線編輯。日 K 監控參數維持獨立。
    </HelpDisclosure>
  </section>;
}
