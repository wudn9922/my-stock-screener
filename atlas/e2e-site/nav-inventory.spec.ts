import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount, OWNER_USER_ID } from './account-fixture';
import { researchFixtureBody } from './research-fixtures';
import type { SavedWatchlist } from '../src/watchlists/model';

const primary = (page: Page) => page.getByRole('navigation', { name: '主要頁面' }).filter({ visible: true });
const sections = (page: Page) => page.getByRole('navigation', { name: '市場分頁' });
const destinations = ['自選', '市場', '選股', '個股', '我的'];

async function fixtures(page: Page) {
  await mockSite(page, { fundamentals: true });
  for (const suffix of ['', '-tw']) {
    const body = readFileSync(new URL(`../src/report/fixtures/themes${suffix}.sample.json`, import.meta.url), 'utf8');
    await page.route(`**/report/themes${suffix}.json`, route => route.fulfill({ contentType: 'application/json', body }));
  }
  for (const [file, fixture] of [['rates', 'backend-sample-rates'], ['rates-history', 'backend-sample-rates-history']]) {
    await page.route(`**/report/${file}.json`, route => route.fulfill({ json: JSON.parse(readFileSync(new URL(`../tests/fixtures/rates/${fixture}.json`, import.meta.url), 'utf8')) }));
  }
  await page.route('**/report/research/**', route => {
    const path = new URL(route.request().url()).pathname.split('/research/')[1]!;
    return route.fulfill({ contentType: 'application/json', body: researchFixtureBody(path).toString() });
  });
  await page.route('**/report/financials/*.json', route => route.fulfill({ contentType: 'application/json',
    body: readFileSync(new URL('../tests/fixtures/financials/us-full.json', import.meta.url), 'utf8') }));
  await page.route('**/atlas/app-version.json', async route => route.fulfill({ json: {
    app: 'atlas', scope: '/my-stock-screener/atlas/', version: await page.locator('meta[name="atlas-build"]').getAttribute('content'),
  } }));
}

async function owner(page: Page) {
  await installLineAccount(page, OWNER_USER_ID);
  const lists = new Map<string, { id: string; definition: SavedWatchlist; updated_at: string }>();
  let version = 0;
  await page.route('**/functions/v1/chart-drawings', route => route.fulfill({ json: { ok: true, records: [] } }));
  await page.route('**/functions/v1/user-settings', route => {
    const body = route.request().postDataJSON();
    let data: unknown = [];
    if (body.action === 'access.get') data = accessEnvelope(OWNER_USER_ID, 'cloud', true).data;
    if (body.action === 'watchlists.list') data = [...lists.values()];
    if (body.action === 'watchlists.save') {
      const definition = body.definition as SavedWatchlist;
      data = { id: definition.id, definition, updated_at: new Date(Date.UTC(2026, 9, 27, 0, 0, ++version)).toISOString() };
      lists.set(definition.id, data as { id: string; definition: SavedWatchlist; updated_at: string });
    }
    if (body.action === 'watchlists.delete') lists.delete(body.id);
    return route.fulfill({ json: { ok: true, data } });
  });
  return lists;
}

async function search(page: Page, symbol: string, fromChart = false) {
  const sheetMode = fromChart || await page.locator('.site-tabbar').isVisible();
  if (fromChart) await page.getByRole('button', { name: /^切換股票（目前/ }).click();
  else if (sheetMode) await page.locator('.site-topbar').getByRole('button', { name: '搜尋股票', exact: true }).click();
  else await page.keyboard.press('/');
  const sheet = page.getByRole('dialog', { name: '搜尋股票' });
  if (sheetMode) await expect(sheet).toBeVisible();
  const input = sheetMode ? sheet.getByRole('combobox', { name: '搜尋股票' }) : page.locator('.site-topbar').getByRole('combobox', { name: '搜尋股票' });
  await expect(input).toBeFocused();
  await input.fill(symbol);
  await expect(page.locator('.search-results [role="option"]').first()).toBeVisible();
  await input.press('Enter');
  await expect(page.getByTestId('active-symbol')).toHaveText(symbol);
}

