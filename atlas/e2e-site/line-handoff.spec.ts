import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { buildReport, mockSite } from './mocks';
import { accessEnvelope, DEVICE_USER_ID } from './account-fixture';
import {
  lineLoginHandoff,
  LineHandoffError,
} from '../../supabase/functions/_shared/lineLoginHandoff';
import {
  installRealSdk,
  sdkIdentityFixture,
  sdkLogin,
  sdkTestUrl,
} from './line-sdk-fixture';

interface LaunchRecord {
  urls: string[];
  inGesture: boolean[];
  pagehide: number;
  beforeunload: number;
  opened: string[];
}
/**
 * Playwright cannot open the real LINE app. Record (and cancel) any top-level
 * line: link activation, whether it ran inside the trusted click gesture, and
 * any pagehide/beforeunload of the original receiver document.
 */
async function recordLineLaunch(page: Page) {
  await page.addInitScript(() => {
    const state = {
      urls: [] as string[],
      inGesture: [] as boolean[],
      pagehide: 0,
      beforeunload: 0,
      opened: [] as string[],
    };
    (window as unknown as { __lineLaunch: typeof state }).__lineLaunch = state;
    let gesture = false;
    window.addEventListener(
      'click',
      (e) => e.isTrusted && (gesture = true),
      true,
    );
    window.addEventListener('click', (e) => e.isTrusted && (gesture = false));
    document.addEventListener(
      'click',
      (event) => {
        const link =
          event.target instanceof Element ? event.target.closest('a') : null;
        if (link?.protocol !== 'line:') return;
        state.urls.push(link.href);
        state.inGesture.push(gesture);
        event.preventDefault(); // Stub the OS app hand-off; never leave the page.
      },
      true,
    );
    window.addEventListener('pagehide', () => state.pagehide++);
    window.addEventListener('beforeunload', () => state.beforeunload++);
    const open = window.open.bind(window);
    window.open = (...args) => {
      state.opened.push(String(args[0]));
      return open(...args);
    };
  });
}
const launchRecord = (page: Page) =>
  page.evaluate(
    () => (window as unknown as { __lineLaunch: LaunchRecord }).__lineLaunch,
  );

