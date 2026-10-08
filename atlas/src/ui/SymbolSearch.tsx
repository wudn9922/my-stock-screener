import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { ArrowUpRight, Clock, Search, X } from 'lucide-react';
import { searchStocks, preloadSymbolDirectory } from '../app/dataSources';
import { sitePreferences, type RecentSearch } from '../app/sitePreferences';
import { isCanonicalSymbol } from '../market-data/MarketProfile';
import { displayTicker } from './format';

export interface SearchResult {
  symbol: string;
  name: string;
  /** Short badge, e.g. 上市 / 上櫃 / NASDAQ / 指數. */
  badge: string;
  kind: 'stock' | 'index' | 'recent' | 'direct';
}
export interface LocalSymbol {
  symbol: string;
  name: string;
}

function exchangeBadge(exchange: string, market: 'TW' | 'US') {
  const value = exchange.toUpperCase();
  if (market === 'TW') return value === 'TPEX' || value === 'OTC' ? '上櫃' : value === 'TWSE' ? '上市' : '台股';
  return value || '美股';
}

/** Ranks local (report) symbols: exact ticker, ticker prefix, name prefix, name contains. */
export function matchLocalSymbols(query: string, symbols: readonly LocalSymbol[], limit = 6): LocalSymbol[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (item: LocalSymbol) => {
    const ticker = displayTicker(item.symbol).replace(/^\^/, '').toLowerCase();
    const name = item.name.toLowerCase();
    if (ticker === q || item.symbol.toLowerCase() === q) return 0;
    if (ticker.startsWith(q.replace(/^\^/, ''))) return 1;
    if (name.startsWith(q)) return 2;
    if (name.includes(q)) return 3;
    return -1;
  };
  return symbols
    .map((item) => ({ item, score: rank(item) }))
    .filter((entry) => entry.score >= 0)
    .sort((a, b) => a.score - b.score)
    .slice(0, limit)
    .map((entry) => entry.item);
}

/**
 * Global stock search (Chinese name, ticker or bare Taiwan digits) with keyboard navigation and
 * recent searches. Renders as an inline dropdown (desktop top bar) or a full sheet (mobile).
 */
