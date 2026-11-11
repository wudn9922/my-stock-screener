import { useState } from 'react';
import { Share2 } from 'lucide-react';
import { PUBLIC_BASE } from '../app/HostingMode';
import { serializeRoute } from '../app/routes';
import type { Timeframe } from '../market-data/MarketDataProvider';

/** Shares only a canonical stock/chart route; no account, watchlist or session identifiers. */
export function ShareStockLink({ symbol, timeframe }: { symbol: string; timeframe: Timeframe }) {
  const [status, setStatus] = useState('');

  async function shareStock() {
    const url = `${location.origin}${PUBLIC_BASE}${serializeRoute({ page: 'chart', symbol, tf: timeframe })}`;
    setStatus('');
    try {
      if (typeof navigator.share === 'function') {
        await navigator.share({ title: `${symbol} 個股圖表`, url });
      } else {
        await navigator.clipboard.writeText(url);
        setStatus('連結已複製');
      }
    } catch (error) {
      // Closing the native share sheet is expected, not a failure.
      if (error instanceof Error && error.name === 'AbortError') return;
      setStatus('分享失敗，請重試');
    }
  }

  return (
    <div className="ws-share-stock">
      <button type="button" className="ws-share-link" onClick={() => void shareStock()}>
        <Share2 size={16} aria-hidden="true" />
        分享個股連結
      </button>
      <span className="ws-share-status" role="status">{status}</span>
    </div>
  );
}
