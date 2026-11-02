import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page, type Locator } from "@playwright/test";
import { mockSite } from "./mocks";
import { mockDeviceAccount, accountDatabase } from "./account-fixture";
import { researchFixtureBody } from "./research-fixtures";
const root = resolve(import.meta.dirname, "../src/report/fixtures");
async function setup(page: Page) {
  await mockDeviceAccount(page);
  await mockSite(page);
  await page.route("**/my-stock-screener/report/research/**", (route) => {
    const path = new URL(route.request().url()).pathname.split(
      "/research/",
    )[1]!;
    if (!/^[a-z0-9/.-]+\.json$/i.test(path))
      return route.fulfill({ status: 400 });
    try {
      return route.fulfill({
        contentType: "application/json",
        body: researchFixtureBody(path),
      });
    } catch {
      return route.fulfill({ status: 404 });
    }
  });
  await page.route("**/my-stock-screener/report/themes*.json", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: readFileSync(
        resolve(
          root,
          new URL(route.request().url()).pathname.endsWith("themes-tw.json")
            ? "themes-tw.sample.json"
            : "themes.sample.json",
        ),
      ),
    }),
  );
}
async function savedCount(page: Page, symbol: string) {
  return page.evaluate(async ({ sym, database }) => {
    const request = indexedDB.open(database);
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const tx = db.transaction("symbols", "readonly");
    const req = tx.objectStore("symbols").get(sym);
    return new Promise<number>((resolve, reject) => {
      req.onsuccess = () => {
        resolve(req.result?.drawings.length ?? 0);
        db.close();
      };
      req.onerror = () => reject(req.error);
    });
  }, { sym: symbol, database: accountDatabase() });
}
async function horizontal(page: Page, chart: Locator) {
  await expect(chart.locator("canvas").first()).toBeVisible();
  await expect(chart.locator(".candle-ohlc")).toContainText("O ");
  await chart
    .getByLabel("繪圖工具", { exact: true })
    .selectOption("horizontal");
  await chart.getByTestId("research-candle-host").scrollIntoViewIfNeeded();
  const box = await chart.getByTestId("research-candle-host").boundingBox();
  const x = box!.x + box!.width * 0.5,
    y = box!.y + box!.height * 0.45;
  if (
    page.context().browser()?.browserType().name() === "chromium" &&
    page.viewportSize()!.width < 600
  ) {
    const touch = await page.context().newCDPSession(page);
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y, id: 1 }],
    });
    const loupe = chart.getByTestId("loupe");
    await expect(loupe).toBeVisible();
    await expect(loupe).toHaveCSS("position", "absolute");
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x: x + 20, y, id: 1 }],
    });
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await touch.detach();
  } else {
    await page.mouse.move(
      box!.x + box!.width * 0.5,
      box!.y + box!.height * 0.45,
    );
    await page.mouse.down();
    await page.mouse.move(
      box!.x + box!.width * 0.58,
      box!.y + box!.height * 0.45,
      { steps: 6 },
    );
    await page.mouse.up();
  }
}
test("commodity drawings persist across reload and stay isolated from FX and periods", async ({
  page,
}) => {
  await setup(page);
  await page.goto("./?page=world&view=commodities");
  await page.getByRole('button', { name: /連續看全部圖表/ }).click();
  const chart = page.locator('[data-chart-identity="world:CMD-WTI"]');
  await horizontal(page, chart);
  const symbol = await chart.getAttribute("data-drawing-symbol");
  await expect.poll(() => savedCount(page, symbol!)).toBe(1);
  await chart.getByRole("button", { name: "週K", exact: true }).click();
  await expect(chart.locator(".candle-ohlc")).toContainText("O ");
  await page.reload();
  await page.getByRole('button', { name: /連續看全部圖表/ }).click();
  await expect(
    page
      .locator('[data-chart-identity="world:CMD-WTI"]')
      .getByRole("button", { name: "週K", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => savedCount(page, symbol!)).toBe(1);
  await page.goto("./?page=world&view=fx");
  await page.getByRole('button', { name: /連續看全部圖表/ }).click();
  const fx = page.locator('[data-chart-identity="world:FX-USDTWD"]');
  await horizontal(page, fx);
  const fxSymbol = await fx.getAttribute("data-drawing-symbol");
  expect(fxSymbol).not.toBe(symbol);
  await expect.poll(() => savedCount(page, fxSymbol!)).toBe(1);
  await expect.poll(() => savedCount(page, symbol!)).toBe(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - innerWidth,
    ),
  ).toBeLessThanOrEqual(1);
});
test("theme chart supports drawings and changes symbol without leaking drawings or removing statistics", async ({
  page,
}) => {
  await setup(page);
  await page.goto("./?page=themes&theme=ai-chips");
  const sheet = page.getByRole("dialog").first();
  await expect(sheet.getByRole("table", { name: "各期間報酬" })).toBeVisible();
  const chart = sheet.locator(".drawable-candles");
  await horizontal(page, chart);
  const symbol = await chart.getAttribute("data-drawing-symbol");
  await expect.poll(() => savedCount(page, symbol!)).toBe(1);
  const selector = sheet.getByLabel("題材K線標的");
  const options = await selector
    .locator("option")
    .evaluateAll((nodes) => nodes.map((n) => (n as HTMLOptionElement).value));
  await selector.selectOption(options[1]!);
  const next = sheet.locator(".drawable-candles");
  await expect(next).not.toHaveAttribute("data-drawing-symbol", symbol!);
  await expect(next.locator(".candle-ohlc")).toContainText("O ");
  expect(
    await savedCount(page, (await next.getAttribute("data-drawing-symbol"))!),
  ).toBe(0);
  await selector.selectOption(options[0]!);
  await expect.poll(() => savedCount(page, symbol!)).toBe(1);
  await expect(sheet.getByRole("heading", { name: /成分股/ })).toBeVisible();
});
