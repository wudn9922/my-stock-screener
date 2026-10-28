import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { buildReport, mockSite } from "./mocks";
import { accountPreference, mockDeviceAccount } from "./account-fixture";

function snapshot(date: string, values: number[]) {
  return {
    version: 1,
    market: "TW",
    dataDate: date,
    capturedAt: date + "T15:00:00Z",
    universe: {
      version: 1,
      fields: ["symbol", "name", "close", "volVsPrev", "asOf"],
      markets: {
        TW: {
          asOf: date,
          rows: ["2330.TW", "2317.TW", "1101.TW"].map((symbol, i) => [
            symbol,
            ["台積電", "鴻海", "台泥"][i],
            100,
            values[i],
            date,
          ]),
        },
      },
    },
    report: { version: 1, groups: [] },
    valuation: null,
    growth: null,
  };
}

test("relative strength compares shared dates, switches benchmark/period and preserves original chart", async ({
  page,
}) => {
  await mockSite(page);
  await page.goto("./?page=chart&symbol=2330.TW&tf=1D");
  await expect(page.getByTestId("ohlc-header")).toContainText("O ");
  await page.getByTestId("chart").evaluate((node) => {
    (window as unknown as { originalChart: Element }).originalChart = node;
  });
  await page.getByTestId("stock-tab-research").click();
  const panel = page.getByRole("region", { name: "個股與大盤強弱比較" });
  await expect(panel.getByTestId("relative-strength-result")).toContainText(
    "大盤",
  );
  await expect(
    panel
      .getByRole("img", { name: "個股與大盤累積報酬比較" })
      .locator("canvas")
      .first(),
  ).toBeVisible();
  await panel.getByLabel("比較基準", { exact: true }).selectOption("^TWOII");
  await expect(panel.getByTestId("relative-strength-result")).toContainText(
    "^TWOII",
  );
  await panel.getByLabel("相對強弱期間").selectOption("21");
  await expect(panel).toContainText("21 個共同交易日");
  expect(
    await page.evaluate(
      () => document.scrollingElement!.scrollWidth - innerWidth,
    ),
  ).toBeLessThanOrEqual(0);
  await page.getByTestId("stock-tab-chart").click();
  expect(
    await page
      .getByTestId("chart")
      .evaluate(
        (node) =>
          (window as unknown as { originalChart: Element }).originalChart ===
          node,
      ),
  ).toBe(true);
});

test("historical screener uses the same condition for entries, retained and exits", async ({
  page,
}) => {
  await mockSite(page, { report: buildReport(20) });
  await page.route("**/report/universe.json", (route) =>
    route.fulfill({ json: snapshot("2026-10-28", [2, 2, 1]).universe }),
  );
  await page.route("**/report/screener-history/index.json", (route) =>
    route.fulfill({
      json: {
        version: 1,
        markets: { TW: ["2026-10-27", "2026-10-28"], US: [] },
      },
    }),
  );
  await page.route("**/report/screener-history/TW-*.json", (route) =>
    route.fulfill({
      json: route.request().url().includes("2026-10-27")
        ? snapshot("2026-10-27", [1, 2, 2])
        : snapshot("2026-10-28", [2, 2, 1]),
    }),
  );
  await page.goto("./?page=screener&group=universe-tw&f=vr:1.5");
  await page
    .locator("summary")
    .filter({ hasText: /^選股結果變化$/ })
    .click();
  const changes = page.locator("details").filter({
    has: page.locator("summary").filter({ hasText: /^選股結果變化$/ }),
  });
  await expect(
    changes.getByRole("button", { name: "新進入 1", exact: true }),
  ).toBeVisible();
  await expect(changes).toContainText("台積電");
  await expect(changes).toContainText("原先未通過：量比");
  await changes
    .getByRole("button", { name: "持續符合 1", exact: true })
    .click();
  await expect(changes).toContainText("鴻海");
  await changes.getByRole("button", { name: "已離開 1", exact: true }).click();
  await expect(changes).toContainText("台泥");
  await changes.getByLabel("名單基準日").selectOption("2026-10-28");
  await expect(changes.getByRole("alert")).toContainText("比較日必須晚於");
  expect(
    await page.evaluate(
      () => document.scrollingElement!.scrollWidth - innerWidth,
    ),
  ).toBeLessThanOrEqual(0);
});

