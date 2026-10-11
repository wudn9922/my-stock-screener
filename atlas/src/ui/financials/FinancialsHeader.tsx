import { useState } from 'react';
import { ArrowLeft, Info, Search, X } from 'lucide-react';
import type { FinancialHistory, PeriodKind } from '../../fundamentals/FinancialHistory';
import { SymbolSearch, type LocalSymbol, type SearchResult } from '../SymbolSearch';
import { displayTicker } from '../format';
import { LEVEL_TEXT, issueText, revisionText } from './financialsView';

export type DisplayMode = 'chart' | 'table';

export interface FinancialsHeaderProps {
  symbol: string | undefined;
  name: string | null;
  history: FinancialHistory | null;
  kind: PeriodKind;
  mode: DisplayMode;
  localSymbols: readonly LocalSymbol[];
  onBack: () => void;
  onKind: (kind: PeriodKind) => void;
  onMode: (mode: DisplayMode) => void;
  onSymbol: (result: SearchResult) => void;
}

/** Sticky page head: back to the chart, symbol / name / currency, quality badge, period and view switches, stock search. */
export function FinancialsHeader({ symbol, name, history, kind, mode, localSymbols, onBack, onKind, onMode, onSymbol }: FinancialsHeaderProps) {
  const [searching, setSearching] = useState(!symbol);
  const [qualityOpen, setQualityOpen] = useState(false);
  const level = history?.quality.level ?? null;
  const issues = history?.quality.issues ?? [];
  return (
    <header className="fin-head">
      <div className="fin-head-row">
        <button type="button" className="fin-back" onClick={onBack} aria-label="回到圖表">
          <ArrowLeft size={18} aria-hidden="true" />
          <span>圖表</span>
        </button>
        <div className="fin-title">
          <b>{symbol ? displayTicker(symbol) : '財報分析'}</b>
          {name && symbol && name.toUpperCase() !== symbol && name !== displayTicker(symbol) && <span className="fin-name">{name}</span>}
          {history && <span className="fin-currency">{history.currency}</span>}
        </div>
        {history && level && (
          <button
            type="button"
            className={`fin-quality ${level}`}
            aria-expanded={qualityOpen}
            onClick={() => setQualityOpen((open) => !open)}
            title="資料品質說明"
          >
            <Info size={13} aria-hidden="true" />
            {LEVEL_TEXT[level]}
          </button>
        )}
        <button
          type="button"
          className={`icon-button${searching ? ' active' : ''}`}
          aria-label={searching ? '關閉搜尋' : '搜尋其他股票'}
          aria-expanded={searching}
          onClick={() => setSearching((open) => !open)}
        >
          {searching ? <X size={18} /> : <Search size={18} />}
        </button>
      </div>
      {searching && (
        <div className="fin-search">
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
        <div className="fin-quality-panel" role="note">
          <p>
            <b>{level ? LEVEL_TEXT[level] : '品質未標示'}</b>
            ：年度 {history.annual.end.length} 期、季度 {history.quarterly.end.length} 期。
          </p>
          {issues.length ? (
            <ul>
              {issues.map((code) => (
                <li key={code}>{issueText(code)}</li>
              ))}
            </ul>
          ) : (
            <p>沒有發現資料問題。</p>
          )}
          {history.quality.revisions.length > 0 && (
            <>
              <p className="fin-revisions-title">修訂紀錄（最近 {Math.min(5, history.quality.revisions.length)} 筆）</p>
              <ul>
                {history.quality.revisions
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
      <div className="fin-head-row controls">
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
    </header>
  );
}
