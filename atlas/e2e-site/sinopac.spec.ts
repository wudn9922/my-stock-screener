import { resolve } from 'node:path';
import { test, expect } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount, DEVICE_USER_ID } from './account-fixture';
test('owner SinoPac status, manual sync, error and read-only imported portfolio', async ({ page }, info) => {
  await mockSite(page, { fundamentals: true });
  await installLineAccount(page);
  const id = '20000000-0000-4000-8000-000000000001';
  let synced = 0;
  const account = {
    id,
    name: '永豐',
    broker: 'sinopac',
    kind: 'tw-broker',
    currency: 'TWD',
  };
  const doc = {
    version: 1,
    accounts: [
      {
        account,
        revision: 1,
        holdings: [
          {
            id: '20000000-0000-4000-8000-000000000002',
            accountId: id,
            symbol: '2330.TW',
            market: 'TW',
            quantity: '1000',
            averageCost: '100',
            costCurrency: 'TWD',
            source: 'api',
            updatedAt: '2026-11-09T01:00:00Z',
          },
        ],
      },
    ],
  };
  await page.route('**/functions/v1/user-settings', (route) => {
    const b = route.request().postDataJSON();
    if (b.action === 'access.get') return route.fulfill({ json: accessEnvelope(DEVICE_USER_ID, 'cloud') });
    if (b.action === 'holdings.sinopac.sync') {
      synced++;
      if (synced > 1)
        return route.fulfill({
          json: {
            ok: true,
            data: {
              accountId: id,
              lastSyncAt: '2026-11-09T01:10:00Z',
              lastSuccessAt: '2026-11-09T01:10:00Z',
              code: 'ok',
              skippedShorts: 2,
            },
          },
        });
      return route.fulfill({
        status: 503,
        json: { ok: false, error: '永豐同步逾時，請稍後再試' },
      });
    }
    return route.fulfill({
      json: {
        ok: true,
        data:
          b.action === 'holdings.list'
            ? doc
            : b.action === 'holdings.sinopac.status'
              ? {
                  accountId: id,
                  lastSyncAt: synced ? '2026-11-09T01:05:00Z' : '2026-11-09T01:00:00Z',
                  lastSuccessAt: null,
                  code: synced ? 'timeout' : 'closed',
                  skippedShorts: 2,
                }
              : [],
      },
    });
  });
  await page.goto('./?page=watchlist');
  await page
    .getByRole('group', { name: '自選檢視' })
    .getByRole('button', { name: '持股', exact: true })
    .click();
  const panel = page.getByRole('region', { name: '永豐台股自動同步' });
  await expect(panel).toContainText('休市未同步');
  await expect(panel).toContainText('有 2 筆融券部位未匯入');
  await expect(page.getByRole('button', { name: '修正持股', exact: true })).toBeDisabled();
  await panel.getByRole('button', { name: '同步', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('永豐同步逾時');
  await expect(panel.getByRole('status')).toContainText('永豐同步逾時');
  await expect(panel).toContainText('2026/11/09 09:05');
  expect(synced).toBe(1);
  await panel.getByRole('button', { name: '同步', exact: true }).click();
  await expect(panel.getByRole('status')).toHaveText('正常');
  await expect(panel).toContainText('上次成功：');
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '修正持股', exact: true })).toBeDisabled();
  expect(synced).toBe(2);
  if (info.project.name === 'mobile-chromium') {
    expect(page.viewportSize()?.width).toBe(390);
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve('docs/screenshots/sinopac-sync-390.png') });
  }
});

test('legacy CSV SinoPac stays editable only after the selected account permission check completes', async ({
  page,
}) => {
  await mockSite(page);
  await installLineAccount(page);
  const id = '20000000-0000-4000-8000-000000000001';
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/functions/v1/user-settings', async (route) => {
    const b = route.request().postDataJSON();
    if (b.action === 'access.get') return route.fulfill({ json: accessEnvelope(DEVICE_USER_ID, 'cloud') });
    if (b.action === 'holdings.sinopac.status') {
      await ready;
      return route.fulfill({ status: 403, json: { ok: false, error: '此帳號無永豐同步權限' } });
    }
    return route.fulfill({
      json: {
        ok: true,
        data:
          b.action === 'holdings.list'
            ? {
                version: 1,
                accounts: [
                  {
                    account: {
                      id,
                      name: '舊永豐 CSV',
                      broker: 'sinopac',
                      kind: 'tw-broker',
                      currency: 'TWD',
                    },
                    holdings: [],
                    revision: 1,
                  },
                ],
              }
            : [],
      },
    });
  });
  await page.goto('./?page=watchlist');
  await page
    .getByRole('group', { name: '自選檢視' })
    .getByRole('button', { name: '持股', exact: true })
    .click();
  const edit = page.getByRole('button', { name: '修正持股', exact: true });
  await expect(edit).toBeDisabled();
  release();
  await expect(page.getByRole('region', { name: '永豐台股自動同步' }).getByRole('alert')).toContainText(
    '尚未設定',
  );
  await expect(edit).toBeEnabled();
  await edit.click();
  await expect(page.getByRole('region', { name: '匯入持股', exact: true })).toBeVisible();
});
