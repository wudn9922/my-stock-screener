import type { Plugin } from 'vite';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
/** Make the worker change with every hashed build so installed PWAs discover new shells. */
export function versionedServiceWorker(): Plugin {
  let outDir = 'dist';
  return {
    name: 'atlas-versioned-service-worker',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir;
    },
    async closeBundle() {
      const index = await readFile(resolve(outDir, 'index.html'), 'utf8');
      const path = resolve(outDir, 'sw.js');
      const source = await readFile(path, 'utf8');
      const revision = createHash('sha256')
        .update(index + source)
        .digest('hex')
        .slice(0, 12);
      await writeFile(path, source.replace('atlas-shell-v1', `atlas-shell-v1-${revision}`));
    },
  };
}
