import { build } from 'esbuild';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const cache = resolve(import.meta.dirname, '../node_modules/.cache');
await mkdir(cache, { recursive: true });
const work = await mkdtemp(resolve(cache, 'atlas-watchlists-'));
try {
  const outfile = resolve(work, 'refresh.mjs');
  await build({
    entryPoints: [resolve(import.meta.dirname, 'refresh-watchlists.ts')],
    outfile,
    bundle: true,
    platform: 'node',
    format: 'esm',
    packages: 'external',
    define: { 'import.meta.env': '{}', 'import.meta.dirname': JSON.stringify(import.meta.dirname) },
  });
  // Keep dependencies resolved from Atlas, while the bundled source is temporary.
  const entry = await import(pathToFileURL(outfile).href);
  await entry.refreshSavedWatchlists();
} finally {
  await rm(work, { recursive: true, force: true });
}
