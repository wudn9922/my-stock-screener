import { useEffect, useState, type FormEvent } from 'react';
import type { AppStore } from '../app/AppStore';
import { toolNames, type Drawing, type DrawingStyle } from '../drawing/DrawingModel';
import { DEFAULT_FIB_LEVELS } from '../tools/Fibonacci';

const defaultFibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

function styleDraft(style: DrawingStyle): DrawingStyle {
  return {
    ...structuredClone(style),
    opacity: style.opacity ?? 1,
    fillOpacity: style.fillOpacity ?? 0.08,
    lineStyle: style.lineStyle ?? 'solid',
    labelsVisible: style.labelsVisible ?? true,
    ...(style.hiddenLevels ? { hiddenLevels: [...style.hiddenLevels] } : {}),
  };
}

function levelsText(levels: number[] | undefined) {
  return (levels ?? defaultFibLevels).join(', ');
}

export function DrawingSettings({
  store,
  drawing,
  onCancelGesture,
}: {
  store: AppStore;
  drawing: Drawing;
  onCancelGesture: () => void;
}) {
  const [draft, setDraft] = useState(() => styleDraft(drawing.style));
  const [levels, setLevels] = useState(() => levelsText(drawing.levels));
  const [visible, setVisible] = useState(drawing.visible);
  const [error, setError] = useState('');

  useEffect(() => {
    setDraft(styleDraft(drawing.style));
    setLevels(levelsText(drawing.levels));
    setVisible(drawing.visible);
    setError('');
  }, [drawing.id, drawing.style, drawing.levels, drawing.visible]);

  const locked = drawing.locked;
  const fib = drawing.type === 'fibonacci';
  const filled = drawing.type === 'rectangle' || drawing.type === 'channel';
  const parsedLevels = levels.split(',').map((value) => Number(value.trim()));
  const levelsValid = !fib || (
    parsedLevels.length >= 2 && parsedLevels.length <= 32 &&
    parsedLevels.every((value) => Number.isFinite(value) && value >= 0 && value <= 1) &&
    new Set(parsedLevels).size === parsedLevels.length
  );

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    try {
      if (locked) return;
      if (!/^#[0-9a-fA-F]{6}$/.test(draft.color)) throw new Error('顏色必須是六位數的十六進位色碼。');
      if (!Number.isFinite(draft.lineWidth) || draft.lineWidth < 1 || draft.lineWidth > 4) throw new Error('線寬必須介於 1 到 4。');
      if (draft.widthMode !== undefined && draft.widthMode !== 'pixels' && draft.widthMode !== 'atr') throw new Error('線寬模式無效。');
      if (!Number.isFinite(draft.opacity) || draft.opacity! < 0 || draft.opacity! > 1) throw new Error('不透明度必須介於 0 到 1。');
      if (filled && (!Number.isFinite(draft.fillOpacity) || draft.fillOpacity! < 0 || draft.fillOpacity! > 0.3)) throw new Error('填色不透明度必須介於 0 到 0.3。');
      if (!levelsValid) throw new Error('費波那契層級需為 2–32 個介於 0 到 1 的不重複數值。');
      onCancelGesture();
      const style = structuredClone(draft);
      if (fib) style.hiddenLevels = parsedLevels.filter((level) => draft.hiddenLevels?.includes(level));
      store.updateDrawing(drawing.id, { style, ...(fib ? { levels: parsedLevels } : {}), visible });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const updateDefault = () => {
    setError('');
    try {
      if (locked) return;
      const defaults = structuredClone(store.getSnapshot().app.drawingDefaults);
      const style = structuredClone(draft);
      if (fib) style.hiddenLevels = DEFAULT_FIB_LEVELS.filter((level) => draft.hiddenLevels?.includes(level));
      defaults[drawing.type] = style;
      store.updateApp({ drawingDefaults: defaults });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const setHidden = (level: number, hidden: boolean) => {
    const current = new Set(draft.hiddenLevels ?? []);
    if (hidden) current.add(level);
    else current.delete(level);
    setDraft({ ...draft, hiddenLevels: [...current] });
  };

  return (
    <section className="research-panel drawing-settings" aria-label="畫線設定">
      <div className="section-heading">
        <span>畫線設定</span>
        <span>{toolNames[drawing.type]}</span>
      </div>
      {locked && <p className="research-disclaimer">此畫線已鎖定：樣式與層級設定停用，仍可隱藏／顯示或解鎖。</p>}
      <form className="research-form" onSubmit={save}>
        <label>
          線條顏色
          <input aria-label="線條顏色" type="color" value={draft.color} disabled={locked} onChange={(event) => setDraft({ ...draft, color: event.target.value })} />
        </label>
        <label>
          線寬
          <select aria-label="線寬" value={draft.widthMode === 'atr' ? 'atr' : String(draft.lineWidth)} disabled={locked} onChange={(event) => {
            const selected = event.target.value;
            setDraft({ ...draft, widthMode: selected === 'atr' ? 'atr' : 'pixels', lineWidth: selected === 'atr' ? 1 : Number(selected) });
          }}>
            <option value="atr">0.02 ATR (14)</option>
            {[1, 2, 3, 4].map((width) => <option key={width} value={width}>{width}px</option>)}
          </select>
        </label>
        <label>
          線條樣式
          <select aria-label="線條樣式" value={draft.lineStyle} disabled={locked} onChange={(event) => setDraft({ ...draft, lineStyle: event.target.value as DrawingStyle['lineStyle'] })}>
            <option value="solid">實線</option>
            <option value="dashed">虛線</option>
            <option value="dotted">點線</option>
          </select>
        </label>
        <label>
          線條不透明度 · {(draft.opacity ?? 1).toFixed(2)}
          <input aria-label="線條不透明度" type="range" min="0" max="1" step="0.05" value={draft.opacity ?? 1} disabled={locked} onChange={(event) => setDraft({ ...draft, opacity: Number(event.target.value) })} />
        </label>
        {filled && (
          <label>
            填色不透明度 · {(draft.fillOpacity ?? 0.08).toFixed(2)}
            <input aria-label="填色不透明度" type="range" min="0" max="0.3" step="0.01" value={draft.fillOpacity ?? 0.08} disabled={locked} onChange={(event) => setDraft({ ...draft, fillOpacity: Number(event.target.value) })} />
          </label>
        )}
        {fib && (
          <>
            <label>
              費波那契層級（以逗號分隔）
              <input aria-label="費波那契層級" aria-invalid={!levelsValid} value={levels} disabled={locked} onChange={(event) => setLevels(event.target.value)} />
            </label>
            <p className="small muted">輸入 2–32 個介於 0 到 1 的不重複數值；未勾選的層級會隱藏。</p>
            {levelsValid && parsedLevels.map((level) => (
              <label className="research-checkbox" key={level}>
                <input type="checkbox" aria-label={`顯示費波那契層級 ${level}`} checked={!draft.hiddenLevels?.includes(level)} disabled={locked} onChange={(event) => setHidden(level, !event.target.checked)} />
                <span>顯示層級與標籤 {level}</span>
              </label>
            ))}
            <label className="research-checkbox">
              <input type="checkbox" aria-label="顯示費波那契標籤" checked={draft.labelsVisible !== false} disabled={locked} onChange={(event) => setDraft({ ...draft, labelsVisible: event.target.checked })} />
              <span>顯示費波那契標籤</span>
            </label>
          </>
        )}
        <label className="research-checkbox">
          <input aria-label="顯示畫線" type="checkbox" checked={visible} onChange={(event) => {
            const next = event.target.checked;
            setVisible(next);
            if (locked) {
              onCancelGesture();
              store.updateDrawing(drawing.id, { visible: next });
            }
          }} />
          <span>顯示</span>
        </label>
        {error && <p className="research-error" role="alert">{error}</p>}
        {!locked ? (
          <>
            <button className="primary-button" type="submit" style={{ minHeight: 44 }}>儲存畫線</button>
            <button type="button" onClick={updateDefault} style={{ minHeight: 44 }} className="ghost-button">將此樣式設為新{toolNames[drawing.type]}的預設</button>
          </>
        ) : (
          <button type="button" onClick={() => { onCancelGesture(); store.mutateDrawing(drawing.id, 'lock'); }} style={{ minHeight: 44 }} className="ghost-button">解鎖畫線</button>
        )}
      </form>
      <p className="small muted">ATR 線寬＝0.02 × 14 根 K 棒 ATR 換算成 0.5–4 像素（至少 1 個實體像素）。新畫線預設使用 ATR 線寬，已儲存的畫線保留原設定。預設樣式只套用於之後的新畫線；費波那契預設維持標準七個層級。</p>
    </section>
  );
}
