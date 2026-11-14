import type { GroupItem } from '../report/schema';
import { displayTicker, maDistance, maValue } from '../ui/format';

/** Excel only reads UTF-8 Chinese correctly when the file starts with a byte-order mark. */
const BOM = '﻿';
const MAX_MA_COLUMNS = 6;

/**
 * One CSV field. Text a spreadsheet would run as a formula (`=`, `+`, `-`, `@`, tab, CR first) gets a
 * leading apostrophe: names and notes come from market data, not from the person opening the file.
 */
function field(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  const text = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const round = (value: number | null, digits: number) => (value === null ? null : Number(value.toFixed(digits)));

const CSV_METRIC_COLUMNS: readonly { title: string; digits: number; value: (item: GroupItem) => number | null }[] = [
  { title: 'RS分數%（3/6/9/12月相對報酬加權40/20/20/20）', digits: 2, value: (item) => item.metrics?.rsScore ?? null },
  { title: 'ATR%', digits: 2, value: (item) => item.metrics?.atrPct ?? null },
  { title: '20日區間寬度%', digits: 2, value: (item) => item.metrics?.range20 ?? null },
];

/** The screener list as shown (same rows, same order) for a spreadsheet. Missing values stay empty. */
export function screenerCsv(items: readonly GroupItem[]): string {
  const periods = [...new Set(items.flatMap((item) => item.maList))].sort((a, b) => a - b).slice(0, MAX_MA_COLUMNS);
  // Metric columns the screener list can filter on; written only when some row has the value, so a file built
  // before the column existed exports exactly as before.
  const metricColumns = CSV_METRIC_COLUMNS.filter(({ value }) => items.some((item) => value(item) !== null));
  const header = ['代號', '名稱', '收盤', '漲跌幅%', '成交量', ...periods.flatMap((period) => [`MA${period}`, `距MA${period}%`]), '資料日', '備註', ...metricColumns.map(({ title }) => title)];
  const rows = items.map((item) => [
    displayTicker(item.symbol),
    item.name,
    item.close,
    round(item.changePct, 2),
    item.volume,
    ...periods.flatMap((period) => {
      const ma = item.maList.includes(period) ? maValue(item.maValues, period) : null;
      return [round(ma, 4), round(maDistance(item.close, ma), 2)];
    }),
    item.asOf,
    item.note,
    ...metricColumns.map(({ value, digits }) => round(value(item), digits)),
  ]);
  return BOM + [header, ...rows].map((row) => row.map(field).join(',')).join('\r\n') + '\r\n';
}

/** A file name safe on every platform: group key and date only. */
export function screenerCsvName(groupKey: string, date: string | null | undefined): string {
  const safe = (value: string) => value.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '');
  return `atlas-${safe(groupKey) || 'screener'}${date ? `-${safe(date)}` : ''}.csv`;
}
