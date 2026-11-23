import type { SupabaseClient } from '@supabase/supabase-js';
import { HoldingsError } from './holdings.ts';

/**
 * E-mail statement sync (owner only): link a holdings account to one broker + market, read the
 * processing log, apply stored trades now, or unlink. Trades arrive through the statement-sync
 * Cloud Run service; nothing here touches PDFs, passwords or Gmail.
 */
export const STATEMENT_BROKERS = ['cathay'] as const;
const KIND = { TW: 'tw-broker', US: 'us-broker' } as const;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const missingTable = (error: { code?: string } | null) => error?.code === '42P01' || error?.code === 'PGRST205' || error?.code === 'PGRST202';
const unavailable = () => new HoldingsError(503, '對帳單同步資料表尚未部署，請由維護者套用 atlas_statement_sync.sql。');
const invalid = () => new HoldingsError(400, '對帳單同步設定不正確。');

type Db = SupabaseClient;
interface Link { account_id: string; broker: string; market: 'TW' | 'US' }

async function links(db: Db, owner: string): Promise<Link[]> {
  const { data, error } = await db.from('atlas_statement_links').select('account_id,broker,market').eq('line_user_id', owner);
  if (error) throw missingTable(error) ? unavailable() : new HoldingsError(503, '對帳單同步暫時無法使用。');
  return (data ?? []) as Link[];
}

async function accounts(db: Db, owner: string): Promise<{ id: string; kind: string }[]> {
  const { data, error } = await db.rpc('atlas_holdings_action', { operation: 'list', owner_id: owner, payload: {}, expected_revision: null });
  if (error || data?.status !== 'ok') throw new HoldingsError(503, '持股服務暫時無法使用，請稍後重試。');
  return (data.document.accounts as { account: { id: string; kind: string } }[]).map((entry) => entry.account);
}

async function apply(db: Db, owner: string, link: Link) {
  const { data, error } = await db.rpc('atlas_statement_apply', { owner_id: owner, broker_code: link.broker, market_code: link.market });
  if (error) throw missingTable(error) ? unavailable() : new HoldingsError(503, '對帳單同步暫時無法使用。');
  return { status: String(data?.status ?? 'service'), applied: Number(data?.applied ?? 0),
    ...(data?.symbol ? { symbol: String(data.symbol), tradeDate: String(data.tradeDate ?? '') } : {}) };
}

async function status(db: Db, owner: string) {
  const current = await links(db, owner);
  const { data: messages, error } = await db.from('atlas_statement_messages')
    .select('broker,received_at,report_date,status,trades,processed_at').eq('line_user_id', owner)
    .order('processed_at', { ascending: false }).limit(20);
  if (error) throw missingTable(error) ? unavailable() : new HoldingsError(503, '對帳單同步暫時無法使用。');
  const { data: pending, error: pendingError } = await db.from('atlas_statement_trades')
    .select('broker,market').eq('line_user_id', owner).is('applied_at', null).limit(1000);
  if (pendingError) throw new HoldingsError(503, '對帳單同步暫時無法使用。');
  return {
    links: current.map((l) => ({
      accountId: l.account_id, broker: l.broker, market: l.market,
      pending: (pending ?? []).filter((p) => p.broker === l.broker && p.market === l.market).length,
    })),
    unlinkedPending: (pending ?? []).filter((p) => !current.some((l) => l.broker === p.broker && l.market === p.market)).length,
    messages: (messages ?? []).map((m) => ({
      broker: m.broker, receivedAt: m.received_at, reportDate: m.report_date, status: m.status, trades: m.trades, processedAt: m.processed_at,
    })),
  };
}

export async function handleStatement(db: Db, owner: string, adminId: string | null, body: Record<string, unknown>) {
  if (!adminId || owner !== adminId) throw new HoldingsError(403, '此帳號無對帳單同步權限。');
  const action = body.action;
  if (action === 'holdings.statement.status') return status(db, owner);
  const accountId = typeof body.accountId === 'string' && uuid.test(body.accountId) ? body.accountId.toLowerCase() : null;
  if (!accountId) throw invalid();
  if (action === 'holdings.statement.link') {
    const broker = body.broker, market = body.market;
    if (!STATEMENT_BROKERS.includes(broker as typeof STATEMENT_BROKERS[number]) || (market !== 'TW' && market !== 'US')) throw invalid();
    const known = await accounts(db, owner);
    const account = known.find((a) => a.id === accountId);
    if (!account || account.kind !== KIND[market]) throw new HoldingsError(400, '請先建立相同市場的持股帳戶。');
    const current = await links(db, owner);
    // A link left behind by a deleted account must not block linking a new one.
    const stale = current.filter((l) => l.broker === broker && l.market === market && !known.some((a) => a.id === l.account_id));
    for (const l of stale) await db.from('atlas_statement_links').delete().eq('line_user_id', owner).eq('account_id', l.account_id);
    if (current.some((l) => l.account_id !== accountId && l.broker === broker && l.market === market && !stale.includes(l)))
      throw new HoldingsError(409, '這家券商的此市場已連結另一個帳戶。');
    const { error } = await db.from('atlas_statement_links')
      .upsert({ line_user_id: owner, account_id: accountId, broker, market }, { onConflict: 'line_user_id,account_id' });
    if (error) throw missingTable(error) ? unavailable() : new HoldingsError(409, '這家券商的此市場已連結另一個帳戶。');
    return apply(db, owner, { account_id: accountId, broker: broker as string, market });
  }
  const link = (await links(db, owner)).find((l) => l.account_id === accountId);
  if (action === 'holdings.statement.apply') {
    if (!link) throw new HoldingsError(404, '此帳戶尚未連結對帳單同步。');
    return apply(db, owner, link);
  }
  if (action === 'holdings.statement.unlink') {
    const { error } = await db.from('atlas_statement_links').delete().eq('line_user_id', owner).eq('account_id', accountId);
    if (error) throw new HoldingsError(503, '對帳單同步暫時無法使用。');
    return { unlinked: !!link };
  }
  throw invalid();
}
