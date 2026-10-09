"""Temporary probe: hit the real TPEx endpoints through main.py's own code."""
import json, sys, traceback, requests
sys.path.insert(0, ".")
import main as m

def raw(label, url, params, method):
    try:
        fn = requests.post if method == "post" else requests.get
        kw = {"data": params} if method == "post" else {"params": params}
        r = fn(url, headers=m.TPEX_HTTP_HEADERS, timeout=20, **kw)
        print(f"RAW {label} {method} {r.status_code} {r.headers.get('content-type')} len={len(r.text)}")
        print("   ", r.text[:600].replace("\n", " "))
    except Exception as e:
        print(f"RAW {label} ERR {type(e).__name__}: {e}")

for (name, url, params, method) in m._tpex_month_attempts(2026, 9, m.TPEX_INDEX_HISTORY_URL, m.TPEX_INDEX_HISTORY_LEGACY_URL):
    raw("index " + name, url, params, method)
for (name, url, params, method) in m._tpex_month_attempts(2026, 9, m.TPEX_TRADING_INDEX_URL, m.TPEX_TRADING_INDEX_LEGACY_URL):
    raw("volume " + name, url, params, method)

for fn in ("fetch_tpex_otc_index_month", "fetch_tpex_otc_volume_month"):
    for ym in ((2026, 9), (2026, 10), (2023, 4)):
        try:
            df = getattr(m, fn)(*ym)
            print(f"OK {fn}{ym}: {len(df)} rows")
            print(df.head(3).to_string()); print(df.tail(2).to_string())
        except Exception:
            print(f"FAIL {fn}{ym}"); traceback.print_exc()
