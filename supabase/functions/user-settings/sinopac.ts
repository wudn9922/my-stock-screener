import type { SupabaseClient } from '@supabase/supabase-js';
import { HoldingsError } from './holdings.ts';
/** Dashboard-pasted secrets often carry a trailing newline or space; never let that break a header or URL. */
const env = (name: string) => Deno.env.get(name)?.trim();
const fail = () => new HoldingsError(503, '永豐同步服務尚未就緒，請聯絡維護者。');
const base64url = (value: Uint8Array) =>
  btoa(String.fromCharCode(...value))
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
/** Runtime-only invoker identity; deployment still uses GitHub Workload Identity. */
async function googleToken(audience: string, fetcher: typeof fetch): Promise<string> {
  try {
    const email = env('SINOPAC_INVOKER_EMAIL')!;
    const pem = env('SINOPAC_INVOKER_PRIVATE_KEY')!;
    const key = Uint8Array.from(atob(pem.replace(/-----[^-]+-----/g, '').replace(/\s/g, '')), (c) =>
      c.charCodeAt(0),
    );
    const encoder = new TextEncoder(),
      now = Math.floor(Date.now() / 1000);
    const payload = [
      { alg: 'RS256', typ: 'JWT' },
      {
        iss: email,
        sub: email,
        aud: 'https://oauth2.googleapis.com/token',
        iat: now,
        exp: now + 300,
        target_audience: audience,
      },
    ]
      .map((v) => base64url(encoder.encode(JSON.stringify(v))))
      .join('.');
    const signing = await crypto.subtle.importKey(
      'pkcs8',
      key,
      { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const signature = base64url(
      new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', signing, encoder.encode(payload))),
    );
    const response = await fetcher('https://oauth2.googleapis.com/token', {
      method: 'POST',
      body: new URLSearchParams({
        grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        assertion: payload + '.' + signature,
      }),
      signal: AbortSignal.timeout(10000),
      redirect: 'error',
    });
    const value = await response.json();
    if (!response.ok || typeof value.id_token !== 'string') throw fail();
    return value.id_token;
  } catch {
    throw fail();
  }
}
export async function handleSinopac(
  db: SupabaseClient,
  owner: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch = fetch,
) {
  const ownerId = env('SINOPAC_OWNER_ID'),
    accountId = env('SINOPAC_ACCOUNT_ID');
  if (
    !ownerId ||
    owner !== ownerId ||
    !accountId ||
    (body.accountId !== undefined && body.accountId !== accountId)
  )
    throw new HoldingsError(403, '此帳號無永豐同步權限。');
  if (body.action === 'holdings.sinopac.sync') {
    try {
      const secret = env('SINOPAC_SHARED_SECRET') ?? '';
      const url = new URL(env('SINOPAC_SYNC_URL') ?? '');
      if (
        new TextEncoder().encode(secret).length < 32 ||
        url.protocol !== 'https:' ||
        !url.hostname.endsWith('.run.app') ||
        url.username ||
        url.password ||
        url.search ||
        url.hash
      )
        throw fail();
      const token = await googleToken(url.origin, fetcher);
      const response = await fetcher(new URL('/sync', url), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Sinopac-Secret': secret,
          Authorization: 'Bearer ' + token,
        },
        body: JSON.stringify({ ownerId, accountId }),
        signal: AbortSignal.timeout(110000),
        redirect: 'error',
      });
      const result = await response.json();
      const messages: Record<string, string> = {
        rate: '60 秒內僅能同步一次',
        login: '永豐登入失敗',
        permission: '永豐帳務權限不足',
        quota: '永豐流量超限，請稍後再試',
        timeout: '永豐同步逾時，請稍後再試',
        conflict: '持股已更新，請重新同步',
        calendar: '交易日曆尚未就緒',
        invalid: '永豐資料無法完整辨識，未更新持股',
      };
      if (!response.ok)
        throw new HoldingsError(
          response.status === 429 ? 429 : 503,
          messages[result.code] ?? '永豐同步暫時無法使用',
        );
    } catch (error) {
      if (error instanceof HoldingsError) throw error;
      throw fail();
    }
  } else if (body.action !== 'holdings.sinopac.status') throw fail();
  const { data, error } = await db
    .from('atlas_sinopac_sync')
    .select('last_sync_at,last_success_at,last_error_code,skipped_shorts')
    .eq('line_user_id', owner)
    .eq('account_id', accountId)
    .maybeSingle();
  if (error) return { accountId, lastSyncAt: null, lastSuccessAt: null, code: 'service', skippedShorts: 0 };
  return {
    accountId,
    lastSyncAt: data?.last_sync_at ?? null,
    lastSuccessAt: data?.last_success_at ?? null,
    code: data?.last_error_code ?? null,
    skippedShorts: data?.skipped_shorts ?? 0,
  };
}
