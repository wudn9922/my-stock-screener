import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';

test('news digest renders text, expands screenshot and switches history', async ({ page }, info) => {
  await mockSite(page);
  const image = { source:'WSJ', file:'wsj-20261128-abc.jpg', snapshot:'2026-11-27T00:00:00Z', sentAt:'2026-11-28T00:00:00Z' };
  await page.route('**/report/news-digest.json', route => route.fulfill({json:{version:1,
    latest:{generatedAt:'2026-11-28T00:00:00Z',text:'<b>新聞原文</b>',screenshots:[image]},
    history:[{generatedAt:'2026-11-27T00:00:00Z',text:'前日新聞',screenshots:[]},null]}}));
  await page.route('**/news/*.jpg', route => route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="500"><rect width="300" height="500" fill="#ddd"/></svg>'}));
  await page.goto('./?page=markets&market=us');
  const card = page.getByRole('region',{name:'華爾街新聞'});
  await expect(card.getByText('<b>新聞原文</b>',{exact:true})).toBeVisible();
  await card.getByRole('button',{name:'WSJ 放大截圖'}).click();
  const viewer = page.getByRole('dialog',{name:'WSJ 新聞截圖'});
  await expect(viewer).toBeVisible();
  await viewer.getByRole('button',{name:'放大',exact:true}).click();
  await expect(viewer.getByText('150%')).toBeVisible();
  await viewer.getByRole('button',{name:'重設'}).click();
  await expect(viewer.getByText('100%')).toBeVisible();
  await viewer.getByRole('button',{name:'關閉'}).click();
  await card.getByLabel('歷史新聞').selectOption('2026-11-27T00:00:00Z');
  await expect(card.getByText('前日新聞')).toBeVisible();
  await card.getByLabel('歷史新聞').selectOption('2026-11-28T00:00:00Z');
  if (page.viewportSize()?.width === 390 && info.project.name.includes('webkit')) await card.screenshot({path:info.outputPath('news-digest-390.png')});
});
