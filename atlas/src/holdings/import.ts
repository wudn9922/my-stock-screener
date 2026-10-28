import {
  Money,
  decimalText,
  accountMarket,
  validateHolding,
  type ColumnMapping,
  type Holding,
  type HoldingAccount,
  type HoldingSource,
} from './model';

export const IMPORT_LIMITS = { bytes: 8 * 1024 * 1024, expanded: 32 * 1024 * 1024, rows: 20000, columns: 128 };
export interface ImportSymbol {
  symbol: string;
  name: string;
  market: 'TW' | 'US';
  etf?: boolean;
}
export interface ImportTable {
  headers: string[];
  rows: string[][];
  fingerprint: string;
  transactions: boolean;
}
export interface PreviewRow {
  line: number;
  symbol: string;
  quantity: string;
  averageCost: string;
  errors: string[];
  skip: boolean;
}
export interface SkippedRow {
  line: number;
  reason: string;
}
export interface ImportPreview {
  rows: PreviewRow[];
  skipped: SkippedRow[];
  warnings: string[];
}
const normalize = (value: string) =>
  value
    .normalize('NFKC')
    .replace(/^\uFEFF/, '')
    .trim();
const headerKey = (value: string) =>
  normalize(value)
    .toLowerCase()
    .replace(/[\s_()（）/.-]/g, '');
