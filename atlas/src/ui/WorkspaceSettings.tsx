import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import type { AppStore } from '../app/AppStore';
import type { DrawingStyle, ToolKind } from '../drawing/DrawingModel';

const workspaceTools: Array<{ id: ToolKind; label: string; filled?: boolean; labels?: boolean }> = [
  { id: 'trend', label: 'Trend line' },
  { id: 'horizontal', label: 'Horizontal line' },
  { id: 'ray', label: 'Horizontal ray' },
  { id: 'rectangle', label: 'Rectangle', filled: true },
  { id: 'fibonacci', label: 'Fibonacci retracement', labels: true },
  { id: 'channel', label: 'Parallel channel', filled: true },
  { id: 'price-range', label: 'Price range measurement', labels: true },
  { id: 'date-range', label: 'Date range measurement', labels: true },
  { id: 'price-date-range', label: 'Price and date measurement', labels: true },
  { id: 'vertical', label: 'Vertical line' },
];

function styleFor(style: Partial<DrawingStyle> | undefined, filled: boolean): DrawingStyle {
  return {
    color: style?.color ?? '#5ca9ff',
    lineWidth: style?.lineWidth ?? 1,
    widthMode: style ? style.widthMode ?? 'pixels' : 'atr',
    lineStyle: style?.lineStyle ?? 'solid',
    opacity: style?.opacity ?? 1,
    ...(filled ? { fillOpacity: style?.fillOpacity ?? 0.08 } : {}),
    ...(style?.labelsVisible === undefined ? {} : { labelsVisible: style.labelsVisible }),
  };
}

