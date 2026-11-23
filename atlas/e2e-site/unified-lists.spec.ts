import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount } from './account-fixture';
import { isCompactConsole } from './console-nav';
import type { ParameterGroup, ParameterStock } from '../src/cloud/settingsApi';

const SHOTS = process.env.ATLAS_SCREENSHOT_DIR;
const recommendation = {
  ticker: '2303.TW', kind: 'SMA', asof: '2026-10-17',
  ranges: [
    { key: 'short', min: 15, max: 33, final: 24, candidates: [] },
    { key: 'medium', min: 34, max: 45, final: 38, candidates: [] },
    { key: 'long', min: 46, max: 110, final: 89, candidates: [] },
  ],
};

/** Cloud account with server groups/stocks; `fail` makes the next writes answer 503. */
async function cloudLists(page: Page) {
  await mockSite(page);
  await installLineAccount(page);
  const groups: ParameterGroup[] = [{ id: 7, name: '波段', ma1: 5, ma2: 10, ma3: 20, ma4: 60 }];
  const stocks: ParameterStock[] = [];
  const writes: Record<string, unknown>[] = [];
  const state = { fail: false, nextId: 40 };
  await page.route('**/functions/v1/chart-drawings', route => route.fulfill({ json: { ok: true, records: [] } }));
  await page.route('**/my-stock-screener/report/ma-selector/2303.TW.json', route => route.fulfill({ json: recommendation }));
  await page.route('**/functions/v1/user-settings', async route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    let data: unknown = [];
    if (body.action === 'groups.list') data = groups;
    if (body.action === 'stocks.list') data = stocks;
    if (body.action === 'stocks.save') {
      writes.push(body);
      if (state.fail) return route.fulfill({ status: 503, json: { ok: false, error: '個股固定參數尚未啟用' } });
      const mas = body.mas as number[];
      const id = Number(body.id ?? state.nextId++);
      const row = { id, ticker: String(body.ticker), group_id: String(body.groupId), ma1: mas[0] ?? 0, ma2: mas[1] ?? 0, ma3: mas[2] ?? 0, ma4: mas[3] ?? 0 };
      const index = stocks.findIndex(entry => entry.id === id);
      if (index < 0) stocks.push(row); else stocks[index] = row;
      data = [{ id }];
    }
    return route.fulfill({ json: { ok: true, data } });
  });
  return { stocks, writes, state };
}

test('chart star opens 加入清單: one tap per list, 建議均線 for a new group member, rollback on failure', async ({ page }, testInfo) => {
  const cloud = await cloudLists(page);
  await page.goto('./?page=chart&symbol=2303.TW&tf=1D');
  const star = page.getByTestId('list-star').filter({ visible: true });
  await expect(star).toHaveAttribute('aria-label', '加入清單：2303.TW');
  await expect(star).toBeEnabled();
  await star.click();
  const sheet = page.getByRole('dialog', { name: '加入清單' });
  await expect(sheet).toBeVisible();
  await expect(star).toHaveAttribute('aria-expanded', 'true');
  const option = (name: string) => sheet.getByRole('checkbox', { name: new RegExp(`^${name}`) });
  await expect(option('台股權值')).toBeVisible();
  await expect(sheet.locator('.list-option').filter({ hasText: '台股權值' }).locator('.list-badge')).toHaveText('提醒／日報');

  await option('圖表收藏').check();
  await expect(option('圖表收藏')).toBeChecked();
  await option('台股權值').check();
  await expect(sheet.getByRole('status')).toContainText('24 / 38 / 89');
  expect(cloud.writes.at(-1)).toMatchObject({ ticker: '2303.TW', groupId: 'tw_g1', mas: [24, 38, 89] });

  // One group per stock: joining 波段 moves the same row (and its MAs) out of 台股權值.
  await option('波段').check();
  await expect(option('台股權值')).not.toBeChecked();
  await expect(option('波段')).toBeChecked();
  await expect.poll(() => cloud.stocks).toEqual([{ id: 40, ticker: '2303.TW', group_id: 'custom_7', ma1: 24, ma2: 38, ma3: 89, ma4: 0 }]);
  if (SHOTS && testInfo.project.name === 'mobile-chromium') await page.screenshot({ path: `${SHOTS}/star-list-picker-390.png` });

  cloud.state.fail = true;
  await option('波段').uncheck();
  await expect(sheet.getByRole('alert')).toContainText('「波段」未更新');
  await expect(option('波段')).toBeChecked();
  expect(cloud.stocks[0]!.group_id).toBe('custom_7');

  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();
  await expect(star).toBeFocused();
  await expect(star).toHaveAttribute('aria-label', /^已在清單中/);
});

