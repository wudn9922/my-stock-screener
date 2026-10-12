import { useState, type ReactNode } from 'react';
import { LineChart } from 'lucide-react';
import type { RatesEtf, Regression } from '../../report/rates';
import type { PageProps } from '../../pages/pageProps';
import { RATES_COLORS } from './TimeChart';
import { LeverageBadge, Segmented, StaticMark, fmtNum, slashDate } from './shared';

type Segment = 'sensitivity' | 'cost';

// Color follows the fund, not its position in the file.
const ETF_SLOTS: Record<string, number> = { TLT: 0, EDV: 1, ZROZ: 2, GOVZ: 3, TMF: 4 };
const etfColor = (symbol: string) => RATES_COLORS[ETF_SLOTS[symbol] ?? 5]!;

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);
const pct = (value: number | null | undefined, digits = 1) => (finite(value) ? `${value.toFixed(digits)}%` : '—');

interface BarRow {
  etf: RatesEtf;
  value: number | null;
  text: string;
  detail: ReactNode;
}

function regressionOf(etf: RatesEtf, against: 'dgs20' | 'dgs30', sample: 'weekly3y' | 'daily1y'): Regression | null {
  return etf.sensitivity[against]?.[sample] ?? null;
}

function rowsFor(
  segment: Segment,
  etfs: readonly RatesEtf[],
  options: { against: 'dgs20' | 'dgs30'; sample: 'weekly3y' | 'daily1y' },
): BarRow[] {
  return etfs.map((etf): BarRow => {
    if (segment === 'sensitivity') {
      const regression = regressionOf(etf, options.against, options.sample);
      const value = regression?.durEmp ?? null;
      return {
        etf,
        value,
        text: value === null ? '—' : `${value.toFixed(1)} 年`,
        detail: (
          <>
            {regression ? `R² ${fmtNum(regression.r2, 2)}・${regression.n ?? '—'} 個樣本` : '無迴歸資料'}・公布 {fmtNum(etf.effectiveDuration)}／模型 {fmtNum(etf.model?.duration ?? null)} 年
            {etf.leverage > 1 ? `（未乘槓桿）` : ''}
          </>
        ),
      };
    }
    return {
      etf,
      value: etf.expenseRatio,
      text: pct(etf.expenseRatio, 2),
      detail: (
        <>
          {etf.expenseNote ? `${etf.expenseNote}・` : ''}
          <StaticMark etf={etf} />
        </>
      ),
    };
  });
}

/** Five long-Treasury ETFs: compare interest-rate sensitivity and fund costs. */
export function EtfCompareCard({ etfs, openChart }: { etfs: readonly RatesEtf[] } & Pick<PageProps, 'openChart'>) {
  const [segment, setSegment] = useState<Segment>('sensitivity');
  const [against, setAgainst] = useState<'dgs20' | 'dgs30'>('dgs30');
  const [sample, setSample] = useState<'weekly3y' | 'daily1y'>('weekly3y');
  const rows = rowsFor(segment, etfs, { against, sample });
  const scale = Math.max(1e-9, ...rows.map((row) => Math.abs(row.value ?? 0)));
  const colorOf = etfColor;

  return (
    <section className="card rates-card rates-wide" aria-labelledby="rates-etf-title">
      <header className="card-head">
        <h2 id="rates-etf-title">
          長天期美債 ETF 比較
          <small>{etfs.length} 檔</small>
        </h2>
      </header>
      <div className="rates-card-body">
        <Segmented
          label="比較項目"
          className="rates-seg-wide"
          value={segment}
          onChange={setSegment}
          options={[
            { value: 'sensitivity', label: '利率敏感度' },
            { value: 'cost', label: '成本' },
          ]}
        />
        {segment === 'sensitivity' && (
          <div className="rates-seg-pair">
            <Segmented
              label="對應殖利率"
              value={against}
              onChange={setAgainst}
              options={[
                { value: 'dgs20', label: '對20年' },
                { value: 'dgs30', label: '對30年' },
              ]}
            />
            <Segmented
              label="樣本"
              value={sample}
              onChange={setSample}
              options={[
                { value: 'weekly3y', label: '週資料3年' },
                { value: 'daily1y', label: '日資料1年' },
              ]}
            />
          </div>
        )}
        <ul className="rates-compare" aria-label="ETF 比較">
          {rows.map((row) => {
            const width = row.value === null ? 0 : (Math.abs(row.value) / scale) * 100;
            return (
              <li key={row.etf.symbol} className="rates-compare-row">
                <div className="rates-compare-head">
                  <i style={{ background: colorOf(row.etf.symbol) }} aria-hidden="true" />
                  <b>{row.etf.symbol}</b>
                  <LeverageBadge leverage={row.etf.leverage} />
                  <span className="rates-compare-name">{row.etf.name}</span>
                  <button
                    type="button"
                    className="icon-button rates-open"
                    aria-label={`開啟 ${row.etf.symbol} 圖表`}
                    onClick={() => openChart({ symbol: row.etf.symbol, name: row.etf.name, tf: '1D' })}
                  >
                    <LineChart size={16} />
                  </button>
                </div>
                <div className="rates-compare-bar">
                  <span className="rates-bar-track" aria-hidden="true">
                    <i className="rates-bar neutral" style={{ width: `${width}%` }} />
                  </span>
                  <b className="rates-bar-value">{row.text}</b>
                </div>
                {row.detail ? <small className="rates-compare-detail">{row.detail}</small> : null}
                {segment === 'cost' && (
                  <dl className="rates-facts">
                    <div>
                      <dt>追蹤指數</dt>
                      <dd>{row.etf.index ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>發行商</dt>
                      <dd>{row.etf.issuer ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>成立日</dt>
                      <dd>{slashDate(row.etf.inception)}</dd>
                    </div>
                    <div>
                      <dt>到期範圍</dt>
                      <dd>{row.etf.maturityRange ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>持有內容</dt>
                      <dd>{row.etf.holdings ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>公布存續期間</dt>
                      <dd>{row.etf.effectiveDuration === null ? '未提供' : `${fmtNum(row.etf.effectiveDuration)} 年`}</dd>
                    </div>
                  </dl>
                )}
              </li>
            );
          })}
        </ul>
        {segment === 'sensitivity' && (
          <p className="rates-note">實證存續期間＝ETF 報酬對殖利率變動的迴歸斜率 ×(−100)，是過去樣本的平均關係，不是保證。殖利率約在美東 15:30 取價、ETF 在 16:00 收盤，時間不同步會讓日資料的估計偏小，所以預設看週資料。TMF 的數字已含 3 倍槓桿。</p>
        )}
        {segment === 'cost' && (
          <p className="rates-note">費用率、指數、存續期間等為人工整理的靜態資料，標「待核對」者請以發行商網站為準。TMF 另有交換合約的融資成本，不包含在費用率內。</p>
        )}
      </div>
    </section>
  );
}
