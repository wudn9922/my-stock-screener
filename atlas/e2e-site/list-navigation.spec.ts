import { copyFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { buildReport, mockSite } from './mocks';

// Set SCREENSHOT_DIR to also copy the phone picture of the list bar there (docs/screenshots/list-nav).
const shotDir = process.env.SCREENSHOT_DIR;

const primary = (page: Page) => page.getByRole('navigation', { name: '主要頁面' }).filter({ visible: true });
const listBar = (page: Page) => page.getByRole('navigation', { name: '清單切換' });
const previous = (page: Page) => listBar(page).getByRole('button', { name: '上一檔', exact: true });
const next = (page: Page) => listBar(page).getByRole('button', { name: '下一檔', exact: true });
const param = (page: Page, name: string) => new URL(page.url()).searchParams.get(name);
const mainScroll = (page: Page) => page.locator('#main').evaluate((main) => main.scrollTop);
const chips = (page: Page) => page.getByRole('group', { name: '已套用的篩選條件' });
const rowSymbols = (rows: Locator) => rows.evaluateAll((links) =>
  links.map((link) => new URL((link as HTMLAnchorElement).href).searchParams.get('symbol')!));

async function expectChart(page: Page, symbol: string) {
  await expect.poll(() => param(page, 'symbol')).toBe(symbol);
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
}

async function screener(page: Page, filter: string) {
  await mockDeviceAccount(page);
  await mockSite(page, { report: buildReport(600) });
  await page.goto(`./?page=screener&group=tw_all&f=${filter}`);
  await expect(page.locator('.screener-row').first()).toBeVisible();
}

/** A touch swipe as pointer events (Chromium/WebKit headless have no swipe gesture API). */
async function swipe(target: Locator, dx: number) {
  const box = (await target.boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  const init = { pointerId: 7, pointerType: 'touch', isPrimary: true, bubbles: true };
  await target.dispatchEvent('pointerdown', { ...init, clientX: x, clientY: y });
  await target.dispatchEvent('pointerup', { ...init, clientX: x + dx, clientY: y + 4 });
}

test('a filtered screener result steps through its stocks in order on the chart, keeping timeframe and view', async ({ page }, info) => {
  await screener(page, 'vol:59000000');
  const rows = page.locator('.screener-row');
  await expect(rows).toHaveCount(11);
  const symbols = await rowSymbols(rows);
  expect(new Set(symbols).size).toBe(11);
  const listName = (await page.locator('.screener-list').getAttribute('aria-label'))!.replace(/股票清單$/, '');

  await rows.first().click();
  await expectChart(page, symbols[0]!);
  await expect(listBar(page)).toContainText(`第 1／11 檔 · ${listName}`);
  await expect(previous(page)).toBeDisabled();
  await expect(next(page)).toBeEnabled();
  // The URL stays a plain chart link; the list lives only in this navigation.
  expect([...new URL(page.url()).searchParams.keys()].sort()).toEqual(['page', 'symbol', 'tf']);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');

  await next(page).click();
  await expectChart(page, symbols[1]!);
  await expect(listBar(page)).toContainText('第 2／11 檔');
  await expect(previous(page)).toBeEnabled();

  // Timeframe and the K線／價量／研究 view survive a step.
  await page.getByRole('group', { name: 'K 線週期' }).getByRole('button', { name: '週期 1W', exact: true }).click();
  await expect.poll(() => param(page, 'tf')).toBe('1W');
  await page.getByTestId('stock-tab-volume').click();
  await expect(page.getByTestId('stock-tab-volume')).toHaveAttribute('aria-current', 'page');
  await next(page).click();
  await expectChart(page, symbols[2]!);
  expect(param(page, 'tf')).toBe('1W');
  await expect(page.getByTestId('stock-tab-volume')).toHaveAttribute('aria-current', 'page');
  await page.getByTestId('stock-tab-chart').click();

  if (info.project.name === 'desktop-chromium') {
    // `[` / `]` on the desktop; ignored while typing in a field.
    await page.locator('.site-brand').focus();
    await page.keyboard.press(']');
    await expectChart(page, symbols[3]!);
    await page.keyboard.press('[');
    await page.keyboard.press('[');
    await expectChart(page, symbols[1]!);
    const searchBox = page.locator('.site-topbar').getByRole('combobox', { name: '搜尋股票' });
    await searchBox.focus();
    await page.keyboard.press(']');
    await expect(searchBox).toHaveValue(']');
    expect(param(page, 'symbol')).toBe(symbols[1]);
    await searchBox.fill('');
  } else {
    // Phones: a horizontal swipe on the bar steps; the same swipe on the chart canvas is left to the chart.
    await swipe(listBar(page), -120);
    await expectChart(page, symbols[3]!);
    await swipe(listBar(page), 120);
    await swipe(listBar(page), 120);
    await expectChart(page, symbols[1]!);
    await swipe(page.getByTestId('chart'), -160);
    await page.waitForTimeout(300);
    expect(param(page, 'symbol')).toBe(symbols[1]);
    // A mostly vertical drag is not a swipe.
    const box = (await listBar(page).boundingBox())!;
    await listBar(page).dispatchEvent('pointerdown', { pointerId: 8, pointerType: 'touch', isPrimary: true, clientX: box.x + 100, clientY: box.y + 10 });
    await listBar(page).dispatchEvent('pointerup', { pointerId: 8, pointerType: 'touch', isPrimary: true, clientX: box.x + 160, clientY: box.y + 120 });
    await page.waitForTimeout(300);
    expect(param(page, 'symbol')).toBe(symbols[1]);
  }

  // Back returns to the list as it was; opening the last stock disables 下一檔.
  await page.goBack();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'screener');
  await expect(rows).toHaveCount(11);
  expect(param(page, 'f')).toBe('vol:59000000');
  await rows.last().click();
  await expectChart(page, symbols[10]!);
  await expect(listBar(page)).toContainText('第 11／11 檔');
  await expect(next(page)).toBeDisabled();
  await previous(page).click();
  await expectChart(page, symbols[9]!);

  if (info.project.name !== 'desktop-chromium') {
    await page.setViewportSize({ width: 375, height: 812 });
    await expect(listBar(page)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    expect(await page.locator('#main').evaluate((main) => main.scrollWidth - main.clientWidth)).toBeLessThanOrEqual(0);
    const barBox = (await listBar(page).boundingBox())!;
    expect(barBox.x).toBeGreaterThanOrEqual(0);
    expect(barBox.x + barBox.width).toBeLessThanOrEqual(375);
    const file = info.outputPath('chart-list-bar-375.png');
    await page.screenshot({ path: file, clip: { x: 0, y: 0, width: 375, height: Math.min(812, barBox.y + barBox.height + 260) } });
    if (shotDir) {
      mkdirSync(resolve(shotDir), { recursive: true });
      copyFileSync(file, resolve(shotDir, `chart-list-bar-375-${info.project.name}.png`));
    }
  }

  // A reload keeps the chart but has no list context, and none is guessed.
  await page.reload();
  await expectChart(page, symbols[9]!);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await expect(listBar(page)).toHaveCount(0);
});

test('a chart opened from search has no list bar, and financials of a listed stock keep it', async ({ page }) => {
  await screener(page, 'vol:59000000');
  const rows = page.locator('.screener-row');
  const symbols = await rowSymbols(rows);
  await rows.nth(4).click();
  await expectChart(page, symbols[4]!);
  await expect(listBar(page)).toContainText('第 5／11 檔');
  await page.getByRole('button', { name: '財務', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'financials');
  await expect(listBar(page)).toContainText('第 5／11 檔');
  await next(page).click();
  await expect.poll(() => param(page, 'symbol')).toBe(symbols[5]);
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'financials');
  await expect(listBar(page)).toContainText('第 6／11 檔');
  await page.getByRole('button', { name: 'K線', exact: true }).click();
  await expectChart(page, symbols[5]!);
  await expect(listBar(page)).toContainText('第 6／11 檔');
  // Search picks a stock on its own: no list.
  await page.getByRole('button', { name: /^切換股票（目前/ }).click();
  const sheet = page.getByRole('dialog', { name: '搜尋股票' });
  const input = sheet.getByRole('combobox', { name: '搜尋股票' });
  await input.fill('2330');
  await expect(page.locator('.search-results [role="option"]').first()).toBeVisible();
  await input.press('Enter');
  await expectChart(page, '2330.TW');
  await expect(listBar(page)).toHaveCount(0);
});

test('a filtered, scrolled screener survives the chart and Back, then a repeated 選股 tap resets it', async ({ page }) => {
  await screener(page, 'vol:30000000');
  const list = page.locator('.screener-list');
  await expect(page.locator('.result-count')).toContainText('301');
  await list.evaluate((element) => { element.scrollTop = 4000; });
  await page.locator('#main').evaluate((main) => { main.scrollTop = main.scrollHeight; });
  await expect.poll(() => list.evaluate((element) => element.scrollTop)).toBe(4000);
  const pageScroll = await mainScroll(page);
  const visibleRow = page.locator('.screener-row').filter({ visible: true }).nth(3);
  const symbol = new URL((await visibleRow.getAttribute('href'))!, page.url()).searchParams.get('symbol')!;
  // A plain click would first scroll the row into view; the positions above are what must come back.
  await visibleRow.dispatchEvent('click');
  await expectChart(page, symbol);
  await expect(listBar(page)).toContainText('／301 檔');

  // Browser Back: same filters, results and both scroll positions.
  await page.goBack();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'screener');
  await expect(chips(page)).toBeVisible();
  expect(param(page, 'f')).toBe('vol:30000000');
  await expect(page.locator('.result-count')).toContainText('301');
  expect(await list.evaluate((element) => element.scrollTop)).toBe(4000);
  expect(await mainScroll(page)).toBe(pageScroll);
  await expect(page.locator(`.screener-row[href*="symbol=${encodeURIComponent(symbol)}"]`)).toBeVisible();

  // Through the bottom bar: away to the chart and back to 選股 restores the same view.
  await primary(page).getByRole('button', { name: '個股', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await primary(page).getByRole('button', { name: '選股', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'screener');
  expect(param(page, 'group')).toBe('tw_all');
  expect(param(page, 'f')).toBe('vol:30000000');
  await expect(chips(page)).toBeVisible();
  expect(await list.evaluate((element) => element.scrollTop)).toBe(4000);
  expect(await mainScroll(page)).toBe(pageScroll);

  // Tapping 選股 again: default group, no filters, at the top; the URL follows.
  await primary(page).getByRole('button', { name: '選股', exact: true }).click();
  await expect(chips(page)).toHaveCount(0);
  await expect.poll(() => param(page, 'f')).toBeNull();
  expect(param(page, 'group')).toBeNull();
  await expect(page.locator('.screener-row').first()).toBeVisible();
  expect(await list.evaluate((element) => element.scrollTop)).toBe(0);
  expect(await mainScroll(page)).toBe(0);
  // At the default already: another tap only scrolls to the top.
  await list.evaluate((element) => { element.scrollTop = 640; });
  await expect.poll(() => list.evaluate((element) => element.scrollTop)).toBe(640);
  const url = page.url();
  await primary(page).getByRole('button', { name: '選股', exact: true }).click();
  await expect.poll(() => list.evaluate((element) => element.scrollTop)).toBe(0);
  expect(page.url()).toBe(url);
});

test('an open watchlist survives a stock chart and returns through 自選; a repeated 自選 tap shows the list overview', async ({ page }) => {
  await mockDeviceAccount(page);
  await mockSite(page, { fundamentals: true });
  await page.goto('./?page=watchlist');
  for (const [name, symbols] of [['導覽甲', 'NVDA, AAPL'], ['導覽乙', 'MSFT, NVDA, AAPL']] as const) {
    await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
    await page.getByRole('textbox', { name: '手動清單名稱' }).fill(name);
    await page.getByRole('combobox', { name: '手動清單市場' }).selectOption('US');
    await page.getByRole('textbox', { name: '手動清單股票' }).fill(symbols);
    await page.getByRole('button', { name: '儲存手動清單', exact: true }).click();
    await expect(page.getByRole('region', { name: '清單追蹤結果' }).getByRole('heading', { name })).toBeVisible();
  }
  const picker = page.getByRole('combobox', { name: '選擇自選清單' });
  const listId = param(page, 'list')!;
  expect(listId).toMatch(/^[0-9a-f-]{36}$/);
  await expect(picker).toHaveValue(listId);
  const board = page.getByRole('region', { name: '自選行情' });
  const quoteRows = board.getByRole('list', { name: '自選行情列表' }).getByRole('link');
  await expect(quoteRows).toHaveCount(3);
  await quoteRows.nth(1).click();
  await expectChart(page, 'NVDA');
  await expect(listBar(page)).toContainText('第 2／3 檔 · 導覽乙');
  await next(page).click();
  await expectChart(page, 'AAPL');
  await expect(next(page)).toBeDisabled();

  // Back to 自選 through the bottom bar: still in that list.
  await primary(page).getByRole('button', { name: '自選', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'watchlist');
  expect(param(page, 'list')).toBe(listId);
  await expect(picker).toHaveValue(listId);
  await expect(quoteRows).toHaveCount(3);
  await expect(page.locator('.site-data-footer')).toContainText('清單：導覽乙');

  // Browser Back from a chart lands on the same list as well.
  await quoteRows.first().click();
  await expectChart(page, 'MSFT');
  await page.goBack();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'watchlist');
  expect(param(page, 'list')).toBe(listId);
  await expect(picker).toHaveValue(listId);

  // A repeated 自選 tap: the overview (default list), saved lists untouched.
  await primary(page).getByRole('button', { name: '自選', exact: true }).click();
  await expect.poll(() => param(page, 'list')).toBeNull();
  await expect(picker).not.toHaveValue(listId);
  await expect(picker.getByRole('option')).toHaveCount(2);
  await expect(page.locator('.site-data-footer')).toContainText('清單：導覽甲');
  expect(await mainScroll(page)).toBe(0);
});

test('hidden pages issue no data requests or live polls while another page is open', async ({ page }) => {
  // 10:30 Taipei on a weekday: Taiwan quotes poll once a minute while subscribed.
  await page.clock.install({ time: new Date('2026-10-27T02:30:00Z') });
  await mockDeviceAccount(page);
  await mockSite(page, { report: buildReport(600), fundamentals: true });
  const requests: string[] = [];
  page.on('request', (request) => {
    const action = request.url().includes('/functions/v1/user-settings') ? (request.postDataJSON() as { action?: string } | null)?.action : undefined;
    requests.push(action ? `settings:${action}` : request.url());
    if (action === 'access.get') requests.pop();
  });
  await page.goto('./?page=watchlist');
  await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
  await page.getByRole('textbox', { name: '手動清單名稱' }).fill('盤中');
  await page.getByRole('textbox', { name: '手動清單股票' }).fill('2330.TW, 2317.TW');
  await page.getByRole('button', { name: '儲存手動清單', exact: true }).click();
  const quoteRows = page.getByRole('region', { name: '自選行情' }).getByRole('list', { name: '自選行情列表' }).getByRole('link');
  await expect(quoteRows).toHaveCount(2);
  const polls = (from: number) => requests.slice(from).filter((url) => url.includes('quotes=') && decodeURIComponent(url).includes('2317.TW'));
  // While on screen the rows take part in the minute refresh (saving leaves phones scrolled to the form).
  await quoteRows.nth(1).scrollIntoViewIfNeeded();
  await expect.poll(() => polls(0).length).toBeGreaterThan(0);
  let mark = requests.length;
  await page.clock.fastForward('01:10');
  await expect.poll(() => polls(mark).length).toBeGreaterThan(0);

  await primary(page).getByRole('button', { name: '選股', exact: true }).click();
  await page.getByRole('button', { name: '台股全市場篩選', exact: true }).click();
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await page.locator('.screener-row').first().click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'chart');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  mark = requests.length;
  // Three minutes on the chart, a focus/visibility return and a list step: nothing for the hidden pages.
  await page.clock.fastForward('03:00');
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
  await next(page).click();
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await page.clock.fastForward('01:10');
  // (The site-wide account check on focus, access.get, is not a page request.)
  const hiddenData = requests.slice(mark).filter((url) =>
    /report\/(latest|universe|growth|trending-stocks|monitor)/.test(url) || /valuation\//.test(url) || url.startsWith('settings:'));
  expect(hiddenData).toEqual([]);
  expect(polls(mark)).toEqual([]);
  expect(await page.locator('.kept-page').count()).toBe(0);

  // Returning subscribes again under the feed's own staleness rule.
  await primary(page).getByRole('button', { name: '自選', exact: true }).click();
  await expect(quoteRows).toHaveCount(2);
  await quoteRows.nth(1).scrollIntoViewIfNeeded();
  await expect.poll(() => polls(mark).length).toBeGreaterThan(0);
});
