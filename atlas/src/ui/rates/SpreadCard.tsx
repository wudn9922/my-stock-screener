import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { Rates, RatesSpread } from '../../report/rates';
import { SPREAD_DEFS, inversionRanges, spreadSeries, spreadSummary, windowStartIndex, valueOnOrBefore } from '../../rates/ratesView';
import { TimeChart, type ChartLine } from './TimeChart';
import { ChangeBp, SectionState, Segmented, fmtBp, slashDate, slashMonth, type HistoryState } from './shared';

type Range = '1y' | '5y' | 'all';
type FedBound = 'upper' | 'lower';
const RANGES = [
  { value: '1y' as const, label: '1年' },
  { value: '5y' as const, label: '5年' },
  { value: 'all' as const, label: '全部' },
];
const LINE_COLOR = '#3d8bfd';
const MAX_EPISODES = 4;
const finite = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x);

/** Keep calculation in bp; show percentage-point differences and never reuse midpoint statistics for a bound. */
export function SpreadCard({
  rates, history, fedBound, onFedBoundChange,
}: { rates: Rates; history: HistoryState; fedBound: FedBound; onFedBoundChange: (next: FedBound) => void }) {
  const [key, setKey] = useState('10yfed');
  const [range, setRange] = useState<Range>('5y');
  const [hover, setHover] = useState<{ value: number | null; time: string | null }>({ value: null, time: null });
  const def = SPREAD_DEFS.find((item) => item.key === key) ?? SPREAD_DEFS[0]!;
  const isFed = def.key === '10yfed';
  const short = isFed ? (fedBound === 'upper' ? 'DFEDTARU' : 'DFEDTARL') : def.short;
  const tenors = rates.curves.nominal.find((c) => c.key === 'now');
  const tenIndex = rates.tenors.indexOf('10Y');
  const ten = tenIndex >= 0 ? tenors?.values[tenIndex] ?? null : null;
  const policyRate = rates.policy?.[fedBound] ?? null;
  const nowBp = isFed
    ? finite(ten) && finite(policyRate) ? Math.round((ten - policyRate) * 1000) / 10 : null
    : rates.spreads.find((item) => item.key === def.key)?.valueBp ?? null;

  const series = useMemo(() => {
    if (history.status !== 'ok') return null;
    const daily = spreadSeries(history.data.daily, def.long, short);
    const weekly = spreadSeries(history.data.weekly, def.long, short);
    return { daily, weekly };
  }, [history, def, short]);

  const missingHistory = history.status === 'ok' &&
    (!series || !series.weekly.values.some(finite));
  const lines = useMemo((): ChartLine[] => {
    if (!series) return [];
    const block = range === '1y' && series.daily.dates.length > 20 ? series.daily : series.weekly;
    const start = range === '5y' ? windowStartIndex(block.dates, 5) : range === '1y' ? windowStartIndex(block.dates, 1) : 0;
    return [{
      id: def.key,
      color: LINE_COLOR,
      kind: 'baseline',
      points: block.dates.slice(start).map((time, i) => ({ time, value: block.values[start + i] ?? null })),
    }];
  }, [series, range, def.key]);

  // Weekly inversion ranges are distinct from the published daily inversion statistic.
  const episodes = useMemo(() => {
    if (!series) return [];
    return inversionRanges(series.weekly.dates, series.weekly.values, { mergeGapDays: 30, minDays: 7 })
      .filter((r) => r.minBp <= -0.5)
      .reverse();
  }, [series]);

  const derivedFed = useMemo((): RatesSpread | null => {
    if (!isFed) return null;
    let chg1mBp: number | null = null;
    if (series && finite(nowBp) && rates.asOf && history.status === 'ok') {
      const date = new Date(`${rates.asOf}T00:00:00Z`);
      date.setUTCMonth(date.getUTCMonth() - 1);
      const previous = valueOnOrBefore(history.data.daily, 'DGS10', date.toISOString().slice(0, 10));
      const previousFed = previous ? valueOnOrBefore(history.data.daily, short, previous.date) : null;
      if (previous && previousFed && previousFed.date === previous.date)
        chg1mBp = Math.round((nowBp - (previous.value - previousFed.value) * 100) * 10) / 10;
    }
    const history5y = series ? series.weekly.values.slice(windowStartIndex(series.weekly.dates, 5)).filter(finite) : [];
    const pct5y = finite(nowBp) && history5y.length
      ? (history5y.filter((v) => v <= nowBp).length / history5y.length) * 100 : null;
    const last = episodes[0] ?? null;
    return {
      key: def.key, long: def.long, short, valueBp: nowBp,
      chg1mBp, inverted: finite(nowBp) && nowBp < 0, invertedDays: null,
      lastInversion: last ? { start: last.start, end: last.end, minBp: last.minBp, minDate: last.minDate } : null,
      pct5y,
    };
  }, [isFed, series, nowBp, rates.asOf, history, short, episodes, def.key, def.long]);
  const spread = derivedFed ?? rates.spreads.find((item) => item.key === def.key) ?? null;
  const changeBp = spread?.chg1mBp ?? null;
  const summary = spread ? spreadSummary(spread, def.label) : [`${def.label} 沒有摘要資料。`];
  const isInverted = finite(nowBp) && nowBp < 0;

  return (
    <section className="card rates-card rates-wide" aria-labelledby="rates-spread-title">
      <header className="card-head">
        <h2 id="rates-spread-title">
          利差
          <small>{isFed ? `10 年殖利率 − Fed 目標區間${fedBound === 'upper' ? '上限' : '下限'}` : '10 年殖利率 − 3 個月殖利率'}</small>
        </h2>
        <Segmented label="利差區間" value={range} onChange={setRange} options={RANGES} />
      </header>
      <div className="rates-card-body">
        <div className="chip-row rates-chip-row" role="group" aria-label="選擇利差">
          {SPREAD_DEFS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`chip rates-chip ${item.key === def.key ? 'active' : ''}`}
              aria-pressed={item.key === def.key}
              onClick={() => { setKey(item.key); setHover({ value: null, time: null }); }}
            >
              {item.label}
              {item.key === def.key && isInverted && <AlertTriangle size={12} className="rates-warn-icon" aria-label="倒掛" />}
            </button>
          ))}
        </div>
        {isFed && (
          <Segmented
            label="Fed 目標利率基準"
            value={fedBound}
            onChange={(next) => { onFedBoundChange(next); setHover({ value: null, time: null }); }}
            options={[{ value: 'upper', label: '上限（預設）' }, { value: 'lower', label: '下限' }]}
          />
        )}
        <div className="rates-headline">
          <b className={isInverted ? 'rates-inverted' : ''}>{hover.value !== null ? fmtBp(hover.value) : fmtBp(nowBp)}</b>
          {hover.time ? <span className="rates-muted">{slashDate(hover.time)}</span> : <ChangeBp bp={changeBp} />}
          {isInverted && !hover.time && (
            <span className="rates-badge warn">
              <AlertTriangle size={11} aria-hidden="true" />
              倒掛
            </span>
          )}
        </div>
        {missingHistory ? (
          <p className="rates-section-state">
            {isFed ? `Fed 目標區間${fedBound === 'upper' ? '上限' : '下限'}` : '利差'}的歷史資料尚未產生，無法顯示走勢。
          </p>
        ) : history.status === 'ok' ? (
          <div className="rates-chart-wrap">
            <TimeChart
              lines={lines}
              zeroLine
              format={(value) => fmtBp(value, false)}
              ariaLabel={`${def.label} 利差走勢（%），低於 0 為倒掛`}
              onHover={(values, time) => setHover({ value: values?.[def.key] ?? null, time })}
            />
          </div>
        ) : <SectionState history={history} what="利差走勢" />}
        <details className="rates-disclosure">
          <summary>利差解讀與資料說明</summary>
          <div className="rates-disclosure-body">
            <ul className="rates-summary">
              {summary.map((line) => <li key={line}>{line}</li>)}
            </ul>
            <p className="source-note">
              {range === '1y' ? '近 1 年為日資料' : '週資料（每週最後一個有值的交易日）'}；
              {isFed ? `Fed 使用目標區間${fedBound === 'upper' ? '上限' : '下限'}，不是有效聯邦基金利率；目標區間從 2008 年底才有。倒掛紀錄依週資料概算。` : ''}
              利差以百分點顯示；低於 0（倒掛）的區段以橘色標示。倒掛不是衰退時間表。
            </p>
          </div>
        </details>
        {episodes.length > 0 && (
          <details className="rates-episodes">
            <summary>{isFed ? '2008 年底' : '2003 年'}以來的倒掛段落（週資料，{episodes.length} 段）</summary>
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
      </div>
    </section>
  );
}
