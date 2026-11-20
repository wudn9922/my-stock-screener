import { describe, expect, it } from 'vitest';
import {
  base64UrlDecode as d,
  base64UrlEncode,
  encryptPushPayload,
  importP256Private,
  importP256Public,
  loadVapidKeys,
  vapidAuthorization,
  vapidJwt,
} from '../../supabase/functions/user-settings/webPushCrypto';

/** RFC 8291 Appendix A ("When I grow up, I want to be a watermelon"). */
const RFC = {
  plaintext: 'V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24',
  asPrivate: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw',
  asPublic: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
  uaPrivate: 'q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94',
  uaPublic: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
  salt: 'DGv6ra1nlYgDCS1FRnbzlw',
  auth: 'BTBZMqHH6r4Tts7J_aSIgg',
  body: 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN',
};

const ab = (bytes: Uint8Array) => bytes.slice().buffer as ArrayBuffer;

/** What the browser does: derive with the user-agent private key and decrypt the single record. */
async function decryptAsUserAgent(body: Uint8Array, uaPrivate: Uint8Array, uaPublic: Uint8Array, auth: Uint8Array) {
  const salt = body.slice(0, 16);
  const idlen = body[20];
  const asPublic = body.slice(21, 21 + idlen);
  const ciphertext = body.slice(21 + idlen);
  const uaKey = await importP256Private(uaPrivate, uaPublic, 'ECDH');
  const secret = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: await importP256Public(asPublic) }, uaKey, 256));
  const hkdf = async (s: Uint8Array, ikm: Uint8Array, info: Uint8Array, len: number) =>
    new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: ab(s), info: ab(info) },
      await crypto.subtle.importKey('raw', ab(ikm), 'HKDF', false, ['deriveBits']), len * 8));
  const te = new TextEncoder();
  const info = new Uint8Array([...te.encode('WebPush: info\0'), ...uaPublic, ...asPublic]);
  const ikm = await hkdf(auth, secret, info, 32);
  const cek = await hkdf(salt, ikm, te.encode('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, te.encode('Content-Encoding: nonce\0'), 12);
  const key = await crypto.subtle.importKey('raw', ab(cek), 'AES-GCM', false, ['decrypt']);
  const plain = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: ab(nonce) }, key, ab(ciphertext)));
  expect(plain.at(-1)).toBe(2);
  return plain.slice(0, -1);
}

describe('RFC 8291 message encryption', () => {
  it('reproduces the RFC 8291 Appendix A body byte for byte', async () => {
    const body = await encryptPushPayload(d(RFC.plaintext), d(RFC.uaPublic), d(RFC.auth), {
      serverPrivate: d(RFC.asPrivate), serverPublic: d(RFC.asPublic), salt: d(RFC.salt),
    });
    expect(base64UrlEncode(body)).toBe(RFC.body);
  });

  it('uses a fresh server key and salt per message that the user agent can decrypt', async () => {
    const message = new TextEncoder().encode(JSON.stringify({ title: '🚨 2330.TW 台積電 向上突破 MA20' }));
    const first = await encryptPushPayload(message, d(RFC.uaPublic), d(RFC.auth));
    const second = await encryptPushPayload(message, d(RFC.uaPublic), d(RFC.auth));
    expect(base64UrlEncode(first.slice(0, 16))).not.toBe(base64UrlEncode(second.slice(0, 16)));
    expect(new DataView(first.buffer).getUint32(16)).toBe(4096);
    expect(first[20]).toBe(65);
    const plain = await decryptAsUserAgent(first, d(RFC.uaPrivate), d(RFC.uaPublic), d(RFC.auth));
    expect(new TextDecoder().decode(plain)).toContain('台積電');
  });

  it('rejects malformed subscription keys and oversized payloads', async () => {
    await expect(encryptPushPayload(new Uint8Array(4), d(RFC.uaPublic).slice(1), d(RFC.auth))).rejects.toThrow();
    await expect(encryptPushPayload(new Uint8Array(4), d(RFC.uaPublic), d(RFC.auth).slice(1))).rejects.toThrow();
    await expect(encryptPushPayload(new Uint8Array(4080), d(RFC.uaPublic), d(RFC.auth))).rejects.toThrow('Payload too large');
    expect(() => d('not base64!')).toThrow();
  });
});

describe('RFC 8292 VAPID', () => {
  it('signs a short-lived ES256 JWT for the push service origin that verifies with the public key', async () => {
    const keys = await loadVapidKeys(RFC.asPublic, RFC.asPrivate, 'mailto:alerts@example.invalid');
    const now = 1_800_000_000;
    const jwt = await vapidJwt(keys, 'https://fcm.googleapis.com/fcm/send/abc:def', now);
    const [header, claims, signature] = jwt.split('.');
    expect(JSON.parse(new TextDecoder().decode(d(header)))).toEqual({ typ: 'JWT', alg: 'ES256' });
    expect(JSON.parse(new TextDecoder().decode(d(claims)))).toEqual({ aud: 'https://fcm.googleapis.com', exp: now + 12 * 3600, sub: 'mailto:alerts@example.invalid' });
    expect(d(signature)).toHaveLength(64);
    const verified = await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, await importP256Public(d(RFC.asPublic), 'ECDSA'),
      ab(d(signature)), ab(new TextEncoder().encode(`${header}.${claims}`)));
    expect(verified).toBe(true);
    expect(vapidAuthorization(jwt, keys)).toBe(`vapid t=${jwt}, k=${RFC.asPublic}`);
  });

  it('refuses missing keys, a mismatched key pair or a subject that is not mailto:/https:', async () => {
    await expect(loadVapidKeys(undefined, RFC.asPrivate, 'mailto:a@b.c')).rejects.toThrow('not configured');
    await expect(loadVapidKeys(RFC.asPublic, RFC.asPrivate, 'alerts@example.invalid')).rejects.toThrow('subject');
    await expect(loadVapidKeys(RFC.uaPublic, RFC.asPrivate, 'https://example.invalid')).rejects.toThrow();
  });
});
