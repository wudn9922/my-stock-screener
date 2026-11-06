// Real HTTP + the actual provider code: no request interception or error filtering.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, webkit, devices } from 'playwright';

const csv = 'Date,Open,High,Low,Close,Volume\n2026-01-02,100,101,99,100,1000\n';
const index = { version: 1, symbol: 'AAPL', market: 'us', path: 'us/AAPL', firstDate: '2026-01-02', lastDate: '2026-01-02',
  years: { 2026: { sha256: createHash('sha256').update(csv).digest('hex'), firstDate: '2026-01-02', lastDate: '2026-01-02', rows: 1, csvBytes: Buffer.byteLength(csv) } } };
const source = process.env.RESEARCH_NAV_SOURCE ?? fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  stdin: { contents: `
    import { KlineArchive } from './src/market-data/KlineArchive';
    import { ArchiveFirstProvider } from './src/market-data/ArchiveFirstProvider';
    window.fallbackCalls = 0;
    const fallback = { id: 'navigation-fixture', supportedTimeframes: ['1D'],
      async getBars() {
        window.fallbackCalls++;
        await fetch('/report/series/index.json');
        return { bars: [], source: 'fallback', session: 'regular', delayed: true, adjusted: true };
      }, async getQuote() { throw new Error('unused'); }
    };
    const archive = new KlineArchive({ baseUrl: location.origin + '/archive/', cache: false });
    const provider = new ArchiveFirstProvider(fallback, archive);
    window.read = async () => {
      try { window.result = (await provider.getBars('AAPL', '1D')).source; }
      catch (error) { window.result = error.name; }
    };
    window.read();
  `, resolveDir: source, sourcefile: 'navigation-fixture.ts', loader: 'ts' },
  bundle: true, write: false, format: 'iife', platform: 'browser', define: { 'import.meta.env': '{}' },
});
let mode, seen, markSeen, held = [], fallbackRequests = 0;
const release = () => { for (const [response, body] of held.splice(0)) response.end(body); };
const server = createServer((request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname;
  if (path === '/bundle.js') {
    response.setHeader('Content-Type', 'text/javascript'); response.end(bundle.outputFiles[0].text); return;
  }
  if (path === '/report/series/index.json') {
    fallbackRequests++; response.end('{"symbols":[]}'); return;
  }
  if (path.startsWith('/archive/')) {
    const body = path.endsWith('.json') ? JSON.stringify(index) : csv;
    if (mode === 'unavailable') { response.writeHead(503); response.end(); return; }
    if ((mode === 'index' && path.includes('/index/')) || (mode === 'body' && path.endsWith('.csv'))) {
      // Hold real bytes, without aborting/mocking a browser request. Navigation
      // itself must cancel it. The body case sends headers before holding bytes.
      if (mode === 'body') {
        response.writeHead(200, { 'Content-Length': Buffer.byteLength(body) });
        response.flushHeaders();
      }
      held.push([response, body]); markSeen(); return;
    }
    response.end(body); return;
  }
  response.setHeader('Content-Type', 'text/html');
  response.end(path === '/next' ? '<html><body>next document</body></html>'
    : '<html><body><button id="activate">activate</button><a href="/next">leave</a><script src="/bundle.js"></script></body></html>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const results = [];
try {
  for (const [project, type, settings] of [
    ['iphone-webkit', webkit, devices['iPhone 13']],
    ['desktop-chromium', chromium, { viewport: { width: 1440, height: 900 } }],
  ]) {
    const browser = await type.launch();
    try {
      for (const scenario of ['index', 'body', 'unavailable', 'cancelled-leave']) {
        mode = scenario === 'cancelled-leave' ? 'index' : scenario;
        seen = new Promise(resolve => { markSeen = resolve; });
        fallbackRequests = 0;
        const context = await browser.newContext({ ...settings, serviceWorkers: 'block' });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        try {
          const bodyResponse = scenario === 'body'
            ? page.waitForResponse(response => response.url().endsWith('.csv')) : undefined;
          await page.goto(base);
          if (scenario === 'unavailable') {
            await page.waitForFunction(() => window.result === 'fallback');
            assert.equal(await page.evaluate(() => window.fallbackCalls), 1);
            assert.equal(fallbackRequests, 1);
          } else {
            let timeout;
            try {
              await Promise.race([seen, new Promise((_, reject) => {
                timeout = setTimeout(() => reject(new Error('Archive request did not reach the real HTTP server')), 10000);
              })]);
            } finally { clearTimeout(timeout); }
            if (scenario === 'body') {
              // Ensure the browser has received headers and is consuming text.
              await bodyResponse;
            }
            if (scenario === 'cancelled-leave') {
              await page.locator('#activate').click();
              await page.evaluate(() => {
                window.preventLeave = event => { event.preventDefault(); event.returnValue = ''; };
                addEventListener('beforeunload', window.preventLeave);
              });
              const dialog = page.waitForEvent('dialog');
              const click = page.evaluate(() => { document.querySelector('a').click(); });
              await (await dialog).dismiss();
              await click;
              await page.waitForFunction(() => window.result === 'AbortError');
              assert.equal(page.url(), base + '/');
              mode = 'ready'; release();
              await page.evaluate(async () => {
                removeEventListener('beforeunload', window.preventLeave);
                await window.read();
              });
              assert.match(await page.evaluate(() => window.result), /GitHub 永久封存/);
            } else {
              await page.goto(base + '/next');
            }
            assert.equal(fallbackRequests, 0);
          }
          assert.deepEqual(errors, []);
          results.push({ project, scenario, passed: true, errors, fallbackRequests });
          console.log(JSON.stringify(results.at(-1)));
        } finally {
          release(); await context.close();
        }
      }
    } finally { await browser.close(); }
  }
} finally {
  release(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
}
