/**
 * Pure parsers for the full-market symbol directory (scripts/build-directory.ts).
 *
 * Included: Taiwan listed (TWSE, .TW) and OTC (TPEx, .TWO) common shares — 4-digit codes whose CFI code
 * is an equity share (ES****), including foreign "-KY" primary listings; US-listed operating companies
 * from NasdaqTrader (NASDAQ, NYSE, NYSE American, NYSE Arca, Cboe BZX, IEX), including ADRs/ADSs of real
 * companies (TSM, NVO, ASML) and MLP common units.
 * Excluded: ETFs/ETNs, warrants (權證), rights, units, preferred shares (特別股), notes/bonds/debentures,
 * closed-end and other fund-like products, SPAC blank-check shells, test issues, and Taiwan depositary
 * receipts (TDR, 存託憑證: few, thinly traded, their EPS/P/E is not comparable to common shares).
 */

export type DirectoryExchange = 'TWSE' | 'TPEx' | 'NASDAQ' | 'NYSE' | 'NYSE American' | 'NYSE Arca' | 'Cboe BZX' | 'IEX';
export type DirectoryItem = [symbol: string, name: string, exchange: DirectoryExchange];

export interface DirectoryDocument {
  version: 1;
  generatedAt: string;
  counts: Record<'TWSE' | 'TPEx' | 'US', number>;
  sources: Record<'TWSE' | 'TPEx' | 'US', string>;
  items: DirectoryItem[];
}

// ------------------------------------------------------------------ Taiwan (TWSE ISIN pages) ----

export interface IsinRow {
  section: string;
  code: string;
  name: string;
  isin: string;
  listedDate: string;
  market: string;
  industry: string;
  cfi: string;
  note: string;
}

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#(\d+);/g, (_, code: string) => String.fromCodePoint(Number(code)));
}

function cellText(html: string): string {
  return decodeEntities(html.replace(/<[^>]*>/g, ' ')).replace(/[\s　]+/g, ' ').trim();
}

/**
 * Parses https://isin.twse.com.tw/isin/C_public.jsp?strMode=2|4 (already decoded from Big5/MS950).
 * Section rows are single `colspan` cells (e.g. 股票, 特別股, ETF, 上市認購(售)權證, 臺灣存託憑證(TDR));
 * data rows have seven cells: 有價證券代號及名稱 (code + U+3000 + name), ISIN, 上市日, 市場別, 產業別, CFICode, 備註.
 */
export function parseIsinHtml(html: string): IsinRow[] {
  const rows: IsinRow[] = [];
  let section = '';
  for (const rowHtml of html.split(/<tr[\s>]/i).slice(1)) {
    const cells = [...rowHtml.matchAll(/<td[^>]*>([\s\S]*?)(?=<td[\s>]|<\/tr>|$)/gi)].map((match) =>
      cellText(match[1]!.replace(/<\/td>[\s\S]*$/i, '')),
    );
    if (cells.length === 1) {
      section = cells[0]!;
      continue;
    }
    if (cells.length < 6) continue;
    const match = /^([0-9A-Z]+)\s+(.+)$/.exec(cells[0]!);
    if (!match) continue;
    rows.push({
      section,
      code: match[1]!,
      name: match[2]!.trim(),
      isin: cells[1] ?? '',
      listedDate: cells[2] ?? '',
      market: cells[3] ?? '',
      industry: cells[4] ?? '',
      cfi: (cells[5] ?? '').toUpperCase(),
      note: cells[6] ?? '',
    });
  }
  return rows;
}

const EXCLUDED_TW_SECTIONS = /權證|特別股|存託憑證|TDR|ETF|ETN|受益|債|基金|指數投資證券/i;

/** Keeps 4-digit common shares (CFI ES*) from the 股票 (and 創新板) sections of one market. */
export function taiwanItemsFromIsin(rows: readonly IsinRow[], exchange: 'TWSE' | 'TPEx'): DirectoryItem[] {
  const expectedMarket = exchange === 'TWSE' ? '上市' : '上櫃';
  const suffix = exchange === 'TWSE' ? 'TW' : 'TWO';
  const items: DirectoryItem[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    if (!/^[0-9]{4}$/.test(row.code)) continue;
    if (!row.cfi.startsWith('ES')) continue;
    if (EXCLUDED_TW_SECTIONS.test(row.section)) continue;
    if (row.section && !/股票|創新板/.test(row.section)) continue;
    if (row.market && !row.market.startsWith(expectedMarket)) continue;
    const symbol = `${row.code}.${suffix}`;
    if (seen.has(symbol)) continue;
    seen.add(symbol);
    items.push([symbol, row.name, exchange]);
  }
  return items;
}

function recordValue(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return '';
}

