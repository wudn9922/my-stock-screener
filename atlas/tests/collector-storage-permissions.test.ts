import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ fetch: vi.fn(), close: vi.fn() }));
vi.mock("undici", () => ({
  fetch: h.fetch,
  EnvHttpProxyAgent: class {
    close = h.close;
  },
}));
vi.mock("node:fs/promises", () => ({ readFile: vi.fn(async () => "{}") }));
vi.mock("../src/report/loadReport", () => ({
  parseReport: () => ({ status: "ok", report: { groups: [] } }),
}));
vi.mock("../src/report/universe", () => ({
  parseUniverse: () => ({
    status: "ok",
    universe: {
      markets: {
        US: {
          asOf: "2026-10-23",
          items: [{ symbol: "NVDA", name: "Nvidia", status: "matched" }],
        },
      },
    },
  }),
  universeMarketOf: () => "US",
}));
vi.mock("../src/fundamentals/GrowthProvider", () => ({
  parseReportGrowth: () => null,
  parseGrowthFile: () => null,
  mergeGrowth: () => null,
}));
vi.mock("../src/watchlists/model", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/watchlists/model")>()),
  // Membership evaluation has its own suite; this fixture isolates consent at publication.
  evaluateWatchlist: () => [{ symbol: "NVDA", name: "Nvidia" }],
}));

import {
  collectorStorageMode,
  refreshSavedWatchlists,
} from "../scripts/refresh-watchlists.ts";
import { fetchScreenerSymbols } from "../scripts/screener-symbols.mjs";

const owner = "Ua0037dbd5037b64fc76804c60520ad76";
const user = "U11111111111111111111111111111111";
const id = "d93f0aa6-c873-4f97-8cf9-45a7b55b6439";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
const row = {
  id,
  line_user_id: user,
  updated_at: "2026-10-23T00:00:00Z",
  definition: {
    id,
    name: "私人清單",
    market: "US",
    kind: "manual",
    group: "universe-us",
    filters: "",
    symbols: ["NVDA"],
    updatedAt: 1,
    cloud: { userId: user, version: "1" },
  },
};

beforeEach(() => {
  vi.stubEnv("ATLAS_WATCHLIST_SYNC_ENABLED", "1");
  vi.stubEnv("SUPABASE_URL", "https://offline.invalid");
  vi.stubEnv("SUPABASE_SERVICE_ROLE_KEY", "offline-secret-test");
  vi.stubEnv("SUPABASE_USER_ID", user);
  vi.stubEnv("ADMIN_LINE_USER_ID", owner);
  h.fetch.mockReset();
  h.close.mockReset();
});
afterEach(() => {
  vi.unstubAllEnvs();
});

describe("scheduled watchlist collector storage consent", () => {
  it("defaults only the owner to cloud without creating a grant", () => {
    expect(collectorStorageMode(owner, owner, 200, [])).toBe("cloud");
    expect(collectorStorageMode(user, owner, 200, [])).toBe("none");
    expect(collectorStorageMode(owner, owner, 404, { code: "PGRST205" })).toBe(
      "cloud",
    );
    expect(collectorStorageMode(user, owner, 404, { code: "PGRST205" })).toBe(
      "none",
    );
  });
  it.each(["none", "device"])(
    "does not touch private tables when mode is %s",
    async (mode) => {
      h.fetch.mockResolvedValue(
        json([{ line_user_id: user, storage_mode: mode }]),
      );
      await refreshSavedWatchlists();
      expect(h.fetch).toHaveBeenCalledOnce();
      expect(String(h.fetch.mock.calls[0][0])).toContain(
        "/atlas_storage_access?",
      );
      expect(h.fetch.mock.calls[0][1].method).toBeUndefined();
    },
  );
  it("denies malformed, duplicate, foreign or unavailable grants", () => {
    for (const body of [
      null,
      {},
      [row, row],
      [{ line_user_id: owner, storage_mode: "cloud" }],
      [{ line_user_id: user, storage_mode: "future" }],
    ])
      expect(collectorStorageMode(user, owner, 200, body)).toBe("none");
    for (const status of [401, 403, 429, 500])
      expect(collectorStorageMode(owner, owner, status, {})).toBe("none");
  });
  it("rechecks revocation before writing and stops the run", async () => {
    h.fetch
      .mockResolvedValueOnce(
        json([{ line_user_id: user, storage_mode: "cloud" }]),
      )
      .mockResolvedValueOnce(
        json([row, { ...row, id: "b93f0aa6-c873-4f97-8cf9-45a7b55b6439" }]),
      )
      .mockResolvedValueOnce(
        json([{ line_user_id: user, storage_mode: "none" }]),
      );
    await refreshSavedWatchlists();
    expect(h.fetch).toHaveBeenCalledTimes(3);
    expect(
      h.fetch.mock.calls.some(([, init]) => init?.method === "PATCH"),
    ).toBe(false);
    expect(
      new URL(h.fetch.mock.calls[1][0]).searchParams.get("line_user_id"),
    ).toBe(`eq.${user}`);
  });
  it("scopes writes to the configured account and rejects foreign response rows", async () => {
    h.fetch.mockImplementation(async (url, init) => {
      if (String(url).includes("/atlas_storage_access?"))
        return json([{ line_user_id: user, storage_mode: "cloud" }]);
      if (init?.method === "PATCH") return json([row]);
      return json([{ ...row, line_user_id: owner }, row]);
    });
    await refreshSavedWatchlists();
    const writes = h.fetch.mock.calls.filter(
      ([, init]) => init?.method === "PATCH",
    );
    expect(writes).toHaveLength(1);
    expect(new URL(writes[0][0]).searchParams.get("line_user_id")).toBe(
      `eq.${user}`,
    );
    expect(JSON.parse(writes[0][1].body).definition.cloud.userId).toBe(user);
  });
});

describe("public market-symbol output excludes private accounts", () => {
  const env = {
    SUPABASE_URL: "https://offline.invalid",
    SUPABASE_SERVICE_ROLE_KEY: "offline-test-only",
    SUPABASE_USER_ID: owner,
  };
  it("rejects an unset or nonowner account before any request", async () => {
    for (const userId of ["", "U-test", user])
      await expect(
        fetchScreenerSymbols({ ...env, SUPABASE_USER_ID: userId }, h.fetch),
      ).rejects.toThrow("administrator");
    expect(h.fetch).not.toHaveBeenCalled();
  });
  it.each(["none", "device"])(
    "does not collect or return owner settings in %s mode",
    async (mode) => {
      h.fetch.mockResolvedValue(
        json([{ line_user_id: owner, storage_mode: mode }]),
      );
      await expect(fetchScreenerSymbols(env, h.fetch)).rejects.toThrow(
        "storage",
      );
      expect(h.fetch).toHaveBeenCalledOnce();
    },
  );
  it("does not return symbols if permission is revoked before publication", async () => {
    let checks = 0;
    h.fetch.mockImplementation(async (url) => {
      if (String(url).includes("/atlas_storage_access?"))
        return json([
          {
            line_user_id: owner,
            storage_mode: ++checks < 3 ? "cloud" : "none",
          },
        ]);
      if (String(url).includes("/stocks?"))
        return json([{ ticker: "NVDA", line_user_id: owner }]);
      return json([{ ticker: "^GSPC" }]);
    });
    await expect(fetchScreenerSymbols(env, h.fetch, () => {})).rejects.toThrow(
      "storage",
    );
    expect(checks).toBe(3);
  });
});
