import {it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {FinancialNormalizer} from '../src/fundamentals/FinancialNormalizer';
import {filingEvents} from '../src/events/FilingEvents';
it('SEC markers use actual audited filing dates, deduplicated accessions and include amendments',()=>{
 const raw=JSON.parse(readFileSync(new URL('./fixtures/sec/AAPL.json',import.meta.url),'utf8'));
 const records=new FinancialNormalizer().normalize(raw,'AAPL','quarterly');
 const events=filingEvents(records);
 expect(events.length).toBeGreaterThan(10);expect(new Set(events.map(e=>e.accession)).size).toBe(events.length);
 for(const e of events){
  const source=records.flatMap(r=>Object.values(r.sourceConcepts).flatMap(p=>p?.inputs??[])).find(s=>s.accession===e.accession)!;
  expect(e.time).toBe(Date.parse(source.filed+'T00:00:00Z')/1000);
  expect(e.form).toMatch(/^10-[QK](\/A)?$/);expect(e.source).toBe(source.filingUrl);
 }
 expect(filingEvents([])).toEqual([]);
});
