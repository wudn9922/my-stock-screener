import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';

// Shape written by ma-selector scripts/build_recommendations.py (synthetic values).
const recommendation = {
  version: 1,
  ticker: '2330.TW',
  kind: 'SMA',
  asof: '2026-10-17',
  bars: 1210,
  limited: false,
  referenceAsof: '2026-10-01',
  suitability: { short: 66.4, long: 99.4 },
  ranges: [
    { key: 'short', label: '短期', min: 15, max: 33, windowYears: 1, final: 24, best: 70.2, candidates: [
      { period: 20, score: 70.2, sarReturn: 0.12, sarDrawdown: -0.08, sarWinRate: 0.5, sarTrades: 10 },
      { period: 24, score: 69.1, sarReturn: 0.153, sarDrawdown: -0.05, sarWinRate: 0.6, sarTrades: 8 },
    ] },
    { key: 'medium', label: '中期', min: 34, max: 45, windowYears: 2, final: 38, best: 60, candidates: [{ period: 38, score: 60, sarReturn: 0.3, sarDrawdown: -0.1, sarWinRate: 0.4, sarTrades: 12 }] },
    { key: 'long', label: '長期', min: 46, max: 110, windowYears: 3, final: 89, best: 99, candidates: [{ period: 89, score: 99, sarReturn: -0.02, sarDrawdown: -0.2, sarWinRate: 0.3, sarTrades: 6 }] },
  ],
};

async function mockLine(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgent', { get: () => 'Mozilla/5.0 Line/14.0.0' });
    (globalThis as unknown as { liff: unknown }).liff = {
      init: async () => {},
      isLoggedIn: () => true,
      getIDToken: () => 'test-token',
      getDecodedIDToken: () => ({ sub: `U${'0'.repeat(32)}`, exp: Date.now() / 1000 + 3600 }),
    };
  });
  const saves: Record<string, unknown>[] = [];
  await page.route('**/functions/v1/user-settings', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    expect(body.idToken).toBe('test-token');
    if (body.action === 'stocks.save') saves.push(body);
    const data =
      body.action === 'stocks.list'
        ? [{ id: 7, ticker: '2330.TW', group_id: 'ungrouped', ma1: 20, ma2: 60, ma3: 0, ma4: 0 }]
        : body.action === 'stocks.save'
          ? [{ id: 7 }]
          : [];
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, data }) });
  });
  return saves;
}

test('chart shows the weekly recommendation, applies it on the daily chart and saves it as a fixed parameter', async ({ page }, testInfo) => {
  await mockSite(page);
  const saves = await mockLine(page);
  await page.route('**/my-stock-screener/report/ma-selector/2330.TW.json', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(recommendation) }),
  );
  await page.goto('./?page=chart&symbol=2330.TW&tf=1W');
  const card = page.getByTestId('ma-recommendation');
  const toggle = page.getByTestId('stock-stats-toggle');
  // Closed: one button sharing an existing header row (P/E on desktop, K線／財務 on phones); no extra row above the chart.
  await expect(toggle).toBeVisible();
  await expect(card).toBeHidden();
  const row = await page.evaluate(() => {
    const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
    const toggle = box('[data-testid="stock-stats-toggle"]');
    const shares = (other: DOMRect) => toggle.top < other.bottom && toggle.bottom > other.top;
    return { shares: shares(box('[data-testid="valuation"]')) || shares(box('.stock-tabs')), overflow: document.scrollingElement!.scrollWidth - innerWidth };
  });
  expect(row.shares).toBe(true);
  expect(row.overflow).toBeLessThanOrEqual(0);

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(card.locator('dd')).toHaveText('短 24（69） · 中 38（60） · 長 89（99）');
  await expect(card.locator('.ws-ma-reco-status')).toHaveText('日線至 2026-10-17');
  await expect(card.locator('table')).toContainText('20（70）、★24（69）');
  await expect(card).toContainText('★不一定是分數最高的那條');
  await expect(card.locator('table')).toContainText('+15.3%');
  await expect(card.getByRole('link', { name: /在 ma-selector 查看/ })).toHaveAttribute('href', /streamlit\.app\/\?ticker=2330\.TW$/);
  await page.screenshot({ path: testInfo.outputPath('ma-recommendation-panel.png') });

  await card.getByRole('button', { name: '套用到圖表' }).click();
  await expect(page.getByRole('status').filter({ hasText: '已在日線圖加入 SMA 24 / 38 / 89' })).toBeVisible();
  await expect(page.getByRole('button', { name: '週期 1D', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(card.getByRole('button', { name: '已套用' })).toBeDisabled();

  await card.getByRole('button', { name: '固定為個股參數' }).click();
  const dialog = page.getByRole('dialog', { name: '2330.TW 固定均線參數' });
  await expect(dialog.getByText('目前已保存：20 / 60；儲存後會被取代。')).toBeVisible();
  await expect(dialog.getByRole('textbox', { name: '個股固定均線參數' })).toHaveValue('24, 38, 89');
  await page.screenshot({ path: testInfo.outputPath('ma-recommendation-fix.png') });
  await dialog.getByRole('button', { name: '確認並永久儲存' }).click();
  await expect(dialog).toBeHidden();
  expect(saves).toEqual([expect.objectContaining({ action: 'stocks.save', ticker: '2330.TW', groupId: 'ungrouped', mas: [24, 38, 89] })]);
  await expect(page.getByText('2330.TW 固定均線 24 / 38 / 89 已永久保存到 Supabase（無分組）。')).toBeVisible();
  // The panel stays open across the dialog; Escape and an outside tap close it.
  await expect(card).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(card).toBeHidden();
  await toggle.click();
  await expect(card).toBeVisible();
  await page.getByTestId('header-quote').click();
  await expect(card).toBeHidden();
});

test('a symbol without a recommendation links to the live ma-selector instead', async ({ page }) => {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/ma-selector/**', (route) => route.fulfill({ status: 404, body: 'not found' }));
  await page.goto('./?page=chart&symbol=NVDA&tf=1D');
  const card = page.getByTestId('ma-recommendation');
  await page.getByTestId('stock-stats-toggle').click();
  await expect(card.locator('dd')).toHaveText('—');
  await expect(card.locator('.ws-ma-reco-status')).toContainText('還沒有建議均線');
  await expect(card.getByRole('button')).toHaveCount(0);
  await expect(card.getByRole('link', { name: /即時計算這檔股票/ })).toHaveAttribute('href', /\?ticker=NVDA$/);
});
