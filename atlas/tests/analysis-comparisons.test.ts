import { describe, expect, it } from "vitest";
import { relativeStrength, stockRs } from "../src/pages/relativeStrength";
import {
  compareScreenerResults,
  snapshotReferenceBars,
} from "../src/pages/screenerChanges";
import { parseFilters } from "../src/pages/screenerFilters";
import {
  parseHistoryIndex,
  parseScreenerSnapshot,
} from "../src/report/screenerHistory";
import {
  parseIndustryRotation,
  sortIndustryRotation,
} from "../src/report/industryRotation";
import { parseUniverse, universeRowToItem } from "../src/report/universe";
import type { Bar } from "../src/market-data/MarketDataProvider";

const bar = (date: string, close: number) =>
  ({
    time: Date.parse(date + "T05:00:00Z") / 1000,
    close,
    open: close,
    high: close,
    low: close,
    volume: 100,
  }) as Bar;
const item = (symbol: string, date: string, ratio: number) =>
  universeRowToItem(
    [symbol, symbol, 100, ratio, date],
    { symbol: 0, name: 1, close: 2, volVsPrev: 3, asOf: 4 },
    { benchmark: "^TWII", asOf: date },
  )!;
describe("relative performance", () => {
  it('uses the published full-market RS, preserves its date and never infers a missing rank', () => {
    const parsed = parseUniverse({ fields: ['symbol', 'rsRank', 'asOf'], markets: {
      TW: { scope: 'full', benchmark: '^TWII', asOf: '2026-10-29', rows: [['2330.TW', 87, '2026-10-28']] },
      US: { scope: 'liquid', rows: [['AAPL', 99, '2026-10-29']] },
    } });
    if (parsed.status !== 'ok') throw new Error('Invalid test universe');
    expect(stockRs(parsed.universe, '2330.TW')).toEqual({ rank: 87, date: '2026-10-28', benchmark: '^TWII', stale: true });
    expect(stockRs(parsed.universe, 'AAPL')).toBeNull();
    expect(stockRs(parsed.universe, '2317.TW')).toBeNull();
    parsed.universe.markets.TW!.items[0]!.metrics!.rsRank = null;
    expect(stockRs(parsed.universe, '2330.TW')).toBeNull();
  });
  it("aligns by actual dates rather than array position, rebases and measures excess in points", () => {
    const result = relativeStrength(
      [bar("2026-10-20", 100), bar("2026-10-21", 150), bar("2026-10-22", 120)],
      [bar("2026-10-20", 200), bar("2026-10-22", 210)],
      "2330.TW",
      "^TWII",
      21,
    )!;
    expect(result.sessions).toBe(1);
    expect(result.points.map((point) => point.time)).toEqual([
      "2026-10-20",
      "2026-10-22",
    ]);
    expect(result.stockReturn).toBeCloseTo(20);
    expect(result.benchmarkReturn).toBeCloseTo(5);
    expect(result.excess).toBeCloseTo(15);
  });
  it("does not fabricate missing/invalid closes or an overlap", () => {
    expect(
      relativeStrength(
        [bar("2026-10-20", 0)],
        [bar("2026-10-20", 100)],
        "AAPL",
        "SPY",
        21,
      ),
    ).toBeNull();
    expect(
      relativeStrength(
        [bar("2026-10-20", 100)],
        [bar("2026-10-22", 100)],
        "AAPL",
        "SPY",
        21,
      ),
    ).toBeNull();
  });
});
describe("historical screener results", () => {
  it("rejects a split-adjusted historical reference that disagrees with the frozen close", () => {
    const captured = item("2330.TW", "2026-10-27", 1.2);
    const aligned = [
      bar("2026-10-26", 95),
      bar("2026-10-27", 100),
      bar("2026-10-28", 105),
    ];
    expect(snapshotReferenceBars(captured, aligned)).toHaveLength(2);
    expect(snapshotReferenceBars(captured, [bar("2026-10-27", 50)])).toEqual(
      [],
    );
    expect(snapshotReferenceBars(captured, [bar("2026-10-26", 100)])).toEqual(
      [],
    );
  });
  it("identifies entries, retained, exits and missing observations with the same conditions", () => {
    const before = ["A", "B", "C", "D"].map((s, i) =>
      item(s, "2026-10-27", [1, 2, 2, 1][i]),
    );
    const after = ["A", "B", "C", "D"].map((s, i) =>
      item(s, s === "D" ? "2026-10-27" : "2026-10-28", [2, 2, 1, 2][i]),
    );
    const result = compareScreenerResults(
      { items: before, context: { market: "TW", asOf: "2026-10-27" } },
      { items: after, context: { market: "TW", asOf: "2026-10-28" } },
      parseFilters("vr:1.5"),
    );
    expect(result.error).toBeNull();
    expect(result.rows.map((row) => row.kind)).toEqual([
      "entered",
      "retained",
      "left",
    ]);
    expect(result.rows[0].reason).toContain("量比");
    const gap = compareScreenerResults(
      { items: [], context: { market: "TW", asOf: "2026-10-27" } },
      { items: after, context: { market: "TW", asOf: "2026-10-28" } },
      parseFilters(""),
    );
    expect(gap.rows.every((row) => row.kind === "unavailable")).toBe(true);
  });
  it("blocks unsupported historical conditions instead of silently dropping them", () => {
    const rows = [item("A", "2026-10-28", 2)];
    expect(
      compareScreenerResults(
        { items: rows, context: { market: "TW" } },
        { items: rows, context: { market: "TW" } },
        parseFilters("eps:1:20"),
      ).error,
    ).toBeTruthy();
    expect(
      parseHistoryIndex({
        version: 1,
        markets: { TW: ["2026-02-30"], US: [] },
      }),
    ).toBeNull();
  });
  it("validates snapshot market and date and never loads todays valuation for a historical snapshot", async () => {
    const snapshot = {
      version: 1,
      market: "TW",
      dataDate: "2026-10-27",
      capturedAt: "2026-10-27T15:00:00Z",
      universe: {
        version: 1,
        fields: ["symbol", "asOf"],
        markets: {
          TW: { asOf: "2026-10-27", rows: [["2330.TW", "2026-10-27"]] },
        },
      },
      report: { groups: [] },
      valuation: null,
    };
    expect(
      await parseScreenerSnapshot(snapshot, "US", "2026-10-27"),
    ).toBeNull();
    expect(
      await parseScreenerSnapshot(snapshot, "TW", "2026-10-28"),
    ).toBeNull();
    expect(
      (await parseScreenerSnapshot(snapshot, "TW", "2026-10-27"))?.pe,
    ).toBeNull();
  });
});
describe("industry comparison", () => {
  it("rejects invalid payload and sorts absent metrics last without mutating input", () => {
    expect(parseIndustryRotation({})).toBeNull();
    const rows = [
      { name: "a", returnPct: null },
      { name: "b", returnPct: 2 },
      { name: "c", returnPct: -1 },
    ].map((row) => ({
      ...row,
      count: 1,
      validCount: 1,
      upShare: null,
      volumeRatio: null,
      volumeCount: 0,
      pairedCount: 0,
      previousReturnPct: null,
      momentumChange: null,
      rank: null,
    }));
    expect(
      sortIndustryRotation(rows, "returnPct").map((row) => row.name),
    ).toEqual(["b", "c", "a"]);
    expect(rows[0].name).toBe("a");
  });
});
