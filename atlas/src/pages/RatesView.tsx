import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { PageProps } from './pageProps';
import { loadRates, loadRatesHistory, type CachedResult, type Rates } from '../report/rates';
import { PageState } from '../ui/PageState';
import { formatDateTime } from '../ui/format';
import { KeyNumbers } from '../ui/rates/KeyNumbers';
import { SpreadCard } from '../ui/rates/SpreadCard';
import { ScenarioCard } from '../ui/rates/ScenarioCard';
import { EtfCompareCard } from '../ui/rates/EtfCompareCard';
import { slashDate, type HistoryState } from '../ui/rates/shared';
import '../ui/rates.css';

export type RatesViewProps = Pick<PageProps, 'navigate' | 'openChart'>;

/** Calendar days after which the summary counts as out of date (weekends and holidays included). */
const STALE_DAYS = 6;

function daysSince(date: string | null, now = Date.now()): number | null {
  if (!date) return null;
  const time = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(time) ? null : Math.floor((now - time) / 86400000);
}

/**
 * 美債與利率研究 (world page → rates view). Loads rates.json, then rates-history.json in parallel;
 * it never depends on the daily report, so it renders outside ReportGate.
 */
export default function RatesView({ openChart }: RatesViewProps) {
  const [rates, setRates] = useState<CachedResult<Rates> | null>(null);
  const [history, setHistory] = useState<HistoryState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [fedBound, setFedBound] = useState<'upper' | 'lower'>('upper');
  useEffect(() => {
    let live = true;
    const refresh = attempt > 0;
    setRates(null);
    setHistory({ status: 'loading' });
    void loadRates(refresh).then((result) => {
      if (live) setRates(result);
    });
    void loadRatesHistory(refresh).then((result) => {
      if (!live) return;
      setHistory(result.status === 'ok' ? { status: 'ok', data: result.data, stale: result.stale } : result);
    });
    return () => {
      live = false;
    };
  }, [attempt]);
  const retry = () => setAttempt((value) => value + 1);

  if (!rates) return <PageState kind="loading" title="載入美債利率資料…" />;
  if (rates.status === 'missing')
    return <PageState kind="empty" title="資料尚未產生" message="美債與利率資料還沒有產生，每日排程執行後就會出現。" onRetry={retry} />;
  if (rates.status !== 'ok') return <PageState kind="error" title="美債利率資料暫時無法顯示" message={rates.message} onRetry={retry} />;

  const data = rates.data;
  const age = daysSince(data.asOf);
  const outdated = age !== null && age > STALE_DAYS;
  return (
    <div className="rates-view">
      <p className="rates-meta">
        資料日 {slashDate(data.asOf)}
        {data.generatedAt && <> · 產生於 {formatDateTime(data.generatedAt)}</>}
        {data.sources.etfAsOf && data.sources.etfAsOf !== data.asOf && <> · ETF 資料日 {slashDate(data.sources.etfAsOf)}</>}
      </p>
      {(rates.stale || outdated || data.missing.length > 0) && (
        <div className="rates-alert" role="status">
          <AlertTriangle size={15} aria-hidden="true" />
          <span>
            {rates.stale && <>重新載入失敗，顯示上次成功取得的資料（{rates.message}）。</>}
            {outdated && <>資料已 {age} 天未更新。</>}
            {data.missing.length > 0 && <>以下序列本次未能更新、沿用舊值：{data.missing.join('、')}。</>}
          </span>
        </div>
      )}
      <KeyNumbers rates={data} history={history} fedBound={fedBound} />
      <div className="rates-grid">
        <SpreadCard rates={data} history={history} fedBound={fedBound} onFedBoundChange={setFedBound} />
        {data.etfs.length > 0 && <ScenarioCard etfs={data.etfs} />}
        {data.etfs.length > 0 && <EtfCompareCard etfs={data.etfs} openChart={openChart} />}
      </div>
      <footer className="rates-sources">
        <details className="rates-disclosure">
          <summary>資料來源與備註</summary>
          <div className="rates-disclosure-body">
        <p>
          來源：{data.sources.rates ?? 'FRED'}（美國聯準會 H.15 與 TIPS 資料）{data.sources.etf ? `；${data.sources.etf}` : ''}。
        </p>
        {data.notes.length > 0 && (
          <ul>
            {data.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
          </div>
        </details>
      </footer>
    </div>
  );
}
