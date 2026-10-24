import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { BacktestPanel } from '../src/ui/BacktestPanel';

describe('backtest percentage-band controls', () => {
  it('offers one reference MA and independent percentage inputs without the retired MA-cross strategy', () => {
    const html = renderToStaticMarkup(<BacktestPanel symbol="AAPL" timeframe="1D" result={null} onResult={() => undefined} />);
    expect(html).toContain('均線百分比進出場');
    expect(html).toContain('aria-label="參考均線類型"');
    expect(html).toContain('aria-label="參考均線週期"');
    expect(html).toContain('aria-label="均線上方百分比"');
    expect(html).toContain('aria-label="均線下方百分比"');
    expect(html).not.toContain('均線交叉');
    expect(html).not.toContain('快線');
    expect(html).not.toContain('慢線');
  });
});
