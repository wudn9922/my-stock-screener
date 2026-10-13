import { useCallback, useEffect, useMemo, useState } from 'react';
import type { PageProps } from './pageProps';
import { PageState, Skeleton } from '../ui/PageState';
import { financialsKey, loadFinancials, type FinancialsLoadResult, type PeriodKind } from '../fundamentals/FinancialHistory';
import { METRIC_GROUPS, type MetricGroupId, type MetricId } from '../fundamentals/financialMetrics';
import type { Report } from '../report/schema';
import type { LocalSymbol, SearchResult } from '../ui/SymbolSearch';
import { formatDateTime } from '../ui/format';
import { FinancialsHeader, type DisplayMode } from '../ui/financials/FinancialsHeader';
import { MetricCard } from '../ui/financials/MetricCard';
import { FinancialsTable } from '../ui/financials/FinancialsTable';
import { SummaryChips } from '../ui/financials/SummaryChips';
import { buildView, depthText, provenanceText } from '../ui/financials/financialsView';
import '../ui/financials.css';

export interface FinancialsPageProps extends PageProps {
  /** Normalized upper-case symbol from `?symbol=`; absent when the URL names none. */
  symbol?: string;
  /** `'q'` for quarterly figures (`?p=q`); undefined means annual. */
  p?: 'q';
}

const VIEW_KEY = 'atlas-financials-view-v1';
const readMode = (): DisplayMode => {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'chart';
  } catch {
    return 'chart';
  }
};
const writeMode = (mode: DisplayMode) => {
  try {
    localStorage.setItem(VIEW_KEY, mode);
  } catch {
    /* Remembering the view is only a convenience. */
  }
};

/** Names the report already knows (index members, scan groups); the symbol directory is the fallback. */
function reportSymbols(report: Report | null): LocalSymbol[] {
  if (!report) return [];
  const map = new Map<string, string>();
  for (const group of report.groups) for (const item of group.items) if (item.status !== 'failed' && !map.has(item.symbol)) map.set(item.symbol, item.name);
  return [...map].map(([symbol, name]) => ({ symbol, name }));
}

/** Why some cards show less for this company (bank statement format, ADR EPS, cumulative quarters). */
function cardNote(group: MetricGroupId, issues: readonly string[], foreignReporter: boolean, kind: PeriodKind): string | null {
  const bank = issues.includes('financial-sector-format');
  if (group === 'growth') {
    const notes: string[] = [];
    if (foreignReporter || issues.includes('foreign-filer')) notes.push('外國發行人：EPS 為每普通股，非每 ADR。');
    if (kind === 'quarterly' && issues.includes('quarterly-ytd-suspected')) notes.push('季度數字疑似為年初累計值，季度長條可能逐季遞增，請以年度為主。');
    return notes.join('') || null;
  }
  if (!bank) return null;
  if (group === 'profitability') return '金融業財報沒有毛利與營業利益：毛利率、營業利益率不適用，淨利率照常計算。';
  if (group === 'nonOperating') return '金融業沒有營業利益，業外損益與業外占比不適用；有效稅率照常計算。';
  if (group === 'returns') return 'ROIC 需要營業利益，金融業不適用；請看 ROE。';
  if (group === 'strength') return '銀行的負債多為存款（營運原料），負債權益比不宜與一般公司比較；沒有營業利益，利息保障倍數不適用。';
  return null;
}

export default function FinancialsPage({ report, navigate, symbol, p }: FinancialsPageProps) {
  const kind: PeriodKind = p === 'q' ? 'quarterly' : 'annual';
  const [mode, setMode] = useState<DisplayMode>(readMode);
  const [loaded, setLoaded] = useState<{ symbol: string; result: FinancialsLoadResult } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [directoryName, setDirectoryName] = useState<{ symbol: string; name: string } | null>(null);
  const [metricOf, setMetricOf] = useState<Partial<Record<MetricGroupId, MetricId>>>({});
  const localSymbols = useMemo(() => reportSymbols(report.status === 'ok' ? report.report : null), [report]);
  const key = financialsKey(symbol);

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
  // A file with only one period type still opens on the other one.
  const effectiveKind: PeriodKind = history && !history[kind].end.length ? (kind === 'annual' ? 'quarterly' : 'annual') : kind;
  const annualView = useMemo(() => (history ? buildView(history, 'annual') : null), [history]);
  const view = useMemo(
    () => (history ? (effectiveKind === 'annual' ? annualView : buildView(history, 'quarterly')) : null),
    [history, effectiveKind, annualView],
  );

  const onKind = useCallback(
    (next: PeriodKind) => navigate({ page: 'financials', ...(symbol ? { symbol } : {}), ...(next === 'quarterly' ? { p: 'q' as const } : {}) }, { replace: true }),
    [navigate, symbol],
  );
  const onMode = (next: DisplayMode) => {
    setMode(next);
    writeMode(next);
  };
  const onSymbol = (picked: SearchResult) => navigate({ page: 'financials', symbol: picked.symbol, ...(p ? { p } : {}) });
  const onBack = () => navigate(symbol ? { page: 'chart', symbol } : { page: 'chart' });

  let body;
  if (!symbol) body = <PageState kind="empty" title="選擇一檔股票" message="用上方搜尋輸入代號或名稱，或從圖表頁按「財報」開啟。" />;
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
  else if (history && view && annualView) {
    const issues = history.quality.issues;
    const splitEvents = history.split.events;
    body = (
      <>
        <SummaryChips history={history} annual={annualView} current={view} />
        <div className="fin-notes">
          <p className="fin-depth">{depthText(history, effectiveKind)}</p>
          <p>
            {provenanceText(history, effectiveKind)}
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
          {(history.foreignReporter || issues.includes('foreign-filer')) && <p>外國發行人：EPS 為每普通股，非每 ADR。</p>}
        </div>
        {mode === 'table' ? (
          <FinancialsTable history={history} view={view} />
        ) : (
          <div className="fin-cards">
            {METRIC_GROUPS.map((group) => (
              <MetricCard
                key={group.id}
                group={group}
                history={history}
                view={view}
                metric={metricOf[group.id] ?? group.metrics.find((id) => view.metrics[id].window.some((i) => view.metrics[id].values[i] !== null)) ?? group.metrics[0]!}
                onMetric={(id) => setMetricOf((current) => ({ ...current, [group.id]: id }))}
                note={cardNote(group.id, issues, history.foreignReporter, effectiveKind)}
              />
            ))}
          </div>
        )}
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
        kind={kind}
        mode={mode}
        localSymbols={localSymbols}
        onBack={onBack}
        onKind={onKind}
        onMode={onMode}
        onSymbol={onSymbol}
      />
      {body}
      <footer className="page-foot fin-foot">
        <span>
          限制：Yahoo 只提供約 4 年年度、5–8 季季度財報，本站逐季累積、無法往回補（部分美股另以 SEC 申報補歷史）；比率由本頁依公式計算，可能與公司公告口徑不同；有息負債可能含租賃負債。
        </span>
        <span>僅供研究參考，非投資建議。</span>
      </footer>
    </div>
  );
}

const trim = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(2));
/** 4 → 「1 拆 4」; a reverse split 0.1 → 「10 併 1」. */
const splitText = (ratio: number) => (ratio >= 1 ? `1 拆 ${trim(ratio)}` : `${trim(1 / ratio)} 併 1`);
