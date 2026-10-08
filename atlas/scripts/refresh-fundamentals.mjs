import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

await mkdir('node_modules/.cache/atlas-fundamentals', { recursive: true });
const outfile = resolve('node_modules/.cache/atlas-fundamentals/refresh-fundamentals-bundle.mjs');
await build({
  entryPoints: ['scripts/refresh-fundamentals.ts'],
  outfile,
  bundle: true,
  platform: 'node',
  format: 'esm',
  packages: 'external',
});
const { refreshFundamentals } = await import(pathToFileURL(outfile).href);
const summary = await refreshFundamentals({ requested: process.argv.slice(2) });
console.log(JSON.stringify(summary, null, 2));
