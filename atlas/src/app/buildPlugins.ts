import type { Plugin } from 'vite';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
/** Make the worker change with every hashed build so installed PWAs discover new shells. */
export function versionedServiceWorker(): Plugin {
  let outDir = 'dist';
  let base = '/';
  return {
    name: 'atlas-versioned-service-worker',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir;
      base = config.base;
    },
    async closeBundle() {
      const indexPath = resolve(outDir, 'index.html');
      const index = (await readFile(indexPath, 'utf8')).replace(/\s*<meta name="atlas-build" content="[a-f0-9]+"\s*\/?>(?:\r?\n)?/g, '');
      const path = resolve(outDir, 'sw.js');
      const source = await readFile(path, 'utf8');
      const revision = createHash('sha256')
        .update(index + source)
        .digest('hex')
        .slice(0, 12);
      await writeFile(path, source.replace('atlas-shell-v1', `atlas-shell-v1-${revision}`));
      await writeFile(indexPath, index.replace('</head>', `    <meta name="atlas-build" content="${revision}" />\n  </head>`));
      // Public deployment metadata only: never contains account/market data.
      await writeFile(resolve(outDir, 'app-version.json'), JSON.stringify({ app: 'atlas', version: revision, scope: base }) + '\n');
    },
  };
}
