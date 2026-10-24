import { accessEnvelope, accountPreference, installLineAccount, mockDeviceAccount } from './account-fixture';
import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite, screenerFixtures } from './mocks';

// Set SCREENSHOT_DIR to keep a picture of the sheet (used for manual visual checks, not by CI).
const shotDir = process.env.SCREENSHOT_DIR;

test('average volume AND stacked annual gates survive sharing, market memory and saved strategies', async ({ page, browser }) => {
  const universe = screenerFixtures.universe() as { fields: string[]; markets: { TW: { rows: unknown[][] } } };
  const growth = screenerFixtures.growth() as { rows: unknown[][] };
  universe.markets.TW.rows = universe.markets.TW.rows.slice(0, 5);
  const histories = [[25, 15, 15, 15, 15], [25, 15, 15, 15, 15], [25, 5, 5, 5, 5], [15, 15, 15, 15, 15], [25, 15, 15, 15, 15]];
  const financialTemplate = growth.rows[0];
  growth.rows = universe.markets.TW.rows.map((row, index) => {
    const financial = [...financialTemplate];
    financial[0] = row[0];
    financial[7] = histories[index];
    financial[10] = 0;
    return financial;
  });
  universe.markets.TW.rows.forEach((row, index) => {
    row[universe.fields.indexOf('volumeAvg20')] = [1_000_000, 999_999, 2_000_000, 2_000_000, null][index];
  });
  const setup = async (target: Page) => {
    await mockSite(target, { report: buildReport(20), fundamentals: true });
    await target.route('**/my-stock-screener/report/universe.json', (route) => route.fulfill({ json: universe }));
    await target.route('**/my-stock-screener/report/growth.json', (route) => route.fulfill({ json: growth }));
  };
  await mockDeviceAccount(page);
  await setup(page);
  await page.goto('./?page=screener&group=universe-tw&f=epsy:5:10;epsy:1:20');
  await expect(page.locator('.result-count')).toHaveText('3 / 5 檔');
  await page.getByRole('button', { name: /篩選條件/ }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  await sheet.getByRole('region', { name: '20 日平均成交量（張）' }).getByRole('button', { name: '≥1,000 張', exact: true }).click();
  await expect(sheet.getByRole('button', { name: '套用（1 檔）' })).toBeEnabled();
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ width: 390, height: 280 });
  await expect(sheet.getByRole('button', { name: '套用（1 檔）' })).toBeInViewport({ ratio: 1 });
  await sheet.getByRole('button', { name: '套用（1 檔）' }).click();
  await page.setViewportSize(viewport);
  const filters = 'vol:1000000;epsy:5:10;epsy:1:20';
  expect(new URL(page.url()).searchParams.get('f')).toBe(filters);
  await expect(page.locator('.result-count')).toHaveText('1 / 5 檔');
  await expect(page.locator('.screener-row')).toContainText('1301');
  const sharedUrl = page.url();
  const fresh = await browser.newPage();
  try {
    await setup(fresh);
    await fresh.goto(sharedUrl);
    await expect(fresh.locator('.result-count')).toHaveText('1 / 5 檔');
    expect(new URL(fresh.url()).searchParams.get('f')).toBe(filters);
  } finally { await fresh.close(); }
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('成交量與疊加成長');
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toBeVisible();
  const strategyUrl = page.url();
  await page.goto('./?page=screener&group=universe-us&f=vol:100000;rev:4:10;rev:1:20');
  await page.getByRole('button', { name: /篩選條件/ }).click();
  await sheet.getByRole('button', { name: /^套用/ }).click();
  await page.goto('./?page=screener&group=universe-tw');
  await expect(page.locator('.result-count')).toHaveText('1 / 5 檔');
  expect(new URL(page.url()).searchParams.get('f')).toBe(filters);
  await page.goto('./?page=screener&group=universe-us');
  await expect(page).toHaveURL(/vol(?::|%3A)100000/);
  expect(new URL(page.url()).searchParams.get('f')).toBe('vol:100000;rev:4:10;rev:1:20');
  await page.goto(strategyUrl);
  await page.reload();
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('成交量與疊加成長');
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('1301');
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('20 日均量 ≥1,000 張');
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('EPS 年增 近5年皆≥10%');
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('EPS 年增 ≥20%');
  await page.goto(sharedUrl);
  await page.getByRole('button', { name: /篩選條件/ }).click();
  const custom = sheet.getByRole('textbox', { name: '自訂20日平均成交量下限（張）' });
  await sheet.getByLabel('草稿條件').getByRole('button', { name: /移除20 日均量/ }).click();
  await expect(custom).toHaveValue('');
  await expect(sheet.getByRole('button', { name: '套用（3 檔）' })).toBeEnabled();
  await sheet.getByRole('button', { name: '重設', exact: true }).click();
  await expect(custom).toHaveValue('');
  await expect(sheet.getByLabel('草稿條件')).toHaveCount(0);
  await expect(sheet.getByRole('button', { name: '套用（5 檔）' })).toBeEnabled();
});

