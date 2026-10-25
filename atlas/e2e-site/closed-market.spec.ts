import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

// Genuine retained scan rows, validated against local OHLCV by the recovery CLI.
const saved = JSON.parse(readFileSync(resolve(import.meta.dirname, '../tests/fixtures/closed-market/snapshot.json'), 'utf8'));

for (const generatedDay of ['2026-10-25', '2026-11-04']) {
  test(`closure report ${generatedDay} retains breadth, candidates and observation-date filters`, async ({ page }, info) => {
    await page.clock.setFixedTime(new Date(`${generatedDay}T12:00:00Z`));
    const report = buildReport(3);
    report.reportDate = generatedDay;
    report.generatedAt = `${generatedDay}T09:31:26Z`;
    report.groups = (report.groups as { key: string }[]).map(group => group.key === 'tw_all' ? saved.group : group);
    await mockSite(page, { report });
    await page.route('**/my-stock-screener/report/breadth.json', route => route.fulfill({
      contentType: 'application/json', body: JSON.stringify(saved.breadth),
    }));
    await page.goto('./?page=markets&market=tw');
    const breadth = page.getByRole('region', { name: '台股市場寬度', exact: true });
    await expect(breadth.locator('.breadth-asof')).toContainText('2026/10/22');
    await expect(breadth.locator('.breadth-pct')).toHaveText(['25.4%', '37.0%']);
    await expect(breadth.locator('.breadth-row-foot').first()).toHaveText('494 / 1,947 檔');
    await breadth.screenshot({ path: info.outputPath('closed-market-breadth.png') });

    await page.goto('./?page=screener&group=tw_all');
    await expect(page.locator('.result-count')).toHaveText('3 檔');
    await expect(page.locator('.screener-meta')).toContainText('資料日 2026/10/22');
    await expect(page.locator('.screener-row .row-id b')).toHaveText(['2492', '1301', '3673']);
    // Average volume and prior-session ratios remain usable after a weekend or
    // long closure; neither condition is evaluated against the wall-clock date.
    await page.goto('./?page=screener&group=tw_all&f=vol:20000000;vr:1.1');
    await expect(page.locator('.result-count')).toHaveText('1 / 3 檔');
    await expect(page.locator('.screener-row .row-id b')).toHaveText(['2492']);
    await page.reload();
    await expect(page.locator('.result-count')).toHaveText('1 / 3 檔');
    await expect(page.getByRole('group', { name: '已套用的篩選條件' })).toContainText('20 日均量');
    await page.screenshot({ path: info.outputPath('closed-market-filters.png') });
  });
}
