import { describe, expect, it, vi } from 'vitest';
import {
  decryptHandoff,
  encryptHandoff,
  handoffHash,
  lineLoginHandoff,
  lineNetworkPrefix,
} from '../../supabase/functions/_shared/lineLoginHandoff';
const ID = '12345678-1234-4234-8234-123456789abc';
const USER = `U${'a'.repeat(32)}`;
const READ = 'r'.repeat(43),
  WRITE = 'w'.repeat(43),
  SERVER = 'server-only-test-secret';
const HEADERS = new Headers({ 'cf-connecting-ip': '203.0.113.21' });
const token = (extra = {}) =>
  [
    'header',
    Buffer.from(
      JSON.stringify({
        sub: USER,
        exp: Date.now() / 1000 + 3600,
        name: '測試帳號',
        ...extra,
      }),
    ).toString('base64url'),
    'signature',
  ].join('.');
function database(status = 'pending', ciphertext?: string) {
  return {
    rpc: vi.fn(async (_name: string, _parameters: Record<string, unknown>) => ({
      data: {
        status,
        expiresAt: new Date(Date.now() + 120000).toISOString(),
        ciphertext,
      },
      error: null as { code?: string } | null,
    })),
  };
}
describe('server-only LINE login handoff', () => {
  it.each([
    ['203.0.113.21', '203.0.113.0/24'],
    ['203.0.113.254', '203.0.113.0/24'],
    ['203.0.114.21', '203.0.114.0/24'],
    ['2001:db8:abcd:12::1234', '2001:0db8:abcd:0012::/64'],
    ['2001:0db8:abcd:0012:0:0:0:ffff', '2001:0db8:abcd:0012::/64'],
    ['::ffff:203.0.113.21', '203.0.113.0/24'],
  ])('normalizes trusted %s to %s', (ip, expected) => {
    expect(lineNetworkPrefix(new Headers({ 'cf-connecting-ip': ip }))).toBe(
      expected,
    );
  });
  it.each([
    '',
    'unknown',
    '1.2.3.256',
    '01.2.3.4',
    '1.2.3.4, 5.6.7.8',
    '2001:db8::1%eth0',
    '[2001:db8::1]',
    '2001::db8::1',
    '1.2.3.4/24',
    '2a06:98c0:3600::103',
  ])('fails closed for absent, ambiguous or proxy-shared IP %s', (ip) => {
    expect(
      lineNetworkPrefix(
        new Headers({
          'cf-connecting-ip': ip,
          'x-real-ip': '203.0.113.21',
          'x-forwarded-for': '203.0.113.21',
        }),
      ),
    ).toBeNull();
  });
  it('does not trust forwarding headers when the managed CF header is absent', () => {
    expect(
      lineNetworkPrefix(
        new Headers({
          'x-forwarded-for': '203.0.113.21',
          'x-real-ip': '203.0.113.21',
        }),
      ),
    ).toBeNull();
  });
  it.each(['begin', 'complete'])(
    'rejects %s without a trusted network before RPC or LINE verification',
    async (operation) => {
      const db = database(),
        verify = vi.fn();
      await expect(
        lineLoginHandoff(
          db,
          {
            action: `line.login.${operation}`,
            intentId: ID,
            readSecret: READ,
            writeSecret: WRITE,
            confirmed: true,
            idToken: token(),
          },
          SERVER,
          new Headers(),
          verify,
        ),
      ).rejects.toThrow('同一個網路');
      expect(db.rpc).not.toHaveBeenCalled();
      expect(verify).not.toHaveBeenCalled();
    },
  );
  it('returns a clear error for SQL-enforced network mismatch', async () => {
    const db = database('network_mismatch');
    await expect(
      lineLoginHandoff(
        db,
        {
          action: 'line.login.complete',
          intentId: ID,
          writeSecret: WRITE,
          confirmed: true,
          idToken: token(),
        },
        SERVER,
        HEADERS,
        async () => USER,
      ),
    ).rejects.toThrow('請在同一個網路下完成登入');
  });
  it('never returns a confirmation code or ciphertext to a no-code poll', async () => {
    const db = database('code_required');
    const result = await lineLoginHandoff(
      db,
      { action: 'line.login.poll', intentId: ID, readSecret: READ },
      SERVER,
      new Headers(),
      vi.fn(),
    );
    expect(result).toEqual({
      status: 'code_required',
      expiresAt: expect.any(String),
      attemptsRemaining: undefined,
    });
    expect(result).not.toHaveProperty('code');
    expect(result).not.toHaveProperty('identity');
    expect(db.rpc.mock.calls[0]![1]).not.toHaveProperty('confirmation_hash');
  });
  it('fails closed even if an older RPC erroneously returns a payload without a confirmation hash', async () => {
    const encrypted = await encryptHandoff(
      { userId: USER, idToken: token(), expiresAt: Date.now() + 3600000 },
      ID,
      SERVER,
    );
    await expect(
      lineLoginHandoff(
        database('consumed', encrypted),
        {
          action: 'line.login.poll',
          intentId: ID,
          readSecret: READ,
        },
        SERVER,
        HEADERS,
        vi.fn(),
      ),
    ).rejects.toThrow('確認碼保護');
  });
  it.each(['12345', '1234567', 'abcdef', 123456])(
    'rejects malformed reverse code %s without calling the RPC',
    async (code) => {
      const db = database();
      await expect(
        lineLoginHandoff(
          db,
          { action: 'line.login.poll', intentId: ID, readSecret: READ, code },
          SERVER,
          HEADERS,
          vi.fn(),
        ),
      ).rejects.toThrow('六位確認碼');
      expect(db.rpc).not.toHaveBeenCalled();
    },
  );
  it('reports wrong-code remaining attempts without an identity and rejects lockout', async () => {
    const db = database('wrong_code');
    const body = {
      action: 'line.login.poll',
      intentId: ID,
      readSecret: READ,
      code: '000123',
    };
    expect(
      await lineLoginHandoff(db, body, SERVER, HEADERS, vi.fn()),
    ).not.toHaveProperty('identity');
    expect(db.rpc.mock.calls[0]![1].confirmation_hash).toBe(
      await handoffHash(`${SERVER}:atlas-line-code-v1:${ID}:000123`),
    );
    await expect(
      lineLoginHandoff(database('locked'), body, SERVER, HEADERS, vi.fn()),
    ).rejects.toThrow('五次');
  });
  it('rejects unconfirmed handoffs before provider verification or database access', async () => {
    const db = database(),
      verify = vi.fn();
    await expect(
      lineLoginHandoff(
        db,
        {
          action: 'line.login.complete',
          intentId: ID,
          writeSecret: WRITE,
          idToken: token(),
        },
        SERVER,
        HEADERS,
        verify,
      ),
    ).rejects.toThrow('配對碼');
    expect(verify).not.toHaveBeenCalled();
    expect(db.rpc).not.toHaveBeenCalled();
  });
  it('stores only hashed proofs and a server-salted caller bucket on explicit begin', async () => {
    const db = database(),
      verify = vi.fn();
    await lineLoginHandoff(
      db,
      {
        action: 'line.login.begin',
        intentId: ID,
        readSecret: READ,
        writeSecret: WRITE,
      },
      SERVER,
      HEADERS,
      verify,
    );
    expect(verify).not.toHaveBeenCalled();
    const parameters = db.rpc.mock.calls[0]![1];
    expect(parameters.read_hash).toBe(await handoffHash(READ));
    expect(parameters.write_hash).toBe(await handoffHash(WRITE));
    expect(JSON.stringify(parameters)).not.toContain(READ);
    expect(JSON.stringify(parameters)).not.toContain(WRITE);
    expect(JSON.stringify(parameters)).not.toContain('203.0.113');
    expect(parameters.network_hash).toBe(
      await handoffHash(`${SERVER}:atlas-line-network-v1:203.0.113.0/24`),
    );
  });
  it('verifies LINE before publishing an encrypted identity and never returns the token to the sender', async () => {
    const db = database('complete'),
      verify = vi.fn(async () => USER),
      idToken = token();
    const result = await lineLoginHandoff(
      db,
      {
        action: 'line.login.complete',
        confirmed: true,
        intentId: ID,
        writeSecret: WRITE,
        idToken,
      },
      SERVER,
      HEADERS,
      verify,
    );
    expect(verify).toHaveBeenCalledExactlyOnceWith(idToken);
    expect(JSON.stringify(result)).not.toContain(idToken);
    expect(result.code).toMatch(/^\d{6}$/);
    const completion = db.rpc.mock.calls[0]![1];
    expect(completion.confirmation_hash).toBe(
      await handoffHash(`${SERVER}:atlas-line-code-v1:${ID}:${result.code}`),
    );
    expect(completion).not.toHaveProperty('code');
    expect(completion.network_hash).toBe(
      await handoffHash(`${SERVER}:atlas-line-network-v1:203.0.113.0/24`),
    );
    const ciphertext = db.rpc.mock.calls[0]![1].encrypted_token as string;
    expect(ciphertext).not.toContain(idToken);
    const receiver = database('consumed', ciphertext);
    const delivered = await lineLoginHandoff(
      receiver,
      {
        action: 'line.login.poll',
        intentId: ID,
        readSecret: READ,
        code: result.code,
      },
      SERVER,
      HEADERS,
      verify,
    );
    expect(delivered.identity).toMatchObject({
      idToken,
      userId: USER,
      displayName: '測試帳號',
    });
    expect(verify).toHaveBeenCalledOnce();
    expect(delivered).not.toHaveProperty('code');
    expect(receiver.rpc.mock.calls[0]![1].confirmation_hash).toBe(
      completion.confirmation_hash,
    );
    expect(receiver.rpc.mock.calls[0]![1]).not.toHaveProperty('write_hash');
  });
  it('rejects invalid provider tokens before any database write', async () => {
    const db = database(),
      verify = vi.fn(async () => {
        throw new Error('provider rejected');
      });
    await expect(
      lineLoginHandoff(
        db,
        {
          action: 'line.login.complete',
          confirmed: true,
          intentId: ID,
          writeSecret: WRITE,
          idToken: token(),
        },
        SERVER,
        HEADERS,
        verify,
      ),
    ).rejects.toThrow('provider rejected');
    expect(db.rpc).not.toHaveBeenCalled();
  });
  it.each([{ exp: Date.now() / 1000 - 1 }, { sub: `U${'b'.repeat(32)}` }])(
    'rejects expired or mismatched verified claims',
    async (extra) => {
      const db = database();
      await expect(
        lineLoginHandoff(
          db,
          {
            action: 'line.login.complete',
            confirmed: true,
            intentId: ID,
            writeSecret: WRITE,
            idToken: token(extra),
          },
          SERVER,
          HEADERS,
          async () => USER,
        ),
      ).rejects.toThrow();
      expect(db.rpc).not.toHaveBeenCalled();
    },
  );
  it('binds ciphertext to both the server key and the intent, and rejects tampering', async () => {
    const encrypted = await encryptHandoff({ idToken: token() }, ID, SERVER);
    await expect(
      decryptHandoff(encrypted, ID + 'different', SERVER),
    ).rejects.toThrow();
    await expect(
      decryptHandoff(encrypted, ID, SERVER + 'rotated'),
    ).rejects.toThrow();
    await expect(
      decryptHandoff(encrypted.slice(0, -8) + 'AAAAAAAA', ID, SERVER),
    ).rejects.toThrow();
  });
  it.each(['missing', 'used', 'busy'])(
    'fails closed for %s intents',
    async (status) => {
      await expect(
        lineLoginHandoff(
          database(status),
          { action: 'line.login.poll', intentId: ID, readSecret: READ },
          SERVER,
          HEADERS,
          vi.fn(),
        ),
      ).rejects.toThrow();
    },
  );
  it('reports a rollout prerequisite without authorizing or silently redirecting', async () => {
    const db = database();
    db.rpc.mockResolvedValue({
      data: { status: '', expiresAt: '', ciphertext: undefined },
      error: { code: 'PGRST202' },
    });
    await expect(
      lineLoginHandoff(
        db,
        { action: 'line.login.poll', intentId: ID, readSecret: READ },
        SERVER,
        HEADERS,
        vi.fn(),
      ),
    ).rejects.toThrow('登入交接服務尚未就緒');
  });
});
