import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { fetch as proxyFetch, EnvHttpProxyAgent } from 'undici';
import { createServer } from 'vite';

const fixtureSymbols = ['JPM', 'WMT', 'XOM'];
const smokeSymbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', ...fixtureSymbols];
const writeFixtures = process.argv.includes('--write-fixtures');
const requested = process.argv.slice(2).filter((arg) => !arg.startsWith('--'));
const symbols = writeFixtures ? fixtureSymbols : requested.length ? requested : smokeSymbols;
const historicalXomCik = '0000034088';
const currentXomMappedCik = '0002115436';
if (symbols.some((symbol) => !/^[A-Z][A-Z0-9.-]{0,9}$/.test(symbol))) {
  throw new Error('Symbols must be uppercase US ticker symbols.');
}

const vite = await createServer({
  configFile: false,
  server: { middlewareMode: true },
  appType: 'custom',
});
const [{ createSecClient }, { FinancialNormalizer }, { conceptMappings }] = await Promise.all([
  vite.ssrLoadModule('/src/fundamentals/SecClient.ts'),
  vite.ssrLoadModule('/src/fundamentals/FinancialNormalizer.ts'),
  vite.ssrLoadModule('/src/fundamentals/FinancialConceptMapper.ts'),
]);
const concepts = new Set(Object.values(conceptMappings).flatMap((mapping) => mapping.concepts));
const { client, close } = createSecClient();
const normalizer = new FinancialNormalizer();
const scratchDir = 'scratch/sec-v1';
const fixturesDir = 'tests/fixtures/sec';
const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const filterCompanyFacts = (data) => {
  const facts = {};
  for (const [concept, value] of Object.entries(data.facts['us-gaap'] ?? {})) {
    if (!concepts.has(concept)) continue;
    const units = Object.fromEntries(
      Object.entries(value.units ?? {}).map(([unit, rows]) => [
        unit,
        rows.filter((row) => row.end >= '2022-01-01'),
      ]),
    );
    if (Object.values(units).some((rows) => rows.length)) facts[concept] = { units };
  }
  return { cik: data.cik, entityName: data.entityName, facts: { 'us-gaap': facts } };
};

