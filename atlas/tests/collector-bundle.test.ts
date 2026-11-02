import { resolve } from 'node:path';
import { build } from 'esbuild';
import { expect, it } from 'vitest';

it('bundles the real Node collector without importing browser providers or Vite raw assets', async () => {
  const scripts = resolve(import.meta.dirname, '../scripts');
  const result = await build({
    entryPoints: [resolve(scripts, 'refresh-watchlists.ts')],
    outfile: 'refresh.mjs',
    bundle: true,
    platform: 'node',
    format: 'esm',
    packages: 'external',
    define: { 'import.meta.env': '{}', 'import.meta.dirname': JSON.stringify(scripts) },
    write: false,
    metafile: true,
  });
  expect(result.outputFiles[0]!.text).toContain('refreshSavedWatchlists');
  const inputs = Object.keys(result.metafile.inputs).join('\n');
  expect(inputs).toContain('fundamentals/peFigures.ts');
  expect(inputs).not.toMatch(/WorldAssetProvider|createScreenerMarketProvider|btcUsdtArchive|\?raw/);
});
