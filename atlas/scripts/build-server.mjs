import { build } from 'esbuild';
await build({
  entryPoints: ['server/fundamentals.ts'],
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node22',
  packages: 'external',
  outfile: 'dist-server/fundamentals.mjs',
});