export function SymbolSearch({
  mode,
  localSymbols,
  onSelect,
  onClose,
  autoFocus = false,
}: {
  mode: 'dropdown' | 'sheet';
  localSymbols: readonly LocalSymbol[];
  onSelect: (result: SearchResult) => void;
  onClose?: () => void;
  autoFocus?: boolean;
}) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(mode === 'sheet');
  const [active, setActive] = useState(0);
  const [remote, setRemote] = useState<{ query: string; results: SearchResult[]; failed: boolean }>({
    query: '',
    results: [],
    failed: false,
  });
  const [recent, setRecent] = useState<RecentSearch[]>(() => sitePreferences.get().recent);
  useEffect(() => sitePreferences.subscribe(() => setRecent(sitePreferences.get().recent)), []);
  useEffect(() => {
    if (autoFocus) inputRef.current?.focus({ preventScroll: true });
  }, [autoFocus]);
  const trimmed = query.trim();
  useEffect(() => {
    if (!trimmed) return;
    let active = true;
    const timer = setTimeout(() => {
      void searchStocks(trimmed, 20)
        .then((entries) => {
          if (active)
            setRemote({
              query: trimmed,
              failed: false,
              results: entries.map((entry) => ({
                symbol: entry.symbol,
                name: entry.name,
                badge: exchangeBadge(entry.exchange, entry.market),
                kind: 'stock' as const,
              })),
            });
        })
        .catch(() => {
          if (active) setRemote({ query: trimmed, results: [], failed: true });
        });
    }, 90);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [trimmed]);
  const results = useMemo<SearchResult[]>(() => {
    if (!trimmed) return recent.map((item) => ({ ...item, badge: '最近', kind: 'recent' as const }));
    const local = matchLocalSymbols(trimmed, localSymbols).map((item) => ({
      symbol: item.symbol,
      name: item.name,
      badge: item.symbol.startsWith('^') ? '指數' : item.symbol.match(/\.TWO?$/) ? '台股' : '美股',
      kind: item.symbol.startsWith('^') ? ('index' as const) : ('stock' as const),
    }));
    const seen = new Set<string>();
    const merged: SearchResult[] = [];
    const remoteResults = remote.query === trimmed ? remote.results : [];
    // Indices from the report first, then the full stock directory.
    for (const item of [...local.filter((r) => r.kind === 'index'), ...remoteResults, ...local])
      if (!seen.has(item.symbol)) {
        seen.add(item.symbol);
        merged.push(item);
      }
    const upper = trimmed.toUpperCase();
    if (!seen.has(upper) && isCanonicalSymbol(upper) && !/^\d+$/.test(upper))
      merged.push({ symbol: upper, name: '直接開啟代號', badge: '代號', kind: 'direct' });
    return merged.slice(0, 20);
  }, [trimmed, recent, localSymbols, remote]);
  const pending = !!trimmed && remote.query !== trimmed;
  const activeIndex = Math.min(active, Math.max(0, results.length - 1));
  useEffect(() => {
    rootRef.current
      ?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);
  useEffect(() => {
    if (mode !== 'dropdown' || !open) return;
    const outside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [mode, open]);
  const choose = (result: SearchResult | undefined) => {
    if (!result) return;
    if (result.kind !== 'direct') sitePreferences.addRecent({ symbol: result.symbol, name: result.name });
    setQuery('');
    setActive(0);
    if (mode === 'dropdown') {
      setOpen(false);
      inputRef.current?.blur();
    }
    onSelect(result);
  };
  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setActive((index) => (results.length ? (index + 1) % results.length : 0));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((index) => (results.length ? (index - 1 + results.length) % results.length : 0));
    } else if (event.key === 'Enter') {
      // Enter that confirms a Chinese IME composition must not pick a result.
      if (event.nativeEvent.isComposing) return;
      event.preventDefault();
      choose(results[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      if (query) setQuery('');
      else {
        setOpen(false);
        inputRef.current?.blur();
        onClose?.();
      }
    }
  };
  const showList = mode === 'sheet' || open;
  return (
    <div ref={rootRef} className={`symbol-search-box ${mode}`}>
      <div className="search-field">
        <Search size={17} aria-hidden="true" />
        <input
          ref={inputRef}
          type="search"
          role="combobox"
          aria-label="搜尋股票"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && results.length ? `${listId}-${activeIndex}` : undefined}
          placeholder="搜尋股票：台積電、2330、NVDA"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="characters"
          spellCheck={false}
          enterKeyHint="search"
          value={query}
          onFocus={() => {
            setOpen(true);
            preloadSymbolDirectory();
          }}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onKeyDown={keyDown}
        />
        {query && (
          <button type="button" className="search-clear" aria-label="清除搜尋" onClick={() => setQuery('')}>
            <X size={16} />
          </button>
        )}
        {mode === 'dropdown' && !query && <kbd aria-hidden="true">/</kbd>}
      </div>
      {showList && (
        <div className="search-results" role="presentation">
          {!trimmed && (
            <div className="search-section-title">
              <span>{recent.length ? '最近搜尋' : '輸入公司名稱或代號'}</span>
              {!!recent.length && (
                <button type="button" onClick={() => sitePreferences.clearRecent()}>
                  清除
                </button>
              )}
            </div>
          )}
          <ul id={listId} role="listbox" aria-label="搜尋結果">
            {results.map((result, index) => (
              <li
                key={`${result.kind}:${result.symbol}`}
                id={`${listId}-${index}`}
                data-index={index}
                role="option"
                aria-selected={index === activeIndex}
                className={index === activeIndex ? 'active' : ''}
                onPointerDown={(event) => event.preventDefault()}
                onPointerEnter={() => setActive(index)}
                onClick={() => choose(result)}
              >
                <span className="result-icon" aria-hidden="true">
                  {result.kind === 'recent' ? <Clock size={15} /> : result.kind === 'direct' ? <ArrowUpRight size={15} /> : displayTicker(result.symbol).replace('^', '').slice(0, 2)}
                </span>
                <span className="result-text">
                  <b>{displayTicker(result.symbol)}</b>
                  <small>{result.name}</small>
                </span>
                <span className="result-badge">{result.badge}</span>
              </li>
            ))}
          </ul>
          {trimmed && !results.length && !pending && (
            <p className="search-empty">
              {remote.failed ? '股票清單暫時無法載入，請輸入完整代號（例如 2330.TW、NVDA）。' : `找不到「${trimmed}」相關的股票`}
            </p>
          )}
          {pending && !results.length && <p className="search-empty">搜尋中…</p>}
        </div>
      )}
    </div>
  );
}
