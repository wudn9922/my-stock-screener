import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { financialSnapshotSchema, type FinancialSnapshot } from '../src/fundamentals/FinancialSnapshotSchema';

const raw = JSON.parse(readFileSync('tests/fixtures/sec/AAPL.json', 'utf8')) as {
  cik: number;
  entityName: string;
};
const normalizer = new FinancialNormalizer();
const cik = String(raw.cik).padStart(10, '0');
const source = `https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`;

function snapshot(): FinancialSnapshot {
  const now = Math.floor(Date.now() / 1000);
  return financialSnapshotSchema.parse({
    version: 1,
    symbol: 'AAPL',
    cik,
    issuerName: raw.entityName,
    source,
    fetchedAt: now,
    generatedAt: now,
    quarterly: normalizer.normalize(raw, 'AAPL', 'quarterly')
      .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))
      .slice(0, 100),
    annual: normalizer.normalize(raw, 'AAPL', 'annual')
      .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))
      .slice(0, 100),
  });
}

describe('versioned financial snapshot contract', () => {
  it('retains normalized provenance and warnings in a newest-first SEC pack', () => {
    const pack = snapshot();
    expect(pack.version).toBe(1);
    expect(pack.source).toBe(source);
    expect(pack.quarterly[0].periodEnd >= pack.quarterly.at(-1)!.periodEnd).toBe(true);
    expect(pack.annual[0].periodEnd >= pack.annual.at(-1)!.periodEnd).toBe(true);
    expect(pack.quarterly[0].sourceConcepts).toEqual(normalizer.normalize(raw, 'AAPL', 'quarterly')
      .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))[0].sourceConcepts);
    expect(pack.quarterly[0].warnings).toEqual(normalizer.normalize(raw, 'AAPL', 'quarterly')
      .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd) || b.filingDate.localeCompare(a.filingDate))[0].warnings);
  });

  it('rejects mismatched identity, period, duplicated periods, invalid dates, and future timestamps', () => {
    const valid = snapshot();
    const mismatchedSymbol = structuredClone(valid);
    mismatchedSymbol.quarterly[0].symbol = 'MSFT';
    expect(financialSnapshotSchema.safeParse(mismatchedSymbol).success).toBe(false);

    const mismatchedPeriod = structuredClone(valid);
    mismatchedPeriod.annual[0].period = 'quarterly';
    expect(financialSnapshotSchema.safeParse(mismatchedPeriod).success).toBe(false);

    const duplicate = structuredClone(valid);
    duplicate.quarterly.splice(1, 0, structuredClone(duplicate.quarterly[0]));
    expect(financialSnapshotSchema.safeParse(duplicate).success).toBe(false);

    const invalidCalendarDate = structuredClone(valid);
    invalidCalendarDate.quarterly[0].filingDate = '2024-02-30';
    expect(financialSnapshotSchema.safeParse(invalidCalendarDate).success).toBe(false);

    const future = structuredClone(valid);
    future.generatedAt = Math.floor(Date.now() / 1000) + 60;
    future.fetchedAt = future.generatedAt;
    expect(financialSnapshotSchema.safeParse(future).success).toBe(false);
  });

  it('requires a nonempty pack with no more than 100 records per period', () => {
    const valid = snapshot();
    expect(financialSnapshotSchema.safeParse({ ...valid, quarterly: [], annual: [] }).success).toBe(false);
    const tooMany = structuredClone(valid);
    tooMany.annual = Array.from({ length: 101 }, () => structuredClone(valid.annual[0]));
    expect(financialSnapshotSchema.safeParse(tooMany).success).toBe(false);
  });
});
