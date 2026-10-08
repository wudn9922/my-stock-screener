// Builds the screener-site bundle into test-results/ and serves it under /my-stock-screener/atlas/.
// Report, directory, valuation and market data are mocked per test with page.route().
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const out = resolve(root, 'test-results/site-dist');
const base = '/my-stock-screener/atlas/';
execFileSync(process.execPath, [resolve(root, 'node_modules/vite/bin/vite.js'), 'build', '--outDir', out, '--emptyOutDir'], {
  cwd: root,
  stdio: 'inherit',
  env: {
    ...process.env,
    VITE_STATIC_HOSTING: '1',
    VITE_PUBLIC_BASE: base,
    VITE_MARKET_PROXY_URL: 'https://edge.test/functions/v1/market-chart',
  },
});
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.ttf': 'font/ttf', '.webmanifest': 'application/manifest+json' };
const port = Number(process.env.SITE_PORT || 4177);
createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url ?? '/', 'http://x').pathname);
  let file = null;
  if (path.startsWith(base)) {
    file = normalize(join(out, path.slice(base.length) || 'index.html'));
    if (!file.startsWith(out)) file = null;
    else
      try {
        if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
      } catch {
        file = null;
      }
  }
  try {
    if (!file) throw new Error('not found');
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('not found');
  }
}).listen(port, '127.0.0.1', () => console.log(`site test server on ${port}`));
