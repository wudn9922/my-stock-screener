import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite, screenerFixtures } from './mocks';

/** Fundamental (EPS / revenue growth) and relative-strength filters on the full-market lists. */

// Set SCREENSHOT_DIR to keep a picture of the sheet (manual visual checks, not used by CI).
const shotDir = process.env.SCREENSHOT_DIR;

const fixtureGrowth = screenerFixtures.growth() as { rows: unknown[][] };
const twUniverse = (screenerFixtures.universe() as { markets: { TW: { rows: unknown[][] } } }).markets.TW.rows.map((row) => String(row[0]));
/** Taiwan fixture stocks whose latest `years` fiscal years all grew at least `min` % (the fixture has no annual turnarounds). */
const annualPassers = (years: number, min: number) =>
  fixtureGrowth.rows.filter((row) => {
    const values = row[7] as (number | null)[];
    return (
      twUniverse.includes(String(row[0])) &&
      Array.from({ length: years }, (_, slot) => values[slot]).every((value) => value !== null && value !== undefined && value >= min)
    );
  });

const openSheet = async (page: Page) => {
  await page.getByRole('button', { name: /篩選條件/ }).click();
  return page.getByRole('dialog', { name: '篩選條件' });
};

test('Taiwan full-market list: fundamental filters are open, annual EPS growth filters and the link round-trips', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  const fundamentals = sheet.getByRole('region', { name: '基本面（EPS／營收成長）' });
  await fundamentals.scrollIntoViewIfNeeded();

  // Not blocked any more: the old 「尚未提供」 explanation is gone, every condition is there.
  await expect(sheet).not.toContainText('尚未提供');
  for (const name of ['季度 EPS 成長', '年度 EPS 成長', '季度營收成長', '年度營收成長'])
    await expect(fundamentals.getByRole('group', { name, exact: true })).toBeVisible();
  await expect(fundamentals.getByRole('switch', { name: 'EPS 成長加速' })).toBeVisible();
  await expect(fundamentals.getByRole('switch', { name: '含虧轉盈' })).toBeChecked();
  await expect(fundamentals.getByRole('switch', { name: '資料不足時排除' })).toBeChecked();
  await expect(fundamentals).toContainText('台股財報歷史較短');

  // Coverage is stated per condition instead of pretending to be complete.
  const annualEps = fundamentals.getByRole('group', { name: '年度 EPS 成長', exact: true });
  await expect(annualEps).toContainText(`財報資料涵蓋`);
  await expect(annualEps).toContainText(`/ ${twUniverse.length} 檔`);

  // Pick 近 3 年皆 ≥10%: the note turns into the full warning about who gets excluded.
  await annualEps.getByRole('group', { name: '年度 EPS 成長期數' }).getByRole('button', { name: '近 3 年皆' }).click();
  await annualEps.getByRole('group', { name: '年度 EPS 成長幅度' }).getByRole('button', { name: '≥10%', exact: true }).click();
  await expect(annualEps).toContainText('此條件僅涵蓋');
  await expect(annualEps).toContainText('有足夠財報歷史（需有近 3 年 EPS YoY）');
  await expect(annualEps).toContainText('其餘');
  await expect(annualEps).toContainText('會被排除（可到下方關閉「資料不足時排除」保留）');
  if (shotDir) await page.screenshot({ path: `${shotDir}/fundamentals-sheet-${info.project.name}.png` });
  const expected = annualPassers(3, 10);
  expect(expected.length).toBeGreaterThan(0);
  expect(expected.length).toBeLessThan(twUniverse.length);
  await sheet.getByRole('button', { name: /^套用/ }).click();

  await expect(page).toHaveURL(/f=epsy(%3A|:)3(%3A|:)10/);
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('EPS 年增 近3年皆≥10%');
  await expect(chips.locator('.active-filters-count')).toContainText(`符合 ${expected.length} / ${twUniverse.length}`);
  // Each shown row carries its annual EPS number (and a tooltip with the history).
  await expect(page.locator('.screener-row .metric-pill').first()).toContainText('EPS年');
  await expect(page.locator('.screener-row .metric-pill').first()).toHaveAttribute('title', /年度（最新→較舊）/);
  if (shotDir) await page.screenshot({ path: `${shotDir}/fundamentals-result-${info.project.name}.png` });

  // The link reproduces the same list and the sheet shows the applied state again.
  await page.goto(page.url());
  await expect(page.getByRole('group', { name: '已套用的篩選條件' }).locator('.active-filters-count')).toContainText(
    `符合 ${expected.length} / ${twUniverse.length}`,
  );
  const again = await openSheet(page);
  const group = again.getByRole('group', { name: '年度 EPS 成長', exact: true });
  await expect(group.getByRole('button', { name: '近 3 年皆' })).toHaveAttribute('aria-pressed', 'true');
  await expect(group.getByRole('button', { name: '≥10%', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('acceleration, turnaround and keep-missing switches go into the link; junk tokens are ignored', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw&f=zzz:1;epsy:9:9;acc:1;eps:1:25;tn:0;nd:keep');
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('EPS 成長加速');
  await expect(chips).toContainText('不含虧轉盈');
  await expect(chips).not.toContainText('年增');
  const sheet = await openSheet(page);
  await expect(sheet.getByRole('switch', { name: 'EPS 成長加速' })).toBeChecked();
  await expect(sheet.getByRole('switch', { name: '含虧轉盈' })).not.toBeChecked();
  await expect(sheet.getByRole('switch', { name: '資料不足時排除' })).not.toBeChecked();
  // Turn the turnaround switch and the exclusion back on, then apply: the link is canonical.
  // (The native checkbox is visually hidden behind its track: click the label like a user does.)
  const labelOf = (name: string) => sheet.locator('label.fs-switch').filter({ has: page.getByRole('switch', { name }) });
  await labelOf('含虧轉盈').click();
  await labelOf('資料不足時排除').click();
  await expect(sheet.getByRole('switch', { name: '含虧轉盈' })).toBeChecked();
  await expect(sheet.getByRole('switch', { name: '資料不足時排除' })).toBeChecked();
  await sheet.getByRole('button', { name: /^套用/ }).click();
  await expect(page).toHaveURL(/f=eps(%3A|:)1(%3A|:)25(%3B|;)acc(%3A|:)1$/);
  await expect(chips).not.toContainText('不含虧轉盈');
});

test('rows of a filtered Taiwan list show quarterly EPS growth and a turnaround', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw&f=eps:1:25');
  const tsmc = page.locator('.screener-row', { hasText: '2330' });
  await expect(tsmc.locator('.metric-pill').first()).toHaveText('EPS +30%');
  await expect(tsmc.locator('.metric-pill').first()).toHaveAttribute(
    'title',
    /季度（最新→較舊）\+30% \/ \+20%.*年度（最新→較舊）\+25% \/ \+15% \/ \+10%/,
  );
  // A loss-to-profit quarter has no YoY number but passes (the switch is on by default).
  await expect(page.locator('.screener-row', { hasText: '1301' }).locator('.metric-pill').first()).toHaveText('EPS 轉盈');
  await page.goto('./?page=screener&group=universe-tw&f=eps:1:25;tn:0');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await expect(page.locator('.screener-row', { hasText: '1301' })).toHaveCount(0);
});

test('1-month and 3-month strength against the index, with the definitions spelled out', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  const rs = sheet.getByRole('region', { name: '相對強弱（對大盤指數）' });
  await expect(rs).toContainText('加權指數（^TWII）');
  await expect(rs).toContainText('在台股「全市場篩選」名單中的排名');
  await expect(rs).toContainText('(1＋個股報酬)÷(1＋大盤報酬)−1');
  await rs.getByRole('button', { name: '1個月強於大盤' }).click();
  await rs.getByRole('button', { name: '3個月強於大盤' }).click();
  await sheet.getByRole('button', { name: /^套用/ }).click();
  await expect(page).toHaveURL(/f=rs3(%3A|:)0(%3B|;)rs1(%3A|:)0/);
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('1月強於大盤');
  await expect(chips).toContainText('3月強於大盤');
});

test('US full-market list: Yahoo quarters first, the financials file fills the rest', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-us&f=eps:1:40');
  const search = page.getByRole('searchbox', { name: '在群組內篩選' });
  // RIVN has no Yahoo record: its +45% EPS comes from growth.json
  await search.fill('RIVN');
  const rivian = page.locator('.screener-row', { hasText: 'RIVN' });
  await expect(rivian.locator('.metric-pill').first()).toHaveText('EPS +45%');
  await expect(rivian.locator('.metric-pill').first()).toHaveAttribute('title', /季度 財報檔/);
  // AAPL's financials row is a -99% placeholder; the Yahoo number (not ≥ +40%) decides
  await search.fill('AAPL');
  await expect(page.locator('.screener-row', { hasText: 'AAPL' })).toHaveCount(0);
  await search.fill('');
  const sheet = await openSheet(page);
  await expect(sheet.getByRole('region', { name: '相對強弱（對大盤指數）' })).toContainText('S&P 500（SPY）');
  const quarterly = sheet.getByRole('group', { name: '季度 EPS 成長', exact: true });
  await expect(quarterly).toContainText('此條件僅涵蓋');
  await expect(sheet.getByRole('region', { name: '基本面（EPS／營收成長）' })).toContainText('季度 Yahoo Finance（S&P 500）優先');
  await expect(sheet.getByRole('region', { name: '基本面（EPS／營收成長）' })).not.toContainText('台股財報歷史較短');
});

