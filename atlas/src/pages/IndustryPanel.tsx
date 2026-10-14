import { useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { useDialogFocus } from '../ui/useDialogFocus';
import { displayTicker, formatPercent, toneClass } from '../ui/format';
import { PERIOD_LABELS } from '../report/themes';
import type { OpenChartOptions } from '../app/Site';
import { INDUSTRY_PERIODS, summarize, type IndustryCell, type IndustryModel, type IndustryPeriod } from './industryModel';
import { formatMoney } from './themesMapModel';

const PAGE = 100;
type SortKey = 'cap' | 'ret';

/**
 * Constituents of one industry cell, as the same bottom sheet / right drawer the theme detail uses
 * (classes, focus trap and Escape handling are shared). A row opens the stock's chart.
 */
export function IndustryPanel({
  cell,
  groupLabel,
  model,
  initialPeriod,
  onClose,
  openChart,
}: {
  cell: IndustryCell;
  /** The sector (US) the cell sits in; empty for Taiwan. */
  groupLabel: string;
  model: IndustryModel;
  initialPeriod: IndustryPeriod;
  onClose: () => void;
  openChart: (options: OpenChartOptions) => void;
}) {
  const [period, setPeriod] = useState<IndustryPeriod>(initialPeriod);
  const [sort, setSort] = useState<SortKey>('cap');
  const [shown, setShown] = useState(PAGE);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useDialogFocus(true, 'industry-detail');
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  const summary = useMemo(() => summarize(cell.stocks, period, model.basis), [cell, period, model.basis]);
  const hasCap = cell.stocks.some((stock) => stock.marketCap !== null);
  const rows = useMemo(() => {
    const byCap = (a: (typeof cell.stocks)[number], b: (typeof cell.stocks)[number]) =>
      (b.marketCap ?? b.turnover ?? -1) - (a.marketCap ?? a.turnover ?? -1) || a.symbol.localeCompare(b.symbol);
    return [...cell.stocks].sort((a, b) => {
      if (sort === 'cap') return byCap(a, b);
      const av = a.returns[period];
      const bv = b.returns[period];
      if (av === null || bv === null) return av === bv ? byCap(a, b) : av === null ? 1 : -1;
      return bv - av || byCap(a, b);
    });
  }, [cell, sort, period]);
  const weightLabel = model.basis === 'turnover' ? '成交值加權' : '市值加權';
  return (
    <div className="theme-sheet-backdrop" onClick={onClose}>
      <aside
        className="theme-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={`${cell.label} 產業成分股`}
        data-dialog-focus="industry-detail"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="sheet-head">
          <span className="sheet-grab" aria-hidden="true" />
          <div className="sheet-title">
            <div>
              <h2>{cell.label}</h2>
              <p>
                {groupLabel && <span className="sheet-tag">{groupLabel}</span>}
                <span>
                  <b>{cell.stocks.length}</b> 檔
                </span>
              </p>
            </div>
            <button type="button" className="icon-button" aria-label="關閉" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="sheet-body">
          <div className="ind-toolbar">
            <div className="segmented compact" role="group" aria-label="成分股期間">
              {INDUSTRY_PERIODS.map((key) => (
                <button key={key} type="button" className={key === period ? 'active' : ''} aria-pressed={key === period} onClick={() => setPeriod(key)}>
                  {PERIOD_LABELS[key]}
                </button>
              ))}
            </div>
            <label className="period-select">
              <span>排序</span>
              <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)} aria-label="成分股排序">
                <option value="cap">{hasCap ? '市值' : '成交值'}</option>
                <option value="ret">{PERIOD_LABELS[period]}報酬</option>
              </select>
            </label>
          </div>
          <div className="detail-facts">
            <span>
              {weightLabel}（{PERIOD_LABELS[period]}）
              <b className={`num ${toneClass(summary.weighted)}`}>{formatPercent(summary.weighted, 1)}</b>
            </span>
            <span>
              等權中位數
              <b className={`num ${toneClass(summary.median)}`}>{formatPercent(summary.median, 1)}</b>
            </span>
            <span>
              上漲家數
              <b className="num">{summary.upShare === null ? '—' : `${Math.round(summary.upShare * 100)}%`}</b>
              <small>
                {summary.validCount} 檔有報酬資料{summary.validCount < summary.count ? `（共 ${summary.count} 檔）` : ''}
              </small>
            </span>
            <span>
              市值合計
              <b className="num">{formatMoney(summary.marketCap, model.market)}</b>
              <small>{summary.capCount < summary.count ? `${summary.capCount}/${summary.count} 檔有市值` : `${summary.count} 檔`}</small>
            </span>
          </div>
          {cell.merged && (
            <p className="detail-muted">
              「其他」合併了成分股少於 3 檔的產業與未分類股票
              {cell.mergedFrom.length ? `：${cell.mergedFrom.slice(0, 12).join('、')}${cell.mergedFrom.length > 12 ? ` 等 ${cell.mergedFrom.length} 個產業` : ''}` : ''}。
            </p>
          )}
          <section className="detail-constituents" aria-label="成分股">
            <div className="constituents-head">
              <h3>
                成分股 <small>依{sort === 'cap' ? (hasCap ? '市值' : '成交值') : `${PERIOD_LABELS[period]}報酬`}排序</small>
              </h3>
            </div>
            <ul className="constituent-list">
              {rows.slice(0, shown).map((stock) => {
                const value = stock.returns[period];
                return (
                  <li key={stock.symbol} className="constituent">
                    <button
                      type="button"
                      className="ind-row"
                      aria-label={`${stock.symbol} ${stock.name}，${PERIOD_LABELS[period]}報酬 ${formatPercent(value, 1)}，開啟圖表`}
                      onClick={() => openChart({ symbol: stock.symbol, name: stock.name, maList: [20, 50] })}
                    >
                      <span className="constituent-id">
                        <b>{displayTicker(stock.symbol)}</b>
                        <span className="constituent-name">{stock.name}</span>
                        <small className="num">{stock.marketCap !== null ? `市值 ${formatMoney(stock.marketCap, model.market)}` : stock.turnover !== null ? `20日均成交值 ${formatMoney(stock.turnover, model.market)}` : '市值未知'}</small>
                      </span>
                      <span className={`num constituent-value ${toneClass(value)}`}>{formatPercent(value, 1)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {shown < rows.length && (
              <button type="button" className="ghost-button open-chart" onClick={() => setShown(shown + PAGE)}>
                再顯示 {Math.min(PAGE, rows.length - shown)} 檔（還有 {rows.length - shown} 檔）
              </button>
            )}
          </section>
          <p className="detail-disclaimer">報酬為收盤資料；僅供研究參考，非投資建議。</p>
        </div>
      </aside>
    </div>
  );
}
