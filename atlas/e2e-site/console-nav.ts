import { expect, type Locator, type Page } from '@playwright/test';

/** Phones and tablets (1099px and below) open 我的 as grouped rows and sub-views; the desktop page keeps its tabs. */
export const isCompactConsole = (page: Page) => (page.viewportSize()?.width ?? Number.POSITIVE_INFINITY) <= 1099;

export type ConsoleSection = 'parameters' | 'lists' | 'admin' | 'display' | 'status' | 'backup';
const TAB: Partial<Record<ConsoleSection, string>> = { parameters: '均線參數', lists: '清單', admin: '管理員', display: '顯示與App', status: '資料狀態' };
const ROW: Record<ConsoleSection, string> = {
  parameters: '個股均線參數', lists: '清單', admin: '管理員儲存權限', display: '安裝到主畫面', status: '資料狀態', backup: '備份與還原',
};

/** The 我的 row (compact) or tab (desktop) that opens a section. */
export function consoleEntry(page: Page, section: ConsoleSection): Locator {
  return isCompactConsole(page)
    ? page.getByRole('link', { name: new RegExp(`^${ROW[section]}`) })
    : page.getByRole('button', { name: TAB[section] ?? ROW[section], exact: true });
}

/** Opens a 我的 section on either layout; from another sub-view it goes back to the list first. */
export async function openConsoleSection(page: Page, section: ConsoleSection) {
  const entry = consoleEntry(page, section);
  if (isCompactConsole(page)) {
    const back = page.getByRole('button', { name: '返回我的', exact: true });
    // After a reload the lazy page may still be mounting: wait for the list or a sub-view before choosing.
    await expect(entry.or(back)).toBeVisible();
    if (await back.isVisible()) await back.click();
  }
  await entry.click();
}
