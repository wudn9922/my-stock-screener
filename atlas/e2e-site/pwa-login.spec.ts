import { expect, test, type Page } from '@playwright/test';
import { chooseDrawingTool } from '../e2e/drawing-picker-helper';
import { accessEnvelope, accountDatabase, DEVICE_USER_ID, SECOND_USER_ID } from './account-fixture';
import { buildReport, mockSite } from './mocks';

interface LineMock {
  account: string | null;
  expires: number;
  deferInit: boolean;
  failInit: boolean;
  initCalls: { config: Record<string, unknown>; url: string }[];
  loginCalls: { redirectUri?: string }[];
  logoutCalls: number;
  finishInit?: () => void;
}

/** External-browser SDK double: no LINE app UA, no real OAuth or provider storage. */
async function externalSdk(page: Page, account: string | null = null, deferInit = false, failInit = false) {
  const sdkRequests: string[] = [];
  await page.addInitScript(({ initialAccount, delayed, failed }) => {
    // Exercise the SDK's same-partition desktop fallback on every viewport.
    // Installed/mobile cross-partition login uses the real-SDK handoff suite.
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Mozilla/5.0 Atlas Desktop Safari' });
    (globalThis as unknown as { __lineMock: LineMock }).__lineMock = {
      account: initialAccount, expires: Date.now() / 1000 + 3600, deferInit: delayed, failInit: failed,
      initCalls: [], loginCalls: [], logoutCalls: 0,
    };
  }, { initialAccount: account, delayed: deferInit, failed: failInit });
  await page.route('https://static.line-scdn.net/liff/edge/2/sdk.js', route => {
    sdkRequests.push(route.request().url());
    return route.fulfill({ contentType: 'text/javascript', body: `
      (() => {
        const state = globalThis.__lineMock;
        globalThis.liff = {
          init: async (config) => {
            state.initCalls.push({ config, url: location.href });
            if (state.deferInit) await new Promise(resolve => { state.finishInit = resolve; });
            if (state.failInit) throw new Error('UNAUTHORIZED: mocked cancelled provider authorization');
          },
          isLoggedIn: () => !!state.account,
          isInClient: () => false,
          getIDToken: () => state.account ? 'token-' + state.account : null,
          getDecodedIDToken: () => state.account ? { sub: state.account, exp: state.expires } : null,
          login: (config) => { state.loginCalls.push(config); },
          logout: () => { state.logoutCalls++; state.account = null; },
        };
      })();
    ` });
  });
  return sdkRequests;
}

function lineState(page: Page) {
  return page.evaluate(() => {
    const state = (globalThis as unknown as { __lineMock: LineMock }).__lineMock;
    return { ...state, finishInit: undefined };
  });
}

async function installedPwa(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true });
    const match = window.matchMedia.bind(window);
    window.matchMedia = (query) => {
      const result = match(query);
      if (query.includes('display-mode: standalone')) Object.defineProperty(result, 'matches', { value: true });
      return result;
    };
  });
}

async function displayColors(page: Page, label: '紅漲綠跌' | '綠漲紅跌') {
  await page.getByRole('button', { name: '顯示設定', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '顯示設定', exact: true });
  await dialog.getByText(label, { exact: true }).click();
  await expect(dialog.getByRole('radio', { name: label, exact: true })).toBeChecked();
  await dialog.getByRole('button', { name: '關閉', exact: true }).click();
}

async function accountDrawingCount(page: Page) {
  return page.evaluate(database => new Promise<number>((resolve, reject) => {
    const request = indexedDB.open(database);
    request.onupgradeneeded = () => { request.transaction!.abort(); reject(new Error('Account drawings were removed')); };
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const tx = db.transaction('symbols', 'readonly');
      const records = tx.objectStore('symbols').getAll();
      tx.oncomplete = () => {
        const count = (records.result as { drawings: unknown[] }[]).reduce((total, record) => total + record.drawings.length, 0);
        db.close(); resolve(count);
      };
      tx.onerror = () => { db.close(); reject(tx.error); };
    };
  }), accountDatabase());
}

