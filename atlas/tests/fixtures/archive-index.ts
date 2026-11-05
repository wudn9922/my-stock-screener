import { createHash } from 'node:crypto';

/** PR-B index v1 wire fixture, hashes generated independently with Node crypto. */
export function archiveIndexFixture(symbol: string, files: Record<number, string>) {
  const market = /\.(TW|TWO)$/.test(symbol) || ['^TWII', '^TWOII'].includes(symbol) ? 'tw' : 'us';
  const years = Object.fromEntries(Object.entries(files).map(([year, body]) => {
    const rows = body.trim().split(/\r?\n/).slice(1);
    return [year, { firstDate: rows[0]!.split(',')[0]!, lastDate: rows.at(-1)!.split(',')[0]!,
      rows: rows.length, csvBytes: Buffer.byteLength(body), sha256: createHash('sha256').update(body).digest('hex') }];
  }));
  const dates = Object.values(years);
  return { version: 1, symbol, market, path: `${market}/${encodeURIComponent(symbol)}`,
    firstDate: dates.map(v => v.firstDate).sort()[0]!, lastDate: dates.map(v => v.lastDate).sort().at(-1)!, years };
}
