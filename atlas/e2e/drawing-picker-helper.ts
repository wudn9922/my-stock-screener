import type { Page } from '@playwright/test';

const categoryForTool = {
  '趨勢線': '線條',
  '水平線': '線條',
  '水平射線': '線條',
  '垂直線': '線條',
  '平行通道': '通道',
  '矩形': '形狀',
  '費波那契回撤': '費波那契',
  '價格區間': '測量',
  '日期區間': '測量',
  '價格與日期區間': '測量',
} as const;

export type DrawingToolName = keyof typeof categoryForTool;

export async function chooseDrawingTool(page: Page, name: DrawingToolName): Promise<void> {
  const category = categoryForTool[name];
  const categoryButton = page.getByRole('button', {
    name: `繪圖分類 ${category}`,
    exact: true,
  });
  if (!(await categoryButton.isVisible())) {
    await page.getByRole('button', { name: '繪圖工具', exact: true }).click();
  }
  await categoryButton.click();
  await page.getByRole('button', { name, exact: true }).click();
}

export async function clickDrawingUtility(page: Page, name: string): Promise<void> {
  const utility = page.getByRole('button', { name, exact: true });
  if (!(await utility.isVisible())) {
    const launcher = page.getByRole('button', { name: '繪圖工具', exact: true });
    if (await launcher.isVisible()) await launcher.click();

    const commonCategory = page.getByRole('button', {
      name: '繪圖分類 常用',
      exact: true,
    });
    if (await commonCategory.isVisible() && !(await utility.isVisible())) await commonCategory.click();
  }
  await utility.click();
}
