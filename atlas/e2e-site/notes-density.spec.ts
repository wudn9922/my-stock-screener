import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { researchFixtureBody } from './research-fixtures';

test('metric explanations occupy no extra row until opened and remain keyboard accessible', async ({ page }, info) => {
  await mockSite(page);
  await page.clock.setFixedTime(new Date('2026-10-22T10:00:00Z'));
  await page.route('**/my-stock-screener/report/research/**', route => {
    const relative = new URL(route.request().url()).pathname.split('/research/')[1]!;
    try {
      const body = researchFixtureBody(relative);
      const range = /^bytes=(\d+)-(\d+)$/.exec(route.request().headers()['range'] ?? '');
      return route.fulfill({ status: range ? 206 : 200, contentType: 'application/json',
        body: range ? body.subarray(Number(range[1]), Number(range[2]) + 1) : body });
    } catch { return route.fulfill({ status: 404 }); }
  });
  await page.goto('./?page=financials&symbol=2330.TW');
  const institutional = page.locator('details.data-research').filter({
    has: page.locator(':scope > summary', { hasText: '法人買賣超' }),
  });
  await institutional.locator(':scope > summary').click();
  const metric = institutional.locator('.research-stat').filter({
    has: page.locator('dt', { hasText: '近 5 日買賣超（張）' }),
  });
  const note = metric.locator('.research-note');
  const trigger = note.locator(':scope > summary');
  await expect(trigger).toHaveAccessibleName('近 5 日買賣超（張）說明');
  await expect(note).not.toHaveAttribute('open');
  await expect(note.locator('.research-note-body')).not.toBeVisible();
  await metric.scrollIntoViewIfNeeded();
  const closed = (await metric.boundingBox())!;
  const triggerBox = (await trigger.boundingBox())!;
  expect(triggerBox.x).toBeGreaterThanOrEqual(closed.x);
  expect(triggerBox.x + triggerBox.width).toBeLessThanOrEqual(closed.x + closed.width);
  expect(triggerBox.height).toBeGreaterThanOrEqual(24);
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(note.locator('.research-note-body')).toBeVisible();
  await expect(note).toContainText('/5 筆');
  expect((await metric.boundingBox())!.height).toBeGreaterThan(closed.height + 10);
  await page.keyboard.press('Enter');
  await expect(note).not.toHaveAttribute('open');
  expect(Math.abs((await metric.boundingBox())!.height - closed.height)).toBeLessThanOrEqual(1);

  const provenance = institutional.locator('.research-provenance').first();
  await expect(provenance.locator(':scope > summary')).toContainText('2026-10-22');
  await expect(provenance.locator('a')).not.toBeVisible();
  await provenance.locator(':scope > summary').click();
  await expect(provenance.locator('a')).toBeVisible();
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await institutional.screenshot({ path: info.outputPath('compact-explanations.png') });
});