/** Fallback: TWSE `t187ap03_L` / TPEx `mopsfin_t187ap03_O` issuer lists (company code = share code). */
export function taiwanItemsFromIssuerList(rows: readonly unknown[], exchange: 'TWSE' | 'TPEx'): DirectoryItem[] {
  const suffix = exchange === 'TWSE' ? 'TW' : 'TWO';
  const items: DirectoryItem[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    if (typeof row !== 'object' || row === null) continue;
    const record = row as Record<string, unknown>;
    const code = recordValue(record, '公司代號', 'SecuritiesCompanyCode', 'Code');
    const name = recordValue(record, '公司簡稱', 'CompanyAbbreviation', 'CompanyName', 'Name');
    if (!/^[0-9]{4}$/.test(code) || !name) continue;
    const symbol = `${code}.${suffix}`;
    if (seen.has(symbol)) continue;
    seen.add(symbol);
    items.push([symbol, name, exchange]);
  }
  return items;
}

/** Last-resort fallback: the tracked public/symbols/taiwan.json catalog, stocks only. */
export function taiwanItemsFromCatalog(document: unknown, exchange: 'TWSE' | 'TPEx'): DirectoryItem[] {
  const entries = (document as { entries?: unknown })?.entries;
  if (!Array.isArray(entries)) return [];
  return entries.flatMap((entry): DirectoryItem[] => {
    const value = entry as { symbol?: unknown; ticker?: unknown; name?: unknown; market?: unknown; kind?: unknown };
    if (value.kind !== 'stock' || value.market !== exchange) return [];
    if (typeof value.ticker !== 'string' || !/^[0-9]{4}$/.test(value.ticker) || typeof value.name !== 'string') return [];
    return [[`${value.ticker}.${exchange === 'TWSE' ? 'TW' : 'TWO'}`, value.name.trim(), exchange]];
  });
}

// ------------------------------------------------------------------------- US (NasdaqTrader) ----

export interface PipeTable {
  rows: Record<string, string>[];
  fileCreationTime?: string;
}

/** Parses NasdaqTrader pipe-delimited symbol files (header row + rows + `File Creation Time:` footer). */
export function parsePipeTable(text: string): PipeTable {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter((line) => line.trim());
  const header = (lines.shift() ?? '').split('|').map((cell) => cell.trim());
  const rows: Record<string, string>[] = [];
  let fileCreationTime: string | undefined;
  for (const line of lines) {
    if (line.startsWith('File Creation Time')) {
      fileCreationTime = line.split('|')[0]!.replace('File Creation Time:', '').trim();
      continue;
    }
    const cells = line.split('|');
    if (cells.length < header.length) continue;
    rows.push(Object.fromEntries(header.map((name, index) => [name, (cells[index] ?? '').trim()])));
  }
  return { rows, ...(fileCreationTime ? { fileCreationTime } : {}) };
}

const US_SYMBOL = /^[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,2})?$/;

/**
 * NasdaqTrader/CQS/ACT spellings → Yahoo: BRK.B, BRK/B, BRK B → BRK-B. Preferred (`$`, `p`, `-` series),
 * unit (`.U`, `=`), warrant (`.WS`, `+`), right (`.R`, `^`, `.RT`) and when-issued (`.W`, `#`) suffixes → null.
 */