test('an ordinary external visitor never loads LIFF or stores guest actions', async ({ page }) => {
  const sdk = await externalSdk(page);
  const privateActions: string[] = [];
  await page.addInitScript(() => {
    const state = globalThis as unknown as { __persistentCalls: string[] };
    state.__persistentCalls = [];
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) { state.__persistentCalls.push(key); return set.call(this, key, value); };
    const open = IDBFactory.prototype.open;
    IDBFactory.prototype.open = function (...args) { state.__persistentCalls.push(`idb:${args[0]}`); return open.apply(this, args); };
  });
  await page.route('**/functions/v1/user-settings', route => {
    privateActions.push(route.request().postDataJSON().action);
    return route.fulfill({ status: 403, json: { ok: false } });
  });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console');
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await displayColors(page, '綠漲紅跌');
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'us');
  expect(await page.evaluate(() => (globalThis as unknown as { __persistentCalls: string[] }).__persistentCalls)).toEqual([]);
  expect(sdk).toEqual([]);
  expect(privateActions).toEqual([]);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-colors', 'tw');
  expect(sdk).toEqual([]);
  expect(privateActions).toEqual([]);
});

test('only the explicit desktop-browser login button initializes LIFF and starts same-partition login', async ({ page }) => {
  const sdk = await externalSdk(page);
  const privateActions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    privateActions.push(route.request().postDataJSON().action);
    return route.fulfill({ status: 403, json: { ok: false } });
  });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console');
  expect(sdk).toEqual([]);
  await page.getByRole('button', { name: '使用 LINE 登入', exact: true }).click();
  await expect.poll(async () => (await lineState(page)).loginCalls.length).toBe(1);
  const state = await lineState(page);
  expect(state.initCalls).toHaveLength(1);
  expect(state.initCalls[0].config).toMatchObject({ liffId: '2010330411-6JhrotT9', withLoginOnExternalBrowser: false });
  const redirect = new URL(state.loginCalls[0].redirectUri!);
  expect(redirect.origin).toBe(new URL(page.url()).origin);
  expect(redirect.pathname).toBe('/my-stock-screener/atlas/');
  expect(redirect.searchParams.get('atlasLineLogin')).toBe('1');
  expect(redirect.searchParams.get('page')).toBe('console');
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('focus')); });
  expect((await lineState(page)).loginCalls).toHaveLength(1);
  expect(privateActions).toEqual([]);
  expect(sdk).toHaveLength(1);
});

for (const rejected of ['denied', 'mismatched'] as const) {
  test(`a ${rejected} server grant keeps explicit browser login and personal changes in RAM`, async ({ page }) => {
    await externalSdk(page, DEVICE_USER_ID);
    const actions: Record<string, unknown>[] = [];
    await page.route('**/functions/v1/user-settings', route => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      actions.push(body);
      return rejected === 'denied'
        ? route.fulfill({ status: 403, json: { ok: false, error: 'No grant' } })
        : route.fulfill({ json: accessEnvelope(SECOND_USER_ID, 'cloud') });
    });
    await mockSite(page, { report: buildReport(20) });
    await page.goto('./?page=console');
    await page.getByRole('button', { name: '使用 LINE 登入', exact: true }).click();
    await expect.poll(() => actions.length).toBeGreaterThan(0);
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
    expect(actions.every(action => action.action === 'access.get' && action.idToken === `token-${DEVICE_USER_ID}`)).toBe(true);
    await displayColors(page, '綠漲紅跌');
    await expect(page.locator('html')).toHaveAttribute('data-colors', 'us');
    expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('atlas-account-')))).toEqual([]);
    expect(await page.evaluate(() => indexedDB.databases())).toEqual([]);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-colors', 'tw');
    await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  });
}

test('the OAuth callback keeps its code until SDK init finishes, then verifies the server grant before routing', async ({ page }) => {
  await externalSdk(page, DEVICE_USER_ID, true);
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    actions.push(route.request().postDataJSON().action);
    return route.fulfill({ json: accessEnvelope() });
  });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console&atlasLineLogin=1&code=provider-code&state=provider-state&liffClientId=2010330411', { waitUntil: 'domcontentloaded' });
  await expect.poll(async () => (await lineState(page)).initCalls.length).toBe(1);
  expect((await lineState(page)).initCalls[0].url).toContain('code=provider-code');
  expect(new URL(page.url()).searchParams.get('code')).toBe('provider-code');
  expect(actions).toEqual([]);
  await page.evaluate(() => (globalThis as unknown as { __lineMock: LineMock }).__lineMock.finishInit!());
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'device');
  await expect.poll(() => new URL(page.url()).searchParams.has('code')).toBe(false);
  expect(new URL(page.url()).searchParams.get('page')).toBe('console');
  expect(new URL(page.url()).searchParams.has('state')).toBe(false);
  expect(new URL(page.url()).searchParams.has('atlasLineLogin')).toBe(false);
  expect(actions).toEqual(['access.get']);
  expect((await lineState(page)).loginCalls).toEqual([]);
});

