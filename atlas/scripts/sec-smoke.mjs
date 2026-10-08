import { createServer } from 'vite';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
const vite = await createServer({
  configFile: false,
  server: { middlewareMode: true },
  appType: 'custom',
});
const { createSecClient } = await vite.ssrLoadModule('/src/fundamentals/SecClient.ts');
const { client, close } = createSecClient();
const { FinancialNormalizer } = await vite.ssrLoadModule(
  '/src/fundamentals/FinancialNormalizer.ts',
);
const normalizer = new FinancialNormalizer();
await mkdir('scratch/sec', { recursive: true });
try {
  for (const symbol of ['AAPL', 'MSFT', 'NVDA', 'TSLA']) {
    const result = process.argv.includes('--cached')
      ? { data: JSON.parse(await readFile(`scratch/sec/${symbol}.json`, 'utf8')), cik: 'cached' }
      : await client.companyFacts(symbol);
    if (!result) throw new Error(`No SEC CIK for ${symbol}`);
    await writeFile(`scratch/sec/${symbol}.json`, JSON.stringify(result.data));
    const annual = normalizer.normalize(result.data, symbol, 'annual'),
      quarterly = normalizer.normalize(result.data, symbol, 'quarterly');
    await writeFile(
      `scratch/sec/${symbol}-normalized.json`,
      JSON.stringify({ annual, quarterly }, null, 2),
    );
    console.log(
      JSON.stringify({
        symbol,
        cik: result.cik,
        annualRecords: annual.length,
        quarterlyRecords: quarterly.length,
        latestAnnualEnd: annual.at(-1)?.periodEnd,
        latestQuarterEnd: quarterly.at(-1)?.periodEnd,
      }),
    );
  }
} finally {
  await close();
  await vite.close();
}
