import { describe, expect, it, vi } from 'vitest';
import {
  decryptHandoff,
  encryptHandoff,
  handoffHash,
  lineLoginHandoff,
} from '../../supabase/functions/_shared/lineLoginHandoff';
const ID = '12345678-1234-4234-8234-123456789abc';
const USER = `U${'a'.repeat(32)}`;
const READ = 'r'.repeat(43),
  WRITE = 'w'.repeat(43),
  SERVER = 'server-only-test-secret';
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
      'test-caller',
      verify,
    );
    expect(verify).not.toHaveBeenCalled();
    const parameters = db.rpc.mock.calls[0]![1];
    expect(parameters.read_hash).toBe(await handoffHash(READ));
    expect(parameters.write_hash).toBe(await handoffHash(WRITE));
    expect(JSON.stringify(parameters)).not.toContain(READ);
    expect(JSON.stringify(parameters)).not.toContain(WRITE);
    expect(JSON.stringify(parameters)).not.toContain('test-caller');
  });
  it('verifies LINE before publishing an encrypted identity and never returns the token to the sender', async () => {
    const db = database('complete'),
      verify = vi.fn(async () => USER),
      idToken = token();
    const result = await lineLoginHandoff(
      db,
      {
        action: 'line.login.complete',
        intentId: ID,
        writeSecret: WRITE,
        idToken,
      },
      SERVER,
      '',
      verify,
    );
    expect(verify).toHaveBeenCalledExactlyOnceWith(idToken);
    expect(JSON.stringify(result)).not.toContain(idToken);
    const ciphertext = db.rpc.mock.calls[0]![1].encrypted_token as string;
    expect(ciphertext).not.toContain(idToken);
    const receiver = database('consumed', ciphertext);
    const delivered = await lineLoginHandoff(
      receiver,
      { action: 'line.login.poll', intentId: ID, readSecret: READ },
      SERVER,
      '',
      verify,
    );
    expect(delivered.identity).toMatchObject({
      idToken,
      userId: USER,
      displayName: '測試帳號',
    });
    expect(verify).toHaveBeenCalledOnce();
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
          intentId: ID,
          writeSecret: WRITE,
          idToken: token(),
        },
        SERVER,
        '',
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
            intentId: ID,
            writeSecret: WRITE,
            idToken: token(extra),
          },
          SERVER,
          '',
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
          '',
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
        '',
        vi.fn(),
      ),
    ).rejects.toThrow('登入交接服務尚未就緒');
  });
});