test('a missing growth file blocks only the fundamentals block, the list keeps working', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: { growth: 'missing' } });
  await page.goto('./?page=screener&group=universe-tw&f=epsy:2:10');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  await expect(sheet.getByRole('region', { name: '基本面（EPS／營收成長）' })).toContainText('尚未產生或暫時無法取得');
  await expect(sheet.getByRole('region', { name: '量比' })).toBeVisible();
});

test('the filter sheet fits the screen: no sideways overflow in the fundamentals block', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw&f=eps:2:20;epsy:3:10;acc:1');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  const fundamentals = sheet.getByRole('region', { name: '基本面（EPS／營收成長）' });
  await fundamentals.scrollIntoViewIfNeeded();
  await expect(fundamentals.getByRole('group', { name: '季度 EPS 成長', exact: true })).toContainText('此條件僅涵蓋');
  const overflow = await sheet.evaluate((element) => {
    const body = element.querySelector('.fs-body') as HTMLElement;
    const right = element.getBoundingClientRect().right + 0.5;
    const wide = [...element.querySelectorAll<HTMLElement>('.fs-body *')].filter((node) => node.getBoundingClientRect().right > right).length;
    return { sheet: element.scrollWidth - element.clientWidth, body: body.scrollWidth - body.clientWidth, wide };
  });
  expect(overflow).toEqual({ sheet: 0, body: 0, wide: 0 });
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  if (shotDir) {
    await page.screenshot({ path: `${shotDir}/fundamentals-fit-${info.project.name}.png` });
    await fundamentals.getByRole('group', { name: '成長進階選項' }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shotDir}/fundamentals-fit-bottom-${info.project.name}.png` });
  }
});