test('average volume threshold is optional, customizable, persisted and removable', async ({ page }, info) => {
  await mockDeviceAccount(page);
  const report = buildReport(20);
  const groups = report.groups as { key: string; items: { volume: number | null; status: string; metrics: { volumeAvg20?: number | null } }[] }[];
  const group = groups.find((entry) => entry.key === 'tw_g1')!;
  const volumes = [99_999, 100_000, 1_000_000, null, 0, 250_000];
  group.items.forEach((entry, index) => { entry.volume = 99_999_999; entry.metrics = { ...entry.metrics, volumeAvg20: volumes[index]! }; entry.status = 'matched'; });
  await mockSite(page, { report });
  await page.goto('./?page=screener&group=tw_g1');
  await expect(page.locator('.result-count')).toHaveText('6 檔');
  await page.getByRole('button', { name: '篩選條件' }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  const volume = sheet.getByRole('region', { name: '20 日平均成交量（張）' });
  await expect(volume.getByRole('button', { name: '不限', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await volume.getByRole('button', { name: '≥1,000 張', exact: true }).click();
  await expect(sheet.getByRole('button', { name: '套用（1 檔）' })).toBeEnabled();
  await sheet.getByRole('button', { name: '套用（1 檔）' }).click();
  await expect(page.locator('.result-count')).toHaveText('1 / 6 檔');
  await expect(page).toHaveURL(/vol(?::|%3A)1000000/);

  await page.getByRole('button', { name: /篩選條件/ }).click();
  const custom = sheet.getByRole('textbox', { name: '自訂20日平均成交量下限（張）' });
  await custom.fill('-1');
  await expect(custom).toHaveAttribute('aria-invalid', 'true');
  await expect(sheet.getByRole('button', { name: /套用/ })).toBeDisabled();
  await custom.fill('250');
  await expect(sheet.getByRole('button', { name: '套用（2 檔）' })).toBeEnabled();
  if (shotDir) await page.screenshot({ path: `${shotDir}/filters-shares-${info.project.name}.png` });
  await sheet.getByRole('button', { name: '套用（2 檔）' }).click();
  await page.reload();
  await expect(page.locator('.result-count')).toHaveText('2 / 6 檔');
  await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('20 日均量 ≥250 張');
  await page.goto('./?page=screener&group=tw_g1');
  await expect(page.locator('.result-count')).toHaveText('2 / 6 檔');
  await expect(page).toHaveURL(/vol(?::|%3A)250000/);
  await page.getByRole('button', { name: /移除.*20 日均量/ }).click();
  await expect(page.locator('.result-count')).toHaveText('6 檔');
  await page.getByRole('button', { name: '篩選條件' }).click();
  await custom.fill('100');
  await expect(sheet.getByRole('button', { name: '套用（3 檔）' })).toBeEnabled();
  await custom.fill('0');
  await expect(sheet.getByRole('button', { name: '套用（6 檔）' })).toBeEnabled();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('filter sheet offers EMA20 distance and the 1.1 / 1.2 / 1.5 volume presets', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=screener&group=tw_g1');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await page.getByRole('button', { name: '篩選條件' }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });

  // Only EMA20 is left under 「均線乖離」.
  const distance = sheet.getByRole('region', { name: '均線乖離' });
  await expect(distance.getByRole('slider')).toHaveCount(2); // lower + upper handle of one range
  await expect(distance.getByRole('slider', { name: 'EMA20 下限' })).toBeVisible();
  await expect(distance).not.toContainText('MA50');
  await expect(distance).not.toContainText('MA200');

  if (shotDir) await page.screenshot({ path: `${shotDir}/filters-ema-${info.project.name}.png` });

  // Volume presets: single choice, tapping the lit one clears it, no free-form input.
  const volume = sheet.getByRole('region', { name: '量比' });
  await expect(volume.locator('details')).not.toHaveAttribute('open');
  await volume.getByText('量比說明', { exact: true }).click();
  await expect(volume).toContainText('今日成交量 ÷ 前一交易日成交量。');
  await expect(volume.getByRole('textbox')).toHaveCount(0);
  const preset = (name: string) => volume.getByRole('button', { name, exact: true });
  await preset('≥1.2').click();
  await expect(preset('≥1.2')).toHaveAttribute('aria-pressed', 'true');
  await preset('≥1.5').click();
  await expect(preset('≥1.2')).toHaveAttribute('aria-pressed', 'false');
  await expect(preset('≥1.5')).toHaveAttribute('aria-pressed', 'true');
  await preset('≥1.5').click();
  await expect(preset('不限')).toHaveAttribute('aria-pressed', 'true');
  if (shotDir) await page.screenshot({ path: `${shotDir}/filters-${info.project.name}.png` });
});

test('an old vr link and retired MA tokens are read with the new meaning', async ({ page }, info) => {
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=screener&group=tw_g1&f=ma50:-5:5;vr:1.3');
  const chips = page.getByRole('group', { name: '已套用的篩選條件' });
  await expect(chips).toContainText('量比 ≥1.3');
  await expect(chips).not.toContainText('MA50');
  await page.getByRole('button', { name: /篩選條件/ }).click();
  const volume = page.getByRole('dialog', { name: '篩選條件' }).getByRole('region', { name: '量比' });
  await expect(volume).toContainText('目前條件：≥1.3');
  for (const name of ['≥1.1', '≥1.2', '≥1.5', '不限'])
    await expect(volume.getByRole('button', { name, exact: true })).toHaveAttribute('aria-pressed', 'false');
  await volume.scrollIntoViewIfNeeded();
  if (shotDir) await page.screenshot({ path: `${shotDir}/filters-legacy-${info.project.name}.png` });
});

test('saved individual minimum MA overrides EMA20 and survives opening the same saved strategy', async ({ page }) => {
  await mockDeviceAccount(page);
  await page.addInitScript(({ key }) => localStorage.setItem(key, JSON.stringify({
    version: 1, nextId: 2, groups: [], stocks: [{ id: 1, ticker: '2330.TW', group_id: 'ungrouped', ma1: 50, ma2: 3, ma3: 20, ma4: 0 }],
  })), { key: accountPreference('atlas-parameter-workspace-v1') });
  const report = buildReport(20);
  const groups = report.groups as { key: string; items: unknown[] }[];
  groups.find(group => group.key === 'tw_g1')!.items = [
    { symbol: '2330.TW', name: '固定參數股', close: 110, changePct: 1, maList: [20, 50], maValues: { 3: 100, 20: 130, 50: 120 }, metrics: { ema20: -15 } },
    { symbol: '1101.TW', name: '預設參數股', close: 110, changePct: 1, maList: [20], maValues: { 20: 130 }, metrics: { ema20: 5 } },
  ];
  await mockSite(page, { report });
  await page.goto('./?page=screener&group=tw_g1&f=ema20:0:20');
  await expect(page.locator('.screener-row .row-id b')).toHaveText(['2330', '1101']);
  await expect(page.locator('.screener-row').first()).toContainText('MA3 +10.0%');
  await expect(page.locator('.screener-row').nth(1)).toContainText('EMA20 +5.0%');
  await page.getByRole('button', { name: /篩選條件/ }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  await expect(sheet.getByRole('slider', { name: '固定最小均線／EMA20 下限' })).toBeVisible();
  await expect(sheet.getByRole('region', { name: '今日漲跌' })).toHaveCount(0);
  await sheet.getByRole('button', { name: '關閉篩選' }).click();
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('個股固定乖離');
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  const result = page.getByRole('region', { name: '清單追蹤結果' });
  await expect(result).toContainText('固定參數股');
  await expect(result).toContainText('預設參數股');
  await page.reload();
  await expect(result).toContainText('固定參數股');
  await expect(result).toContainText('預設參數股');
});

test('unavailable full-market data with a moving-average filter never loops parameter reads', async ({ page }) => {
  await installLineAccount(page);
  await mockSite(page, { report: buildReport(20), fundamentals: true });
  let parameterReads = 0;
  await page.route('**/functions/v1/user-settings', route => {
    const action = route.request().postDataJSON().action;
    if (action === 'stocks.list') parameterReads++;
    return route.fulfill({ json: action === 'access.get' ? accessEnvelope(undefined, 'cloud') : { ok: true, data: [] } });
  });
  await page.route('**/my-stock-screener/report/universe.json', route => route.fulfill({ status: 503, body: 'temporarily unavailable' }));
  await page.goto('./?page=screener&group=universe-tw&f=ema20:0:20');
  await expect(page.getByRole('button', { name: /篩選條件/ })).toBeDisabled();
  await page.getByRole('combobox', { name: '排序' }).selectOption('gain');
  await page.getByRole('combobox', { name: '排序' }).selectOption('loss');
  expect(parameterReads).toBe(0);
});
