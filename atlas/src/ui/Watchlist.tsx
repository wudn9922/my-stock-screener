import { ChevronUp, ChevronDown, X, Plus, Search, Settings2 } from 'lucide-react';
import { useState } from 'react';
import type { Quote } from '../market-data/MarketDataProvider';
import { getMarketProfile } from '../market-data/MarketProfile';
import { resolveSymbolInput, type SymbolCatalogEntry } from '../market-data/SymbolCatalog';
import { IconButton } from './IconButton';
import { formatPercent, toneClass } from './format';
export const companies: Record<string, string> = {
  AAPL: 'Apple Inc.',
  MSFT: 'Microsoft',
  NVDA: 'NVIDIA',
  TSLA: 'Tesla',
  AMD: 'Advanced Micro Devices',
  META: 'Meta Platforms',
  GOOGL: 'Alphabet',
  AMZN: 'Amazon',
  NVO: 'Novo Nordisk',
};
export function Watchlist({
  symbols,
  active,
  onSelect,
  onChange,
  quotes = {},
  catalogEntries = [],
}: {
  quotes?: Record<string, Quote>;
  catalogEntries?: readonly SymbolCatalogEntry[];
  symbols: string[];
  active: string;
  onSelect: (symbol: string) => void;
  onChange: (symbols: string[]) => void;
}) {
  const [adding, setAdding] = useState(false),
    [input, setInput] = useState(''),
    [error, setError] = useState(''),
    [manage, setManage] = useState(false);
  const reorder = (index: number, delta: number) => {
    const next = [...symbols],
      j = index + delta;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j], next[index]];
    onChange(next);
  };
  return (
    <>
      <div className="panel-heading">
        <div>
          <h2>自選清單</h2>
        </div>
        <IconButton label="新增自選股" onClick={() => setAdding(!adding)}>
          <Plus size={18} />
        </IconButton>
      </div>
      {adding && (
        <form
          className="watchlist-add"
          onSubmit={(e) => {
            e.preventDefault();
            try {
              const s = resolveSymbolInput(input, catalogEntries);
              if (!symbols.includes(s)) onChange([...symbols, s]);
              setInput('');
              setError('');
              setAdding(false);
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          <Search size={16} />
          <input
            aria-label="自選股代號"
            placeholder="代號，例如 2330 或 NVDA"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            required
          />
          <button type="submit" className="primary-button">加入</button>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="watchlist-label">
        <span>代號／名稱</span>
        <IconButton label="管理自選清單" active={manage} onClick={() => setManage(!manage)}>
          <Settings2 size={16} />
        </IconButton>
      </div>
      <div className={`watchlist-rows ${manage ? 'managing' : ''}`}>
        {symbols.map((symbol, index) => {
          const quote = quotes[symbol];
          const profile = quote?.market ?? getMarketProfile(symbol);
          const entry = catalogEntries.find((item) => item.symbol === symbol);
          const exchange = entry?.market ?? profile.exchange;
          const company =
            entry?.name ??
            companies[symbol] ??
            (profile.market === 'TW'
              ? `台股 · ${exchange === 'TWSE' ? '上市' : '上櫃'}`
              : '美股');
          const companyLabel =
            entry && profile.market === 'TW'
              ? `${company} · ${exchange === 'TWSE' ? '上市' : '上櫃'}`
              : company;

          return (
            <div key={symbol} className={`watch-row ${symbol === active ? 'selected' : ''}`}>
              <button
                className="watch-symbol"
                aria-label={`選擇 ${symbol}`}
                onClick={() => onSelect(symbol)}
              >
                <span className={`ticker-icon ticker-${index % 4}`}>{symbol.slice(0, 1)}</span>
                <span>
                  <b>{symbol}</b>
                  <small>{companyLabel}</small>
                </span>
                {quote && (
                  <span
                    className="watch-quote"
                    title={`${quote.source ?? ''} · 最後一根 K 棒 ${new Date(quote.asOf * 1000).toLocaleString('zh-TW')}`}
                  >
                    {quote.price.toFixed(2)}
                    <small className={toneClass(quote.changePercent)}>
                      {formatPercent(quote.changePercent)} ·{' '}
                      {quote.dataState === 'simulated' ? '模擬' : '延遲'}
                      {quote.cacheStatus === 'stale' ? ' · 舊資料' : ''}
                    </small>
                  </span>
                )}
                {symbol === active && <span className="active-dot" />}
              </button>
              <div className="watch-actions">
                <IconButton
                  label={`上移 ${symbol}`}
                  disabled={index === 0}
                  onClick={() => reorder(index, -1)}
                >
                  <ChevronUp size={13} />
                </IconButton>
                <IconButton
                  label={`下移 ${symbol}`}
                  disabled={index === symbols.length - 1}
                  onClick={() => reorder(index, 1)}
                >
                  <ChevronDown size={13} />
                </IconButton>
                <IconButton
                  label={`從自選清單移除 ${symbol}`}
                  onClick={() => onChange(symbols.filter((s) => s !== symbol))}
                >
                  <X size={13} />
                </IconButton>
              </div>
            </div>
          );
        })}
      </div>
      <div className="watch-footer">
        <span className="status-dot" />
        <span>
          自選清單只儲存在這台裝置
          <br />
          <small>可在「工作區設定」匯出備份</small>
        </span>
      </div>
    </>
  );
}
