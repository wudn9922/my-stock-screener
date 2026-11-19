import { describe, expect, it } from "vitest";
import { sameTimeVolume } from "../src/market-data/SameTimeVolume";
import type { Bar, BarResult } from "../src/market-data/MarketDataProvider";
const t = (s: string) => Date.parse(s) / 1000;
const session = (start: string, count: number, volume: number): Bar[] =>
  Array.from({ length: count }, (_, i) => ({
    time: t(start) + i * 300,
    open: 10,
    high: 10,
    low: 10,
    close: 10,
    volume,
  }));
const result = (bars: Bar[], asOf: number): BarResult => ({
  bars,
  asOf,
  source: "fixture",
  session: "regular",
  delayed: true,
  adjusted: true,
  dataState: "delayed",
});
describe("same-session elapsed-time volume", () => {
  it("compares completed buckets across a weekend and DST boundary, excluding the forming bucket and pre/post bars", () => {
    const bars = [
      ...session("2026-10-30T13:30:00Z", 78, 100),
      ...session("2026-11-02T14:30:00Z", 8, 200),
      ...session("2026-11-02T13:00:00Z", 1, 999999),
    ];
    const value = sameTimeVolume(
      result(bars, t("2026-11-02T15:07:00Z")),
      "AAPL",
      t("2026-11-02T15:07:00Z"),
    );
    expect(sameTimeVolume(result(bars, t("2026-11-02T15:07:00Z")), "BRK-B", t("2026-11-02T15:07:00Z"))).toHaveProperty("status", "ok");
    expect(sameTimeVolume(result(bars, t("2026-11-02T15:07:00Z")), "BTC-USD", t("2026-11-02T15:07:00Z"))).toHaveProperty("status", "unavailable");
    expect(value).toMatchObject({
      status: "ok",
      ratio: 2,
      cutoff: "10:05",
      currentVolume: 1400,
      previousVolume: 700,
      date: "2026-11-02",
      previousDate: "2026-10-30",
      stale: false,
    });
  });
  it("uses TW 09:00 exchange-local open and reports historical sessions on holidays", () => {
    const bars = [
      ...session("2026-10-29T01:00:00Z", 54, 3000),
      ...session("2026-10-30T01:00:00Z", 5, 1500),
    ];
    expect(
      sameTimeVolume(
        result(bars, t("2026-10-30T02:00Z")),
        "2330.TW",
        t("2026-11-01T02:00Z"),
      ),
    ).toMatchObject({ status: "ok", ratio: 0.5, cutoff: "09:25", stale: true });
  });
  it("refuses missing buckets, unknown volume, duplicate buckets, zero denominator and short prior sessions", () => {
    const old = session("2026-10-29T13:30Z", 6, 10),
      current = session("2026-10-30T13:30Z", 6, 20),
      at = t("2026-10-30T15:00Z");
    const check = (bars: Bar[], metadata = {}) =>
      sameTimeVolume({ ...result(bars, at), ...metadata }, "AAPL", at);
    expect(check([...old.slice(1), ...current])).toMatchObject({
      status: "unavailable",
      reason: expect.stringContaining("不完整"),
    });
    expect(
      check([...old, ...current], { missingVolumeAt: [old[2]!.time] }),
    ).toHaveProperty("status", "unavailable");
    expect(check([...old, ...current, current[0]!])).toHaveProperty(
      "status",
      "unavailable",
    );
    expect(
      check([...old.map((x) => ({ ...x, volume: 0 })), ...current]),
    ).toMatchObject({ reason: expect.stringContaining("為零") });
    expect(check([...old.slice(0, 3), ...current])).toHaveProperty(
      "status",
      "unavailable",
    );
    expect(check(current)).toHaveProperty("status", "unavailable");
    expect(
      check([...old, ...current], { dataState: "simulated" }),
    ).toHaveProperty("status", "unavailable");
  });
  it("covers indices with real intraday volume and hides those whose source has none", () => {
    const at = t("2026-11-19T02:32:00Z");
    const tw = (date: string, count: number, volume: number) => session(`${date}T01:00:00Z`, count, volume);
    expect(sameTimeVolume(result([...tw("2026-11-18", 54, 100), ...tw("2026-11-19", 18, 150)], at), "^TWII", at))
      .toMatchObject({ status: "ok", ratio: 1.5, cutoff: "10:30", stale: false });
    // Zero intraday volume (Yahoo's ^TWII today) is not a 0.00x ratio.
    expect(sameTimeVolume(result([...tw("2026-11-18", 54, 100), ...tw("2026-11-19", 18, 0)], at), "^TWII", at))
      .toHaveProperty("status", "unavailable");
    expect(sameTimeVolume(result([...tw("2026-11-18", 54, 0), ...tw("2026-11-19", 18, 0)], at), "^TWII", at))
      .toHaveProperty("status", "unavailable");
    const ny = t("2026-11-19T15:07:00Z");
    expect(sameTimeVolume(result([...session("2026-11-18T14:30:00Z", 78, 100), ...session("2026-11-19T14:30:00Z", 7, 50)], ny), "^GSPC", ny))
      .toMatchObject({ status: "ok", ratio: 0.5, cutoff: "10:05" });
  });
});
