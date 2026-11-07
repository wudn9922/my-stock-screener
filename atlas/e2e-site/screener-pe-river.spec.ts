import { expect, test, type Page } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { buildReport, mockSite } from './mocks';

function fixture(market: 'TW' | 'US') {
  const report = buildReport(20);
  const group = (report.groups as { key: string; market: string; items: unknown[] }[]).find(group => group.market === market)!;
  const symbols = Array.from({ length: 5 }, (_, index) => market === 'TW' ? `${6000 + index}.TW` : `RIV${index}`);
  group.items = symbols.map((symbol, index) => ({ symbol, name: `河流測試 ${index}`, status: 'matched', close: 100, changePct: 1,
    maList: [20], maValues: { 20: 90 }, asOf: report.reportDate, metrics: { volumeAvg20: 100000 } }));
  // Slot 4 has no river (absent): it never passes a river condition.
  const percentiles = [5, 25, 50, 80, null];
  const river = { version: 1, market, generatedAt: '2026-11-07T00:00:00Z', priceThrough: report.reportDate, basis: 'test',
    items: Object.fromEntries(symbols.flatMap((symbol, index) => percentiles[index] === null ? [] : [[symbol, [percentiles[index], 0]]])) };
  return { report, group, symbols, river };
}

async function setup(page: Page, data: ReturnType<typeof fixture>, river: unknown = data.river) {
  await mockSite(page, { report: data.report });
  await page.route(`**/atlas/valuation/pe-river-${data.river.market.toLowerCase()}.json`, route =>
    river === null ? route.fulfill({ status: 404, body: '' }) : route.fulfill({ json: river }));
}

for (const market of ['TW', 'US'] as const) {
  test(`${market}: P/E river percentile presets, custom bounds, sharing and phone layout`, async ({ page }, info) => {
    await mockDeviceAccount(page);
    const data = fixture(market);
    await setup(page, data);
    await page.goto(`./?page=screener&group=${data.group.key}`);
    await expect(page.locator('.screener-row')).toHaveCount(5);
    await page.getByRole('button', { name: '篩選條件', exact: true }).click();
    const sheet = page.getByRole('dialog', { name: '篩選條件' });
    await sheet.getByRole('navigation', { name: '篩選分類' }).getByRole('button', { name: '基本面', exact: true }).click();
    const river = sheet.getByRole('region', { name: '本益比河流分位', exact: true });
    await expect(river.getByRole('button', { name: '不限', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await river.getByRole('button', { name: '低於 P30', exact: true }).click();
    await expect(sheet.getByRole('button', { name: '套用（2 檔）' })).toBeEnabled();
    const lower = river.getByRole('textbox', { name: '河流分位下限' });
    const upper = river.getByRole('textbox', { name: '河流分位上限' });
    await expect(upper).toHaveValue('30');
    await lower.fill('40');
    await expect(river.getByRole('alert')).toContainText('下限不可大於上限');
    await expect(sheet.getByRole('button', { name: /^套用/ })).toBeDisabled();
    await upper.fill('90');
    await expect(sheet.getByRole('button', { name: '套用（2 檔）' })).toBeEnabled();
    const original = page.viewportSize()!;
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    }
    await river.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`pe-river-${market.toLowerCase()}-390.png`) });
    await page.setViewportSize(original);
    await sheet.getByRole('button', { name: '套用（2 檔）' }).click();
    expect(new URL(page.url()).searchParams.get('f')).toBe('per:40:90');
    await expect(page.locator('.screener-row')).toHaveCount(2);
    for (const index of [2, 3]) await expect(page.locator('.screener-row', { hasText: data.symbols[index]!.replace(/\.TW$/, '') })).toBeVisible();
    await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('河流分位 P40～P90');
    await page.reload();
    await expect(page.locator('.screener-row')).toHaveCount(2);
  });
}

test('a missing P/E river file never shows an unfiltered list', async ({ page }) => {
  await mockDeviceAccount(page);
  const data = fixture('US');
  await setup(page, data, null);
  await page.goto(`./?page=screener&group=${data.group.key}&f=per::30`);
  await expect(page.getByText('本益比河流資料尚未產生或暫時無法取得').first()).toBeVisible();
  await expect(page.locator('.screener-row')).toHaveCount(0);
});
