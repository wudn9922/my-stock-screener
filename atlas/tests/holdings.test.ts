import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  checkXlsx,
  detectMapping,
  makeTable,
  parseNumber,
  parseText,
  previewErrors,
  previewHoldings,
  previewSnapshot,
  previewTransactions,
  resolveHoldingSymbol,
  validatePreviewRow,
  type ImportSymbol,
} from '../src/holdings/import';
import {
  emptyHoldings,
  replaceAccount,
  summarizeHoldings,
  valueHolding,
  validateHoldingsDocument,
  validateHoldingAccount,
  type Holding,
  type HoldingAccount,
} from '../src/holdings/model';

const tw: HoldingAccount = {
  id: '11111111-1111-4111-8111-111111111111',
  name: '測試券商',
  kind: 'tw-broker',
  currency: 'TWD',
  broker: 'kgi',
};
const us: HoldingAccount = { ...tw, kind: 'us-broker', currency: 'USD', broker: 'firstrade' };
const directory: ImportSymbol[] = [
  { symbol: '2330.TW', market: 'TW', name: '台積電' },
  { symbol: '6488.TWO', market: 'TW', name: '環球晶' },
  { symbol: 'AAPL', market: 'US', name: 'Apple' },
  { symbol: 'NVDA', market: 'US', name: 'NVIDIA' },
];
const table = async (text: string) => makeTable(parseText(text));
const fixture = (path: string) => readFileSync(new URL(`./fixtures/holdings/${path}`, import.meta.url), 'utf8');
const holding = (overrides: Partial<Holding> = {}): Holding => ({
  id: '22222222-2222-4222-8222-222222222222',
  accountId: tw.id,
  symbol: '2330.TW',
  market: 'TW',
  quantity: '1000',
  averageCost: '80',
  costCurrency: 'TWD',
  source: 'paste',
  updatedAt: '2026-10-28T01:00:00Z',
  ...overrides,
});