test('我的 › 清單 manages groups, watchlists and favourites in one place; the old 自訂分組 link opens it', async ({ page }, testInfo) => {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/ma-selector/**', route => route.fulfill({ status: 404, body: 'not found' }));
  await page.goto('./?page=console&view=groups');
  const manager = page.getByRole('region', { name: '清單管理' });
  await expect(manager).toBeVisible();
  if (isCompactConsole(page)) {
    await expect(page.locator('.me-subhead h2')).toHaveText('清單');
    await expect(page).toHaveURL(/view=lists/);
  } else await expect(page.getByRole('button', { name: '清單', exact: true })).toHaveAttribute('aria-pressed', 'true');

  const create = manager.getByRole('form', { name: '新增清單' });
  await create.getByRole('textbox', { name: '清單名稱' }).fill('短線');
  await create.getByRole('combobox', { name: '清單類型' }).selectOption('group');
  await create.getByRole('button', { name: '建立清單' }).click();
  const group = manager.locator('.list-card').filter({ hasText: '短線' });
  await expect(group).toContainText('分組');
  await group.locator('summary').click();
  await group.getByRole('textbox', { name: '加入股票到 短線' }).fill('2330.tw');
  await group.getByRole('button', { name: '加入', exact: true }).click();
  await expect(group.getByRole('list', { name: '短線 的股票' })).toContainText('2330');
  await expect(group.locator('.list-card-mas')).toHaveText('20 / 50 / 200');
  await expect(group.getByRole('button', { name: '刪除清單' })).toBeDisabled();

  await create.getByRole('textbox', { name: '清單名稱' }).fill('AI');
  await create.getByRole('combobox', { name: '清單類型' }).selectOption('watchlist');
  await create.getByRole('combobox', { name: '清單市場' }).selectOption('US');
  await create.getByRole('button', { name: '建立清單' }).click();
  const watchlist = manager.locator('.list-card').filter({ hasText: 'AI' });
  await watchlist.locator('summary').click();
  await watchlist.getByRole('textbox', { name: '加入股票到 AI' }).fill('2317.TW');
  await watchlist.getByRole('button', { name: '加入', exact: true }).click();
  await expect(manager.getByRole('alert')).toContainText('僅限美股');
  await watchlist.getByRole('textbox', { name: '加入股票到 AI' }).fill('NVDA');
  await watchlist.getByRole('button', { name: '加入', exact: true }).click();
  await expect(watchlist.getByRole('list', { name: 'AI 的股票' })).toContainText('NVDA');
  await expect(manager.locator('.list-card').filter({ hasText: '圖表收藏' })).toBeVisible();
  if (SHOTS && testInfo.project.name === 'mobile-chromium') {
    await watchlist.locator('summary').click();
    await manager.locator('.list-manager-lists').evaluate(element => element.scrollIntoView({ block: 'start' }));
    await page.screenshot({ path: `${SHOTS}/unified-list-manager-390.png` });
  }
  expect(await page.locator('#main').evaluate(element => element.scrollWidth - element.clientWidth)).toBe(0);

  // Leaving a group keeps the stock's MAs (ungrouped); then the empty group can be deleted.
  await group.getByRole('button', { name: '從 短線 移除 2330.TW' }).click();
  await expect(group).toContainText('還沒有股票');
  page.once('dialog', dialog => void dialog.accept());
  await group.getByRole('button', { name: '刪除清單' }).click();
  await expect(manager.locator('.list-card').filter({ hasText: '短線' })).toHaveCount(0);
});

test('清單 notifications: 所有清單都通知 by default, then MA / drawing per list with rollback on a failed save', async ({ page }, testInfo) => {
  await mockSite(page);
  await installLineAccount(page);
  const saves: unknown[] = [];
  const state = { fail: false, stored: null as unknown };
  await page.route('**/functions/v1/chart-drawings', route => route.fulfill({ json: { ok: true, records: [] } }));
  await page.route('**/functions/v1/user-settings', async route => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    if (body.action === 'alerts.settings.get') return route.fulfill({ json: { ok: true, data: { settings: state.stored ?? { version: 1, mode: 'all', lists: {} } } } });
    if (body.action === 'alerts.settings.save') {
      saves.push(body.settings);
      if (state.fail) return route.fulfill({ status: 503, json: { ok: false, error: '測試寫入失敗' } });
      state.stored = body.settings;
      return route.fulfill({ json: { ok: true, data: { settings: body.settings } } });
    }
    const data = body.action === 'groups.list' ? [{ id: 7, name: '波段', ma1: 5, ma2: 10, ma3: 20, ma4: 60 }]
      : body.action === 'stocks.list' ? [{ id: 1, ticker: '2330.TW', group_id: 'custom_7', ma1: 20, ma2: 60, ma3: 0, ma4: 0 }] : [];
    return route.fulfill({ json: { ok: true, data } });
  });
  await page.goto('./?page=console&view=lists');
  const notify = page.getByRole('region', { name: '清單通知' });
  const all = notify.getByRole('switch', { name: '所有清單都通知' });
  await expect(all).toBeChecked();
  const card = page.locator('.list-card').filter({ hasText: '波段' });
  await card.locator('summary').click();
  const flags = card.getByRole('group', { name: '波段 的通知' });
  await expect(flags.getByRole('checkbox', { name: '均線突破／跌破' })).toBeDisabled();
  await expect(flags.getByRole('checkbox', { name: '均線突破／跌破' })).toBeChecked();

  await all.uncheck();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0]).toMatchObject({ version: 1, mode: 'custom', lists: { 'group:custom_7': { ma: true, drawing: true }, 'group:tw_g1': { ma: true, drawing: true } } });
  await flags.getByRole('checkbox', { name: '均線突破／跌破' }).uncheck();
  await expect.poll(() => saves.length).toBe(2);
  expect((saves[1] as { lists: Record<string, unknown> }).lists['group:custom_7']).toEqual({ ma: false, drawing: true });
  if (SHOTS && testInfo.project.name === 'mobile-chromium') {
    await notify.evaluate(element => element.scrollIntoView({ block: 'start' }));
    await page.screenshot({ path: `${SHOTS}/list-notifications-390.png` });
  }

  state.fail = true;
  await flags.getByRole('checkbox', { name: '畫線突破／跌破' }).uncheck();
  await expect(notify.getByRole('alert')).toContainText('通知設定未儲存');
  await expect(flags.getByRole('checkbox', { name: '畫線突破／跌破' })).toBeChecked();
  await expect(flags.getByRole('checkbox', { name: '均線突破／跌破' })).not.toBeChecked();
});
