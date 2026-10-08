import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fetch, EnvHttpProxyAgent } from 'undici';

/**
 * Network access for the build-time data scripts, with an offline fixture mode:
 * when `fixtureDir` is set (env ATLAS_FIXTURE_DIR or `--fixtures=<dir>`), every request reads
 * `<fixtureDir>/<fixture>` as UTF-8 text instead of contacting the network (a missing file behaves
 * like a failed request, so fallback paths can be exercised offline).
 */
export interface SourceRequest {
  /** Fixture file name used in offline mode. */
  fixture: string;
  /** Network body encoding; default utf-8. TWSE ISIN pages are Big5 (MS950). */
  encoding?: 'utf-8' | 'big5';
  headers?: Record<string, string>;
  /** Treat this HTTP status as "no data" and resolve to null instead of throwing (e.g. 404 for frames). */
  emptyOn?: number[];
}

export interface SourceFetcher {
  readonly offline: boolean;
  text(url: string, request: SourceRequest): Promise<string | null>;
  json(url: string, request: SourceRequest): Promise<unknown>;
  close(): Promise<void>;
}

export interface SourceFetcherOptions {
  fixtureDir?: string;
  userAgent?: string;
  /** Minimum spacing between network requests (ms); default 250 (≤ 4 req/s). */
  minIntervalMs?: number;
  timeoutMs?: number;
  retries?: number;
}

export function fixtureDirFromEnv(argv: readonly string[] = process.argv, env: NodeJS.ProcessEnv = process.env): string | undefined {
  const flag = argv.find((arg) => arg.startsWith('--fixtures='));
  const value = flag ? flag.slice('--fixtures='.length) : env.ATLAS_FIXTURE_DIR;
  return value?.trim() || undefined;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createSourceFetcher(options: SourceFetcherOptions = {}): SourceFetcher {
  const fixtureDir = options.fixtureDir;
  const minIntervalMs = options.minIntervalMs ?? 250;
  const timeoutMs = options.timeoutMs ?? 60_000;
  const retries = options.retries ?? 2;
  const userAgent = options.userAgent ?? 'Mozilla/5.0 (compatible; my-stock-screener-atlas/1.0; +https://github.com/wudn9922/my-stock-screener)';
  const dispatcher = fixtureDir ? undefined : new EnvHttpProxyAgent();
  let lastRequestAt = 0;

  async function networkText(url: string, request: SourceRequest): Promise<string | null> {
    let lastError: unknown;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const wait = lastRequestAt + minIntervalMs - Date.now();
      if (wait > 0) await sleep(wait);
      lastRequestAt = Date.now();
      try {
        const response = await fetch(url, {
          dispatcher,
          signal: AbortSignal.timeout(timeoutMs),
          headers: { 'User-Agent': userAgent, Accept: '*/*', ...request.headers },
        });
        if (request.emptyOn?.includes(response.status)) {
          await response.body?.cancel();
          return null;
        }
        if (!response.ok) {
          await response.body?.cancel();
          const error = new Error(`HTTP ${response.status} for ${url}`);
          if (response.status !== 429 && response.status < 500) throw Object.assign(error, { permanent: true });
          throw error;
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        return new TextDecoder(request.encoding ?? 'utf-8').decode(bytes);
      } catch (error) {
        lastError = error;
        if ((error as { permanent?: boolean }).permanent) break;
        if (attempt < retries) await sleep(2000 * (attempt + 1));
      }
    }
    throw lastError instanceof Error ? lastError : new Error(String(lastError));
  }

  async function text(url: string, request: SourceRequest): Promise<string | null> {
    if (!fixtureDir) return networkText(url, request);
    try {
      return await readFile(join(fixtureDir, request.fixture), 'utf8');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT' && request.emptyOn?.includes(404)) return null;
      throw new Error(`Fixture ${request.fixture} unavailable for ${url}`, { cause: error });
    }
  }

  return {
    offline: !!fixtureDir,
    text,
    async json(url, request) {
      const body = await text(url, request);
      return body === null ? null : (JSON.parse(body) as unknown);
    },
    async close() {
      await dispatcher?.close();
    },
  };
}
