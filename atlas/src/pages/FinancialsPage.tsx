import { userStorage } from '../auth/storage';
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { PageProps } from './pageProps';
import { PageState, Skeleton } from '../ui/PageState';
import { emptySeries, financialsKey, loadFinancials, type FinancialsLoadResult, type PeriodKind } from '../fundamentals/FinancialHistory';
import { METRIC_GROUPS, type MetricGroupId, type MetricId } from '../fundamentals/financialMetrics';
import type { Timeframe } from '../market-data/MarketDataProvider';
import { twListingForAdr } from '../fundamentals/adrListings';
import type { Report } from '../report/schema';
import type { LocalSymbol, SearchResult } from '../ui/SymbolSearch';
import { formatDateTime } from '../ui/format';
import { FinancialsHeader, type DisplayMode } from '../ui/financials/FinancialsHeader';
import { FinancialNote } from '../ui/financials/FinancialNote';
import { KeyMetrics } from '../ui/financials/KeyMetrics';
import { MetricCard } from '../ui/financials/MetricCard';
import { QuarterStrip } from '../ui/financials/QuarterStrip';
import { SpanControl } from '../ui/financials/SpanControl';
import { StatementTable } from '../ui/financials/StatementTable';
import { PeRiverCard } from '../ui/financials/PeRiverCard';
import { SummaryChips } from '../ui/financials/SummaryChips';
import { VolumePanel } from '../ui/research/VolumePanel';
import { InstitutionPanel } from '../ui/research/InstitutionPanel';
import { OfficialAlerts } from '../ui/research/OfficialAlerts';
import { SecRadar } from '../ui/research/SecRadar';
import { ChipsSection } from '../ui/financials/ChipsCard';
import { PeerComparison } from '../ui/financials/PeerComparison';
import { isChipsPageSymbol } from '../report/twChips';
import { TAIWAN_SYMBOL_REGEX } from '../market-data/MarketProfile';
import { CHART_PERIODS, adrEpsNote, buildView, cardSummaryLine, depthText, provenanceText } from '../ui/financials/financialsView';
import '../ui/financials.css';
import { dataHealth } from '../report/dataHealth';

export interface FinancialsPageProps extends PageProps {
  /** Normalized upper-case symbol from `?symbol=`; absent when the URL names none. */
  symbol?: string;
  /** Explicit period selection; US defaults to quarters and Taiwan to annual. */
  p?: 'q' | 'a';
  /** Chart timeframe the 「K線」 tab returns to (`?tf=`). */
  tf?: Timeframe;
}

const VIEW_KEY = 'atlas-financials-view-v1';
const readMode = (): DisplayMode => {
  try {
    return userStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'chart';
  } catch {
    return 'chart';
  }
};
const writeMode = (mode: DisplayMode) => {
  try {
    userStorage.setItem(VIEW_KEY, mode);
  } catch {
    /* Remembering the view is only a convenience. */
  }
};

/** Cards open at first render: growth and profitability. Every other section starts folded. */
const OPEN_GROUPS: readonly MetricGroupId[] = ['growth', 'profitability'];

/** Names the report already knows (index members, scan groups); the symbol directory is the fallback. */
function reportSymbols(report: Report | null): LocalSymbol[] {
  if (!report) return [];
  const map = new Map<string, string>();
  for (const group of report.groups) for (const item of group.items) if (item.status !== 'failed' && !map.has(item.symbol)) map.set(item.symbol, item.name);
  return [...map].map(([symbol, name]) => ({ symbol, name }));
}

/**
 * Why some cards show less for this company (bank statement format, ADR EPS, cumulative quarters).
 * adrBasis: the EPS are already per ADR (adrEpsNote), so the per-ordinary-share remark does not apply.
 */
function cardNote(group: MetricGroupId, issues: readonly string[], foreignReporter: boolean, kind: PeriodKind, adrBasis: boolean): string | null {
  const bank = issues.includes('financial-sector-format');
  if (group === 'growth') {
    const notes: string[] = [];
    if (!adrBasis && (foreignReporter || issues.includes('foreign-filer'))) notes.push('外國發行人：EPS 為每普通股，非每 ADR。');
    if (kind === 'quarterly' && issues.includes('quarterly-ytd-suspected')) notes.push('季度數字疑似為年初累計值，季度長條可能逐季遞增，請以年度為主。');
    return notes.join('') || null;
  }
  if (!bank) return null;
  if (group === 'profitability') return '金融業財報沒有毛利與營業利益：毛利、毛利率、營業利益率不適用，淨利率照常計算。';
  if (group === 'nonOperating') return '金融業沒有營業利益，業外損益與業外占比不適用；有效稅率照常計算。';
  if (group === 'returns') return 'ROIC 需要營業利益，金融業不適用；請看 ROE。';
  if (group === 'strength') return '銀行的負債多為存款（營運原料），負債權益比不宜與一般公司比較；沒有營業利益，利息保障倍數不適用。';
  return null;
}

