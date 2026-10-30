import { expect, test, type Page } from '@playwright/test';
import { mockDeviceAccount } from './account-fixture';
import { buildReport, mockSite } from './mocks';

function fixture(market: 'TW' | 'US', count = 6) {
  const report = buildReport(20);
  const group = (report.groups as { key: string; market: string; items: unknown[] }[]).find(group => group.market === market)!;
  const symbols = Array.from({ length: count }, (_, index) => market === 'TW' ? `${5000 + index}.TW` : `PE${index}`);
  group.items = symbols.map((symbol, index) => ({ symbol, name: `本益比測試 ${index}`, status: 'matched', close: 100, changePct: 1,
    maList: [20], maValues: { 20: 90 }, asOf: report.reportDate, metrics: { volumeAvg20: index === 1 ? 50000 : 100000 } }));
  const valuation = { version: 1, market, items: Object.fromEntries(symbols.filter((_, index) => index !== 4).map(symbol => {
    const slot = symbols.indexOf(symbol);
    const multiple = slot === 0 ? 10 : slot === 1 ? 20 : slot === 5 || slot === count - 1 ? 15 : 50;
    return [symbol, market === 'TW'
      ? { epsTtm: slot === 3 ? null : 100 / multiple, epsAnnual: 20, exchangePeTtm: slot === 3 ? null : multiple, source: 'TWSE' }
      : { epsTtm: slot === 3 ? -1 : slot === 5 ? null : 100 / multiple, epsAnnual: slot === 5 ? 100 / multiple : 20, source: 'Yahoo Finance' }];
  })) };
  return { report, group, symbols, valuation };
}

async function setup(page: Page, data: ReturnType<typeof fixture>) {
  await mockSite(page, { report: data.report });
  await page.route(`**/atlas/valuation/${data.valuation.market.toLowerCase()}.json`, route => route.fulfill({ json: data.valuation }));
}

