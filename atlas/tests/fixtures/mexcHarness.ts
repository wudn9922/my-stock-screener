import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformSync } from 'esbuild';
import * as policy from '../../../supabase/functions/_shared/storageAccess';
import * as watchlists from '../../../supabase/functions/user-settings/watchlists';
import * as lineHandoff from '../../../supabase/functions/_shared/lineLoginHandoff';
import * as deviceSession from '../../../supabase/functions/_shared/deviceSession';
import * as holdings from '../../../supabase/functions/user-settings/holdings';
import * as mexc from '../../../supabase/functions/user-settings/mexc';
import type { MexcConnection, MexcBalance, MexcQuote } from '../../../supabase/functions/user-settings/mexcContract';
import { replaceAccount } from '../../src/holdings/model';

export const MEXC_USER = 'U11111111111111111111111111111111';
export const OTHER_USER = 'U22222222222222222222222222222222';
export const TEST_KEY = 'test-mexc-access-key-9876';
export const TEST_SECRET = 'test-mexc-secret-never-return-12345678';
export const MASTER = btoa('k'.repeat(32));
export const MEXC_ACCOUNT: holdings.HoldingAccount = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'MEXC 測試',
  kind: 'crypto-exchange',
  currency: 'USDT',
  broker: 'mexc',
};
type Row = Record<string, unknown>;
type PrivateConnection = MexcConnection & { credential: unknown };
type Reservation = { requestId: string | null; accountId: string; purpose: string; expires: number; next: number };

