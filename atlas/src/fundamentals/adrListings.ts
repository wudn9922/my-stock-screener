/**
 * Taiwan companies that also trade in the US as an ADR. The ADR's financial page links to the home
 * listing (台股) financials. Figures are never converted between the two: they are separate series
 * in their own currencies (ADR file: the reporting currency; 台股 file: TWD).
 *
 * Hand-kept table. `sharesPerAdr` is the number of common shares per ADR; null means the ratio is not
 * confirmed and no ratio is shown.
 */
export interface TwAdrListing {
  /** US ticker of the ADR, upper case. */
  readonly adr: string;
  /** Taiwan listing in the canonical form used by docs/report/financials (`2330.TW`). */
  readonly tw: string;
  readonly twName: string;
  readonly sharesPerAdr: number | null;
}

export const TW_ADR_LISTINGS: readonly TwAdrListing[] = [
  { adr: 'TSM', tw: '2330.TW', twName: '台積電', sharesPerAdr: 5 },
  { adr: 'UMC', tw: '2303.TW', twName: '聯電', sharesPerAdr: 5 },
  { adr: 'ASX', tw: '3711.TW', twName: '日月光投控', sharesPerAdr: 2 },
  { adr: 'CHT', tw: '2412.TW', twName: '中華電', sharesPerAdr: 10 },
];

/** The Taiwan listing of an ADR symbol, or null for every other symbol (including the Taiwan codes). */
export function twListingForAdr(symbol: string | undefined): TwAdrListing | null {
  if (!symbol) return null;
  const adr = symbol.trim().toUpperCase();
  return TW_ADR_LISTINGS.find((listing) => listing.adr === adr) ?? null;
}

/** Ratio line for the header: "1 ADR ＝ 5 股普通股", or "ADR 比例待確認" when not confirmed. */
export function adrRatioText(listing: TwAdrListing): string {
  return listing.sharesPerAdr === null ? 'ADR 比例待確認' : `1 ADR ＝ ${listing.sharesPerAdr} 股普通股`;
}
