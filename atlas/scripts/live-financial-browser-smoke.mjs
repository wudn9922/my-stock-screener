import { chromium } from 'playwright';
import { expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } }),
    errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://127.0.0.1:4173');
  await expect(page.getByTestId('ohlc-header')).toContainText('O ', { timeout: 30000 });
  const response = page.waitForResponse((r) => r.url().includes('/api/fundamentals?symbol=AAPL'), {
    timeout: 40000,
  });
  await page.getByRole('button', { name: 'Financials', exact: true }).click();
  const res = await response;
  if (!res.ok()) throw new Error(`SEC backend ${res.status()}`);
  const records = await res.json(),
    latest = records.at(-1);
  const value = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(latest.revenue);
  await expect(page.getByTestId('financial-revenue')).toHaveText(value, { timeout: 30000 });
  await page.getByLabel('Audit Revenue', { exact: true }).click();
  await expect(page.getByTestId('financial-panel')).toContainText(
    latest.sourceConcepts.revenue.inputs[0].accession,
  );
  await page.screenshot({ path: 'scratch/live-financial-desktop.png' });
  const report = {
    testedAt: new Date().toISOString(),
    source: 'Live official SEC through production preview backend; no fixture routes',
    symbol: 'AAPL',
    records: records.length,
    fiscalYear: latest.fiscalYear,
    fiscalQuarter: latest.fiscalQuarter,
    periodEnd: latest.periodEnd,
    revenue: latest.revenue,
    renderedRevenue: await page.getByTestId('financial-revenue').textContent(),
    runtimeErrors: errors,
  };
  if (errors.length) throw new Error(errors.join(';'));
  await writeFile('docs/live-financial-browser-smoke.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