/** Transactional relational substitute. No SQL executed; no real MEXC network calls. */
export function createMexcFixture(mode: policy.StorageMode = 'cloud') {
  let currentTime = Date.parse('2026-10-28T10:00:00Z');
  const documents = new Map<string, holdings.HoldingsDocument>();
  const connections = new Map<string, PrivateConnection>();
  const reservations = new Map<string, Reservation>();
  const grants = new Map<string, policy.StorageMode>([[MEXC_USER, mode]]);
  const calls: { name: string; parameters: Row }[] = [],
    reads: string[] = [],
    errorLogs: unknown[][] = [];
  const upstream: { url: string; init?: RequestInit }[] = [];
  let failure:
    'none' | 'write-key' | 'bad-key' | 'malformed' | 'missing-rpc' | 'db-detail' | 'network' | 'rate' | 'redirect' =
    'none';
  let sourceBalances = [
    { asset: 'BTC', free: '0.1', locked: '0.02' },
    { asset: 'ETH', free: '2', locked: '0' },
    { asset: 'USDT', free: '100', locked: '0' },
  ];
  const ownerDocument = (owner: string) =>
    structuredClone(documents.get(owner) ?? { version: 1 as const, accounts: [] });
  const slot = (owner: string, id: string) => `${owner}:${id}`;
  function publicResult(owner: string) {
    const document = ownerDocument(owner);
    return {
      status: 'ok',
      document,
      retryAt: reservations.has(owner) ? new Date(reservations.get(owner)!.next).toISOString() : null,
      connections: [...connections]
        .filter(([key]) => key.startsWith(owner + ':'))
        .map(([, connection]) => {
          const publicValue = {
            accountId: connection.accountId,
            keyLast4: connection.keyLast4,
            connectedAt: connection.connectedAt,
            syncedAt: connection.syncedAt,
            snapshotId: connection.snapshotId,
            holdingRevision: connection.holdingRevision,
            needsReview: connection.needsReview,
            balances: connection.balances,
            quotes: connection.quotes,
          };
          const account = document.accounts.find((a) => a.account.id === connection.accountId);
          return {
            ...structuredClone(publicValue),
            needsReview: connection.needsReview || connection.holdingRevision !== account?.revision,
          };
        }),
    };
  }
  const db = {
    async rpc(
      name: string,
      parameters: Row,
    ): Promise<{ data: unknown; error: { code?: string; message?: string } | null }> {
      calls.push({ name, parameters: structuredClone(parameters) });
      const owner = String(parameters.owner_id),
        operation = String(parameters.operation),
        payload = (parameters.payload ?? {}) as Row;
      const result = (data: unknown) => ({ data, error: null });
      if (failure === 'missing-rpc') return { data: null, error: { code: 'PGRST202' } };
      if (failure === 'db-detail')
        return { data: null, error: { code: 'XX000', message: `${TEST_SECRET} ${TEST_KEY} PRIVATE-BALANCE` } };
      const document = ownerDocument(owner);
      if (name === 'atlas_holdings_action') {
        if (operation === 'list') return result({ status: 'ok', document });
        if (operation === 'replaceAccount') {
          const account = holdings.validateHoldingAccount(payload.account);
          const previous = document.accounts.find((a) => a.account.id === account.id);
          if ((previous?.revision ?? null) !== parameters.expected_revision) return result({ status: 'conflict' });
          const next = replaceAccount(document, account, payload.holdings as holdings.Holding[]);
          documents.set(owner, next);
          return result({ status: 'ok', document: next });
        }
        if (operation === 'deleteAccount') {
          document.accounts = document.accounts.filter((a) => a.account.id !== payload.accountId);
          connections.delete(slot(owner, String(payload.accountId)));
          documents.set(owner, document);
          return result({ status: 'ok', document });
        }
        throw new Error('Unexpected holdings operation');
      }
      if (name !== 'atlas_mexc_action') throw new Error('Unexpected RPC');
      const accountId = String(payload.accountId),
        key = slot(owner, accountId);
      const account = document.accounts.find((a) => a.account.id === accountId),
        connection = connections.get(key);
      if (operation === 'status') return result(publicResult(owner));
      if (operation === 'backoff') {
        const reservation = reservations.get(owner);
        if (reservation && reservation.requestId === payload.requestId)
          reservation.next = Math.max(reservation.next, currentTime + Math.min(3600, Number(payload.seconds)) * 1000);
        return result({ status: 'ok' });
      }
      if (operation === 'claim') {
        if (payload.purpose === 'connect') {
          if ((account?.revision ?? null) !== payload.expectedRevision) return result({ status: 'conflict' });
          if (!account && document.accounts.length >= 30) return result({ status: 'limit' });
        } else if (!account || !connection) return result({ status: 'missing' });
        if (account && account.account.broker !== 'mexc') return result({ status: 'invalid' });
        const previous = reservations.get(owner);
        if (previous && previous.next > currentTime)
          return result({ status: 'rate_limited', retryAfter: Math.ceil((previous.next - currentTime) / 1000) });
        reservations.set(owner, {
          requestId: String(payload.requestId),
          accountId,
          purpose: String(payload.purpose),
          expires: currentTime + 30000,
          next: currentTime + 30000,
        });
        return result({
          status: 'ok',
          expectedRevision: account?.revision ?? null,
          credential: payload.purpose === 'sync' ? connection?.credential : null,
        });
      }
      if (operation === 'disconnect') {
        if (!account) return result({ status: 'missing' });
        connections.delete(key);
        const reservation = reservations.get(owner);
        if (reservation?.accountId === accountId) reservation.requestId = null;
        return result(publicResult(owner));
      }
      if (operation !== 'finish' && operation !== 'apply') throw new Error('Unexpected MEXC operation');
      if ((account?.revision ?? null) !== payload.expectedRevision) return result({ status: 'conflict' });
      const reservation = reservations.get(owner);
      if (
        operation === 'finish' &&
        (!reservation?.requestId ||
          reservation.requestId !== payload.requestId ||
          reservation.accountId !== accountId ||
          reservation.purpose !== payload.purpose ||
          reservation.expires <= currentTime)
      )
        return result({ status: 'conflict' });
      if (
        operation === 'apply' &&
        (!connection?.needsReview ||
          connection.snapshotId !== payload.snapshotId ||
          connection.holdingRevision !== account?.revision)
      )
        return result({ status: 'conflict' });
      const balances = (operation === 'apply' ? connection!.balances : payload.balances) as MexcBalance[];
      const definition =
        payload.purpose === 'connect' ? holdings.validateHoldingAccount(payload.account) : account!.account;
      const qty = (text: string) => {
        const [i, f = ''] = text.split('.');
        return BigInt(i!) * 10n ** 18n + BigInt(f.padEnd(18, '0'));
      };
      const needsReview =
        operation !== 'apply' &&
        balances.some((b) => {
          const h = account?.holdings.find((h) => h.symbol === b.symbol);
          return !h || qty(b.quantity) > qty(h.quantity);
        });
      let next = document;
      if (!needsReview) {
        const normalized = balances.map((b) => ({
          id: account?.holdings.find((h) => h.symbol === b.symbol)?.id ?? crypto.randomUUID(),
          accountId,
          symbol: b.symbol,
          market: 'CRYPTO' as const,
          quantity: b.quantity,
          averageCost:
            operation === 'apply'
              ? (payload.costs as Record<string, string>)[b.asset]!
              : account!.holdings.find((h) => h.symbol === b.symbol)!.averageCost,
          costCurrency: 'USDT' as const,
          source: 'api' as const,
          updatedAt: new Date(currentTime).toISOString(),
        }));
        if (normalized.some((h) => h.averageCost === undefined)) return result({ status: 'invalid' });
        if (
          document.accounts.reduce((sum, a) => sum + (a.account.id === accountId ? 0 : a.holdings.length), 0) +
            normalized.length >
          2000
        )
          return result({ status: 'limit' });
        next = replaceAccount(document, definition, normalized);
      } else if (!account) next = replaceAccount(document, definition, []);
      const nextRevision = next.accounts.find((a) => a.account.id === accountId)!.revision;
      if (operation === 'finish')
        connections.set(key, {
          accountId,
          credential: payload.purpose === 'connect' ? payload.credential : connection!.credential,
          keyLast4: payload.purpose === 'connect' ? String(payload.keyLast4) : connection!.keyLast4,
          connectedAt: payload.purpose === 'connect' ? new Date(currentTime).toISOString() : connection!.connectedAt,
          syncedAt: new Date(currentTime).toISOString(),
          snapshotId: String(payload.requestId),
          holdingRevision: nextRevision,
          needsReview,
          balances: structuredClone(balances),
          quotes: structuredClone(payload.quotes as Record<string, MexcQuote>),
        });
      else {
        connection!.needsReview = false;
        connection!.holdingRevision = nextRevision;
      }
      if (operation === 'finish') reservation!.requestId = null;
      documents.set(owner, next);
      return result(publicResult(owner));
    },
    from(table: string) {
      reads.push(table);
      let target = '';
      const query = {
        select: () => query,
        eq: (column: string, value: unknown) => {
          if (column === 'line_user_id') target = String(value);
          return query;
        },
        neq: () => query,
        order: () => query,
        limit: () => query,
        maybeSingle: () => query,
        single: () => query,
        then: (resolve: (value: unknown) => unknown) =>
          Promise.resolve(
            resolve({
              data:
                table === policy.STORAGE_ACCESS_TABLE
                  ? grants.has(target)
                    ? {
                        line_user_id: target,
                        storage_mode: grants.get(target),
                        label: '',
                        updated_at: '2026-10-28T00:00:00Z',
                      }
                    : null
                  : [],
              error: null,
            }),
          ),
      };
      return query;
    },
  };
  let beforeUpstream: ((path: string) => void | Promise<void>) | undefined;
  const mexcFetch: typeof fetch = async (input, init) => {
    const url = String(input);
    upstream.push({ url, init });
    const path = new URL(url).pathname;
    await beforeUpstream?.(path);
    if (failure === 'network') throw new Error(`PRIVATE ${TEST_SECRET} ${url}`);
    if (failure === 'rate')
      return new Response('private rate details', { status: 429, headers: { 'Retry-After': '120' } });
    if (failure === 'redirect')
      return new Response('private redirect', { status: 302, headers: { Location: 'https://untrusted.invalid/' } });
    if (path === '/api/v3/apiKeyInfo')
      return Response.json({
        accessKey: new Headers(init?.headers).get('X-MEXC-APIKEY'),
        status: failure === 'bad-key' ? 'FROZEN' : 'VALID',
        permissions: failure === 'write-key' ? 'SPOT_ACCOUNT_READ,SPOT_DEAL_WRITE' : 'SPOT_ACCOUNT_READ',
        remainingValidity: 60,
        note: 'PRIVATE-DROP-THIS',
        apiSecret: 'upstream-echo-must-drop',
      });
    if (path === '/api/v3/account')
      return Response.json(
        failure === 'malformed'
          ? { accountType: 'SPOT', balances: null }
          : {
              accountType: 'SPOT',
              balances: sourceBalances,
              canTrade: true,
              canWithdraw: true,
              canDeposit: true,
              permissions: ['SPOT'],
            },
      );
    if (path === '/api/v3/ticker/price')
      return Response.json([
        { symbol: 'BTCUSDT', price: '65000' },
        { symbol: 'ETHUSDT', price: '3500' },
      ]);
    throw new Error('Unexpected upstream path');
  };
  function handler(user = MEXC_USER, verified = true) {
    let serve!: (request: Request) => Promise<Response>;
    const source = readFileSync(
      new URL('../../../supabase/functions/user-settings/index.ts', import.meta.url),
      'utf8',
    ).replace(/^import .*;\r?\n/gm, '');
    runInNewContext(transformSync(source, { loader: 'ts', target: 'es2022', format: 'cjs' }).code, {
      ...policy,
      ...watchlists,
      ...lineHandoff,
      ...deviceSession,
      ...holdings,
      ...mexc,
      handleMexc: (
        database: Parameters<typeof mexc.handleMexc>[0],
        owner: string,
        body: Row,
        master: string | undefined,
        authorize: () => Promise<unknown>,
      ) => mexc.handleMexc(database, owner, body, master, authorize, mexcFetch, () => currentTime),
      Deno: {
        env: {
          get: (key: string) =>
            key === 'ADMIN_LINE_USER_ID'
              ? policy.DEFAULT_ADMIN_LINE_ID
              : key === 'HOLDINGS_ENCRYPTION_KEY'
                ? MASTER
                : key === 'ATLAS_FIXED_PARAMETERS_ENABLED'
                  ? '1'
                  : 'offline-test-only',
        },
        serve: (callback: typeof serve) => {
          serve = callback;
        },
      },
      createClient: () => db,
      fetch: async () =>
        Response.json(verified ? { sub: user } : { error: 'invalid_token' }, { status: verified ? 200 : 401 }),
      Request,
      Response,
      URLSearchParams,
      TextEncoder,
      crypto,
      btoa,
      console: { error: (...args: unknown[]) => errorLogs.push(args) },
    });
    return async (body: Row) => {
      const response = await serve(
        new Request('https://offline.invalid/', {
          method: 'POST',
          body: JSON.stringify({ idToken: 'fake-test-token', ...body }),
        }),
      );
      return { status: response.status, headers: response.headers, body: await response.json() };
    };
  }
  return {
    db,
    handler,
    documents,
    connections,
    reservations,
    grants,
    calls,
    reads,
    upstream,
    errorLogs,
    mexcFetch,
    now: () => currentTime,
    advance: (ms = 31000) => {
      currentTime += ms;
    },
    setBalances: (value: typeof sourceBalances) => {
      sourceBalances = value;
    },
    setFailure: (value: typeof failure) => {
      failure = value;
    },
    onUpstream: (callback?: typeof beforeUpstream) => {
      beforeUpstream = callback;
    },
    publicResult,
  };
}
