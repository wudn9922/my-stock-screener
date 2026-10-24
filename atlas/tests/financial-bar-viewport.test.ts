import { describe, expect, it } from 'vitest';
import { initialBarViewport, panBarViewport, zoomBarViewport } from '../src/ui/financials/barViewport';

describe('financial chart time viewport', () => {
  it('starts at the newest requested periods and keeps older source periods available', () => {
    const initial = initialBarViewport(60, 20);
    expect(initial).toEqual({ start: 40, end: 60 });
    const zoomed = zoomBarViewport(initial, 60, 'out');
    expect(zoomed).toEqual({ start: 30, end: 60 });
    expect(panBarViewport(zoomed, 60, -1)).toEqual({ start: 15, end: 45 });
  });

  it('zooms down to one period without moving beyond either end of the real series', () => {
    let view = initialBarViewport(4, 20);
    for (let i = 0; i < 10; i++) view = zoomBarViewport(view, 4, 'in');
    expect(view).toEqual({ start: 3, end: 4 });
    expect(panBarViewport(view, 4, 1)).toEqual(view);
    for (let i = 0; i < 10; i++) view = panBarViewport(view, 4, -1);
    expect(view).toEqual({ start: 0, end: 1 });
    for (let i = 0; i < 10; i++) view = zoomBarViewport(view, 4, 'out');
    expect(view).toEqual({ start: 0, end: 4 });
  });

  it('leaves gaps in their original positions when the visible range is moved', () => {
    const values = [10, null, 30, null, 50, 60];
    const initial = initialBarViewport(values.length, 4);
    const earlier = panBarViewport(initial, values.length, -1);
    expect(values.slice(earlier.start, earlier.end)).toEqual([10, null, 30, null]);
    expect(values).toEqual([10, null, 30, null, 50, 60]);
  });
});
