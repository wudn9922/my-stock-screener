import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

const IPHONE_SAFARI = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const IPAD_DESKTOP_SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15';
const ANDROID_CHROME = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';

async function deviceBrowser(page: Page, userAgent: string, options: { maxTouchPoints?: number; standalone?: boolean; displayMode?: boolean } = {}) {
  if (/\bLine\//i.test(userAgent)) {
    await page.route('https://static.line-scdn.net/liff/edge/2/sdk.js', route => route.fulfill({
      contentType: 'application/javascript',
      body: 'window.liff = { init: async () => {}, isLoggedIn: () => false, getIDToken: () => null };',
    }));
  }
  await page.addInitScript(({ ua, touchPoints, standalone, displayMode }) => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, get: () => ua });
    Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, get: () => touchPoints });
    Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => standalone });
    if (displayMode) {
      const originalMatchMedia = window.matchMedia.bind(window);
      window.matchMedia = (query: string) => {
        const result = originalMatchMedia(query);
        if (query.includes('display-mode: standalone')) Object.defineProperty(result, 'matches', { configurable: true, get: () => true });
        return result;
      };
    }
  }, { ua: userAgent, touchPoints: options.maxTouchPoints ?? 1, standalone: options.standalone ?? false, displayMode: options.displayMode ?? false });
}

async function openInstallSettings(page: Page) {
  await page.getByRole('button', { name: '顯示設定', exact: true }).click();
  const panel = page.getByRole('region', { name: '安裝 Atlas', exact: true });
  await expect(panel).toBeVisible();
  return panel;
}

async function offerInstall(page: Page, outcome: 'accepted' | 'dismissed') {
  return page.evaluate((choice) => {
    const state = window as unknown as { installPromptCalls: number; promptHadUserActivation: boolean };
    state.installPromptCalls = 0;
    state.promptHadUserActivation = false;
    const event = new Event('beforeinstallprompt', { cancelable: true });
    Object.defineProperties(event, {
      platforms: { value: ['web'] },
      userChoice: { value: Promise.resolve({ outcome: choice, platform: 'web' }) },
      prompt: { value: async () => {
        state.installPromptCalls += 1;
        state.promptHadUserActivation = navigator.userActivation?.isActive ?? false;
        return { outcome: choice, platform: 'web' };
      } },
    });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  }, outcome);
}

test('iPhone Safari explains Share → Add to Home Screen without claiming automatic installation', async ({ page }) => {
  await deviceBrowser(page, IPHONE_SAFARI);
  await mockSite(page);
  await page.goto('./');
  const panel = await openInstallSettings(page);
  await expect(panel).toContainText('分享');
  await expect(panel).toContainText('加入主畫面');
  await expect(panel).toContainText('以 Web App 打開');
  await expect(panel.getByRole('button', { name: '安裝 Atlas', exact: true })).toHaveCount(0);
  await expect(panel).not.toContainText('已加入主畫面');
});

test('iPad Safari desktop user agent still receives Apple installation instructions', async ({ page }) => {
  await deviceBrowser(page, IPAD_DESKTOP_SAFARI, { maxTouchPoints: 5 });
  await mockSite(page);
  await page.goto('./');
  const panel = await openInstallSettings(page);
  await expect(panel).toContainText('Safari');
  await expect(panel).toContainText('分享');
  await expect(panel).toContainText('以 Web App 打開');
  await expect(panel.getByRole('button', { name: '安裝 Atlas', exact: true })).toHaveCount(0);
});

for (const [device, ua, browser] of [
  ['iPhone', IPHONE_SAFARI, 'Safari'],
  ['Android', ANDROID_CHROME, 'Chrome'],
] as const) {
  test(`LINE on ${device} explains opening ${browser} and copies the current page link`, async ({ page }) => {
    await deviceBrowser(page, `${ua} Line/14.0.0`);
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
        writeText: async (value: string) => { (window as unknown as { copiedInstallLink: string }).copiedInstallLink = value; },
      } });
    });
    await mockSite(page);
    await page.goto('./?page=markets&market=us');
    const panel = await openInstallSettings(page);
    await expect(panel).toContainText(`在 ${browser} 開啟`);
    await expect(panel).toContainText('LINE');
    const currentLink = page.url();
    await panel.getByRole('button', { name: '複製目前頁面連結', exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { copiedInstallLink?: string }).copiedInstallLink)).toBe(currentLink);
    await expect(panel.getByRole('button', { name: '安裝 Atlas', exact: true })).toHaveCount(0);
  });
}

test('Android retains an early install event and prompts only after an explicit click; cancellation remains uninstalled', async ({ page }) => {
  await deviceBrowser(page, ANDROID_CHROME);
  await mockSite(page);
  await page.goto('./');
  expect(await offerInstall(page, 'dismissed')).toBe(true);
  expect(await page.evaluate(() => (window as unknown as { installPromptCalls: number }).installPromptCalls)).toBe(0);
  const panel = await openInstallSettings(page);
  const install = panel.getByRole('button', { name: '安裝 Atlas', exact: true });
  await expect(install).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { installPromptCalls: number }).installPromptCalls)).toBe(0);
  await install.click();
  await expect(panel).toContainText('已取消安裝');
  expect(await page.evaluate(() => (window as unknown as { installPromptCalls: number }).installPromptCalls)).toBe(1);
  await expect(panel).not.toContainText('Atlas 已加入主畫面');
  await expect(panel).not.toContainText('已從主畫面開啟 Atlas');
  await expect(install).toHaveCount(0);
  await expect(panel).toContainText('Chrome 選單');
});

