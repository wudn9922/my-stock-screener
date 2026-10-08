import { z } from 'zod';
import { financialMetrics } from './FundamentalsProvider';
import { financialSchema } from './financialSchema';
import { CANONICAL_SYMBOL_REGEX } from '../market-data/MarketProfile';

const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const realDate = (value: string) =>
  datePattern.test(value) &&
  Number.isFinite(Date.parse(value)) &&
  new Date(`${value}T00:00:00.000Z`).toISOString().slice(0, 10) === value;
const date = z.string().refine(realDate, 'Expected a real calendar date');
const epochSeconds = z.number().int().positive();
const symbol = z.string().regex(CANONICAL_SYMBOL_REGEX);
const cikSchema = z.string().regex(/^[0-9]{10}$/);

const financialSnapshotBaseSchema = z.object({
  version: z.literal(1),
  symbol,
  cik: cikSchema,
  issuerName: z.string().trim().min(1).max(300),
  source: z.url().regex(/^https:\/\/data\.sec\.gov\/api\/xbrl\/companyfacts\/CIK[0-9]{10}\.json$/),
  fetchedAt: epochSeconds,
  generatedAt: epochSeconds,
  quarterly: z.array(financialSchema).max(100),
  annual: z.array(financialSchema).max(100),
});

function validRecordDates(record: z.infer<typeof financialSchema>) {
  if (![record.periodStart, record.periodEnd, record.filingDate].every((value) => date.safeParse(value).success)) return false;
  return Object.values(record.sourceConcepts).every((provenance) =>
    !provenance || provenance.inputs.every((input) =>
      date.safeParse(input.filed).success && date.safeParse(input.end).success &&
      (input.start === null || date.safeParse(input.start).success),
    ),
  );
}

function uniquePeriodKey(record: z.infer<typeof financialSchema>) {
  return `${record.period}:${record.fiscalYear}:${record.fiscalQuarter ?? 'FY'}`;
}

function checkRecords(
  records: z.infer<typeof financialSchema>[],
  period: 'quarterly' | 'annual',
  symbolName: string,
  cik: string,
  issuerName: string,
  source: string,
  today: string,
  c: z.RefinementCtx,
) {
  const periods = new Set<string>();
  for (let i = 0; i < records.length; i++) {
    const record = records[i];
    const path = [period, i];
    if (record.symbol !== symbolName || record.cik !== cik || record.period !== period) {
      c.addIssue({ code: 'custom', path, message: 'Financial record identity does not match snapshot' });
    }
    if (record.issuerName !== undefined && record.issuerName !== issuerName) {
      c.addIssue({ code: 'custom', path: [...path, 'issuerName'], message: 'Issuer name does not match snapshot' });
    }
    if (record.periodEnd > today || record.filingDate > today || record.filingDate < record.periodEnd) {
      c.addIssue({ code: 'custom', path, message: 'Financial record contains a future or inconsistent date' });
    }
    if (!validRecordDates(record)) {
      c.addIssue({ code: 'custom', path, message: 'Financial record contains an invalid calendar date' });
    }
    const key = uniquePeriodKey(record);
    if (periods.has(key)) c.addIssue({ code: 'custom', path, message: 'Duplicate fiscal period' });
    periods.add(key);
    if (i > 0) {
      const previous = records[i - 1];
      if (
        previous.periodEnd < record.periodEnd ||
        (previous.periodEnd === record.periodEnd && previous.filingDate < record.filingDate)
      ) {
        c.addIssue({ code: 'custom', path, message: 'Financial records must be newest first' });
      }
    }
    for (const provenance of Object.values(record.sourceConcepts)) {
      if (!provenance) continue;
      for (const input of provenance.inputs) {
        if (input.secUrl !== source) {
          c.addIssue({ code: 'custom', path: [...path, 'sourceConcepts'], message: 'Metric provenance source does not match snapshot' });
        }
        if (input.filed > today) {
          c.addIssue({ code: 'custom', path: [...path, 'sourceConcepts'], message: 'Metric provenance filing date is in the future' });
        }
      }
    }
    if (!financialMetrics.some((metric) => record[metric] !== null)) {
      c.addIssue({ code: 'custom', path, message: 'Financial record has no reported or derived metrics' });
    }
  }
}

export const financialSnapshotSchema = financialSnapshotBaseSchema.superRefine((snapshot, c) => {
  const expectedSource = `https://data.sec.gov/api/xbrl/companyfacts/CIK${snapshot.cik}.json`;
  if (snapshot.source !== expectedSource) {
    c.addIssue({ code: 'custom', path: ['source'], message: 'Snapshot source does not match its CIK' });
  }
  const now = Math.floor(Date.now() / 1000);
  if (snapshot.generatedAt > now || snapshot.fetchedAt > now || snapshot.generatedAt < snapshot.fetchedAt) {
    c.addIssue({ code: 'custom', message: 'Snapshot timestamps are future-dated or out of order' });
  }
  if (snapshot.quarterly.length + snapshot.annual.length === 0) {
    c.addIssue({ code: 'custom', message: 'Snapshot must contain at least one supported financial period' });
  }
  const today = new Date(now * 1000).toISOString().slice(0, 10);
  checkRecords(snapshot.quarterly, 'quarterly', snapshot.symbol, snapshot.cik, snapshot.issuerName, snapshot.source, today, c);
  checkRecords(snapshot.annual, 'annual', snapshot.symbol, snapshot.cik, snapshot.issuerName, snapshot.source, today, c);
});

export type FinancialSnapshot = z.infer<typeof financialSnapshotSchema>;
export interface FinancialSnapshotMetadata {
  symbol: string;
  source: string;
  fetchedAt: number;
  generatedAt: number;
  cacheStatus: 'fresh' | 'cached' | 'stale';
  offline: boolean;
  stale: boolean;
}
