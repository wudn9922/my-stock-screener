import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { isCompactConsole } from './console-nav';

const overflow = (page: Page) => page.locator('#main').evaluate(element => element.scrollWidth - element.clientWidth);

test('我的 on phones and tablets: account card, three groups of 52px rows, no horizontal overflow', async ({ page }) => {
  test.skip(!isCompactConsole(page), 'phone and tablet layout');
  await mockSite(page);
  await page.goto('./?page=console');
  await expect(page.getByRole('link', { name: /^個股均線參數/ })).toBeVisible();
  for (const group of ['個人化', '顯示', '資料']) await expect(page.getByRole('heading', { name: group, level: 2, exact: true })).toBeVisible();
  const card = page.locator('.storage-policy-account');
  await expect(card).toContainText('訪客模式');
  const login = card.getByRole('button', { name: '使用 LINE 登入', exact: true });
  expect((await login.boundingBox())!.height).toBe(48);
  expect(await login.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(await page.evaluate(() => {
    const probe = document.createElement('i');
    probe.style.color = 'var(--accent)';
    document.body.append(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  }));
  expect((await card.getByRole('checkbox').boundingBox())!.width).toBe(20);
  // Rounded: a row below a fractional offset measures 51.99997px from float layout coordinates.
  for (const row of await page.locator('a.me-row').all()) expect(Math.round((await row.boundingBox())!.height)).toBeGreaterThanOrEqual(52);
  expect((await page.locator('.me-row-color').boundingBox())!.height).toBe(56);
  for (const button of await page.locator('.me-seg button').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  await expect(page.getByRole('group', { name: '漲跌顏色' }).getByRole('button', { name: /紅漲/ })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('group', { name: '漲跌顏色' }).getByRole('button', { name: /綠漲/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'us');
  for (const width of [390, 360, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await overflow(page)).toBe(0);
  }
});

test('我的 sub-views are routes: a 52px header, browser back and the list scroll come back', async ({ page }) => {
  test.skip(!isCompactConsole(page), 'phone and tablet layout');
  await mockSite(page);
  await page.setViewportSize({ width: 390, height: 520 });
  await page.goto('./?page=console');
  const row = page.getByRole('link', { name: /^資料狀態/ });
  await expect(row).toBeVisible();
  const main = page.locator('#main');
  await main.evaluate(element => element.scrollTo({ top: 90 }));
  await expect.poll(() => main.evaluate(element => element.scrollTop)).toBeGreaterThan(40);
  // Playwright scrolls a target into view before clicking it, by an amount that differs between engines. Read the
  // list scroll and tap the row in one step so `before` is exactly the scroll the tap leaves.
  await row.evaluate(element => element.scrollIntoView({ block: 'center' }));
  const before = await row.evaluate(element => {
    const top = document.getElementById('main')!.scrollTop;
    (element as HTMLElement).click();
    return top;
  });
  expect(before).toBeGreaterThan(40);
  await expect(page).toHaveURL(/page=console&view=status/);
  const back = page.getByRole('button', { name: '返回我的', exact: true });
  // The tap is a DOM click, so measure once the sub-view has settled; round away sub-pixel float noise.
  const size = async (locator: typeof back) => { const box = (await locator.boundingBox())!; return { w: Math.round(box.width), h: Math.round(box.height) }; };
  await expect.poll(async () => (await size(page.locator('.me-subhead'))).h).toBe(52);
  expect((await size(back)).w).toBeGreaterThanOrEqual(44);
  expect((await size(back)).h).toBeGreaterThanOrEqual(44);
  await expect(page.locator('.me-subhead h2')).toHaveText('資料狀態');
  await expect(page.locator('.site-topbar')).toBeHidden();
  await expect(page.getByRole('complementary', { name: '目前儲存權限' })).toBeVisible();
  await expect(page.getByText('資料健康總覽')).toBeVisible();
  await expect(page.getByText('盤中每分鐘更新（Yahoo 延遲報價）')).toBeVisible();
  expect(await overflow(page)).toBe(0);
  await back.click();
  await expect(page).toHaveURL(/page=console$/);
  await expect(row).toBeVisible();
  expect(Math.abs(await main.evaluate(element => element.scrollTop) - before)).toBeLessThanOrEqual(2);
  await row.click();
  await expect(page.getByRole('button', { name: '返回我的', exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/page=console$/);
  await expect(row).toBeVisible();
  // A deep link into a sub-view has no list entry before it: back opens the list in place.
  await page.goto('./?page=console&view=groups');
  await page.getByRole('button', { name: '返回我的', exact: true }).click();
  await expect(page).toHaveURL(/page=console$/);
  await expect(page.getByRole('link', { name: /^自訂分組/ })).toBeVisible();
});

test('storage mode and data health no longer follow every page on phones and tablets', async ({ page }) => {
  test.skip(!isCompactConsole(page), 'phone and tablet layout');
  await mockSite(page);
  for (const route of ['watchlist', 'markets', 'screener']) {
    await page.goto(`./?page=${route}`);
    await expect(page.locator('#main')).toBeVisible();
    await expect(page.getByRole('complementary', { name: '目前儲存權限' })).toHaveCount(0);
    await expect(page.locator('.data-health')).toHaveCount(0);
  }
});

test('tablet 我的 keeps its column at 640px and centred', async ({ page }) => {
  test.skip(!isCompactConsole(page), 'phone and tablet layout');
  await mockSite(page);
  await page.setViewportSize({ width: 820, height: 1180 });
  await page.goto('./?page=console');
  await expect(page.getByRole('link', { name: /^個股均線參數/ })).toBeVisible();
  const group = (await page.locator('.me-group').first().boundingBox())!;
  const card = (await page.locator('.storage-policy-account').boundingBox())!;
  expect(group.width).toBeLessThanOrEqual(640);
  expect(Math.abs(group.x + group.width / 2 - 410)).toBeLessThanOrEqual(1);
  expect(Math.abs(card.x - group.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(card.width - group.width)).toBeLessThanOrEqual(1);
});

test('desktop 我的 keeps its tabs and the footer notices', async ({ page }) => {
  test.skip(isCompactConsole(page), 'desktop layout');
  await mockSite(page);
  await page.goto('./?page=console');
  await expect(page.getByRole('group', { name: '控制台分頁' })).toBeVisible();
  await expect(page.locator('.me-row')).toHaveCount(0);
  await expect(page.locator('.site-data-footer').getByRole('complementary', { name: '目前儲存權限' })).toHaveCount(1);
  await expect(page.locator('.site-data-footer .data-health')).toHaveCount(1);
});
