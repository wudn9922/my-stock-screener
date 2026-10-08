import type { Page } from '@playwright/test';

const categoryForTool = {
  'Trend Line': 'lines',
  'Horizontal Line': 'lines',
  'Horizontal Ray': 'lines',
  'Vertical Line': 'lines',
  'Parallel Channel': 'channel',
  Rectangle: 'shapes',
  'Fibonacci Retracement': 'fibonacci',
  'Price Range': 'measurements',
  'Date Range': 'measurements',
  'Price + Date Range': 'measurements',
} as const;

export type DrawingToolName = keyof typeof categoryForTool;

export async function chooseDrawingTool(page: Page, name: DrawingToolName): Promise<void> {
  const category = categoryForTool[name];
  const categoryButton = page.getByRole('button', {
    name: `Drawing category ${category}`,
    exact: true,
  });
  if (!(await categoryButton.isVisible())) {
    await page.getByRole('button', { name: 'Drawing Tools', exact: true }).click();
  }
  await categoryButton.click();
  await page.getByRole('button', { name, exact: true }).click();
}

export async function clickDrawingUtility(page: Page, name: string): Promise<void> {
  const utility = page.getByRole('button', { name, exact: true });
  if (!(await utility.isVisible())) {
    const launcher = page.getByRole('button', { name: 'Drawing Tools', exact: true });
    if (await launcher.isVisible()) await launcher.click();

    const commonCategory = page.getByRole('button', {
      name: 'Drawing category common',
      exact: true,
    });
    if (await commonCategory.isVisible() && !(await utility.isVisible())) await commonCategory.click();
  }
  await utility.click();
}
