import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { FinancialNormalizer } from '../src/fundamentals/FinancialNormalizer';
import { financialSchema } from '../src/fundamentals/financialSchema';
import { SecClient } from '../src/fundamentals/SecClient';

const normalizer = new FinancialNormalizer();
const symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'JPM', 'WMT', 'XOM'] as const;
const readJson = (path: string) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const fixture = (symbol: string) => readJson(`./fixtures/sec/${symbol}.json`);
const sha256 = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');

describe('expanded official SEC companyfacts fixtures', () => {
  it.each([
    {
      symbol: 'AAPL',
      counts: [4, 18],
      annual: [2025, '2025-09-27', 416161000000, 7.46],
      quarter: [2026, 3, '2026-06-27', 109417000000, 2.02],
    },
    {
      symbol: 'MSFT',
      counts: [5, 18],
      annual: [2026, '2026-06-30', 331839000000, 17.95],
      quarter: [2026, 4, '2026-06-30', 90007000000, null],
    },
    {
      symbol: 'NVDA',
      counts: [5, 18],
      annual: [2026, '2026-01-25', 215938000000, 4.9],
      quarter: [2027, 2, '2026-07-26', 96221000000, 2.46],
    },
    {
      symbol: 'TSLA',
      counts: [4, 18],
      annual: [2025, '2025-12-31', 94827000000, 1.08],
      quarter: [2026, 2, '2026-06-30', 28236000000, 0.32],
    },
    {
      symbol: 'JPM',
      counts: [4, 18],
      annual: [2025, '2025-12-31', 182447000000, 20.02],
      quarter: [2026, 2, '2026-06-30', 57347000000, 7.7],
    },
    {
      symbol: 'WMT',
      counts: [5, 18],
      annual: [2026, '2026-01-31', 706413000000, 2.73],
      quarter: [2027, 2, '2026-07-31', 186100000000, 0.8],
    },
    {
      symbol: 'XOM',
      counts: [4, 17],
      annual: [2025, '2025-12-31', 332238000000, 6.7],
      quarter: [2026, 1, '2026-03-31', 85138000000, 1],
    },
  ] as const)(
    '$symbol keeps actual annual and latest-quarter revenue/EPS values with fiscal labels',
    ({ symbol, counts, annual: annualExpected, quarter: quarterExpected }) => {
      const annual = normalizer.normalize(fixture(symbol), symbol, 'annual');
      const quarterly = normalizer.normalize(fixture(symbol), symbol, 'quarterly');
      const latestAnnual = annual.at(-1)!;
      const latestQuarter = quarterly.at(-1)!;

      expect([annual.length, quarterly.length]).toEqual(counts);
      expect([
        latestAnnual.fiscalYear,
        latestAnnual.periodEnd,
        latestAnnual.revenue,
        latestAnnual.epsDiluted,
      ]).toEqual(annualExpected);
      expect([
        latestQuarter.fiscalYear,
        latestQuarter.fiscalQuarter,
        latestQuarter.periodEnd,
        latestQuarter.revenue,
        latestQuarter.epsDiluted,
      ]).toEqual(quarterExpected);
      for (const record of [...annual, ...quarterly])
        expect(financialSchema.safeParse(record).success).toBe(true);
    },
  );

  it('preserves source units, instant/duration kinds, fiscal periods, and YTD calculation lineage', () => {
    for (const symbol of symbols) {
      for (const period of ['annual', 'quarterly'] as const) {
        for (const record of normalizer.normalize(fixture(symbol), symbol, period)) {
          for (const [metric, provenance] of Object.entries(record.sourceConcepts)) {
            if (!provenance) continue;
            expect(provenance.inputs.length).toBeGreaterThan(0);
            for (const source of provenance.inputs) {
              expect(source.secUrl).toBe(
                `https://data.sec.gov/api/xbrl/companyfacts/CIK${String(fixture(symbol).cik).padStart(10, '0')}.json`,
              );
              expect(source.filingUrl).toContain(source.accession.replaceAll('-', ''));
              expect(source.unit).toBe(metric?.startsWith('eps') ? 'USD/shares' : 'USD');
              expect(source.kind).toBe(
                ['cash', 'totalAssets', 'totalLiabilities', 'equity'].includes(metric!)
                  ? 'instant'
                  : 'duration',
              );
              expect(source.start === null).toBe(source.kind === 'instant');
              expect(source.filed >= source.end).toBe(true);
            }
          }
        }
      }
    }

    const aapl = normalizer.normalize(fixture('AAPL'), 'AAPL', 'quarterly').at(-1)!;
    const aaplRevenue = aapl.sourceConcepts.revenue!.inputs[0];
    const aaplCash = aapl.sourceConcepts.cash!.inputs[0];
    const aaplOcf = aapl.sourceConcepts.operatingCashFlow!;
    expect(aaplRevenue).toMatchObject({
      concept: 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
      unit: 'USD',
      kind: 'duration',
      start: aapl.periodStart,
      end: aapl.periodEnd,
    });
    expect(aaplCash).toMatchObject({
      unit: 'USD',
      kind: 'instant',
      start: null,
      end: aapl.periodEnd,
    });
    expect(aaplOcf.derived).toBe(true);
    expect(aaplOcf.inputs.map(({ start, end }) => [start, end])).toEqual([
      ['2025-09-28', '2026-06-27'],
      ['2025-09-28', '2026-03-28'],
    ]);
    expect(aaplOcf.calculation).toContain('current cumulative − previous cumulative');

    const msftQ4 = normalizer.normalize(fixture('MSFT'), 'MSFT', 'quarterly').at(-1)!;
    expect(msftQ4.sourceConcepts.revenue).toMatchObject({
      derived: true,
      inputs: [
        { start: '2025-07-01', end: '2026-06-30', unit: 'USD', kind: 'duration' },
        { start: '2025-07-01', end: '2026-03-31', unit: 'USD', kind: 'duration' },
      ],
    });

    const jpmAnnual = normalizer.normalize(fixture('JPM'), 'JPM', 'annual').at(-1)!;
    const jpmQ2 = normalizer.normalize(fixture('JPM'), 'JPM', 'quarterly').at(-1)!;
    expect(jpmAnnual.sourceConcepts.revenue!.inputs[0].concept).toBe('us-gaap:Revenues');
    expect(jpmQ2.revenue).toBe(57347000000);
    expect(jpmQ2.sourceConcepts.revenue).toMatchObject({
      derived: false,
      calculation: null,
      inputs: [
        {
          concept: 'us-gaap:RevenuesNetOfInterestExpense',
          unit: 'USD',
          value: 57347000000,
          start: '2026-04-01',
          end: '2026-06-30',
          kind: 'duration',
          form: '10-Q',
          filed: '2026-08-06',
          accession: '0001628280-26-054343',
        },
      ],
    });
  });

  it('leaves unsupported actual filing values null and keeps the historical XOM fixture distinct from current resolution', () => {
    const jpmQ2 = normalizer.normalize(fixture('JPM'), 'JPM', 'quarterly').at(-1)!;
    expect(jpmQ2).toMatchObject({
      grossProfit: null,
      operatingExpenses: null,
      capex: null,
      cash: null,
      freeCashFlow: null,
    });
    for (const metric of ['grossProfit', 'operatingExpenses', 'capex', 'cash', 'freeCashFlow'])
      expect(jpmQ2.sourceConcepts[metric as keyof typeof jpmQ2.sourceConcepts]).toBeUndefined();

    const wmtQ2 = normalizer.normalize(fixture('WMT'), 'WMT', 'quarterly').at(-1)!;
    expect(wmtQ2.totalLiabilities).toBeNull();
    expect(wmtQ2.sourceConcepts.totalLiabilities).toBeUndefined();

    const xom = fixture('XOM');
    expect(xom).toMatchObject({ cik: 34088, entityName: 'Exxon Mobil Corporation' });
    const xomQ1 = normalizer.normalize(xom, 'XOM', 'quarterly').at(-1)!;
    expect(xomQ1).toMatchObject({ grossProfit: null, operatingExpenses: null });

    const provenance = readJson('./fixtures/sec/v1-provenance.json');
    const xomSource = provenance.fixtures.find(
      (entry: { symbol: string }) => entry.symbol === 'XOM',
    );
    expect(xomSource.resolution).toContain('historical Exxon Mobil Corporation CIK 0000034088');
    expect(xomSource.resolution).toContain('current SEC ticker map resolves XOM to 0002115436');

    const currentXom = fixture('XOM-current');
    expect(currentXom).toMatchObject({ cik: 2115436, entityName: 'ExxonMobil Holdings Corp' });
    const currentAnnual = normalizer.normalize(currentXom, 'XOM', 'annual');
    const currentQuarterly = normalizer.normalize(currentXom, 'XOM', 'quarterly');
    expect(currentAnnual).toHaveLength(0);
    expect(currentQuarterly).toHaveLength(2);
    for (const record of [...currentAnnual, ...currentQuarterly])
      expect(financialSchema.safeParse(record).success).toBe(true);
    expect(currentQuarterly.at(-1)).toMatchObject({
      fiscalYear: 2026,
      fiscalQuarter: 2,
      periodStart: '2026-04-01',
      periodEnd: '2026-06-30',
      revenue: 116017000000,
      epsDiluted: 3.48,
      operatingCashFlow: null,
      capex: null,
      freeCashFlow: null,
    });
    expect(currentQuarterly.at(-1)!.sourceConcepts.revenue!.inputs[0].concept).toBe(
      'us-gaap:Revenues',
    );
  });

  it('records official source URLs, retrieval timestamps, and fixture hashes separately from prior provenance', () => {
    const manifest = readJson('./fixtures/sec/v1-provenance.json');
    expect(manifest.version).toBe(1);
    expect(manifest.fixtures.map((entry: { symbol: string }) => entry.symbol).sort()).toEqual([
      'JPM',
      'WMT',
      'XOM',
      'XOM-current',
    ]);
    for (const entry of manifest.fixtures as Array<{
      symbol: string;
      cik: number;
      source: string;
      retrievedAt: string;
      sourceObjectSha256: string;
      fixtureSha256: string;
      sourceObjectHashMethod: string;
      filter: string;
    }>) {
      const fixtureText = readFileSync(
        new URL(`./fixtures/sec/${entry.symbol}.json`, import.meta.url),
      );
      const raw = JSON.parse(fixtureText.toString('utf8'));
      expect(entry.source).toBe(
        `https://data.sec.gov/api/xbrl/companyfacts/CIK${String(entry.cik).padStart(10, '0')}.json`,
      );
      expect(Number.isNaN(Date.parse(entry.retrievedAt))).toBe(false);
      expect(entry.sourceObjectSha256).toMatch(/^[a-f0-9]{64}$/);
      expect(entry.fixtureSha256).toBe(sha256(fixtureText));
      expect(entry.sourceObjectHashMethod).toContain('not a hash of SEC wire bytes');
      expect(entry.filter).toContain('Test-only');
      expect(raw.cik).toBe(entry.cik);
    }

    const previous = readJson('./fixtures/sec/provenance.json');
    expect(previous.map((entry: { symbol: string }) => entry.symbol)).toEqual([
      'AAPL',
      'MSFT',
      'NVDA',
      'TSLA',
    ]);
  });
});

describe('SEC string CIK identity', () => {
  const tickerMap = { '0': { ticker: 'XOM', cik_str: 2115436 } };

  it('accepts a digit string only when its numeric CIK matches the resolved ticker CIK', async () => {
    const transport = async (url: string) =>
      url.includes('company_tickers')
        ? tickerMap
        : { cik: '2115436', entityName: 'ExxonMobil Holdings Corp', facts: { ffd: {} } };
    const client = new SecClient(transport, 'Atlas SEC expanded test', 0);
    const result = await client.companyFacts('XOM');

    expect(result).toMatchObject({
      cik: '0002115436',
      data: { cik: 2115436, entityName: 'ExxonMobil Holdings Corp' },
    });
    expect(normalizer.normalize(result!.data, 'XOM', 'annual')).toEqual([]);
  });

  it('rejects a digit-string CIK that belongs to a different issuer', async () => {
    const transport = async (url: string) =>
      url.includes('company_tickers')
        ? tickerMap
        : { cik: '34088', entityName: 'Exxon Mobil Corporation', facts: { 'us-gaap': {} } };
    const client = new SecClient(transport, 'Atlas SEC expanded test', 0);

    await expect(client.companyFacts('XOM')).rejects.toThrow('identity/schema mismatch');
  });
});
