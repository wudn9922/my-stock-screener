import type { GroupItem } from "../report/schema";
import {
  getMarketProfile,
  sessionDate,
  type Bar,
} from "../market-data/MarketDataProvider";
import type { ScreenerSnapshot } from "../report/screenerHistory";
import { universeMarketOf } from "../report/universe";
import {
  activeChips,
  applyFilters,
  EMPTY_FILTERS,
  filterAvailability,
  type FilterContext,
  type FilterState,
  type FilterChip,
} from "./screenerFilters";
import { itemsForView, type StatusView } from "./screenerItems";

export type ChangeKind = "entered" | "retained" | "left" | "unavailable";
export const CHANGE_LABELS: Record<ChangeKind, string> = {
  entered: "新進入",
  retained: "持續符合",
  left: "已離開",
  unavailable: "觀測不足",
};
/** A newly split/adjusted series must not be compared with a frozen pre-split close. */
export function snapshotReferenceBars(
  item: GroupItem | undefined,
  bars: Bar[],
): Bar[] {
  if (!item?.asOf || item.close === null || item.close <= 0) return [];
  const profile = getMarketProfile(item.symbol);
  const aligned = bars.filter(
    (bar) => sessionDate(bar.time, profile) <= item.asOf!,
  );
  const last = aligned.at(-1);
  if (
    !last ||
    sessionDate(last.time, profile) !== item.asOf ||
    !Number.isFinite(last.close) ||
    Math.abs(last.close - item.close) > Math.max(0.0001, item.close * 0.000001)
  )
    return [];
  return aligned;
}
export function snapshotItems(
  snapshot: ScreenerSnapshot | undefined,
  group: string,
  view: StatusView,
  query: string,
): GroupItem[] {
  if (!snapshot) return [];
  const items = universeMarketOf(group)
    ? snapshot.market.items
    : (snapshot.groups.find((entry) => entry.key === group)?.items ?? []);
  const text = query.trim().toLocaleLowerCase();
  return itemsForView(items, view).filter(
    (item) =>
      !text || `${item.symbol} ${item.name}`.toLocaleLowerCase().includes(text),
  );
}
function onlyCondition(filters: FilterState, chip: FilterChip): FilterState {
  const one = {
    ...EMPTY_FILTERS,
    tr: [],
    turn: filters.turn,
    keepMissing: filters.keepMissing,
  } as FilterState;
  if (chip.id.startsWith("tr:"))
    one.tr = filters.tr.filter((flag) => chip.id === `tr:${flag}`);
  else if (chip.id.startsWith("growth:"))
    one.extraGates = [filters.extraGates![Number(chip.id.split(":")[1])]];
  else Object.assign(one, { [chip.key]: filters[chip.key] });
  return one;
}
export function compareScreenerResults(
  previous: { items: GroupItem[]; context: FilterContext },
  next: { items: GroupItem[]; context: FilterContext },
  filters: FilterState,
) {
  const chips = activeChips(filters).filter((chip) => chip.id !== "turn");
  const unavailable = [previous, next].flatMap((side) =>
    activeChips(filters, filterAvailability(side.items, side.context))
      .filter((chip) => chip.id !== "turn" && chip.unavailable)
      .map((chip) => `${chip.label}：${chip.unavailable}`),
  );
  if (unavailable.length)
    return { error: [...new Set(unavailable)].join("；"), rows: [] };
  const sets = [previous, next].map((side) => {
    const observed = side.items.filter(
      (item) => item.status !== "failed" && item.asOf === side.context.asOf,
    );
    return {
      items: new Map(side.items.map((item) => [item.symbol, item])),
      observed: new Set(observed.map((item) => item.symbol)),
      passed: new Set(
        applyFilters(observed, filters, side.context).map(
          (item) => item.symbol,
        ),
      ),
      conditions: chips.map((chip) => ({
        label: chip.label,
        symbols: new Set(
          applyFilters(
            observed,
            onlyCondition(filters, chip),
            side.context,
          ).map((item) => item.symbol),
        ),
      })),
    };
  });
  const [before, after] = sets;
  const symbols = new Set([...before.passed, ...after.passed]);
  const rows = [...symbols].sort().map((symbol) => {
    const was = before.passed.has(symbol),
      is = after.passed.has(symbol);
    const gap = !before.observed.has(symbol) || !after.observed.has(symbol);
    const kind: ChangeKind = gap
      ? "unavailable"
      : was && is
        ? "retained"
        : is
          ? "entered"
          : "left";
    const failedBefore = before.conditions
      .filter((test) => !test.symbols.has(symbol))
      .map((test) => test.label);
    const failedAfter = after.conditions
      .filter((test) => !test.symbols.has(symbol))
      .map((test) => test.label);
    return {
      symbol,
      name: (after.items.get(symbol) ?? before.items.get(symbol))!.name,
      kind,
      reason: gap
        ? "其中一天沒有此股的有效觀測或未收錄，不判定為條件進出。"
        : kind === "retained"
          ? "兩日皆符合相同條件。"
          : kind === "entered"
            ? `原先未通過：${failedBefore.join("、")}；比較日已通過。`
            : `比較日未通過：${failedAfter.join("、")}。`,
    };
  });
  return { error: null, rows };
}
