import { expect, test } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

// Set SCREENSHOT_DIR to keep a picture of the sheet (used for manual visual checks, not by CI).
const shotDir = process.env.SCREENSHOT_DIR;

test('daily share threshold is optional, customizable, persisted and removable', async ({ page }, info) => {
  const report = buildReport(20);
  const groups = report.groups as { key: string; items: { volume: number | null; status: string }[] }[];
  const group = groups.find((entry) => entry.key === 'tw_g1')!;
  const volumes = [99_999, 100_000, 1_000_000, null, 0, 250_000];
  group.items.forEach((entry, index) => { entry.volume = volumes[index]!; entry.status = 'matched'; });
  await mockSite(page, { report });
  await page.goto('./?page=screener&group=tw_g1');
  await expect(page.locator('.result-count')).toHaveText('6 檔');
  await page.getByRole('button', { name: '篩選條件' }).click();
  const sheet = page.getByRole('dialog', { name: '篩選條件' });
  const volume = sheet.getByRole('region', { name: '每日成交股數' });
  await expect(volume.getByRole('button', { name: '不限', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await volume.getByRole('button', { name: '≥100 萬股', exact: true }).click();
  await expect(sheet.getByRole('button', { name: '套用（1 檔）' })).toBeEnabled();
  await sheet.getByRole('button', { name: '套用（1 檔）' }).click();
  await expect(page.locator('.result-count')).toHaveText('1 / 6 檔');
  await expect(page).toHaveURL(/vol(?::|%3A)1000000/);

  await page.getByRole('button', { name: /篩選條件/ }).click();
  const custom = sheet.getByRole('textbox', { name: '自訂成交股數下限（股）' });
  await custom.fill('-1');
  await expect(custom).toHaveAttribute('aria-invalid', 'true');
  await expect(sheet.getByRole('button', { name: /套用/ })).toBeDisabled();
  await custom.fill('250000');
  await expect(sheet.getByRole('button', { name: '套用（2 檔）' })).toBeEnabled();
  if (shotDir) await page.screenshot({ path: `${shotDir}/filters-shares-${info.project.name}.png` });
  await sheet.getByRole('button', { name: '套用（2 檔）' }).click();
  await page.reload();
  await expect(page.locator('.result-count')).toHaveText('2 / 6 檔');
  await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('成交股數 ≥25 萬股');
  await page.goto('./?page=screener&group=tw_g1');
  await expect(page.locator('.result-count')).toHaveText('2 / 6 檔');
  await expect(page).toHaveURL(/vol(?::|%3A)250000/);
  await page.getByRole('button', { name: /移除.*成交股數/ }).click();
  await expect(page.locator('.result-count')).toHaveText('6 檔');
  await page.getByRole('button', { name: '篩選條件' }).click();
  await custom.fill('100000');
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
