import { useEffect, useState, useSyncExternalStore, type FormEvent } from 'react';
import type { AppStore } from '../app/AppStore';
import type { DrawingStyle, ToolKind } from '../drawing/DrawingModel';

const workspaceTools: Array<{ id: ToolKind; label: string; filled?: boolean; labels?: boolean }> = [
  { id: 'trend', label: '趨勢線' },
  { id: 'horizontal', label: '水平線' },
  { id: 'ray', label: '水平射線' },
  { id: 'rectangle', label: '矩形', filled: true },
  { id: 'fibonacci', label: '費波那契回撤', labels: true },
  { id: 'channel', label: '平行通道', filled: true },
  { id: 'price-range', label: '價格區間測量', labels: true },
  { id: 'date-range', label: '日期區間測量', labels: true },
  { id: 'price-date-range', label: '價格與日期測量', labels: true },
  { id: 'vertical', label: '垂直線' },
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
      if (!/^#[0-9a-fA-F]{6}$/.test(draft.color)) throw new Error('顏色必須是六位數的十六進位色碼。');
      if (!Number.isInteger(draft.lineWidth) || draft.lineWidth < 1 || draft.lineWidth > 4) throw new Error('線寬必須是 1 到 4 的整數。');
      if (draft.widthMode !== 'pixels' && draft.widthMode !== 'atr') throw new Error('線寬模式無效。');
      if (!Number.isFinite(draft.opacity) || draft.opacity! < 0 || draft.opacity! > 1) throw new Error('不透明度必須介於 0 到 1。');
      if (toolConfig.filled && (!Number.isFinite(draft.fillOpacity) || draft.fillOpacity! < 0 || draft.fillOpacity! > 0.3)) throw new Error('填色不透明度必須介於 0 到 0.3。');
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
    <section className="research-panel workspace-settings" aria-label="工作區設定">
      <div className="section-heading">
        <span>新畫線預設樣式</span>
        <span>本機</span>
      </div>
      <p className="research-disclaimer">設定與指標組合儲存在這個瀏覽器。「匯出設定」會備份所有股票的畫線、指標與提醒；行情資料不在備份內。</p>
      <form className="research-form" onSubmit={save}>
        
        <label>
          繪圖工具
          <select aria-label="預設樣式的繪圖工具" value={tool} onChange={(event) => setTool(event.target.value as ToolKind)}>
            {workspaceTools.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
          </select>
        </label>
        <label>
          線條顏色
          <input aria-label="預設線條顏色" type="color" value={draft.color} onChange={(event) => setDraft({ ...draft, color: event.target.value })} />
        </label>
        <label>
          線寬
          <select aria-label="預設線寬" value={draft.widthMode === 'atr' ? 'atr' : String(draft.lineWidth)} onChange={(event) => {
            const selected = event.target.value;
            setDraft({ ...draft, widthMode: selected === 'atr' ? 'atr' : 'pixels', lineWidth: selected === 'atr' ? 1 : Number(selected) });
          }}>
            <option value="atr">0.02 ATR (14)</option>
            {[1, 2, 3, 4].map((width) => <option key={width} value={width}>{width}px</option>)}
          </select>
        </label>
        <label>
          線條樣式
          <select aria-label="預設線條樣式" value={draft.lineStyle} onChange={(event) => setDraft({ ...draft, lineStyle: event.target.value as DrawingStyle['lineStyle'] })}>
            <option value="solid">實線</option>
            <option value="dashed">虛線</option>
            <option value="dotted">點線</option>
          </select>
        </label>
        <label>
          不透明度 · {(draft.opacity ?? 1).toFixed(2)}
          <input aria-label="預設不透明度" type="range" min="0" max="1" step="0.05" value={draft.opacity ?? 1} onChange={(event) => setDraft({ ...draft, opacity: Number(event.target.value) })} />
        </label>
        {toolConfig.filled && (
          <label>
            填色不透明度 · {(draft.fillOpacity ?? 0.08).toFixed(2)}
            <input aria-label="預設填色不透明度" type="range" min="0" max="0.3" step="0.01" value={draft.fillOpacity ?? 0.08} onChange={(event) => setDraft({ ...draft, fillOpacity: Number(event.target.value) })} />
          </label>
        )}
        {toolConfig.labels && (
          <label className="research-checkbox">
            <input type="checkbox" aria-label="預設顯示標籤" checked={draft.labelsVisible !== false} onChange={(event) => setDraft({ ...draft, labelsVisible: event.target.checked })} />
            <span>顯示{tool === 'fibonacci' ? '費波那契' : ''}標籤</span>
          </label>
        )}
        {error && <p className="research-error" role="alert">{error}</p>}
        <button className="primary-button" type="submit" style={{ minHeight: 44 }}>儲存新畫線預設樣式</button>
      </form>
      <p className="small muted">ATR 線寬＝0.02 × 14 根 K 棒 ATR（最小 0.5 像素）；已儲存的線寬維持不變。預設樣式只套用於新畫線。</p>
      <section className="settings-subsection" aria-labelledby="indicator-presets-heading">
        <h3 id="indicator-presets-heading">指標組合</h3>
        {!app.indicatorPresets.length ? <p className="small muted">尚未儲存任何組合。</p> : (
          <ul className="preset-list">
            {app.indicatorPresets.map((preset) => (
              <li key={preset.id}>
                <span><b>{preset.name}</b><small>{preset.indicators.length} 個指標</small></span>
                <button type="button" aria-label={`移除組合 ${preset.name}`} onClick={() => removePreset(preset.id)} style={{ minHeight: 44 }}>移除</button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="settings-subsection" aria-labelledby="debug-setting-heading">
        <h3 id="debug-setting-heading">診斷</h3>
        <label className="research-checkbox">
          <input type="checkbox" aria-label="啟用除錯模式" checked={app.workspace.debug} onChange={(event) => store.updateApp({ workspace: { ...store.getSnapshot().app.workspace, debug: event.target.checked } })} />
          <span>顯示除錯資訊</span>
        </label>
      </section>
    </section>
  );
}
