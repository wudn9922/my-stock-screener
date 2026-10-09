"""Temporary probe: Yahoo EPS endpoints via curl_cffi (Chrome TLS impersonation)."""
import json, time
from curl_cffi import requests as cr
s = cr.Session(impersonate="chrome")
r = s.get("https://fc.yahoo.com/", allow_redirects=True)
print("fc", r.status_code, list(s.cookies.keys()))
r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
crumb = r.text
print("crumb", r.status_code, crumb[:20])
syms = ["AAPL","MSFT","TSM","NVO","BRK-B","ASML","SHOP","BABA","TM","SONY","INTC","RIVN","ZZZZ"]
fields = "symbol,epsTrailingTwelveMonths,trailingPE,epsCurrentYear,financialCurrency,currency,quoteType,regularMarketPrice"
r = s.get("https://query1.finance.yahoo.com/v7/finance/quote", params={"symbols": ",".join(syms), "fields": fields, "crumb": crumb})
print("quote", r.status_code)
try:
    for q in r.json()["quoteResponse"]["result"]:
        print("  ", {f: q.get(f) for f in fields.split(",")})
except Exception as e:
    print("  parse", e, r.text[:200])
# batch size: 400 real-ish symbols
big = ["AAPL","MSFT","GOOG","AMZN","META","NVDA","TSLA","JPM","V","MA"] * 40
big = [f"{b}" for b in big]
for n in (100, 250, 400):
    r = s.get("https://query1.finance.yahoo.com/v7/finance/quote", params={"symbols": ",".join(big[:n]), "fields": "symbol,epsTrailingTwelveMonths", "crumb": crumb})
    print("batch", n, r.status_code, len(r.json().get("quoteResponse", {}).get("result", [])) if r.status_code == 200 else r.text[:80])
now = int(time.time())
t0 = time.time()
for sym in ["AAPL", "TSM", "NVO", "MSFT", "BRK-B"]:
    r = s.get(f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}",
              params={"symbol": sym, "type": "annualDilutedEPS,annualBasicEPS,trailingDilutedEPS", "period1": now - 4*365*86400, "period2": now, "crumb": crumb})
    print("ts", sym, r.status_code)
    if r.status_code == 200:
        for x in r.json()["timeseries"]["result"]:
            t = x["meta"]["type"][0]
            print("   ", t, [f"{p['asOfDate']}:{p['reportedValue']['raw']}{p.get('currencyCode','')}" for p in (x.get(t) or []) if p])
print("ts 5 in", round(time.time() - t0, 2), "s")
