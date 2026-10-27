import { expect, test } from '@playwright/test';
import { buildLargeUniverse } from './largeUniverse';
import { buildReport, mockSite } from './mocks';

/**
 * Full-market lists at real size (scope "full": ~1,850 Taiwan and 6,000 US rows, synthetic): the range,
 * count and exclusions are shown, the list stays virtualized and filtering / sorting / scrolling stay quick.
 * Timings are printed for the record (headless Chromium, not a device measurement).
 */
const shotDir = process.env.SCREENSHOT_DIR;

test('US full-market list: scope, count and exclusions are shown; 6,000 rows stay quick', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), universe: buildLargeUniverse() });
  const start = Date.now();
  await page.goto('./?page=screener&group=universe-us');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const firstPaint = Date.now() - start;

  const meta = page.locator('.screener-meta');
  await expect(meta).toContainText('6,000 檔');
  await expect(meta).toContainText('美股 NYSE／NASDAQ／NYSE American 等交易所普通股與 ADR（6,120 檔）');
  const note = page.locator('.screener-note');
  await expect(note).toHaveAttribute('aria-expanded', 'false');
  await note.click();
  await expect(note).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('.screener-note')).toContainText('排除近 5 個交易日無 K 線');
  await expect(page.locator('.screener-note')).toContainText('Nasdaq 股票篩選器');
  await expect(page.locator('.result-count')).toHaveText('6000 檔');

  // Virtualized: only the rows on screen (plus overscan) are in the DOM.
  const rendered = await page.locator('.screener-row').count();
  expect(rendered).toBeLessThan(80);

  // Sort by RS percentile.
  let mark = Date.now();
  await page.getByRole('combobox', { name: '排序' }).selectOption({ label: 'RS 百分位（強→弱）' });
  await expect(page.locator('.screener-row').first()).toContainText('RS 99');
  const sortMs = Date.now() - mark;

  // Scroll to the end of the list and back: rows keep rendering.
  const list = page.locator('.screener-list');
  mark = Date.now();
  await list.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
  await expect(page.locator('.screener-row').last()).toBeVisible();
  const scrollMs = Date.now() - mark;
  await list.evaluate((element) => element.scrollTo({ top: 0 }));

  // Search inside the list.
  mark = Date.now();
  await page.getByRole('searchbox', { name: '在群組內篩選' }).fill('Company 5999');
  await expect(page.locator('.screener-row').first()).toContainText('Company 5999');
  const searchMs = Date.now() - mark;

  // A filter from the link: RS ≥ 70 and close above MA50 / MA200.
  mark = Date.now();
  await page.goto('./?page=screener&group=universe-us&f=rs:70;tr:a50,a200');
  await expect(page.locator('.active-filters-count')).toContainText(/符合 [\d,]+ \/ 6,000/);
  const filterMs = Date.now() - mark;

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  console.info(
    `[${info.project.name}] first rows ${firstPaint} ms (navigation + 1.4 MB JSON), sort ${sortMs} ms, ` +
      `scroll to end ${scrollMs} ms, filter link ${filterMs} ms (navigation), search ${searchMs} ms, ${rendered} rows in DOM`,
  );
  if (shotDir) await page.screenshot({ path: `${shotDir}/universe-us-${info.project.name}.png` });
});

test('Taiwan full-market list shows the official scope and the note without overflowing', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), universe: buildLargeUniverse() });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await expect(page.locator('.screener-meta')).toContainText('1,850 檔');
  await expect(page.locator('.screener-meta')).toContainText('台股上市＋上櫃普通股（上市 1,060、上櫃 830 檔）');
  const note = page.locator('.screener-note');
  await expect(note).toHaveAttribute('aria-expanded', 'false');
  await note.click();
  await expect(note).toHaveAttribute('aria-expanded', 'true');
  await expect(note).toContainText('不含 ETF、TDR、特別股、權證與興櫃');
  const chip = page.getByRole('button', { name: /台股全市場篩選/ });
  await expect(chip).toContainText('1850');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (shotDir) await page.screenshot({ path: `${shotDir}/universe-tw-${info.project.name}.png` });
});
