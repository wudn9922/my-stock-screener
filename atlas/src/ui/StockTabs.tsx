import './stockTabs.css';

export type StockTab = 'chart' | 'financials';

const LABELS: Record<StockTab, string> = { chart: 'K線', financials: '財務' };

/**
 * 「K線｜財務」 switch for one stock. Shared by the chart header and the financials header. The page the
 * user is on is marked with aria-current; the other tab asks the host to navigate (keeping the symbol).
 */
export function StockTabs({ active, onSelect, className }: { active: StockTab; onSelect: (tab: StockTab) => void; className?: string }) {
  return (
    <nav className={`stock-tabs${className ? ` ${className}` : ''}`} aria-label="個股頁面">
      {(['chart', 'financials'] as const).map((tab) => {
        const current = tab === active;
        return (
          <button
            key={tab}
            type="button"
            className={current ? 'active' : ''}
            aria-current={current ? 'page' : undefined}
            onClick={() => {
              if (!current) onSelect(tab);
            }}
          >
            {LABELS[tab]}
          </button>
        );
      })}
    </nav>
  );
}
