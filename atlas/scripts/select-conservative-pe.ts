import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { financialsKey, parseFinancials } from '../src/fundamentals/FinancialHistory';
import { assessEligibility, conservativeEps, conservativePePriceIssue } from '../src/fundamentals/conservativePe';

const input: unknown = JSON.parse(readFileSync(0, 'utf8'));
if (!Array.isArray(input) || input.some(symbol => typeof symbol !== 'string')) throw new Error('Invalid symbol input');
const result = [];
for (const symbol of input as string[]) {
  const key = financialsKey(symbol);
  if (!key) continue;
  try {
    const parsed = parseFinancials(JSON.parse(readFileSync(resolve('docs/report/financials', key + '.json'), 'utf8')));
    if (parsed.status !== 'ok' || financialsKey(parsed.data.symbol) !== key) continue;
    const history = parsed.data;
    const annual = conservativeEps(history, 'annual');
    if (!assessEligibility(annual).applicable || conservativePePriceIssue(history)) continue;
    const quarters = conservativeEps(history, 'quarterly').slice(-4);
    const ttm = quarters.length === 4 && quarters.every((row, i) => row.eps !== null && Number.isFinite(row.eps) && row.period !== null
      && (i === 0 || row.period === quarters[i - 1]!.period! + 1));
    const total = ttm ? quarters.reduce((sum, row) => sum + row.eps!, 0) : 0;
    const last = annual.at(-1)!;
    const baseline = ttm && total > 0
      ? { eps: total, end: quarters.at(-1)!.end, basis: 'ttm' }
      : { eps: last.eps!, end: last.end, basis: 'annual' };
    result.push({ symbol: key, market: history.market, baseline });
  } catch {
    // Missing or malformed public financials are not eligible.
  }
}
process.stdout.write(JSON.stringify(result));
