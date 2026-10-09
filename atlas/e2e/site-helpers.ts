import { expect, type Page } from '@playwright/test';

/**
 * Shared navigation for the redesigned Atlas shell (global search, dock rail / bottom-sheet panels).
 * Labels are the user-facing Traditional Chinese names.
 */
export type WorkspacePanel = 'watchlist' | 'indicators' | 'drawings' | 'backtest' | 'alerts' | 'settings';
export const panelNames: Record<WorkspacePanel, string> = {
  watchlist: '自選清單',
  indicators: '指標',
  drawings: '畫線物件',
  backtest: '策略回測',
  alerts: '價格提醒',
  settings: '工作區設定',
};

export async function isMobileShell(page: Page): Promise<boolean> {
  return page.locator('.site-tabbar').isVisible();
}

/** Opens a workspace panel: the desktop dock, or the mobile/fullscreen bottom sheet. */
export async function openPanel(page: Page, panel: WorkspacePanel): Promise<void> {
  const name = panelNames[panel];
  const sheet = page.locator('.bottom-sheet');
  if (await sheet.isVisible()) {
    await sheet.getByRole('tab', { name, exact: true }).click();
    await expect(sheet.getByRole('tab', { name, exact: true })).toHaveAttribute('aria-selected', 'true');
    return;
  }
  const dock = page.locator('.dock-rail').getByRole('button', { name, exact: true });
  if (await dock.isVisible()) {
    if ((await dock.getAttribute('aria-pressed')) !== 'true') await dock.click();
    await expect(dock).toHaveAttribute('aria-pressed', 'true');
    return;
  }
  await page.getByRole('button', { name: '更多面板', exact: true }).click();
  await sheet.getByRole('tab', { name, exact: true }).click();
  await expect(sheet.getByRole('tab', { name, exact: true })).toHaveAttribute('aria-selected', 'true');
}

export async function closePanel(page: Page): Promise<void> {
  const close = page.getByRole('button', { name: '關閉面板', exact: true });
  if (await close.isVisible()) await close.click();
}

/** Types into the global stock search (top bar on desktop, 搜尋 sheet on phones) and confirms. */
export async function searchSymbol(page: Page, text: string): Promise<void> {
  await closePanel(page);
  if (await isMobileShell(page)) {
    const input = page.getByRole('dialog', { name: '搜尋股票' }).getByRole('combobox', { name: '搜尋股票' });
    if (!(await input.isVisible())) await page.locator('.site-tabbar').getByRole('button', { name: '搜尋', exact: true }).click();
    await input.fill(text);
    await expect(page.locator('.search-results [role="option"]').first()).toBeVisible();
    await input.press('Enter');
    return;
  }
  const input = page.locator('.site-topbar').getByRole('combobox', { name: '搜尋股票' });
  await input.fill(text);
  await expect(page.locator('.site-topbar .search-results [role="option"]').first()).toBeVisible();
  await input.press('Enter');
}

/** The data-source select lives in 工作區設定; this opens the panel, selects, and closes the sheet. */
export async function setProvider(page: Page, value: 'demo' | 'snapshot' | 'yahoo' | 'market'): Promise<void> {
  await openPanel(page, 'settings');
  await page.getByLabel('資料來源', { exact: true }).selectOption(value);
  await closePanel(page);
}

export async function expectProvider(page: Page, value: string): Promise<void> {
  await openPanel(page, 'settings');
  await expect(page.getByLabel('資料來源', { exact: true })).toHaveValue(value);
  await closePanel(page);
}

/** Clicks 匯出設定 (in 工作區設定) and returns the download. */
export async function exportSettings(page: Page) {
  await openPanel(page, 'settings');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '匯出設定', exact: true }).click();
  const file = await download;
  await closePanel(page);
  return file;
}

export function saveState(page: Page) {
  return page.getByTestId('save-state');
}

/** Clicks 匯出設定 in 工作區設定 (start `page.waitForEvent('download')` first), then closes the sheet. */
export async function clickExport(page: Page): Promise<void> {
  await openPanel(page, 'settings');
  await page.getByRole('button', { name: '匯出設定', exact: true }).click();
  await closePanel(page);
}
