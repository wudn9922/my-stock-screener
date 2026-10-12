import { LineChart } from 'lucide-react';
import type { RatesEtf } from '../../report/rates';
import type { PageProps } from '../../pages/pageProps';
import { RATES_COLORS } from './TimeChart';
import { LeverageBadge, StaticMark, fmtNum, slashDate } from './shared';

// Keep each fund's color stable across the rates page.
const ETF_SLOTS: Record<string, number> = { TLT: 0, EDV: 1, ZROZ: 2, GOVZ: 3, TMF: 4 };
const etfColor = (symbol: string) => RATES_COLORS[ETF_SLOTS[symbol] ?? 5]!;
const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);

/** Five long-Treasury ETFs: costs and static fund facts, with direct links to their charts. */
export function EtfCompareCard({ etfs, openChart }: { etfs: readonly RatesEtf[] } & Pick<PageProps, 'openChart'>) {
  const scale = Math.max(1e-9, ...etfs.map((etf) => etf.expenseRatio ?? 0));

  return (
    <section className="card rates-card rates-wide" aria-labelledby="rates-etf-title">
      <header className="card-head">
        <h2 id="rates-etf-title">
          長天期美債 ETF 比較
          <small>成本與基本資料 · {etfs.length} 檔</small>
        </h2>
      </header>
      <div className="rates-card-body">
        <ul className="rates-compare" aria-label="ETF 成本比較">
          {etfs.map((etf) => {
            const ratio = finite(etf.expenseRatio) ? etf.expenseRatio : null;
            const width = ratio === null ? 0 : (Math.abs(ratio) / scale) * 100;
            return (
              <li key={etf.symbol} className="rates-compare-row">
                <div className="rates-compare-head">
                  <i style={{ background: etfColor(etf.symbol) }} aria-hidden="true" />
                  <b>{etf.symbol}</b>
                  <LeverageBadge leverage={etf.leverage} />
                  <span className="rates-compare-name">{etf.name}</span>
                  <button
                    type="button"
                    className="icon-button rates-open"
                    aria-label={`開啟 ${etf.symbol} 圖表`}
                    onClick={() => openChart({ symbol: etf.symbol, name: etf.name, tf: '1D' })}
                  >
                    <LineChart size={16} />
                  </button>
                </div>
                <div className="rates-compare-bar">
                  <span className="rates-bar-track" aria-hidden="true">
                    <i className="rates-bar neutral" style={{ width: `${width}%` }} />
                  </span>
                  <b className="rates-bar-value">{ratio === null ? '—' : `${ratio.toFixed(2)}%`}</b>
                </div>
                <small className="rates-compare-detail">
                  {etf.expenseNote ? `${etf.expenseNote}・` : ''}
                  <StaticMark etf={etf} />
                </small>
                <dl className="rates-facts">
                  <div><dt>追蹤指數</dt><dd>{etf.index ?? '—'}</dd></div>
                  <div><dt>發行商</dt><dd>{etf.issuer ?? '—'}</dd></div>
                  <div><dt>成立日</dt><dd>{slashDate(etf.inception)}</dd></div>
                  <div><dt>到期範圍</dt><dd>{etf.maturityRange ?? '—'}</dd></div>
                  <div><dt>持有內容</dt><dd>{etf.holdings ?? '—'}</dd></div>
                  <div><dt>公布存續期間</dt><dd>{etf.effectiveDuration === null ? '未提供' : `${fmtNum(etf.effectiveDuration)} 年`}</dd></div>
                </dl>
              </li>
            );
          })}
        </ul>
        <p className="rates-note">費用率、指數、存續期間等為人工整理的靜態資料，標「待核對」者請以發行商網站為準。TMF 另有交換合約融資成本，不包含在費用率內。</p>
      </div>
    </section>
  );
}
