import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

await mkdir('node_modules/.cache/atlas-symbols', { recursive: true });
const outfile = resolve('node_modules/.cache/atlas-symbols/refresh-symbols-bundle.mjs');
await build({ entryPoints: ['scripts/refresh-symbols.ts'], outfile, bundle: true, platform: 'node', format: 'esm', packages: 'external' });
await import(pathToFileURL(outfile).href);
