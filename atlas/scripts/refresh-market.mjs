import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
await mkdir('node_modules/.cache/atlas-market', { recursive: true });
const outfile = resolve('node_modules/.cache/atlas-market/refresh-market-bundle.mjs');
await build({ entryPoints: ['scripts/refresh-market.ts'], outfile, bundle: true, platform: 'node', format: 'esm', packages: 'external' });
await import(pathToFileURL(outfile).href);
