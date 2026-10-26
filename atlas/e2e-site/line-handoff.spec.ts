import { expect, test, type BrowserContext } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import { accessEnvelope, DEVICE_USER_ID } from './account-fixture';
import {
  installRealSdk,
  sdkIdentityFixture,
  sdkLogin,
  sdkTestUrl,
} from './line-sdk-fixture';

/** Two independent contexts deliberately share no cookies, localStorage or IndexedDB. */
test('installed PWA receives a one-use LINE identity from a different browser partition', async ({
  page,
  browser,
}) => {
  const actions: Record<string, unknown>[] = [];
  let completed = false,
    consumed = false;
  let intentId = '',
    readSecret = '',
    writeSecret = '';
  let receiverAccess = 0;
  const identity = sdkIdentityFixture();
  const candidate = {
    userId: DEVICE_USER_ID,
    idToken: identity.idToken,
    expiresAt: Date.now() + 3600000,
    displayName: '本人測試帳號',
  };
  const route = async (context: BrowserContext) =>
    context.route('**/functions/v1/user-settings', async (r) => {
      const body = r.request().postDataJSON() as Record<string, unknown>;
      actions.push(body);
      if (body.action === 'line.login.begin') {
        intentId = body.intentId as string;
        readSecret = body.readSecret as string;
        writeSecret = body.writeSecret as string;
        return r.fulfill({
          json: {
            ok: true,
            data: {
              status: 'pending',
              expiresAt: new Date(Date.now() + 120000).toISOString(),
            },
          },
        });
      }
      if (body.action === 'line.login.complete') {
        expect(body.confirmed).toBe(true);
        expect(body.intentId).toBe(intentId);
        expect(body.writeSecret).toBe(writeSecret);
        expect(body.idToken).toBe(candidate.idToken);
        completed = true;
        return r.fulfill({ json: { ok: true, data: { status: 'complete' } } });
      }
      if (body.action === 'line.login.poll') {
        expect(body.readSecret).toBe(readSecret);
        if (completed && !consumed) {
          consumed = true;
          return r.fulfill({
            json: {
              ok: true,
              data: { status: 'complete', identity: candidate },
            },
          });
        }
        return r.fulfill({ json: { ok: true, data: { status: 'pending' } } });
      }
      if (body.action === 'access.get') {
        if (context === page.context()) receiverAccess++;
        return r.fulfill({ json: accessEnvelope(DEVICE_USER_ID) });
      }
      return r.fulfill({ json: { ok: true, data: { status: 'consumed' } } });
    });
  await page.addInitScript(() =>
    Object.defineProperty(navigator, 'standalone', {
      configurable: true,
      value: true,
    }),
  );
  await mockSite(page, { report: buildReport(20) });
  await route(page.context());
  await page.context().route('https://liff.line.me/**', (r) =>
    r.fulfill({
      contentType: 'text/html',
      body: '<p>LINE provider fixture</p>',
    }),
  );
  await page.goto('./?page=console');
  const popupPromise = page.context().waitForEvent('page');
  await page
    .getByRole('button', { name: '使用 LINE 登入', exact: true })
    .click();
  const popup = await popupPromise;
  await expect.poll(() => popup.url()).toContain('atlasLineHandoff=');
  expect(popup.url()).not.toContain(readSecret);
  await expect(
    page.getByText('請在 LINE 完成驗證後，返回這個 Atlas 視窗。', {
      exact: false,
    }),
  ).toBeVisible();
  const originalUrl = page.url();
  const sender = await browser.newContext({ serviceWorkers: 'block' });
  try {
    await route(sender);
    const sdk = await installRealSdk(sender, identity);
    const callback = await sender.newPage();
    await mockSite(callback, { report: buildReport(20) });
    const target = sdkTestUrl(
      new URL(
        `?atlasLineHandoff=${intentId}&atlasLineWrite=${writeSecret}`,
        originalUrl,
      ).href,
    );
    await callback.goto(await sdkLogin(callback, target));
    const approval = callback.getByRole('group', {
      name: '確認 LINE 登入交接',
    });
    await expect(approval).toContainText(
      intentId.replace(/-/g, '').slice(-6).toUpperCase(),
    );
    expect(completed).toBe(false);
    await expect(
      page.getByRole('group', { name: '確認 LINE 登入帳號' }),
    ).toHaveCount(0);
    await approval
      .getByRole('button', { name: '確認登入原來的 Atlas' })
      .click();
    await expect(
      callback.getByText(
        'LINE 驗證完成，請返回原來的 Atlas 視窗或主畫面 App，確認帳號後完成登入。',
      ),
    ).toBeVisible();
    await page.bringToFront();
    const confirm = page.getByRole('group', { name: '確認 LINE 登入帳號' });
    await expect(confirm).toContainText('本人測試帳號');
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute(
      'data-storage-mode',
      'none',
    );
    expect(receiverAccess).toBe(0);
    expect(sdk.exchanges).toHaveLength(1);
    expect(sdk.exchanges[0].hasVerifier).toBe(true);
    await confirm.getByRole('button', { name: '確認登入此帳號' }).click();
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute(
      'data-storage-mode',
      'device',
    );
    expect(page.url()).toBe(originalUrl);
    expect(
      await page.evaluate(() =>
        Object.keys(localStorage).filter(
          (k) => k.includes('Handoff') || k.includes('readSecret'),
        ),
      ),
    ).toEqual([]);
    await page.getByRole('button', { name: '登出 LINE', exact: true }).click();
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute(
      'data-storage-mode',
      'none',
    );
    expect(consumed).toBe(true);
  } finally {
    await sender.close();
    await popup.close();
  }
});

for (const insideLine of [false, true])
  test(`an unavailable WindowProxy offers a login link without OAuth loops (LINE browser=${insideLine})`, async ({
    page,
  }) => {
    await page.addInitScript((inLine) => {
      Object.defineProperty(navigator, 'standalone', { value: true });
      window.open = () => null;
      if (inLine)
        Object.defineProperty(navigator, 'userAgent', {
          configurable: true,
          value: 'Mozilla/5.0 (iPhone) Line/15.0.0',
        });
    }, insideLine);
    await page.route('https://static.line-scdn.net/liff/edge/2/sdk.js', (r) =>
      r.fulfill({
        contentType: 'text/javascript',
        body: `window.liff={init:async()=>{},isLoggedIn:()=>false,isInClient:()=>false,getIDToken:()=>null,getDecodedIDToken:()=>null,logout:()=>{},login:()=>{throw Error('unexpected OAuth loop')}};`,
      }),
    );
    await mockSite(page);
    await page.route('**/functions/v1/user-settings', (r) =>
      r.fulfill({
        json: {
          ok: true,
          data: {
            status: 'pending',
            expiresAt: new Date(Date.now() + 120000).toISOString(),
          },
        },
      }),
    );
    await page.goto('./?page=console');
    await page
      .getByRole('button', { name: '使用 LINE 登入', exact: true })
      .click();
    await expect(
      page.getByRole('link', { name: '開啟 LINE 登入視窗' }),
    ).toBeVisible();
    await page.getByRole('button', { name: '取消登入', exact: true }).click();
    await expect(page.locator('[data-storage-mode]')).toContainText(
      '已取消 LINE 登入',
    );
    await expect(
      page.getByRole('button', { name: '使用 LINE 登入', exact: true }),
    ).toBeEnabled();
  });
