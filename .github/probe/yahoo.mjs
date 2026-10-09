// Temporary probe: which Yahoo endpoints work from GitHub Actions for EPS.
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';
const r1 = await fetch('https://fc.yahoo.com/', { headers: { 'User-Agent': UA }, redirect: 'manual' });
const cookies = (r1.headers.getSetCookie?.() ?? []).map((c) => c.split(';')[0]).join('; ');
console.log('fc', r1.status, 'cookie?', !!cookies);
const r2 = await fetch('https://query2.finance.yahoo.com/v1/test/getcrumb', { headers: { 'User-Agent': UA, Cookie: cookies } });
const crumb = await r2.text();
console.log('crumb', r2.status, crumb.slice(0, 20));
const syms = ['AAPL','MSFT','TSM','NVO','BRK-B','ASML','SHOP','BABA','TM','SONY','INTC','RIVN','ZZZZ'];
const fields = 'symbol,epsTrailingTwelveMonths,trailingPE,epsCurrentYear,epsForward,financialCurrency,currency,quoteType,regularMarketPrice,earningsTimestamp';
for (const host of ['query1', 'query2']) {
  const u = `https://${host}.finance.yahoo.com/v7/finance/quote?symbols=${syms.join(',')}&fields=${fields}&crumb=${encodeURIComponent(crumb)}`;
  const r = await fetch(u, { headers: { 'User-Agent': UA, Cookie: cookies } });
  const j = await r.json().catch(() => null);
  console.log('quote', host, r.status);
  for (const q of j?.quoteResponse?.result ?? []) console.log('  ', JSON.stringify(Object.fromEntries(fields.split(',').map((f) => [f, q[f]]))));
}
// batch size limit check
const many = Array.from({ length: 300 }, (_, i) => ['AAPL','MSFT','GOOG','AMZN','META'][i % 5] + (i < 5 ? '' : ''));
const big = await fetch(`https://query1.finance.yahoo.com/v7/finance/quote?symbols=${[...new Set(many)].join(',')}&crumb=${encodeURIComponent(crumb)}`, { headers: { 'User-Agent': UA, Cookie: cookies } });
console.log('big', big.status);
const now = Math.floor(Date.now() / 1000);
for (const s of ['AAPL', 'TSM', 'NVO', 'MSFT']) {
  const u = `https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/${s}?symbol=${s}&type=annualDilutedEPS,annualBasicEPS,trailingDilutedEPS,quarterlyDilutedEPS&period1=${now - 4 * 365 * 86400}&period2=${now}&crumb=${encodeURIComponent(crumb)}`;
  const r = await fetch(u, { headers: { 'User-Agent': UA, Cookie: cookies } });
  const j = await r.json().catch(() => null);
  console.log('ts', s, r.status);
  for (const x of j?.timeseries?.result ?? []) {
    const type = x.meta?.type?.[0];
    const pts = (x[type] ?? []).filter(Boolean).map((p) => `${p.asOfDate}:${p.reportedValue?.raw}${p.currencyCode ?? ''}`);
    console.log('   ', type, pts.join(' '));
  }
}
