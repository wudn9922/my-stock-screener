import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { fetchResearch } from "../src/report/research";
import { decodeResearchRecord } from "../src/report/researchStore";
const record = ["stock", "AAPL", "台積電", null];
const body = JSON.stringify(record);
const size = new TextEncoder().encode(body).byteLength;
const index = {
  generatedAt: "2026-10-22T00:00:00Z",
  storage: {
    format: "research-market-v1",
    markets: {
      US: {
        path: "volume/US.json",
        records: { AAPL: [2, size, 0] },
        shapes: { stock: ["symbol", "name", "close"] },
      },
    },
  },
};
const schema = z.object({
  symbol: z.string(),
  name: z.string(),
  close: z.number().nullable(),
});
afterEach(() => vi.unstubAllGlobals());
describe("compact research Pages transport", () => {
  it("uses UTF-8 byte ranges and validates the requested stock", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(index)))
      .mockResolvedValueOnce(new Response(body, { status: 206 }));
    vi.stubGlobal("fetch", fetch);
    expect(
      await fetchResearch("volume/AAPL", schema, undefined, "AAPL"),
    ).toEqual({ symbol: "AAPL", name: "台積電", close: null });
    expect(fetch.mock.calls[1]![1].headers.Range).toBe(`bytes=2-${size + 1}`);
  });
  it("accepts HTTP 200 from a server without range support", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response(JSON.stringify(index)))
        .mockResolvedValueOnce(new Response(JSON.stringify([record]))),
    );
    expect((await fetchResearch("volume/AAPL", schema)).symbol).toBe("AAPL");
  });
  it("reloads an index once if a deployment invalidates offsets", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(index)))
      .mockResolvedValueOnce(new Response("[invalid", { status: 206 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(index)))
      .mockResolvedValueOnce(new Response(body, { status: 206 }));
    vi.stubGlobal("fetch", fetch);
    expect((await fetchResearch("volume/AAPL", schema)).symbol).toBe("AAPL");
    expect(fetch).toHaveBeenCalledTimes(4);
  });
  it("rejects a different stock even when validly encoded", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation((url: string) =>
          Promise.resolve(
            new Response(
              url.includes("volume/US")
                ? body.replace("AAPL", "NVDA")
                : JSON.stringify(index),
              { status: url.includes("volume/US") ? 206 : 200 },
            ),
          ),
        ),
    );
    await expect(fetchResearch("volume/AAPL", schema)).rejects.toThrow(
      "股票代號不符",
    );
  });
  it("missing stock stays unavailable rather than displaying another record", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(index))),
    );
    await expect(fetchResearch("volume/MISSING", schema)).rejects.toThrow(
      "尚未收錄",
    );
  });
  it("rejects unknown shapes and decodes list, null and empty object", () => {
    expect(
      decodeResearchRecord([null, ["empty"], null, -1000], { empty: [] }),
    ).toEqual([{}, null, -1000]);
    expect(() => decodeResearchRecord(["unknown"], {})).toThrow("格式驗證失敗");
  });
  it("rejects another module's market path", async () => {
    const wrong = structuredClone(index);
    wrong.storage.markets.US.path = "institutions/TW.json";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(wrong))),
    );
    await expect(fetchResearch("volume/AAPL", schema)).rejects.toThrow(
      "格式驗證失敗",
    );
  });
});
