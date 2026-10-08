import { useId, useState, type FormEvent } from 'react';
import { Eye, EyeOff, LockKeyhole, UnlockKeyhole, Settings2, Trash2, Plus } from 'lucide-react';
import { IconButton } from './IconButton';
import type { AppStore } from '../app/AppStore';
import { reportError } from '../errors/UserErrors';
import {
  indicatorColors,
  type IndicatorInstance,
  type IndicatorType,
  type PriceSource,
} from '../indicators/IndicatorRegistry';
import type { Timeframe } from '../market-data/MarketDataProvider';

export function IndicatorPanel({
  store,
  symbol,
  timeframe,
  indicators,
}: {
  store: AppStore;
  symbol: string;
  timeframe: Timeframe;
  indicators: IndicatorInstance[];
}) {
  const periodId = useId();
  const typeId = useId();
  const [period, setPeriod] = useState('24');
  const [indicatorType, setIndicatorType] = useState<IndicatorType>('SMA');
  const [editing, setEditing] = useState<string | null>(null);
  const [presetName, setPresetName] = useState('');
  const [selectedPreset, setSelectedPreset] = useState('');
  const [error, setError] = useState('');
  const presets = store.getSnapshot().app.indicatorPresets;
  const indicatorHome = store.symbol(symbol).preferences.ownershipMigration?.indicatorHome;
  const isVolume = indicatorType === 'Volume';

  const add = () => {
    setError('');
    try {
      const p = isVolume ? 1 : Number(period);
      if (!isVolume && (!Number.isInteger(p) || p < 1 || p > 5000)) return;
      store.addIndicator({
        id: crypto.randomUUID(),
        symbol,
        type: indicatorType,
        period: p,
        // Volume reads bar.volume in the registry; close is retained for the shared model contract.
        source: 'close',
        visible: true,
        locked: false,
        lineWidth: isVolume ? 2 : 1,
        widthMode: isVolume ? 'pixels' : 'atr',
        color: indicatorColors[indicators.length % indicatorColors.length],
        scope: { timeframe },
      });
    } catch (caught) {
      setError(reportError('ui', caught));
    }
  };

  const savePreset = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = presetName.trim();
    if (!name) return;
    setError('');
    try {
      store.saveIndicatorPreset(name, symbol);
      setPresetName('');
    } catch (caught) {
      setError(reportError('ui', caught));
    }
  };

  const applyPreset = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedPreset) return;
    setError('');
    try {
      store.applyIndicatorPreset(selectedPreset, symbol);
    } catch (caught) {
      setError(reportError('ui', caught));
    }
  };

  const saveSettings = (event: FormEvent<HTMLFormElement>, indicator: IndicatorInstance) => {
    event.preventDefault();
    setError('');
    try {
      const data = new FormData(event.currentTarget);
      if (indicator.type === 'Volume') {
        store.updateIndicator(symbol, indicator.id, { color: String(data.get('color')) });
        setEditing(null);
        return;
      }

      const nextPeriod = Number(data.get('period'));
      if (Number.isInteger(nextPeriod) && nextPeriod >= 1 && nextPeriod <= 5000) {
        const selectedWidth = String(data.get('width'));
        const widthMode = selectedWidth === 'atr' ? 'atr' : 'pixels';
        const lineWidth = selectedWidth === 'atr' ? 1 : Number(selectedWidth);
        if (selectedWidth !== 'atr' && ![1, 2, 3, 4].includes(lineWidth)) return;
        store.updateIndicator(symbol, indicator.id, {
          period: nextPeriod,
          source: data.get('source') as PriceSource,
          color: String(data.get('color')),
          lineWidth: lineWidth as 1 | 2 | 3 | 4,
          widthMode,
        });
        setEditing(null);
      }
    } catch (caught) {
      setError(reportError('ui', caught));
    }
  };

  return (
    <section className="indicator-panel">
      <div className="section-heading">
        <span>MOVING AVERAGES</span>
        <span>{indicators.length}</span>
      </div>
      <p className="muted small" data-testid="indicator-scope">
        {symbol} · {timeframe} 股票＋週期專屬
      </p>
      {indicatorHome && (
        <p className="muted small" role="note">
          舊均線已歸入 {indicatorHome}；各週期現在獨立。
        </p>
      )}
      {error && (
        <p className="research-error" role="alert">
          {error}
        </p>
      )}
      <form
        className="add-ma"
        onSubmit={(event) => {
          event.preventDefault();
          add();
        }}
      >
        <label className="sr-only" htmlFor={typeId}>
          Indicator type
        </label>
        <select
          id={typeId}
          aria-label="Indicator type"
          value={indicatorType}
          onChange={(event) => setIndicatorType(event.target.value as IndicatorType)}
        >
          <option value="SMA">SMA</option>
          <option value="EMA">EMA</option>
          <option value="Volume">Volume</option>
        </select>
        {isVolume ? (
          <span>紅綠量柱 · 均量 MA 20</span>
        ) : (
          <>
            <label className="sr-only" htmlFor={periodId}>
              {indicatorType} period
            </label>
            <input
              id={periodId}
              aria-label={`${indicatorType} period`}
              type="number"
              min="1"
              max="5000"
              required
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
            />
          </>
        )}
        <button className="primary-button" type="submit" aria-label={`Add ${indicatorType}`}>
          <Plus size={16} />
          新增
        </button>
      </form>
      <div className="indicator-presets">
        <form className="ma-settings" onSubmit={savePreset}>
          <label>
            Preset name
            <input
              aria-label="Preset name"
              maxLength={80}
              value={presetName}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </label>
          <button className="primary-button" type="submit" disabled={!presetName.trim()}>
            Save preset
          </button>
        </form>
        <form className="ma-settings" onSubmit={applyPreset}>
          <label>
            Indicator preset
            <select
              aria-label="Indicator preset"
              value={selectedPreset}
              onChange={(event) => setSelectedPreset(event.target.value)}
            >
              <option value="">Choose a preset</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
          </label>
          <button className="primary-button" type="submit" disabled={!selectedPreset}>
            Apply preset
          </button>
        </form>
      </div>
      {indicators.length === 0 && (
        <div className="empty-state">
          加入第一條 SMA
          <br />
          <span>每檔股票獨立儲存</span>
        </div>
      )}
      {indicators.map((indicator) => {
        const isIndicatorVolume = indicator.type === 'Volume';
        const title = isIndicatorVolume ? 'Volume' : `${indicator.type} ${indicator.period}`;
        const displaySource = isIndicatorVolume ? 'volume' : indicator.source;
        return (
          <div
            className="ma-card"
            key={indicator.id}
            data-testid={
              isIndicatorVolume ? `indicator-volume-${indicator.id}` : `ma-${indicator.period}`
            }
          >
            <div className="ma-card-title">
              <span className="color-dot" style={{ background: indicator.color }} />
              <b>{title}</b>
              <span className="muted small">{displaySource}</span>
            </div>
            <div className="ma-actions">
              <IconButton
                label={`${indicator.visible ? 'Hide' : 'Show'} ${title}`}
                onClick={() =>
                  store.updateIndicator(symbol, indicator.id, { visible: !indicator.visible })
                }
              >
                {indicator.visible ? <Eye size={16} /> : <EyeOff size={16} />}
              </IconButton>
              <IconButton
                label={`${indicator.locked ? 'Unlock' : 'Lock'} ${title}`}
                active={indicator.locked}
                onClick={() =>
                  store.updateIndicator(symbol, indicator.id, { locked: !indicator.locked })
                }
              >
                {indicator.locked ? <LockKeyhole size={16} /> : <UnlockKeyhole size={16} />}
              </IconButton>
              <IconButton
                label={`Settings ${title}`}
                disabled={indicator.locked}
                active={editing === indicator.id}
                onClick={() => setEditing(editing === indicator.id ? null : indicator.id)}
              >
                <Settings2 size={16} />
              </IconButton>
              <IconButton
                label={`Remove ${title}`}
                disabled={indicator.locked}
                onClick={() => store.removeIndicator(symbol, indicator.id)}
              >
                <Trash2 size={16} />
              </IconButton>
            </div>
            {editing === indicator.id && !indicator.locked && (
              <form className="ma-settings" onSubmit={(event) => saveSettings(event, indicator)}>
                {isIndicatorVolume ? null : (
                  <>
                    <label>
                      Period
                      <input
                        type="number"
                        name="period"
                        min="1"
                        max="5000"
                        required
                        defaultValue={indicator.period}
                      />
                    </label>
                    <label>
                      Source
                      <select name="source" defaultValue={indicator.source}>
                        {(['open', 'high', 'low', 'close'] as const).map((source) => (
                          <option key={source} value={source}>
                            {source}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                <label>
                  Color
                  <input type="color" name="color" defaultValue={indicator.color} />
                </label>
                {isIndicatorVolume && (
                  <p className="muted small">顏色套用於 MA 20 均量線；量柱依 K 線漲跌顯示紅綠。</p>
                )}
                {!isIndicatorVolume && (
                  <label>
                    Width
                    <select name="width" aria-label={`${title} width`} defaultValue={indicator.widthMode === 'atr' ? 'atr' : String(indicator.lineWidth)}>
                      <option value="atr">0.02 ATR (14)</option>
                      {[1, 2, 3, 4].map((width) => (
                        <option key={width} value={width}>
                          {width}px
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <button className="primary-button">儲存</button>
              </form>
            )}
          </div>
        );
      })}
      <p className="small muted">
        Lock 後仍可隱藏／顯示。
        <br />
        解鎖後才能修改或刪除。ATR width maps 0.02 × 14-bar ATR through the price scale; native SMA/EMA strokes round to 1–4 px. Pixel mode uses native 1–4 px. New SMA/EMA instances use ATR, while saved instances keep their current width mode.
      </p>
    </section>
  );
}
