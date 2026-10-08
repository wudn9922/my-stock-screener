import type { CompanyFundamentals } from '../fundamentals/FundamentalsProvider';
export interface FilingEvent { time: number; form: string; accession: string; source: string }
/** Filing date is an SEC event date, not an earnings-announcement timestamp. */
export function filingEvents(records: readonly CompanyFundamentals[]): FilingEvent[] {
  const events = new Map<string, FilingEvent>();
  for (const r of records) for (const provenance of Object.values(r.sourceConcepts))
    for (const input of provenance?.inputs ?? []) {
      if (!/^10-[QK](\/A)?$/.test(input.form)) continue;
      events.set(input.accession, { time: Date.parse(input.filed + 'T00:00:00Z') / 1000, form: input.form, accession: input.accession, source: input.filingUrl });
    }
  return [...events.values()].sort((a,b) => a.time-b.time);
}
