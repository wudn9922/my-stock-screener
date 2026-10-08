import { z } from 'zod';
import { conceptMappings } from './FinancialConceptMapper';
import {
  financialMetrics,
  type CompanyFundamentals,
  type FinancialMetric,
  type FinancialPeriod,
  type FinancialSource,
  type FinancialProvenance,
} from './FundamentalsProvider';
import { normalizeSymbol } from '../market-data/MarketDataProvider';
const day = 86400000;
const date = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((s) => Number.isFinite(Date.parse(s)) && new Date(s).toISOString().slice(0, 10) === s);
const rawFactSchema = z.object({
  start: date.optional(),
  end: date,
  val: z.number().finite(),
  accn: z.string().regex(/^\d{10}-\d{2}-\d{6}$/),
  fy: z.number().int().min(1990).max(2200),
  fp: z.enum(['FY', 'Q1', 'Q2', 'Q3', 'Q4']),
  form: z.enum(['10-K', '10-K/A', '10-Q', '10-Q/A']),
  filed: date,
});
const inputSchema = z.object({
  cik: z.number().int().positive(),
  facts: z.record(
    z.string(),
    z.record(
      z.string(),
      z.object({ units: z.record(z.string(), z.array(z.unknown())) }).passthrough(),
    ),
  ),
});
type MappedMetric = Exclude<FinancialMetric, 'freeCashFlow'>;
type Fact = FinancialSource & { metric: MappedMetric; rank: number };
interface Focus {
  end: string;
  year: number;
  quarter: number;
  filed: string;
}
interface FiscalYear {
  year: number;
  start: string;
  end: string | null;
  filed: string;
  annual: boolean;
}
interface Period {
  year: FiscalYear;
  quarter: 1 | 2 | 3 | 4 | null;
  start: string;
  end: string;
}
const days = (start: string, end: string) => (Date.parse(end) - Date.parse(start)) / day + 1;
const after = (end: string) => new Date(Date.parse(end) + day).toISOString().slice(0, 10);
const rangeFor = (quarter: number): [number, number] =>
  quarter === 4 ? [330, 395] : [quarter * 91 - 30, quarter * 91 + 30];
const inRange = (value: number, range: [number, number]) => value >= range[0] && value <= range[1];
const latest = (a: FinancialSource, b: FinancialSource) =>
  b.filed.localeCompare(a.filed) || b.accession.localeCompare(a.accession);
