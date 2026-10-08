import { execFileSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const env = {
  ...process.env,
  VITE_STATIC_HOSTING: '1',
  VITE_PUBLIC_BASE: process.env.VITE_PUBLIC_BASE || '/lightweight-drawing-lab/',
};

function runNodeScript(script, args = []) {
  execFileSync(process.execPath, [resolve(projectRoot, 'node_modules', script), ...args], {
    cwd: projectRoot,
    env,
    stdio: 'inherit',
  });
}

runNodeScript('typescript/bin/tsc', ['-b']);
runNodeScript('vite/bin/vite.js', ['build', '--outDir', 'dist-pages']);
