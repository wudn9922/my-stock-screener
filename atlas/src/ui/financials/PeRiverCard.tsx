import { Suspense, lazy, useEffect, useMemo, useState, type ReactNode } from 'react';
import { ChevronDown, RefreshCw } from 'lucide-react';
import type { FinancialHistory } from '../../fundamentals/FinancialHistory';
import { ZONE_TEXT, buildPeRiver, formatMultiple, zoneOf, type PriceBar } from '../../fundamentals/peRiver';
import { calendarDate, getMarketProfile } from '../../market-data/MarketProfile';
import { PanelBoundary } from '../PanelBoundary';
import { Skeleton } from '../PageState';
import { BAND_COLORS, buildRiverChartData } from './peRiverChartData';
import { coverageText, delayText, epsBasisText, excludedText, percentileText, premiumText, shortWarning, unavailableDetail } from './peRiverText';
import './peRiver.css';

// lightweight-charts is only fetched once there is a river to draw.
const PeRiverChart = lazy(() => import('./PeRiverChart'));

export type PriceLoad =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; bars: PriceBar[]; priceBasis: string | undefined; asOfSec: number; stale: boolean };

/** Weekly bars come with their own price basis; 10 years is what the provider offers for 1W. */
async function loadWeeklyPrices(symbol: string, signal: AbortSignal): Promise<PriceLoad> {
  try {
    const { marketProvider } = await import('../../app/dataSources');
    const result = await marketProvider().getBars(symbol, '1W', undefined, signal);
    const last = result.bars.at(-1);
    return {
      status: 'ok',
      bars: result.bars.map((bar) => ({ time: bar.time, close: bar.close })),
      priceBasis: result.priceBasis,
      asOfSec: result.quote?.asOf ?? result.asOf ?? last?.time ?? 0,
      stale: result.cacheStatus === 'stale',
    };
  } catch (error) {
    if (signal.aborted) return { status: 'loading' };
    return { status: 'error', message: error instanceof Error && error.message ? error.message : '股價資料暫時無法取得。' };
  }
}

/** Fetches the weekly price history for the file's symbol and shows the P/E river. */
export function PeRiverCard({ history }: { history: FinancialHistory }) {
  const symbol = history.symbol;
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ symbol: string; attempt: number; load: PriceLoad } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void loadWeeklyPrices(symbol, controller.signal).then((load) => {
      if (!controller.signal.aborted) setLoaded({ symbol, attempt, load });
    });
    return () => controller.abort();
  }, [symbol, attempt]);
  const load: PriceLoad = loaded && loaded.symbol === symbol && loaded.attempt === attempt ? loaded.load : { status: 'loading' };
  return <PeRiverPanel history={history} load={load} onRetry={() => setAttempt((n) => n + 1)} />;
}

