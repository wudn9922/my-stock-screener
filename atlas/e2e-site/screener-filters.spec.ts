import { expect, test } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

// Set SCREENSHOT_DIR to keep a picture of the sheet (used for manual visual checks, not by CI).
const shotDir = process.env.SCREENSHOT_DIR;

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
