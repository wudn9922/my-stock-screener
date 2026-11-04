import { expect, test } from "@playwright/test";
import { mockSite, yahooChart } from "./mocks";
const at = Date.parse("2026-11-03T16:02:00Z") / 1000;

test("ANT and Claude find the IPO record, open details and never open a fictitious stock chart", async ({
  page,
}) => {
  await mockSite(page);
  await page.route("**/report/ipo-watch.json", (route) =>
    route.fulfill({ status: 404, body: "missing" }),
  );
  await page.goto("./");
  const mobile = await page.locator(".site-tabbar").isVisible();
  if (mobile)
    await page.getByRole("button", { name: "搜尋股票", exact: true }).click();
  const input = page.getByRole("combobox", { name: "搜尋股票", exact: true });
  await input.fill("ANT");
  await page.getByRole("option", { name: /ANT.*Anthropic.*IPO/ }).click();
  const details = page.getByRole("region", { name: "IPO 公司資料" });
  await expect(details).toContainText("尚未公布");
  await expect(details).toContainText("申請上市");
  await expect(details.getByRole("link", { name: /官方申報/ })).toHaveAttribute(
    "href",
    /www.sec.gov/,
  );
  await expect(page.locator(".site")).toHaveAttribute("data-page", "watchlist");
  await details.getByRole("button", { name: "返回搜尋" }).click();
  await input.fill("Claude");
  await expect(
    page.getByRole("option", { name: /ANT.*Anthropic/ }),
  ).toBeVisible();
});

test("stock volume view compares completed 5m sessions, while extended quotes keep regular close separate", async ({
  page,
}) => {
  await mockSite(page);
  await page.addInitScript((now) => {
    Date.now = () => now;
  }, at * 1000);
  await page.route(
    "https://edge.test/functions/v1/market-chart**",
    async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.has("quotes"))
        return route.fulfill({
          json: { version: 1, quotes: [], errors: [], fetchedAt: at },
        });
      const symbol = url.searchParams.get("symbol") ?? "AAPL",
        interval = url.searchParams.get("interval") ?? "1d";
      const raw = yahooChart(symbol, interval);
      if (interval === "5m") {
        const times = [
          ...Array.from(
            { length: 78 },
            (_, i) => Date.parse("2026-11-02T14:30Z") / 1000 + i * 300,
          ),
          ...Array.from(
            { length: 19 },
            (_, i) => Date.parse("2026-11-03T14:30Z") / 1000 + i * 300,
          ),
        ];
        const result = raw.chart.result[0]!;
        result.timestamp = times;
        result.indicators.quote = [
          {
            open: times.map(() => 100),
            high: times.map(() => 100),
            low: times.map(() => 100),
            close: times.map(() => 100),
            volume: times.map((_, i) => (i < 78 ? 100 : 200)),
          },
        ];
      }
      return route.fulfill({ json: raw });
    },
  );
  await page.route("**/report/extended-quotes/AAPL.json", (route) =>
    route.fulfill({
      json: {
        version: 1,
        symbol: "AAPL",
        checkedAt: "2026-11-03T14:00Z",
        source: "Nasdaq 公開快照（非即時）",
        pre: { status: "ok", price: 123.45, asOf: "2026-11-03T14:20:00Z" },
        post: {
          status: "unavailable",
          price: null,
          asOf: null,
          reason: "來源未提供此時段成交報價",
        },
      },
    }),
  );
  await page.goto("./?page=chart&symbol=AAPL");
  await expect(page.getByTestId("active-symbol")).toHaveText("AAPL");
  await expect(page.getByTestId("header-quote").locator("b")).not.toHaveText(
    "—",
  );
  const regular = await page.getByTestId("header-quote").innerText();
  await page.getByTestId("extended-quotes-toggle").click();
  const quotes = page.getByRole("region", { name: "盤前盤後報價" });
  await expect(quotes).toContainText("123.45 USD");
  await expect(quotes).toContainText("非即時");
  await expect(quotes).toContainText("來源未提供此時段成交報價");
  expect(await page.getByTestId("header-quote").innerText()).toBe(regular);
  await page.getByTestId("extended-quotes-toggle").click();
  await page.getByRole("button", { name: "價量", exact: true }).click();
  const volume = page.getByRole("region", { name: "前一交易日同時間量比" });
  await expect(volume).toContainText("2.00 倍");
  await expect(volume).toContainText("累計至 11:00");
  await expect(volume).toContainText("2026-11-03／2026-11-02");
});

test("missing extended coverage and missing intraday history explain the cause", async ({
  page,
}) => {
  await mockSite(page);
  await page.route("https://edge.test/functions/v1/market-chart**", (route) =>
    new URL(route.request().url()).searchParams.get("interval") === "5m"
      ? route.fulfill({ status: 503, json: { error: "upstream" } })
      : route.fallback(),
  );
  await page.route("**/report/extended-quotes/NVDA.json", (route) =>
    route.fulfill({ status: 404, body: "missing" }),
  );
  await page.goto("./?page=chart&symbol=NVDA");
  await expect(page.getByTestId("active-symbol")).toHaveText("NVDA");
  await expect(page.getByTestId("ohlc-header")).toContainText("O ");
  await expect(page.locator(".chart-loading")).toHaveCount(0);
  await page.getByTestId("extended-quotes-toggle").click();
  await expect(
    page.getByRole("region", { name: "盤前盤後報價" }),
  ).toContainText("此股票尚未收錄延長時段快照");
  await expect(
    page
      .getByRole("region", { name: "盤前盤後報價" })
      .getByRole("link", { name: "查看官方盤前頁面" }),
  ).toHaveAttribute("href", /nasdaq.com.*nvda\/pre-market/);
  await page.getByTestId("extended-quotes-toggle").click();
  await page.getByRole("button", { name: "價量", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "前一交易日同時間量比" }),
  ).toContainText("無法計算");
});

test('fast Enter on ANT opens filing details while the public IPO update is still pending', async ({ page }) => {
  await mockSite(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/report/ipo-watch.json', async route => { await pending; await route.fulfill({ status: 404, body: 'missing' }).catch(() => undefined); });
  await page.goto('./');
  if (await page.locator('.site-tabbar').isVisible()) await page.getByRole('button', { name: '搜尋股票', exact: true }).click();
  try {
    await page.getByRole('combobox', { name: '搜尋股票', exact: true }).fill('ANT');
    await page.getByRole('combobox', { name: '搜尋股票', exact: true }).press('Enter');
    await expect(page.getByRole('region', { name: 'IPO 公司資料' })).toContainText('Anthropic');
    await expect(page.locator('.site')).toHaveAttribute('data-page', 'watchlist');
  } finally { release(); }
});
