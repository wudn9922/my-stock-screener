import { useEffect, useState, type FormEvent } from 'react';
import type { AppStore } from '../app/AppStore';
import type { Drawing, DrawingStyle } from '../drawing/DrawingModel';
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
      if (!/^#[0-9a-fA-F]{6}$/.test(draft.color)) throw new Error('Color must be a six-digit hex value.');
      if (!Number.isFinite(draft.lineWidth) || draft.lineWidth < 1 || draft.lineWidth > 4) throw new Error('Line width must be from 1 through 4.');
      if (draft.widthMode !== undefined && draft.widthMode !== 'pixels' && draft.widthMode !== 'atr') throw new Error('Line width mode is invalid.');
      if (!Number.isFinite(draft.opacity) || draft.opacity! < 0 || draft.opacity! > 1) throw new Error('Opacity must be from 0 through 1.');
      if (filled && (!Number.isFinite(draft.fillOpacity) || draft.fillOpacity! < 0 || draft.fillOpacity! > 0.3)) throw new Error('Fill opacity must be from 0 through 0.3.');
      if (!levelsValid) throw new Error('Fibonacci levels must contain 2–32 distinct numbers from 0 through 1.');
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
    <section className="research-panel drawing-settings" aria-label="Drawing settings">
      <div className="section-heading">
        <span>DRAWING SETTINGS</span>
        <span>{drawing.type}</span>
      </div>
      {locked && <p className="research-disclaimer">This drawing is locked. Style and level settings are disabled; visibility and unlock remain available.</p>}
      <form className="research-form" onSubmit={save}>
        <label>
          Line color
          <input aria-label="Drawing line color" type="color" value={draft.color} disabled={locked} onChange={(event) => setDraft({ ...draft, color: event.target.value })} />
        </label>
        <label>
          Line width
          <select aria-label="Drawing line width" value={draft.widthMode === 'atr' ? 'atr' : String(draft.lineWidth)} disabled={locked} onChange={(event) => {
            const selected = event.target.value;
            setDraft({ ...draft, widthMode: selected === 'atr' ? 'atr' : 'pixels', lineWidth: selected === 'atr' ? 1 : Number(selected) });
          }}>
            <option value="atr">0.02 ATR (14)</option>
            {[1, 2, 3, 4].map((width) => <option key={width} value={width}>{width}px</option>)}
          </select>
        </label>
        <label>
          Line style
          <select aria-label="Drawing line style" value={draft.lineStyle} disabled={locked} onChange={(event) => setDraft({ ...draft, lineStyle: event.target.value as DrawingStyle['lineStyle'] })}>
            <option value="solid">Solid</option>
            <option value="dashed">Dashed</option>
            <option value="dotted">Dotted</option>
          </select>
        </label>
        <label>
          Line opacity · {(draft.opacity ?? 1).toFixed(2)}
          <input aria-label="Drawing opacity" type="range" min="0" max="1" step="0.05" value={draft.opacity ?? 1} disabled={locked} onChange={(event) => setDraft({ ...draft, opacity: Number(event.target.value) })} />
        </label>
        {filled && (
          <label>
            Fill opacity · {(draft.fillOpacity ?? 0.08).toFixed(2)}
            <input aria-label="Drawing fill opacity" type="range" min="0" max="0.3" step="0.01" value={draft.fillOpacity ?? 0.08} disabled={locked} onChange={(event) => setDraft({ ...draft, fillOpacity: Number(event.target.value) })} />
          </label>
        )}
        {fib && (
          <>
            <label>
              Fibonacci levels · comma-separated
              <input aria-label="Fibonacci levels" aria-invalid={!levelsValid} value={levels} disabled={locked} onChange={(event) => setLevels(event.target.value)} />
            </label>
            <p className="small muted">Enter 2–32 distinct values from 0 to 1. Unchecked levels remain hidden.</p>
            {levelsValid && parsedLevels.map((level) => (
              <label className="research-checkbox" key={level}>
                <input type="checkbox" aria-label={`Show Fibonacci label ${level}`} checked={!draft.hiddenLevels?.includes(level)} disabled={locked} onChange={(event) => setHidden(level, !event.target.checked)} />
                <span>Show level and label {level}</span>
              </label>
            ))}
            <label className="research-checkbox">
              <input type="checkbox" aria-label="Show Fibonacci labels" checked={draft.labelsVisible !== false} disabled={locked} onChange={(event) => setDraft({ ...draft, labelsVisible: event.target.checked })} />
              <span>Show Fibonacci labels</span>
            </label>
          </>
        )}
        <label className="research-checkbox">
          <input aria-label="Drawing visible" type="checkbox" checked={visible} onChange={(event) => {
            const next = event.target.checked;
            setVisible(next);
            if (locked) {
              onCancelGesture();
              store.updateDrawing(drawing.id, { visible: next });
            }
          }} />
          <span>Visible</span>
        </label>
        {error && <p className="research-error" role="alert">{error}</p>}
        {!locked ? (
          <>
            <button className="primary-button" type="submit" style={{ minHeight: 44 }}>Save drawing</button>
            <button type="button" onClick={updateDefault} style={{ minHeight: 44 }}>Save style as default for new {drawing.type} drawings</button>
          </>
        ) : (
          <button type="button" onClick={() => { onCancelGesture(); store.mutateDrawing(drawing.id, 'lock'); }} style={{ minHeight: 44 }}>Unlock drawing</button>
        )}
      </form>
      <p className="small muted">ATR width maps 0.02 × 14-bar ATR through the price scale to a 0.5–4 CSS-pixel stroke, with at least one raster pixel. Native chart lines round to 1–4 px. New drawings use ATR by default, while saved drawings keep their current width mode. Style defaults apply to future drawings only; Fibonacci defaults keep the standard seven levels, and only matching default levels can be hidden.</p>
    </section>
  );
}