test('Android accepted prompt awaits actual appinstalled before reporting installation complete', async ({ page }) => {
  await deviceBrowser(page, ANDROID_CHROME);
  await mockSite(page);
  await page.goto('./');
  await offerInstall(page, 'accepted');
  const panel = await openInstallSettings(page);
  await panel.getByRole('button', { name: '安裝 Atlas', exact: true }).click();
  await expect(panel).toContainText('已送出安裝要求');
  await expect(panel).not.toContainText('Atlas 已加入主畫面');
  await page.evaluate(() => window.dispatchEvent(new Event('appinstalled')));
  await expect(panel).toContainText('Atlas 已加入主畫面');
  await expect(panel.getByRole('button', { name: '安裝 Atlas', exact: true })).toHaveCount(0);
});

test('Android without a browser install event explains the Chrome menu fallback', async ({ page }) => {
  await deviceBrowser(page, ANDROID_CHROME);
  await mockSite(page);
  await page.goto('./');
  const panel = await openInstallSettings(page);
  await expect(panel).toContainText('Chrome 選單');
  await expect(panel).toContainText(/安裝應用程式|加入主畫面/);
  await expect(panel.getByRole('button', { name: '安裝 Atlas', exact: true })).toHaveCount(0);
});

for (const source of ['navigator.standalone', 'display-mode'] as const) {
  test(`an existing ${source} session hides redundant installation steps`, async ({ page }) => {
    await deviceBrowser(page, IPHONE_SAFARI, { standalone: source === 'navigator.standalone', displayMode: source === 'display-mode' });
    await mockSite(page);
    await page.goto('./');
    // The installed app opens on its login page first.
    await page.getByRole('button', { name: '先以訪客瀏覽', exact: true }).click();
    const panel = await openInstallSettings(page);
    await expect(panel).toContainText('已從主畫面開啟 Atlas');
    await expect(panel.getByRole('button', { name: '安裝 Atlas', exact: true })).toHaveCount(0);
    await expect(panel.getByRole('list')).toHaveCount(0);
    await expect(panel.getByRole('button', { name: '複製目前頁面連結', exact: true })).toHaveCount(0);
  });
}

test('installation help remains scrollable and touch accessible in portrait and landscape', async ({ page }, info) => {
  await deviceBrowser(page, `${IPHONE_SAFARI} Line/14.0.0`);
  await mockSite(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('./');
  const panel = await openInstallSettings(page);
  const dialog = page.getByRole('dialog', { name: '顯示設定', exact: true });
  for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(viewport);
    const copyButton = panel.getByRole('button', { name: '複製目前頁面連結', exact: true });
    await copyButton.scrollIntoViewIfNeeded();
    await expect(copyButton).toBeVisible();
    const target = await copyButton.boundingBox();
    expect(target!.height).toBeGreaterThanOrEqual(44);
    expect(target!.width).toBeGreaterThanOrEqual(44);
    const dialogBox = await dialog.boundingBox();
    expect(dialogBox!.x).toBeGreaterThanOrEqual(0);
    expect(dialogBox!.y).toBeGreaterThanOrEqual(0);
    expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(viewport.width);
    expect(dialogBox!.y + dialogBox!.height).toBeLessThanOrEqual(viewport.height);
    expect(await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await dialog.screenshot({ path: info.outputPath(`pwa-settings-${viewport.width}.png`) });
  }
});

test('manifest keeps the installed identity and serves its icons under the screener project path', async ({ page }) => {
  await mockSite(page);
  await page.goto('./');
  const manifestLink = page.locator('link[rel="manifest"]');
  const manifestURL = await manifestLink.evaluate((element) => (element as HTMLLinkElement).href);
  expect(new URL(manifestURL).pathname).toBe('/my-stock-screener/atlas/manifest.webmanifest');
  const response = await page.request.get(manifestURL);
  expect(response.ok()).toBe(true);
  const manifest = await response.json() as { id: string; start_url: string; scope: string; display: string; icons: { src: string; sizes: string }[] };
  expect(manifest.id).toBe('./');
  expect(manifest.start_url).toBe('./');
  expect(manifest.scope).toBe('./');
  expect(manifest.display).toBe('standalone');
  expect(manifest.icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(['192x192', '512x512']));
  for (const icon of manifest.icons) {
    const url = new URL(icon.src, manifestURL);
    expect(url.pathname.startsWith('/my-stock-screener/atlas/')).toBe(true);
    const asset = await page.request.get(url.href);
    expect(asset.ok()).toBe(true);
    expect([...((await asset.body()).subarray(0, 8))]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
  }
});
