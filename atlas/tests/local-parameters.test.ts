import { describe, expect, it } from "vitest";
import { LocalParameters } from "../src/cloud/localParameters";
import type { ParameterGroup, ParameterStock } from "../src/cloud/settingsApi";

function memory() {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
  };
}

describe("account device and disposable parameter workspaces", () => {
  it("performs group and stock CRUD while keeping the grouped/fixed preference distinction", () => {
    const storage = memory();
    const api = new LocalParameters(storage);
    const group = api.call("groups.create", {
      name: "我的研究",
    }) as ParameterGroup;
    expect(api.call("groups.list", {})).toEqual([
      expect.objectContaining({ name: "我的研究" }),
    ]);
    api.call("stocks.save", {
      ticker: "nvda",
      groupId: `custom_${group.id}`,
      mas: [20, 60],
    });
    api.call("stocks.save", {
      ticker: "NVDA",
      groupId: "ungrouped",
      mas: [24, 58],
    });
    expect(() =>
      api.call("stocks.save", {
        ticker: "NVDA",
        groupId: "us_g1",
        mas: [5, 20],
      }),
    ).toThrow("已有分組");
    expect(() => api.call("groups.delete", { id: group.id })).toThrow(
      "仍有股票",
    );
    api.call("groups.rename", { id: group.id, name: "半導體" });
    const grouped = (api.call("stocks.list", {}) as ParameterStock[]).find(
      (s) => s.group_id !== "ungrouped",
    )!;
    api.call("stocks.save", {
      id: grouped.id,
      ticker: grouped.ticker,
      groupId: grouped.group_id,
      mas: [50, 200],
    });
    expect(
      (api.call("stocks.list", {}) as ParameterStock[]).find(
        (s) => s.id === grouped.id,
      ),
    ).toMatchObject({ ma1: 50, ma2: 200 });
    api.call("stocks.delete", { id: grouped.id });
    api.call("groups.delete", { id: group.id });
    expect(api.call("groups.list", {})).toEqual([]);
    expect(api.call("stocks.list", {})).toEqual([
      expect.objectContaining({
        ticker: "NVDA",
        group_id: "ungrouped",
        ma1: 24,
        ma2: 58,
      }),
    ]);
    expect(new LocalParameters(storage).call("stocks.list", {})).toEqual(
      api.call("stocks.list", {}),
    );
  });

  it("does not read another account store or silently overwrite malformed data", () => {
    const first = memory(),
      second = memory();
    const a = new LocalParameters(first),
      b = new LocalParameters(second);
    a.call("stocks.save", {
      ticker: "2330.TW",
      groupId: "tw_g1",
      mas: [5, 10],
    });
    expect(b.call("stocks.list", {})).toEqual([]);
    const key = [...first.map.keys()][0]!;
    first.setItem(key, "{broken");
    expect(() => a.call("groups.create", { name: "不覆寫" })).toThrow();
    expect(first.getItem(key)).toBe("{broken");
  });

  it("rejects invalid groups, identifiers and averages before writing anything", () => {
    const storage = memory(),
      api = new LocalParameters(storage);
    for (const payload of [
      { ticker: "NVDA", groupId: "custom_999", mas: [20] },
      { ticker: "NVDA", groupId: "admin_index", mas: [20] },
      { ticker: "<script>", groupId: "us_g1", mas: [20] },
      { ticker: "NVDA", groupId: "us_g1", mas: [20, 20] },
      { ticker: "NVDA", groupId: "us_g1", mas: [0, 1001] },
      { ticker: "NVDA", groupId: "us_g1", mas: [] },
      { ticker: "NVDA", groupId: "us_g1", mas: [1, 2, 3, 4, 5] },
    ])
      expect(() => api.call("stocks.save", payload)).toThrow();
    expect(() => api.call("stocks.delete", { id: -1 })).toThrow();
    expect(() => api.call("groups.create", { name: " ".repeat(3) })).toThrow();
    expect(storage.map.size).toBe(0);
  });

  it("enforces bounded per-account groups and permits edits after a limit is reached", () => {
    const api = new LocalParameters(memory());
    for (let i = 0; i < 50; i++)
      api.call("groups.create", { name: `分組${i}` });
    expect(() => api.call("groups.create", { name: "超過" })).toThrow("50");
    const first = (api.call("groups.list", {}) as ParameterGroup[])[0]!;
    api.call("groups.rename", { id: first.id, name: "仍可修改" });
    expect((api.call("groups.list", {}) as ParameterGroup[])[0]!.name).toBe(
      "仍可修改",
    );
  });
});
