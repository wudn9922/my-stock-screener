import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

await mkdir('node_modules/.cache/atlas-fundamentals', { recursive: true });
const outfile = resolve('node_modules/.cache/atlas-fundamentals/build-fundamentals-bundle.mjs');
await build({
  entryPoints: ['scripts/build-fundamentals.ts'],
  outfile,
  bundle: true,
  platform: 'node',
  format: 'esm',
  packages: 'external',
});
const { buildFundamentals } = await import(pathToFileURL(outfile).href);
const maxAgeHours = Number(process.env.FUNDAMENTALS_MAX_AGE_HOURS);
const manifest = await buildFundamentals({
  ...(process.argv.length > 2 ? { symbols: process.argv.slice(2) } : {}),
  ...(Number.isFinite(maxAgeHours) && maxAgeHours >= 0 ? { maxAgeSeconds: maxAgeHours * 3600 } : {}),
});
const counts = {};
for (const entry of Object.values(manifest.symbols)) counts[entry.status] = (counts[entry.status] ?? 0) + 1;
console.log(`fundamentals: ${JSON.stringify(counts)}`);
