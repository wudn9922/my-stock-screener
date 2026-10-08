import { z } from 'zod';
import { financialMetrics, type FinancialMetric } from './FundamentalsProvider';
const date = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const source = z.object({
  concept: z.string(),
  unit: z.string(),
  value: z.number().finite(),
  accession: z.string(),
  form: z.string(),
  filed: date,
  secUrl: z.url(),
  filingUrl: z.url(),
  start: date.nullable(),
  end: date,
  fiscalYearFocus: z.number().int(),
  fiscalPeriodFocus: z.string(),
  kind: z.enum(['instant', 'duration']),
});
const provenance = z.object({
  derived: z.boolean(),
  calculation: z.string().nullable(),
  inputs: z.array(source).min(1),
});
const metricFields = Object.fromEntries(
  financialMetrics.map((metric) => [metric, z.number().finite().nullable()]),
) as Record<FinancialMetric, z.ZodNullable<z.ZodNumber>>;
export const financialSchema = z
  .object({
    symbol: z.string(),
    issuerName: z.string().optional(),
    cik: z.string().regex(/^[0-9]{10}$/).optional(),
    period: z.enum(['quarterly', 'annual']),
    fiscalYear: z.number().int(),
    fiscalQuarter: z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(4)]).nullable(),
    periodStart: date,
    periodEnd: date,
    filingDate: date,
    form: z.string(),
    accession: z.string(),
    sourceConcepts: z.partialRecord(z.enum(financialMetrics), provenance),
    warnings: z.array(z.string()),
    ...metricFields,
  })
  .superRefine((r, c) => {
    if (r.periodStart > r.periodEnd || (r.period === 'annual') !== (r.fiscalQuarter === null))
      c.addIssue({ code: 'custom', message: 'Invalid fiscal period' });
    for (const metric of financialMetrics) {
      const p = r.sourceConcepts[metric];
      if (r[metric] !== null && !p)
        c.addIssue({ code: 'custom', message: 'Metric provenance missing', path: [metric] });
      if (!p) continue;
      if (
        r[metric] === null ||
        (p.derived
          ? !p.calculation
          : p.calculation !== null || p.inputs.length !== 1 || p.inputs[0].value !== r[metric])
      )
        c.addIssue({ code: 'custom', message: 'Invalid metric provenance', path: [metric] });
      for (const source of p.inputs)
        if (source.unit !== (metric.startsWith('eps') ? 'USD/shares' : 'USD'))
          c.addIssue({ code: 'custom', message: 'Unexpected metric unit', path: [metric] });
    }
  });
