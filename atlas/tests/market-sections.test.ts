import { describe, expect, it } from 'vitest';
import { INITIAL_MARKET_SECTIONS, marketSectionOf, rememberMarketSection } from '../src/ui/MarketSectionNav';

describe('市場 sections', () => {
  it('maps every market-area route to one section and other pages to none', () => {
    expect(marketSectionOf({ page: 'markets', market: 'tw' })).toBe('markets');
    expect(marketSectionOf({ page: 'markets', market: 'us' })).toBe('markets');
    for (const market of ['jp', 'kr', 'eu'] as const) expect(marketSectionOf({ page: 'markets', market })).toBe('markets');
    expect(marketSectionOf({ page: 'world', view: 'fx' })).toBe('world');
    expect(marketSectionOf({ page: 'themes', market: 'tw', v: 'map' })).toBe('themes');
    expect(marketSectionOf({ page: 'screener' })).toBeNull();
    expect(marketSectionOf({ page: 'watchlist' })).toBeNull();
  });

  it('remembers each section as it was left without reopening a theme detail', () => {
    let memory = rememberMarketSection(INITIAL_MARKET_SECTIONS, { page: 'markets', market: 'eu' });
    memory = rememberMarketSection(memory, { page: 'world', view: 'commodities' });
    memory = rememberMarketSection(memory, { page: 'themes', market: 'tw', v: 'map', theme: 'ai-chips' });
    memory = rememberMarketSection(memory, { page: 'screener', group: 'tw_g1' });
    expect(memory).toEqual({
      markets: { page: 'markets', market: 'eu' },
      world: { page: 'world', view: 'commodities' },
      themes: { page: 'themes', market: 'tw', v: 'map' },
    });
    expect(INITIAL_MARKET_SECTIONS.markets).toEqual({ page: 'markets', market: 'tw' });
  });
});
