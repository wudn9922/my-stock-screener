import { expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { WatchlistBoard } from '../src/ui/WatchlistBoard';
import { liveQuotes } from '../src/market-data/LiveQuotes';
import { createWatchlist } from '../src/watchlists/model';

it('A1 renders the live receive time inside the existing toolbar, with its stable test id', () => {
  const feed = liveQuotes(), before = feed.lastUpdatedAt;
  feed.lastUpdatedAt = Date.parse('2026-11-04T04:20:00Z');
  try {
    const list = createWatchlist({ name: 'A1', market: 'US', kind: 'manual', group: 'universe-us', filters: '', symbols: ['AAPL'] });
    const html = renderToStaticMarkup(<WatchlistBoard list={list} lists={[list]} parameters={[]} onSelectList={() => undefined} openChart={() => undefined} navigate={() => undefined} />);
    const toolbar = html.slice(html.indexOf('class="wb-list-toolbar"'), html.indexOf('<ul class="wb-rows"'));
    expect(toolbar).toContain('data-testid="board-live-time"');
    expect(toolbar).toContain('dateTime="2026-11-04T04:20:00.000Z"');
    expect(html.match(/data-testid="board-live-time"/g)).toHaveLength(1);
  } finally { feed.lastUpdatedAt = before; }
});
