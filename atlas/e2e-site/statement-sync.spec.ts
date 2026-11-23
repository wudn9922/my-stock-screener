import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount, OWNER_USER_ID } from './account-fixture';

test('owner creates a Cathay US account linked to e-mail statements and sees its log', async ({ page }, info) => {
  await mockSite(page);
  await installLineAccount(page, OWNER_USER_ID);
  let doc = { version: 1, accounts: [] as { account: { id: string }; holdings: unknown[]; revision: number }[] };
  const links: { accountId: string; broker: string; market: string; pending: number }[] = [];
  const calls: string[] = [];
  await page.route('**/functions/v1/user-settings', (route) => {
    const body = route.request().postDataJSON();
    calls.push(body.action);
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(OWNER_USER_ID, 'cloud', true) });
    if (body.action === 'holdings.replaceAccount')
      doc = { version: 1, accounts: [{ account: body.account, holdings: body.holdings, revision: 1 }] };
    if (body.action === 'holdings.statement.link') {
      links.push({ accountId: body.accountId, broker: body.broker, market: body.market, pending: 0 });
      return route.fulfill({ json: { ok: true, data: { status: 'ok', applied: 2 } } });
    }
    if (body.action === 'holdings.statement.status')
      return route.fulfill({ json: { ok: true, data: { links, unlinkedPending: 0, messages: [
        { broker: 'cathay', receivedAt: '2026-11-19T12:00:00Z', reportDate: '2026-11-19', status: 'ok', trades: 2, processedAt: '2026-11-19T12:01:00Z' },
        { broker: null, receivedAt: '2026-11-20T12:00:00Z', reportDate: null, status: 'password', trades: 0, processedAt: '2026-11-20T12:01:00Z' },
      ] } } });
    return route.fulfill({ json: { ok: true, data: String(body.action).startsWith('holdings.') ? doc : [] } });
  });
  await page.goto('./?page=watchlist');
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await page.getByRole('button', { name: '新增帳戶', exact: true }).click();
  const form = page.getByRole('form', { name: '新增持股帳戶' });
  await form.getByRole('combobox', { name: '新增方式' }).selectOption('email');
  await expect(form.getByRole('combobox', { name: '對帳單券商' }).getByRole('option', { name: /永豐/ })).toHaveAttribute('disabled', '');
  await form.getByRole('textbox', { name: '帳戶名稱' }).fill('國泰美股');
  await form.getByRole('button', { name: '建立並連結' }).click();
  const panel = page.getByRole('region', { name: 'Email 對帳單同步' });
  await expect(panel).toContainText('國泰證券 美股');
  await expect(page.getByText('已建立並連結 Email 對帳單。已套用 2 筆成交。')).toBeVisible();
  await expect(panel.getByRole('list', { name: '最近處理的對帳單' })).toContainText('2026/11/19');
  await expect(panel).toContainText('PDF 密碼不正確');
  // Manual corrections stay available on a synced account.
  await expect(page.getByRole('button', { name: '修正持股', exact: true })).toBeEnabled();
  expect(calls.indexOf('holdings.replaceAccount')).toBeLessThan(calls.indexOf('holdings.statement.link'));
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  if (info.project.name === 'mobile-chromium') {
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('statement-sync-390.png') });
  }
});

test('non-owners never see the e-mail option', async ({ page }) => {
  await mockSite(page);
  await installLineAccount(page);
  await page.route('**/functions/v1/user-settings', (route) => {
    const body = route.request().postDataJSON();
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    if (String(body.action).startsWith('holdings.statement.')) throw new Error('non-owner requested statement data');
    return route.fulfill({ json: { ok: true, data: String(body.action).startsWith('holdings.') ? { version: 1, accounts: [] } : [] } });
  });
  await page.goto('./?page=watchlist');
  await page.getByRole('group', { name: '自選檢視' }).getByRole('button', { name: '持股', exact: true }).click();
  await page.getByRole('button', { name: '新增帳戶', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '新增方式' }).getByRole('option')).toHaveText(['API 自動同步', '匯入 CSV']);
});
