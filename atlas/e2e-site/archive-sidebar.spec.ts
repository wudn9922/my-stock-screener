import { expect, test } from '@playwright/test';
import { clock, archives, live } from './archive-fixture';
import { mockSite } from './mocks';

test('C4 visible sidebar TW/US rows share one batch and update once a minute',async({page},info)=>{
  await page.clock.install({time:clock});await mockSite(page);
  const symbols=['2330.TW','2317.TW','2454.TW','NVDA','AAPL','MSFT','TSLA','GOOGL'];
  await archives(page,symbols);const {batches,dailyFiveYears}=await live(page);
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.locator('.data-source')).toContainText('GitHub 永久封存');
  await expect.poll(()=>batches.length).toBe(1);
  const before=batches.length;
  if(info.project.name==='desktop-chromium')await page.getByRole('button',{name:'圖表收藏',exact:true}).click();
  else {await page.getByRole('button',{name:'更多面板',exact:true}).click();await page.getByRole('tab',{name:'圖表收藏',exact:true}).click();}
  await expect.poll(()=>batches.length).toBe(before+1);
  expect(batches.at(-1)!.some(s=>s.endsWith('.TW'))).toBe(true);
  await page.locator('[data-watch-symbol="AAPL"]').scrollIntoViewIfNeeded();
  const quote=page.locator('[data-watch-symbol="AAPL"] .watch-quote');await expect(quote).toBeVisible();
  if(info.project.name!=='desktop-chromium')await expect.poll(()=>batches.length).toBe(before+2);
  expect(batches.slice(before).some(batch=>batch.some(s=>!s.endsWith('.TW')))).toBe(true);
  const beforeMinute=batches.length;
  const old=await quote.textContent();await page.clock.fastForward(61_000);
  await expect.poll(()=>batches.length).toBe(beforeMinute+1);await expect(quote).not.toHaveText(old!);
  expect(dailyFiveYears).toEqual([]);
  await page.screenshot({path:info.outputPath('batched-sidebar.png')});
});