function mode<T>(items: T[]): T {
  const counts = new Map<T, number>();
  for (const v of items) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1])[0][0];
}
function collect(raw: unknown): Fact[] {
  const data = inputSchema.parse(raw),
    taxonomy = data.facts['us-gaap'] ?? {},
    result: Fact[] = [];
  for (const [metric, mapping] of Object.entries(conceptMappings) as [
    MappedMetric,
    (typeof conceptMappings)[MappedMetric],
  ][]) {
    mapping.concepts.forEach((concept, rank) => {
      for (const value of taxonomy[concept]?.units[mapping.unit] ?? []) {
        const parsed = rawFactSchema.safeParse(value);
        if (!parsed.success) continue;
        const f = parsed.data;
        if (
          f.end > f.filed ||
          (mapping.kind === 'instant' && f.start !== undefined) ||
          (mapping.kind === 'duration' && (!f.start || !inRange(days(f.start, f.end), [60, 395])))
        )
          continue;
        result.push({
          metric,
          rank,
          concept: `us-gaap:${concept}`,
          unit: mapping.unit,
          value: f.val,
          accession: f.accn,
          form: f.form,
          filed: f.filed,
          start: f.start ?? null,
          end: f.end,
          fiscalYearFocus: f.fy,
          fiscalPeriodFocus: f.fp,
          kind: mapping.kind,
          secUrl: `https://data.sec.gov/api/xbrl/companyfacts/CIK${String(data.cik).padStart(10, '0')}.json`,
          filingUrl: `https://www.sec.gov/Archives/edgar/data/${data.cik}/${f.accn.replaceAll('-', '')}/`,
        });
      }
    });
  }
  return result;
}
/** Infer the filing focus from its current duration end, not comparative facts' fy/fp labels. */
function calendars(facts: Fact[]): FiscalYear[] {
  const byAccession = new Map<string, Fact[]>();
  for (const f of facts)
    if (f.kind === 'duration') {
      const list = byAccession.get(f.accession) ?? [];
      list.push(f);
      byAccession.set(f.accession, list);
    }
  const focuses = new Map<string, Focus>();
  for (const [accession, list] of byAccession) {
    const end = list.reduce((e, f) => (f.end > e ? f.end : e), ''),
      current = list.filter((f) => f.end === end);
    const key = mode(current.map((f) => `${f.fiscalYearFocus}:${f.fiscalPeriodFocus}`)),
      [fy, fp] = key.split(':');
    focuses.set(accession, {
      end,
      year: Number(fy),
      quarter: fp === 'FY' ? 4 : Number(fp.slice(1)),
      filed: current[0].filed,
    });
  }
  const candidates = new Map<number, FiscalYear[]>();
  for (const f of facts) {
    if (!f.start) continue;
    const focus = focuses.get(f.accession);
    if (!focus) continue;
    const annual = f.form.startsWith('10-K');
    const length = days(f.start, f.end);
    if (!inRange(length, rangeFor(annual ? 4 : focus.quarter))) continue;
    const delta = (Date.parse(focus.end) - Date.parse(f.end)) / day,
      years = Math.round(delta / 365.2425);
    if (years < 0 || Math.abs(delta - years * 365.2425) > 35) continue;
    const year = focus.year - years,
      list = candidates.get(year) ?? [];
    list.push({ year, start: f.start, end: annual ? f.end : null, filed: f.filed, annual });
    candidates.set(year, list);
  }
  return [...candidates.values()]
    .map(
      (list) =>
        list.sort(
          (a, b) => Number(b.annual) - Number(a.annual) || b.filed.localeCompare(a.filed),
        )[0],
    )
    .sort((a, b) => a.year - b.year);
}
function periods(years: FiscalYear[], facts: Fact[], kind: FinancialPeriod): Period[] {
  if (kind === 'annual')
    return years.flatMap((year) =>
      year.end ? [{ year, quarter: null, start: year.start, end: year.end }] : [],
    );
  return years.flatMap((year) => {
    const candidates = new Map<number, Fact[]>();
    for (const f of facts) {
      if (!f.start || f.start < year.start || f.end < year.start || (year.end && f.end > year.end))
        continue;
      const elapsed = days(year.start, f.end),
        q = Math.round(elapsed / 91.25);
      if (q < 1 || q > 4 || !inRange(elapsed, rangeFor(q)) || (q === 4 && f.end !== year.end))
        continue;
      if (f.start !== year.start && !inRange(days(f.start, f.end), [60, 120])) continue;
      const list = candidates.get(q) ?? [];
      list.push(f);
      candidates.set(q, list);
    }
    const ends = new Map<number, string>();
    for (const [q, list] of candidates) ends.set(q, mode(list.map((f) => f.end)));
    const result: Period[] = [];
    for (const [q, end] of [...ends].sort((a, b) => a[0] - b[0])) {
      const previous = ends.get(q - 1),
        direct = facts.filter(
          (f) =>
            f.start &&
            f.end === end &&
            f.start >= year.start &&
            inRange(days(f.start, f.end), [60, 120]),
        );
      const start =
        q === 1
          ? year.start
          : previous
            ? after(previous)
            : direct.length
              ? mode(direct.map((f) => f.start!))
              : null;
      if (!start || !inRange(days(start, end), [60, 120])) continue;
      result.push({ year, quarter: q as 1 | 2 | 3 | 4, start, end });
    }
    return result;
  });
}
function publicSource(f: Fact): FinancialSource {
  const { metric, rank, ...source } = f;
  void metric;
  void rank;
  return source;
}
function provenance(f: Fact): FinancialProvenance {
  return { derived: false, calculation: null, inputs: [publicSource(f)] };
}
/** Conservative standalone quarters. Conflicting same-filing values and unsafe alignment remain null. */
export class FinancialNormalizer {
  normalize(raw: unknown, symbol: string, kind: FinancialPeriod): CompanyFundamentals[] {
    symbol = normalizeSymbol(symbol);
    const issuer = z.object({cik:z.number().int().positive(),entityName:z.string().optional()}).parse(raw);
    const facts = collect(raw),
      years = calendars(facts);
    return periods(years, facts, kind)
      .map((period) => {
        const warnings: string[] = [];
        const blocked = new Set<FinancialMetric>();
        const select = (
          metric: MappedMetric,
          start: string | null,
          end: string,
          options: { concept?: string; cutoff?: string } = {},
        ) => {
          const list = facts
            .filter(
              (f) =>
                f.metric === metric &&
                f.start === start &&
                f.end === end &&
                (!options.concept || f.concept === options.concept) &&
                (!options.cutoff || f.filed <= options.cutoff),
            )
            .sort((a, b) => a.rank - b.rank || latest(a, b));
          const chosen = list[0];
          if (!chosen) return { fact: null, conflict: false };
          const same = list.filter(
            (f) =>
              f.concept === chosen.concept &&
              f.accession === chosen.accession &&
              f.filed === chosen.filed,
          );
          if (new Set(same.map((f) => f.value)).size > 1) {
            warnings.push(`${metric}: conflicting facts in ${chosen.accession}; N/A`);
            return { fact: null, conflict: true };
          }
          return { fact: chosen, conflict: false };
        };
        const values = Object.fromEntries(financialMetrics.map((m) => [m, null])) as Record<
          FinancialMetric,
          number | null
        >;
        const sources: CompanyFundamentals['sourceConcepts'] = {};
        for (const metric of Object.keys(conceptMappings) as MappedMetric[]) {
          const mapping = conceptMappings[metric],
            selected = select(metric, mapping.kind === 'instant' ? null : period.start, period.end);
          if (selected.conflict) blocked.add(metric);
          if (selected.fact) {
            values[metric] = selected.fact.value;
            sources[metric] = provenance(selected.fact);
            if (metric === 'capex' && selected.fact.value < 0) {
              values[metric] = Math.abs(selected.fact.value);
              sources[metric] = {
                ...provenance(selected.fact),
                derived: true,
                calculation: 'CapEx = abs(raw payments); positive cash outflow convention',
              };
            }
            continue;
          }
          // EPS uses weighted-average shares: cumulative EPS subtraction is never valid here.
          if (
            selected.conflict ||
            mapping.kind === 'instant' ||
            kind === 'annual' ||
            metric.startsWith('eps') ||
            period.quarter === 1
          )
            continue;
          const cumulative = select(metric, period.year.start, period.end);
          if (!cumulative.fact) continue;
          const current = cumulative.fact,
            previousEnd = new Date(Date.parse(period.start) - day).toISOString().slice(0, 10);
          const prior = select(metric, period.year.start, previousEnd, {
            concept: current.concept,
            cutoff: current.filed,
          });
          if (!prior.fact || prior.fact.unit !== current.unit) continue;
          const previous = prior.fact;
          if (
            metric === 'capex' &&
            Math.sign(current.value) !== Math.sign(previous.value) &&
            previous.value !== 0 &&
            current.value !== 0
          )
            continue;
          const value =
            metric === 'capex'
              ? Math.abs(current.value) - Math.abs(previous.value)
              : current.value - previous.value;
          if (metric === 'capex' && value < 0) {
            warnings.push('capex: cumulative outflows decreased; quarterly CapEx is N/A');
            continue;
          }
          values[metric] = value;
          sources[metric] = {
            derived: true,
            calculation: `${metric} Q${period.quarter} = ${metric === 'capex' ? 'abs(current cumulative) − abs(previous cumulative)' : 'current cumulative − previous cumulative'} (${metric === 'capex' ? Math.abs(current.value) : current.value} − ${metric === 'capex' ? Math.abs(previous.value) : previous.value}); aligned fiscal start ${period.year.start}, quarter ${period.start} to ${period.end}; latest baseline filed no later than minuend`,
            inputs: [publicSource(current), publicSource(previous)],
          };
        }
        const derive = (
          metric: FinancialMetric,
          a: FinancialMetric,
          b: FinancialMetric,
          formula: string,
        ) => {
          if (blocked.has(metric) || values[a] === null || values[b] === null) return;
          values[metric] = values[a]! - values[b]!;
          sources[metric] = {
            derived: true,
            calculation: `${formula} (${values[a]} − ${values[b]}); ${a}: ${sources[a]?.calculation ?? 'raw standalone value'}; ${b}: ${sources[b]?.calculation ?? 'raw standalone value'}`,
            inputs: [...(sources[a]?.inputs ?? []), ...(sources[b]?.inputs ?? [])],
          };
        };
        if (values.grossProfit === null)
          derive(
            'grossProfit',
            'revenue',
            'costOfRevenue',
            'Gross Profit = Revenue − Cost of Revenue',
          );
        derive(
          'freeCashFlow',
          'operatingCashFlow',
          'capex',
          'Free Cash Flow = Operating Cash Flow − CapEx',
        );
        const inputs = Object.values(sources)
            .flatMap((s) => s?.inputs ?? [])
            .sort(latest),
          head =
            sources.revenue?.inputs.find((s) => s.kind === 'duration' && s.end === period.end) ??
            inputs.find((s) => s.kind === 'duration' && s.end === period.end) ??
            inputs[0];
        return {
          ...values,
          symbol,
          issuerName: issuer.entityName,
          cik: String(issuer.cik).padStart(10, '0'),
          period: kind,
          fiscalYear: period.year.year,
          fiscalQuarter: period.quarter,
          periodStart: period.start,
          periodEnd: period.end,
          filingDate: head?.filed ?? period.year.filed,
          form: head?.form ?? '',
          accession: head?.accession ?? '',
          sourceConcepts: sources,
          warnings,
        };
      })
      .filter((r) => financialMetrics.some((m) => r[m] !== null))
      .sort((a, b) => a.periodEnd.localeCompare(b.periodEnd));
  }
}
