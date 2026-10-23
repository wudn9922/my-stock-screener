import type { Page } from '@playwright/test';

export const DEVICE_USER_ID = `U${'0'.repeat(32)}`;
export const SECOND_USER_ID = `U${'1'.repeat(32)}`;
export const OWNER_USER_ID = 'Ua0037dbd5037b64fc76804c60520ad76';
export type TestStorageMode = 'none' | 'device' | 'cloud';
export const accountDatabase = (userId = DEVICE_USER_ID, mode: TestStorageMode = 'device') =>
  `atlas-terminal-screener:account:${userId}:${mode}`;
export const accountPreference = (key: string, userId = DEVICE_USER_ID, mode: TestStorageMode = 'device') =>
  `atlas-account-screener:${userId}:${mode}:${key}`;

/** A verified LINE session, used explicitly only by tests that exercise authorized persistence. */
export async function installLineAccount(page: Page, userId = DEVICE_USER_ID, token = 'test-token') {
  await page.addInitScript(({ initialId, initialToken }) => {
    const state = globalThis as unknown as { __lineAccount: string | null; __lineToken: string; liff: unknown };
    state.__lineAccount = initialId;
    state.__lineToken = initialToken;
    Object.defineProperty(navigator, 'userAgent', { get: () => 'Mozilla/5.0 Line/14.0.0' });
    state.liff = {
      init: async () => {},
      isLoggedIn: () => !!state.__lineAccount,
      getIDToken: () => state.__lineAccount ? state.__lineToken : null,
      getDecodedIDToken: () => state.__lineAccount ? { sub: state.__lineAccount, exp: Date.now() / 1000 + 3600 } : null,
    };
  }, { initialId: userId, initialToken: token });
}
export function accessEnvelope(userId = DEVICE_USER_ID, storageMode: TestStorageMode = 'device', isAdmin = false, revision = 'test-1') {
  return { ok: true, data: { userId, storageMode, isAdmin, revision } };
}
export async function mockDeviceAccount(page: Page) {
  await installLineAccount(page);
  await page.route('**/functions/v1/user-settings', route => {
    const body = route.request().postDataJSON() as { action?: string };
    return body.action === 'access.get'
      ? route.fulfill({ json: accessEnvelope() })
      : route.fulfill({ status: 403, json: { ok: false, error: 'Device account must not request cloud personal data' } });
  });
}
