import { z } from "zod";
import { reportUrl } from "./loadReport";

export const DATA_STATUS_LABELS = {
  ok: "正常",
  late: "延遲",
  stale: "過期",
  missing: "缺少",
} as const;
export const STATUS_MARKETS = ["TW", "US", "全球", "無"] as const;
export const STATUS_MARKET_LABELS = {
  TW: "台股",
  US: "美股",
  全球: "全球",
  無: "其他",
} as const;
const date = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((value) => {
    const parsed = new Date(`${value}T00:00:00Z`);
    return (
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    );
  });
const timestamp = z
  .string()
  .refine(
    (value) =>
      /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)),
  );
const schema = z.object({
  version: z.literal(1),
  generatedAt: timestamp,
  sources: z
    .array(
      z.object({
        id: z.string().min(1),
        name: z.string().min(1),
        market: z.enum(STATUS_MARKETS),
        dataDate: date.nullable(),
        generatedAt: z.union([timestamp, date]).nullable(),
        expectedDate: date.nullable(),
        lagTradingDays: z.number().int().nonnegative().nullable(),
        status: z.enum(["ok", "late", "stale", "missing"]),
        reason: z.string(),
        counts: z
          .object({
            stale: z.number().nonnegative().nullable().optional(),
            missing: z.number().nonnegative().nullable().optional(),
          })
          .optional(),
      }),
    )
    .min(1)
    .max(200),
  workflows: z
    .array(
      z.object({
        file: z.string(),
        name: z.string(),
        status: z.string(),
        conclusion: z.string().nullable(),
        startedAt: timestamp.nullable(),
        updatedAt: timestamp.nullable(),
        url: z
          .string()
          .refine((value) =>
            /^https:\/\/github\.com\/wudn9922\/my-stock-screener\/actions\/(?:runs\/\d+|workflows\/[a-z0-9-]+\.yml)$/.test(
              value,
            ),
          ),
        reason: z.string().optional(),
      }),
    )
    .max(30),
});
export type DataStatus = z.infer<typeof schema>;
export type DataSourceStatus = DataStatus["sources"][number];
export type DataStatusResult =
  { status: "ok"; data: DataStatus } | { status: "error"; message: string };

export function parseDataStatus(value: unknown): DataStatusResult {
  const parsed = schema.safeParse(value);
  if (
    !parsed.success ||
    new Set(parsed.data.sources.map((source) => source.id)).size !==
      parsed.data.sources.length
  )
    return {
      status: "error",
      message: "資料狀態檔格式不正確，請稍後重新載入。",
    };
  return { status: "ok", data: parsed.data };
}
export function statusSummary(sources: readonly DataSourceStatus[]) {
  const counts = { ok: 0, late: 0, stale: 0, missing: 0 };
  sources.forEach((source) => counts[source.status]++);
  return counts;
}
export function groupDataStatus(sources: readonly DataSourceStatus[]) {
  return STATUS_MARKETS.map((market) => ({
    market,
    label: STATUS_MARKET_LABELS[market],
    sources: sources.filter((source) => source.market === market),
  }));
}
/** The status view never downloads the universe or source reports to infer freshness. */
export async function fetchDataStatus(
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<DataStatusResult> {
  try {
    const response = await fetcher(new URL("data-status.json", reportUrl()), {
      cache: "no-cache",
      signal,
    });
    if (!response.ok)
      return {
        status: "error",
        message:
          response.status === 404
            ? "資料狀態檔尚未產生，請等待資料狀態工作流程完成後再試。"
            : `無法讀取資料狀態檔（HTTP ${response.status}），請稍後重試。`,
      };
    return parseDataStatus(await response.json());
  } catch {
    return {
      status: "error",
      message: "無法連線或讀取資料狀態檔，請檢查網路後重新載入。",
    };
  }
}

export function workflowLabel(run: DataStatus["workflows"][number]): string {
  if (run.status === "queued") return "排隊中";
  if (run.status === "in_progress") return "執行中";
  return (
    (
      {
        success: "成功",
        failure: "失敗",
        cancelled: "取消",
        skipped: "略過",
        timed_out: "逾時",
        action_required: "需要處理",
        neutral: "中立",
      } as Record<string, string>
    )[run.conclusion ?? ""] ?? "無法取得"
  );
}