describe('private browser import', () => {
  it.each([
    '代號\t庫存股數\t成本均價\n２３３０ 台積電\t１，５００\t８００',
    '股票代號  股數  均價\n2330  1,500  800',
    'Symbol,Quantity,Avg Cost\n2330,1500,800',
    '證券代號,股數,平均成本\n2330,"1,500",800',
  ])('handles paste delimiters and fullwidth digits', async (text) => {
    const input = await table(text),
      mapping = detectMapping(input)!;
    const result = previewSnapshot(input, mapping, tw, directory);
    expect(result.rows[0]).toMatchObject({ symbol: '2330.TW', quantity: '1500', averageCost: '800', errors: [] });
  });
  it('reads quoted comma numbers, escaped quotes and embedded CSV newlines without shifting cells', async () => {
    const parsed = parseText('代號,股數,均價,備註\n2330,"1,500","1,200.5","test ""quote""\nline"');
    expect(parsed[1]).toEqual(['2330', '1,500', '1,200.5', 'test "quote"\nline']);
    const input = await makeTable(parsed);
    expect(previewSnapshot(input, detectMapping(input)!, tw, directory).rows[0]!.averageCost).toBe('1200.5');
  });
  it('does not guess an ambiguous unquoted thousands separator', async () => {
    const input = await table('代號,股數,均價\n2330,1,234,567');
    expect(previewSnapshot(input, detectMapping(input)!, tw, directory).rows[0]!.errors.join()).toContain('欄數');
  });
  it('rejoins unambiguous unquoted thousands in comma-separated text', async () => {
    const input = await table('代號,股數,均價\n2330,1,500,80');
    expect(previewSnapshot(input, detectMapping(input)!, tw, directory).rows[0]).toMatchObject({
      quantity: '1500',
      averageCost: '80',
      errors: [],
    });
  });
  it('derives average from total cost in shares and lots', async () => {
    const input = await table('商品代號\t庫存張數\t付出成本\n6488\t2\t2000000');
    expect(previewSnapshot(input, detectMapping(input)!, tw, directory).rows[0]).toMatchObject({
      symbol: '6488.TWO',
      quantity: '2000',
      averageCost: '1000',
      errors: [],
    });
  });
  it('uses account mapping only when header fingerprint matches', async () => {
    const input = await table('代碼,持有,單價\n2330,5,100');
    expect(detectMapping(input)).toBeNull();
    const saved = { fingerprint: input.fingerprint, symbol: 0, quantity: 1, average: 2, unit: 'shares' as const };
    expect(detectMapping(input, saved)).toEqual(saved);
    expect(detectMapping(await table('代碼,持有,單價,新欄\n2330,5,100,x'), saved)).toBeNull();
  });
  it.each(['2330', '2330 台積電', '2330.TW'])('resolves TW symbols using the supplied universe: %s', (text) =>
    expect(resolveHoldingSymbol(text, tw, directory)).toBe('2330.TW'),
  );
  it('does not invent the TWSE suffix or accept a mismatched exchange', () => {
    expect(resolveHoldingSymbol('6488', tw, directory)).toBe('6488.TWO');
    expect(resolveHoldingSymbol('6488.TW', tw, directory)).toBeNull();
    expect(resolveHoldingSymbol('9999', tw, directory)).toBeNull();
  });
  it('shows invalid rows, accepts corrections/skips and rejects duplicates', async () => {
    const input = await table('代號,股數,均價\n9999,broken,10\n2330,1,80');
    const rows = previewSnapshot(input, detectMapping(input)!, tw, directory).rows;
    expect(previewErrors(rows)).toBe(true);
    expect(() => previewHoldings(rows, tw, 'paste')).toThrow();
    rows[0] = { ...rows[0]!, skip: true };
    expect(previewHoldings(rows, tw, 'paste')).toHaveLength(1);
    rows[0] = validatePreviewRow(
      { ...rows[0]!, skip: false, symbol: '6488', quantity: '0.5', averageCost: '100' },
      tw,
      directory,
    );
    expect(previewHoldings(rows, tw, 'paste')).toHaveLength(2);
    rows[0] = { ...rows[0]!, symbol: '2330.TW' };
    expect(previewErrors(rows)).toBe(true);
  });
  it.each(['NaN', 'Infinity', '-1', '12,34', '=1+1', '1000000000000000', '0.1234567890123456789'])(
    'rejects unsafe numeric input without echoing it',
    (value) => {
      expect(() => parseNumber(value, true)).toThrow();
      try {
        parseNumber(value, true);
      } catch (error) {
        expect((error as Error).message).not.toContain(value);
      }
    },
  );
  it('keeps fractional crypto values without floating-point loss', () => {
    expect(parseNumber('0.123456789012345678')).toBe('0.123456789012345678');
    expect(parseNumber('1e-8')).toBe('0.00000001');
    expect(
      resolveHoldingSymbol('BTC/USDT', { ...tw, kind: 'crypto-exchange', currency: 'USDT', broker: 'mexc' }, []),
    ).toBe('BTC/USDT');
  });
  it('inspects the fake KGI XLSX and rejects malformed/oversized ZIP metadata', () => {
    const bytes = readFileSync(new URL('./fixtures/holdings/kgi-inventory.xlsx', import.meta.url));
    expect(() => checkXlsx(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))).not.toThrow();
    const corrupted = bytes.slice();
    const signature = Buffer.from([0x50, 0x4b, 0x01, 0x02]);
    const offset = corrupted.indexOf(signature);
    corrupted.writeUInt32LE(40 * 1024 * 1024, offset + 24);
    expect(() =>
      checkXlsx(corrupted.buffer.slice(corrupted.byteOffset, corrupted.byteOffset + corrupted.byteLength)),
    ).toThrow();
    expect(() => checkXlsx(new ArrayBuffer(20))).toThrow();
  });
});

describe('Firstrade average-cost ledger', () => {
  it('uses the export columns, net purchase costs, partial sells, full closes and visible skipped dividends', async () => {
    const input = await table(fixture('firstrade-transactions.csv'));
    expect(input.transactions).toBe(true);
    const preview = previewTransactions(input, us, directory);
    expect(preview.rows).toEqual([
      { line: 4, symbol: 'AAPL', quantity: '15', averageCost: '110.1', skip: false, errors: [] },
    ]);
    expect(preview.skipped).toEqual([{ line: 5, reason: expect.stringContaining('非一般') }]);
  });
  it('sorts trades by date, preserves average through sales and includes fees when net amount is absent', async () => {
    const input = await table(
      'Symbol,Quantity,Price,Action,TradeDate,Commission,Fee\nAAPL,1,150,SELL,2026-01-05,0,0\nAAPL,2,100,BUY,2026-01-02,1,0.5',
    );
    expect(previewTransactions(input, us, directory).rows[0]).toMatchObject({
      quantity: '1',
      averageCost: '100.75',
      errors: [],
    });
  });
  it.each([
    'AAPL,-20,120,SELL,2026-01-05',
    'AAPL,2,100,BUY,2026-02-30',
    'AAPL,2,100,BUY TO COVER,2026-01-05',
    'AAPL,-2,100,BUY,2026-01-05',
  ])('blocks incomplete/invalid history: %s', (row) => {
    return table('Symbol,Quantity,Price,Action,TradeDate\n' + row).then((input) =>
      expect(previewErrors(previewTransactions(input, us, directory).rows)).toBe(true),
    );
  });
  it('retains splits and other non-trade rows in skipped review', async () => {
    const input = await table(
      'Symbol,Quantity,Price,Action,TradeDate\nAAPL,1,100,BUY,2026-01-02\nAAPL,1,0,Other STOCK SPLIT,2026-01-05',
    );
    const preview = previewTransactions(input, us, directory);
    expect(preview.skipped).toHaveLength(1);
    expect(preview.warnings).toHaveLength(1);
  });
});

