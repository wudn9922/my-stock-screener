import { useMemo, useState } from 'react';
import type { RatesEtf } from '../../report/rates';
import {
  SCENARIO_RANGE_BP,
  SCENARIO_SHOCKS_BP,
  formulaParts,
  scenarioTable,
  toScenarioEtf,
  withBorrowedDuration,
  type DurationBasis,
} from '../../rates/bondMath';
import { formatPercent, toneClass } from '../format';
import { LeverageBadge, Segmented, StaticMark, fmtNum } from './shared';

const QUICK = [-200, -100, -50, -25, 0, 25, 50, 100, 200] as const;
const signedShock = (bp: number) => `${bp > 0 ? '+' : ''}${(bp / 100).toFixed(2)}%`;
const pctText = (value: number) => `${value < 0 ? '−' : ''}${Math.abs(value).toFixed(2)}%`;

/** Instant parallel-shift scenarios: L·(−D·Δy + ½·C·Δy²) per ETF, bars for the slider value plus a fixed-shock table. */
export function ScenarioCard({ etfs }: { etfs: readonly RatesEtf[] }) {
  const [bp, setBp] = useState(100);
  const [basis, setBasis] = useState<DurationBasis>('published');
  const borrowed = useMemo(() => withBorrowedDuration(etfs), [etfs]);
  const inputs = useMemo(() => borrowed.etfs.map(toScenarioEtf), [borrowed]);
  const rows = useMemo(() => scenarioTable(inputs, [bp], basis), [inputs, bp, basis]);
  const table = useMemo(() => scenarioTable(inputs, SCENARIO_SHOCKS_BP, basis), [inputs, basis]);
  const scale = Math.max(5, ...rows.map((row) => Math.abs(row.cells[0]?.pct ?? 0)));
  const example = rows.find((row) => !row.singleDayOnly && row.duration !== null && row.convexity !== null);
  const exampleBp = bp === 0 ? 100 : bp;
  const parts = example ? formulaParts(example.duration!, example.convexity!, exampleBp) : null;
  const publishedSource = etfs.find((etf) => etf.effectiveDuration !== null) ?? null;
  const leveraged = rows.find((row) => row.singleDayOnly && row.duration !== null);
  const leveragedParts = leveraged ? formulaParts(leveraged.duration!, leveraged.convexity ?? 0, exampleBp, leveraged.leverage) : null;
  return (
    <section className="card rates-card rates-wide" aria-labelledby="rates-scenario-title">
      <header className="card-head">
        <h2 id="rates-scenario-title">
          存續期間情境分析
          <small>殖利率瞬間平行移動</small>
        </h2>
        <Segmented
          label="存續期間來源"
          value={basis}
          onChange={setBasis}
          options={[
            { value: 'published', label: '公布值' },
            { value: 'model', label: '模型值' },
          ]}
        />
      </header>
      <div className="rates-card-body">
        <label className="rates-slider">
          <span>
            長天期殖利率變動 <b className={toneClass(bp)}>{signedShock(bp)}</b>
          </span>
          <input
            type="range"
            min={SCENARIO_RANGE_BP.min}
            max={SCENARIO_RANGE_BP.max}
            step={SCENARIO_RANGE_BP.step}
            value={bp}
            aria-valuetext={signedShock(bp)}
            onChange={(event) => setBp(Number(event.target.value))}
          />
        </label>
        <div className="chip-row rates-chip-row" role="group" aria-label="快速選擇">
          {QUICK.map((value) => (
            <button key={value} type="button" className={`chip rates-chip ${bp === value ? 'active' : ''}`} aria-pressed={bp === value} onClick={() => setBp(value)}>
              {signedShock(value)}
            </button>
          ))}
        </div>
        <ul className="rates-bars" aria-label={`殖利率 ${signedShock(bp)} 時各 ETF 的估計價格變動`}>
          {rows.map((row) => {
            const cell = row.cells[0]!;
            const width = cell.pct === null ? 0 : (Math.abs(cell.pct) / scale) * 50;
            return (
              <li key={row.symbol} className="rates-bar-row">
                <span className="rates-bar-label">
                  <b>{row.symbol}</b>
                  <LeverageBadge leverage={row.leverage} />
                  <small>
                    D {fmtNum(row.duration)} 年（{row.durationBasis === 'published' ? '公布' : row.durationBasis === 'model' ? '模型' : '—'}
                    {borrowed.borrowedFrom[row.symbol] ? `，同 ${borrowed.borrowedFrom[row.symbol]}` : ''}）
                  </small>
                </span>
                <span className="rates-bar-track centered" aria-hidden="true">
                  <i
                    className={`rates-bar ${toneClass(cell.pct)}`}
                    style={{ left: `${cell.pct !== null && cell.pct < 0 ? 50 - width : 50}%`, width: `${width}%` }}
                  />
                </span>
                <b className={`rates-bar-value ${toneClass(cell.pct)}`}>
                  {cell.pct === null ? '—' : formatPercent(cell.pct, 1)}
                  {cell.floored && <small>（歸零下限）</small>}
                </b>
              </li>
            );
          })}
        </ul>
        <div className="table-scroll rates-scenario-scroll">
          <table className="data-table rates-table rates-scenario-table">
            <caption className="sr-only">各 ETF 在不同殖利率變動下的估計價格變動（%）</caption>
            <thead>
              <tr>
                <th scope="col" className="sticky-col">ETF</th>
                {SCENARIO_SHOCKS_BP.map((shock) => (
                  <th key={shock} scope="col" className="num">
                    {signedShock(shock)}
                  </th>
                ))}
                <th scope="col" className="num">D 公布</th>
                <th scope="col" className="num">D 模型</th>
                <th scope="col" className="num">凸性 C（年²）</th>
              </tr>
            </thead>
            <tbody>
              {table.map((row, index) => {
                const etf = etfs[index]!;
                return (
                  <tr key={row.symbol}>
                    <th scope="row" className="sticky-col">
                      {row.symbol}
                      {row.singleDayOnly && <small className="rates-muted"> 單日</small>}
                    </th>
                    {row.cells.map((cell) => (
                      <td key={cell.bp} className={`num ${toneClass(cell.pct)}`} title={etf.name}>
                        {cell.pct === null ? '—' : cell.floored ? '−100%' : formatPercent(cell.pct, 1)}
                      </td>
                    ))}
                    <td className="num">
                      {fmtNum(inputs[index]!.effectiveDuration)}
                      {borrowed.borrowedFrom[row.symbol] && <small className="rates-muted"> ＊</small>}
                    </td>
                    <td className="num">{fmtNum(inputs[index]!.model?.duration ?? null)}</td>
                    <td className="num">{fmtNum(row.convexity, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <details className="rates-disclosure">
          <summary>情境計算說明與公式</summary>
          <div className="rates-disclosure-body">
        <p className="rates-muted rates-static-line">
          D 公布＝發行商公布的有效存續期間
          {publishedSource && (
            <>
              （<StaticMark etf={publishedSource} />）
            </>
          )}
          ；沒有公布值時自動改用模型值（以今日 20、30 年殖利率估算的等面額債券梯形）。凸性一律使用模型值。
          {Object.entries(borrowed.borrowedFrom).map(([symbol, donor]) => (
            <span key={symbol}>
              {' '}
              ＊{symbol} 追蹤與 {donor} 相同的指數，公布值沿用 {donor} 的數字（再乘上槓桿倍數）。
            </span>
          ))}
        </p>
        {parts && example && (
          <div className="rates-formula">
            <b>公式範例（{example.symbol}，殖利率 {signedShock(exampleBp)}）</b>
            <code>
              −D×Δy + ½×C×Δy² = −{example.duration!.toFixed(2)} × {parts.dyPct.toFixed(2)}% + ½ × {example.convexity!.toFixed(0)} × ({parts.dyPct.toFixed(2)}%)²
            </code>
            <code>
              = {pctText(parts.durationTerm)} {parts.convexityTerm >= 0 ? '+' : '−'} {Math.abs(parts.convexityTerm).toFixed(2)}% ≈ {pctText(parts.total)}
            </code>
            {leveraged && leveragedParts && (
              <code>
                {leveraged.symbol}：{leveraged.leverage} × ({pctText(leveragedParts.durationTerm)} {leveragedParts.convexityTerm >= 0 ? '+' : '−'}{' '}
                {Math.abs(leveragedParts.convexityTerm).toFixed(2)}%) ≈ {pctText(leveragedParts.total)}
                {leveragedParts.floored ? '（下限 −100%）' : ''}（僅單日）
              </code>
            )}
            <p>
              D＝存續期間（年），Δy＝殖利率變動（{parts.dyPct.toFixed(2)}% ＝ {(parts.dyPct / 100).toFixed(4)}），C＝凸性（年²）。發行商常把凸性寫成每
              1% 殖利率變動平方的數字（例如 {parts.publishedConvexity.toFixed(2)}），要乘以 100 才是這裡的 C（{example.convexity!.toFixed(0)}）。
            </p>
            <p>這是二階近似：±1.00% 內誤差通常小於 0.5 個百分點；±2.00% 以上可能差到幾個百分點。只看價格、不含利息收入與費用，也假設整條曲線平行移動。</p>
          </div>
        )}
          </div>
        </details>
      </div>
    </section>
  );
}
