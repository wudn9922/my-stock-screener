import './stockTabs.css';

export type StockTab = 'chart' | 'financials' | 'volume' | 'research';
/** The views of the chart page itself (financials is its own page). */
export type StockView = Exclude<StockTab, 'financials'>;

const LABELS: Record<StockTab, string> = { chart: 'K線', financials: '財務', volume: '價量', research: '研究' };

/**
 * 「K線｜財務｜價量｜研究」 switch for one stock. Shared by the chart header and the financials header. The view
 * the user is on is marked with aria-current; another tab asks the host to switch (keeping the symbol).
 * Hosts without the chart views pass only `['chart', 'financials']`.
 */
export function StockTabs({ active, onSelect, className, tabs = ['chart', 'financials', 'volume', 'research'] }: {
  active: StockTab;
  onSelect: (tab: StockTab) => void;
  className?: string;
  tabs?: readonly StockTab[];
}) {
  return (
    <nav className={`stock-tabs${className ? ` ${className}` : ''}`} aria-label="個股頁面">
      {tabs.map((tab) => {
        const current = tab === active;
        return (
          <button
            key={tab}
            type="button"
            className={current ? 'active' : ''}
            aria-current={current ? 'page' : undefined}
            data-testid={`stock-tab-${tab}`}
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
