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
  headlineReturn,
  industryMethodNote,
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
describe("industry comparison method fields", () => {
  const oldRow = {
    name: "a",
    count: 4,
    validCount: 4,
    returnPct: 17.7,
    upShare: 60,
    volumeRatio: null,
    volumeCount: 0,
    pairedCount: 0,
    previousReturnPct: null,
    momentumChange: null,
    rank: 1,
  };
  const file = (rowValue: object, extra: object = {}) => {
    const period = {
      startDate: "2026-10-01",
      endDate: "2026-11-12",
      volumePreviousStart: "2026-09-01",
      rows: [rowValue],
    };
    return {
      version: 1,
      generatedAt: "2026-11-13T00:00:00Z",
      markets: {
        US: {
          asOf: "2026-11-12",
          scope: "full",
          ...extra,
          periods: { d1: period, w1: period, m1: period, m3: period },
        },
      },
    };
  };
  const method = { rankBy: "median" as const, minAmount: 5_000_000, minPrice: 5, minMembers: 3 };

  it("parses a file published before the method fields existed and falls back to the mean", () => {
    const parsed = parseIndustryRotation(file(oldRow));
    const parsedRow = parsed?.markets.US?.periods.m1.rows[0];
    expect(parsedRow).toBeDefined();
    expect(parsed?.markets.US?.method).toBeUndefined();
    expect(headlineReturn(parsedRow!)).toBe(17.7);
  });

  it("parses the new fields and uses the median as headline, including null for unranked groups", () => {
    const ranked = {
      ...oldRow,
      liquidCount: 4,
      medianReturnPct: 3.5,
      capWeightedReturnPct: null,
    };
    const parsed = parseIndustryRotation(file(ranked, { method }));
    const parsedRow = parsed?.markets.US?.periods.m1.rows[0];
    expect(parsed?.markets.US?.method).toEqual(method);
    expect(parsedRow?.capWeightedReturnPct).toBeNull();
    expect(headlineReturn(parsedRow!)).toBe(3.5);
    const unranked = { ...ranked, returnPct: null, medianReturnPct: null, rank: null };
    const unrankedRow = parseIndustryRotation(file(unranked, { method }))?.markets.US?.periods.m1.rows[0];
    expect(headlineReturn(unrankedRow!)).toBeNull();
  });

  it("rejects a malformed method block", () => {
    expect(parseIndustryRotation(file(oldRow, { method: { ...method, rankBy: "mean" } }))).toBeNull();
  });

  it("sorts by the median headline when present, otherwise by the mean", () => {
    const make = (name: string, returnPct: number | null, median?: number | null) => ({
      ...oldRow,
      name,
      returnPct,
      ...(median === undefined ? {} : { medianReturnPct: median }),
    });
    const withMedian = [make("spiky", 50, 1), make("steady", 5, 4), make("thin", null, null)];
    expect(sortIndustryRotation(withMedian, "returnPct").map((r) => r.name)).toEqual(["steady", "spiky", "thin"]);
    const legacy = [make("spiky", 50), make("steady", 5)];
    expect(sortIndustryRotation(legacy, "returnPct").map((r) => r.name)).toEqual(["spiky", "steady"]);
  });

  it("builds the visible method note from the method block", () => {
    expect(industryMethodNote(method, "US")).toBe(
      "排名依中位數；僅計入 20 日均成交值 ≥ US$500萬、股價 ≥ US$5 的股票；少於 3 檔不排名。",
    );
    expect(industryMethodNote({ ...method, minAmount: 20_000_000, minPrice: 10 }, "TW")).toBe(
      "排名依中位數；僅計入 20 日均成交值 ≥ NT$2,000萬、股價 ≥ NT$10 的股票；少於 3 檔不排名。",
    );
  });
});
