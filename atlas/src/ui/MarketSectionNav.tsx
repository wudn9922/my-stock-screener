import type { PageProps } from '../pages/pageProps';
import { serializeRoute, type Route } from '../app/routes';

export type MarketSection = 'markets' | 'world' | 'themes';
export type MarketSectionRoutes = { [K in MarketSection]: Extract<Route, { page: K }> };

/** Section links wrap the existing pages without changing any page's own selectors. */
export function MarketSectionNav({ active, routes, navigate }: {
  active: MarketSection; routes: MarketSectionRoutes; navigate: PageProps['navigate'];
}) {
  return <nav className="market-section-nav segmented" aria-label="市場分頁">
    {([['markets', '大盤'], ['world', '世界'], ['themes', '題材']] as const).map(([page, label]) => (
      <a key={page} href={serializeRoute(routes[page])} aria-current={active === page ? 'page' : undefined}
        className={active === page ? 'active' : ''} onClick={event => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          navigate(routes[page]);
        }}>{label}</a>
    ))}
  </nav>;
}
