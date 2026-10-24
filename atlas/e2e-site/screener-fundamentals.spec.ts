import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite, screenerFixtures } from './mocks';

/** Fundamental (EPS / revenue growth) and relative-strength filters on the full-market lists. */

// Set SCREENSHOT_DIR to keep a picture of the sheet (manual visual checks, not used by CI).
const shotDir = process.env.SCREENSHOT_DIR;

const fixtureGrowth = screenerFixtures.growth() as { rows: unknown[][] };
const twUniverse = screenerFixtures.universe().markets.TW!.rows.map((row) => String(row[0]));
/**
 * Taiwan fixture stocks that pass an annual gate: of the latest `years` fiscal years, the ones with data (at least
 * ceil(years / 2) of them) must have growth ≥ `min` % in at least `share` % of the cases (a turnaround counts as growth).
 */
const annualPassers = (years: number, min: number, share = 100) =>
  fixtureGrowth.rows.filter((row) => {
    if (!twUniverse.includes(String(row[0]))) return false;
    const values = row[7] as (number | null)[];
    const turns = row[10] as number;
    let grown = 0;
    let measured = 0;
    for (let slot = 0; slot < years; slot++) {
      const value = values[slot] ?? null;
      if (value === null) {
        if (((turns >> slot) & 1) === 1) {
          grown += 1;
          measured += 1;
        }
        continue;
      }
      measured += 1;
      if (value >= min) grown += 1;
    }
    return measured >= Math.ceil(years / 2) && grown * 100 >= share * measured;
  });

