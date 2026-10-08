import { ChevronUp, ChevronDown, X, Plus, Search, Settings2 } from 'lucide-react';
import { useState } from 'react';
import type { Quote } from '../market-data/MarketDataProvider';
import { getMarketProfile } from '../market-data/MarketProfile';
import { resolveSymbolInput, type SymbolCatalogEntry } from '../market-data/SymbolCatalog';
import { IconButton } from './IconButton';
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
          <span className="eyebrow">YOUR MARKET</span>
          <h2>Watchlist</h2>
        </div>
        <IconButton label="Add watchlist symbol" onClick={() => setAdding(!adding)}>
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
            aria-label="Watchlist ticker"
            placeholder="Ticker"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            required
          />
          <button type="submit">Add</button>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="watchlist-label">
        <span>SYMBOL / COMPANY</span>
        <IconButton label="Manage watchlist" active={manage} onClick={() => setManage(!manage)}>
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
              ? `Taiwan listed equity · ${exchange} · ${profile.currency}`
              : 'US listed equity');
          const companyLabel =
            entry && profile.market === 'TW'
              ? `${company} · ${exchange} · ${profile.currency}`
              : company;

          return (
            <div key={symbol} className={`watch-row ${symbol === active ? 'selected' : ''}`}>
              <button
                className="watch-symbol"
                aria-label={`Select ${symbol}`}
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
                    title={`${quote.source ?? ''} · last bar ${new Date(quote.asOf * 1000).toLocaleString()}`}
                  >
                    {profile.currency} {quote.price.toFixed(2)}
                    <small>
                      {quote.changePercent.toFixed(2)}% ·{' '}
                      {quote.dataState === 'simulated' ? 'SIM' : 'delayed'}
                      {quote.cacheStatus === 'stale' ? ' · STALE' : ''}
                    </small>
                  </span>
                )}
                {symbol === active && <span className="active-dot" />}
              </button>
              <div className="watch-actions">
                <IconButton
                  label={`Move ${symbol} up`}
                  disabled={index === 0}
                  onClick={() => reorder(index, -1)}
                >
                  <ChevronUp size={13} />
                </IconButton>
                <IconButton
                  label={`Move ${symbol} down`}
                  disabled={index === symbols.length - 1}
                  onClick={() => reorder(index, 1)}
                >
                  <ChevronDown size={13} />
                </IconButton>
                <IconButton
                  label={`Remove ${symbol} from watchlist`}
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
          Local workspace
          <br />
          <small>設定儲存於此裝置</small>
        </span>
      </div>
    </>
  );
}