export function normalizeUsSymbol(raw: string): string | null {
  const symbol = raw.trim().toUpperCase();
  if (!symbol || /[$=+^#*!%~]/.test(symbol)) return null;
  const match = /^([A-Z][A-Z0-9]{0,6})(?:[./ ]([A-Z0-9]{1,3}))?$/.exec(symbol);
  if (!match) return null;
  const [, root, suffix] = match;
  if (suffix === undefined) return root!;
  if (/^(U|UN|WS|W|WI|WD|R|RT|CL|CV|PR|P[A-Z])$/.test(suffix) || suffix.length > 2) return null;
  const yahoo = `${root}-${suffix}`;
  return US_SYMBOL.test(yahoo) ? yahoo : null;
}

const EXCLUDE_NAME_PATTERNS: readonly [RegExp, string][] = [
  [/\bwarrants?\b/i, 'warrant'],
  [/\b(?:etf|etn|exchange[- ]traded)\b/i, 'etf'],
  [/\bfunds?\b/i, 'fund'],
  [/\bpreferred\b|\bpref(?:erence)?\b|\bpfd\b|\bperpetual\b|\bcumulative\b|\bredeemable\b/i, 'preferred'],
  [/%/, 'fixed-income'],
  [/\bnotes?\b|\bdebentures?\b|\bbonds?\b|\bdue \d{4}\b|\bsenior secured\b|\bsubordinated\b/i, 'debt'],
  [/\bmunicipal\b|\bclosed[- ]end\b|\btax[- ]free\b/i, 'fund'],
  [/\b(?:income|credit|dividend|yield|treasury|opportunit(?:y|ies)|municipal|bond)\b[^|]*\btrust\b/i, 'fund'],
  [/\bacquisition (?:corp(?:oration)?|co|company|ltd|limited|inc)\b|\bblank check\b/i, 'spac'],
  [/\bwhen[- ]issued\b|\bwhen issued\b/i, 'when-issued'],
  [/\bcontingent value\b|\bescrow\b|\btest (?:stock|issue)\b/i, 'other'],
];

/** Closed-end funds usually trade as "Shares of Beneficial Interest"; REITs and royalty trusts are kept. */
const BENEFICIAL_INTEREST = /\bbeneficial interest\b/i;
const REAL_ASSET_TRUST = /realty|reit|royalty|propert|real estate|residential|apartment|hotel|lodging|mortgage/i;

/** Reason a US security is excluded from the directory, or null for an operating company's equity. */
export function usExclusionReason(name: string): string | null {
  for (const [pattern, reason] of EXCLUDE_NAME_PATTERNS) if (pattern.test(name)) return reason;
  if (BENEFICIAL_INTEREST.test(name) && !REAL_ASSET_TRUST.test(name)) return 'fund';
  // Units: SPAC units are excluded, MLP "Common Units" (EPD, ET, MPLX) are operating equities.
  if (/\bunits?\b/i.test(name) && !/\bcommon units?\b|\blimited partnership units?\b|\bpartnership units?\b/i.test(name)) return 'unit';
  if (/\brights?\b/i.test(name) && !/\bcommon (?:stock|shares)\b|\bordinary shares\b/i.test(name)) return 'right';
  return null;
}

const NAME_SUFFIX =
  /\s*[,-]?\s*(?:new\s+)?(?:(?:class|series)\s+[a-z0-9]+\s+)?(?:common\s+(?=shares of beneficial interest|units?\b))?(?:common stock|common shares?|ordinary shares?|common units?(?:\s+representing.*)?|limited partnership units?.*|subordinate voting shares?|voting shares?|shares of beneficial interest|american depositary shares?.*|american depository shares?.*|american depositary receipts?.*|sponsored adr.*|depositary shares?.*|ads|adr|shares|stock)\s*$/i;

/** "Apple Inc. - Common Stock" → "Apple Inc."; "Agilent Technologies, Inc. Common Stock" → "Agilent Technologies, Inc." */
export function cleanUsName(raw: string): string {
  let name = raw.trim();
  const dash = name.indexOf(' - ');
  if (dash > 0) name = name.slice(0, dash);
  for (let i = 0; i < 2; i++) name = name.replace(NAME_SUFFIX, '').trim();
  return name.replace(/[\s,]+$/, '') || raw.trim();
}

const OTHER_EXCHANGES: Readonly<Record<string, DirectoryExchange>> = {
  A: 'NYSE American',
  N: 'NYSE',
  P: 'NYSE Arca',
  Z: 'Cboe BZX',
  V: 'IEX',
};

/** https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt */
export function usItemsFromNasdaqListed(text: string): DirectoryItem[] {
  return parsePipeTable(text).rows.flatMap((row): DirectoryItem[] => {
    if (row['Test Issue'] === 'Y' || row.ETF === 'Y' || row.NextShares === 'Y') return [];
    const name = row['Security Name'] ?? '';
    if (usExclusionReason(name)) return [];
    const symbol = normalizeUsSymbol(row.Symbol ?? '');
    return symbol ? [[symbol, cleanUsName(name), 'NASDAQ']] : [];
  });
}

/** https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt (NYSE, NYSE American, Arca, Cboe, IEX). */
export function usItemsFromOtherListed(text: string): DirectoryItem[] {
  return parsePipeTable(text).rows.flatMap((row): DirectoryItem[] => {
    if (row['Test Issue'] === 'Y' || row.ETF === 'Y') return [];
    const exchange = OTHER_EXCHANGES[row.Exchange ?? ''];
    if (!exchange) return [];
    const name = row['Security Name'] ?? '';
    if (usExclusionReason(name)) return [];
    const symbol = normalizeUsSymbol(row['ACT Symbol'] ?? row['CQS Symbol'] ?? '');
    return symbol ? [[symbol, cleanUsName(name), exchange]] : [];
  });
}

// --------------------------------------------------------------------------------- assembly ----

/** De-duplicates by symbol (first wins) and sorts Taiwan first (by code), then US (by ticker). */
export function mergeItems(...groups: readonly DirectoryItem[][]): DirectoryItem[] {
  const seen = new Map<string, DirectoryItem>();
  for (const group of groups) for (const item of group) if (!seen.has(item[0])) seen.set(item[0], item);
  const rank = (item: DirectoryItem) => (item[2] === 'TWSE' ? 0 : item[2] === 'TPEx' ? 1 : 2);
  return [...seen.values()].sort((a, b) => rank(a) - rank(b) || a[0].localeCompare(b[0]));
}

export function directoryItemsFromDocument(document: unknown): DirectoryItem[] {
  const items = (document as { items?: unknown })?.items;
  if (!Array.isArray(items)) return [];
  return items.filter(
    (item): item is DirectoryItem =>
      Array.isArray(item) && item.length === 3 && item.every((part) => typeof part === 'string'),
  );
}

export function buildDirectoryDocument(
  parts: Record<'TWSE' | 'TPEx' | 'US', { items: DirectoryItem[]; source: string }>,
  generatedAt: string,
): DirectoryDocument {
  return {
    version: 1,
    generatedAt,
    counts: { TWSE: parts.TWSE.items.length, TPEx: parts.TPEx.items.length, US: parts.US.items.length },
    sources: { TWSE: parts.TWSE.source, TPEx: parts.TPEx.source, US: parts.US.source },
    items: mergeItems(parts.TWSE.items, parts.TPEx.items, parts.US.items),
  };
}
