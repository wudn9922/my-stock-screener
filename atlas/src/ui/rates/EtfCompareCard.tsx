import { useMemo, useState, type ReactNode } from 'react';
import { LineChart } from 'lucide-react';
import type { RatesEtf, Regression } from '../../report/rates';
import { rebase, windowStartIndex } from '../../rates/ratesView';
import type { PageProps } from '../../pages/pageProps';
import { formatPercent, toneClass } from '../format';
import { RATES_COLORS, TimeChart, type ChartLine } from './TimeChart';
import { LeverageBadge, SectionState, Segmented, StaticMark, fmtNum, slashDate, slashMonth, type HistoryState } from './shared';

type Segment = 'returns' | 'risk' | 'sensitivity' | 'cost';
type ReturnWindow = '1y' | '3y' | '5y' | '10y' | 'inception';
type RiskMetric = 'vol1y' | 'vol3y' | 'vol5y' | 'mdd' | 'mdd5y' | 'current';
type OverlayWindow = '1y' | '3y' | '5y' | '10y' | 'all';

const RETURN_WINDOWS: { value: ReturnWindow; label: string }[] = [
  { value: '1y', label: '1年' },
  { value: '3y', label: '3年' },
  { value: '5y', label: '5年' },
  { value: '10y', label: '10年' },
  { value: 'inception', label: '成立以來' },
];
const RISK_METRICS: { value: RiskMetric; label: string }[] = [
  { value: 'vol1y', label: '1年波動' },
  { value: 'vol3y', label: '3年波動' },
  { value: 'vol5y', label: '5年波動' },
  { value: 'mdd', label: '最大回撤' },
  { value: 'mdd5y', label: '近5年最大回撤' },
  { value: 'current', label: '目前回撤' },
];
const OVERLAY_WINDOWS: { value: OverlayWindow; label: string; years: number | null }[] = [
  { value: '1y', label: '1年', years: 1 },
  { value: '3y', label: '3年', years: 3 },
  { value: '5y', label: '5年', years: 5 },
  { value: '10y', label: '10年', years: 10 },
  { value: 'all', label: '全部', years: null },
];

// Color follows the fund, not its position in the file.
const ETF_SLOTS: Record<string, number> = { TLT: 0, EDV: 1, ZROZ: 2, GOVZ: 3, TMF: 4 };
const etfColor = (symbol: string) => RATES_COLORS[ETF_SLOTS[symbol] ?? 5]!;

const finite = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);
const pct = (value: number | null | undefined, digits = 1) => (finite(value) ? `${value.toFixed(digits)}%` : '—');

interface BarRow {
  etf: RatesEtf;
  value: number | null;
  /** Bar color: direction (up/down, follows 紅漲綠跌) or a neutral magnitude. */
  tone: 'up' | 'down' | 'flat' | 'neutral';
  text: string;
  detail: ReactNode;
}

function regressionOf(etf: RatesEtf, against: 'dgs20' | 'dgs30', sample: 'weekly3y' | 'daily1y'): Regression | null {
  return etf.sensitivity[against]?.[sample] ?? null;
}

