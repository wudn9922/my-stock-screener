import { App, type ChartWorkspaceProps } from './App';
import { LegacyImportCard } from '../ui/LegacyImportCard';
import { useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { getChartStore } from './chartStore';
import { ChartPeHistory } from '../ui/financials/ChartPeHistory';

/** Lazy route entry for the chart workspace (engine, drawings, panels and their styles). */
export default function ChartPage(props: ChartWorkspaceProps) {
  const store = getChartStore();
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const symbol = state.app.activeSymbol;
  const pe = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = viewport.current;
    const main = element?.closest<HTMLElement>('.site-main');
    if (!element || !main || props.active === false) return;
    const account = main.querySelector<HTMLElement>(':scope > .site-account-flow');
    const resize = () => {
      const style = getComputedStyle(main);
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      element.style.setProperty('--chart-viewport-height', `${Math.max(0, main.clientHeight - padding - (account?.getBoundingClientRect().height ?? 0))}px`);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(main);
    if (account) observer.observe(account);
    resize();
    return () => observer.disconnect();
  }, [props.active]);
  const showPe = props.active !== false && state.app.provider !== 'demo' && !symbol.startsWith('^');
  return (
    <><div ref={viewport} className="chart-page-layout">
      {props.active !== false && <LegacyImportCard />}
      <App {...props} onShowPeChart={showPe ? () => {
        pe.current?.focus({ preventScroll: true });
        pe.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
      } : undefined} />
    </div>
    {showPe && <div ref={pe} tabIndex={-1}><ChartPeHistory key={symbol} symbol={symbol} /></div>}</>
  );
}
