import { describe, expect, it } from 'vitest';
import { PAGES, parseRoute, serializeRoute } from '../src/app/routes';

const route = (search: string) => parseRoute(search).route;

describe('themes route', () => {
  it('registers the page', () => {
    expect(PAGES).toContain('themes');
  });

  it('parses the page, its theme key and the aliases', () => {
    expect(route('?page=themes')).toEqual({ page: 'themes' });
    expect(route('?page=themes&theme=ai-chips')).toEqual({ page: 'themes', theme: 'ai-chips' });
    expect(route('?page=theme&theme=gold-miners')).toEqual({ page: 'themes', theme: 'gold-miners' });
    expect(route('?page=themes&theme=AI-Chips')).toEqual({ page: 'themes', theme: 'ai-chips' });
    expect(route('?page=themes&theme=has space')).toEqual({ page: 'themes' });
    expect(route('?page=themes&theme=<script>')).toEqual({ page: 'themes' });
    expect(route(`?page=themes&theme=${'a'.repeat(41)}`)).toEqual({ page: 'themes' });
    expect(route(`?page=themes&theme=${'a'.repeat(40)}`)).toEqual({ page: 'themes', theme: 'a'.repeat(40) });
    expect(route('?page=themes&theme=')).toEqual({ page: 'themes' });
  });

  it('selects the Taiwan themes with market=tw and ignores everything else', () => {
    expect(route('?page=themes&market=tw')).toEqual({ page: 'themes', market: 'tw' });
    expect(route('?page=themes&market=TW')).toEqual({ page: 'themes', market: 'tw' });
    expect(route('?page=themes&market=tw&theme=foundry')).toEqual({ page: 'themes', market: 'tw', theme: 'foundry' });
    expect(route('?page=themes&market=us')).toEqual({ page: 'themes' });
    expect(route('?page=themes&market=jp')).toEqual({ page: 'themes' });
    expect(route('?page=themes&market=')).toEqual({ page: 'themes' });
    expect(route('?page=themes&market=tw%20x')).toEqual({ page: 'themes' });
    expect(route(`?liff.state=${encodeURIComponent('?page=themes&market=tw')}`)).toEqual({ page: 'themes', market: 'tw' });
  });

  it('serializes the Taiwan route canonically and round-trips it', () => {
    expect(serializeRoute({ page: 'themes', market: 'tw' })).toBe('?page=themes&market=tw');
    expect(serializeRoute({ page: 'themes', market: 'tw', theme: 'foundry' })).toBe('?page=themes&market=tw&theme=foundry');
    for (const r of [{ page: 'themes', market: 'tw' }, { page: 'themes', market: 'tw', theme: 'cooling' }] as const) {
      expect(parseRoute(serializeRoute(r))).toEqual({ route: r, rewrite: false });
    }
    // The default market is not spelled out, and an invalid one is rewritten away.
    expect(parseRoute('?page=themes&market=us').rewrite).toBe(true);
    expect(parseRoute('?page=themes&market=jp').rewrite).toBe(true);
    expect(parseRoute('?page=themes').rewrite).toBe(false);
  });

  it('opens from a LINE liff.state link (query or path)', () => {
    expect(route(`?liff.state=${encodeURIComponent('?page=themes&theme=nuclear')}`)).toEqual({ page: 'themes', theme: 'nuclear' });
    expect(route(`?liff.state=${encodeURIComponent('/my-stock-screener/atlas/themes')}`)).toEqual({ page: 'themes' });
  });

  it('serializes canonically and round-trips', () => {
    for (const r of [{ page: 'themes' }, { page: 'themes', theme: 'ai-chips' }] as const) {
      const search = serializeRoute(r);
      expect(parseRoute(search)).toEqual({ route: r, rewrite: false });
    }
    expect(serializeRoute({ page: 'themes', theme: 'ev-battery' })).toBe('?page=themes&theme=ev-battery');
    // Aliases and junk are rewritten to the canonical URL.
    expect(parseRoute('?page=theme&theme=ev-battery').rewrite).toBe(true);
    expect(parseRoute('?page=themes&theme=!!').rewrite).toBe(true);
  });
});
