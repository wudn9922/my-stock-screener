import { useMemo } from 'react';
import { ChevronRight } from 'lucide-react';
import type { PageProps } from './pageProps';
import { ChartAttribution, ReportGate, TrendBadge } from './ReportGate';
import { MARKET_KEYS, type IndexStatus, type MarketKey, type Report } from '../report/schema';
import { LineTextCard } from '../ui/LineTextCard';
import { MiniChart } from '../ui/MiniChart';
import { indicatorColors } from '../indicators/IndicatorRegistry';
import { formatDateTime, formatPercent, formatPrice, maDistance, maValue, toneClass } from '../ui/format';
import { serializeRoute } from '../app/routes';

export const MARKET_META: Record<MarketKey, { name: string; flag: string }> = {
  tw: { name: '台灣', flag: '🇹🇼' },
  jp: { name: '日本', flag: '🇯🇵' },
  kr: { name: '韓國', flag: '🇰🇷' },
  eu: { name: '歐洲', flag: '🇪🇺' },
  us: { name: '美國', flag: '🇺🇸' },
};

function IndexCard({ index, onOpen }: { index: IndexStatus; onOpen: () => void }) {
  const tone = toneClass(index.changePct);
  return (
    <a
      className="card index-card"
      href={serializeRoute({ page: 'chart', symbol: index.symbol, tf: '1D' })}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onOpen();
      }}
      aria-label={`${index.name} ${formatPrice(index.close)}，開啟完整圖表`}
    >
      <div className="index-card-head">
        <div className="index-card-name">
          <b>{index.name}</b>
          <small>{index.symbol}</small>
        </div>
        <div className={`index-card-quote ${tone}`}>
          <b>{formatPrice(index.close)}</b>
          <span>{formatPercent(index.changePct)}</span>
        </div>
      </div>
      <TrendBadge trend={index.trend} label={index.trendLabel} />
      <MiniChart symbol={index.symbol} maList={index.maList} height={164} />
      <div className="index-card-foot">
        <span className="ma-legend">
          {index.maList.length ? (
            index.maList.map((period, i) => {
              const distance = maDistance(index.close, maValue(index.maValues, period));
              return (
                <span key={period} className="ma-chip">
                  <i style={{ background: indicatorColors[i % indicatorColors.length] }} aria-hidden="true" />
                  MA{period}
                  {distance !== null && <em className={toneClass(distance)}>{formatPercent(distance, 1)}</em>}
                </span>
              );
            })
          ) : (
            <span className="muted-text">未設定均線</span>
          )}
        </span>
        <span className="index-score">
          {index.scoreLabel && <b>{index.scoreLabel}</b>}
          {index.score !== null && index.scoreMax ? ` ${index.score > 0 ? '+' : ''}${index.score}/${index.scoreMax}` : ''}
          <ChevronRight size={16} aria-hidden="true" className="card-chevron" />
        </span>
      </div>
    </a>
  );
}

function sourceSummary(indices: readonly IndexStatus[]) {
  const sources = [...new Set(indices.map((index) => index.source).filter(Boolean))];
  const asOf = indices
    .map((index) => index.asOf)
    .filter((value): value is string => !!value)
    .sort()
    .at(-1);
  return { sources, asOf };
}

function MarketView({ report, market, openChart }: { report: Report; market: MarketKey } & Pick<PageProps, 'openChart'>) {
  const data = report.markets.find((entry) => entry.key === market);
  const meta = MARKET_META[market];
  const { sources, asOf } = sourceSummary(data?.indices ?? []);
  const counts = useMemo(() => {
    const out = { bull: 0, bear: 0, other: 0 };
    for (const index of data?.indices ?? [])
      if (index.trend === 'bull') out.bull++;
      else if (index.trend === 'bear') out.bear++;
      else out.other++;
    return out;
  }, [data]);
  if (!data)
    return (
      <div className="page-state empty" role="status">
        <b>{meta.name}市場今日沒有資料</b>
        <p>報告未包含此市場，請切換其他市場或稍後再看。</p>
      </div>
    );
  return (
    <div className="markets-layout">
      <section className="markets-main" aria-label={`${data.name || meta.name}指數`}>
        <div className="section-bar">
          <h2>
            {data.name || meta.name}主要指數
            <span className="count-pill">{data.indices.length}</span>
          </h2>
          {data.indices.length > 0 && (
            <span className="breadth" aria-label={`多頭 ${counts.bull}、空頭 ${counts.bear}、中性 ${counts.other}`}>
              <span className="trend-dot bull" />多 {counts.bull}
              <span className="trend-dot bear" />空 {counts.bear}
              {counts.other > 0 && (
                <>
                  <span className="trend-dot neutral" />中性 {counts.other}
                </>
              )}
            </span>
          )}
        </div>
        {data.indices.length ? (
          <div className="index-grid">
            {data.indices.map((index) => (
              <IndexCard
                key={index.symbol}
                index={index}
                onOpen={() => openChart({ symbol: index.symbol, name: index.name, tf: '1D', maList: index.maList })}
              />
            ))}
          </div>
        ) : (
          <p className="muted-text">此市場沒有啟用的指數。</p>
        )}
      </section>
      <aside className="markets-side">
        <LineTextCard title={`${data.flag || meta.flag} ${data.name || meta.name} LINE 摘要`} text={data.lineText} />
        <p className="source-note">
          資料來源：{sources.length ? sources.join('、') : '每日量化報告'}
          {asOf ? ` · 資料時間 ${formatDateTime(asOf)}` : ''}
        </p>
      </aside>
    </div>
  );
}

export default function MarketsPage({ report, reload, navigate, openChart, market }: PageProps & { market: MarketKey }) {
  return (
    <div className="page markets-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">大盤</h2>
          {report.status === 'ok' && (
            <p className="page-sub">
              報告日 {formatDateTime(report.report.reportDate, false)}
              {report.report.generatedAt ? ` · 產生於 ${formatDateTime(report.report.generatedAt)}` : ''}
            </p>
          )}
        </div>
      </div>
      <div className="tabs market-tabs" role="tablist" aria-label="市場">
        {MARKET_KEYS.map((key) => {
          const entry = report.status === 'ok' ? report.report.markets.find((m) => m.key === key) : undefined;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={market === key}
              className={market === key ? 'active' : ''}
              onClick={() => navigate({ page: 'markets', market: key })}
            >
              <span aria-hidden="true" className="flag">{entry?.flag || MARKET_META[key].flag}</span>
              {MARKET_META[key].name}
            </button>
          );
        })}
      </div>
      <ReportGate report={report} reload={reload}>
        {(data) => <MarketView report={data} market={market} openChart={openChart} />}
      </ReportGate>
      <footer className="page-foot">
        <span>延遲行情，僅供研究參考，非投資建議。</span>
        <ChartAttribution />
      </footer>
    </div>
  );
}