test("historical comparison explains missing history without breaking current screener", async ({
  page,
}) => {
  await mockSite(page, { report: buildReport(20) });
  await page.route("**/report/screener-history/index.json", (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto("./?page=screener&group=tw_all");
  await page
    .locator("summary")
    .filter({ hasText: /^選股結果變化$/ })
    .click();
  await expect(page.getByRole("alert")).toContainText("保存至少兩個交易日");
  await expect(page.locator(".screener-row").first()).toBeVisible();
});

test("historical fixed MAs reject a new price basis instead of manufacturing an exit", async ({
  page,
}) => {
  await mockDeviceAccount(page);
  await page.addInitScript(
    ({ key }) =>
      localStorage.setItem(
        key,
        JSON.stringify({
          version: 1,
          nextId: 2,
          groups: [],
          stocks: [
            {
              id: 1,
              ticker: "2330.TW",
              group_id: "ungrouped",
              ma1: 29,
              ma2: 34,
              ma3: 73,
              ma4: 0,
            },
          ],
        }),
      ),
    { key: accountPreference("atlas-parameter-workspace-v1") },
  );
  await mockSite(page, { report: buildReport(20) });
  await page.route("**/report/universe.json", (route) =>
    route.fulfill({ json: snapshot("2026-10-08", [2, 2, 1]).universe }),
  );
  await page.route("**/report/screener-history/index.json", (route) =>
    route.fulfill({
      json: {
        version: 1,
        markets: { TW: ["2026-10-07", "2026-10-08"], US: [] },
      },
    }),
  );
  await page.route("**/report/screener-history/TW-*.json", (route) =>
    route.fulfill({
      json: route.request().url().includes("2026-10-07")
        ? snapshot("2026-10-07", [1, 2, 2])
        : snapshot("2026-10-08", [2, 2, 1]),
    }),
  );
  await page.goto("./?page=screener&group=universe-tw&f=ema20:-5:5");
  await page
    .locator("summary")
    .filter({ hasText: /^選股結果變化$/ })
    .click();
  const changes = page.locator(".screener-changes");
  await expect(changes).toContainText("固定均線缺少與快照同價格口徑的歷史資料");
  await expect(
    changes.getByRole("group", { name: "名單變化類別" }),
  ).toHaveCount(0);
});

test("industry comparison switches shared windows and metrics; missing volume stays unknown", async ({
  page,
}) => {
  await mockSite(page);
  await page.route("**/report/themes.json", (route) =>
    route.fulfill({
      json: JSON.parse(
        readFileSync(
          new URL("../src/report/fixtures/themes.sample.json", import.meta.url),
          "utf8",
        ),
      ),
    }),
  );
  const row = (name: string, ret: number, volume: number | null) => ({
    name,
    returnPct: ret,
    volumeRatio: volume,
    count: 10,
    validCount: 9,
    upShare: 70,
    volumeCount: volume === null ? 0 : 8,
    pairedCount: 8,
    previousReturnPct: 2,
    momentumChange: ret - 2,
    rank: 1,
  });
  const period = {
    startDate: "2026-09-28",
    endDate: "2026-10-28",
    volumePreviousStart: "2026-08-28",
    rows: [row("Technology", 10, 1.2), row("Finance", 5, null)],
  };
  await page.route("**/report/industry-rotation.json", (route) =>
    route.fulfill({
      json: {
        version: 1,
        generatedAt: "2026-10-28T10:00:00Z",
        markets: {
          US: {
            asOf: "2026-10-28",
            scope: "full",
            periods: {
              d1: { ...period, rows: [row("Technology", 1, 0.8)] },
              w1: period,
              m1: period,
              m3: period,
            },
          },
        },
      },
    }),
  );
  await page.goto("./?page=themes");
  await page
    .locator("summary")
    .filter({ hasText: /^產業輪動比較$/ })
    .click();
  const rows = page.getByRole("list", { name: "產業輪動比較結果" });
  await expect(rows).toContainText("科技 Technology");
  await expect(rows).toContainText("成交量比 —");
  await page
    .getByLabel("產業比較排序", { exact: true })
    .selectOption("volumeRatio");
  await expect(rows.locator("li").first()).toContainText("Technology");
  await page.getByLabel("產業比較期間", { exact: true }).selectOption("d1");
  await expect(rows).toContainText("成交量比 0.80 倍");
  await expect(rows.locator("li")).toHaveCount(1);
  expect(
    await page.evaluate(
      () => document.scrollingElement!.scrollWidth - innerWidth,
    ),
  ).toBeLessThanOrEqual(0);
});
