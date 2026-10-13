import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';

/** Unmatched / failed stocks in fixed and custom groups (fixture: tw_g1 = 3 matched + 2 unmatched + 1 failed). */

test('a fixed group lists matched, unmatched and failed rows with a 全部｜成功｜未成功 control', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=screener&group=tw_g1');
  const rows = page.locator('.screener-row');
  await expect(rows).toHaveCount(6);
  await expect(page.locator('.screener-row.is-unmatched')).toHaveCount(2);
  await expect(page.locator('.screener-row.is-failed')).toHaveCount(1);
  await expect(page.locator('.screener-row.is-unmatched .status-badge').first()).toHaveText('未符合');
  await expect(page.locator('.screener-row.is-failed .status-badge')).toHaveText('失敗');
  await expect(page.locator('.screener-row.is-failed')).toContainText('下載失敗');
  await expect(page.locator('.screener-row.is-failed .row-quote b')).toHaveText('—');

  // The chip counts the matched stocks and shows the others as "+N".
  const chip = page.locator('.chip-row').filter({ hasText: '台股' }).getByRole('button', { name: /權值精選/ });
  await expect(chip.locator('.chip-count')).toHaveText('3');
  await expect(chip.locator('.chip-unmatched')).toHaveText('+3');
  await expect(chip.locator('.chip-unmatched')).toHaveAttribute('title', '另有 3 檔未成功');

  const control = page.getByRole('group', { name: '成功與未成功股票' });
  await expect(control.getByRole('button', { name: /^全部/ })).toHaveAttribute('aria-pressed', 'true');
  await control.getByRole('button', { name: /^成功/ }).click();
  await expect(rows).toHaveCount(3);
  await expect(page.locator('.status-badge')).toHaveCount(0);
  await control.getByRole('button', { name: /^未成功/ }).click();
  await expect(rows).toHaveCount(3);
  await expect(page.locator('.status-badge')).toHaveCount(3);
  await control.getByRole('button', { name: /^全部/ }).click();
  await expect(rows).toHaveCount(6);

  // No horizontal page overflow with the control on a phone.
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test('sorting keeps failed rows last; the search box finds a row by its reason', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=screener&group=tw_g1');
  await page.getByRole('combobox', { name: '排序' }).selectOption('symbol');
  const symbols = await page.locator('.screener-row .row-id b').allTextContents();
  expect(symbols.at(-1)).toBe('6505');
  await page.getByRole('searchbox', { name: '在群組內篩選' }).fill('下載失敗');
  await expect(page.locator('.screener-row')).toHaveCount(1);
});

test('a failed row still opens its chart', async ({ page }) => {
  const requested = await mockSite(page);
  await page.goto('./?page=screener&group=tw_g1');
  await page.getByRole('searchbox', { name: '在群組內篩選' }).fill('6505');
  await page.locator('.screener-row.is-failed').click();
  await expect(page).toHaveURL(/page=chart&symbol=6505\.TW/);
  await expect.poll(() => requested.includes('6505.TW')).toBe(true);
});

test('full-market lists have no unmatched control', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=screener&group=tw_all');
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await expect(page.getByRole('group', { name: '成功與未成功股票' })).toHaveCount(0);
  await expect(page.locator('.status-badge')).toHaveCount(0);
});

test('a group without unmatched stocks looks as before', async ({ page }) => {
  await mockSite(page);
  await page.goto('./?page=screener&group=us_g1');
  await expect(page.locator('.screener-row')).toHaveCount(3);
  await expect(page.getByRole('group', { name: '成功與未成功股票' })).toHaveCount(0);
  await expect(page.locator('.chip-row').filter({ hasText: '美股' }).getByRole('button', { name: /權值精選/ }).locator('.chip-unmatched')).toHaveCount(0);
});