/** Two independent contexts deliberately share no cookies, localStorage or IndexedDB. */
for (const scenario of [
  'legitimate',
  'attempt-limit',
  'different-network',
] as const)
  test(`installed PWA reverse-code handoff: ${scenario}`, async ({
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
    let row: Record<string, unknown> | null = null,
      attempts = 0;
    const database = {
      rpc: async (_name: string, p: Record<string, unknown>) => {
        let data: Record<string, unknown> = { status: 'missing' };
        if (p.operation === 'begin') {
          row = { ...p };
          data = {
            status: 'pending',
            expiresAt: new Date(Date.now() + 120000).toISOString(),
          };
        } else if (row && p.intent_id === row.intent_id) {
          if (p.operation === 'complete' && p.write_hash === row.write_hash) {
            if (p.network_hash !== row.network_hash)
              data = { status: 'network_mismatch' };
            else if (completed) data = { status: 'used' };
            else {
              row = { ...row, ...p };
              completed = true;
              data = { status: 'complete' };
            }
          } else if (p.read_hash === row.read_hash) {
            if (p.operation === 'cancel') {
              row = null;
              data = { status: 'consumed' };
            } else if (!completed) data = { status: 'pending' };
            else if (!p.confirmation_hash) data = { status: 'code_required' };
            else if (p.confirmation_hash !== row.confirmation_hash) {
              attempts++;
              if (attempts >= 5) {
                row = null;
                data = { status: 'locked' };
              } else
                data = {
                  status: 'wrong_code',
                  attemptsRemaining: 5 - attempts,
                };
            } else {
              consumed = true;
              data = { status: 'consumed', ciphertext: row.encrypted_token };
              row = null;
            }
          }
        }
        return { data, error: null };
      },
    };
    const route = async (context: BrowserContext) =>
      context.route('**/functions/v1/user-settings', async (r) => {
        const body = r.request().postDataJSON() as Record<string, unknown>;
        actions.push(body);
        if (body.action === 'access.get') {
          if (context === page.context()) receiverAccess++;
          return r.fulfill({ json: accessEnvelope(DEVICE_USER_ID) });
        }
        if (body.action === 'line.login.begin') {
          intentId = body.intentId as string;
          readSecret = body.readSecret as string;
          writeSecret = body.writeSecret as string;
        }
        if (body.action === 'line.login.complete') {
          expect(body.confirmed).toBe(true);
          expect(body.intentId).toBe(intentId);
          expect(body.writeSecret).toBe(writeSecret);
          expect(body.idToken).toBe(candidate.idToken);
        }
        // Simulate managed ingress IPs; execute the actual crypto / code / network handler.
        // Row locking, attempts, upgrade and permissions are separately tested on real PostgreSQL.
        const ip =
          context === page.context()
            ? '203.0.113.21'
            : scenario === 'different-network'
              ? '198.51.100.7'
              : '203.0.113.99';
        try {
          const data = await lineLoginHandoff(
            database,
            body,
            'offline-server-secret',
            new Headers({ 'cf-connecting-ip': ip }),
            async (idToken) => {
              expect(idToken).toBe(candidate.idToken);
              return DEVICE_USER_ID;
            },
          );
          return r.fulfill({ json: { ok: true, data } });
        } catch (error) {
          if (!(error instanceof LineHandoffError)) throw error;
          return r.fulfill({
            status: error.status,
            json: { ok: false, error: error.message },
          });
        }
      });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'standalone', {
        configurable: true,
        value: true,
      });
      Object.defineProperty(navigator, 'userAgent', {
        configurable: true,
        value: 'Mozilla/5.0 (iPhone) AppleWebKit/605.1.15',
      });
    });
    await recordLineLaunch(page);
    await mockSite(page, { report: buildReport(20) });
    await route(page.context());
    await page.context().route('https://liff.line.me/**', (r) =>
      r.fulfill({
        contentType: 'text/html',
        body: '<p>LINE provider fixture</p>',
      }),
    );
    await page.goto('./?page=console');
    const originalBeforeLogin = page.url();
    let mainNavigations = 0;
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) mainNavigations++;
    });
    await page
      .getByRole('button', { name: '使用 LINE 登入', exact: true })
      .click();
    // iOS WebKit ignores custom schemes in subframes: no hidden launch frame.
    const launchFrame = page.locator('iframe[title="開啟 LINE"]');
    await expect(launchFrame).toHaveCount(0);
    const launch = await launchRecord(page);
    expect(launch.urls).toHaveLength(1);
    expect(launch.inGesture).toEqual([true]);
    expect(launch.urls[0]).toMatch(/^line:\/\/app\//);
    const appUrl = new URL(launch.urls[0]!);
    expect(appUrl.pathname).toBe('/2010330411-6JhrotT9');
    expect(appUrl.searchParams.get('atlasLineHandoff')).toBe(intentId);
    expect(appUrl.searchParams.get('atlasLineWrite')).toBe(writeSecret);
    expect(appUrl.href).not.toContain(readSecret);
    expect(launch.opened).toEqual([]);
    expect(launch.pagehide).toBe(0);
    expect(launch.beforeunload).toBe(0);
    await expect(page.locator('a[data-atlas-line-launch]')).toHaveCount(0);
    expect(page.context().pages()).toHaveLength(1);
    expect(page.url()).toBe(originalBeforeLogin);
    expect(mainNavigations).toBe(0);
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
      await expect(approval).toContainText(
        '只有你剛剛在自己的裝置按了登入，才按確認',
      );
      await approval
        .getByRole('button', { name: '確認登入原來的 Atlas' })
        .click();
      if (scenario === 'different-network') {
        await expect(
          callback.getByText('請在同一個網路下完成登入', { exact: true }),
        ).toBeVisible();
        expect(completed).toBe(false);
        expect(consumed).toBe(false);
        expect(receiverAccess).toBe(0);
        expect(row).not.toHaveProperty('encrypted_token');
        await page.bringToFront();
        await expect(
          page.getByRole('group', { name: '確認 LINE 登入帳號' }),
        ).toHaveCount(0);
        await page
          .getByRole('button', { name: '取消登入', exact: true })
          .click();
        return;
      }
      const codeGroup = callback.getByRole('group', {
        name: 'LINE 登入確認碼',
      });
      await expect(codeGroup).toBeVisible();
      const code = (await codeGroup.locator('strong').innerText()).trim();
      expect(code).toMatch(/^\d{6}$/);
      expect(callback.url()).not.toContain(code);
      expect(page.url()).not.toContain(code);
      await page.bringToFront();
      const form = page.getByRole('form', { name: '輸入 LINE 確認碼' });
      await expect(form).toBeVisible();
      const input = form.getByRole('textbox', { name: 'LINE 六位確認碼' });
      await expect(input).toBeFocused();
      await page.getByRole('button', { name: '取消登入', exact: true }).focus();
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await expect(input).toBeFocused();
      await expect(input).toBeInViewport();
      await page.getByRole('button', { name: '取消登入', exact: true }).focus();
      await page.evaluate(() =>
        document.dispatchEvent(new Event('visibilitychange')),
      );
      await expect(input).toBeFocused();
      await expect(
        page.getByRole('group', { name: '確認 LINE 登入帳號' }),
      ).toHaveCount(0);
      expect(consumed).toBe(false);
      expect(receiverAccess).toBe(0);
      expect(
        actions
          .filter((a) => a.action === 'line.login.poll')
          .every((a) => a.code === undefined),
      ).toBe(true);
      const wrong = code === '000000' ? '111111' : '000000';
      const wrongCount = scenario === 'attempt-limit' ? 5 : 1;
      for (let n = 0; n < wrongCount; n++) {
        await form
          .getByRole('textbox', { name: 'LINE 六位確認碼' })
          .fill(wrong);
        await form
          .getByRole('button', { name: '驗證確認碼', exact: true })
          .click();
        if (n < 4)
          await expect(form.getByRole('alert')).toHaveText(
            `確認碼不正確，還可嘗試 ${4 - n} 次。`,
          );
      }
      if (scenario === 'attempt-limit') {
        await expect(page.locator('[data-storage-mode]')).toContainText(
          '確認碼錯誤已達五次',
        );
        await expect(form).toHaveCount(0);
        expect(row).toBeNull();
        expect(consumed).toBe(false);
        expect(receiverAccess).toBe(0);
        await expect(
          page.getByRole('button', { name: '使用 LINE 登入', exact: true }),
        ).toBeEnabled();
        return;
      }
      await form.getByRole('textbox', { name: 'LINE 六位確認碼' }).fill(code);
      await form
        .getByRole('button', { name: '驗證確認碼', exact: true })
        .click();
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
      await page
        .getByRole('button', { name: '登出 LINE', exact: true })
        .click();
      await expect(page.locator('[data-storage-mode]')).toHaveAttribute(
        'data-storage-mode',
        'none',
      );
      expect(consumed).toBe(true);
      await expect(launchFrame).toHaveCount(0);
      // The receiver document (and its RAM read secret) was never unloaded.
      expect(mainNavigations).toBe(0);
      expect(await launchRecord(page)).toMatchObject({
        pagehide: 0,
        beforeunload: 0,
        opened: [],
      });
      expect((await launchRecord(page)).urls).toHaveLength(1);
    } finally {
      await sender.close();
    }
  });

