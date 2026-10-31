import { expect, test, type Page } from '@playwright/test';
import { mockSite } from './mocks';
import { accessEnvelope } from './account-fixture';

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
  await page.route('**/functions/v1/chart-drawings', route => route.fulfill({ json: { ok: true, records: [] } }));
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
  const stocks: { id: number; ticker: string; group_id: string; ma1: number; ma2: number; ma3: number; ma4: number }[] = [];
  await page.route('**/functions/v1/user-settings', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    expect(body.idToken).toBe('test-token');
    if (body.action === 'access.get') return route.fulfill({ json: accessEnvelope(undefined, 'cloud') });
    if (body.action === 'stocks.save') {
      saves.push(body);
      const mas = body.mas as number[];
      const stock = { id: 7, ticker: String(body.ticker), group_id: String(body.groupId), ma1: mas[0] ?? 0, ma2: mas[1] ?? 0, ma3: mas[2] ?? 0, ma4: mas[3] ?? 0 };
      stocks.splice(0, stocks.length, stock);
    }
    const data =
      body.action === 'stocks.list'
        ? stocks
        : body.action === 'stocks.save'
          ? [{ id: 7 }]
          : [];
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, data }) });
  });
  return { saves, stocks };
}

test('chart shows the monthly recommendation, applies it on the daily chart and saves it as a fixed parameter', async ({ page }, testInfo) => {
  await mockSite(page);
  const { saves, stocks } = await mockLine(page);
  await page.route('**/my-stock-screener/report/ma-selector/2330.TW.json', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(recommendation) }),
  );
  await page.goto('./?page=chart&symbol=2330.TW&tf=1W');
  const card = page.getByTestId('ma-recommendation');
  const toggle = page.getByTestId('ma-recommendation-toggle');
  // Recommendations and volatility share the stock view control group.
  // Hidden also matches an element not yet mounted; wait for the lazy chart header before measuring it.
  await expect(toggle).toBeVisible();
  await expect(page.getByTestId('valuation')).toBeVisible();
  await expect(card).toBeHidden();
  await expect(toggle).toBeVisible();
  const row = await page.evaluate(() => {
    const tools = document.querySelector('[data-testid="ma-recommendation-toggle"]')!.getBoundingClientRect();
    const tabs = document.querySelector('[data-testid="stock-tab-research"]')!.getBoundingClientRect();
    return {
      tops: ['volatility-toggle', 'ma-recommendation-toggle', 'stock-tab-volume']
        .map(id => document.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect().top),
      tabsOverlap: Math.max(0, Math.min(tools.right, tabs.right) - Math.max(tools.left, tabs.left)) *
        Math.max(0, Math.min(tools.bottom, tabs.bottom) - Math.max(tools.top, tabs.top)),
      overflow: document.scrollingElement!.scrollWidth - innerWidth,
    };
  });
  expect(Math.max(...row.tops) - Math.min(...row.tops)).toBeLessThanOrEqual(12);
  expect(row.tabsOverlap).toBe(0);
  expect(row.overflow).toBeLessThanOrEqual(0);

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(card.locator('dd')).toHaveText('短 24（69） · 中 38（60） · 長 89（99）');
  await expect(card.locator('.ws-ma-reco-status')).toHaveText('日線至 2026-10-17');
  await expect(card.getByRole('button', { name: '套用 SMA 20', exact: true })).toHaveText('20（70）');
  await expect(card.getByRole('button', { name: '套用 SMA 24', exact: true })).toHaveText('★24（69）');
  const explanation = card.locator('.help-disclosure');
  await expect(explanation.locator('.help-disclosure-body')).toBeHidden();
  await explanation.locator('summary').click();
  await expect(explanation.locator('.help-disclosure-body')).toBeVisible();
  await expect(explanation).toContainText('★不一定是分數最高的那條');
  await expect(card.locator('table')).toContainText('+15.3%');
  await expect(card.getByRole('link', { name: /在 ma-selector 查看/ })).toHaveAttribute('href', /streamlit\.app\/\?ticker=2330\.TW$/);
  await page.screenshot({ path: testInfo.outputPath('ma-recommendation-panel.png') });

  await expect(card.getByRole('button', { name: '固定為個股參數' })).toBeDisabled();
  // A non-star candidate can be applied alone; fixing starts from applied MAs, not the three recommendations.
  await card.getByRole('button', { name: '套用 SMA 20', exact: true }).click();
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 20']);
  const applied20 = card.getByRole('button', { name: '取消套用 SMA 20', exact: true });
  await expect(applied20).toBeEnabled();
  await expect(applied20).toHaveAttribute('aria-pressed', 'true');
  await card.getByRole('button', { name: '固定為個股參數' }).click();
  const onlyApplied = page.getByRole('dialog', { name: '2330.TW 固定均線參數' });
  await expect(onlyApplied.getByRole('textbox', { name: '個股固定均線參數' })).toHaveValue('20');
  await onlyApplied.getByRole('button', { name: '取消編輯' }).click();
  await applied20.click();
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveCount(0);
  await expect(card.getByRole('button', { name: '套用 SMA 20', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await expect(card.getByRole('button', { name: '固定為個股參數' })).toBeDisabled();
  await card.getByRole('button', { name: '套用 SMA 20', exact: true }).click();
  await card.getByRole('button', { name: '套用全部建議' }).click();
  await expect(page.getByRole('status').filter({ hasText: '已在日線圖加入 SMA 24 / 38 / 89' })).toBeVisible();
  // Applying a candidate switches the actual chart to 1D.
  await expect(page.locator('.timeframe-buttons button[aria-label="週期 1D"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(card.getByRole('button', { name: '建議已套用', exact: true })).toBeDisabled();
  // Starred candidates also toggle independently of the other applied lines.
  await card.getByRole('button', { name: '取消套用 SMA 24', exact: true }).click();
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveText(['SMA 20', 'SMA 38', 'SMA 89']);
  await expect(card.getByRole('button', { name: '取消套用 SMA 20', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await card.getByRole('button', { name: '套用 SMA 24', exact: true }).click();

  stocks.push({ id: 7, ticker: '2330.TW', group_id: 'ungrouped', ma1: 20, ma2: 60, ma3: 0, ma4: 0 });
  await card.getByRole('button', { name: '固定為個股參數' }).click();
  const dialog = page.getByRole('dialog', { name: '2330.TW 固定均線參數' });
  await expect(dialog.getByRole('status')).toContainText('目前已保存：20 / 60；保留無分組');
  await expect(dialog.getByRole('textbox', { name: '個股固定均線參數' })).toHaveValue('20, 60');
  await dialog.getByRole('button', { name: /改用目前圖表的均線/ }).click();
  await expect(dialog.getByRole('textbox', { name: '個股固定均線參數' })).toHaveValue('20, 24, 38, 89');
  await page.screenshot({ path: testInfo.outputPath('ma-recommendation-fix.png') });
  await dialog.getByRole('button', { name: '確認並儲存到雲端' }).click();
  await expect(dialog).toBeHidden();
  expect(saves).toEqual([expect.objectContaining({ action: 'stocks.save', ticker: '2330.TW', groupId: 'ungrouped', mas: [20, 24, 38, 89] })]);
  await expect(page.getByText('2330.TW 固定均線 20 / 24 / 38 / 89已儲存到你的 Supabase 帳號。')).toBeVisible();
  // The recommendation menu stays open across the dialog; a sibling control dismisses it.
  await expect(card).toBeVisible();
  await page.getByTestId('stock-tab-chart').click();
  await expect(card).toBeHidden();
  await expect(page.getByTestId('chart')).toBeVisible();
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveCount(4);
  await toggle.click();
  await expect(card).toBeVisible();
});

test('a symbol without a recommendation links to the live ma-selector instead', async ({ page }) => {
  await mockSite(page);
  await page.route('**/my-stock-screener/report/ma-selector/**', (route) => route.fulfill({ status: 404, body: 'not found' }));
  await page.goto('./?page=chart&symbol=NVDA&tf=1D');
  const card = page.getByTestId('ma-recommendation');
  await page.getByTestId('ma-recommendation-toggle').click();
  await expect(card.locator('dd')).toHaveText('—');
  await expect(card.locator('.ws-ma-reco-status')).toContainText('還沒有建議均線');
  await expect(card.getByRole('button', { name: '固定為個股參數' })).toBeDisabled();
  await expect(card.getByRole('link', { name: /即時計算這檔股票/ })).toHaveAttribute('href', /\?ticker=NVDA$/);
});
