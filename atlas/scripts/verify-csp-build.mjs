// Fail the Pages build if either emitted HTML entry retains the CSP placeholder
// or accidentally allows the e2e-only market proxy host.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const output = resolve(process.argv[2] || 'dist-pages');
const policy = (name) => {
  const markup = readFileSync(resolve(output, name), 'utf8');
  const result = markup.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/);
  if (!result) throw new Error(`Missing CSP meta tag in ${name}`);
  const value = result[1];
  if (value.includes('__ATLAS_MARKET_PROXY_ORIGIN__') || value.includes('https://edge.test'))
    throw new Error(`Invalid production CSP source in ${name}`);
  if (value.includes("'unsafe-eval'")) throw new Error(`unsafe-eval is disallowed in ${name}`);
  return value;
};
const index = policy('index.html');
const launch = policy('line-login-launch.html');
if (index !== launch) throw new Error('CSP differs between index and LINE launch');
console.log('Pages CSP verified: no test origin, unsafe-eval or unresolved marker.');
