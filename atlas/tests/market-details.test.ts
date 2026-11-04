import { describe, expect, it, vi, afterEach } from "vitest";
import { loadFailureReason } from "../src/market-data/loadFailureReason";
import { ipoSchema, loadIpos, matchIpos } from "../src/ipo/ipoWatch";
import { INITIAL_IPOS } from "../src/ipo/initial";
import { normalizeYahooResponse } from "../src/market-data/YahooNormalizer";
import { fetchExtendedQuotes } from "../src/market-data/extendedQuotes";
afterEach(() => vi.unstubAllGlobals());
const at = Date.parse("2026-11-03T23:00Z") / 1000;
const raw = () => ({
  chart: {
    result: [
      {
        meta: {
          symbol: "AAPL",
          currency: "USD",
          regularMarketPrice: 100,
          regularMarketTime: at - 7200,
          preMarketPrice: 98,
          preMarketTime: at - 36000,
          postMarketPrice: 102,
          postMarketTime: at - 3600,
        },
        timestamp: [at - 86400, at - 8000],
        indicators: {
          quote: [
            {
              open: [99, 100],
              high: [99, 100],
              low: [99, 100],
              close: [99, 100],
              volume: [100, null],
            },
          ],
        },
      },
    ],
  },
});
it("keeps regular price unchanged and preserves only timestamped, valid extended quotes; unknown volume stays marked", () => {
  expect(normalizeYahooResponse(raw(), at, "1D", "AAPL")).toMatchObject({
    quote: {
      price: 100,
      extended: { pre: { price: 98 }, post: { price: 102 } },
    },
    missingVolumeAt: [at - 8000],
  });
  const input = raw();
  input.chart.result[0]!.meta.postMarketTime = at + 10000;
  expect(
    normalizeYahooResponse(input, at, "1D", "AAPL").quote?.extended?.post,
  ).toBeUndefined();
});
it("searches ANT/Claude as IPO metadata with no invented listing date or price, and rejects untrusted links", () => {
  expect(matchIpos(INITIAL_IPOS, "ant")[0]).toMatchObject({
    symbol: "ANT",
    listingDate: null,
    price: null,
  });
  expect(matchIpos(INITIAL_IPOS, "claude")).toHaveLength(1);
  expect(
    ipoSchema.safeParse({
      ...INITIAL_IPOS[0],
      sourceUrl: "javascript:alert(1)",
    }).success,
  ).toBe(false);
});
it("retains dated IPO reference metadata when the update file is unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("", { status: 404 })),
  );
  expect(await loadIpos()).toMatchObject({
    companies: INITIAL_IPOS,
    reason: expect.stringContaining("HTTP 404"),
  });
});
it("refuses mismatched extended snapshots and explains missing coverage", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("", { status: 404 })),
  );
  await expect(fetchExtendedQuotes("NVDA")).rejects.toThrow("尚未收錄");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        version: 1,
        symbol: "AAPL",
        checkedAt: "x",
        source: "fixture",
        pre: { status: "unavailable" },
        post: { status: "unavailable" },
      }),
    ),
  );
  await expect(fetchExtendedQuotes("NVDA")).rejects.toThrow("代號不符");
});
describe("safe public failure reasons", () => {
  it.each([
    ["HTTP 429", "請求過多"],
    ["HTTP 404", "找不到"],
    ["Timed out", "逾時"],
    ["JSON failed", "格式無效"],
    ["HTTP 502", "服務暫時"],
    ["Failed to fetch", "檢查網路"],
  ])("%s has an actionable explanation", (message, reason) => {
    expect(loadFailureReason(new Error(message))).toContain(reason);
  });
  it("never echoes arbitrary URLs, secrets or response content", () => {
    expect(
      loadFailureReason(new Error("secret=abc https://private.example/")),
    ).not.toContain("abc");
  });
});


it("does not reject valid regular bars for missing or malformed optional extended metadata", () => {
  const row = raw().chart.result[0]!;
  for (const invalid of [null, 0, -1, "not-provided"]) {
    const payload = { chart: { result: [{ ...row, meta: { ...row.meta,
      preMarketPrice: invalid, preMarketTime: invalid, postMarketPrice: invalid, postMarketTime: invalid,
    } }] } };
    expect(normalizeYahooResponse(payload, at, "1D", "AAPL").quote).toMatchObject({ price: 100, extended: {} });
  }
});
