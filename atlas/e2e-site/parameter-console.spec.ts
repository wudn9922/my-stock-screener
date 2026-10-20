import { expect, test, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import type { SavedWatchlist } from '../src/watchlists/model';
import type { ParameterStock } from '../src/cloud/settingsApi';

async function mockConsole(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgent', {
      get: () => 'Mozilla/5.0 Line/14.0.0',
    });
    (globalThis as unknown as { liff: unknown }).liff = {
      init: async () => {},
      isLoggedIn: () => true,
      getIDToken: () => 'test-token',
      getDecodedIDToken: () => ({
        sub: `U${'0'.repeat(32)}`,
        exp: Date.now() / 1000 + 3600,
      }),
    };
  });
  await mockSite(page, { report: buildReport(20) });
  const stocks: ParameterStock[] = Array.from({ length: 9 }, (_, i) => ({
    id: i + 1,
    ticker: `STOCK${i}`,
    group_id: i < 5 ? 'us_g1' : 'us_g2',
    ma1: 20,
    ma2: 50,
    ma3: 200,
    ma4: 0,
  }));
  const lists = new Map<string, { id: string; definition: SavedWatchlist; updated_at: string }>();
  const requests: Record<string, unknown>[] = [];
  const state = { failDelete: false, failSave: false, version: 0 };
  await page.route('**/functions/v1/user-settings', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    expect(body.idToken).toBe('test-token');
    let data: unknown = [];
    const error = (message: string, status: number) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify({ ok: false, error: message }),
      });
    switch (body.action) {
      case 'stocks.list':
        data = stocks;
        break;
      case 'stocks.save': {
        const mas = body.mas as number[];
        const index = stocks.findIndex((stock) =>
          body.id ? stock.id === body.id : stock.ticker === body.ticker && stock.group_id === body.groupId,
        );
        const stock = {
          id: index < 0 ? 100 + stocks.length : stocks[index].id,
          ticker: String(body.ticker),
          group_id: String(body.groupId),
          ma1: mas[0] ?? 0,
          ma2: mas[1] ?? 0,
          ma3: mas[2] ?? 0,
          ma4: mas[3] ?? 0,
        };
        if (index < 0) stocks.push(stock);
        else stocks[index] = stock;
        data = [{ id: stock.id }];
        break;
      }
      case 'stocks.delete': {
        if (state.failDelete) return error('刪除暫時失敗', 503);
        const index = stocks.findIndex((stock) => stock.id === body.id);
        if (index >= 0) stocks.splice(index, 1);
        break;
      }
      case 'watchlists.list':
        data = [...lists.values()];
        break;
      case 'watchlists.save': {
        if (state.failSave) return error('Supabase 尚未部署', 503);
        const definition = body.definition as SavedWatchlist;
        const previous = lists.get(definition.id);
        if ((previous?.updated_at ?? null) !== body.expectedUpdatedAt) return error('版本衝突', 409);
        const row = {
          id: definition.id,
          definition,
          updated_at: `2026-10-20T10:00:${String(++state.version).padStart(2, '0')}Z`,
        };
        lists.set(row.id, row);
        data = row;
        break;
      }
      case 'watchlists.delete': {
        if (state.failDelete) return error('雲端刪除暫時失敗', 503);
        const previous = lists.get(String(body.id));
        if (previous?.updated_at !== body.expectedUpdatedAt) return error('版本衝突', 409);
        lists.delete(String(body.id));
        break;
      }
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, data }),
    });
  });
  return { stocks, lists, requests, state };
}

async function createRulesList(page: Page) {
  await page.goto('./?page=screener&group=tw_all&f=chg:3:');
  await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
  await page.getByRole('textbox', { name: '自選清單名稱' }).fill('自動同步策略');
  await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
  await expect(page.getByRole('region', { name: '清單追蹤結果' })).toBeVisible();
}

test('list creation, monitoring changes and renaming sync automatically; deletion removes both copies', async ({ page }) => {
  const cloud = await mockConsole(page);
  await createRulesList(page);
  await expect.poll(() => cloud.lists.size).toBe(1);
  const current = () => [...cloud.lists.values()][0].definition;
  await expect.poll(() => current().history.length).toBeGreaterThan(0);
  await page.getByRole('switch', { name: '監控此清單的突破提醒' }).check();
  await expect.poll(() => current().monitorBreakouts).toBe(true);
  await page.getByRole('textbox', { name: '清單突破監控均線' }).fill('13, 34');
  await page.getByRole('textbox', { name: '清單突破監控均線' }).press('Tab');
  await expect.poll(() => current().maList).toEqual([13, 34]);
  await page.getByText('清單設定與 Supabase 同步', { exact: true }).click();
  await page.getByRole('textbox', { name: '重新命名清單' }).fill('重新命名策略');
  await page.getByRole('button', { name: '儲存名稱', exact: true }).click();
  await expect.poll(() => current().name).toBe('重新命名策略');
  cloud.state.failDelete = true;
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: '刪除清單', exact: true }).click();
  await expect(page.locator('.console-message').last()).toContainText('雲端刪除暫時失敗');
  expect(cloud.lists.size).toBe(1);
  await expect(page.getByRole('group', { name: '我的自選清單' }).getByRole('button')).toHaveCount(1);
  cloud.state.failDelete = false;
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: '刪除清單', exact: true }).click();
  await expect.poll(() => cloud.lists.size).toBe(0);
  await expect(page.getByText('還沒有條件清單', { exact: true })).toBeVisible();
});