for (const [kind, suffix, marker] of [
  ['code without state', '?page=console&code=fixture-code&liffClientId=2010330411&liffRedirectUri=fixture', 'code=fixture-code'],
  ['LIFF primary state', '?liff.state=%2Fatlas%2F%3Fpage%3Dconsole', 'liff.state='],
  ['LIFF primary fragment', '?page=console#access_token=fixture-access&context_token=fixture-context', 'context_token='],
] as const) test(`${kind} reaches SDK init before the router can remove it`, async ({ page }) => {
  await installedPwa(page);
  await externalSdk(page, DEVICE_USER_ID, true);
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    actions.push(route.request().postDataJSON().action);
    return route.fulfill({ json: accessEnvelope() });
  });
  await mockSite(page);
  await page.goto('./' + suffix, { waitUntil: 'domcontentloaded' });
  await expect.poll(async () => (await lineState(page)).initCalls.length).toBe(1);
  expect((await lineState(page)).initCalls[0].url).toContain(marker);
  expect(page.url()).toContain(marker);
  expect(actions).toEqual([]);
  await page.evaluate(() => (globalThis as unknown as { __lineMock: LineMock }).__lineMock.finishInit!());
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'device');
  expect(new URL(page.url()).searchParams.get('page')).toBe('console');
  expect(page.url()).not.toContain(marker);
  expect(actions).toEqual(['access.get']);
  expect((await lineState(page)).loginCalls).toEqual([]);
});

test('a return without an SDK session explains retry and sends no access request', async ({ page }) => {
  await externalSdk(page);
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    actions.push(route.request().postDataJSON().action);
    return route.fulfill({ status: 401, json: { ok: false } });
  });
  await mockSite(page);
  await page.goto('./?page=console&code=fixture-code&liffClientId=2010330411');
  await expect(page.locator('[data-storage-mode]')).toContainText('回傳未完成驗證');
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  expect(actions).toEqual([]);
  expect((await lineState(page)).loginCalls).toEqual([]);
  await page.getByRole('button', { name: '使用 LINE 登入', exact: true }).click();
  await expect.poll(async () => (await lineState(page)).loginCalls.length).toBe(1);
});

test('an explicit login timeout releases the button and a late SDK init never navigates', async ({ page }) => {
  await page.clock.install();
  await externalSdk(page, null, true);
  await mockSite(page);
  await page.goto('./?page=console');
  const login = page.getByRole('button', { name: '使用 LINE 登入', exact: true });
  await login.click();
  await expect.poll(async () => (await lineState(page)).initCalls.length).toBe(1);
  await expect(page.getByRole('button', { name: '正在開啟 LINE 登入…', exact: true })).toBeDisabled();
  await page.clock.fastForward(16000);
  await expect(page.locator('[data-storage-mode]')).toContainText('逾時');
  await expect(login).toBeEnabled();
  await page.evaluate(() => (globalThis as unknown as { __lineMock: LineMock }).__lineMock.finishInit!());
  await expect.poll(async () => (await lineState(page)).loginCalls.length).toBe(0);
  await login.click();
  await expect.poll(async () => (await lineState(page)).loginCalls.length).toBe(1);
  expect((await lineState(page)).initCalls).toHaveLength(1);
});

test('a cancelled OAuth callback stays a guest and never retries or redirects automatically', async ({ page }) => {
  await externalSdk(page, null, false, true);
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    actions.push(route.request().postDataJSON().action);
    return route.fulfill({ status: 403, json: { ok: false } });
  });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console&atlasLineLogin=1&error=access_denied&state=provider-state');
  await expect(page.getByRole('button', { name: '使用 LINE 登入', exact: true })).toBeVisible();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('focus')); });
  expect((await lineState(page)).initCalls).toHaveLength(1);
  expect((await lineState(page)).loginCalls).toEqual([]);
  expect(actions).toEqual([]);
});

