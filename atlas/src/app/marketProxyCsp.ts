import type { Plugin } from 'vite';

/** Replaced by Vite for each HTML entry, never a real CSP source on the wire. */
export const MARKET_PROXY_CSP_MARKER = '__ATLAS_MARKET_PROXY_ORIGIN__';

/** Include only the exact API origin, not a function path or a test wildcard. */
export function marketProxyOrigin(raw: string | undefined): string {
  if (!raw?.trim()) return '';
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error('VITE_MARKET_PROXY_URL must be an absolute HTTP(S) URL');
  }
  if (url.protocol !== 'https:' &&
    !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))
    throw new Error('VITE_MARKET_PROXY_URL must use HTTPS (HTTP allowed only for loopback)');
  if (url.username || url.password) throw new Error('VITE_MARKET_PROXY_URL must not include credentials');
  return url.origin;
}

export function injectMarketProxyCsp(html: string, proxyUrl: string | undefined): string {
  if (!html.includes('http-equiv="Content-Security-Policy"')) return html;
  if (!html.includes(MARKET_PROXY_CSP_MARKER))
    throw new Error('CSP market proxy placeholder missing from HTML entry');
  return html.replaceAll(MARKET_PROXY_CSP_MARKER, marketProxyOrigin(proxyUrl));
}

/** Both Pages entries share the same CSP and receive their environment's proxy at build time. */
export function marketProxyCsp(proxyUrl: string | undefined): Plugin {
  return {
    name: 'atlas-market-proxy-csp',
    transformIndexHtml(html) {
      return injectMarketProxyCsp(html, proxyUrl);
    },
  };
}
