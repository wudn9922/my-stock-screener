import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { Rates } from '../../report/rates';
import { SPREAD_DEFS, inversionRanges, spreadSeries, spreadSummary, windowStartIndex } from '../../rates/ratesView';
import { TimeChart, type ChartLine } from './TimeChart';
import { ChangeBp, SectionState, Segmented, fmtBp, slashDate, slashMonth, type HistoryState } from './shared';

type Range = '1y' | '5y' | 'all';
const RANGES = [
  { value: '1y' as const, label: '1年' },
  { value: '5y' as const, label: '5年' },
  { value: 'all' as const, label: '全部' },
];
const LINE_COLOR = '#3d8bfd';
const MAX_EPISODES = 4;

/** Yield spreads: baseline chart (inverted stretch in the warning hue), summary and past inversions. */
export function SpreadCard({ rates, history }: { rates: Rates; history: HistoryState }) {
  const [key, setKey] = useState('10y2y');
  const [range, setRange] = useState<Range>('5y');
  const [hover, setHover] = useState<{ value: number | null; time: string | null }>({ value: null, time: null });
  const def = SPREAD_DEFS.find((item) => item.key === key) ?? SPREAD_DEFS[0]!;
  const spread = rates.spreads.find((item) => item.key === def.key) ?? null;

  const lines = useMemo((): ChartLine[] => {
    if (history.status !== 'ok') return [];
    const block = range === '1y' && history.data.daily.dates.length > 20 ? history.data.daily : history.data.weekly;
    const series = spreadSeries(block, def.long, def.short);
    const start = range === '5y' ? windowStartIndex(series.dates, 5) : range === '1y' ? windowStartIndex(series.dates, 1) : 0;
    return [
      {
        id: def.key,
        color: LINE_COLOR,
        kind: 'baseline',
        points: series.dates.slice(start).map((time, index) => ({ time, value: series.values[start + index] ?? null })),
      },
    ];
  }, [history, range, def]);

  // Past episodes from the full weekly history (short flips merged, one-week blips dropped).
  const episodes = useMemo(() => {
    if (history.status !== 'ok') return [];
    const series = spreadSeries(history.data.weekly, def.long, def.short);
    // Rounding-level dips (shallower than −0.5bp) are not listed.
    return inversionRanges(series.dates, series.values, { mergeGapDays: 30, minDays: 7 })
      .filter((range) => range.minBp <= -0.5)
      .reverse();
  }, [history, def]);

  const summary = spread ? spreadSummary(spread, def.label) : [`${def.label} 沒有摘要資料。`];
  return (
    <section className="card rates-card" aria-labelledby="rates-spread-title">
      <header className="card-head">
        <h2 id="rates-spread-title">
          利差
          <small>長天期減短天期</small>
        </h2>
        <Segmented label="利差區間" value={range} onChange={setRange} options={RANGES} />
      </header>
      <div className="rates-card-body">
        <div className="chip-row rates-chip-row" role="group" aria-label="選擇利差">
          {SPREAD_DEFS.map((item) => {
            const entry = rates.spreads.find((s) => s.key === item.key);
            return (
              <button
                key={item.key}
                type="button"
                className={`chip rates-chip ${item.key === def.key ? 'active' : ''}`}
                aria-pressed={item.key === def.key}
                onClick={() => setKey(item.key)}
              >
                {item.label}
                {entry?.inverted && <AlertTriangle size={12} className="rates-warn-icon" aria-label="倒掛" />}
              </button>
            );
          })}
        </div>
        <div className="rates-headline">
          <b className={spread?.inverted ? 'rates-inverted' : ''}>{hover.value !== null ? fmtBp(hover.value) : fmtBp(spread?.valueBp ?? null)}</b>
          {hover.time ? <span className="rates-muted">{slashDate(hover.time)}</span> : <ChangeBp bp={spread?.chg1mBp ?? null} />}
          {spread?.inverted && !hover.time && (
            <span className="rates-badge warn">
              <AlertTriangle size={11} aria-hidden="true" />
              倒掛
            </span>
          )}
        </div>
        {history.status === 'ok' ? (
          <div className="rates-chart-wrap">
            <TimeChart
              lines={lines}
              zeroLine
              format={(value) => `${Math.round(value)}bp`}
              ariaLabel={`${def.label} 利差走勢（bp），低於 0 為倒掛`}
              onHover={(values, time) => setHover({ value: values?.[def.key] ?? null, time })}
            />
          </div>
        ) : (
          <SectionState history={history} what="利差走勢" />
        )}
        <ul className="rates-summary">
          {summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {episodes.length > 0 && (
          <details className="rates-episodes">
            <summary>2003 年以來的倒掛段落（週資料，{episodes.length} 段）</summary>
            <ul>
              {episodes.slice(0, MAX_EPISODES).map((episode) => (
                <li key={episode.start}>
                  {slashMonth(episode.start)}–{episode.end ? slashMonth(episode.end) : '至今'}，最深 {fmtBp(episode.minBp)}
                </li>
              ))}
              {episodes.length > MAX_EPISODES && <li className="rates-muted">另有 {episodes.length - MAX_EPISODES} 段較早的倒掛。</li>}
            </ul>
          </details>
        )}
        <p className="source-note">
          {range === '1y' ? '近 1 年為日資料' : '週資料（每週最後一個有值的交易日）'}；低於 0（倒掛）的區段以橘色標示。倒掛不是衰退時間表，詳見下方說明。
        </p>
      </div>
    </section>
  );
}
