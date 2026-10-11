import { useEffect, useMemo, useState } from 'react';
import { Pause, Play, RotateCcw } from 'lucide-react';
import type { Rates } from '../../report/rates';
import { curveDomain, curveFrame, type CurveFrame } from '../../rates/ratesView';
import { RATES_COLORS } from './TimeChart';
import { Segmented, fmtBp, fmtYield, slashDate, type HistoryState } from './shared';

type Mode = 'nominal' | 'real';
const COMPARE = [
  { key: '1w', label: '1週前' },
  { key: '1m', label: '1個月前' },
  { key: '3m', label: '3個月前' },
  { key: '1y', label: '1年前' },
  { key: '2y', label: '2年前' },
] as const;
// Fixed color per comparison date (slot 0 blue is close to the accent used for today).
const COMPARE_COLORS: Record<string, string> = {
  '1w': RATES_COLORS[1],
  '1m': RATES_COLORS[2],
  '3m': RATES_COLORS[3],
  '1y': RATES_COLORS[4],
  '2y': RATES_COLORS[5],
};
const TODAY_COLOR = '#3d8bfd';
const GHOST_COLOR = '#7f8898';
const FPS_MS = 125;

const W = 360;
const H = 210;
const PAD = { left: 38, right: 10, top: 12, bottom: 26 };

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);

function pathOf(values: readonly (number | null)[], x: (index: number) => number, y: (value: number) => number): string {
  let pen = 'M';
  const parts: string[] = [];
  values.forEach((value, index) => {
    if (!finite(value)) {
      pen = 'M';
      return;
    }
    parts.push(`${pen}${x(index).toFixed(1)},${y(value).toFixed(1)}`);
    pen = 'L';
  });
  return parts.join(' ');
}

interface Drawn {
  id: string;
  label: string;
  date: string | null;
  values: (number | null)[];
  color: string;
  width: number;
  dashed?: boolean;
  dots?: boolean;
}

