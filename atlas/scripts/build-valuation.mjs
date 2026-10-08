// Bundles scripts/build-valuation.ts with esbuild (node_modules/.cache) and runs it; see that file for options.
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

await mkdir('node_modules/.cache/atlas-data', { recursive: true });
const outfile = resolve('node_modules/.cache/atlas-data/build-valuation-bundle.mjs');
await build({ entryPoints: ['scripts/build-valuation.ts'], outfile, bundle: true, platform: 'node', format: 'esm', packages: 'external', logLevel: 'warning' });
await import(pathToFileURL(outfile).href);