/** Taiwan fixture stocks whose latest `years` fiscal years all grew (a turnaround counts), as the EPS streak counts them. */
const streakPassers = (years: number) =>
  fixtureGrowth.rows.filter((row) => {
    if (!twUniverse.includes(String(row[0]))) return false;
    const values = row[7] as (number | null)[];
    const turns = row[10] as number;
    return Array.from({ length: years }, (_, slot) => slot).every((slot) =>
      values[slot] !== null && values[slot] !== undefined ? (values[slot] as number) > 0 : ((turns >> slot) & 1) === 1,
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
  for (const name of ['季度 EPS 成長', '年度 EPS 成長']) await expect(fundamentals.getByRole('group', { name, exact: true })).toBeVisible();
  // 「營收」 is a closed disclosure until it is opened
  const revenue = fundamentals.getByRole('button', { name: /^營收/ });
  await expect(revenue).toHaveAttribute('aria-expanded', 'false');
  await revenue.click();
  await expect(revenue).toHaveAttribute('aria-expanded', 'true');
  for (const name of ['季度營收成長', '年度營收成長']) await expect(fundamentals.getByRole('group', { name, exact: true })).toBeVisible();
  await expect(fundamentals.getByRole('switch', { name: 'EPS 成長加速' })).toBeVisible();
  await expect(fundamentals.getByRole('switch', { name: '含虧轉盈' })).toBeChecked();
  await expect(fundamentals.getByRole('switch', { name: '資料不足時排除' })).toBeChecked();
  // The source and how Taiwan quarters are made are spelled out (official statements, year-to-date differences)
  await expect(fundamentals).toContainText('年度累計數相減');
  await expect(fundamentals).toContainText('第 4 季＝全年－前三季');
  await expect(fundamentals).toContainText('公開資訊觀測站／證交所・櫃買中心 OpenAPI 綜合損益表');

  // Coverage is stated per condition instead of pretending to be complete.
  const annualEps = fundamentals.getByRole('group', { name: '年度 EPS 成長', exact: true });
  await expect(annualEps).toContainText(`財報資料涵蓋`);
  await expect(annualEps).toContainText(`/ ${twUniverse.length} 檔`);

  // Pick 近 3 年 with ≥10%: the note turns into the full warning about who gets excluded.
  await annualEps.getByRole('group', { name: '年度 EPS 成長 · 期數' }).getByRole('button', { name: '近 3 年', exact: true }).click();
  await annualEps.getByRole('group', { name: '年度 EPS 成長 · 幅度' }).getByRole('button', { name: '≥10%', exact: true }).click();
  await expect(annualEps).toContainText('此條件僅涵蓋');
  await expect(annualEps).toContainText('有足夠財報歷史（近 3 年中至少 2 年的 EPS YoY）');
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
  await expect(group.getByRole('button', { name: '近 3 年', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(group.getByRole('button', { name: '≥10%', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('acceleration, turnaround and keep-missing switches go into the link; junk tokens are ignored', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw&f=zzz:1;epsy:11:9;epss:0;acc:1;eps:1:25;tn:0;nd:keep');
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
  await expect(tsmc.locator('.metric-pill').first()).toHaveText('EPS +30% 成1／衰0');
  await expect(tsmc.locator('.metric-pill').first()).toHaveAttribute(
    'title',
    /季度（最新→較舊）\+30% \/ \+20%.*年度（最新→較舊）\+25% \/ \+15% \/ \+10%/,
  );
  // A loss-to-profit quarter has no YoY number but passes (the switch is on by default).
  await expect(page.locator('.screener-row', { hasText: '1301' }).locator('.metric-pill').first()).toHaveText('EPS 轉盈 成1／衰0');
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

test('US full-market list: growth.json first, the Yahoo quarters only where they are newer', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-us&f=eps:1:40');
  const search = page.getByRole('searchbox', { name: '在群組內篩選' });
  // RIVN has no Yahoo record: its +45% EPS comes from growth.json (SEC XBRL frames, a derived Q4 in the latest pair)
  await search.fill('RIVN');
  const rivian = page.locator('.screener-row', { hasText: 'RIVN' });
  await expect(rivian.locator('.metric-pill').first()).toHaveText('EPS +45% 成1／衰0');
  await expect(rivian.locator('.metric-pill').first()).toHaveAttribute('title', /來源：季度 SEC XBRL frames、年度 SEC XBRL frames/);
  await expect(rivian.locator('.metric-pill').first()).toHaveAttribute('title', /含以全年減前三季推算的第 4 季/);
  // AAPL's growth.json quarter is a report behind (-99% placeholder); the newer Yahoo number (not ≥ +40%) decides
  await search.fill('AAPL');
  await expect(page.locator('.screener-row', { hasText: 'AAPL' })).toHaveCount(0);
  // MSFT: the same quarter in both files: growth.json's +50% (SEC) wins over Yahoo's +6%
  await search.fill('MSFT');
  const msft = page.locator('.screener-row', { hasText: 'MSFT' });
  await expect(msft.locator('.metric-pill').first()).toHaveText('EPS +50% 成1／衰0');
  await expect(msft.locator('.metric-pill').first()).toHaveAttribute('title', /來源：季度 SEC 財報（companyfacts）/);
  await search.fill('');
  const sheet = await openSheet(page);
  await expect(sheet.getByRole('region', { name: '相對強弱（對大盤指數）' })).toContainText('S&P 500（SPY）');
  const quarterly = sheet.getByRole('group', { name: '季度 EPS 成長', exact: true });
  await expect(quarterly).toContainText('此條件僅涵蓋');
  const fundamentals = sheet.getByRole('region', { name: '基本面（EPS／營收成長）' });
  await expect(fundamentals).toContainText('SEC 財報（companyfacts）與 SEC XBRL frames（全市場）');
  await expect(fundamentals).toContainText('外國發行人（20-F）多只有年度資料');
  await expect(fundamentals).not.toContainText('年度累計數相減');
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
  await page.goto('./?page=screener&group=universe-tw&f=eps:2:20;epsy:3:10;epss:2;acc:1');
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

test('annual windows: the periods a group cannot cover are disabled, and the streak options follow the data', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  const annualEps = sheet.getByRole('group', { name: '年度 EPS 成長', exact: true });
  await annualEps.scrollIntoViewIfNeeded();
  const periods = annualEps.getByRole('group', { name: '年度 EPS 成長 · 期數' });
  // Taiwan's annual history in the fixture is three years: 1 / 2 / 3 can be picked, 5 and 10 cannot
  for (const name of ['最近 1 年', '近 2 年', '近 3 年', '近 5 年']) await expect(periods.getByRole('button', { name, exact: true })).toBeEnabled();
  for (const name of ['近 10 年']) {
    await expect(periods.getByRole('button', { name })).toBeDisabled();
    await expect(periods.getByRole('button', { name })).toHaveAttribute('title', /沒有足夠/);
  }
  await sheet.getByRole('button', { name: /^進階：嚴格連續成長/ }).click();
  const streak = sheet.getByRole('group', { name: 'EPS 連續成長年數' });
  await streak.scrollIntoViewIfNeeded();
  await expect(streak.getByRole('button', { name: '≥10 年' })).toBeDisabled();
  await expect(streak).toContainText('各年數涵蓋檔數');
  await expect(streak).toContainText('從最新年度往回數');
});

test('ten years of annual EPS growth: the 10-year window applies from the link and in the sheet', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  // The fixture has three annual years per stock: give the first five Taiwan rows ten years, all at +20%
  const growth = JSON.parse(JSON.stringify(screenerFixtures.growth())) as { rows: unknown[][] };
  const longRows = growth.rows.filter((row) => twUniverse.includes(String(row[0]))).slice(0, 5);
  for (const row of longRows) {
    row[7] = Array.from({ length: 10 }, () => 20);
    row[8] = Array.from({ length: 10 }, () => 20);
    row[10] = 0;
  }
  await page.route('**/my-stock-screener/report/growth.json', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(growth) }),
  );
  await page.goto('./?page=screener&group=universe-tw&f=epsy:10:10');
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('EPS 年增 近10年皆≥10%');
  await expect(chips.locator('.active-filters-count')).toContainText(`符合 ${longRows.length} / ${twUniverse.length}`);
  await expect(page.locator('.screener-row .metric-pill').first()).toHaveAttribute('title', /年度（最新→較舊）\+20% \/ \+20% \/ \+20%/);
  const sheet = await openSheet(page);
  const periods = sheet.getByRole('group', { name: '年度 EPS 成長 · 期數' }).first();
  await expect(periods.getByRole('button', { name: '近 10 年', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(periods.getByRole('button', { name: '近 10 年', exact: true })).toBeEnabled();
  await expect(periods.getByRole('button', { name: '近 5 年', exact: true })).toBeEnabled();
  await expect(periods.getByRole('button', { name: '近 5 年', exact: true })).toHaveAttribute('aria-pressed', 'false');
});

test('EPS growth streak: picked in the sheet, applied, shown in the rows and kept in the link', async ({ page }) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  await sheet.getByRole('button', { name: /^進階：嚴格連續成長/ }).click();
  const streak = sheet.getByRole('group', { name: 'EPS 連續成長年數' });
  await streak.scrollIntoViewIfNeeded();
  await streak.getByRole('button', { name: '≥3 年' }).click();
  await expect(streak).toContainText('此條件僅涵蓋');
  await expect(streak).toContainText('最近 3 年每一年都有 EPS YoY');
  await sheet.getByRole('button', { name: /^套用/ }).click();
  const expected = streakPassers(3);
  expect(expected.length).toBeGreaterThan(0);
  expect(expected.length).toBeLessThan(twUniverse.length);
  await expect(page).toHaveURL(/f=epss(%3A|:)3/);
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('EPS 連續成長 ≥3 年');
  await expect(chips.locator('.active-filters-count')).toContainText(`符合 ${expected.length} / ${twUniverse.length}`);
  await expect(page.locator('.screener-row .metric-pill').first()).toContainText('EPS連增');

  // The link reproduces the list and the sheet shows the streak again
  await page.goto(page.url());
  await expect(page.getByRole('group', { name: '已套用的篩選條件' }).locator('.active-filters-count')).toContainText(
    `符合 ${expected.length} / ${twUniverse.length}`,
  );
  const again = await openSheet(page);
  await again.getByRole('button', { name: /^進階：嚴格連續成長/ }).click();
  await expect(again.getByRole('group', { name: 'EPS 連續成長年數' }).getByRole('button', { name: '≥3 年' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

test('growth terms are typed in: periods, threshold and 成長佔比 P change the rule, the counts and the link', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  const quarterly = sheet.getByRole('group', { name: '季度 EPS 成長', exact: true });
  await quarterly.scrollIntoViewIfNeeded();
  // Nothing applied yet: the sheet says what is missing before the condition takes effect
  await expect(quarterly).toContainText('選擇或輸入幅度 X 後才會套用');
  await quarterly.getByRole('textbox', { name: '季度 EPS 成長 · 期數（季）' }).fill('3');
  await quarterly.getByRole('textbox', { name: '季度 EPS 成長 · 幅度 X（%）' }).fill('15');
  await expect(quarterly.locator('.fs-rule')).toHaveText('近 3 季的 EPS 年增皆 ≥ 15%');
  await quarterly.getByRole('textbox', { name: '季度 EPS 成長 · 成長佔比 P（%）' }).fill('66.7');
  await expect(quarterly.locator('.fs-rule')).toHaveText('近 3 季中至少 66.7% 的季度 EPS 年增 ≥ 15%');
  // 2 of 3 is 66.67%, below 66.7%: all three must grow
  await expect(quarterly).toContainText('成長 3 次／衰退 0 次');
  await sheet.getByRole('button', { name: /^套用/ }).click();
  await expect(page).toHaveURL(/f=eps(%3A|:)3(%3A|:)15(%3A|:)66\.7/);
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('EPS YoY 近3季成長≥66.7%（≥15%）');
  // 2330: both quarters grow (+30%, +20%): 2 periods with data, enough for 3 (ceil(3 / 2) = 2)
  await expect(page.locator('.screener-row', { hasText: '2330' }).locator('.metric-pill').first()).toHaveText('EPS +30% 成2／衰0');
  if (shotDir) await page.screenshot({ path: `${shotDir}/fundamentals-terms-${info.project.name}.png` });

  // The link brings the terms back; a share outside 1–100 is refused in place and the rule keeps the last valid value
  await page.goto(page.url());
  const again = await openSheet(page);
  const reopened = again.getByRole('group', { name: '季度 EPS 成長', exact: true });
  await expect(reopened.getByRole('textbox', { name: '季度 EPS 成長 · 期數（季）' })).toHaveValue('3');
  await expect(reopened.getByRole('textbox', { name: '季度 EPS 成長 · 成長佔比 P（%）' })).toHaveValue('66.7');
  const share = reopened.getByRole('textbox', { name: '季度 EPS 成長 · 成長佔比 P（%）' });
  await share.fill('150');
  await expect(share).toHaveAttribute('aria-invalid', 'true');
  await expect(reopened.locator('.fs-term-error')).toContainText('請輸入 1–100');
  await expect(reopened.locator('.fs-rule')).toHaveText('近 3 季中至少 66.7% 的季度 EPS 年增 ≥ 15%');
  // A partial number (「1.」) is kept while typing
  await share.fill('1.');
  await expect(share).toHaveValue('1.');
  await expect(share).toHaveAttribute('aria-invalid', 'false');
});

test('the revenue block is a disclosure: closed by default with its conditions in the head, and it opens on a button', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw&f=rev:4:10');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  const fundamentals = sheet.getByRole('region', { name: '基本面（EPS／營收成長）' });
  await fundamentals.scrollIntoViewIfNeeded();
  const revenue = fundamentals.getByRole('button', { name: /^營收/ });
  await expect(revenue).toHaveAttribute('aria-expanded', 'false');
  await expect(revenue).toContainText('已啟用 1 項');
  await expect(fundamentals.locator('.fs-disclosure-summary')).toContainText('營收 YoY 近4季皆≥10%');
  await expect(fundamentals.getByRole('group', { name: '季度營收成長', exact: true })).toBeHidden();
  if (shotDir) await page.screenshot({ path: `${shotDir}/fundamentals-revenue-closed-${info.project.name}.png` });
  await revenue.click();
  await expect(revenue).toHaveAttribute('aria-expanded', 'true');
  const quarterlyRevenue = fundamentals.getByRole('group', { name: '季度營收成長', exact: true });
  await expect(quarterlyRevenue).toBeVisible();
  await expect(quarterlyRevenue.getByRole('button', { name: '近 4 季', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(quarterlyRevenue.getByRole('textbox', { name: '季度營收成長 · 幅度 X（%）' })).toHaveValue('10');
  if (shotDir) await page.screenshot({ path: `${shotDir}/fundamentals-revenue-open-${info.project.name}.png` });
});

test('stacked annual EPS conditions survive apply, reload, removal, cancel and reset', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  const sheet = await openSheet(page);
  await sheet.getByRole('navigation', { name: '篩選分類' }).getByRole('button', { name: /^基本面/ }).click();
  await expect(sheet.getByRole('region', { name: '今日漲跌' })).toBeHidden();
  await expect(sheet.getByRole('button', { name: /^進階：嚴格連續成長/ })).toHaveAttribute('aria-expanded', 'false');
  const first = sheet.getByRole('group', { name: '年度 EPS 成長', exact: true });
  await first.getByRole('button', { name: '近 5 年', exact: true }).click();
  await first.getByRole('button', { name: '≥10%', exact: true }).click();
  await sheet.getByRole('button', { name: /^新增年度 EPS 成長條件/ }).click();
  const second = sheet.getByRole('group', { name: '年度 EPS 成長 · 條件 2', exact: true });
  const threshold = second.getByRole('textbox', { name: '年度 EPS 成長 · 條件 2 · 幅度 X（%）', exact: true });
  await threshold.fill('');
  await expect(threshold).toHaveAttribute('aria-invalid', 'true');
  await expect(second).toBeVisible();
  await threshold.fill('20');
  await expect(threshold).toHaveAttribute('aria-invalid', 'false');
  await expect(first.locator('.fs-rule')).toHaveText('近 5 年的 EPS 年增皆 ≥ 10%');
  await expect(second.locator('.fs-rule')).toHaveText('最近 1 年的 EPS 年增 ≥ 20%');
  const expected = annualPassers(5, 10).filter((row) => annualPassers(1, 20).some((other) => row[0] === other[0]));
  await expect(sheet.getByRole('button', { name: /^套用/ })).toHaveText(`套用（${expected.length} 檔）`);
  if (shotDir) await page.screenshot({ path: `${shotDir}/stacked-annual-${info.project.name}.png` });
  await sheet.getByRole('button', { name: /^套用/ }).click();
  expect(new URL(page.url()).searchParams.get('f')).toBe('epsy:5:10;epsy:1:20');
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips.locator('.active-filters-count')).toContainText(`符合 ${expected.length} / ${twUniverse.length}`);
  await page.reload();
  await expect(chips).toContainText('EPS 年增 近5年皆≥10%');
  await expect(chips).toContainText('EPS 年增 ≥20%');
  const again = await openSheet(page);
  await expect(again.getByRole('group', { name: '年度 EPS 成長 · 條件 2', exact: true }).getByRole('button', { name: '≥20%', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await again.getByRole('button', { name: '移除年度 EPS 成長', exact: true }).click();
  await expect(again.getByRole('group', { name: '年度 EPS 成長', exact: true }).locator('.fs-rule')).toHaveText('最近 1 年的 EPS 年增 ≥ 20%');
  await again.getByRole('button', { name: '關閉篩選', exact: true }).click();
  expect(new URL(page.url()).searchParams.get('f')).toBe('epsy:5:10;epsy:1:20');
  const reset = await openSheet(page);
  await reset.getByRole('button', { name: '重設', exact: true }).click();
  await expect(reset.getByLabel('草稿條件')).toHaveCount(0);
  await expect(reset.getByRole('group', { name: '年度 EPS 成長', exact: true }).getByRole('textbox', { name: '年度 EPS 成長 · 期數（年）', exact: true })).toHaveValue('1');
  await reset.getByRole('button', { name: /^套用/ }).click();
  expect(new URL(page.url()).searchParams.has('f')).toBe(false);
});

for (const [title, token, revenue] of [['季度 EPS 成長', 'eps', false], ['季度營收成長', 'rev', true], ['年度營收成長', 'revy', true]] as const) {
  test(`${title}: add and remove a second independent condition`, async ({ page }) => {
    await mockSite(page, { report: buildReport(20), fundamentals: true });
    await page.goto(`./?page=screener&group=universe-tw&f=${token}:3:10`);
    const sheet = await openSheet(page);
    await sheet.getByRole('navigation', { name: '篩選分類' }).getByRole('button', { name: /^基本面/ }).click();
    if (revenue) await sheet.getByRole('button', { name: /^營收/ }).click();
    await sheet.getByRole('button', { name: new RegExp(`^新增${title}條件`) }).click();
    const second = sheet.getByRole('group', { name: `${title} · 條件 2`, exact: true });
    await second.getByRole('button', { name: '≥30%', exact: true }).click();
    await expect(sheet.getByLabel('草稿條件')).toContainText('≥30%');
    await second.getByRole('button', { name: `移除${title} · 條件 2`, exact: true }).click();
    await expect(sheet.getByRole('group', { name: `${title} · 條件 2`, exact: true })).toHaveCount(0);
    await expect(sheet.getByRole('group', { name: title, exact: true }).locator('.fs-rule')).toContainText('≥ 10%');
    await sheet.getByRole('button', { name: /^套用/ }).click();
    expect(new URL(page.url()).searchParams.get('f')).toBe(`${token}:3:10`);
  });
}

test('EPS acceleration switches between annual and quarterly data and can require both', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  await page.goto('./?page=screener&group=universe-tw&f=acc:1');
  const sheet = await openSheet(page);
  await sheet.getByRole('navigation', { name: '篩選分類' }).getByRole('button', { name: /^基本面/ }).click();
  const period = sheet.getByRole('group', { name: 'EPS 成長加速 · 週期', exact: true });
  await period.getByRole('button', { name: '年度年增', exact: true }).click();
  await expect(sheet.getByRole('group', { name: 'EPS 成長加速週期', exact: true })).toContainText('最新完整會計年度 EPS 相對前一年的年增率');
  const expected = fixtureGrowth.rows.filter((row) => {
    const values = row[7] as (number | null)[];
    const turns = row[10] as number;
    return twUniverse.includes(String(row[0])) && values[1] != null && ((values[0] != null && values[0] > values[1]) || (values[0] == null && (turns & 1) === 1));
  });
  await expect(sheet.getByRole('button', { name: /^套用/ })).toHaveText(`套用（${expected.length} 檔）`);
  if (shotDir) await page.screenshot({ path: `${shotDir}/annual-acceleration-${info.project.name}.png` });
  await sheet.getByRole('button', { name: /^套用/ }).click();
  expect(new URL(page.url()).searchParams.get('f')).toBe('accy:1');
  await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('年度 EPS 成長加速');
  await page.reload();
  const again = await openSheet(page);
  const control = again.getByRole('group', { name: 'EPS 成長加速 · 週期', exact: true });
  await expect(control.getByRole('button', { name: '年度年增', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await control.getByRole('button', { name: '兩者皆符合', exact: true }).click();
  await expect(again.getByRole('group', { name: 'EPS 成長加速週期', exact: true })).toContainText('選兩者時必須同時符合');
  await again.getByRole('button', { name: /^套用/ }).click();
  expect(new URL(page.url()).searchParams.get('f')).toBe('acc:1;accy:1');
});
