// Optional diagnostic: replay the CI cold-route layout change during a native
// pointer press. This is not part of the ordinary legacy-import spec's inputs.
import { execFileSync, spawn } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const stamp = Date.now();
const generated = resolve(root, `test-results/legacy-import-race-source-${stamp}`);
const out = resolve(process.env.LEGACY_IMPORT_RACE_OUT ?? `${root}/test-results/legacy-import-race-${stamp}`);
const ref = process.env.LEGACY_IMPORT_SPEC_REF;
const source = ref
  ? execFileSync('git', ['show', `${ref}:atlas/e2e-site/legacy-import.spec.ts`], { cwd: root, encoding: 'utf8' })
  : await readFile(resolve(root, 'e2e-site/legacy-import.spec.ts'), 'utf8');
await mkdir(generated, { recursive: true });
await mkdir(out, { recursive: true });
const hook = `
test.beforeEach(async ({ page }) => {
  let released = false;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = () => { released = true; resolve(); }; });
  await page.route('**/assets/ConsolePage-*.js', async route => {
    const response = await route.fetch();
    // A readiness assertion must also be able to finish without a pointer press.
    const timer = setTimeout(release, 5000);
    await gate;
    clearTimeout(timer);
    await route.fulfill({ response });
  });
  await page.addInitScript(() => {
    for (const type of ['pointerdown', 'pointerup', 'click']) document.addEventListener(type, event => {
      const target = event.target as HTMLElement;
      console.debug('__LEGACY_INPUT__' + JSON.stringify({ type, trusted: event.isTrusted,
        target: target.tagName, testid: target.getAttribute('data-testid'), text: target.textContent?.slice(0, 80),
        consoleReady: !!document.querySelector('.parameter-console'),
        cardBusy: document.querySelector('[data-testid="legacy-import-card"]')?.getAttribute('aria-busy') }));
    }, true);
  });
  page.on('console', message => { if (message.text().startsWith('__LEGACY_INPUT__')) console.log(message.text()); });
  page.on('request', request => {
    if (request.url().includes('/functions/v1/')) {
      const body = request.postDataJSON();
      if (body?.action) console.log('__LEGACY_REQUEST__' + JSON.stringify({ action: body.action }));
    }
  });
  const original = page.getByTestId.bind(page);
  page.getByTestId = (id: string | RegExp) => {
    const locator = original(id), click = locator.click.bind(locator);
    if (id === 'legacy-import-confirm') locator.click = async options => {
      if (released) return click(options);
      await locator.hover();
      await page.mouse.down();
      release();
      await expect(page.getByRole('region', { name: '原有均線參數控制台' })).toBeVisible();
      await page.mouse.up();
    };
    return locator;
  };
});
`;
const spec = source
  .replace("from './mocks'", `from ${JSON.stringify(resolve(root, 'e2e-site/mocks'))}`)
  .replace("from './account-fixture'", `from ${JSON.stringify(resolve(root, 'e2e-site/account-fixture'))}`);
await writeFile(resolve(generated, 'legacy-import-race.spec.ts'), spec + '\n' + hook);
await writeFile(resolve(generated, 'config.ts'), `
import base from ${JSON.stringify(resolve(root, 'e2e-site/playwright.compact.config'))};
export default { ...base, testDir: ${JSON.stringify(generated)}, testMatch: ['legacy-import-race.spec.ts'],
  webServer: { ...base.webServer, cwd: ${JSON.stringify(root)} } };
`);
console.log(JSON.stringify({ sourceRef: ref ?? 'working tree', generated, out,
  schedule: 'Release the cold ConsolePage chunk between native pointerdown and pointerup; no assertion or timeout is changed.' }));
const child = spawn(process.execPath, [resolve(root, 'node_modules/@playwright/test/cli.js'), 'test',
  '--config', resolve(generated, 'config.ts'), '--project=iphone375-webkit',
  '--grep', 'cloud explicitly merges old settings, preserves account conflicts, and can rerun without duplicates',
  '--workers=1', '--retries=0', '--trace=on', '--output', resolve(out, 'artifacts'), '--reporter=line,json'], {
  cwd: root, stdio: 'inherit', env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_FILE: resolve(out, 'results.json') },
});
const code = await new Promise((accept, reject) => { child.once('error', reject); child.once('exit', accept); });
process.exitCode = code ?? 1;