for (const insideLine of [false, true])
  test(`an unavailable WindowProxy offers a login link without OAuth loops (LINE browser=${insideLine})`, async ({
    page,
  }) => {
    await recordLineLaunch(page);
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

test('manual launch sheet keeps its explanation when native dispatch and closing are blocked', async ({
  page,
}) => {
  await page.context().addInitScript(() => {
    if (!location.pathname.endsWith('/line-login-launch.html')) return;
    const state = window as unknown as Window & { closeCalls: number };
    state.closeCalls = 0;
    window.close = () => {
      state.closeCalls++;
    };
    // Model OS app dispatch without unloading the explanatory document.
    document.addEventListener(
      'click',
      (event) => {
        if ((event.target as HTMLElement).id === 'continue-line')
          event.preventDefault();
      },
      true,
    );
  });
  await recordLineLaunch(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { value: true });
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone)',
      configurable: true,
    });
  });
  let begin: Record<string, unknown> = {};
  await mockSite(page);
  await page.route('**/functions/v1/user-settings', (r) => {
    const body = r.request().postDataJSON();
    if (body.action === 'line.login.begin') begin = body;
    return r.fulfill({
      json: {
        ok: true,
        data: {
          status: 'pending',
          expiresAt: new Date(Date.now() + 120000).toISOString(),
        },
      },
    });
  });
  await page.goto('./?page=console');
  const original = page.url();
  await page
    .getByRole('button', { name: '使用 LINE 登入', exact: true })
    .click();
  const opened = page.context().waitForEvent('page');
  await page.getByRole('link', { name: '開啟 LINE 登入視窗' }).click();
  const sheet = await opened;
  try {
    const explanation = sheet.getByText(
      '已在 LINE 繼續登入，完成後請按左上角「完成／X」回到原本的 Atlas 輸入確認碼',
      { exact: true },
    );
    await expect(explanation).toBeVisible();
    await expect.poll(() => sheet.url()).not.toContain('atlasLineWrite');
    const link = new URL(
      (await sheet
        .getByRole('link', { name: '在 LINE 中繼續' })
        .getAttribute('href'))!,
    );
    expect(link.origin).toBe('https://liff.line.me');
    expect(link.searchParams.get('atlasLineWrite')).toBe(begin.writeSecret);
    expect(link.href).not.toContain(begin.readSecret);
    expect(sheet.url()).not.toContain(begin.readSecret);
    await sheet.getByRole('link', { name: '在 LINE 中繼續' }).click();
    await sheet.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        value: 'hidden',
      });
      document.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        value: 'visible',
      });
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
    });
    expect(
      await sheet.evaluate(
        () => (window as unknown as Window & { closeCalls: number }).closeCalls,
      ),
    ).toBeGreaterThan(0);
    await expect(explanation).toBeVisible();
    expect(
      await sheet.evaluate(() => ({
        local: Object.keys(localStorage),
        session: Object.keys(sessionStorage),
      })),
    ).toEqual({ local: [], session: [] });
    expect(page.url()).toBe(original);
    await page.bringToFront();
    await page.getByRole('button', { name: '取消登入', exact: true }).click();
  } finally {
    await sheet.close();
  }
});

