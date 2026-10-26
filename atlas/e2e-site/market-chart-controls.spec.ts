import { expect, test, type Locator } from '@playwright/test';
import { buildReport, mockSite } from './mocks';

async function paintedSeries(chart: Locator) {
  return chart.locator('canvas').evaluateAll(canvases => {
    const counts = { firstMa: 0, secondMa: 0, volume: 0 };
    const near = (a: number, b: number) => Math.abs(a - b) <= 5;
    for (const element of canvases) {
      const canvas = element as HTMLCanvasElement;
      const context = canvas.getContext('2d');
      if (!context || !canvas.width || !canvas.height) continue;
      const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
      for (let pixel = 0; pixel < data.length; pixel += 4) {
        const [r, g, b, alpha] = data.subarray(pixel, pixel + 4);
        if (near(r, 240) && near(g, 179) && near(b, 91) && alpha > 240) counts.firstMa++;
        if (near(r, 142) && near(g, 153) && near(b, 243) && alpha > 240) counts.secondMa++;
        // Histograms use translucent up/down fills; candle bodies are opaque.
        if (alpha >= 145 && alpha <= 160 && (
          (near(r, 242) && near(g, 54) && near(b, 69)) ||
          (near(r, 34) && near(g, 171) && near(b, 148))
        )) counts.volume++;
      }
    }
    return counts;
  });
}

test('market chart paints configured long MAs and volume, zooms independently and opens the workspace explicitly', async ({ page }, info) => {
  const report = buildReport(3);
  const market = (report.markets as { key: string; indices: { maList: number[] }[] }[]).find(entry => entry.key === 'tw')!;
  // Both periods exceed the 130-bar initial viewport; rendering either requires
  // calculating with source history before that viewport.
  market.indices[0]!.maList = [200, 250];
  await mockSite(page, { report });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('./?page=markets&market=tw');
  const card = page.getByRole('article', { name: '台灣加權指數', exact: true });
  const chart = card.locator('.mini-chart');
  await expect(chart).toHaveAttribute('data-status', 'ready');
  await expect(card.locator('.ma-chip')).toContainText(['MA200', 'MA250']);
  const count = chart.getByLabel('顯示 K 線根數');
  await expect(count).toHaveText('130');
  await expect.poll(async () => (await paintedSeries(chart)).firstMa).toBeGreaterThan(20);
  await expect.poll(async () => (await paintedSeries(chart)).secondMa).toBeGreaterThan(20);
  await expect.poll(async () => (await paintedSeries(chart)).volume).toBeGreaterThan(20);
  await chart.getByRole('button', { name: '放大圖表', exact: true }).click();
  await expect(count).toHaveText('87');
  await chart.getByRole('button', { name: '縮小圖表', exact: true }).click();
  await expect(count).toHaveText('131');
  await chart.getByRole('button', { name: '重設', exact: true }).click();
  await expect(count).toHaveText('130');
  await expect(page).toHaveURL(/page=markets&market=tw/);
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await card.screenshot({ path: info.outputPath('market-configured-ma-volume.png') });
  await card.getByRole('link', { name: '台灣加權指數，開啟完整圖表', exact: true }).click();
  await expect(page).toHaveURL(/page=chart&symbol=\^TWII&tf=1D/);
  await expect(page.getByTestId('active-symbol')).toHaveText('^TWII');
  await expect(page.locator('.indicator-chip .indicator-name')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('an unconfigured market chart has volume without default MAs and unavailable data keeps controls disabled', async ({ page }) => {
  const report = buildReport(3);
  const market = (report.markets as { key: string; indices: { maList: number[] }[] }[]).find(entry => entry.key === 'tw')!;
  market.indices[0]!.maList = [];
  await mockSite(page, { report, failingSymbols: ['^TWOII'] });
  await page.goto('./?page=markets&market=tw');
  const card = page.getByRole('article', { name: '台灣加權指數', exact: true });
  const chart = card.locator('.mini-chart');
  await expect(chart).toHaveAttribute('data-status', 'ready');
  await expect(card.getByText('未設定均線', { exact: true })).toBeVisible();
  await expect(card.locator('.ma-chip')).toHaveCount(0);
  await expect.poll(async () => (await paintedSeries(chart)).volume).toBeGreaterThan(20);
  expect((await paintedSeries(chart)).firstMa).toBe(0);
  expect((await paintedSeries(chart)).secondMa).toBe(0);
  const missing = page.getByRole('article', { name: '台灣櫃買指數(OTC)', exact: true }).locator('.mini-chart');
  await missing.scrollIntoViewIfNeeded();
  await expect(missing).toHaveAttribute('data-status', 'error');
  await expect(missing.getByText('暫無圖表資料')).toBeVisible();
  await expect(missing.getByRole('button', { name: '放大圖表', exact: true })).toBeDisabled();
});
