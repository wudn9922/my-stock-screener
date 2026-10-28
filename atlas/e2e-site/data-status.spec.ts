import { expect, test } from "@playwright/test";
import { mockSite } from "./mocks";

const sources = ["ok", "late", "stale", "missing"].map((status, i) => ({
  id: String(i),
  name: ["全市場台股", "全市場美股", "利率（FRED）", "成長財務彙整"][i],
  market: ["TW", "US", "全球", "無"][i],
  status,
  dataDate: status === "missing" ? null : "2026-10-27",
  generatedAt:
    status === "missing"
      ? null
      : i === 1
        ? "2026-10-27"
        : "2026-10-28T01:23:00Z",
  expectedDate: "2026-10-28",
  lagTradingDays: i === 3 ? null : i,
  reason: `來源判定原因 ${i}`,
}));
const payload = {
  version: 1,
  generatedAt: "2026-10-28T10:00:00Z",
  summary: { ok: 4, late: 0, stale: 0, missing: 0 },
  sources,
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
    {
      file: "universe-full.yml",
      name: "全市場資料",
      status: "completed",
      conclusion: "success",
      startedAt: "2026-10-28T00:00:00Z",
      updatedAt: "2026-10-28T01:00:00Z",
      url: "https://github.com/wudn9922/my-stock-screener/actions/runs/124",
    },
  ],
};

test("status tab groups all states, Taiwan time, explanations and Actions links without loading large inputs", async ({
  page,
}, info) => {
  await mockSite(page);
  const requested: string[] = [];
  page.on("request", (request) => requested.push(request.url()));
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/report/data-status.json", (route) =>
    route.fulfill({ json: payload }),
  );
  await page.goto("./?page=console");
  const tabs = page.getByRole("group", { name: "控制台分頁" });
  await tabs.getByRole("button", { name: "資料狀態", exact: true }).click();
  const panel = page.getByRole("region", { name: "資料狀態", exact: true });
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText(
    "1 項延遲・1 項過期・1 項缺少",
  );
  for (const label of ["台股", "美股", "全球", "其他"])
    await expect(
      panel.getByRole("region", { name: `${label}資料狀態` }),
    ).toBeVisible();
  await expect(
    panel.locator(".data-status-badge").filter({ hasText: /^正常$/ }),
  ).toHaveCount(1);
  await expect(
    panel.locator(".data-status-badge").filter({ hasText: /^延遲$/ }),
  ).toHaveCount(1);
  await expect(
    panel.locator(".data-status-badge").filter({ hasText: /^過期$/ }),
  ).toHaveCount(1);
  await expect(
    panel.locator(".data-status-badge").filter({ hasText: /^缺少$/ }),
  ).toHaveCount(1);
  await expect(panel).toContainText("2026/10/28 09:23");
  await expect(panel).toContainText("2026-10-27（僅日期）");
  await expect(panel.getByText("來源判定原因 0", { exact: true })).toBeHidden();
  await panel.locator("summary").filter({ hasText: "全市場台股" }).click();
  await expect(
    panel.getByText("來源判定原因 0", { exact: true }),
  ).toBeVisible();
  await expect(panel.getByRole("link", { name: "每日報告 ↗" })).toHaveAttribute(
    "href",
    /actions\/runs\/123$/,
  );
  await expect(panel).toContainText("失敗");
  await expect(panel).toContainText("成功");
  expect(
    requested.some((url) =>
      /\/report\/(universe|breadth|growth)\.json/.test(url),
    ),
  ).toBe(false);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.scrollingElement!.scrollWidth - innerWidth,
    ),
  ).toBeLessThanOrEqual(0);
  for (const button of await tabs.getByRole("button").all())
    expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({
    path: info.outputPath("data-status-390.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("all healthy sources display 全部正常", async ({ page }) => {
  await mockSite(page);
  await page.route("**/report/data-status.json", (route) =>
    route.fulfill({
      json: {
        ...payload,
        sources: sources.map((source) => ({ ...source, status: "ok" })),
      },
    }),
  );
  await page.goto("./?page=console");
  await page.getByRole("button", { name: "資料狀態", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "全部正常", exact: true }),
  ).toBeVisible();
});

test("missing, malformed and offline status files keep settings usable and support retry", async ({
  page,
}) => {
  await mockSite(page);
  let mode = "missing";
  await page.route("**/report/data-status.json", (route) =>
    mode === "offline"
      ? route.abort()
      : mode === "missing"
        ? route.fulfill({ status: 404 })
        : mode === "invalid"
          ? route.fulfill({ contentType: "text/html", body: "<html>" })
          : route.fulfill({ json: payload }),
  );
  await page.goto("./?page=console");
  await page.getByRole("button", { name: "資料狀態", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("資料狀態檔尚未產生");
  for (const next of ["invalid", "offline"]) {
    mode = next;
    await page.getByRole("button", { name: "重新載入", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(
      "無法連線或讀取資料狀態檔",
    );
  }
  mode = "ok";
  await page.getByRole("button", { name: "重新載入", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "資料狀態", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "顯示與App", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "顯示與App", exact: true }),
  ).toBeVisible();
});