for (const inLine of [false, true]) test(`a stalled ${inLine ? 'LINE' : 'external'} OAuth init preserves its callback and offers guest recovery`, async ({ page }) => {
  if (inLine) await page.addInitScript(() => Object.defineProperty(navigator, 'userAgent', { configurable: true, get: () => 'Mozilla/5.0 Line/14.0.0' }));
  await page.clock.install();
  const sdk = await externalSdk(page, DEVICE_USER_ID, true);
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    actions.push(route.request().postDataJSON().action);
    return route.fulfill({ json: accessEnvelope() });
  });
  await mockSite(page);
  await page.goto('./?page=console&atlasLineLogin=1&code=provider-code&state=provider-state', { waitUntil: 'domcontentloaded' });
  await expect.poll(async () => (await lineState(page)).initCalls.length).toBe(1);
  await page.clock.fastForward(16000);
  await expect(page.getByRole('alert')).toContainText('LINE 登入尚未完成');
  await expect(page.getByRole('button', { name: '重新嘗試登入' })).toBeVisible();
  expect(new URL(page.url()).searchParams.get('code')).toBe('provider-code');
  expect(actions).toEqual([]);
  expect(sdk).toHaveLength(1);
  await page.getByRole('link', { name: '先開啟 Atlas' }).click();
  await expect(page.locator('.site')).toBeVisible();
  expect(new URL(page.url()).searchParams.has('code')).toBe(false);
  expect(sdk).toHaveLength(1);
  expect(actions).toEqual([]);
});

test('SDK browser signout isolates the workspace without deleting drawings, and expiry never redirects to login', async ({ page }) => {
  const sdk = await externalSdk(page, DEVICE_USER_ID);
  const actions: string[] = [];
  await page.route('**/functions/v1/user-settings', route => {
    actions.push(route.request().postDataJSON().action);
    return route.fulfill({ json: accessEnvelope() });
  });
  await mockSite(page, { report: buildReport(20) });
  await page.goto('./?page=console&atlasLineLogin=1&code=provider-code&state=provider-state&liffClientId=2010330411');
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'device');
  await page.getByRole('button', { name: '個股', exact: true }).click();
  await expect(page.getByTestId('chart').locator('canvas').first()).toBeVisible();
  await expect(page.getByTestId('ohlc-header')).toContainText('O');
  await chooseDrawingTool(page, '水平線');
  const bounds = (await page.getByTestId('chart').boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width * 0.5, bounds.y + bounds.height * 0.4);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.5 + 25, bounds.y + bounds.height * 0.4 + 10, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator('.chart-status')).toContainText('畫線 1 條');
  await expect.poll(() => accountDrawingCount(page)).toBe(1);
  await page.getByRole('button', { name: '登出 LINE', exact: true }).click();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  expect((await lineState(page)).logoutCalls).toBe(1);
  expect(await accountDrawingCount(page)).toBe(1);
  await expect(page.locator('.chart-status')).toContainText('畫線 0 條');
  await page.evaluate(account => { (globalThis as unknown as { __lineMock: LineMock }).__lineMock.account = account; }, DEVICE_USER_ID);
  await page.getByRole('button', { name: '使用 LINE 登入', exact: true }).click();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'device');
  await expect(page.locator('.chart-status')).toContainText('畫線 1 條');
  const requestsBeforeReload = sdk.length, grantsBeforeReload = actions.length;
  await page.reload();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await expect(page.locator('.chart-status')).toContainText('畫線 0 條');
  expect(sdk).toHaveLength(requestsBeforeReload);
  expect((await lineState(page)).initCalls).toEqual([]);
  expect(actions).toHaveLength(grantsBeforeReload);
  await page.getByRole('button', { name: '使用 LINE 登入', exact: true }).click();
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'device');
  await expect(page.locator('.chart-status')).toContainText('畫線 1 條');
  expect(sdk).toHaveLength(requestsBeforeReload + 1);
  expect((await lineState(page)).initCalls).toHaveLength(1);
  expect(actions.length).toBeGreaterThan(grantsBeforeReload);
  await page.evaluate(() => {
    (globalThis as unknown as { __lineMock: LineMock }).__lineMock.expires = Date.now() / 1000;
    window.dispatchEvent(new Event('focus'));
  });
  await expect(page.locator('[data-storage-mode]')).toHaveAttribute('data-storage-mode', 'none');
  await expect(page.locator('.chart-status')).toContainText('畫線 0 條');
  expect(await accountDrawingCount(page)).toBe(1);
  expect((await lineState(page)).loginCalls).toEqual([]);
  expect(actions.every(action => action === 'access.get')).toBe(true);
});
