// Optional Node preload: record the original smoke's browser events without handling fetch rejections.
import { chromium, webkit } from 'playwright';
import { writeFileSync } from 'node:fs';

const file = process.env.RESEARCH_PAGES_DIAGNOSTICS_FILE;
if (!file) throw new Error('RESEARCH_PAGES_DIAGNOSTICS_FILE is required');
const records = [];
const add = (project, event) => records.push({ at: Date.now(), project, ...event });
process.once('exit', () => writeFileSync(file, records.map(record => JSON.stringify(record)).join('\n') + '\n'));

for (const [project, type] of [['desktop-chromium', chromium], ['iphone-webkit', webkit]]) {
  const launch = type.launch.bind(type);
  type.launch = async (...args) => {
    const browser = await launch(...args);
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async (...options) => {
      const context = await newContext(...options);
      await context.addInitScript(() => {
        const record = event => console.debug('__RESEARCH_LIFETIME__' + JSON.stringify({
          at: Date.now(), document: performance.timeOrigin, page: location.href,
          visibility: document.visibilityState, readyState: document.readyState, ...event,
        }));
        for (const event of ['beforeunload', 'pagehide', 'pageshow', 'visibilitychange'])
          window.addEventListener(event, value => record({ event, persisted: value.persisted }));
        window.addEventListener('unhandledrejection', value => record({ event: 'unhandledrejection', reason: String(value.reason), stack: value.reason?.stack }));
        const original = window.fetch;
        window.fetch = function(input, init) {
          const url = typeof input === 'string' ? input : input?.url ?? String(input);
          if (url.includes('/series/') || url.includes('kline-archive')) {
            const signal = init?.signal ?? input?.signal;
            record({ event: 'fetch-start', url, signalSupplied: !!signal, aborted: signal?.aborted, stack: new Error().stack });
            signal?.addEventListener('abort', () => record({ event: 'fetch-abort', url, reason: String(signal.reason) }), { once: true });
          }
          // Return the native promise unchanged: diagnostic listeners must not swallow an unhandled rejection.
          return Reflect.apply(original, this, arguments);
        };
      });
      context.on('page', page => {
        page.on('pageerror', error => add(project, { event: 'pageerror', page: page.url(), name: error.name, message: error.message, stack: error.stack }));
        page.on('console', message => {
          if (message.text().startsWith('__RESEARCH_LIFETIME__')) add(project, JSON.parse(message.text().slice('__RESEARCH_LIFETIME__'.length)));
          else if (message.type() === 'error') add(project, { event: 'console-error', page: page.url(), message: message.text() });
        });
        page.on('request', request => {
          if (request.url().includes('/series/') || request.url().includes('kline-archive')) add(project, {
            event: 'request', page: page.url(), url: request.url(), method: request.method(), range: request.headers().range ?? null,
          });
        });
        page.on('requestfailed', request => add(project, { event: 'requestfailed', page: page.url(), url: request.url(), failure: request.failure() }));
        page.on('response', response => {
          if (response.url().includes('/series/')) add(project, { event: 'response', page: page.url(), url: response.url(), status: response.status(), headers: response.headers() });
        });
      });
      return context;
    };
    return browser;
  };
}
