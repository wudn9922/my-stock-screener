import { createClient } from '@supabase/supabase-js';
import { resolveLineUser, DeviceSessionError } from '../_shared/deviceSession.ts';
import { adminLineId, parseStorageUserId } from '../_shared/storageAccess.ts';
import { createMonitorHandler } from './handler.ts';

// Reuse existing identity/grant helpers without changing the login endpoints.
Deno.serve(async request => {
  const url = Deno.env.get('SUPABASE_URL'), key = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  if (!url || !key) return new Response(JSON.stringify({ ok: false, error: '監控訊號服務尚未啟用。' }), {
    status: 503, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
  const deadline = AbortSignal.any([request.signal, AbortSignal.timeout(12_000)]);
  const database = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { fetch: (input, init) => fetch(input, { ...init, signal: deadline }) },
  });
  const verify = async (token: string) => {
    const channel = Deno.env.get('LINE_LOGIN_CHANNEL_ID');
    if (!channel) throw new DeviceSessionError(503, '監控訊號服務尚未啟用。');
    const response = await fetch('https://api.line.me/oauth2/v2.1/verify', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ id_token: token, client_id: channel }), signal: deadline,
      redirect: 'error',
    });
    const identity = await response.json().catch(() => null);
    if (!response.ok || !identity || typeof identity.sub !== 'string') throw new DeviceSessionError(401, '登入已失效，請重新開啟 Atlas。');
    try { return parseStorageUserId(identity.sub); } catch { throw new DeviceSessionError(401, '登入已失效，請重新開啟 Atlas。'); }
  };
  return createMonitorHandler({ database, verifyIdentity: token => resolveLineUser(database, token, verify),
    adminId: adminLineId(Deno.env.get('ADMIN_LINE_USER_ID')) })(request);
});