await mkdir(scratchDir, { recursive: true });
const startedAt = new Date().toISOString();
const provenance = [];
try {
  // Concurrent calls exercise SecClient's shared ticker-map cache, URL deduplication,
  // serialized request queue, and SEC-compliant minimum request interval.
  const results = writeFixtures
    ? []
    : await Promise.all(symbols.map((symbol) => client.companyFacts(symbol)));
  const summaries = [];
  for (let index = 0; index < symbols.length; index += 1) {
    const symbol = symbols[index];
    let result = results[index];
    let resolution = 'Official SEC ticker map through SecClient';
    let retrievedAt = new Date().toISOString();
    if (writeFixtures && symbol === 'XOM') {
      const mappedCik = await client.resolveCik('XOM');
      if (mappedCik === historicalXomCik) {
        result = await client.companyFacts('XOM');
      } else {
        if (mappedCik !== currentXomMappedCik) {
          throw new Error(
            `XOM SEC mapping changed to ${mappedCik}; inspect issuer identity before refreshing the historical fixture.`,
          );
        }
        // Preserve both official routes as separate fixtures: current ticker resolution and a
        // historical Exxon Mobil Corporation CIK used to exercise broader normalizer coverage.
        const current = await client.companyFacts('XOM');
        if (!current || current.cik !== mappedCik) {
          throw new Error('Current XOM response did not match the SEC ticker-map CIK.');
        }
        const currentRetrievedAt = new Date().toISOString();
        const currentSource = `https://data.sec.gov/api/xbrl/companyfacts/CIK${current.cik}.json`;
        const currentObject = JSON.stringify(current.data);
        const currentFixtureText = JSON.stringify(filterCompanyFacts(current.data));
        const currentAnnual = normalizer.normalize(current.data, 'XOM', 'annual');
        const currentQuarterly = normalizer.normalize(current.data, 'XOM', 'quarterly');
        await writeFile(`${scratchDir}/XOM-current.json`, currentObject);
        await writeFile(
          `${scratchDir}/XOM-current-normalized.json`,
          JSON.stringify({ annual: currentAnnual, quarterly: currentQuarterly }, null, 2),
        );
        await writeFile(`${fixturesDir}/XOM-current.json`, currentFixtureText);
        summaries.push({
          symbol: 'XOM',
          fixture: 'XOM-current.json',
          cik: current.cik,
          entityName: current.data.entityName,
          resolution: 'Current official SEC ticker map through SecClient',
          annualRecords: currentAnnual.length,
          quarterlyRecords: currentQuarterly.length,
          latestAnnual: currentAnnual.at(-1)
            ? {
                fiscalYear: currentAnnual.at(-1).fiscalYear,
                periodEnd: currentAnnual.at(-1).periodEnd,
                revenue: currentAnnual.at(-1).revenue,
                epsDiluted: currentAnnual.at(-1).epsDiluted,
              }
            : null,
          latestQuarter: currentQuarterly.at(-1)
            ? {
                fiscalYear: currentQuarterly.at(-1).fiscalYear,
                fiscalQuarter: currentQuarterly.at(-1).fiscalQuarter,
                periodStart: currentQuarterly.at(-1).periodStart,
                periodEnd: currentQuarterly.at(-1).periodEnd,
                revenue: currentQuarterly.at(-1).revenue,
                epsDiluted: currentQuarterly.at(-1).epsDiluted,
                operatingCashFlow: currentQuarterly.at(-1).operatingCashFlow,
                capex: currentQuarterly.at(-1).capex,
                freeCashFlow: currentQuarterly.at(-1).freeCashFlow,
              }
            : null,
        });
        provenance.push({
          symbol: 'XOM-current',
          cik: Number(current.cik),
          entityName: current.data.entityName,
          source: currentSource,
          retrievedAt: currentRetrievedAt,
          resolution: 'Current official SEC ticker map through SecClient.',
          sourceObjectSha256: sha256(currentObject),
          fixtureSha256: sha256(currentFixtureText),
          sourceObjectHashMethod:
            'SHA-256 of compact JSON.stringify output from the parsed response object supplied to the filter (SecClient normalizes digit-string CIKs to numbers); this is not a hash of SEC wire bytes.',
          filter:
            'Mapped US-GAAP concepts only; unit rows with end >= 2022-01-01; source fact values, units and filing metadata are preserved. Test-only; UI never imports fixtures.',
        });

        await delay(500);
        const dispatcher = new EnvHttpProxyAgent();
        try {
          const response = await proxyFetch(
            `https://data.sec.gov/api/xbrl/companyfacts/CIK${historicalXomCik}.json`,
            {
              dispatcher,
              signal: AbortSignal.timeout(20000),
              headers: {
                'User-Agent':
                  process.env.SEC_USER_AGENT ??
                  'AtlasResearchTerminal/0.2 (local research prototype)',
                Accept: 'application/json',
                'Accept-Encoding': 'gzip, deflate',
              },
            },
          );
          if (!response.ok)
            throw new Error(`SEC returned ${response.status} for historical XOM CIK`);
          const data = await response.json();
          if (
            data?.cik !== 34088 ||
            data?.entityName !== 'Exxon Mobil Corporation' ||
            !data?.facts?.['us-gaap']
          ) {
            throw new Error(
              'Historical XOM source failed its exact CIK, entity, or US-GAAP identity check.',
            );
          }
          result = { cik: historicalXomCik, data };
          resolution = `Test-only historical Exxon Mobil Corporation CIK ${historicalXomCik}; current SEC ticker map resolves XOM to ${mappedCik} (${current.data.entityName}).`;
          retrievedAt = new Date().toISOString();
        } finally {
          await dispatcher.close();
        }
      }
    } else if (writeFixtures) {
      result = await client.companyFacts(symbol);
      retrievedAt = new Date().toISOString();
    }
    if (!result) throw new Error(`SEC ticker mapping did not resolve ${symbol}`);
    const filtered = filterCompanyFacts(result.data);
    const source = `https://data.sec.gov/api/xbrl/companyfacts/CIK${result.cik}.json`;
    const sourceObject = JSON.stringify(result.data);
    const fixtureText = JSON.stringify(filtered);
    await writeFile(`${scratchDir}/${symbol}.json`, sourceObject);
    const annual = normalizer.normalize(result.data, symbol, 'annual');
    const quarterly = normalizer.normalize(result.data, symbol, 'quarterly');
    await writeFile(
      `${scratchDir}/${symbol}-normalized.json`,
      JSON.stringify({ annual, quarterly }, null, 2),
    );

    const latestAnnual = annual.at(-1);
    const latestQuarter = quarterly.at(-1);
    summaries.push({
      symbol,
      cik: result.cik,
      entityName: result.data.entityName,
      resolution,
      annualRecords: annual.length,
      quarterlyRecords: quarterly.length,
      latestAnnual: latestAnnual
        ? {
            fiscalYear: latestAnnual.fiscalYear,
            periodStart: latestAnnual.periodStart,
            periodEnd: latestAnnual.periodEnd,
            revenue: latestAnnual.revenue,
            epsDiluted: latestAnnual.epsDiluted,
          }
        : null,
      latestQuarter: latestQuarter
        ? {
            fiscalYear: latestQuarter.fiscalYear,
            fiscalQuarter: latestQuarter.fiscalQuarter,
            periodStart: latestQuarter.periodStart,
            periodEnd: latestQuarter.periodEnd,
            revenue: latestQuarter.revenue,
            epsDiluted: latestQuarter.epsDiluted,
            operatingCashFlow: latestQuarter.operatingCashFlow,
            capex: latestQuarter.capex,
            freeCashFlow: latestQuarter.freeCashFlow,
          }
        : null,
    });

    if (writeFixtures) {
      await writeFile(`${fixturesDir}/${symbol}.json`, fixtureText);
      provenance.push({
        symbol,
        cik: Number(result.cik),
        entityName: result.data.entityName,
        source,
        retrievedAt,
        resolution,
        sourceObjectSha256: sha256(sourceObject),
        fixtureSha256: sha256(fixtureText),
        sourceObjectHashMethod:
          'SHA-256 of compact JSON.stringify output from the parsed response object supplied to the filter (SecClient normalizes digit-string CIKs to numbers); this is not a hash of SEC wire bytes.',
        filter:
          'Mapped US-GAAP concepts only; unit rows with end >= 2022-01-01; source fact values, units and filing metadata are preserved. Test-only; UI never imports fixtures.',
      });
    }
  }
  if (writeFixtures) {
    await writeFile(
      `${fixturesDir}/v1-provenance.json`,
      `${JSON.stringify(
        { version: 1, generatedAt: new Date().toISOString(), fixtures: provenance },
        null,
        2,
      )}\n`,
    );
  }
  console.log(
    JSON.stringify(
      {
        startedAt,
        completedAt: new Date().toISOString(),
        userAgent:
          process.env.SEC_USER_AGENT ?? 'AtlasResearchTerminal/0.2 (local research prototype)',
        mode: writeFixtures ? 'write-new-company-fixtures' : 'smoke',
        summaries,
      },
      null,
      2,
    ),
  );
} finally {
  await close();
  await vite.close();
}
