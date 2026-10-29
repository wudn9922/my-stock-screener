import { useState } from 'react';
import { ArrowLeft, Info, Search, X } from 'lucide-react';
import type { FinancialHistory, PeriodKind } from '../../fundamentals/FinancialHistory';
import { SymbolSearch, type LocalSymbol, type SearchResult } from '../SymbolSearch';
import { StockTabs, type StockView } from '../StockTabs';
import { displayTicker } from '../format';
import { adrRatioText, type TwAdrListing } from '../../fundamentals/adrListings';
import { LEVEL_TEXT, issueText, revisionText } from './financialsView';
import { FinancialNote } from './FinancialNote';

export type DisplayMode = 'chart' | 'table';

export interface FinancialsHeaderProps {
  symbol: string | undefined;
  name: string | null;
  history: FinancialHistory | null;
  kind: PeriodKind;
  showQuarterly: boolean;
  futuHref: string | null;
  mode: DisplayMode;
  localSymbols: readonly LocalSymbol[];
  /** Taiwan listing of the ADR being viewed: the header links to its financials. */
  homeListing?: TwAdrListing | null;
  /** Returns to the chart page, optionally in its 價量／研究 view. */
  onBack: (view?: StockView) => void;
  /** The chart page offers 價量／研究 views (site build); otherwise only 「K線｜財務」. */
  stockViews?: boolean;
  onKind: (kind: PeriodKind) => void;
  onMode: (mode: DisplayMode) => void;
  onSymbol: (result: SearchResult) => void;
  onHomeListing?: (listing: TwAdrListing) => void;
}

/**
 * Compact page head: identity, quality and search share the first row. Period and view switches
 * share the second row; secondary queries are folded. The whole head scrolls with the figures.
 */
export function FinancialsHeader({
  symbol, name, history, kind, showQuarterly, futuHref, mode, localSymbols, homeListing, onBack, stockViews = false, onKind, onMode, onSymbol, onHomeListing,
}: FinancialsHeaderProps) {
  const [searching, setSearching] = useState(!symbol);
  const [qualityOpen, setQualityOpen] = useState(false);
  const level = history?.quality.level ?? null;
  const issues = history?.quality.issues ?? [];
  const visibleIssues = issues;
  const visibleRevisions = history?.quality.revisions ?? [];
  return (
    <header className="fin-head">
      <div className="fin-head-row fin-head-main">
        {!symbol && (
          <button type="button" className="fin-back" onClick={() => onBack()} aria-label="回到圖表">
            <ArrowLeft size={18} aria-hidden="true" />
            <span>圖表</span>
          </button>
        )}
        <div className="fin-title">
          <b>{symbol ? displayTicker(symbol) : '財報分析'}</b>
          {history && <span className="fin-currency">{history.currency}</span>}
          {name && symbol && name.toUpperCase() !== symbol && name !== displayTicker(symbol) && <span className="fin-name">{name}</span>}
        </div>
        <div className="fin-head-tools">
          {(futuHref && symbol || homeListing && onHomeListing) && (
            <FinancialNote label="其他查詢" className="fin-other-queries">
              {futuHref && symbol && (
                <div className="fin-outbound-row">
                  <a className="fin-futu-link" href={futuHref} target="_blank" rel="noopener noreferrer">在富途網頁查看財報</a>
                  <a className="fin-futu-link" href={`ftnn://quote/2/${encodeURIComponent(symbol)}/`}>在富途 App 開啟</a>
                </div>
              )}
              {homeListing && onHomeListing && (
                <p className="fin-home-listing">
                  也可查看{' '}
                  <button type="button" className="fin-home-link" title={`${homeListing.twName}（台股）`} onClick={() => onHomeListing(homeListing)}>
                    {homeListing.tw}（台股）財報
                  </button>
                  <span className="fin-home-ratio">{adrRatioText(homeListing)}</span>
                </p>
              )}
            </FinancialNote>
          )}
          {history && level && (
            <button
              type="button"
              className={`fin-quality ${level}`}
              aria-expanded={qualityOpen}
              aria-controls="fin-quality-details"
              onClick={() => setQualityOpen((open) => !open)}
              title="資料品質說明"
            >
              <Info size={12} aria-hidden="true" />
              {LEVEL_TEXT[level]}
            </button>
          )}
          <button
            type="button"
            className={`icon-button fin-search-toggle${searching ? ' active' : ''}`}
            aria-label={searching ? '關閉搜尋' : '搜尋其他股票'}
            aria-expanded={searching}
            aria-controls="fin-symbol-search"
            onClick={() => setSearching((open) => !open)}
          >
            {searching ? <X size={16} /> : <Search size={16} />}
          </button>
        </div>
      </div>
      <div className="fin-head-row controls">
        {symbol && <div className="stock-page-controls fin-stock-controls">
          <StockTabs active="financials" tabs={stockViews ? undefined : ['chart', 'financials']}
            onSelect={tab => tab !== 'financials' && onBack(tab)} />
        </div>}
        {showQuarterly && (
          <div className="segmented compact" role="group" aria-label="期間">
            {(
              [
                ['annual', '年度'],
                ['quarterly', '季度'],
              ] as const
            ).map(([value, label]) => (
              <button key={value} type="button" className={kind === value ? 'active' : ''} aria-pressed={kind === value} onClick={() => onKind(value)}>
                {label}
              </button>
            ))}
          </div>
        )}
        <div className="segmented compact" role="group" aria-label="顯示方式">
          {(
            [
              ['chart', '圖表'],
              ['table', '表格'],
            ] as const
          ).map(([value, label]) => (
            <button key={value} type="button" className={mode === value ? 'active' : ''} aria-pressed={mode === value} onClick={() => onMode(value)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {searching && (
        <div className="fin-search" id="fin-symbol-search">
          <SymbolSearch
            mode="dropdown"
            autoFocus={!!symbol}
            localSymbols={localSymbols}
            onSelect={(result) => {
              setSearching(false);
              onSymbol(result);
            }}
          />
        </div>
      )}
      {qualityOpen && history && (
        <div className="fin-quality-panel" id="fin-quality-details" role="note">
          <p>
            <b>{level ? LEVEL_TEXT[level] : '品質未標示'}</b>
            {`：年度 ${history.annual.end.length} 期、季度 ${history.quarterly.end.length} 期。`}
          </p>
          {visibleIssues.length ? (
            <ul>
              {visibleIssues.map((code) => (
                <li key={code}>{issueText(code)}</li>
              ))}
            </ul>
          ) : (
            <p>沒有發現資料問題。</p>
          )}
          {visibleRevisions.length > 0 && (
            <>
              <p className="fin-revisions-title">修訂紀錄（最近 {Math.min(5, visibleRevisions.length)} 筆）</p>
              <ul>
                {visibleRevisions
                  .slice(-5)
                  .reverse()
                  .map((revision) => (
                    <li key={`${revision.kind}|${revision.period}|${revision.end}|${revision.field}|${revision.seen ?? ''}`}>{revisionText(history, revision)}</li>
                  ))}
              </ul>
            </>
          )}
        </div>
      )}
    </header>
  );
}
