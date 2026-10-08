import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const projectRoot = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const npmCliPath = process.env.npm_execpath;
const command = npmCliPath ? process.execPath : process.platform === 'win32' ? 'npm.cmd' : 'npm';
const args = npmCliPath ? [npmCliPath, 'run', 'build'] : ['run', 'build'];

const result = spawnSync(command, args, {
  cwd: projectRoot,
  env: { ...process.env, VITE_NATIVE_WRAPPER: '1' },
  shell: process.platform === 'win32' && !npmCliPath,
  stdio: 'inherit',
});

if (result.error) {
  throw result.error;
}

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
