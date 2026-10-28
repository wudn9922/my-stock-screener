import { describe, expect, it } from "vitest";
import {
  fetchDataStatus,
  groupDataStatus,
  parseDataStatus,
  statusSummary,
  workflowLabel,
} from "../src/report/dataStatus";

const source = (id: string, market: string, status: string) => ({
  id,
  name: id,
  market,
  status,
  dataDate: status === "missing" ? null : "2026-10-27",
  generatedAt: null,
  expectedDate: "2026-10-28",
  lagTradingDays: null,
  reason: "測試原因",
});
export const statusFixture = {
  version: 1,
  generatedAt: "2026-10-28T10:00:00Z",
  summary: { ok: 999, late: 0, stale: 0, missing: 0 },
  sources: [
    source("全市場台股", "TW", "ok"),
    source("全市場美股", "US", "late"),
    source("利率", "全球", "stale"),
    source("成長財務", "無", "missing"),
  ],
  workflows: [
    {
      file: "schedule.yml",
      name: "每日報告",
      status: "completed",
      conclusion: "failure",
      startedAt: "2026-10-28T00:00:00Z",
      updatedAt: "2026-10-28T01:00:00Z",
      url: "https://github.com/wudn9922/my-stock-screener/actions/runs/123",
    },
  ],
};
describe("data status contract", () => {
  it("preserves date-only refresh precision", () => {
    const parsed = parseDataStatus({
      ...statusFixture,
      sources: [{ ...statusFixture.sources[0], generatedAt: "2026-10-27" }],
    });
    expect(parsed.status).toBe("ok");
    if (parsed.status === "ok")
      expect(parsed.data.sources[0].generatedAt).toBe("2026-10-27");
  });
  it("groups in market order and computes summary from validated rows", () => {
    const parsed = parseDataStatus(statusFixture);
    expect(parsed.status).toBe("ok");
    if (parsed.status !== "ok") return;
    expect(
      groupDataStatus(parsed.data.sources).map((group) => [
        group.label,
        group.sources.length,
      ]),
    ).toEqual([
      ["台股", 1],
      ["美股", 1],
      ["全球", 1],
      ["其他", 1],
    ]);
    expect(statusSummary(parsed.data.sources)).toEqual({
      ok: 1,
      late: 1,
      stale: 1,
      missing: 1,
    });
    expect(workflowLabel(parsed.data.workflows[0])).toBe("失敗");
  });
  it("rejects malformed dates, duplicate ids, unsafe links and unknown state", () => {
    for (const patch of [
      { status: "good" },
      { dataDate: "2026-02-30" },
      { lagTradingDays: -1 },
    ]) {
      expect(
        parseDataStatus({
          ...statusFixture,
          sources: [{ ...statusFixture.sources[0], ...patch }],
        }).status,
      ).toBe("error");
    }
    expect(
      parseDataStatus({
        ...statusFixture,
        sources: [statusFixture.sources[0], statusFixture.sources[0]],
      }).status,
    ).toBe("error");
    expect(
      parseDataStatus({
        ...statusFixture,
        workflows: [
          { ...statusFixture.workflows[0], url: "javascript:alert(1)" },
        ],
      }).status,
    ).toBe("error");
  });
  it("only reads the small status file and reports HTTP, malformed JSON and network errors", async () => {
    const paths: string[] = [];
    expect(
      (
        await fetchDataStatus(undefined, async (input) => {
          paths.push(String(input));
          return new Response(JSON.stringify(statusFixture));
        })
      ).status,
    ).toBe("ok");
    expect(paths).toHaveLength(1);
    expect(paths[0]).toMatch(/\/report\/data-status\.json$/);
    for (const fetcher of [
      async () => new Response("", { status: 404 }),
      async () => new Response("<html>"),
      async () => {
        throw new Error("offline");
      },
    ]) {
      expect((await fetchDataStatus(undefined, fetcher)).status).toBe("error");
    }
  });
});
