import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { financialsKey, parseFinancials } from '../src/fundamentals/FinancialHistory';
import { conservativePeScenarioBaseline } from '../src/fundamentals/conservativePeAi';

const taiwan = JSON.parse(readFileSync('atlas/public/symbols/taiwan.json', 'utf8')) as {
  entries: { symbol: string; name: string }[];
};
const names = new Map(taiwan.entries.map(entry => [entry.symbol, entry.name]));

// The deployed directory contains cleaned public NasdaqTrader issuer names.
// Reuse it instead of making US news coverage depend on SEC being reachable.
try {
  const directory = JSON.parse(readFileSync('docs/atlas/symbols/directory.json', 'utf8')) as { version?: number; items?: unknown[] };
  if (directory.version === 1 && Array.isArray(directory.items)) {
    for (const row of directory.items) {
      if (!Array.isArray(row) || row.length !== 3 || typeof row[0] !== 'string' || typeof row[1] !== 'string') continue;
      const key = financialsKey(row[0]);
      const name = row[1].trim();
      if (key && name.length > 0 && name.length <= 200 && !names.has(key)) names.set(key, name);
    }
  }
} catch {
  // Optional public directory; the existing SEC issuer lookup remains a fallback.
}

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
