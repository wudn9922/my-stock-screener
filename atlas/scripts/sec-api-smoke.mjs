import { writeFile } from 'node:fs/promises';
const output = [];
for (const symbol of ['AAPL', 'MSFT', 'NVDA', 'TSLA']) {
  for (const period of ['annual', 'quarterly']) {
    const start = Date.now();
    const response = await fetch(
      `http://127.0.0.1:8788/api/fundamentals?symbol=${symbol}&period=${period}`,
    );
    if (!response.ok) throw new Error(`${symbol} ${period}: ${response.status}`);
    const records = await response.json();
    if (!records.length) throw new Error(`Missing ${symbol} financials`);
    const latest = records.at(-1);
    output.push({
      symbol,
      period,
      records: records.length,
      elapsedMs: Date.now() - start,
      fiscalYear: latest.fiscalYear,
      fiscalQuarter: latest.fiscalQuarter,
      periodStart: latest.periodStart,
      periodEnd: latest.periodEnd,
      revenue: latest.revenue,
      epsDiluted: latest.epsDiluted,
      operatingCashFlow: latest.operatingCashFlow,
      capex: latest.capex,
      freeCashFlow: latest.freeCashFlow,
      revenueDerived: latest.sourceConcepts.revenue?.derived,
      cashFlowDerived: latest.sourceConcepts.operatingCashFlow?.derived,
      accession: latest.accession,
      filingDate: latest.filingDate,
    });
  }
}
await writeFile(
  'scratch/sec/api-smoke.json',
  JSON.stringify(
    {
      testedAt: new Date().toISOString(),
      backend: 'standalone production bundle; official SEC network',
      results: output,
    },
    null,
    2,
  ),
);
console.log(JSON.stringify(output));
