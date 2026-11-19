import { useSyncExternalStore } from 'react';
import { SCREENER_HOSTING } from '../app/HostingMode';
import { StorageNotice } from '../auth/StorageNotice';
import { MARKET_LOGIN_MESSAGE, marketProxyAccess, type MarketAccessSnapshot } from '../market-data/marketProxyAccess';
import './marketLogin.css';

/** Signed in / signed out / over quota, for the market-chart function (re-renders on every change). */
export function useMarketAccess(): MarketAccessSnapshot {
  return useSyncExternalStore(marketProxyAccess.subscribe, marketProxyAccess.getSnapshot);
}

/**
 * Shown where a chart or quote would need the market-chart function while signed out (or after a 401):
 * 「登入 LINE 後可查看」 and the site's existing LINE login button.
 */
export function MarketLoginPrompt({ compact = false }: { compact?: boolean }) {
  return <div className={`market-login-prompt${compact ? ' compact' : ''}`} role="group" aria-label={MARKET_LOGIN_MESSAGE}>
    <b>{MARKET_LOGIN_MESSAGE}</b>
    {!compact && <p>公開資料庫未收錄此股票；即時行情與更多股票需要登入 LINE（免費）。</p>}
    {SCREENER_HOSTING && <StorageNotice variant="prompt" />}
  </div>;
}

/** Site-wide, non-blocking: the function's quota text while a 429 is in force. */
export function MarketQuotaNotice() {
  const access = useMarketAccess();
  if (access.status !== 'quota' || !access.message) return null;
  return <p className="market-quota-notice" role="status" data-market-quota={access.code}>
    {access.message}
  </p>;
}