test('each rule-list member can save permanent ungrouped parameters that survive removal of the list', async ({ page }) => {
  const cloud = await mockConsole(page);
  await createRulesList(page);
  const button = page.getByRole('button', { name: /^設定 .+ 固定均線參數$/ }).first();
  const ticker = (await button.getAttribute('aria-label'))!.split(' ')[1];
  await button.click();
  const dialog = page.getByRole('dialog', {
    name: `${ticker} 固定均線參數`,
    exact: true,
  });
  await dialog.getByRole('textbox', { name: '個股固定均線參數' }).fill('13, 34, 89');
  await dialog.getByRole('button', { name: '確認並永久儲存' }).click();
  await expect(dialog).not.toBeVisible();
  expect(cloud.stocks.find((stock) => stock.ticker === ticker && stock.group_id === 'ungrouped')).toMatchObject({
    ma1: 13,
    ma2: 34,
    ma3: 89,
  });
  await page.reload();
  await expect(page.getByRole('button', { name: `設定 ${ticker} 固定均線參數` })).toContainText('13 / 34 / 89');
  page.once('dialog', (confirmation) => confirmation.accept());
  await page.getByRole('button', { name: '刪除清單', exact: true }).click();
  await expect(page.getByText('還沒有條件清單', { exact: true })).toBeVisible();
  expect(cloud.stocks.some((stock) => stock.ticker === ticker && stock.group_id === 'ungrouped')).toBe(true);
});

test('existing parameter stocks support search, group filters, a five-row scroll area and modal edit/delete', async ({ page }) => {
  const cloud = await mockConsole(page);
  await page.goto('./?page=console');
  await page.getByRole('button', { name: '均線參數', exact: true }).click();
  const table = page.getByRole('table', { name: '雲端股票參數' });
  await expect(table.locator('tbody tr')).toHaveCount(9);
  const viewport = page.getByRole('region', { name: '已有參數股票清單' });
  if (process.env.SCREENSHOT_DIR)
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/parameter-list-${test.info().project.name}.png` });
  expect(await viewport.evaluate((node) => node.scrollHeight > node.clientHeight && node.clientHeight <= 380)).toBe(true);
  await page.getByRole('combobox', { name: '已有參數股票組別' }).selectOption('us_g2');
  await expect(table.locator('tbody tr')).toHaveCount(4);
  await page.getByRole('searchbox', { name: '搜尋已有參數股票' }).fill('STOCK8');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  const row = table.locator('tbody tr').first();
  await row.getByRole('button', { name: '編輯', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '編輯 STOCK8 均線參數' });
  if (process.env.SCREENSHOT_DIR)
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/parameter-edit-${test.info().project.name}.png` });
  await dialog.getByRole('textbox', { name: '編輯股票均線參數' }).fill('7, 21');
  await dialog.getByRole('button', { name: '取消編輯' }).click();
  expect(cloud.requests.filter((request) => request.action === 'stocks.save')).toHaveLength(0);
  await row.getByRole('button', { name: '編輯', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '編輯 STOCK8 均線參數' });
  await dialog.getByRole('textbox', { name: '編輯股票均線參數' }).fill('7, 21');
  await dialog.getByRole('button', { name: '確認編輯' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(row).toContainText('7 / 21');
  await row.getByRole('button', { name: '刪除', exact: true }).click();
  const confirmation = page.getByRole('dialog', {
    name: '確定刪除？',
    exact: true,
  });
  await expect(confirmation).toContainText('STOCK8');
  await confirmation.getByRole('button', { name: '取消刪除' }).click();
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '刪除', exact: true }).click();
  cloud.state.failDelete = true;
  await confirmation.getByRole('button', { name: '確認刪除' }).click();
  await expect(confirmation.getByRole('alert')).toContainText('刪除暫時失敗');
  await expect(confirmation).toBeVisible();
  cloud.state.failDelete = false;
  await confirmation.getByRole('button', { name: '確認刪除' }).click();
  await expect(confirmation).not.toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test('a missing Supabase deployment preserves a new list and offers a retry', async ({ page }) => {
  const cloud = await mockConsole(page);
  cloud.state.failSave = true;
  await createRulesList(page);
  await expect(page.locator('.console-message').last()).toContainText('自動同步未完成');
  await expect(page.locator('.console-message').last()).toContainText('本機');
  await page.getByText('清單設定與 Supabase 同步', { exact: true }).click();
  cloud.state.failSave = false;
  await page.getByRole('button', { name: '重試同步', exact: true }).click();
  await expect.poll(() => cloud.lists.size).toBe(1);
});
