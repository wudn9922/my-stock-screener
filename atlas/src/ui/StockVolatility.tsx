import { useEffect, useState } from 'react';
import { closedBars, type BarResult, type MarketDataProvider } from '../market-data/MarketDataProvider';
import { getMarketProfile } from '../market-data/MarketProfile';
import { stockVolatility } from '../market-data/StockVolatility';
import { StockStatsMenu } from './StockStatsMenu';

export function StockVolatility({ symbol, provider, reloadToken }: { symbol: string; provider: MarketDataProvider; reloadToken: number }) {
  const [loaded, setLoaded] = useState<{
    symbol: string;
    provider: MarketDataProvider;
    token: number;
    result: BarResult | null;
  }>();
  useEffect(() => {
    const abort = new AbortController();
    void provider.getBars(symbol, '1D', undefined, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setLoaded({ symbol, provider, token: reloadToken, result });
      },
      () => {
        if (!abort.signal.aborted) setLoaded({ symbol, provider, token: reloadToken, result: null });
      },
    );
    return () => abort.abort();
  }, [symbol, provider, reloadToken]);
  const current = loaded?.symbol === symbol && loaded.provider === provider && loaded.token === reloadToken ? loaded : undefined;
  const result = current?.result;
  const market = result?.market ?? getMarketProfile(symbol);
  const stats = result ? stockVolatility(closedBars({ ...result, market }, '1D', Date.now() / 1000)) : null;
  const date = stats ? new Intl.DateTimeFormat('sv-SE', { timeZone: market.timezone }).format(stats.time * 1000) : null;
  const status = !current
    ? '日線載入中'
    : !result
      ? '日線暫時無法取得'
      : !stats
        ? '日線不足或資料無效（至少需 21 根完整日線）'
        : `日線至 ${date}${result.cacheStatus === 'stale' ? ' · 離線舊快取' : ''}`;
  const atrFormula =
    'ATR(20)%＝Wilder ATR(20) ÷ 同日收盤價 × 100；TR＝max(最高−最低、|最高−前收|、|最低−前收|)。前 20 個 TR 取平均後，每日以 (前 ATR×19＋當日 TR)÷20 更新。';
  const rangeFormula = '20 日平均日內振幅＝最近 20 根完整日線的 (最高−最低) ÷ 前一交易日收盤價 × 100，再取平均；不含隔夜跳空。';
  return (
    <div className="ws-volatility" data-testid="stock-volatility" aria-label={`${symbol} 日線波動`} aria-busy={!current}>
      {[
        { id: 'atr', label: 'ATR（20）%', caption: 'ATR20%', value: stats?.atrPercent, formula: atrFormula },
        { id: 'range', label: '20 日平均日內振幅', caption: '20日平均振幅', value: stats?.intradayRangePercent, formula: rangeFormula },
      ].map(({ id, label, caption, value, formula }) => <StockStatsMenu
        key={id}
        label={`${label} 計算說明`}
        triggerLabel={`${label} ${value === undefined ? '—' : `${value.toFixed(2)}%`}，計算說明`}
        testId={`volatility-${id}-toggle`}
        trigger={<span className="ws-volatility-tile"><span>{caption}</span><strong data-volatility-value>{value === undefined ? '—' : `${value.toFixed(2)}%`}</strong></span>}
      >
        <p className="muted small">{status}</p>
        <p>
          {formula}
          <br />
          固定使用已收盤日線，與圖表週期無關；日線缺日不補值。
          {stats ? ` ATR 原值 ${stats.atr.toFixed(2)}，基準收盤 ${stats.close.toFixed(2)}。` : ''}
          {result
            ? ` 來源：${result.source}；${result.priceBasis === 'split-adjusted' ? '拆股調整價格' : result.priceBasis === 'unadjusted' ? '未還原價格，除權息／分割可能放大波動' : '價格還原基準未確認'}。`
            : ''}
        </p>
      </StockStatsMenu>)}
    </div>
  );
}
