import { test } from '@playwright/test';
import { verifyTimeframeOwnership } from './timeframe-scope-helper';

test('1D, 1W and 1M indicators and drawings stay isolated through reload and export', async ({
  page,
}) => {
  await page.goto('/');
  await verifyTimeframeOwnership(page);
});
