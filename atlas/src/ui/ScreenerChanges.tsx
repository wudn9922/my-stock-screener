import { useEffect, useMemo, useState } from "react";
import {
  fetchHistoryIndex,
  fetchScreenerSnapshot,
  type ScreenerSnapshot,
} from "../report/screenerHistory";
import type { UniverseMarketKey } from "../report/universe";
import type { FilterState } from "../pages/screenerFilters";
import {
  compareScreenerResults,
  snapshotItems,
  snapshotReferenceBars,
  CHANGE_LABELS,
  type ChangeKind,
} from "../pages/screenerChanges";
import { useScreenerFixedMAs } from "../pages/useScreenerFixedMAs";
import type { StatusView } from "../pages/screenerItems";
import type { PageProps } from "../pages/pageProps";
import { HelpDisclosure } from "./HelpDisclosure";
import { getDailyBars } from "../app/dataSources";
import type { GroupItem } from "../report/schema";
import "./analysisComparisons.css";

function snapshotLoader(items: readonly GroupItem[]) {
  const bySymbol = new Map(items.map((item) => [item.symbol, item]));
  return async (symbol: string) =>
    snapshotReferenceBars(bySymbol.get(symbol), await getDailyBars(symbol));
}

function Comparison({
  market,
  group,
  filters,
  query,
  view,
  openChart,
}: {
  market: UniverseMarketKey;
  group: string;
  filters: FilterState;
  query: string;
  view: StatusView;
} & Pick<PageProps, "openChart">) {
  const [dates, setDates] = useState<string[]>([]);
  const [from, setFrom] = useState(""),
    [to, setTo] = useState("");
  const [snapshots, setSnapshots] =
    useState<[ScreenerSnapshot, ScreenerSnapshot]>();
  const [error, setError] = useState(""),
    [attempt, setAttempt] = useState(0);
  const [kind, setKind] = useState<ChangeKind>("entered");
  const [limit, setLimit] = useState(50);
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    setDates([]);
    void fetchHistoryIndex(abort.signal)
      .then((index) => {
        if (!abort.signal.aborted) {
          const list = index[market];
          setDates(list);
          setFrom(list.at(-2) ?? "");
          setTo(list.at(-1) ?? "");
        }
      })
      .catch((caught) => {
        if (!abort.signal.aborted)
          setError(
            caught instanceof Error ? caught.message : "無法讀取歷史日期。",
          );
      });
    return () => abort.abort();
  }, [market, attempt]);
  useEffect(() => {
    setSnapshots(undefined);
    if (!from || !to || from >= to) return;
    const abort = new AbortController();
    setError("");
    void Promise.all([
      fetchScreenerSnapshot(market, from, abort.signal),
      fetchScreenerSnapshot(market, to, abort.signal),
    ])
      .then((values) => {
        if (!abort.signal.aborted) setSnapshots(values);
      })
      .catch((caught) => {
        if (!abort.signal.aborted)
          setError(caught instanceof Error ? caught.message : "快照讀取失敗。");
      });
    return () => abort.abort();
  }, [market, from, to, attempt]);
  useEffect(() => setLimit(50), [from, to, group, filters, query, view, kind]);
  const previousItems = useMemo(
    () => snapshotItems(snapshots?.[0], group, view, query),
    [snapshots, group, view, query],
  );
  const nextItems = useMemo(
    () => snapshotItems(snapshots?.[1], group, view, query),
    [snapshots, group, view, query],
  );
  const previousLoader = useMemo(
    () => snapshotLoader(previousItems),
    [previousItems],
  );
  const nextLoader = useMemo(() => snapshotLoader(nextItems), [nextItems]);
  const previousMAs = useScreenerFixedMAs(
    !!filters.ema20,
    previousItems,
    previousLoader,
    true,
  );
  const nextMAs = useScreenerFixedMAs(
    !!filters.ema20,
    nextItems,
    nextLoader,
    true,
  );
  const result = useMemo(
    () =>
      snapshots
        ? compareScreenerResults(
            {
              items: previousItems,
              context: {
                market,
                asOf: from,
                growth: snapshots[0].growth,
                pe: snapshots[0].pe,
                ...previousMAs,
              },
            },
            {
              items: nextItems,
              context: {
                market,
                asOf: to,
                growth: snapshots[1].growth,
                pe: snapshots[1].pe,
                ...nextMAs,
              },
            },
            filters,
          )
        : null,
    [
      snapshots,
      previousItems,
      nextItems,
      market,
      from,
      to,
      filters,
      previousMAs.fixedMAs,
      previousMAs.maReferenceReason,
      nextMAs.fixedMAs,
      nextMAs.maReferenceReason,
    ],
  );
  return (
    <div className="analysis-comparison">
      {error ? (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            className="ghost-button"
            onClick={() => setAttempt((n) => n + 1)}
          >
            重試歷史資料
          </button>
        </p>
      ) : dates.length < 2 ? (
        <p role="status">
          {dates.length
            ? "目前只有一個交易日快照，累積下一個交易日後即可比較。"
            : "載入可比較日期…"}
        </p>
      ) : (
        <>
          <div className="analysis-controls">
            <label>
              基準日
              <select
                aria-label="名單基準日"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
              >
                {dates.map((date) => (
                  <option key={date}>{date}</option>
                ))}
              </select>
            </label>
            <label>
              比較日
              <select
                aria-label="名單比較日"
                value={to}
                onChange={(event) => setTo(event.target.value)}
              >
                {dates.map((date) => (
                  <option key={date}>{date}</option>
                ))}
              </select>
            </label>
          </div>
          {from >= to ? (
            <p role="alert">比較日必須晚於基準日。</p>
          ) : !result ? (
            <p role="status">載入兩日快照…</p>
          ) : result.error ? (
            <p role="status">無法完整套用相同條件：{result.error}</p>
          ) : (
            <>
              <div
                className="analysis-controls"
                role="group"
                aria-label="名單變化類別"
              >
                {(Object.keys(CHANGE_LABELS) as ChangeKind[]).map((key) => (
                  <button
                    type="button"
                    className={`chip ${kind === key ? "active" : ""}`}
                    aria-pressed={kind === key}
                    key={key}
                    onClick={() => setKind(key)}
                  >
                    {CHANGE_LABELS[key]}{" "}
                    {result.rows.filter((row) => row.kind === key).length}
                  </button>
                ))}
              </div>
              <ul className="analysis-comparison-list">
                {result.rows
                  .filter((row) => row.kind === kind)
                  .slice(0, limit)
                  .map((row) => (
                    <li className="analysis-comparison-row" key={row.symbol}>
                      <button
                        type="button"
                        onClick={() => openChart({ symbol: row.symbol })}
                      >
                        {row.symbol} {row.name}
                      </button>
                      <p>{row.reason}</p>
                    </li>
                  ))}
              </ul>
              {!result.rows.some((row) => row.kind === kind) && (
                <p>此類別沒有股票。</p>
              )}
              {result.rows.filter((row) => row.kind === kind).length >
                limit && (
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setLimit((n) => n + 50)}
                >
                  顯示更多變化
                </button>
              )}
            </>
          )}
          {snapshots && (
            <p className="analysis-caption">
              快照保存於 {snapshots[0].capturedAt}／{snapshots[1].capturedAt}
            </p>
          )}
        </>
      )}
      <HelpDisclosure label="歷史名單比較說明">
        兩日套用目前相同群組、搜尋與篩選條件，使用各日實際保存的行情及財務快照。僅提供開始保存後的日期；不是還原當日收盤時已知的所有資訊。固定均線沿用目前設定並對齊各日行情，不使用新的股價代替歷史參考。缺少觀測另列，不推定已離開。
      </HelpDisclosure>
    </div>
  );
}

export function ScreenerChanges(props: Parameters<typeof Comparison>[0]) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="research-disclosure screener-changes"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>選股結果變化</summary>
      <div className="research-disclosure-body">
        {open && <Comparison key={props.market} {...props} />}
      </div>
    </details>
  );
}
