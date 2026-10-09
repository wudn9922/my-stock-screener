"""Temporary probe: ADR P/E = market cap / (TTM net income in USD)."""
import time
from curl_cffi import requests as cr
s = cr.Session(impersonate="chrome")
s.get("https://fc.yahoo.com/", timeout=20)
crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20).text
RATIO = dict(TSM=5,NVO=1,ASML=1,TM=10,BABA=8,SONY=1,HSBC=5,BP=6,SAP=1,UL=1,NVS=1,AZN=0.5,SHEL=2,PDD=4,BIDU=8,JD=2,INFY=1,HDB=3,MUFG=1,RIO=1,BHP=2,SNY=0.5,GSK=2,NTES=5,TCOM=1,ITUB=1,VALE=1,PBR=2,BTI=1,DEO=4,AAPL=1,MSFT=1)
f = "symbol,marketCap,sharesOutstanding,regularMarketPrice,epsTrailingTwelveMonths,trailingPE,financialCurrency,currency"
q = s.get("https://query1.finance.yahoo.com/v7/finance/quote", params={"symbols": ",".join(RATIO), "fields": f, "crumb": crumb}).json()["quoteResponse"]["result"]
quotes = {x["symbol"]: x for x in q}
curs = sorted({x.get("financialCurrency") for x in q if x.get("financialCurrency") not in (None, "USD")})
fx = {x["symbol"][:3]: x.get("regularMarketPrice") for x in s.get("https://query1.finance.yahoo.com/v7/finance/quote", params={"symbols": ",".join(c + "=X" for c in curs), "crumb": crumb}).json()["quoteResponse"]["result"]}
fx["USD"] = 1.0
print("fx", fx)
now = int(time.time())
print(f"{'sym':5} {'cur':4} {'price':>8} {'mcap$B':>8} {'NI_ttm$B':>9} {'PE_mcap':>8} {'eps/ADR':>8} {'yahooEPS':>8} {'yahooPE':>8} {'yEPSxR?':>8} {'shares':>14} niAsOf")
for sym in RATIO:
    x = quotes.get(sym, {})
    cur = x.get("financialCurrency") or "USD"
    ts = s.get(f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}",
               params={"symbol": sym, "type": "trailingNetIncomeCommonStockholders,trailingNetIncome", "period1": now - 2*365*86400, "period2": now, "crumb": crumb}).json()
    ni = {}
    for r in ts["timeseries"]["result"]:
        t = r["meta"]["type"][0]
        pts = [p for p in (r.get(t) or []) if p]
        if pts:
            p = max(pts, key=lambda p: p["asOfDate"])
            ni[t] = (p["reportedValue"]["raw"], p.get("currencyCode"), p["asOfDate"])
    v = ni.get("trailingNetIncomeCommonStockholders") or ni.get("trailingNetIncome")
    mcap = x.get("marketCap"); price = x.get("regularMarketPrice")
    if not v or not mcap or not fx.get(v[1] or cur):
        print(sym, "missing", v, mcap); continue
    ni_usd = v[0] / fx[v[1] or cur]
    pe = mcap / ni_usd if ni_usd > 0 else float("nan")
    eps_adr = price / pe if ni_usd > 0 else ni_usd / (mcap / price)
    ye = x.get("epsTrailingTwelveMonths")
    print(f"{sym:5} {cur:4} {price:8.2f} {mcap/1e9:8.1f} {ni_usd/1e9:9.2f} {pe:8.2f} {eps_adr:8.3f} {ye if ye is not None else float('nan'):8.3f} {x.get('trailingPE') or float('nan'):8.2f} {(ye or 0)*RATIO[sym]:8.3f} {x.get('sharesOutstanding') or 0:14,.0f} {v[2]} {v[1]}")
    time.sleep(0.15)
