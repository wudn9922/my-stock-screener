import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope, installLineAccount } from './account-fixture';
import { openPanel } from '../e2e/site-helpers';

for (const mode of ['guest', 'none', 'device', 'cloud'] as const) test(`A5 ${mode}: price alert offers Discord only through the existing cloud horizontal drawing`, async ({ page }, info) => {
  await mockSite(page);
  if (mode !== 'guest') await installLineAccount(page);
  await page.route('**/functions/v1/user-settings', route => {
    const body = route.request().postDataJSON();
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, mode === 'none' ? 'none' : mode === 'cloud' ? 'cloud' : 'device') });
    return route.fulfill({ json: { ok: true, data: [] } });
  });
  const saves: { symbol: string; drawings: { id: string; type: string; visible: boolean; points: { price: number; timeframe: string }[] }[] }[] = [];
  await page.route('**/functions/v1/chart-drawings', route => {
    const body = route.request().postDataJSON();
    if (mode !== 'cloud') throw new Error('Non-cloud account attempted a drawing request');
    if (body.action === 'atlas.list') return route.fulfill({ json: { ok: true, records: [] } });
    expect(body.action).toBe('atlas.save'); saves.push(body);
    return route.fulfill({ json: { ok: true, updatedAt: body.updatedAt } });
  });
  const notifications: string[] = [];
  await page.route(/https:\/\/(?:[^/]*discord[^/]*|api\.line\.me)\//, route => { notifications.push(route.request().url()); return route.abort(); });
  await page.goto('./?page=chart&symbol=AAPL&tf=1D');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ');
  await openPanel(page, 'alerts');
  const panel = page.locator('.alert-panel').filter({ visible: true });
  await expect(panel).toContainText('瀏覽器提醒：只在開著此圖表時檢查');
  await expect(panel).toContainText('Discord 推播：由伺服器監控');
  await panel.getByLabel('提醒價格', { exact: true }).fill('123.45');
  await panel.getByRole('button', { name: '建立提醒', exact: true }).click();
  const button = panel.getByRole('button', { name: '推播到 Discord', exact: true });
  if (mode === 'cloud') {
    await expect(button).toBeEnabled(); await button.click();
    await expect(panel).toContainText('已建立水平線；同步完成後由伺服器監控突破／跌破。');
    await expect.poll(() => saves.length).toBe(1);
    expect(saves[0]).toMatchObject({ symbol: 'AAPL', drawings: [{ type: 'horizontal', visible: true, points: [{ price: 123.45, timeframe: '1D' }] }] });
    await expect(button).toBeEnabled(); await button.click();
    await expect(panel).toContainText('此價格已有水平線');
    expect(saves).toHaveLength(1);
  } else {
    await expect(button).toBeDisabled();
    await expect(panel).toContainText('需具有 Supabase 雲端保存授權');
    expect(saves).toHaveLength(0);
  }
  expect(notifications).toEqual([]);
  expect(await panel.evaluate(el => el.scrollWidth - el.clientWidth)).toBe(0);
  await button.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath(`a5-${mode}-price-alert.png`) });
});
