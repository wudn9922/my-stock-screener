import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createServer } from 'vite';
const vite = await createServer({
  configFile: false,
  server: { middlewareMode: true },
  appType: 'custom',
});
const { conceptMappings } = await vite.ssrLoadModule('/src/fundamentals/FinancialConceptMapper.ts');
const concepts = new Set(Object.values(conceptMappings).flatMap((m) => m.concepts));
await mkdir('tests/fixtures/sec', { recursive: true });
const provenance = [];
try {
  for (const symbol of ['AAPL', 'MSFT', 'NVDA', 'TSLA']) {
    const original = await readFile(`scratch/sec/${symbol}.json`),
      data = JSON.parse(original);
    const facts = {};
    for (const [concept, value] of Object.entries(data.facts['us-gaap'])) {
      if (!concepts.has(concept)) continue;
      const units = Object.fromEntries(
        Object.entries(value.units).map(([unit, rows]) => [
          unit,
          rows.filter((r) => r.end >= '2022-01-01'),
        ]),
      );
      if (Object.values(units).some((rows) => rows.length)) facts[concept] = { units };
    }
    const filtered = { cik: data.cik, entityName: data.entityName, facts: { 'us-gaap': facts } };
    const output = JSON.stringify(filtered);
    await writeFile(`tests/fixtures/sec/${symbol}.json`, output);
    provenance.push({
      symbol,
      cik: data.cik,
      source: `https://data.sec.gov/api/xbrl/companyfacts/CIK${String(data.cik).padStart(10, '0')}.json`,
      retrieved: '2026-10-06',
      originalSha256: createHash('sha256').update(original).digest('hex'),
      fixtureSha256: createHash('sha256').update(output).digest('hex'),
      filter:
        'Mapped US-GAAP concepts only; raw facts with end >= 2022-01-01. Values/periods/accessions unchanged. Test-only; UI never imports fixtures.',
    });
  }
  await writeFile('tests/fixtures/sec/provenance.json', JSON.stringify(provenance, null, 2));
} finally {
  await vite.close();
}
