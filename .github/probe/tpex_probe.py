"""Temporary probe: dump TPEx trading-index JSON metadata for two months."""
import json, sys, requests
sys.path.insert(0, ".")
import main as m
for y, mo in ((2026, 9), (2023, 4), (2024, 12), (2025, 6)):
    r = requests.get(m.TPEX_TRADING_INDEX_URL, params={"date": f"{y}/{mo:02d}/01", "response": "json"},
                     headers=m.TPEX_HTTP_HEADERS, timeout=20)
    d = r.json()
    for t in d.get("tables", []):
        rows = t.pop("data", [])
        print(y, mo, json.dumps(t, ensure_ascii=False), "rows", len(rows), rows[:1])
    print(y, mo, "top keys", [k for k in d if k != "tables"], {k: d[k] for k in d if k not in ("tables",)})
    s = m.parse_tpex_trading_payload(d)
    print(y, mo, "parsed head", s.head(1).to_dict())
