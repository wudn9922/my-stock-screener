import { LineChart } from 'lucide-react';
import type { RatesEtf } from '../../report/rates';
import type { PageProps } from '../../pages/pageProps';
import { RATES_COLORS } from './TimeChart';
import { LeverageBadge, StaticMark, fmtNum, slashDate } from './shared';

// Keep each fund's color stable across the rates page.
const ETF_SLOTS: Record<string, number> = { TLT: 0, EDV: 1, ZROZ: 2, GOVZ: 3, TMF: 4 };
const etfColor = (symbol: string) => RATES_COLORS[ETF_SLOTS[symbol] ?? 5]!;
const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);

/** Five long-Treasury ETFs in one table (a row per fund) for side-by-side comparison, with direct links to their charts. */
export function EtfCompareCard({ etfs, openChart }: { etfs: readonly RatesEtf[] } & Pick<PageProps, 'openChart'>) {
  return (
    <section className="card rates-card rates-wide" aria-labelledby="rates-etf-title">
      <header className="card-head">
        <h2 id="rates-etf-title">
          長天期美債 ETF 比較
          <small>成本與基本資料 · {etfs.length} 檔</small>
        </h2>
      </header>
      <div className="rates-card-body">
        <div className="rates-etf-table-wrap" role="region" aria-label="長天期美債 ETF 比較表" tabIndex={0}>
          <table className="rates-etf-table">
            <thead>
              <tr>
                <th scope="col">基金</th>
                <th scope="col" className="num">費用率</th>
                <th scope="col" className="num">公布存續期間</th>
                <th scope="col">追蹤指數</th>
                <th scope="col">發行商</th>
                <th scope="col">成立日</th>
                <th scope="col">到期範圍</th>
                <th scope="col">持有內容</th>
                <th scope="col">
                  <span className="sr-only">圖表</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {etfs.map((etf) => {
                const ratio = finite(etf.expenseRatio) ? etf.expenseRatio : null;
                return (
                  <tr key={etf.symbol}>
                    <th scope="row">
                      <span className="rates-etf-fund-head">
                        <i style={{ background: etfColor(etf.symbol) }} aria-hidden="true" />
                        <b>{etf.symbol}</b>
                        <LeverageBadge leverage={etf.leverage} />
                      </span>
                      <span className="rates-etf-fund-name">{etf.name}</span>
                    </th>
                    <td className="num">
                      <span className="rates-etf-expense">
                        <b>{ratio === null ? '—' : `${ratio.toFixed(2)}%`}</b>
                        <StaticMark etf={etf} />
                      </span>
                      {etf.expenseNote ? <small className="rates-etf-note">{etf.expenseNote}</small> : null}
                    </td>
                    <td className="num">{etf.effectiveDuration === null ? '未提供' : `${fmtNum(etf.effectiveDuration)} 年`}</td>
                    <td>{etf.index ?? '—'}</td>
                    <td>{etf.issuer ?? '—'}</td>
                    <td>{slashDate(etf.inception)}</td>
                    <td>{etf.maturityRange ?? '—'}</td>
                    <td>{etf.holdings ?? '—'}</td>
                    <td>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`開啟 ${etf.symbol} 圖表`}
                        onClick={() => openChart({ symbol: etf.symbol, name: etf.name, tf: '1D' })}
                      >
                        <LineChart size={16} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="rates-note">費用率、指數、存續期間等為人工整理的靜態資料，標「待核對」者請以發行商網站為準。TMF 另有交換合約融資成本，不包含在費用率內。</p>
      </div>
    </section>
  );
}