for (const device of ['iphone', 'desktop'] as const)
  test(`launch hint when LINE does not open: ${device}`, async ({ page }) => {
    await recordLineLaunch(page);
    await page.addInitScript(
      (ua) => {
        Object.defineProperty(navigator, 'standalone', { value: true });
        Object.defineProperty(navigator, 'userAgent', {
          value: ua,
          configurable: true,
        });
        Object.defineProperty(navigator, 'platform', {
          value: ua.includes('iPhone') ? 'iPhone' : 'Win32',
          configurable: true,
        });
        Object.defineProperty(navigator, 'maxTouchPoints', {
          value: ua.includes('iPhone') ? 5 : 0,
          configurable: true,
        });
      },
      device === 'iphone'
        ? 'Mozilla/5.0 (iPhone) AppleWebKit/605.1.15'
        : 'Mozilla/5.0 (Windows NT 10.0) Chrome/130',
    );
    let begin: Record<string, unknown> = {};
    await mockSite(page);
    await page.route('**/functions/v1/user-settings', (r) => {
      const body = r.request().postDataJSON();
      if (body.action === 'line.login.begin') begin = body;
      return r.fulfill({
        json: {
          ok: true,
          data: {
            status: 'pending',
            expiresAt: new Date(Date.now() + 120000).toISOString(),
          },
        },
      });
    });
    await page.goto('./?page=console');
    const original = page.url();
    await page
      .getByRole('button', { name: '使用 LINE 登入', exact: true })
      .click();
    const link = page.getByRole('link', { name: '開啟 LINE 登入視窗' });
    await expect(link).toBeVisible();
    const hint = page.getByText('LINE 沒有自動開啟？請點這裡', { exact: true });
    const launch = await launchRecord(page);
    expect(launch.pagehide).toBe(0);
    if (device === 'desktop') {
      // Desktop path unchanged: one explanatory window, no scheme or hint.
      expect(launch.urls).toEqual([]);
      expect(launch.opened).toHaveLength(1);
      expect(new URL(launch.opened[0]!).pathname).toMatch(
        /\/line-login-launch\.html$/,
      );
      await page.waitForTimeout(2500);
      await expect(hint).toHaveCount(0);
    } else {
      expect(launch.inGesture).toEqual([true]);
      expect(new URL(launch.urls[0]!).searchParams.get('atlasLineWrite')).toBe(
        begin.writeSecret,
      );
      expect(launch.opened).toEqual([]);
      // Still visible and focused after ~1.5 s: assume LINE did not open.
      await expect(hint).toBeVisible({ timeout: 5000 });
      await expect(link).toHaveClass(/primary-button/);
      await expect(link).toHaveAttribute(
        'aria-describedby',
        'line-launch-missed',
      );
      // Never auto-open a window after the delay (popup blockers).
      await page.waitForTimeout(500);
      expect((await launchRecord(page)).opened).toEqual([]);
      expect((await launchRecord(page)).urls).toHaveLength(1);
      expect(page.context().pages()).toHaveLength(1);
    }
    expect(page.url()).toBe(original);
    await page.getByRole('button', { name: '取消登入', exact: true }).click();
    await expect(hint).toHaveCount(0);
  });

