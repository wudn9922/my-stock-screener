import { DEFAULT_LIFF_ID, type LineIdentity } from './lineIdentity';
const API =
  'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/user-settings';
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SECRET = /^[A-Za-z0-9_-]{43}$/;
export interface LoginCandidate {
  userId: string;
  idToken: string;
  displayName: string;
  expiresAt: number;
}
interface Intent {
  intentId: string;
  writeSecret: string;
}
/** Capture before either LIFF or the application router consumes the URL. Never a storage grant. */
export function incomingLineHandoff(
  search = globalThis.location?.search ?? '',
): Intent | null {
  let params = new URLSearchParams(search);
  const state = params.get('liff.state');
  if (state) {
    try {
      params = new URL(state, 'https://atlas.invalid/').searchParams;
    } catch {
      return null;
    }
  }
  const intentId = params.get('atlasLineHandoff'),
    writeSecret = params.get('atlasLineWrite');
  return intentId &&
    UUID.test(intentId) &&
    writeSecret &&
    SECRET.test(writeSecret)
    ? { intentId, writeSecret }
    : null;
}
// Import cycle is deliberately avoided: lineIdentity only checks the public marker shape itself.
const incoming = incomingLineHandoff();
let completionMessage: string | null = null;
let incomingIdentity: LineIdentity | null = null;
let sending = false;
export const lineHandoffMessage = () => completionMessage;
export const linePairingCode = (intentId: string) =>
  intentId.replace(/-/g, '').slice(-6).toUpperCase();
export function incomingLineApproval() {
  if (
    !incoming ||
    !incomingIdentity?.idToken() ||
    (typeof window !== 'undefined' && window.top !== window.self)
  )
    return null;
  return { code: linePairingCode(incoming.intentId) };
}
/** Loading a link must never transfer a logged-in visitor's identity to its receiver. */
export function prepareIncomingLineHandoff(identity: LineIdentity | null) {
  if (!incoming) return;
  incomingIdentity = identity;
  completionMessage = incomingLineApproval()
    ? '請核對原來 Atlas 的配對碼；只有這次登入由你本人發起時才確認。'
    : '這次登入未完成，請返回原來的 Atlas 重新登入。';
}
export function cancelIncomingLineHandoff() {
  incomingIdentity = null;
  completionMessage = '已取消傳送 LINE 登入，請返回原來的 Atlas。';
}
class HandoffRequestError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
async function request(body: Record<string, unknown>, signal?: AbortSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, 15000);
  try {
    const response = await fetch(
      import.meta.env?.VITE_SETTINGS_API_URL || API,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        cache: 'no-store',
        credentials: 'omit',
        signal: controller.signal,
      },
    );
    const envelope = (await response.json()) as {
      ok?: boolean;
      error?: string;
      data?: Record<string, unknown>;
    };
    if (!response.ok || !envelope.ok || !envelope.data)
      throw new HandoffRequestError(
        response.status,
        envelope.error || 'LINE 登入交接未完成，請重新嘗試。',
      );
    if (signal?.aborted) throw new Error('已取消 LINE 登入。');
    return envelope.data;
  } catch (error) {
    if (signal?.aborted) throw new Error('已取消 LINE 登入。');
    if (controller.signal.aborted)
      throw new Error('LINE 登入服務連線逾時，請重新嘗試。');
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
export async function completeIncomingLineHandoff() {
  if (!incoming || sending || !incomingLineApproval()) return;
  const idToken = incomingIdentity?.idToken();
  if (!idToken) {
    completionMessage = '這次登入未完成，請返回原來的 Atlas 重新登入。';
    return;
  }
  sending = true;
  try {
    // LINE may open before the original page's begin POST has committed.
    // Retry this same intent briefly, never OAuth or an already used intent.
    for (let attempt = 0; ; attempt++) {
      try {
        await request({
          action: 'line.login.complete',
          ...incoming,
          idToken,
          confirmed: true,
        });
        break;
      } catch (error) {
        if (
          !(error instanceof HandoffRequestError) ||
          error.status !== 404 ||
          attempt >= 10
        )
          throw error;
        await new Promise((resolve) => setTimeout(resolve, 750));
      }
    }
    completionMessage =
      'LINE 驗證完成，請返回原來的 Atlas 視窗或主畫面 App，確認帳號後完成登入。';
    incomingIdentity = null;
  } catch (error) {
    completionMessage =
      error instanceof Error ? error.message : '登入交接未完成。';
  } finally {
    sending = false;
  }
}
export function needsSeparateLineLogin() {
  return (
    /iPhone|iPad|iPod|Android/i.test(globalThis.navigator?.userAgent ?? '') ||
    (globalThis.navigator as Navigator & { standalone?: boolean })
      ?.standalone === true ||
    globalThis.matchMedia?.('(display-mode: standalone)').matches === true
  );
}
const secret = () =>
  btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
/** The receiving proof never leaves this page's RAM or goes into the LINE URL. */
export async function beginSeparateLineLogin(
  signal: AbortSignal,
  onOpenUrl: (url: string) => void = () => {},
): Promise<LoginCandidate> {
  if (signal.aborted) throw new Error('已取消 LINE 登入。');
  const intentId = crypto.randomUUID(),
    readSecret = secret(),
    writeSecret = secret();
  const target = new URL(
    `https://liff.line.me/${import.meta.env?.VITE_LIFF_ID || DEFAULT_LIFF_ID}/atlas/`,
  );
  target.searchParams.set('atlasLineHandoff', intentId);
  target.searchParams.set('atlasLineWrite', writeSecret);
  onOpenUrl(target.href);
  let created = false;
  try {
    const creation = request(
      { action: 'line.login.begin', intentId, readSecret, writeSecret },
      signal,
    );
    // Open within the gesture; iOS/noopener can return null even when it opens.
    // The original App never needs control of the separate browser window.
    try {
      window.open(target.href, '_blank', 'noopener,noreferrer');
    } catch {
      /* The explicit link remains available. */
    }
    const result = await creation;
    created = true;
    const expiresAt = Date.parse(String(result.expiresAt));
    if (
      !Number.isFinite(expiresAt) ||
      expiresAt <= Date.now() ||
      expiresAt > Date.now() + 150000
    )
      throw new Error('登入交接期限不正確。');
    while (!signal.aborted && Date.now() < expiresAt) {
      if (document.visibilityState !== 'hidden') {
        const poll = await request(
          { action: 'line.login.poll', intentId, readSecret },
          signal,
        );
        if (poll.status === 'complete') {
          const candidate = poll.identity as LoginCandidate;
          if (
            !candidate ||
            !/^U[0-9a-f]{32}$/.test(candidate.userId) ||
            typeof candidate.idToken !== 'string' ||
            typeof candidate.displayName !== 'string' ||
            !Number.isFinite(candidate.expiresAt) ||
            candidate.expiresAt <= Date.now() + 60000
          )
            throw new Error('LINE 登入交接驗證失敗。');
          return candidate;
        }
      }
      await new Promise<void>((resolve, reject) => {
        const cancel = () => {
          clearTimeout(timer);
          signal.removeEventListener('abort', cancel);
          reject(new Error('已取消 LINE 登入。'));
        };
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', cancel);
          resolve();
        }, 1500);
        signal.addEventListener('abort', cancel, { once: true });
        if (signal.aborted) cancel();
      });
    }
    throw new Error(
      signal.aborted ? '已取消 LINE 登入。' : '這次登入已逾時，請重新登入。',
    );
  } finally {
    if (created)
      void request({ action: 'line.login.cancel', intentId, readSecret }).catch(
        () => {},
      );
  }
}
