import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { summarizeHoldings, valueHolding, type Holding, type HoldingAccount } from '../src/holdings/model';
import {
  defaultRules,
  dustThreshold,
  emptyVisibility,
  forgetAccount,
  hiddenReason,
  hide,
  holdingKey,
  setRules,
  unhide,
  validateVisibility,
} from '../src/holdings/visibility';
import { HiddenHoldings } from '../src/holdings/HoldingsVisibility';

const mexc: HoldingAccount = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'MEXC',
  kind: 'crypto-exchange',
  currency: 'USDT',
  broker: 'mexc',
};
const tw: HoldingAccount = {
  id: '33333333-3333-4333-8333-333333333333',
  name: 'TW',
  kind: 'tw-broker',
  currency: 'TWD',
  broker: 'kgi',
};
const holding = (account: HoldingAccount, symbol: string, quantity: string, id: string): Holding => ({
  id,
  accountId: account.id,
  symbol,
  market: account.kind === 'tw-broker' ? 'TW' : 'CRYPTO',
  quantity,
  averageCost: '1',
  costCurrency: account.currency,
  source: 'api',
  updatedAt: '2026-11-07T00:00:00Z',
});
const btc = holding(mexc, 'BTC/USDT', '0.1', '44444444-4444-4444-8444-444444444441');
const dust = holding(mexc, 'PEPE/USDT', '3', '44444444-4444-4444-8444-444444444442');
const usdt = holding(mexc, 'USDT/USDT', '250', '44444444-4444-4444-8444-444444444443');
const noPrice = holding(mexc, 'RARE/USDT', '0.0001', '44444444-4444-4444-8444-444444444444');
const tsmc = holding(tw, '2330.TW', '5', '44444444-4444-4444-8444-444444444445');
const quote = (price: string) => ({ price, previous: null, asOf: '2026-11-07T00:00:00Z', source: 'test' });
const prices: Record<string, string> = { 'BTC/USDT': '60000', 'PEPE/USDT': '0.001', 'USDT/USDT': '1', '2330.TW': '1' };
const rows = [btc, dust, usdt, noPrice, tsmc].map((h) => ({
  holding: h,
  account: h.accountId === mexc.id ? mexc : tw,
  result: valueHolding(h, prices[h.symbol] ? quote(prices[h.symbol]!) : undefined),
}));
const reasons = (preferences = emptyVisibility()) =>
  Object.fromEntries(rows.map((r) => [r.holding.symbol, hiddenReason(r.holding, r.account, r.result.value, preferences)]));