for (const market of ['TW', 'US'] as const) {
  test(`${market}: P/E range previews, validates, shares, combines, persists and saves as a watchlist`, async ({ page }, info) => {
    await mockDeviceAccount(page);
    const data = fixture(market);
    await setup(page, data);
    await page.goto(`./?page=screener&group=${data.group.key}`);
    await expect(page.locator('.screener-row')).toHaveCount(6);
    await page.getByRole('button', { name: '篩選條件', exact: true }).click();
    const sheet = page.getByRole('dialog', { name: '篩選條件' });
    await sheet.getByRole('navigation', { name: '篩選分類' }).getByRole('button', { name: '基本面', exact: true }).click();
    const pe = sheet.getByRole('region', { name: '本益比', exact: true });
    await expect(pe.getByRole('button', { name: '不限', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await pe.getByRole('button', { name: '≤20 倍', exact: true }).click();
    const lower = pe.getByRole('textbox', { name: '本益比下限（倍）' });
    const upper = pe.getByRole('textbox', { name: '本益比上限（倍）' });
    await lower.fill('10');
    await expect(sheet.getByRole('button', { name: '套用（3 檔）' })).toBeEnabled();
    await upper.fill('5');
    await expect(pe.getByRole('alert')).toContainText('下限不可大於上限');
    await expect(sheet.getByRole('button', { name: /^套用/ })).toBeDisabled();
    await upper.fill('20');
    const original = page.viewportSize()!;
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    }
    await page.screenshot({ path: info.outputPath(`pe-${market.toLowerCase()}-390.png`) });
    await page.setViewportSize(original);
    await sheet.getByRole('button', { name: '套用（3 檔）' }).click();
    expect(new URL(page.url()).searchParams.get('f')).toBe('pe:10:20');
    await expect(page.locator('.screener-row')).toHaveCount(3);
    for (const index of [0, 1, 5]) await expect(page.locator('.screener-row', { hasText: data.symbols[index]!.replace(/\.TW$/, '') })).toBeVisible();
    await page.reload();
    await expect(page.locator('.screener-row')).toHaveCount(3);
    await page.getByRole('button', { name: /篩選條件/ }).click();
    await expect(lower).toHaveValue('10');
    await expect(upper).toHaveValue('20');
    await lower.fill('15');
    await sheet.getByRole('button', { name: '關閉篩選', exact: true }).click();
    expect(new URL(page.url()).searchParams.get('f')).toBe('pe:10:20');
    await page.getByRole('button', { name: /篩選條件/ }).click();
    await expect(lower).toHaveValue('10');
    await sheet.getByRole('navigation', { name: '篩選分類' }).getByRole('button', { name: /^全部條件/ }).click();
    await sheet.getByRole('region', { name: /20 日平均成交量/ }).getByRole('button', { name: market === 'TW' ? '≥100 張' : '≥100,000 股', exact: true }).click();
    await sheet.getByRole('button', { name: '套用（2 檔）' }).click();
    expect(new URL(page.url()).searchParams.get('f')).toBe('vol:100000;pe:10:20');
    const filteredUrl = page.url();
    await page.getByRole('button', { name: '存成自選清單', exact: true }).click();
    await page.getByRole('textbox', { name: '自選清單名稱' }).fill(`本益比 ${market}`);
    await page.getByRole('button', { name: '儲存並追蹤清單' }).click();
    await expect(page).toHaveURL(/page=watchlist&list=/);
    await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText('本益比 10～20 倍');
    await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText(data.symbols[0]!.replace(/\.TW$/, ''));
    await page.reload();
    await expect(page.getByRole('region', { name: '清單追蹤結果' })).toContainText(data.symbols[5]!.replace(/\.TW$/, ''));
    await page.goto(`./?page=screener&group=${data.group.key}`);
    await expect(page.locator('.screener-row')).toHaveCount(6);
    expect(new URL(page.url()).searchParams.get('f')).toBeNull();
    await page.goto(filteredUrl);
    await expect(page.locator('.screener-row')).toHaveCount(2);
    await page.getByRole('button', { name: /移除條件：本益比/ }).click();
    expect(new URL(page.url()).searchParams.get('f')).toBe('vol:100000');
    await page.getByRole('button', { name: /篩選條件/ }).click();
    await expect(lower).toHaveValue(''); await expect(upper).toHaveValue('');
    await pe.getByRole('button', { name: '≤10 倍', exact: true }).click();
    await sheet.getByRole('button', { name: '重設', exact: true }).click();
    await expect(lower).toHaveValue(''); await expect(upper).toHaveValue('');
    await sheet.getByRole('button', { name: '套用（6 檔）' }).click();
    await expect(page.locator('.screener-row')).toHaveCount(6);
    expect(new URL(page.url()).searchParams.has('f')).toBe(false);
  });
}

test('P/E filters every source row, including a match outside the virtualized viewport', async ({ page }) => {
  const data = fixture('US', 90);
  await setup(page, data);
  await page.goto(`./?page=screener&group=${data.group.key}`);
  await expect(page.locator('.result-count')).toHaveText('90 檔');
  await expect(page.locator('.screener-row', { hasText: 'PE89' })).toHaveCount(0);
  await page.goto(`./?page=screener&group=${data.group.key}&f=pe:10:20`);
  await expect(page.locator('.screener-row', { hasText: 'PE89' })).toBeVisible();
  await expect(page.locator('.result-count')).toHaveText('4 / 90 檔');
});

test('missing P/E file shows an explicit error, never an unfiltered result; removing the condition recovers', async ({ page }) => {
  const data = fixture('US');
  await setup(page, data);
  await page.route('**/atlas/valuation/us.json', route => route.fulfill({ status: 404, body: '' }));
  await page.goto(`./?page=screener&group=${data.group.key}&f=pe::20`);
  await expect(page.getByRole('alert').filter({ hasText: '本益比資料尚未產生或暫時無法取得' })).toBeVisible();
  await expect(page.locator('.screener-row')).toHaveCount(0);
  await page.getByRole('button', { name: /移除條件：本益比/ }).click();
  await expect(page.locator('.screener-row')).toHaveCount(6);
});

test('a delayed US valuation never leaks into Taiwan results after switching markets', async ({ page }) => {
  const us = fixture('US'), tw = fixture('TW');
  (us.report.groups as { key: string; items: unknown[] }[]).find(group => group.key === tw.group.key)!.items = tw.group.items;
  await setup(page, us);
  await page.route('**/atlas/valuation/tw.json', route => route.fulfill({ json: tw.valuation }));
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/atlas/valuation/us.json', async route => { await pending; await route.fulfill({ json: us.valuation }); });
  try {
    await page.goto(`./?page=screener&group=${us.group.key}&f=pe::20`);
    await expect(page.getByText('載入本益比資料…', { exact: true })).toBeVisible();
    await expect(page.locator('.screener-row')).toHaveCount(0);
    await page.locator('.chip-row').first().getByRole('button', { name: /^全市場潛伏/ }).click();
    await expect(page.locator('.screener-row')).toHaveCount(6);
    await page.getByRole('button', { name: /篩選條件/ }).click();
    const sheet = page.getByRole('dialog', { name: '篩選條件' });
    await sheet.getByRole('region', { name: '本益比', exact: true }).getByRole('button', { name: '≤10 倍', exact: true }).click();
    await sheet.getByRole('button', { name: '套用（1 檔）' }).click();
    const response = page.waitForResponse('**/atlas/valuation/us.json');
    release(); await response;
    await expect(page.locator('.screener-row')).toHaveCount(1);
    await expect(page.locator('.screener-row')).toContainText('5000');
    await expect(page.locator('.screener-row .row-pe')).toContainText('10.0');
    expect(new URL(page.url()).searchParams.get('f')).toBe('pe::10');
  } finally { release(); }
});
