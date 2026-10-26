import { expect, test } from '@playwright/test';
import { mockSite, buildReport } from './mocks';
import { accessEnvelope, DEVICE_USER_ID } from './account-fixture';
import { installRealSdk, sdkLogin, sdkTestUrl } from './line-sdk-fixture';

test('real SDK exchanges a code in its original partition but cannot exchange it in a fresh partition', async ({
  page,
  browser,
}) => {
  const sdk = await installRealSdk(page.context());
  await mockSite(page, { report: buildReport(20) });
  await page.route('**/functions/v1/user-settings', (r) =>
    r.fulfill({ json: accessEnvelope(DEVICE_USER_ID) }),
  );
  const target = sdkTestUrl(
    new URL('./?page=console&atlasLineLogin=1', test.info().project.use.baseURL)
      .href,
  );
  const callback = await sdkLogin(page, target);
  await page.goto(callback);
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute(
    'data-storage-mode',
    'device',
  );
  expect(sdk.exchanges).toHaveLength(1);
  const separate = await browser.newContext({ serviceWorkers: 'block' });
  try {
    const isolated = await installRealSdk(separate, sdk.identity);
    const other = await separate.newPage();
    await mockSite(other, { report: buildReport(20) });
    await other.goto(callback);
    await expect(other.locator('[data-storage-mode]')).toHaveAttribute(
      'data-storage-mode',
      'none',
    );
    await expect(other.locator('[data-storage-mode]')).toContainText(
      '登入回傳未完成驗證',
    );
    expect(isolated.exchanges).toHaveLength(0);
  } finally {
    await separate.close();
  }
});

test('primary root handles the real SDK callback before SDK secondary navigation', async ({
  page,
}) => {
  const sdk = await installRealSdk(page.context());
  await mockSite(page, { report: buildReport(20) });
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', (r) => {
    actions.push(r.request().postDataJSON().action);
    return r.fulfill({
      json: {
        ok: true,
        data:
          r.request().postDataJSON().action === 'line.login.complete'
            ? { status: 'complete' }
            : accessEnvelope(DEVICE_USER_ID).data,
      },
    });
  });
  const secondary =
    '/atlas/?page=console&atlasLineHandoff=12345678-1234-4234-8234-123456789abc&atlasLineWrite=' +
    'w'.repeat(43);
  const root = sdkTestUrl(
    new URL(
      '../?liff.state=' + encodeURIComponent(secondary),
      test.info().project.use.baseURL,
    ).href,
  );
  await page.goto(await sdkLogin(page, root));
  await expect(
    page.getByRole('group', { name: '確認 LINE 登入交接' }),
  ).toContainText('789ABC');
  expect(actions).not.toContain('line.login.complete');
  await page.getByRole('button', { name: '確認登入原來的 Atlas' }).click();
  await expect(
    page.getByText(
      'LINE 驗證完成，請返回原來的 Atlas 視窗或主畫面 App，確認帳號後完成登入。',
    ),
  ).toBeVisible();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute(
    'data-storage-mode',
    'device',
  );
  expect(sdk.exchanges).toHaveLength(1);
  expect(new URL(sdk.exchanges[0].frameUrl).pathname).toBe(
    '/my-stock-screener/',
  );
  expect(actions).toContain('line.login.complete');
});
