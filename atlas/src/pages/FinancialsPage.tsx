import type { PageProps } from './pageProps';
import { PageState } from '../ui/PageState';

export interface FinancialsPageProps extends PageProps {
  /** Normalized upper-case symbol from `?symbol=`; absent when the URL names none. */
  symbol?: string;
  /** `'q'` for quarterly figures (`?p=q`); undefined means annual. */
  p?: 'q';
}

/** Placeholder: replaced by the financials analysis page (docs/report/financials/<KEY>.json). */
export default function FinancialsPage({ symbol }: FinancialsPageProps) {
  return (
    <div className="page financials-page">
      <PageState kind="empty" title="資料尚未產生" message={symbol ? `${symbol} 的財報資料還沒有產生。` : '請從圖表頁選擇股票後再開啟財報。'} />
    </div>
  );
}