test('five destinations, 320/390px targets, top search, slash shortcut, gear and LINE login remain reachable', async ({ page }, info) => {
  await fixtures(page);
  await page.goto('./');
  await expect(primary(page).getByRole('button')).toHaveText(destinations);
  // The login bar no longer takes space on every page; it lives on 我的.
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toHaveCount(0);
  const original = page.viewportSize()!;
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(() => document.documentElement.style.setProperty('--safe-area-inset-bottom', '20px'));
    const bar = page.locator('.site-tabbar');
    await expect(bar.getByRole('button')).toHaveText(destinations);
    const measures = await bar.evaluate(node => ({
      padding: parseFloat(getComputedStyle(node).paddingBottom),
      targets: [...node.querySelectorAll('button')].map(button => ({
        width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height,
        top: button.getBoundingClientRect().top, label: parseFloat(getComputedStyle(button.querySelector('span')!).fontSize),
      })),
    }));
    expect(measures.targets).toHaveLength(5);
    expect(new Set(measures.targets.map(target => target.top)).size).toBe(1);
    expect(Math.max(...measures.targets.map(target => target.width)) - Math.min(...measures.targets.map(target => target.width))).toBeLessThanOrEqual(1);
    for (const target of measures.targets) { expect(target.height).toBeGreaterThanOrEqual(44); expect(target.width).toBeGreaterThanOrEqual(44); expect(target.label).toBeGreaterThanOrEqual(12); }
    expect(measures.padding).toBeGreaterThanOrEqual(20);
    const topSearch = page.locator('.site-topbar').getByRole('button', { name: '搜尋股票', exact: true });
    const box = await topSearch.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(44); expect(box!.height).toBeGreaterThanOrEqual(44);
    await topSearch.click();
    await expect(page.getByRole('dialog', { name: '搜尋股票' })).toBeVisible();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    if (width === 390) await page.screenshot({ path: info.outputPath('navigation-mobile-390.png') });
  }
  await page.setViewportSize(original);
  if (info.project.name === 'desktop-chromium') {
    await expect(page.locator('.site-rail')).toBeVisible();
    await expect(page.locator('.site-topbar').getByRole('combobox', { name: '搜尋股票' })).toBeVisible();
    await page.locator('.site-brand').focus();
    await page.keyboard.press('/');
    await expect(page.locator('.site-topbar').getByRole('combobox', { name: '搜尋股票' })).toBeFocused();
    await page.screenshot({ path: info.outputPath('navigation-desktop.png') });
  }
  await page.getByRole('button', { name: '顯示設定', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '顯示設定', exact: true });
  await expect(dialog.getByRole('region', { name: '安裝 Atlas', exact: true })).toBeVisible();
  await expect(dialog.getByRole('region', { name: '版本更新', exact: true })).toBeVisible();
  await expect(dialog).toContainText('報告日期');
  await expect(dialog).toContainText('產生時間');
  await expect(dialog.locator('summary')).toContainText('行情與儲存說明');
  await dialog.getByRole('button', { name: '關閉', exact: true }).click();
  await search(page, 'NVDA');
  await primary(page).getByRole('button', { name: '我的', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
});

test('market section inventory and remembered selectors, screener filters, chart/financial round trip', async ({ page }) => {
  test.setTimeout(180000);
  await fixtures(page);
  await page.goto('./');
  await primary(page).getByRole('button', { name: '市場', exact: true }).click();
  await expect(sections(page).getByRole('link')).toHaveText(['大盤', '全球', '題材']);
  await expect(sections(page).getByRole('link', { name: '大盤', exact: true })).toHaveAttribute('aria-current', 'page');
  for (const [key, label, section] of [['tw', '台灣', '大盤'], ['jp', '日本', '大盤'], ['kr', '韓國', '大盤'], ['eu', '歐洲', '大盤'], ['us', '美國', '大盤']]) {
    const tab = page.getByRole('tab', { name: new RegExp(label!) });
    await tab.click(); await expect(tab).toHaveAttribute('aria-selected', 'true');
    await expect(page).toHaveURL(new RegExp(`page=markets&market=${key}`));
    await expect(page.locator('.markets-layout')).toBeVisible();
    await expect(sections(page).getByRole('link', { name: section, exact: true })).toHaveAttribute('aria-current', 'page');
  }
  await page.getByRole('tab', { name: /台灣/ }).click();
  await expect(page.getByRole('tab', { name: /台灣/ })).toHaveAttribute('aria-selected', 'true');
  await sections(page).getByRole('link', { name: '全球', exact: true }).click();
  await expect(primary(page).getByRole('button', { name: '市場', exact: true })).toHaveAttribute('aria-current', 'page');
  for (const [view, label, content] of [['', '美債', '.rates-view'], ['indices', '指數', '.world-table-card'], ['commodities', '原物料', '.world-candle-card'], ['fx', '匯率', '.world-candle-card']]) {
    const button = page.getByRole('group', { name: '世界頁面' }).getByRole('button', { name: label, exact: true });
    await button.click(); await expect(button).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator(content!).first()).toBeVisible();
    expect(new URL(page.url()).searchParams.get('view')).toBe(view || null);
  }
  await sections(page).getByRole('link', { name: '題材', exact: true }).click();
  await expect(sections(page).getByRole('link', { name: '題材', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.theme-tile')).toHaveCount(30);
  await page.locator('.theme-tile', { hasText: '光通訊' }).click();
  await expect(page.getByRole('dialog', { name: /光通訊/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('tablist', { name: '市場' }).getByRole('tab', { name: /台股/ }).click();
  await expect(page.locator('.theme-tile')).toHaveCount(8);
  await page.getByRole('group', { name: '檢視' }).getByRole('button', { name: '熱力圖', exact: true }).click();
  await expect(page.locator('.themes-map')).toBeVisible();
  await sections(page).getByRole('link', { name: '大盤', exact: true }).click();
  await page.getByRole('tab', { name: /美國/ }).click();
  await expect(page.getByRole('tab', { name: /美國/ })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: /歐洲/ }).click();
  await expect(page.getByRole('tab', { name: /歐洲/ })).toHaveAttribute('aria-selected', 'true');
  await sections(page).getByRole('link', { name: '全球', exact: true }).click();
  await expect(page).toHaveURL(/view=fx/);
  await sections(page).getByRole('link', { name: '題材', exact: true }).click();
  await expect(page).toHaveURL(/page=themes&market=tw&v=map/);
  await primary(page).getByRole('button', { name: '選股', exact: true }).click();
  await page.getByRole('button', { name: /權值精選/ }).first().click();
  await expect(page.locator('.screener-row').first()).toBeVisible();
  await page.getByRole('button', { name: /篩選條件/ }).click();
  await expect(page.getByRole('dialog', { name: '篩選條件' })).toBeVisible();
  await page.getByRole('dialog', { name: '篩選條件' }).getByRole('button', { name: /^套用/ }).click();
  await primary(page).getByRole('button', { name: '個股', exact: true }).click();
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await search(page, 'NVDA', true);
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await page.getByTestId('chart').evaluate(node => { (window as unknown as { inventoryChart: Element }).inventoryChart = node; });
  await page.getByRole('button', { name: '財務', exact: true }).click();
  await expect(page.locator('.site')).toHaveAttribute('data-page', 'financials');
  await expect(page.locator('.financials-page')).toBeVisible();
  await expect(primary(page).getByRole('button', { name: '個股', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('button', { name: 'K線', exact: true }).click();
  await expect(page.getByTestId('active-symbol')).toHaveText('NVDA');
  expect(await page.getByTestId('chart').evaluate(node => (window as unknown as { inventoryChart: Element }).inventoryChart === node)).toBe(true);
});

test('watchlists retain actions and cloud lists; 我的 retains parameters, admin, display/App and gear', async ({ page }, info) => {
  test.setTimeout(180000);
  await fixtures(page);
  const cloud = await owner(page);
  await page.goto('./');
  await primary(page).getByRole('button', { name: '自選', exact: true }).click();
  await expect(page.locator('.page-title')).toHaveText('自選清單');
  await expect(page.getByRole('complementary', { name: '目前儲存權限' })).toBeVisible();
  await expect(page.getByRole('button', { name: '重新檢查舊版資料', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '以篩選條件建立清單', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '匯入備份', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '匯出清單備份', exact: true })).toBeEnabled();
  for (const [name, symbol] of [['導覽 NVDA', 'NVDA'], ['導覽 AAPL', 'AAPL']]) {
    await page.getByRole('button', { name: '建立手動清單', exact: true }).click();
    await page.getByRole('textbox', { name: '手動清單名稱' }).fill(name!);
    await page.getByRole('combobox', { name: '手動清單市場' }).selectOption('US');
    await page.getByRole('textbox', { name: '手動清單股票' }).fill(symbol!);
    await page.getByRole('button', { name: '儲存手動清單', exact: true }).click();
    await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText(symbol!);
    await expect.poll(() => cloud.size).toBe(symbol === 'NVDA' ? 1 : 2);
  }
  const listPicker = page.getByRole('combobox', { name: '選擇自選清單' });
  await listPicker.selectOption((await listPicker.getByRole('option', { name: /導覽 NVDA/ }).getAttribute('value'))!);
  const result = page.getByRole('region', { name: '清單追蹤結果' });
  await expect(result).toContainText('NVDA');
  await expect(result).toContainText('目前入選');
  await expect(result.getByRole('switch', { name: '監控此清單的突破提醒' })).toBeVisible();
  await expect(result.getByRole('textbox', { name: '清單突破監控均線' })).toBeVisible();
  await result.getByRole('button', { name: '設定 NVDA 固定均線參數' }).click();
  await expect(page.getByRole('dialog', { name: 'NVDA 固定均線參數' })).toBeVisible();
  await page.getByRole('button', { name: '取消編輯', exact: true }).click();
  await expect(page.getByText('SEC 8-K 重大事件雷達', { exact: false }).first()).toBeVisible();
  const exported = page.waitForEvent('download');
  await page.getByRole('button', { name: '匯出清單備份', exact: true }).click();
  const download = await exported;
  const payload = readFileSync((await download.path())!);
  page.once('dialog', dialog => dialog.accept());
  await page.locator('.console-page input[type="file"]').setInputFiles({ name: 'atlas-watchlists.json', mimeType: 'application/json', buffer: payload });
  await expect(page.getByRole('group', { name: '我的自選清單' }).getByRole('option')).toHaveCount(2);
  await page.getByText('載入與管理雲端清單', { exact: true }).click();
  await page.getByRole('button', { name: '載入雲端清單', exact: true }).click();
  await expect(page.getByRole('button', { name: '匯入本機', exact: true })).toHaveCount(2);
  await expect(page.getByRole('button', { name: '刪除雲端清單', exact: true })).toHaveCount(2);
  await primary(page).getByRole('button', { name: '我的', exact: true }).click();
  await expect(page.locator('.page-title')).toHaveText('我的');
  await expect(page.getByRole('link', { name: '自選清單 →', exact: true })).toBeVisible();
  await expect(page.getByRole('complementary', { name: '目前儲存權限' })).toBeVisible();
  await page.getByRole('button', { name: '均線參數', exact: true }).click();
  const parameters = page.getByRole('region', { name: '原有均線參數控制台' });
  await expect(parameters).toBeVisible();
  await expect(parameters.getByRole('textbox', { name: '參數股票代號' })).toBeVisible();
  await expect(parameters.locator('a[href*="liff.line.me"]').first()).toHaveAttribute('href', /page=console$/);
  await page.getByRole('button', { name: '管理員', exact: true }).click();
  await expect(page.getByRole('region', { name: '管理員儲存權限' })).toBeVisible();
  await page.getByRole('button', { name: '顯示與App', exact: true }).click();
  const display = page.getByRole('region', { name: '顯示與App', exact: true });
  await expect(display.getByRole('region', { name: '安裝 Atlas', exact: true })).toBeVisible();
  await expect(display.getByRole('region', { name: '版本更新', exact: true })).toBeVisible();
  await display.getByText('綠漲紅跌', { exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'us');
  await page.reload();
  await page.getByRole('button', { name: '顯示與App', exact: true }).click();
  await expect(display.getByRole('radio', { name: '綠漲紅跌' })).toBeChecked();
  await page.getByRole('button', { name: '顯示設定', exact: true }).click();
  const gear = page.getByRole('dialog', { name: '顯示設定', exact: true });
  await expect(gear.getByRole('radio', { name: '綠漲紅跌' })).toBeChecked();
  await gear.getByText('紅漲綠跌', { exact: true }).click();
  await expect(display.getByRole('radio', { name: '紅漲綠跌' })).toBeChecked();
  await gear.getByRole('button', { name: '關閉', exact: true }).click();
  await page.screenshot({ path: info.outputPath(info.project.name === 'desktop-chromium' ? 'my-display-desktop.png' : 'my-display-mobile-390.png') });
  await page.getByRole('link', { name: '自選清單 →', exact: true }).click();
  await expect(page.locator('.page-title')).toHaveText('自選清單');
  await expect(page.getByRole('group', { name: '我的自選清單' }).getByRole('option')).toHaveCount(2);
});
