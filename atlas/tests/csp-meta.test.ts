import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  injectMarketProxyCsp,
  MARKET_PROXY_CSP_MARKER,
  marketProxyOrigin,
} from '../src/app/marketProxyCsp';

const paths = ['../index.html', '../line-login-launch.html'] as const;
const PROD_PROXY = 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart';
const TEST_PROXY = 'https://edge.test/functions/v1/market-chart';
const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
const policy = (html: string) =>
  html.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/)?.[1] ?? '';

describe('static Pages CSP (real LIFF device validation remains required)', () => {
  it.each(paths)('%s has a restrictive CSP and an environment proxy placeholder', (path) => {
    const html = read(path);
    const csp = policy(html);
    expect(csp).not.toBe('');
    expect(html.indexOf('http-equiv="Content-Security-Policy"')).toBeLessThan(html.indexOf('<script'));
    expect(csp).toContain("script-src 'self' https://static.line-scdn.net");
    expect(csp).toContain("style-src 'self' 'unsafe-inline'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain('https://bxhqpfeberqbtxymghyt.supabase.co');
    expect(csp).toContain(MARKET_PROXY_CSP_MARKER);
    expect(csp).not.toContain('https://edge.test');
    expect(csp).not.toMatch(/frame-ancestors|report-uri|unsafe-eval/);
  });

  it('keeps both HTML entry policies identical', () => {
    expect(policy(read(paths[0]))).toEqual(policy(read(paths[1])));
  });

  it.each(paths)('production build policy on %s excludes test host and unresolved markers', (path) => {
    const csp = policy(injectMarketProxyCsp(read(path), PROD_PROXY));
    expect(csp).toContain('https://bxhqpfeberqbtxymghyt.supabase.co');
    expect(csp).not.toContain('edge.test');
    expect(csp).not.toContain(MARKET_PROXY_CSP_MARKER);
    expect(csp).not.toMatch(/unsafe-eval|frame-ancestors|report-uri/);
  });

  it.each(paths)('e2e build policy on %s permits only the configured proxy origin', (path) => {
    const csp = policy(injectMarketProxyCsp(read(path), TEST_PROXY));
    expect(csp).toContain('https://edge.test;');
    expect(csp).not.toContain(MARKET_PROXY_CSP_MARKER);
  });

  it('extracts and validates exact proxy origins without paths or credentials', () => {
    expect(marketProxyOrigin(PROD_PROXY)).toBe('https://bxhqpfeberqbtxymghyt.supabase.co');
    expect(marketProxyOrigin(TEST_PROXY)).toBe('https://edge.test');
    expect(marketProxyOrigin(undefined)).toBe('');
    expect(marketProxyOrigin('')).toBe('');
    expect(() => marketProxyOrigin('javascript:alert(1)')).toThrow();
    expect(() => marketProxyOrigin('http://example.com/path')).toThrow();
    expect(() => marketProxyOrigin('https://user:secret@example.com/path')).toThrow();
  });
});