test('no launch hint when the page is hidden right after the LINE scheme dispatch', async ({
  page,
}) => {
  await recordLineLaunch(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { value: true });
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone)',
      configurable: true,
    });
  });
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
  // Model iOS backgrounding the PWA while LINE takes over.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
    document.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
  });
  await page.waitForTimeout(2500);
  await expect(
    page.getByRole('link', { name: '開啟 LINE 登入視窗' }),
  ).toBeVisible();
  await expect(
    page.getByText('LINE 沒有自動開啟？請點這裡', { exact: true }),
  ).toHaveCount(0);
  expect((await launchRecord(page)).urls).toHaveLength(1);
  await page.getByRole('button', { name: '取消登入', exact: true }).click();
});

test('the launch sheet is readable before JavaScript or SDK initialization', async ({
  browser,
}, info) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    serviceWorkers: 'block',
  });
  try {
    const sheet = await context.newPage();
    await sheet.goto(
      new URL('line-login-launch.html', info.project.use.baseURL).href,
    );
    await expect(
      sheet.getByRole('heading', { name: '返回原本的 Atlas 完成登入' }),
    ).toBeVisible();
    await expect(sheet.getByRole('status')).toContainText('完成／X');
  } finally {
    await context.close();
  }
});

test('the launch sheet rejects malformed or arbitrary redirect targets', async ({
  page,
}) => {
  await page.goto(
    './line-login-launch.html?atlasLineHandoff=bad&atlasLineWrite=bad&atlasLiffId=https://attacker.invalid',
  );
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('link', { name: '在 LINE 中繼續' })).toHaveCount(
    0,
  );
  await expect(page.getByRole('status')).toContainText('完成／X');
  expect(new URL(page.url()).search).toBe('');
  await page.goto(
    './line-login-launch.html?atlasLineHandoff=12345678-1234-4234-8234-123456789abc&atlasLineWrite=' +
      'w'.repeat(43) +
      '&atlasLiffId=999999-ATTACKER&redirect=https://attacker.invalid',
  );
  const link = new URL(
    (await page
      .getByRole('link', { name: '在 LINE 中繼續' })
      .getAttribute('href'))!,
  );
  expect(link.origin).toBe('https://liff.line.me');
  expect(link.pathname).toBe('/2010330411-6JhrotT9/atlas/');
  expect([...link.searchParams.keys()].sort()).toEqual([
    'atlasLineHandoff',
    'atlasLineWrite',
  ]);
});
