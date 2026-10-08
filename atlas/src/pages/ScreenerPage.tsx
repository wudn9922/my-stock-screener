import { useEffect, useMemo, useRef, useState, type UIEvent } from 'react';
import { ChevronRight, Search } from 'lucide-react';
import type { PageProps } from './pageProps';
import { ReportGate } from './ReportGate';
import type { GroupItem, Report, ReportGroup } from '../report/schema';
import { getPeFigures, type PeFigures } from '../app/dataSources';
import { mapWithConcurrency } from './worldPerformance';
import {
  displayTicker,
  formatPercent,
  formatPrice,
  formatRatio,
  maDistance,
  maValue,
  toneClass,
} from '../ui/format';
import { serializeRoute } from '../app/routes';
import { getMarketProfile } from '../market-data/MarketProfile';

const ROW_HEIGHT = 64;
const OVERSCAN = 8;
type SortMode = 'default' | 'gain' | 'loss' | 'symbol' | 'ma';
const SORT_LABELS: Record<SortMode, string> = {
  default: '報告順序',
  gain: '漲幅高→低',
  loss: '跌幅高→低',
  symbol: '代號',
  ma: '均線乖離（大→小）',
};
const KIND_LABELS: Record<ReportGroup['kind'], string> = { fixed: '精選', custom: '自訂', scan: '掃描' };

