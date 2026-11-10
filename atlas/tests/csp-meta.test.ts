import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const paths = ['../index.html', '../line-login-launch.html'] as const;
const policy = (path: string) => {
  const html = readFileSync(new URL(path, import.meta.url), 'utf8');
  return html.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/)?.[1] ?? '';
};

describe('static Pages CSP (real LIFF device validation remains required)', () => {
  it.each(paths)('%s has a restrictive policy before executable scripts', (path) => {
    const html = readFileSync(new URL(path, import.meta.url), 'utf8');
    const csp = policy(path);
    expect(csp).not.toBe('');
    expect(html.indexOf('http-equiv="Content-Security-Policy"')).toBeLessThan(html.indexOf('<script'));
    expect(csp).toContain("script-src 'self' https://static.line-scdn.net");
    expect(csp).toContain("style-src 'self' 'unsafe-inline'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain('https://bxhqpfeberqbtxymghyt.supabase.co');
    expect(csp).not.toMatch(/frame-ancestors|report-uri|unsafe-eval/);
  });
  it('applies the same policy to both browser entry points', () => {
    expect(policy(paths[0])).toEqual(policy(paths[1]));
  });
});
