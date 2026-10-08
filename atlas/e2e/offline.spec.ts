import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { resolve } from 'node:path';
import { once } from 'node:events';
// WebKit's Playwright forced-offline mode fails navigation before SW dispatch.
// Stop an isolated real server instead; all page requests then face ECONNREFUSED.
async function temporaryPreview(): Promise<ChildProcess> {
  const child = spawn(
    process.execPath,
    [
      resolve('node_modules/vite/bin/vite.js'),
      'preview',
      '--host',
      '127.0.0.1',
      '--port',
      '4174',
      '--strictPort',
    ],
    { stdio: 'pipe' },
  );
  await new Promise<void>((done, reject) => {
    child.stdout!.on('data', (data) => {
      if (data.toString().includes('4174')) done();
    });
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error('Temporary preview exited ' + code)));
  });
  return child;
}
test('production PWA opens Demo and IndexedDB settings offline after shell caching', async ({
  page,
  context,
  browserName,
}) => {
  let server: ChildProcess | undefined;
  try {
    if (browserName === 'webkit') server = await temporaryPreview();
    await page.goto(`http://127.0.0.1:${server ? 4174 : 4173}`);
    await expect(page.getByTestId('ohlc-header')).toContainText('O ');
    await expect(page.locator('.chart-loading')).toHaveCount(0);
    await page.evaluate(async () => {
      await navigator.serviceWorker.ready;
    });
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
    if (await page.locator('.mobile-nav').isVisible())
      await page.locator('.mobile-nav').getByRole('button', { name: 'SMA', exact: true }).click();
    await page.getByLabel('SMA period', { exact: true }).fill('24');
    await page.getByRole('button', { name: 'Add SMA', exact: true }).click();
    const close = page.getByRole('button', { name: 'Close panel', exact: true });
    if (await close.isVisible()) await close.click();
    await expect(page.locator('.app-footer')).toContainText('WORKSPACE SAVED');
    if (server) {
      const exited = once(server, 'exit');
      server.kill('SIGTERM');
      await exited;
      server = undefined;
    } else await context.setOffline(true);
    await page.reload();
    await expect(page.getByTestId('ohlc-header')).toContainText('O ');
    await expect(page.locator('.chart-loading')).toHaveCount(0);
    await expect(page.locator('.indicator-chip')).toContainText('SMA 24');
    await expect(page.getByLabel('Market data source', { exact: true })).toHaveValue('demo');
  } finally {
    server?.kill('SIGTERM');
    await context.setOffline(false);
  }
});