export const COLUMN_ALIASES = {
  symbol: ['代號', '股票代號', '商品代號', '證券代號', '股票代碼', '證券名稱', '股票名稱', 'symbol', 'ticker'],
  quantity: ['庫存股數', '股數', '數量', '庫存', '庫存數量', '餘額股數', 'quantity', 'shares', '庫存張數', '張數'],
  average: ['成本均價', '平均成本', '均價', '買進均價', '買進平均成本', '平均單價', 'avg cost', 'average cost'],
  total: ['付出成本', '成本', '總成本', '投資成本', '原始成本', 'cost basis'],
} as const;
const aliases = Object.fromEntries(
  Object.entries(COLUMN_ALIASES).map(([key, values]) => [key, new Set(values.map(headerKey))]),
);
export function parseNumber(input: string, positive = false): string {
  let text = normalize(input).replace(/^(?:NT\$|US\$|TWD|USD|USDT|\$)\s*/i, '');
  if (/^\(.*\)$/.test(text)) text = '-' + text.slice(1, -1);
  if (text.includes(',') && !/^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$/.test(text)) throw new Error('數字的千分位格式不正確');
  text = text.replace(/,/g, '');
  if (!/^-?\d+(?:\.\d+)?(?:e[+-]?\d{1,3})?$/i.test(text)) throw new Error('請填有效數字（最多 18 位小數）');
  const value = new Money(text);
  if (value.decimalPlaces() > 18 || value.isNegative() || (positive && value.isZero()) || value.gte('1000000000000000'))
    throw new Error(positive ? '股數必須大於零，且不超過欄位上限' : '成本必須為零或正數，且不超過欄位上限');
  return decimalText(value);
}
/** RFC-style quoted CSV; quoted newlines remain in their own cell. */
function delimited(text: string, separator: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [],
    cell = '',
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i]!;
    if (char === '"' && (quoted || cell.trim() === '')) {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (!quoted && (char === separator || char === '\n')) {
      row.push(normalize(cell));
      cell = '';
      if (row.length > IMPORT_LIMITS.columns * 3) throw new Error('表格欄數過多');
      if (char === '\n') {
        if (row.some(Boolean)) rows.push(row);
        row = [];
        if (rows.length > IMPORT_LIMITS.rows) throw new Error('表格列數過多');
      }
    } else if (char !== '\r') cell += char;
  }
  if (quoted) throw new Error('引號尚未結束，請檢查表格格式');
  row.push(normalize(cell));
  if (row.some(Boolean)) rows.push(row);
  return rows;
}
/** Repair only a UNIQUE grouping consistent with a numeric header and valid thousands. */
function repairThousands(row: string[], headers: string[]): string[] {
  if (row.length <= headers.length) return row;
  const solutions: string[][] = [];
  let visits = 0;
  function walk(position: number, column: number, result: string[]) {
    if (solutions.length > 1 || ++visits > 5000) return;
    if (column === headers.length) {
      if (position === row.length) solutions.push(result);
      return;
    }
    const numeric = ['quantity', 'average', 'total'].some((key) => aliases[key]!.has(headerKey(headers[column]!)));
    const max = Math.min(numeric ? 6 : 1, row.length - position - (headers.length - column - 1));
    for (let size = 1; size <= max; size++) {
      const cell = row.slice(position, position + size).join(',');
      if (size > 1 && !/^\d{1,3}(?:,\d{3})+(?:\.\d+)?$/.test(cell)) continue;
      walk(position + size, column + 1, [...result, cell]);
    }
  }
  walk(0, 0, []);
  return visits <= 5000 && solutions.length === 1 ? solutions[0]! : row;
}
export function parseText(text: string): string[][] {
  if (new TextEncoder().encode(text).length > IMPORT_LIMITS.bytes) throw new Error('檔案或文字超過 8 MB');
  const normalized = text.normalize('NFKC');
  const first = normalized.split(/\r?\n/).find((line) => line.trim()) ?? '';
  if (first.includes('\t')) return delimited(normalized, '\t');
  if (first.includes(',')) {
    const rows = delimited(normalized, ',');
    return rows.map((row, index) => (index ? repairThousands(row, rows[0]!) : row));
  }
  return normalized
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => line.trim().split(/ {2,}/).map(normalize));
}
export async function makeTable(input: unknown[][]): Promise<ImportTable> {
  if (!input.length || input.length > IMPORT_LIMITS.rows || input.some((row) => row.length > IMPORT_LIMITS.columns))
    throw new Error('表格為空或超過大小限制');
  const rows = input
    .map((row) =>
      row.map((cell) => (cell instanceof Date ? cell.toISOString().slice(0, 10) : normalize(String(cell ?? '')))),
    )
    .filter((row) => row.some(Boolean));
  // Some broker exports start with a title. Keep only recognized header lines; never log titles.
  const index = rows
    .slice(0, 20)
    .findIndex(
      (row) =>
        row.some((cell) => aliases.symbol!.has(headerKey(cell))) &&
        row.some((cell) => aliases.quantity!.has(headerKey(cell))),
    );
  const [headers = [], ...body] = rows.slice(index < 0 ? 0 : index);
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(headers.map(headerKey).join('\t')));
  const fingerprint = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const keys = headers.map(headerKey);
  return {
    headers,
    rows: body,
    fingerprint,
    transactions: ['symbol', 'quantity', 'action', 'tradedate'].every((key) => keys.includes(key)),
  };
}
export function detectMapping(table: ImportTable, saved?: ColumnMapping): ColumnMapping | null {
  if (
    saved?.fingerprint === table.fingerprint &&
    [saved.symbol, saved.quantity, saved.average ?? saved.total ?? -1].every(
      (index) => index >= 0 && index < table.headers.length,
    )
  )
    return saved;
  const find = (key: string) => table.headers.findIndex((header) => aliases[key]!.has(headerKey(header)));
  const symbol = find('symbol'),
    quantity = find('quantity'),
    average = find('average'),
    total = find('total');
  if (symbol < 0 || quantity < 0 || (average < 0 && total < 0)) return null;
  return {
    fingerprint: table.fingerprint,
    symbol,
    quantity,
    ...(average < 0 ? {} : { average }),
    ...(total < 0 ? {} : { total }),
    unit: /張/.test(table.headers[quantity]!) ? 'lots' : 'shares',
  };
}
export function resolveHoldingSymbol(
  input: string,
  account: HoldingAccount,
  directory: readonly ImportSymbol[],
): string | null {
  const text = normalize(input).toUpperCase();
  if (account.kind === 'crypto-exchange') {
    const symbol = text.replace(/[-_]/g, '/');
    if (/^[A-Z0-9]{1,20}\/USDT$/.test(symbol)) return symbol;
    if (/^[A-Z0-9]{1,20}$/.test(symbol) && symbol !== 'USDT') return `${symbol}/USDT`;
    return null;
  }
  if (account.kind === 'tw-broker') {
    const token = /^(\d{4,6}[A-Z]?)(?:\.(TW|TWO))?(?:\s|$)/.exec(text);
    if (!token) {
      const names = directory.filter((entry) => entry.market === 'TW' && entry.name === text);
      return names.length === 1 ? names[0]!.symbol : null;
    }
    const entries = directory.filter(
      (entry) =>
        entry.market === 'TW' &&
        entry.symbol.split('.')[0] === token[1] &&
        (!token[2] || entry.symbol.endsWith('.' + token[2])),
    );
    return entries.length === 1 ? entries[0]!.symbol : null;
  }
  const symbol = text.replace(/^([A-Z]{1,6})\.([A-Z]{1,2})$/, '$1-$2');
  return directory.some((entry) => entry.market === 'US' && entry.symbol === symbol) ? symbol : null;
}
export function validatePreviewRow(
  row: PreviewRow,
  account: HoldingAccount,
  directory: readonly ImportSymbol[],
): PreviewRow {
  const errors: string[] = [];
  const symbol = resolveHoldingSymbol(row.symbol, account, directory);
  if (!symbol) errors.push('代號找不到，請確認市場、上市／上櫃代號或更新目錄');
  let quantity = row.quantity,
    averageCost = row.averageCost;
  try {
    quantity = parseNumber(quantity, true);
  } catch {
    errors.push('股數不是有效正數');
  }
  try {
    averageCost = parseNumber(averageCost);
  } catch {
    errors.push('均價不是有效非負數');
  }
  return { ...row, symbol: symbol ?? row.symbol, quantity, averageCost, errors };
}
export function previewSnapshot(
  table: ImportTable,
  mapping: ColumnMapping,
  account: HoldingAccount,
  directory: readonly ImportSymbol[],
): ImportPreview {
  const rows = table.rows.map((cells, index): PreviewRow => {
    let quantity = cells[mapping.quantity] ?? '',
      averageCost = mapping.average === undefined ? '' : (cells[mapping.average] ?? '');
    try {
      quantity = parseNumber(quantity, true);
      if (mapping.unit === 'lots') quantity = decimalText(new Money(quantity).times(1000));
      if (!averageCost && mapping.total !== undefined)
        averageCost = decimalText(new Money(parseNumber(cells[mapping.total] ?? '')).div(quantity));
    } catch {
      /* Invalid cells stay visible and editable; no input appears in errors. */
    }
    const row = validatePreviewRow(
      { line: index + 2, symbol: cells[mapping.symbol] ?? '', quantity, averageCost, errors: [], skip: false },
      account,
      directory,
    );
    if (cells.length !== table.headers.length) row.errors.push('欄數與標題不符，請修正或略過（逗號千分位可加引號）');
    return row;
  });
  return { rows, skipped: [], warnings: [] };
}
export function previewTransactions(
  table: ImportTable,
  account: HoldingAccount,
  directory: readonly ImportSymbol[],
): ImportPreview {
  const index = Object.fromEntries(table.headers.map((header, i) => [headerKey(header), i]));
  const book = new Map<
    string,
    { quantity: InstanceType<typeof Money>; cost: InstanceType<typeof Money>; line: number }
  >();
  const rows: PreviewRow[] = [],
    skipped: SkippedRow[] = [],
    blocked = new Set<string>();
  const date = (cells: string[]) => {
    const text = cells[index.tradedate!] ?? '';
    const match = /^(?:(\d{4})-(\d{2})-(\d{2})|(\d{1,2})\/(\d{1,2})\/(\d{4}))$/.exec(text);
    if (!match) return null;
    const value = match[1]
      ? `${match[1]}-${match[2]}-${match[3]}`
      : `${match[6]}-${match[4]!.padStart(2, '0')}-${match[5]!.padStart(2, '0')}`;
    return Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value ? value : null;
  };
  const ordered = table.rows
    .map((cells, i) => ({ cells, line: i + 2, date: date(cells) }))
    .sort((a, b) => (a.date ?? '').localeCompare(b.date ?? '') || a.line - b.line);
  for (const entry of ordered) {
    const get = (key: string) => entry.cells[index[key] ?? -1] ?? '';
    const action = normalize(get('action')).toUpperCase();
    if (!/^(BUY|SELL)(?:\s|$)/.test(action)) {
      skipped.push({ line: entry.line, reason: '非一般買賣列（含股利、分割、轉入轉出等），未納入成本；請核對庫存' });
      continue;
    }
    const symbol = resolveHoldingSymbol(get('symbol'), account, directory);
    try {
      if (!entry.date || !symbol || account.kind !== 'us-broker' || /OPTION|SHORT|COVER/.test(action))
        throw new Error();
      const quantity = new Money(
        parseNumber(action.startsWith('SELL') ? get('quantity').replace(/^-/, '') : get('quantity'), true),
      );
      const previous = book.get(symbol) ?? { quantity: new Money(0), cost: new Money(0), line: entry.line };
      if (action.startsWith('BUY')) {
        const amountText = normalize(get('amount'));
        let cost;
        if (amountText) {
          if (!/^[-(]/.test(amountText)) throw new Error();
          cost = new Money(parseNumber(amountText.replace(/^-/, '').replace(/^\((.*)\)$/, '$1')));
        } else
          cost = quantity
            .times(parseNumber(get('price')))
            .plus(parseNumber(get('commission') || '0'))
            .plus(parseNumber(get('fee') || '0'));
        book.set(symbol, {
          quantity: previous.quantity.plus(quantity),
          cost: previous.cost.plus(cost),
          line: entry.line,
        });
      } else {
        if (quantity.gt(previous.quantity)) throw new Error();
        const remaining = previous.quantity.minus(quantity);
        book.set(symbol, {
          quantity: remaining,
          cost: remaining.isZero() ? new Money(0) : previous.cost.times(remaining).div(previous.quantity),
          line: entry.line,
        });
      }
    } catch {
      if (symbol) blocked.add(symbol);
      rows.push({
        line: entry.line,
        symbol: symbol ?? get('symbol'),
        quantity: get('quantity'),
        averageCost: '',
        skip: false,
        errors: ['交易紀錄不完整或不支援，請核對日期、買賣數量與期初庫存'],
      });
    }
  }
  for (const [symbol, item] of book)
    if (item.quantity.gt(0) && !blocked.has(symbol))
      rows.push({
        line: item.line,
        symbol,
        quantity: decimalText(item.quantity),
        averageCost: decimalText(item.cost.div(item.quantity)),
        skip: false,
        errors: [],
      });
  return {
    rows,
    skipped,
    warnings: [
      '以匯入期間內的買賣重建平均成本，無法推知期初庫存。請核對券商目前庫存；股利再投資、分割及移轉等略過列須自行修正。',
    ],
  };
}
export function previewErrors(rows: PreviewRow[]): boolean {
  const active = rows.filter((row) => !row.skip);
  return active.some((row) => row.errors.length) || new Set(active.map((row) => row.symbol)).size !== active.length;
}
export function previewHoldings(rows: PreviewRow[], account: HoldingAccount, source: HoldingSource): Holding[] {
  if (previewErrors(rows)) throw new Error('請修正或略過問題列，並合併重複代號。');
  return rows
    .filter((row) => !row.skip)
    .map((row) =>
      validateHolding(
        {
          id: crypto.randomUUID(),
          accountId: account.id,
          symbol: row.symbol,
          market: accountMarket(account),
          quantity: row.quantity,
          averageCost: row.averageCost,
          costCurrency: account.currency,
          source,
          updatedAt: new Date().toISOString(),
        },
        account,
      ),
    );
}

/** Inspect central-directory sizes BEFORE decompression; reject encrypted/oversized archives. */
export function checkXlsx(buffer: ArrayBuffer) {
  const view = new DataView(buffer);
  let end = -1;
  for (let i = view.byteLength - 22; i >= Math.max(0, view.byteLength - 65557); i--)
    if (view.getUint32(i, true) === 0x06054b50) {
      end = i;
      break;
    }
  if (end < 0) throw new Error();
  const count = view.getUint16(end + 10, true),
    start = view.getUint32(end + 16, true);
  if (count > 2048 || count < 1) throw new Error();
  let cursor = start,
    expanded = 0;
  for (let i = 0; i < count; i++) {
    if (view.getUint32(cursor, true) !== 0x02014b50 || view.getUint16(cursor + 8, true) & 1) throw new Error();
    expanded += view.getUint32(cursor + 24, true);
    if (expanded > IMPORT_LIMITS.expanded) throw new Error();
    cursor +=
      46 + view.getUint16(cursor + 28, true) + view.getUint16(cursor + 30, true) + view.getUint16(cursor + 32, true);
  }
}
export async function readHoldingFile(file: File): Promise<ImportTable> {
  try {
    if (file.size > IMPORT_LIMITS.bytes) throw new Error();
    const buffer = await file.arrayBuffer();
    if (/\.xlsx$/i.test(file.name)) {
      checkXlsx(buffer);
      const { readSheet } = await import('read-excel-file/browser');
      return await makeTable(await readSheet(buffer, { parseNumber: (value) => value }));
    }
    if (!/\.csv$/i.test(file.name)) throw new Error();
    let text;
    const prefix = new Uint8Array(buffer, 0, Math.min(2, buffer.byteLength));
    if (prefix[0] === 0xff && prefix[1] === 0xfe) text = new TextDecoder('utf-16le', { fatal: true }).decode(buffer);
    else if (prefix[0] === 0xfe && prefix[1] === 0xff)
      text = new TextDecoder('utf-16be', { fatal: true }).decode(buffer);
    else
      try {
        text = new TextDecoder('utf-8', { fatal: true }).decode(buffer);
      } catch {
        text = new TextDecoder('big5', { fatal: true }).decode(buffer);
      }
    return await makeTable(parseText(text));
  } catch {
    throw new Error('無法解析檔案。請使用 8 MB 以內的 CSV／XLSX，或直接貼上表格文字。');
  }
}
