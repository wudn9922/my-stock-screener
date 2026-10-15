import { describe, expect, it } from 'vitest';
import { financialsKey } from '../src/fundamentals/FinancialHistory';
import { TW_ADR_LISTINGS, adrRatioText, twListingForAdr } from '../src/fundamentals/adrListings';

describe('ADR → Taiwan home listing', () => {
  it('maps the Taiwan ADRs, ignoring case and spaces', () => {
    expect(twListingForAdr('TSM')).toMatchObject({ tw: '2330.TW', sharesPerAdr: 5 });
    expect(twListingForAdr(' umc ')).toMatchObject({ tw: '2303.TW', sharesPerAdr: 5 });
    expect(twListingForAdr('ASX')).toMatchObject({ tw: '3711.TW', sharesPerAdr: 2 });
    expect(twListingForAdr('CHT')).toMatchObject({ tw: '2412.TW', sharesPerAdr: 10 });
  });

  it('is null for every other symbol, Taiwan codes included', () => {
    expect(twListingForAdr('AAPL')).toBeNull();
    expect(twListingForAdr('2330.TW')).toBeNull();
    expect(twListingForAdr('^TWII')).toBeNull();
    expect(twListingForAdr('')).toBeNull();
    expect(twListingForAdr(undefined)).toBeNull();
  });

  it('keeps the table in the file-key form the financials page loads', () => {
    const adrs = TW_ADR_LISTINGS.map((item) => item.adr);
    expect(new Set(adrs).size).toBe(adrs.length);
    for (const item of TW_ADR_LISTINGS) {
      expect(financialsKey(item.tw)).toBe(item.tw);
      expect(item.tw).toMatch(/\.(TW|TWO)$/);
    }
  });

  it('shows the ratio only when it is confirmed', () => {
    expect(adrRatioText({ adr: 'TSM', tw: '2330.TW', twName: '台積電', sharesPerAdr: 5 })).toBe('1 ADR ＝ 5 股普通股');
    expect(adrRatioText({ adr: 'X', tw: '2330.TW', twName: '台積電', sharesPerAdr: null })).toBe('ADR 比例待確認');
  });
});
