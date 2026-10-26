// Serve the actual Pages build + repository report directory first; see docs/RESEARCH_ACCEPTANCE.md.
// No requests are mocked. Artifacts stay in ignored test-results/.
import { chromium, webkit, devices } from "playwright";
import { writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve(import.meta.dirname, "../test-results/research-pages-live");
await mkdir(out, { recursive: true });
const base =
  process.env.RESEARCH_PAGES_BASE ||
  "http://127.0.0.1:4188/my-stock-screener/atlas/";
const results = [];
for (const [name, type, settings] of [
  ["desktop-chromium", chromium, { viewport: { width: 1440, height: 900 } }],
  ["iphone-webkit", webkit, devices["iPhone 13"]],
]) {
  const browser = await type.launch();
  const context = await browser.newContext({
    ...settings,
    serviceWorkers: "block",
    locale: "zh-TW",
    timezoneId: "Asia/Taipei",
  });
  const page = await context.newPage();
  const errors = [];
  const research = [];
  const monthly = [];
  const indexVolume = [];
  const cryptoAssets = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.url().includes("/report/research/"))
      research.push({
        path: new URL(response.url()).pathname,
        status: response.status(),
        range: response.headers()["content-range"] ?? null,
        bytes: Number(response.headers()["content-length"] ?? 0),
      });
  });
  try {
    await page.goto(base + "?page=world&view=commodities");
    await page.getByRole("heading", { name: "WTI 原油" }).waitFor();
    await page.waitForFunction(() =>
      document
        .querySelector('[data-chart-identity="world:CMD-WTI"] .candle-ohlc')
        ?.textContent?.includes("O "),
    );
    await page.screenshot({
      path:
        out +
        "/" +
        (name === "iphone-webkit" ? "iphone-world.png" : "desktop-world.png"),
      fullPage: false,
    });
    const btc = page.locator('.world-candle-card').filter({ has: page.getByRole('heading', { name: 'BTC/USDT', exact: true }) });
    await btc.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.querySelector('[data-chart-identity="world:CRYPTO-BTCUSDT"] .candle-ohlc')?.textContent?.includes('O '));
    if (!(await btc.innerText()).includes('USDT/BTC')) throw new Error('BTC/USDT must retain the actual quote currency');
    cryptoAssets.push({ pair: 'BTC/USDT', source: await btc.locator('.source-note').first().innerText() });
    await btc.screenshot({ path: resolve(out, name + '-btcusdt.png') });
    await page.getByRole("button", { name: "匯率", exact: true }).click();
    await page.getByRole("heading", { name: "USD/TWD", exact: true }).waitFor();
    await page.waitForFunction(() =>
      document
        .querySelector('[data-chart-identity="world:FX-USDTWD"] .candle-ohlc')
        ?.textContent?.includes("O "),
    );
    for (const market of ["tw", "us"]) {
      await page.goto(base + "?page=markets&market=" + market);
      // Exercise the real generated report and the bundled historical record,
      // including the case where the producer has not yet published monthly data.
      await page.waitForFunction(() => {
        const plots = [...document.querySelectorAll(".breakout-card .bo-history-chart")];
        return plots.length === 3 && plots.every(plot =>
          Number(plot.getAttribute("data-point-count")) >= 4 &&
          (plot.getAttribute("aria-label")?.match(/\d{4}-\d{2}-\d{2} \d+(?:\.\d+)?%/g)?.length ?? 0) >= 4,
        );
      });
      const card = page.locator("section.breakout-card");
      if (/近一週|近1週/.test(await card.innerText()))
        throw new Error("Full-market success rates still expose the retired weekly view");
      monthly.push({ market, pointCounts: await card.locator(".bo-history-chart").evaluateAll(plots =>
        plots.map(plot => Number(plot.getAttribute("data-point-count"))),
      ) });
      if (market === "tw") {
        // The official TPEx daily series is available locally in the real Pages
        // tree; no live proxy or synthetic volume is needed for this assertion.
        const volume = page.getByRole("region", { name: "指數價量分析", exact: true });
        await volume.getByLabel("價量分析指數").selectOption("^TWOII");
        await page.waitForFunction(() => {
          const values = [...document.querySelectorAll(".index-volume-card .research-stats dd")];
          return values.length === 4 && values.some(value => /\d+\/\d+ = \d+(?:\.\d+)?%/.test(value.textContent));
        });
        if (/異常量|RVOL/.test(await volume.innerText()))
          throw new Error("Index volume analysis must contain directional ratios only");
        const rawResponse = await page.request.get(new URL("../report/series/%5ETWOII.json", base).href);
        if (!rawResponse.ok()) throw new Error("Official index daily series unavailable");
        const raw = await rawResponse.json();
        const latestDay = new Date(raw.bars.at(-1).time * 1000).toISOString().slice(0, 10);
        if (!(await volume.locator(".index-volume-date").innerText()).includes(latestDay))
          throw new Error("Index volume analysis did not retain the latest completed source date");
        indexVolume.push({ symbol: "^TWOII", asOf: latestDay, ratios: await volume.locator(".research-stats dd").allTextContents() });
      }
    }
    for (const symbol of ["2330.TW", "6488.TWO", "AAPL"]) {
      await page.goto(base + "?page=financials&symbol=" + symbol);
      const volume = page.locator("details.data-research").filter({
        has: page.locator(":scope > summary", { hasText: "價量分析" }),
      });
      await volume.locator(":scope > summary").click();
      await volume.getByText("上漲縮量比例", { exact: true }).waitFor();
      if (symbol === "2330.TW" && name === "iphone-webkit") {
        await volume.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: resolve(out, "iphone-stock.png"),
          fullPage: false,
        });
      }
      if (symbol !== "AAPL") {
        const flow = page.locator("details.data-research").filter({
          has: page.locator(":scope > summary", { hasText: "法人買賣超" }),
        });
        await flow.locator(":scope > summary").click();
        await flow
          .getByText("法人買超日價格區間（估計）", { exact: true })
          .waitFor();
      } else {
        const sec = page.locator("details.data-research").filter({
          has: page.locator(":scope > summary", { hasText: "SEC 8-K" }),
        });
        await sec.locator(":scope > summary").click();
        await sec
          .locator("summary", { hasText: /^查詢覆蓋與申報類型$/ })
          .click();
        await sec.getByText(/CIK 對應/).waitFor();
      }
    }
    const overflow = await page.evaluate(() =>
      Math.max(
        document.scrollingElement.scrollWidth - innerWidth,
        document.getElementById("main").scrollWidth -
          document.getElementById("main").clientWidth,
      ),
    );
    results.push({
      project: name,
      source: "Unmocked Pages build with actual committed report JSON",
      research,
      monthly,
      indexVolume,
      cryptoAssets,
      errors,
      horizontalOverflow: overflow,
      passed:
        errors.length === 0 &&
        overflow <= 1 &&
        research.length >= 5 &&
        research.every((r) => r.status === 200 || r.status === 206) &&
        research.some(
          (r) => r.status === 206 && r.bytes > 0 && r.bytes < 100000,
        ),
    });
  } catch (error) {
    await page
      .screenshot({
        path: resolve(out, name + "-failure.png"),
        fullPage: false,
      })
      .catch(() => {});
    await writeFile(
      resolve(out, name + "-failure.json"),
      JSON.stringify(
        { url: page.url(), message: String(error), errors, research },
        null,
        2,
      ) + "\n",
    );
    throw error;
  } finally {
    await browser.close();
  }
}
await writeFile(
  resolve(out, "pages-live-smoke.json"),
  JSON.stringify({ checkedAt: new Date().toISOString(), results }, null, 2) +
    "\n",
);
console.log(JSON.stringify(results));
if (results.some((r) => !r.passed)) process.exit(1);
