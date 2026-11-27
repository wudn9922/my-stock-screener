import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { financialsKey, parseFinancials } from '../src/fundamentals/FinancialHistory';
import { conservativePeScenarioBaseline } from '../src/fundamentals/conservativePeAi';

const taiwan = JSON.parse(readFileSync('atlas/public/symbols/taiwan.json', 'utf8')) as {
  entries: { symbol: string; name: string }[];
};
const names = new Map(taiwan.entries.map(entry => [entry.symbol, entry.name]));

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
    const baseline = conservativePeScenarioBaseline(history);
    if (!baseline) continue;
    result.push({ symbol: key, market: history.market, companyName: names.get(key) ?? null, baseline });
  } catch {
    // Missing or malformed public financials are not eligible.
  }
}
process.stdout.write(JSON.stringify(result));
