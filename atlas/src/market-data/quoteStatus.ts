import { getMarketProfile } from './MarketProfile';
import { formatLiveTime, inRegularSession } from './LiveQuotes';
import type { Quote } from './MarketDataProvider';

/** Shared regular-session label. Extended trades never supply this timestamp or price. */
export function quoteStatus(symbol: string, quote: Pick<Quote, 'asOf' | 'dataState' | 'retrievedAt'> | null | undefined, receivedAt?: number, nowMs = Date.now()): string {
  const updatedAt = receivedAt ?? (quote?.retrievedAt ? quote.retrievedAt * 1000 : undefined);
  const update = updatedAt && Number.isFinite(updatedAt) ? formatLiveTime(updatedAt) : '—';
  if (quote?.dataState === 'simulated') return `模擬報價 · 更新於 ${update}`;
  // The feed's final-print grace period is not part of the regular trading session.
  if (inRegularSession(getMarketProfile(symbol), nowMs, 0)) return `延遲報價 · 更新於 ${update}`;
  const trade = quote?.asOf;
  const time = trade && Number.isFinite(trade) && trade * 1000 <= nowMs + 300_000
    ? new Intl.DateTimeFormat('zh-TW', { timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(trade * 1000).replace(/[,\s]+/g, ' ')
    : '—';
  return `已收盤 · 最後成交 ${time}`;
}