/** Yield curve (SVG, equally spaced tenors) with comparison dates, nominal/real toggle and weekly replay. */
export function YieldCurveCard({ rates, history }: { rates: Rates; history: HistoryState }) {
  const [mode, setMode] = useState<Mode>('nominal');
  const [compare, setCompare] = useState<Set<string>>(() => new Set(['1m', '1y']));
  const [picked, setPicked] = useState<number | null>(null);
  const [frameIndex, setFrameIndex] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const tenors = mode === 'nominal' ? rates.tenors : rates.realTenors;
  const curves = rates.curves[mode];
  const today = curves.find((curve) => curve.key === 'now') ?? null;
  const weeks = history.status === 'ok' ? history.data.weekly.dates.length : 0;
  const replaying = frameIndex !== null && history.status === 'ok';

  useEffect(() => {
    if (!playing || history.status !== 'ok') return;
    const timer = window.setInterval(() => {
      setFrameIndex((index) => {
        const next = (index ?? 0) + 1;
        if (next >= weeks) {
          setPlaying(false);
          return weeks - 1;
        }
        return next;
      });
    }, FPS_MS);
    return () => window.clearInterval(timer);
  }, [playing, history.status, weeks]);

  const frame: CurveFrame | null = useMemo(
    () => (history.status === 'ok' && frameIndex !== null ? curveFrame(history.data, frameIndex, rates.tenors, rates.realTenors) : null),
    [history, frameIndex, rates.tenors, rates.realTenors],
  );
  // A fixed y range for the whole replay so the curve moves instead of the axis.
  const replayDomain = useMemo((): [number, number] | null => {
    if (history.status !== 'ok') return null;
    const block = history.data.weekly;
    const ids = mode === 'nominal' ? ['DGS1MO', 'DGS2', 'DGS10', 'DGS30'] : ['DFII5', 'DFII10', 'DFII30'];
    return curveDomain(ids.map((id) => block.series[id] ?? []));
  }, [history, mode]);

  const drawn: Drawn[] = [];
  if (replaying && frame) {
    if (today) drawn.push({ id: 'ghost', label: `今日（${slashDate(today.date)}）`, date: today.date, values: today.values, color: GHOST_COLOR, width: 1.5, dashed: true });
    drawn.push({ id: 'frame', label: slashDate(frame.date), date: frame.date, values: mode === 'nominal' ? frame.nominal : frame.real, color: TODAY_COLOR, width: 2.5, dots: true });
  } else {
    for (const option of COMPARE) {
      if (!compare.has(option.key)) continue;
      const curve = curves.find((item) => item.key === option.key);
      if (curve) drawn.push({ id: option.key, label: option.label, date: curve.date, values: curve.values, color: COMPARE_COLORS[option.key]!, width: 1.5 });
    }
    if (today) drawn.push({ id: 'now', label: '今日', date: today.date, values: today.values, color: TODAY_COLOR, width: 2.75, dots: true });
  }
  const [low, high] = replaying && replayDomain ? replayDomain : curveDomain(drawn.map((line) => line.values));
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const step = tenors.length > 1 ? plotW / (tenors.length - 1) : plotW;
  const x = (index: number) => PAD.left + index * step;
  const y = (value: number) => PAD.top + ((high - value) / (high - low || 1)) * plotH;
  const ticks: number[] = [];
  const tickStep = high - low > 4 ? 1 : high - low > 2 ? 0.5 : 0.25;
  for (let value = Math.ceil(low / tickStep) * tickStep; value <= high + 1e-9; value += tickStep) ticks.push(Number(value.toFixed(2)));
  const pickedIndex = picked !== null && picked < tenors.length ? picked : null;
  const main = drawn.at(-1);

  const toggleCompare = (key: string) =>
    setCompare((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const startPlay = () => {
    if (history.status !== 'ok') return;
    if (frameIndex === null || frameIndex >= weeks - 1) setFrameIndex(0);
    setPlaying(true);
  };

  return (
    <section className="card rates-card" aria-labelledby="rates-curve-title">
      <header className="card-head">
        <h2 id="rates-curve-title">
          殖利率曲線
          <small>{replaying && frame ? `回放 ${slashDate(frame.date)}` : today ? slashDate(today.date) : ''}</small>
        </h2>
        <Segmented
          label="名目或實質"
          value={mode}
          onChange={(value) => {
            setMode(value);
            setPicked(null);
          }}
          options={[
            { value: 'nominal', label: '名目' },
            { value: 'real', label: '實質' },
          ]}
        />
      </header>
      <div className="rates-card-body">
        {!replaying && (
          <div className="chip-row rates-chip-row" role="group" aria-label="比較日期">
            {COMPARE.map((option) => {
              const curve = curves.find((item) => item.key === option.key);
              const on = compare.has(option.key);
              return (
                <button
                  key={option.key}
                  type="button"
                  className={`chip rates-chip ${on ? 'active' : ''}`}
                  aria-pressed={on}
                  disabled={!curve}
                  title={curve?.date ? slashDate(curve.date) : '無資料'}
                  onClick={() => toggleCompare(option.key)}
                >
                  <i style={{ background: COMPARE_COLORS[option.key] }} aria-hidden="true" />
                  {option.label}
                </button>
              );
            })}
          </div>
        )}
        <figure className="rates-curve">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${mode === 'nominal' ? '名目' : '實質'}殖利率曲線，橫軸為期限、縱軸為殖利率（%）`}>
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} className="rates-grid-line" />
                <text x={PAD.left - 6} y={y(tick) + 3.5} className="rates-axis" textAnchor="end">
                  {tick.toFixed(tickStep < 0.5 ? 2 : 1)}
                </text>
              </g>
            ))}
            {low < 0 && high > 0 && <line x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} className="rates-zero-line" />}
            {tenors.map((tenor, index) => (
              <text key={tenor} x={x(index)} y={H - 8} className={`rates-axis ${pickedIndex === index ? 'on' : ''}`} textAnchor="middle">
                {tenor}
              </text>
            ))}
            {pickedIndex !== null && <line x1={x(pickedIndex)} x2={x(pickedIndex)} y1={PAD.top} y2={PAD.top + plotH} className="rates-guide" />}
            {drawn.map((line) => (
              <path
                key={line.id}
                d={pathOf(line.values, x, y)}
                fill="none"
                stroke={line.color}
                strokeWidth={line.width}
                strokeDasharray={line.dashed ? '4 3' : undefined}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            ))}
            {main?.dots &&
              main.values.map((value, index) =>
                finite(value) ? (
                  <circle key={index} cx={x(index)} cy={y(value)} r={pickedIndex === index ? 4.5 : 3} fill={main.color} stroke="#11151b" strokeWidth={1.5} />
                ) : null,
              )}
            {tenors.map((tenor, index) => (
              <rect
                key={tenor}
                x={x(index) - step / 2}
                y={PAD.top}
                width={step}
                height={plotH + PAD.bottom}
                fill="transparent"
                className="rates-hit"
                onClick={() => setPicked(pickedIndex === index ? null : index)}
              >
                <title>{tenor}</title>
              </rect>
            ))}
          </svg>
        </figure>
        {pickedIndex !== null ? (
          <div className="rates-readout" aria-live="polite">
            <b>{tenors[pickedIndex]}</b>
            {drawn
              .slice()
              .reverse()
              .map((line) => {
                const value = line.values[pickedIndex];
                const base = main?.values[pickedIndex];
                const diff = line !== main && finite(value) && finite(base) ? (base - value) * 100 : null;
                return (
                  <span key={line.id}>
                    <i style={{ background: line.color }} aria-hidden="true" />
                    {line.label} {fmtYield(value ?? null, 3)}
                    {diff !== null && <small>（{replaying ? '此週較今日' : '今日較此'} {fmtBp(diff)}）</small>}
                  </span>
                );
              })}
          </div>
        ) : (
          <p className="rates-hint">點曲線上的期限可看各日期的數值。</p>
        )}
        <div className="rates-replay">
          <button
            type="button"
            className="icon-button"
            aria-label={playing ? '暫停回放' : '播放歷史回放'}
            disabled={history.status !== 'ok'}
            onClick={() => (playing ? setPlaying(false) : startPlay())}
          >
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <input
            type="range"
            min={0}
            max={Math.max(0, weeks - 1)}
            step={1}
            value={frameIndex ?? Math.max(0, weeks - 1)}
            disabled={history.status !== 'ok'}
            aria-label="歷史回放週次"
            aria-valuetext={frame ? slashDate(frame.date) : '今日'}
            onChange={(event) => {
              setPlaying(false);
              setFrameIndex(Number(event.target.value));
            }}
          />
          {replaying ? (
            <button
              type="button"
              className="icon-button"
              aria-label="回到今日"
              onClick={() => {
                setPlaying(false);
                setFrameIndex(null);
              }}
            >
              <RotateCcw size={17} />
            </button>
          ) : null}
        </div>
        <p className="rates-replay-label">
          {history.status === 'loading'
            ? '歷史回放：資料載入中…'
            : history.status !== 'ok'
              ? `歷史回放暫時無法使用：${history.message}`
              : replaying && frame
                ? `${slashDate(frame.date)} · 10Y−2Y ${fmtBp(frame.spread10y2yBp)}${finite(frame.spread10y2yBp) && frame.spread10y2yBp < 0 ? '（倒掛）' : ''} · 灰色虛線為今日曲線`
                : `歷史回放：拖動滑桿或按播放，從 ${slashDate(history.data.weekly.dates[0])} 起每週一格。`}
        </p>
        {mode === 'real' && (
          <p className="rates-hint">實質殖利率＝抗通膨公債（TIPS）殖利率；30 年期在 2010 年前沒有資料。</p>
        )}
      </div>
    </section>
  );
}
