import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  researchBars,
  researchCandlesSchema,
  researchDrawingSymbol,
} from "../src/report/researchCandles";
import { CANONICAL_SYMBOL_REGEX } from "../src/market-data/MarketDataProvider";
const packet = {
  version: 1,
  symbol: "FX-USDTWD",
  interval: "1D",
  bars: [["2026-10-20", 32, 33, 31, 32.5, null]],
};
describe("Research candle contracts", () => {
  it("rejects invalid OHLC and nonascending dates", () => {
    expect(
      researchCandlesSchema.safeParse({
        ...packet,
        bars: [["2026-10-20", 32, 31, 30, 32.5, null]],
      }).success,
    ).toBe(false);
    expect(
      researchCandlesSchema.safeParse({
        ...packet,
        bars: [...packet.bars, ...packet.bars],
      }).success,
    ).toBe(false);
  });
  it("rejects impossible calendar dates", () => {
    expect(
      researchCandlesSchema.safeParse({
        ...packet,
        bars: [["2026-02-30", 32, 33, 31, 32.5, null]],
      }).success,
    ).toBe(false);
  });
  it("preserves source calendar day and quote direction", () => {
    const bars = researchBars(packet, "FX-USDTWD");
    expect(new Date(bars[0]!.time * 1000).toISOString().slice(0, 10)).toBe(
      "2026-10-20",
    );
    expect(bars[0]!.close).toBe(32.5);
    expect(() => researchBars(packet, "FX-EURUSD")).toThrow("代號不符");
  });
  it("uses stable distinct canonical drawing identities per theme, market and instrument", () => {
    const ids = [
      "world:CMD-WTI",
      "world:CMD-GOLD",
      "theme:us:ai-chips:SMH",
      "theme:tw:ai-chips:2330.TW",
    ].map(researchDrawingSymbol);
    expect(new Set(ids).size).toBe(4);
    for (const id of ids) expect(CANONICAL_SYMBOL_REGEX.test(id)).toBe(true);
    expect(researchDrawingSymbol("world:CMD-WTI")).toBe(ids[0]);
  });
  it("validates retained daily commodity and FX histories (and live snapshots in CI)", () => {
    for (const [id, symbol] of Object.entries({
      wti: "CMD-WTI", brent: "CMD-BRENT", gas: "CMD-GAS", gold: "CMD-GOLD",
      silver: "CMD-SILVER", copper: "CMD-COPPER", usdtwd: "FX-USDTWD",
      usdjpy: "FX-USDJPY", eurusd: "FX-EURUSD", usdcny: "FX-USDCNY", usdkrw: "FX-USDKRW",
    })) {
      const csv = readFileSync(resolve(import.meta.dirname, `../../data/world/${symbol}.csv`), "utf8");
      const bars = csv.trim().split(/\r?\n/).slice(1).map((line) => {
        const [day, ...values] = line.split(",");
        return [day, ...values.map((v) => v === "" ? null : Number(v))];
      });
      const raw = process.env.ATLAS_RESEARCH_DIR
        ? JSON.parse(readFileSync(resolve(process.env.ATLAS_RESEARCH_DIR, `candles/${id}.json`), "utf8"))
        : { version: 1, symbol, interval: "1D", bars };
      expect(researchCandlesSchema.safeParse(raw).success, id).toBe(true);
      expect(raw.bars.length).toBeGreaterThan(1000);
      expect(raw.bars).toEqual(bars);
    }
  });
});