/** The card itself (no fetching), so every state can be rendered and tested. */
export function PeRiverPanel({ history, load, onRetry }: { history: FinancialHistory; load: PriceLoad; onRetry?: () => void }) {
  const market = useMemo(() => getMarketProfile(history.symbol), [history.symbol]);
  const result = useMemo(
    () => (load.status === 'ok' ? buildPeRiver(history, load.bars, { asOfSec: load.asOfSec, priceBasis: load.priceBasis }) : null),
    [history, load],
  );
  const chartData = useMemo(() => (result?.status === 'ok' ? buildRiverChartData(result.model, market) : null), [result, market]);
  const state = load.status !== 'ok' ? load.status : result!.status === 'ok' ? 'ok' : 'unavailable';

  let body;
  if (load.status === 'loading') {
    body = (
      <div className="pe-river-loading" role="status" aria-label="載入股價與本益比河流中">
        <Skeleton height={28} width="60%" />
        <Skeleton height={220} />
      </div>
    );
  } else if (load.status === 'error') {
    body = (
      <div className="pe-river-empty" role="alert">
        <b>股價資料載入失敗</b>
        <p>{load.message}</p>
        <p>本益比河流需要約 10 年的週收盤價；財報其他區塊不受影響。</p>
        {onRetry && (
          <button type="button" className="ghost-button" onClick={onRetry}>
            <RefreshCw size={15} />
            重新載入
          </button>
        )}
      </div>
    );
  } else if (result && result.status === 'unavailable') {
    const detail = unavailableDetail(result.reason, result);
    body = (
      <div className="pe-river-empty" role="status" data-reason={result.reason}>
        <b>{result.message}</b>
        {detail && <p>{detail}</p>}
        {result.notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
        <p className="pe-river-fine">歷史本益比不代表未來，僅供研究參考。</p>
      </div>
    );
  } else if (result && chartData) {
    const { model } = result;
    const { current } = model;
    const warning = shortWarning(model);
    const dataDate = load.status === 'ok' && load.asOfSec > 0 ? calendarDate(load.asOfSec, market) : null;
    body = (
      <div className="pe-river-body">
        {current ? (
          <>
            <div className="fin-headline">
              <b className="fin-big">{formatMultiple(current.pe)}</b>
              <span className="fin-big-unit">目前本益比</span>
              <span className="fin-period">EPS {current.eps.toFixed(2)} · {current.basis === 'ttm' ? 'TTM' : '年度'}</span>
            </div>
            <div className="fin-chips">
              <span className="fin-chip pe-percentile">{percentileText(current)}</span>
              <span className="fin-chip">{premiumText(current)}</span>
            </div>
            <p className="pe-river-zone">{ZONE_TEXT[zoneOf(current.percentile)]}。</p>
          </>
        ) : (
          <p className="fin-card-note pe-river-nocurrent">{model.currentReason}</p>
        )}
        <PeRiverChartBoundary>
          <PeRiverChart data={chartData} ariaLabel={`${history.symbol} 股價與本益比河流圖，五條線為歷史本益比 P10 到 P90 對應的股價`} />
        </PeRiverChartBoundary>
        <ul className="pe-river-legend" aria-label="河流倍數">
          {model.levels.map((level) => (
            <li key={level.p}>
              <i style={{ background: BAND_COLORS[level.p] }} aria-hidden="true" />
              <span>
                P{level.p} · {formatMultiple(level.multiple)}
              </span>
              <b>{current ? (current.eps * level.multiple).toFixed(2) : '—'}</b>
            </li>
          ))}
        </ul>
        <p className="pe-river-fine">{current ? '右欄為以目前 EPS 乘上該倍數的股價。' : ''}橘黃線為股價，藍色線為 EPS × 歷史本益比倍數（縱軸為對數座標）。</p>
        {warning && <p className="fin-warn pe-river-warn">{warning}</p>}
        {model.notes.map((note) => (
          <p key={note} className="fin-warn pe-river-warn">
            {note}
          </p>
        ))}
        <dl className="pe-river-basis">
          <div>
            <dt>資料日</dt>
            <dd>
              股價至 {dataDate ?? '—'}（週 K 收盤，已還原分割、未還原股利）{load.status === 'ok' && load.stale ? '，快取資料可能不是最新' : ''}
            </dd>
          </div>
          <div>
            <dt>EPS 口徑</dt>
            <dd>
              {epsBasisText(model)}
              {current ? `；最新一期 ${current.epsPeriodEnd}` : ''}
            </dd>
          </div>
          <div>
            <dt>樣本</dt>
            <dd>
              {coverageText(model)}
              {excludedText(model) ? `。${excludedText(model)}` : ''}
            </dd>
          </div>
          <div>
            <dt>統計方式</dt>
            <dd>
              倍數線為本檔有效本益比的 P10／P30／P50／P70／P90（線性內插）；百分位為目前本益比在同一序列的名次；本益比 &gt; 200 倍視為異常剔除。{delayText(history.market)}
            </dd>
          </div>
        </dl>
        <p className="pe-river-fine">
          本益比由本頁財報檔重算，可能與圖表頁標頭的本益比（Yahoo／證交所口徑）略有差異。歷史本益比不代表未來，僅供研究參考。
        </p>
      </div>
    );
  }

  return (
    <details className="card pe-river" open data-group="pe-river" data-state={state}>
      <summary className="fin-card-head">
        <h2>本益比河流圖</h2>
        <ChevronDown size={18} className="fin-chevron" aria-hidden="true" />
      </summary>
      {body}
    </details>
  );
}

/** Chunk-load or chart errors become a short message inside the card instead of taking the page down. */
function PeRiverChartBoundary({ children }: { children: ReactNode }) {
  return (
    <PanelBoundary>
      <Suspense fallback={<Skeleton height={260} />}>{children}</Suspense>
    </PanelBoundary>
  );
}
