import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import { researchFixtureBody } from './research-fixtures';

test('volume ratios lead the centered anomaly controls and volatility values remain large', async ({ page }, info) => {
  await mockSite(page);
  await page.clock.setFixedTime(new Date('2026-10-22T10:00:00Z'));
  await page.route('**/my-stock-screener/report/research/**', route => {
    const path = new URL(route.request().url()).pathname.split('/research/')[1]!;
    try {
      const body = researchFixtureBody(path);
      const range = /^bytes=(\d+)-(\d+)$/.exec(route.request().headers().range ?? '');
      return route.fulfill({ status: range ? 206 : 200, contentType: 'application/json',
        body: range ? body.subarray(Number(range[1]), Number(range[2]) + 1) : body });
    } catch { return route.fulfill({ status: 404 }); }
  });
  await page.goto('./?page=chart&symbol=2330.TW&tf=1D');
  await page.getByTestId('stock-tab-volume').click();
  const panel = page.getByRole('region', { name: '價量', exact: true });
  const ratios = panel.getByRole('region', { name: '上漲／下跌放量與縮量比例', exact: true });
  const anomaly = panel.getByRole('region', { name: '異常量', exact: true });
  await expect(ratios.locator('.research-stats dt')).toHaveText([
    '上漲縮量比例', '下跌放量比例', '上漲放量比例', '下跌縮量比例',
  ]);
  await expect(ratios.getByRole('heading')).toBeVisible();
  await expect(ratios.locator('details[open]')).toHaveCount(0);
  await expect(anomaly.locator('dd')).toContainText('1.2');
  const layout = await panel.evaluate(node => {
    const ratios = node.querySelector('.volume-analysis-ratios')!.getBoundingClientRect();
    const anomaly = node.querySelector('.volume-analysis-anomaly')!.getBoundingClientRect();
    const results = node.querySelector('.volume-analysis-results')!.getBoundingClientRect();
    return { ratioBottom: ratios.bottom, anomalyTop: anomaly.top,
      anomalyCenter: (anomaly.left + anomaly.right) / 2, resultsCenter: (results.left + results.right) / 2,
      overflow: node.scrollWidth - node.clientWidth };
  });
  expect(layout.ratioBottom).toBeLessThanOrEqual(layout.anomalyTop);
  expect(Math.abs(layout.anomalyCenter - layout.resultsCenter)).toBeLessThanOrEqual(1);
  expect(layout.overflow).toBeLessThanOrEqual(0);
  const values = await ratios.locator('dd').allTextContents();
  await anomaly.getByLabel('異常放量門檻').fill('1');
  await expect(anomaly.locator('dd')).toContainText('異常放量');
  await expect(ratios.locator('dd')).toHaveText(values);
  await panel.screenshot({ path: info.outputPath('volume-ratios-first.png') });

  await page.getByTestId('volatility-toggle').click();
  const volatility = page.getByRole('region', { name: '波動率', exact: true });
  await expect(volatility.locator('[data-volatility-value]')).toHaveText([/\d+\.\d{2}%/, /\d+\.\d{2}%/]);
  const cards = await volatility.locator('.ws-volatility-values > div').evaluateAll(nodes => nodes.map(node => {
    const value = node.querySelector('dd')!;
    const rect = node.getBoundingClientRect();
    return { width: rect.width, height: rect.height, font: parseFloat(getComputedStyle(value).fontSize) };
  }));
  expect(cards).toHaveLength(2);
  for (const card of cards) {
    expect(card.font).toBeGreaterThanOrEqual(28);
    expect(card.height).toBeGreaterThanOrEqual(90);
    expect(card.width).toBeGreaterThanOrEqual(120);
  }
  await expect(volatility.locator('details[open]')).toHaveCount(0);
  await volatility.screenshot({ path: info.outputPath('volatility-large-values.png') });
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});
