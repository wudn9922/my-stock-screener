import type { ReactNode } from 'react';
import { PageState } from '../ui/PageState';
import type { Report } from '../report/schema';
import type { ReportState } from '../report/useReport';

/** Renders the page only with a usable report; otherwise a friendly loading/empty/error state. */
export function ReportGate({
  report,
  reload,
  children,
}: {
  report: ReportState;
  reload: () => void;
  children: (report: Report) => ReactNode;
}) {
  if (report.status === 'loading') return <PageState kind="loading" title="載入每日報告…" />;
  if (report.status === 'ok') return <>{children(report.report)}</>;
  return (
    <PageState
      kind={report.status === 'missing' ? 'empty' : 'error'}
      title={report.status === 'missing' ? '今日報告尚未產生' : '每日報告暫時無法顯示'}
      message={`${report.message} 你仍可使用搜尋與圖表查看任何股票。`}
      onRetry={reload}
    />
  );
}

export const TREND_TEXT: Record<string, string> = {
  bull: '多頭',
  bear: '空頭',
  neutral: '盤整',
  unknown: '未判定',
};

export function TrendBadge({ trend, label }: { trend: string; label?: string }) {
  const text = label?.trim() || TREND_TEXT[trend] || '未判定';
  return (
    <span className={`trend-badge trend-${TREND_TEXT[trend] ? trend : 'unknown'}`} title={text}>
      {text}
    </span>
  );
}

export function ChartAttribution() {
  return (
    <span className="attribution">
      圖表技術：
      <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">
        TradingView Lightweight Charts™
      </a>
    </span>
  );
}
