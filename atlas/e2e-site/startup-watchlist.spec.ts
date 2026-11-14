import { expect, test } from "@playwright/test";
import { mockSite, buildReport } from "./mocks";
import { mockDeviceAccount, accountPreference } from "./account-fixture";
import { createWatchlist, visitWatchlist } from "../src/watchlists/model";

test("welcome is visible before the app bundle and the normal launch opens watchlists", async ({
  page,
}) => {
  await mockSite(page);
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/assets/index-*.js", async (route) => {
    await waiting;
    await route.continue();
  });
  await page.goto("./", { waitUntil: "commit" });
  try {
    await expect(
      page.getByRole("main", { name: "Atlas 歡迎頁面" }),
    ).toBeVisible();
    await expect(page.getByRole("status")).toContainText("正在開啟 Atlas");
    expect(
      await page
        .locator("#atlas-welcome")
        .evaluate((element) => getComputedStyle(element).backgroundColor),
    ).toBe("rgb(14, 16, 20)");
  } finally {
    release();
  }
  await expect(page.locator(".site")).toHaveAttribute("data-page", "watchlist");
  await expect(page.locator("#atlas-welcome")).toHaveCount(0);
  // End the deliberate bundle interception before a second full navigation (WebKit).
  await page.waitForLoadState("load");
  await page.unroute("**/assets/index-*.js");
  await page.goto("./?page=markets&market=us");
  await expect(page.locator(".site")).toHaveAttribute("data-page", "markets");
});

test("failed app script keeps the welcome page and gives a reason and retry", async ({
  page,
}) => {
  await page.clock.install();
  await page.route("**/assets/index-*.js", (route) => route.abort("failed"));
  await page.goto("./", { waitUntil: "commit" });
  await expect(
    page.getByRole("main", { name: "Atlas 歡迎頁面" }),
  ).toBeVisible();
  await expect(page.locator("#atlas-boot-status")).toContainText(
    "應用程式檔案載入失敗",
  );
  await page.clock.fastForward(16000);
  await expect(page.locator("#atlas-boot-status")).toContainText("應用程式檔案載入失敗");
  await expect(
    page.getByRole("button", { name: "重新載入", exact: true }),
  ).toBeVisible();
});

test("compact home reserves at least 80% of main viewport for quotes; tickers lead and notes follow the content", async ({
  page,
}, testInfo) => {
  await mockDeviceAccount(page);
  const report = buildReport(40) as ReturnType<typeof buildReport> & {
    reportDate: string;
    groups: { key: string; items: { symbol: string; name: string }[] }[];
  };
  const group = report.groups.find((group) => group.key === "tw_all")!;
  const members = group.items
    .slice(0, 30)
    .map((item) => ({ symbol: item.symbol, name: item.name }));
  const list = visitWatchlist(
    createWatchlist({
      name: "密集自選",
      market: "TW",
      kind: "manual",
      group: "tw_all",
      filters: "",
      symbols: members.map((item) => item.symbol),
    }),
    members,
    report.reportDate,
  ).list;
  await page.addInitScript(
    ({ key, list }) =>
      localStorage.setItem(key, JSON.stringify({ version: 1, lists: [list] })),
    { key: accountPreference("atlas-saved-watchlists-v1"), list },
  );
  await mockSite(page, { report });
  await page.goto("./");
  const board = page.getByRole("region", { name: "自選行情" }),
    row = board.locator(".wb-row").first();
  await expect(row).toBeVisible();
  await expect(row.locator(".wb-name b")).toHaveText(
    members[0]!.symbol.replace(/\.TWO?$/, ""),
  );
  await expect(row.locator(".wb-name small")).toHaveText(members[0]!.name);
  const geometry = await row.evaluate((element) => {
    const main = document.getElementById("main")!,
      list = document.querySelector(".wb-rows")!;
    const top = main.getBoundingClientRect().top,
      height = main.getBoundingClientRect().height;
    return {
      proportion: (top + height - list.getBoundingClientRect().top) / height,
      height: element.getBoundingClientRect().height,
      overflow: main.scrollWidth - main.clientWidth,
      ticker: parseFloat(
        getComputedStyle(element.querySelector(".wb-name b")!).fontSize,
      ),
      name: parseFloat(
        getComputedStyle(element.querySelector(".wb-name small")!).fontSize,
      ),
    };
  });
  expect(geometry.proportion).toBeGreaterThanOrEqual(0.8);
  expect(geometry.height).toBeGreaterThanOrEqual(44);
  expect(geometry.height).toBeLessThanOrEqual(52);
  expect(geometry.ticker).toBeGreaterThan(geometry.name);
  expect(geometry.overflow).toBe(0);
  for (const button of await board
    .locator(".wb-toolbar button, .wb-list-toolbar button, select")
    .all())
    expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({ path: testInfo.outputPath("compact-watchlist.png") });
  const footer = page.locator(".site-data-footer");
  await expect(footer).toContainText("設定選股條件、追蹤名單變化");
  await expect(page.getByText("這台裝置沒有可匯入的舊版資料。", { exact: true })).toHaveCount(0);
  await expect(footer.getByTestId('legacy-import-card')).toHaveCount(0);
  expect((await footer.boundingBox())!.y).toBeGreaterThan(
    (await row.boundingBox())!.y,
  );
  await board.getByRole("button", { name: /^連續看全部股票圖表/ }).click();
  await expect(
    board.getByRole("button", { name: "返回行情清單", exact: true }),
  ).toBeVisible();
});

test("quote failures name a useful public cause without exposing the response body", async ({
  page,
}) => {
  await mockSite(page, { failingSymbols: ["AAPL"] });
  await page.route("**/report/trending-stocks.json", (route) =>
    route.fulfill({
      json: {
        version: 1,
        source: "line",
        fetchedAt: "2026-11-03T10:00:00Z",
        sentAt: "2026-11-03T10:00:00Z",
        symbols: ["AAPL"],
      },
    }),
  );
  await page.goto("./");
  const row = page.locator(".wb-row").first();
  await expect(row).toContainText("載入失敗");
  await expect(row).toContainText("行情服務暫時無法使用");
  await expect(row).not.toContainText("upstream");
});
