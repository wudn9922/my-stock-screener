import { test } from '@playwright/test';
import { verifyTimeframeOwnership } from '../e2e/timeframe-scope-helper';

test('Pages keeps 1D, 1W and 1M indicators and drawings isolated through reload and export', async ({
  page,
}) => {
  await page.goto('/lightweight-drawing-lab/');
  await verifyTimeframeOwnership(page);
});
