import { useMemo, useState } from 'react';
import type { Rates } from '../../report/rates';
import { windowStartIndex } from '../../rates/ratesView';
import { RATES_COLORS, TimeChart, type ChartLine } from './TimeChart';
import { ChangeBp, SectionState, Segmented, fmtYield, slashDate, type HistoryState } from './shared';

type Range = '1y' | '5y' | 'all';
const LINES = [
  { id: 'DGS10', label: '10年名目', color: RATES_COLORS[0] },
  { id: 'DFII10', label: '10年實質（TIPS）', color: RATES_COLORS[2] },
  { id: 'T10YIE', label: '10年通膨預期', color: RATES_COLORS[1] },
] as const;
const ROWS = [
  { key: '5Y', label: '5年', real: '5Y' },
  { key: '10Y', label: '10年', real: '10Y' },
  { key: '5y5y', label: '5年後的5年', real: null },
  { key: '20Y', label: '20年', real: '20Y' },
  { key: '30Y', label: '30年', real: '30Y' },
] as const;

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);

/** Nominal 10Y, TIPS 10Y and the 10Y breakeven on one percent axis, plus a small breakeven table. */
export function RealYieldCard({ rates, history }: { rates: Rates; history: HistoryState }) {
  const [range, setRange] = useState<Range>('5y');
  const [hover, setHover] = useState<{ values: Record<string, number> | null; time: string | null }>({ values: null, time: null });
  const lines = useMemo((): ChartLine[] => {
    if (history.status !== 'ok') return [];
    const block = range === '1y' && history.data.daily.dates.length > 20 ? history.data.daily : history.data.weekly;
    const start = range === 'all' ? 0 : windowStartIndex(block.dates, range === '1y' ? 1 : 5);
    return LINES.map((line) => ({
      id: line.id,
      color: line.color,
      points: block.dates.slice(start).map((time, index) => ({ time, value: block.series[line.id]?.[start + index] ?? null })),
    }));
  }, [history, range]);
  const nowCurve = rates.curves.nominal.find((curve) => curve.key === 'now');
  const realNow = rates.curves.real.find((curve) => curve.key === 'now');
  const nominal10 = nowCurve?.values[rates.tenors.indexOf('10Y')] ?? null;
  const real10 = realNow?.values[rates.realTenors.indexOf('10Y')] ?? null;
  const breakeven10 = rates.inflation['10Y']?.value ?? null;
  const latest: Record<string, number | null> = { DGS10: nominal10, DFII10: real10, T10YIE: breakeven10 };
  const shown = hover.values ?? latest;
  const residual = finite(nominal10) && finite(real10) && finite(breakeven10) ? nominal10 - real10 - breakeven10 : null;
  return (
    <section className="card rates-card rates-wide" aria-labelledby="rates-real-title">
      <header className="card-head">
        <h2 id="rates-real-title">
          實質利率與通膨預期
          <small>{hover.time ? slashDate(hover.time) : ''}</small>
        </h2>
        <Segmented
          label="期間"
          value={range}
          onChange={setRange}
          options={[
            { value: '1y', label: '1年' },
            { value: '5y', label: '5年' },
            { value: 'all', label: '全部' },
          ]}
        />
      </header>
      <div className="rates-card-body">
        <p className="rates-identity">
          名目 <b>{fmtYield(nominal10)}</b> ≈ 實質 <b>{fmtYield(real10)}</b> ＋ 預期通膨 <b>{fmtYield(breakeven10)}</b>
          {residual !== null && Math.abs(residual) >= 0.005 && <small>（差 {residual > 0 ? '+' : ''}{residual.toFixed(2)} 個百分點，來自取價與流動性差異）</small>}
        </p>
        <ul className="rates-legend" aria-label="圖例">
          {LINES.map((line) => (
            <li key={line.id}>
              <i style={{ background: line.color }} aria-hidden="true" />
              <span>{line.label}</span>
              <b>{fmtYield(shown[line.id] ?? null)}</b>
            </li>
          ))}
        </ul>
        {history.status === 'ok' ? (
          <div className="rates-chart-wrap">
            <TimeChart
              lines={lines}
              format={(value) => `${value.toFixed(2)}%`}
              ariaLabel="10 年名目、實質殖利率與通膨預期走勢（%）"
              onHover={(values, time) => setHover({ values, time })}
            />
          </div>
        ) : (
          <SectionState history={history} what="走勢圖" />
        )}
        <div className="table-scroll">
          <table className="data-table rates-table">
            <caption className="sr-only">通膨預期（損益兩平通膨率）</caption>
            <thead>
              <tr>
                <th scope="col">期限</th>
                <th scope="col" className="num">通膨預期</th>
                <th scope="col" className="num">1月變化</th>
                <th scope="col" className="num">實質殖利率</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => {
                const point = rates.inflation[row.key];
                const realIndex = row.real ? rates.realTenors.indexOf(row.real) : -1;
                const realValue = realIndex >= 0 ? (realNow?.values[realIndex] ?? null) : null;
                return (
                  <tr key={row.key}>
                    <th scope="row">
                      {row.label}
                      {point?.derived && <small className="rates-muted"> 推算</small>}
                    </th>
                    <td className="num">{fmtYield(point?.value ?? null)}</td>
                    <td className="num">
                      <ChangeBp bp={finite(point?.chg1m) ? point.chg1m * 100 : null} label="" />
                    </td>
                    <td className="num">{row.real ? fmtYield(realValue) : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="source-note">5年、10年、5年後的5年取自 FRED 損益兩平通膨率（T5YIE、T10YIE、T5YIFR）；20年、30年為「名目殖利率 − TIPS 殖利率」推算。通膨預期含風險溢酬與流動性因素，不等於實際通膨預測。</p>
      </div>
    </section>
  );
}