function rowsFor(
  segment: Segment,
  etfs: readonly RatesEtf[],
  options: { window: ReturnWindow; risk: RiskMetric; against: 'dgs20' | 'dgs30'; sample: 'weekly3y' | 'daily1y' },
): BarRow[] {
  return etfs.map((etf): BarRow => {
    if (segment === 'returns') {
      const entry = etf.returns[options.window];
      const annualized = options.window !== '1y';
      const value = entry ? (annualized ? (entry.annualized ?? null) : entry.total) : null;
      return {
        etf,
        value,
        tone: toneClass(value),
        text: value === null ? '—' : formatPercent(value, 1),
        detail:
          entry === null
            ? options.window === 'inception'
              ? '無資料'
              : '成立未滿此期間'
            : annualized
              ? `年化；累積 ${formatPercent(entry.total, 1)}${options.window === 'inception' && entry.start ? `（自 ${slashDate(entry.start)}）` : ''}`
              : '',
      };
    }
    if (segment === 'risk') {
      const risk = etf.risk;
      if (options.risk === 'vol1y' || options.risk === 'vol3y' || options.risk === 'vol5y') {
        const value = risk[options.risk];
        return { etf, value, tone: 'neutral', text: pct(value), detail: value === null ? '成立未滿此期間' : '' };
      }
      if (options.risk === 'mdd') {
        const drawdown = risk.maxDrawdown;
        const value = drawdown?.value ?? null;
        return {
          etf,
          value,
          tone: toneClass(value),
          text: pct(value),
          detail: drawdown?.peak
            ? `${slashMonth(drawdown.peak)} 高點 → ${slashMonth(drawdown.trough)} 低點；${drawdown.recovered ? `${slashMonth(drawdown.recovered)} 回到高點` : '尚未回到高點'}`
            : '成立以來',
        };
      }
      const value = options.risk === 'mdd5y' ? risk.mdd5y : risk.currentDrawdown;
      return { etf, value, tone: toneClass(value), text: pct(value), detail: options.risk === 'mdd5y' ? '近 5 年內最大跌幅' : '距歷史高點' };
    }
    if (segment === 'sensitivity') {
      const regression = regressionOf(etf, options.against, options.sample);
      const value = regression?.durEmp ?? null;
      return {
        etf,
        value,
        tone: 'neutral',
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
      tone: 'neutral',
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

/** Five long-Treasury ETFs: segmented comparison bars and a rebased total-return overlay. */
export function EtfCompareCard({ etfs, history, openChart }: { etfs: readonly RatesEtf[]; history: HistoryState } & Pick<PageProps, 'openChart'>) {
  const [segment, setSegment] = useState<Segment>('returns');
  const [window, setWindow] = useState<ReturnWindow>('1y');
  const [risk, setRisk] = useState<RiskMetric>('vol1y');
  const [against, setAgainst] = useState<'dgs20' | 'dgs30'>('dgs30');
  const [sample, setSample] = useState<'weekly3y' | 'daily1y'>('weekly3y');
  const [overlay, setOverlay] = useState<OverlayWindow>('5y');
  const [hover, setHover] = useState<{ values: Record<string, number> | null; time: string | null }>({ values: null, time: null });
  const rows = rowsFor(segment, etfs, { window, risk, against, sample });
  const scale = Math.max(1e-9, ...rows.map((row) => Math.abs(row.value ?? 0)));
  // Returns can have either sign: bars grow left or right from a center line.
  const signed = segment === 'returns';
  const colorOf = etfColor;

  const overlayData = useMemo(() => {
    if (history.status !== 'ok') return { lines: [] as ChartLine[], meta: {} as Record<string, { last: number | null; late: string | null }> };
    const block = history.data.etf;
    const years = OVERLAY_WINDOWS.find((item) => item.value === overlay)?.years ?? null;
    const start = windowStartIndex(block.dates, years);
    const meta: Record<string, { last: number | null; late: string | null }> = {};
    const lines: ChartLine[] = [];
    etfs.forEach((etf) => {
      const raw = block.series[etf.symbol];
      if (!raw) return;
      // Whole history: the file's own inception = 100 indices; windows: rebased to 100 at the window start.
      const rebased = years === null ? { values: raw.slice(start), baseIndex: raw.findIndex(finite), lateStart: false } : rebase(raw, start);
      const values = rebased.values;
      const last = [...values].reverse().find(finite) ?? null;
      meta[etf.symbol] = {
        last,
        late: rebased.lateStart && rebased.baseIndex !== null ? (block.dates[rebased.baseIndex] ?? null) : null,
      };
      lines.push({
        id: etf.symbol,
        color: etfColor(etf.symbol),
        dashed: etf.leverage > 1,
        points: block.dates.slice(start).map((time, offset) => ({ time, value: values[offset] ?? null })),
      });
    });
    return { lines, meta };
  }, [history, overlay, etfs]);

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
            { value: 'returns', label: '報酬' },
            { value: 'risk', label: '風險' },
            { value: 'sensitivity', label: '利率敏感度' },
            { value: 'cost', label: '成本' },
          ]}
        />
        {segment === 'returns' && (
          <div className="chip-row rates-chip-row" role="group" aria-label="報酬期間">
            {RETURN_WINDOWS.map((item) => (
              <button key={item.value} type="button" className={`chip rates-chip ${window === item.value ? 'active' : ''}`} aria-pressed={window === item.value} onClick={() => setWindow(item.value)}>
                {item.label}
              </button>
            ))}
          </div>
        )}
        {segment === 'risk' && (
          <div className="chip-row rates-chip-row" role="group" aria-label="風險指標">
            {RISK_METRICS.map((item) => (
              <button key={item.value} type="button" className={`chip rates-chip ${risk === item.value ? 'active' : ''}`} aria-pressed={risk === item.value} onClick={() => setRisk(item.value)}>
                {item.label}
              </button>
            ))}
          </div>
        )}
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
                  <span className={`rates-bar-track ${signed ? 'centered' : ''}`} aria-hidden="true">
                    <i
                      className={`rates-bar ${row.tone === 'neutral' ? 'neutral' : row.tone}`}
                      style={
                        signed
                          ? { left: `${row.value !== null && row.value < 0 ? 50 - width / 2 : 50}%`, width: `${width / 2}%` }
                          : { width: `${width}%` }
                      }
                    />
                  </span>
                  <b className={`rates-bar-value ${row.tone === 'neutral' ? '' : row.tone}`}>{row.text}</b>
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
        {segment === 'returns' && (
          <p className="rates-hint">總報酬含配息再投入；1 年為累積報酬，3 年以上為年化報酬。過去報酬不代表未來表現。</p>
        )}
        {segment === 'risk' && (
          <p className="rates-hint">波動度＝日對數報酬的年化標準差；回撤＝從前高到低點的跌幅。3 倍槓桿 ETF 的波動約為標的的 3 倍；回撤不會剛好是 3 倍（最多就是 −100%），但通常深得多、也更難回到前高。</p>
        )}
        {segment === 'sensitivity' && (
          <p className="rates-note">實證存續期間＝ETF 報酬對殖利率變動的迴歸斜率 ×(−100)，是過去樣本的平均關係，不是保證。殖利率約在美東 15:30 取價、ETF 在 16:00 收盤，時間不同步會讓日資料的估計偏小，所以預設看週資料。TMF 的數字已含 3 倍槓桿。</p>
        )}
        {segment === 'cost' && (
          <p className="rates-note">費用率、指數、存續期間等為人工整理的靜態資料，標「待核對」者請以發行商網站為準。TMF 另有交換合約的融資成本，不包含在費用率內。</p>
        )}
        <div className="rates-subhead">
          <h3>
            總報酬走勢 <small>{overlay === 'all' ? '成立日 = 100' : '區間起點 = 100'}{hover.time ? `・${slashDate(hover.time)}` : ''}</small>
          </h3>
          <Segmented label="走勢期間" value={overlay} onChange={setOverlay} options={OVERLAY_WINDOWS.map(({ value, label }) => ({ value, label }))} />
        </div>
        {history.status === 'ok' ? (
          <>
            <div className="rates-chart-wrap tall">
              <TimeChart
                lines={overlayData.lines}
                format={(value) => value.toFixed(0)}
                ariaLabel="五檔長天期美債 ETF 還原總報酬指數走勢"
                onHover={(values, time) => setHover({ values, time })}
              />
            </div>
            <ul className="rates-legend" aria-label="圖例">
              {etfs.map((etf) => {
                const meta = overlayData.meta[etf.symbol];
                const value = hover.values ? (hover.values[etf.symbol] ?? null) : (meta?.last ?? null);
                return (
                  <li key={etf.symbol}>
                    <i className={etf.leverage > 1 ? 'dashed' : ''} style={{ background: etf.leverage > 1 ? 'transparent' : colorOf(etf.symbol), borderColor: colorOf(etf.symbol) }} aria-hidden="true" />
                    <span>
                      {etf.symbol}
                      {meta?.late && <small className="rates-muted">（{slashMonth(meta.late)} 起算）</small>}
                    </span>
                    <b className={value === null ? '' : toneClass(value - 100)}>{value === null ? '—' : value.toFixed(1)}</b>
                  </li>
                );
              })}
            </ul>
          </>
        ) : (
          <SectionState history={history} what="總報酬走勢" />
        )}
        <p className="source-note">總報酬以 Yahoo 還原收盤價計算（含配息再投入），週資料；成立較晚的 ETF 從成立後第一筆資料起算。</p>
      </div>
    </section>
  );
}
