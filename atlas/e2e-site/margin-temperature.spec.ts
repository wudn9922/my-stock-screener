import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';

// Immutable supplied golden fixture: scheduled updates never make this UI test stale.
const golden = readFileSync(resolve(import.meta.dirname, '../../tests/fixtures/margin_temperature/gauge_golden.csv'), 'utf8').trim().split(/\r?\n/);
const cols = golden[0]!.split(',');
const field = (parts: string[], name: string) => parts[cols.indexOf(name)]!;
const rows = golden.slice(1).map(line => {
  const parts = line.split(',');
  const pct = field(parts, 'pct') === '' ? null : Number(field(parts, 'pct'));
  const mdd = field(parts, 'mdd') === '' ? null : Number(field(parts, 'mdd'));
  return {
    date: field(parts, 'date'), close: Number(field(parts, 'close')), pct, mdd,
    hot: pct !== null && pct >= 90, cold: mdd !== null && mdd <= -30,
  };
});
const latest = rows.at(-1)!;
const history = { version: 1, asOf: latest.date, provisional: true, scope: 'TWSE listed', latest, rows };

test('Taiwan margin temperature displays exact latest values, history and phone-safe layout', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await mockSite(page);
  await page.route('**/my-stock-screener/report/margin-temperature.json', route => route.fulfill({ json: history }));
  await page.goto('./?page=markets&market=tw');

  const card = page.getByTestId('margin-temperature-card');
  await expect(card).toBeVisible();
  await expect(card.getByText('85.2%', { exact: true })).toBeVisible();
  await expect(card.getByText('-17.5%', { exact: true })).toBeVisible();
  await expect(card.locator('.mt-status-tags')).toContainText('中性');
  await expect(card).toContainText('資料日期 2026-11-12');
  await expect(card).toContainText('本週暫計');
  await expect(card).toContainText('僅描述融資水位偏熱／偏冷，非買賣訊號。');
  await expect(card.locator('.mt-gauge-track')).toHaveCount(2);
  await expect(card.locator('.mt-price-chart')).toBeVisible();
  await expect(card.locator('.mt-chart-legend').first()).toContainText('過熱週');
  await expect(card.locator('.mt-chart-legend')).toContainText('過冷週');
  await card.getByRole('combobox', { name: '融資溫度歷史區間' }).selectOption('3y');
  await expect(card.locator('.mt-price-chart')).toBeVisible();
  await card.getByText('表格檢視', { exact: false }).click();
  await expect(card.locator('tbody tr')).toHaveCount(1022);
  expect(await page.locator('#main').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
  if (info.project.name === 'iphone-webkit') {
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('margin-temperature-390.png') });
  }
  expect(errors).toEqual([]);
});
