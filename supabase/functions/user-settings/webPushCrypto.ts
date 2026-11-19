/**
 * Standard Web Push with WebCrypto only (runs on Supabase Edge/Deno and in Node tests):
 * message encryption RFC 8291 (aes128gcm, RFC 8188) and VAPID RFC 8292 (ES256 JWT).
 * No third-party push account; the browser's own push service delivers the message.
 */
const encoder = new TextEncoder();
const ab = (bytes: Uint8Array) => bytes.slice().buffer as ArrayBuffer;

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function base64UrlDecode(value: string): Uint8Array {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]*={0,2}$/.test(value)) throw new Error('Invalid base64url');
  const clean = value.replace(/=+$/, '').replace(/-/g, '+').replace(/_/g, '/');
  if (clean.length % 4 === 1) throw new Error('Invalid base64url');
  const binary = atob(clean + '='.repeat((4 - (clean.length % 4)) % 4));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

export function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const result = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

async function hkdf(salt: Uint8Array, ikm: Uint8Array, info: Uint8Array, length: number) {
  const key = await crypto.subtle.importKey('raw', ab(ikm), 'HKDF', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: ab(salt), info: ab(info) }, key, length * 8);
  return new Uint8Array(bits);
}

/** A P-256 public key in uncompressed form (65 bytes, 0x04 || X || Y) as WebCrypto ECDH key. */
export async function importP256Public(raw: Uint8Array, usage: 'ECDH' | 'ECDSA' = 'ECDH') {
  if (raw.length !== 65 || raw[0] !== 4) throw new Error('Invalid P-256 public key');
  const algorithm = usage === 'ECDH' ? { name: 'ECDH', namedCurve: 'P-256' } : { name: 'ECDSA', namedCurve: 'P-256' };
  return crypto.subtle.importKey('raw', ab(raw), algorithm, true, usage === 'ECDH' ? [] : ['verify']);
}

/** Private P-256 key from its 32-byte scalar plus the matching public point (the web-push key format). */
export async function importP256Private(privateRaw: Uint8Array, publicRaw: Uint8Array, usage: 'ECDH' | 'ECDSA') {
  if (privateRaw.length !== 32 || publicRaw.length !== 65 || publicRaw[0] !== 4) throw new Error('Invalid P-256 key pair');
  const jwk = {
    kty: 'EC', crv: 'P-256', ext: true,
    d: base64UrlEncode(privateRaw),
    x: base64UrlEncode(publicRaw.slice(1, 33)),
    y: base64UrlEncode(publicRaw.slice(33, 65)),
  };
  return usage === 'ECDH'
    ? crypto.subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits'])
    : crypto.subtle.importKey('jwk', jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
}

export interface EncryptOptions {
  /** Test vectors only: a fixed application-server key pair and salt. Production always uses fresh random values. */
  serverPrivate?: Uint8Array;
  serverPublic?: Uint8Array;
  salt?: Uint8Array;
  recordSize?: number;
}

/**
 * Encrypt one push message for a subscription (single aes128gcm record).
 * `uaPublic` is the subscription's p256dh key, `authSecret` its 16-byte auth secret.
 */
export async function encryptPushPayload(plaintext: Uint8Array, uaPublic: Uint8Array, authSecret: Uint8Array, options: EncryptOptions = {}) {
  if (authSecret.length !== 16) throw new Error('Invalid auth secret');
  const recordSize = options.recordSize ?? 4096;
  // 16-byte tag + 1 delimiter byte must fit with the plaintext in one record.
  if (plaintext.length + 17 > recordSize) throw new Error('Payload too large');
  const uaKey = await importP256Public(uaPublic, 'ECDH');
  let serverPublic: Uint8Array;
  let serverPrivateKey: CryptoKey;
  if (options.serverPrivate && options.serverPublic) {
    serverPublic = options.serverPublic;
    serverPrivateKey = await importP256Private(options.serverPrivate, options.serverPublic, 'ECDH');
  } else {
    const pair = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']) as CryptoKeyPair;
    serverPublic = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
    serverPrivateKey = pair.privateKey;
  }
  const salt = options.salt ?? crypto.getRandomValues(new Uint8Array(16));
  if (salt.length !== 16) throw new Error('Invalid salt');
  const ecdhSecret = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, serverPrivateKey, 256));
  const keyInfo = concatBytes(encoder.encode('WebPush: info\0'), uaPublic, serverPublic);
  const ikm = await hkdf(authSecret, ecdhSecret, keyInfo, 32);
  const cek = await hkdf(salt, ikm, encoder.encode('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, encoder.encode('Content-Encoding: nonce\0'), 12);
  const key = await crypto.subtle.importKey('raw', ab(cek), 'AES-GCM', false, ['encrypt']);
  // 0x02 marks the last (and only) record; no extra padding.
  const padded = concatBytes(plaintext, new Uint8Array([2]));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: ab(nonce), tagLength: 128 }, key, ab(padded)));
  const header = new Uint8Array(21 + serverPublic.length);
  header.set(salt, 0);
  new DataView(header.buffer).setUint32(16, recordSize);
  header[20] = serverPublic.length;
  header.set(serverPublic, 21);
  return concatBytes(header, ciphertext);
}

export interface VapidKeys {
  publicKey: Uint8Array;
  privateKey: CryptoKey;
  /** base64url of the uncompressed public key, sent as `k=`. */
  publicKeyText: string;
  subject: string;
}

/** Keys are the base64url strings printed by scripts/generate_vapid_keys.py. */
export async function loadVapidKeys(publicText: string | undefined, privateText: string | undefined, subject: string | undefined): Promise<VapidKeys> {
  if (!publicText || !privateText || !subject) throw new Error('VAPID keys are not configured');
  if (!/^(mailto:\S+@\S+|https:\/\/\S+)$/.test(subject)) throw new Error('VAPID subject must be mailto: or https:');
  const publicKey = base64UrlDecode(publicText.trim());
  const privateKey = await importP256Private(base64UrlDecode(privateText.trim()), publicKey, 'ECDSA');
  return { publicKey, privateKey, publicKeyText: base64UrlEncode(publicKey), subject };
}

/** RFC 8292 JWT for one push service origin; ES256 signatures from WebCrypto are already raw r||s. */
export async function vapidJwt(keys: VapidKeys, endpoint: string, nowSeconds = Math.floor(Date.now() / 1000)) {
  const audience = new URL(endpoint).origin;
  const header = base64UrlEncode(encoder.encode(JSON.stringify({ typ: 'JWT', alg: 'ES256' })));
  // Below the 24 h maximum; push services reject tokens that live too long.
  const claims = base64UrlEncode(encoder.encode(JSON.stringify({ aud: audience, exp: nowSeconds + 12 * 3600, sub: keys.subject })));
  const unsigned = `${header}.${claims}`;
  const signature = new Uint8Array(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, keys.privateKey, ab(encoder.encode(unsigned))));
  return `${unsigned}.${base64UrlEncode(signature)}`;
}

export function vapidAuthorization(jwt: string, keys: VapidKeys) {
  return `vapid t=${jwt}, k=${keys.publicKeyText}`;
}