export default function FinancialsPage({ report, navigate, symbol, p, tf }: FinancialsPageProps) {
  const isTaiwanSymbol = !!symbol && (TAIWAN_SYMBOL_REGEX.test(symbol) || /^\d{4,6}[A-Z]?$/.test(symbol));
  const key = financialsKey(symbol);
  const showQuarterly = key !== null || isTaiwanSymbol;
  const kind: PeriodKind = p === 'a' ? 'annual' : p === 'q' || !isTaiwanSymbol ? 'quarterly' : 'annual';
  const [mode, setMode] = useState<DisplayMode>(readMode);
  const [loaded, setLoaded] = useState<{ symbol: string; result: FinancialsLoadResult } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [directoryName, setDirectoryName] = useState<{ symbol: string; name: string } | null>(null);
  const [metricOf, setMetricOf] = useState<Partial<Record<MetricGroupId, MetricId>>>({});
  const [spans, setSpans] = useState<Record<PeriodKind, number>>({ annual: CHART_PERIODS.annual, quarterly: CHART_PERIODS.quarterly });
  const localSymbols = useMemo(() => reportSymbols(report.status === 'ok' ? report.report : null), [report]);

  useEffect(() => {
    if (!symbol) return;
    let active = true;
    void loadFinancials(symbol, attempt > 0).then((result) => {
      if (active) setLoaded({ symbol, result });
    });
    return () => {
      active = false;
    };
  }, [symbol, attempt]);

  // A bare Taiwan code (`?symbol=2330`) is resolved through the symbol directory once.
  useEffect(() => {
    if (!symbol || key || !/^\d{4,6}[A-Z]?$/.test(symbol)) return;
    let active = true;
    void import('../app/dataSources')
      .then((module) => module.searchStocks(symbol, 10))
      .then((entries) => {
        const match = entries.find((entry) => entry.symbol.startsWith(`${symbol}.`));
        if (active && match) navigate({ page: 'financials', symbol: match.symbol, ...(p ? { p } : {}) }, { replace: true });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [symbol, key, p, navigate]);

  const reportName = symbol ? (localSymbols.find((item) => item.symbol === symbol)?.name ?? null) : null;
  useEffect(() => {
    if (!symbol || reportName || !key) return;
    let active = true;
    void import('../app/dataSources')
      .then((module) => module.searchStocks(symbol, 5))
      .then((entries) => {
        const match = entries.find((entry) => entry.symbol === symbol);
        if (active && match) setDirectoryName({ symbol, name: match.name });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [symbol, reportName, key]);
  const name = reportName ?? (directoryName && directoryName.symbol === symbol ? directoryName.name : null);

  const result = loaded && loaded.symbol === symbol ? loaded.result : null;
  const history = result?.status === 'ok' ? result.data : null;
  useEffect(() => {
    if (!symbol || !result) return;
    dataHealth.record(result.status === 'ok'
      ? { id: 'financials', label: `${symbol} 財報`, status: 'ok', date: result.data.generatedAt, source: result.data.source, detail: `${result.data.quarterly.end.length} 季、${result.data.annual.end.length} 年；品質 ${result.data.quality.level ?? '未評定'}；缺值仍以「—」呈現` }
      : { id: 'financials', label: `${symbol} 財報`, status: result.status, detail: result.message });
  }, [symbol, result]);
  // Preserve each source's quarterly periods; missing quarters are never filled from annual EPS.
  const effectiveKind: PeriodKind = history && showQuarterly && !history[kind].end.length ? (kind === 'annual' ? 'quarterly' : 'annual') : kind;
  const displayHistory = useMemo(() => {
    if (!history) return null;
    const maskSummedAnnualEps = history.market === 'US' && history.quality.issues.includes('eps-annual-summed');
    const annual = maskSummedAnnualEps
      ? {
          ...history.annual,
          values: {
            ...history.annual.values,
            epsDiluted: history.annual.end.map(() => null),
            epsBasic: history.annual.end.map(() => null),
          },
        }
      : history.annual;
    return { ...history, annual };
  }, [history]);
  const span = spans[effectiveKind];
  const annualView = useMemo(() => (displayHistory ? buildView(displayHistory, 'annual') : null), [displayHistory]);
  const quarterlyView = useMemo(() => (displayHistory && showQuarterly ? buildView(displayHistory, 'quarterly') : null), [displayHistory, showQuarterly]);
  // The chart range applies to the period type on screen; the summary tiles keep the default annual window.
  const view = useMemo(() => (displayHistory ? buildView(displayHistory, effectiveKind, span) : null), [displayHistory, effectiveKind, span]);

  useEffect(() => {
    if (showQuarterly || p !== 'q') return;
    navigate({ page: 'financials', ...(symbol ? { symbol } : {}), ...(tf ? { tf } : {}) }, { replace: true });
  }, [showQuarterly, p, navigate, symbol, tf]);

  const onKind = useCallback(
    (next: PeriodKind) =>
      navigate(
        {
          page: 'financials',
          ...(symbol ? { symbol } : {}),
          ...(tf ? { tf } : {}),
          p: next === 'quarterly' ? 'q' : 'a',
        },
        { replace: true },
      ),
    [navigate, symbol, tf, showQuarterly],
  );
  const onMode = (next: DisplayMode) => {
    setMode(next);
    writeMode(next);
  };
  // Changing the stock keeps the period type but not the chart timeframe, which belonged to the previous symbol.
  const onSymbol = (picked: SearchResult) => navigate({
    page: 'financials',
    symbol: picked.symbol,
    p: effectiveKind === 'quarterly' ? 'q' : 'a',
  });
  const homeListing = twListingForAdr(symbol);
  const onHomeListing = (listing: { tw: string }) => navigate({ page: 'financials', symbol: listing.tw, ...(p ? { p } : {}) });
  const onBack = () => navigate(symbol ? { page: 'chart', symbol, ...(tf ? { tf } : {}) } : { page: 'chart' });

  let body;
  if (!symbol) body = <PageState kind="empty" title="選擇一檔股票" message="用上方搜尋輸入代號或名稱，或從圖表頁按「財務」開啟。" />;
  else if (!key && !/^\d{4,6}[A-Z]?$/.test(symbol))
    body = <PageState kind="empty" title="沒有個股財報" message={`${symbol} 是指數或不支援的代號，指數沒有財報。`} />;
  else if (!result)
    body = (
      <div className="fin-loading" role="status" aria-label="載入財報中">
        <Skeleton height={64} />
        <Skeleton height={240} />
        <Skeleton height={240} />
      </div>
    );
  else if (result.status === 'missing')
    body = (
      <PageState kind="empty" title="財報資料尚未產生" message={`${result.message}排程每天更新，新加入的股票約需一兩天；指數、ETF 沒有財報。`} />
    );
  else if (result.status !== 'ok')
    body = <PageState kind="error" title={result.status === 'invalid' ? '財報資料無法辨識' : '財報資料載入失敗'} message={result.message} onRetry={() => setAttempt((n) => n + 1)} />;
  else if (history && displayHistory && view && annualView && (!showQuarterly || quarterlyView)) {
    const issues = history.quality.issues;
    const splitEvents = history.split.events;
    const adrNote = adrEpsNote(history);
    const metricCard = (group: (typeof METRIC_GROUPS)[number]) => (
      <MetricCard
        key={group.id}
        group={group}
        history={displayHistory}
        view={view}
        metric={metricOf[group.id] ?? group.metrics.find((id) => view.metrics[id].window.some((i) => view.metrics[id].values[i] !== null)) ?? group.metrics[0]!}
        onMetric={(id) => setMetricOf((current) => ({ ...current, [group.id]: id }))}
        note={cardNote(group.id, issues, history.foreignReporter, effectiveKind, adrNote !== null)}
        defaultOpen={OPEN_GROUPS.includes(group.id)}
        summary={cardSummaryLine(view, group.metrics, history.currency)}
      />
    );
    const leading = METRIC_GROUPS.filter((group) => OPEN_GROUPS.includes(group.id));
    const folded = METRIC_GROUPS.filter((group) => !OPEN_GROUPS.includes(group.id));
    body = (
      <>
        {showQuarterly && quarterlyView && <QuarterStrip history={displayHistory} quarterly={quarterlyView} />}
        {mode === 'table' ? (
          <StatementTable history={displayHistory} view={view} />
        ) : (
          <>
            <SpanControl
              kind={effectiveKind}
              count={displayHistory[effectiveKind].end.length}
              span={span}
              onSpan={(next) => setSpans((current) => ({ ...current, [effectiveKind]: next }))}
            />
            <div className="fin-cards">{leading.map(metricCard)}</div>
          </>
        )}
        <div className="fin-folds">
          <KeyMetrics history={displayHistory} view={view} />
          <SummaryChips history={displayHistory} annual={annualView} current={view} />
          <PeRiverCard history={history.market === 'US' ? { ...displayHistory, quarterly: emptySeries() } : displayHistory} />
          {mode === 'table' ? null : folded.map(metricCard)}
        </div>
        <FinancialNote className="fin-notes" label="資料來源與備註">
          <p className="fin-depth">{depthText(displayHistory, effectiveKind)}</p>
          <p>
            {provenanceText(displayHistory, effectiveKind)}
            {history.checkedAt ? ` · 資料更新日 ${formatDateTime(history.checkedAt, false)}` : ''}
            {` · 金額單位：財報幣別 ${history.currency}`}
          </p>
          {effectiveKind !== kind && <p className="fin-warn">這檔股票沒有{kind === 'annual' ? '年度' : '季度'}資料，先顯示{effectiveKind === 'annual' ? '年度' : '季度'}。</p>}
          {history.split.adjusted && splitEvents.length > 0 && (
            <p>
              已依股票分割還原 EPS 與股數：
              {splitEvents.map((event) => `${formatDateTime(event.date, false)}（${splitText(event.ratio)}）`).join('、')}
            </p>
          )}
          {adrNote && <p>{adrNote}</p>}
          {!adrNote && (history.foreignReporter || issues.includes('foreign-filer')) && <p>外國發行人：EPS 為每普通股，非每 ADR。</p>}
        </FinancialNote>
      </>
    );
  }

  return (
    <div className="page financials-page">
      <FinancialsHeader
        key={symbol ?? ''}
        symbol={symbol}
        name={name}
        history={history}
        kind={effectiveKind}
        showQuarterly={showQuarterly}
        futuHref={symbol && key && !isTaiwanSymbol ? `https://www.futunn.com/stock/${encodeURIComponent(symbol)}-US` : null}
        mode={mode}
        localSymbols={localSymbols}
        homeListing={homeListing}
        onBack={onBack}
        onKind={onKind}
        onMode={onMode}
        onSymbol={onSymbol}
        onHomeListing={onHomeListing}
      />
      {body}
      {symbol && isChipsPageSymbol(symbol) && <><ChipsSection symbol={symbol} /><InstitutionPanel key={symbol} symbol={symbol}/><OfficialAlerts symbol={symbol}/></>}
      {symbol && key && <VolumePanel key={`volume-${symbol}`} symbol={symbol}/>}
      {symbol && key && !isTaiwanSymbol && <SecRadar symbol={symbol}/>}
      {history && displayHistory && <PeerComparison key={`peers-${history.symbol}`} history={displayHistory} kind={effectiveKind} navigate={navigate} />}
      <footer className="page-foot fin-foot">
        <FinancialNote label="資料限制與研究說明">
        <p>
          {showQuarterly
            ? '限制：Yahoo 只提供約 4 年年度、5–8 季季度財報，本站逐季累積、無法往回補（部分美股另以 SEC 申報補歷史，台股另以 FinMind〔源自公開資訊觀測站〕回溯至 2015 年）；比率由本頁依公式計算，可能與公司公告口徑不同；有息負債可能含租賃負債。'
            : '限制：缺少的財報欄位以「—」呈現；比率由本頁依所選期間計算，可能與公司公告口徑不同。'}
        </p>
        <p>僅供研究參考，非投資建議。</p>
        </FinancialNote>
      </footer>
    </div>
  );
}

const trim = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(2));
/** 4 → 「1 拆 4」; a reverse split 0.1 → 「10 併 1」. */
const splitText = (ratio: number) => (ratio >= 1 ? `1 拆 ${trim(ratio)}` : `${trim(1 / ratio)} 併 1`);