describe('replacement, valuation and conversion', () => {
  it('replaces exactly one account atomically instead of accumulating, preserving other accounts', () => {
    const other = { ...tw, id: '33333333-3333-4333-8333-333333333333', name: '其他' };
    const base = replaceAccount(replaceAccount(emptyHoldings(), tw, [holding()]), other, []);
    const replaced = replaceAccount(base, tw, [holding({ quantity: '2000' })]);
    expect(replaced.accounts).toHaveLength(2);
    expect(replaced.accounts.find((a) => a.account.id === tw.id)!.holdings[0]!.quantity).toBe('2000');
    expect(base.accounts.find((a) => a.account.id === tw.id)!.holdings[0]!.quantity).toBe('1000');
    expect(replaceAccount(replaced, tw, []).accounts.find((a) => a.account.id === tw.id)!.holdings).toEqual([]);
    expect(() => replaceAccount(base, tw, [holding({ quantity: '-1' })])).toThrow();
  });
  it('rejects oversized accounts, forged markets and arbitrary extra metadata', () => {
    expect(() => validateHoldingAccount({ ...tw, rawFile: 'sensitive' })).toThrow();
    expect(() => replaceAccount(emptyHoldings(), tw, [holding({ market: 'US' })])).toThrow();
    expect(() =>
      validateHoldingsDocument({
        version: 1,
        accounts: Array.from({ length: 31 }, () => ({ account: tw, holdings: [], revision: 1 })),
      }),
    ).toThrow();
  });
  it('calculates market value, unrealized return and daily P&L', () => {
    expect(valueHolding(holding(), { price: '100', previous: '98', asOf: '2026-10-28', source: 'test' })).toEqual({
      value: '100000',
      cost: '80000',
      profit: '20000',
      percent: '25',
      today: '2000',
      fee: '0',
    });
    expect(valueHolding(holding(), undefined).profit).toBeNull();
    expect(
      valueHolding(holding({ averageCost: '0' }), { price: '100', previous: null, asOf: '', source: '' }).percent,
    ).toBeNull();
  });
  it('uses fractional quantities precisely and applies the configured TW stock/ETF selling costs only when enabled', () => {
    const quote = { price: '100', previous: '90', asOf: '', source: '' };
    expect(valueHolding(holding({ quantity: '0.1' }), quote).value).toBe('10');
    expect(valueHolding(holding(), quote, { enabled: false, discount: '0.28', etfs: new Set() }).profit).toBe('20000');
    expect(valueHolding(holding(), quote, { enabled: true, discount: '0.28', etfs: new Set() }).fee).toBe('339.9');
    expect(valueHolding(holding(), quote, { enabled: true, discount: '0.28', etfs: new Set(['2330.TW']) }).fee).toBe(
      '139.9',
    );
    expect(valueHolding(holding({ market: 'US' }), quote, { enabled: true, discount: '1', etfs: new Set() }).fee).toBe(
      '0',
    );
  });
  it('converts USD at the dated rate, does not assume a USDT peg and marks missing quotes as partial', () => {
    const quotes = { price: '100', previous: '90', asOf: '', source: '' };
    const rows = [holding(), holding({ costCurrency: 'USD' })].map((h) => ({
      holding: h,
      result: valueHolding(h, quotes),
    }));
    const rates = [{ currency: 'USD' as const, twd: '32', asOf: '2026-10-27' }];
    expect(summarizeHoldings(rows, rates)).toMatchObject({ twd: '3300000', profitTwd: '660000', partial: false });
    expect(
      summarizeHoldings(
        [...rows, { holding: holding({ costCurrency: 'USDT' }), result: valueHolding(holding(), quotes) }],
        rates,
      ).partial,
    ).toBe(true);
    expect(summarizeHoldings([{ holding: holding(), result: valueHolding(holding(), undefined) }], rates).partial).toBe(
      true,
    );
  });
});
