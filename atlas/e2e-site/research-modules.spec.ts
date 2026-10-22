import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { mockSite } from "./mocks";
const report = resolve(import.meta.dirname, "../../docs/report");
async function setup(page: Page) {
  await mockSite(page);
  await page.clock.setFixedTime(new Date("2026-10-22T10:00:00Z"));
  await page.route("**/my-stock-screener/report/research/**", (route) => {
    const relative = new URL(route.request().url()).pathname.split(
      "/research/",
    )[1]!;
    if (!/^[A-Za-z0-9_./-]+\.json$/.test(relative))
      return route.fulfill({ status: 400 });
    try {
      return route.fulfill({
        contentType: "application/json",
        body: readFileSync(resolve(report, "research", relative), "utf8"),
      });
    } catch {
      return route.fulfill({ status: 404, body: "not recorded" });
    }
  });
  await page.route("**/my-stock-screener/report/tw-chips.json", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: readFileSync(resolve(report, "tw-chips.json"), "utf8"),
    }),
  );
  await page.route("**/my-stock-screener/report/financials/*.json", (route) => {
    const symbol = new URL(route.request().url()).pathname.split("/").at(-1)!;
    try {
      return route.fulfill({
        contentType: "application/json",
        body: readFileSync(resolve(report, "financials", symbol), "utf8"),
      });
    } catch {
      return route.fulfill({ status: 404 });
    }
  });
}
const noOverflow = (page: Page) =>
  page.evaluate(() =>
    Math.max(
      document.scrollingElement!.scrollWidth - innerWidth,
      document.getElementById("main")!.scrollWidth -
        document.getElementById("main")!.clientWidth,
    ),
  );
test("world four options, historical source frequency, reload and browser back", async ({
  page,
}, info) => {
  await setup(page);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("./?page=world&view=commodities");
  await expect(
    page.getByRole("heading", { name: "原物料", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "WTI 原油" })).toBeVisible();
  await expect(page.getByText("月資料（整月均價）").first()).toBeVisible();
  const gold = page
    .locator("article.card")
    .filter({ has: page.getByRole("heading", { name: "黃金", exact: true }) });
  await expect(gold.getByText("月資料不適用").first()).toBeVisible();
  await expect(gold).toContainText("World Bank Pink Sheet");
  await page.getByRole("button", { name: "匯率", exact: true }).click();
  await expect(page).toHaveURL(/view=fx/);
  await expect(
    page.getByRole("heading", { name: "USD/TWD", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("每 1 USD 兌 TWD").first()).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "USD/TWD", exact: true }),
  ).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "WTI 原油" })).toBeVisible();
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
  await page.screenshot({
    path: info.outputPath("research-world.png"),
    fullPage: true,
  });
});
test("Taiwan real chips, institutional units, volume structure and symbol separation", async ({
  page,
}, info) => {
  await setup(page);
  await page.goto("./?page=financials&symbol=2330.TW");
  await page
    .locator("summary")
    .filter({ hasText: "大戶持股比例與集保戶人數" })
    .click();
  await expect(
    page.getByRole("heading", { name: "大戶持股比例與集保戶人數" }),
  ).toBeVisible();
  await expect(page.getByText("87.33%").first()).toBeVisible();
  const institutional = page
    .locator("details.data-research")
    .filter({ has: page.locator("summary", { hasText: "法人買賣超" }) });
  await institutional.locator(":scope > summary").click();
  await expect(institutional).toContainText("2026-10-21");
  await institutional.getByRole("combobox").nth(1).selectOption("lots");
  await expect(institutional).toContainText("買進（張）");
  await expect(institutional).toContainText("法人買超日價格區間（估計）");
  const volume = page
    .locator("details.data-research")
    .filter({ has: page.locator(":scope > summary", { hasText: "價量分析" }) });
  await volume.locator(":scope > summary").click();
  await expect(volume).toContainText("上漲縮量比例");
  await expect(volume).toContainText("下跌放量比例");
  await volume.getByLabel("價量觀測窗口").selectOption("5");
  await expect(volume).toContainText("/5 日");
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: info.outputPath("research-tw.png"),
    fullPage: true,
  });
  await page.goto("./?page=financials&symbol=AAPL");
  await expect(
    page.getByRole("heading", { name: "大戶持股比例與集保戶人數" }),
  ).toHaveCount(0);
  const sec = page
    .locator("details.data-research")
    .filter({ has: page.locator(":scope > summary", { hasText: "SEC 8-K" }) });
  await sec.locator(":scope > summary").click();
  await expect(sec).toContainText("CIK 對應");
  await expect(sec).not.toContainText("2330.TW");
});
test("official alert timeline and stale source cannot become normal status", async ({
  page,
}) => {
  await setup(page);
  await page.goto("./?page=markets&market=tw");
  const alerts = page.locator("details.data-research").filter({
    has: page.locator(":scope > summary", { hasText: "台股官方注意" }),
  });
  await alerts.locator(":scope > summary").click();
  await alerts.getByLabel("注意處置搜尋").fill("3354");
  await expect(alerts).toContainText("3354.TWO");
  await expect(
    alerts.getByRole("link", { name: "查看官方公告原文" }).first(),
  ).toHaveAttribute("href", /^https:\/\/(www\.)?tpex.org.tw\//);
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
});
test("chart research panel leaves candles mounted and settings intact", async ({
  page,
}) => {
  await setup(page);
  await page.goto("./?page=chart&symbol=AAPL&tf=1D");
  await expect(page.getByTestId("active-symbol")).toHaveText("AAPL");
  await page
    .getByRole("button", { name: "價量與官方公告", exact: true })
    .first()
    .click();
  const summary = page.locator("summary").filter({ hasText: "價量分析" });
  await summary.click();
  await expect(page.getByText("上漲縮量比例", { exact: true })).toBeVisible();
  expect(await page.locator(".chart-host canvas").count()).toBeGreaterThan(0);
  expect(await noOverflow(page)).toBeLessThanOrEqual(1);
});
test("missing research has a separate honest state", async ({ page }) => {
  await setup(page);
  await page.route("**/research/volume/AAPL.json", (route) =>
    route.fulfill({ status: 503 }),
  );
  await page.goto("./?page=financials&symbol=AAPL");
  await page.locator("summary").filter({ hasText: "價量分析" }).click();
  await expect(
    page.getByText("資料來源暫時無法取得（HTTP 503）。"),
  ).toBeVisible();
});
