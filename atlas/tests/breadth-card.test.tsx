import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BreadthBody } from '../src/ui/BreadthCard';
import { parseBreadth } from '../src/report/breadth';
import {
  parseBreadthSignals,
  SIGNAL_STATES,
} from '../src/report/breadthSignals';
import {
  breadthFixture,
  breadthSignalsFixture,
  signalIndex,
} from './fixtures/breadth-signals';

function breadth() {
  const parsed = parseBreadth(breadthFixture());
  if (parsed.status !== 'ok') throw new Error('Invalid breadth fixture');
  return parsed.data.markets.us!;
}
function signals(state = 'active' as (typeof SIGNAL_STATES)[number]) {
  const parsed = parseBreadthSignals(breadthSignalsFixture(state));
  if (parsed.status !== 'ok') throw new Error('Invalid signals fixture');
  return parsed.data.markets.us!;
}
describe('calibrated breadth presentation', () => {
  it.each(SIGNAL_STATES)(
    'renders %s and its plan or trigger without mixing states',
    (state) => {
      const html = renderToStaticMarkup(
        <BreadthBody data={breadth()} signals={signals(state)} />,
      );
      const expected = {
        none: '目前無訊號',
        watch: '等待回檔',
        new: '新訊號',
        active: '持有中',
        target: '已停利',
        stop: '已停損',
        timeout: '持有到期',
      };
      expect(html).toContain(expected[state]);
      expect(html).toContain('66%（隨便進場 48%）');
      expect(html).toContain('70 筆');
      if (state === 'watch')
        expect(html).toContain('指數收盤跌破 6,015 即觸發');
      if (state === 'new') {
        expect(html).toContain('下一個交易日開盤進場');
        expect(html).toContain('進場參考');
        expect(html).toContain('停損估計');
      }
      if (state === 'active') {
        expect(html).toContain('3／60 個交易日');
        expect(html).toContain('2027-01-21');
      }
      if (['target', 'stop', 'timeout'].includes(state))
        expect(html).toContain('出場 2026-10-30');
    },
  );
  it('renders calibrated zones, percentiles and four P10/P90 lines with signal markers', () => {
    const html = renderToStaticMarkup(
      <BreadthBody data={breadth()} signals={signals()} />,
    );
    expect(html).toContain('歷史第 6.5 百分位');
    expect(html).toContain('breadth-tag oversold');
    expect(html.match(/class="breadth-threshold /g)).toHaveLength(4);
    expect(html).toContain('MA20 P10：22%');
    expect(html).toContain('下限 P10 22.0% · 上限 P90 76.0%');
    expect(html).toContain('data-signal-date="2026-10-27"');
    expect(html).toContain('歷史訊號指數');
    expect(html).toContain('2004-01-02 至 2026-10-30');
    expect(html).toContain('命中率＝進場後先碰到停利');
    expect(html).toContain('含存活者偏差');
  });
  it('shows conservative overbought language and identifies weaker index evidence', () => {
    const value = signals();
    value.overbought!.active = true;
    value.lowSignal.indices.push({
      ...signalIndex('watch', '^RUT', '羅素 2000'),
      stats: { ...value.lowSignal.indices[0]!.stats, hitRate: 0.57 },
    });
    const html = renderToStaticMarkup(
      <BreadthBody data={breadth()} signals={value} />,
    );
    expect(html).toContain('超買不是賣出訊號');
    expect(html).toContain('超買後先跌 40% vs 平常 41%');
    expect(html).toContain('參考性較低');
  });
  it('retains breadth numbers/history without optional signals and removes old fixed hot/weak tags', () => {
    const html = renderToStaticMarkup(<BreadthBody data={breadth()} />);
    expect(html).toContain('18.0%');
    expect(html).toContain('42.0%');
    expect(html).toContain('<svg');
    expect(html).not.toContain('抓低點訊號');
    expect(html).not.toContain('偏弱');
    expect(html).not.toContain('偏熱');
    expect(html).not.toContain('超賣');
  });
  it('keeps TW numerical breadth and chart while explaining non-applicability in a disclosure', () => {
    const reason = '台股測試原因';
    const html = renderToStaticMarkup(
      <BreadthBody
        data={breadth()}
        twSignals={{ applicable: false, reason }}
      />,
    );
    expect(html).toContain('18.0%');
    expect(html).toContain('<svg');
    expect(html).toContain('<summary>超買超賣訊號不適用</summary>');
    expect(html).toContain(reason);
    expect(html).not.toContain('breadth-tag');
    expect(html).not.toContain('抓低點訊號');
  });
  it('keeps the no-signal section collapsed by default', () => {
    const html = renderToStaticMarkup(
      <BreadthBody data={breadth()} signals={signals('none')} />,
    );
    expect(html).toContain('<summary>抓低點訊號 · 目前無訊號</summary>');
    expect(html).not.toContain('<details open');
  });
});
