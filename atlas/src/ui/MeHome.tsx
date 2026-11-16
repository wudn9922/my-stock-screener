import { useSyncExternalStore, type MouseEvent, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { sitePreferences, type ColorConvention } from '../app/sitePreferences';
import { CONSOLE_VIEWS, serializeRoute, type ConsoleView, type Route } from '../app/routes';
import './me.css';

export const CONSOLE_VIEW_TITLES: Record<ConsoleView, string> = {
  parameters: '個股均線參數',
  groups: '自訂分組',
  app: '安裝到主畫面',
  status: '資料狀態',
  backup: '備份與還原',
  admin: '管理員儲存權限',
};
export const isConsoleView = (value: unknown): value is ConsoleView => (CONSOLE_VIEWS as readonly unknown[]).includes(value);

const plainClick = (event: MouseEvent) => !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0);

/** One 52px link row of a 我的 group: label, an optional value (numbers in the numeric font) and a chevron. */
export function MeRow({ label, value, route, navigate }: {
  label: string;
  value?: ReactNode;
  route: Route;
  navigate: (route: Route) => void;
}) {
  return <a className="me-row" href={serializeRoute(route)} onClick={event => {
    if (!plainClick(event)) return;
    event.preventDefault();
    navigate(route);
  }}>
    <span className="me-row-label">{label}</span>
    <span className="me-row-end">
      {value !== undefined && value !== '' && <span className="me-row-value">{value}</span>}
      <ChevronRight size={16} strokeWidth={2} aria-hidden="true" />
    </span>
  </a>;
}

export function MeGroup({ label, children }: { label: string; children: ReactNode }) {
  return <>
    <h2 className="me-group-label">{label}</h2>
    <div className="me-group">{children}</div>
  </>;
}

/** 漲跌顏色 as a 56px row with an inline two-way control (the same preference as the display dialog). */
export function MeColorRow() {
  const preferences = useSyncExternalStore(sitePreferences.subscribe, sitePreferences.get);
  const options: readonly [ColorConvention, string][] = [['tw', '紅漲'], ['us', '綠漲']];
  return <div className="me-row me-row-color">
    <span className="me-row-label" id="me-color-label">漲跌顏色</span>
    <div className="me-seg" role="group" aria-labelledby="me-color-label">
      {options.map(([value, label]) => (
        <button key={value} type="button" className={`me-opt-${value}${preferences.colors === value ? ' active' : ''}`}
          aria-pressed={preferences.colors === value} onClick={() => sitePreferences.setColors(value)}>
          <span className="me-tri" aria-hidden="true">▲</span>{label}
        </button>
      ))}
    </div>
  </div>;
}

/** Full-page sub-view chrome: a 52px header (back, title) over today's unchanged forms. */
export function MeSubview({ title, onBack, children }: { title: string; onBack: () => void; children: ReactNode }) {
  return <>
    <header className="me-subhead">
      <button type="button" className="me-back" aria-label="返回我的" onClick={onBack}><ChevronLeft size={24} strokeWidth={2} aria-hidden="true" /></button>
      <h2>{title}</h2>
    </header>
    <div className="me-subbody" data-console-view-body>{children}</div>
  </>;
}