export function WorkspaceSettings({ store }: { store: AppStore }) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const app = snapshot.app;
  const [tool, setTool] = useState<ToolKind>('trend');
  const toolConfig = workspaceTools.find((entry) => entry.id === tool)!;
  const [draft, setDraft] = useState(() => styleFor(app.drawingDefaults[tool], !!toolConfig.filled));
  const [error, setError] = useState('');

  useEffect(() => {
    setDraft(styleFor(app.drawingDefaults[tool], !!toolConfig.filled));
    setError('');
  }, [tool, app.drawingDefaults, toolConfig.filled]);

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    try {
      if (!/^#[0-9a-fA-F]{6}$/.test(draft.color)) throw new Error('Color must be a six-digit hex value.');
      if (!Number.isInteger(draft.lineWidth) || draft.lineWidth < 1 || draft.lineWidth > 4) throw new Error('Line width must be an integer from 1 through 4.');
      if (draft.widthMode !== 'pixels' && draft.widthMode !== 'atr') throw new Error('Line width mode is invalid.');
      if (!Number.isFinite(draft.opacity) || draft.opacity! < 0 || draft.opacity! > 1) throw new Error('Opacity must be from 0 through 1.');
      if (toolConfig.filled && (!Number.isFinite(draft.fillOpacity) || draft.fillOpacity! < 0 || draft.fillOpacity! > 0.3)) throw new Error('Fill opacity must be from 0 through 0.3.');
      const next = structuredClone(store.getSnapshot().app.drawingDefaults);
      next[tool] = structuredClone(draft);
      store.updateApp({ drawingDefaults: next });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const removePreset = (id: string) => {
    const latest = store.getSnapshot().app;
    store.updateApp({ indicatorPresets: latest.indicatorPresets.filter((preset) => preset.id !== id) });
  };

  return (
    <section className="research-panel workspace-settings" aria-label="Workspace settings">
      <div className="section-heading">
        <span>WORKSPACE SETTINGS</span>
        <span>LOCAL</span>
      </div>
      <p className="research-disclaimer">This is a free research prototype. Settings and indicator presets are stored in this browser. Export/Import in the top bar backs up the full workspace settings across symbols; SEC financial facts and market data are not part of that backup.</p>
      <form className="research-form" onSubmit={save}>
        <h3>Defaults for new drawings</h3>
        <label>
          Drawing tool
          <select aria-label="Drawing tool for defaults" value={tool} onChange={(event) => setTool(event.target.value as ToolKind)}>
            {workspaceTools.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
          </select>
        </label>
        <label>
          Line color
          <input aria-label="Default drawing color" type="color" value={draft.color} onChange={(event) => setDraft({ ...draft, color: event.target.value })} />
        </label>
        <label>
          Line width
          <select aria-label="Default drawing width" value={draft.widthMode === 'atr' ? 'atr' : String(draft.lineWidth)} onChange={(event) => {
            const selected = event.target.value;
            setDraft({ ...draft, widthMode: selected === 'atr' ? 'atr' : 'pixels', lineWidth: selected === 'atr' ? 1 : Number(selected) });
          }}>
            <option value="atr">0.02 ATR (14)</option>
            {[1, 2, 3, 4].map((width) => <option key={width} value={width}>{width}px</option>)}
          </select>
        </label>
        <label>
          Line style
          <select aria-label="Default drawing style" value={draft.lineStyle} onChange={(event) => setDraft({ ...draft, lineStyle: event.target.value as DrawingStyle['lineStyle'] })}>
            <option value="solid">Solid</option>
            <option value="dashed">Dashed</option>
            <option value="dotted">Dotted</option>
          </select>
        </label>
        <label>
          Opacity · {(draft.opacity ?? 1).toFixed(2)}
          <input aria-label="Default drawing opacity" type="range" min="0" max="1" step="0.05" value={draft.opacity ?? 1} onChange={(event) => setDraft({ ...draft, opacity: Number(event.target.value) })} />
        </label>
        {toolConfig.filled && (
          <label>
            Fill opacity · {(draft.fillOpacity ?? 0.08).toFixed(2)}
            <input aria-label="Default fill opacity" type="range" min="0" max="0.3" step="0.01" value={draft.fillOpacity ?? 0.08} onChange={(event) => setDraft({ ...draft, fillOpacity: Number(event.target.value) })} />
          </label>
        )}
        {toolConfig.labels && (
          <label className="research-checkbox">
            <input type="checkbox" aria-label="Show default Fibonacci labels" checked={draft.labelsVisible !== false} onChange={(event) => setDraft({ ...draft, labelsVisible: event.target.checked })} />
            <span>Show {toolConfig.labels && tool === 'fibonacci' ? 'Fibonacci ' : ''}labels</span>
          </label>
        )}
        {error && <p className="research-error" role="alert">{error}</p>}
        <button className="primary-button" type="submit" style={{ minHeight: 44 }}>Save new-drawing defaults</button>
      </form>
      <p className="small muted">ATR width maps to 0.02 × 14-bar ATR in price space, with a 0.5 CSS-pixel minimum; native chart lines round to 1–4 px. Existing saved widths stay fixed unless you change their mode. Defaults apply to new drawings.</p>
      <section className="settings-subsection" aria-labelledby="indicator-presets-heading">
        <h3 id="indicator-presets-heading">Indicator presets</h3>
        {!app.indicatorPresets.length ? <p className="small muted">No saved presets.</p> : (
          <ul className="preset-list">
            {app.indicatorPresets.map((preset) => (
              <li key={preset.id}>
                <span><b>{preset.name}</b><small>{preset.indicators.length} indicator{preset.indicators.length === 1 ? '' : 's'}</small></span>
                <button type="button" aria-label={`Remove preset ${preset.name}`} onClick={() => removePreset(preset.id)} style={{ minHeight: 44 }}>Remove</button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="settings-subsection" aria-labelledby="debug-setting-heading">
        <h3 id="debug-setting-heading">Diagnostics</h3>
        <label className="research-checkbox">
          <input type="checkbox" aria-label="Enable workspace debug mode" checked={app.workspace.debug} onChange={(event) => store.updateApp({ workspace: { ...store.getSnapshot().app.workspace, debug: event.target.checked } })} />
          <span>Enable debug details</span>
        </label>
      </section>
    </section>
  );
}
