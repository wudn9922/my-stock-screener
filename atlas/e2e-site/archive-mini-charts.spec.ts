import { expect, test } from '@playwright/test';
import { clock, archives, manualList, live } from './archive-fixture';
import { mockSite } from './mocks';
import { mockDeviceAccount } from './account-fixture';

test('C5 chart review subscribes only visible charts, reuses quotes on return, and refreshes after five minutes',async({page},info)=>{
  await page.clock.install({time:clock});await mockDeviceAccount(page);await mockSite(page);
  const symbols=Array.from({length:12},(_,i)=>`M${i}`);
  await archives(page,symbols);const {batches,dailyFiveYears}=await live(page);
  await page.goto('./?page=watchlist');await manualList(page,symbols);await expect(page.locator('.wb-row')).toHaveCount(12);
  await expect.poll(()=>batches.length).toBeGreaterThan(0);
  const chartBatchesStart=batches.length;
  await page.getByRole('button',{name:/連續看全部股票圖表/}).click();
  const cards=page.locator('.stock-chart-card');await expect(cards).toHaveCount(12);
  await expect(cards.first().locator('.mini-chart')).toHaveAttribute('data-status','ready');
  await page.clock.fastForward(61_000);
  const countFirst=()=>batches.filter(batch=>batch.includes('M0')).length;
  expect(countFirst()).toBe(1);
  await cards.nth(10).scrollIntoViewIfNeeded();await expect(cards.nth(10).locator('.mini-chart')).toHaveAttribute('data-status','ready');
  await cards.first().scrollIntoViewIfNeeded();await expect(cards.first().locator('.mini-chart')).toHaveAttribute('data-status','ready');
  await page.clock.fastForward(1000);expect(countFirst()).toBe(1);
  await page.clock.fastForward(240_000);await expect.poll(countFirst).toBe(2);
  expect(batches.slice(chartBatchesStart).every(batch=>batch.length<8)).toBe(true); // only viewport charts, never all twelve
  expect(dailyFiveYears).toEqual([]);
  await page.screenshot({path:info.outputPath('live-chart-review.png')});
});