/** Filters (ticker / name / note) and sorts a group's rows. Pure; exported for tests. */
export function filterAndSortItems(items: readonly GroupItem[], query: string, sort: SortMode): GroupItem[] {
  const q = query.trim().toLowerCase();
  const filtered = q
    ? items.filter(
        (item) =>
          item.symbol.toLowerCase().includes(q) ||
          item.name.toLowerCase().includes(q) ||
          (item.note ?? '').toLowerCase().includes(q),
      )
    : [...items];
  const firstMa = (item: GroupItem) =>
    item.maList.length ? maDistance(item.close, maValue(item.maValues, item.maList[0]!)) : null;
  const byNumber = (value: (item: GroupItem) => number | null, direction: 1 | -1) => (a: GroupItem, b: GroupItem) => {
    const x = value(a),
      y = value(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return direction * (x - y);
  };
  if (sort === 'gain') filtered.sort(byNumber((item) => item.changePct, -1));
  else if (sort === 'loss') filtered.sort(byNumber((item) => item.changePct, 1));
  else if (sort === 'ma') filtered.sort(byNumber(firstMa, -1));
  else if (sort === 'symbol') filtered.sort((a, b) => a.symbol.localeCompare(b.symbol));
  return filtered;
}

function GroupChips({
  groups,
  active,
  onChange,
}: {
  groups: readonly ReportGroup[];
  active: string;
  onChange: (key: string) => void;
}) {
  const markets: ('TW' | 'US')[] = ['TW', 'US'];
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('[aria-pressed="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'center' });
  }, [active]);
  return (
    <div className="group-chips" ref={ref} role="group" aria-label="選股群組">
      {markets.map((market) => {
        const list = groups.filter((group) => group.market === market);
        if (!list.length) return null;
        return (
          <div key={market} className="chip-row">
            <span className="chip-row-label">{market === 'TW' ? '台股' : '美股'}</span>
            {list.map((group) => (
              <button
                key={group.key}
                type="button"
                className={`chip ${group.key === active ? 'active' : ''}`}
                aria-pressed={group.key === active}
                onClick={() => onChange(group.key)}
              >
                {group.name.replace(/^(?:🇹🇼|🇺🇸)?\s*(?:台股|美股)\s*[-－]?\s*/u, '')}
                <span className="chip-count">{group.items.length}</span>
                {group.kind !== 'fixed' && <span className="chip-kind">{KIND_LABELS[group.kind]}</span>}
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Row({
  item,
  pe,
  top,
  onOpen,
}: {
  item: GroupItem;
  pe: PeFigures | undefined;
  top: number;
  onOpen: () => void;
}) {
  const tone = toneClass(item.changePct);
  return (
    <a
      className="screener-row"
      style={{ transform: `translateY(${top}px)` }}
      href={serializeRoute({ page: 'chart', symbol: item.symbol, tf: '1D' })}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onOpen();
      }}
    >
      <span className="row-id">
        <b>{displayTicker(item.symbol)}</b>
        <small>{item.name !== item.symbol ? item.name : getMarketProfile(item.symbol).market === 'TW' ? '台股' : '美股'}</small>
      </span>
      <span className="row-mas">
        {item.maList.slice(0, 3).map((period) => {
          const distance = maDistance(item.close, maValue(item.maValues, period));
          return (
            <span key={period} className={`ma-pill ${toneClass(distance)}`} title={`收盤相對 MA${period} 的乖離`}>
              MA{period} {distance === null ? '—' : formatPercent(distance, 1)}
            </span>
          );
        })}
        {item.note && <span className="row-note">{item.note}</span>}
      </span>
      <span className="row-pe" title={pe?.reason ?? (pe ? `本益比 ${formatRatio(pe.pe)} · TTM ${formatRatio(pe.peTtm)}` : '')}>
        {pe && (pe.pe !== null || pe.peTtm !== null) ? (
          <>
            <small>本益比</small>
            {formatRatio(pe.peTtm ?? pe.pe)}
          </>
        ) : null}
      </span>
      <span className={`row-quote ${tone}`}>
        <b>{formatPrice(item.close)}</b>
        <span className="change-pill">{formatPercent(item.changePct)}</span>
      </span>
      <ChevronRight size={16} className="row-chevron" aria-hidden="true" />
    </a>
  );
}

function ScreenerView({
  report,
  group: requested,
  navigate,
  openChart,
}: { report: Report; group?: string } & Pick<PageProps, 'navigate' | 'openChart'>) {
  const groups = report.groups;
  const group = groups.find((g) => g.key === requested) ?? groups[0];
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortMode>('default');
  const [scroll, setScroll] = useState({ top: 0, height: 600 });
  const [pe, setPe] = useState<Record<string, PeFigures>>({});
  const listRef = useRef<HTMLDivElement>(null);
  const items = useMemo(() => (group ? filterAndSortItems(group.items, query, sort) : []), [group, query, sort]);
  useEffect(() => {
    listRef.current?.scrollTo?.({ top: 0 });
    setScroll((previous) => ({ ...previous, top: 0 }));
  }, [group?.key, query, sort]);
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => setScroll((previous) => ({ ...previous, height: list.clientHeight || 600 }));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [group?.key]);
  const start = Math.max(0, Math.floor(scroll.top / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(items.length, Math.ceil((scroll.top + scroll.height) / ROW_HEIGHT) + OVERSCAN);
  const slice = items.slice(start, end);
  // P/E for the rows on screen only, after scrolling settles.
  const visibleKey = slice.map((item) => item.symbol).join(',');
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      const wanted = slice.filter((item) => !pe[item.symbol] && !item.symbol.startsWith('^'));
      void mapWithConcurrency(wanted, 3, async (item) => {
        const figures = await getPeFigures(item.symbol, item.close);
        if (live) setPe((previous) => ({ ...previous, [item.symbol]: figures }));
      });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // `slice`/`pe` are read at the moment the visible set settles.
  }, [visibleKey]);
  if (!group)
    return (
      <div className="page-state empty" role="status">
        <b>報告沒有選股群組</b>
      </div>
    );
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const top = event.currentTarget.scrollTop;
    setScroll((previous) => (Math.abs(previous.top - top) < ROW_HEIGHT / 2 ? previous : { ...previous, top }));
  };
  return (
    <div className="screener-body">
      <GroupChips groups={groups} active={group.key} onChange={(key) => navigate({ page: 'screener', group: key })} />
      <div className="screener-tools">
        <label className="filter-field">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="在群組內篩選"
            placeholder={`在「${group.name}」中篩選`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="sort-field">
          <span className="sr-only">排序</span>
          <select aria-label="排序" value={sort} onChange={(event) => setSort(event.target.value as SortMode)}>
            {(Object.keys(SORT_LABELS) as SortMode[]).map((mode) => (
              <option key={mode} value={mode}>
                {SORT_LABELS[mode]}
              </option>
            ))}
          </select>
        </label>
        <span className="result-count">
          {items.length === group.items.length ? `${items.length} 檔` : `${items.length} / ${group.items.length} 檔`}
        </span>
      </div>
      <div className="screener-list card" ref={listRef} onScroll={onScroll} role="list" aria-label={`${group.name}股票清單`}>
        {items.length ? (
          <div className="screener-rows" style={{ height: items.length * ROW_HEIGHT }}>
            {slice.map((item, offset) => (
              <div role="listitem" key={item.symbol}>
                <Row
                  item={item}
                  pe={pe[item.symbol]}
                  top={(start + offset) * ROW_HEIGHT}
                  onOpen={() => openChart({ symbol: item.symbol, name: item.name, tf: '1D', maList: item.maList })}
                />
              </div>
            ))}
          </div>
        ) : (
          <p className="list-empty">{query ? `沒有符合「${query}」的股票` : '此群組今日沒有股票'}</p>
        )}
      </div>
    </div>
  );
}

export default function ScreenerPage({ report, reload, navigate, openChart, group }: PageProps & { group?: string }) {
  return (
    <div className="page screener-page">
      <div className="page-head">
        <div>
          <h2 className="page-title">選股</h2>
          {report.status === 'ok' && (
            <p className="page-sub">每日報告的群組與掃描結果 · 點選股票開啟圖表並套用群組均線</p>
          )}
        </div>
      </div>
      <ReportGate report={report} reload={reload}>
        {(data) => <ScreenerView report={data} group={group} navigate={navigate} openChart={openChart} />}
      </ReportGate>
    </div>
  );
}
