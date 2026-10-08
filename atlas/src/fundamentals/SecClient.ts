import { fetch as proxyFetch, EnvHttpProxyAgent } from 'undici';
import { normalizeSymbol } from '../market-data/MarketDataProvider';
export type SecTransport = (url: string, userAgent: string) => Promise<unknown>;
export class SecUnavailableError extends Error {
  constructor(
    message: string,
    readonly status = 502,
  ) {
    super(message);
  }
}
/** Server-only, fixed SEC destinations. A single queue stays below SEC's 10 req/s limit. */
export class SecClient {
  private cache = new Map<string, { expires: number; value: unknown }>();
  private pending = new Map<string, Promise<unknown>>();
  private queue: Promise<unknown> = Promise.resolve();
  private nextRequest = 0;
  private cooldown = 0;
  constructor(
    private transport: SecTransport,
    private userAgent = 'AtlasResearchTerminal/0.2 (local research prototype)',
    private intervalMs = 500,
    private now = () => Date.now(),
  ) {}
  private get(url: string, ttl: number): Promise<unknown> {
    const cached = this.cache.get(url);
    if (cached && cached.expires > this.now()) return Promise.resolve(cached.value);
    const prior = this.pending.get(url);
    if (prior) return prior;
    const promise = this.queue
      .catch(() => {})
      .then(async () => {
        if (this.cooldown > this.now())
          throw new SecUnavailableError('SEC rate limit cooldown; try again later', 429);
        const wait = Math.max(0, this.nextRequest - this.now());
        if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
        this.nextRequest = this.now() + this.intervalMs;
        try {
          const value = await this.transport(url, this.userAgent);
          this.cache.delete(url);
          this.cache.set(url, { expires: this.now() + ttl, value });
          while (this.cache.size > 32) this.cache.delete(this.cache.keys().next().value!);
          return value;
        } catch (error) {
          if (error instanceof SecUnavailableError && [403, 429].includes(error.status))
            this.cooldown = this.now() + 60000;
          throw error;
        }
      })
      .finally(() => this.pending.delete(url));
    this.queue = promise;
    this.pending.set(url, promise);
    return promise;
  }
  async resolveCik(rawSymbol: string): Promise<string | null> {
    const symbol = normalizeSymbol(rawSymbol).replaceAll('.', '-');
    const data = await this.get('https://www.sec.gov/files/company_tickers.json', 86400000);
    if (!data || typeof data !== 'object') {
      this.cache.delete('https://www.sec.gov/files/company_tickers.json');
      throw new SecUnavailableError('Invalid SEC ticker mapping');
    }
    const rows = Object.values(data).filter(
      (v): v is { ticker: string; cik_str: number } =>
        !!v &&
        typeof v === 'object' &&
        'ticker' in v &&
        typeof v.ticker === 'string' &&
        'cik_str' in v &&
        typeof v.cik_str === 'number' &&
        Number.isInteger(v.cik_str) &&
        v.cik_str > 0 &&
        v.cik_str < 1e10,
    );
    if (!rows.length) {
      this.cache.delete('https://www.sec.gov/files/company_tickers.json');
      throw new SecUnavailableError('Invalid SEC ticker mapping');
    }
    const row = rows.find((r) => r.ticker.toUpperCase().replaceAll('.', '-') === symbol);
    return row ? String(row.cik_str).padStart(10, '0') : null;
  }
  async companyFacts(symbol: string): Promise<{ cik: string; data: unknown } | null> {
    const cik = await this.resolveCik(symbol);
    if (!cik) return null;
    const url = `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;
    const data = await this.get(url, 21600000);
    if (
      !data ||
      typeof data !== 'object' ||
      !('cik' in data) ||
      !((typeof data.cik === 'number' && Number.isInteger(data.cik)) || (typeof data.cik === 'string' && /^[0-9]{1,10}$/.test(data.cik))) ||
      Number(data.cik) !== Number(cik) ||
      !('facts' in data) ||
      !data.facts ||
      typeof data.facts !== 'object'
    ) {
      this.cache.delete(url);
      throw new SecUnavailableError('SEC companyfacts identity/schema mismatch');
    }
    return { cik, data: { ...data, cik: Number(data.cik) } };
  }
}
export function createSecClient(userAgent = process.env.SEC_USER_AGENT) {
  const dispatcher = new EnvHttpProxyAgent();
  const client = new SecClient(async (url, ua) => {
    const response = await proxyFetch(url, {
      dispatcher,
      signal: AbortSignal.timeout(20000),
      headers: {
        'User-Agent': ua,
        Accept: 'application/json',
        'Accept-Encoding': 'gzip, deflate',
      },
    });
    if (!response.ok)
      throw new SecUnavailableError(`SEC returned ${response.status}`, response.status);
    return response.json();
  }, userAgent);
  return { client, close: () => dispatcher.close() };
}
