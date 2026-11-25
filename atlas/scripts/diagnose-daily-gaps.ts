/** Offline scan. Bundle with esbuild and pass CURRENT_ARCHIVE PREVIOUS_ARCHIVE DATA_DIR CLOSURES_JSON.
 * Each archive directory must be a complete checkout of a pinned kline-archive revision.
 * DATA_DIR is a local daily snapshot, used as a comparison sample rather than an authenticated live feed.
 * Optional arguments are SYMBOL (default ZTS) and Yahoo-shaped recent and full daily responses for that symbol.
 */
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { ArchiveFirstProvider, archivePeriod } from '../src/market-data/ArchiveFirstProvider';
import { EdgeYahooProvider } from '../src/market-data/EdgeYahooProvider';
import { KlineArchive, mergeArchiveDaily, parseArchiveCsv, parseArchiveIndex } from '../src/market-data/KlineArchive';
import { validKline, usableBarResult } from '../src/market-data/KlineIntegrity';
import { normalizeLive } from '../src/market-data/LiveQuotes';
import { closedMarketDates, marketClosuresSchema } from '../src/market-data/MarketClosures';
import { calendarDate, getMarketProfile } from '../src/market-data/MarketProfile';
import type { Bar, BarResult } from '../src/market-data/MarketDataProvider';
import { MarketProxyAccess, staticTokenSource } from '../src/market-data/marketProxyAccess';
import { quarantined } from '../../docs/report/kline-corrections.json';

const [current, previous, data, closures, traceSymbol = 'ZTS', recentFile, fullFile] = process.argv.slice(2);
if (!current || !previous || !data || !closures) {
  throw new Error('Usage: diagnose-daily-gaps CURRENT_ARCHIVE PREVIOUS_ARCHIVE DATA_DIR CLOSURES_JSON [SYMBOL] [RECENT_JSON|-] [FULL_JSON]');
}
const read = (path: string) => readFileSync(path, 'utf8');
const print = (record: object) => console.log(JSON.stringify(record));
const hash = (body: string) => createHash('sha256').update(body).digest('hex');
const policy = marketClosuresSchema.parse(JSON.parse(read(closures)));
const header = 'Date,Open,High,Low,Close,Volume\n';
const quarantine = new Set(quarantined.map(row => `${row.symbol}:${row.date}`));
const recentCutoff = (last: string) => new Date(Date.parse(last) - 31 * 86_400_000).toISOString().slice(0, 10);

function parseRecent(lines: string[], symbol: string, since: string): Bar[] {
  const byYear = new Map<number, string[]>();
  for (const line of lines) {
    if (line.slice(0, 10) < since) continue;
    const year = Number(line.slice(0, 4));
    const entries = byYear.get(year) ?? [];
    entries.push(line);
    byYear.set(year, entries);
  }
  return [...byYear].flatMap(([year, entries]) => parseArchiveCsv(header + entries.join('\n'), symbol, year).bars);
}

for (const market of ['tw', 'us'] as const) {
  const closed = closedMarketDates(market === 'tw' ? '^TWII' : '^GSPC', policy);
  const counts = { symbols: 0, comparable: 0, splitMismatch: 0, mixedRevision: 0, inconsistentArchive: 0, filtered: 0, heavilyFiltered: 0 };
  for (const file of readdirSync(join(current, 'index', market)).sort()) {
    if (!file.endsWith('.json')) continue;
    const raw = JSON.parse(read(join(current, 'index', market, file)));
    const symbol: string = raw.symbol;
    const entry = parseArchiveIndex(raw, symbol)!;
    const oldPath = join(previous, 'index', market, file);
    const old = existsSync(oldPath) ? parseArchiveIndex(JSON.parse(read(oldPath)), symbol) : null;
    const since = recentCutoff(entry.lastDate);
    const retained: string[] = [];
    const lost: { date: string; reason: string }[] = [];
    const mixedYears: { year: string; rows: number }[] = [];
    const badYears: string[] = [];
    counts.symbols++;
    for (const [year, detail] of Object.entries(entry.years)) {
      const filename = join(current, decodeURIComponent(entry.path), `${year}.csv`);
      if (!existsSync(filename)) { badYears.push(year); continue; }
      const body = read(filename), sha = hash(body);
      if (sha !== detail.sha256) badYears.push(year);
      if (old?.years[year] && old.years[year].sha256 !== sha) mixedYears.push({ year, rows: detail.rows });
      for (const line of body.trimEnd().split(/\r?\n/).slice(1)) {
        const [date, o, h, l, c, v] = line.split(',');
        const valid = validKline({ time: 0, open: Number(o), high: Number(h), low: Number(l), close: Number(c), volume: Number(v) });
        const reason = !valid ? 'invalid OHLCV' : quarantine.has(`${symbol}:${date}`) ? 'quarantine' : closed.has(date!) ? 'closure' : '';
        if (reason) lost.push({ date: date!, reason });
        else if (date! >= since) retained.push(line);
      }
    }
    const local = join(data, `${symbol}.csv`);
    const profile = getMarketProfile(symbol);
    const archiveBars = retained.length ? parseRecent(retained, symbol, since) : [];
    let splitMismatch = false;
    if (archiveBars.length && existsSync(local)) {
      const recent = parseRecent(read(local).trimEnd().split(/\r?\n/).slice(1).filter(line => line.slice(0, 10) >= since), symbol, since);
      const dates = new Set(archiveBars.map(bar => calendarDate(bar.time, profile)));
      if (recent.some(bar => dates.has(calendarDate(bar.time, profile)))) {
        counts.comparable++;
        splitMismatch = mergeArchiveDaily(archiveBars, recent, symbol, entry.lastDate, closed).splitMismatch;
      }
    }
    if (splitMismatch) counts.splitMismatch++;
    if (mixedYears.length) counts.mixedRevision++;
    if (badYears.length) counts.inconsistentArchive++;
    if (lost.length) counts.filtered++;
    if (lost.length >= 2) counts.heavilyFiltered++;
    if (splitMismatch || mixedYears.length || badYears.length || lost.length) print({ symbol, splitMismatch, mixedYears, badYears, lost });
  }
  print({ market, ...counts });
}