describe('holdings visibility rules', () => {
  it('shows everything by default', () => {
    expect(Object.values(reasons())).toEqual([null, null, null, null, null]);
  });
  it('hides dust strictly below the threshold in the account currency, never a holding without a price', () => {
    const preferences = setRules(emptyVisibility(), mexc.id, { dust: { enabled: true, threshold: '10' }, hideUsdt: false });
    expect(reasons(preferences)).toEqual({
      'BTC/USDT': null,
      'PEPE/USDT': 'dust',
      'USDT/USDT': null,
      'RARE/USDT': null,
      // The TW account has its own (default, off) rules: 5 TWD is not hidden.
      '2330.TW': null,
    });
    const exact = setRules(emptyVisibility(), mexc.id, { dust: { enabled: true, threshold: '0.003' }, hideUsdt: false });
    expect(reasons(exact)['PEPE/USDT']).toBeNull();
  });
  it('hides USDT only for crypto accounts when enabled', () => {
    const preferences = setRules(emptyVisibility(), mexc.id, { ...defaultRules(), hideUsdt: true });
    expect(reasons(preferences)['USDT/USDT']).toBe('usdt');
    expect(reasons(preferences)['BTC/USDT']).toBeNull();
  });
  it('keys manual hiding by account + symbol, so a re-synced holding with a new id stays hidden', () => {
    const preferences = hide(emptyVisibility(), holdingKey(mexc.id, 'BTC/USDT'));
    expect(reasons(preferences)['BTC/USDT']).toBe('manual');
    const resynced = { ...btc, id: '55555555-5555-4555-8555-555555555555', quantity: '0.2' };
    expect(hiddenReason(resynced, mexc, '12000', preferences)).toBe('manual');
    // Same symbol in another account is unaffected.
    expect(hiddenReason({ ...btc, accountId: tw.id }, mexc, '1', preferences)).toBeNull();
  });
  it('unhiding a rule-hidden holding records an explicit show; hiding again clears it', () => {
    const key = holdingKey(mexc.id, 'PEPE/USDT');
    let preferences = setRules(emptyVisibility(), mexc.id, { dust: { enabled: true, threshold: '10' }, hideUsdt: false });
    preferences = hide(preferences, key);
    expect(reasons(preferences)['PEPE/USDT']).toBe('manual');
    preferences = unhide(preferences, key, true);
    expect(reasons(preferences)['PEPE/USDT']).toBeNull();
    expect(preferences.shown).toEqual([key]);
    preferences = hide(preferences, key);
    expect(preferences.shown).toEqual([]);
    expect(unhide(preferences, key, false)).toMatchObject({ hidden: [], shown: [] });
  });
  it('totals computed from visible rows exclude hidden holdings', () => {
    const preferences = hide(
      setRules(emptyVisibility(), mexc.id, { dust: { enabled: true, threshold: '10' }, hideUsdt: true }),
      holdingKey(mexc.id, 'BTC/USDT'),
    );
    const visible = rows.filter((r) => hiddenReason(r.holding, r.account, r.result.value, preferences) === null);
    expect(visible.map((r) => r.holding.symbol)).toEqual(['RARE/USDT', '2330.TW']);
    const totals = summarizeHoldings(visible, []);
    expect(totals.currencies.get('USDT')!.value.toString()).toBe('0');
    expect(totals.currencies.get('USDT')!.missing).toBe(1);
    expect(summarizeHoldings(rows, []).currencies.get('USDT')!.value.toString()).toBe('6250.003');
  });
  it('forgets only the deleted account', () => {
    let preferences = hide(emptyVisibility(), holdingKey(mexc.id, 'BTC/USDT'));
    preferences = hide(preferences, holdingKey(tw.id, '2330.TW'));
    preferences = setRules(preferences, mexc.id, { ...defaultRules(), hideUsdt: true });
    expect(forgetAccount(preferences, mexc.id)).toEqual({
      version: 1,
      hidden: [holdingKey(tw.id, '2330.TW')],
      shown: [],
      accounts: {},
    });
  });
  it('validates stored preferences strictly', () => {
    const valid = setRules(hide(emptyVisibility(), holdingKey(mexc.id, 'BTC/USDT')), mexc.id, defaultRules());
    expect(validateVisibility(JSON.parse(JSON.stringify(valid)))).toEqual(valid);
    for (const broken of [
      null,
      { ...valid, version: 2 },
      { ...valid, hidden: ['not-a-key'] },
      { ...valid, hidden: [`${mexc.id}|<script>`] },
      { ...valid, accounts: { [mexc.id]: { dust: { enabled: true, threshold: '-1' }, hideUsdt: false } } },
      { ...valid, accounts: { [mexc.id]: { dust: { enabled: true, threshold: '0' }, hideUsdt: false } } },
      { ...valid, accounts: { 'bad-id': defaultRules() } },
    ])
      expect(() => validateVisibility(broken)).toThrow();
  });
  it.each([
    ['1', '1'],
    [' 2.5 ', '2.5'],
    ['0.000001', '0.000001'],
    ['0', null],
    ['-1', null],
    ['abc', null],
    ['1e3', null],
    ['', null],
  ])('dust threshold %j → %j', (input, expected) => {
    expect(dustThreshold(input)).toBe(expected);
  });
  it('lists hidden holdings collapsed, with the not-counted note and an unhide action', () => {
    const html = renderToStaticMarkup(
      createElement(HiddenHoldings, {
        account: mexc,
        rows: [{ holding: usdt, result: rows[2]!.result, hidden: 'usdt' as const }],
        rules: { ...defaultRules(), hideUsdt: true },
        onUnhide: () => undefined,
      }),
    );
    expect(html).toContain('<details>');
    expect(html).not.toContain('<details open');
    expect(html).toContain('<summary>已隱藏 1 檔</summary>');
    expect(html).toContain('不計入總市值');
    expect(html).toContain('USDT 計價單位');
    expect(html).toMatch(/aria-label="USDT\/USDT 已隱藏".*<button[^>]*>取消隱藏<\/button>/);
    expect(renderToStaticMarkup(createElement(HiddenHoldings, { account: mexc, rows: [], onUnhide: () => undefined }))).toBe('');
  });
});
