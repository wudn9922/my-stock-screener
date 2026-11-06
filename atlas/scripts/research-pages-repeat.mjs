// Repeat the unmodified smoke journey sequentially; every exit/error remains a failure.
import { spawn } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const count = Number(process.env.RESEARCH_PAGES_RUNS ?? 20);
if (!Number.isInteger(count) || count < 1) throw new Error('RESEARCH_PAGES_RUNS must be a positive integer');
const out = resolve(process.env.RESEARCH_PAGES_OUT ?? 'test-results/research-pages-repeat');
await mkdir(out, { recursive: true });
const runs = [];
for (let run = 1; run <= count; run++) {
  const directory = resolve(out, String(run).padStart(2, '0'));
  // Refuse to mix an older run's result with this run (use a fresh output root).
  await mkdir(directory);
  const startedAt = new Date().toISOString();
  const diagnostics = process.env.RESEARCH_PAGES_DIAGNOSTICS === '1';
  const child = spawn(process.execPath, [
    ...(diagnostics ? ['--import', resolve(import.meta.dirname, 'research-pages-diagnostics.mjs')] : []),
    resolve(import.meta.dirname, 'research-pages-smoke.mjs'),
  ], {
    env: { ...process.env, RESEARCH_PAGES_OUT: directory,
      RESEARCH_PAGES_DIAGNOSTICS_FILE: resolve(directory, 'events.jsonl') },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout.on('data', data => { log += data; });
  child.stderr.on('data', data => { log += data; });
  const exitCode = await new Promise((accept, reject) => {
    child.once('error', reject);
    child.once('exit', accept);
  });
  await writeFile(resolve(directory, 'run.log'), log);
  let result;
  try { result = JSON.parse(await readFile(resolve(directory, 'pages-live-smoke.json'), 'utf8')); }
  catch { /* A crash or assertion failure remains visible via exitCode and run.log. */ }
  const passed = exitCode === 0 && result?.results?.length === 2 && result.results.every(project => project.passed);
  runs.push({ run, startedAt, finishedAt: new Date().toISOString(), exitCode, passed, results: result?.results ?? null });
  await writeFile(resolve(out, 'summary.json'), JSON.stringify({ count, runs }, null, 2) + '\n');
  console.log(JSON.stringify({ run, exitCode, projects: result?.results.map(({ project, passed, errors }) => ({ project, passed, errors })) }));
}
if (runs.some(run => !run.passed)) process.exitCode = 1;