const pinnedIndex = join('index', getMarketProfile(traceSymbol).market.toLowerCase(), `${encodeURIComponent(traceSymbol)}.json`);
const makeArchive = (indexRoot: string) => new KlineArchive({ cache: false, baseUrl: 'https://archive.test/',
  fetcher: async (url, init) => {
    const path = decodeURIComponent(new URL(String(url), 'https://archive.test/').pathname.slice(1));
    const origin = path.startsWith('index/') && init?.cache !== 'reload' ? indexRoot : current;
    const file = path.endsWith('market-closures.json') ? closures : join(origin, path);
    return existsSync(file) ? new Response(read(file)) : new Response(null, { status: 404 });
  },
});
const profile = getMarketProfile(traceSymbol);
const dates = (bars: Bar[]) => bars.map(bar => calendarDate(bar.time, profile));
const complete = await makeArchive(current).getDaily(traceSymbol);
if (!complete) throw new Error(`Missing ${pinnedIndex}`);
const expected = dates(complete.bars);
function trace(stage: string, result: BarResult) {
  const actual = new Set(dates(result.bars));
  print({ stage, symbol: traceSymbol, bars: actual.size, source: result.source, missing: expected.filter(day => !actual.has(day)) });
}
trace('coherent getDaily', complete);
const mixed = makeArchive(previous);
const mixedDaily = await mixed.getDaily(traceSymbol);
if (mixedDaily) {
  trace('mixed getDaily', mixedDaily);
  trace('mixed archivePeriod', archivePeriod(mixedDaily, traceSymbol, '1D'));
  const unexpected = async (): Promise<never> => { throw new Error('Unexpected Yahoo fallback'); };
  const provider = new ArchiveFirstProvider({ id: 'offline', supportedTimeframes: ['1D'], getBars: unexpected, getQuote: unexpected }, mixed);
  trace('mixed ArchiveFirstProvider', await provider.getBars(traceSymbol, '1D'));
}
const local = join(data, `${traceSymbol}.csv`);
const recentInput = recentFile && recentFile !== '-' ? recentFile : undefined;
const live = recentInput ? normalizeLive(JSON.parse(read(recentInput)), traceSymbol, Date.now() / 1000) : null;
if (recentInput && !live) print({ stage: 'normalizeLive', error: 'recent Yahoo response rejected' });
const localLines = existsSync(local) ? read(local).trimEnd().split(/\r?\n/).slice(1) : [];
const recent = live?.bars ?? parseRecent(localLines, traceSymbol, recentCutoff(expected.at(-1)!));
const merged = mergeArchiveDaily(complete.bars, recent, traceSymbol, undefined, new Set(complete.closedSessions));
print({ stage: live ? 'provided normalizeLive / mergeArchiveDaily' : 'snapshot mergeArchiveDaily', recent: recent.length, splitMismatch: merged.splitMismatch });
trace('merged usableBarResult', usableBarResult({ ...complete, bars: merged.bars }, traceSymbol, '1D'));
if (fullFile && recent.length) {
  const yahoo = new EdgeYahooProvider('https://fixture.test/', {
    access: new MarketProxyAccess(staticTokenSource('offline-fixture')),
    fetcher: async () => new Response(read(fullFile)),
  });
  try {
    trace('Yahoo getBars', await yahoo.getBars(traceSymbol, '1D'));
  } catch (error) {
    print({ stage: 'Yahoo getBars', error: String(error) });
  }
  const provider = new ArchiveFirstProvider(yahoo, makeArchive(current), yahoo);
  const split = archivePeriod({ ...complete, bars: recent, archive: { ...complete.archive!, status: 'split' } }, traceSymbol, '1D');
  trace('simulated split recent segment', split);
  trace('split fallback getOlderBars', await provider.getOlderBars(traceSymbol, '1D', split));
}
