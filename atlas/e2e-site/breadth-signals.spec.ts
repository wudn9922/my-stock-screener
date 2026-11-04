import { expect, test } from '@playwright/test';
import { mockSite } from './mocks';
import {
  breadthFixture,
  breadthSignalsFixture,
  signalIndex,
} from '../tests/fixtures/breadth-signals';

async function setup(
  page: import('@playwright/test').Page,
  signals: unknown = breadthSignalsFixture(),
  status = 200,
) {
  await mockSite(page);
  await page.route('**/report/breadth.json', (route) =>
    route.fulfill({ json: breadthFixture() }),
  );
  await page.route('**/report/breadth-signals.json', (route) =>
    route.fulfill({
      status,
      ...(status === 200 ? { json: signals } : { body: '' }),
    }),
  );
}

test('US calibrated plans, selectable history markers and folded explanations fit a narrow card', async ({
  page,
}, info) => {
  const fixture = breadthSignalsFixture('new');
  fixture.markets.us.lowSignal.indices.push({
    ...signalIndex('watch', '^RUT', '羅素 2000'),
    markers: ['2026-10-29'],
    stats: { ...signalIndex().stats, hitRate: 0.57 },
  });
  await setup(page, fixture);
  await page.goto('./?page=markets&market=us');
  const card = page.getByRole('region', { name: '美股市場寬度', exact: true });
  await expect(card.locator('.breadth-pct')).toHaveText(['18.0%', '42.0%']);
  await expect(card.locator('.breadth-bounds').first()).toHaveText('下限 P10 22.0% · 上限 P90 76.0%');
  await expect(
    card.getByText('歷史第 6.5 百分位', { exact: false }),
  ).toBeVisible();
  await expect(card.getByText('新訊號', { exact: true })).toBeVisible();
  await expect(card.getByText('進場參考', { exact: true })).toBeVisible();
  await expect(card.getByText(/下一個交易日開盤進場/)).toBeVisible();
  await expect(card.getByText(/指數收盤跌破 6,015 即觸發/)).toBeVisible();
  await expect(card.getByText('參考性較低', { exact: true })).toBeVisible();
  await expect(card.locator('.breadth-threshold')).toHaveCount(4);
  await expect(card.locator('[data-signal-date="2026-10-27"]')).toHaveCount(1);
  await card.getByLabel('歷史訊號指數').selectOption('^RUT');
  await expect(card.locator('[data-signal-date="2026-10-27"]')).toHaveCount(0);
  await expect(card.locator('[data-signal-date="2026-10-29"]')).toHaveCount(1);
  const help = card
    .locator('details')
    .filter({ has: page.locator('summary', { hasText: '樣本與歷史說明' }) });
  await expect(help).not.toHaveAttribute('open', '');
  await help.locator('summary').click();
  await expect(help).toContainText('命中率＝進場後先碰到停利');
  await expect(help).toContainText('2004-01-02 至 2026-10-30');
  await expect(help).toContainText('並非投資建議');
  await help.locator('summary').click();
  for (const control of await card.locator('summary, select').all())
    expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(
    await card.evaluate((el) => el.scrollWidth - el.clientWidth),
  ).toBeLessThanOrEqual(0);
  expect(
    await page
      .locator('#main')
      .evaluate((el) => el.scrollWidth - el.clientWidth),
  ).toBeLessThanOrEqual(0);
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: info.outputPath('breadth-us-calibrated.png') });
});

test('TW keeps numbers and history with no calibrated labels or trade plans', async ({
  page,
}, info) => {
  await setup(page);
  await page.goto('./?page=markets&market=tw');
  const card = page.getByRole('region', { name: '台股市場寬度', exact: true });
  await expect(card.locator('.breadth-pct')).toHaveText(['18.0%', '42.0%']);
  await expect(card.getByRole('img', { name: /比例走勢/ })).toBeVisible();
  await expect(card.locator('.breadth-tag')).toHaveCount(0);
  await expect(
    card.getByText('超買超賣訊號不適用', { exact: true }),
  ).toBeVisible();
  await card.getByText('超買超賣訊號不適用', { exact: true }).click();
  await expect(card.getByText(/台股回測未勝過隨便進場/)).toBeVisible();
  await expect(
    card.locator(
      '.breadth-low-signals, .breadth-marker-select, .breadth-threshold',
    ),
  ).toHaveCount(0);
  expect(
    await card.evaluate((el) => el.scrollWidth - el.clientWidth),
  ).toBeLessThanOrEqual(0);
  await card.screenshot({
    path: info.outputPath('breadth-tw-not-applicable.png'),
  });
});

for (const mode of ['missing', 'invalid', 'stale'] as const) {
  test(`${mode} optional signals leave existing breadth numbers and chart available`, async ({
    page,
  }) => {
    const fixture = breadthSignalsFixture();
    if (mode === 'stale') fixture.markets.us.asOf = '2026-10-29';
    await setup(
      page,
      mode === 'invalid'
        ? { version: 1, markets: { us: { readings: 'bad' } } }
        : fixture,
      mode === 'missing' ? 404 : 200,
    );
    await page.goto('./?page=markets&market=us');
    const card = page.getByRole('region', {
      name: '美股市場寬度',
      exact: true,
    });
    await expect(card.locator('.breadth-pct')).toHaveText(['18.0%', '42.0%']);
    await expect(card.getByRole('img', { name: /比例走勢/ })).toBeVisible();
    await expect(
      card.locator(
        '.breadth-tag, .breadth-low-signals, .breadth-marker-select',
      ),
    ).toHaveCount(0);
    await expect(card.getByText(/目前無訊號/)).toHaveCount(0);
  });
}

test('US no-signal section folds to a single line and overbought wording stays conservative', async ({
  page,
}) => {
  const fixture = breadthSignalsFixture('none');
  fixture.markets.us.overbought.active = true;
  fixture.markets.us.readings.ma20.zone = 'extreme_overbought';
  await setup(page, fixture);
  await page.goto('./?page=markets&market=us');
  const card = page.getByRole('region', { name: '美股市場寬度', exact: true });
  await expect(
    card.getByText('抓低點訊號 · 目前無訊號', { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText('命中率 66%（隨便進場 48%） · 70 筆', { exact: true }),
  ).toBeHidden();
  await card.getByText('抓低點訊號 · 目前無訊號', { exact: true }).click();
  await expect(
    card.getByText('命中率 66%（隨便進場 48%） · 70 筆', { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText('極端超買（趨勢強）', { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText('超買不是賣出訊號。', { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText('超買後先跌 40% vs 平常 41%', { exact: true }),
  ).toBeVisible();
});
