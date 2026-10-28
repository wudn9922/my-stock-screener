import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';

for (const short of [false, true]) test(`screener data health follows the virtual list without overlap${short ? ' on a short 390px phone' : ''}`, async ({ page }) => {
  await mockSite(page);
  if (short) await page.setViewportSize({ width: 390, height: 520 });
  await page.goto('./?page=screener&group=tw_all');
  const list = page.locator('.screener-list'); await expect(list).toBeVisible();
  // Virtualized stock list keeps its own bounded, working scroll area.
  expect(await list.evaluate(el => el.scrollHeight)).toBeGreaterThan(await list.evaluate(el => el.clientHeight));
  await list.evaluate(el => { el.scrollTop = 2000; });
  await expect.poll(() => list.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  const health = page.locator('.data-health'); const summary = health.locator(':scope > summary');
  await summary.scrollIntoViewIfNeeded(); await expect(summary).toBeVisible();
  const separation = await page.evaluate(() => ({
    health: document.querySelector('.data-health')!.getBoundingClientRect().top,
    list: document.querySelector('.screener-list')!.getBoundingClientRect().bottom,
  }));
  expect(separation.health).toBeGreaterThanOrEqual(separation.list);
  await summary.click(); await expect(health).toHaveAttribute('open', '');
  const retry = health.getByRole('button', { name: /重新檢查|檢查中/ }); await retry.scrollIntoViewIfNeeded();
  await expect(retry).toBeVisible();
  const last = health.locator('.health-entries > li').last(); await last.scrollIntoViewIfNeeded(); await expect(last).toBeVisible();
  const main = await page.locator('#main').boundingBox(); const lastBox = await last.boundingBox();
  expect(lastBox!.y + lastBox!.height).toBeLessThanOrEqual(main!.y + main!.height + 1);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `test-results/site/screener-health-${short ? 'short' : 'normal'}.png` });
});
