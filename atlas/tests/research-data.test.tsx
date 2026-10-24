import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  institutionsSchema,
  volumeSchema,
  alertsSchema,
  secSchema,
  worldAssetsSchema,
  volumeStructure,
  fetchResearch,
  type VolumeFeature,
} from "../src/report/research";
import {
  decodeResearchRecord,
  marketIndexSchema,
} from "../src/report/researchStore";
import { holderBaseline, largeHolderSchema } from "../src/report/twChips";
import { parseRoute, serializeRoute } from "../src/app/routes";
import { HistoryPlot, num, pct, Provenance } from "../src/ui/research/Common";
import { dispositionStatus } from "../src/ui/research/OfficialAlerts";
import { VolumeAnalysis } from "../src/ui/research/VolumePanel";
// Unit tests use a reviewed corpus; CI also runs against the restored live tree.
const root = process.env.ATLAS_RESEARCH_DIR
  ? resolve(process.env.ATLAS_RESEARCH_DIR)
  : resolve(import.meta.dirname, "fixtures/research-public");
const rawJson = (p: string) =>
  JSON.parse(readFileSync(resolve(root, p), "utf8"));
const json = (p: string) => {
  const match = /^(volume|institutions)\/(.+)\.json$/.exec(p);
  if (match && !["TW", "TWO", "US"].includes(match[2]!)) {
    const module = match[1]!,
      symbol = match[2]!;
    const market =
      module === "volume"
        ? /\.(TW|TWO)$/.test(symbol)
          ? "TW"
          : "US"
        : symbol.split(".").at(-1)!;
    const spec = rawJson(`${module}.json`).storage.markets[market];
    return decodeResearchRecord(
      rawJson(spec.path)[spec.records[symbol][2]],
      spec.shapes,
    );
  }
  return rawJson(p);
};
const allStocks = (module: string) => {
  const markets = rawJson(`${module}.json`).storage.markets as Record<
    string,
    { path: string; shapes: Record<string, string[]> }
  >;
  return Object.values(markets).flatMap((spec) =>
    (rawJson(spec.path) as unknown[]).map((record) =>
      decodeResearchRecord(record, spec.shapes),
    ),
  );
};
const feature = (up: boolean, rvol: number | null): VolumeFeature => ({
  date: "2026-10-01",
  close: 100,
  volume: 100,
  valid: true,
  returnPct: up ? 1 : -1,
  priorAvg20: 100,
  rvol,
  amount: 10000,
  amountBasis: "proxy",
  rangePct: 1,
  missingReason: null,
});
afterEach(() => vi.unstubAllGlobals());
describe("public research contracts", () => {
  it("validates every institutional shard and preserves negative values", () => {
    expect(
      marketIndexSchema.safeParse(rawJson("institutions.json")).success,
    ).toBe(true);
    const stocks = allStocks("institutions");
    expect(stocks.length).toBeGreaterThan(2);
    if (process.env.ATLAS_RESEARCH_DIR)
      expect(stocks.length).toBeGreaterThan(1000);
    for (const stock of stocks) {
      expect(institutionsSchema.safeParse(stock).success).toBe(true);
    }
    expect(
      institutionsSchema
        .parse(json("institutions/2330.TW.json"))
        .rows.some((r) => r.foreign.net < 0),
    ).toBe(true);
  }, 30000);
  it("validates every daily price-volume shard", () => {
    expect(marketIndexSchema.safeParse(rawJson("volume.json")).success).toBe(
      true,
    );
    for (const stock of allStocks("volume"))
      expect(volumeSchema.safeParse(stock).success).toBe(true);
  }, 30000);
  it("validates actual official alerts, SEC and world sources", () => {
    expect(alertsSchema.safeParse(json("alerts.json")).success).toBe(true);
    expect(secSchema.safeParse(json("sec-radar.json")).success).toBe(true);
    expect(worldAssetsSchema.safeParse(json("world-assets.json")).success).toBe(
      true,
    );
  });
  it("rejects wrong symbols instead of leaking prior stock values", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify(json("volume/AAPL.json"))),
        ),
    );
    await expect(
      fetchResearch("volume/NVDA", volumeSchema, undefined, "AAPL"),
    ).rejects.toThrow("股票代號不符");
  });
  it("distinguishes unavailable HTTP from malformed schema", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("bad", { status: 503 })),
    );
    await expect(fetchResearch("volume/AAPL", volumeSchema)).rejects.toThrow(
      "503",
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
    await expect(fetchResearch("volume/AAPL", volumeSchema)).rejects.toThrow(
      "格式驗證失敗",
    );
  });
});
describe("causal structure and dates", () => {
  it("keeps 8/11 separately from downward events", () => {
    const f = [{ ...feature(true, null), volume: 100 }];
    for (let i = 0; i < 20; i++) {
      f.push({
        ...feature(i < 11, i < 8 ? 2 : 0.5),
        volume: f.at(-1)!.volume! * (i < 8 ? 0.9 : 1.1),
      });
    }
    const v = volumeStructure(f, 20);
    expect(v.upShrink).toEqual({
      numerator: 8,
      denominator: 11,
      pct: (8 / 11) * 100,
    });
    expect(v.downExpand).toEqual({ numerator: 9, denominator: 9, pct: 100 });
  });
  it("N/A when no upward/downward days; missing prior session excluded", () => {
    expect(volumeStructure([feature(true, null)], 5).upShrink.pct).toBeNull();
    expect(volumeStructure([feature(true, 0.5)], 5).downExpand.pct).toBeNull();
  });
  it("compares adjacent volumes without requiring RVOL20 warmup", () => {
    const v = volumeStructure(
      [
        { ...feature(true, null), volume: 100 },
        { ...feature(true, null), volume: 80 },
        { ...feature(false, 0.1), volume: 120 },
        { ...feature(false, 2), volume: 60 },
        { ...feature(true, 0.1), volume: 90 },
      ],
      4,
    );
    expect(v.observations).toBe(4);
    expect(v.upShrink).toEqual({ numerator: 1, denominator: 2, pct: 50 });
    expect(v.upExpand).toEqual({ numerator: 1, denominator: 2, pct: 50 });
    expect(v.downShrink).toEqual({ numerator: 1, denominator: 2, pct: 50 });
    expect(v.downExpand).toEqual({ numerator: 1, denominator: 2, pct: 50 });
  });
  it("does not bridge invalid, zero or missing sessions, and equal volume is neutral", () => {
    for (const previous of [
      { ...feature(true, 1), valid: false },
      { ...feature(true, 1), volume: 0 },
      { ...feature(true, 1), volume: null },
    ]) {
      expect(
        volumeStructure(
          [feature(true, 1), previous, { ...feature(true, 1), volume: 50 }],
          1,
        ).observations,
      ).toBe(0);
    }
    expect(
      volumeStructure(
        [feature(true, 1), { ...feature(true, 1), valid: false }],
        1,
      ).observations,
    ).toBe(0);
    expect(
      volumeStructure(
        [feature(true, 1), { ...feature(true, 1), returnPct: null }],
        1,
      ).observations,
    ).toBe(0);
    const equal = volumeStructure([feature(true, 1), feature(true, 1)], 1);
    expect(equal.observations).toBe(1);
    expect(equal.upShrink.numerator).toBe(0);
    expect(equal.upExpand.numerator).toBe(0);
  });
  it.each(["indices", "commodities", "fx"] as const)(
    "round trips world %s without changing default rates",
    (view) => {
      const route = { page: "world", view } as const;
      expect(parseRoute(serializeRoute(route)).route).toEqual(route);
      expect(parseRoute("?page=world").route).toEqual({ page: "world" });
    },
  );
  it("LIFF unwraps shareable world routes", () => {
    expect(parseRoute("?liff.state=%3Fpage%3Dworld%26view%3Dfx").route).toEqual(
      { page: "world", view: "fx" },
    );
  });
  it("calendar-week comparison never uses a missing week", () => {
    const group = { pct: 50, people: 10, shares: 100 };
    const row = largeHolderSchema.parse({
      date: "2026-10-16",
      count: 100,
      totalShares: 200,
      over400: group,
      over1000: group,
    });
    expect(holderBaseline([row], 52)).toBeNull();
    expect(holderBaseline([{ ...row, date: "2026-10-09" }, row], 1)?.date).toBe(
      "2026-10-09",
    );
  });
});
describe("accessible summaries", () => {
  it("keeps price-volume research to abnormal volume and four directional ratios", () => {
    const data = volumeSchema.parse(json("volume/2330.TW.json"));
    const html = renderToStaticMarkup(<VolumeAnalysis data={data} />);
    expect((html.match(/<dt>/g) ?? []).length).toBe(5);
    for (const label of [
      "異常量 RVOL20",
      "上漲縮量比例",
      "下跌放量比例",
      "上漲放量比例",
      "下跌縮量比例",
    ])
      expect(html).toContain(label);
    for (const label of [
      "ADV",
      "Amihud",
      "近期最大量日期",
      "價格變化",
      "連續縮量上漲",
      "成交量與收盤價",
      "流動性與完整數據",
    ])
      expect(html).not.toContain(label);
    expect(html).toContain("有效觀測");
    expect(html).toContain("前一個交易日的成交量");
    expect(html).not.toContain('aria-label="結構縮量門檻"');
    expect(html).not.toContain('aria-label="結構放量門檻"');
    expect(html).toContain('class="research-note source-note"');
  });

  it("null stays N/A and actual zero remains zero", () => {
    expect(num(null)).toBe("N/A");
    expect(num(0)).toBe("0");
    expect(pct(null)).toBe("N/A");
  });
  it("stale snapshot retains original date visibly", () => {
    const data = {
      ...worldAssetsSchema.parse(json("world-assets.json")).series.gold!,
      status: "stale" as const,
    };
    const html = renderToStaticMarkup(<Provenance data={data} />);
    expect(html).toContain(data.asOf);
    expect(html).toContain("資料較舊");
  });
  it("chart can be inspected with date range input, no fake missing points", () => {
    const html = renderToStaticMarkup(
      <HistoryPlot
        label="test"
        points={[
          ["2026-10-01", 1],
          ["2026-10-02", null],
          ["2026-10-03", 2],
        ]}
      />,
    );
    expect(html).toContain('type="range"');
    expect(html).toContain("2026-10-03");
  });
  it("disposition past advertised end is never declared released", () => {
    const row = alertsSchema
      .parse(json("alerts.json"))
      .events.find((e) => e.kind === "disposal")!;
    expect(dispositionStatus(row, "2099-01-01")).toContain("待確認");
  });
});
